# Temporal Distributed Node Execution Architecture

> **Current control contract:** [Temporal workflow control](temporal-workflow-control.md)
> defines generation ownership, acknowledged Stop/Resume, Reset, and recovery.
> This document describes execution, Activity dispatch, worker routing, and
> continuation. The [original execution-engine RFC](ARCHIVE/temporal-execution-engine-rfc.md)
> is historical design context; use the current docs and implementation for behavior.

## Overview

Executable workflow nodes run as **Temporal Activities** or, for supported agents,
child Workflows. Each receives its own execution context, enabling distributed
workers to run independent steps. The orchestrator dispatches in one of three
ways depending on the run's frozen settings flags:

| Dispatch | Trigger | Use case |
|---|---|---|
| **Legacy single activity** (`execute_node_activity`) | `TEMPORAL_PER_TYPE_DISPATCH=false` | Every node routed through one dispatcher activity. WebSocket round-trip back to the FastAPI server. Stable since Wave 11; kept as the fallback path. |
| **Per-type activity** (`node.{type}.v{version}`) | `TEMPORAL_PER_TYPE_DISPATCH=true` (production default) | Each plugin gets its own Activity definition. Graph dispatch applies plugin retry and, with the frozen worker-pool flag on, `cls.task_queue`. Ordinary graph timeout/heartbeat remain 24 h / 2 min; Workspace-task patches use plugin values. Version 1 agent-tool dispatch records and uses all declared Activity policies. |
| **Agent-as-child-workflow** (`AgentWorkflow`) | `TEMPORAL_AGENT_WORKFLOW_ENABLED=true` | Agent types in `AGENT_WORKFLOW_TYPES` in `services/temporal/workflow.py` run as child Workflows. Each LLM turn and ordinary tool call is a regular Activity; direct delegations can start attached agent children and Task Manager assignments start detached runners. |

`rlm_agent`, `claude_code_agent` and `vertex_managed_agent` are intentionally excluded from AgentWorkflow — their externalised loops (RLM REPL / Claude CLI `--resume` / Vertex Interactions API `previous_interaction_id` chaining) require single-process state continuity. The authoritative list is `AGENT_WORKFLOW_TYPES` in `services/temporal/workflow.py`; any agent type outside it runs as an ordinary per-type activity.

**Routing snapshot (`_temporal_routing_v1`).** The three dispatch flags — `TEMPORAL_PER_TYPE_DISPATCH`, `TEMPORAL_AGENT_WORKFLOW_ENABLED` and `TEMPORAL_WORKER_POOL_ENABLED` (which picks the task queue) — are read once by whoever starts the run and frozen into the workflow input under `_temporal_routing_v1` (`services/temporal/executor.py::capture_temporal_routing_input`). `TemporalExecutor.execute_workflow` captures them per run; `DeploymentManager` captures them when it registers a push/poll listener or creates a cron Schedule, and the listener hands the same snapshot to every run it spawns. `MachinaWorkflow.run` dispatches only from that snapshot (`_frozen_routing_from_input`) and never re-reads `Settings`, so changing a flag affects runs started, and triggers registered, afterwards — never a run already in flight. An input without a snapshot (or with an unknown version) gets `_SAFE_FROZEN_ROUTING`: agent workflow and per-type dispatch on, worker pool off. Locked by `tests/temporal/test_frozen_routing.py`.

## Execution Routing & Running

`WorkflowService.execute_workflow` routes each run (`server/services/workflow.py`):

1. If `TEMPORAL_ENABLED=true` and Temporal is configured → `_execute_temporal()` (the distributed path documented in this file).
2. Else → `_execute_sequential()` (single-threaded fallback).

(The Redis-backed `_execute_parallel()` branch still exists in the facade but is unreachable in all shipped configs — no shipped configuration enables Redis — so the effective routing is Temporal → sequential.)

**Running with Temporal** — the Temporal server and the embedded worker start automatically with every launch script:

```bash
company start            # Starts the app; the backend lifespan starts the Temporal dev server when TEMPORAL_ENABLED
company dev              # Starts Temporal server + all services (dev mode)
company stop             # Stops all services including Temporal
```

(`bun run start` / `bun run dev` / `bun run stop` are thin wrappers over the same `company` verbs.)

The embedded Temporal worker runs **inside the Python backend**, not as a separate process. `main.py` only schedules `run_temporal_lifecycle` (task `temporal-init`); [services/temporal/lifecycle.py](../server/services/temporal/lifecycle.py) `_start_execution_engine` creates the `TemporalWorkerManager` and then the `TemporalWorkerPool`.

**Standalone worker** (for horizontal scaling — add more pollers against the same task queue):

```bash
cd server
python -m services.temporal.worker
```

This invokes `run_standalone_worker()` from `services/temporal/worker.py`. It also starts the chat relay
(`services/chat/relay.py`): the worker has no sockets of its own, so the chat run events and the `chat.updated` /
`approval_lifecycle` broadcasts its activities make go to the backend over `/ws/internal`
([Chat Protocol → Standalone workers](./chat_protocol.md#standalone-workers)). Other broadcasts from its activities
(node status, agent progress) still reach only its own process.

## System Architecture

```
                TEMPORAL SERVER (port TEMPORAL_FRONTEND_GRPC_PORT)
                                  |
              Task Queue: machina-tasks  (workflows + framework activities)
  +---------------------------------------------------------------+
  |  Workflow: MachinaWorkflow (orchestrator only)                |
  |  - Parses graph structure from React Flow                     |
  |  - Filters config nodes (tools, memory, services)             |
  |  - Resolves activity per node (legacy / per-type / agent-wf)  |
  |  - Per-type activities carry task_queue=cls.task_queue        |
  |    (Wave 16; TEMPORAL_WORKER_POOL_ENABLED, default on)        |
  |  - Schedules activities (FIRST_COMPLETED pattern)             |
  |  - Collects results and routes outputs to dependent nodes     |
  +---------------------------------------------------------------+
                                  |
          Activity / child-workflow scheduling (routed by queue)
                                  |
   machina-tasks        specialised queues (TemporalWorkerPool)
        |            ai-heavy   code-exec   browser   rest-api  ...
  +-----------+     +--------+  +--------+  +-------+  +-------+
  | Manager   |     | Pool   |  | Pool   |  | Pool  |  | Pool  |
  | worker    |     | worker |  | worker |  | worker|  | worker|
  | (wf tasks |     | aiAgent|  | python |  |browser|  | gmail |
  | + legacy  |     | chatA  |  | js/ts  |  |       |  | brave |
  | dispatch  |     | deepA  |  |        |  |       |  | ...   |
  | + AgentWf)|     +--------+  +--------+  +-------+  +-------+
  +-----------+          |           |          |          |
        +----------------+-----------+----------+----------+
                           |
                           v
                   In-process call (F4.A) OR
                   WebSocket round-trip (legacy)
                   +----------------+
                   | OpenCompany      |
                   | workflow_svc   |
                   +----------------+
```

## Key Architecture Principles

### 1. External Work = Independent Activity

Ordinary nodes and the external steps of agent child Workflows run as Activities with:
- **Own context** - No shared mutable state between nodes
- **Own retry policy** - Plugin declarations take precedence over ordinary defaults. Agent LLM steps use unlimited transient retries; a declared one-attempt tool keeps that policy.
- **Own timeout** - Ordinary graph dispatch uses a 24 h ceiling and 2-minute heartbeat; Workspace-task nodes and version 1 agent-tool dispatch consume plugin values. Different Activities can progress independently.
- **Own worker** - Can execute on any available worker in the cluster

### 2. Workflow = Pure Orchestrator

The workflow ONLY orchestrates:
- Parses the graph structure from React Flow nodes/edges
- Filters out config nodes (tools, memory, model configs)
- Determines execution order based on dependencies
- Schedules activities using FIRST_COMPLETED pattern
- Collects results and routes outputs to dependent nodes

Provider requests, tool execution, database access, and external effects happen
in Activities. Workflow code owns deterministic scheduling, admission gates,
result bookkeeping, and durable continuation.

### 3. Context Passing (Immutable)

Each node receives an immutable context snapshot:

```python
context = {
    "node_id": "1:aiAgent:1",
    "node_type": "aiAgent",
    "node_data": {
        "model": "gpt-4",
        "prompt": "{{chattrigger.message}}",
        "systemMessage": "You are a helpful assistant"
    },
    "inputs": {  # Outputs from upstream nodes
        "1:chatTrigger:1": {"message": "Hello", "timestamp": "..."},
    },
    "workflow_id": "1",                              # Backend-allocated application identity
    "workflow_slug": "AI_Assistant_1",                  # Human-readable, used for Temporal child IDs
    "session_id": "session-xyz",
    "execution_id": "1:execution:1",             # Application execution identity; distinct from
                                                  # Temporal Workflow ID and Temporal Run ID
    "nodes": [...],  # Full list for tool/memory detection
    "edges": [...],  # Full list for tool/memory detection
}
```

## Cooperative generation Stop and Resume

New generations record `resource_manifest.execution_control_version=1` and
propagate that protocol in their Workflow input. The version is a generation
contract, independent of routing flags. Existing generations keep their prior
command paths; deployment of new code does not silently give old histories the
stronger acknowledgement guarantee.

Stop means **finish admitted work, retain its result, and wait before the next
action**. For a model response with tools A and B, Stop during A lets A finish
and records its result; B stays pending. Resume admits B from the existing
continuation. The model response and A are not explicitly scheduled again.
Already-admitted parallel work drains concurrently under its original retry
policies. Controller events remain queued while stopped.

`services/temporal/execution_control.py::ExecutionControl` holds the revision,
pause posture, producer hold, active-action count, and pending child-start
count in Workflow state. Owners expose completed `set_control_state` and
`wait_for_checkpoint` Updates and a read-only `execution_control_status` Query.
Initialization binds the input before early handlers can run; pure validators
reject malformed or wrong-generation/chain requests before acceptance. The
helper does not intercept SDK commands: callers use admission gates at their
existing scheduling boundaries.

The boundary accounting is deliberate:

| Work | Admission and acknowledgement |
|---|---|
| Model request, ordinary node/tool, polling fetch, compaction, tool refresh | Gate before scheduling; count admitted work through result consumption and its normal bookkeeping. |
| Child Workflow start | Gate and count until Temporal acknowledges the start, allowing complete native child enumeration afterward. |
| Child result or subagent-permit wait | Excluded from local drain count; the child is controlled separately and a waiting parent must not block Stop. |
| Control enrollment, checkpoint waits, maintenance | Excluded from business-work drain; completed results can still be recorded while stopped. |

The generation controller records only **independent roots**: graph runs,
cron/job roots, and detached `DelegatedTaskWorkflow` runners that can outlive
their parent. Roots enroll through an acknowledged controller Update before
business work; a regular maintenance Activity bridges Workflow code to the
Temporal client because Workflows cannot directly send Updates. Attached
agent children inherit the scope and are discovered from native execution
descriptions. There is no SQL participant registry, recurring participant
heartbeat, or separate checkpoint store.

`services/deployment/execution_control.py::transition_generation` first holds
controller producers and fences every reachable Workflow, then waits for
their checkpoint acknowledgements. Setters acknowledge already-admitted child
starts before the next Describe call. It reconciles new root registrations
until the controller membership epoch is stable. `pausing` means Stop has been
requested; `paused` is published only after admitted business work and
bookkeeping settle throughout that topology. A request timeout leaves
`pausing` for reconciliation and does not cancel the draining Activity.

Resume publishes a newer intent with producers held, releases existing
descendants and independent roots, reconciles membership, and releases the
controller producers last. Local collectors and schedules reopen through the
generation control handler. Revisions prevent delayed enrollment replies or
stale control messages from overriding newer intent. Control uses current
Workflow-ID handles with first-execution-chain checks, so continue-as-new
Run-ID changes remain addressable and reused IDs cannot control another chain.
An unexpectedly lost version 1 controller fails closed; an empty replacement
root map cannot prove a generation is safely resumable.

`rlm_agent`, `claude_code_agent`, and `vertex_managed_agent` remain whole-node
Activities. Stop waits for the admitted Activity, including its internal tool
work, to settle. It does not suspend the CLI, REPL, or cloud agent between
internal calls. Direct Workspace tasks and separately approved sends have
their own execution ownership; generation Stop does not control those
independent executions.

Heartbeats provide liveness and cancellation/progress information when an
Activity implements that contract. A heartbeat status string cannot checkpoint
an opaque request or process session. We use regular Activities for provider
and tool work, preserving retries, heartbeats, and worker routing. The
[Long-running Activity pattern](https://docs.temporal.io/design-patterns/long-running-activity)
explains those contracts; [Resumable Activity](https://docs.temporal.io/design-patterns/resumable-activity)
addresses retrying a failed Activity after corrective input, which is a
different operation from releasing this suspended continuation. The
[Python messaging guide](https://docs.temporal.io/develop/python/workflows/message-passing)
defines the Signal/Update/Query acknowledgement and handler rules used here.

Stop can remain `pausing` during a persistent provider outage because
`LLM_STEP_RETRY` intentionally has no attempt limit. A worker loss or ambiguous
external effect before Temporal records completion can still trigger an
Activity retry according to the tool's policy; external-effect deduplication
belongs to the tool's idempotency contract. Resume does not promise exactly-once
delivery to external systems.

For API shapes, chat lifecycle, recovery, and verification, see
[Temporal workflow control](temporal-workflow-control.md) and
[Chat protocol](chat_protocol.md).

## Execution Flow

### 1. Workflow Receives Request

```
OpenCompany Server
       |
       v execute_workflow()
TemporalExecutor
       |
       v client.execute_workflow()
Temporal Server
       |
       v schedules workflow task
MachinaWorkflow.run()
```

### 2. Workflow Orchestrates Nodes (FIRST_COMPLETED Pattern)

```python
# 1. Filter config nodes
exec_nodes, exec_edges = self._filter_executable_graph(nodes, edges)

# 2. Build dependency graph
deps, node_map = self._build_dependency_maps(exec_nodes, exec_edges)

# 3. Handle pre-executed triggers
for node in exec_nodes:
    if node.get("_pre_executed"):
        completed.add(node["id"])

# 4. Continuous scheduling loop
while True:
    ready = self._find_ready_nodes(deps, completed, running, node_map)

    for node_id in ready:
        node_type = node_map[node_id].get("type", "unknown")

        # Safety: auto-complete trigger nodes that weren't pre-executed
        if node_type in TRIGGER_NODE_TYPES and not node.get("_pre_executed"):
            completed.add(node_id)
            continue

        # F4.A: resolve to per-type name + queue when the run's frozen
        # routing snapshot has the flag on, else fall back to the legacy
        # single dispatcher. (Simplified: the real loop calls
        # _resolve_dispatch(..., routing_snapshot=frozen_routing), which
        # also picks the AgentWorkflow child path.)
        activity_name, activity_queue = self._resolve_activity(
            node_type,
            per_type_dispatch_enabled=frozen_routing["per_type_dispatch_enabled"],
            worker_pool_enabled=frozen_routing["worker_pool_enabled"],
        )
        start_kwargs = dict(
            args=[context],
            activity_id=node_id,
            # Heartbeat is the liveness mechanism; start_to_close only
            # bounds a single legitimate step (24h; unconditional, no
            # workflow.patched marker).
            start_to_close_timeout=_NODE_ACTIVITY_START_TO_CLOSE,
            heartbeat_timeout=timedelta(minutes=2),
            retry_policy=activity_retry_policy,  # plugin cls.retry_policy wins when declared
        )
        if activity_queue is not None:
            start_kwargs["task_queue"] = activity_queue

        handle = workflow.start_activity(activity_name, **start_kwargs)
        running[node_id] = handle

    if not running:
        break

    done_id, result = await self._wait_any_complete(running)
    if not result.get("success"):
        # The first failure stops the run (see "4. The run result").
        errors.append({"node_id": done_id, "error": result.get("error", "Unknown error")})
        break
    completed.add(done_id)
    outputs[done_id] = result
```

### 3. Activity executes the node

**Legacy path** (`execute_node_activity`): The activity round-trips through the local WebSocket back to FastAPI, which dispatches to the plugin handler. This was the only path before F4.A.

**Per-type path** (`node.{type}.v{version}`, F4.A): The Activity body lives on the plugin class via `BaseNode.as_activity()` and calls `workflow_service.execute_node(...)` **directly** — no WebSocket round-trip. Same DI container (the embedded worker shares the FastAPI process), same broadcasting + parameter-fetch pipeline. Activity options are selected by the caller, not the registration decorator. Graph dispatch uses plugin retry/queue with ordinary 24 h / 2 min timeout/heartbeat defaults, except the Workspace-task patch. Version 1 AgentWorkflow tool bindings record and use the plugin's timeout, heartbeat, retry, and queue declarations during payload preparation or explicit refresh. See `server/services/plugin/base.py:as_activity`, `server/services/temporal/workflow.py`, and `server/services/temporal/agent_workflow.py::_execute_plugin_tool_activity`.

```python
# F4.A per-type activity body (server/services/plugin/base.py)
@activity.defn(name=f"node.{cls.type}.v{cls.version}")
async def _node_activity(context: Dict[str, Any]) -> Dict[str, Any]:
    # ... pre_executed / disabled checks ...
    broadcaster = container.status_broadcaster()
    await broadcaster.update_node_status(node_id, "executing", ..., workflow_id=...)

    workflow_service = container.workflow_service()
    result = await workflow_service.execute_node(
        node_id=node_id, node_type=cls.type,
        parameters=node_data,
        nodes=context.get("nodes", []), edges=context.get("edges", []),
        session_id=context.get("session_id", "default"),
        workflow_id=workflow_id,
        outputs=context.get("inputs", {}),
    )
    # ... broadcast success/error, return result ...
```

**Heartbeat strategy (critical for long-running activities):**

The 2-minute `heartbeat_timeout` would kill browser or claude_code_agent activities that routinely run 5-10 minutes. Both dispatch paths emit `activity.heartbeat()` at progress points — legacy on every non-matching WebSocket message, per-type at the start of each pipeline stage.

In the legacy path the server broadcasts status updates, tool-glow events, and progress messages continuously during execution, so the WS-read-loop heartbeats keep the activity alive for as long as anything is happening. Start/end heartbeats alone are not enough — any operation longer than 2 minutes would trigger `TIMEOUT_TYPE_HEARTBEAT` and Temporal would retry (or fail) the activity.

### 4. The run result

`MachinaWorkflow.run` returns `{success, outputs, execution_trace, errors, total_nodes}`.
- `total_nodes` is the size of the executable graph.
- `execution_trace` lists every node the run completed, skipped or auto-completed.
- `success` is true only when no node failed and every node in the executable graph completed.
- The first node that fails stops the run. `errors` then holds that failure as `{node_id, error}`, plus whichever of `hint`, `requires_user_action` and `retryable` the node supplied. It is `None` when nothing failed.
- Only an empty graph returns a top-level `error` ("No nodes provided") instead.

`TemporalExecutor.execute_workflow` turns that into the Run response, and `WorkflowService._execute_temporal` forwards it:
- `errors` is the workflow's list unchanged, or `[{"error": ...}]` for an empty graph or a failed Temporal call;
- `error` is the first entry's message, or `None`;
- `total_nodes` is the workflow's count, and `completed_nodes` is the length of `execution_trace`.

The in-process paths return the same four fields (`_run_errors` and `_node_failure` in `services/workflow.py`):
- the sequential fallback counts the nodes reachable from the start node, which leaves out tools and other nodes wired into an agent;
- the Redis-only parallel engine counts its node executions, which leave out config nodes and agent sub-nodes.

The editor's Run dialog shows `error` when a run fails ("Workflow failed: …") and `completed_nodes` / `total_nodes` when it succeeds ("N/M nodes completed"). Before these fields were set, a failed Run said "Unknown error" and a successful one said "0/0 nodes completed" ([Known Errors #31](./errors.md)). Locked by `server/tests/temporal/test_run_errors.py` and `server/tests/services/test_workflow_run_result.py`.

## Connection Pooling

Activities use a shared aiohttp.ClientSession for connection pooling:

```python
class NodeExecutionActivities:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session  # Shared session with connection pool

    @activity.defn
    async def execute_node_activity(self, context: Dict) -> Dict:
        # Each activity gets its own WebSocket from the pool
        async with self.session.ws_connect(self.ws_url) as ws:
            await ws.send_json(message)
            async for msg in ws:
                return msg.data

# Session configuration
connector = aiohttp.TCPConnector(
    limit=100,              # Max connections in pool
    limit_per_host=100,     # Max connections per host
    enable_cleanup_closed=True,
)
session = aiohttp.ClientSession(connector=connector)
```

Benefits:
- **No race conditions** - Each activity has exclusive WebSocket
- **Connection reuse** - TCP connections are pooled and reused
- **Configurable limits** - Control max concurrent connections

## Scaling Patterns

### Horizontal Worker Scaling

```
                 Temporal Server
                       |
       +---------------+---------------+
       v               v               v
  +---------+     +---------+     +---------+
  |Worker 1 |     |Worker 2 |     |Worker 3 |
  | Node A  |     | Node B  |     | Node C  |
  | Node D  |     | Node E  |     | Node F  |
  +---------+     +---------+     +---------+

Add more workers = handle more concurrent nodes
```

### Specialized Worker Pools (Wave 16 — `TEMPORAL_WORKER_POOL_ENABLED`, default on since 16.4)

`TemporalWorkerPool` is started by the Temporal lifecycle task (`services/temporal/lifecycle.py::_start_execution_engine`, right after `TemporalWorkerManager`) and stopped before it by the `main.py` lifespan teardown. One activity-only Worker per plugin-declared queue polls its specialised queue, and `MachinaWorkflow._resolve_activity` returns `(activity_name, cls.task_queue)` when the run's frozen routing snapshot has `worker_pool_enabled` set, so per-type activities land there. Setting `TEMPORAL_WORKER_POOL_ENABLED=false` (then restarting) stops the pool, and runs started afterwards route every activity to the single manager worker on `machina-tasks` — **the flag is the rollback channel** (locked default-on by `test_task_queue_coverage.py::TestWorkerPoolDefaultOn`). It is not retroactive: `_resolve_activity` receives the flag from the run's `_temporal_routing_v1` snapshot rather than reading `Settings`, so a run (or a deployed trigger) that started with the pool on keeps scheduling onto the specialised queues, which have no poller while the pool is off.

```
Queue: rest-api         Queue: ai-heavy         Queue: code-exec
     |                       |                       |
+----+----+             +----+----+             +----+----+
| Worker  |             | Worker  |             | Worker  |
| gmail   |             | aiAgent |             | python  |
| brave   |             | chatA   |             | js exec |
| twitter |             | deepA   |             | ts exec |
+---------+             +---------+             +---------+
```

Pre-flight invariants live in `server/tests/test_task_queue_coverage.py` (every queue populated, every plugin queue declared, `DEFAULT_CONCURRENCY` covers every queue — an off-registry queue would hang activities at schedule-to-start).

### Worker Performance Tuning (Waves 17-18)

All knobs live in `services/temporal/worker.py`; every default is env-overridable. `DEPLOYMENT_MODE` (`local` / `cloud` / `self_hosted`) is the topology hint (`core/config.py`) and also feeds the dev-placeholder-secret startup guard (`dev_secret_offenders()` warns when placeholders survive outside `local`). `company deploy` sets `DEPLOYMENT_MODE=cloud` on deployed VMs (`cli/commands/deploy/_secrets.py::build_app_env`).

| Queue | Concurrency (cloud) | Concurrency (local = halved, floor 1) | Rate limit (act/s) | Slot sizing |
|---|---|---|---|---|
| `machina-default` | 20 | 10 | — | fixed |
| `rest-api` | 50 | 25 | 100 | fixed |
| `ai-heavy` | 4 | 2 | 60 | **resource-based** (80% CPU+mem target) |
| `code-exec` | 10 | 5 | — | fixed |
| `triggers-poll` | 100 | 50 | — | fixed |
| `triggers-event` | 100 | 50 | — | fixed |
| `android` | 10 | 5 | — | fixed |
| `browser` | 4 | 2 | 10 | **resource-based** (80% CPU+mem target) |
| `messaging` | 20 | 10 | 20 | fixed |

Env overrides: `TEMPORAL_<QUEUE>_CONCURRENCY` (int) and `TEMPORAL_<QUEUE>_RATE_LIMIT` (float/sec) always win over the mode-scaled defaults.

- **Worker identity** (Wave 17.4): `machina-<queue>-<deployment_mode>` — readable Workers tab in the Temporal Web UI.
- **Sticky workflow cache** (Wave 18.2, manager worker only): `max_cached_workflows` = local 50 / cloud 500 / self_hosted 100. Cached workflows skip Event-History replay; evictions are a latency cost, not an error.
- **Poller autoscaling** (Wave 18.3): `PollerBehaviorAutoscaling` — manager activity pollers 1-10 (initial 2), workflow pollers 1-20 (initial 2); pool workers 1-5 (initial 1). Invariant per the [worker-performance docs](https://docs.temporal.io/develop/worker-performance): pollers stay below executor slots.
- **Resource-based slot supplier** (Wave 18.4): `ai-heavy` + `browser` (unpredictable workloads) use `ResourceBasedSlotSupplier` targeting 80% host CPU + memory, `minimum_slots=1`, `maximum_slots` = the per-queue concurrency. Requires `temporalio>=1.25.0` (pinned).
- **SDK tracing interceptor ownership**: `TracingInterceptor` is registered exactly once on the shared `Client.connect(...)`. The Temporal Python SDK automatically prepends compatible client interceptors to every `Worker` built from that client. Worker constructors must not repeat `TracingInterceptor`; doing so creates paired OpenTelemetry spans for one execution.
- **Application observability interceptor** (Wave 17.3, `services/temporal/_interceptors.py`): workers explicitly register `ObservabilityWorkerInterceptor` for `activity_retry` WARN logs when `activity.info().attempt > 1` — the "worker died and Temporal re-dispatched" signal — and replay-guarded `workflow_start` logs. This is separate from SDK tracing and remains worker-owned.
- **Periodic activity heartbeat** (Wave 17.6, `plugin/base.py::as_activity`): 30s background beat during long bodies so a laptop-sleep crash is detected within one `heartbeat_timeout` (2 min) instead of `start_to_close` (24 h).
- **Cron catch-up bound** (Wave 17.1, `services/temporal/schedules.py`): `SchedulePolicy(catchup_window=24h)` + `SKIP` overlap — a laptop offline a week does not replay 168 hourly ticks on wake.
- **LLM-step retry** (Wave 17.2, since reverted): `LLM_STEP_RETRY` on `agent.execute_llm_step` is now **unlimited** (`maximum_attempts=0`, 5 s → 5 min exponential backoff) — see the docstring in `services/temporal/_retry_policies.py`. The one-shot policy Wave 17.2 shipped meant one transient provider error killed a months-long deployment and tripped the circuit breaker; terminal outcomes still fail fast because `_as_temporal_llm_error` marks non-retryable categories (invalid_request, authentication, ...) with `non_retryable=True`, and provider `retry_after` hints ride `ApplicationError.next_retry_delay`. Accepted trade-off: an ambiguous loss can re-bill a prompt. `agent.refresh_tools` keeps the 3-attempt default (idempotent canvas rebuild).

**Metric watchlist** (Temporal Web UI / metrics endpoint): `schedule_to_start_latency` (elevated = raise pollers or slots), `worker_task_slots_available` (0 = raise concurrency or add hosts), `poll_success_rate` (target >= 90%), `sticky_cache_evictions` (persistent growth = raise `max_cached_workflows`).

**Tuning order** (per the worker-performance docs): host provisioning -> executor slots -> poller counts -> rate limits.

### Agent-as-child-workflow (F4.B)

When `TEMPORAL_AGENT_WORKFLOW_ENABLED=true` and the node type is in `AGENT_WORKFLOW_TYPES` (`aiAgent`, `chatAgent`, the specialized agents and the team leads; `rlm_agent`, `claude_code_agent` and `vertex_managed_agent` are excluded), the orchestrator schedules `AgentWorkflow` as a child workflow instead of an activity. Inside the workflow:

```
AgentWorkflow.run(context):
  0. restore the carried prepared_payload on a version 1 continuation;
     otherwise execute_activity("agent.prepare_payload")
       resolves the DB-backed payload from the canvas context — runs
       workflow_service._param_resolver.resolve so {{node.field}}
       templates in prompt / system_message become real values BEFORE
       the LLM sees them. Temporal workflows must be deterministic,
       so DB lookups + edge walking + tool schema build live here.
       _build_tool_from_node produces AgentToolSpec values. Tool entries
       carry the serialized ToolDef declaration plus routing tool_info;
       native LLM steps consume the definition directly.
  emit_phase("starting", status="executing")
  loop until "final" or max_iterations:
    1. emit_phase("llm_step", iteration=N)
    2. execute_activity("agent.execute_llm_step")
         returns {kind, assistant_message, calls?, content?, usage}.
         assistant_message is a MessageWire dict: ordered text/reasoning/tool
         blocks plus JSON-safe provider continuation state (Gemini thought
         signatures, Anthropic signed/redacted thinking, OpenAI response
         metadata). It is appended verbatim to messages.
         After filter_empty_messages, a list with no user/assistant/tool
         message raises ApplicationError(type="EmptyAgentPrompt",
         non_retryable=True) — providers require >=1 non-system
         message (Gemini pulls system messages into system_instruction
         and rejects empty contents), so the failure surfaces to the
         parent LLM on attempt 1 instead of consuming the retry budget.
    3. if kind == "tool_calls":
         for each call:
           emit_phase("executing_tool", tool_name=...)
           if delegate_to_* AND child type in AGENT_WORKFLOW_TYPES:
             # {task, context} are per-invocation INPUT, not node
             # config — both empty -> tool-error back to the LLM,
             # no child spawn.
             execute_child_workflow("AgentWorkflow", child_context)
               child_context = {**tool_payload,
                                "parent_node_id": <self>,
                                "invocation": {"task": …, "context": …}}
               id = _delegation_child_id(...)
                  # "{agent_workflow_id}-delegate-{tool_node_id}-{iteration+1}-{call_index+1}"
           else:
             execute_activity(f"node.{tool_node_type}.v{version}")
           emit_phase("tool_completed", tool_name=...)
           _serialise_tool_result unwraps F4.A's {success, result, ...}
           envelope so the LLM sees only the handler's return value
           (matches the in-process tool-call serialisation in
           services/agent_runtime.py:run_native_agent_loop), then an
           external tool's text is cut to payload["tool_result_max_chars"]
           (services/tool_output.py; delegations, skill loads and Task
           Manager are never cut).
    4. for each tool result with an ``operations`` field
       (canvas-mutating tools — today only ``agentBuilder``):
         if payload["auto_rebind_tools"] is True:
           execute_activity("agent.refresh_tools", {operations: …})
              translates add_node ops with component_kind=="tool" OR
              usable_as_tool=True (minus chat-model plugins and the
              Skills node, masterSkill, which is tool-kind but feeds
              input-skill) into the same tool_payload shape
              prepare_agent_payload emits.
              Reuses ai_service._build_tool_from_node + get_node_class,
              yielding fresh AgentToolSpec / ToolDef declarations.
           tools.extend(refresh_result["tools"])
           tool_index.update(...)
           # next execute_llm_step sees the new tools.
    5. execute_activity("agent.persist_turn")
         append_to_memory_markdown(content, "human", prompt) +
         (content, "ai", response); trim window; broadcast
         node_parameters_updated CloudEvents (source_hint="agent").
    6. transcript pressure, rules picked by the recorded
       payload["context_pressure_version"] (agent_context_pressure.py):
         version 1: clear earlier turns' tool results past
           payload["transcript_budget_bytes"]; if the next request reaches
           compaction_threshold, or the earlier turns alone keep the
           transcript over budget, execute_activity("agent.compact_context")
           on the earlier turns only and keep the latest turn verbatim;
           then cut the latest turn's external results to fit.
         no version (histories recorded before it): if the running sum of
           every step's tokens >= compaction_threshold, summarize the
           whole transcript.
         A summarizer failure after the activity's retries ends the run
         with error_type="CompactionError".
  execute_activity("agent.store_output")
       wraps workflow_service.store_node_output for output_main /
       output_top / output_0 — same writes NodeExecutor.execute does
       at services/node_executor.py:198-200, so downstream nodes
       can resolve {{aiAgent.response}} via ParameterResolver.
  emit_phase("completed", status="success")
```

The `agent.prepare_payload` result is recorded in history. There is one
engine and one wire standard (`MessageWire`): every turn uses `ChatUnifier`
plus the native provider SDKs, and no `llm_engine` / `message_wire_version`
discriminator is recorded (the cutover-era markers and the
`InvalidAgentLLMEngine` refusal path were purged; `tests/llm/test_single_wire_standard.py`
fails the build if they reappear). Pre-cutover deployments are handled by Reset,
which starts a fresh generation. Changing the environment cannot change an
execution after it starts, and a native run never falls back after a provider
request starts.

`emit_phase(phase, status?)` is a thin helper that schedules `agent.broadcast_progress`. The activity emits `WorkflowEvent.agent_progress` (CloudEvents v1.0, `type="com.opencompany.agent.progress"`) for FE consumers; when `status` is supplied it also drives a raw-dict `update_node_status` for the canvas-glow color (executing / success / error). Same dual-channel pattern F4.A's `_node_activity` uses. When this workflow is itself a delegated child (`context["parent_node_id"]` set), every `emit_phase` call ALSO schedules a second broadcast against the parent's `node_id` with `phase="delegating"` — the parent's canvas badge then advances in real time while the child loops, instead of freezing at "executing" glow until the child completes.

Each LLM step is one Activity and each ordinary tool call is one per-type
Activity. In version 1, gates surround model requests, tool admission, tool
refresh, compaction, and child starts; admitted result and transcript
bookkeeping finish while stopped. Unstarted calls from a recorded response
stay in the current Workflow continuation. Stop preserves the existing
parallel scheduling and never converts child-result waits into drain work.

Team-lead Task Manager assignments persist first, then the lead starts a
deterministic detached `DelegatedTaskWorkflow` with
`ParentClosePolicy.ABANDON` and receives `queued` immediately. The runner owns
the root-wide permit, claim, child `AgentWorkflow`, terminal result/usage
persistence, `taskTrigger`, and permit release, so the assigning lead can
return without polling. A version 1 runner independently enrolls with the
generation controller; its attached agent child needs no separate enrollment.
Direct non-team delegation retains the attached child-workflow path.
Non-agent tools and excluded types (`rlm_agent`, `claude_code_agent`,
`vertex_managed_agent`) still use `execute_activity`. Failures surface as
durable task failures and trigger review rather than being lost when the lead
invocation closes.

Which graph a firing runs depends on the deployment kind. Controlled generations (the listener payload carries a `data_scope_id`) execute the graph snapshot carried in their trigger registration on every firing, with no per-firing graph lookup. Legacy uncontrolled deployments (no `data_scope_id`) keep the hot lookup: each push or poll firing in `TriggerListenerWorkflow._spawn_child_run` / `PollingTriggerWorkflow._spawn_child_run` resolves the latest persisted workflow graph through `load_persisted_workflow_graph_activity` before filtering downstream nodes, falling back to the deployment snapshot if the lookup fails; that is what makes tools added after deployment available to `taskTrigger` and other triggered agent runs on those deployments. Edge traversal accepts both canonical `targetHandle` and legacy `target_handle`; tool choice remains entirely with the agent and no trigger-specific tool-use prompt is injected.

**Delegation input contract (input-vs-config separation).** The LLM's `{task, context}` args are per-invocation *input*, not node configuration, and travel as the child workflow input's `invocation` field. `prepare_agent_payload` applies it AFTER its config resolution (`{**node_data, **db_params}` — DB wins for config liveness): `task` → system_message, `context`-or-`task` → prompt — the same semantics as the legacy `handlers.tools._execute_delegated_agent`. Stored node parameters (including the empty default `prompt` the frontend persists on drop) therefore never override the delegated task. A call with both fields empty is rejected at the parent's call boundary (tool-error message to the LLM, no child spawn). Bypass agents dispatched as plain activities (any type outside `AGENT_WORKFLOW_TYPES`: `rlm_agent` / `claude_code_agent` / `vertex_managed_agent`) instead receive the remap directly in `node_data` — their per-type activity consumes `node_data` verbatim with no DB re-merge.

**Canvas-aware tools** opt into receiving the parent Workflow's `nodes`/`edges`
with `needs_canvas: ClassVar[bool] = True`. Version 1 records that declaration
with the resolved tool binding; legacy F4.B reads the plugin class at dispatch.
Opted-in tools receive the parent's canvas; others keep the empty-canvas
optimization. `agentBuilder.add_tool` compares the run's canvas with the saved
graph, so a saved tool missing from the generation snapshot returns a bind-only
`add_node` operation. Its calling agent is `invoking_agent_node_id`, else
`parent_node_id`. Operations reload the saved graph for in-run duplicate
detection; see the [agentBuilder card](./node-logic-flows/ai_tools/agentBuilder.md).

Version 1 also records each ordinary tool's timeout, heartbeat, retry, and
queue policy in `tool_info.activity_policy`. Both initial preparation and
explicit refresh produce these bindings; continue-as-new carries the resolved
set. Scheduling uses those recorded declarations, with specialized queue
routing only when the frozen worker-pool flag is on. A plugin update during
Stop or worker restart does not implicitly replace an already-bound policy.
Legacy histories keep their prior scheduling options, including the existing
Workspace-task policy patch.

`collect_agent_activities()` registers the agent activities — read the live set
from that function rather than trusting a count here, which drifts on every
addition. Seven are the core loop activities:

| Activity | Purpose |
|---|---|
| `agent.prepare_payload` | Resolves the DB-backed payload (provider / model / system_message / user_prompt / `AgentToolSpec`-derived tool definitions / memory_node_id / memory_content / memory_window_size / max_iterations / thinking_config / compaction_threshold / tool_result_max_chars / transcript_budget_bytes / context_pressure_version / auto_rebind_tools), and leaves credential resolution at the LLM activity boundary. Reads `UserSettings.agent_recursion_limit`, `auto_rebind_tools_after_canvas_change`, `tool_result_max_chars`, `max_concurrent_subagents` and `max_delegation_depth`. Recording the three transcript-pressure keys here is what lets a history recorded before them replay the original rules. Applies the optional `invocation` field (delegation children) after config resolution — per-invocation input always beats stored parameters. |
| `agent.execute_llm_step` | One LLM turn. The native branch decodes Message Wire V2, rebuilds `ToolDef` values, calls `run_native_llm_step(ChatUnifier, ...)` with SDK retries disabled, heartbeats while awaiting the provider, and returns the exact assistant message + tool calls + normalized usage. Guards against un-invokable payloads: post-filter system-only message lists raise `ApplicationError(type="EmptyAgentPrompt", non_retryable=True)`. |
| `agent.refresh_tools` | Translates `workflow_ops` add_node ops (`component_kind="tool"` OR `usable_as_tool=True`; never a chat model or the Skills node) into fresh `AgentToolSpec`-derived `tool_payload` entries via `_build_tool_from_node`. Workflow extends `tools` + `tool_index` from the result. |
| `agent.persist_turn` | Appends the latest human/assistant exchange to memory markdown, trims the window, broadcasts `node.parameters.updated`. |
| `agent.compact_context` | The shared client-side summarizer, run under transcript pressure (step 6 above). Not best-effort: after the activity's retries a failure ends the run with `CompactionError`, because past that point the transcript could only grow until the provider rejected it. |
| `agent.store_output` | Writes `output_main` / `output_top` / `output_0` so downstream nodes resolve `{{aiAgent.response}}` via `ParameterResolver`. |
| `agent.broadcast_progress` | Emits `WorkflowEvent.agent_progress` (CloudEvents v1.0) + optional raw-dict `update_node_status` for canvas-glow color. Single helper drives every phase emit. |

The other ten support skills and durable delegation (17 `agent.*` activities in total at the time of writing; none carry a `.v1` suffix — only `node.{type}.v{n}` and `poll.*` activities are versioned):

| Activity | Purpose |
|---|---|
| `agent.skill.invoke` | Executes one progressive skill action with a history-recorded, retry-safe result. |
| `agent.skill.clear` | Clears the agent's turn-scoped skill state. |
| `agent.begin_delegation` | Idempotently persists and claims a direct delegation before child startup. |
| `agent.queue_delegation` | Persists a team task before it waits for a root-wide concurrency permit. |
| `agent.cancel_delegation` | Cancels a queued/running delegated task and records the cancellation idempotently. |
| `agent.acquire_subagent_permit` | Heartbeats while polling the durable root coordinator for a subagent permit. |
| `agent.release_subagent_permit` | Idempotently releases a root-wide subagent permit. |
| `agent.register_task_execution` | Persists the runner and child Temporal identities for trace inspection. |
| `agent.finish_delegation` | Idempotently records a delegated child's terminal result and usage. |
| `agent.finalize_team` | Finalizes a team after its required tasks reach accepted or terminal states. |

**Context needs no dedicated activities.** The plain conversation store
(see [agent_context_flow.md](./agent_context_flow.md)) rides the existing
pipeline: `agent.prepare_payload` loads the stored conversation for
`(workflow_id, generation, agent_node_id)` and returns it with the
`conversation_key` (load failures are LOUD — `ConversationLoadFailed`
retryable / `ConversationTooLarge` non-retryable at 1 MB), and
`agent.execute_llm_step` saves `[...sent, assistant]` best-effort after each
provider call. A version 1 continue-as-new rollover carries the live transcript
and prepared state, so it does not reload changed configuration or reconstruct
the continuation from the conversation store. Legacy rollover behavior is
documented in [agent context flow](agent_context_flow.md#message-seeding-what-a-runs-initial-messages-list-is). The
journal-era activities (`agent.prepare_context`,
`agent.reconstruct_context_messages`, `agent.append_context`) are retired;
`agent.compact_context` (the shared client-side summarizer that bounds the
carried transcript) remains.
Tool calls scheduled by the workflow carry the model's unmerged `tool_args`
in the per-type activity payload so ToolNodes validate against their
`ToolInput` via `execute_as_tool` (see `tests/nodes/test_tool_call_dispatch.py`).

The F4.B compaction threshold is prepared from the model context length and
the ratio configuration, and is absent when the global `COMPACTION_ENABLED`
is off. It does not read `SessionTokenState.custom_threshold` or the
per-session `compaction_enabled`; those stored per-session controls are
therefore not authoritative for this path. The transcript's byte budget and
the tool-result cap apply whether or not compaction is on (see
[agent_context_flow.md → Transcript size](./agent_context_flow.md)).

**Broadcasts inside the loop** wrap `WorkflowEvent` (CloudEvents v1.0) per RFC §6.4: `agent_progress` events (`com.opencompany.agent.progress`) and `node_parameters_updated` events (`com.opencompany.node.parameters.updated`) flow through the `StatusBroadcaster.broadcast_agent_progress` and `StatusBroadcaster.broadcast_node_parameters_updated` wrappers respectively. The latter is reused by the legacy `routers/websocket.py:handle_save_node_parameters` (user-source) and `services/cli_agent/service.py:_persist_memory` (cli-source) — all three emission sites share the same envelope, distinguished by `source_hint` (`"user"` / `"cli"` / `"agent"`).

`rlm_agent`, `claude_code_agent` and `vertex_managed_agent` are NOT migrated (they are absent from `AGENT_WORKFLOW_TYPES`) — their internal session state (RLM REPL / Claude CLI `--resume` with stable `cwd` / Vertex Interactions API `previous_interaction_id` and environment chaining) requires single-process continuity and would break across activity boundaries.

References: [Temporal AI Cookbook](https://docs.temporal.io/ai-cookbook), [`temporal-community/temporal-ai-agent`](https://github.com/temporal-community/temporal-ai-agent), [`temporalio.contrib.openai_agents`](https://github.com/temporalio/sdk-python/tree/main/temporalio/contrib/openai_agents).

### Agent continuation under history pressure

`AgentWorkflow` rolls over at a clean completed-turn boundary, with no live
delegation handles or Task Manager tasks. Version 1 parks at that boundary
while stopped and waits for all control handlers to finish before
continue-as-new. The next input carries pause/revision/hold state, application
execution identity, transcript, accumulated thinking, iteration, usage,
context usage, prepared payload, and the resolved tool bindings. A rollover
changes Temporal's Run ID while preserving the application's execution scope.

The converter measures the **complete encoded continuation input**, including
graph, preparation, and bindings, against `_CAN_INPUT_MAX_BYTES` (1,900,000
bytes). Existing post-turn result relief and compaction run before this clean
boundary. If the argument still cannot fit, version 1 fails explicitly with
`AgentContinuationTooLarge`; it never returns to the opening prompt. A large
graph or binding set can overflow even when the transcript alone fits.
Legacy histories retain their previous preparation and 1 MB transcript-only
fallback for replay compatibility. The new capacity guarantee is limited to
version 1 generations.

The [Continue-As-New guidance](https://docs.temporal.io/design-patterns/continue-as-new)
motivates bounded histories and explicit carried state. Controller rollover
can still happen while stopped because it carries pending events and root
membership; the agent rule above specifically avoids rolling over a live or
suspended tool/delegation turn.

The focused tests use stubbed business Activities with real Temporal test
Workflows, including tool drain/continuation, worker restart, actual rollover,
and replay: [agent execution control](../server/tests/temporal/test_agent_execution_control_integration.py),
[generation topology](../server/tests/temporal/test_generation_execution_control_integration.py),
and [execution-control replay](../server/tests/temporal/test_execution_control_replay.py).
Plugin options and capacity errors are also covered by
[AgentWorkflow tests](../server/tests/temporal/test_agent_workflow.py).
These checks do not establish external-tool exactly-once effects or eliminate
the ingress and duplicate-trigger naming limits recorded in the
[control guide](temporal-workflow-control.md).

### Direct Workspace tasks

A node class that declares `workspace_task = True` (the phone nodes in `server/nodes/mobile/`) can also run a single task outside any graph run. The Workspace's **Ask AI to use the phone** box posts to `/api/mobile/{workflow_id}/{node_id}/tasks` (`server/nodes/mobile/_router.py`), and `services/node_invocations.py::submit` admits the task.

- **Admission.** Each OpenCompany workflow has one long-lived `WorkspaceTaskControllerWorkflow` (`services/temporal/workspace_tasks_workflow.py`, Temporal id from `node_invocations.controller_id`), created on first use through Update-With-Start. Its `submit` Update checks the admission epoch, bounds the number of active tasks, deduplicates a retried submission by its id and prompt fingerprint, and starts one `NodeInvocationWorkflow` child per task (`services/temporal/node_invocation.py`). The child runs the node's per-type activity once, with no retry, and records the run through `workflow_runs.record_completion`.
- **Reset.** The controller's `reset` Update first fences new submissions by advancing the epoch. It then cancels every admitted child and waits for each to close, and runs the `workspace_tasks.reset_runtime` activity (`services/temporal/workspace_task_activities.py::reset_workspace_task_runtime`). That activity cancels invocations started before the controller existed and calls each Workspace-task node's `reset_execution_state`. The Update returns only after that cleanup. If cleanup fails, the fence stays in place, and retrying Reset resumes the same request. The workflow toolbar's Reset reaches the controller through `services/deployment/handlers.py::_reset_workspace_tasks`, even for a workflow that was never started; see [temporal-workflow-control.md](./temporal-workflow-control.md#workspace-tasks).
- **Registration and rollover.** Both workflow classes are in `worker.py::_framework_workflows`, and every framework worker registers `reset_workspace_task_runtime`. The controller continues-as-new under history pressure, carrying its epoch and its bounded submission and reset records.
- **Inside graph and agent runs.** Two patch markers give Workspace-task nodes their own activity options. `workspace-node-cancellation-v1` (`MachinaWorkflow`) uses the node class's `start_to_close_timeout` and `heartbeat_timeout`, and makes cancellation wait until the activity has finished cleaning up. `workspace-task-activity-policy-v1` (tool calls in `AgentWorkflow`) does the same and also applies the class's `retry_policy` and, with the worker pool on, its `task_queue`.

What the user sees, including what Reset leaves on the phone, is in [docs/mobile-workspace.md](../docs/mobile-workspace.md#resetting-phone-tasks).

### Approved sends

A tool call that sends, made while the workflow asks first, is held as a draft (`services/approvals/tool_calls.py`)
instead of running. When the owner presses Send, `decide_approval` starts `ApprovedToolCallWorkflow`
(`services/temporal/approved_tool_call_workflow.py`, id `approval-send-<approval id>-r<revision>`, `payload_version` 1,
unsandboxed like the other framework workflows):

- it sleeps until the draft's Undo window closes (`grace_until`);
- `approvals.claim_send` moves the row from approved to sending, only at the revision the workflow was started for,
  with a claim token derived from the workflow run; an Undo, a newer Send or a cancel moved the row, and the
  workflow ends with nothing sent. A retried claim by the same run finds its own claim;
- it runs the node's own `node.<type>.v<n>` activity once (`maximum_attempts=1`), with the call's arguments over the
  node's settings; the activity runs only under that claim (`approval_execution` in its input);
- `approvals.record_outcome` records `sent`, `not_sent` (the node said it did not go) or `unknown` (the activity
  broke off, so it may have gone), and leaves the employee an `[update]` note for its next chat turn.

The bookkeeping activities retry until they land. Both are in every framework worker's activity list
(`services/approvals/activities.py::APPROVAL_ACTIVITIES`), and the workflow class is in `_framework_workflows`. A
send that never started (the server stopped between the decision and the start) is started again by the approvals
reconcile; one that never reported ends `failed` with an unknown outcome. See
[Chat Protocol, Approvals](./chat_protocol.md#approvals).

## Config Node Filtering

Certain nodes provide configuration rather than executing:

```python
# Config handles - nodes connecting via these are filtered out
# (services/temporal/workflow.py)
CONFIG_HANDLES = {
    "input-context",
    "input-tools",
    "input-memory",  # replay/import compatibility for legacy input-memory graphs only
    "input-model",
    "input-skill",
    "input-task",
    "input-teammates",
}

# Trigger node types - event listeners, never scheduled as blocking activities
# Authoritative list: server/constants.py WORKFLOW_TRIGGER_TYPES (frozenset;
# the copy below can fall behind it). Omitting a trigger there
# is a silent failure: find_trigger_nodes filters on this set, so deploy
# ignores the node.
WORKFLOW_TRIGGER_TYPES = frozenset([
    "start", "cronScheduler",
    "webhookTrigger", "whatsappReceive",
    "whatsappBusinessReceive", "whatsappBusinessStatus",
    "discordReceive", "discordInteraction",
    "workflowTrigger", "chatTrigger", "taskTrigger",
    "twitterReceive", "googleGmailReceive", "telegramReceive",
    "emailReceive", "msMailReceive",
])

# Android service types (connect directly to agent input-tools) -- authoritative list
# in server/constants.py ANDROID_SERVICE_NODE_TYPES.
```

Config nodes are:
- Filtered from the execution graph
- Their configuration is passed to target nodes via node_data
- Not scheduled as activities

Trigger nodes that aren't the firing trigger are:
- Auto-completed with `{not_triggered: True}` output
- Never scheduled as blocking activities (would wait indefinitely for events)
- Marked `_pre_executed` in deployment runs by `_execute_from_trigger()`

## Retry & Fault Tolerance

| Scenario | Behavior |
|----------|----------|
| Ordinary node or agent-support Activity fails transiently | The shared ordinary default is 3 attempts with backoff; declared plugin policies and specialized support policies take precedence. |
| `AgentWorkflow` LLM-step activity fails | `LLM_STEP_RETRY` retries without limit (`maximum_attempts=0`, 5 s → 5 min backoff; `services/temporal/_retry_policies.py`) unless the provider error category is non-retryable (invalid_request, authentication, ...). Provider SDK retries stay disabled so Temporal owns the retry schedule and `retry_after` hints. |
| Worker crashes mid-execution | Workflow state replays from history; an unfinished Activity retries according to its timeout/retry policy on an eligible worker. Completed recorded work is not explicitly rescheduled by Resume. |
| A node/tool Activity times out | Temporal applies the scheduled retry policy. Graph timeout/heartbeat defaults and version 1 agent-tool/plugin options differ as described above. |
| All retries exhausted | Workflow receives failure, stops execution |

## File Structure

```
server/services/temporal/
├── __init__.py          # Exports TemporalExecutor, TemporalClientWrapper
├── activities.py        # NodeExecutionActivities class (legacy WebSocket round-trip path)
│   ├── execute_node_activity()   # Main activity method
│   └── _execute_via_websocket()  # WebSocket execution
├── plugin_activities.py # collect_plugin_activities() -> per-type node.{type}.v{ver} activities (F4.A)
├── agent_workflow.py    # AgentWorkflow loop + detached DelegatedTaskWorkflow
├── agent_activities.py  # collect_agent_activities() -> the agent.* activities (F4.B)
├── workflow.py          # MachinaWorkflow class
│   ├── run()                     # Main orchestrator
│   ├── _filter_executable_graph() # Config node filtering
│   ├── _build_dependency_maps()   # Graph analysis
│   ├── _find_ready_nodes()        # Dependency resolution
│   └── _wait_any_complete()       # FIRST_COMPLETED wait
├── workflow_control_workflow.py  # WorkflowControlWorkflow (per-generation controller: triggers, signals, polling, pause)
├── execution_control.py         # Revision-ordered business admission and checkpoint accounting
├── execution_control_activities.py # Client-side Update bridge for independent-root enrollment
├── trigger_listener_workflow.py  # TriggerListenerWorkflow (legacy push-trigger listener)
├── polling_trigger_workflow.py   # PollingTriggerWorkflow (legacy polling listener)
├── workspace_tasks_workflow.py   # WorkspaceTaskControllerWorkflow (per-workflow admission + Reset for direct Workspace tasks)
├── node_invocation.py            # NodeInvocationWorkflow (one direct Workspace task)
├── approved_tool_call_workflow.py # ApprovedToolCallWorkflow (sends a held tool call once the owner approved it)
├── workspace_task_activities.py  # reset_workspace_task_runtime (activity workspace_tasks.reset_runtime)
├── schedules.py         # Temporal Schedule creation for cronScheduler
├── search_attributes.py # EVENT_SEARCH_ATTRIBUTES (7 custom SAs, incl. ControlEventTypes)
├── _retry_policies.py   # DEFAULT_ACTIVITY_RETRY / QUICK_ACTIVITY_RETRY / LLM_STEP_RETRY / PERMIT_WAIT_RETRY / ...
├── _interceptors.py     # ObservabilityWorkerInterceptor (activity_retry WARN, workflow_start logs)
├── plugin_registry.py   # Temporal plugin registry
├── worker.py            # TemporalWorkerManager + TemporalWorkerPool
│   ├── start()                   # Start embedded worker
│   ├── stop()                    # Cleanup
│   └── run_standalone_worker()   # For horizontal scaling
├── lifecycle.py         # run_temporal_lifecycle (dev-server supervision, connect loop, wiring, watchdog)
├── _runtime.py / _install.py / _handlers.py / _refresh.py  # dev-server supervisor, pooch installer, WS + refresh hooks
├── executor.py          # TemporalExecutor entry point
├── ws_client.py         # WebSocket connection pool for the legacy activity path
└── client.py            # TemporalClientWrapper (runtime heartbeat disabled)
```

Read the live module list from the directory; the tree above is a map, not an inventory.

## Implementation Notes

### Worker Registration (Critical)

For class-based activities, pass the **bound method**:

```python
# WRONG - causes "Activity <unknown> missing attributes"
activities=[self._activities]

# CORRECT - pass the bound method
activities=[self._activities.execute_node_activity]
```

### Activity Invocation (Critical)

When using class-based activities, invoke by **string name**:

```python
# WRONG - works only with standalone function activities
workflow.start_activity(execute_node_activity, args=[context])

# CORRECT - use string name for class-based activities
workflow.start_activity("execute_node_activity", args=[context])
```

### Runtime Configuration

Worker heartbeating is disabled to avoid warnings on older Temporal server versions:

```python
runtime = Runtime(
    telemetry=TelemetryConfig(),
    worker_heartbeat_interval=None,  # Disable runtime heartbeating
)
client = await Client.connect(server_address, namespace=namespace, runtime=runtime)
```

## Server Management

The Temporal binary + persistence are managed in-process by the plugin-folder pattern at [`server/services/temporal/`](../server/services/temporal/). Single supervised process — the official `temporal` CLI's `server start-dev` mode, per [docs.temporal.io/develop/python/set-up-your-local-python](https://docs.temporal.io/develop/python/set-up-your-local-python). The server-management sibling files (`_install.py`, `_runtime.py`, `_handlers.py`, `_refresh.py`, `lifecycle.py`, `client.py`, plus `__init__.py` for registry wiring) match the [Wave 11 plugin-folder pattern](./plugin_system.md#self-contained-plugin-folders) that `server/nodes/whatsapp/` uses for its Go binary; the rest of the package (see the file structure above) is the execution engine itself.

**What runs**: one process — `temporal server start-dev --port $TEMPORAL_FRONTEND_GRPC_PORT --ui-port $TEMPORAL_UI_PORT --db-filename ~/.opencompany/temporal.db --namespace default`. Both gRPC + Web UI bind to the same process (per docs.temporal.io/cli/server — "all running in a single process"). SQLite-backed durability; history persisted across restarts.

**Modern libs doing the heavy lifting** (zero custom infrastructure code):
- **[`pooch`](https://pypi.org/project/pooch/)** — `services/temporal/_install.py` downloads the official `temporal` CLI archive from `https://temporal.download/cli/archive/latest?platform=<os>&arch=<arch>`. Cross-platform (Windows zip / macOS+Linux tar.gz), cached at `<DATA_DIR>/packages/temporal/` via `_cache_dir() = core.paths.package_dir("temporal")` (pooch's `path=` argument). The retrieve call passes an explicit `downloader=pooch.HTTPDownloader(timeout=300, progressbar=True)`: the timeout is per-socket-read (not total transfer), so arbitrarily slow links can finish the ~114 MB fetch — pooch's 30 s default aborted them — and `progressbar` must live on the downloader because `retrieve()` ignores its own kwarg when `downloader=` is explicit. Failed downloads never poison the cache (pooch writes to a temp file and renames atomically on success). Standalone entry: `python -m services.temporal._install` — invoked **fatally** by `company build` step [6/6] (so first `company start` doesn't pay the download; contract locked by `test_temporal_install_is_fatal_on_failure`) and **non-fatally** by npm postinstall (`scripts/install.js` try/catch — `TemporalServerRuntime._pre_spawn()` re-downloads lazily on first `company start`, so a failed eager fetch never fails `npm install -g`). The binary cache survives `company clean` (`packages` is in `_OPENCOMPANY_KEEP`, `cli/commands/clean.py`). Pre-fix this used `pooch.os_cache("opencompany-temporal")` (`~/.cache/OpenCompany/opencompany-temporal/` etc.) — a separate OS-cache namespace operators reported as "not local"; it now sits under DATA_DIR alongside the Stripe binary and the shared npm tree.
- **`BaseProcessSupervisor` + `BaseSupervisor`** (`server/services/_supervisor/`) — the in-house supervisor base classes that `server/nodes/whatsapp/_runtime.py` also uses. Provides cross-platform signal handling (POSIX `setsid` + Windows Job Objects + `CREATE_NEW_PROCESS_GROUP` for graceful `CTRL_BREAK_EVENT` shutdown), restart policy via tenacity, log draining, status snapshots. We subclass both — zero custom supervisor logic.

**Lifecycle wiring (July 2026)**: backend-owned, and modular — the whole Temporal runtime story lives in [`services/temporal/lifecycle.py`](../server/services/temporal/lifecycle.py) (`run_temporal_lifecycle`); `main.py` only schedules it as one background task. The module owns: (1) dev-server supervision via `TemporalServerRuntime.ensure_started()` before each connect attempt — a TCP probe on the gRPC port skips the spawn when a server (previously spawned or externally managed) is already listening, and non-loopback `TEMPORAL_SERVER_ADDRESS` values are never spawned at; (2) the forever-retrying connect loop; (3) the config-gated startup sweep; (4) executor + worker manager + worker pool wiring; (5) the boot-time workflow-control reconcile (`services.deployment.handlers.reconcile_active_controls_on_boot`); and (6) a **resident dev-server watchdog** — after startup the task stays alive, probing every `TEMPORAL_HEALTH_MONITOR_INTERVAL_SECONDS` and respawning a dev-server child that died (or restarting a supervisor-owned child that wedged: port bound but gRPC not SERVING for 4 consecutive probes). The runtime registers with `services._supervisor.register_supervisor` so `shutdown_all_supervisors()` actually stops it at teardown. The CLI no longer supervises Temporal: the `_temporal_specs.py` command helpers and the `_supervised_runtime.py` shim were deleted (the shim was a full second server-venv Python process, ~81 MB resident, plus a ~28 MB resident `uv run` parent — pure supervision overhead).

**Worker crash-restart (months-long durability)**: the SDK `Worker` is single-use — a second `run()` on the same instance raises `RuntimeError("Already started")`. Both restart loops therefore REBUILD a fresh worker per attempt: `TemporalWorkerManager._run_worker` via `_build_worker()`, and every `TemporalWorkerPool` queue worker runs under `_run_queue_worker` (previously a bare `worker.run()` task — one crash silently killed the queue's only worker and its activities pended forever). Backoff knobs: `TEMPORAL_WORKER_RESTART_BACKOFF_SECONDS` / `_MAX_SECONDS`. Locked by `tests/temporal/test_worker_restart.py`.

**WS surface**: `_handlers.py` registers `temporal_status` / `temporal_start` / `temporal_stop` via `services.ws_handler_registry.register_ws_handlers`. `_refresh.py` registers a status-refresh callback via `services.status_broadcaster.register_service_refresh`, which seeds the FE health indicator once at startup (refresh callbacks run once, in a background task, not on each WebSocket connect).

**Months-long durability contract**: running and paused deployments retain their
Temporal histories across backend restarts. New child starts have no Workflow
lifetime cap; individual Activities retain their declared attempt timeouts and
retry limits. History pressure triggers continuation rather than a periodic
restart. Existing executions can still have timers recorded by an older start;
removing a timeout from new start options does not erase those timers. The
poll-interval floor is unconditional, while controller queue and execution
control changes use the version gates below. The strongest Stop/Resume
guarantee applies to version 1 generations with their controller chain intact.

- **No lifetime caps on new child runs.** Trigger/cron-spawned `MachinaWorkflow` runs, agent children, and delegated-task runners previously carried 1-2h `execution_timeout`/`run_timeout` — Temporal's timeout timers keep ticking through a cooperative pause, so any pause longer than the cap silently terminated the run (and a timed-out delegated runner skipped its compensation: leaked permit + stuck task row). New executions start children unbounded (no patch marker was retained for this change). The live `workflow.patched` markers are `machina-conditional-edges-v1`, `machina-run-record-v1`, `machina-chat-run-v1`, `workspace-node-cancellation-v1` and `workflow-node-user-action-error-v1` in `services/temporal/workflow.py`, `machina-trigger-listener-node-filter` in `services/temporal/trigger_listener_workflow.py`, and `workspace-task-activity-policy-v1`, `agent-user-action-error-v1` and `agent-blocked-tool-turn-save-v1` in `services/temporal/agent_workflow.py`; the list grows, so check it with `grep -rn "workflow.patched(" server/services/temporal`. `machina-run-record-v1` gates the `workflow_runs.record_completion` activity that trigger-spawned runs schedule when they finish, for Normal mode's "done today" (see [normal_mode.md](./normal_mode.md#done-today)). `machina-chat-run-v1` gates the `chat_run.start` / `chat_run.finish` activities a run spawned by the owner's chat message schedules to claim and finish its chat run; the run id is read only from an event whose source is `opencompany://services/chat` (see [chat_protocol.md](./chat_protocol.md#runs)). `workflow-node-user-action-error-v1` gates `MachinaWorkflow._node_failure`: when a node's activity or child workflow raises, it follows the error's causes to an `LLMError.*` or `MissingAgentProviderCredential` failure whose details carry `requires_user_action` (a missing or rejected key, billing, quota, permission, an unknown model or a wrong base URL) and returns a `NodeUserError` result with that message, its `hint` and `retryable: false` instead of the bare error text, so `chat_run.finish` records the hint and `workflow_control.pause_on_failure` pauses the deployment on that first failure rather than counting it toward the threshold. `agent-user-action-error-v1` gates the matching stop in `AgentWorkflow`: a tool result (a delegated agent's included) with an `error` and `requires_user_action` ends the run after that tool turn, before another model request or compaction, and the agent clears its active skills, emits `failed` and returns the error as a `NodeUserError`. `agent-blocked-tool-turn-save-v1` gates the `agent.persist_turn` activity that this stop runs first when the agent has a conversation key (a Context node in a started generation): it appends the turn's tool results to the stored conversation (`append_tool_results`), so the conversation does not end on unanswered tool calls. The two `workspace-*` markers are described under [Direct Workspace tasks](#direct-workspace-tasks). Liveness is the activity layer's job: node activities heartbeat every 30s against a 2-minute `heartbeat_timeout`; their `start_to_close` is a generous 24h ceiling, not 10 minutes. The subagent-permit wait uses `PERMIT_WAIT_RETRY` (unlimited attempts) so a queued delegation waits as long as admission takes instead of failing after ~3h.
- **History-pressure continue-as-new everywhere.** Temporal terminates any workflow around ~51,200 history events. `WorkflowControlWorkflow` (which multiplexes all of a deployment's triggers into one history) now rolls over on `is_continue_as_new_suggested()` / a 10K-event soft cap, carrying trigger specs, per-trigger provider `seen_ids` (written back into the spec after every poll cycle), queued push events, the bounded dedup baseline, and the control state — a rollover works mid-pause too, since a paused controller still accretes signal history. `TriggerListenerWorkflow`/`PollingTriggerWorkflow` gained the same pressure check (the old `_processed_count >= 16_000` gate was unreachable: real spawns cost ~15-25 events each, and polling counted only emitted events while a quiet mailbox burned ~11 events/cycle — dead in ~3 days at the 60s default). Poll intervals are clamped to a 30s floor (`_MIN_POLL_INTERVAL_S` in `polling_trigger_workflow.py` and `workflow_control_workflow.py`), with no patch gate. Because run ids change on rollover, **controller handles are addressed by workflow id only, never run_id-pinned** (`_controller_handle`, manager `register_trigger`).
- **dispatch.emit controller narrowing.** Controllers advertise their push event types via the `ControlEventTypes` keyword-list Search Attribute (upserted as triggers register); `dispatch.emit` skips controllers with no matching trigger instead of signalling every running controller with every platform event (each unmatched signal was ~4 immutable history events — one busy deployment burned every other controller's rollover budget). Controllers without the attribute (pre-upgrade histories) keep match-all behaviour.
- **Boot-time reconcile** (`reconcile_active_controls_on_boot`, called from the lifecycle module after workers start): runs the lazy `_reconcile_control` over every active control row, converges `starting` rows a crash left behind (controller alive with triggers registered → `running`; alive-but-empty for a graph that declares triggers → `failed` + controller closed; vanished → `failed`), and re-arms the process-local half of running/paused generations from the persisted graph snapshot — DeploymentManager state, in-process collectors for non-canary trigger types, cron pause posture. Idempotent by construction (controller `register_trigger` keyed by listener id, legacy starts use `USE_EXISTING`, cron creation preserves server-owned pause state).

**Controller event accumulation.** `controller-durable-queue-v2` preserves FIFO across carried events and the existing durable spillway. New execution-control version 1 histories also record `controller-event-accumulator-v1`: pending IDs remain protected beyond the recent-ID window, rollover includes Signals received during spill Activities, and a duplicate deterministic child start cannot block subsequent events. Legacy and pre-marker histories retain their recorded paths. Trigger processing stays immediate; no inactivity batching is added. See [event accumulation during Stop and rollover](./temporal-workflow-control.md#event-accumulation-during-stop-and-rollover) for scope and delivery limits.

**Controller Update traffic.** Fresh version 1 histories record `controller-messaging-v1` so successful Updates can request history-pressure rollover even without event Signals or pollers. The same gate prevents producer-only Safe Apply compatibility messages from reopening a stopped generation. Pure Update validators reject malformed control requests before acceptance; the main loop waits for handlers before rollover. See [workflow messaging review](./temporal-workflow-control.md#workflow-messaging-review).

**Startup sweep (debug-only escape hatch)**: [`TemporalClientWrapper.terminate_running_workflows`](../server/services/temporal/client.py) — gated on `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP` (default **`false`**; keep it false — setting `true` converts every boot into a namespace-wide terminate sweep). Even when enabled, any control row in an active state (the shared `WORKFLOW_CONTROL_ACTIVE_STATES`, which includes `resetting`) vetoes the sweep. History is preserved (UI shows workflows as `Terminated`, not deleted); only active execution stops.

**Port management**: Temporal owns `TEMPORAL_FRONTEND_GRPC_PORT` (gRPC) + `TEMPORAL_UI_PORT` (Web UI). Both bound by the same `temporal.exe` process; both listed in `cli.config.Config.all_ports` so `company stop`'s port-freeing pre-flight covers them.

**Direct CLI access**: the pooch-installed `temporal` binary lives under `<DATA_DIR>/packages/temporal/` (= `~/.opencompany/packages/temporal/` by default, on every OS). Run `temporal --version`, `temporal workflow list`, etc. directly from there.

**Cluster tunables** — all sourced from `.env.template` (canonical defaults; no Python-side fallbacks). Settings fields with `Field(env="...")` require the env var to be present, surfaced via `cli.config.load_config()`'s `.env.template` → `.env` → `os.environ` merge.

| Setting | Env var | `.env.template` default | Purpose |
|---|---|---|---|
| `temporal_enabled` | `TEMPORAL_ENABLED` | `true` | Master toggle. When false, `WorkflowService` falls back to the sequential executor. |
| `temporal_server_address` | `TEMPORAL_SERVER_ADDRESS` | `localhost:<TEMPORAL_FRONTEND_GRPC_PORT>` | Address the Python SDK client connects to. |
| `temporal_namespace` | `TEMPORAL_NAMESPACE` | `default` | Bootstrapped at server start. |
| `temporal_task_queue` | `TEMPORAL_TASK_QUEUE` | `machina-tasks` | Default task queue for the embedded worker. |
| `temporal_per_type_dispatch` | `TEMPORAL_PER_TYPE_DISPATCH` | `true` | F4.A flag — per-type activity dispatch. |
| `temporal_agent_workflow_enabled` | `TEMPORAL_AGENT_WORKFLOW_ENABLED` | `true` | F4.B flag — agent-as-child-workflow. |
| `temporal_frontend_grpc_port` | `TEMPORAL_FRONTEND_GRPC_PORT` | see `.env.template` | gRPC port (`--port`). Drives the readiness probe. |
| `temporal_ui_port` | `TEMPORAL_UI_PORT` | see `.env.template` | Web UI port (`--ui-port`). CLI default is `--port + 1000`; pinned into the serial block that starts at `PYTHON_BACKEND_PORT` instead. |
| `temporal_sqlite_path` | `TEMPORAL_SQLITE_PATH` | `temporal.db` | SQLite file (`--db-filename`). Resolved relative to `DATA_DIR` (= `~/.opencompany/`) unless absolute — flat under `~/.opencompany/` like `credentials.db` / `workflow.db`. |
| `temporal_graceful_shutdown_seconds` | `TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS` | `30` | `CTRL_BREAK_EVENT` (Windows) / `SIGTERM` (POSIX) → tree-kill grace window. Shared with the embedded worker shutdown. |
| `temporal_terminate_running_on_startup` | `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP` | `false` | Debug-only startup sweep (see the durability contract above). Keep false so running and paused deployments survive restarts. |
| `temporal_health_monitor_interval_seconds` | `TEMPORAL_HEALTH_MONITOR_INTERVAL_SECONDS` | `15` | Resident dev-server watchdog probe cadence (lifecycle module; loopback deployments only). |
| `temporal_health_check_attempts` | `TEMPORAL_HEALTH_CHECK_ATTEMPTS` | `5` | Startup readiness gate: how many times to poll the WorkflowService gRPC health check for SERVING (after the gRPC port binds) before the worker / visibility sweep act. Bounded — the unbounded layer is the lifecycle reconnect loop. Carries a matching Python default so a pre-existing `.env` keeps booting. |
| `temporal_health_check_delay_seconds` | `TEMPORAL_HEALTH_CHECK_DELAY_SECONDS` | `0.5` | Delay between readiness-probe attempts. |
| `temporal_health_check_timeout_seconds` | `TEMPORAL_HEALTH_CHECK_TIMEOUT_SECONDS` | `2.0` | Per-attempt timeout for one gRPC health-check call. |
| `temporal_sweep_attempts` | `TEMPORAL_SWEEP_ATTEMPTS` | `4` | Boot-time terminate-running sweep: retries for the Visibility query that races shard acquisition ("shard status unknown") before giving up for that boot. Only relevant when `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP=true`. |
| `temporal_sweep_backoff_seconds` | `TEMPORAL_SWEEP_BACKOFF_SECONDS` | `0.5` | Linear backoff base for the sweep retries (`attempt x base`). |
| `workflow_control_crash_recovery` | `WORKFLOW_CONTROL_CRASH_RECOVERY` | `pause` | After an UNCLEAN shutdown (kill/crash, dirty-bit marker), boot pauses generations still `running` so the user consciously resumes; `resume` restores them running. Clean restarts always restore as-is. |
| `workflow_control_missing_controller` | `WORKFLOW_CONTROL_MISSING_CONTROLLER` | `pause` | Legacy generations can converge to `paused` and rebuild on Resume; `fail` keeps Reset-only recovery. Version 1 fails closed when its controller is lost because an empty replacement cannot recover authoritative root membership. |
| `workflow_control_pause_on_failure` | `WORKFLOW_CONTROL_PAUSE_ON_FAILURE` | `true` | Circuit breaker: repeatedly-failing trigger-spawned runs pause their deployment (fix + Resume) instead of firing into the same error indefinitely. Evaluated activity-side; never touches recorded commands. |
| `workflow_control_pause_on_failure_threshold` | `WORKFLOW_CONTROL_PAUSE_ON_FAILURE_THRESHOLD` | `3` | Failed runs inside the rolling window required to trip the breaker — one node hiccup never pauses a deployment. `1` = pause on the first failure. Resume resets the streak. |
| `workflow_control_pause_on_failure_window_seconds` | `WORKFLOW_CONTROL_PAUSE_ON_FAILURE_WINDOW_SECONDS` | `600` | Rolling window for the failure streak; older failures age out (streak state lives in the cache table with a matching TTL). |

Full recovery-policy semantics: [temporal-workflow-control.md → Recovery policies](./temporal-workflow-control.md#recovery-policies).

The legacy `TEMPORAL_SERVER_READY_TIMEOUT_SECONDS` knob (CLI-supervised-era readiness wait) was removed — it had no consumer since the backend-owned cutover. The temporary agent-engine cutover variable that was read directly rather than through `Settings` is gone too, and nothing replaced it: there is one engine, and no engine selector is recorded per execution (see the `agent.prepare_payload` note above).

## Debugging

The Web UI is at `http://localhost:<TEMPORAL_UI_PORT>`; the UI's HTTP API rides the gRPC port + 1000.

### Reading Temporal tracing output

The compact OpenTelemetry formatter intentionally places one completed span on
one line. Several lines for a single node are normal because they describe
different layers of the same execution:

| Span or log | Layer and interpretation |
|---|---|
| `StartActivity:<activity>` | Workflow-side scheduling span; propagates trace context but does not execute the node body |
| `RunActivity:<activity>` | Worker-side invocation span covering the activity attempt |
| `node.<type>.execute` | OpenCompany application span around the plugin's `execute()` body |
| `CompleteWorkflow:AgentWorkflow` | Agent child workflow reached a terminal state |
| `CompleteWorkflow:MachinaWorkflow` | Parent graph workflow reached a terminal state |

`CompleteWorkflow ... 0ms` is expected. Temporal creates the completion span at
one replay-safe workflow timestamp, so its duration is not the workflow's
wall-clock runtime. An `AgentWorkflow` completion and a `MachinaWorkflow`
completion are also separate parent/child lifecycle events.

Exact adjacent pairs are not expected. If `StartActivity`, `RunActivity`, or
`CompleteWorkflow` appears twice with the same operation name,
`temporalWorkflowID`, `temporalRunID`, and (when present)
`temporalActivityID`, inspect interceptor ownership before investigating task
retries. The shared client already carries `TracingInterceptor`, and the SDK
prepends it to workers; adding a second instance to `Worker(...,
interceptors=...)` creates two nested spans with the same Temporal attributes.
The compact formatter does not print OpenTelemetry span IDs, which makes those
distinct spans look byte-for-byte identical.

Verification checklist:

1. `Client.connect(...)` contains one `TracingInterceptor`.
2. Manager, pool, standalone, and test worker constructors do not add another;
   their explicit list contains `ObservabilityWorkerInterceptor` and any
   distinct plugin interceptors only.
3. Temporal Event History contains one activity attempt unless a real retry
   occurred.
4. The application log contains one `node.<type>.execute` span for one node-body
   invocation.

After correcting duplicate instrumentation, one `StartActivity`, one
`RunActivity`, and one application node span may still appear for the same
activity. Those are expected scheduling, worker, and application layers—not
duplicate execution.

```bash
# Temporal Web UI
open http://localhost:$TEMPORAL_UI_PORT

# List workflows via the local CLI binary (under <DATA_DIR>/packages/temporal/)
~/.opencompany/packages/temporal/.../temporal.exe workflow list --address localhost:$TEMPORAL_FRONTEND_GRPC_PORT

# Re-fetch the binary at any time
uv run python -m services.temporal._install
```
