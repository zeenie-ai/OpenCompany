"""Codex's documented ``exec --json`` automation protocol.

Login and execution share an OpenCompany-managed binary and CODEX_HOME.
The CLI owns credentials; OpenCompany consumes JSONL thread/item/turn events
and supplies its per-batch MCP token through the subprocess environment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.cli_agent.config import get_provider_config
from services.cli_agent.protocol import CanonicalUsage
from services.cli_agent.types import CodexTaskSpec

NAME = "codex"
MCP_TOKEN_ENV = "OPENCOMPANY_CODEX_MCP_TOKEN"
_FINAL_EVENT_TYPES = frozenset({"turn.completed", "turn.failed"})
_TOOL_ITEM_TYPES = frozenset({"command_execution", "file_change", "mcp_tool_call", "web_search"})
_AUTH_ERROR_MARKERS = (
    "openai_api_key not set",
    "401 unauthorized",
    "authentication required",
    "not authenticated",
    "not logged in",
    "please run 'codex login'",
    "please run `codex login`",
    "invalid api key",
    "auth/missing",
)


class OpenAICodexProvider:
    """Provider for the pinned Codex CLI's non-interactive surface."""

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

    def binary_path(self) -> Path:
        from services.cli_agent._codex_auth import codex_binary_path

        return Path(codex_binary_path())

    def interactive_argv(
        self,
        task: Any,
        *,
        defaults: Dict[str, Any],
        mcp_endpoint_url: Optional[str] = None,
        mcp_bearer_token: Optional[str] = None,
        connected_tool_names: Optional[List[str]] = None,
        connected_skill_names: Optional[List[str]] = None,
        include_prompt: bool = True,
    ) -> List[str]:
        if not isinstance(task, CodexTaskSpec):
            raise TypeError(f"OpenAICodexProvider.interactive_argv requires CodexTaskSpec, got {type(task).__name__}")

        ask = task.ask_for_approval or defaults.get("default_ask_for_approval", self._defaults.get("default_ask_for_approval", "never"))
        # `exec` has no --ask-for-approval flag. Override its documented
        # config directly instead of relying on interactive/TUI options.
        argv = [str(self.binary_path()), "exec", "--json", "-c", f"approval_policy={json.dumps(ask)}"]
        model = task.model or defaults.get("default_model") or self._defaults.get("default_model", "gpt-5.2-codex")
        argv += ["--model", model]
        sandbox = task.sandbox or defaults.get("default_sandbox", self._defaults.get("default_sandbox", "workspace-write"))
        argv += ["--sandbox", sandbox]

        if mcp_endpoint_url and mcp_bearer_token:
            # No secret in argv or persistent config. The session sets this env
            # variable; the MCP server enforces the batch's connected-tool scope.
            argv += [
                "-c",
                f"mcp_servers.opencompany.url={json.dumps(mcp_endpoint_url)}",
                "-c",
                f"mcp_servers.opencompany.bearer_token_env_var={json.dumps(MCP_TOKEN_ENV)}",
                "-c",
                "mcp_servers.opencompany.required=true",
            ]

        if include_prompt:
            prompt = task.prompt
            if task.system_prompt:
                prompt = f"<system>\n{task.system_prompt}\n</system>\n\n{prompt}"
            argv += ["--", prompt]
        return argv

    def login_argv(self) -> List[str]:
        return [str(self.binary_path()), "login"]

    def auth_status_argv(self) -> List[str]:
        return [str(self.binary_path()), "login", "status"]

    def detect_auth_error(self, stderr: str, exit_code: int) -> bool:
        return any(marker in stderr.lower() for marker in _AUTH_ERROR_MARKERS)

    def parse_event(self, line: str) -> Optional[Dict[str, Any]]:
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def is_final_event(self, event: Dict[str, Any]) -> bool:
        return event.get("type") in _FINAL_EVENT_TYPES

    def event_to_session_result(self, events: List[Dict[str, Any]], stderr: str, exit_code: int) -> Dict[str, Any]:
        completed = [event for event in events if event.get("type") == "turn.completed"]
        failed = next((event for event in reversed(events) if event.get("type") == "turn.failed"), None)
        success = exit_code == 0 and bool(completed) and failed is None
        error = None
        if not success:
            failure = failed or next((event for event in reversed(events) if event.get("type") == "error"), {})
            detail = failure.get("error") or failure.get("message")
            if isinstance(detail, dict):
                detail = detail.get("message")
            error = detail if isinstance(detail, str) and detail else stderr.strip()[-2000:]
            if not error:
                error = f"codex exited with code {exit_code}" if exit_code else "Codex stream ended without turn.completed"

        thread_id = next((event.get("thread_id") for event in events if event.get("type") == "thread.started"), None)
        tool_ids = {
            event["item"]["id"]
            for event in events
            if event.get("type") == "item.completed"
            and isinstance(event.get("item"), dict)
            and event["item"].get("type") in _TOOL_ITEM_TYPES
            and event["item"].get("id")
        }
        return {
            "session_id": thread_id,
            "response": self._extract_response(events),
            "cost_usd": None,
            "duration_ms": None,
            "num_turns": len(completed) + int(failed is not None),
            "tool_calls": len(tool_ids),
            "canonical_usage": self.canonical_usage(events),
            "provider_data": {"thread_id": thread_id} if thread_id else {},
            "success": success,
            "error": error,
        }

    def canonical_usage(self, events: List[Dict[str, Any]]) -> CanonicalUsage:
        usage = CanonicalUsage()
        for event in events:
            if event.get("type") != "turn.completed":
                continue
            counts = event.get("usage") or {}
            if not isinstance(counts, dict):
                continue
            cached = int(counts.get("cached_input_tokens") or 0)
            usage.input_tokens += max(0, int(counts.get("input_tokens") or 0) - cached)
            usage.cache_read += cached
            usage.output_tokens += int(counts.get("output_tokens") or 0)
            usage.reasoning_tokens += int(counts.get("reasoning_output_tokens") or 0)
            usage.request_count += 1
        return usage

    def supports(self, feature: str) -> bool:
        return feature in self._supports

    @staticmethod
    def _extract_response(events: List[Dict[str, Any]]) -> str:
        for event in reversed(events):
            item = event.get("item")
            if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    return text
        return ""
