# AI Employee (`ai_employee`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/ai_employee/__init__.py`](../../../server/nodes/agent/ai_employee/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Team lead** | **yes** -- `input-teammates` handle enabled (`team_lead_agent_handles()`) |
| **Tests** | [Shared agent behavior](../../../server/tests/nodes/test_specialized_agents.py) and [managed Talk, job admission and specialist assignment](../../../server/tests/services/employees/test_team_plan_admission.py) |

## Purpose

In recipe v3 the Chat Employee uses this node to converse with the owner,
assign work to its Orchestrator and review the outer task. The same node
type can serve as a nested manager. The admitted role and reporting lines
determine which tools its instance receives. In recipe v2 the AI Employee
is the job lead identified by the Talk contact's `input-employee-team`.

## Staffing and assignment

Chat and the Orchestrator own separate Context, instructions, Task Managers
and Task Completed triggers. Chat's board displays its assignment to the
Orchestrator. The Orchestrator and nested managers assign work to connected
reports and review each returned submission. Only the outer submission
returns to Chat for review and the user-facing answer. [Managed team workflow](../../employee_teams.md)

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
