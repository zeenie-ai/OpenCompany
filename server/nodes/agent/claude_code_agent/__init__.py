"""Claude Code Agent — multi-instance via `AICliService`.

Refactored from the single-task shim. ``Params.tasks`` is a list of
``ClaudeTaskSpec``; each task gets its own git worktree, its own session
id, and runs in parallel under a 5-way semaphore.

Back-compat: an empty `tasks` array with a non-empty `prompt` falls back
to a single-task batch — preserves the legacy single-shot UX.

Skill instructions connected via the parent node's ``input-skill`` handle
are exposed to the CLI through the MCP server's ``listSkills`` /
``getSkill`` tools (NOT concatenated into the system prompt — the
agent fetches them on-demand). Connected skill names ARE collected here
and passed to ``run_batch()`` so the MCP server can scope its responses.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue
from services.plugin.edge_walker import collect_agent_connections

from .._handles import STD_AGENT_HINTS, std_agent_handles

logger = get_logger(__name__)


# Late import the cli_agent types so node_registry import doesn't pull
# the whole MCP / pool stack at module import time.
from services.cli_agent import ClaudeTaskSpec  # noqa: E402
from services.cli_agent.types import SessionResultModel  # noqa: E402

# --- Self-registration -----------------------------------------------------
#
# Per the canonical plugin-folder pattern (see
# ``docs-internal/plugin_system.md`` § "Self-contained plugin folders"),
# the plugin folder owns its provider class + auth helpers + session
# pool + skill materialiser. The generic framework
# (``services/cli_agent/``) doesn't import from us — it asks three
# parallel registries (one per concern, all in ``factory.py``) for
# the right callable by provider name.
from services.cli_agent.factory import (  # noqa: E402
    register_provider,
    register_session_pool,
    register_skill_materialiser,
)
from services.ws_handler_registry import register_ws_handlers  # noqa: E402

from ._handlers import WS_HANDLERS as _CLAUDE_WS_HANDLERS  # noqa: E402
from ._pool import get_session_pool as _claude_get_session_pool  # noqa: E402
from ._provider import AnthropicClaudeProvider  # noqa: E402
from ._skills import materialise_skills as _claude_materialise_skills  # noqa: E402

register_provider("claude", AnthropicClaudeProvider)
register_session_pool("claude", _claude_get_session_pool)
register_skill_materialiser("claude", _claude_materialise_skills)
register_ws_handlers(_CLAUDE_WS_HANDLERS)


# Claude Code-supported models. Per
# https://code.claude.com/docs/en/cli-reference the ``--model`` flag
# accepts either an Anthropic-managed alias (``sonnet`` / ``opus`` /
# ``haiku`` — resolves to the latest in that family) or a full model ID.
# We surface both so users can pin a specific revision OR float to the
# tier-latest. Free-text ``str`` was the prior type; switching to
# ``Literal`` gives the parameter panel a dropdown without losing
# back-compat (every value the field used to accept is still in the list).
ClaudeCodeModel = Literal[
    # Anthropic-managed aliases — track the freshest model per family
    "sonnet",
    "opus",
    "haiku",
    # Pinned full IDs — current generation (Claude 4.x)
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-opus-4",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-sonnet-4",
    "claude-haiku-4-5",
]


ClaudeCodeEffort = Literal["low", "medium", "high", "xhigh", "max"]


# Set of valid model strings for the ``mode="before"`` validator below.
# Derived once at import time from the Literal type so the two stay in
# sync — adding a new model to the Literal automatically updates this set.
_VALID_CLAUDE_MODELS = frozenset(ClaudeCodeModel.__args__)  # type: ignore[attr-defined]
_DEFAULT_CLAUDE_MODEL: str = "claude-sonnet-4-6"


class ClaudeCodeAgentParams(BaseModel):
    """Claude Code node parameters.

    UI surface: a single ``prompt`` field. Every other field is kept on
    the model for back-compat with existing saved workflows and to feed
    sensible defaults into the per-task ``ClaudeTaskSpec`` — but is
    hidden from the parameter panel via ``json_schema_extra.hidden``
    so the canvas-side surface stays minimal.

    Two execution paths (both invisible to the user):
      1. ``tasks=[...]`` — explicit list (advanced; saved workflows only)
      2. ``tasks=[]`` + ``prompt`` — synthesises a single-task batch (default)
    """

    # The ONLY visible field. Everything below is ``hidden: true``.
    prompt: str = Field(
        default="",
        description="The prompt sent to Claude Code.",
        json_schema_extra={"rows": 4, "placeholder": "Ask Claude Code to..."},
    )

    # --- Hidden: advanced multi-task batch ---
    tasks: List[ClaudeTaskSpec] = Field(
        default_factory=list,
        description="Advanced: list of Claude tasks to run in parallel " "(max 5 concurrent). Use ``prompt`` for the common case.",
        json_schema_extra={"hidden": True, "rows": 1},
    )

    # --- Hidden: model + system prompt + execution scoping ---
    model: ClaudeCodeModel = Field(
        default="claude-sonnet-4-6",
        description=(
            "Default model for tasks that don't override it. Aliases "
            "(``sonnet`` / ``opus`` / ``haiku``) resolve to the latest in "
            "each family; full IDs pin a specific revision."
        ),
        json_schema_extra={"hidden": True},
    )
    system_prompt: Optional[str] = Field(
        default=None,
        json_schema_extra={"hidden": True, "rows": 3},
    )
    working_directory: Optional[str] = Field(
        default=None,
        description="Git repo root. Defaults to the workflow's workspace dir.",
        json_schema_extra={"hidden": True},
    )
    max_parallel: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Concurrency cap.",
        json_schema_extra={"hidden": True},
    )
    allowed_credentials: List[str] = Field(
        default_factory=list,
        description="Credential names the CLI is permitted to fetch via MCP.",
        json_schema_extra={"hidden": True},
    )

    # --- Hidden: model knobs (effort, fallback) ---
    effort: Optional[ClaudeCodeEffort] = Field(
        default=None,
        description=(
            "Reasoning-effort level passed via ``--effort``. Applies to " "thinking-capable models (Opus / Sonnet); ignored by Haiku."
        ),
        json_schema_extra={"hidden": True},
    )
    fallback_model: Optional[ClaudeCodeModel] = Field(
        default=None,
        description=("Model to fall back to when the primary is overloaded. " "Passed via ``--fallback-model``."),
        json_schema_extra={"hidden": True},
    )

    # Saved workflow JSON may persist these list fields as `null` rather
    # than `[]` when the user has never edited them. Coerce so Pydantic's
    # strict list validation doesn't reject the params on load.
    @field_validator("tasks", "allowed_credentials", mode="before")
    @classmethod
    def _none_is_empty_list(cls, v: Any) -> Any:
        return [] if v is None else v

    # Older saved workflows (pre-Literal cutover) carry free-form
    # ``model`` strings — empty, ``"claude-sonnet-4.6"`` (dot-spelled),
    # ``"claude-3.5-sonnet"`` (old Claude 3.x naming), API-style date
    # suffixes like ``"claude-3-5-sonnet-20241022"``, etc. Strict Literal
    # validation would reject those and the whole node would fail to
    # load. Coerce unknown values to the default so legacy workflows
    # keep working; the UI dropdown still constrains new edits.
    @field_validator("model", "fallback_model", mode="before")
    @classmethod
    def _coerce_unknown_model(cls, v: Any, info: Any) -> Any:
        # ``fallback_model`` accepts None (no fallback) — let it through.
        if v is None and info.field_name == "fallback_model":
            return None
        if isinstance(v, str) and v in _VALID_CLAUDE_MODELS:
            return v
        # Unknown / empty / None on ``model`` → default; unknown on
        # ``fallback_model`` → None (drop the bad fallback).
        if info.field_name == "fallback_model":
            return None
        return _DEFAULT_CLAUDE_MODEL

    model_config = ConfigDict(extra="ignore")


class ClaudeCodeAgentOutput(BaseModel):
    """Aggregated batch output."""

    success: bool = True
    n_tasks: int = 0
    n_succeeded: int = 0
    n_failed: int = 0
    total_cost_usd: Optional[float] = None
    wall_clock_ms: int = 0
    tasks: List[SessionResultModel] = Field(default_factory=list)
    provider: str = "claude"
    timestamp: Optional[str] = None
    # Legacy single-task fields, populated when n_tasks==1 for back-compat:
    response: Optional[str] = None
    session_id: Optional[str] = None
    cost_usd: Optional[float] = None
    # `extra="forbid"` so a malformed CLI envelope raises a real
    # ValidationError at the Output boundary instead of silently emitting
    # an unparseable shape downstream.
    model_config = ConfigDict(extra="forbid")


class ClaudeCodeAgentNode(ActionNode):
    type = "claude_code_agent"
    display_name = "Claude Code"
    subtitle = "Agentic Coding"
    group = ("agent",)
    description = "Run N parallel Claude Code CLI sessions over a list of tasks. " "Each task is isolated in its own git worktree."
    component_kind = "agent"
    tool_description = "ONE-SHOT delegation to Claude Code Agent. Call ONCE per task, returns task_id. Agentic coding with file reading, editing, and command execution - do NOT re-call."
    handles = std_agent_handles()
    ui_hints = STD_AGENT_HINTS
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = TaskQueue.AI_HEAVY

    Params = ClaudeCodeAgentParams
    Output = ClaudeCodeAgentOutput

    @Operation(
        "execute",
        cost={"service": "claude_code_agent", "action": "run", "count": 1},
    )
    async def execute_op(
        self,
        ctx: NodeContext,
        params: ClaudeCodeAgentParams,
    ) -> Any:
        from services.cli_agent.service import get_ai_cli_service
        from services.cli_agent.types import session_result_to_model
        from services.plugin.deps import get_database
        from services.status_broadcaster import get_status_broadcaster

        start_time = time.time()
        broadcaster = get_status_broadcaster()
        workflow_id = ctx.workflow_id
        node_id = ctx.node_id

        await broadcaster.update_node_status(
            node_id,
            "executing",
            {"message": "Starting Claude Code batch..."},
            workflow_id=workflow_id,
        )

        # Collect connected memory/skills/tools/input/task in one pass —
        # same edge-walker the AI Agent uses (services/plugin/edge_walker.py).
        # This must run BEFORE prompt resolution so the auto-fallback can
        # read from `input_data` exactly the way `nodes/agent/_inline.py`
        # does for the standard agent path.
        database = get_database()
        memory_data, skill_data, tool_data, input_data, _ = await collect_agent_connections(
            node_id,
            ctx.raw,
            database,
            log_prefix="[Claude Code]",
        )
        connected_skills = [s.get("skill_name") or s.get("label") for s in skill_data if s.get("skill_name") or s.get("label")]

        # Memory bridge: claude maintains its own session JSONL on disk
        # under `<CLAUDE_CONFIG_DIR>/projects/<cwd-encoded>/<UUID>.jsonl`.
        # The project_key is derived from cwd (`[^a-zA-Z0-9.-] -> -`),
        # so memory continuity needs only a STABLE cwd across runs.
        # That's handled by AICliService passing memory_bound=True so
        # AICliSession spawns under repo_root instead of an ephemeral
        # worktree — see `services/cli_agent/session.py:cwd()`.
        #
        # Continuity flag: when memory is wired, we set
        # ``continue_session=True`` on the task spec. The argv-builder
        # emits ``--continue`` and claude auto-loads the most recent
        # conversation under the cwd (per code.claude.com/docs/en/
        # cli-reference). No UUID round-trip through the memory node's
        # params required — claude tracks its own sessions on disk and
        # the auto-find-latest is the cleaner primitive than the
        # pre-cutover UUID5-pre-mint + `--session-id <UUID5>` dance.
        #
        # First run: ``--continue`` with no prior session under the cwd
        # is a benign no-op; claude starts fresh. Subsequent runs find
        # and continue the prior JSONL.
        continue_session = bool(memory_data)
        if memory_data:
            logger.info(
                "[Claude Code memory] memory_node=%s -> --continue " "(claude auto-finds latest session under cwd)",
                memory_data.get("node_id"),
            )

        tasks = list(params.tasks)
        if not tasks:
            # AI-Agent-style prompt resolution: explicit `prompt` field
            # wins; otherwise extract from upstream input via the same
            # `message > text > content > str()` chain `_inline.py` uses.
            prompt = (params.prompt or "").strip()
            if not prompt and input_data:
                prompt = (
                    (input_data.get("message") if isinstance(input_data, dict) else None)
                    or (input_data.get("text") if isinstance(input_data, dict) else None)
                    or (input_data.get("content") if isinstance(input_data, dict) else None)
                    or str(input_data)
                )
            if not prompt:
                raise NodeUserError(
                    "Claude Code agent has no prompt — fill in the Prompt "
                    "field, populate the Tasks array, or connect a node "
                    "to the Input handle."
                )
            spec_kwargs: dict = {
                "prompt": prompt,
                "model": params.model,
                "system_prompt": params.system_prompt,
                "continue_session": continue_session,
            }
            if params.effort is not None:
                spec_kwargs["effort"] = params.effort
            if params.fallback_model is not None:
                spec_kwargs["fallback_model"] = params.fallback_model
            tasks = [ClaudeTaskSpec(**spec_kwargs)]
        else:
            # Apply node-level defaults to tasks that don't override.
            for i, t in enumerate(tasks):
                changed: dict = {}
                if not t.model and params.model:
                    changed["model"] = params.model
                if not t.system_prompt and params.system_prompt:
                    changed["system_prompt"] = params.system_prompt
                if t.effort is None and params.effort is not None:
                    changed["effort"] = params.effort
                if t.fallback_model is None and params.fallback_model is not None:
                    changed["fallback_model"] = params.fallback_model
                # Only auto-enable --continue when memory is wired AND
                # the task didn't explicitly opt in/out or pick a UUID.
                if continue_session and not t.continue_session and not t.resume_session_id:
                    changed["continue_session"] = True
                if changed:
                    tasks[i] = t.model_copy(update=changed)

        # Memory continuity requires serial execution; parallel
        # `--resume` against one session JSONL would corrupt it.
        if memory_data and len(tasks) > 1:
            raise NodeUserError(
                "Memory-bound batches must run one task at a time. " "Reduce Tasks to a single entry, or disconnect the " "memory node."
            )

        # Workspace dir — workflow.py injects this into context
        workspace_dir = ctx.raw.get("workspace_dir") or params.working_directory
        if workspace_dir is None:
            from core.config import Settings

            workspace_dir = Path(Settings().workspace_base_resolved) / (workflow_id or "default")
        workspace_dir = Path(workspace_dir)

        repo_root = Path(params.working_directory) if params.working_directory else None

        svc = get_ai_cli_service()
        result = await svc.run_batch(
            "claude",
            tasks=tasks,
            node_id=node_id,
            workflow_id=workflow_id or "",
            workspace_dir=workspace_dir,
            broadcaster=broadcaster,
            repo_root=repo_root,
            connected_skill_names=connected_skills,
            connected_tools=tool_data,
            connected_memory=memory_data,
            allowed_credentials=params.allowed_credentials,
            max_parallel=params.max_parallel,
        )

        elapsed = time.time() - start_time
        logger.debug(
            "[claude_code_agent] node=%s tasks=%d ok=%d fail=%d elapsed=%.2fs",
            node_id,
            result.n_tasks,
            result.n_succeeded,
            result.n_failed,
            elapsed,
        )

        await broadcaster.update_node_status(
            node_id,
            "success" if result.n_failed == 0 else "warning",
            {
                "message": (f"Batch complete: {result.n_succeeded}/{result.n_tasks} " f"succeeded"),
                "n_tasks": result.n_tasks,
                "n_succeeded": result.n_succeeded,
                "n_failed": result.n_failed,
            },
            workflow_id=workflow_id,
        )

        task_models = [session_result_to_model(t).model_dump() for t in result.tasks]

        # Legacy single-task convenience fields
        legacy_response = None
        legacy_session_id = None
        legacy_cost = None
        if len(result.tasks) == 1:
            legacy_response = result.tasks[0].response
            legacy_session_id = result.tasks[0].session_id
            legacy_cost = result.tasks[0].cost_usd

        return {
            "success": result.n_failed == 0,
            "n_tasks": result.n_tasks,
            "n_succeeded": result.n_succeeded,
            "n_failed": result.n_failed,
            "total_cost_usd": result.total_cost_usd,
            "wall_clock_ms": result.wall_clock_ms,
            "tasks": task_models,
            "provider": result.provider,
            "timestamp": result.timestamp or datetime.now().isoformat(),
            "response": legacy_response,
            "session_id": legacy_session_id,
            "cost_usd": legacy_cost,
        }
