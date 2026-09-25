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
- **Reset** revision-guards the old generation, closes controller and local
  admission, removes Temporal cron schedules, cancels local compatibility
  resources, and then performs a final strict execution sweep before archiving
  the old generation. It leaves the control state `ready`; the user must press
  **Start** to create the next generation.

Clients send an expected revision and idempotency key with every mutation.
New state transitions compare-and-swap the expected revision; Start persists
its request key, controller Updates receive unique request identities, and
stable/retry states are idempotent from their durable state.
Revisions increase across generation boundaries instead of restarting at zero,
so a delayed request from an archived generation cannot satisfy a later
generation's compare-and-swap check.
The toolbar and command palette derive available actions from the server's
`can_start`, `can_pause`, `can_resume`, and `can_reset` fields.

Start does not publish `running` merely because deployment setup was placed on
an asyncio task. The control handler waits for trigger/listener setup to finish;
setup failure moves the generation to `failed` and closes its controller.
Once the `running` compare-and-swap commits, a status-broadcast/projection
failure does not tear down that live generation; the failed request is recovered
by the client's authoritative status resync.

Pause and Resume use the controller's acknowledged `set_control_state` Temporal
Update. The database first publishes `pausing`/`resuming`, then publishes the
stable state only after the Update result confirms `paused`/`running`. If the
Update is known to have been rejected before admission, the database and local
admission gate return to the prior stable state. An unknown outcome, such as a
transport timeout after the server may have accepted the Update, remains
transitional. Status reads and explicit UI retries reconcile that state by
idempotently retrying the desired Update, reapplying schedule/local admission
gates, and completing the database CAS.
Legacy pause/resume Signals remain registered for history compatibility and
fan out to already-running descendant workflows. That fan-out and every cron
Schedule mutation are strict lifecycle barriers: a visibility or per-target
failure leaves the database in `pausing`/`resuming` for reconciliation instead
of falsely publishing a stable state.

Every accepted control transition emits `workflow_control_status`. Clients
merge reads, mutation responses, and broadcasts monotonically by generation
then database revision; an older response cannot move another tab backward.
Only one lifecycle mutation per workflow may be pending in a browser tab.
Lifecycle requests have a five-minute acknowledgement window, and a timed-out
request is treated as successful when the immediate authoritative resync
already reports its requested stable state. Transitional Pause, Resume, and
Reset states expose an explicit retry rather than trapping the toolbar in a
permanent spinner.

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

**Reset** reaches `ready` only after durable executions and schedules are gone,
the generation data scope is archived, and node/runtime state has been reset.
If any cleanup step fails, the control remains `resetting`; retrying Reset
resumes cleanup instead of falsely completing or skipping it. Reset does not
delete outputs, tasks, traces, or the graph snapshot. The toolbar returns to
**Start**. The next Start creates a new generation, execution ID, Temporal
controller, and empty runtime namespace from the then-current saved canvas.
Historical scopes therefore remain queryable without leaking state into the
new run.

Reset quiesces every producer before its final strict Visibility sweep: local
admission closes synchronously, the controller is told to close, cron Schedules
are deleted, and legacy local resources are cancelled. `EventWorkflowId` is
propagated to cron action executions and detached/abandoned graph children, so
the first Visibility pass identifies both the controller tree and standalone
execution roots. Because Temporal does not inherit custom Search Attributes
onto child workflows, a second batched `RootWorkflowId` pass expands every
tagged root to its active Agent and delegated descendants before signal or
termination fan-out.
Idempotent cron redeploys refresh the Schedule's frozen action metadata while
preserving its current paused state; an ownership check prevents a same-label
Schedule from being overwritten by a different application workflow.
Once `reset` is persisted, duplicate Reset requests return idempotently without
repeating generation-wide cleanup; this prevents an old retry from racing and
terminating a newly started generation.

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
prefer termination over durable resumption. The active-state guard reads the
shared `WORKFLOW_CONTROL_ACTIVE_STATES` set (which includes `resetting`), so a
boot mid-reset can never sweep the namespace.

## Months-long generations

A controlled generation is expected to run — or stay paused — for months, so
the control plane is hardened against Temporal's per-run event-history ceiling
(~51,200 events) and against backend restarts:

- **Controller continue-as-new.** `WorkflowControlWorkflow` rolls its run over
  under history pressure (`is_continue_as_new_suggested()` or a 10K-event soft
  cap), carrying trigger specs, per-trigger provider `seen_ids` (written back
  into the carried spec after every poll cycle), queued push events, the
  bounded dedup baseline, and the control state + revision. Rollover works
  mid-pause; the paused state carries. Because run ids change on rollover,
  every control surface addresses the controller **by workflow id only** —
  `controller_run_id` is stored for provenance but never pinned on a handle.
- **Signal narrowing.** The controller upserts the `ControlEventTypes`
  keyword-list Search Attribute as push triggers register; `dispatch.emit`
  skips controllers whose deployment has no matching trigger so other
  deployments' traffic cannot burn a controller's rollover budget.
- **Boot-time reconcile** (`reconcile_active_controls_on_boot`, invoked by the
  Temporal lifecycle task after workers start): runs the lazy reconcile over
  every active row, converges crash-stranded `starting` rows (controller alive
  with registered triggers, or a triggerless graph → `running`; alive-but-empty
  while the graph declares triggers → `failed` with the orphan controller
  closed), and re-arms the process-local half of running/paused generations
  from the persisted graph snapshot — DeploymentManager runtime state,
  in-process collectors for non-canary trigger types, and the paused posture
  (admission, trigger pause flags, cron schedule pause). Re-arm is idempotent:
  controller `register_trigger` is keyed by listener id, legacy listener
  starts use `USE_EXISTING`, and cron schedule creation preserves server-owned
  pause state.
- **No lifetime caps.** Spawned graph runs, agent children, and delegated-task
  runners no longer carry 1-2h execution/run timeouts (Temporal's timers keep
  ticking through a pause, so the caps silently terminated paused work).
  Replay-patched; see TEMPORAL_ARCHITECTURE.md for the patch inventory.

## Recovery policies

Three env-driven policies (canonical defaults + semantics in
`.env.template`; consumed by `services/deployment/handlers.py`) govern how a
generation behaves around kills, crashes, and failures. All three reuse the
cooperative control-plane pause — Temporal's native Pause/Unpause (server
1.28+) is deliberately not used: it is an operational control with no Python
SDK client methods, and it halts workflow-task dispatch entirely, so a
natively-paused controller could not process the `set_control_state` Update
that Resume relies on.

- **`WORKFLOW_CONTROL_CRASH_RECOVERY`** (`pause` | `resume`, default
  `pause`): after an UNCLEAN shutdown — kill or crash, detected via a
  dirty-bit cache marker the graceful lifespan teardown clears (registered
  through the generic shutdown-hook registry) — the boot reconcile pauses
  every generation still `running` so the user consciously resumes it. A
  clean `company stop` + start always restores deployments as they were.
- **`WORKFLOW_CONTROL_MISSING_CONTROLLER`** (`pause` | `fail`, default
  `pause`): a live generation whose controller execution vanished
  (terminated in the Temporal UI, killed, retention-deleted) converges to
  `paused` instead of `failed`. Resume then **rebuilds the controller**:
  same generation-scoped workflow id started with the documented
  `WorkflowIdConflictPolicy.USE_EXISTING` semantics (race-safe — adopts a
  live controller or starts a fresh run after termination), persists the
  new run id, tears down stale local trigger state, and re-arms from the
  persisted graph snapshot before re-applying the running Update.
  `starting` rows always fail instead (nothing durable runs yet; Reset +
  Start rebuilds cleanly). `fail` preserves the legacy Reset-only
  behaviour.
- **`WORKFLOW_CONTROL_PAUSE_ON_FAILURE`** (default `true`): circuit
  breaker — when trigger-spawned runs keep failing, MachinaWorkflow
  schedules `workflow_control.pause_on_failure` and the deployment pauses so the user
  fixes the cause and Resumes, instead of the trigger firing into the same
  error indefinitely. The breaker trips only after
  **`WORKFLOW_CONTROL_PAUSE_ON_FAILURE_THRESHOLD`** (default `3`) failed
  runs inside the rolling
  **`WORKFLOW_CONTROL_PAUSE_ON_FAILURE_WINDOW_SECONDS`** (default `600`)
  — one node hiccup on one firing (missing config, transient API error)
  never pauses a deployment; `1` restores pause-on-the-first-failure. The
  streak lives in the durable cache table keyed by the generation (Reset /
  Start begins fresh), Resume resets it, and tripping clears it. Only
  deployment-spawned runs qualify (they carry a `_pre_executed` firing
  trigger; a failed manual canvas run never pauses a live deployment), and
  every knob is evaluated on the activity side so flipping config never
  touches recorded workflow commands.

**Why an automatic pause happened.** Each of the three records it on the
control row: `pause_reason` is `failures` (the circuit breaker),
`recovery` (crash recovery) or `controller_missing`, with a readable
`pause_detail`. `serialize_control` emits both; a transition back to
`running` (Resume, reconcile, restore) clears them. Only server-side calls
can set a reason; a `pause_workflow` from a socket cannot. Normal mode shows
such an employee as "Needs attention" ([normal_mode.md](./normal_mode.md)).

## Canvas editability (`can_edit` capability)

Whether a workflow's canvas may be edited is a **server-owned capability**,
emitted by `serialize_control` alongside `can_start` / `can_pause` / … —
the frontend renders it and never re-derives the rule from state strings.
Editable while nothing is armed (`ready` / `failed` / `never_started`) and
while **paused** (controlled generations execute their immutable admitted
snapshot, so edits cannot corrupt in-flight runs — paused is exactly the
"fix it, then resume" posture the recovery policies produce); locked while
`starting` / `running` / transitional. On the client,
[`lib/canvasLock.ts`](../client/src/lib/canvasLock.ts) maps the capability
to a boolean + reason. Precedence is strict: once a generation governs the
workflow (any state other than `never_started`), `can_edit` is rendered
verbatim and the legacy broadcaster lock is **not** consulted — control-plane
deployments hold that lock for their whole armed lifetime (paused included),
so letting it override would deny the server's paused-is-editable grant. The
legacy lock decides only for ungoverned workflows (deployments driven outside
the control plane, which never create a control row); `Dashboard` feeds it to the React
Flow interaction props AND a shared `guardCanvasEdit` toast-guard covering
the paths those props cannot reach (palette drop, paste, context-menu
delete/rename, node disable, parameter saves); `TopToolbar` shows a
`Locked` badge so a blocked drag never reads as dead UI.

The pause/resume/cancel/re-arm paths also emit the UI-facing
`workflow_status(executing=…)` + `deployment_status` broadcasts (status
`paused` keeps `isRunning=true` — armed but not executing), and the
connect-time deployment snapshot carries `paused_workflow_ids` so an
armed-but-paused generation doesn't animate as running after a reconnect.

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
