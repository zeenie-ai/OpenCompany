"""One CLI session per task — interactive PTY + on-disk JSONL events.

Each session is bound to:
  - one provider (Claude or Codex)
  - one task spec (`ClaudeTaskSpec` / `CodexTaskSpec`)
  - one git worktree (created in `_pre_spawn`, removed in `cleanup`)
  - one IDE lockfile (written in `_pre_spawn` when the provider supports it)

**Transport / protocol split** (see
``docs-internal/claude_code_interactive_mode.md``):

  - Transport: a PTY (``ptyprocess`` on POSIX / ``pywinpty>=3.0.3`` on
    Windows) keeps ``claude`` alive in TUI mode. The PTY's stdout —
    ANSI-rendered TUI — is intentionally discarded.
  - Protocol: events are read from the on-disk session JSONL at
    ``<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session>.jsonl``,
    same shape ``-p`` used to write to stdout (CHANGELOG 2.1.101 /
    2.1.126 confirms the shared writer).

Inherits from :class:`BaseProcessSupervisor` for its idempotent locked
start/stop machinery but overrides ``_do_start`` / ``_do_stop`` /
``is_running`` to drive a :class:`PtyHandle` + :class:`JsonlWatcher`
pair instead of a plain ``anyio.open_process``. ``self._proc`` stays
``None`` for the whole session lifetime; the PTY handle is the
authoritative process owner.

Sessions are NOT registered in the global supervisor registry — they're
owned by ``AICliService.run_batch()`` for the lifetime of one batch.

Liveness: ``wait_for_completion(timeout_seconds)`` waits for the JSONL
``result`` event (signalled by ``_on_event`` setting an ``asyncio.Event``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import anyio
import yaml

from core.logging import get_logger
from services._supervisor.process import BaseProcessSupervisor
from services.cli_agent.jsonl_watcher import JsonlWatcher, snapshot_jsonl_sizes
from services.cli_agent.lockfile import remove_ide_lockfile, write_ide_lockfile
from services.cli_agent.protocol import AICliProvider, CanonicalUsage, SessionResult
from services.cli_agent.transports import PtyHandle, get_pty_transport
from services.cli_agent.types import BaseAICliTaskSpec

logger = get_logger(__name__)

# Claude derives its project_key from cwd by replacing every char that
# isn't [a-zA-Z0-9.-] with `-`. Verified byte-for-byte against the
# on-disk `~/.claude-machina/projects/` listing in the memory-bridge
# research.
_PROJECT_KEY_RE = re.compile(r"[^a-zA-Z0-9.-]")

# How long to wait for claude to materialise its session JSONL after
# spawn on a first-run (no `--resume`). Five seconds is generous —
# under load the file usually appears within 200 ms.
_FIRST_RUN_JSONL_TIMEOUT = 5.0


class AICliSession(BaseProcessSupervisor):
    """One CLI subprocess for one task."""

    # ``pipe_streams`` was the parent's flag for whether ``_do_start``
    # should pipe stdout/stderr through ``drain_stream`` loggers. We
    # override ``_do_start`` entirely (PTY-based spawn) so it's
    # irrelevant — left as False to match reality.
    pipe_streams = False
    terminate_grace_seconds = 5.0
    graceful_shutdown = sys.platform == "win32"

    def __init__(
        self,
        *,
        provider: AICliProvider,
        task: BaseAICliTaskSpec,
        repo_root: Path,
        workspace_dir: Path,
        node_id: str,
        workflow_id: str,
        broadcaster: Any,
        defaults: Dict[str, Any],
        mcp_port: int,
        batch_token: str,
        connected_tool_names: Optional[List[str]] = None,
        connected_skill_names: Optional[List[str]] = None,
        memory_bound: bool = False,
    ) -> None:
        super().__init__()
        self._provider = provider
        self._task = task
        self._task_id = task.task_id or f"t_{uuid.uuid4().hex[:8]}"
        self._repo_root = Path(repo_root).resolve()
        self._worktree_dir = (
            Path(workspace_dir).resolve() / node_id / f"wt_{self._task_id}"
        )
        self._branch = task.branch or f"machina/{self._task_id}"
        self._broadcaster = broadcaster
        self._defaults = defaults
        self._mcp_port = mcp_port
        self._batch_token = batch_token
        self._node_id = node_id
        self._workflow_id = workflow_id
        # Names of `mcp__machinaos__*` tools to add to `--allowedTools`.
        self._connected_tool_names: List[str] = list(connected_tool_names or [])
        # Skills to materialise into `<worktree>/.claude/skills/<name>/SKILL.md`
        # so claude auto-discovers them per the documented project-scope
        # path (https://code.claude.com/docs/en/skills#where-skills-live).
        self._connected_skill_names: List[str] = list(connected_skill_names or [])
        # Memory-bound runs use ``cwd=repo_root`` so claude's project_key
        # (derived from cwd via `[^a-zA-Z0-9.-] -> -`) stays stable
        # across spawns. With a stable project_key, ``--resume <UUID>``
        # finds the prior session JSONL claude wrote on its previous
        # turn under ``<CLAUDE_CONFIG_DIR>/projects/<key>/<UUID>.jsonl``.
        # The per-task git worktree (random `wt_t_<rand>` suffix) is
        # incompatible with this — every spawn would land under a
        # brand-new project_key with no prior JSONL.
        self._memory_bound: bool = bool(memory_bound)

        # Streaming state
        self._events: List[Dict[str, Any]] = []
        self._exit_code: Optional[int] = None
        self._lockfile_path: Optional[Path] = None

        # PTY + JSONL state — set in `_do_start`, cleared in `_do_stop`.
        self._pty_handle: Optional[PtyHandle] = None
        self._jsonl_watcher: Optional[JsonlWatcher] = None
        self._session_jsonl_path: Optional[Path] = None
        # Signalled by `_on_event` when claude writes a `result` event;
        # `wait_for_completion` awaits this.
        self._result_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def label(self) -> str:
        return f"AICliSession_{self._provider.name}_{self._task_id}"

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def branch(self) -> str:
        return self._branch

    @property
    def worktree_dir(self) -> Path:
        return self._worktree_dir

    # ------------------------------------------------------------------
    # BaseProcessSupervisor surface
    # ------------------------------------------------------------------

    def binary_path(self) -> Path:
        return self._provider.binary_path()

    def argv(self) -> List[str]:
        return self._provider.interactive_argv(
            self._task,
            defaults=self._defaults,
            mcp_endpoint_url=self._mcp_endpoint_url(),
            mcp_bearer_token=self._batch_token,
            connected_tool_names=self._connected_tool_names,
        )

    def _mcp_endpoint_url(self) -> str:
        """Absolute URL of MachinaOs's FastMCP JSON-RPC endpoint.

        Mirrors the ``url`` written into the IDE lockfile. FastMCP serves
        at ``/mcp`` of the sub-app; ``main.py`` mounts it at ``/mcp/ide``;
        the JSON-RPC URL is therefore ``/mcp/ide/mcp``."""
        return f"http://127.0.0.1:{self._mcp_port}/mcp/ide/mcp"

    def cwd(self) -> Optional[Path]:
        # Memory-bound runs spawn directly under repo_root so claude's
        # project_key stays constant and `--resume` finds the prior
        # JSONL across runs. Non-memory runs use the per-task worktree.
        if self._memory_bound:
            return self._repo_root
        return self._worktree_dir

    def env(self) -> Dict[str, str]:
        # Inherit parent env, then redirect provider config to MachinaOs's
        # project-local isolation dir for providers that support it (Claude
        # via CLAUDE_CONFIG_DIR; Codex/Gemini still use their native auth
        # paths until isolation is wired). Without this, the agent's
        # spawned CLI reads the user's personal `~/.claude/` credentials
        # instead of the ones the credentials modal's Login button wrote.
        e: Dict[str, str] = {**os.environ, "PYTHONUNBUFFERED": "1"}
        if self._provider.name == "claude":
            from services.claude_oauth import MACHINA_CLAUDE_DIR
            e["CLAUDE_CONFIG_DIR"] = str(MACHINA_CLAUDE_DIR)
        if self._lockfile_path and self._provider.ide_lock_env_var:
            e[self._provider.ide_lock_env_var] = str(self._lockfile_path)
        # Composio-style parent-run-ID for MCP correlation
        e["MACHINA_PARENT_RUN_ID"] = (
            f"{self._workflow_id}:{self._node_id}:{self._batch_token[:8]}"
        )
        return e

    async def _pre_spawn(self) -> None:
        """Create the per-task git worktree (non-memory-bound runs only)
        and (if supported) write the IDE lockfile. Failures abort
        `_do_start` cleanly via RuntimeError."""
        # 1. Per-task git worktree — skipped for memory-bound runs
        # which use cwd=repo_root to keep claude's project_key stable.
        if not self._memory_bound:
            self._worktree_dir.parent.mkdir(parents=True, exist_ok=True)
            wt_proc = await anyio.run_process(
                [
                    "git", "-C", str(self._repo_root),
                    "worktree", "add",
                    str(self._worktree_dir),
                    "-b", self._branch,
                ],
                check=False,
            )
            if wt_proc.returncode != 0:
                err = (wt_proc.stderr or b"").decode(
                    "utf-8", errors="replace",
                ).strip()
                raise RuntimeError(f"git worktree add failed: {err}")
        else:
            logger.info(
                "[%s] memory-bound: skipping worktree, using cwd=%s",
                self.label, self._repo_root,
            )

        # 2. IDE lockfile (VSCode pattern) — providers that support it.
        # `workspace_dir` in the lockfile points at whatever cwd will
        # actually be — repo_root for memory-bound runs, else worktree.
        lockfile_workspace = (
            self._repo_root if self._memory_bound else self._worktree_dir
        )
        if self._provider.supports("ide_lockfile") and self._provider.ide_lockfile_dir:
            try:
                self._lockfile_path = write_ide_lockfile(
                    ide_lockfile_dir=self._provider.ide_lockfile_dir,
                    pid=os.getpid(),
                    port=self._mcp_port,
                    token=self._batch_token,
                    workspace_dir=lockfile_workspace,
                    ide_name=self._provider.name,
                )
            except OSError as exc:
                logger.warning(
                    "[%s] IDE lockfile write failed (%s) — continuing without MCP tools",
                    self.label, exc,
                )

        # 3. Materialise connected skills under cwd's `.claude/skills/`.
        # cwd is repo_root for memory-bound, worktree otherwise — both
        # are project-scope per the spec.
        if self._connected_skill_names:
            await self._materialise_skills()

    async def _materialise_skills(self) -> None:
        """Write `<worktree>/.claude/skills/<name>/SKILL.md` for each
        connected skill so the spawned claude can invoke them via the
        built-in `Skill` tool. Filesystem skills are copied wholesale
        (preserves `scripts/` + `references/`); DB skills are
        reconstructed from frontmatter.

        Only ``AICliSession`` (the non-pooled, one-shot agent path)
        calls this. ``ClaudeSessionPool`` (the pooled, memory-bound
        path) leaves skills accessible via the FastMCP server's
        ``listSkills``/``getSkill`` tools instead. Switching the pool
        to the standard ``--add-dir`` skill discovery is tracked
        separately (requires restructuring ``server/skills/`` to the
        Agent Skills `<root>/.claude/skills/<name>/SKILL.md` shape).
        """
        from services.skill_loader import get_skill_loader

        loader = get_skill_loader()
        # cwd-relative — repo_root for memory-bound runs, worktree otherwise.
        skills_dir = self.cwd() / ".claude" / "skills"
        try:
            skills_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "[%s] cannot create skills dir %s: %s — skipping",
                self.label, skills_dir, exc,
            )
            return

        for name in self._connected_skill_names:
            try:
                skill = await loader.load_skill_async(name)
            except Exception as exc:
                logger.warning(
                    "[%s] load_skill_async(%r) failed: %s",
                    self.label, name, exc,
                )
                continue
            if skill is None:
                logger.warning(
                    "[%s] skill %r not found — skipping materialisation",
                    self.label, name,
                )
                continue

            dest = skills_dir / name
            try:
                if skill.metadata.path is not None:
                    # Filesystem skill: copy whole directory tree.
                    shutil.copytree(
                        skill.metadata.path, dest, dirs_exist_ok=True,
                    )
                else:
                    # DB skill: reconstruct frontmatter + body.
                    dest.mkdir(parents=True, exist_ok=True)
                    frontmatter = {
                        "name": skill.metadata.name,
                        "description": skill.metadata.description,
                        "allowed-tools": " ".join(skill.metadata.allowed_tools),
                        "metadata": skill.metadata.metadata,
                    }
                    body = (
                        f"---\n"
                        f"{yaml.safe_dump(frontmatter, sort_keys=False)}"
                        f"---\n\n"
                        f"{skill.instructions}"
                    )
                    (dest / "SKILL.md").write_text(body, encoding="utf-8")
                logger.info(
                    "[CC-Agent _pre_spawn] materialised skill %r -> %s",
                    name, dest,
                )
            except OSError as exc:
                logger.warning(
                    "[%s] failed to materialise skill %r at %s: %s",
                    self.label, name, dest, exc,
                )

    # ------------------------------------------------------------------
    # _do_start: PTY spawn + JSONL watcher (interactive cutover)
    # ------------------------------------------------------------------

    async def _do_start(self) -> None:
        binary = self.binary_path()
        if not binary.exists():
            raise FileNotFoundError(f"{self.label} binary not found at {binary}")

        await self._pre_spawn()

        # The new path is claude-specific. Codex / Gemini's automation
        # surface is `codex exec --json` / `gemini --output-format
        # stream-json` — both write events to stdout, not disk — and
        # need the legacy spawn path. For now claude is the only
        # provider actually wired to a node plugin; once codex_agent
        # ships, that branch grows here. We raise loudly if a non-
        # claude provider tries to use this session class so the gap
        # isn't silent.
        if self._provider.name != "claude":
            raise RuntimeError(
                f"AICliSession (interactive PTY+JSONL) currently only "
                f"supports the 'claude' provider; got "
                f"{self._provider.name!r}. Codex / Gemini still use the "
                f"stream-json-on-stdout path and need a separate session "
                f"class — track via codex_agent plugin work."
            )

        # Where claude will write its session JSONL. The project_key is
        # derived from cwd by the algorithm verified in the memory
        # bridge research (every non-`[a-zA-Z0-9.-]` char → `-`).
        project_dir = self._project_dir()
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._logger.warning(
                "[%s] could not create project dir %s: %s",
                self.label, project_dir, exc,
            )

        resume_uuid = getattr(self._task, "resume_session_id", None)
        if resume_uuid:
            # Resume runs: known location. Claude opens this file and
            # appends to it on each turn.
            self._session_jsonl_path = project_dir / f"{resume_uuid}.jsonl"

        # Snapshot every existing JSONL's size BEFORE spawn so the post-spawn
        # locator can detect "claude wrote here" via deterministic size-diff —
        # works uniformly for fresh spawns (new filename) AND ``--continue``
        # spawns (existing file grows). Size is monotonic and filesystem-
        # portable; mtime / wall-clock comparisons are not.
        baseline_sizes = snapshot_jsonl_sizes(project_dir)
        transport = get_pty_transport()
        argv = self.argv()
        try:
            self._pty_handle = await transport.spawn(
                argv,
                cwd=self.cwd() or self._repo_root,
                env=self.env(),
            )
        except FileNotFoundError:
            raise
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(f"PTY spawn failed: {exc}") from exc

        self._logger.info(
            "[%s] spawned pid=%s task=%s branch=%s cwd=%s",
            self.label, self._pty_handle.pid, self._task_id,
            self._branch, self.cwd(),
        )

        # Resolve the JSONL path. For known-UUID `--resume` runs the
        # path is already known; otherwise (fresh OR `--continue`)
        # discover via size-diff against the pre-spawn baseline: claude
        # either creates a new file (fresh) or appends an init event to
        # the existing latest file (--continue). Either way, the first
        # ``.jsonl`` whose size > baseline is the right file.
        if self._session_jsonl_path is None:
            try:
                self._session_jsonl_path = await self._wait_for_session_jsonl(
                    project_dir, baseline_sizes,
                )
            except TimeoutError as exc:
                # Surface a clean error envelope rather than letting the
                # spawn limp on with no event source.
                raise RuntimeError(str(exc)) from exc

        self._logger.info(
            "[%s] watching session JSONL: %s",
            self.label, self._session_jsonl_path,
        )

        # Start the JSONL tail-f. Events flow into `_on_event` which
        # also signals `self._result_event` when a `result` line lands.
        # `start_from_end=False` so the watcher catches the `system/init`
        # event claude writes immediately on startup (it's already on
        # disk by the time we start watching).
        self._jsonl_watcher = JsonlWatcher(
            self._session_jsonl_path,
            on_event=self._on_jsonl_event,
            start_from_end=False,
        )
        await self._jsonl_watcher.start()

    # ------------------------------------------------------------------
    # JSONL location helpers
    # ------------------------------------------------------------------

    def _project_dir(self) -> Path:
        """Return the dir where claude writes its session JSONLs for
        our cwd. ``<MACHINA_CLAUDE_DIR>/projects/<project_key>/``.

        Claude derives ``project_key`` by replacing every non-
        ``[a-zA-Z0-9.-]`` char in ``str(cwd)`` with ``-``. Verified
        byte-for-byte against the on-disk
        ``data/claude-machina/projects/`` listing in the memory-bridge
        research.
        """
        from services.claude_oauth import MACHINA_CLAUDE_DIR

        cwd = self.cwd() or self._repo_root
        project_key = _PROJECT_KEY_RE.sub("-", str(cwd))
        return Path(MACHINA_CLAUDE_DIR) / "projects" / project_key

    async def _wait_for_session_jsonl(
        self, project_dir: Path, baseline: Dict[str, int],
    ) -> Path:
        """Find the JSONL claude is using post-spawn via size-diff vs baseline.

        ``baseline`` is the ``{name: size}`` snapshot taken BEFORE the
        spawn. After spawn we poll for the first ``.jsonl`` that's
        either new (not in baseline) or grown (size > baseline size).
        Handles both fresh spawns (new filename) and ``--continue``
        spawns (existing file grows). File size is a monotonic,
        filesystem-portable signal — no wall-clock vs monotonic clock
        comparison, no mtime-resolution races.

        Raises :class:`TimeoutError` after
        :data:`_FIRST_RUN_JSONL_TIMEOUT` seconds — surfaces as a clean
        spawn failure rather than a silently dead session.
        """
        deadline_mono = time.monotonic() + _FIRST_RUN_JSONL_TIMEOUT
        while time.monotonic() < deadline_mono:
            current = snapshot_jsonl_sizes(project_dir)
            for name, size in current.items():
                if size > baseline.get(name, 0):
                    return project_dir / name
            await asyncio.sleep(0.1)
        raise TimeoutError(
            f"No session JSONL appeared or grew under {project_dir} "
            f"within {_FIRST_RUN_JSONL_TIMEOUT}s — claude may have "
            f"failed to start or the CLAUDE_CONFIG_DIR is misconfigured."
        )

    async def _on_jsonl_event(self, event: Dict[str, Any]) -> None:
        """JsonlWatcher callback: append + dispatch + result-signal +
        compaction-record.

        Wraps the existing :meth:`_on_event` so the events list is
        updated centrally, the result-event Asyncio primitive fires
        when claude writes a `result` line, and native compactions
        (``system/compact_boundary``) flow into the shared
        :class:`CompactionService` so the local-threshold path doesn't
        also fire.
        """
        self._events.append(event)
        await self._on_event(event)
        if self._provider.is_final_event(event):
            self._result_event.set()
        if (
            event.get("type") == "system"
            and event.get("subtype") == "compact_boundary"
        ):
            await self._record_native_compaction(event)

    async def _record_native_compaction(self, event: Dict[str, Any]) -> None:
        """Forward a native ``/compact`` event into ``CompactionService``.

        Avoids double-compaction: without this, the local-threshold
        path in ``services/ai.py:_track_token_usage`` and claude's own
        auto-compaction would both trigger and the UI's token gauge
        would race against claude's internal state.
        """
        try:
            from services.compaction import get_compaction_service
        except Exception:  # pragma: no cover — defensive
            return
        svc = get_compaction_service()
        if svc is None:
            return

        metadata = event.get("compact_metadata") or {}
        pre_tokens = int(metadata.get("pre_tokens") or 0)
        # Session-id for the compaction store: prefer the memory bridge
        # session_id (stable across `--resume`), fall back to the
        # current task's resume_session_id or the node id.
        session_id = (
            getattr(self._task, "resume_session_id", None)
            or getattr(self._task, "session_id", None)
            or self._node_id
        )
        model = getattr(self._task, "model", "") or ""
        try:
            await svc.record(
                session_id=session_id,
                node_id=self._node_id,
                provider=self._provider.name,
                model=model,
                tokens_before=pre_tokens,
                tokens_after=0,
                summary=None,
            )
            self._logger.info(
                "[%s] native compaction recorded pre_tokens=%d trigger=%s",
                self.label, pre_tokens, metadata.get("trigger", "unknown"),
            )
        except Exception as exc:  # pragma: no cover — best-effort
            self._logger.debug(
                "[%s] compaction.record failed: %s", self.label, exc,
            )

    # ------------------------------------------------------------------
    # Event dispatch (UI broadcasts)
    # ------------------------------------------------------------------

    async def _on_event(self, event: Dict[str, Any]) -> None:
        # Tag every interesting event in the backend log so the operator
        # can see what claude is actually doing — tool calls, assistant
        # text, hook events. Without this the stream is invisible from
        # the backend (only the Terminal panel saw it).
        self._log_event_summary(event)

        if self._provider.is_final_event(event):
            payload = {
                "phase": "ai_cli_subtask",
                "task_id": self._task_id,
                "provider": self._provider.name,
                "status": "finalising",
            }
            for k in ("total_cost_usd", "duration_ms", "num_turns", "session_id"):
                v = event.get(k)
                if v is not None:
                    payload[k] = v
            await self._safe_node_status("executing", payload)
        else:
            msg = (
                event.get("message")
                or event.get("text")
                or event.get("delta")
                or json.dumps(event)
            )
            text = msg if isinstance(msg, str) else json.dumps(msg)
            await self._safe_terminal_log(text[:500], level="info")

    def _log_event_summary(self, event: Dict[str, Any]) -> None:
        """One-line summary per claude stream event. Picks out the event
        types that matter for tool-call debugging."""
        etype = event.get("type", "?")
        try:
            if etype == "system" and event.get("subtype") == "init":
                tools = event.get("tools") or []
                mcp_servers = event.get("mcp_servers") or []
                self._logger.info(
                    "[CC-Agent stream] system.init tools=%d (sample=%s) "
                    "mcp_servers=%s",
                    len(tools), tools[:8],
                    [s.get("name") for s in mcp_servers if isinstance(s, dict)],
                )
            elif etype == "assistant":
                msg = event.get("message") or {}
                content = msg.get("content") or []
                tool_uses = [
                    c for c in content
                    if isinstance(c, dict) and c.get("type") == "tool_use"
                ]
                texts = [
                    c.get("text", "") for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                ]
                if tool_uses:
                    for tu in tool_uses:
                        self._logger.info(
                            "[CC-Agent stream] assistant->tool_use name=%s "
                            "input_keys=%s",
                            tu.get("name"),
                            list((tu.get("input") or {}).keys()),
                        )
                elif texts:
                    sample = " ".join(t for t in texts if isinstance(t, str))[:300]
                    self._logger.info(
                        "[CC-Agent stream] assistant.text: %r", sample,
                    )
            elif etype == "tool_use":
                self._logger.info(
                    "[CC-Agent stream] tool_use name=%s input_keys=%s",
                    event.get("name"),
                    list((event.get("input") or {}).keys()),
                )
            elif etype == "tool_result":
                content = event.get("content") or ""
                preview = content if isinstance(content, str) else json.dumps(content)
                self._logger.info(
                    "[CC-Agent stream] tool_result is_error=%s content=%r",
                    event.get("is_error", False), preview[:300],
                )
            elif etype == "hook":
                self._logger.info(
                    "[CC-Agent stream] hook %s",
                    event.get("hook_event_name") or event.get("subtype") or "?",
                )
            elif etype == "result":
                self._logger.info(
                    "[CC-Agent stream] result is_error=%s subtype=%s "
                    "session_id=%s duration_ms=%s num_turns=%s cost=%s",
                    event.get("is_error", False), event.get("subtype"),
                    event.get("session_id"),
                    event.get("duration_ms"), event.get("num_turns"),
                    event.get("total_cost_usd"),
                )
        except Exception:
            self._logger.debug("[CC-Agent stream] log-summary failed for event")

    async def _safe_terminal_log(self, message: str, *, level: str) -> None:
        if not self._broadcaster:
            return
        try:
            await self._broadcaster.broadcast_terminal_log({
                "source": f"{self._provider.name}:{self._task_id}",
                "level": level,
                "message": message,
            })
        except Exception:
            pass

    async def _safe_node_status(self, status: str, data: Dict[str, Any]) -> None:
        if not self._broadcaster:
            return
        try:
            await self._broadcaster.update_node_status(
                self._node_id, status, data,
                workflow_id=self._workflow_id,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Override BaseProcessSupervisor: we own the process via the
        PTY handle, not ``self._proc``."""
        return self._pty_handle is not None and self._pty_handle.is_alive()

    async def _do_stop(self) -> None:
        """Override BaseProcessSupervisor: stop the JSONL watcher and
        kill the PTY child. Idempotent — clearing both refs prevents a
        double-stop from racing the kill-cascade."""
        watcher = self._jsonl_watcher
        self._jsonl_watcher = None
        if watcher is not None:
            try:
                await watcher.stop()
            except Exception as exc:  # pragma: no cover — defensive
                self._logger.debug(
                    "[%s] watcher stop: %s", self.label, exc,
                )

        handle = self._pty_handle
        self._pty_handle = None
        if handle is not None:
            pid = handle.pid
            try:
                await handle.kill()
            except Exception as exc:  # pragma: no cover — defensive
                self._logger.debug(
                    "[%s] PTY kill: %s", self.label, exc,
                )
            self._logger.info("[%s] stopped pid=%s", self.label, pid)

    async def wait_for_completion(self, timeout_seconds: int) -> SessionResult:
        """Wait for the JSONL ``result`` event with hard timeout.

        In interactive mode the PTY process doesn't exit between turns
        — claude stays alive waiting for the next user input. The
        protocol contract is the ``result`` event on the JSONL: when
        that lands, the turn is complete and we can collect the
        outcome.

        Hard timeout: matches the old stdout-driven behaviour. If the
        result event doesn't arrive within ``timeout_seconds``, we
        force-stop the PTY and surface a timeout error.
        """
        if self._pty_handle is None:
            return self._build_result(
                success=False, error="session never started",
            )

        try:
            await asyncio.wait_for(
                self._result_event.wait(), timeout=timeout_seconds,
            )
            # The result event fired; claude wrote `result` on the JSONL.
            # The subprocess is still alive (interactive TUI stays
            # running) — we treat the turn as exit-code 0 since claude
            # didn't crash.
            self._exit_code = 0
            return self._build_result(success=True)
        except asyncio.TimeoutError:
            await self.stop()
            self._exit_code = -1
            return self._build_result(
                success=False,
                error=f"timeout after {timeout_seconds}s",
            )

    async def cleanup(self) -> None:
        """Stop the watcher + kill the PTY child and remove the
        worktree + lockfile."""
        try:
            await self.stop()
        except Exception as exc:
            self._logger.debug("[%s] stop during cleanup: %s", self.label, exc)

        if self._lockfile_path:
            remove_ide_lockfile(self._lockfile_path)
            self._lockfile_path = None

        # Remove the per-task worktree (only created for non-memory
        # runs; memory-bound spawns ran directly under repo_root).
        if not self._memory_bound:
            try:
                await anyio.run_process(
                    [
                        "git", "-C", str(self._repo_root),
                        "worktree", "remove", "--force",
                        str(self._worktree_dir),
                    ],
                    check=False,
                )
            except Exception as exc:
                self._logger.debug("[%s] worktree remove: %s", self.label, exc)

    # ------------------------------------------------------------------
    # Result construction
    # ------------------------------------------------------------------

    def _build_result(
        self,
        *,
        success: bool,
        error: Optional[str] = None,
    ) -> SessionResult:
        # PTY merges stdout + stderr onto one stream; we never read it
        # (the JSONL on disk is the protocol surface). Pass empty
        # stderr — the provider's event_to_session_result reconstructs
        # error context from JSONL `result.subtype == "error"` events.
        provider_result = self._provider.event_to_session_result(
            self._events,
            "",
            self._exit_code if self._exit_code is not None else -1,
        )

        canonical = provider_result.get("canonical_usage")
        if not isinstance(canonical, CanonicalUsage):
            canonical = self._provider.canonical_usage(self._events)

        final_success = success and provider_result.get("success", True)
        final_error = error or provider_result.get("error")

        return SessionResult(
            task_id=self._task_id,
            session_id=provider_result.get("session_id"),
            provider=self._provider.name,
            prompt=self._task.prompt,
            branch=self._branch,
            worktree_path=str(self._worktree_dir),
            response=str(provider_result.get("response") or "")[:4000],
            cost_usd=provider_result.get("cost_usd"),
            duration_ms=provider_result.get("duration_ms"),
            num_turns=provider_result.get("num_turns"),
            tool_calls=int(provider_result.get("tool_calls", 0)),
            canonical_usage=canonical,
            provider_data=dict(provider_result.get("provider_data") or {}),
            success=final_success,
            error=final_error if not final_success else None,
        )
