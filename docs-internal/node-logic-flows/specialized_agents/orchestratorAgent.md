# Orchestrator Agent (`orchestrator_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/orchestrator_agent/__init__.py`](../../../server/nodes/agent/orchestrator_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Team lead** | **yes** -- `input-teammates` handle enabled (`team_lead_agent_handles()`) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

Team-lead agent that coordinates multiple specialized agents. Teammates
are wired via the extra `input-teammates` handle and become
`delegate_to_<agent_type>` tools the orchestrator can call.

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
  calls `collect_teammate_connections` to expand teammates into tools.

## Teammate collection

`collect_teammate_connections(node_id, context, database)` in
[`server/services/plugin/edge_walker.py`](../../../server/services/plugin/edge_walker.py):

1. Scans `context.edges` for `edge.target == node_id` and
   `edge.targetHandle == 'input-teammates'`.
2. Resolves the source node in `context.nodes`.
3. Filters to `node_type in AI_AGENT_TYPES`.
4. Loads `database.get_node_parameters(source_id)` for each teammate.
5. Returns a list of `{node_id, node_type, label, parameters}` dicts.

`prepare_agent_call` then walks each teammate's own `input-tools` edges to
populate `child_tools` (so the delegation tool description lists what each
teammate can do) and appends the teammates to `tool_data` before
`execute_chat_agent` is called, so the LLM sees them as ordinary
`delegate_to_*` tools.

## Behaviour

Inputs, parameters, outputs, logic flow -- see **[Generic Specialized
Agent Pattern](./_pattern.md)**. The only difference is the teammate
expansion above.

## Edge cases

- Non-`AI_AGENT_TYPES` nodes wired to `input-teammates` are silently
  skipped.
- Concurrency of `delegate_to_*` calls is not controlled by any node
  parameter (no `teamMode`); it depends on how the downstream
  `delegate_to_*` tool implementation / Temporal child-workflow dispatch
  handles concurrent invocations.
- When zero teammates are connected, the node behaves identically to any
  other specialized agent.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Sibling team lead**: [`aiEmployee`](./aiEmployee.md)
- **Architecture**: [Agent Teams](../../agent_teams.md)
