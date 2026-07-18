# Orchestrator Agent (`orchestrator_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/orchestrator_agent/__init__.py`](../../../server/nodes/agent/orchestrator_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Team lead** | **yes** -- `input-teammates` handle enabled (`team_lead_agent_handles()`) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

Team-lead agent that coordinates agents wired through `input-teammates`.
Task Manager is intrinsically bound and is the only model-facing delegation
interface. Internal delegate descriptors resolve authorized assignees after a
durable `assign_task` record exists.

## What is unique to this node

- **`input-teammates` handle** (extra input beyond the shared 5; declared
  via `team_lead_agent_handles()` in `_handles.py`).
- **No extra parameters**: uses the same `SpecializedAgentParams` as every
  other agent. There is **no** `teamMode` / `maxConcurrent` field — those
  were removed (or never existed on the current Pydantic model).
- **`tool_description`** declared so a parent team lead delegating to this
  node gets a meaningful tool string ("ONE-SHOT delegation ...").
- **Team lead detection**: `prepare_agent_call` (in `_inline.py`) checks
  `node_type in TEAM_LEAD_TYPES = {'orchestrator_agent', 'ai_employee'}` and
  calls `collect_teammate_connections` to build internal teammate descriptors.
- **Task control panel**: selecting the node opens its non-removable Task
  Manager middle panel.

## Teammate collection

`collect_teammate_connections(node_id, context, database)` in
[`server/services/plugin/edge_walker.py`](../../../server/services/plugin/edge_walker.py):

1. Scans `context.edges` for `edge.target == node_id` and
   `edge.targetHandle == 'input-teammates'`.
2. Resolves the source node in `context.nodes`.
3. Filters to `node_type in AI_AGENT_TYPES`.
4. Loads `database.get_node_parameters(source_id)` for each teammate.
5. Returns a list of `{node_id, node_type, label, parameters}` dicts.

`prepare_agent_call` walks each teammate's own `input-tools` edges to populate
capability descriptions. The LLM receives connected node IDs and calls
`task_manager(operation="assign_task", ...)`; direct delegate tools are hidden.

## Behaviour

Inputs, parameters, outputs, logic flow -- see **[Generic Specialized
Agent Pattern](./_pattern.md)**. The only difference is the teammate
expansion above.

## Edge cases

- Non-`AI_AGENT_TYPES` nodes wired to `input-teammates` are silently
  skipped.
- Concurrency is root-scoped and defaults to three active descendants.
  Additional durable assignments remain queued.
- When zero teammates are connected, the node behaves identically to any
  other specialized agent.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Sibling team lead**: [`aiEmployee`](./aiEmployee.md)
- **Architecture**: [Agent Teams](../../agent_teams.md)
