# Temporal Workflow Control and Team Traces

Workflow deployments use a persisted control generation and a long-lived
`WorkflowControlWorkflow`. The application database is authoritative for the
current generation, revision, graph snapshot, and UI authorization; Temporal
is authoritative for execution history.

This is the current control contract. Use it with
[Temporal architecture](TEMPORAL_ARCHITECTURE.md),
[node authoring](node_creation.md), [event routing](event_framework.md), and
[chat protocol](chat_protocol.md). Archived RFCs describe earlier designs.

## Control lifecycle

- **Start** creates the first generation and deploys its snapshotted graph.
- **Stop** (the `pause_workflow` API) cooperatively gates new trigger admissions, workflow nodes, agent
  turns, tool calls, polling iterations, and delegated work. In-flight work may
  finish and remains durable. Push events stay queued in the controller,
  provider polling cannot launch graph runs, Temporal cron schedules are
  paused, and armed trigger nodes switch to an explicit paused visual state.
- **Resume** releases the same running Temporal executions and drains buffered
  trigger events in FIFO order, unpauses cron schedules, and rearms trigger
  nodes.
- **Reset** revision-guards the old generation, closes controller and local
  admission, removes Temporal cron schedules, cancels local compatibility
  resources, and then performs a final strict execution sweep and resets the
  workflow's [Workspace tasks](#workspace-tasks) before archiving the old
  generation. It leaves the control state `ready`; the user must press
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

Legacy Pause and Resume use the controller's acknowledged `set_control_state` Temporal
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

## Acknowledged Stop for new generations

New generations atomically store `resource_manifest.execution_control_version=1`.
Existing generations keep their recorded command paths and legacy Signals;
unordered Signals do not change version 1 control intent. Version 1 uses
revision-ordered `set_control_state` and `wait_for_checkpoint` Updates, with
`execution_control_status` Queries on controlled workflows.

Stop during tool A means A finishes under its existing retry policy, its result
and normal bookkeeping are retained, and the next pending action waits for
Resume. Completed LLM responses retain their pending tool calls. Parallel work
already admitted drains concurrently. Stop does not cancel, terminate, or
replace the execution. `pausing` means admission is closing or admitted work is
draining; `paused` means the checkpoint has been acknowledged throughout the
execution tree. A request timeout leaves `pausing` for reconciliation and does
not cancel the tool. Unlimited LLM retries can keep it there during an outage.

The existing controller stores only independently living roots: graph runs,
cron firings, employee jobs, and detached delegation runners. Each enrolls
through an acknowledged controller Update before business work. A small
maintenance Activity bridges the Workflow to the client Update API. Registry
entries contain Workflow ID and first execution Run ID, and are carried with
the membership epoch through controller continue-as-new. Terminal roots remove
themselves; control operations remove stale registrations after authoritative
Describe calls. There is no SQL participant registry or periodic lease loop.

Each admission setter closes local scheduling and waits for admitted child
starts to acknowledge. Native `DescribeWorkflowExecution.pending_children`
then discovers attached children. Stop closes the complete discovered tree
before awaiting any checkpoint. Checkpoints count admitted business Activities
through result bookkeeping; maintenance Activities, permit waits, sleeps and
child-result waits are excluded. Root membership is reconciled until stable.
Visibility scans remain part of destructive Reset, not Stop acknowledgement.

### Stop and Resume acknowledgement sequence

The orchestration lives in
[`transition_generation`](../server/services/deployment/execution_control.py);
the participant gate and counters live in
[`ExecutionControl`](../server/services/temporal/execution_control.py).

1. Stop persists the `pausing` revision and closes process-local producers.
   Cron schedules and trigger projections are paused before tree traversal.
2. A completed controller admission Update closes controller producers. Each
   independent root and attached descendant receives the same revision. Its
   setter waits only for child-start acknowledgements, allowing the subsequent
   native Describe to enumerate children already being started.
3. After admission is closed throughout the discovered tree, checkpoint
   Updates run concurrently. Each waits for local admitted work and result
   bookkeeping, without waiting for child completion. This lets a paused child
   remain alive without deadlocking its parent's acknowledgement.
4. A changed membership epoch restarts discovery. Stale registrations are
   removed only after native Describe confirms a terminal, missing, or
   different execution chain. The database publishes `paused` after the
   membership and checkpoint barriers settle.
5. Resume persists `resuming` and applies a new running revision with
   `producers_held=true`. Descendants are released before roots. Controller
   release checks the membership epoch atomically; a late registration causes
   another reconciliation pass. Local producers and cron schedules reopen
   after that release, and the database publishes `running`.

The admission acknowledgement is distinct from complete drain. Status counts
continue to report admitted business work during `pausing`; a zero UI count
does not establish the checkpoint. Checkpoint sleeps, permit waits, enrollment
maintenance and child-result waits are not business work. Completing a child
result still performs its normal bookkeeping while admission is closed.

Resume sets the next revision while holding producer admission, resumes
descendants before their roots, and atomically checks controller membership
before reopening producers. A late root inherits the controller's held state
and is included in reconciliation. Handles address current Workflow IDs;
native chain identity and Update payload checks prevent Workflow ID reuse
from controlling a different execution. Application revisions survive rollover
because Temporal Update-ID deduplication is scoped to one execution.

Agents roll over only at clean boundaries with no live delegation handles,
while running and after pending control handlers finish. Continuation carries
prepared configuration and tool policies, transcript, thinking, iteration,
usage and control state. The complete encoded continuation input is measured.
Existing compaction is used; an oversized version 1 continuation reports
`AgentContinuationTooLarge` rather than restarting at the opening prompt.
Ordinary tool dispatch respects frozen plugin retry, queue, heartbeat and
timeout declarations. Heartbeats retain their liveness/failure-recovery role;
they are not checkpoints for opaque provider or tool sessions.

Controlled chat Stop resolves the run's exact owning generation, preserves its
run/reply identity, partial output, subscription and occupied lane, and derives
Stopping/Stopped/Resume from workflow control. Suspension retains live output;
a server process restart cannot reconstruct unsaved text deltas from the hub.
See the [chat persistence limits](chat_protocol.md#controlled-stop-acknowledgement).
The watchdog excludes stopped
generations from age expiry; successful Resume persists `last_resumed_at` in
existing control metadata to give a fresh timeout window. Direct Workspace
tasks and separately approved delivery continuations remain independent.
Claude Code, RLM and Vertex managed agents drain at their whole Activity boundary.

Real SDK integration tests use stub Activities and replay captured new and
legacy histories. They exercise parallel drain, attached children, late roots,
worker restart, pending model tool calls and two actual agent rollovers.
Admission acknowledgement and complete drain latency are logged separately.

The guarantee applies to recorded completion. Resume does not schedule a
successfully recorded tool again. If an external effect happened before
Temporal recorded the Activity result, the Activity's existing retry and
idempotency contract still determines whether that effect can repeat.

The protocol follows Temporal's [entity lifecycle](https://docs.temporal.io/design-patterns/entity-lifecycle-patterns),
[event accumulator](https://docs.temporal.io/design-patterns/event-accumulator),
[long-running Activity](https://docs.temporal.io/design-patterns/long-running-activity),
and [continue-as-new](https://docs.temporal.io/develop/python/workflows/continue-as-new)
guidance. The [resumable Activity pattern](https://docs.temporal.io/design-patterns/resumable-activity)
reruns a failed Activity after corrective input; it does not implement Resume
after a successfully completed tool. Provider and tool operations remain
regular Activities rather than [Local Activities](https://docs.temporal.io/design-patterns/local-activities).

## Workflow messaging review

The [messaging patterns](https://docs.temporal.io/design-patterns/workflow-messaging-patterns)
and [sending reference](https://docs.temporal.io/sending-messages) support this
division of responsibilities:

| Operation | Message | Acknowledgement |
|---|---|---|
| Incoming trigger event | Signal with stable event ID | Temporal accepted the Signal; handler processing is asynchronous |
| Stop/Resume admission | Completed `set_control_state` Update | Requested revision applied and admitted child starts acknowledged |
| Stop drain | Completed `wait_for_checkpoint` Update | Admitted work and result bookkeeping settled |
| Independent root registration/removal | Update through a maintenance Activity | Controller membership mutation completed |
| UI status | Synchronous Query | Read-only snapshot; not a transition acknowledgement |

The Activity bridge is required because a Workflow cannot directly issue an
Update to another Workflow. `execute_update` waits for completion; merely
reaching the Accepted stage does not establish that work drained.

Pure control validators check message shape, state, revision, generation,
execution chain and admission-hold type before acceptance. Handler-side typed
`ApplicationError` checks remain for direct calls and replay. An ordinary
`ValueError` or `TypeError` in an accepted Python Update handler retries the
Workflow Task rather than rejecting only the Update. These distinctions follow
the [handler exception rules](https://docs.temporal.io/handling-messages#exceptions-in-message-handlers).

State/revision changes happen before the first await. Checkpoint waits use
`workflow.wait_condition` and allow newer control handlers to run; a lock held
through draining would prevent Resume from superseding the wait. Input state
is initialized with `@workflow.init`; pending handlers finish before return or
rollover. See the [Python async-handler guidance](https://docs.temporal.io/develop/python/workflows/message-passing#use-async-handlers)
and [official safe-handler sample](https://github.com/temporalio/samples-python/blob/main/message_passing/safe_message_handlers/workflow.py).

New version 1 histories record `controller-messaging-v1`. Successful controller
Updates check the existing history-pressure signal and wake the main loop to
continue-as-new, even when no events or polls occur. The Update handler never
performs the rollover itself. This accounts for [per-execution Update limits](https://docs.temporal.io/evaluate/cloud/limits#per-workflow-execution-update-limits)
in addition to event-history growth. Existing revisions, membership and events
carry into the next run; replay tests cover pre-marker histories.

Employee Safe Apply keeps its producer-only admission pause. Its compatibility
string Resume can reopen producers only while the versioned participant is
running and unheld; it cannot override Stop or a held Resume. Task-review
draining is likewise disabled while generation control closes admission.

A client deadline or cancellation does not cancel an accepted Update. Retain
the desired revision and transitional state and reconcile the outcome, as
specified by the [Update client contract](https://python.temporal.io/temporalio.client.WorkflowHandle.html#execute_update).
Update-ID deduplication covers a single run; application revision and membership
identity preserve idempotency across rollover and maintenance Activity retries.

### Control interfaces

The WebSocket APIs retain their existing names. Example Stop request data:

```json
{
  "workflow_id": "saved-workflow-id",
  "expected_revision": 12,
  "idempotency_key": "stop-request-unique-id"
}
```

Send this data with `type: "pause_workflow"`; Resume uses
`type: "resume_workflow"` and the latest returned revision. Status uses
`get_workflow_control_status`. Read the authoritative response and its
capabilities before the next mutation: a stable-state CAS also advances the
database revision. Do not infer the next revision from a local counter.

Controlled `stop_chat_run` takes `run_id`, `expected_revision` and
`idempotency_key`. It authorizes the session, resolves the run's workflow and
root execution identity, and delegates to Stop with an owning-generation
guard. It returns the generation control payload plus `run_id` and
`resumable: true`. Resume uses the generation's `resume_workflow` API. See
[chat protocol](chat_protocol.md) for legacy terminal Stop and wire details.

Internal Update payloads are not the browser API:

| Surface | Fields and result |
|---|---|
| `set_control_state` | `state` (`paused` or `running`), transition `revision`, `generation`, `first_execution_run_id`, and optional boolean `producers_held`; returns applied intent and admission acknowledgement |
| `wait_for_checkpoint` | Same intent fields, or omitted payload to wait for existing intent; returns counters and `checkpoint` |
| `register_execution` | `workflow_id`, `first_execution_run_id`, `generation`; returns controller participant state/revision and hold for the enrolling root |
| `unregister_execution` | Same membership identity; removes only a matching execution chain |
| `execution_control_status` | Read-only participant state, revision, hold, active actions, pending child starts and checkpoint |
| Controller `status` | Also includes `live_roots`, `membership_epoch`, producer state and event queue status |

Final controller release additionally includes `expected_membership_epoch`.
Rejected shape, state, revision, generation or execution identity produces a
typed Update error; no accepted mutation should be followed by a raw input
conversion failure. A stale revision cannot overwrite newer intent. Same-revision
registration results cannot undo an explicit release. Child inheritance omits
the parent's root-chain and release markers; same-Workflow continuation carries
them, so a new child's enrollment still learns the controller's current hold.

Separate identity finding: existing listener IDs and child display prefixes use
trigger labels. Two same-label trigger nodes can collide during registration.
That pre-existing naming contract needs an immutable trigger-node identity for
multi-trigger deployments; the messaging hardening above does not change names
or recorded child IDs.

## Event accumulation during Stop and rollover

The controller already serves as the generation's event accumulator: its
stable Workflow ID groups Signals, and its queue retains each accepted event
until trigger processing can resume. The [Event Accumulator pattern](https://docs.temporal.io/design-patterns/event-accumulator)
provides useful buffering and deduplication guidance. Inactivity timers and
batch processing would change the existing immediate, per-event trigger
behavior, so neither is introduced. Signal-With-Start is not used to replace
a lost version 1 controller with an empty execution-root registry.

New version 1 histories record `controller-event-accumulator-v1`. This marker
preserves recorded command decisions for legacy and earlier version 1 histories.
Pending event keys are protected independently of the bounded recent-ID cache,
including the event currently being dispatched. Restored overflow pages keep
their original FIFO position when the same pending event was redelivered.
Existing durable overflow storage and read receipts are reused.

Continue-as-new drains the appended queue tail after each spill and checks again
after control handlers finish, so Signals accepted during a spill are included
in the continuation or overflow store. Reset closes the controller and prevents
rollover from resurrecting it. An already-started deterministic child is treated
as a duplicate admission; transient start failures retain the event for retry
instead of blocking all later events permanently on a successful duplicate.

Message producers use their existing stable provider identities: Telegram's
chat/message pair, Discord message or interaction ID, WhatsApp chat/message
and direction, and chat run ID or saved message ID. Payloads without a complete
provider identity retain a fresh ID rather than merging unrelated messages.
Recent completed-ID memory remains bounded; deterministic child IDs and the
existing overflow keys also protect redelivery. This is not an unlimited
generation-wide deduplication ledger.

These queue guarantees begin when Temporal accepts an event Signal. The existing
Visibility-based, best-effort producer dispatch remains unchanged; durable
end-to-end producer delivery would require a separate admission/outbox design.

The published accumulator Python example currently assigns a boolean from
`workflow.wait_condition`. Follow the installed
[Python SDK contract](https://python.temporal.io/temporalio.workflow.html#wait_condition):
it returns normally without a value and raises `asyncio.TimeoutError` on timeout.
Use `try`/`except` if a timeout outcome is needed. This controller uses admission
and rollover conditions rather than the example's inactivity timer.

## Compatibility and rollout

Start writes `execution_control_version=1` atomically with a new generation.
Deploying this code does not upgrade an already-admitted generation or rewrite
its history. Reset followed by Start admits the stronger protocol for that
workflow. There is no migration of an old run's continuation into a new root.

| Gate | Purpose |
|---|---|
| Persisted generation `execution_control_version=1` | Root enrollment, cooperative gates, acknowledged execution-tree drain, held Resume, frozen agent continuation and controlled chat Stop |
| `controller-event-accumulator-v1` | Pending-key protection, duplicate-start handling and final Signal fences during controller rollover |
| `controller-messaging-v1` | Successful Updates request main-loop rollover and Safe Apply producer controls cannot override generation suspension |
| Existing controller durable-queue markers | Preserve the recorded overflow/spill command paths |

New markers are recorded at the relevant runtime command paths, not in
constructors, Queries or validators. Pre-marker and legacy histories retain
their previous paths; native replay tests cover these cases. Update handlers
request rollover, while the main Workflow waits for handlers and performs it.
Controller rollover may occur while paused; Agent rollover parks until running
at a clean boundary without live child handles.

No participant SQL registry, recurring lease heartbeat, separate checkpoint
store or generic tool progress cursor is added. The existing controller history,
native execution descriptions, queue overflow storage and application revisions
provide the necessary control state.

## Verification and operations

The regression suite separates participant behavior, whole-generation control,
event durability, messaging and chat/UI contracts:

| Tests | Guarantees exercised |
|---|---|
| [Participant unit tests](../server/tests/temporal/test_execution_control.py) | A finishes once, B waits; parallel drain; bookkeeping; child-start acknowledgements; delayed registration/revision guards; held release and carry |
| [Agent SDK integration](../server/tests/temporal/test_agent_execution_control_integration.py) | Recorded model calls and pending tools survive Stop, worker restart and two real agent rollovers; replay and complete-input capacity error |
| [Control replay integration](../server/tests/temporal/test_execution_control_replay.py) | Suspended worker restart, actual continuation runs, new and legacy history replay |
| [Generation SDK integration](../server/tests/temporal/test_generation_execution_control_integration.py) | Attached graph/agent children, cron roots and jobs, late enrollment, detached lifetime and controller rollover |
| [Accumulator unit tests](../server/tests/temporal/test_controller_accumulator.py) and [SDK integration](../server/tests/temporal/test_controller_accumulator_integration.py) | Signals during spill and handler fences, pending-key eviction, restored FIFO, duplicate child start, transient retry, Reset, restart and pre-marker replay |
| [Messaging unit tests](../server/tests/temporal/test_controller_messaging.py) and [SDK integration](../server/tests/temporal/test_controller_messaging_integration.py) | Malformed Updates rejected before acceptance without Workflow Task failure, valid subsequent messages, Update-only rollover, Safe Apply guards and replay |
| [Versioned handler tests](../server/tests/services/test_versioned_workflow_control.py) | Timeout keeps intent closed, Resume ordering/deadline metadata, missing controller fail-closed and owning-generation guard |
| [Chat Stop](../server/tests/services/chat/test_stop.py) and [watchdog](../server/tests/services/chat/test_watchdog.py) | Controlled run identity/lane retention, legacy terminal Stop, long-pause protection and fresh resumed age window |

Run from `server/` after the normal dependency setup:

```bash
uv run pytest -q -p no:cacheprovider tests/temporal/test_execution_control.py tests/temporal/test_controller_accumulator.py tests/temporal/test_controller_messaging.py tests/services/test_versioned_workflow_control.py
uv run pytest -q -p no:cacheprovider tests/temporal/test_agent_execution_control_integration.py tests/temporal/test_execution_control_replay.py tests/temporal/test_generation_execution_control_integration.py tests/temporal/test_controller_accumulator_integration.py tests/temporal/test_controller_messaging_integration.py
uv run pytest -q tests/temporal/test_agent_workflow.py tests/temporal/test_controller_queue.py tests/services/chat
```

Native tests launch Temporal's time-skipping test server in isolated processes
and use stub provider/tool Activities. They exercise actual Updates,
Continue-As-New and `Replayer`, without calling live provider APIs. The local
test server does not expose production dynamic Update-limit settings: rollover
tests use deterministic history-pressure hooks held unchanged during replay.
They validate the rollover path, not production load capacity.

Client control/chat regressions and type checking run from the repository root:

```bash
bun run --filter react-flow-client test src/components/ui/__tests__/CommandPaletteHost.workflowControl.test.ts src/contexts/__tests__/webSocketLifecycle.test.tsx src/features/chat/__tests__/chatPane.test.tsx src/features/home/__tests__/presentation.test.ts src/features/home/__tests__/employeeView.test.tsx src/features/home/__tests__/workspace.test.tsx
bun run --filter react-flow-client typecheck
```

When Stop remains `pausing`, inspect the controller participant revision/hold,
live-root membership and each participant's `active_actions` and
`pending_child_starts`. The `Generation admission acknowledged` log measures
time until admission closes; `Generation checkpoint acknowledged` measures
the complete transition. A retrying LLM or whole managed-agent Activity can
legitimately keep Stop in progress. Read status and retry reconciliation with
the returned revision; do not terminate a tool to make the UI report Stopped.

If a transition RPC times out, the durable Update may still complete. Keep the
transitional state and reconcile. If a version 1 controller is lost, its root
registry cannot be reconstructed safely by starting an empty controller; Reset
and Start are required. Explicit continuation capacity errors likewise surface
to the operator rather than silently rebuilding an agent conversation.

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

Deleting a workflow uses Reset as its shutdown barrier
([deletion.py](../server/services/workflow_storage/deletion.py)): a generation
that is not `reset`, or a Workspace controller that exists, is Reset with the
latest control revision before the graph is removed, and a legacy local
deployment is cancelled. If shutdown fails, or a Start was admitted meanwhile,
the delete is refused (`workflow_shutdown_failed`) and the graph stays. A
workflow that never ran is deleted without connecting to Temporal.

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

## Workspace tasks

Phone tasks submitted directly from the Workspace run outside the control
generation, under a per-workflow `WorkspaceTaskControllerWorkflow` (see
[TEMPORAL_ARCHITECTURE.md → Direct Workspace tasks](./TEMPORAL_ARCHITECTURE.md#direct-workspace-tasks)).
They can exist before the first Start and after a Reset. Pause and Resume do
not gate them; of the control states, only a Reset in progress blocks new
submissions. The control plane covers them in two places.

- **Reset.** With an active generation, Reset resets the Workspace tasks after
  its final execution sweep and before archiving
  (`services/deployment/handlers.py::_reset_workspace_tasks`), and
  `workflow_runtime_reset` carries `cancelled_workspace_tasks`. With no
  generation, or after the latest one was reset, Reset still checks the
  expected revision and then resets only the Workspace tasks. It returns
  idempotently when the graph has no Workspace-task node and no controller
  exists; otherwise it broadcasts `workflow_runtime_reset` and
  `workflow_control_status`.
- **Status.** Every control status read, mutation response and
  `workflow_control_status` broadcast passes through `_with_runtime_counts`,
  which merges `_workspace_runtime_status`. When the saved graph contains a
  Workspace-task node, `can_reset` is true in every state and the controller's
  active tasks are added to `active_count` and `in_flight_count`. Once the
  controller exists, the payload also carries `workspace_epoch`,
  `workspace_resetting` and `workspace_reset_request_id`; `workspace_available`
  is false when Temporal or the controller query is unavailable. While the
  controller is resetting, the payload reports `state: "resetting"` and turns
  off `can_start`, `can_pause`, `can_resume` and `can_edit`.

## Months-long generations

A controlled generation is expected to run — or stay paused — for months, so
the control plane is hardened against Temporal's per-run event-history ceiling
(~51,200 events) and against backend restarts:

- **Controller continue-as-new.** `WorkflowControlWorkflow` rolls its run over
  under history pressure (`is_continue_as_new_suggested()` or a 10K-event soft
  cap), carrying trigger specs, per-trigger provider `seen_ids` (written back
  into the carried spec after every poll cycle), queued push events, the
  bounded recent dedup baseline, pending keys, durable overflow position,
  control intent/revision/hold, live roots and membership epoch. Rollover works
  mid-pause; the paused state carries. Because Run IDs change on rollover,
  control handles address the current controller **by Workflow ID**, with the
  first execution Run ID checked as the chain identity. The recorded
  `controller_run_id` is not pinned as the current Run ID on a handle.
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
  pause state. Listener and Schedule ids use the slug the generation's Start
  recorded in its snapshot (`workflow_slug`), not the saved one, so a rename
  while it runs cannot give the re-arm a second set of ids. That slug stays
  taken while the generation lives (`next_available_slug` skips it), and a
  Schedule whose workflow was deleted is replaced by the next workflow given
  its id instead of failing that workflow's Start.
- **No lifetime caps.** Spawned graph runs, agent children, and delegated-task
  runners no longer carry 1-2h execution/run timeouts (Temporal's timers keep
  ticking through a pause, so the caps silently terminated paused work).
  The caps were removed unconditionally, with no `workflow.patched` marker;
  TEMPORAL_ARCHITECTURE.md lists the markers that do exist.

## Recovery policies

Three env-driven policies (canonical defaults + semantics in
`.env.template`; consumed by `services/deployment/handlers.py`) govern how a
generation behaves around kills, crashes, and failures. All three reuse the
cooperative control-plane pause. The application requires Workflow Tasks to
keep processing events, completed results and control messages while stopped;
it does not use a server-level pause of Workflow Task dispatch for this
handshake. The registered control Updates remain available for reconciliation
and Resume.

- **`WORKFLOW_CONTROL_CRASH_RECOVERY`** (`pause` | `resume`, default
  `pause`): after an UNCLEAN shutdown — kill or crash, detected via a
  dirty-bit cache marker the graceful lifespan teardown clears (registered
  through the generic shutdown-hook registry) — the boot reconcile pauses
  every generation still `running` so the user consciously resumes it. A
  clean `company stop` + start always restores deployments as they were.
- **`WORKFLOW_CONTROL_MISSING_CONTROLLER`** (`pause` | `fail`, default
  `pause`): a legacy live generation whose controller execution vanished
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
  Version 1 generations always fail closed when their controller is lost.
  Resume cannot rebuild an empty registry and claim verified resumability;
  Reset followed by Start is required.
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
