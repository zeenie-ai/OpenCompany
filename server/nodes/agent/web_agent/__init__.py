from .._specialized import SpecializedAgentBase


class WebAgentNode(SpecializedAgentBase):
    type = "web_agent"
    display_name = "Web Agent"
    subtitle = "Browser Automation"
    group = ("agent",)
    description = "AI agent for web automation"
