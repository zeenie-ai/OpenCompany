"""Pydantic task specs (discriminated union) + result models.

The discriminated union lets each plugin's `Params.tasks` be hard-typed
to one variant (e.g. `list[ClaudeTaskSpec]`), so the LLM tool schema
fast-path at `services/ai.py:2898` produces a clean per-provider schema
with no `$defs`/`$ref`.

v1 ships Claude + Codex plugins fully wired. `GeminiTaskSpec` is part of
the union (and exported) so the type system, factory dispatch keys, and
config JSON are ready, but `create_cli_provider("gemini")` raises
`NotImplementedError`. v2 swaps the stub for the real impl.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Task specs (discriminated union)
# ---------------------------------------------------------------------------


class BaseAICliTaskSpec(BaseModel):
    """Shared fields for every CLI task."""

    task_id: Optional[str] = Field(
        default=None,
        description="Auto-assigned `t_<8hex>` if omitted.",
    )
    prompt: str = Field(
        ...,
        description="The task prompt sent to the CLI.",
        json_schema_extra={"rows": 4},
    )
    branch: Optional[str] = Field(
        default=None,
        description="Branch name for the per-task git worktree. " "Auto-named `machina/<task_id>` if omitted.",
    )
    model: Optional[str] = Field(default=None)
    timeout_seconds: int = Field(
        default=600,
        ge=10,
        le=3600,
        description="Hard timeout per task. On expiry the session is "
        "terminate_then_kill'd and a diagnostic dump is "
        "written to ~/.claude-machina/logs/.",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        json_schema_extra={"rows": 3},
    )

    # `extra="forbid"` surfaces typo'd task fields as Pydantic
    # ValidationError at the spec boundary instead of silently dropping
    # them and confusing downstream consumers.
    model_config = ConfigDict(extra="forbid")


class ClaudeTaskSpec(BaseAICliTaskSpec):
    """Claude Code CLI task. Full feature set: sessions, resume, budget,
    turns, allowed_tools, permission_mode."""

    provider: Literal["claude"] = "claude"
    session_id: Optional[str] = Field(
        default=None,
        description="Start a named session. Pair with `resume_session_id` "
        "to chain conversations. Note: silently dropped in "
        "interactive mode (claude assigns its own UUID); "
        "kept for back-compat.",
    )
    resume_session_id: Optional[str] = Field(
        default=None,
        description="Resume from a specific prior session UUID. Mutually "
        "exclusive with `continue_session`. Generally unset by "
        "claude_code_agent — set `continue_session=True` for "
        "memory-bound runs and let claude find its own latest.",
    )
    continue_session: bool = Field(
        default=False,
        description="Emit `--continue` so claude auto-loads the most "
        "recent conversation under the current cwd (per "
        "code.claude.com/docs/en/cli-reference). The cleaner "
        "alternative to passing a specific UUID via "
        "`resume_session_id` — claude handles session "
        "tracking itself, no UUID round-trip through the "
        "memory node's params required.",
    )
    max_turns: Optional[int] = Field(
        default=None,
        ge=1,
        description="Per-task turn cap. Defaults to provider config.",
    )
    max_budget_usd: Optional[float] = Field(
        default=None,
        ge=0,
        description="Per-task USD budget. Defaults to provider config.",
    )
    allowed_tools: Optional[str] = Field(
        default=None,
        description="Comma-separated tool list. Default is empty — "
        "claude built-ins (Read/Edit/Bash/Glob/Grep/Write/"
        "Skill/WebSearch/WebFetch) are intentionally NOT in "
        "the allowlist; the agent only gets connected MCP "
        "tools + MachinaOs's own MCP infrastructure tools.",
    )
    permission_mode: Literal["default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"] = "dontAsk"

    # ---- optional documented CLI flags (cli-reference) ----
    effort: Optional[Literal["low", "medium", "high", "xhigh", "max"]] = Field(
        default=None,
        description="Reasoning-effort level. Available levels depend on the model.",
    )
    fallback_model: Optional[str] = Field(
        default=None,
        description="Fallback model when the primary is overloaded (print mode).",
    )
    add_dir: List[str] = Field(
        default_factory=list,
        description="Extra working directories the CLI may read/edit.",
    )
    disallowed_tools: Optional[str] = Field(
        default=None,
        description="Comma-separated tools removed from Claude's context.",
    )
    agent: Optional[str] = Field(
        default=None,
        description="Override the configured `agent` setting for this task.",
    )


class CodexTaskSpec(BaseAICliTaskSpec):
    """OpenAI Codex CLI task. Sandbox-first; no session/resume/budget/turns."""

    provider: Literal["codex"] = "codex"
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] = "workspace-write"
    ask_for_approval: Literal["untrusted", "on-request", "never"] = "never"


class GeminiTaskSpec(BaseAICliTaskSpec):
    """Google Gemini CLI task (v2 — stub provider in v1).

    Schema lives in v1 so the discriminated union JSON Schema for the
    LLM tool fast-path doesn't change when v2 lands.
    """

    provider: Literal["gemini"] = "gemini"
    session_id: Optional[str] = None
    resume: Optional[str] = Field(
        default=None,
        description='"latest" | "<index>" | "<UUID>"',
    )
    yolo: bool = False
    sandbox: bool = False


AICliTaskSpec = Annotated[
    Union[ClaudeTaskSpec, CodexTaskSpec, GeminiTaskSpec],
    Field(discriminator="provider"),
]


# ---------------------------------------------------------------------------
# Result models (Pydantic for serialisation; mirror dataclasses in protocol.py)
# ---------------------------------------------------------------------------


class CanonicalUsagePydantic(BaseModel):
    """Pydantic mirror of `protocol.CanonicalUsage` for output serialisation."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning_tokens: int = 0
    request_count: int = 0


class SessionResultModel(BaseModel):
    """Per-task result, JSON-serialisable."""

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
    canonical_usage: CanonicalUsagePydantic = Field(
        default_factory=CanonicalUsagePydantic,
    )
    provider_data: Dict[str, Any] = Field(default_factory=dict)
    success: bool = False
    error: Optional[str] = None


class BatchSummary(BaseModel):
    """Aggregated batch summary."""

    n_tasks: int = 0
    n_succeeded: int = 0
    n_failed: int = 0
    total_cost_usd: Optional[float] = None
    wall_clock_ms: int = 0
    budget_remaining_usd: Optional[float] = None


class BatchResultModel(BaseModel):
    """Top-level batch result returned by `run_batch()`."""

    tasks: List[SessionResultModel] = Field(default_factory=list)
    summary: BatchSummary = Field(default_factory=BatchSummary)
    provider: str = ""
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def session_result_to_model(sr: Any) -> SessionResultModel:
    """Convert a `protocol.SessionResult` dataclass to its Pydantic mirror."""
    cu = CanonicalUsagePydantic(
        input_tokens=sr.canonical_usage.input_tokens,
        output_tokens=sr.canonical_usage.output_tokens,
        cache_read=sr.canonical_usage.cache_read,
        cache_write=sr.canonical_usage.cache_write,
        reasoning_tokens=sr.canonical_usage.reasoning_tokens,
        request_count=sr.canonical_usage.request_count,
    )
    return SessionResultModel(
        task_id=sr.task_id,
        session_id=sr.session_id,
        provider=sr.provider,
        prompt=sr.prompt,
        branch=sr.branch,
        worktree_path=sr.worktree_path,
        response=sr.response,
        cost_usd=sr.cost_usd,
        duration_ms=sr.duration_ms,
        num_turns=sr.num_turns,
        tool_calls=sr.tool_calls,
        canonical_usage=cu,
        provider_data=sr.provider_data,
        success=sr.success,
        error=sr.error,
    )
