from typing import Literal

from pydantic import Field

from .._specialized import SpecializedAgentBase, SpecializedAgentParams


class CodingAgentParams(SpecializedAgentParams):
    tool_mode: Literal["connected", "workspace"] = Field(
        default="connected",
        description="Connected tools only, or add the workflow workspace coding toolkit.",
    )


class CodingAgentNode(SpecializedAgentBase):
    type = "coding_agent"
    display_name = "Coding Agent"
    subtitle = "Code Execution"
    group = ("agent",)
    description = "AI agent for code execution"
    ui_hints = {**SpecializedAgentBase.ui_hints, "createsAgentBundle": True}
    Params = CodingAgentParams
