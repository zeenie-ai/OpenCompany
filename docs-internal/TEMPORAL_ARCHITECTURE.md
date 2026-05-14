# Temporal Distributed Node Execution Architecture

## Overview

Each workflow node executes as a **Temporal activity** with its own isolated context, enabling horizontal scaling across distributed workers. The orchestrator dispatches in one of three ways depending on settings flags:

| Dispatch | Trigger | Use case |
|---|---|---|
| **Legacy single activity** (`execute_node_activity`) | Default | Every node routed through one dispatcher activity. WebSocket round-trip back to the FastAPI server. Stable since Wave 11. |
| **Per-type activity** (`node.{type}.v{version}`) | `TEMPORAL_PER_TYPE_DISPATCH=true` | Each plugin gets its own `@activity.defn`. Per-plugin retry / timeout / heartbeat configs apply. Worker pool can later specialise per `cls.task_queue` (browser / code-exec / ai-heavy / ...). Shipped in F4.A (commit `8261b05`). |
| **Agent-as-child-workflow** (`AgentWorkflow`) | `TEMPORAL_AGENT_WORKFLOW_ENABLED=true` | AI Agents (aiAgent, chatAgent, 12 specialized agents, 2 team leads) run as Temporal child workflows. Each LLM turn = activity; each tool call = per-type activity. Mirrors Temporal's AI Cookbook canonical pattern. F4.B infrastructure shipped (commit `a4d009e`); per-agent migrations follow. |

`deep_agent`, `rlm_agent`, `claude_code_agent` are intentionally excluded from AgentWorkflow — their externalised loops (deepagents package / RLM REPL / Claude CLI `--resume`) require single-process state continuity.

## System Architecture

```
                        TEMPORAL SERVER (port 7233)
                                  |
              Task Queue: machina-tasks
  +---------------------------------------------------------------+
  |  Workflow: MachinaWorkflow (orchestrator only)                |
  |  - Parses graph structure from React Flow                     |
  |  - Filters config nodes (tools, memory, services)             |
  |  - Resolves activity per node (legacy / per-type / agent-wf)  |
  |  - Schedules activities (FIRST_COMPLETED pattern)             |
  |  - Collects results and routes outputs to dependent nodes     |
  +---------------------------------------------------------------+
                                  |
          Activity / child-workflow scheduling
    +--------+  +--------+  +--------+  +----------------+
    | Node A |  | Node B |  | Node C |  | AgentWorkflow  |  (F4.B child wf)
    +--------+  +--------+  +--------+  +----------------+
         |          |           |              |
         v          v           v              v
  +----------+  +----------+  +----------+  +-----------------------+
  | Worker 1 |  | Worker 2 |  | Worker 3 |  | LLM step + tool steps |
  | aiAgent  |  | timer    |  | console  |  | (per-type activities) |
  +----------+  +----------+  +----------+  +-----------------------+
         |          |           |              |
         +----------+-----------+--------------+
                           |
                           v
                   In-process call (F4.A) OR
                   WebSocket round-trip (legacy)
                   +----------------+
                   | MachinaOs      |
                   | workflow_svc   |
                   +----------------+
```

## Key Architecture Principles

### 1. Node = Independent Activity

Each node runs as a separate Temporal activity with:
- **Own context** - No shared mutable state between nodes
- **Own retry policy** - Failed nodes retry independently (up to 3 attempts)
- **Own timeout** - Long AI nodes don't block short nodes (10 min default)
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
    "node_id": "aiAgent-123",
    "node_type": "aiAgent",
    "node_data": {
        "model": "gpt-4",
        "prompt": "{{chattrigger.message}}",
        "systemMessage": "You are a helpful assistant"
    },
    "inputs": {  # Outputs from upstream nodes
        "chatTrigger-456": {"message": "Hello", "timestamp": "..."},
    },
    "workflow_id": "workflow-789",
    "session_id": "session-xyz",
    "nodes": [...],  # Full list for tool/memory detection
    "edges": [...],  # Full list for tool/memory detection
}
```

## Execution Flow

### 1. Workflow Receives Request

```
MachinaOs Server
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
            start_to_close_timeout=timedelta(minutes=10),
            heartbeat_timeout=timedelta(minutes=2),
            retry_policy=retry_policy,
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

The 2-minute `heartbeat_timeout` would kill DeepAgent or browser activities that routinely run 5-10 minutes. Both dispatch paths emit `activity.heartbeat()` at progress points — legacy on every non-matching WebSocket message, per-type at the start of each pipeline stage.

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

### Specialized Worker Pools (Future)

F4.A laid the infrastructure (per-type dispatch with `task_queue=cls.task_queue` on `BaseNode`) but the single `TemporalWorkerManager` still polls one queue. Wiring `TemporalWorkerPool` in `main.py` is the deferred follow-up:

```
Queue: rest-api         Queue: ai-heavy         Queue: code-exec
     |                       |                       |
+----+----+             +----+----+             +----+----+
| Worker  |             | Worker  |             | Worker  |
| gmail   |             | aiAgent |             | python  |
| brave   |             | chatA   |             | js exec |
| twitter |             | deepA   |             | ts exec |
+---------+             +---------+             +---------+

Plugin classes already declare cls.task_queue. When the pool wires
in, `MachinaWorkflow._resolve_activity` starts returning
(activity_name, cls.task_queue) instead of (activity_name, None).
```

Per-queue defaults (concurrency caps in `services/temporal/worker.py:TemporalWorkerPool.DEFAULT_CONCURRENCY`):

| Queue | Default concurrency | Use case |
|---|---|---|
| `machina-default` | 20 | Catch-all |
| `rest-api` | 50 | Lightweight HTTP calls |
| `ai-heavy` | 4 | LLM agent loops |
| `code-exec` | 10 | Python / JS / TS sandboxes |
| `triggers-poll` | 100 | Gmail polling, etc. |
| `triggers-event` | 100 | Event-waiter triggers |
| `android` | 10 | ADB / relay ops |
| `browser` | 4 | Playwright / agent-browser |
| `messaging` | 20 | WhatsApp / Telegram |

### Agent-as-child-workflow (F4.B)

When `TEMPORAL_AGENT_WORKFLOW_ENABLED=true` and the node type is in the migrating set (`aiAgent` / `chatAgent` / 12 specialized agents / 2 team leads), the orchestrator schedules `AgentWorkflow` as a child workflow instead of an activity. Inside the workflow:

```
AgentWorkflow.run(context):
  0. execute_activity("agent.prepare_payload.v1")
       resolves the DB-backed payload from the canvas context — runs
       workflow_service._param_resolver.resolve so {{node.field}}
       templates in prompt / system_message become real values BEFORE
       the LLM sees them. Temporal workflows must be deterministic,
       so DB lookups + edge walking + tool schema build live here.
       Tool entries carry the raw tool_info dict — execute_llm_step
       rebuilds the StructuredTool via ai_service._build_tool_from_node
       inside the activity (no JSON-schema round-trip).
  emit_phase("starting", status="executing")
  loop until "final" or max_iterations:
    1. emit_phase("llm_step", iteration=N)
    2. execute_activity("agent.execute_llm_step.v1")
         returns {kind, assistant_message, calls?, content?, usage}.
         assistant_message is the full LangChain-serialized AIMessage
         (messages_to_dict round-trip preserves provider-specific
         fields: Gemini thought_signature, Anthropic cache markers,
         OpenAI reasoning_content) — appended verbatim to messages.
    3. if kind == "tool_calls":
         for each call:
           emit_phase("executing_tool", tool_name=...)
           execute_activity(f"node.{tool_node_type}.v{version}")
           emit_phase("tool_completed", tool_name=...)
           _serialise_tool_result unwraps F4.A's {success, result, ...}
           envelope so the LLM sees only the handler's return value
           (matches services/ai.py:create_tool_node behavior).
    4. execute_activity("agent.persist_turn.v1")
         append_to_memory_markdown(content, "human", prompt) +
         (content, "ai", response); trim window; broadcast
         node_parameters_updated CloudEvents (source_hint="agent").
    5. if token_total >= compaction_threshold:
         execute_activity("agent.compact_memory.v1")
         null-guarded against worker-bootstrap race; replaces messages
         with summary only when result.success is True.
  execute_activity("agent.store_output.v1")
       wraps workflow_service.store_node_output for output_main /
       output_top / output_0 — same writes NodeExecutor.execute does
       at services/node_executor.py:197-199, so downstream nodes
       can resolve {{aiAgent.response}} via ParameterResolver.
  emit_phase("completed", status="success")
```

`emit_phase(phase, status?)` is a thin helper that schedules `agent.broadcast_progress.v1`. The activity emits `WorkflowEvent.agent_progress` (CloudEvents v1.0, `type="com.machinaos.agent.progress"`) for FE consumers; when `status` is supplied it also drives a raw-dict `update_node_status` for the canvas-glow color (executing / success / error). Same dual-channel pattern F4.A's `_node_activity` uses.

Each LLM step is one activity, each tool call is one per-type activity (the same activities F4.A registered). Failures of tool activities surface as error messages back to the LLM (matching today's LangGraph behaviour); the agent loop continues.

**Broadcasts inside the loop** wrap `WorkflowEvent` (CloudEvents v1.0) per RFC §6.4: `agent_progress` events (`com.machinaos.agent.progress`) and `node_parameters_updated` events (`com.machinaos.node.parameters.updated`) flow through the `StatusBroadcaster.broadcast_agent_progress` and `StatusBroadcaster.broadcast_node_parameters_updated` wrappers respectively. The latter is reused by the legacy `routers/websocket.py:handle_save_node_parameters` (user-source) and `services/cli_agent/service.py:_persist_memory` (cli-source) — all three emission sites share the same envelope, distinguished by `source_hint` (`"user"` / `"cli"` / `"agent"`).

`deep_agent`, `rlm_agent`, `claude_code_agent` are NOT migrated — their internal session state (`deepagents` package / RLM REPL / Claude CLI `--resume` with stable `cwd`) requires single-process continuity and would break across activity boundaries.

References: [Temporal AI Cookbook](https://docs.temporal.io/ai-cookbook), [`temporal-community/temporal-ai-agent`](https://github.com/temporal-community/temporal-ai-agent), [`temporalio.contrib.openai_agents`](https://github.com/temporalio/sdk-python/tree/main/temporalio/contrib/openai_agents).

## Config Node Filtering

Certain nodes provide configuration rather than executing:

```python
# Config handles - nodes connecting via these are filtered out
CONFIG_HANDLES = {"input-tools", "input-memory", "input-model", "input-skill", "input-task", "input-teammates"}

# Trigger node types - event listeners, never scheduled as blocking activities
# Authoritative list: server/constants.py WORKFLOW_TRIGGER_TYPES (frozenset).
TRIGGER_NODE_TYPES = frozenset([
    "webhookTrigger", "whatsappReceive", "workflowTrigger",
    "chatTrigger", "taskTrigger",
    "twitterReceive", "googleGmailReceive", "telegramReceive",
    "emailReceive",
])

# Android service types (connect to androidTool) -- authoritative list
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
| Node WebSocket call fails | Temporal retries (up to 3 attempts with backoff) |
| Worker crashes mid-execution | Temporal reschedules on another worker |
| Node times out (10 min) | Temporal retries with backoff |
| All retries exhausted | Workflow receives failure, stops execution |

## File Structure

```
server/services/temporal/
├── __init__.py          # Exports TemporalExecutor, TemporalClientWrapper
├── activities.py        # NodeExecutionActivities class
│   ├── execute_node_activity()   # Main activity method
│   └── _execute_via_websocket()  # WebSocket execution
├── workflow.py          # MachinaWorkflow class
│   ├── run()                     # Main orchestrator
│   ├── _filter_executable_graph() # Config node filtering
│   ├── _build_dependency_maps()   # Graph analysis
│   ├── _find_ready_nodes()        # Dependency resolution
│   └── _wait_any_complete()       # FIRST_COMPLETED wait
├── worker.py            # TemporalWorkerManager
│   ├── start()                   # Start embedded worker
│   ├── stop()                    # Cleanup
│   └── run_standalone_worker()   # For horizontal scaling
├── executor.py          # TemporalExecutor entry point
└── client.py            # TemporalClientWrapper (runtime heartbeat disabled)
```

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

The Temporal binary + persistence layer are managed in-process by the plugin-folder pattern at [`server/services/temporal/`](../server/services/temporal/) — the `temporal-server` npm package has been retired. Six sibling files (`_install.py`, `_runtime.py`, `_config.py`, `_handlers.py`, `_refresh.py`, plus `__init__.py` for registry wiring) match the [Wave 11 plugin-folder pattern](./plugin_system.md#self-contained-plugin-folders) that `server/nodes/whatsapp/` uses for its Go binary.

**Persistence backends** (selected by `TEMPORAL_BACKEND` env var):

| Backend | When | What runs |
|---|---|---|
| `sqlite` (dev default) | `TEMPORAL_BACKEND=sqlite` | Single supervised process running `temporal api` (the binary's built-in `start-dev` mode). In-memory or file-based SQLite. Zero deps beyond the Temporal binary. |
| `postgres` (prod) | `TEMPORAL_BACKEND=postgres` | Two supervised processes: `temporal-postgres` (pgserver-managed PostgreSQL 16.2) then `temporal-server` (binary with YAML config pointing at the Postgres). Ordered automatically by the supervisor's TCP readiness probe. |

**Modern libs doing the heavy lifting** (zero custom infrastructure code):
- **[`pooch`](https://pypi.org/project/pooch/)** — `services/temporal/_install.py` downloads `temporal-server` + `temporal-sql-tool` from `temporalio/temporal` GitHub releases. SHA-256 verified (digests pinned in `_install.py:_CHECKSUMS`); XDG-cached via `pooch.os_cache()`; tar.gz / zip extraction handled by `pooch.Untar()` / `pooch.Unzip()`. Falls back to `shutil.which("temporal-server")` for system installs. ~30 LOC total.
- **[`pgserver`](https://pypi.org/project/pgserver/)** — bundles PostgreSQL 16.2 binaries cross-platform (Linux / macOS / Windows, x86_64 + ARM64) via pip. Single API: `pgserver.get_server(data_dir)` returns a managed instance. `PostgresRuntime` wraps its lifecycle in `BaseSupervisor.start()` / `.stop()`.
- **`BaseProcessSupervisor` + `BaseSupervisor`** (`server/services/_supervisor/`) — the existing in-house supervisor base classes that `server/nodes/whatsapp/_runtime.py` already uses. Provides cross-platform signal handling (POSIX setsid + Windows Job Objects + psutil tree-kill), restart policy via tenacity, log draining, status snapshots. We subclass both — zero custom supervisor logic.

**ServiceSpec wiring**: [`machina/commands/_temporal_specs.py`](../machina/commands/_temporal_specs.py) returns the right ServiceSpec set for the selected backend. Both `start.py` and `dev.py` call it. The generic [`_supervised_runtime.py`](../machina/commands/_supervised_runtime.py) shim (~50 LOC) lets the `Manager` supervisor schedule any `BaseSupervisor` singleton as a regular subprocess — `python -m machina.commands._supervised_runtime services.temporal._runtime:get_postgres_runtime`.

**WS surface**: `_handlers.py` registers `temporal_status` / `temporal_start` / `temporal_stop` via `services.ws_handler_registry.register_ws_handlers`. `_refresh.py` registers a WS-connect callback via `services.status_broadcaster.register_service_refresh` so the FE health indicator stays current.

**Port management**: Temporal owns ports 7233 / 8233 / 8080 / 9090. Postgres picks a dynamic free port (read via `get_postgres_runtime().uri`). Both are excluded from MachinaOS `allPorts` and from port-freeing in `scripts/start.js`.

**Direct CLI access** is still possible — the `temporal` binary on PATH (or under `pooch.os_cache("machinaos-temporal")`) supports `temporal server start --config <yaml>`, `temporal-sql-tool` subcommands, and `tctl`. The supervisor invokes these binaries; you can too.

**Cluster tunables** (all live in [`core/config.py:Settings`](../server/core/config.py); zero magic numbers in the runtime / config / supervisor wiring):

| Setting | Env var | Default | Purpose |
|---|---|---|---|
| `temporal_backend` | `TEMPORAL_BACKEND` | `postgres` | Backend selector — `postgres` (default, durable) / `sqlite` (fast-iteration dev). |
| `temporal_frontend_grpc_port` | `TEMPORAL_FRONTEND_GRPC_PORT` | `7233` | gRPC port the SDK client connects to. Drives the readiness probe + cluster `rpcAddress`. |
| `temporal_matching_grpc_port` | `TEMPORAL_MATCHING_GRPC_PORT` | `7235` | Internal matching service. |
| `temporal_history_grpc_port` | `TEMPORAL_HISTORY_GRPC_PORT` | `7234` | Internal history service. |
| `temporal_worker_grpc_port` | `TEMPORAL_WORKER_GRPC_PORT` | `7239` | Internal worker service. |
| `temporal_bind_local_only` | `TEMPORAL_BIND_LOCAL_ONLY` | `true` | When `true`, all gRPC ports bind `127.0.0.1`. Flip to `false` for multi-host deployments (`0.0.0.0`). |
| `temporal_num_history_shards` | `TEMPORAL_NUM_HISTORY_SHARDS` | `4` | History-shard count. Bump for higher write throughput; not online-resizable. |
| `temporal_default_max_conns` | `TEMPORAL_DEFAULT_MAX_CONNS` | `20` | Postgres pool size for the `temporal` (history) datastore. |
| `temporal_visibility_max_conns` | `TEMPORAL_VISIBILITY_MAX_CONNS` | `4` | Postgres pool size for the `temporal_visibility` datastore. |
| `temporal_max_conn_lifetime` | `TEMPORAL_MAX_CONN_LIFETIME` | `5m` | Postgres connection rotation interval. Accepts Go-duration strings (`30m`, `2h`, etc.). Short window forces periodic refresh — Temporal community recommendation. |
| `temporal_binary_version` | `TEMPORAL_BINARY_VERSION` | `1.31.0` | Pinned `temporalio/temporal` release tag. Bumping requires a matching entry in `_install.py:_CHECKSUMS_BY_VERSION`. |
| `temporal_graceful_shutdown_seconds` | `TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS` | `30` | SIGTERM → SIGKILL grace window for the Temporal binary supervisor. Shared with the embedded worker shutdown. |
| `temporal_postgres_dsn` | `TEMPORAL_POSTGRES_DSN` | (unset) | Reserved for multi-host deployments — when set, bypasses pgserver and points Temporal at an external Postgres. Out of scope for current sprint. |

Two supervisor-build-time env vars (read inside `_temporal_specs.py`, not in `Settings`):

| Env var | Default | Purpose |
|---|---|---|
| `TEMPORAL_PG_READY_TIMEOUT_SECONDS` | `60` | How long the supervisor waits for pgserver to be ready before marking the spec failed. |
| `TEMPORAL_SERVER_READY_TIMEOUT_SECONDS` | `120` | How long the supervisor waits for Temporal's gRPC port to come up. Includes the first-run binary download (~90 MB). |

### Known benign log noise

Temporal's matching engine periodically writes task-queue metadata for the **internal `temporal-system` namespace** (e.g. `/_sys/default-worker-tq/1`). When two writes contend, the older one's request context gets canceled — Temporal then auto-retries, and workflow execution is unaffected. The cancellation surfaces in the server log as:

```
ERROR msg="Operation failed with internal error." error="UpdateTaskQueue failed. Failed to start transaction. Error: context canceled" operation=UpdateTaskQueue
ERROR msg="Persistent store operation failure" component=matching-engine wf-task-queue-name=/_sys/default-worker-tq/1 wf-namespace=temporal-system
```

These are **not caused by the Postgres backend** — they appeared identically with the previous SQLite path and are reported as benign across the Temporal community ([forum thread](https://community.temporal.io/t/persistent-store-operation-failure-on-updatetaskqueue-operations/11173)). The connection-pool tunables above (`temporal_default_max_conns`, `temporal_max_conn_lifetime`) are sized per Temporal's recommended baseline to minimise the frequency. No further action is required unless you see them in a **user namespace** (e.g. `default` instead of `temporal-system`), which would indicate a real persistence problem.

## Debugging

```bash
# Check task queue pollers
curl http://localhost:8233/api/v1/namespaces/default/task-queues/machina-tasks

# List recent workflows
curl "http://localhost:8233/api/v1/namespaces/default/workflows"

# Get workflow history
curl "http://localhost:8233/api/v1/namespaces/default/workflows/{id}/history"

# Temporal Web UI
open http://localhost:8080

# Temporal HTTP API
open http://localhost:8233
```
