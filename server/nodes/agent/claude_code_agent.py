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
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue
from services.plugin.edge_walker import collect_agent_connections

from ._handles import STD_AGENT_HINTS, std_agent_handles

logger = get_logger(__name__)


# Late import the cli_agent types so node_registry import doesn't pull
# the whole MCP / pool stack at module import time.
from services.cli_agent import ClaudeTaskSpec  # noqa: E402
from services.cli_agent.types import SessionResultModel  # noqa: E402


class ClaudeCodeAgentParams(BaseModel):
    """Multi-task batch parameters for Claude Code.

    Two paths:
      1. ``tasks=[...]`` — explicit list, runs N in parallel
      2. ``tasks=[]`` + legacy ``prompt`` — synthesises a single-task batch
    """

    tasks: List[ClaudeTaskSpec] = Field(
        default_factory=list,
        description="List of Claude tasks to run in parallel (max 5 concurrent).",
        json_schema_extra={"rows": 1},
    )
    # Legacy single-task fallback ----------------------------------------
    prompt: str = Field(
        default="",
        description="Legacy: single-prompt fallback used only when "
                    "`tasks` is empty.",
        json_schema_extra={"rows": 4, "placeholder": "Or use the tasks array..."},
    )
    model: str = Field(
        default="claude-sonnet-4-6",
        description="Default model for tasks that don't override it.",
    )
    system_prompt: Optional[str] = Field(default=None, json_schema_extra={"rows": 3})
    working_directory: Optional[str] = Field(
        default=None,
        description="Git repo root. Defaults to the workflow's workspace dir.",
    )
    max_parallel: int = Field(
        default=5, ge=1, le=20,
        description="Concurrency cap.",
    )
    allowed_credentials: List[str] = Field(
        default_factory=list,
        description="Credential names the CLI is permitted to fetch via MCP.",
    )

    # Saved workflow JSON may persist these list fields as `null` rather
    # than `[]` when the user has never edited them. Coerce so Pydantic's
    # strict list validation doesn't reject the params on load.
    @field_validator("tasks", "allowed_credentials", mode="before")
    @classmethod
    def _none_is_empty_list(cls, v: Any) -> Any:
        return [] if v is None else v

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
    description = (
        "Run N parallel Claude Code CLI sessions over a list of tasks. "
        "Each task is isolated in its own git worktree."
    )
    component_kind = "agent"
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
        self, ctx: NodeContext, params: ClaudeCodeAgentParams,
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
            node_id, "executing",
            {"message": "Starting Claude Code batch..."},
            workflow_id=workflow_id,
        )

        # Collect connected memory/skills/tools/input/task in one pass —
        # same edge-walker the AI Agent uses (services/plugin/edge_walker.py).
        # This must run BEFORE prompt resolution so the auto-fallback can
        # read from `input_data` exactly the way `nodes/agent/_inline.py`
        # does for the standard agent path.
        database = get_database()
        _, skill_data, tool_data, input_data, _ = await collect_agent_connections(
            node_id, ctx.raw, database, log_prefix="[Claude Code]",
        )
        connected_skills = [
            s.get("skill_name") or s.get("label")
            for s in skill_data
            if s.get("skill_name") or s.get("label")
        ]

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
            tasks = [
                ClaudeTaskSpec(
                    prompt=prompt,
                    model=params.model,
                    system_prompt=params.system_prompt,
                ),
            ]
        else:
            # Apply node-level defaults to tasks that don't override
            for i, t in enumerate(tasks):
                changed: dict = {}
                if not t.model and params.model:
                    changed["model"] = params.model
                if not t.system_prompt and params.system_prompt:
                    changed["system_prompt"] = params.system_prompt
                if changed:
                    tasks[i] = t.model_copy(update=changed)

        # Workspace dir — workflow.py injects this into context
        workspace_dir = ctx.raw.get("workspace_dir") or params.working_directory
        if workspace_dir is None:
            from core.config import Settings
            workspace_dir = Path(Settings().workspace_base_resolved) / (
                workflow_id or "default"
            )
        workspace_dir = Path(workspace_dir)

        repo_root = (
            Path(params.working_directory) if params.working_directory else None
        )

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
            allowed_credentials=params.allowed_credentials,
            max_parallel=params.max_parallel,
        )

        elapsed = time.time() - start_time
        logger.debug(
            "[claude_code_agent] node=%s tasks=%d ok=%d fail=%d elapsed=%.2fs",
            node_id, result.n_tasks, result.n_succeeded, result.n_failed, elapsed,
        )

        await broadcaster.update_node_status(
            node_id, "success" if result.n_failed == 0 else "warning",
            {
                "message": (
                    f"Batch complete: {result.n_succeeded}/{result.n_tasks} "
                    f"succeeded"
                ),
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

