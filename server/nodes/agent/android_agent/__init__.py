from .._specialized import SpecializedAgentBase


class AndroidAgentNode(SpecializedAgentBase):
    type = "android_agent"
    display_name = "Android Agent"
    subtitle = "Device Control"
    group = ("agent",)
    description = "AI agent for Android device control"
