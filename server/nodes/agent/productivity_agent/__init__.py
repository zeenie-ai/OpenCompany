from .._specialized import SpecializedAgentBase


class ProductivityAgentNode(SpecializedAgentBase):
    type = "productivity_agent"
    display_name = "Productivity Agent"
    subtitle = "Workflows"
    group = ("agent",)
    description = "AI agent for productivity workflows"
