from .._specialized import SpecializedAgentBase


class AutonomousAgentNode(SpecializedAgentBase):
    type = "autonomous_agent"
    display_name = "Autonomous Agent"
    subtitle = "Autonomous Ops"
    group = ("agent",)
    description = "Autonomous agent using Code Mode patterns"
