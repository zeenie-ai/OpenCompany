from .._specialized import SpecializedAgentBase


class TaskAgentNode(SpecializedAgentBase):
    type = "task_agent"
    display_name = "Task Agent"
    subtitle = "Task Automation"
    group = ("agent",)
    description = "AI agent for task automation"
