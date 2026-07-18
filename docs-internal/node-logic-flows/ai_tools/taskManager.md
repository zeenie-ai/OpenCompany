# Task Manager (`taskManager`)

Task Manager is the durable control plane intrinsically bound to
`orchestrator_agent` and `ai_employee`. It is hidden from the palette and Agent
Builder, so users cannot remove the lead's task capability. Historical explicit
nodes remain readable and protected from deletion.

## Scope and authorization

The runtime injects workflow, execution, root execution, team, and lead IDs.
Model and browser payloads cannot select an arbitrary team. Assignment and
reassignment resolve against fresh canonical `input-teammates` descriptors and
the persisted execution membership snapshot.

## Operations

| Operation | Required fields | Valid source state |
|---|---|---|
| `assign_task` | `title`, `mission`, connected `assignee_node_id` or exact delegate name | new |
| `list_tasks` | optional `status_filter` | any |
| `get_task` | `task_id` | any |
| `modify_task` | `task_id`, `expected_revision`, changed fields | blocked/queued |
| `cancel_task` | `task_id`, `expected_revision`, optional `reason` | blocked/queued/running |
| `retry_task` | `task_id`, `expected_revision` | failed/submitted/cancelled |
| `reassign_task` | task/revision and connected assignee | failed/submitted/cancelled |
| `accept_task` | task/revision, normally | submitted |
| `finish_team` | none | every task accepted/cancelled |

`revision` is accepted as a compatibility alias for `expected_revision`. When
exactly one submitted task exists in the scoped execution, `accept_task` may
omit both fields and safely resolves that task. Zero or multiple submissions
produce an error instructing the lead to list/review tasks first.

`mark_done` is a deprecated alias for `accept_task`; it does not remove records.

## Assignment result

`assign_task` persists the task before returning a trusted
`delegation_request`. Temporal preflights same-turn assignments, starts children
under the root-wide permit coordinator, and returns the child result alongside
the task identity. Legacy execution passes the same precreated task ID into its
child bridge. Retries therefore cannot duplicate tasks.

## Status semantics

- `blocked`: dependencies unresolved
- `queued`: waiting for admission
- `running`: child owns the current attempt
- `submitted`: child returned; lead review required
- `accepted`: approved and counted as Done
- `failed`: unresolved terminal attempt
- `cancelled`: intentionally stopped/waived

See [Agent Teams](../../agent_teams.md) and
[Team Monitor](../chat_utility/teamMonitor.md).
