from .._specialized import SpecializedAgentBase


class CodingAgentNode(SpecializedAgentBase):
    type = "coding_agent"
    display_name = "Coding Agent"
    subtitle = "Code Execution"
    group = ("agent",)
    description = "AI agent for code execution"
