# Orchestrator Agent (`orchestrator_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/orchestrator_agent/__init__.py`](../../../server/nodes/agent/orchestrator_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Team lead** | **yes** -- `input-teammates` handle enabled (`team_lead_agent_handles()`) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

The Orchestrator coordinates agents wired through `input-teammates`. In a
recipe v3 hire it receives the outer task on the Chat Employee's board. Its
own Task Manager assigns work to its connected reports. A nested manager
can delegate again under the same recorded assignment and review rules.
The Orchestrator reviews its children's results before returning its own
submission to Chat. [Managed team workflow](../../employee_teams.md)

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

`prepare_agent_call` walks each teammate's `input-tools` edges to populate
capability descriptions. Lifecycle version 1 exposes a bound
`spawn_<teammate>` for each connected template and `assign_task` for existing
instances. Version 0 uses `task_manager(operation="assign_task", ...)`.

## Behaviour

Inputs, parameters, outputs, logic flow -- see **[Generic Specialized
Agent Pattern](./_pattern.md)**. The only difference is the teammate
expansion above.

## Edge cases

- Non-`AI_AGENT_TYPES` nodes wired to `input-teammates` are silently
  skipped.
- Concurrency is root-scoped and defaults to three active descendants.
  Additional durable assignments remain queued.
- When a managed job needs a missing specialist, the owner can approve a
  staffing change through Builder, apply it and start fresh work.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Sibling team lead**: [`aiEmployee`](./ai_employee.md)
- **Architecture**: [Agent Teams](../../agent_teams.md)
