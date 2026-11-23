from .._handles import team_lead_agent_handles
from .._specialized import SpecializedAgentBase


class OrchestratorAgentNode(SpecializedAgentBase):
    type = "orchestrator_agent"
    display_name = "Orchestrator Agent"
    subtitle = "Agent Coordination"
    group = ("agent",)
    description = "Team lead that delegates to connected specialized agents"
    handles = team_lead_agent_handles()
    ui_hints = {**SpecializedAgentBase.ui_hints, "createsAgentBundle": True}
    # No isTaskManagerPanel here: the panel dispatch in MiddleSection is
    # exclusive, so the hint would replace the agent's parameter form
    # (prompt / provider / model) with the task board. The board lives on
    # the taskManager tool node connected to this lead's input-tools.
    # ui_hints inherits STD_AGENT_HINTS from SpecializedAgentBase.
    tool_description = (
        "ONE-SHOT delegation to Orchestrator Agent. Call ONCE per task, returns task_id. Coordinates multiple agents - do NOT re-call."
    )
