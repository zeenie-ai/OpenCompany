"""One CLI session per task — `BaseProcessSupervisor` subclass.

Each session is bound to:
  - one provider (Claude or Codex)
  - one task spec (`ClaudeTaskSpec` / `CodexTaskSpec`)
  - one git worktree (created in `_pre_spawn`, removed in `cleanup`)
  - one IDE lockfile (written in `_pre_spawn` when the provider supports it)

Inherits `BaseProcessSupervisor`'s locked idempotent start/stop, recursive
`kill_tree`, `terminate_then_kill(5s)` grace, and Windows
`CTRL_BREAK_EVENT` path. We override `_do_start` to wire NDJSON consumers
instead of the parent's generic `drain_stream(logger.info)`.

Sessions are NOT registered in the global supervisor registry — they're
owned by `AICliService.run_batch()` for the lifetime of one batch.

Liveness: relies on `wait_for_completion(timeout_seconds)` as the watchdog
and the per-broadcast Temporal heartbeat fired by every
`update_node_status()` call. We do not run our own per-second heartbeat
loop or write diagnostic dump files.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import anyio
import yaml

from core.logging import get_logger
from services._supervisor.process import BaseProcessSupervisor
from services.cli_agent.lockfile import remove_ide_lockfile, write_ide_lockfile
from services.cli_agent.protocol import AICliProvider, CanonicalUsage, SessionResult
from services.cli_agent.types import BaseAICliTaskSpec

logger = get_logger(__name__)


class AICliSession(BaseProcessSupervisor):
    """One CLI subprocess for one task."""

    pipe_streams = True
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

        # Streaming state
        self._events: List[Dict[str, Any]] = []
        self._stderr_lines: List[str] = []
        self._exit_code: Optional[int] = None
        self._lockfile_path: Optional[Path] = None

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
        return self._provider.headless_argv(
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
        """Create the per-task git worktree and (if supported) write the
        IDE lockfile. Failures abort `_do_start` cleanly via RuntimeError."""
        # 1. git worktree
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
            err = (wt_proc.stderr or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"git worktree add failed: {err}")

        # 2. IDE lockfile (VSCode pattern) — providers that support it
        if self._provider.supports("ide_lockfile") and self._provider.ide_lockfile_dir:
            try:
                self._lockfile_path = write_ide_lockfile(
                    ide_lockfile_dir=self._provider.ide_lockfile_dir,
                    pid=os.getpid(),
                    port=self._mcp_port,
                    token=self._batch_token,
                    workspace_dir=self._worktree_dir,
                    ide_name=self._provider.name,
                )
            except OSError as exc:
                logger.warning(
                    "[%s] IDE lockfile write failed (%s) — continuing without MCP tools",
                    self.label, exc,
                )

        # 3. Materialise connected skills into `<worktree>/.claude/skills/`.
        # Project-scope path auto-discovers because the worktree IS claude's
        # cwd. Best-effort — failures warn but don't abort the spawn.
        if self._connected_skill_names:
            await self._materialise_skills()

    async def _materialise_skills(self) -> None:
        """Write `<worktree>/.claude/skills/<name>/SKILL.md` for each
        connected skill so the spawned claude can invoke them via the
        built-in `Skill` tool. Filesystem skills are copied wholesale
        (preserves `scripts/` + `references/`); DB skills are
        reconstructed from frontmatter."""
        from services.skill_loader import get_skill_loader

        loader = get_skill_loader()
        skills_dir = self._worktree_dir / ".claude" / "skills"
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
    # _do_start: replace parent's drain tasks with NDJSON consumers
    # ------------------------------------------------------------------

    async def _do_start(self) -> None:
        binary = self.binary_path()
        if not binary.exists():
            raise FileNotFoundError(f"{self.label} binary not found at {binary}")

        await self._pre_spawn()

        kwargs: Dict[str, Any] = {
            "cwd": str(self.cwd()),
            "env": self.env(),
            # `claude -p` opens stdin to read piped input; we never pipe
            # anything, so without DEVNULL the CLI waits 3s and prints
            # "Warning: no stdin data received in 3s, proceeding without
            # it." Tools come over MCP, not stdin — close the handle
            # explicitly to skip the warning + the 3s stall.
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if sys.platform == "win32" and self.graceful_shutdown:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        self._proc = await anyio.open_process(self.argv(), **kwargs)
        self._logger.info(
            "[%s] spawned pid=%s task=%s branch=%s",
            self.label, self._proc.pid, self._task_id, self._branch,
        )

        # NDJSON consumers replace the parent's `drain_stream(logger.info)`.
        # We populate `self._drain_tasks` so the parent's `_do_stop`
        # cancels them on stop.
        self._drain_tasks = [
            asyncio.create_task(self._consume_stdout(self._proc.stdout)),
            asyncio.create_task(self._consume_stderr(self._proc.stderr)),
        ]

    # ------------------------------------------------------------------
    # Stream consumers
    # ------------------------------------------------------------------

    async def _consume_stdout(self, stream: Optional[anyio.abc.ByteReceiveStream]) -> None:
        if stream is None:
            return
        buf = b""
        try:
            async for chunk in stream:
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    text = raw.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    event = self._provider.parse_event(text)
                    if event is None:
                        continue
                    self._events.append(event)
                    await self._on_event(event)
            if buf:
                text = buf.decode("utf-8", errors="replace").strip()
                if text:
                    event = self._provider.parse_event(text)
                    if event is not None:
                        self._events.append(event)
                        await self._on_event(event)
        except (anyio.ClosedResourceError, anyio.EndOfStream, asyncio.CancelledError):
            pass
        except Exception as exc:  # pragma: no cover — defensive
            self._logger.debug("[%s] stdout consumer ended: %s", self.label, exc)

    async def _consume_stderr(self, stream: Optional[anyio.abc.ByteReceiveStream]) -> None:
        if stream is None:
            return
        buf = b""
        try:
            async for chunk in stream:
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    text = raw.decode("utf-8", errors="replace").rstrip()
                    if not text:
                        continue
                    self._stderr_lines.append(text)
                    # Mirror to backend log too — without this the spawned
                    # CLI's MCP-discovery / auth-failure / debug output
                    # only reaches the Terminal panel, leaving the
                    # operator log blind during runtime debugging.
                    self._logger.info("[CC-Agent stderr] %s", text)
                    await self._safe_terminal_log(text, level="error")
            if buf:
                text = buf.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._stderr_lines.append(text)
                    self._logger.info("[CC-Agent stderr] %s", text)
                    await self._safe_terminal_log(text, level="error")
        except (anyio.ClosedResourceError, anyio.EndOfStream, asyncio.CancelledError):
            pass
        except Exception as exc:  # pragma: no cover
            self._logger.debug("[%s] stderr consumer ended: %s", self.label, exc)

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

    async def wait_for_completion(self, timeout_seconds: int) -> SessionResult:
        """Wait for the CLI to exit, with hard timeout watchdog."""
        if self._proc is None:
            return self._build_result(
                success=False, error="session never started",
            )

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=timeout_seconds)
            self._exit_code = self._proc.returncode
            return self._build_result(success=(self._exit_code == 0))
        except asyncio.TimeoutError:
            await self.stop()
            return self._build_result(
                success=False,
                error=f"timeout after {timeout_seconds}s",
            )

    async def cleanup(self) -> None:
        """Stop the process and remove the worktree + lockfile."""
        try:
            await self.stop()
        except Exception as exc:
            self._logger.debug("[%s] stop during cleanup: %s", self.label, exc)

        if self._lockfile_path:
            remove_ide_lockfile(self._lockfile_path)
            self._lockfile_path = None

        # Remove the worktree (force, since branch lifecycle is the
        # batch's responsibility — best-effort).
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
        provider_result = self._provider.event_to_session_result(
            self._events,
            "\n".join(self._stderr_lines),
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
