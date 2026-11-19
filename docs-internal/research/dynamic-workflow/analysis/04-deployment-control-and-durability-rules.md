# Deployment control and durability rules: code map

Scope: how a saved workflow is started, stopped, resumed and reset on Temporal (one "generation" per Start), how trigger events become runs, how the control plane recovers, and the rules a change to workflow code must follow. Written on 2026-11-12, as of commit 908a8ce6. Read-only research: no code or test was run, and nothing here is a design.

While this note was written, HEAD moved twice: to 90fc876c (`chore(release): v0.3.2`: version files, client canvas files and one document), then to 48c34266, which adds that release's recorded histories under `server/tests/temporal/fixtures/0.3.2/` and edits three documents and one test's docstring. `git diff --stat 908a8ce6 48c34266` lists no file under `server/services` or `server/nodes` and no test module under `server/tests`, so the code citations hold for all three commits. The test harness in section 5.11 is described as of 48c34266.

Citation labels are shortened (`deployment/handlers.py:2104` is under `server/services/`); every label links to the real file.

## 1. Summary

- A Start admits one **generation**: a `workflow_control_executions` row with an immutable `graph_snapshot`, the slug that names its Temporal ids, and version fields in `resource_manifest`. One long-lived `WorkflowControlWorkflow` per generation (id `workflow-control-<workflow id>-g<generation>`) holds the trigger specs, the event queue and the registry of independently living runs.
- A run takes its behaviour from its own workflow input, never from live settings. The controller copies each trigger's frozen `listener_args` into the `MachinaWorkflow` child it starts; flags travel in `_temporal_routing_v1` and in `_INHERITED_SCOPE_KEYS`.
- Stop (the `pause_workflow` command) cancels nothing. In a version 1 generation every workflow in the tree closes admission through a `set_control_state` Update and acknowledges a checkpoint through `wait_for_checkpoint` once its admitted activities and child starts have settled. A workflow type takes part by exposing those two Updates and the `execution_control_status` Query and by wrapping activities in `ExecutionControl.action()` and child starts in `child_start()`; a type that does not must be in `NON_PARTICIPANTS`, or Stop cannot complete (INFERRED, section 7).
- Reset is the only control that kills. It **terminates** (not cancels) every running execution Visibility finds for the generation, so no workflow cleanup code runs and the handler closes the database records itself.
- A change to which commands a shared workflow type emits needs a `workflow.patched` marker (new ids ship dormant for one release through `DORMANT_PATCHES`) or a generation flag in `GENERATION_FLAGS`. The gates are the N-1 replay corpus and the golden identity file. A new workflow type needs neither.
- Children start with no execution or run timeout; liveness is activity heartbeats; long-lived loops continue-as-new at a 10,000-event soft cap and refuse a carried input above 1,900,000 bytes.
- All 15 workflow types run with `sandboxed=False` and no determinism lint exists, so determinism rests on convention and on what the recorded histories exercise.
- One precedent exists for a tool call that starts its own durable workflow: `start_inline_pi_delegation` admits the call in the database and starts `PiInlineDelegationWorkflow` through the client under a deterministic id. That workflow registers as a root, so Stop reaches it; it carries no `EventWorkflowId`, so Reset is INFERRED not to find it (section 7; section 5 rule 22).
- Context for the lead: [temporal_production_practices.md](../../../temporal_production_practices.md) records an owner decision of 2026-11-11 that OpenCompany "takes no pydantic-ai dependency and builds no Code Mode feature".

## 2. Files and responsibilities

| File | Lines | Owns |
|---|---|---|
| [server/services/temporal/workflow_control_workflow.py](../../../../server/services/temporal/workflow_control_workflow.py) | 1,021 | `WorkflowControlWorkflow`: control state, trigger specs, push-event queue, poll loops, root registry, spawn of per-event runs, continue-as-new, spill and dead letters |
| [server/services/temporal/execution_control.py](../../../../server/services/temporal/execution_control.py) | 229 | `ExecutionControl`: per-workflow admission gate, action and child-start counters, root enrollment, carried control state |
| [server/services/deployment/execution_control.py](../../../../server/services/deployment/execution_control.py) | 212 | `transition_generation`: the client-side tree walk that sends admission, checkpoint and release Updates; `NON_PARTICIPANTS` |
| [server/services/temporal/execution_control_activities.py](../../../../server/services/temporal/execution_control_activities.py) | 30 | Activities `execution_control.register` / `.unregister`: bridge from workflow code to the controller's Updates |
| [server/services/temporal/trigger_listener_workflow.py](../../../../server/services/temporal/trigger_listener_workflow.py) | 644 | Legacy push listener; `_spawn_child_run` (the one place a per-event `MachinaWorkflow` child is started); `_build_run_graph`; `_history_pressure`; `event_workflow_search_attributes` |
| [server/services/temporal/polling_trigger_workflow.py](../../../../server/services/temporal/polling_trigger_workflow.py) | 427 | Legacy polling listener; poll constants the controller reuses |
| [server/services/temporal/schedules.py](../../../../server/services/temporal/schedules.py) | 440 | Cron as Temporal Schedules: create or update in place, pause, delete |
| [server/nodes/scheduler/cron_scheduler/_workflow.py](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py) | 265 | `CronTriggerWorkflow`: one firing, one child run |
| [server/services/temporal/controller_queue.py](../../../../server/services/temporal/controller_queue.py) | 103 | Activities `controller.queue.spill` / `.read` over `workflow_queued_events`; dead-letter rows |
| [server/services/temporal/update_calls.py](../../../../server/services/temporal/update_calls.py) | 41 | `bounded_update` and `UpdatePending` |
| [server/services/temporal/lifecycle.py](../../../../server/services/temporal/lifecycle.py) | 336 | Connect loop, optional terminate sweep, worker start, boot reconcile, dev-server watchdog, `stuck_runs` |
| [server/services/temporal/patches.py](../../../../server/services/temporal/patches.py) | 24 | `DORMANT_PATCHES`, `activate_patch` |
| [server/services/temporal/_retry_policies.py](../../../../server/services/temporal/_retry_policies.py) | 229 | Every shared `RetryPolicy` constant |
| [server/services/temporal/_exits.py](../../../../server/services/temporal/_exits.py) | 27 | `is_workflow_bug` |
| [server/services/temporal/_interceptors.py](../../../../server/services/temporal/_interceptors.py) | 149 | `activity_retry` logging, `stuck_retry` attention items, replay-guarded workflow log |
| [server/services/temporal/workflow_registry.py](../../../../server/services/temporal/workflow_registry.py) | 38 | `framework_workflows()` (workers) and `replay_workflows()` (replay gate) |
| [server/services/temporal/worker.py](../../../../server/services/temporal/worker.py) | 960 | Worker construction, `_main_queue_activities`, per-queue pool, crash-restart loops, patch activation callback |
| [server/services/temporal/client.py](../../../../server/services/temporal/client.py) | 255 | Client connect, the one `TracingInterceptor`, search-attribute registration, terminate sweep |
| [server/services/temporal/search_attributes.py](../../../../server/services/temporal/search_attributes.py) | 191 | The seven custom search attributes |
| [server/services/temporal/executor.py](../../../../server/services/temporal/executor.py) | 259 | `TemporalExecutor` (top-level `MachinaWorkflow` starts), `capture_temporal_routing_input` |
| [server/services/temporal/workflow.py](../../../../server/services/temporal/workflow.py) | 1,291 | `MachinaWorkflow`; `GENERATION_FLAGS`; control handlers and gates of the graph run (read to L1162) |
| [server/services/temporal/agent_workflow.py](../../../../server/services/temporal/agent_workflow.py) | 3,549 | `AgentWorkflow`, `DelegatedTaskWorkflow`: control handlers, child starts, rollover (only those parts read) |
| [server/services/temporal/employee_job_workflow.py](../../../../server/services/temporal/employee_job_workflow.py) | 167 | `EmployeeJobWorkflow` (a registered root), reviewed delivery |
| [server/services/temporal/pi_inline_delegation.py](../../../../server/services/temporal/pi_inline_delegation.py) | 408 | `start_inline_pi_delegation` (a tool call admits and starts a detached workflow through the client) and `PiInlineDelegationWorkflow` (a registered root that runs one `DelegatedTaskWorkflow` child) |
| [server/services/temporal/workspace_tasks_workflow.py](../../../../server/services/temporal/workspace_tasks_workflow.py) | 215 | `WorkspaceTaskControllerWorkflow`: admission and Reset of direct Workspace tasks, outside generations |
| [server/services/temporal/approval_workflow.py](../../../../server/services/temporal/approval_workflow.py) | 75 | `ApprovalWorkflow`: the one non-participant child |
| [server/services/deployment/control.py](../../../../server/services/deployment/control.py) | 199 | `begin_generation`, `serialize_control` (capabilities, `can_edit`), revision-guarded `transition` |
| [server/services/deployment/handlers.py](../../../../server/services/deployment/handlers.py) | 2,961 | WebSocket commands Start, Stop, Resume, Reset, deploy; reconcile; boot recovery; recovery policies; circuit breaker |
| [server/services/deployment/manager.py](../../../../server/services/deployment/manager.py) | 1,477 | `DeploymentManager`: arms triggers (controller `register_trigger`, Schedules, Start node), process-local admission gate |
| [server/services/deployment/restart.py](../../../../server/services/deployment/restart.py) | 148 | `restart_with_latest_graph` (Reset then Start), `pending_changes` |
| [server/services/deployment/triggers.py](../../../../server/services/deployment/triggers.py) | 338 | In-process collector/processor triggers; `build_cron_expression`; trigger discovery |
| [server/services/deployment/runtime_state.py](../../../../server/services/deployment/runtime_state.py) | 62 | Reset's per-node archive and `reset_execution_state` call |
| [server/services/deployment/state.py](../../../../server/services/deployment/state.py) | 66 | `DeploymentState`, `TriggerInfo` |
| [server/services/deployment/canary_registry.py](../../../../server/services/deployment/canary_registry.py) | 137 | Which trigger types use Temporal, and their CloudEvents type |
| [server/services/deployment/poll_registry.py](../../../../server/services/deployment/poll_registry.py) | 55 | In-process poll coroutine factories (legacy path) |
| [server/services/events/dispatch.py](../../../../server/services/events/dispatch.py) | 289 | `emit`: Visibility query, `on_event` Signals, optional broadcast |
| [server/services/coding_agent_runtime.py](../../../../server/services/coding_agent_runtime.py) | 100 | `freeze_generation_harness`: copies generation flags from the manifest into workflow input |
| [server/services/employees/safe_apply.py](../../../../server/services/employees/safe_apply.py) | 363 | Apply without Reset: `pause_admissions`, `replace_graph`, snapshot rewrite |
| [server/models/database.py](../../../../server/models/database.py#L524) | L524-L616 | `WorkflowControlExecution`, `WORKFLOW_CONTROL_ACTIVE_STATES`, `WorkflowRunDataScope` |

## 3. How control executes

### 3.1 Start

1. The `start_workflow` command runs `_run_start` as a task of its own, shielded from the socket, so a dropped socket does not abandon it ([deployment/handlers.py:2091](../../../../server/services/deployment/handlers.py#L2091)).
2. Idempotency and compare-and-swap: a known idempotency key answers the earlier outcome; `expected_revision` must equal the latest row's revision; the latest row must be absent or `reset` ([deployment/handlers.py:2112](../../../../server/services/deployment/handlers.py#L2112)).
3. The graph is normalized, sanitized, given the team plan and validated. Errors answer `validation_failed` and create nothing ([deployment/handlers.py:2162](../../../../server/services/deployment/handlers.py#L2162)).
4. `begin_generation` builds the row: id `workflow-control:<workflow id>:<generation>`, `execution_id` `<workflow id>:execution:<n>`, `controller_workflow_id`, `graph_snapshot`, `resource_manifest`, and a revision that continues from the previous generation ([deployment/control.py:120](../../../../server/services/deployment/control.py#L120), [core/database.py:280](../../../../server/core/database.py#L280)). `create_workflow_control` inserts the row and a `WorkflowRunDataScope` in one transaction ([core/database.py:3180](../../../../server/core/database.py#L3180)). The row's status starts as `starting` ([models/database.py:556](../../../../server/models/database.py#L556)).
5. `_start_controller` starts the controller with the Temporal client: `start_workflow("WorkflowControlWorkflow", args=[{workflow_id, generation, execution_id, root_execution_id, data_scope_id, state: "running", revision, execution_control_version, controller_workflow_id}], id=controller_workflow_id, search_attributes=[EventWorkflowId])`. The run id is stored as `controller_run_id` ([deployment/handlers.py:638](../../../../server/services/deployment/handlers.py#L638), [deployment/handlers.py:2223](../../../../server/services/deployment/handlers.py#L2223)).
6. `handle_deploy_workflow` requires a live generation, reads nodes, edges and parameters from the row's `graph_snapshot` (never from the request), validates again, takes the broadcaster lock and runs the deployment as a task ([deployment/handlers.py:216](../../../../server/services/deployment/handlers.py#L216), [deployment/handlers.py:350](../../../../server/services/deployment/handlers.py#L350)).
7. `DeploymentManager.deploy` arms the triggers ([deployment/manager.py:272](../../../../server/services/deployment/manager.py#L272)):
   - cron nodes become Temporal Schedules ([deployment/manager.py:1044](../../../../server/services/deployment/manager.py#L1044));
   - a Start node fires once per generation, guarded by a claim in the runtime-mutation ledger ([deployment/manager.py:548](../../../../server/services/deployment/manager.py#L548));
   - an event trigger of a live controlled generation is sent to the controller as a `register_trigger` Signal carrying its frozen `listener_args` ([deployment/manager.py:947](../../../../server/services/deployment/manager.py#L947)).
8. Start waits for that setup, then moves the row `starting` to `running` ([deployment/handlers.py:2249](../../../../server/services/deployment/handlers.py#L2249)).
9. A Start that raises is undone last in, first out: cancel the local deployment, Signal the controller `reset`, terminate the generation's executions, fail the row, broadcast. Each step runs even if the one before failed ([deployment/handlers.py:2043](../../../../server/services/deployment/handlers.py#L2043)).

### 3.2 An event becomes a run

**Push trigger.**

1. The producer calls `dispatch.emit(event, broadcast=...)` ([events/dispatch.py:63](../../../../server/services/events/dispatch.py#L63)).
2. `emit` lists Running workflows whose `EventType` search attribute equals the event's type, plus every Running `WorkflowControlWorkflow`. Both clauses are narrowed by `EventWorkflowId` when the envelope carries a `workflow_id`. Controllers whose `ControlEventTypes` attribute lacks the type are skipped. Each target gets the Signal `on_event`, addressed by workflow id ([events/dispatch.py:132](../../../../server/services/events/dispatch.py#L132), [events/dispatch.py:251](../../../../server/services/events/dispatch.py#L251)). Delivery is best effort: a failed query or Signal is logged, not retried ([events/dispatch.py:163](../../../../server/services/events/dispatch.py#L163)).
3. The controller's `on_event` handler queues the event for each trigger spec that is not retired, not a polling trigger, and listens for the type. The dedup key is `<listener id>:<event id>` ([workflow_control_workflow.py:357](../../../../server/services/temporal/workflow_control_workflow.py#L357)).
4. The main loop pops the next due event while `_state == "running"` and calls `_spawn_push_run` ([workflow_control_workflow.py:449](../../../../server/services/temporal/workflow_control_workflow.py#L449), [workflow_control_workflow.py:853](../../../../server/services/temporal/workflow_control_workflow.py#L853)). No listener workflow runs; the controller constructs a `TriggerListenerWorkflow` object only to call its helper.
5. `_spawn_child_run` ([trigger_listener_workflow.py:309](../../../../server/services/temporal/trigger_listener_workflow.py#L309)):
   - evaluates the trigger's own filter in the activity `evaluate_trigger_filter_activity` when `filter_params` is set, behind the marker `machina-trigger-listener-node-filter`;
   - builds the run graph: the firing trigger is marked `_pre_executed` with the event as its output, sibling triggers are pre-executed as `not_triggered` ([trigger_listener_workflow.py:607](../../../../server/services/temporal/trigger_listener_workflow.py#L607));
   - broadcasts the trigger status in an activity;
   - calls the admission check, then starts the child inside `child_start_control.child_start()` ([trigger_listener_workflow.py:440](../../../../server/services/temporal/trigger_listener_workflow.py#L440)).
6. The child's options, verbatim ([trigger_listener_workflow.py:443](../../../../server/services/temporal/trigger_listener_workflow.py#L443)). `child_id` is `<slug>-<trigger label>-<event id>` and the search attribute is `EventWorkflowId`; no timeout is passed:

```python
child_options: Dict[str, Any] = {
    "id": child_id,
    "parent_close_policy": ParentClosePolicy.ABANDON,
    "id_reuse_policy": (
        WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY
    ),
}
if search_attributes is not None:
    child_options["search_attributes"] = search_attributes
```

7. The child payload carries the filtered graph, ids, `execution_control_*` keys, `_temporal_routing_v1`, `graphVersion`, `generation`, `context_execution_id` and `parameter_snapshot` ([trigger_listener_workflow.py:453](../../../../server/services/temporal/trigger_listener_workflow.py#L453)).
8. The child `MachinaWorkflow` binds control state from its input and enrolls as a root before any business work: activity `execution_control.register`, which sends the controller the Update `register_execution` ([temporal/workflow.py:514](../../../../server/services/temporal/workflow.py#L514), [temporal/execution_control.py:206](../../../../server/services/temporal/execution_control.py#L206), [execution_control_activities.py:17](../../../../server/services/temporal/execution_control_activities.py#L17), [workflow_control_workflow.py:234](../../../../server/services/temporal/workflow_control_workflow.py#L234)).
9. A spawn that fails: a duplicate child id counts as already accepted (with the accumulator marker); any other failure goes back to the head of the queue and is retried every 5 s, or, once `controller-spawn-backoff-v1` is active, waits behind the queue and is dead-lettered on its fifth failure ([workflow_control_workflow.py:530](../../../../server/services/temporal/workflow_control_workflow.py#L530), [workflow_control_workflow.py:834](../../../../server/services/temporal/workflow_control_workflow.py#L834)).

**Polling trigger.** The controller runs one asyncio task per polling spec ([workflow_control_workflow.py:892](../../../../server/services/temporal/workflow_control_workflow.py#L892)): a `workflow.sleep` of `max(30, poll_interval)` seconds, then the activity `poll.<node type>.v<version>` with `activity_id` set to the trigger node id, inside `self._execution_control.action()`. The first cycle only records a baseline. New items join the same queue with the same dedup, and `seen_ids` is written back into the trigger spec. The main loop then spawns each item as in steps 4 to 8, with `payload_mode="native"`.

**Cron.** `create_cron_schedule` creates a Schedule with id `<slug>-<trigger label>` whose action starts `CronTriggerWorkflow` with id `<slug>-<label>-{{.ScheduledStartTime}}` and the search attributes `EventWorkflowId`, `TriggerNodeId` and `EventTriggerKind="cron"`; overlap policy SKIP, catch-up window 24 h ([schedules.py:193](../../../../server/services/temporal/schedules.py#L193)). Each firing enrolls as a root, then starts one `MachinaWorkflow` child with the same ABANDON options ([cron_scheduler/_workflow.py:75](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py#L75), [cron_scheduler/_workflow.py:176](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py#L176)).

**Start node and in-process triggers.** `_spawn_run` leads to `TemporalExecutor.execute_workflow`, which starts a top-level `MachinaWorkflow` with id `<slug>-<uuid8>` and `EventWorkflowId`. For an admitted generation the payload gets `controller_workflow_id`, `execution_control_version` and the generation's flags, so this run also enrolls as a root ([deployment/manager.py:1211](../../../../server/services/deployment/manager.py#L1211), [deployment/manager.py:1308](../../../../server/services/deployment/manager.py#L1308), [executor.py:126](../../../../server/services/temporal/executor.py#L126)).

**Legacy listeners.** Without a live controlled generation, `_start_canary_listener` starts a standalone `TriggerListenerWorkflow` or `PollingTriggerWorkflow` with `USE_EXISTING` and four search attributes ([deployment/manager.py:981](../../../../server/services/deployment/manager.py#L981)). INFERRED: current code cannot reach that branch for a new Start, because the only production caller of `DeploymentManager.deploy` is `handle_deploy_workflow`, which refuses without a live generation ([deployment/handlers.py:216](../../../../server/services/deployment/handlers.py#L216)).

### 3.3 Stop and Resume

Every generation started by current code is version 1 (`execution_control_version: 1` in the manifest, [deployment/control.py:168](../../../../server/services/deployment/control.py#L168)).

**Stop, version 1.**

1. `handle_pause_workflow` moves the row `running` to `pausing` under a per-workflow lock, schedules one transition task per control revision, broadcasts, and closes the process-local admission gate ([deployment/handlers.py:2338](../../../../server/services/deployment/handlers.py#L2338)).
2. The socket waits at most 10 s for that task, then answers `transition_pending: true`; the drain continues in the task ([deployment/handlers.py:153](../../../../server/services/deployment/handlers.py#L153)).
3. The task pauses the cron Schedules (`ScheduleHandle.pause`) and marks trigger nodes paused ([deployment/handlers.py:1387](../../../../server/services/deployment/handlers.py#L1387), [schedules.py:384](../../../../server/services/temporal/schedules.py#L384)).
4. `transition_generation(paused=True)` ([deployment/execution_control.py:56](../../../../server/services/deployment/execution_control.py#L56)):
   1. Describe the controller by workflow id and compare its first run id with `controller_run_id`.
   2. Update the controller: `set_control_state` with `{generation, revision, state: "paused", first_execution_run_id, producers_held: True}`, Update id `control:<revision>:admission`. The controller's `_state` becomes `paused` ([workflow_control_workflow.py:129](../../../../server/services/temporal/workflow_control_workflow.py#L129)).
   3. Query the controller's `status` for `membership_epoch` and `live_roots`. Walk the controller and every root: send each running workflow the same Update, then Describe it again and enqueue its `pending_children`, except types in `NON_PARTICIPANTS` ([deployment/execution_control.py:99](../../../../server/services/deployment/execution_control.py#L99)).
   4. In each workflow the handler applies the revision and waits until child starts already in progress are acknowledged, so the next Describe sees every child ([temporal/execution_control.py:108](../../../../server/services/temporal/execution_control.py#L108)).
   5. Send every target the Update `wait_for_checkpoint` (id `control:<revision>:checkpoint`) concurrently. Each answers when its `_actions` and `_child_starts` counters are both 0 ([deployment/execution_control.py:154](../../../../server/services/deployment/execution_control.py#L154), [temporal/execution_control.py:114](../../../../server/services/temporal/execution_control.py#L114)).
   6. Query `membership_epoch` again; if a root registered meanwhile, repeat from step 3.
5. The row moves `pausing` to `paused` ([deployment/handlers.py:1410](../../../../server/services/deployment/handlers.py#L1410)).

How each kind of running work is reached: a per-event graph run, a cron firing, an employee job, a Task Manager runner (`DelegatedTaskWorkflow`) and a detached Coding Agent delegation (`PiInlineDelegationWorkflow`) are registered roots in the controller's `live_roots` (the five `register_root()` call sites: [temporal/workflow.py:520](../../../../server/services/temporal/workflow.py#L520), [cron_scheduler/_workflow.py:81](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py#L81), [employee_job_workflow.py:105](../../../../server/services/temporal/employee_job_workflow.py#L105), [agent_workflow.py:609](../../../../server/services/temporal/agent_workflow.py#L609), [pi_inline_delegation.py:359](../../../../server/services/temporal/pi_inline_delegation.py#L359)); an agent child of a graph run and an agent delegated directly by another agent are attached children, found through `pending_children` of the workflow above them.

The gate every participant shares, verbatim ([temporal/execution_control.py:121](../../../../server/services/temporal/execution_control.py#L121)):

```python
async def wait_until_running(self) -> None:
    if self.enabled and (self.state != "running" or self.producers_held):
        await workflow.wait_condition(lambda: self.state == "running" and not self.producers_held)

async def begin_action(self) -> None:
    await self.wait_until_running()
    if self.enabled:
        self._actions += 1
```

**Where a stopped workflow parks** (the cooperative pause points):

| Workflow | Pause points |
|---|---|
| `MachinaWorkflow` | Before the trigger-output store activity, before the chat-run claim, and before each node dispatch (`_wait_until_resumed`); `ready` is emptied while the state is not running; a node activity is admitted through `action()`, an agent child through `child_start()` ([temporal/workflow.py:284](../../../../server/services/temporal/workflow.py#L284), [temporal/workflow.py:677](../../../../server/services/temporal/workflow.py#L677), [temporal/workflow.py:819](../../../../server/services/temporal/workflow.py#L819), [temporal/workflow.py:847](../../../../server/services/temporal/workflow.py#L847), [temporal/workflow.py:892](../../../../server/services/temporal/workflow.py#L892)) |
| `WorkflowControlWorkflow` | The main loop pops events only while running; `_wait_until_running` raises `_AdmissionDeferred` and the event stays queued; each poll loop waits for running before its next cycle ([workflow_control_workflow.py:382](../../../../server/services/temporal/workflow_control_workflow.py#L382), [workflow_control_workflow.py:884](../../../../server/services/temporal/workflow_control_workflow.py#L884), [workflow_control_workflow.py:919](../../../../server/services/temporal/workflow_control_workflow.py#L919)) |
| `AgentWorkflow`, `DelegatedTaskWorkflow` | `_wait_until_resumed` plus `action()` or `child_start()` around steps, tool calls and delegation starts; the agent's rollover also waits until running ([agent_workflow.py:599](../../../../server/services/temporal/agent_workflow.py#L599), [agent_workflow.py:864](../../../../server/services/temporal/agent_workflow.py#L864), [agent_workflow.py:1884](../../../../server/services/temporal/agent_workflow.py#L1884), [agent_workflow.py:3187](../../../../server/services/temporal/agent_workflow.py#L3187)) |
| `EmployeeJobWorkflow`, `CronTriggerWorkflow` | The child start and the delivery activity are fenced ([employee_job_workflow.py:122](../../../../server/services/temporal/employee_job_workflow.py#L122), [cron_scheduler/_workflow.py:187](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py#L187)) |
| `PiCodingAgentWorkflow` | Inherits `AgentWorkflow`; its `wait_for_checkpoint` also stops its external driver ([pi_workflow.py:50](../../../../server/services/temporal/pi_workflow.py#L50)) |
| `PiInlineDelegationWorkflow` | Waits for the running state right after it registers; the admission activity and the child start are fenced ([pi_inline_delegation.py:359](../../../../server/services/temporal/pi_inline_delegation.py#L359)) |

**Resume, version 1.** The row moves `paused` to `resuming`. `transition_generation(paused=False)` sends the admission Update with `state: "running", producers_held: True` to the controller and the tree, then release Updates (`producers_held: False`, id `control:<revision>:release`) to descendants before roots, then to the controller with `expected_membership_epoch`; a changed epoch leaves the controller held and the loop runs again ([deployment/execution_control.py:184](../../../../server/services/deployment/execution_control.py#L184), [workflow_control_workflow.py:131](../../../../server/services/temporal/workflow_control_workflow.py#L131)). The task then unpauses the Schedules, drains locally queued events, and moves the row to `running` with `last_resumed_at` and a cleared pause reason ([deployment/handlers.py:1416](../../../../server/services/deployment/handlers.py#L1416)).

**Version 0 (generations admitted before execution control).** A string Update `set_control_state("paused" | "running")` on the controller, the Schedule pause, then Signals `pause` or `resume` to every Running execution Visibility finds for the generation ([deployment/handlers.py:2381](../../../../server/services/deployment/handlers.py#L2381), [deployment/handlers.py:941](../../../../server/services/deployment/handlers.py#L941)). Version 1 workflows ignore those Signals ([temporal/workflow.py:251](../../../../server/services/temporal/workflow.py#L251)).

**Chat Stop.** `stop_chat_run` on a controlled generation calls the same pause handler with an owning-generation guard. On an uncontrolled run it marks the run `stopping`, and the watchdog (every 30 s) cancels the run's workflow once a 45 s grace has passed ([chat/handlers.py:510](../../../../server/services/chat/handlers.py#L510), [chat/ledger.py:724](../../../../server/services/chat/ledger.py#L724)).

### 3.4 What Stop does not interrupt, and what a paused generation still does

Not interrupted by Stop:

- **An admitted activity.** It runs to its end under its own retry policy; the row stays `pausing` until it does. The LLM step's policy has no attempt limit ([_retry_policies.py:97](../../../../server/services/temporal/_retry_policies.py#L97)).
- **Whole-node agents.** `rlm_agent` and `claude_code_agent` are excluded from `AGENT_WORKFLOW_TYPES` and run as one activity, so Stop waits for the whole node ([temporal/workflow.py:106](../../../../server/services/temporal/workflow.py#L106)). [TEMPORAL_ARCHITECTURE.md](../../../TEMPORAL_ARCHITECTURE.md) adds `codex_agent`.
- **`ApprovalWorkflow`**, the one type in `NON_PARTICIPANTS` ([deployment/execution_control.py:25](../../../../server/services/deployment/execution_control.py#L25)); and, in a generation with `approval_wait_version` 1, the Approval gate node's activity, which is deliberately not an admitted action ([temporal/workflow.py:888](../../../../server/services/temporal/workflow.py#L888)).
- **Waits that are not actions**: a child's result, the subagent permit wait, root registration ([agent_workflow.py:641](../../../../server/services/temporal/agent_workflow.py#L641), [temporal/execution_control.py:206](../../../../server/services/temporal/execution_control.py#L206)).
- **End-of-run bookkeeping.** `chat_run.finish`, `workflow_control.pause_on_failure`, `workflow_runs.record_completion` and the root's unregistration are scheduled without an admission gate, so a run whose last node finishes during Stop still completes ([temporal/workflow.py:943](../../../../server/services/temporal/workflow.py#L943), [temporal/workflow.py:527](../../../../server/services/temporal/workflow.py#L527)).
- **Direct Workspace tasks and approved sends.** They run outside the generation ([temporal-workflow-control.md](../../../temporal-workflow-control.md), section "Workspace tasks").
- **In-process delegations.** A delegation started by an agent that does not run as `AgentWorkflow` is an `asyncio.create_task` in the handling process ([services/handlers/tools.py:900](../../../../server/services/handlers/tools.py#L900)). RFC-0006 section 6.2 says Stop and Reset do not reach it; NOT VERIFIED here. A Coding Agent target with `pi_harness_version` 1 takes the durable path of section 7 instead ([services/handlers/tools.py:388](../../../../server/services/handlers/tools.py#L388)).

A paused generation still:

- accepts `on_event` Signals and queues them, deduplicated, and can continue-as-new while paused, spilling the queue to the database ([workflow_control_workflow.py:376](../../../../server/services/temporal/workflow_control_workflow.py#L376));
- keeps its Schedules paused in Temporal;
- lets a poll loop's current `workflow.sleep` timer fire; the loop then waits for the running state without polling ([workflow_control_workflow.py:928](../../../../server/services/temporal/workflow_control_workflow.py#L928));
- keeps the timeouts and heartbeats of admitted activities running until they finish;
- queues events of in-process triggers in memory, which a restart loses ([deployment/manager.py:1225](../../../../server/services/deployment/manager.py#L1225));
- is exempt from the chat watchdog's age limit ([chat/ledger.py:760](../../../../server/services/chat/ledger.py#L760));
- has no workflow-level timer to expire, because no child carries a lifetime timeout (section 5 rule 12);
- has an editable canvas (section 3.8).

### 3.5 Reset

1. `_reset_workflow` moves the row from any of `starting`, `running`, `pausing`, `paused`, `resuming`, `failed` to `resetting`, closes local admission and cancels local transition tasks ([deployment/handlers.py:2617](../../../../server/services/deployment/handlers.py#L2617)).
2. Signal the controller `reset`: it sets `_closed`, cancels its poll tasks, leaves its loop and completes ([deployment/handlers.py:2641](../../../../server/services/deployment/handlers.py#L2641), [workflow_control_workflow.py:289](../../../../server/services/temporal/workflow_control_workflow.py#L289)).
3. Delete the cron Schedules, strictly ([deployment/handlers.py:2652](../../../../server/services/deployment/handlers.py#L2652)).
4. Cancel the process-local deployment ([deployment/handlers.py:2656](../../../../server/services/deployment/handlers.py#L2656)).
5. Terminate every running execution of the generation: `WorkflowHandle.terminate(reason="workflow_reset")` for each hit of the query `(RootWorkflowId='<controller id>' OR EventWorkflowId='<workflow id>') AND ExecutionStatus='Running'`, expanded by `RootWorkflowId IN (...)` in batches of 40 ([deployment/handlers.py:870](../../../../server/services/deployment/handlers.py#L870), [deployment/handlers.py:1000](../../../../server/services/deployment/handlers.py#L1000)).
6. Close in the database what the terminated runs could not: approval waits, browser task claims, open team work and requests, waiting Apply requests, dead-letter rows; reset the Workspace tasks (an Update, bounded at 240 s); archive the data scope; call every node class's `reset_execution_state` ([deployment/handlers.py:2683](../../../../server/services/deployment/handlers.py#L2683), [deployment/runtime_state.py:8](../../../../server/services/deployment/runtime_state.py#L8)).
7. The row moves `resetting` to `reset`, which the client sees as `ready` ([deployment/handlers.py:2720](../../../../server/services/deployment/handlers.py#L2720), [deployment/control.py:49](../../../../server/services/deployment/control.py#L49)).

A step that raises leaves the row `resetting`. The same Reset runs again from the owner's retry or the next boot ([deployment/handlers.py:2560](../../../../server/services/deployment/handlers.py#L2560)). Termination runs no workflow code: no `finally`, no compensation. The handler says so: "A terminated run closes none of its own records" ([deployment/handlers.py:2691](../../../../server/services/deployment/handlers.py#L2691)).

### 3.6 Apply and restart

**What `restart.py` does.** `restart_with_latest_graph` resets the generation and, if it was running, starts a new one from the saved graph. In-flight runs are terminated by the Reset and the conversations are cleared ([deployment/restart.py:72](../../../../server/services/deployment/restart.py#L72)). `pending_changes` compares the saved graph with the snapshot by node (id, type, label key, disabled) and edge (ends, handles, condition) ([deployment/restart.py:110](../../../../server/services/deployment/restart.py#L110)).

**What production calls.** A search of `server/` finds no caller of `restart_with_latest_graph` outside its test. Apply and Turn on Talk call `apply_saved_changes` (Safe Apply) and use only `pending_changes` from `restart.py` ([employees/handlers.py:219](../../../../server/services/employees/handlers.py#L219), [employees/handlers.py:228](../../../../server/services/employees/handlers.py#L228)).

**Safe Apply** ([employees/safe_apply.py:103](../../../../server/services/employees/safe_apply.py#L103)):

1. Update the controller `pause_admissions(drain_tasks)`; pause the Schedules; close the local gate. This "does not signal Pause to children".
2. If work is in flight, answer `waiting` and interrupt nothing; a recovery loop retries every 10 s ([employees/activation.py:55](../../../../server/services/employees/activation.py#L55)). With `stop_work`, call `WorkflowHandle.cancel()` on every Running execution tagged `EventWorkflowId`, except controllers and listeners, and close the open work rows.
3. Rewrite each Schedule's action arguments, then Update the controller `replace_graph(snapshot)`. Its validator requires the paused state. It swaps `nodes`, `edges`, `parameter_snapshot` and `graphVersion` in each remaining trigger's `listener_args` and marks triggers whose node is gone `retired` ([workflow_control_workflow.py:297](../../../../server/services/temporal/workflow_control_workflow.py#L297)).
4. Write the row's `graph_snapshot` and `graph_hash`, add 1 to its revision, and send the string Update `set_control_state("running")`.

The generation, its controller and its conversations stay. Runs already started keep the snapshot in their own input; only later runs use the new graph.

### 3.7 Boot recovery and recovery policies

1. `run_temporal_lifecycle` connects (retry every 3 s), runs the terminate sweep only if `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP` is on and no control row is active, starts the workers, runs the boot reconcile, then stays as the dev-server watchdog ([lifecycle.py:64](../../../../server/services/temporal/lifecycle.py#L64), [lifecycle.py:171](../../../../server/services/temporal/lifecycle.py#L171)).
2. `reconcile_active_controls_on_boot`, per active row ([deployment/handlers.py:1585](../../../../server/services/deployment/handlers.py#L1585)):
   - `resetting`: run the Reset once more;
   - version 1 `pausing` or `resuming`: finish the transition in a background task, then re-arm;
   - `starting`: commit `running` if the controller holds triggers or the graph has none, else fail the generation and close the orphan controller ([deployment/handlers.py:1747](../../../../server/services/deployment/handlers.py#L1747));
   - `running` or `paused`: `_rearm_generation` deploys again from `graph_snapshot` ([deployment/handlers.py:1806](../../../../server/services/deployment/handlers.py#L1806)). The re-arm is idempotent: a known listener keeps its first spec and takes only the re-sent parameter snapshot ([workflow_control_workflow.py:340](../../../../server/services/temporal/workflow_control_workflow.py#L340)), a Schedule is updated in place and keeps its paused state ([schedules.py:254](../../../../server/services/temporal/schedules.py#L254)), and a Start node does not fire twice.
3. **Crash recovery.** Boot stamps the cache key `workflow_control:shutdown_state` with `running`; a graceful shutdown stamps `clean`. If boot finds `running` and `WORKFLOW_CONTROL_CRASH_RECOVERY=pause` (the default), each generation still `running` is paused through the normal pause handler with `pause_reason=recovery` ([deployment/handlers.py:1508](../../../../server/services/deployment/handlers.py#L1508), [deployment/handlers.py:1538](../../../../server/services/deployment/handlers.py#L1538)).
4. **Missing controller.** A version 0 generation with `WORKFLOW_CONTROL_MISSING_CONTROLLER=pause` converges to `paused`, and Resume rebuilds the controller with `WorkflowIDConflictPolicy.USE_EXISTING` and re-arms it from the snapshot. A version 1 generation always fails (`controller_execution_missing`) with admission closed and Schedules paused, because its root registry cannot be rebuilt; it needs Reset, then Start ([deployment/handlers.py:796](../../../../server/services/deployment/handlers.py#L796), [deployment/handlers.py:1939](../../../../server/services/deployment/handlers.py#L1939)).
5. **Circuit breaker.** A failed trigger-spawned `MachinaWorkflow` schedules the activity `workflow_control.pause_on_failure` (one attempt). The activity side counts a streak in the cache table and calls the pause handler at the threshold: 3 failed runs, each within 600 s of the one before. A failure marked `requires_user_action` pauses at once. A success does not reset the streak; Resume and a trip clear it ([temporal/workflow.py:291](../../../../server/services/temporal/workflow.py#L291), [deployment/handlers.py:2831](../../../../server/services/deployment/handlers.py#L2831)).
6. **Attention items.** `stuck_retry`: an activity attempt past its limit (8 for the LLM step and compaction, 10 otherwise; planned waits excluded) ([_interceptors.py:44](../../../../server/services/temporal/_interceptors.py#L44)). `stuck_runs`: every 300 s the watchdog counts Running workflows with `TemporalReportedProblems`, which the pinned server sets after 5 failed workflow tasks in a row ([lifecycle.py:316](../../../../server/services/temporal/lifecycle.py#L316), [config/temporal_cli.json:9](../../../../server/config/temporal_cli.json#L9)). An item drops off one hour after its last sighting ([services/attention.py:37](../../../../server/services/attention.py#L37)).
7. **Worker crash.** Both worker loops build a fresh `Worker` per restart attempt, with a doubling backoff ([worker.py:280](../../../../server/services/temporal/worker.py#L280), [worker.py:571](../../../../server/services/temporal/worker.py#L571)).

### 3.8 Canvas lock

`serialize_control` emits `can_edit`, true for `ready`, `paused`, `failed` and a workflow never started ([deployment/control.py:39](../../../../server/services/deployment/control.py#L39)). The client renders it verbatim once a generation exists; the broadcaster lock that `handle_deploy_workflow` takes decides only for workflows no generation governs ([lib/canvasLock.ts:35](../../../../client/src/lib/canvasLock.ts#L35)). Editing a paused generation changes nothing that runs until Apply, a Reset or the next Start.

### 3.9 Continue-as-new in long-lived workflows

The shared test, verbatim without its docstring ([trigger_listener_workflow.py:90](../../../../server/services/temporal/trigger_listener_workflow.py#L90)):

```python
def _history_pressure(soft_cap: int) -> bool:
    try:
        info = workflow.info()
        return bool(
            info.is_continue_as_new_suggested()
            or info.get_current_history_length() > soft_cap
        )
    except Exception:  # direct unit invocation outside Temporal runtime
        return False
```

| Workflow | Rolls over when | Carries | Guards |
|---|---|---|---|
| `WorkflowControlWorkflow` ([workflow_control_workflow.py:670](../../../../server/services/temporal/workflow_control_workflow.py#L670)) | Pressure at 10,000 events, checked after each event, poll cycle and (with `controller-messaging-v1`) Update. Only the main loop acts on it. Works while paused | The start input plus state, revision, trigger specs with their `seen_ids`, up to 256 queued events (the rest spilled to `workflow_queued_events`), overflow position, `drain_tasks`, the last 4,096 dedup keys; version 1 adds control state, `live_roots` and `membership_epoch` | Version 1 waits for its own checkpoint and for `workflow.all_handlers_finished`; poll tasks are cancelled and awaited; an encoded input above 1,900,000 bytes raises the non-retryable `ControllerContinuationTooLarge`; a controller closed by Reset does not roll over |
| `AgentWorkflow` ([agent_workflow.py:3172](../../../../server/services/temporal/agent_workflow.py#L3172)) | Pressure at 10,000 events, at a turn boundary, with no live delegation | Context, control state and `_agent_resume` (iteration, transcript, usage, prepared payload without its seed keys, tools, thinking text) | Parks until running; waits for handlers; above 1,900,000 bytes raises `AgentContinuationTooLarge`. The legacy path carries the transcript only and drops it above 1,000,000 bytes |
| `TriggerListenerWorkflow`, legacy ([trigger_listener_workflow.py:290](../../../../server/services/temporal/trigger_listener_workflow.py#L290)) | 16,000 processed events or pressure, checked after a processed event | The last 256 queued events (older ones dropped with a warning) and the pause flag | None |
| `PollingTriggerWorkflow`, legacy ([polling_trigger_workflow.py:238](../../../../server/services/temporal/polling_trigger_workflow.py#L238)) | Same, checked after a successful cycle | `seen_ids` and the pause flag | None |
| `WorkspaceTaskControllerWorkflow` ([workspace_tasks_workflow.py:195](../../../../server/services/temporal/workspace_tasks_workflow.py#L195)) | Idle, and 256 operations, the server's suggestion, or 10,000 events | Epoch and the last 128 submissions and resets | Only with no active child, no start in progress, no reset and no unfinished handler |

## 4. Inputs, outputs and state

### 4.1 The control row

`WorkflowControlExecution` ([models/database.py:534](../../../../server/models/database.py#L534)), one per generation, unique on `(workflow_id, generation)` and on `(workflow_id, idempotency_key)`:

| Field | Meaning |
|---|---|
| `id` | `workflow-control:<workflow id>:<generation>` |
| `generation` | 1, 2, ... per workflow; a new one is admitted only when the latest is `reset` |
| `execution_id`, `root_execution_id`, `data_scope_id` | The application execution identity `<workflow id>:execution:<n>`; also the session namespace of node outputs and conversations |
| `controller_workflow_id` | `workflow-control-<workflow id>-g<generation>`; handles use this id only |
| `controller_run_id` | The controller's first run id, used as chain identity, never as an address |
| `graph_snapshot` | `{graphVersion, owner_id, parameters (sanitized), nodes, edges, workflow_slug}` ([deployment/control.py:157](../../../../server/services/deployment/control.py#L157)); Safe Apply replaces it in place |
| `graph_hash` | SHA-256 over nodes and edges |
| `resource_manifest` | Version fields (4.2), plus `last_resumed_at` after a Resume |
| `status` | `starting`, `running`, `pausing`, `paused`, `resuming`, `resetting`, `reset`, `failed`. Active set: all but `reset` and `failed` ([models/database.py:529](../../../../server/models/database.py#L529)) |
| `revision` | Compare-and-swap counter; every transition adds 1 ([core/database.py:3270](../../../../server/core/database.py#L3270)); it does not restart at a new generation |
| `pause_reason`, `pause_detail` | `failures`, `recovery` or `controller_missing` for an automatic pause |
| `terminal_reason` | For example `workflow_reset`, `controller_execution_missing`, `interrupted_start` |

### 4.2 Version fields and generation flags

| Field | Values | Written | Read | Selects |
|---|---|---|---|---|
| `execution_control_version` | 1, or absent | Manifest at Start ([deployment/control.py:168](../../../../server/services/deployment/control.py#L168)); copied into the controller input, `listener_args`, cron arguments and executor payloads ([deployment/handlers.py:631](../../../../server/services/deployment/handlers.py#L631), [deployment/manager.py:960](../../../../server/services/deployment/manager.py#L960)) | `ExecutionControl.bind` ([temporal/execution_control.py:39](../../../../server/services/temporal/execution_control.py#L39)) | The acknowledged Stop protocol instead of the legacy `pause` / `resume` Signals |
| `pi_harness_version` (generation flag) | 1, or absent | Manifest at Start; `freeze_generation_harness` copies the manifest's value over the starter's default ([coding_agent_runtime.py:60](../../../../server/services/coding_agent_runtime.py#L60)) | `_frozen_routing_from_input`, `_resolve_dispatch` ([temporal/workflow.py:190](../../../../server/services/temporal/workflow.py#L190), [temporal/workflow.py:1029](../../../../server/services/temporal/workflow.py#L1029)) | The Coding Agent runs as `PiCodingAgentWorkflow` |
| `approval_wait_version` (generation flag) | 1, or absent | Not written by Start today; frozen only when the manifest holds it | [temporal/workflow.py:788](../../../../server/services/temporal/workflow.py#L788), [temporal/workflow.py:891](../../../../server/services/temporal/workflow.py#L891), [agent_workflow.py:342](../../../../server/services/temporal/agent_workflow.py#L342) | Approval modes; the gate waits outside Stop; a chat turn waits in `ApprovalWorkflow` |
| `_temporal_routing_v1` | `{version: 1, agent_workflow_enabled, per_type_dispatch_enabled, worker_pool_enabled}` plus the flags | `capture_temporal_routing_input`, from live settings when a trigger is armed or a top-level run starts ([executor.py:29](../../../../server/services/temporal/executor.py#L29)) | `_frozen_routing_from_input`; an unknown version falls back to `_SAFE_FROZEN_ROUTING` ([temporal/workflow.py:148](../../../../server/services/temporal/workflow.py#L148)) | Child workflow or activity per agent node; per-type activity names; per-queue routing |
| `graphVersion` with `generation` | `graphVersion >= 2` and `generation > 0` | Snapshot at Start; `listener_args` ([deployment/manager.py:914](../../../../server/services/deployment/manager.py#L914)) | [trigger_listener_workflow.py:478](../../../../server/services/temporal/trigger_listener_workflow.py#L478), [temporal/workflow.py:749](../../../../server/services/temporal/workflow.py#L749) | Whether node contexts carry the conversation scope (`data_scope_id`, `context_execution_id`) |
| `context_pressure_version` | A constant recorded per agent run | The result of `agent.prepare_payload` ([agent_activities.py:1873](../../../../server/services/temporal/agent_activities.py#L1873)) | [agent_workflow.py:1251](../../../../server/services/temporal/agent_workflow.py#L1251) | Which transcript-relief rules a run and its replay follow |

`GENERATION_FLAGS` itself, verbatim ([temporal/workflow.py:187](../../../../server/services/temporal/workflow.py#L187)):

```python
GENERATION_FLAGS = ("pi_harness_version", "approval_wait_version")
```

How a later run reads a flag: for a push or polling trigger the value sits in the trigger's `listener_args`, recorded in the controller's history at `register_trigger` and carried through every rollover; `_spawn_child_run` copies the `_temporal_routing_v1` dict into the child's input; `MachinaWorkflow` reads it once and puts each flag on node contexts; `_INHERITED_SCOPE_KEYS` forwards it to delegated children and tool calls ([agent_workflow.py:310](../../../../server/services/temporal/agent_workflow.py#L310)). A top-level run (Start node, in-process trigger) reads the control row when it starts ([executor.py:162](../../../../server/services/temporal/executor.py#L162)).

INFERRED: the three routing booleans are frozen at Start only for push and polling triggers. A re-arm rewrites a cron Schedule's action arguments with a fresh capture ([deployment/manager.py:1111](../../../../server/services/deployment/manager.py#L1111), [schedules.py:262](../../../../server/services/temporal/schedules.py#L262)), and a top-level run captures them at its own start. The two generation flags come from the control row on every path.

Other version keys appear in workflow code and were not traced for this note: `native_workspace_version`, `pi_local_control_version`, `history_version`, `dispatch_version`, `runtime_behavior_version`, `browser_runtime_version`, `browser_routing_version`, `inline_delegation_version`, `lease_version`. None of them is written by `begin_generation`.

### 4.3 Controller input and trigger specs

- **Start input**: `{workflow_id, generation, execution_id, root_execution_id, data_scope_id, state, revision, execution_control_version, controller_workflow_id}` ([deployment/handlers.py:666](../../../../server/services/deployment/handlers.py#L666)).
- **Trigger spec** (the `register_trigger` Signal): `{listener_id, workflow_type, listener_args, event_type, event_types, trigger_node_id, workflow_id, trigger_kind}` ([deployment/manager.py:971](../../../../server/services/deployment/manager.py#L971)). `listener_id` is `<slug>-<trigger label>`.
- **`listener_args`**: workflow id and slug, trigger node id and label, node type, sanitized `filter_params`, the full `nodes` and `edges`, `parameter_snapshot`, `session_id`, `user_id`, `_temporal_routing_v1`, `graphVersion`, `generation`, `execution_id`, `root_execution_id`, `data_scope_id`, `execution_control_version`, `controller_workflow_id`; for polling also `version` and `seen_ids` ([deployment/manager.py:896](../../../../server/services/deployment/manager.py#L896)). Each trigger embeds the whole graph.

### 4.4 Messages

| Workflow | Signals | Updates | Queries |
|---|---|---|---|
| `WorkflowControlWorkflow` ([workflow_control_workflow.py:109](../../../../server/services/temporal/workflow_control_workflow.py#L109)) | `pause`, `resume` (no-ops in version 1), `reset`, `register_trigger`, `on_event` | `set_control_state` (a dict, or the legacy string), `wait_for_checkpoint`, `register_execution`, `unregister_execution`, `pause_admissions`, `replace_graph` | `status`, `execution_control_status` |
| `MachinaWorkflow` ([temporal/workflow.py:251](../../../../server/services/temporal/workflow.py#L251)) | `pause`, `resume`, `on_event` | `set_control_state`, `wait_for_checkpoint` | `execution_control_status` |
| `AgentWorkflow`, `DelegatedTaskWorkflow` ([agent_workflow.py:571](../../../../server/services/temporal/agent_workflow.py#L571), [agent_workflow.py:836](../../../../server/services/temporal/agent_workflow.py#L836)) | `pause`, `resume` | `set_control_state`, `wait_for_checkpoint` | `execution_control_status` |
| `EmployeeJobWorkflow` ([employee_job_workflow.py:79](../../../../server/services/temporal/employee_job_workflow.py#L79)) | none | `set_control_state`, `wait_for_checkpoint` | `execution_control_status` |
| `CronTriggerWorkflow` ([cron_scheduler/_workflow.py:56](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py#L56)) | `pause`, `resume` | `set_control_state`, `wait_for_checkpoint` | `execution_control_status` |
| `ApprovalWorkflow` ([approval_workflow.py:39](../../../../server/services/temporal/approval_workflow.py#L39)) | `nudge` | none | none |
| `WorkspaceTaskControllerWorkflow` ([workspace_tasks_workflow.py:55](../../../../server/services/temporal/workspace_tasks_workflow.py#L55)) | none | `submit`, `reset` | `describe` |

Payloads:

- `set_control_state` (dict): `state` (`paused` or `running`), `revision`, `generation`, `first_execution_run_id`, `producers_held`; the controller's final release adds `expected_membership_epoch`. The validator rejects a wrong generation, a different execution chain, a bad state or a non-integer revision before the Update is accepted ([temporal/execution_control.py:58](../../../../server/services/temporal/execution_control.py#L58)). A revision not newer than the one applied changes nothing ([temporal/execution_control.py:84](../../../../server/services/temporal/execution_control.py#L84)).
- Answer of both control Updates and of the Query: `{execution_control_version, generation, state, revision, producers_held, active_actions, pending_child_starts, checkpoint}` ([temporal/execution_control.py:168](../../../../server/services/temporal/execution_control.py#L168)).
- `register_execution` / `unregister_execution`: `{controller_workflow_id, generation, workflow_id, first_execution_run_id}`; a closed controller or a stale generation is rejected ([workflow_control_workflow.py:219](../../../../server/services/temporal/workflow_control_workflow.py#L219)).
- Controller `status`: `{state, revision, triggers, queued_events}` plus, in version 1, `participant_state`, `participant_revision`, `producers_held`, `live_roots`, `membership_epoch`, `active_actions`, `pending_child_starts`, `checkpoint` ([workflow_control_workflow.py:416](../../../../server/services/temporal/workflow_control_workflow.py#L416)).

### 4.5 Patch markers in the code

Twenty ids at 25 call sites; no `deprecate_patch` call exists (search of `server/`).

| Marker | Site | Gates |
|---|---|---|
| `controller-durable-queue-v2` | [workflow_control_workflow.py:443](../../../../server/services/temporal/workflow_control_workflow.py#L443) | FIFO queue with a durable prefix; polled items queued before admission |
| `controller-durable-overflow-v1` | [workflow_control_workflow.py:729](../../../../server/services/temporal/workflow_control_workflow.py#L729) | The older spill of events beyond 256 at rollover |
| `controller-event-accumulator-v1` | [workflow_control_workflow.py:624](../../../../server/services/temporal/workflow_control_workflow.py#L624) | Pending-key protection, duplicate-start handling, Signal fences at rollover (version 1 only) |
| `controller-messaging-v1` | [workflow_control_workflow.py:658](../../../../server/services/temporal/workflow_control_workflow.py#L658) | Updates request rollover; Safe Apply cannot reopen a stopped generation |
| `controller-spawn-backoff-v1` (dormant) | [workflow_control_workflow.py:548](../../../../server/services/temporal/workflow_control_workflow.py#L548) | Spawn backoff and the dead letter |
| `machina-conditional-edges-v1` | [temporal/workflow.py:601](../../../../server/services/temporal/workflow.py#L601) | Skipping a node whose edge conditions fail |
| `machina-run-record-v1` | [temporal/workflow.py:350](../../../../server/services/temporal/workflow.py#L350) | The `workflow_runs.record_completion` activity |
| `machina-chat-run-v1` | [temporal/workflow.py:656](../../../../server/services/temporal/workflow.py#L656) | The `chat_run.start` and `chat_run.finish` activities |
| `workspace-node-cancellation-v1` | [temporal/workflow.py:879](../../../../server/services/temporal/workflow.py#L879) | `WAIT_CANCELLATION_COMPLETED` and plugin timeouts for Workspace-task nodes |
| `workflow-node-user-action-error-v1` | [temporal/workflow.py:1111](../../../../server/services/temporal/workflow.py#L1111) | A node failure that keeps its owner-action hint |
| `machina-trigger-listener-node-filter` | [trigger_listener_workflow.py:341](../../../../server/services/temporal/trigger_listener_workflow.py#L341) | The trigger filter activity before a spawn |
| `workspace-task-activity-policy-v1`, `employee-task-manager-runtime-v2`, `agent-node-binding-refresh-v2`, `agent-tool-binding-key-v1`, `employee-no-delegation-repair-v1`, `employee-task-manager-tool-scope-v2`, `agent-user-action-error-v1`, `agent-blocked-tool-turn-save-v1` | [agent_workflow.py:146](../../../../server/services/temporal/agent_workflow.py#L146), [agent_workflow.py:961](../../../../server/services/temporal/agent_workflow.py#L961), [agent_workflow.py:1152](../../../../server/services/temporal/agent_workflow.py#L1152), [agent_workflow.py:1155](../../../../server/services/temporal/agent_workflow.py#L1155), [agent_workflow.py:1544](../../../../server/services/temporal/agent_workflow.py#L1544), [agent_workflow.py:2099](../../../../server/services/temporal/agent_workflow.py#L2099), [agent_workflow.py:2583](../../../../server/services/temporal/agent_workflow.py#L2583), [agent_workflow.py:2888](../../../../server/services/temporal/agent_workflow.py#L2888) | Agent-loop changes; not examined here |
| `pi-paused-receipt-checkpoint-v1` | [pi_workflow.py:333](../../../../server/services/temporal/pi_workflow.py#L333) | Not examined here |

## 5. Rules for new workflow code

Each rule reads "A new workflow-code feature must ...". "Verified" says whether the rule was checked in code or tests for this note; RFC sections were read but RFC bodies are frozen.

### 5.1 Versioning and replay

1. **Gate every change to the commands a shared workflow type emits.** A new or removed activity schedule, child start or marker in one of the 15 registered types goes behind `workflow.patched("<id>")` or a generation flag. A brand-new workflow type needs neither, because no recorded history runs it.
   - Source: RFC-0005 section 24.1 rules 2 and 4; RFC-0006 section 22.5 ([RFC-0005](../../../../RFC-0005-EVENT-OS-ON-TEMPORAL.md), [RFC-0006](../../../../RFC-0006-TEMPORAL-DESIGN-PATTERNS.md)); the comments at [temporal/workflow.py:35](../../../../server/services/temporal/workflow.py#L35) and [trigger_listener_workflow.py:80](../../../../server/services/temporal/trigger_listener_workflow.py#L80); enforced by `test_workflow_command_identities_match_release_or_explicit_patch_entry` ([test_workflow_identity_golden.py:38](../../../../server/tests/temporal/test_workflow_identity_golden.py#L38)).
   - Verified: yes.
2. **Know what replay compares: a command's kind, its activity or child type, and its id.** Changing a timeout, a retry policy, a task queue, a parent close policy or a payload needs no patch. Changing a cancellation type needs one whenever a recorded history can hold a cancel of that activity or child.
   - Source: RFC-0006 section 5 rule 6 and section 6.2 (R1, R2); code comments at [_retry_policies.py:189](../../../../server/services/temporal/_retry_policies.py#L189), [trigger_listener_workflow.py:542](../../../../server/services/temporal/trigger_listener_workflow.py#L542), [agent_workflow.py:111](../../../../server/services/temporal/agent_workflow.py#L111) and [record_fixtures.py:70](../../../../server/tests/temporal/record_fixtures.py#L70); precedent for the cancellation exception at [temporal/workflow.py:879](../../../../server/services/temporal/workflow.py#L879).
   - Verified: the comments and the precedent, yes. The SDK core's comparison itself is NOT VERIFIED; the RFC cites the core sources.
3. **Keep child ids and activity ids deterministic and stable.** The golden file records each command's `id` and `activity_id` expression and the body of every helper whose name ends in `_id`.
   - Source: [workflow_identities.py:72](../../../../server/tests/temporal/workflow_identities.py#L72); id helpers at [temporal/workflow.py:176](../../../../server/services/temporal/workflow.py#L176) and [agent_workflow.py:285](../../../../server/services/temporal/agent_workflow.py#L285). RFC-0006 section 5 rule 10 adds that an id feeding an idempotency key must be unique across the continue-as-new chain.
   - Verified: yes for the golden mechanics.
4. **Call `workflow.patched` on a runtime command path, once per run, after the eligibility test.** Not in `@workflow.init`, a Query or an Update validator. Memoize the answer. Test what depends only on workflow input first, so a run that does not need the feature records no marker.
   - Source: [workflow_control_workflow.py:586](../../../../server/services/temporal/workflow_control_workflow.py#L586), [workflow_control_workflow.py:620](../../../../server/services/temporal/workflow_control_workflow.py#L620), [workflow_control_workflow.py:653](../../../../server/services/temporal/workflow_control_workflow.py#L653), [temporal/workflow.py:342](../../../../server/services/temporal/workflow.py#L342), [temporal/workflow.py:651](../../../../server/services/temporal/workflow.py#L651); [temporal-workflow-control.md](../../../temporal-workflow-control.md), section "Compatibility and rollout".
   - Verified: yes.
5. **Ship a new patch id dormant for one release.** List it in `DORMANT_PATCHES`; that release's runs take the old branch and record no marker; the next release removes it. Every worker that runs workflows passes `patch_activation_callback=activate_patch`. Documented exception: a fix for sends, money or approvals may activate at once.
   - Source: [patches.py:14](../../../../server/services/temporal/patches.py#L14); [worker.py:271](../../../../server/services/temporal/worker.py#L271), [worker.py:888](../../../../server/services/temporal/worker.py#L888), [worker.py:943](../../../../server/services/temporal/worker.py#L943); locked by [test_frozen_routing.py:219](../../../../server/tests/temporal/test_frozen_routing.py#L219) and [test_workflow_identity_golden.py:71](../../../../server/tests/temporal/test_workflow_identity_golden.py#L71); RFC-0006 section 18.9 recommendation 2; RFC-0005 section 24.3.
   - Verified: yes. The exception exists only in the documents; no code encodes it. The 19 other ids predate the protocol and are active.
6. **Never delete a marker in one step.** `workflow.patched`, then `workflow.deprecate_patch` one release after no open history can lack the marker, then removal one release later.
   - Source: RFC-0005 section 24.3; [test_workflow_identity_golden.py:62](../../../../server/tests/temporal/test_workflow_identity_golden.py#L62).
   - Verified: the test, yes. No marker has been deprecated yet.
7. **Use a generation flag when new behaviour should apply to new generations only.** Name it in `GENERATION_FLAGS`, have Start write it into `resource_manifest`, freeze it into workflow input, and branch on the input. Read the flag one release before any release writes it.
   - Source: RFC-0005 section 24.1 rule 2 and section 24.3; RFC-0006 section 22.5; [temporal/workflow.py:181](../../../../server/services/temporal/workflow.py#L181); [coding_agent_runtime.py:60](../../../../server/services/coding_agent_runtime.py#L60); the golden accepts an entry with `flags` ([test_workflow_identity_golden.py:55](../../../../server/tests/temporal/test_workflow_identity_golden.py#L55)).
   - Verified: yes. `approval_wait_version` is the live example of "read before written": workflow code reads it and `begin_generation` does not write it ([deployment/control.py:168](../../../../server/services/deployment/control.py#L168)).
8. **A version key in the workflow's or the Update's own input is also an accepted gate.** Only inputs made by new server code carry it, so older histories keep their commands.
   - Source: [workspace_tasks_workflow.py:100](../../../../server/services/temporal/workspace_tasks_workflow.py#L100) (`history_version`), [temporal/execution_control.py:35](../../../../server/services/temporal/execution_control.py#L35), [agent_workflow.py:1251](../../../../server/services/temporal/agent_workflow.py#L1251).
   - Verified: yes, at these three sites.
9. **Never rename or remove a Signal, Update or Query handler.** Adding one is fine. The legacy `pause` and `resume` Signals stay registered as no-ops for that reason.
   - Source: [test_workflow_identity_golden.py:94](../../../../server/tests/temporal/test_workflow_identity_golden.py#L94); [temporal/workflow.py:251](../../../../server/services/temporal/workflow.py#L251).
   - Verified: yes.
10. **Never rename a plugin activity type or move a released plugin to another task queue.** Activity names are `node.<type>.v<version>`; an open agent run's frozen tool policy names the old queue.
    - Source: [test_workflow_identity_golden.py:150](../../../../server/tests/temporal/test_workflow_identity_golden.py#L150).
    - Verified: yes.
11. **Keep recorded payload keys readable.** Read a new input or result key with `.get()` and a default; never rename or remove a key an open run may hold.
    - Source: [temporal_production_practices.md](../../../temporal_production_practices.md), "Rules worth keeping" rule 2; the pattern at [workflow_control_workflow.py:563](../../../../server/services/temporal/workflow_control_workflow.py#L563).
    - Verified: the pattern, yes; the rule's wording comes from the document.

### 5.2 Lifetime and liveness

12. **Start children with no `execution_timeout` and no `run_timeout`.** Temporal's timeout timers keep running through a cooperative pause, so a cap would terminate a run paused longer than the cap.
    - Source: [trigger_listener_workflow.py:59](../../../../server/services/temporal/trigger_listener_workflow.py#L59), [temporal/workflow.py:154](../../../../server/services/temporal/workflow.py#L154), [temporal/workflow.py:840](../../../../server/services/temporal/workflow.py#L840); `test_listener_child_runs_carry_no_lifetime_caps` ([test_durability_hardening.py:70](../../../../server/tests/temporal/test_durability_hardening.py#L70)).
    - Verified: yes. A search of `server/services` finds no `execution_timeout`, `run_timeout`, `task_timeout` or schedule-to-close argument.
13. **Get liveness from activity heartbeats.** A graph node's activity has a 24 h `start_to_close_timeout` and a 2 min `heartbeat_timeout`; the plugin activity wrapper beats every 30 s while the body runs (when the plugin's `start_to_close_timeout` exceeds its `heartbeat_timeout`).
    - Source: [temporal/workflow.py:173](../../../../server/services/temporal/workflow.py#L173), [temporal/workflow.py:868](../../../../server/services/temporal/workflow.py#L868), [plugin/base.py:1375](../../../../server/services/plugin/base.py#L1375).
    - Verified: yes.
14. **Continue-as-new under history pressure, from the main run method only.** Use `_history_pressure(soft_cap)`; let handlers only request the rollover; wait for `workflow.all_handlers_finished`; put no `await` between the last buffer check and `workflow.continue_as_new`.
    - Source: [workflow_control_workflow.py:648](../../../../server/services/temporal/workflow_control_workflow.py#L648), [workflow_control_workflow.py:705](../../../../server/services/temporal/workflow_control_workflow.py#L705); RFC-0006 section 5 rule 9; RFC-0005 section 24.4 row 1.6 states that from SDK 1.33 a continue-as-new from an Update handler fails the workflow task.
    - Verified: the code, yes; the SDK statement is from the RFC.
15. **Carry bounded, measured state.** Measure the carried input with `workflow.payload_converter()` and fail explicitly above 1,900,000 bytes; move what does not fit into the database.
    - Source: [workflow_control_workflow.py:763](../../../../server/services/temporal/workflow_control_workflow.py#L763), [agent_workflow.py:3212](../../../../server/services/temporal/agent_workflow.py#L3212), [controller_queue.py:92](../../../../server/services/temporal/controller_queue.py#L92).
    - Verified: yes.
16. **Do not roll over while a live child handle exists**, unless the child is `ABANDON` and its identity is carried.
    - Source: [agent_workflow.py:3172](../../../../server/services/temporal/agent_workflow.py#L3172); the controller's children are `ABANDON` and its `live_roots` are carried ([workflow_control_workflow.py:755](../../../../server/services/temporal/workflow_control_workflow.py#L755)). RFC-0006 section 12.5 quotes Temporal's documentation: "A parent cannot directly await a child started in a previous Workflow run".
    - Verified: yes for the code.
17. **Address a long-lived workflow by workflow id, never by a pinned run id.** Where identity matters, compare `first_execution_run_id`.
    - Source: [deployment/handlers.py:688](../../../../server/services/deployment/handlers.py#L688); [deployment/execution_control.py:68](../../../../server/services/deployment/execution_control.py#L68); `test_controller_handles_are_never_run_id_pinned` ([test_durability_hardening.py:743](../../../../server/tests/temporal/test_durability_hardening.py#L743)).
    - Verified: yes. The Visibility fan-outs (legacy Signals, Reset's terminate) do address the listed run id of each hit ([deployment/handlers.py:968](../../../../server/services/deployment/handlers.py#L968)).

### 5.3 Taking part in Stop, Resume and Reset

18. **Take part in cooperative Stop, or be declared a non-participant.** Bind `ExecutionControl` from the input in `@workflow.init`; expose the Updates `set_control_state` and `wait_for_checkpoint` with validators, and the Query `execution_control_status`; wrap each business activity in `action()` and each child start in `child_start()`; do not count child-result waits or permit waits; a root that can outlive its parent calls `register_root()` first and `unregister_root()` on the way out, then waits for `workflow.all_handlers_finished`.
    - Source: [temporal/execution_control.py:108](../../../../server/services/temporal/execution_control.py#L108); the smallest full example is [employee_job_workflow.py:71](../../../../server/services/temporal/employee_job_workflow.py#L71); the walk at [deployment/execution_control.py:109](../../../../server/services/deployment/execution_control.py#L109); `NON_PARTICIPANTS` at [deployment/execution_control.py:25](../../../../server/services/deployment/execution_control.py#L25).
    - Verified: yes.
19. **Never implement Stop by cancelling the running activity or by a heartbeat cursor.** Stop closes admission for the next operation and lets admitted operations finish.
    - Source: [event_framework.md](../../../event_framework.md), section "Activity policy and Stop boundaries"; [temporal-workflow-control.md](../../../temporal-workflow-control.md), section "Acknowledged Stop for new generations".
    - Verified: the Stop path issues no cancel ([deployment/handlers.py:2338](../../../../server/services/deployment/handlers.py#L2338), [deployment/execution_control.py:56](../../../../server/services/deployment/execution_control.py#L56)).
20. **Expect Reset to terminate, and keep durable facts in the database.** Whatever a restart, a Reset or a new Temporal database must recover lives in the application database; Reset closes those rows itself.
    - Source: [deployment/handlers.py:2691](../../../../server/services/deployment/handlers.py#L2691); RFC-0006 section 5 rule 1.
    - Verified: yes for the handler.
21. **Re-raise a workflow bug before any exit cleanup that issues commands.** A plain exception fails the workflow task, not the run; cleanup recorded first would stop the fixed release from replaying. Nothing may configure failure exception types.
    - Source: [_exits.py:16](../../../../server/services/temporal/_exits.py#L16); [temporal/workflow.py:521](../../../../server/services/temporal/workflow.py#L521); [employee_job_workflow.py:128](../../../../server/services/temporal/employee_job_workflow.py#L128); [test_workflow_bug_guard.py:58](../../../../server/tests/temporal/test_workflow_bug_guard.py#L58); RFC-0006 section 18.2.
    - Verified: yes.
22. **Give a detached root the `EventWorkflowId` search attribute.** Reset and Safe Apply find roots by it; attached children are found through `RootWorkflowId` and carry none.
    - Source: [deployment/handlers.py:870](../../../../server/services/deployment/handlers.py#L870); [agent_workflow.py:2030](../../../../server/services/temporal/agent_workflow.py#L2030); [employees/jobs.py:264](../../../../server/services/employees/jobs.py#L264); [employees/safe_apply.py:190](../../../../server/services/employees/safe_apply.py#L190).
    - Verified: yes for those sites. One detached root lacks the attribute: `PiInlineDelegationWorkflow` is started by the client with an id, a task queue, `REJECT_DUPLICATE` and a memo only ([pi_inline_delegation.py:222](../../../../server/services/temporal/pi_inline_delegation.py#L222)), and RFC-0006 section 6.2 recommendation R3 records that "the Pi inline runner lacks it". INFERRED: the Visibility query of section 3.5 does not match it, so Reset and Safe Apply do not find it; Stop does reach it, because it registers as a root ([pi_inline_delegation.py:359](../../../../server/services/temporal/pi_inline_delegation.py#L359)).
23. **Choose and state the child policies.** What the code does today:

    | Parent to child | Parent close | Cancellation type | Id reuse |
    |---|---|---|---|
    | Controller or cron to per-event `MachinaWorkflow` ([trigger_listener_workflow.py:443](../../../../server/services/temporal/trigger_listener_workflow.py#L443), [cron_scheduler/_workflow.py:176](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py#L176)) | `ABANDON` | SDK default | `ALLOW_DUPLICATE_FAILED_ONLY` |
    | `MachinaWorkflow` to agent ([temporal/workflow.py:843](../../../../server/services/temporal/workflow.py#L843)) | SDK default | SDK default | SDK default |
    | `AgentWorkflow` to delegated agent ([agent_workflow.py:1886](../../../../server/services/temporal/agent_workflow.py#L1886)) | SDK default | SDK default | SDK default |
    | `AgentWorkflow` to `DelegatedTaskWorkflow` runner ([agent_workflow.py:2030](../../../../server/services/temporal/agent_workflow.py#L2030)) | `ABANDON`, with `EventWorkflowId` | SDK default | SDK default |
    | `DelegatedTaskWorkflow`, `EmployeeJobWorkflow` to agent ([agent_workflow.py:667](../../../../server/services/temporal/agent_workflow.py#L667), [employee_job_workflow.py:124](../../../../server/services/temporal/employee_job_workflow.py#L124)) | SDK default | SDK default | SDK default |
    | `AgentWorkflow` to `ApprovalWorkflow` ([agent_workflow.py:877](../../../../server/services/temporal/agent_workflow.py#L877)) | `TERMINATE` | `WAIT_CANCELLATION_COMPLETED` | `ALLOW_DUPLICATE` |
    | Workspace controller to `NodeInvocationWorkflow` ([workspace_tasks_workflow.py:114](../../../../server/services/temporal/workspace_tasks_workflow.py#L114)) | `REQUEST_CANCEL` | `WAIT_CANCELLATION_COMPLETED` | `REJECT_DUPLICATE` |
    | `NodeInvocationWorkflow` to agent ([node_invocation.py:37](../../../../server/services/temporal/node_invocation.py#L37)) | `REQUEST_CANCEL` | `WAIT_CANCELLATION_COMPLETED` | SDK default |
    | `PiInlineDelegationWorkflow` to `DelegatedTaskWorkflow` runner ([pi_inline_delegation.py:369](../../../../server/services/temporal/pi_inline_delegation.py#L369)) | `REQUEST_CANCEL` | `WAIT_CANCELLATION_COMPLETED` | SDK default |

    - Source: the sites above; RFC-0006 section 6.2 records the SDK defaults as `TERMINATE`, `WAIT_CANCELLATION_COMPLETED` and `ALLOW_DUPLICATE`, and section 5 rule 5 asks every start to state all three.
    - Verified: the sites, yes. Rule 5 is a target, not the current state (section 8).

### 5.4 Determinism

24. **Keep workflow code to coordination.** No I/O, wall clock, randomness or settings read; use `workflow.now()`, `workflow.uuid4()`, `workflow.sleep` and `workflow.wait_condition`; run everything else in an activity and let history record its result; evaluate a configuration knob on the activity side, or freeze it into the input at start.
    - Source: RFC-0006 section 5 rule 3; [temporal/workflow.py:139](../../../../server/services/temporal/workflow.py#L139), [temporal/workflow.py:162](../../../../server/services/temporal/workflow.py#L162); [activities.py:591](../../../../server/services/temporal/activities.py#L591); [deployment/handlers.py:2849](../../../../server/services/deployment/handlers.py#L2849); [workflow_control_workflow.py:397](../../../../server/services/temporal/workflow_control_workflow.py#L397).
    - Verified: yes. Nothing enforces it: every `@workflow.defn` in [server/services/temporal](../../../../server/services/temporal) and the cron workflow pass `sandboxed=False`, the workers pass no workflow runner ([worker.py:244](../../../../server/services/temporal/worker.py#L244)), and no lint exists (RFC-0006 section 17.18 records that it was dropped; no file named `test_workflow_lint.py` exists).
25. **Import inside workflow functions only what the worker process has already loaded.** A first import during a workflow task can outlast the SDK's 2-second deadlock check.
    - Source: the comment at [test_controller_spawn_backoff_integration.py:27](../../../../server/tests/temporal/test_controller_spawn_backoff_integration.py#L27); the pass-through precedent at [temporal/workflow.py:23](../../../../server/services/temporal/workflow.py#L23).
    - Verified: the comments, yes; the 2-second figure is theirs.
26. **Guard workflow interceptors against replay.**
    - Source: [_interceptors.py:126](../../../../server/services/temporal/_interceptors.py#L126).
    - Verified: yes.

### 5.5 Payloads

27. **Pass references, not bytes, and measure what can grow.** A node result of 2,097,152 bytes or more raises `NodeUserError`; `PayloadsTooLarge` is non-retryable; queue batches stop at 512,000 bytes.
    - Source: [media/limits.py:26](../../../../server/services/media/limits.py#L26); [plugin/base.py:1134](../../../../server/services/plugin/base.py#L1134); [_retry_policies.py:52](../../../../server/services/temporal/_retry_policies.py#L52); RFC-0006 section 5 rule 23.
    - Verified: yes.
28. **Remember that an oversized activity or child input fails the workflow task, not the activity.** No retry policy bounds that; the run stays Running and only the `stuck_runs` item shows it. Only the two continue-as-new guards measure an input before scheduling today.
    - Source: [temporal_production_practices.md](../../../temporal_production_practices.md), item 3 and "Rules worth keeping" rules 3 and 4 (queued for v0.3.3, not built).
    - Verified: NOT VERIFIED beyond the two guards of rule 15.
29. **Put no secret in a Temporal input.** Parameter snapshots are sanitized; an activity reads credentials from the saved row.
    - Source: [deployment/handlers.py:360](../../../../server/services/deployment/handlers.py#L360); [deployment/manager.py:903](../../../../server/services/deployment/manager.py#L903); RFC-0006 section 5 rule 24.
    - Verified: yes.

### 5.6 Retries and side effects

30. **Use a named policy from `_retry_policies.py` at every activity call site.** A test rejects an inline `RetryPolicy(...)` in six listed workflow files; a new workflow module is not covered until it is added to that list.
    - Source: [test_retry_policies.py:206](../../../../server/tests/test_retry_policies.py#L206).
    - Verified: yes. Modules outside the list do build policies inline ([workspace_tasks_workflow.py:106](../../../../server/services/temporal/workspace_tasks_workflow.py#L106), [node_invocation.py:50](../../../../server/services/temporal/node_invocation.py#L50)).
31. **Run a send once.** An approved send uses `SEND_ONCE`; the activity wrapper refuses a later attempt of a call that is not safe to repeat and records the outcome as unknown; a plugin's declared policy is passed explicitly, so Temporal's unlimited default never applies to a tool.
    - Source: [approved_tool_call_workflow.py:34](../../../../server/services/temporal/approved_tool_call_workflow.py#L34); [agent_workflow.py:899](../../../../server/services/temporal/agent_workflow.py#L899); [plugin/base.py:1343](../../../../server/services/plugin/base.py#L1343); [temporal/workflow.py:860](../../../../server/services/temporal/workflow.py#L860).
    - Verified: yes.
32. **Retry a transient failure only where a repeat is safe.** An HTTP 408, 429 or 5xx becomes a retryable, BENIGN `ApplicationError` only for a call that is safe to repeat and is not a send; user and configuration errors are non-retryable.
    - Source: [plugin/base.py:166](../../../../server/services/plugin/base.py#L166), [plugin/base.py:187](../../../../server/services/plugin/base.py#L187); [plugin/scaling.py:59](../../../../server/services/plugin/scaling.py#L59).
    - Verified: yes.
33. **Use an unlimited policy only for idempotent work, and expect the retry alert.** Today: the LLM step, delegation cleanup, bookkeeping, permit waits, reviewed delivery, queue paging.
    - Source: the definitions from [_retry_policies.py:85](../../../../server/services/temporal/_retry_policies.py#L85) to [_retry_policies.py:180](../../../../server/services/temporal/_retry_policies.py#L180); [_interceptors.py:44](../../../../server/services/temporal/_interceptors.py#L44); RFC-0006 section 5 rule 16.
    - Verified: yes.
34. **Give a counter that must count once a single attempt.**
    - Source: `BREAKER_COUNT_RETRY` ([_retry_policies.py:183](../../../../server/services/temporal/_retry_policies.py#L183)).
    - Verified: yes.
35. **Let cancellation reach the activity.** The wrapper re-raises a cancel; on a worker shutdown a call safe to repeat fails retryably, and any other answers that it may have happened.
    - Source: [plugin/base.py:1544](../../../../server/services/plugin/base.py#L1544).
    - Verified: yes.

### 5.7 Messages

36. **Keep handlers small and idempotent.** A Signal handler only buffers or sets a flag; rejections live in Update validators, which write no history; an Update carries an absolute state and a revision, because Update-id dedup covers one run only.
    - Source: [workflow_control_workflow.py:357](../../../../server/services/temporal/workflow_control_workflow.py#L357), [workflow_control_workflow.py:176](../../../../server/services/temporal/workflow_control_workflow.py#L176); [temporal/execution_control.py:84](../../../../server/services/temporal/execution_control.py#L84); RFC-0006 section 5 rules 12 and 15.
    - Verified: yes.
37. **Bound every client-side Update a request waits on, and treat a timeout as pending.** The Update may still be applied.
    - Source: [update_calls.py:22](../../../../server/services/temporal/update_calls.py#L22); [employees/safe_apply.py:16](../../../../server/services/employees/safe_apply.py#L16); [node_invocations.py:14](../../../../server/services/node_invocations.py#L14); RFC-0006 section 5 rule 13.
    - Verified: yes.

### 5.8 Fan-out

38. **Bound every fan-out and decide the fate of the rest.** Existing bounds: 3 concurrent delegations per turn and a depth of 2; 32 active Workspace tasks. In a version 1 graph run a node failure lets running nodes finish and starts nothing new; an uncontrolled run ends at the first failure.
    - Source: [agent_workflow.py:1592](../../../../server/services/temporal/agent_workflow.py#L1592); [workspace_tasks_workflow.py:20](../../../../server/services/temporal/workspace_tasks_workflow.py#L20); [temporal/workflow.py:677](../../../../server/services/temporal/workflow.py#L677), [temporal/workflow.py:932](../../../../server/services/temporal/workflow.py#L932); RFC-0006 section 5 rule 7.
    - Verified: yes.
    - Not bounded today: a graph run starts every ready node, and the controller starts one run per event with no cap (RFC-0006 sections 6.3 and 12.2, "OpenCompany today"; no cap was found in the code read for this note). Every node input carries the full `nodes`, `edges` and `parameter_snapshot` ([temporal/workflow.py:739](../../../../server/services/temporal/workflow.py#L739)), so commands issued in one workflow task multiply that payload; section 6.3 recommendation 2 puts "graph by reference" before any added parallelism.
    - Ceilings the RFC takes from Temporal's documentation: 2,000 pending activities and 2,000 pending children per run (the documentation advises at most 1,000 children per parent), and a 4 MB workflow-task completion, above which the execution is terminated. NOT VERIFIED against the server.
    - Section 6.3 recommendation 5 (concurrent read-only tool calls, not built) states that each concurrent call "must also be admitted by execution control (`begin_action`) so Stop's checkpoint counts it". The RFC says a turn's tool calls run one at a time today, apart from Task Manager preflights and delegated children; that was not re-checked here. None of the markers and flags the RFC names for added parallelism (`agent-delegation-window-v2`, `tool_concurrency_version`, `run_cap_version`, `machina-close-and-drain-v1`) appears in `server`.

### 5.9 Search attributes, tracing and registration

39. **Set only registered search attributes.** The seven are declared in `EVENT_SEARCH_ATTRIBUTES` and registered at connect; a start that sets an unregistered one fails. Children do not inherit them.
    - Source: [search_attributes.py:49](../../../../server/services/temporal/search_attributes.py#L49); [client.py:149](../../../../server/services/temporal/client.py#L149); the comment at [deployment/handlers.py:889](../../../../server/services/deployment/handlers.py#L889).
    - Verified: yes; the inheritance statement is a code comment.
40. **Register `TracingInterceptor` once, on the client.** Workers list only `ObservabilityWorkerInterceptor`.
    - Source: [client.py:115](../../../../server/services/temporal/client.py#L115); [worker.py:255](../../../../server/services/temporal/worker.py#L255); [test_client_readiness.py:79](../../../../server/tests/temporal/test_client_readiness.py#L79).
    - Verified: yes. Only the client side is test-locked.
41. **Register through the shared lists.** A workflow type goes into `framework_workflows()`, or a `SimplePlugin` through `register_temporal_plugin`; a main-queue activity goes into `_main_queue_activities`. Workers and the replay gate read the same lists.
    - Source: [workflow_registry.py:6](../../../../server/services/temporal/workflow_registry.py#L6); [worker.py:648](../../../../server/services/temporal/worker.py#L648); [test_workflow_identity_golden.py:139](../../../../server/tests/temporal/test_workflow_identity_golden.py#L139).
    - Verified: yes.

### 5.10 Rules the RFC states that no check enforces

42. **Arm no timer below one second and none with a non-positive timeout.** RFC-0006 also asks for one live timer per workflow over named absolute deadlines, through a shared helper.
    - Source: RFC-0006 section 5 rule 8; the floors at [workflow_control_workflow.py:497](../../../../server/services/temporal/workflow_control_workflow.py#L497) and [approval_workflow.py:31](../../../../server/services/temporal/approval_workflow.py#L31).
    - Verified: the one-second floors, yes. The shared helper does not exist.
43. **Run a new workflow type in the sandbox, importing only modules without import side effects.**
    - Source: RFC-0006 section 5 rule 3 and section 17.18 recommendation 2.
    - Verified: no existing type does. The docstring of `ApprovalWorkflow`, the newest type, says it is unsandboxed "like every type in this package", because importing the package has side effects ([approval_workflow.py:13](../../../../server/services/temporal/approval_workflow.py#L13)).
44. **Schedule no UI-only activity in a new type.**
    - Source: RFC-0006 section 5 rule 11.
    - Verified: no check exists; existing types do schedule broadcast activities ([trigger_listener_workflow.py:526](../../../../server/services/temporal/trigger_listener_workflow.py#L526)).
45. **Set an explicit priority key on every top-level start.**
    - Source: RFC-0006 section 5 rule 22.
    - Verified: not built; no start in `server/services` passes a priority.

### 5.11 Tests

46. **Replay the previous release's histories and keep its command identities, or add a reviewed entry.** Then add a real-SDK test that records a history on each branch and replays it.
    - Source: RFC-0005 section 24.1 rule 4; RFC-0006 section 5 rule 26; the harness below.
    - Verified: the tests were read, not run.

**The harness.**

- **Replay corpus.** [test_replay_corpus.py](../../../../server/tests/temporal/test_replay_corpus.py) replays, in a child process, every history under `server/tests/temporal/fixtures/<set>/` with `Replayer(workflows=replay_workflows())`. It checks each workflow module's source hash against the set's origin commit, refuses a history with a failed workflow task, and requires a history for every registered type except those a golden entry names under `new_types`. It replays every set it finds ([test_replay_corpus.py:36](../../../../server/tests/temporal/test_replay_corpus.py#L36)). Three sets exist as of 48c34266: 21 scenarios in [fixtures/0.3.1](../../../../server/tests/temporal/fixtures/0.3.1/manifest.json) and 6 cancel-path scenarios in [fixtures/0.3.1-cancel](../../../../server/tests/temporal/fixtures/0.3.1-cancel/manifest.json), both recorded from commit a9ee6675 (release 0.3.1) on temporalio 1.30.0 and CLI 1.9.1; and the same 27 scenario names in [fixtures/0.3.2](../../../../server/tests/temporal/fixtures/0.3.2/manifest.json), recorded from 90fc876c on temporalio 1.34.0 and CLI 1.9.1. While those folders are in the tree, a change has to replay all three. No history in any set names `ApprovalWorkflow`.
- **Golden identities.** [fixtures/workflow_identities.json](../../../../server/tests/temporal/fixtures/workflow_identities.json) holds the release's workflow types, the activity, child-workflow and patch commands with their ids, handler names, plugin activity names and plugin task queues. [workflow_identities.py](../../../../server/tests/temporal/workflow_identities.py) builds the snapshot from the AST. A changed digest needs a `patch_entries` item with `before`, `after`, `reason` and one of `patch` (a live marker), `flags` (names in `GENERATION_FLAGS`) or `new_types`. The file still holds the 0.3.1 baseline (its `origin_commit` is a9ee6675, [fixtures/workflow_identities.json:2](../../../../server/tests/temporal/fixtures/workflow_identities.json#L2)): the v0.3.2 cut did not refresh it, because the refresh drops the reviewed entries and the replay gate would then fail `ApprovalWorkflow`, which has no recorded scenario ([ci_cd.md](../../../ci_cd.md), "Cutting a release").
- **Recorder.** [record_fixtures.py](../../../../server/tests/temporal/record_fixtures.py) drives the production workflow list with fake activities under production names on a throwaway dev server, from a git archive of `--source-commit`, and never rewrites a set. A scenario added after a release goes into a supplementary folder recorded from that release's commit.
- **Release check.** `test_the_current_version_has_its_replay_histories` fails a version with no fixture set ([test_release_pipeline_config.py:464](../../../../cli/tests/test_release_pipeline_config.py#L464)). The set is recorded from the `chore(release)` commit and committed in the commit after it ([ci_cd.md](../../../ci_cd.md), "Cutting a release"). CI sets `TEMPORAL_REQUIRE_NATIVE_REPLAY=1` and `TEMPORAL_TEST_CLI` ([predeploy.yml](../../../../.github/workflows/predeploy.yml)).
- **How the newest patch was tested** (`controller-spawn-backoff-v1`, [test_controller_spawn_backoff_integration.py:146](../../../../server/tests/temporal/test_controller_spawn_backoff_integration.py#L146)): a subprocess, `WorkflowEnvironment.start_time_skipping()`, a worker with `max_cached_workflows=0` so every task replays the whole history; one run with `patch_activation_callback=activate_patch` asserting the old behaviour and no marker in history; one run with a callback returning True asserting the new behaviour and the marker; each history replayed with `Replayer`. Plus a golden entry naming the marker and its `DORMANT_PATCHES` entry.
- **How the newest flag was tested** (`approval_wait_version`): a unit test that the flag rides from the manifest into the payload, the routing dict and the inherited keys ([test_agent_approval_wait_integration.py:279](../../../../server/tests/temporal/test_agent_approval_wait_integration.py#L279)); real-SDK runs with and without the flag, each replayed ([test_approval_gate_stop_integration.py:52](../../../../server/tests/temporal/test_approval_gate_stop_integration.py#L52)); a golden entry with `flags` and `new_types`.
- **Control-plane tests** are listed in [temporal-workflow-control.md](../../../temporal-workflow-control.md), section "Verification and operations"; the durability contract is locked by [test_durability_hardening.py](../../../../server/tests/temporal/test_durability_hardening.py) and [test_worker_restart.py](../../../../server/tests/temporal/test_worker_restart.py).

**Known holes in the harness.**

- The golden inventories only `execute_activity`, `start_activity`, `execute_child_workflow`, `start_child_workflow`, `patched` and `deprecate_patch`, and skips a call with no positional argument ([workflow_identities.py:10](../../../../server/tests/temporal/workflow_identities.py#L10), [workflow_identities.py:89](../../../../server/tests/temporal/workflow_identities.py#L89)). Timers, `continue_as_new` and search-attribute upserts are not inventoried.
- The recorder's fake `agent.prepare_payload` carries no `context_pressure_version` and no compaction threshold ([record_fixtures.py:182](../../../../server/tests/temporal/record_fixtures.py#L182)), so recorded agent histories take the legacy pressure branch.
- Replay proves only the recorded paths: changed arguments, and branches no recorded history takes, pass the gate ([temporal_production_practices.md](../../../temporal_production_practices.md), "Rules worth keeping" rule 1).

## 6. Limits, thresholds, timeouts and retries

| What | Value | Defined at |
|---|---|---|
| Stop or Resume socket answer | 10 s | [deployment/handlers.py:97](../../../../server/services/deployment/handlers.py#L97) |
| Status query and counts | 1 s | [deployment/handlers.py:92](../../../../server/services/deployment/handlers.py#L92) |
| Boot re-arm wait; retry pause | 30 s; 1 s | [deployment/handlers.py:93](../../../../server/services/deployment/handlers.py#L93) |
| Safe Apply, per controller Update | 30 s | [employees/safe_apply.py:18](../../../../server/services/employees/safe_apply.py#L18) |
| Apply claim lease | 2 min, renewed every 30 s | [employees/safe_apply.py:216](../../../../server/services/employees/safe_apply.py#L216) |
| Apply and activation recovery loop | every 10 s | [employees/activation.py:72](../../../../server/services/employees/activation.py#L72) |
| Workspace submit; Workspace reset | 15 s; 240 s | [node_invocations.py:14](../../../../server/services/node_invocations.py#L14) |
| History soft cap (controller, listeners, agent, Workspace controller) | 10,000 events | [workflow_control_workflow.py:30](../../../../server/services/temporal/workflow_control_workflow.py#L30), [agent_workflow.py:350](../../../../server/services/temporal/agent_workflow.py#L350) |
| Legacy listener rollover by count | 16,000 processed events | [trigger_listener_workflow.py:58](../../../../server/services/temporal/trigger_listener_workflow.py#L58) |
| Carried queued events; carried dedup keys | 256; 4,096 | [workflow_control_workflow.py:34](../../../../server/services/temporal/workflow_control_workflow.py#L34) |
| Continue-as-new input cap | 1,900,000 bytes | [workflow_control_workflow.py:41](../../../../server/services/temporal/workflow_control_workflow.py#L41), [agent_workflow.py:384](../../../../server/services/temporal/agent_workflow.py#L384) |
| Legacy agent transcript carry | 1,000,000 bytes | [agent_workflow.py:383](../../../../server/services/temporal/agent_workflow.py#L383) |
| Queue page; one large event | 100 events or 512,000 bytes; 200,000-character chunks | [controller_queue.py:92](../../../../server/services/temporal/controller_queue.py#L92), [workflow_control_workflow.py:787](../../../../server/services/temporal/workflow_control_workflow.py#L787) |
| Poll interval default; floor | 60 s; 30 s | [polling_trigger_workflow.py:56](../../../../server/services/temporal/polling_trigger_workflow.py#L56), [workflow_control_workflow.py:40](../../../../server/services/temporal/workflow_control_workflow.py#L40) |
| Poll activity `start_to_close` | `min(180, max(30, 4 x interval))` s, 3 attempts | [workflow_control_workflow.py:910](../../../../server/services/temporal/workflow_control_workflow.py#L910), [polling_trigger_workflow.py:63](../../../../server/services/temporal/polling_trigger_workflow.py#L63) |
| Poll result cap | 512,000 bytes | [plugin/polling.py:66](../../../../server/services/plugin/polling.py#L66) |
| Spawn retry without the marker; with it | every 5 s at the head; 5 s, 30 s, 2 min, 10 min, then a dead letter on the fifth failure | [workflow_control_workflow.py:48](../../../../server/services/temporal/workflow_control_workflow.py#L48), [workflow_control_workflow.py:548](../../../../server/services/temporal/workflow_control_workflow.py#L548) |
| Queue paging activities | 60 s, unlimited attempts | [workflow_control_workflow.py:453](../../../../server/services/temporal/workflow_control_workflow.py#L453) |
| Trigger filter; status broadcast | 10 s; 5 s; both `QUICK_ACTIVITY_RETRY` | [trigger_listener_workflow.py:87](../../../../server/services/temporal/trigger_listener_workflow.py#L87), [trigger_listener_workflow.py:527](../../../../server/services/temporal/trigger_listener_workflow.py#L527) |
| Root register and unregister | 60 s, `DEFAULT_ACTIVITY_RETRY` | [temporal/execution_control.py:210](../../../../server/services/temporal/execution_control.py#L210) |
| Graph node activity | `start_to_close` 24 h, `heartbeat_timeout` 2 min, the plugin's policy or the default | [temporal/workflow.py:173](../../../../server/services/temporal/workflow.py#L173), [temporal/workflow.py:861](../../../../server/services/temporal/workflow.py#L861) |
| Plugin defaults | 10 min `start_to_close`, 2 min heartbeat, 3 attempts 1 s to 60 s | [plugin/scaling.py:55](../../../../server/services/plugin/scaling.py#L55), [plugin/scaling.py:91](../../../../server/services/plugin/scaling.py#L91) |
| `DEFAULT_ACTIVITY_RETRY` | 3 attempts, 1 s to 30 s | [_retry_policies.py:66](../../../../server/services/temporal/_retry_policies.py#L66) |
| `QUICK_ACTIVITY_RETRY` | 2 attempts, 250 ms to 5 s | [_retry_policies.py:77](../../../../server/services/temporal/_retry_policies.py#L77) |
| `LLM_STEP_RETRY` | unlimited, 5 s doubling to 5 min | [_retry_policies.py:97](../../../../server/services/temporal/_retry_policies.py#L97) |
| `DELEGATION_CLEANUP_RETRY`, `BOOKKEEPING_RETRY`, `PERMIT_WAIT_RETRY`, `QUEUE_PAGING_RETRY` | unlimited, 1 s to 30 s | [_retry_policies.py:117](../../../../server/services/temporal/_retry_policies.py#L117) |
| `REVIEWED_DELIVERY_RETRY` | unlimited, 2 s to 1 min | [_retry_policies.py:159](../../../../server/services/temporal/_retry_policies.py#L159) |
| `BREAKER_COUNT_RETRY`; breaker activity | 1 attempt; 30 s | [_retry_policies.py:186](../../../../server/services/temporal/_retry_policies.py#L186), [temporal/workflow.py:326](../../../../server/services/temporal/workflow.py#L326) |
| Circuit breaker | on; 3 failed runs; 600 s window | [deployment/handlers.py:2754](../../../../server/services/deployment/handlers.py#L2754), [.env.template:199](../../../../.env.template#L199) |
| Crash recovery; missing controller | `pause`; `pause` | [.env.template:187](../../../../.env.template#L187) |
| Startup terminate sweep | off | [.env.template:172](../../../../.env.template#L172) |
| `stuck_retry` limits | 8 (LLM step, compaction), 10 otherwise | [_interceptors.py:46](../../../../server/services/temporal/_interceptors.py#L46) |
| `stuck_runs` | checked every 300 s; set after 5 failed tasks | [lifecycle.py:50](../../../../server/services/temporal/lifecycle.py#L50), [config/temporal_cli.json:9](../../../../server/config/temporal_cli.json#L9) |
| Attention item window | 1 hour | [services/attention.py:37](../../../../server/services/attention.py#L37) |
| Dev-server watchdog | probe every 15 s; restart after 4 unhealthy probes | [lifecycle.py:44](../../../../server/services/temporal/lifecycle.py#L44), [.env.template:179](../../../../.env.template#L179) |
| Connect retry; worker restart backoff | 3 s; 1 s doubling to 30 s | [lifecycle.py:40](../../../../server/services/temporal/lifecycle.py#L40), [worker.py:298](../../../../server/services/temporal/worker.py#L298) |
| Cron Schedule | overlap SKIP, catch-up window 24 h | [schedules.py:226](../../../../server/services/temporal/schedules.py#L226) |
| Delegation | at most 3 concurrent subagents, depth at most 2 | [agent_workflow.py:1592](../../../../server/services/temporal/agent_workflow.py#L1592) |
| Workspace tasks | 32 active; 128 remembered submissions and resets; 256 operations per run | [workspace_tasks_workflow.py:20](../../../../server/services/temporal/workspace_tasks_workflow.py#L20) |
| Chat run | stop grace 45 s; longest run 86,400 s; watchdog every 30 s | [config/chat_defaults.json:3](../../../../server/config/chat_defaults.json#L3) |
| Payload thresholds | warning 524,288 bytes; error 2,097,152 bytes | [media/limits.py:26](../../../../server/services/media/limits.py#L26) |
| Workflow task slots; sticky cache | 10; 50 (local), 500 (cloud), 100 (self-hosted) | [worker.py:252](../../../../server/services/temporal/worker.py#L252), [worker.py:89](../../../../server/services/temporal/worker.py#L89) |
| Visibility expansion batch | 40 roots per query | [deployment/handlers.py:910](../../../../server/services/deployment/handlers.py#L910) |
| Versions | temporalio 1.34.0; CLI 1.9.1; server 1.32.0 | [uv.lock:3347](../../../../server/uv.lock#L3347), [config/temporal_cli.json:3](../../../../server/config/temporal_cli.json#L3) |

Server-side limits the code comments rely on but does not set: termination near 51,200 history events ([workflow_control_workflow.py:18](../../../../server/services/temporal/workflow_control_workflow.py#L18)) and a 2 MiB cap on a continue-as-new argument ([workflow_control_workflow.py:31](../../../../server/services/temporal/workflow_control_workflow.py#L31)). RFC-0006 section 8.3 adds 2,000 Updates and 10,000 Signals per run, a suggestion at 4,096 events or 4 MiB, and section 6.2 a limit of 2,000 pending children per parent. NOT VERIFIED against the server.

## 7. Extension points relevant to a dynamic workflow

Facts only.

**A new generation flag is added and read in these places:**

- its name in `GENERATION_FLAGS` ([temporal/workflow.py:187](../../../../server/services/temporal/workflow.py#L187)); `_frozen_routing_from_input` then keeps any listed flag whose value is 1 ([temporal/workflow.py:209](../../../../server/services/temporal/workflow.py#L209));
- its value in the manifest that `begin_generation` writes ([deployment/control.py:168](../../../../server/services/deployment/control.py#L168));
- a line in `freeze_generation_harness`, which copies flags one by one, not by looping over `GENERATION_FLAGS` ([coding_agent_runtime.py:60](../../../../server/services/coding_agent_runtime.py#L60)). It is called where a trigger is armed and where a top-level run starts ([deployment/manager.py:949](../../../../server/services/deployment/manager.py#L949), [deployment/manager.py:1117](../../../../server/services/deployment/manager.py#L1117), [executor.py:166](../../../../server/services/temporal/executor.py#L166));
- a line where `MachinaWorkflow` copies the flag onto node contexts ([temporal/workflow.py:783](../../../../server/services/temporal/workflow.py#L783));
- an entry in `_INHERITED_SCOPE_KEYS` ([agent_workflow.py:310](../../../../server/services/temporal/agent_workflow.py#L310)) and in `INLINE_SCOPE_KEYS` ([pi_inline_delegation.py:28](../../../../server/services/temporal/pi_inline_delegation.py#L28)) to reach delegated children and tool calls;
- optionally `serialize_control`, which exposes `approval_wait_version` to the client ([deployment/control.py:63](../../../../server/services/deployment/control.py#L63));
- a golden entry with `flags` ([fixtures/workflow_identities.json:942](../../../../server/tests/temporal/fixtures/workflow_identities.json#L942)).

**A new patch id is registered in these places:** the `workflow.patched("<id>")` call itself; `DORMANT_PATCHES` for its first release ([patches.py:14](../../../../server/services/temporal/patches.py#L14)); a golden entry with `patch` and a `reason`. No other registry of patch ids exists.

**A new workflow type is registered in:** `framework_workflows()` ([workflow_registry.py:21](../../../../server/services/temporal/workflow_registry.py#L21)), or a `SimplePlugin` from a plugin folder ([plugin_registry.py:46](../../../../server/services/temporal/plugin_registry.py#L46), as the cron plugin does in [its package file, line 43](../../../../server/nodes/scheduler/cron_scheduler/__init__.py#L43)); a golden entry with `new_types`. Its activities go into `_main_queue_activities` ([worker.py:648](../../../../server/services/temporal/worker.py#L648)) or come from the plugin registry. Recorder scenarios are the entries of `SCENARIOS` and `ROLLOVER_SCENARIOS` ([record_fixtures.py:1188](../../../../server/tests/temporal/record_fixtures.py#L1188)); a type with no scenario keeps the golden file from being refreshed at the next release cut, as `ApprovalWorkflow` did at v0.3.2 ([ci_cd.md](../../../ci_cd.md), "Cutting a release").

**What a child workflow type needs to be stopped, resumed and reset with its parent:**

- **Stop and Resume, version 1.** The walk sends `set_control_state` and then `wait_for_checkpoint` to every Running workflow it reaches through `pending_children`, unless the type is in `NON_PARTICIPANTS` ([deployment/execution_control.py:109](../../../../server/services/deployment/execution_control.py#L109)), and `count_generation_actions` sends each the Query `execution_control_status` ([deployment/execution_control.py:36](../../../../server/services/deployment/execution_control.py#L36)). INFERRED: a reachable type without those handlers makes the Update fail, the transition task raise, and the row stay `pausing`; `ApprovalWorkflow` is listed for exactly that reason ("answers no control Updates", [approval_workflow.py:12](../../../../server/services/temporal/approval_workflow.py#L12)).
- **Control state reaches a child through its input.** A parent passes `_child_control_scope(...)`, which is `ExecutionControl.carry()` without the parent's chain markers ([agent_workflow.py:435](../../../../server/services/temporal/agent_workflow.py#L435)); the child binds it in `@workflow.init` so an early Update finds the state.
- **A child that outlives its parent** (`ABANDON`) is not found through `pending_children` once the parent closes. The existing ones enroll with the controller through `register_root()` and carry `EventWorkflowId` ([agent_workflow.py:609](../../../../server/services/temporal/agent_workflow.py#L609), [agent_workflow.py:2030](../../../../server/services/temporal/agent_workflow.py#L2030)).
- **Stop, version 0.** The handler signals `pause` and `resume` to every Running execution in the generation's tree ([deployment/handlers.py:941](../../../../server/services/deployment/handlers.py#L941)).
- **Reset.** Any Running execution whose root is the controller or a root tagged `EventWorkflowId` is terminated, whatever its type; no handler is needed and none runs ([deployment/handlers.py:888](../../../../server/services/deployment/handlers.py#L888)). Rows the type owns in the database must be closed by the Reset handler or by a node class's `reset_execution_state` ([deployment/runtime_state.py:39](../../../../server/services/deployment/runtime_state.py#L39)).
- **Cancellation.** Three paths cancel instead of terminating: chat Stop's grace and the 24 h chat limit ([chat/ledger.py:737](../../../../server/services/chat/ledger.py#L737)), Safe Apply with `stop_work` ([employees/safe_apply.py:189](../../../../server/services/employees/safe_apply.py#L189)), and a Workspace Reset or cancel ([workspace_tasks_workflow.py:168](../../../../server/services/temporal/workspace_tasks_workflow.py#L168)). RFC-0006 section 6.2 describes what follows: a cancel reaches only the handle the workflow is awaiting; an agent child started with SDK defaults is terminated when its parent closes; and a plain agent turns a cancelled tool call into a tool error and keeps working (fixture `task_runner_cancel`). NOT VERIFIED here beyond the start options in rule 23.

**What the control walk costs per workflow in the tree:**

- Stop sends each reachable workflow two Updates (admission, checkpoint) and Resume two (admission, release), each with Describe calls around them. The admission walk visits workflows one after another; the checkpoint Updates run concurrently ([deployment/execution_control.py:109](../../../../server/services/deployment/execution_control.py#L109), [deployment/execution_control.py:170](../../../../server/services/deployment/execution_control.py#L170)).
- Each independently living root costs one activity and one controller Update to register and the same to unregister ([temporal/execution_control.py:206](../../../../server/services/temporal/execution_control.py#L206)). With `controller-messaging-v1` the controller counts that Update traffic toward its rollover ([workflow_control_workflow.py:663](../../../../server/services/temporal/workflow_control_workflow.py#L663)).

**Admission accounting available to workflow code:** `action()` for a business activity through the consumption of its result, `child_start()` for a child start through its acknowledgement, `begin_bookkeeping()` for consuming a child's result while admission is closed ([temporal/execution_control.py:130](../../../../server/services/temporal/execution_control.py#L130)). `PiDescendantExecutionControl` shows a variant that tracks children itself and forwards control through an activity ([pi_control.py:9](../../../../server/services/temporal/pi_control.py#L9)).

**A long-running external process inside one workflow** has one precedent: `PiCodingAgentWorkflow.wait_for_checkpoint` stops its driver before it acknowledges the checkpoint ([pi_workflow.py:50](../../../../server/services/temporal/pi_workflow.py#L50)).

**A tool call that starts a detached durable workflow from outside workflow code** has one precedent, `start_inline_pi_delegation` ([pi_inline_delegation.py:123](../../../../server/services/temporal/pi_inline_delegation.py#L123)), reached from the tool dispatcher when the delegation target is a Coding Agent with `pi_harness_version` 1 ([services/handlers/tools.py:388](../../../../server/services/handlers/tools.py#L388)):

- It checks the target's edge in the admitted graph, the delegation depth and the caller's runtime access, then derives the runner id `coding-inline-<digest>` from the root execution, the parent node, the target node and the tool call id (plus the protocol version and a precreated task id, if any).
- It writes an immutable admission row and the first polling receipt in one transaction, and refuses a retry of the same call with different content ([pi_inline_delegation.py:79](../../../../server/services/temporal/pi_inline_delegation.py#L79)).
- It starts `PiInlineDelegationWorkflow` through the client with `REJECT_DUPLICATE`, treats `WorkflowAlreadyStartedError` as the recorded operation, and returns at once with a task id; the caller polls for the result.
- The workflow registers as a root, verifies the admission in an activity, and only then starts `DelegatedTaskWorkflow` as its child; it exposes the three control handlers ([pi_inline_delegation.py:320](../../../../server/services/temporal/pi_inline_delegation.py#L320)). A request above 1,900,000 bytes is refused ([pi_inline_delegation.py:75](../../../../server/services/temporal/pi_inline_delegation.py#L75)).
- RFC-0006 section 6.2 recommendation R3 proposes the same shape for every agent type as a new workflow type started with `EventWorkflowId`, with "Versioning: none (a new workflow type; old runs never meet it)". Not built: the only inline type in `server` is the Pi one.

**Positions already on record about a program or a foreign loop inside workflow code:**

- `MachinaWorkflow` is itself an interpreter: it walks the graph frozen at Start and schedules `node.<type>.v<version>` activities by name ([temporal/workflow.py:972](../../../../server/services/temporal/workflow.py#L972)). RFC-0006 section 18.17 compares it with Temporal's `dsl` sample (a workflow that interprets a declarative tree and runs parallel branches with `asyncio.gather`) and records the verdict "Adopted correctly (the interpreter)". Its one recommendation is to freeze dispatch (activity names, queues, timeouts, policies) into run input.
- RFC-0006 section 16.14 gives the verdict "Not applicable" to plugins that run a framework's agent loop inside the workflow (OpenAI Agents SDK, Strands, AI SDK by Vercel): a plugin "would put a foreign, mostly preview loop into workflow code that runs unsandboxed and force a migration of every recorded AgentWorkflow history". Section 16.15 gives the same verdict for the LangGraph, Google ADK and Deep Agents samples, and notes that they keep the conversation in workflow state while OpenCompany keeps it in the application database.
- RFC-0006 section 12.5 rejects a MapReduce tree of child workflows ("do not adopt (never)") and points a future bulk job to the Batch Iterator or an admission-bounded window.
- The owner decision of 2026-11-11 quoted in the summary ([temporal_production_practices.md](../../../temporal_production_practices.md)).

## 8. Where the docs and RFCs disagree with the code

1. **Apply is not a restart.** [CLAUDE.md](../../../../CLAUDE.md) (the Normal Mode row) says Turn on Talk and Apply are "each a restart on the saved graph through `services/deployment/restart.py`". The code calls Safe Apply ([employees/handlers.py:219](../../../../server/services/employees/handlers.py#L219)), and `restart_with_latest_graph` has no production caller. [normal_mode.md](../../../normal_mode.md) says Safe Apply in its text.
2. **RFC-0006 section 5 rule 5** ("every child start states its policies"; agent children use `REQUEST_CANCEL`) is not the current state. Four agent-child start sites pass only arguments and an id (rule 23 above). The RFC itself records that the lint was dropped and the change moved to a later lane.
3. **RFC-0006 section 5 rule 9** names a rollover "at 1,800 Updates". The code counts no Updates; it follows the server's suggestion and the 10,000-event cap. Section 22.5 of the same RFC notes that the extra marker is not needed.
4. **RFC-0006 section 5 rule 8** (one timer over named deadlines through a shared helper) has no code: no such helper exists, and the controller and `ApprovalWorkflow` call `workflow.wait_condition` with a timeout directly.
5. **RFC-0006 section 5 rules 14 and 22** (owner actions as intent rows; a priority key at every start, with an AST test) have no code. Stop and Resume run their transitions from the handler, and no start passes a priority.
6. **RFC-0006 section 22.5** lists twelve generation flags and says `begin_generation` writes a flag into the manifest. The code has two flags, and Start writes only `pi_harness_version` (with `execution_control_version`).
7. **The list of live markers** in [TEMPORAL_ARCHITECTURE.md](../../../TEMPORAL_ARCHITECTURE.md) (the months-long durability contract) names nine ids. The code has twenty (section 4.5). The document says the list grows and points to a search; RFC-0006 section 18.9 has the right count.
8. **Stale docstrings.** The cron workflow names a function `register_temporal_workflow` that does not exist; registration is `register_temporal_plugin` ([cron_scheduler/_workflow.py:6](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py#L6)). The listener module says only `webhookTrigger` uses it ([trigger_listener_workflow.py:23](../../../../server/services/temporal/trigger_listener_workflow.py#L23)), while fifteen trigger types register and a controlled generation runs no listener at all. The durability test's header says the lifetime-cap removal was "replay-patched" ([test_durability_hardening.py:7](../../../../server/tests/temporal/test_durability_hardening.py#L7)); the code and the documents say no marker was kept. The breaker's docstring names the activity with a `.v1` suffix it does not have ([deployment/handlers.py:2837](../../../../server/services/deployment/handlers.py#L2837), [activities.py:625](../../../../server/services/temporal/activities.py#L625)).
9. **Type counts.** [TEMPORAL_ARCHITECTURE.md](../../../TEMPORAL_ARCHITECTURE.md) says the recorder drives "all 14 production workflow types"; fifteen are registered. `ApprovalWorkflow` was added after the 0.3.1 recording and has no recorded history in any set, the 0.3.2 set included; the golden excuses it through `new_types`.
10. **"Frozen at Start" is not uniform.** The documents describe the routing snapshot as frozen at Start. For cron and for top-level runs the three routing booleans are captured again later (section 4.2, INFERRED).

## 9. Not verified / open questions

- **SDK behaviour taken from documents, not from temporalio's source:** what replay compares; that `workflow.patched` is memoized per run; that the activation callback is asked once per id per run outside replay; the default child policies; the server's limits and suggestion thresholds.
- **Whether an `ABANDON` child stays in its parent's `pending_children`** while the parent runs. RFC-0006 section 6.2 leaves it open too.
- **What happens when Stop reaches a workflow without the control handlers.** Section 7 marks it INFERRED; no test for it was found or run.
- **Safe Apply and the snapshot.** Safe Apply builds the new `graph_snapshot` from the saved workflow's data and sets no `workflow_slug` ([employees/safe_apply.py:122](../../../../server/services/employees/safe_apply.py#L122), [employees/safe_apply.py:294](../../../../server/services/employees/safe_apply.py#L294)). [temporal_production_practices.md](../../../temporal_production_practices.md) item 1 lists the effect (two listeners after a rename, an Apply and a restart) as queued for v0.3.3. Whether the saved data carries `graphVersion` and `owner_id`, which the re-arm also reads from the snapshot, was not checked.
- **How an in-flight activity learns that its workflow was terminated.** The practices note says a cancel arrives on a heartbeat response, up to 48 s late for the LLM step; not checked.
- **Files named but not opened:** `services/browser_reset.py`, `services/approvals/waiting.py`, `services/workflow_storage/deletion.py`, `services/execution/conditions.py`, and most of `pi_workflow.py`. What they do is described from call sites and documents only.
- **RFC coverage.** Of RFC-0006, sections 5, 6.2, 6.3, 8.3, 12.2, 12.4, 12.5, 16.14, 16.15, 17.18, 18.2, 18.9, 18.17, 22.5 and 24 were read; of RFC-0005, section 24. The RFC's own code line references predate the current files, so an RFC statement counts as verified here only where a current code line is cited beside it.
- **Which trigger types still use the in-process collector path.** Fifteen node types register with the canary registry; the rest were not enumerated.
- **Test status.** No test was run. The 0.3.2 fixture set was committed (48c34266) while this note was being finished: its manifest and the commit message were read, and its history files were only searched for the name `ApprovalWorkflow`. The commit message gives 67 histories; that count was not checked.
- **The exception to the dormant-patch rule** (sends, money, approvals) has no code or test; who decides that a patch qualifies is not written down.
- **The owner decision of 2026-11-11** against a Code Mode feature is quoted from [temporal_production_practices.md](../../../temporal_production_practices.md); whether it covers a dynamic-workflow tool is for the owner.
