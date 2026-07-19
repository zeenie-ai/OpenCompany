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
