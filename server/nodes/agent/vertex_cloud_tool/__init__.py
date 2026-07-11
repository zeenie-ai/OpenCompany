"""Vertex Cloud Tool — display-only canvas node.

Minted dynamically by ``vertex_managed_agent`` (see its ``_ops.py``) to
show which cloud-side tools the managed agent used during a run
(sandbox commands, Google Search, URL context, ...). The managed agent
runs entirely in Google's cloud, so these nodes are the canvas-visible
trace of that remote activity: one node per distinct tool, pulsed
executing->success after each turn that used it.

Pure display: default ``_EmptyParams`` / ``_EmptyOutput`` and no
``@Operation`` (the plugin contract explicitly exempts pure-display
plugins). It is never scheduled by the executor — nodes wired to an
agent's ``input-tools`` handle are excluded as sub-nodes.
"""

from __future__ import annotations

from services.plugin import ActionNode


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
    handles = (
        {
            "name": "output-main",
            "kind": "output",
            "position": "right",
            "label": "Tool",
            "role": "tools",
        },
    )
    annotations = {"destructive": False, "readonly": True, "open_world": False}
