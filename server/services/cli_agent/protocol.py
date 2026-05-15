"""Protocol for AI CLI providers (Claude Code, Codex, Gemini).

Mirrors `services/llm/protocol.py` shape: a structurally-typed Protocol
with a small, explicit surface. Each concrete provider lives under
`providers/<vendor>.py`.

The framework spawns N parallel CLI sessions (one `AICliSession` each,
backed by `BaseProcessSupervisor`) over a list of tasks. Per-CLI
differences (argv, JSON event schema, auth handling, feature support)
are isolated to the provider; the session/pool/service layer is generic.

Auth: native CLI handles its own tokens. We only trigger the login flow
(`login_argv()`) and detect logged-in state (`auth_status_argv()` +
`detect_auth_error()`). No credential wrapping, no `~/.claude-machina/`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------

@dataclass
class CanonicalUsage:
    """Vendor-normalised token counts.

    Pattern from Hermes `agent/usage_pricing.py:CanonicalUsage` — every
    provider's usage shape (Anthropic vs Codex vs OpenAI handle cache
    differently) maps into this so the existing `services/pricing.py`
    can compute USD without per-vendor branches.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning_tokens: int = 0
    request_count: int = 0


@dataclass
class SessionResult:
    """Per-task result returned by `AICliSession`.

    Shared keys are the schema; vendor extras live in `provider_data`.
    """
    task_id: str
    session_id: Optional[str] = None
    provider: str = ""
    prompt: str = ""
    branch: Optional[str] = None
    worktree_path: Optional[str] = None
    response: str = ""
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    num_turns: Optional[int] = None
    tool_calls: int = 0
    canonical_usage: CanonicalUsage = field(default_factory=CanonicalUsage)
    provider_data: Dict[str, Any] = field(default_factory=dict)
    success: bool = False
    error: Optional[str] = None


@dataclass
class BatchResult:
    """Aggregated result returned by `AICliService.run_batch()`."""
    tasks: List[SessionResult] = field(default_factory=list)
    n_tasks: int = 0
    n_succeeded: int = 0
    n_failed: int = 0
    total_cost_usd: Optional[float] = None
    wall_clock_ms: int = 0
    budget_remaining_usd: Optional[float] = None
    provider: str = ""
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Provider protocol (structural typing)
# ---------------------------------------------------------------------------

@runtime_checkable
class AICliProvider(Protocol):
    """Structural Protocol for an AI CLI provider.

    Concrete classes:
      - `providers.anthropic_claude.AnthropicClaudeProvider`
      - `providers.openai_codex.OpenAICodexProvider`
      - `providers.google_gemini.GoogleGeminiProvider` (v2 stub)
    """

    name: str                          # "claude" | "codex" | "gemini"
    package_name: str                  # npm package
    binary_name: str                   # "claude" | "codex" | "gemini"
    ide_lock_env_var: Optional[str]    # CLAUDE_IDE_LOCK | GEMINI_IDE_LOCK | None
    ide_lockfile_dir: Optional[Path]   # <MACHINA_CLAUDE_DIR>/ide | <tmpdir>/gemini/ide

    # ---- spawn surface ---------------------------------------------------

    def binary_path(self) -> Path: ...
        # Resolve the CLI binary. Resolution chain (Composio pattern):
        #   1) shutil.which(<binary_name>)
        #   2) `npx --yes <package_name>` shim path
        # Raises FileNotFoundError if neither is available.

    def interactive_argv(self, task: Any, *, defaults: Dict[str, Any]) -> List[str]: ...
        # Build the full argv (binary + flags) for spawning a session
        # over `task` (a `<Provider>TaskSpec` Pydantic model). For
        # Claude, this is the interactive-TUI shape (no `-p`, prompt as
        # positional after `--`); for Codex, it's `codex exec --json`
        # since that's Codex's automation surface. `defaults` comes
        # from `ai_cli_providers.json` for this provider.

    # ---- native auth (no token wrapping) --------------------------------

    def login_argv(self) -> List[str]: ...
        # CLI's own login command, e.g. ["claude", "login"]. Spawned
        # interactively from the Credentials Modal. CLI stores its own
        # credentials in `~/.claude/`, `~/.codex/`, `~/.gemini/`.

    def auth_status_argv(self) -> Optional[List[str]]: ...
        # No-op invocation to verify auth, e.g. ["claude", "--print", "-p", "ok"].
        # Returns None if no cheap probe exists; in that case the framework
        # infers from the first session's stderr.

    def detect_auth_error(self, stderr: str, exit_code: int) -> bool: ...
        # Match "not logged in" patterns:
        #   Claude: "Please run 'claude login'"
        #   Codex:  HTTP 401 / "OPENAI_API_KEY not set"
        #   Gemini: exit_code == 1 (FatalAuthenticationError)

    # ---- streaming output parsing ---------------------------------------

    def parse_event(self, line: str) -> Optional[Dict[str, Any]]: ...
        # Parse a single NDJSON line from stdout. Return None for
        # un-parseable garbage.

    def is_final_event(self, event: Dict[str, Any]) -> bool: ...
        # True if this event marks end-of-task. For Claude: `type=="result"`.
        # For Gemini: `type=="result"`. For Codex: `type=="complete"` or
        # heuristic fallback.

    def event_to_session_result(
        self,
        events: List[Dict[str, Any]],
        stderr: str,
        exit_code: int,
    ) -> Dict[str, Any]: ...
        # Reconstruct a partial dict of `SessionResult` fields from the
        # event stream. Returns:
        #   {
        #     "session_id": ..., "response": ..., "cost_usd": ...,
        #     "duration_ms": ..., "num_turns": ..., "tool_calls": ...,
        #     "success": bool, "error": Optional[str],
        #     "canonical_usage": CanonicalUsage,
        #     "provider_data": {<vendor-specific>},
        #   }
        # provider_data carries vendor-only metadata (Anthropic
        # reasoning_details, Codex call_id, Gemini extra_content) without
        # bloating the shared schema. Pattern from
        # Hermes agent/transports/types.py NormalizedResponse.

    def canonical_usage(self, events: List[Dict[str, Any]]) -> CanonicalUsage: ...
        # Normalise vendor token-counting into the shared `CanonicalUsage`
        # shape. Pattern from Hermes agent/usage_pricing.py.

    # ---- feature gating --------------------------------------------------

    def supports(self, feature: str) -> bool: ...
        # Feature flags consulted by the session/service layer.
        # Recognised: "max_budget", "max_turns", "session_id", "resume",
        # "mcp_runtime", "json_cost", "ide_lockfile", "sandbox".
