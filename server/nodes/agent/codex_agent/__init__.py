"""OpenAI Codex CLI agent — multi-instance via `AICliService`.

Sandbox-first companion to `claude_code_agent`. Each task gets its own
git worktree; sandbox is enforced by Codex itself, not by us.

Codex has no session/resume/budget/turns surface — `CodexTaskSpec`
exposes only `sandbox` + `ask_for_approval` as task-level overrides.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue
from services.plugin.edge_walker import collect_agent_connections

from .._handles import STD_AGENT_HINTS, std_agent_handles

logger = get_logger(__name__)


from services.cli_agent import CodexTaskSpec  # noqa: E402


class CodexAgentParams(BaseModel):
    """Multi-task batch parameters for Codex."""

    tasks: List[CodexTaskSpec] = Field(
        default_factory=list,
        description="List of Codex tasks to run in parallel (max 5 concurrent).",
        json_schema_extra={"rows": 1},
    )
    prompt: str = Field(
        default="",
        description="Legacy: single-prompt fallback used only when " "`tasks` is empty.",
        json_schema_extra={"rows": 4, "placeholder": "Or use the tasks array..."},
    )
    model: str = Field(
        default="gpt-5.2-codex",
        description="Default model for tasks that don't override it.",
    )
    sandbox: str = Field(
        default="workspace-write",
        description="Default sandbox for tasks that don't override it. " "One of: read-only | workspace-write | danger-full-access.",
    )
    ask_for_approval: str = Field(
        default="never",
        description="Default approval mode: untrusted | on-request | never.",
    )
    system_prompt: Optional[str] = Field(default=None, json_schema_extra={"rows": 3})
    working_directory: Optional[str] = None
    max_parallel: int = Field(default=5, ge=1, le=20)
    allowed_credentials: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class CodexAgentOutput(BaseModel):
    success: bool = True
    n_tasks: int = 0
    n_succeeded: int = 0
    n_failed: int = 0
    wall_clock_ms: int = 0
    tasks: List[Any] = Field(default_factory=list)
    provider: str = "codex"
    timestamp: Optional[str] = None
    # Legacy single-task convenience
    response: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class CodexAgentNode(ActionNode):
    type = "codex_agent"
    display_name = "Codex"
    subtitle = "Sandboxed Coding"
    group = ("agent",)
    description = "Run N parallel OpenAI Codex CLI sessions. Sandbox enforced " "by Codex itself; per-task git worktree isolation."
    component_kind = "agent"
    handles = std_agent_handles()
    ui_hints = STD_AGENT_HINTS
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = TaskQueue.AI_HEAVY

    Params = CodexAgentParams
    Output = CodexAgentOutput

    @Operation(
        "execute",
        cost={"service": "codex_agent", "action": "run", "count": 1},
    )
    async def execute_op(
        self,
        ctx: NodeContext,
        params: CodexAgentParams,
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
            {"message": "Starting Codex batch..."},
            workflow_id=workflow_id,
        )

        tasks = list(params.tasks)
        if not tasks:
            prompt = params.prompt or self._infer_prompt_from_inputs(ctx, node_id)
            if not prompt:
                raise RuntimeError("codex_agent: provide either `tasks` or `prompt`")
            tasks = [
                CodexTaskSpec(
                    prompt=prompt,
                    model=params.model,
                    sandbox=params.sandbox,  # type: ignore[arg-type]
                    ask_for_approval=params.ask_for_approval,  # type: ignore[arg-type]
                    system_prompt=params.system_prompt,
                ),
            ]
        else:
            for i, t in enumerate(tasks):
                changed: dict = {}
                if not t.model and params.model:
                    changed["model"] = params.model
                if not t.system_prompt and params.system_prompt:
                    changed["system_prompt"] = params.system_prompt
                if changed:
                    tasks[i] = t.model_copy(update=changed)

        database = get_database()
        _, skill_data, tool_data, _, _ = await collect_agent_connections(
            node_id,
            ctx.raw,
            database,
        )
        connected_skills = [s.get("skill_name") or s.get("label") for s in skill_data if s.get("skill_name") or s.get("label")]

        workspace_dir = ctx.raw.get("workspace_dir") or params.working_directory
        if workspace_dir is None:
            from core.config import Settings

            workspace_dir = Path(Settings().workspace_base_resolved) / (workflow_id or "default")
        workspace_dir = Path(workspace_dir)

        repo_root = Path(params.working_directory) if params.working_directory else None

        svc = get_ai_cli_service()
        result = await svc.run_batch(
            "codex",
            tasks=tasks,
            node_id=node_id,
            workflow_id=workflow_id or "",
            workspace_dir=workspace_dir,
            broadcaster=broadcaster,
            repo_root=repo_root,
            connected_skill_names=connected_skills,
            connected_skill_descriptors=skill_data,
            connected_tools=tool_data,
            execution_id=ctx.execution_id,
            allowed_credentials=params.allowed_credentials,
            max_parallel=params.max_parallel,
        )

        elapsed = time.time() - start_time
        logger.debug(
            "[codex_agent] node=%s tasks=%d ok=%d fail=%d elapsed=%.2fs",
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

        legacy_response = result.tasks[0].response if len(result.tasks) == 1 else None

        return {
            "success": result.n_failed == 0,
            "n_tasks": result.n_tasks,
            "n_succeeded": result.n_succeeded,
            "n_failed": result.n_failed,
            "total_cost_usd": result.total_cost_usd,  # always None for Codex
            "wall_clock_ms": result.wall_clock_ms,
            "tasks": task_models,
            "provider": result.provider,
            "timestamp": result.timestamp or datetime.now().isoformat(),
            "response": legacy_response,
        }

    @staticmethod
    def _infer_prompt_from_inputs(ctx: NodeContext, node_id: str) -> str:
        for edge in ctx.raw.get("edges", []):
            if edge.get("target") != node_id:
                continue
            handle = edge.get("targetHandle")
            if handle not in ("input-main", None):
                continue
            src = ctx.raw.get("outputs", {}).get(edge.get("source"), {})
            if isinstance(src, dict):
                for k in ("message", "text", "content", "prompt"):
                    val = src.get(k)
                    if val:
                        return str(val)
                return str(src)
            elif src:
                return str(src)
        return ""
