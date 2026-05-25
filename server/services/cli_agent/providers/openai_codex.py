"""OpenAI Codex CLI provider.

Sandbox-first companion to Claude. Codex is a Rust binary
(``codex-rs/cli``); the npm package wraps it for distribution.

Subprocess: ``codex exec --json --model <m> --sandbox <mode>
--ask-for-approval <when> <prompt>``

Auth: native — ``codex login`` writes ``~/.codex/auth.json``. We do NOT
inject ``OPENAI_API_KEY`` ourselves (the CLI reads its own auth file).
If the user prefers env-based auth they can set the env in their shell;
we inherit it via ``os.environ`` in the session.

Feature surface (v1):
  - sandbox: read-only / workspace-write / danger-full-access
  - ask_for_approval: untrusted / on-request / never
  - NO sessions, NO resume, NO budget cap, NO max-turns
  - NO IDE lockfile (Codex CLI doesn't honor it yet); session writes
    no lockfile env var

Final event detection: best-effort by event-type matching ("complete",
"task_complete", "done") with stream-end fallback. The Codex CLI's JSON
schema isn't publicly documented, so this is fragile by design — pin
tests against vendored fixtures.

Cost: not exposed in JSON output. ``cost_usd=None`` always; aggregate
``summary.total_cost_usd`` becomes ``None`` if any task is Codex.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging import get_logger

from services.cli_agent.config import get_provider_config
from services.cli_agent.protocol import CanonicalUsage
from services.cli_agent.types import CodexTaskSpec

logger = get_logger(__name__)

NAME = "codex"

# Best-effort markers for Codex's "stream-end" / "task complete" event.
# Update if/when OpenAI publishes a stable schema.
_FINAL_EVENT_TYPES = frozenset(
    {
        "complete",
        "task_complete",
        "done",
        "finished",
        "result",
    }
)

_AUTH_ERROR_MARKERS = (
    "OPENAI_API_KEY not set",
    "401 Unauthorized",
    "Authentication required",
    "Not authenticated",
    "Please run 'codex login'",
    "Please run `codex login`",
    "Invalid API key",
    "auth/missing",
)


class OpenAICodexProvider:
    """`AICliProvider` for OpenAI's Codex CLI."""

    def __init__(self) -> None:
        cfg = get_provider_config(NAME)
        if cfg is None:
            raise RuntimeError(f"Provider config missing for {NAME!r}. Check ai_cli_providers.json.")
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
        which_codex = shutil.which(self.binary_name)
        if which_codex:
            return Path(which_codex)

        npx = shutil.which("npx")
        if npx:
            return Path(npx)

        raise FileNotFoundError(
            f"Neither {self.binary_name!r} nor 'npx' found in PATH. " f"Install with: npm install -g {self.package_name}"
        )

    def interactive_argv(
        self,
        task: Any,  # CodexTaskSpec
        *,
        defaults: Dict[str, Any],
        mcp_endpoint_url: Optional[str] = None,
        mcp_bearer_token: Optional[str] = None,
        connected_tool_names: Optional[List[str]] = None,
        include_prompt: bool = True,
    ) -> List[str]:
        """Build the codex spawn argv. Codex's automation surface is
        ``codex exec --json`` (not a TUI mode like claude); this method
        keeps the name aligned with the Protocol while emitting Codex's
        documented non-interactive form. MCP bridging params are accepted
        for Protocol uniformity but unused (codex's MCP config lives in
        ``~/.codex/config.toml``). ``include_prompt`` is honoured so the
        session pool can spawn without an initial prompt."""
        _ = (mcp_endpoint_url, mcp_bearer_token, connected_tool_names)
        if not isinstance(task, CodexTaskSpec):
            raise TypeError("OpenAICodexProvider.interactive_argv requires CodexTaskSpec, " f"got {type(task).__name__}")

        which_codex = shutil.which(self.binary_name)
        if which_codex:
            argv: List[str] = [which_codex]
        else:
            npx = shutil.which("npx")
            if not npx:
                raise FileNotFoundError(f"Neither {self.binary_name!r} nor 'npx' found in PATH")
            argv = [npx, "--yes", self.package_name]

        argv += ["exec", "--json"]

        model = task.model or defaults.get("default_model") or self._defaults.get("default_model", "gpt-5.2-codex")
        argv += ["--model", model]

        sandbox = task.sandbox or defaults.get(
            "default_sandbox",
            self._defaults.get("default_sandbox", "workspace-write"),
        )
        argv += ["--sandbox", sandbox]

        ask = task.ask_for_approval or defaults.get(
            "default_ask_for_approval",
            self._defaults.get("default_ask_for_approval", "never"),
        )
        argv += ["--ask-for-approval", ask]

        # System prompt — Codex doesn't have a dedicated --system-prompt
        # flag in `exec`. Prepend it to the user prompt with a clear
        # divider when present.
        if include_prompt and task.prompt:
            prompt = task.prompt
            if task.system_prompt:
                prompt = f"<system>\n{task.system_prompt}\n</system>\n\n{task.prompt}"
            argv += [prompt]
        return argv

    # ---- native auth -----------------------------------------------------

    def login_argv(self) -> List[str]:
        return list(self._login_argv) or ["codex", "login"]

    def auth_status_argv(self) -> Optional[List[str]]:
        return list(self._auth_status_argv) if self._auth_status_argv else None

    def detect_auth_error(self, stderr: str, exit_code: int) -> bool:
        if not stderr and exit_code == 0:
            return False
        return any(m in stderr for m in _AUTH_ERROR_MARKERS)

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
        return event.get("type") in _FINAL_EVENT_TYPES

    def event_to_session_result(
        self,
        events: List[Dict[str, Any]],
        stderr: str,
        exit_code: int,
    ) -> Dict[str, Any]:
        # Codex doesn't have a single canonical "result" event; treat the
        # last assistant message as the response, with the final event
        # (if any) for metadata.
        final = next(
            (e for e in reversed(events) if e.get("type") in _FINAL_EVENT_TYPES),
            None,
        )

        response = self._extract_response(events)
        provider_data = self._extract_provider_data(events)

        tool_calls = sum(1 for e in events if e.get("type") in ("tool_call", "tool_use", "function_call"))

        success = exit_code == 0
        error: Optional[str] = None
        if exit_code != 0:
            error = stderr.strip()[-2000:] or f"codex exited with code {exit_code}"
        elif not response:
            # Codex sometimes emits no terminal event; only fail if there's
            # nothing useful in the stream at all.
            if not events:
                success = False
                error = "no events received from codex"

        # session_id: Codex doesn't expose one; leave None.
        session_id: Optional[str] = None
        if final and isinstance(final.get("session_id"), str):
            session_id = final["session_id"]

        # duration_ms / num_turns: best-effort lookups
        duration_ms = None
        num_turns = None
        if final:
            stats = final.get("stats") or {}
            duration_ms = stats.get("duration_ms") or final.get("duration_ms")
            num_turns = stats.get("turns") or final.get("turns")

        return {
            "session_id": session_id,
            "response": response,
            "cost_usd": None,  # Codex doesn't expose cost in --json
            "duration_ms": duration_ms,
            "num_turns": num_turns,
            "tool_calls": tool_calls,
            "canonical_usage": self.canonical_usage(events),
            "provider_data": provider_data,
            "success": success,
            "error": error,
        }

    def canonical_usage(self, events: List[Dict[str, Any]]) -> CanonicalUsage:
        # Codex doesn't expose token counts in `--json` output. Return zeros.
        # If a future Codex schema adds usage, extract here.
        request_count = sum(1 for e in events if e.get("type") in ("assistant", "message", "response"))
        return CanonicalUsage(request_count=request_count)

    # ---- feature gating --------------------------------------------------

    def supports(self, feature: str) -> bool:
        return feature in self._supports

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _extract_response(events: List[Dict[str, Any]]) -> str:
        """Best-effort: walk the event stream looking for the final
        assistant message body."""
        # Try in priority order: explicit `result` field on a final event,
        # then last assistant `message`/`text`/`content`, then concatenated
        # delta text.
        for evt in reversed(events):
            if evt.get("type") in _FINAL_EVENT_TYPES:
                for key in ("result", "response", "text", "content"):
                    val = evt.get(key)
                    if isinstance(val, str) and val:
                        return val
                msg = evt.get("message")
                if isinstance(msg, dict):
                    val = msg.get("content") or msg.get("text")
                    if isinstance(val, str):
                        return val
                break  # one final event is enough

        # Last assistant message
        for evt in reversed(events):
            if evt.get("type") not in ("assistant", "message"):
                continue
            for key in ("text", "content", "response"):
                val = evt.get(key)
                if isinstance(val, str) and val:
                    return val
            msg = evt.get("message")
            if isinstance(msg, dict):
                val = msg.get("content") or msg.get("text")
                if isinstance(val, str) and val:
                    return val

        # Concatenate delta-style text events
        parts: List[str] = []
        for evt in events:
            if evt.get("type") in ("delta", "text", "content"):
                val = evt.get("text") or evt.get("content")
                if isinstance(val, str):
                    parts.append(val)
        return "".join(parts)

    @staticmethod
    def _extract_provider_data(events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Pull Codex-specific metadata into provider_data."""
        data: Dict[str, Any] = {}
        # Last assistant message — capture call_id / response_item_id
        for evt in reversed(events):
            if evt.get("type") in ("assistant", "message", "response"):
                for key in ("call_id", "response_item_id", "id", "model"):
                    if key in evt and evt[key] is not None:
                        data.setdefault(key, evt[key])
                break
        return data
