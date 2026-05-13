from .._specialized import SpecializedAgentBase


class SocialAgentNode(SpecializedAgentBase):
    type = "social_agent"
    display_name = "Social Agent"
    subtitle = "Social Messaging"
    group = ("agent",)
    description = "AI agent for social messaging"
