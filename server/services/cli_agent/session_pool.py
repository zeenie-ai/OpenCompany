"""Warm-process pool for interactive ``claude`` sessions.

Keeps a live ``claude`` PTY per ``simpleMemory.node_id`` so successive
turns can reuse the warm process — the PTY stays alive across batches
and the next prompt rides on the existing claude session (saves the
~1-2 s spawn cost). By default acquire is **non-disruptive**: warm
reuse returns the same session UUID, so the memory bridge's
conversation continuity stays intact (claude appends to its existing
JSONL).

Context reset is an **explicit** operation via :meth:`clear`. ``/clear``
mints a NEW session UUID with a NEW JSONL file — per issue
`claude-code#32871 <https://github.com/anthropics/claude-code/issues/32871>`_
— so calling it is the right primitive when the user wants to start a
fresh conversation in the same pooled process (e.g. a "Clear
conversation" button on simpleMemory). The memory bridge auto-picks
up the new UUID from the next turn's ``result`` event.

Lifecycle policy:
  - **Idle TTL**: 30 min default. A background reaper task terminates
    pooled sessions whose ``last_used_at`` exceeds the threshold.
  - **Max size**: 16 default. LRU eviction when full.
  - **Crash recovery**: ``acquire()`` checks ``is_alive()`` and respawns
    transparently when the pooled PTY has died.
  - **Concurrency**: per-key ``asyncio.Lock`` serialises turns against
    the same pooled session (the existing parallel-batch guard in
    ``claude_code_agent`` enforces N=1 per memory node, so the lock is
    a belt-and-braces invariant).
  - **Shutdown**: ``ClaudeSessionPool.shutdown_all()`` terminates every
    pooled PTY. Wire into FastAPI's lifespan hook.

Pattern mirrors :mod:`services.process_service` (long-lived process pool)
and :mod:`nodes.telegram._service` (singleton service with lifecycle).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from core.logging import get_logger
from services.cli_agent.factory import create_cli_provider
from services.cli_agent.jsonl_watcher import (
    JsonlDirWatcher,
    JsonlWatcher,
    session_uuid_from_jsonl_path,
)
from services.cli_agent.protocol import AICliProvider, SessionResult
from services.cli_agent.transports import PtyHandle, get_pty_transport
from services.cli_agent.types import ClaudeTaskSpec

logger = get_logger(__name__)


# Defaults — keep aligned with the operator's mental model. The 30 min
# idle TTL matches a typical "I switched tasks but might come back"
# workflow; 16 concurrent pooled sessions matches the FastAPI default
# worker count.
_DEFAULT_IDLE_TTL = 30 * 60.0
_DEFAULT_MAX_SIZE = 16
_DEFAULT_REAPER_INTERVAL = 60.0  # reaper tick

# How long to wait for the new JSONL file to appear after sending
# ``/clear``. Empirically ~200 ms on Linux; 5 s is a generous cap that
# matches the first-spawn JSONL-locate timeout in ``AICliSession``.
_CLEAR_JSONL_TIMEOUT = 5.0


@dataclass
class PooledClaudeSession:
    """One live ``claude`` PTY + the metadata to reuse it across turns.

    Owns the PTY transport handle, the current ``JsonlWatcher``, and a
    persistent ``JsonlDirWatcher`` over the project dir (used to detect
    the new UUID claude assigns after each ``/clear``).
    """

    memory_node_id: str
    project_dir: Path
    pty_handle: PtyHandle
    current_session_uuid: str
    current_jsonl_path: Path
    dir_watcher: JsonlDirWatcher
    jsonl_watcher: JsonlWatcher
    cwd: Path
    # MCP bearer token embedded in the spawned claude's `--mcp-config`.
    # Set by the caller at spawn time so the warm-reuse path can read
    # it back and re-register the batch context with the SAME token
    # the live claude already has in its argv. The pool does NOT
    # issue/unregister tokens — the CLI handles its own MCP auth, the
    # caller (run_batch) owns the lifecycle.
    bearer_token: str = ""
    last_used_at: float = field(default_factory=time.monotonic)
    # Per-session lock; held throughout a turn or a /clear rotation. The
    # reaper consults ``lock.locked()`` to skip in-flight sessions — no
    # separate in_flight flag needed.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Set by the JSONL handler when claude writes a ``result`` event;
    # ``send_turn`` awaits this. Cleared before each new turn.
    result_event: asyncio.Event = field(default_factory=asyncio.Event)
    # Per-turn event buffer, cleared on each ``send_turn`` so the result
    # builder only sees the current turn's events.
    events_this_turn: List[Dict[str, Any]] = field(default_factory=list)
    # Dir-watcher signal for ``/clear``: fires when a new ``.jsonl``
    # appears under ``project_dir``.
    new_jsonl_event: asyncio.Event = field(default_factory=asyncio.Event)
    new_jsonl_path: Optional[Path] = None


class ClaudeSessionPool:
    """Per-``memory_node_id`` pool of warm ``claude`` PTYs.

    Acquired via :meth:`acquire` (auto-spawns if missing, sends
    ``/clear`` + captures new UUID if reusing). Released via
    :meth:`release` (marks idle for the reaper).
    """

    def __init__(
        self,
        *,
        idle_ttl: float = _DEFAULT_IDLE_TTL,
        max_size: int = _DEFAULT_MAX_SIZE,
        reaper_interval: float = _DEFAULT_REAPER_INTERVAL,
    ) -> None:
        self._pool: Dict[str, PooledClaudeSession] = {}
        self._idle_ttl = float(idle_ttl)
        self._max_size = int(max_size)
        self._reaper_interval = float(reaper_interval)
        # Top-level lock guards `self._pool` mutations only (insert,
        # evict). Per-session turns serialise via `PooledClaudeSession.lock`.
        self._pool_lock = asyncio.Lock()
        self._reaper_task: Optional[asyncio.Task[None]] = None
        self._shutdown = asyncio.Event()
        self._provider: AICliProvider = create_cli_provider("claude")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_reaper(self) -> None:
        """Spawn the background idle-eviction task. Safe to call repeatedly."""
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        self._shutdown.clear()
        self._reaper_task = asyncio.create_task(
            self._reaper_loop(), name="ClaudeSessionPool.reaper",
        )

    def peek(self, memory_node_id: str) -> Optional[PooledClaudeSession]:
        """Return the live session for ``memory_node_id`` or ``None``.

        Useful for callers that need to know whether to issue a fresh
        MCP bearer token before :meth:`acquire`. ``None`` means either
        no entry or the entry's PTY has died (caller should issue a
        fresh token for the impending cold-spawn). Best-effort: a TOCTOU
        race between this check and a concurrent :meth:`acquire` is
        harmless — the orphaned token leaks until app shutdown.
        """
        session = self._pool.get(memory_node_id)
        if session is None:
            return None
        if not session.pty_handle.is_alive():
            return None
        return session

    async def acquire(
        self,
        memory_node_id: str,
        *,
        spec: ClaudeTaskSpec,
        cwd: Path,
        env: Dict[str, str],
        defaults: Dict[str, Any],
        mcp_endpoint_url: Optional[str],
        mcp_bearer_token: Optional[str],
        connected_tool_names: Optional[List[str]] = None,
        workflow_id: Optional[str] = None,
    ) -> PooledClaudeSession:
        """Return a live :class:`PooledClaudeSession`.

        Behaviour:
          - **No live entry**: spawn fresh. ``spec.prompt`` is *not*
            included in argv (the caller writes it via
            :meth:`send_turn` once the watcher is attached).
          - **Live entry**: return as-is. The next :meth:`send_turn`
            writes the prompt to the existing PTY; claude appends to
            its current JSONL so the conversation continues in the
            same session UUID. **No implicit ``/clear``** — that would
            break the memory bridge's continuity contract. The caller
            can explicitly call :meth:`clear` to reset context (mints
            a new UUID via ``/clear`` and swaps the watcher).
          - **Dead entry**: drop and spawn fresh.

        Caller MUST call :meth:`release` (or use the contextmanager
        wrapper) so the reaper can free idle sessions.
        """
        async with self._pool_lock:
            existing = self._pool.get(memory_node_id)
            if existing is not None and not existing.pty_handle.is_alive():
                logger.info(
                    "[ClaudeSessionPool] dropping dead session for "
                    "memory_node=%s pid=%s",
                    memory_node_id, existing.pty_handle.pid,
                )
                await self._terminate_locked(memory_node_id, reason="crashed")
                existing = None

            if existing is not None:
                # Warm path. Take the per-session lock OUTSIDE the
                # pool_lock to avoid deadlocks if a turn is still
                # finishing.
                pass
            else:
                # Cold path. Evict LRU if at capacity.
                if len(self._pool) >= self._max_size:
                    await self._evict_lru_locked()
                session = await self._spawn(
                    memory_node_id=memory_node_id,
                    spec=spec,
                    cwd=cwd,
                    env=env,
                    defaults=defaults,
                    mcp_endpoint_url=mcp_endpoint_url,
                    mcp_bearer_token=mcp_bearer_token,
                    connected_tool_names=connected_tool_names,
                )
                self._pool[memory_node_id] = session
                logger.info(
                    "[ClaudeSessionPool] spawned new session memory_node=%s "
                    "pid=%s uuid=%s",
                    memory_node_id, session.pty_handle.pid,
                    session.current_session_uuid,
                )
                # Typed CloudEvent — FE wires per-memory-node lifecycle
                # status from the same envelope as agent_progress /
                # node_parameters_updated. Fire-and-forget; broadcaster
                # is the process-wide singleton.
                await self._emit_event(
                    "spawned",
                    memory_node_id=memory_node_id,
                    workflow_id=workflow_id,
                    session_uuid=session.current_session_uuid,
                    pid=session.pty_handle.pid,
                )
                return session

        # Warm-reuse path. Returned as-is — preserves memory continuity
        # (claude appends to its existing JSONL on the next prompt). The
        # caller can opt in to context reset via the explicit
        # :meth:`clear` method.
        assert existing is not None
        existing.last_used_at = time.monotonic()
        logger.info(
            "[ClaudeSessionPool] warm reuse memory_node=%s pid=%s uuid=%s",
            memory_node_id, existing.pty_handle.pid,
            existing.current_session_uuid,
        )
        return existing

    async def clear(
        self,
        session: PooledClaudeSession,
        *,
        workflow_id: Optional[str] = None,
    ) -> str:
        """Explicit ``/clear`` — mints a new session UUID and rotates
        the watcher. Returns the new UUID. Use when the user wants to
        start a fresh conversation in the same pooled process (e.g.
        a "Clear conversation" button on simpleMemory). The memory
        bridge auto-picks up the new UUID via the next ``send_turn``
        result event."""
        old_uuid = session.current_session_uuid
        await self._clear_and_swap(session)
        if session.current_session_uuid != old_uuid:
            await self._emit_event(
                "cleared",
                memory_node_id=session.memory_node_id,
                workflow_id=workflow_id,
                old_session_uuid=old_uuid,
                new_session_uuid=session.current_session_uuid,
            )
        return session.current_session_uuid

    async def release(
        self, session: PooledClaudeSession,
    ) -> None:
        """Update last-used-at so the reaper measures idle time from now.

        Nothing else needed — the per-turn lock is released by
        ``send_turn``'s context manager, so once ``release`` returns the
        session is genuinely idle and the reaper may evict it.
        """
        session.last_used_at = time.monotonic()

    async def terminate(self, memory_node_id: str) -> None:
        """Force-terminate a specific pooled session."""
        async with self._pool_lock:
            await self._terminate_locked(memory_node_id)

    async def shutdown_all(self) -> None:
        """Terminate every pooled session + stop the reaper. Wire into
        FastAPI's lifespan ``shutdown`` event."""
        self._shutdown.set()
        if self._reaper_task is not None and not self._reaper_task.done():
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reaper_task = None

        async with self._pool_lock:
            keys = list(self._pool.keys())
            for key in keys:
                await self._terminate_locked(key, reason="shutdown")

    async def send_turn(
        self,
        session: PooledClaudeSession,
        prompt: str,
        *,
        timeout_seconds: int = 600,
        workflow_id: Optional[str] = None,
    ) -> SessionResult:
        """Write a prompt to a pooled session and await the next
        ``result`` event. Serialises against other callers via the
        session's lock. Emits a ``claude.session.usage`` CloudEvent
        after each successful turn so the FE can render cost/token
        data per memory node."""
        async with session.lock:
            session.result_event.clear()
            session.events_this_turn = []
            # Claude's interactive TUI reads input on Enter (``\r``);
            # closing stdin does NOT submit (claude-code#15553).
            payload = (prompt.rstrip("\r\n") + "\r").encode("utf-8")
            await session.pty_handle.write(payload)
            try:
                await asyncio.wait_for(
                    session.result_event.wait(),
                    timeout=float(timeout_seconds),
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[ClaudeSessionPool] turn timeout memory_node=%s "
                    "prompt_len=%d",
                    session.memory_node_id, len(prompt),
                )
                return self._build_result_from_events(
                    session=session,
                    prompt=prompt,
                    success=False,
                    error=f"timeout after {timeout_seconds}s",
                )
            session.last_used_at = time.monotonic()
            result = self._build_result_from_events(
                session=session,
                prompt=prompt,
                success=True,
            )
            # Surface per-turn usage as a typed CloudEvent. Same data
            # ``/usage`` displays in the TUI — but structured, so the FE
            # can render a panel without scraping plain-text TUI output.
            cu = result.canonical_usage
            await self._emit_event(
                "usage",
                memory_node_id=session.memory_node_id,
                workflow_id=workflow_id,
                session_uuid=result.session_id or session.current_session_uuid,
                total_cost_usd=result.cost_usd,
                input_tokens=cu.input_tokens,
                output_tokens=cu.output_tokens,
                cache_read_input_tokens=cu.cache_read,
                cache_creation_input_tokens=cu.cache_write,
                duration_ms=result.duration_ms,
                num_turns=result.num_turns,
            )
            return result

    # ------------------------------------------------------------------
    # Spawn / clear / dispatch internals
    # ------------------------------------------------------------------

    async def _spawn(
        self,
        *,
        memory_node_id: str,
        spec: ClaudeTaskSpec,
        cwd: Path,
        env: Dict[str, str],
        defaults: Dict[str, Any],
        mcp_endpoint_url: Optional[str],
        mcp_bearer_token: Optional[str],
        connected_tool_names: Optional[List[str]],
    ) -> PooledClaudeSession:
        """Cold-spawn a new pooled claude session. Caller holds pool_lock."""
        from services.claude_oauth import MACHINA_CLAUDE_DIR
        from .session import _PROJECT_KEY_RE  # noqa: PLC0415 — internal reuse

        # Where claude will write JSONLs for this cwd.
        project_key = _PROJECT_KEY_RE.sub("-", str(cwd))
        project_dir = Path(MACHINA_CLAUDE_DIR) / "projects" / project_key
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "[ClaudeSessionPool] mkdir project_dir failed dir=%s exc=%s",
                project_dir, exc,
            )

        # Snapshot existing JSONL files BEFORE spawn so we can pick out
        # the new one claude creates.
        try:
            baseline_files = {
                p.name for p in project_dir.iterdir()
                if p.suffix == ".jsonl"
            }
        except OSError:
            baseline_files = set()

        # Build argv WITHOUT the prompt — we'll write the first turn's
        # prompt to the PTY in `send_turn` so the spawn-to-ready path
        # is the same as the post-/clear path.
        argv = self._provider.interactive_argv(
            spec,
            defaults=defaults,
            mcp_endpoint_url=mcp_endpoint_url,
            mcp_bearer_token=mcp_bearer_token,
            connected_tool_names=connected_tool_names,
            include_prompt=False,
        )

        # Spawn via the cross-platform PTY transport.
        transport = get_pty_transport()
        pty_handle = await transport.spawn(argv, cwd=cwd, env=env)

        # Wait for the JSONL file to appear (claude writes the
        # system/init event almost immediately on startup).
        jsonl_path = await self._wait_for_new_jsonl(
            project_dir, baseline_files,
        )
        session_uuid = session_uuid_from_jsonl_path(jsonl_path) or ""

        # Build the session shell with placeholder watchers, then start
        # them. Order: dir_watcher first so any subsequent /clear
        # detects the post-clear file via the dir baseline. The bearer
        # token is stored on the session so warm reuse can re-register
        # the batch context with the SAME token the claude already has
        # in its argv (the caller owns issue / unregister).
        session = PooledClaudeSession(
            memory_node_id=memory_node_id,
            project_dir=project_dir,
            pty_handle=pty_handle,
            current_session_uuid=session_uuid,
            current_jsonl_path=jsonl_path,
            dir_watcher=None,  # type: ignore[arg-type]
            jsonl_watcher=None,  # type: ignore[arg-type]
            cwd=cwd,
            bearer_token=mcp_bearer_token or "",
        )

        async def _on_new_jsonl(path: Path) -> None:
            session.new_jsonl_path = path
            session.new_jsonl_event.set()

        async def _on_event(event: Dict[str, Any]) -> None:
            session.events_this_turn.append(event)
            if self._provider.is_final_event(event):
                session.result_event.set()

        session.dir_watcher = JsonlDirWatcher(
            project_dir, on_new_file=_on_new_jsonl,
        )
        await session.dir_watcher.start()
        session.jsonl_watcher = JsonlWatcher(
            jsonl_path, on_event=_on_event, start_from_end=False,
        )
        await session.jsonl_watcher.start()

        return session

    async def _clear_and_swap(self, session: PooledClaudeSession) -> None:
        """Send ``/clear``, await the new JSONL filename via the dir
        watcher, swap the file watcher to the new path. Holds the
        per-session lock so concurrent turns don't race."""
        async with session.lock:
            session.new_jsonl_event.clear()
            session.new_jsonl_path = None
            # Stop the current file watcher BEFORE sending /clear so
            # any tail-end output of the old session doesn't leak into
            # the new turn's events buffer.
            await session.jsonl_watcher.stop()
            session.events_this_turn = []
            session.result_event.clear()

            # Slash commands must be at the start of a line; claude
            # auto-fires on Enter.
            await session.pty_handle.write(b"/clear\r")

            try:
                await asyncio.wait_for(
                    session.new_jsonl_event.wait(),
                    timeout=_CLEAR_JSONL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[ClaudeSessionPool] /clear didn't produce a new JSONL "
                    "memory_node=%s within %ss; falling back to mtime sort",
                    session.memory_node_id, _CLEAR_JSONL_TIMEOUT,
                )

            new_path = session.new_jsonl_path
            if new_path is None:
                # Fallback: pick the newest .jsonl in the project dir.
                try:
                    candidates = [
                        p for p in session.project_dir.iterdir()
                        if p.suffix == ".jsonl"
                    ]
                    if candidates:
                        new_path = max(candidates, key=lambda p: p.stat().st_mtime)
                except OSError:
                    pass

            if new_path is not None and new_path != session.current_jsonl_path:
                session.current_jsonl_path = new_path
                session.current_session_uuid = (
                    session_uuid_from_jsonl_path(new_path) or ""
                )
                logger.info(
                    "[ClaudeSessionPool] /clear rotated memory_node=%s "
                    "new_uuid=%s",
                    session.memory_node_id, session.current_session_uuid,
                )

            # Reattach the file watcher to whatever path we believe is
            # current. If detection failed we re-watch the previous
            # file — the next turn's `result` event will still fire.
            async def _on_event(event: Dict[str, Any]) -> None:
                session.events_this_turn.append(event)
                if self._provider.is_final_event(event):
                    session.result_event.set()

            session.jsonl_watcher = JsonlWatcher(
                session.current_jsonl_path,
                on_event=_on_event,
                start_from_end=False,
            )
            await session.jsonl_watcher.start()

    async def _wait_for_new_jsonl(
        self, project_dir: Path, baseline: set[str],
    ) -> Path:
        """Mirror of ``AICliSession._wait_for_new_jsonl``. Local copy so
        the pool doesn't depend on a private method of the session
        class."""
        deadline = time.monotonic() + _CLEAR_JSONL_TIMEOUT
        while time.monotonic() < deadline:
            try:
                current = {
                    p.name for p in project_dir.iterdir()
                    if p.suffix == ".jsonl"
                }
            except OSError:
                current = set()
            new_names = current - baseline
            if new_names:
                candidates = [project_dir / n for n in new_names]
                try:
                    return max(candidates, key=lambda p: p.stat().st_mtime)
                except OSError:
                    return candidates[0]
            await asyncio.sleep(0.1)
        raise TimeoutError(
            f"No session JSONL appeared under {project_dir} within "
            f"{_CLEAR_JSONL_TIMEOUT}s"
        )

    def _build_result_from_events(
        self,
        *,
        session: PooledClaudeSession,
        prompt: str,
        success: bool,
        error: Optional[str] = None,
    ) -> SessionResult:
        """Translate the per-turn event buffer to a ``SessionResult``.

        Reuses the provider's ``event_to_session_result`` so the cost
        / token / response extraction stays identical to the non-
        pooled path.
        """
        provider_result = self._provider.event_to_session_result(
            session.events_this_turn,
            stderr="",
            exit_code=0 if success else -1,
        )
        final_success = success and provider_result.get("success", True)
        final_error = error or provider_result.get("error")
        return SessionResult(
            task_id=f"pooled_{uuid.uuid4().hex[:8]}",
            session_id=provider_result.get("session_id")
                       or session.current_session_uuid,
            provider=self._provider.name,
            prompt=prompt,
            worktree_path=str(session.cwd),
            response=str(provider_result.get("response") or "")[:4000],
            cost_usd=provider_result.get("cost_usd"),
            duration_ms=provider_result.get("duration_ms"),
            num_turns=provider_result.get("num_turns"),
            tool_calls=int(provider_result.get("tool_calls", 0)),
            canonical_usage=provider_result.get(
                "canonical_usage",
            ) or self._provider.canonical_usage(session.events_this_turn),
            provider_data=dict(provider_result.get("provider_data") or {}),
            success=final_success,
            error=final_error if not final_success else None,
        )

    # ------------------------------------------------------------------
    # Eviction / reaper
    # ------------------------------------------------------------------

    async def _terminate_locked(
        self,
        memory_node_id: str,
        *,
        reason: str = "explicit",
    ) -> None:
        """Caller holds ``self._pool_lock``. ``reason`` is the
        CloudEvent ``reason`` value (``idle`` / ``crashed`` / ``evicted``
        / ``shutdown`` / ``explicit``) — drives the
        ``claude.session.terminated`` envelope."""
        session = self._pool.pop(memory_node_id, None)
        if session is None:
            return
        session_uuid = session.current_session_uuid
        try:
            await session.jsonl_watcher.stop()
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug(
                "[ClaudeSessionPool] watcher stop on terminate: %s", exc,
            )
        try:
            await session.dir_watcher.stop()
        except Exception as exc:  # pragma: no cover
            logger.debug(
                "[ClaudeSessionPool] dir watcher stop on terminate: %s", exc,
            )
        try:
            await session.pty_handle.kill()
        except Exception as exc:  # pragma: no cover
            logger.debug(
                "[ClaudeSessionPool] PTY kill on terminate: %s", exc,
            )
        logger.info(
            "[ClaudeSessionPool] terminated memory_node=%s reason=%s",
            memory_node_id, reason,
        )
        await self._emit_event(
            "terminated",
            memory_node_id=memory_node_id,
            reason=reason,
            session_uuid=session_uuid,
        )

    async def _evict_lru_locked(self) -> None:
        """Caller holds ``self._pool_lock``. Evicts the least-recently-
        used non-in-flight entry."""
        idle_entries = [
            (key, sess) for key, sess in self._pool.items()
            if not sess.lock.locked()
        ]
        if not idle_entries:
            # All sessions in-flight; nothing safe to evict. Caller
            # spawns over the soft cap.
            logger.info(
                "[ClaudeSessionPool] all %d entries in-flight; pool over cap",
                len(self._pool),
            )
            return
        oldest_key, _ = min(
            idle_entries, key=lambda kv: kv[1].last_used_at,
        )
        logger.info(
            "[ClaudeSessionPool] evicting LRU memory_node=%s", oldest_key,
        )
        await self._terminate_locked(oldest_key, reason="evicted")

    # ------------------------------------------------------------------
    # CloudEvent emission helper
    # ------------------------------------------------------------------

    async def _emit_event(
        self,
        kind: Literal["spawned", "cleared", "terminated", "usage"],
        *,
        memory_node_id: str,
        workflow_id: Optional[str] = None,
        **payload: Any,
    ) -> None:
        """Dispatch one of the four claude-session CloudEvents.

        Lazy-imports the broadcaster singleton so the pool stays a
        leaf service. Failures are swallowed (best-effort) — a missed
        UI update must not break the agent loop.
        """
        try:
            from services.status_broadcaster import get_status_broadcaster
            broadcaster = get_status_broadcaster()
        except Exception:  # pragma: no cover — defensive
            return

        try:
            if kind == "spawned":
                await broadcaster.broadcast_claude_session_spawned(
                    memory_node_id,
                    session_uuid=payload["session_uuid"],
                    pid=payload["pid"],
                    workflow_id=workflow_id,
                )
            elif kind == "cleared":
                await broadcaster.broadcast_claude_session_cleared(
                    memory_node_id,
                    old_session_uuid=payload["old_session_uuid"],
                    new_session_uuid=payload["new_session_uuid"],
                    workflow_id=workflow_id,
                )
            elif kind == "terminated":
                await broadcaster.broadcast_claude_session_terminated(
                    memory_node_id,
                    reason=payload["reason"],
                    session_uuid=payload.get("session_uuid"),
                    workflow_id=workflow_id,
                )
            elif kind == "usage":
                await broadcaster.broadcast_claude_session_usage(
                    memory_node_id,
                    session_uuid=payload["session_uuid"],
                    total_cost_usd=payload.get("total_cost_usd"),
                    input_tokens=payload.get("input_tokens", 0),
                    output_tokens=payload.get("output_tokens", 0),
                    cache_read_input_tokens=payload.get(
                        "cache_read_input_tokens", 0,
                    ),
                    cache_creation_input_tokens=payload.get(
                        "cache_creation_input_tokens", 0,
                    ),
                    duration_ms=payload.get("duration_ms"),
                    num_turns=payload.get("num_turns"),
                    workflow_id=workflow_id,
                )
        except Exception as exc:  # pragma: no cover — best-effort
            logger.debug(
                "[ClaudeSessionPool] _emit_event(%s) failed: %s",
                kind, exc,
            )

    async def _reaper_loop(self) -> None:
        try:
            while not self._shutdown.is_set():
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(),
                        timeout=self._reaper_interval,
                    )
                    return  # shutdown fired
                except asyncio.TimeoutError:
                    pass

                now = time.monotonic()
                async with self._pool_lock:
                    # Skip in-flight sessions — a turn that takes longer
                    # than idle_ttl would otherwise get reaped mid-flight.
                    expired_keys = [
                        key for key, sess in self._pool.items()
                        if not sess.lock.locked()
                        and now - sess.last_used_at >= self._idle_ttl
                    ]
                    for key in expired_keys:
                        logger.info(
                            "[ClaudeSessionPool] reaping idle session "
                            "memory_node=%s age=%.1fs",
                            key, now - self._pool[key].last_used_at,
                        )
                        await self._terminate_locked(key, reason="idle")
        except asyncio.CancelledError:
            raise


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_instance: Optional[ClaudeSessionPool] = None


def get_session_pool() -> ClaudeSessionPool:
    """Return the process-wide ``ClaudeSessionPool``."""
    global _instance
    if _instance is None:
        _instance = ClaudeSessionPool()
    return _instance
