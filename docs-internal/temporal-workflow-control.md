# Temporal Workflow Control and Team Traces

Workflow deployments use a persisted control generation and a long-lived
`WorkflowControlWorkflow`. The application database is authoritative for the
current generation, revision, graph snapshot, and UI authorization; Temporal
is authoritative for execution history.

## Control lifecycle

- **Start** creates the first generation and deploys its snapshotted graph.
- **Pause** cooperatively gates new trigger admissions, workflow nodes, agent
  turns, tool calls, polling iterations, and delegated work. In-flight work may
  finish and remains durable. Push events stay queued in the controller,
  provider polling cannot launch graph runs, Temporal cron schedules are
  paused, and armed trigger nodes switch to an explicit paused visual state.
- **Resume** signals the same running Temporal executions and drains buffered
  trigger events in FIFO order, unpauses cron schedules, and rearms trigger
  nodes.
- **Reset** revision-guards the old generation, terminates visible executions
  carrying the workflow search attribute, cancels local deployment resources,
  and archives the old generation. It leaves the control state `ready`; the
  user must press **Start** to create the next generation.

All mutations require an expected revision and support an idempotency key.
The toolbar and command palette derive available actions from the server's
`can_start`, `can_pause`, `can_resume`, and `can_reset` fields.

## Generation-scoped workflow data

The editable `workflows` row is the stable canvas definition; it is not runtime
state. Every successful **Start** atomically creates a
`workflow_run_data_scopes` row alongside the control generation. The scope:

- has the same durable `execution_id` used by the controller and team records;
- snapshots each node's type and complete `data` payload at admission time;
- records the controller's actual Temporal Workflow ID and Run ID;
- becomes the session namespace for node outputs, conversations, and other
  session-keyed runtime records;
- remains immutable as an execution snapshot while runtime records accumulate
  under its scope ID.

**Reset** terminates the generation and marks its data scope `archived`; it does
not delete outputs, tasks, traces, or the graph snapshot. The toolbar returns to
**Start**. The next Start creates a new generation, execution ID, Temporal
controller, and empty runtime namespace from the then-current saved canvas.
Historical scopes therefore remain queryable without leaking state into the
new run.

The editor's live projection follows the same boundary. Reset broadcasts
`workflow_runtime_reset`, clears node statuses, variables, and console/chat
projections, and remounts the parameter panel so local output
reducers cannot retain results. Persisted console and chat rows carry the root
execution ID; current-run reads filter by that ID while archived rows remain in
the database. Node parameters are canvas configuration and intentionally remain
unchanged across Reset.

`simpleMemory` configuration survives, but conversation state does not leak
across generations. Reset snapshots current memory parameters into the archived
scope, then clears the live transcript, continuation metadata, connected
sessions, vector/direct-memory caches, conversation rows, and token/compaction
state. The explicit **Clear Memory** action performs the same clear without
resetting the workflow.

This is a framework contract, not a Reset special case. The runtime coordinator
archives every node's canvas data and parameters under the current execution,
then invokes the registered node class's `reset_execution_state` hook. Stateless
nodes inherit the no-op base implementation; stateful plugins own cleanup of
their external stores. Deployment control never switches on node type.

On server restart, active controlled deployments are excluded from the legacy
startup termination sweep. `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP` defaults to
`false`; enabling it is intended only for legacy installations that explicitly
prefer termination over durable resumption.

## Delegated-task traces

Each task attempt stores the actual parent, detached runner, and child Temporal
workflow/run identities registered from `workflow.info()` at child startup.
Retries and reassignments create immutable attempts instead of overwriting old
links or results.

`get_team_task_trace` and Task Manager's `inspect_task_trace` first authorize a
task through its saved workflow, lead, execution, and team membership. They
then fetch only the persisted Temporal execution link. Results are sanitized,
cursor-paginated (50 by default, 100 maximum), and limited to normalized
workflow, child, activity, retry, timer, signal, cancellation, and failure
metadata. Raw prompts, arguments, secrets, and results are never exposed.

Large histories support bounded grep-style inspection with literal or term
matching, category filters, contextual events, and opaque continuation cursors.
The lead searches sanitized projections in chunks instead of loading the full
Temporal history into its conversation.

The Task Manager Trace tab loads history only when opened. Team Monitor remains
a lightweight read-only view of generation and child execution state.

Controlled generations do not create trigger-listener workflow executions.
The `WorkflowControlWorkflow` stores trigger registrations and inbound push
events in its own history and performs polling cycles as activities. Only a
real triggered graph execution becomes a child workflow. Legacy deployments
created before workflow control retain the standalone listener compatibility
path until they are reset and explicitly started again.
