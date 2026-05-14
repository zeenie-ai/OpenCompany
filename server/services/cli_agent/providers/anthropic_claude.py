"""Anthropic Claude Code CLI provider — interactive TUI mode.

Reference implementation for the `AICliProvider` Protocol. MachinaOs
drives the same interactive `claude` invocation a normal user runs at
their terminal — not `claude -p` headless. The PTY keeps the process
alive in TUI mode while we read events from the on-disk session JSONL
at ``<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session>.jsonl``
instead of from stdout. See ``docs-internal/claude_code_interactive_mode.md``.

Subprocess: ``claude --permission-mode bypassPermissions
--allowedTools <list> [--model ...] [--append-system-prompt ...]
[--mcp-config ...] [--strict-mcp-config] [--resume <UUID>]
[--effort ...] [--add-dir ...] [--disallowedTools ...] [--agent ...]
-- "<prompt>"``

**Tools + skills are preserved**:
  - ``--mcp-config`` registers MachinaOs's FastMCP server; the spawned
    `claude` discovers `mcp__machinaos__*` tools via `tools/list`.
  - ``--allowedTools`` carries the explicit allowlist (built-ins +
    every wired MCP tool) — same shape as the prior headless path.
  - Skills are still materialised under ``<cwd>/.claude/skills/`` by
    ``AICliSession._materialise_skills`` (unchanged).

**Permission mode**: ``bypassPermissions`` lets every allowlist entry
fire without a TUI prompt — non-interactive automation has no human at
the keyboard to click "Allow." Behaviorally equivalent to
``--dangerously-skip-permissions`` but uses the documented permission
mode (``code.claude.com/docs/en/permission-modes``) and is the same
flag a Composio AO "permissionless" launch sets.

Flags dropped in the interactive cutover (no longer emitted; the
on-disk JSONL gives us the same data without `-p`):
``-p / --print``, ``--output-format``, ``--verbose``,
``--include-partial-messages``, ``--include-hook-events``,
``--max-turns``, ``--max-budget-usd``, ``--session-id``,
``--fallback-model``. ``--max-budget-usd`` / ``--max-turns`` become
external monitors (Phase 2).

Binary + auth: shared with the auth surface via
``services.claude_oauth.claude_binary_path()`` — single project-local
install at ``<repo>/data/claude-machina/npm/`` and ``CLAUDE_CONFIG_DIR``
set on the spawn env so the agent picks up the same credentials the
Login button wrote.

Final event (parsed off disk by ``JsonlWatcher``): ``type == "result"``
carries ``total_cost_usd``, ``duration_ms``, ``num_turns``,
``session_id``, and the assistant's ``result`` string — same shape `-p`
used to write to stdout (Claude Code CHANGELOG 2.1.101 / 2.1.126
confirms the shared JSONL writer).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging import get_logger

from services.claude_oauth import claude_binary_path
from services.cli_agent.config import get_provider_config
from services.cli_agent.protocol import CanonicalUsage
from services.cli_agent.types import ClaudeTaskSpec

logger = get_logger(__name__)

NAME = "claude"


class AnthropicClaudeProvider:
    """`AICliProvider` for Anthropic's Claude Code CLI."""

    def __init__(self) -> None:
        cfg = get_provider_config(NAME)
        if cfg is None:
            raise RuntimeError(
                f"Provider config missing for {NAME!r}. Check ai_cli_providers.json."
            )
        self.name = NAME
        self.package_name = cfg.package_name
        self.binary_name = cfg.binary_name
        self.ide_lock_env_var = cfg.ide_lock_env_var
        self.ide_lockfile_dir = cfg.ide_lockfile_dir
        self._defaults = cfg.defaults
        self._supports = cfg.supports
        self._login_argv = cfg.login_argv
        self._auth_status_argv = cfg.auth_status_argv

    # ---- spawn surface ---------------------------------------------------

    def binary_path(self) -> Path:
        """Resolve the project-local `claude` binary.

        Delegates to ``services.claude_oauth.claude_binary_path`` — same
        path used by the credentials Login button. Lazy-installs into
        ``<repo>/data/claude-machina/npm/`` on first miss. Raises
        ``FileNotFoundError`` if ``npm`` isn't on PATH.
        """
        return Path(claude_binary_path())

    def interactive_argv(
        self,
        task: Any,  # ClaudeTaskSpec
        *,
        defaults: Dict[str, Any],
        mcp_endpoint_url: Optional[str] = None,
        mcp_bearer_token: Optional[str] = None,
        connected_tool_names: Optional[List[str]] = None,
        include_prompt: bool = True,
    ) -> List[str]:
        """Build the full argv (binary + flags) for one interactive task.

        Emits the same invocation a human user runs (``claude ... --
        "<prompt>"``) rather than ``claude -p``. The TUI is held alive
        by a PTY; events are read from the on-disk JSONL at
        ``<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session>.jsonl``.

        ``mcp_endpoint_url`` + ``mcp_bearer_token`` (if both set) are
        emitted as a ``--mcp-config <json>`` block so the spawned
        ``claude`` registers MachinaOs's FastMCP server (works
        identically in interactive + headless modes per
        https://code.claude.com/docs/en/mcp).

        Tools and skills are unchanged from the prior headless path —
        same ``--allowedTools`` list (built-ins + every wired
        ``mcp__machinaos__*``), same skill materialisation under
        ``<cwd>/.claude/skills/`` (in ``AICliSession._materialise_skills``).
        The permission flag flips from ``acceptEdits`` to
        ``bypassPermissions`` so non-Edit tools in the allowlist fire
        without prompting the TUI (no human to click "Allow").

        ``include_prompt=False`` is used by the session pool when
        respawning a fresh process whose first prompt will be written
        to the PTY rather than passed on argv (e.g. after a `/clear`).
        """
        if not isinstance(task, ClaudeTaskSpec):
            raise TypeError(
                "AnthropicClaudeProvider.interactive_argv requires ClaudeTaskSpec, "
                f"got {type(task).__name__}"
            )

        argv: List[str] = [str(self.binary_path())]

        # MCP server registration — same shape as the prior headless
        # path; the Claude Code MCP doc's ``mcp.json`` example. Works
        # identically in interactive mode.
        if mcp_endpoint_url and mcp_bearer_token:
            # `alwaysLoad: true` opts this server out of MCP tool-search
            # deferral so all `mcp__machinaos__*` tools enter context at
            # session start instead of waiting for a `ToolSearch` call
            # the agent often doesn't make
            # (https://code.claude.com/docs/en/mcp#scale-with-mcp-tool-search).
            mcp_payload = json.dumps({
                "mcpServers": {
                    "machinaos": {
                        "type": "http",
                        "url": mcp_endpoint_url,
                        "headers": {
                            "Authorization": f"Bearer {mcp_bearer_token}",
                        },
                        "alwaysLoad": True,
                    }
                }
            })
            argv += ["--mcp-config", mcp_payload, "--strict-mcp-config"]

        # Model
        model = (
            task.model
            or defaults.get("default_model")
            or self._defaults.get("default_model", "claude-sonnet-4-6")
        )
        argv += ["--model", model]

        # Session continuity. Three valid states, mutually exclusive:
        #   - ``resume_session_id`` set → ``--resume <UUID>`` (explicit;
        #     used by ``--fork-session`` UI + any caller that already
        #     knows the exact UUID).
        #   - ``continue_session=True`` → ``--continue`` (claude
        #     auto-loads the latest conversation under the current
        #     cwd, per code.claude.com/docs/en/cli-reference). The
        #     cleaner default for memory-bound runs because it avoids
        #     ferrying a UUID through the memory node's params — claude
        #     tracks its own sessions on disk under
        #     ``<CLAUDE_CONFIG_DIR>/projects/<project_key>/`` and
        #     ``--continue`` picks the newest.
        #   - Neither → no flag (fresh session; claude assigns a new
        #     UUID which the post-spawn JSONL locator discovers).
        # ``--session-id`` is intentionally NOT emitted in interactive
        # mode — the CLI rejects it.
        if task.resume_session_id:
            argv += ["--resume", task.resume_session_id]
        elif task.continue_session:
            argv += ["--continue"]

        # Allowed tools — built-in defaults plus every workflow tool we
        # exposed via the per-batch FastMCP bridge. Same shape as the
        # prior headless argv. With ``--permission-mode bypassPermissions``
        # (below) the allowlist becomes documentation-of-intent rather
        # than an active gate, but we keep it explicit so the surface is
        # auditable. Default fallback:
        #   - Read,Edit,Bash,Glob,Grep,Write — filesystem + shell escape hatches
        #   - Skill — invoke materialised `.claude/skills/<name>/SKILL.md`
        #   - WebSearch,WebFetch — escape hatches when no MCP tool matches
        # `ToolSearch` is intentionally NOT here: it's permission-free per
        # https://code.claude.com/docs/en/tools-reference#built-in-tools.
        allowed = task.allowed_tools or defaults.get(
            "default_allowed_tools",
            self._defaults.get(
                "default_allowed_tools",
                "Read,Edit,Bash,Glob,Grep,Write,Skill,WebSearch,WebFetch",
            ),
        )
        allowed_list: List[str] = (
            [t.strip() for t in allowed.split(",") if t.strip()]
            if allowed else []
        )
        if connected_tool_names:
            allowed_list += [
                f"mcp__machinaos__{name}" for name in connected_tool_names
            ]
        # Always permit MachinaOs's built-in MCP tools so the agent can
        # discover skills + read workspace files without prompting.
        allowed_list += [
            "mcp__machinaos__getWorkspaceFiles",
            "mcp__machinaos__listSkills",
            "mcp__machinaos__getSkill",
            "mcp__machinaos__getCredential",
            "mcp__machinaos__broadcastLog",
        ]
        if allowed_list:
            argv += ["--allowedTools", ",".join(allowed_list)]

        # Permission mode — ``bypassPermissions`` for non-interactive
        # automation. ``acceptEdits`` (the prior default) auto-permits
        # Edit-class tools but prompts for everything else, which would
        # hang the PTY since no human is present to approve.
        # ``bypassPermissions`` is one of the documented permission
        # modes (https://code.claude.com/docs/en/permission-modes) —
        # behaviourally equivalent to ``--dangerously-skip-permissions``
        # but uses the documented mode enum. Per-task override still
        # honoured if the user explicitly wants ``acceptEdits``.
        perm = task.permission_mode or defaults.get(
            "default_permission_mode",
            self._defaults.get("default_permission_mode", "bypassPermissions"),
        )
        if perm:
            argv += ["--permission-mode", perm]

        # System prompt — appended to Claude Code's built-in system prompt
        if task.system_prompt:
            argv += ["--append-system-prompt", task.system_prompt]
            logger.info(
                "[CC-Agent argv] --append-system-prompt (task) "
                "length=%d preview=%r",
                len(task.system_prompt), task.system_prompt[:200],
            )

        # Optional per-task overrides (work in interactive mode per
        # code.claude.com/docs/en/cli-reference). ``--max-turns``,
        # ``--max-budget-usd``, ``--fallback-model`` are ``-p``-only and
        # not emitted; the task spec keeps the fields for back-compat
        # but they're silently dropped here.
        if task.effort:
            argv += ["--effort", task.effort]
        for path in task.add_dir:
            argv += ["--add-dir", path]
        if task.disallowed_tools:
            argv += ["--disallowedTools", task.disallowed_tools]
        if task.agent:
            argv += ["--agent", task.agent]

        # Prompt as positional argument after `--`. Claude auto-submits
        # the positional as turn 1 and stays interactive (per
        # https://code.claude.com/docs/en/cli-reference). The session
        # pool sets include_prompt=False for spawns whose first prompt
        # will be written to the PTY directly.
        if include_prompt and task.prompt:
            argv += ["--", task.prompt]

        return argv

    # ---- native auth -----------------------------------------------------

    def login_argv(self) -> List[str]:
        return list(self._login_argv) or ["claude", "login"]

    def auth_status_argv(self) -> Optional[List[str]]:
        return list(self._auth_status_argv) if self._auth_status_argv else None

    def detect_auth_error(self, stderr: str, exit_code: int) -> bool:
        """True if stderr/exit_code indicate the user isn't logged in."""
        if not stderr and exit_code == 0:
            return False
        markers = (
            "Please run 'claude login'",
            "Please run `claude login`",
            "Not authenticated",
            "Authentication required",
            "401 Unauthorized",
            "Invalid API key",
        )
        return any(m in stderr for m in markers)

    # ---- streaming output parsing ---------------------------------------

    def parse_event(self, line: str) -> Optional[Dict[str, Any]]:
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def is_final_event(self, event: Dict[str, Any]) -> bool:
        return event.get("type") == "result"

    def event_to_session_result(
        self,
        events: List[Dict[str, Any]],
        stderr: str,
        exit_code: int,
    ) -> Dict[str, Any]:
        """Reconstruct shared result fields from the event stream."""
        final = next(
            (e for e in reversed(events) if e.get("type") == "result"),
            None,
        )

        # Session ID can come from `system.init` or `result`
        session_id: Optional[str] = None
        for evt in events:
            sid = evt.get("session_id")
            if sid:
                session_id = sid
                break

        tool_calls = sum(
            1 for evt in events
            if evt.get("type") == "tool_use"
            or (evt.get("type") == "assistant" and self._has_tool_use(evt))
        )

        provider_data: Dict[str, Any] = {}
        for evt in events:
            if evt.get("type") == "assistant":
                msg = evt.get("message") or {}
                rd = msg.get("reasoning_details") or msg.get("thinking")
                if rd is not None:
                    provider_data.setdefault("reasoning_details", rd)
                    break

        success = exit_code == 0 and final is not None
        error: Optional[str] = None
        if exit_code != 0:
            error = stderr.strip()[-2000:] or f"claude exited with code {exit_code}"
        elif final is None:
            error = "no result event received"

        response = ""
        cost: Optional[float] = None
        duration_ms: Optional[int] = None
        num_turns: Optional[int] = None
        if final:
            response = str(final.get("result") or "")
            cost = final.get("total_cost_usd")
            duration_ms = final.get("duration_ms")
            num_turns = final.get("num_turns")
            if final.get("subtype") == "error":
                success = False
                error = error or response or "result event reports error"

        cu = self.canonical_usage(events)

        return {
            "session_id": session_id,
            "response": response,
            "cost_usd": cost,
            "duration_ms": duration_ms,
            "num_turns": num_turns,
            "tool_calls": tool_calls,
            "canonical_usage": cu,
            "provider_data": provider_data,
            "success": success,
            "error": error,
        }

    def canonical_usage(self, events: List[Dict[str, Any]]) -> CanonicalUsage:
        """Pull token counts from the `result` event's `usage` block.

        Anthropic shape:
          {
            "input_tokens": int,
            "output_tokens": int,
            "cache_creation_input_tokens": int,
            "cache_read_input_tokens": int,
          }
        """
        final = next(
            (e for e in reversed(events) if e.get("type") == "result"),
            None,
        )
        if not final:
            return CanonicalUsage()

        usage = final.get("usage") or {}
        request_count = (
            int(final.get("num_turns") or 0)
            or sum(1 for e in events if e.get("type") == "assistant")
        )
        return CanonicalUsage(
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cache_read=int(usage.get("cache_read_input_tokens", 0)),
            cache_write=int(usage.get("cache_creation_input_tokens", 0)),
            reasoning_tokens=0,  # Claude doesn't expose this separately
            request_count=request_count,
        )

    # ---- feature gating --------------------------------------------------

    def supports(self, feature: str) -> bool:
        return feature in self._supports

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _has_tool_use(event: Dict[str, Any]) -> bool:
        msg = event.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            return any(
                isinstance(blk, dict) and blk.get("type") == "tool_use"
                for blk in content
            )
        return False
