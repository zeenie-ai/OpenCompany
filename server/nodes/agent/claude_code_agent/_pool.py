"""Long-lived ``claude`` subprocess pool — VSCode-extension pattern.

Keeps one warm ``claude --output-format stream-json --input-format
stream-json --verbose --ide`` subprocess per ``simpleMemory.node_id``
so successive turns can reuse the same process — same session UUID
across turns, no respawn cost. Mirrors what Anthropic's official
VSCode extension does (verified from the on-disk extension source at
``$VSCODE_EXT_DIR/anthropic.claude-code-<ver>/extension.js`` line 156).

Why subprocess (not PTY): pywinpty's ConPTY emulation does not deliver
keystrokes to claude's Ink TUI on Windows — empirically confirmed
across four test variants (`docs-internal/claude_code_interactive_mode.md`
records the post-mortem). Plain stdio pipes work cross-platform and
match the documented multi-turn integration pattern.

Per-turn mechanics:

  1. Caller writes one stream-json line to ``proc.stdin``:
     ``{"type":"user","message":{"role":"user","content":"..."}}\\n``
  2. Claude reads stdin, processes the turn, emits stream-json events
     to ``proc.stdout`` (``system/init``, ``assistant``, ``user``,
     ``system/compact_boundary``, and finally ``result``). Claude
     ALSO writes a subset of events to the on-disk session JSONL at
     ``<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session_uuid>.jsonl``
     for persistent multi-batch memory — but the ``result`` event is
     stdout-only in stream-json mode, so stdout is our runtime contract.
  3. A background ``stdout_reader_task`` started at spawn time parses
     each line as a stream-json event and dispatches via
     :meth:`_handle_stream_event`. Events flow into the per-turn
     buffer; ``result_event`` fires when the final ``type=="result"``
     event lands.
  4. ``send_turn`` returns a :class:`SessionResult` built from the
     turn's events. The session UUID is captured from the result event
     so subsequent batches can spawn ``--resume <UUID>`` if the
     pooled process has been reaped.

Lifecycle policy:

  - **Idle TTL**: 30 min default. Background reaper terminates
    long-idle subprocesses.
  - **Max size**: 16 concurrent pooled sessions. LRU eviction at cap.
  - **Crash recovery**: ``acquire()`` checks ``process.returncode`` and
    respawns transparently when the pooled subprocess has died.
  - **Per-session lock**: serialises turns against the same pooled
    process so two ``send_turn`` calls don't write overlapping
    stream-json lines.
  - **Shutdown**: :meth:`shutdown_all` closes every subprocess's stdin
    (claude exits cleanly on EOF) and waits briefly, then kills any
    holdouts. Wire into FastAPI's lifespan ``shutdown`` event.

Continuity across process restarts:

  - First spawn for a memory-bound run: argv emits ``--continue``
    (claude resolves the latest conversation under cwd's
    ``project_key`` automatically; works whether or not a prior
    JSONL exists).
  - Each successful turn captures ``result.session_id`` onto
    ``session.current_session_uuid``. If the subprocess is later
    reaped and a new ``acquire`` happens, the next spawn emits
    ``--resume <session.current_session_uuid>`` so the SAME JSONL
    keeps growing across process restarts.
  - :meth:`clear` is an explicit context-reset primitive: kill the
    subprocess, drop the captured UUID, let the next ``acquire``
    spawn fresh with no continuity flag (claude assigns a new UUID).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from core.logging import get_logger
from services.cli_agent.factory import create_cli_provider
from services.cli_agent.protocol import AICliProvider, SessionResult
from services.cli_agent.types import ClaudeTaskSpec

logger = get_logger(__name__)


# Defaults — keep aligned with the operator's mental model.
_DEFAULT_IDLE_TTL = 30 * 60.0
_DEFAULT_MAX_SIZE = 16
_DEFAULT_REAPER_INTERVAL = 60.0  # reaper tick

# Soft window for stdin EOF to drive a graceful claude shutdown before
# we escalate to ``proc.kill()``. Claude exits within ~100 ms of EOF.
_SHUTDOWN_GRACE = 2.0


@dataclass
class PooledClaudeSession:
    """One live ``claude`` subprocess + the metadata to reuse it across turns.

    No PTY, no file watcher. ``process`` is an ``asyncio.subprocess.Process``
    opened with ``stdin=PIPE``, ``stdout=PIPE``, ``stderr=PIPE``. Prompts
    go via ``process.stdin.write(stream_json_line)``; events come back on
    ``process.stdout`` (stream-json output mode), parsed line-by-line by
    a background ``stdout_reader_task`` that dispatches each parsed event
    via :meth:`ClaudeSessionPool._handle_stream_event`. Stderr is drained
    separately and logged.

    The on-disk JSONL at
    ``<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session_uuid>.jsonl``
    is still written by claude — it's the persistence layer that
    ``--continue`` / ``--resume`` rehydrate from across process
    restarts — but the runtime contract here is stdout-only because
    the ``result`` event (final turn outcome) goes ONLY to stdout in
    stream-json mode, NOT to the JSONL file.
    """

    memory_node_id: str
    process: asyncio.subprocess.Process
    cwd: Path
    # ``current_session_uuid`` is "" until the first turn's ``system/init``
    # or ``result`` event reveals it. Subsequent spawns for the same
    # memory_node_id (after a crash / reap) emit ``--resume <UUID>`` so
    # the same on-disk JSONL keeps growing across process restarts.
    current_session_uuid: str = ""
    # Bearer token baked into claude's argv (via ``--mcp-config``) at
    # spawn time. The token persists for the subprocess's lifetime —
    # claude reads it from the frozen argv on every MCP request, so we
    # can never rotate it without respawning. Stored here so the pool
    # can (a) rebind the matching :class:`BatchContext` in place when
    # the tool / skill / credential surface changes between batches
    # without paying a cold-spawn, and (b) call ``unregister_batch``
    # in ``_terminate_locked`` to drain the FastMCP tool refcounts
    # when the subprocess dies.
    batch_token: str = ""
    # Per-workflow workspace dir (``data/workspaces/<workflow_id>/``)
    # passed via ``--add-dir`` so claude discovers the skills tree
    # under ``<workspace_dir>/.claude/skills/`` per the
    # "Automatic discovery from parent and nested directories" rule
    # in code.claude.com/docs/en/skills. Per-workflow isolation:
    # workflow A's wired skills never bleed into workflow B's
    # subprocess even when both spawn with ``cwd=repo_root``.
    workspace_dir: Optional[Path] = None
    # Set of skill names currently materialised under
    # ``<workspace_dir>/.claude/skills/`` for this warm subprocess.
    # The pool calls :func:`nodes.agent.claude_code_agent._skills.materialise_skills`
    # with this as the ``previous_skill_names`` arg on every warm
    # reuse, so only the delta is touched on disk. Claude's filesystem
    # watcher picks up add/remove events without respawning.
    materialised_skills: frozenset = field(default_factory=frozenset)
    last_used_at: float = field(default_factory=time.monotonic)
    # Lease held from ``acquire`` through context rebinding, ``send_turn``,
    # and ``release``.  The narrower ``lock`` below only protected stdin;
    # without this lease a second batch could replace the MCP BatchContext
    # after the first batch acquired the session but before its turn ran.
    turn_lease: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Per-session lock; held throughout a turn. The reaper consults
    # ``lock.locked()`` to skip in-flight sessions.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Set by the stdout reader when claude emits a ``result`` event;
    # ``send_turn`` awaits this. Cleared before each new turn.
    result_event: asyncio.Event = field(default_factory=asyncio.Event)
    # Per-turn event buffer, cleared on each ``send_turn``.
    events_this_turn: List[Dict[str, Any]] = field(default_factory=list)
    # Background tasks: parse stream-json events from stdout (single
    # source of truth for runtime events), drain stderr (logged at
    # warning level).
    stdout_reader_task: Optional[asyncio.Task[None]] = None
    stderr_drain_task: Optional[asyncio.Task[None]] = None


class ClaudeSessionPool:
    """Per-``memory_node_id`` pool of warm ``claude`` subprocesses.

    Public surface (unchanged from the PTY-era pool — callers in
    :mod:`services.cli_agent.service` see the same method shapes):

      - :meth:`acquire` — get or spawn a session for a memory_node_id
      - :meth:`send_turn` — write one stream-json user message, return result
      - :meth:`release` — mark idle for the reaper
      - :meth:`clear` — kill subprocess + drop UUID (next acquire spawns fresh)
      - :meth:`terminate` — force-drop a specific entry
      - :meth:`shutdown_all` — drop everything (FastAPI lifespan hook target)
      - :meth:`peek` — read-only inspection
      - :meth:`start_reaper` — begin background idle-eviction
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
            self._reaper_loop(),
            name="ClaudeSessionPool.reaper",
        )

    def peek(self, memory_node_id: str) -> Optional[PooledClaudeSession]:
        """Return the live session for ``memory_node_id`` or ``None``."""
        session = self._pool.get(memory_node_id)
        if session is None:
            return None
        if session.process.returncode is not None:
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
        connected_skill_names: Optional[List[str]] = None,
        workspace_dir: Optional[Path] = None,
        workflow_id: Optional[str] = None,
    ) -> PooledClaudeSession:
        """Return a live :class:`PooledClaudeSession`.

        Behaviour:

          - No live entry → spawn fresh.
          - Live entry whose subprocess is dead → drop and respawn,
            propagating ``current_session_uuid`` so the new spawn
            emits ``--resume <UUID>`` and continues the same JSONL.
          - Live entry with live subprocess → return as-is. Next
            ``send_turn`` writes the prompt to the existing subprocess's
            stdin and claude appends to the same session JSONL.

        Caller MUST call :meth:`release` so the reaper can mark idle.
        """
        while True:
            # Capture or create the entry under the global map lock, but do
            # not wait for a busy per-memory lease here: a queued turn for M1
            # must never prevent an unrelated M2 from being acquired.
            async with self._pool_lock:
                existing = self._pool.get(memory_node_id)
                crashed_uuid = ""
                if existing is not None and existing.process.returncode is not None:
                    logger.info(
                        "[ClaudeSessionPool] dropping dead session "
                        "memory_node=%s pid=%s exit=%s — will respawn",
                        memory_node_id,
                        existing.process.pid,
                        existing.process.returncode,
                    )
                    crashed_uuid = existing.current_session_uuid
                    await self._terminate_locked(memory_node_id, reason="crashed")
                    existing = None

                if existing is None:
                    if len(self._pool) >= self._max_size:
                        await self._evict_lru_locked()

                    # When recovering from a crash, splice the captured UUID
                    # into the spec so the spawn emits ``--resume <UUID>``.
                    if crashed_uuid and not spec.resume_session_id:
                        spec.resume_session_id = crashed_uuid
                        spec.continue_session = False

                    session = await self._spawn(
                        memory_node_id=memory_node_id,
                        spec=spec,
                        cwd=cwd,
                        env=env,
                        defaults=defaults,
                        mcp_endpoint_url=mcp_endpoint_url,
                        mcp_bearer_token=mcp_bearer_token,
                        connected_tool_names=connected_tool_names,
                        connected_skill_names=connected_skill_names,
                        workspace_dir=workspace_dir,
                    )
                    if mcp_bearer_token:
                        session.batch_token = mcp_bearer_token
                    session.workspace_dir = workspace_dir
                    session.materialised_skills = frozenset(
                        connected_skill_names or []
                    )
                    if crashed_uuid:
                        session.current_session_uuid = crashed_uuid
                    self._pool[memory_node_id] = session
                    await session.turn_lease.acquire()  # new; never contended
                    try:
                        logger.info(
                            "[ClaudeSessionPool] spawned new session memory_node=%s pid=%s",
                            memory_node_id,
                            session.process.pid,
                        )
                        await self._emit_event(
                            "spawned",
                            memory_node_id=memory_node_id,
                            workflow_id=workflow_id,
                            session_uuid=session.current_session_uuid,
                            pid=session.process.pid,
                        )
                    except BaseException:
                        if session.turn_lease.locked():
                            session.turn_lease.release()
                        await self._terminate_locked(
                            memory_node_id,
                            reason="acquire_failed",
                        )
                        raise
                    return session

            # ``existing`` was captured while holding the map lock. Wait only
            # on its own lease, then verify it was not evicted/replaced while
            # we waited before rebinding any context.
            await existing.turn_lease.acquire()
            try:
                async with self._pool_lock:
                    still_current = (
                        self._pool.get(memory_node_id) is existing
                        and existing.process.returncode is None
                    )
            except BaseException:
                # Cancellation can land while waiting for the map lock after
                # this turn already acquired its per-memory lease.
                if existing.turn_lease.locked():
                    existing.turn_lease.release()
                raise
            if not still_current:
                existing.turn_lease.release()
                continue

            return await self._prepare_warm_reuse(
                existing,
                memory_node_id=memory_node_id,
                mcp_bearer_token=mcp_bearer_token,
                connected_skill_names=connected_skill_names,
            )

    async def _prepare_warm_reuse(
        self,
        existing: PooledClaudeSession,
        *,
        memory_node_id: str,
        mcp_bearer_token: Optional[str],
        connected_skill_names: Optional[List[str]],
    ) -> PooledClaudeSession:
        """Rebind one already-leased warm process for the next turn."""
        try:
            existing.last_used_at = time.monotonic()
            if (
                existing.batch_token
                and mcp_bearer_token
                and mcp_bearer_token != existing.batch_token
            ):
                from services.cli_agent.mcp_server import (
                    lookup_batch,
                    rebind_batch,
                    unregister_batch,
                )

                new_ctx = lookup_batch(mcp_bearer_token)
                if new_ctx is not None:
                    rebind_batch(
                        existing.batch_token,
                        workflow_id=new_ctx.workflow_id,
                        node_id=new_ctx.node_id,
                        execution_id=new_ctx.execution_id,
                        broadcaster=new_ctx.broadcaster,
                        connected_tools=new_ctx.connected_tools,
                        connected_skill_names=new_ctx.connected_skill_names,
                        allowed_credentials=new_ctx.allowed_credentials,
                        workspace_dir=new_ctx.workspace_dir,
                    )
                    unregister_batch(mcp_bearer_token)

            if existing.workspace_dir is not None:
                from ._skills import materialise_skills

                new_set = frozenset(connected_skill_names or [])
                added, removed = await materialise_skills(
                    existing.workspace_dir,
                    new_set,
                    previous_skill_names=existing.materialised_skills,
                    log_label=f"pool {memory_node_id}",
                )
                if added or removed:
                    logger.info(
                        "[ClaudeSessionPool] skill diff "
                        "memory_node=%s +%d -%d (now %d wired)",
                        memory_node_id,
                        added,
                        removed,
                        len(new_set),
                    )
                existing.materialised_skills = new_set

            logger.info(
                "[ClaudeSessionPool] warm reuse memory_node=%s pid=%s uuid=%s",
                memory_node_id,
                existing.process.pid,
                existing.current_session_uuid or "(unresolved)",
            )
            return existing
        except BaseException:
            # ``acquire`` did not return a session, so its caller has no
            # object it can pass to ``release``. Also discard the fresh token
            # allocated for a failed warm rebind; service.py deliberately
            # leaves pool-path token ownership to this class.
            if (
                mcp_bearer_token
                and mcp_bearer_token != existing.batch_token
            ):
                try:
                    from services.cli_agent.mcp_server import unregister_batch

                    unregister_batch(mcp_bearer_token)
                except Exception:
                    pass
            if existing.turn_lease.locked():
                existing.turn_lease.release()
            raise

    async def clear(
        self,
        session: PooledClaudeSession,
        *,
        workflow_id: Optional[str] = None,
    ) -> str:
        """Kill the subprocess, drop the captured UUID. Next ``acquire``
        spawns fresh with no continuity flag (claude assigns a new
        session UUID).

        Stream-json input mode has no in-process slash-command
        equivalent of the TUI's ``/clear`` (the VSCode extension
        source confirms this — context resets are subprocess restarts,
        not slash messages). Emits ``claude.session.cleared`` for FE
        parity with the old PTY-era contract.
        """
        async with session.lock:
            old_uuid = session.current_session_uuid
            # Drop the pool entry and kill the subprocess.
            await self.terminate(session.memory_node_id)
        if old_uuid:
            await self._emit_event(
                "cleared",
                memory_node_id=session.memory_node_id,
                workflow_id=workflow_id,
                old_session_uuid=old_uuid,
                new_session_uuid="",
            )
        return ""

    async def release(self, session: PooledClaudeSession) -> None:
        """Release the context+turn lease and start the idle clock."""
        session.last_used_at = time.monotonic()
        if session.turn_lease.locked():
            session.turn_lease.release()

    async def terminate(self, memory_node_id: str) -> None:
        """Force-drop a specific pooled session."""
        async with self._pool_lock:
            await self._terminate_locked(memory_node_id)

    async def shutdown_all(self) -> None:
        """Terminate every pooled subprocess + stop the reaper.

        Wire into FastAPI's lifespan ``shutdown`` event.
        """
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
        """Write one stream-json user message to ``proc.stdin`` and await
        the next ``result`` event from claude's stdout stream.

        The stdout reader task (started at spawn time) parses each line
        as a stream-json event and dispatches via
        :meth:`_handle_stream_event`. When the final ``result`` event
        lands, ``result_event`` is set and we return.

        Serialised by ``session.lock`` so two concurrent ``send_turn``
        calls can't interleave their stream-json lines on stdin.
        """
        async with session.lock:
            if session.process.returncode is not None:
                return self._build_result_from_events(
                    session=session,
                    events=[],
                    prompt=prompt,
                    success=False,
                    error=(f"claude subprocess exited (code " f"{session.process.returncode}) before turn"),
                )

            session.result_event.clear()
            session.events_this_turn = []

            # Stream-json input shape — the VSCode extension's wire
            # format. Newline-delimited; one JSON object per turn.
            message = {
                "type": "user",
                "message": {"role": "user", "content": prompt},
            }
            payload = (json.dumps(message) + "\n").encode("utf-8")
            try:
                assert session.process.stdin is not None
                session.process.stdin.write(payload)
                await session.process.stdin.drain()
            except (ConnectionError, BrokenPipeError, AttributeError) as exc:
                return self._build_result_from_events(
                    session=session,
                    events=[],
                    prompt=prompt,
                    success=False,
                    error=f"failed to deliver prompt to claude stdin: {exc}",
                )

            try:
                await asyncio.wait_for(
                    session.result_event.wait(),
                    timeout=float(timeout_seconds),
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[ClaudeSessionPool] turn timeout memory_node=%s " "prompt_len=%d",
                    session.memory_node_id,
                    len(prompt),
                )
                return self._build_result_from_events(
                    session=session,
                    events=session.events_this_turn,
                    prompt=prompt,
                    success=False,
                    error=f"timeout after {timeout_seconds}s",
                )

            session.last_used_at = time.monotonic()
            result = self._build_result_from_events(
                session=session,
                events=session.events_this_turn,
                prompt=prompt,
                success=True,
            )
            # Persist the authoritative UUID from the result event so
            # crash-recovery respawns can emit ``--resume <UUID>``.
            if result.session_id:
                session.current_session_uuid = result.session_id

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
    # Spawn internals
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
        connected_skill_names: Optional[List[str]] = None,
        workspace_dir: Optional[Path] = None,
    ) -> PooledClaudeSession:
        """Cold-spawn a new pooled claude subprocess. Caller holds pool_lock."""
        # Materialise connected skills under
        # ``<workspace_dir>/.claude/skills/`` BEFORE spawning so the
        # spawned claude sees them on its first filesystem scan. The
        # workspace dir (per-workflow) is passed via ``--add-dir``
        # (emitted by ``AICliService.run_batch``), and claude scans
        # ``.claude/skills/`` inside every ``--add-dir`` path per
        # code.claude.com/docs/en/skills. This gives us per-workflow
        # isolation — workflow A's wired skills never bleed into
        # workflow B's subprocess even when both spawn with
        # ``cwd=repo_root``. Paired with the conditional ``Skill``
        # entry in ``--allowedTools`` (see ``interactive_argv``) —
        # both fire when ``connected_skill_names`` is non-empty.
        # Falls back to ``cwd`` only when no workspace_dir was
        # provided (defensive — every production caller passes one).
        if connected_skill_names:
            from ._skills import materialise_skills

            target_dir = workspace_dir or cwd
            await materialise_skills(
                target_dir,
                connected_skill_names,
                previous_skill_names=None,  # cold spawn: no prior set
                log_label=f"pool {memory_node_id}",
            )

        # ``include_prompt`` is ignored by the new ``interactive_argv``
        # (stream-json input mode reads the prompt from stdin), but we
        # pass ``False`` to make the intent explicit for any future
        # provider that still honours it.
        argv = self._provider.interactive_argv(
            spec,
            defaults=defaults,
            mcp_endpoint_url=mcp_endpoint_url,
            mcp_bearer_token=mcp_bearer_token,
            connected_tool_names=connected_tool_names,
            connected_skill_names=connected_skill_names,
            include_prompt=False,
        )

        # Subprocess spawn — the VSCode-extension pattern. PIPE on
        # stdin/stdout/stderr; no PTY. The stdout-reader task below
        # parses each stream-json line into an event and dispatches it.
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        session = PooledClaudeSession(
            memory_node_id=memory_node_id,
            process=process,
            cwd=cwd,
        )

        # stdout reader — the single runtime contract in stream-json
        # output mode. Each line is one stream-json event
        # (``system/init``, ``assistant``, ``user``, ``result``,
        # ``system/compact_boundary``, etc.). We dispatch via
        # :meth:`_handle_stream_event` which populates the per-turn
        # buffer and signals ``result_event`` on the final event.
        async def _consume_stdout() -> None:
            assert process.stdout is not None
            try:
                while True:
                    raw = await process.stdout.readline()
                    if not raw:
                        return
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug(
                            "[ClaudeSessionPool] stdout non-JSON line " "memory_node=%s line=%r",
                            memory_node_id,
                            line[:200],
                        )
                        continue
                    try:
                        await self._handle_stream_event(session, event)
                    except Exception as exc:  # pragma: no cover
                        logger.warning(
                            "[ClaudeSessionPool] handler raised " "memory_node=%s exc=%s",
                            memory_node_id,
                            exc,
                        )
            except (asyncio.CancelledError, ConnectionError):
                return
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug(
                    "[ClaudeSessionPool] stdout reader memory_node=%s exc=%s",
                    memory_node_id,
                    exc,
                )

        async def _drain_stderr() -> None:
            assert process.stderr is not None
            try:
                while True:
                    raw = await process.stderr.readline()
                    if not raw:
                        return
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    if line:
                        logger.warning(
                            "[ClaudeSessionPool] claude stderr " "memory_node=%s pid=%s: %s",
                            memory_node_id,
                            process.pid,
                            line,
                        )
            except (asyncio.CancelledError, ConnectionError):
                return
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug(
                    "[ClaudeSessionPool] stderr drain memory_node=%s exc=%s",
                    memory_node_id,
                    exc,
                )

        session.stdout_reader_task = asyncio.create_task(
            _consume_stdout(),
            name=f"ClaudeSessionPool.stdout_reader({memory_node_id})",
        )
        session.stderr_drain_task = asyncio.create_task(
            _drain_stderr(),
            name=f"ClaudeSessionPool.stderr_drain({memory_node_id})",
        )

        return session

    # ------------------------------------------------------------------
    # Stream-json event dispatch
    # ------------------------------------------------------------------

    async def _handle_stream_event(
        self,
        session: PooledClaudeSession,
        event: Dict[str, Any],
    ) -> None:
        """Per-event dispatcher for stream-json lines on ``proc.stdout``.

        Three concerns:
          1. Capture the session UUID from any event that carries one
             (``system/init`` for fresh spawns, ``result`` for the
             authoritative value) so crash-recovery respawns can emit
             ``--resume <UUID>``.
          2. Append to the per-turn buffer + signal ``result_event``
             on the final ``result`` event so ``send_turn`` returns.
          3. Forward ``system/compact_boundary`` events to
             :class:`CompactionService` so the local-threshold path
             doesn't double-fire on claude's native auto-compaction.
        """
        sid = event.get("session_id") or event.get("sessionId")
        if sid and not session.current_session_uuid:
            session.current_session_uuid = sid
        session.events_this_turn.append(event)
        if self._provider.is_final_event(event):
            session.result_event.set()
        if event.get("type") == "system" and event.get("subtype") == "compact_boundary":
            await self._record_native_compaction(session, event)

    @staticmethod
    async def _record_native_compaction(
        session: PooledClaudeSession,
        event: Dict[str, Any],
    ) -> None:
        """Forward a ``system/compact_boundary`` event to
        :class:`CompactionService` so the local-threshold path doesn't
        double-fire on claude's native auto-compaction."""
        try:
            from services.compaction import get_compaction_service
        except Exception:  # pragma: no cover
            return
        svc = get_compaction_service()
        if svc is None:
            return
        metadata = event.get("compact_metadata") or {}
        pre_tokens = int(metadata.get("pre_tokens") or 0)
        try:
            await svc.record(
                session_id=session.memory_node_id,
                node_id=session.memory_node_id,
                provider="claude",
                model="",
                tokens_before=pre_tokens,
                tokens_after=0,
                summary=None,
            )
            logger.info(
                "[ClaudeSessionPool] native compaction recorded " "memory_node=%s pre_tokens=%d trigger=%s",
                session.memory_node_id,
                pre_tokens,
                metadata.get("trigger", "unknown"),
            )
        except Exception as exc:  # pragma: no cover
            logger.debug(
                "[ClaudeSessionPool] compaction.record failed: %s",
                exc,
            )

    def _build_result_from_events(
        self,
        *,
        session: PooledClaudeSession,
        events: List[Dict[str, Any]],
        prompt: str,
        success: bool,
        error: Optional[str] = None,
    ) -> SessionResult:
        """Translate per-turn JSONL events to a :class:`SessionResult`."""
        provider_result = self._provider.event_to_session_result(
            events,
            stderr="",
            exit_code=0 if success else -1,
        )
        final_success = success and provider_result.get("success", True)
        final_error = error or provider_result.get("error")
        return SessionResult(
            task_id=f"pooled_{uuid.uuid4().hex[:8]}",
            session_id=provider_result.get("session_id") or session.current_session_uuid,
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
            )
            or self._provider.canonical_usage(events),
            provider_data=dict(provider_result.get("provider_data") or {}),
            success=final_success,
            error=final_error if not final_success else None,
        )

    # ------------------------------------------------------------------
    # Termination + eviction
    # ------------------------------------------------------------------

    async def _terminate_locked(
        self,
        memory_node_id: str,
        *,
        reason: str = "explicit",
    ) -> None:
        """Caller holds ``self._pool_lock``. Stop the watcher, close
        stdin (graceful exit), wait briefly, then kill if still alive.
        Emits the ``claude.session.terminated`` CloudEvent."""
        session = self._pool.pop(memory_node_id, None)
        if session is None:
            return
        session_uuid = session.current_session_uuid

        # Drop the spawn-time BatchContext: decrement FastMCP tool
        # refcounts (so disconnected tools eventually fall off the
        # registry) and remove the token from ``_active_tokens`` (so a
        # subsequent MCP request with a stale token gets a clean 401
        # instead of resolving to leaked state). Best-effort — a failure
        # here must not block subprocess teardown.
        if session.batch_token:
            try:
                from services.cli_agent.mcp_server import unregister_batch

                unregister_batch(session.batch_token)
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug(
                    "[ClaudeSessionPool] unregister_batch on terminate " "memory_node=%s exc=%s",
                    memory_node_id,
                    exc,
                )

        # 1. Close stdin so claude can exit gracefully.
        process = session.process
        if process.returncode is None and process.stdin is not None:
            try:
                process.stdin.close()
            except (ConnectionError, BrokenPipeError, AttributeError):
                pass

        # 2. Brief grace period for graceful exit.
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=_SHUTDOWN_GRACE)
            except asyncio.TimeoutError:
                pass

        # 3. Escalate to kill if still alive.
        if process.returncode is None:
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass
            except Exception as exc:  # pragma: no cover
                logger.debug(
                    "[ClaudeSessionPool] kill memory_node=%s exc=%s",
                    memory_node_id,
                    exc,
                )

        # 4. Cancel reader/drain tasks (they'll exit on their own when
        # the pipes close, but cancel makes the cleanup deterministic).
        for task in (session.stdout_reader_task, session.stderr_drain_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        logger.info(
            "[ClaudeSessionPool] terminated memory_node=%s pid=%s reason=%s",
            memory_node_id,
            process.pid,
            reason,
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
            (key, sess)
            for key, sess in self._pool.items()
            if not sess.lock.locked() and not sess.turn_lease.locked()
        ]
        if not idle_entries:
            logger.info(
                "[ClaudeSessionPool] all %d entries in-flight; pool over cap",
                len(self._pool),
            )
            return
        oldest_key, _ = min(
            idle_entries,
            key=lambda kv: kv[1].last_used_at,
        )
        logger.info(
            "[ClaudeSessionPool] evicting LRU memory_node=%s",
            oldest_key,
        )
        await self._terminate_locked(oldest_key, reason="evicted")

    # ------------------------------------------------------------------
    # CloudEvent emission helper (unchanged)
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

        Lazy-imports the broadcaster singleton so the pool stays a leaf
        service. Best-effort: a missed UI update must not break the
        agent loop.
        """
        try:
            from services.status_broadcaster import get_status_broadcaster

            broadcaster = get_status_broadcaster()
        except Exception:  # pragma: no cover
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
                        "cache_read_input_tokens",
                        0,
                    ),
                    cache_creation_input_tokens=payload.get(
                        "cache_creation_input_tokens",
                        0,
                    ),
                    duration_ms=payload.get("duration_ms"),
                    num_turns=payload.get("num_turns"),
                    workflow_id=workflow_id,
                )
        except Exception as exc:  # pragma: no cover
            logger.debug(
                "[ClaudeSessionPool] _emit_event(%s) failed: %s",
                kind,
                exc,
            )

    async def _reaper_loop(self) -> None:
        try:
            while not self._shutdown.is_set():
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(),
                        timeout=self._reaper_interval,
                    )
                    return
                except asyncio.TimeoutError:
                    pass
                now = time.monotonic()
                async with self._pool_lock:
                    expired_keys = [
                        key
                        for key, sess in self._pool.items()
                        if not sess.lock.locked()
                        and not sess.turn_lease.locked()
                        and now - sess.last_used_at >= self._idle_ttl
                    ]
                    for key in expired_keys:
                        logger.info(
                            "[ClaudeSessionPool] reaping idle session " "memory_node=%s age=%.1fs",
                            key,
                            now - self._pool[key].last_used_at,
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
