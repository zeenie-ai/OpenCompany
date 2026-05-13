from .._specialized import SpecializedAgentBase


class ConsumerAgentNode(SpecializedAgentBase):
    type = "consumer_agent"
    display_name = "Consumer Agent"
    subtitle = "Consumer Support"
    group = ("agent",)
    description = "AI agent for consumer interactions"
