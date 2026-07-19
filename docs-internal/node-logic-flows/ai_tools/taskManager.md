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
| `list_tasks` | optional `status_filter`, `include_history` | any |
| `get_task` | `task_id` | any |
| `inspect_task_trace` | `task_id`; optional attempt, detail, cursor, limit; grep-style query/filter/context fields | any registered attempt |
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
`delegation_request`. Temporal starts a detached `DelegatedTaskWorkflow` and
returns `queued` immediately. The runner owns admission, child execution,
compact result/usage persistence, `taskTrigger`, and permit release. Legacy
execution passes the same precreated task ID into its background child bridge.
Retries therefore cannot duplicate tasks.

Each claimed attempt persists the actual Temporal parent, detached runner, and
child workflow/run identities. `inspect_task_trace` authorizes through the
task's saved workflow/lead/execution scope and then reads that persisted
identity; callers cannot supply an arbitrary Temporal workflow ID. It returns
sanitized `summary`, `failures`, or `timeline` events in cursor pages (50 by
default, 100 maximum), with explicit `execution_not_registered`,
`temporal_unavailable`, and `retention_expired` states. Trace inspection is
audited. Historical task results remain readable when Temporal history has
expired.

For large histories, `detail="search"` performs a bounded server-side scan of
sanitized event metadata. `query` is required; `search_mode` is `literal`,
`all_terms`, or `any_terms`; optional category filters cover activity, child,
failure, signal, timer, and workflow events. Results mark exact matches and
include zero to five neighboring events. Each call scans at most 500 events and
returns an opaque `next_cursor`, allowing the lead to progressively investigate
a trace without placing the complete history in model context.

The human panel includes durable history by default, so workflow restarts do
not clear the visible list. Specific execution selections remain scoped for
safe mutations. Created/started/completed timestamps are normalized as UTC and
shown locally. Elapsed time starts at `started_at`, ticks while running, and
freezes at `completed_at`. Usage shows input, output, and total tokens, with a
fallback to usage embedded in historical result envelopes.

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
