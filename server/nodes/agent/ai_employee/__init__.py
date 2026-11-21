from .._handles import team_lead_agent_handles
from .._specialized import SpecializedAgentBase


class AIEmployeeNode(SpecializedAgentBase):
    type = "ai_employee"
    display_name = "AI Employee"
    subtitle = "Team Orchestration"
    group = ("agent",)
    description = "Team lead for multi-agent coordination"
    handles = team_lead_agent_handles()
    # No isTaskManagerPanel here: the panel dispatch in MiddleSection is
    # exclusive, so the hint would replace the agent's parameter form
    # (prompt / provider / model) with the task board. The board lives on
    # the taskManager tool node connected to this lead's input-tools.
    ui_hints = {**SpecializedAgentBase.ui_hints, "createsAgentBundle": True}
    tool_description = (
        "ONE-SHOT delegation to AI Employee. Call ONCE per task, returns task_id. Coordinates multiple agents - do NOT re-call."
    )
