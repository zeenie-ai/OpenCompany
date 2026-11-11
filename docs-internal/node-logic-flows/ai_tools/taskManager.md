# Task Manager (`taskManager`)

| Field | Value |
|------|-------|
| **Category** | tool / ai |
| **Backend handler** | [`server/nodes/tool/task_manager/__init__.py::TaskManagerNode.manage`](../../../server/nodes/tool/task_manager/__init__.py) |
| **Tests** | [`server/tests/nodes/test_task_manager_delegation_bridge.py`](../../../server/tests/nodes/test_task_manager_delegation_bridge.py) |
| **Skill (if any)** | [`server/skills/assistant/task-manager/SKILL.md`](../../../server/skills/assistant/task-manager/SKILL.md) |
| **Dual-purpose tool** | ToolNode - tool name `task_manager` |

Task Manager is the durable control plane intrinsically bound to
`orchestrator_agent` and `ai_employee`. It is hidden from the palette and Agent
Builder, so users cannot remove the lead's task capability. Historical explicit
nodes remain readable and protected from deletion.

Version 2 employee recipes also bind this tool to the separate Talk agent. Talk
uses `submit_job` to acknowledge substantive work promptly; the lead uses the
existing assignment and review operations. Specialists connect through the
lead's canonical `input-teammates` handle and keep their own Context and scoped
tools. The recipe does not add job-forwarding nodes or a duplicate Task Manager.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Declared (left, role `main`); the `manage` op reads scope from `ctx.raw`, not from upstream data |

`output-tool` (top, role `tools`) is the only output handle; connect it to a
team lead's `input-tools`. `ui_hints` = `isTaskManagerPanel`,
`hideInputSection`, `hideOutputSection`, `hideRunButton` (all `True`);
`needs_canvas = True`.

## Parameters

`TaskManagerParams` remains permissive for saved node settings, including
legacy labels and nullable fields. Model calls use the separate, locked
`TaskManagerToolInput`: `operation` is required and unknown fields are rejected
before task persistence or delegation. Saved settings cannot supply arguments
missing from a model call, and a persisted custom tool schema cannot replace
this input contract.

Pydantic validates the operation-specific requirements below. Required strings
must contain non-whitespace text; `modify_task` must supply at least one
non-null `title`, `mission`, `context`, or `acceptance_criteria` (an empty object
is a supplied value). Trace search requires a nonblank `query`. Other nullable
fields may remain null. Authorization, connected-teammate resolution,
task state and revision checks remain in the trusted runtime. Correctable
input errors return through the existing tool-error loop without creating a task.

The table lists saved parameter defaults; model calls must always provide
`operation`. No field carries `displayOptions.show`.

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `operation` | `submit_job` / `assign_task` / `list_tasks` / `get_task` / `modify_task` / `cancel_task` / `retry_task` / `reassign_task` / `accept_task` / `finish_team` / `mark_done` / `inspect_task_trace` | `list_tasks` | no | - | Operation to run; `mark_done` is a deprecated alias for `accept_task` |
| `task_id` | string | `None` | no | - | Target task. Required by `get_task`, `inspect_task_trace`, `modify_task`, `cancel_task`, `retry_task`, `reassign_task`; `accept_task` / `mark_done` may omit it when exactly one submitted task exists |
| `title` | string (max 500) | `None` | no | - | Task title; required by `assign_task`, editable via `modify_task` |
| `mission` | string (max 10000) | `None` | no | - | Mission and expected result; required by `submit_job` and `assign_task`, editable via `modify_task` |
| `context` | object | `None` | no | - | Free-form context stored on the task and forwarded to the assignee |
| `acceptance_criteria` | object | `None` | no | - | Acceptance criteria stored on the task |
| `depends_on` | string[] | `None` | no | - | Task ids this task waits on (`assign_task`) |
| `assignee_node_id` | string | `None` | no | - | Connected teammate node id (`assign_task` / `reassign_task`); with `delegate_name`, must resolve to exactly one connected teammate |
| `delegate_name` | string | `None` | no | - | Teammate `delegate_tool_name`, alternative to `assignee_node_id` |
| `expected_revision` | int (ge 0) | `None` | no | - | Optimistic-concurrency revision for mutations; read from the task when omitted |
| `revision` | int (ge 0) | `None` | no | - | Explicit tool-input compatibility spelling; a non-null `expected_revision` takes precedence |
| `reason` | string (max 2000) | `None` | no | - | Cancellation reason (`cancel_task`) |
| `status_filter` | string | `None` | no | - | `list_tasks` status filter |
| `include_history` | boolean | `false` | no | - | `list_tasks`: include prior executions' durable history |
| `attempt` | int (ge 0) | `None` | no | - | `inspect_task_trace`: attempt to inspect |
| `cursor` | string | `None` | no | - | `inspect_task_trace`: opaque page cursor |
| `limit` | int (ge 1, le 100) | `50` | no | - | `inspect_task_trace`: page size |
| `detail` | `summary` / `failures` / `timeline` / `search` | `summary` | no | - | `inspect_task_trace` detail mode |
| `query` | string (max 200) | `None` | no | - | `inspect_task_trace`: search text (required when `detail=search`) |
| `search_mode` | `literal` / `all_terms` / `any_terms` | `literal` | no | - | `inspect_task_trace`: search matching |
| `case_sensitive` | boolean | `false` | no | - | `inspect_task_trace`: case-sensitive search |
| `context_lines` | int (ge 0, le 5) | `2` | no | - | `inspect_task_trace`: neighbouring events returned around each match |
| `scan_limit` | int (ge 1, le 500) | `250` | no | - | `inspect_task_trace`: max events scanned per call |
| `categories` | (`failure` / `activity` / `child` / `signal` / `timer` / `workflow`)[] | `None` | no | - | `inspect_task_trace`: event category filter |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-tool` | object | Tool result returned to the LLM (`TaskManagerOutput`, `extra="allow"`) |

### Output payload

```ts
{
  success: boolean;      // always true on the success path
  operation?: string;    // echoed operation
  task?: object;         // get_task, assign_task and every mutation
  tasks?: object[];      // list_tasks
  team?: object;         // finish_team
  job_id?: string;       // submit_job: durable employee job identity
  state?: string;        // submit_job: job state at admission
  acknowledgement?: string; // submit_job: acknowledgement, not the final result
}
```

`extra="allow"` lets per-operation extras through: `count` (`list_tasks`),
`trace` (`inspect_task_trace`), `delegation` + `delegation_request`
(`assign_task`), and `delegation_request` on `retry_task` / `reassign_task`.

## Scope and authorization

The runtime injects workflow, execution, root execution, team, and lead IDs.
Model and browser payloads cannot select an arbitrary team. Assignment and
reassignment resolve against fresh canonical `input-teammates` descriptors and
the persisted execution membership snapshot.

## Operations

| Operation | Required fields | Valid source state |
|---|---|---|
| `submit_job` | `mission`; trusted version 2 employee Talk context | new job or idempotent existing admission |
| `assign_task` | `title`, `mission`, connected `assignee_node_id` or exact delegate name | new |
| `list_tasks` | optional `status_filter`, `include_history` | any |
| `get_task` | `task_id` | any |
| `inspect_task_trace` | `task_id`; optional attempt, detail, cursor, limit; grep-style query/filter/context fields | any registered attempt |
| `modify_task` | `task_id`, at least one changed field; optional `expected_revision` | blocked/queued |
| `cancel_task` | `task_id`; optional `expected_revision`, `reason` | blocked/queued/running |
| `retry_task` | `task_id`; optional `expected_revision` | failed/submitted/cancelled |
| `reassign_task` | `task_id`, connected assignee; optional `expected_revision` | failed/submitted/cancelled |
| `accept_task` | `task_id` unless one submission is unambiguous; optional `expected_revision` | submitted |
| `finish_team` | none | every task accepted/cancelled |

`revision` is accepted as a compatibility spelling of `expected_revision`.
When both non-null fields are supplied, `expected_revision` wins. An omitted
revision is read from the current task. When
exactly one submitted task exists in the scoped execution, `accept_task` may
omit both fields and safely resolves that task. Zero or multiple submissions
produce an error instructing the lead to list/review tasks first.

`mark_done` is a deprecated alias for `accept_task`; it does not remove records.

Example (replace the assignee with an ID from the connected teammate list):

```json
{"operation":"assign_task","title":"Research","mission":"Find cited evidence for the report","assignee_node_id":"web-agent","context":{"topic":"queues"},"acceptance_criteria":{"sources":"Cite primary sources"}}
```

Both assignee identifiers may be supplied if they resolve to the same connected
teammate. The tool does not repair corrupted field names, infer missions from
`task`, or accept runtime scope such as `workflow_id` from model arguments.

## Invalid calls and recovery

A tool call can be valid JSON and still have invalid arguments. For example,
curly quotes or prose inside a property name do not supply the canonical
`title`, `mission`, or `assignee_node_id` field. JSON parsing may therefore
succeed while `TaskManagerToolInput` rejects the call.

Native and Pi assignment preflight pass the unmerged model arguments through
`execute_as_tool`. A validation failure returns a `ValidationError` tool result
before persistence or delegation; the next model turn can correct the call.
The original assistant call is retained in the transcript. A subsequent valid
assignment follows the normal durable task and runner path.

Mutation failures are distinct from input validation. A well-formed
`modify_task` is still refused once a task is running: it only edits blocked
or queued work. `get_task` and `list_tasks` include `allowed_actions` for the
observed status; use those together with `status` and `revision` before choosing
a mutation. These are short action names such as `modify` and `cancel`; use
the corresponding `modify_task` or `cancel_task` tool operation. The database
still checks state and revision atomically, so a task
may change after it is read.

If that check rejects a mutation, the service reports a `TaskMutationConflict`
with the latest observed scoped task state and revision. Task Manager returns
it as a `NodeUserError` with a hint to call `get_task` and review
`allowed_actions`. It does not automatically retry the write, change its
operation, or cancel running work. Refreshing a revision cannot make an
operation valid in the wrong state. Expected conflicts produce a warning
without a traceback; unexpected database failures retain their error type and
normal exception logging. Both are failure results, not successful mutations.

Newly prepared runs receive the locked schema after updated workers are
deployed. Already prepared Temporal runs keep their recorded definitions;
the change does not rewrite history or require a database migration.

If the next model turn takes time, inspect its LLM activity separately from the
completed tool call. Activity heartbeats show worker liveness, not provider
progress. Native LLM-step deadlines and retry status are documented in
[Native LLM SDK](../../native_llm_sdk.md#local-llm-providers-ollama-lm-studio).

## Employee job admission and delivery

`submit_job` is available only to the employee's configured Talk agent. The
server resolves the lead and delivery destinations from the plan captured in
the admitted graph; model arguments cannot choose a different employee or
recipient. The durable job retains the original request, parameters, workspace
generation, and delivery identity across delegation, review, and retries.

The saved graph keeps node data to UI fields, so the plan is saved on the
employee row. Start and Apply copy it onto the lead in the generation's
snapshot, and every deploy (Start and the restart re-arm) copies it onto the
lead in the deployed listeners, which a deploy sanitizes again
(`team_runtime.admit_team_plan`). A run holds only the nodes its trigger
reaches, so the runtime reads the plan, and a job takes its graph, from that
whole snapshot (`team_runtime.admitted_graph`).

The acknowledgement confirms assignment only. Specialists submit work through
Task Manager, and the lead reviews and accepts it. The runtime delivers the
reviewed result once to the original conversation or app, preserving approval
gates. Scheduled and app-triggered lead work uses the same server job boundary.
Existing version 1 graphs keep their compatible execution path.

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
