# Graph execution on Temporal: code map

Scope: how a workflow graph (canvas nodes and edges) runs on Temporal: `MachinaWorkflow`, the per-node activities, the executor, the worker pool and the versioning machinery.
Written 2026-11-12, as of commit 908a8ce6, from reading the code. Nothing was run.
The agent loop, delegation and teams, deployment control, code sandboxes and the plugin system belong to sibling files; hand-off points are named where they occur. Link text is abbreviated (temporal/workflow.py:216); every link target is the full repository path. Marks: INFERRED = derived, not read directly; NOT VERIFIED = could not be checked.

## 1. Summary

- `MachinaWorkflow` is a DAG orchestrator: it drops config nodes, then runs a first-completed loop that starts every ready node as an activity or a child workflow and collects `{success, result}` envelopes. No business logic and no user-written code runs in workflow code ([temporal/workflow.py:216](../../../../server/services/temporal/workflow.py#L216), [536](../../../../server/services/temporal/workflow.py#L536)).
- Every workflow class in the repository is declared `sandboxed=False`, and no Worker sets a workflow runner, sandbox restrictions or passthrough modules. Determinism rests on convention, a replay corpus and a golden identity test, not on the SDK sandbox (section 5).
- Command identity is per canvas node: `activity_id = node_id`, child id `<root workflow id>-agent-<node_id>`, activity type `node.{type}.v{version}` read from the live plugin registry while the workflow runs. One node maps to one command per run.
- Agents run as child workflows (`AgentWorkflow`, `PiCodingAgentWorkflow`) whose single input carries the whole graph, the parameter snapshot and the direct upstream results. Results accumulate in workflow state and in the return value, each payload is bound by Temporal's 2 MiB limit, and `MachinaWorkflow` never continues-as-new.
- Behaviour that differs per generation is selected from the workflow input (`_temporal_routing_v1`, `GENERATION_FLAGS`, `execution_control_version`), never from live settings. A new `workflow.patched` id ships dormant through `DORMANT_PATCHES`.

## 2. Files and responsibilities

| File | Lines (approx.) | Owns |
|---|---|---|
| [server/services/temporal/workflow.py](../../../../server/services/temporal/workflow.py) | 1,290 | `MachinaWorkflow`: input parsing, config filtering, dependency maps, the scheduling loop, dispatch resolution, control handlers, patch ids, `GENERATION_FLAGS` |
| [server/services/temporal/executor.py](../../../../server/services/temporal/executor.py) | 260 | `TemporalExecutor.execute_workflow` (direct run start), `capture_temporal_routing_input`, `capture_browser_routing_input` |
| [server/services/temporal/activities.py](../../../../server/services/temporal/activities.py) | 690 | Legacy `execute_node_activity` (WebSocket hop), `store_node_output_activity`, trigger and bookkeeping activities |
| [server/services/plugin/base.py](../../../../server/services/plugin/base.py) | 1,600+ | `BaseNode.as_activity` (per-type activity body), planned retry, result-size guard, class-level Temporal knobs |
| [server/services/plugin/scaling.py](../../../../server/services/plugin/scaling.py) | 100 | `TaskQueue` names, plugin `RetryPolicy`, default timeouts |
| [server/services/temporal/plugin_activities.py](../../../../server/services/temporal/plugin_activities.py) | 145 | Collecting `node.*` and `poll.*` activities, `distinct_task_queues` |
| [server/services/temporal/plugin_registry.py](../../../../server/services/temporal/plugin_registry.py) | 80 | Registry of Temporal `SimplePlugin` objects (plugin-owned workflows) |
| [server/services/temporal/workflow_registry.py](../../../../server/services/temporal/workflow_registry.py) | 40 | `framework_workflows()` (the 14 framework workflow classes), `replay_workflows()` |
| [server/services/temporal/node_invocation.py](../../../../server/services/temporal/node_invocation.py) | 125 | `NodeInvocationWorkflow`: one node as its own workflow (direct Workspace tasks) |
| [server/services/temporal/worker.py](../../../../server/services/temporal/worker.py) | 950 | `TemporalWorkerManager`, `TemporalWorkerPool`, Browser owner and Pi driver pools, standalone worker, activity lists |
| [server/services/temporal/client.py](../../../../server/services/temporal/client.py) | 255 | Client connect, readiness gate, search-attribute registration, debug terminate sweep |
| [server/services/temporal/patches.py](../../../../server/services/temporal/patches.py) | 25 | `DORMANT_PATCHES`, `activate_patch` |
| [server/services/temporal/_retry_policies.py](../../../../server/services/temporal/_retry_policies.py) | 230 | Every shared `RetryPolicy` constant, `frozen_retry_policy` |
| [server/services/temporal/_interceptors.py](../../../../server/services/temporal/_interceptors.py) | 150 | `ObservabilityWorkerInterceptor` (retry logs, stuck-retry attention items, workflow start log) |
| [server/services/temporal/search_attributes.py](../../../../server/services/temporal/search_attributes.py) | 190 | The seven custom search attributes and their registration |
| [server/services/temporal/ws_client.py](../../../../server/services/temporal/ws_client.py) | 270 | `internal_ws_url()`; a WebSocket pool with no production caller (section 8) |
| [server/services/temporal/execution_control.py](../../../../server/services/temporal/execution_control.py) | 230 | `ExecutionControl` admission and checkpoint state held in workflow state (hand-off: deployment control) |
| [server/services/temporal/trigger_listener_workflow.py](../../../../server/services/temporal/trigger_listener_workflow.py) | 645 | Builds the child input for trigger firings, `_build_run_graph` (hand-off: deployment control) |
| [server/services/workflow.py](../../../../server/services/workflow.py) | 1,155 | `WorkflowService`: routing a run to Temporal or in-process, `execute_node`, the node output store |
| [server/services/node_executor.py](../../../../server/services/node_executor.py) | 525 | `NodeExecutor`: parameter preparation, handler dispatch, output persistence |
| [server/services/parameter_resolver.py](../../../../server/services/parameter_resolver.py) | 270 | `{{node.field}}` template resolution |
| [server/services/execution/conditions.py](../../../../server/services/execution/conditions.py) | 440 | `evaluate_condition`, called from workflow code |
| [server/services/run_graph.py](../../../../server/services/run_graph.py) | 60 | `collect_downstream_node_ids` (which nodes a firing runs) |
| [server/services/coding_agent_runtime.py](../../../../server/services/coding_agent_runtime.py) | 100 | `freeze_generation_harness`, `coding_agent_ancestors`, `agent_workflow_name` |
| [server/services/media/limits.py](../../../../server/services/media/limits.py) | 55 | Payload size constants |

## 3. How it executes

### 3.1 Who starts a graph run

1. **Direct run (editor Run, generation 0).** The WebSocket handler passes the client's full `nodes` and `edges` to `WorkflowService.execute_workflow` ([websocket.py:905](../../../../server/routers/websocket.py#L905), [968](../../../../server/routers/websocket.py#L968)). The facade requires a start node, then picks a path: Temporal when `use_temporal` (default `settings.temporal_enabled`) and an executor is wired, else the Redis parallel engine when `redis_enabled`, else sequential ([services/workflow.py:536-558](../../../../server/services/workflow.py#L536), [570](../../../../server/services/workflow.py#L570), [609](../../../../server/services/workflow.py#L609)). A graph with an enabled `coding_agent` refuses to run without Temporal ([523](../../../../server/services/workflow.py#L523)).
2. **Client start.** `TemporalExecutor.execute_workflow` builds the input and calls `client.execute_workflow(MachinaWorkflow.run, payload, id="<slug>-<uuid8>", task_queue=<main queue>)` with the search attribute `EventWorkflowId`, and awaits the result ([executor.py:126-127](../../../../server/services/temporal/executor.py#L126), [140-150](../../../../server/services/temporal/executor.py#L140), [181-194](../../../../server/services/temporal/executor.py#L181)). Primitives: workflow start, search attribute.
3. **Trigger firing (deployed generation).** A listener, the per-generation controller or the cron workflow starts `MachinaWorkflow` as a **child workflow** with id `<slug>-<trigger_label>-<event id or firing time>`, `ParentClosePolicy.ABANDON` and `WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY` ([trigger_listener_workflow.py:418-419](../../../../server/services/temporal/trigger_listener_workflow.py#L418), [443-451](../../../../server/services/temporal/trigger_listener_workflow.py#L443), [497-501](../../../../server/services/temporal/trigger_listener_workflow.py#L497); [polling_trigger_workflow.py:361-411](../../../../server/services/temporal/polling_trigger_workflow.py#L361); [cron_scheduler/_workflow.py:167](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py#L167), [203](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py#L203)). The controller reuses the listener's method ([workflow_control_workflow.py:853-876](../../../../server/services/temporal/workflow_control_workflow.py#L853)). `_build_run_graph` keeps the firing trigger, its downstream nodes and their config dependencies, marks the firing trigger `_pre_executed` with its output and sibling triggers `_pre_executed` with `{not_triggered: true}` ([trigger_listener_workflow.py:607-641](../../../../server/services/temporal/trigger_listener_workflow.py#L607), [run_graph.py:13-61](../../../../server/services/run_graph.py#L13)). Hand-off: deployment control.

### 3.2 `MachinaWorkflow.run`

4. `@workflow.init` creates per-run state and binds `ExecutionControl` from the input ([temporal/workflow.py:236-249](../../../../server/services/temporal/workflow.py#L236)).
5. `run` calls `_run_graph` directly when `execution_control_version` is not 1. Otherwise `register_root()` runs the **activity** `execution_control.register` (60 s, `DEFAULT_ACTIVITY_RETRY`), which sends the **Update** `register_execution` to the controller workflow; the returned state is applied before any business work. A `finally` block exits open admissions, runs the **activity** `execution_control.unregister` and waits for `workflow.all_handlers_finished`, unless the exception is a workflow bug ([temporal/workflow.py:514-534](../../../../server/services/temporal/workflow.py#L514), [temporal/execution_control.py:206-229](../../../../server/services/temporal/execution_control.py#L206), [execution_control_activities.py:17-27](../../../../server/services/temporal/execution_control_activities.py#L17), [_exits.py:16-27](../../../../server/services/temporal/_exits.py#L16)).
6. `_run_graph` reads the input, freezes routing with `_frozen_routing_from_input`, and when `pi_harness_version == 1` computes `pi_delegators`: agents that have a `coding_agent` attached through `input-tools` or `input-teammates`, transitively ([temporal/workflow.py:551-579](../../../../server/services/temporal/workflow.py#L551), [coding_agent_runtime.py:73-85](../../../../server/services/coding_agent_runtime.py#L73)). An empty `nodes` list returns `{success: false, error: "No nodes provided"}` ([583](../../../../server/services/temporal/workflow.py#L583)).
7. **Config filtering (workflow code).** `_filter_executable_graph` removes every node that is the source of a config edge (target handle in `CONFIG_HANDLES`, except `input-task` coming from a `taskTrigger`), every Android service node that is the source of any edge, and every `masterSkill` node that is the source of any edge, plus all edges touching them ([60-83](../../../../server/services/temporal/workflow.py#L60), [1162-1204](../../../../server/services/temporal/workflow.py#L1162)). This is the sub-node exclusion: tools, skills, models, Context and teammates wired to an agent never become commands here. The agent child finds them in the `nodes` and `edges` it receives (hand-off: agent loop).
8. `_build_dependency_maps` sets `deps[target]` to the set of its sources over the remaining edges; `_build_conditional_edge_map` groups incoming edges that carry `data.condition` by target ([1206-1248](../../../../server/services/temporal/workflow.py#L1206)). `workflow.patched("machina-conditional-edges-v1")` is evaluated once ([601](../../../../server/services/temporal/workflow.py#L601)). Primitive: patch marker.
9. **Pre-executed triggers.** Each `_pre_executed` node gets `outputs[id] = {success: true, result: _trigger_output, pre_executed: true}` and counts as completed. For the firing one (its output has no `not_triggered`) the workflow awaits the **activity** `store_node_output_activity` (10 s start-to-close, no retry policy passed) so templates can resolve against it ([610-647](../../../../server/services/temporal/workflow.py#L610), [activities.py:502-546](../../../../server/services/temporal/activities.py#L502)).
10. **Chat run claim.** When the firing trigger's event envelope came from the chat service and carries a `run_id`, and `workflow.patched("machina-chat-run-v1")` is true, the **activity** `chat_run.start` (30 s, `DEFAULT_ACTIVITY_RETRY`) claims the run; the returned `run_scope` rides every node context ([368-414](../../../../server/services/temporal/workflow.py#L368), [654-658](../../../../server/services/temporal/workflow.py#L654), [778](../../../../server/services/temporal/workflow.py#L778)).

### 3.3 The scheduling loop

11. Each pass of `while True` ([668](../../../../server/services/temporal/workflow.py#L668)):
    - **Ready set.** `_find_ready_nodes` returns nodes that are neither completed nor running and whose `deps` are a subset of `completed`, in the order of the input `nodes` list ([1271-1291](../../../../server/services/temporal/workflow.py#L1271)). Nothing caps how many nodes start in one pass.
    - **Triggers that were not pre-executed** complete in workflow code with `{success: true, result: {not_triggered: true}, skipped_trigger: true}` ([703-713](../../../../server/services/temporal/workflow.py#L703)). On a direct run this is how `start` completes; every other trigger on the canvas completes the same way, so its downstream branch becomes ready too (INFERRED from the loop iterating all of `node_map`).
    - **Conditional edges.** When the patch is on and the node has conditional incoming edges, `_conditions_met` evaluates each condition against the source's whole envelope (so fields read `success` or `result.<field>`), OR-any. With no match the node is marked completed and added to the trace with no `outputs` entry; skipping is not transitive ([720-726](../../../../server/services/temporal/workflow.py#L720), [1250-1269](../../../../server/services/temporal/workflow.py#L1250)). Locked by [test_conditional_edges.py](../../../../server/tests/temporal/test_conditional_edges.py).
    - **Context.** The per-node `context` dict is built ([729-794](../../../../server/services/temporal/workflow.py#L729); fields in section 4).
    - **Dispatch** is resolved (section 3.4), admission is re-checked, then the command is issued ([800-901](../../../../server/services/temporal/workflow.py#L800)). A child start is awaited until the server acknowledges it; an activity start is not awaited. Both handles go into `running`.
    - **Exit.** The loop ends when nothing is running and the pass auto-completed nothing ([907-910](../../../../server/services/temporal/workflow.py#L907)).
12. **Wait.** `_wait_any_complete` scans `running` in insertion order for a finished handle; otherwise it awaits `workflow.wait_condition(lambda: any(h.done() ...))` and then that handle. An exception from the handle becomes `_node_failure(e)` ([1128-1160](../../../../server/services/temporal/workflow.py#L1128)). No `asyncio.wait` is used.
13. **Result.** A truthy `success` stores the envelope in `outputs`, marks the node completed and appends it to `execution_trace`. Anything else appends `{node_id, error, hint?, requires_user_action?, retryable?}` to `errors` ([915-938](../../../../server/services/temporal/workflow.py#L915)).
14. **Failure propagation.** An uncontrolled run breaks out of the loop at the first failure, leaving other handles un-awaited. A controlled run (execution control version 1) keeps looping with `ready = []` until the running nodes settle ([677-678](../../../../server/services/temporal/workflow.py#L677), [934-935](../../../../server/services/temporal/workflow.py#L934)). Nodes downstream of a failed or never-completed node are never ready. A dependency cycle leaves its nodes unscheduled and the run returns `success: false` with `errors: None` (INFERRED from the subset test and the final `success` formula at [941](../../../../server/services/temporal/workflow.py#L941)).
15. **Disabled nodes.** The workflow does not look at `disabled`. Both activity bodies return `{success: true, skipped: true, reason: "disabled"}` when `node_data.disabled` is set ([plugin/base.py:1258-1276](../../../../server/services/plugin/base.py#L1258), [activities.py:126-143](../../../../server/services/temporal/activities.py#L126)).

The admission gating at the top of each pass, verbatim ([676-684](../../../../server/services/temporal/workflow.py#L676)):

```python
ready = self._find_ready_nodes(deps, completed, running, node_map)
if self._execution_control.enabled and errors:
    ready = []
if self._execution_control.enabled and self._execution_control.status()["state"] != "running" and running:
    # Results and graph bookkeeping settle even when admission is
    # closed. Waiting for Resume here would deadlock checkpoint.
    ready = []
elif self._execution_control.enabled and self._execution_control.status().get("producers_held") and running:
    ready = []
```

### 3.4 Dispatch: which primitive runs each node

`_resolve_dispatch` and `_resolve_activity` decide, from the frozen routing snapshot and the plugin class registry ([972-1106](../../../../server/services/temporal/workflow.py#L972)). Checks run in the order of the table.

| Node | Condition | Primitive | Name | Task queue |
|---|---|---|---|---|
| `coding_agent` | `pi_harness_version == 1` | child workflow | `PiCodingAgentWorkflow` | none passed (parent's queue) |
| Agent type that is a coding-agent ancestor | `pi_harness_version == 1` and type in `AGENT_WORKFLOW_TYPES` | child workflow | `AgentWorkflow` | parent's queue |
| `browser_agent` | always | child workflow | `AgentWorkflow` | parent's queue |
| Type in `AGENT_WORKFLOW_TYPES` | `agent_workflow_enabled` | child workflow | `AgentWorkflow` | parent's queue |
| `browser` with an owner binding | `browser_routing_version == 1` and a binding for the node | activity | `node.browser.v{version}` | the binding's physical owner queue ([811-815](../../../../server/services/temporal/workflow.py#L811)) |
| Registered plugin class | `per_type_dispatch_enabled` | activity | `node.{cls.type}.v{cls.version}` | `cls.task_queue` when `worker_pool_enabled`, else none (workflow's queue) |
| Anything else (flag off, or type with no plugin class) | fallback | activity | `execute_node_activity` | none (workflow's queue) |
| Trigger type, not pre-executed | | workflow code | auto-complete | |
| Config node (tool, skill, model, Context, teammate) | | none | filtered out | |

- `AGENT_WORKFLOW_TYPES` has 16 members: `aiAgent`, `chatAgent`, twelve specialized agents and the two team leads ([115-136](../../../../server/services/temporal/workflow.py#L115)). `rlm_agent`, `claude_code_agent` and `codex_agent` are outside it and run as one whole-node activity; each declares `TaskQueue.AI_HEAVY` and `RetryPolicy(maximum_attempts=1)` ([rlm_agent:43-44](../../../../server/nodes/agent/rlm_agent/__init__.py#L43), [codex_agent:107-108](../../../../server/nodes/agent/codex_agent/__init__.py#L107), [claude_code_agent:256-260](../../../../server/nodes/agent/claude_code_agent/__init__.py#L256)).
- **Child start options.** Only `args=[context]` and `id` are passed ([843-851](../../../../server/services/temporal/workflow.py#L843)). The id is `<root Temporal workflow id>-agent-<node_id>`, not patch-gated ([176-178](../../../../server/services/temporal/workflow.py#L176)). No timeouts, retry policy, task queue, parent close policy or cancellation type are set, so the SDK defaults apply: `ParentClosePolicy.TERMINATE` and `ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED` (read from the installed temporalio 1.34.0 `workflow` package).
- **Activity start options.** `activity_id=node_id`, `start_to_close_timeout=24 h`, `heartbeat_timeout=2 min`, `retry_policy` = the plugin class's `retry_policy.to_temporal()`, and `task_queue` only when dispatch returned one ([862-886](../../../../server/services/temporal/workflow.py#L862)). `BaseNode` always carries a retry policy, so `DEFAULT_ACTIVITY_RETRY` applies only when no plugin class exists for the type. A class with `workspace_task = True` gets its own timeouts and `WAIT_CANCELLATION_COMPLETED` behind the patch `workspace-node-cancellation-v1` ([879-884](../../../../server/services/temporal/workflow.py#L879)).
- **Child task queue.** No queue is passed, so a child runs on its parent's queue (SDK default). Workflows therefore stay on the main queue, the only one whose workers register workflow classes.
- **Local activities:** none. No `execute_local_activity` or `start_local_activity` call exists anywhere in the server tree.
- **Timers:** none in `MachinaWorkflow`. Its waits are `workflow.wait_condition` without a timeout.
- **Inside workflow code:** config filtering, dependency maps, condition evaluation (`evaluate_condition`), dispatch resolution against the live plugin registry, `coding_agent_ancestors`, and reading the chat run id from the input.
- **Other sites that name a node's activity.** Direct Workspace tasks record `activity`, `task_queue` and `timeout_s` in the `NodeInvocationWorkflow` input ([node_invocations.py:220-222](../../../../server/services/node_invocations.py#L220)); approved sends compute the name in [approvals/execution.py:90](../../../../server/services/approvals/execution.py#L90); agent tool calls use a version frozen into the prepared payload ([agent_workflow.py:2394](../../../../server/services/temporal/agent_workflow.py#L2394), hand-off: agent loop).

### 3.5 The per-type activity body

`BaseNode.as_activity()` returns one `@activity.defn(name="node.{type}.v{version}")` function per plugin class ([plugin/base.py:1204-1227](../../../../server/services/plugin/base.py#L1204)). It runs in the worker process and calls the service layer directly; there is no WebSocket hop on this path. In order:

1. Pre-executed passthrough and disabled skip ([1238-1276](../../../../server/services/plugin/base.py#L1238)).
2. Chat: a tool call of a run the owner stopped answers without running; otherwise a chat step begins ([1288-1314](../../../../server/services/plugin/base.py#L1288)).
3. Approvals: `approval_calls.check` may hold the call for the owner or substitute claimed arguments ([1319-1342](../../../../server/services/plugin/base.py#L1319)).
4. Redo guard: a later attempt of a call that is not safe to repeat answers that it may already have happened ([1346-1365](../../../../server/services/plugin/base.py#L1346)).
5. Broadcast `executing`; start a 30 s background heartbeat task when the class's `start_to_close_timeout` exceeds its `heartbeat_timeout`; heartbeat once ([1368-1395](../../../../server/services/plugin/base.py#L1368)).
6. Copy a fixed list of context keys, plus the class's `context_keys`, into `extras` ([1406-1450](../../../../server/services/plugin/base.py#L1406)). The whole `parameter_snapshot` is not in that list; the node's own captured parameters arrive as `parameters`.
7. `workflow_service.execute_node(...)` with `outputs=context["inputs"]` ([1451-1463](../../../../server/services/plugin/base.py#L1451)). That builds the run context (workspace directory, `get_output_fn`), then `NodeExecutor.execute` checks runtime access, prepares parameters, dispatches to the plugin handler and stores the output ([services/workflow.py:202-318](../../../../server/services/workflow.py#L202), [node_executor.py:130-208](../../../../server/services/node_executor.py#L130)). Hand-off: plugin system.
8. Planned retry, interrupted wait, approval record, status and output broadcasts, final heartbeat, return ([1475-1542](../../../../server/services/plugin/base.py#L1475)).
9. `CancelledError`: reads `activity.cancellation_details()`. A call that is not safe to repeat is recorded as an unknown outcome, and that answer is returned as the result when the cause is a worker shutdown. A repeatable call fails retryably on a worker shutdown. Every other cancellation is re-raised ([1544-1579](../../../../server/services/plugin/base.py#L1544)).

**Planned retry (transient failures).** `_execute_body` converts exceptions to error envelopes. An exception that carries an HTTP 408, 429 or 5xx is also reported into `extras["_transient_failures"]` ([163](../../../../server/services/plugin/base.py#L163), [166-184](../../../../server/services/plugin/base.py#L166), [865-879](../../../../server/services/plugin/base.py#L865)). The wrapper then raises a retryable `ApplicationError` with category `BENIGN`, so Temporal runs the call again under the activity's policy, only when all of these hold ([187-242](../../../../server/services/plugin/base.py#L187)): the policy has an attempt left; the error type is not in its non-retryable list; a provider `retry_after` fits `maximum_interval` and the schedule-to-close budget (it becomes `next_retry_delay`); the chat run is not being stopped; and the call is one that may run again (`approval_calls.retryable_call`: safe to repeat and not a send). Otherwise the error envelope is returned as the result. The legacy activity and the in-process path never retry this way.

### 3.6 The legacy activity and the WebSocket hop

`NodeExecutionActivities.execute_node_activity` is the fallback named in section 3.4 ([activities.py:68-217](../../../../server/services/temporal/activities.py#L68)). It handles pre-executed, disabled and the redo guard itself, posts status to the HTTP route /api/workflow/broadcast-status (5 s timeout), then `_execute_via_websocket` ([219-309](../../../../server/services/temporal/activities.py#L219)):

- opens a WebSocket to the backend's own host and port, route /ws/internal, from the shared aiohttp session, presenting the token derived from `SECRET_KEY` ([28-41](../../../../server/services/temporal/activities.py#L28), [311-322](../../../../server/services/temporal/activities.py#L311));
- sends `{type: "execute_node", request_id, node_id, node_type, parameters, nodes, edges, session_id, workflow_id, execution_id, user_id, outputs: context.inputs}` plus a few forwarded keys;
- reads until a message with the same `request_id` arrives, heartbeating on every other message and every 30 s of silence (`ws.receive()` under a 30 s `asyncio.wait_for`; aiohttp `heartbeat=30`, `receive_timeout=None`).

The backend endpoint checks the token before accepting, serves only `execute_node`, `execute_ai_node`, `chat_run_publish` and `ping`, and routes `execute_node` to `handle_execute_node`, which calls the same `workflow_service.execute_node` ([websocket.py:1626-1713](../../../../server/routers/websocket.py#L1626), [authz/ws_surface.py:69-76](../../../../server/services/authz/ws_surface.py#L69), [websocket.py:445-564](../../../../server/routers/websocket.py#L445)). The activity returns the WebSocket response dict as the node result. Its docstring states that a worker running it must reach the originating FastAPI server ([72-75](../../../../server/services/temporal/activities.py#L72)).

### 3.7 Data flow between nodes

There are two channels.

- **In history.** The workflow keeps each envelope in `outputs` and copies the `result` of every direct dependency into the next node's input as `context["inputs"] = {dep_id: result}` ([959-970](../../../../server/services/temporal/workflow.py#L959)). The activity passes it on as `outputs`, and the resolver reads those nodes from `run_outputs` first ([node_executor.py:255-268](../../../../server/services/node_executor.py#L255), [parameter_resolver.py:50-72](../../../../server/services/parameter_resolver.py#L50)).
- **Out of history.** `NodeExecutor.execute` writes a successful result to the output store under `output_main`, `output_top` and `output_0` ([node_executor.py:161-186](../../../../server/services/node_executor.py#L161)). The store is an in-memory dict keyed `<session_id>_<node_id>` plus the database ([services/workflow.py:944-961](../../../../server/services/workflow.py#L944)). Pre-executed triggers reach it through `store_node_output_activity`; agent children through `agent.store_output` (hand-off: agent loop).

Template resolution (`{{node.field}}`) happens inside the activity, never in workflow code. The resolver gathers outputs of every node in the graph: nodes present in `run_outputs` come from this run, all others from the shared store under `output_0`; the key is the node's label, lower-cased without spaces, else its type ([parameter_resolver.py:74-129](../../../../server/services/parameter_resolver.py#L74), [145-170](../../../../server/services/parameter_resolver.py#L145)). Nine node types (the code executors including `montyExecutor`, `console`, `canvas`, `chatReply` and others) also get `connected_outputs` read from the store by edge ([node_executor.py:359-381](../../../../server/services/node_executor.py#L359)).

What is copied into every activity and child input: the full `nodes` and `edges` lists (for a trigger run the firing trigger's node dict still carries `_trigger_output` with the whole event envelope), the whole `parameter_snapshot` when present, and the direct upstream results ([729-745](../../../../server/services/temporal/workflow.py#L729)). No size check exists in the workflow for these inputs or for the aggregate return value (section 6).

### 3.8 End of a run

16. After the loop: `success = no errors and len(completed) == len(node_map)`; the **activity** `chat_run.finish` when a chat run was claimed; the **activity** `workflow_control.pause_on_failure` (30 s, one attempt) for a failed trigger-spawned run; the **activity** `workflow_runs.record_completion` (30 s, `QUICK_ACTIVITY_RETRY`) behind the patch `machina-run-record-v1` ([941-947](../../../../server/services/temporal/workflow.py#L941), [291-366](../../../../server/services/temporal/workflow.py#L291), [416-440](../../../../server/services/temporal/workflow.py#L416)).
17. The workflow returns `{success, outputs, execution_trace, errors, total_nodes}` ([951-957](../../../../server/services/temporal/workflow.py#L951)). It returns normally when `success` is false; the Temporal execution does not end as Failed.
18. `TemporalExecutor` maps the result to the Run response and adds `error`, `completed_nodes`, `execution_time` ([executor.py:219-234](../../../../server/services/temporal/executor.py#L219)).

### 3.9 Control: pause, stop, cancel

- **Controlled generations (`execution_control_version == 1`).** Handlers on `MachinaWorkflow`: Updates `set_control_state` and `wait_for_checkpoint`, each with a validator, and the Query `execution_control_status` ([264-282](../../../../server/services/temporal/workflow.py#L264)). The client side is `transition_generation`: it sends `set_control_state` (Update id `control:{revision}:admission`) to the controller, every registered root and every pending child, then `wait_for_checkpoint` (`control:{revision}:checkpoint`) on Stop, or a release Update on Resume ([deployment/execution_control.py:56-212](../../../../server/services/deployment/execution_control.py#L56)). Inside the workflow the gates are `_wait_until_resumed()` before each command, an `action()` admission around each node activity until its result is consumed, and `child_start()` around each child start acknowledgement ([284-289](../../../../server/services/temporal/workflow.py#L284), [847-849](../../../../server/services/temporal/workflow.py#L847), [892-895](../../../../server/services/temporal/workflow.py#L892), [temporal/execution_control.py:108-166](../../../../server/services/temporal/execution_control.py#L108)). Stop is cooperative: running activities are not cancelled, new commands are held. The Approval node of an `approval_wait_version` 1 generation takes no admission, so Stop completes while it waits ([891](../../../../server/services/temporal/workflow.py#L891)).
- **Older generations.** Signals `pause` and `resume` flip `_control_paused`; both are ignored when execution control is enabled ([251-262](../../../../server/services/temporal/workflow.py#L251)). They are sent to every visible workflow of the generation by `_signal_generation_workflows` ([deployment/handlers.py:941-997](../../../../server/services/deployment/handlers.py#L941)). The loop waits on `workflow.wait_condition` ([673-674](../../../../server/services/temporal/workflow.py#L673)).
- **Cancellation.** `MachinaWorkflow` has no cancellation handling: no `CancelledError` clause, no shield, no cancellation scope. Two production call sites request cancellation of running graph workflows: the chat watchdog ([chat/watchdog.py:63-82](../../../../server/services/chat/watchdog.py#L63)) and Apply with stop-work ([employees/safe_apply.py:189-194](../../../../server/services/employees/safe_apply.py#L189)). Reset terminates every visible execution of the generation ([deployment/handlers.py:1000-1020](../../../../server/services/deployment/handlers.py#L1000)).
- **Editor Stop on a direct run.** `handle_cancel_execution` resets node statuses in the broadcaster only; it makes no Temporal call ([websocket.py:567-606](../../../../server/routers/websocket.py#L567)).
- **Circuit breaker.** The `workflow_control.pause_on_failure` activity pauses the deployment after repeated failed runs; the policy lives on the activity side ([activities.py:625-657](../../../../server/services/temporal/activities.py#L625)). Hand-off: deployment control.
- **Unused signal.** The `on_event` Signal queues events in `_matched_events` with per-run dedup, but `_run_graph` never reads the queue; `_has_event_matching` and `_pop_matching_event` have no caller ([442-512](../../../../server/services/temporal/workflow.py#L442)).

## 4. Inputs, outputs and state

### 4.1 Workflow input (`workflow_data`, one dict)

| Key | Meaning | Read at |
|---|---|---|
| `nodes` | List of node dicts: `id`, `type`, `data`; trigger runs add `_pre_executed`, `_trigger_output` | [551](../../../../server/services/temporal/workflow.py#L551) |
| `edges` | List of edge dicts: `source`, `target`, `sourceHandle`, `targetHandle` (or `target_handle`), `data.condition` | [552](../../../../server/services/temporal/workflow.py#L552) |
| `session_id` | Output-store namespace, default `default` | [553](../../../../server/services/temporal/workflow.py#L553) |
| `workflow_id`, `workflow_slug`, `tenant_id` | Application identity, display slug, tenant | [554-559](../../../../server/services/temporal/workflow.py#L554) |
| `graphVersion` (or `graph_version`), `generation` | Graph contract version and deployment generation; both default 0 | [560-565](../../../../server/services/temporal/workflow.py#L560) |
| `execution_id` | Per-run id; falls back to the Temporal workflow id | [572](../../../../server/services/temporal/workflow.py#L572) |
| `root_execution_id`, `data_scope_id`, `context_execution_id`, `context_session_id` | Conversation and data scope, forwarded when `graphVersion >= 2` and `generation > 0` | [749-775](../../../../server/services/temporal/workflow.py#L749) |
| `user_id` | Principal the run executes as | [776](../../../../server/services/temporal/workflow.py#L776) |
| `parameter_snapshot` | Map of node id to captured parameters; when a node id is present it replaces `node["data"]` as `node_data` | [732](../../../../server/services/temporal/workflow.py#L732) |
| `_temporal_routing_v1` | `{version: 1, agent_workflow_enabled, per_type_dispatch_enabled, worker_pool_enabled, pi_harness_version?, approval_wait_version?}` | [190-210](../../../../server/services/temporal/workflow.py#L190) |
| `browser_routing_version`, `browser_bindings` | Owner routing for `browser` nodes in distributed mode | [790-794](../../../../server/services/temporal/workflow.py#L790) |
| `pi_local_control_version`, `pi_local_control_state`, `pi_local_control_revision` | Pi control for one-off runs | [785-787](../../../../server/services/temporal/workflow.py#L785) |
| `execution_control_version`, `controller_workflow_id`, `execution_control_state`, `execution_control_revision`, `execution_control_producers_held`, `execution_control_root_run_id`, `execution_control_released_revision` | Cooperative control state | [temporal/execution_control.py:39-56](../../../../server/services/temporal/execution_control.py#L39) |

Builders: `TemporalExecutor.execute_workflow` for direct runs ([executor.py:140-179](../../../../server/services/temporal/executor.py#L140)); `TriggerListenerWorkflow._spawn_child_run` ([trigger_listener_workflow.py:453-496](../../../../server/services/temporal/trigger_listener_workflow.py#L453)), `PollingTriggerWorkflow._spawn_child_run` ([polling_trigger_workflow.py:371-406](../../../../server/services/temporal/polling_trigger_workflow.py#L371)) and `CronTriggerWorkflow._start_graph` ([cron_scheduler/_workflow.py:200-229](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py#L200)) for firings. Those three copy from listener arguments that `DeploymentManager` builds at Start ([deployment/manager.py:896-913](../../../../server/services/deployment/manager.py#L896)).

### 4.2 Node context (the single argument of every node activity and agent child)

`node_id`, `node_type`, `node_data`, `inputs`, `workflow_id`, `workflow_slug`, `tenant_id`, `session_id`, `execution_id`, `nodes`, `edges`, `parameter_snapshot` (when present), `pre_executed`, `trigger_output`, `temporal_worker_pool_enabled`; the control carry keys from `ExecutionControl.carry()` minus the root run id and the released revision; `graphVersion`, `generation`, `root_execution_id`, `data_scope_id`, `context_execution_id`, `context_session_id` for versioned generations; `user_id`; `run_scope`; `pi_harness_version` and the `pi_local_control_*` keys; `approval_wait_version`; `browser_routing_version`, `browser_bindings`, `_browser_owner`, `_browser_task_id` ([729-794](../../../../server/services/temporal/workflow.py#L729), [temporal/execution_control.py:180-192](../../../../server/services/temporal/execution_control.py#L180)).

### 4.3 Node result envelopes

| Source | Shape |
|---|---|
| Per-type activity, success | `{success: true, result, execution_time, timestamp, execution_id, node_id, node_type}` ([plugin/base.py:1176-1181](../../../../server/services/plugin/base.py#L1176), [1465-1468](../../../../server/services/plugin/base.py#L1465)) |
| Per-type activity, failure | `{success: false, error, error_type, ...}` returned, not raised ([1183-1200](../../../../server/services/plugin/base.py#L1183)) |
| Legacy activity | The WebSocket response: `{type, request_id, success, node_id, execution_id, result, error, execution_time, timestamp, node_type}` ([websocket.py:550-558](../../../../server/routers/websocket.py#L550)) |
| Agent child | `{success: true, result: {...}}` or `{success: false, error, ...}` ([agent_workflow.py:3341](../../../../server/services/temporal/agent_workflow.py#L3341)) |
| Pre-executed trigger | `{success: true, result: <trigger output>, pre_executed: true}`, built in the workflow |
| Trigger not pre-executed | `{success: true, result: {not_triggered: true}, skipped_trigger: true}` |
| Disabled node | `{success: true, skipped: true, reason: "disabled", ...}` |
| Raised failure (retries exhausted, child failed) | `{success: false, error: str(e)}`, or with the patch `workflow-node-user-action-error-v1` a `NodeUserError`-typed envelope with `hint` and `requires_user_action` when the cause chain holds an `LLMError.*` or `MissingAgentProviderCredential` failure ([1108-1126](../../../../server/services/temporal/workflow.py#L1108)) |

`MachinaWorkflow` reads only `success`, `result`, `error`, `hint`, `requires_user_action` and `retryable`.

### 4.4 Workflow output

`{success: bool, outputs: {node_id: envelope}, execution_trace: [node_id], errors: [..] or None, total_nodes: int}`. `execution_trace` includes skipped and auto-completed nodes; `total_nodes` is the size of the executable graph.

### 4.5 State

- Workflow-local: `outputs`, `completed`, `running`, `errors`, `execution_trace` (locals of `_run_graph`); `_seen_event_ids`, `_matched_events`, `_control_paused`, `_execution_control`, `_action_contexts` (instance).
- Outside history: the node output store, the status broadcaster's cache, and whatever each plugin persists.

### 4.6 `NodeInvocationWorkflow` input

`{principal, workflow_id, node_id, fingerprint, context, activity, task_queue, timeout_s, history_version?, dispatch_version?, dispatch_kind?}`. It runs exactly one node: an agent child workflow with id `<workflow id>:agent` when `dispatch_version == 1` and `dispatch_kind` is `native_agent` or `pi_agent`, otherwise the named activity once (`maximum_attempts=1`). It exposes a `describe` Query and records status and completion in shielded cleanup ([node_invocation.py:16-124](../../../../server/services/temporal/node_invocation.py#L16)).

## 5. Determinism, versioning and durability

### 5.1 The sandbox is off

- All 14 framework workflow classes and the cron plugin workflow are declared `sandboxed=False` (for example [temporal/workflow.py:216](../../../../server/services/temporal/workflow.py#L216), [node_invocation.py:16](../../../../server/services/temporal/node_invocation.py#L16), [agent_workflow.py:808](../../../../server/services/temporal/agent_workflow.py#L808), [cron_scheduler/_workflow.py:40](../../../../server/nodes/scheduler/cron_scheduler/_workflow.py#L40)).
- No `Worker(...)` call passes `workflow_runner`, `unsandboxed_workflow_runner`, `workflow_failure_exception_types` or `debug_mode` ([worker.py:244-272](../../../../server/services/temporal/worker.py#L244), [874-889](../../../../server/services/temporal/worker.py#L874), [929-944](../../../../server/services/temporal/worker.py#L929)). In temporalio 1.34.0 the defaults are `SandboxedWorkflowRunner()` for sandboxed definitions and `UnsandboxedWorkflowRunner()` for definitions that opt out, so every workflow here runs unsandboxed.
- `SandboxRestrictions` and passthrough-module configuration appear nowhere in the server tree. The single `workflow.unsafe.imports_passed_through()` block wraps the `evaluate_condition` import ([32-33](../../../../server/services/temporal/workflow.py#L32)).
- The stated reason: importing the `services.temporal` package registers WebSocket handlers and pulls logging and DI ([node_invocation.py:13-15](../../../../server/services/temporal/node_invocation.py#L13), [temporal/__init__.py:38-44](../../../../server/services/temporal/__init__.py#L38)).
- Nothing configures failure exception types, so a plain exception in workflow code fails the workflow task and leaves the run open; exit cleanup re-raises such a bug without recording commands ([_exits.py:1-27](../../../../server/services/temporal/_exits.py#L1), locked by [test_workflow_bug_guard.py:58-73](../../../../server/tests/temporal/test_workflow_bug_guard.py#L58)).

### 5.2 What workflow code imports and reads

- Module level: `temporalio.workflow`, `_exits`, `_retry_policies`, `execution_control`, `services.execution.conditions.evaluate_condition`, and two frozensets from `constants` ([14-33](../../../../server/services/temporal/workflow.py#L14), [91-94](../../../../server/services/temporal/workflow.py#L91)).
- Kept inside methods on purpose: `services.node_registry.get_node_class`, `core.config.Settings`, `services.coding_agent_runtime` ([578](../../../../server/services/temporal/workflow.py#L578), [862](../../../../server/services/temporal/workflow.py#L862), [1077-1080](../../../../server/services/temporal/workflow.py#L1077)).
- `Settings()` is read in workflow code only when `_resolve_dispatch` gets no routing snapshot, which `run` never does ([998-1016](../../../../server/services/temporal/workflow.py#L998)).
- The live plugin registry is read in workflow code for `cls.type`, `cls.version`, `cls.task_queue`, `cls.retry_policy`, `cls.workspace_task` and, for Workspace-task nodes, the class timeouts. The activity type name is therefore computed at workflow time, replay included, and one `workflow.patched` call is conditional on a plugin attribute ([879](../../../../server/services/temporal/workflow.py#L879), [1101-1106](../../../../server/services/temporal/workflow.py#L1101)). RFC-0004 defect C2 describes this and proposes freezing node dispatch into the run input. A frozen policy exists for agent tool calls ([_retry_policies.py:197-212](../../../../server/services/temporal/_retry_policies.py#L197)), not for graph nodes.
- `evaluate_condition` logs through the application logger on every call, in workflow code, with no replay guard ([conditions.py:106-113](../../../../server/services/execution/conditions.py#L106)).
- Command order is deterministic: `ready` follows the input list order and `running` is an insertion-ordered dict. The one set iteration (`_get_node_inputs` over `deps`) shapes an activity input, not the command order.

### 5.3 Patch ids

| Id | Where | Gates |
|---|---|---|
| `machina-conditional-edges-v1` | [39](../../../../server/services/temporal/workflow.py#L39), [601](../../../../server/services/temporal/workflow.py#L601) | Skipping a node whose conditional incoming edges all fail |
| `machina-run-record-v1` | [44](../../../../server/services/temporal/workflow.py#L44), [350](../../../../server/services/temporal/workflow.py#L350) | The `workflow_runs.record_completion` activity |
| `machina-chat-run-v1` | [50](../../../../server/services/temporal/workflow.py#L50), [656](../../../../server/services/temporal/workflow.py#L656) | The `chat_run.start` and `chat_run.finish` activities |
| `workspace-node-cancellation-v1` | [879](../../../../server/services/temporal/workflow.py#L879) | Class timeouts and `WAIT_CANCELLATION_COMPLETED` for `workspace_task` nodes |
| `workflow-node-user-action-error-v1` | [1111](../../../../server/services/temporal/workflow.py#L1111) | Mapping a wrapped provider or credential failure to a user-action envelope |
| `machina-trigger-listener-node-filter` | [trigger_listener_workflow.py:84](../../../../server/services/temporal/trigger_listener_workflow.py#L84), [341](../../../../server/services/temporal/trigger_listener_workflow.py#L341) | Running the trigger's own filter before spawning a run |

`node_invocation.py`, `executor.py` and the activity modules call no `workflow.patched`. Other workflow modules carry their own ids (in `agent_workflow.py`, `pi_workflow.py` and `workflow_control_workflow.py`); those belong to the sibling files. No `deprecate_patch` call exists anywhere in the server tree.

### 5.4 Generation flags and other input-borne switches

`GENERATION_FLAGS = ("pi_harness_version", "approval_wait_version")` ([187](../../../../server/services/temporal/workflow.py#L187)). `_frozen_routing_from_input` keeps a flag only when its recorded value is 1 and raises on any other recorded `pi_harness_version` ([190-210](../../../../server/services/temporal/workflow.py#L190)):

```python
return {
    "version": TEMPORAL_ROUTING_INPUT_VERSION,
    "agent_workflow_enabled": raw.get("agent_workflow_enabled") is True,
    "per_type_dispatch_enabled": (
        raw.get("per_type_dispatch_enabled") is True
    ),
    "worker_pool_enabled": raw.get("worker_pool_enabled") is True,
    **{flag: 1 for flag in GENERATION_FLAGS if raw.get(flag) == 1},
}
```

| Switch | Selects in graph execution |
|---|---|
| `pi_harness_version == 1` | `coding_agent` runs as the `PiCodingAgentWorkflow` child; agents above a coding agent run as `AgentWorkflow` children regardless of `agent_workflow_enabled`; the context carries `pi_harness_version` and the `pi_local_control_*` keys ([579](../../../../server/services/temporal/workflow.py#L579), [783-787](../../../../server/services/temporal/workflow.py#L783), [1029-1032](../../../../server/services/temporal/workflow.py#L1029)) |
| `approval_wait_version == 1` | The context carries the flag; the `approvalGate` node's activity is not an admitted action ([788-789](../../../../server/services/temporal/workflow.py#L788), [891](../../../../server/services/temporal/workflow.py#L891)) |
| `agent_workflow_enabled`, `per_type_dispatch_enabled`, `worker_pool_enabled` | Child workflow or activity, per-type or legacy name, plugin queue or the workflow's queue (section 3.4) |
| `execution_control_version == 1` | The admission and checkpoint protocol of section 3.9 |
| `graphVersion >= 2` and `generation > 0` | Conversation and data scope keys on the context ([749-775](../../../../server/services/temporal/workflow.py#L749)) |
| `browser_routing_version == 1` | Owner-queue routing of `browser` nodes |

Where the values come from: Start writes `resource_manifest={"execution_control_version": 1, "pi_harness_version": 1}` on the control row ([deployment/control.py:168](../../../../server/services/deployment/control.py#L168)); `freeze_generation_harness` copies `pi_harness_version` and `approval_wait_version` from that manifest into the payload and its routing block ([coding_agent_runtime.py:45-66](../../../../server/services/coding_agent_runtime.py#L45)); `capture_temporal_routing_input` reads the three dispatch settings once and defaults `pi_harness_version` to 1 ([executor.py:29-48](../../../../server/services/temporal/executor.py#L29)). An input with no snapshot, or an unknown snapshot version, gets `_SAFE_FROZEN_ROUTING`: agent workflow and per-type dispatch on, worker pool off ([148-153](../../../../server/services/temporal/workflow.py#L148), locked by [test_frozen_routing.py](../../../../server/tests/temporal/test_frozen_routing.py)). The golden identity test accepts a command change that names a flag from `GENERATION_FLAGS` ([test_workflow_identity_golden.py:46-58](../../../../server/tests/temporal/test_workflow_identity_golden.py#L46)).

### 5.5 Dormant patches

```python
DORMANT_PATCHES: frozenset[str] = frozenset({"controller-spawn-backoff-v1"})

def activate_patch(inp: PatchActivationInput) -> bool:
    return inp.patch_id not in DORMANT_PATCHES
```

([patches.py:14-21](../../../../server/services/temporal/patches.py#L14), docstring omitted). The three Workers that host workflows pass `patch_activation_callback=activate_patch` (manager, standalone, `create_worker`). The SDK consults the callback at the first non-replay `patched()` call for an id with no marker in history, so a dormant id takes the old branch and records nothing in that release. The golden test asserts every dormant id names a live `patched` marker ([test_workflow_identity_golden.py:78-81](../../../../server/tests/temporal/test_workflow_identity_golden.py#L78)).

### 5.6 Replay gates

- `framework_workflows()` is the one list used by the manager worker, the standalone worker, `create_worker` and `replay_workflows()`; the last adds plugin workflows through each registered `SimplePlugin` ([workflow_registry.py:6-38](../../../../server/services/temporal/workflow_registry.py#L6)).
- The identity inventory scans every module in the Temporal services folder plus the cron workflow file for `execute_activity`, `start_activity`, `execute_child_workflow`, `start_child_workflow`, `patched`, `deprecate_patch`, and for Signal, Update and Query handlers ([workflow_identities.py:10-17](../../../../server/tests/temporal/workflow_identities.py#L10)). Local activities, timers and continue-as-new are not in that set.
- The replay corpus test replays recorded histories of the previous release ([test_replay_corpus.py](../../../../server/tests/temporal/test_replay_corpus.py)); its contents were not opened for this file.

### 5.7 Continue-as-new and lifetime

- `MachinaWorkflow` and `NodeInvocationWorkflow` never call `workflow.continue_as_new` and never read `is_continue_as_new_suggested()`. The shared helper `_history_pressure` lives in [trigger_listener_workflow.py:90-103](../../../../server/services/temporal/trigger_listener_workflow.py#L90) and is used by the listener, polling, agent and controller workflows.
- No workflow execution or run timeout is set on graph runs or on agent children ([154-173](../../../../server/services/temporal/workflow.py#L154), [840-846](../../../../server/services/temporal/workflow.py#L840)). Liveness is the activity heartbeat timeout.
- Search attributes: starters set `EventWorkflowId`; `MachinaWorkflow` upserts none. Seven custom attributes are registered at connect ([search_attributes.py:49-120](../../../../server/services/temporal/search_attributes.py#L49), [client.py:154-161](../../../../server/services/temporal/client.py#L154)).

### 5.8 RFC-0004 items visible in this area

Present in code: SDK floor 1.34.0 ([server/pyproject.toml:199](../../../../server/pyproject.toml#L199)); pinned CLI 1.9.1 and server 1.32.0 with fairness and one task-queue partition ([temporal_cli.json:3-10](../../../../server/config/temporal_cli.json#L3)); the routing snapshot; the replay corpus and golden identities; the `BENIGN` category and `activity.cancellation_details()` in the per-type wrapper; the transient-retry rule; `patch_activation_callback`. Absent: the workflow sandbox, a per-node dispatch freeze for graph nodes, local activities, explicit child close and cancellation policies on agent children started by `MachinaWorkflow`.

## 6. Limits, budgets, timeouts and retries

| Item | Value | Defined at |
|---|---|---|
| Node activity start-to-close (graph) | 24 h | [temporal/workflow.py:173](../../../../server/services/temporal/workflow.py#L173), [875](../../../../server/services/temporal/workflow.py#L875) |
| Node activity heartbeat timeout (graph) | 2 min | [876](../../../../server/services/temporal/workflow.py#L876) |
| Node activity retry (plugin default) | 1 s initial, coefficient 2.0, 60 s cap, 3 attempts; non-retryable `ValidationError`, `PermissionDeniedError`, `InvalidParametersError`, `NodeUserError`, `PayloadsTooLarge` | [plugin/scaling.py:48-87](../../../../server/services/plugin/scaling.py#L48) |
| `DEFAULT_ACTIVITY_RETRY` | 1 s initial, 30 s cap, 3 attempts; non-retryable `NodeUserError`, `InvalidEvent`, `PayloadsTooLarge` | [_retry_policies.py:52-71](../../../../server/services/temporal/_retry_policies.py#L52) |
| `QUICK_ACTIVITY_RETRY` | 250 ms initial, 5 s cap, 2 attempts | [_retry_policies.py:77-82](../../../../server/services/temporal/_retry_policies.py#L77) |
| `BREAKER_COUNT_RETRY` | 1 attempt | [_retry_policies.py:186](../../../../server/services/temporal/_retry_policies.py#L186) |
| Class timeout defaults (ordinary graph dispatch ignores them; Workspace-task nodes use them; the background-heartbeat test reads them) | action 10 min, AI 30 min, trigger 24 h, code 5 min; heartbeat 2 min | [plugin/scaling.py:91-99](../../../../server/services/plugin/scaling.py#L91) |
| `store_node_output_activity` | 10 s start-to-close, no retry policy passed | [639-647](../../../../server/services/temporal/workflow.py#L639) |
| `chat_run.start`, `chat_run.finish` | 30 s, `DEFAULT_ACTIVITY_RETRY` | [398-403](../../../../server/services/temporal/workflow.py#L398), [433-438](../../../../server/services/temporal/workflow.py#L433) |
| `execution_control.register`, `.unregister` | 60 s, `DEFAULT_ACTIVITY_RETRY` | [temporal/execution_control.py:210-229](../../../../server/services/temporal/execution_control.py#L210) |
| Agent child workflow | no timeouts, no retry policy | [843-851](../../../../server/services/temporal/workflow.py#L843) |
| Per-type activity background heartbeat | every 30 s | [plugin/base.py:1383-1391](../../../../server/services/plugin/base.py#L1383) |
| Legacy activity heartbeat | every 30 s of silence and on every unrelated message | [activities.py:288-302](../../../../server/services/temporal/activities.py#L288) |
| Shared aiohttp session | pool 100, total 300 s, connect 10 s | [activities.py:373-396](../../../../server/services/temporal/activities.py#L373), [worker.py:119](../../../../server/services/temporal/worker.py#L119) |
| Node result size | warn at 524,288 B, raise `NodeUserError` at 2,097,152 B | [plugin/base.py:1107-1158](../../../../server/services/plugin/base.py#L1107), [media/limits.py:26-27](../../../../server/services/media/limits.py#L26) |
| Stuck-retry attention limit | attempt 8 for `agent.execute_llm_step` and `agent.compact_context`, 10 otherwise | [_interceptors.py:46-60](../../../../server/services/temporal/_interceptors.py#L46) |
| Main worker | 100 activity slots, 10 concurrent workflow tasks, sticky cache 50 (local), 500 (cloud), 100 (self-hosted); pollers autoscale to 10 (activity) and 20 (workflow) | [worker.py:77-89](../../../../server/services/temporal/worker.py#L77), [244-272](../../../../server/services/temporal/worker.py#L244) |
| Pool worker slots per queue | `machina-default` 20, `rest-api` 50, `ai-heavy` 4, `code-exec` 10, `triggers-poll` 100, `triggers-event` 100, `android` 10, `browser` 4, `messaging` 20; halved in local mode, floor 1; env override per queue | [worker.py:371-381](../../../../server/services/temporal/worker.py#L371), [416-437](../../../../server/services/temporal/worker.py#L416) |
| Pool worker rate limit (activities per second, per worker) | `rest-api` 100, `ai-heavy` 60, `browser` 10, `messaging` 20, others none | [worker.py:388-398](../../../../server/services/temporal/worker.py#L388) |
| Resource-based tuner | `ai-heavy` and `browser`: target 80% CPU and memory, 1 to max(concurrency, 2) slots | [worker.py:462-495](../../../../server/services/temporal/worker.py#L462) |
| Pi driver queue | `coding-pi-driver`: 1 slot local, 2 otherwise | [worker.py:718-759](../../../../server/services/temporal/worker.py#L718) |
| Worker crash restart backoff | 1.0 s doubling to 30.0 s | [core/config.py:295-304](../../../../server/core/config.py#L295), [worker.py:298-323](../../../../server/services/temporal/worker.py#L298) |
| Graceful shutdown | 30 s | [.env.template:174](../../../../.env.template#L174), [worker.py:52-61](../../../../server/services/temporal/worker.py#L52) |
| Shipped flags | Temporal on, per-type dispatch on, agent workflow on, worker pool on, main queue `machina-tasks`, mode `local` | [.env.template:86-136](../../../../.env.template#L86) |

Not set anywhere in graph dispatch: `schedule_to_close_timeout`, `schedule_to_start_timeout`, a cap on concurrently running nodes, a size check on the node context, a size check on the aggregate workflow result. Server-side limits (2 MiB per payload, 2,000 pending activities or children, 51,200 history events, continue-as-new suggested at 4,096 events or 4 MiB) are as listed in RFC-0004 sections 4.2 and 12.1; they were not measured here.

## 7. Extension points relevant to a dynamic workflow

Facts about where things are decided today.

1. **Primitive per node type.** `_resolve_dispatch`, `_resolve_activity` and `AGENT_WORKFLOW_TYPES` in [temporal/workflow.py](../../../../server/services/temporal/workflow.py) (lines 115-136 and 972-1106). A type outside the set and without special handling runs as one activity per node.
2. **What never becomes a command.** `CONFIG_HANDLES`, `_is_config_edge`, `SKILL_NODE_TYPES`, and the trigger and Android sets imported from [server/constants.py](../../../../server/constants.py) (lines 227 and 397).
3. **Command ids.** `activity_id=node_id` and `_agent_child_workflow_id`. Nothing in `MachinaWorkflow` adds an invocation counter, so a node cannot be issued twice in one run under these ids. TEMPORAL_ARCHITECTURE.md gives the agent loop's delegation child id as `{agent_workflow_id}-delegate-{tool_node_id}-{iteration+1}-{call_index+1}`; that code was not read for this file (hand-off: delegation and teams).
4. **Registering a workflow type.** Add the class to `framework_workflows()` ([workflow_registry.py:6-27](../../../../server/services/temporal/workflow_registry.py#L6)), or register a `SimplePlugin` from a plugin package, as the cron scheduler does ([cron_scheduler/__init__.py:43](../../../../server/nodes/scheduler/cron_scheduler/__init__.py#L43), [plugin_registry.py:46-76](../../../../server/services/temporal/plugin_registry.py#L46)). Only the main-queue workers register workflows; pool workers are activity-only ([worker.py:531-547](../../../../server/services/temporal/worker.py#L531)).
5. **Registering an activity.** A plugin class gets `node.{type}.v{version}` automatically through `as_activity` and `collect_plugin_activities` ([plugin_activities.py:37-84](../../../../server/services/temporal/plugin_activities.py#L37)). Framework activities are listed in `_main_queue_activities` ([worker.py:648-693](../../../../server/services/temporal/worker.py#L648)). The main-queue worker registers every plugin activity (all but `browser` in distributed mode); each pool worker registers those whose `cls.task_queue` matches. Activity sets are computed when a Worker is built (start or crash restart). No dynamic activity or dynamic workflow definition is registered.
6. **Task queues.** Names live in `TaskQueue` ([plugin/scaling.py:17-45](../../../../server/services/plugin/scaling.py#L17)); the pool discovers queues from plugin classes ([plugin_activities.py:125-144](../../../../server/services/temporal/plugin_activities.py#L125)); slot and rate defaults are in `TemporalWorkerPool`. [test_task_queue_coverage.py](../../../../server/tests/test_task_queue_coverage.py) checks the coverage.
7. **Per-generation switch.** Read in `_frozen_routing_from_input` and `ExecutionControl.bind`; written by `capture_temporal_routing_input`, `freeze_generation_harness` and the control row's `resource_manifest` (section 5.4). A new flag name goes into `GENERATION_FLAGS`.
8. **Shipping a new patch id.** `DORMANT_PATCHES` in [patches.py](../../../../server/services/temporal/patches.py).
9. **Result contract.** The graph treats a node as succeeded only when the returned dict has a truthy `success`; downstream nodes receive its `result`.
10. **Context plumbing.** A key reaches a node's `NodeContext.raw` on the per-type path only if it is in the fixed list or the class's `context_keys` ([plugin/base.py:1410-1447](../../../../server/services/plugin/base.py#L1410)). The legacy path forwards a shorter list ([activities.py:259-270](../../../../server/services/temporal/activities.py#L259), [websocket.py:461-482](../../../../server/routers/websocket.py#L461)).
11. **An existing one-node workflow.** `NodeInvocationWorkflow` runs a single node (activity or agent child) with the saved graph as context, outside any graph run.
12. **An existing code node.** `montyExecutor` is a registered node type on the `code-exec` queue ([monty_executor/__init__.py:139](../../../../server/nodes/code/monty_executor/__init__.py#L139), [code/_base.py:46](../../../../server/nodes/code/_base.py#L46)). It runs inside an activity. Hand-off: code sandboxes.
13. **Workflow-task capacity.** Workflow code for every workflow type runs on the main-queue worker with `max_concurrent_workflow_tasks=10`.
14. **Identity inventory scope.** A workflow module outside the Temporal services folder and the cron file is not scanned by the golden identity test (section 5.6).

## 8. Where the docs disagree with the code

1. **Worker identity.** [TEMPORAL_ARCHITECTURE.md](../../../TEMPORAL_ARCHITECTURE.md) ("Worker Performance Tuning") gives `machina-<queue>-<deployment_mode>`. The code builds `opencompany-{queue}-{deployment_mode}` ([worker.py:74](../../../../server/services/temporal/worker.py#L74)).
2. **`ws_client.py`.** The same document's file tree calls it the WebSocket pool for the legacy activity path. The legacy activity opens its own socket from the shared session ([activities.py:276-281](../../../../server/services/temporal/activities.py#L276)). `WSConnectionPool`, `execute_node_ws` and `TemporalWSClient` have no production caller; only `internal_ws_url()` is imported, by the chat relay.
3. **Failure handling in the loop.** The document's loop sketch breaks at the first failure. The code breaks only for uncontrolled runs; controlled generations let running nodes finish (section 3.3, item 14). The sketch is labelled simplified.
4. **Trigger list copy.** The document's copy of `WORKFLOW_TRIGGER_TYPES` omits `stripeReceive` and `dodoPaymentsReceive` ([constants.py:397-425](../../../../server/constants.py#L397)). The document warns that its copy can fall behind.
5. **Who marks sibling triggers.** "Config Node Filtering" says `_execute_from_trigger()` marks non-firing triggers `_pre_executed`. On the Temporal path `_build_run_graph` does it; `_execute_from_trigger` is the local deployment path.
6. **Agent type count.** Code comments say 15 migrating agent types and "Specialized agents (11)" ([core/config.py:154](../../../../server/core/config.py#L154), [worker.py:210](../../../../server/services/temporal/worker.py#L210), [temporal/workflow.py:119](../../../../server/services/temporal/workflow.py#L119), [797](../../../../server/services/temporal/workflow.py#L797)). The set has 16 members, twelve of them specialized agents.
7. **Condition import.** [CLAUDE.md](../../../../CLAUDE.md) says importing `evaluate_condition` from the submodule avoids the package `__init__`, which pulls in the executor, cache, recovery sweeper and DLQ. The comment in the code says the package `__init__` still runs and that this is tolerated ([temporal/workflow.py:23-31](../../../../server/services/temporal/workflow.py#L23)).
8. **Facade size.** CLAUDE.md describes the workflow facade as about 840 lines; it is 1,155.
9. **"Any worker" wording.** The package and workflow docstrings say node activities can run on any worker in a cluster ([temporal/__init__.py:6-9](../../../../server/services/temporal/__init__.py#L6), [temporal/workflow.py:7](../../../../server/services/temporal/workflow.py#L7)). Per-type activities call the in-process service container, and the legacy activity must reach the originating server; the standalone worker refuses to start outside distributed mode ([worker.py:799-804](../../../../server/services/temporal/worker.py#L799)).
10. **RFC-0004 local activities.** Section 15 of [RFC-0004](../../../../RFC-0004-TEMPORAL-SDK-FEATURE-ADOPTION.md) records local activities as reopened for progress broadcasts in new generations. No local activity call exists in the code at this commit. The RFC is a design document, so this is a status note, not an error.
11. **RFC-0004 line references.** Its section 4.1 cites the first-completed wait at lines 1101-1133; it is now at 1128-1160. The RFC says its line numbers are anchored to a baseline.

[DESIGN.md](../../../DESIGN.md) was skimmed; its routing description (Temporal, then the unreachable Redis branch, then sequential) matches the facade.

## 9. Not verified / open questions

1. Nothing was executed. Every statement about runtime behaviour comes from reading code and the installed SDK's signatures.
2. **Cancellation semantics.** With no handler in `MachinaWorkflow`, the effect of a cancel request is INFERRED from SDK behaviour: the pending await raises, the controlled `finally` block then awaits one more activity, and children follow the default `TERMINATE` close policy when the parent closes. Not confirmed by a test read here.
3. **Uncontrolled failure path.** After the `break`, handles still running are abandoned. INFERRED: when the workflow returns, the server cancels pending activities and terminates running agent children under the default close policy.
4. **`disabled` with a parameter snapshot.** `node_data` is the snapshot entry when the node id is present, and the snapshot is the sanitized saved parameters ([deployment/handlers.py:362](../../../../server/services/deployment/handlers.py#L362)). Whether that entry carries `disabled` was not checked, so whether a disabled node is skipped in deployed runs is NOT VERIFIED. Agent children are started without any disabled check in `MachinaWorkflow`.
5. **`ToolNode` in main-flow position.** `ToolNode._wrap_success` returns a flat dict with no `success` key ([plugin/tool.py:214-241](../../../../server/services/plugin/tool.py#L214)), the per-type activity returns it unchanged, and `interpret_result` is used only for broadcasts. `canvas` is a `ToolNode` with an `input-main` handle ([canvas/__init__.py:163-190](../../../../server/nodes/tool/canvas/__init__.py#L163)). INFERRED: the graph would record such a node as failed with "Unknown error". NOT VERIFIED by a test or a run.
6. **`store_node_output_activity` retries.** It is scheduled without a retry policy. INFERRED: Temporal's default unlimited policy applies; RFC-0004 section 9.2 lists it that way.
7. **Legacy socket lifetime.** Whether the shared session's 300 s total timeout bounds a long legacy WebSocket execution was not determined.
8. **Thread model.** Async activities share the backend's event loop in the embedded worker (the per-type docstring says the worker shares the FastAPI process). How workflow tasks are scheduled relative to that loop, and the deadlock-detector behaviour for unsandboxed workflows, were not checked.
9. **History cost.** Events per node (activity or child) and the practical node count before a run needs continue-as-new were not measured.
10. **Replay corpus contents** and the recorder were not opened; the count of scenarios in the architecture document was not checked.
11. **Activity type on replay.** That replay compares only a command's kind, activity or child type, and id is taken from RFC-0004 section 8.5, not from the SDK source.
12. **Docs coverage.** TEMPORAL_ARCHITECTURE.md was read in full except its server-management and debugging sections; DESIGN.md and RFC-0004 were skimmed.
