# AI Employee (`ai_employee`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/ai_employee/__init__.py`](../../../server/nodes/agent/ai_employee/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Team lead** | **yes** -- `input-teammates` handle enabled (`team_lead_agent_handles()`) |
| **Tests** | [Shared agent behavior](../../../server/tests/nodes/test_specialized_agents.py) and [managed Talk, job admission and specialist assignment](../../../server/tests/services/employees/test_team_plan_admission.py) |

## Purpose

The AI Employee is the lead of a managed employee team. It shares the
orchestration runtime with `orchestrator_agent`. Its `leadsEmployeeTeam` hint
also lets its `output-top` connect to a Talk contact's `input-employee-team`.
Talk is an `aiAgent` that holds the conversation with the owner. This connection
identifies the AI Employee that receives jobs submitted through Talk.

## Staffing and assignment

Hire gives Talk and the lead their own Task Manager and Master Skill nodes.
When the owner requests a specialist, Talk uses its connected Agent Builder
to wire the specialist's `output-top` to the lead's `input-teammates`.
The specialist's tools connect to its own `input-tools`.

Once the updated team is active, Talk submits the job through its Task Manager.
The lead uses its own manager's `spawn_<teammate>` tool to create an instance
of the connected specialist and assign its first task. The lead reviews the
result before delivery. [Managed AI employee teams](../../employee_teams.md)

## Behaviour

The lead uses the shared `SpecializedAgentParams` schema. Its
`tool_description` describes its role when a parent lead delegates to it.
See **[Orchestrator Agent](./orchestrator_agent.md)** for the teammate
expansion details, and **[Generic Specialized Agent Pattern](./_pattern.md)**
for the shared contract.

## Edge cases

- Same as `orchestrator_agent`: non-agent teammates are rejected by topology
  validation; durable assignments use the root-wide concurrency coordinator.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Sibling team lead**: [`orchestratorAgent`](./orchestrator_agent.md)
- **Architecture**: [Agent Teams](../../agent_teams.md)
