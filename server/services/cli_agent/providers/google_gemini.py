"""Google Gemini CLI provider — v2 stub.

The factory raises ``NotImplementedError`` for ``"gemini"`` in v1, but
the type+config layer is wired so v2 activation is mechanical (replace
this stub, drop the factory branch). See `cli_agent_framework.md` →
"v2 follow-up — Gemini activation" for the full v2 PR shape.

Reference for v2 implementation:
- argv: ``gemini --prompt <p> --output-format stream-json --model ...
  [--session-id <UUID>] [--resume latest|<idx>|<UUID>] [--yolo] [--sandbox]``
- Final event: ``type == "result"`` (from
  ``packages/cli/src/nonInteractiveCli.ts:JsonStreamEventType.RESULT``)
- ``result`` event carries ``session_id`` + ``stats.duration_ms``
- Exit code 1 = ``FatalAuthenticationError``
- Auth: ``gemini auth login`` writes ``~/.gemini/``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from services.cli_agent.protocol import CanonicalUsage


class GoogleGeminiProvider:
    """v2 stub. Constructed only via direct instantiation in tests; the
    factory bypasses this class entirely and raises NotImplementedError."""

    name = "gemini"

    def __init__(self) -> None:
        raise NotImplementedError("GoogleGeminiProvider is a v2 stub. The factory raises " "NotImplementedError for 'gemini' in v1.")

    # The Protocol surface below is declared so static type-checkers see
    # the class as a complete `AICliProvider`. None of these are reachable
    # because `__init__` raises.

    package_name: str = "@google/gemini-cli"
    binary_name: str = "gemini"
    ide_lock_env_var: Optional[str] = "GEMINI_IDE_LOCK"
    ide_lockfile_dir: Optional[Path] = None  # config.py expands to <tmpdir>/gemini/ide

    def binary_path(self) -> Path:  # pragma: no cover
        raise NotImplementedError

    def interactive_argv(
        self,
        task: Any,
        *,
        defaults: Dict[str, Any],
        mcp_endpoint_url: Optional[str] = None,
        mcp_bearer_token: Optional[str] = None,
        connected_tool_names: Optional[List[str]] = None,
        include_prompt: bool = True,
    ) -> List[str]:  # pragma: no cover
        raise NotImplementedError

    def login_argv(self) -> List[str]:  # pragma: no cover
        return ["gemini", "auth", "login"]

    def auth_status_argv(self) -> Optional[List[str]]:  # pragma: no cover
        return ["gemini", "--version"]

    def detect_auth_error(self, stderr: str, exit_code: int) -> bool:  # pragma: no cover
        return exit_code == 1 and "FatalAuthenticationError" in stderr

    def parse_event(self, line: str) -> Optional[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def is_final_event(self, event: Dict[str, Any]) -> bool:  # pragma: no cover
        return event.get("type") == "result"

    def event_to_session_result(
        self,
        events: List[Dict[str, Any]],
        stderr: str,
        exit_code: int,
    ) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def canonical_usage(self, events: List[Dict[str, Any]]) -> CanonicalUsage:  # pragma: no cover
        return CanonicalUsage()

    def supports(self, feature: str) -> bool:  # pragma: no cover
        return feature in {"session_id", "resume", "sandbox", "mcp_runtime", "ide_lockfile"}
