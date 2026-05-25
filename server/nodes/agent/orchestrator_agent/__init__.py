from .._handles import team_lead_agent_handles
from .._specialized import SpecializedAgentBase


class OrchestratorAgentNode(SpecializedAgentBase):
    type = "orchestrator_agent"
    display_name = "Orchestrator Agent"
    subtitle = "Agent Coordination"
    group = ("agent",)
    description = "Team lead that delegates to connected specialized agents"
    handles = team_lead_agent_handles()
    tool_description = (
        "ONE-SHOT delegation to Orchestrator Agent. Call ONCE per task, returns task_id. Coordinates multiple agents - do NOT re-call."
    )
