# Temporal Distributed Node Execution Architecture

> **Canonical system design:** See
> [Temporal Execution Engine RFC](temporal-execution-engine-rfc.md) for the
> current control-generation, trigger-hub, agent-team, pause/reset, trace, and
> security architecture. This document remains the detailed node dispatch and
> activity/worker inventory.

## Overview

Each workflow node executes as a **Temporal activity** with its own isolated context, enabling horizontal scaling across distributed workers. The orchestrator dispatches in one of three ways depending on settings flags:

| Dispatch | Trigger | Use case |
|---|---|---|
| **Legacy single activity** (`execute_node_activity`) | `TEMPORAL_PER_TYPE_DISPATCH=false` | Every node routed through one dispatcher activity. WebSocket round-trip back to the FastAPI server. Stable since Wave 11; kept as the fallback path. |
| **Per-type activity** (`node.{type}.v{version}`) | `TEMPORAL_PER_TYPE_DISPATCH=true` (production default) | Each plugin gets its own `@activity.defn`. Per-plugin retry / timeout / heartbeat configs apply. With `TEMPORAL_WORKER_POOL_ENABLED=true` (default since Wave 16.4) the activity also carries `task_queue=cls.task_queue`, landing it on its specialised `TemporalWorkerPool` worker (browser / code-exec / ai-heavy / ...). Shipped in F4.A (commit `8261b05`); queue routing activated in Wave 16. |
| **Agent-as-child-workflow** (`AgentWorkflow`) | `TEMPORAL_AGENT_WORKFLOW_ENABLED=true` | AI Agents (aiAgent, chatAgent, 11 specialized agents, 2 team leads) run as Temporal child workflows. Each LLM turn = activity; each tool call = per-type activity. Mirrors Temporal's AI Cookbook canonical pattern. F4.B infrastructure shipped (commit `a4d009e`); per-agent migrations follow. |

`rlm_agent`, `claude_code_agent` are intentionally excluded from AgentWorkflow — their externalised loops (RLM REPL / Claude CLI `--resume`) require single-process state continuity.

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

The embedded Temporal worker runs **inside the Python backend** — registered in the `main.py` lifespan via `TemporalWorkerManager`, not as a separate process.

**Standalone worker** (for horizontal scaling — add more pollers against the same task queue):

```bash
cd server
python -m services.temporal.worker
```

This invokes `run_standalone_worker()` from `services/temporal/worker.py`.

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

### 1. Node = Independent Activity

Each node runs as a separate Temporal activity with:
- **Own context** - No shared mutable state between nodes
- **Own retry policy** - Failed nodes retry independently (up to 3 attempts)
- **Own timeout** - Long AI nodes don't block short nodes (`_NODE_ACTIVITY_START_TO_CLOSE` = 24 h ceiling in `services/temporal/workflow.py`; liveness comes from the 2-minute heartbeat, not the start-to-close cap)
- **Own worker** - Can execute on any available worker in the cluster

### 2. Workflow = Pure Orchestrator

The workflow ONLY orchestrates:
- Parses the graph structure from React Flow nodes/edges
- Filters out config nodes (tools, memory, model configs)
- Determines execution order based on dependencies
- Schedules activities using FIRST_COMPLETED pattern
- Collects results and routes outputs to dependent nodes

**NO business logic in workflow** - all execution happens in activities.

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

        # F4.A: resolve to per-type name + queue when the flag is on,
        # else fall back to the legacy single dispatcher.
        activity_name, activity_queue = self._resolve_activity(node_type)
        start_kwargs = dict(
            args=[context],
            activity_id=node_id,
            # Heartbeat is the liveness mechanism; start_to_close only
            # bounds a single legitimate step (24h; pre-patch histories
            # replay against the old 10min cap).
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
    completed.add(done_id)
    outputs[done_id] = result
```

### 3. Activity executes the node

**Legacy path** (`execute_node_activity`): The activity round-trips through the local WebSocket back to FastAPI, which dispatches to the plugin handler. This was the only path before F4.A.

**Per-type path** (`node.{type}.v{version}`, F4.A): The activity body lives on the plugin class via `BaseNode.as_activity()` and calls `workflow_service.execute_node(...)` **directly** — no WebSocket round-trip. Same DI container (the worker shares the FastAPI process), same broadcasting + parameter-fetch pipeline. Each plugin class declares its own `start_to_close_timeout` / `retry_policy` / `heartbeat_timeout` so they're applied at activity definition time. See `server/services/plugin/base.py:as_activity`.

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

`TemporalWorkerPool` is wired into the `main.py` lifespan (starts right after `TemporalWorkerManager`, stops before it). One activity-only Worker per plugin-declared queue polls its specialised queue, and `MachinaWorkflow._resolve_activity` returns `(activity_name, cls.task_queue)` so per-type activities land there. Setting `TEMPORAL_WORKER_POOL_ENABLED=false` stops the pool and routes every activity back to the single manager worker on `machina-tasks` — **the flag is the rollback channel** (locked default-on by `test_task_queue_coverage.py::TestWorkerPoolDefaultOn`).

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
- **Application observability interceptor** (Wave 17.3, `services/temporal/_interceptors.py`): workers explicitly register `ObservabilityWorkerInterceptor` for `activity_retry` WARN logs when `activity.info().attempt > 1` — the "worker died and Temporal re-dispatched" signal — and replay-guarded `workflow_start` logs. This is separate from SDK tracing and remains worker-owned. Since plugin failures raise typed `ApplicationError`s at the activity boundary, `attempt > 1` also means a retryable plugin failure was re-dispatched; the `executing` node-status broadcast carries `attempt` / `max_attempts` to disambiguate, the failure log line reports the plugin's `error_type` and `non_retryable` instead of the uniform wrapper class, and a terminal user-correctable failure (`NodeUserError`, validation, credential, output-contract) logs `activity_end` at INFO because the plugin already emitted its one WARN line.
- **Periodic activity heartbeat** (Wave 17.6, `plugin/base.py::as_activity`): 30s background beat during long bodies so a laptop-sleep crash is detected within one `heartbeat_timeout` (2 min) instead of `start_to_close` (24 h).
- **Cron catch-up bound** (Wave 17.1, `services/temporal/schedules.py`): `SchedulePolicy(catchup_window=24h)` + `SKIP` overlap — a laptop offline a week does not replay 168 hourly ticks on wake.
- **LLM-step retry** (Wave 17.2, since reverted): `LLM_STEP_RETRY` on `agent.execute_llm_step` is now **unlimited** (`maximum_attempts=0`, 5 s → 5 min exponential backoff) — see the docstring in `services/temporal/_retry_policies.py`. The one-shot policy Wave 17.2 shipped meant one transient provider error killed a months-long deployment and tripped the circuit breaker; terminal outcomes still fail fast because `_as_temporal_llm_error` marks non-retryable categories (invalid_request, authentication, ...) with `non_retryable=True`, and provider `retry_after` hints ride `ApplicationError.next_retry_delay`. Accepted trade-off: an ambiguous loss can re-bill a prompt. `agent.refresh_tools` keeps the 3-attempt default (idempotent canvas rebuild).

**Metric watchlist** (Temporal Web UI / metrics endpoint): `schedule_to_start_latency` (elevated = raise pollers or slots), `worker_task_slots_available` (0 = raise concurrency or add hosts), `poll_success_rate` (target >= 90%), `sticky_cache_evictions` (persistent growth = raise `max_cached_workflows`).

**Tuning order** (per the worker-performance docs): host provisioning -> executor slots -> poller counts -> rate limits.

### Agent-as-child-workflow (F4.B)

When `TEMPORAL_AGENT_WORKFLOW_ENABLED=true` and the node type is in the migrating set (`aiAgent` / `chatAgent` / 11 specialized agents / 2 team leads), the orchestrator schedules `AgentWorkflow` as a child workflow instead of an activity. Inside the workflow:

```
AgentWorkflow.run(context):
  0. execute_activity("agent.prepare_payload")
       resolves the DB-backed payload from the canvas context — runs
       workflow_service._param_resolver.resolve so {{node.field}}
       templates in prompt / system_message become real values BEFORE
       the LLM sees them. Temporal workflows must be deterministic,
       so DB lookups + edge walking + tool schema build live here.
       _build_tool_from_node produces AgentToolSpec values. Tool entries
       carry the serialized ToolDef declaration plus routing tool_info;
       native LLM steps consume the definition directly.
       Records llm_engine="native" + message_wire_version=2.
  emit_phase("starting", status="executing")
  loop until "final" or max_iterations:
    1. emit_phase("llm_step", iteration=N)
    2. execute_activity("agent.execute_llm_step")
         returns {kind, assistant_message, calls?, content?, usage}.
         assistant_message is MessageWireV2: ordered text/reasoning/tool
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
               id = f"{parent_workflow_id}-delegate-{child_node_id}-{iter}"
           else:
             execute_activity(f"node.{tool_node_type}.v{version}")
           emit_phase("tool_completed", tool_name=...)
           _serialise_tool_result unwraps F4.A's {success, result, ...}
           envelope so the LLM sees only the handler's return value
           (matches the in-process tool-call serialisation in
           services/agent_runtime.py:run_native_agent_loop).
    4. for each tool result with an ``operations`` field
       (canvas-mutating tools — today only ``agentBuilder``):
         if payload["auto_rebind_tools"] is True:
           execute_activity("agent.refresh_tools", {operations: …})
              translates add_node ops with component_kind=="tool" OR
              usable_as_tool=True (minus chat-model plugins) into the
              same tool_payload shape prepare_agent_payload emits.
              Reuses ai_service._build_tool_from_node + get_node_class,
              yielding fresh AgentToolSpec / ToolDef declarations.
           tools.extend(refresh_result["tools"])
           tool_index.update(...)
           # next execute_llm_step sees the new tools.
    5. execute_activity("agent.persist_turn")
         append_to_memory_markdown(content, "human", prompt) +
         (content, "ai", response); trim window; broadcast
         node_parameters_updated CloudEvents (source_hint="agent").
    6. if token_total >= compaction_threshold:
         execute_activity("agent.compact_context")
         null-guarded against worker-bootstrap race; replaces messages
         with summary only when result.success is True.
  execute_activity("agent.store_output")
       wraps workflow_service.store_node_output for output_main /
       output_top / output_0 — same writes NodeExecutor.execute does
       at services/node_executor.py:198-200, so downstream nodes
       can resolve {{aiAgent.response}} via ParameterResolver.
  emit_phase("completed", status="success")
```

The `agent.prepare_payload` result is recorded in history and therefore
acts as the deterministic engine selector. New executions default to
`llm_engine="native"` with Message Wire V2 and use `ChatUnifier` plus the
native provider SDKs for every turn. Histories whose recorded prepare result
has no engine marker are pre-cutover histories: their messages are in a retired
wire format the native reader cannot interpret, so `agent.execute_llm_step`
refuses them with a non-retryable
`ApplicationError(type="InvalidAgentLLMEngine")` rather than misreading them.
The operator fix is to Reset the deployment, which starts a fresh generation.
Changing the environment cannot change an execution after it starts,
and a native run never falls back after a provider request starts.

`emit_phase(phase, status?)` is a thin helper that schedules `agent.broadcast_progress`. The activity emits `WorkflowEvent.agent_progress` (CloudEvents v1.0, `type="com.opencompany.agent.progress"`) for FE consumers; when `status` is supplied it also drives a raw-dict `update_node_status` for the canvas-glow color (executing / success / error). Same dual-channel pattern F4.A's `_node_activity` uses. When this workflow is itself a delegated child (`context["parent_node_id"]` set), every `emit_phase` call ALSO schedules a second broadcast against the parent's `node_id` with `phase="delegating"` — the parent's canvas badge then advances in real time while the child loops, instead of freezing at "executing" glow until the child completes.

Each LLM step is one activity and each ordinary tool call is one per-type activity. Team-lead Task Manager assignments are different: persistence happens first, then the lead starts a deterministic detached `DelegatedTaskWorkflow` with `ParentClosePolicy.ABANDON` and receives `queued` immediately. The runner owns the root-wide permit, claim, child `AgentWorkflow`, terminal result/usage persistence, `taskTrigger`, and permit release, so the assigning lead can return without polling. Direct non-team delegation retains the child-workflow path. Non-agent tools and excluded types (`rlm_agent`, `claude_code_agent`) still go through `execute_activity`. Failures surface as durable task failures and trigger review rather than being lost when the lead invocation closes.

Durable event listeners retain their deployment graph as a fallback, but each firing resolves the latest persisted workflow graph through `load_persisted_workflow_graph_activity` before filtering downstream nodes. This makes tools added after deployment available to `taskTrigger` and other triggered agent runs. Edge traversal accepts both canonical `targetHandle` and legacy `target_handle`; tool choice remains entirely with the agent and no trigger-specific tool-use prompt is injected.

**Delegation input contract (input-vs-config separation).** The LLM's `{task, context}` args are per-invocation *input*, not node configuration, and travel as the child workflow input's `invocation` field. `prepare_agent_payload` applies it AFTER its config resolution (`{**node_data, **db_params}` — DB wins for config liveness): `task` → system_message, `context`-or-`task` → prompt — the same semantics as the legacy `handlers.tools._execute_delegated_agent`. Stored node parameters (including the empty default `prompt` the frontend persists on drop) therefore never override the delegated task. A call with both fields empty is rejected at the parent's call boundary (tool-error message to the LLM, no child spawn). Bypass agents dispatched as plain activities (`rlm_agent` / `claude_code_agent`) instead receive the remap directly in `node_data` — their per-type activity consumes `node_data` verbatim with no DB re-merge.

**Canvas-aware tools** opt into receiving the parent workflow's `nodes`/`edges` by declaring `needs_canvas: ClassVar[bool] = True` on their `BaseNode` subclass. The F4.B tool dispatch reads this via `services.node_registry.get_node_class(node_type).needs_canvas` and forwards `context.get("nodes")` / `context.get("edges")` into `tool_payload`; default plugins keep the empty-canvas optimisation. Today only `agentBuilder` opts in (it walks edges to resolve its calling agent and mutates the canvas). Operations inside agentBuilder reload via `database.get_workflow(workflow_id)` so in-run duplicate detection sees mutations from earlier calls in the same workflow run — see [agent_builder section in CLAUDE.md](../CLAUDE.md).

`collect_agent_activities()` registers the agent activities — read the live set
from that function rather than trusting a count here, which drifts on every
addition. Seven are the core loop activities:

| Activity | Purpose |
|---|---|
| `agent.prepare_payload` | Resolves the DB-backed payload (provider / model / system_message / user_prompt / `AgentToolSpec`-derived tool definitions / memory_node_id / memory_content / memory_window_size / max_iterations / thinking_config / compaction_threshold / auto_rebind_tools), records `llm_engine` + `message_wire_version`, and leaves credential resolution at the LLM activity boundary. Reads `UserSettings.agent_recursion_limit` + `UserSettings.auto_rebind_tools_after_canvas_change`. Applies the optional `invocation` field (delegation children) after config resolution — per-invocation input always beats stored parameters. |
| `agent.execute_llm_step` | One LLM turn. The native branch decodes Message Wire V2, rebuilds `ToolDef` values, calls `run_native_llm_step(ChatUnifier, ...)` with SDK retries disabled, heartbeats while awaiting the provider, and returns the exact assistant message + tool calls + normalized usage. Guards against un-invokable payloads: post-filter system-only message lists raise `ApplicationError(type="EmptyAgentPrompt", non_retryable=True)`. |
| `agent.refresh_tools` | Translates `workflow_ops` add_node ops (`component_kind="tool"` OR `usable_as_tool=True`) into fresh `AgentToolSpec`-derived `tool_payload` entries via `_build_tool_from_node`. Workflow extends `tools` + `tool_index` from the result. |
| `agent.persist_turn` | Appends the latest human/assistant exchange to memory markdown, trims the window, broadcasts `node.parameters.updated`. |
| `agent.compact_context` | Context-pressure compaction when cumulative active-context tokens hit the threshold (the shared client-side summarizer). Best-effort: continues with un-compacted history on failure; it is not an agent termination control. |
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
provider call. A continue-as-new rollover carries the live transcript in its
resume marker, so nothing reconstructs from the store mid-run. The
journal-era activities (`agent.prepare_context`,
`agent.reconstruct_context_messages`, `agent.append_context`) are retired;
`agent.compact_context` (the shared client-side summarizer that bounds the
carried transcript) remains.
Tool calls scheduled by the workflow carry the model's unmerged `tool_args`
in the per-type activity payload so ToolNodes validate against their
`ToolInput` via `execute_as_tool` (see `tests/nodes/test_tool_call_dispatch.py`).

The F4.B compaction threshold is prepared from the model context length and
the ratio configuration. It currently does not read
`SessionTokenState.custom_threshold` or `compaction_enabled`; those stored
per-session controls are therefore not authoritative for this path.

**Broadcasts inside the loop** wrap `WorkflowEvent` (CloudEvents v1.0) per RFC §6.4: `agent_progress` events (`com.opencompany.agent.progress`) and `node_parameters_updated` events (`com.opencompany.node.parameters.updated`) flow through the `StatusBroadcaster.broadcast_agent_progress` and `StatusBroadcaster.broadcast_node_parameters_updated` wrappers respectively. The latter is reused by the legacy `routers/websocket.py:handle_save_node_parameters` (user-source) and `services/cli_agent/service.py:_persist_memory` (cli-source) — all three emission sites share the same envelope, distinguished by `source_hint` (`"user"` / `"cli"` / `"agent"`).

`rlm_agent`, `claude_code_agent` are NOT migrated — their internal session state (RLM REPL / Claude CLI `--resume` with stable `cwd`) requires single-process continuity and would break across activity boundaries.

References: [Temporal AI Cookbook](https://docs.temporal.io/ai-cookbook), [`temporal-community/temporal-ai-agent`](https://github.com/temporal-community/temporal-ai-agent), [`temporalio.contrib.openai_agents`](https://github.com/temporalio/sdk-python/tree/main/temporalio/contrib/openai_agents).

## Config Node Filtering

Certain nodes provide configuration rather than executing:

```python
# Config handles - nodes connecting via these are filtered out
# (services/temporal/workflow.py)
CONFIG_HANDLES = {
    "input-context",
    "input-tools",
    "input-memory",  # replay/import compatibility for V1 graph snapshots only
    "input-model",
    "input-skill",
    "input-task",
    "input-teammates",
}

# Trigger node types - event listeners, never scheduled as blocking activities
# Authoritative list: server/constants.py WORKFLOW_TRIGGER_TYPES (frozenset,
# 17 entries at the time of writing). Omitting a trigger there is a silent
# failure: find_trigger_nodes filters on this set, so deploy ignores the node.
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
# in server/constants.py ANDROID_SERVICE_NODE_TYPES (16 entries since
# Wave 11.I).
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
| Plugin node returns a failure envelope (`{success: False, error_type, retryable}`) | `BaseNode.as_activity` (and the legacy `execute_node_activity`) raises a typed `ApplicationError`: `type` = the envelope's `error_type`, `non_retryable` from its `retryable` verdict and the attempt cap, the envelope as `details[0]`. Attempts come from `BaseNode.effective_retry_policy()` (a declared `retry_policy` wins; triggers and mutating nodes get one attempt; `readonly: True` keeps three) and are capped activity-side from `activity.info()` even when the scheduled policy is unlimited. Only the final attempt broadcasts `error`; earlier attempts stay `executing` with `attempt` / `max_attempts` / `last_error`. Rollback: `TEMPORAL_PLUGIN_FAILURE_RETRIES=false` returns the envelope as a successful completion (the pre-fix behaviour, no retries). |
| Ordinary node or agent-support activity raises (infrastructure failure) | Temporal retries up to 3 attempts with backoff unless the error type is non-retryable. |
| `AgentWorkflow` tool-call activity fails | The tool activity carries the tool plugin's effective policy under `machina-plugin-failure-retries-v1` (pre-patch histories scheduled it with no policy, Temporal's unlimited default). After the last attempt the model receives the plugin's failure envelope as JSON and continues; the delegation branch reports the same text to `agent.finish_delegation`. |
| `AgentWorkflow` LLM-step activity fails | `LLM_STEP_RETRY` retries without limit (`maximum_attempts=0`, 5 s → 5 min backoff; `services/temporal/_retry_policies.py`) unless the provider error category is non-retryable (invalid_request, authentication, ...). Provider SDK retries stay disabled so Temporal owns the retry schedule and `retry_after` hints. |
| Worker crashes mid-execution | Temporal reschedules on another worker |
| Ordinary node times out | Temporal applies that activity's retry policy; plugin timeouts vary by node type. |
| All retries exhausted | `MachinaWorkflow._wait_any_complete` unwraps the `ActivityError` cause (`services/temporal/_failures.activity_failure_envelope`) into the plugin's envelope, records `{node_id, error, error_type}` in `errors[]`, stops execution, and hands the pause-on-failure breaker the plugin's message rather than the SDK wrapper text. |

**Failure classification** happens at the source, in `services/plugin/retryability.py`, because only the raising site can tell a 404 from a 503 or see the `retryable` attribute on a wrapped `LLMError`. `BaseNode._wrap_error` stamps the verdict as `retryable`: `NodeUserError`, Pydantic validation, credential (`PermissionDeniedError`), unknown-operation, Output-contract and cancellation failures are permanent; `httpx` 4xx other than 408 / 425 / 429 are permanent; 5xx, timeouts, connection errors and unknown exceptions are transient; a boolean `retryable` attribute on the exception or its `__cause__` chain wins. Temporal vetoes by `type` name before it reads `non_retryable`, so a retryable failure whose `error_type` is a non-retryable name is raised as `<error_type>.retryable`. Exceptions that escape the plugin (the payload-size guard, template resolution, the output store) are classified the same way by `NodeExecutor.execute`. `NodeContext.attempt` and `NodeContext.idempotency_key` (`f"{workflow_run_id}-{activity_id}"`, per the Temporal Python docs) let a node that opts into retries make its writes idempotent. `tests/fixtures/effective_retry_attempts_snapshot.json` pins the effective attempt count per node type.

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
├── trigger_listener_workflow.py  # TriggerListenerWorkflow (legacy push-trigger listener)
├── polling_trigger_workflow.py   # PollingTriggerWorkflow (legacy polling listener)
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

**WS surface**: `_handlers.py` registers `temporal_status` / `temporal_start` / `temporal_stop` via `services.ws_handler_registry.register_ws_handlers`. `_refresh.py` registers a WS-connect callback via `services.status_broadcaster.register_service_refresh` so the FE health indicator stays current.

**Months-long durability contract**: running and paused deployments survive backend restarts, are never auto-terminated, and keep executing for months. Mechanisms, each replay-patch-guarded where it changes recorded commands:

- **No lifetime caps on new child runs.** Trigger/cron-spawned `MachinaWorkflow` runs, agent children, and delegated-task runners previously carried 1-2h `execution_timeout`/`run_timeout` — Temporal's timeout timers keep ticking through a cooperative pause, so any pause longer than the cap silently terminated the run (and a timed-out delegated runner skipped its compensation: leaked permit + stuck task row). New executions start children unbounded (patches: `*-unbounded-child-runs-v1`, `machina-unbounded-lifetimes-v1`, `agent-unbounded-lifetimes-v1`). Liveness is the activity layer's job: node activities heartbeat every 30s against a 2-minute `heartbeat_timeout`; their `start_to_close` is a generous 24h ceiling, not 10 minutes. The subagent-permit wait uses `PERMIT_WAIT_RETRY` (unlimited attempts) so a queued delegation waits as long as admission takes instead of failing after ~3h.
- **History-pressure continue-as-new everywhere.** Temporal terminates any workflow around ~51,200 history events. `WorkflowControlWorkflow` (which multiplexes all of a deployment's triggers into one history) now rolls over on `is_continue_as_new_suggested()` / a 10K-event soft cap, carrying trigger specs, per-trigger provider `seen_ids` (written back into the spec after every poll cycle), queued push events, the bounded dedup baseline, and the control state — a rollover works mid-pause too, since a paused controller still accretes signal history. `TriggerListenerWorkflow`/`PollingTriggerWorkflow` gained the same pressure check (the old `_processed_count >= 16_000` gate was unreachable: real spawns cost ~15-25 events each, and polling counted only emitted events while a quiet mailbox burned ~11 events/cycle — dead in ~3 days at the 60s default). Poll intervals are clamped to a 30s floor on the patched path. Because run ids change on rollover, **controller handles are addressed by workflow id only, never run_id-pinned** (`_controller_handle`, manager `register_trigger`).
- **dispatch.emit controller narrowing.** Controllers advertise their push event types via the `ControlEventTypes` keyword-list Search Attribute (upserted as triggers register); `dispatch.emit` skips controllers with no matching trigger instead of signalling every running controller with every platform event (each unmatched signal was ~4 immutable history events — one busy deployment burned every other controller's rollover budget). Controllers without the attribute (pre-upgrade histories) keep match-all behaviour.
- **Boot-time reconcile** (`reconcile_active_controls_on_boot`, called from the lifecycle module after workers start): runs the lazy `_reconcile_control` over every active control row, converges `starting` rows a crash left behind (controller alive with triggers registered → `running`; alive-but-empty for a graph that declares triggers → `failed` + controller closed; vanished → `failed`), and re-arms the process-local half of running/paused generations from the persisted graph snapshot — DeploymentManager state, in-process collectors for non-canary trigger types, cron pause posture. Idempotent by construction (controller `register_trigger` keyed by listener id, legacy starts use `USE_EXISTING`, cron creation preserves server-owned pause state).

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
| `temporal_plugin_failure_retries` | `TEMPORAL_PLUGIN_FAILURE_RETRIES` | `true` | Structured plugin failures raise typed `ApplicationError`s at the activity boundary so the plugin RetryPolicy applies (see Retry & Fault Tolerance). `false` returns the envelope as a successful completion: no retries, the pre-fix behaviour. Evaluated activity-side; never touches recorded commands. |
| `temporal_frontend_grpc_port` | `TEMPORAL_FRONTEND_GRPC_PORT` | see `.env.template` | gRPC port (`--port`). Drives the readiness probe. |
| `temporal_ui_port` | `TEMPORAL_UI_PORT` | see `.env.template` | Web UI port (`--ui-port`). CLI default is `--port + 1000`; pinned into the serial block that starts at `PYTHON_BACKEND_PORT` instead. |
| `temporal_sqlite_path` | `TEMPORAL_SQLITE_PATH` | `temporal.db` | SQLite file (`--db-filename`). Resolved relative to `DATA_DIR` (= `~/.opencompany/`) unless absolute — flat under `~/.opencompany/` like `credentials.db` / `workflow.db`. |
| `temporal_graceful_shutdown_seconds` | `TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS` | `30` | `CTRL_BREAK_EVENT` (Windows) / `SIGTERM` (POSIX) → tree-kill grace window. Shared with the embedded worker shutdown. |
| `temporal_terminate_running_on_startup` | `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP` | `false` | Debug-only startup sweep (see the durability contract above). Keep false so running and paused deployments survive restarts. |
| `temporal_health_monitor_interval_seconds` | `TEMPORAL_HEALTH_MONITOR_INTERVAL_SECONDS` | `15` | Resident dev-server watchdog probe cadence (lifecycle module; loopback deployments only). |
| `workflow_control_crash_recovery` | `WORKFLOW_CONTROL_CRASH_RECOVERY` | `pause` | After an UNCLEAN shutdown (kill/crash, dirty-bit marker), boot pauses generations still `running` so the user consciously resumes; `resume` restores them running. Clean restarts always restore as-is. |
| `workflow_control_missing_controller` | `WORKFLOW_CONTROL_MISSING_CONTROLLER` | `pause` | A live generation whose controller vanished converges to `paused` (Resume rebuilds the controller); `fail` preserves the legacy Reset-only behaviour. |
| `workflow_control_pause_on_failure` | `WORKFLOW_CONTROL_PAUSE_ON_FAILURE` | `true` | Circuit breaker: repeatedly-failing trigger-spawned runs pause their deployment (fix + Resume) instead of firing into the same error indefinitely. Evaluated activity-side; never touches recorded commands. |
| `workflow_control_pause_on_failure_threshold` | `WORKFLOW_CONTROL_PAUSE_ON_FAILURE_THRESHOLD` | `3` | Failed runs inside the rolling window required to trip the breaker — one node hiccup never pauses a deployment. `1` = pause on the first failure. Resume resets the streak. |
| `workflow_control_pause_on_failure_window_seconds` | `WORKFLOW_CONTROL_PAUSE_ON_FAILURE_WINDOW_SECONDS` | `600` | Rolling window for the failure streak; older failures age out (streak state lives in the cache table with a matching TTL). |

Full recovery-policy semantics: [temporal-workflow-control.md → Recovery policies](./temporal-workflow-control.md#recovery-policies).

The legacy `TEMPORAL_SERVER_READY_TIMEOUT_SECONDS` knob (CLI-supervised-era readiness wait) was removed — it had no consumer since the backend-owned cutover. The temporary agent-engine cutover variable that was read directly rather than through `Settings` is gone too: the engine selector is now recorded per execution by `agent.prepare_payload` (see above).

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
