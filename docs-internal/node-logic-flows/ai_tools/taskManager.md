# Task Manager (`taskManager`)

Lifecycle version 1 exposes narrow typed tools from
`services.agent_instances.tools`. A connected graph node is an authorized
template; spawning creates an independent instance and its first reviewed
task. The runtime supplies identity, conversation history, title, scope and
scheduling metadata. The model supplies instructions and explicit dependencies.

New generations capture `agent_lifecycle_version=1` and
`agent_protocol_version=1`. Already-admitted generations without those markers
retain version 0, including their tool schemas and later trigger firings.
Lifecycle version is separate from employee recipe version and graph version.

| Field | Value |
|------|-------|
| **Category** | tool / ai |
| **Backend handler** | [`server/nodes/tool/task_manager/__init__.py::TaskManagerNode.manage`](../../../server/nodes/tool/task_manager/__init__.py) |
| **Tests** | [`server/tests/services/agent_instances/`](../../../server/tests/services/agent_instances/), legacy [`test_task_manager_delegation_bridge.py`](../../../server/tests/nodes/test_task_manager_delegation_bridge.py) |
| **Skill (if any)** | [`server/skills/assistant/task-manager/SKILL.md`](../../../server/skills/assistant/task-manager/SKILL.md) |
| **Tool surface** | Version 1: typed bound tools below; version 0: `task_manager` |

Recipe v3 gives Chat and its Orchestrator separate Task Managers. Chat's board
shows the outer job task assigned to the Orchestrator. The Orchestrator's board
shows the subtasks it assigns to its direct reports. A nested manager has its
own board and reviews its children's submissions before submitting its own.
Recipe v2 retains the Talk and lead boards selected by its admitted graph.
[Managed team workflow](../../employee_teams.md)

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Declared (left, role `main`); the `manage` op reads scope from `ctx.raw`, not from upstream data |

`output-tool` (top, role `tools`) connects to its owning agent's `input-tools`.
`ui_hints` = `isTaskManagerPanel`,
`hideInputSection`, `hideOutputSection`, `hideRunButton` (all `True`);
`needs_canvas = True`.

## Lifecycle version 1 tool contract

The common manifest carries the public schema, Pydantic validator, trusted
binding and execution traits across native, Pi, CLI/MCP and RLM. All inputs
reject unknown fields. Saved node settings or a custom tool schema cannot fill
missing model arguments. Execution scope, template selection, provider settings
and sender identity are supplied by the runtime.

Chat intake receives `submit_job` and `get_job_status`. Chat's reviewer can
inspect, accept, retry, cancel or request changes to its outer assignment and
finish the job. Managers have assignment, review, messaging and observation
tools for their reports. Leaf workers have messaging and observation tools.
Full lead instances also receive instance controls. The actual bound
`<teammate>` suffix combines node type and a stable node-ID hash. Use the
published tool name; there is no model-supplied template argument.

| Tool | Required arguments | Optional arguments | Effect / valid source state |
|---|---|---|---|
| `submit_job` | `instructions` | — | Chat submits substantive work. In v3, a retried admission reuses the same job and outer task. A missing specialist can produce a team-change status. |
| `get_job_status` | — | `job_id` | Talk reads the saved job state, task counts and any recorded failure. Omit the ID for this employee's most recently updated job. |
| `spawn_<teammate>` | `instructions` | `after` | A manager creates an instance, task, first attempt and inbox obligation in one commit |
| `assign_task` | `agent`, `instructions` | `after` | A manager assigns new work to an existing teammate instance |
| `send_message` | `agent`, `message` | — | Same-job coordination; return a delivery receipt |
| `list_agents` | — | — | Instances, capabilities and execution state |
| `get_agent` | `agent` | — | Instance and recent runs/results |
| `wait_for_agents` | — | `agents`, `timeout_seconds` | Wait for other instances; default selection excludes caller |
| `list_tasks` | — | `status` | This job's tasks, current references and allowed actions |
| `get_task` | `task_ref` | — | Read task and attempts; reads also accept a task ID in this field |
| `inspect_task` | `task_ref` | `detail`, `query`, `cursor`, `limit` | Bounded stored result and Temporal trace evidence |
| `modify_task` | `task_ref`, `instructions` | `after` | The manager edits a `blocked` or `queued` task. Omitting `after` preserves dependencies. |
| `request_changes` | `task_ref`, `feedback` | — | Append feedback to a `submitted` task and queue another attempt on its instance |
| `accept_task` | `task_ref` | — | Accept a `submitted` task and unblock eligible dependents |
| `retry_task` | `task_ref` | — | Retry a `failed`, `submitted` or `cancelled` task with its requirements, feedback and history |
| `cancel_task` | `task_ref` | `reason` | Cancel a `blocked`, `queued` or `running` task and fence its attempt. Its instance remains reusable. |
| `reassign_<teammate>` | `task_ref` | — | The manager moves a `failed`, `submitted` or `cancelled` task to a fresh instance and attempt |
| `stop_agent` | `agent` | — | Lead: halt admission and interrupt execution; no automatic task retry |
| `resume_agent` | `agent` | — | Lead: reopen a drained, nonclosed instance and its held inbox |
| `close_agent` | `agent` | — | Lead: require no active execution, unresolved tasks or pending inbox |
| `finish_team` | — | — | The Chat reviewer or legacy lead applies the reviewed completion gates. Managed delivery also checks the destination and approval. |

`instructions` and `feedback` must contain nonblank text and are bounded to
10,000 characters. Their whitespace is preserved in storage. `agent` and
`task_ref` are nonblank references of at most 2,000 characters. `after` and
`agents` accept at most 100 references; dependencies are explicit and must
belong to the same job/generation. `reason` is at most 2,000 characters.
`wait_for_agents.timeout_seconds` is an integer from zero to 30, default 30.

`list_tasks.status` is one of `blocked`, `queued`, `running`, `submitted`,
`accepted`, `failed`, `cancelled`. `inspect_task.detail` is `summary` (default),
`failures`, `timeline` or `search`; `query` is at most 200 characters; `limit`
defaults to 25 and ranges from one to 100. It returns stored result excerpts of
at most 4,000 characters with `next_cursor`, plus bounded trace evidence where
available. Stored full results remain readable when Temporal traces are
unavailable; result cursors and Temporal cursors are separate.

### Task references and review

The runtime preserves the exact instructions and derives a short display
title. It does not ask the model to supply `operation`, `title`, `mission`,
`assignee_node_id` or generated acceptance criteria. For example, the arguments
to an exposed spawn binding are simply:

```json
{"instructions":"Find three primary sources on durable queues and summarize their recovery guarantees."}
```

Copy the returned `agent.id` for messages and follow-up assignments. Copy the
latest `task.task_ref` for review; a typical subsequent call is:

```json
{"task_ref":"<copy current task_ref>","feedback":"Add direct links and explain what survives a worker restart."}
```

A versioned `task_ref` carries the observed team, task, revision, attempt and
status. Every mutation checks it atomically. A stale reference returns
`stale_reference`; read again before acting. Unlike version 0, no mutation
infers the only submitted task or silently substitutes its latest revision.
`allowed_actions` uses full names such as `modify_task` and `request_changes`;
the `reassign` action means choose one of the exposed bound reassign tools.

`request_changes` appends feedback once and snapshots the next attempt's
effective contract. Retry retains the instructions and accumulated feedback;
reassignment retains them while changing the instance. The task remains the
same record. The default stored task budget permits three retries after
attempt zero. `after` becomes satisfied only when every prerequisite is
accepted; cancelled prerequisites cannot satisfy it.

### Results and delivery receipts

Talk uses Agent Builder when the owner asks to change the team. A work order
can also include an explicit staffing instruction, such as “If no teammate
can write code, add a coding specialist.” In that case `submit_job` checks the
active team before admitting the job. If the team needs that specialist, the
response contains `job_created: false`. Its `state` tells Talk what to do next.

| `state` | Next action |
|---|---|
| `awaiting_approval` | Show the pending access request. **Allow and add** saves the reviewed specialist. |
| `pending_apply` | Use Safe Apply to activate the saved team. Submit the original work once the specialist is active. |
| `team_change_blocked` | Report the returned `message`, such as a missing Builder connection or revoked access, so the owner can resolve it. |

With **Ask first** off, the authenticated owner's request can go straight to
`pending_apply`. Use `get_job_status` once a submission returns a job ID.
[Agent Builder](agentBuilder.md)

Version 1 results can include `agent`, `agents`, `runs`, `task`, `tasks`,
`attempts`, `delivery`, `status`, `trace` and `result_excerpt`. Public result
summaries are bounded while full results remain in storage. Provider session
state and configuration credentials are not part of the public instance view.
An assignment response confirms durable admission, not execution or success.

| Receipt state | Meaning |
|---|---|
| `queued` | Persisted, awaiting the recipient owner |
| `admitted` | Assigned to a particular run |
| `consumed` | Durable response/checkpoint and receipt advancement committed together |
| `held` | Recovery required, such as a chain limit or failure before checkpoint |
| `cancelled` | Superseded input or interrupted execution |
| `recipient_closed` | Pending input disposed by job retirement or Reset |

Requests carry a trusted identity and payload fingerprint. A duplicate returns
the original receipt; reuse with different content fails with
`idempotency_conflict`. `consumed`, an idle instance and a message acknowledgement
do not imply accepted work. Messages coordinate; they do not revise task
requirements or confer owner authorization.

Default peer limits are 8,192 UTF-8 bytes per message, 256 pending peer messages
and 1,048,576 pending peer bytes per recipient, configured by
`AGENT_PEER_MESSAGE_BYTES`, `AGENT_PENDING_MESSAGES` and `AGENT_PENDING_BYTES`.
Queued, admitted and held peer deliveries count toward the pending limits.
Oversize or full inbox admission returns `message_too_large` or `inbox_full`.
Mandatory task/review notices remain durable outside those peer quotas.
`AGENT_PEER_CHAIN_TURNS` defaults to eight automatically induced turns; further
input in that causal chain is held with a notice until explicit resume.

### Ownership and recovery

`AgentInstanceSessionWorkflow` serializes all input for an instance. Native/Pi
consume mailbox input at complete tool-round boundaries; CLI and RLM consume
it on the next turn. Native/Pi waits release root capacity and driver resources;
opaque runtimes that cannot yield their lease return `pending` immediately.
Instance history and output/session namespaces isolate repeated spawns of the
same template and separate jobs.

Submission and the owning lead's review notice commit together. A version 1
`taskTrigger` notification only wakes the exact persisted lead; it cannot
create a second review executor. Signals are hints, with fair database-backed
reconciliation recovering missed starts, wakeups, stops and permit cleanup.

After three classified invalid orchestration requests, the logical turn halts.
The counter survives continuation/retries and does not reset after unrelated
successes. Infrastructure errors and stale references do not count. Already
admitted operations may settle; no new provider request or effect is admitted.
Child work is preserved. A recoverable lead failure with existing tasks exposes
`review_blocked` and the owner-authenticated, idempotent
`retry_employee_review` action / **Retry review** button. Recovery reuses the
same lead, tasks and model without repeating accepted work.

Generation Stop preserves pending input and continuations. Resume continues
that generation. `stop_agent` instead interrupts an instance; failed work needs
an explicit retry. Reset fences old instances, cancels unfinished attempts and
disposes pending deliveries. It never redirects them to a replacement agent.

`finish_team` requires all tasks accepted or cancelled; managed employee teams
must also have assigned work. Final employee delivery is stricter: at least one
task, every task accepted, a nonempty final lead result and the original
delivery/approval gates. A finish receipt alone does not authorize delivery.

### Side effects

Validated lifecycle calls write instance/run/task/attempt, inbox, transcript
and idempotency records transactionally. They request deterministic Temporal
owner starts or signals after commit and broadcast UI state as a best-effort
notification. Executing an instance can invoke its configured model, tools or
CLI subprocess through the existing adapter. Final reviewed employee delivery
can append to Talk or invoke the captured app destination and approval gate.
Read/inspection tools return scoped state and may read Temporal history; they
do not select arbitrary teams or provider session IDs.

## Legacy contract (version 0)

The sections below retain the single `task_manager` operation schema and
detached task-runner semantics for old generations and compatibility callers.
They do not describe version 1 model arguments or instance ownership. Employee
job admission/delivery provenance and UI presentation are shared unless a
version difference is stated.

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
| `operation` | `submit_job` / `get_job_status` / `assign_task` / `list_tasks` / `get_task` / `modify_task` / `cancel_task` / `retry_task` / `reassign_task` / `accept_task` / `finish_team` / `mark_done` / `inspect_task_trace` | `list_tasks` | no | - | Operation to run. `mark_done` is a deprecated alias for `accept_task`. |
| `job_id` | string (max 255) | `None` | no | - | Job to inspect with `get_job_status`. Defaults to the most recently updated job for this employee. |
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
| `get_job_status` | trusted employee Talk context, optional `job_id` | read the job state and task counts for this employee |
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

Version 0 callers use the locked legacy validator; already prepared Temporal
runs keep their recorded definitions. The version 1 rollout adds storage and
selects its different tool surface at generation admission. It does not rewrite
old histories.

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

The submission receipt reports the saved job state. Talk can use `get_job_status`
to see whether any specialist tasks have been assigned. Specialists submit
work through Task Manager, and the lead reviews and accepts it. The runtime delivers the
reviewed result once to the original conversation or app, preserving approval
gates. Scheduled and app-triggered lead work uses the same server job boundary.
Existing employee recipe version 1 graphs keep their compatible execution
path; that recipe number is distinct from agent lifecycle version 1.

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
