from .._handles import team_lead_agent_handles
from .._specialized import SpecializedAgentBase


class AIEmployeeNode(SpecializedAgentBase):
    type = "ai_employee"
    display_name = "AI Employee"
    subtitle = "Team Orchestration"
    group = ("agent",)
    description = "Team lead for multi-agent coordination"
    handles = team_lead_agent_handles()
    tool_description = (
        "ONE-SHOT delegation to AI Employee. Call ONCE per task, returns task_id. Coordinates multiple agents - do NOT re-call."
    )
