# AI Employee (`ai_employee`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/ai_employee/__init__.py`](../../../server/nodes/agent/ai_employee/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Team lead** | **yes** -- `input-teammates` handle enabled (`team_lead_agent_handles()`) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

Team-lead agent identical in behaviour to `orchestrator_agent`. Distinct
only in frontend presentation (display name "AI Employee", subtitle
"Team Orchestration"). Backend routing is the same:
`SpecializedAgentBase.execute_op` -> `prepare_agent_call`
(`collect_teammate_connections`) -> internal teammate descriptors plus the
intrinsic Task Manager -> `execute_chat_agent`.

## What is unique to this node

- **`input-teammates` handle** (same as `orchestrator_agent`, via
  `team_lead_agent_handles()`).
- **No extra parameters**: reuses `SpecializedAgentParams` verbatim. There
  is **no** `teamMode` or `maxConcurrent` field on the current Pydantic
  model — those claims were stale.
- **`tool_description`** declared for when a parent team lead delegates to it.

## Behaviour

See **[Orchestrator Agent](./orchestratorAgent.md)** for the teammate
expansion details, and **[Generic Specialized Agent Pattern](./_pattern.md)**
for the shared contract.

## Edge cases

- Same as `orchestrator_agent`: non-agent teammates are rejected by topology
  validation; durable assignments use the root-wide concurrency coordinator.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Sibling team lead**: [`orchestratorAgent`](./orchestratorAgent.md)
- **Architecture**: [Agent Teams](../../agent_teams.md)
