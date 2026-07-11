"""Vertex Cloud Tool — display-only canvas node.

Minted dynamically by ``vertex_managed_agent`` (see its ``_ops.py``) to
show which cloud-side tools the managed agent used during a run
(sandbox commands, Google Search, URL context, ...). The managed agent
runs entirely in Google's cloud, so these nodes are the canvas-visible
trace of that remote activity: one node per distinct tool, pulsed
executing->success after each turn that used it.

Display-only, but it declares a real ``Params`` model: the frontend
fetches every node type's input schema for the parameter panel, and
plugins that stay on the default ``_EmptyParams`` are deliberately NOT
registered into ``NODE_INPUT_MODELS`` (``BaseNode.__init_subclass__``
passes ``input_model=None``), which logs a "No plugin Params
registered" warning and leaves the panel schema-less. The fields mirror
what ``_ops.ensure_cloud_tool_nodes`` persists per minted node. The
single ``info`` operation just echoes them — the run button is hidden
and the executor never schedules this node anyway (nodes wired to an
agent's ``input-tools`` handle are excluded as sub-nodes).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class VertexCloudToolParams(BaseModel):
    """Set by the minting helper — not meant to be edited by hand."""

    cloud_tool_key: Optional[str] = Field(
        default=None,
        description="Stable key of the cloud-side tool (type:... or fn:...).",
    )
    label: Optional[str] = Field(
        default=None,
        description="Display label of the cloud-side tool.",
    )

    model_config = ConfigDict(extra="ignore")


class VertexCloudToolOutput(BaseModel):
    cloud_tool_key: Optional[str] = None
    label: Optional[str] = None
    message: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class VertexCloudToolNode(ActionNode):
    type = "vertexCloudTool"
    display_name = "Cloud Tool"
    subtitle = "Vertex Activity"
    group = ("tool",)
    description = (
        "Shows a cloud-side tool the Vertex managed agent used. Created "
        "automatically; safe to delete."
    )
    # "square" (not "tool"): renders identically via SquareNode but skips
    # the ToolSchemaEditor panel — there is no schema to edit on a
    # display-only node. group=("tool",) still auto-derives isConfigNode.
    component_kind = "square"
    ui_hints = {"hideRunButton": True}
    # Never expose to LLMs as a callable tool — it has no behavior.
    usable_as_tool = False
    # Tool nodes hang below the agent and connect upward: output on TOP
    # (house shape — see calculator_tool), into the agent's bottom
    # input-tools handle.
    handles = (
        {
            "name": "output-tool",
            "kind": "output",
            "position": "top",
            "label": "Tool",
            "role": "tools",
        },
    )
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.REST_API

    Params = VertexCloudToolParams
    Output = VertexCloudToolOutput

    @Operation("info")
    async def info_op(
        self,
        ctx: NodeContext,
        params: VertexCloudToolParams,
    ) -> VertexCloudToolOutput:
        """Echo the display metadata (this node performs no work)."""
        return VertexCloudToolOutput(
            cloud_tool_key=params.cloud_tool_key,
            label=params.label,
            message=(
                "Display node: shows a cloud-side tool used by the Vertex "
                "managed agent."
            ),
        )
