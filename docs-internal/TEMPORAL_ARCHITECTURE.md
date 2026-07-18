# Temporal Distributed Node Execution Architecture

## Overview

Each workflow node executes as a **Temporal activity** with its own isolated context, enabling horizontal scaling across distributed workers. The orchestrator dispatches in one of three ways depending on settings flags:

| Dispatch | Trigger | Use case |
|---|---|---|
| **Legacy single activity** (`execute_node_activity`) | `TEMPORAL_PER_TYPE_DISPATCH=false` | Every node routed through one dispatcher activity. WebSocket round-trip back to the FastAPI server. Stable since Wave 11; kept as the fallback path. |
| **Per-type activity** (`node.{type}.v{version}`) | `TEMPORAL_PER_TYPE_DISPATCH=true` (production default) | Each plugin gets its own `@activity.defn`. Per-plugin retry / timeout / heartbeat configs apply. With `TEMPORAL_WORKER_POOL_ENABLED=true` (default since Wave 16.4) the activity also carries `task_queue=cls.task_queue`, landing it on its specialised `TemporalWorkerPool` worker (browser / code-exec / ai-heavy / ...). Shipped in F4.A (commit `8261b05`); queue routing activated in Wave 16. |
| **Agent-as-child-workflow** (`AgentWorkflow`) | `TEMPORAL_AGENT_WORKFLOW_ENABLED=true` | AI Agents (aiAgent, chatAgent, 11 specialized agents, 2 team leads) run as Temporal child workflows. Each LLM turn = activity; each tool call = per-type activity. Mirrors Temporal's AI Cookbook canonical pattern. F4.B infrastructure shipped (commit `a4d009e`); per-agent migrations follow. |

`rlm_agent`, `claude_code_agent` are intentionally excluded from AgentWorkflow — their externalised loops (RLM REPL / Claude CLI `--resume`) require single-process state continuity.

## Execution Routing & Running

`WorkflowService.execute_workflow` picks one of three executors in priority order (`server/services/workflow.py`):

1. If `TEMPORAL_ENABLED=true` and Temporal is configured → `_execute_temporal()` (the distributed path documented in this file).
2. Else if Redis is available → `_execute_parallel()` (local parallel orchestration via `WorkflowExecutor`).
3. Else → `_execute_sequential()` (single-threaded fallback).

**Running with Temporal** — the Temporal server and the embedded worker start automatically with every launch script:

```bash
npm run start            # Starts Temporal server + all services
npm run dev              # Starts Temporal server + all services (dev mode)
npm run stop             # Stops all services including Temporal
```

The embedded Temporal worker runs **inside the Python backend** — registered in the `main.py` lifespan via `TemporalWorkerManager`, not as a separate process.

**Standalone worker** (for horizontal scaling — add more pollers against the same task queue):

```bash
cd server
python -m services.temporal.worker
```

This invokes `run_standalone_worker()` from `services/temporal/worker.py`.

## System Architecture

```
                        TEMPORAL SERVER (port 7233)
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
    "workflow_id": "6868cbbf4a36409fbd07ca24999f8b66",  # 32-hex UUID, stable across rename
    "workflow_slug": "AI_Assistant_1",                  # Human-readable, used for Temporal child IDs
    "session_id": "session-xyz",
    "execution_id": "AI_Assistant_1-a1b2c3d4",  # Stable per run (= the MachinaWorkflow Temporal id);
                                                # threaded into AgentWorkflow tool payloads + delegate
                                                # children so session-keyed nodes (browser) reuse one
                                                # instance per run instead of minting a uuid per call
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

All knobs live in `services/temporal/worker.py`; every default is env-overridable. `DEPLOYMENT_MODE` (`local` / `cloud` / `self_hosted`) is the topology hint (`core/config.py`).

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
- **Observability interceptors** (Wave 17.3, `services/temporal/_interceptors.py`): `activity_retry` WARN when `activity.info().attempt > 1` — the "worker died and Temporal re-dispatched" signal; `workflow_start` guarded by `workflow.unsafe.is_replaying()`.
- **Periodic activity heartbeat** (Wave 17.6, `plugin/base.py::as_activity`): 30s background beat during long bodies so a laptop-sleep crash is detected within one `heartbeat_timeout` (2 min) instead of `start_to_close` (10 min).
- **Cron catch-up bound** (Wave 17.1, `services/temporal/schedules.py`): `SchedulePolicy(catchup_window=24h)` + `SKIP` overlap — a laptop offline a week does not replay 168 hourly ticks on wake.
- **One-shot LLM-step retry** (Wave 17.2): `LLM_STEP_RETRY(maximum_attempts=1)` on `agent.execute_llm_step.v1` — LLM calls are not idempotent; the workflow surfaces the failure instead of silently re-billing the prompt. `agent.refresh_tools.v1` deliberately keeps 3 attempts (idempotent canvas rebuild).

**Metric watchlist** (Temporal Web UI / metrics endpoint): `schedule_to_start_latency` (elevated = raise pollers or slots), `worker_task_slots_available` (0 = raise concurrency or add hosts), `poll_success_rate` (target >= 90%), `sticky_cache_evictions` (persistent growth = raise `max_cached_workflows`).

**Tuning order** (per the worker-performance docs): host provisioning -> executor slots -> poller counts -> rate limits.

### Agent-as-child-workflow (F4.B)

When `TEMPORAL_AGENT_WORKFLOW_ENABLED=true` and the node type is in the migrating set (`aiAgent` / `chatAgent` / 11 specialized agents / 2 team leads), the orchestrator schedules `AgentWorkflow` as a child workflow instead of an activity. Inside the workflow:

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
         After filter_empty_messages, a list with no human/ai/tool
         message raises ApplicationError(type="EmptyAgentPrompt",
         non_retryable=True) — providers require >=1 non-system
         message (Gemini pulls SystemMessages into system_instruction
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
           services/ai.py:_run_agent_loop).
    4. for each tool result with an ``operations`` field
       (canvas-mutating tools — today only ``agentBuilder``):
         if payload["auto_rebind_tools"] is True:
           execute_activity("agent.refresh_tools.v1", {operations: …})
             translates add_node ops with component_kind=="tool" OR
             usable_as_tool=True (minus chat-model plugins) into the
             same tool_payload shape prepare_agent_payload emits.
             Reuses ai_service._build_tool_from_node + get_node_class.
           tools.extend(refresh_result["tools"])
           tool_index.update(...)
           # next execute_llm_step.v1 sees the new tools.
    5. execute_activity("agent.persist_turn.v1")
         append_to_memory_markdown(content, "human", prompt) +
         (content, "ai", response); trim window; broadcast
         node_parameters_updated CloudEvents (source_hint="agent").
    6. if token_total >= compaction_threshold:
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

`emit_phase(phase, status?)` is a thin helper that schedules `agent.broadcast_progress.v1`. The activity emits `WorkflowEvent.agent_progress` (CloudEvents v1.0, `type="com.opencompany.agent.progress"`) for FE consumers; when `status` is supplied it also drives a raw-dict `update_node_status` for the canvas-glow color (executing / success / error). Same dual-channel pattern F4.A's `_node_activity` uses. When this workflow is itself a delegated child (`context["parent_node_id"]` set), every `emit_phase` call ALSO schedules a second broadcast against the parent's `node_id` with `phase="delegating"` — the parent's canvas badge then advances in real time while the child loops, instead of freezing at "executing" glow until the child completes.

Each LLM step is one activity and each ordinary tool call is one per-type activity. Team-lead Task Manager assignments are different: persistence happens first, then the lead starts a deterministic detached `DelegatedTaskWorkflow` with `ParentClosePolicy.ABANDON` and receives `queued` immediately. The runner owns the root-wide permit, claim, child `AgentWorkflow`, terminal result/usage persistence, `taskTrigger`, and permit release, so the assigning lead can return without polling. Direct non-team delegation retains the child-workflow path. Non-agent tools and excluded types (`rlm_agent`, `claude_code_agent`) still go through `execute_activity`. Failures surface as durable task failures and trigger review rather than being lost when the lead invocation closes.

Durable event listeners retain their deployment graph as a fallback, but each firing resolves the latest persisted workflow graph through `load_persisted_workflow_graph_activity` before filtering downstream nodes. This makes tools added after deployment available to `taskTrigger` and other triggered agent runs. Edge traversal accepts both canonical `targetHandle` and legacy `target_handle`; tool choice remains entirely with the agent and no trigger-specific tool-use prompt is injected.

**Delegation input contract (input-vs-config separation).** The LLM's `{task, context}` args are per-invocation *input*, not node configuration, and travel as the child workflow input's `invocation` field. `prepare_agent_payload` applies it AFTER its config resolution (`{**node_data, **db_params}` — DB wins for config liveness): `task` → system_message, `context`-or-`task` → prompt — the same semantics as the legacy `handlers.tools._execute_delegated_agent`. Stored node parameters (including the empty default `prompt` the frontend persists on drop) therefore never override the delegated task. A call with both fields empty is rejected at the parent's call boundary (tool-error message to the LLM, no child spawn). Bypass agents dispatched as plain activities (`rlm_agent` / `claude_code_agent`) instead receive the remap directly in `node_data` — their per-type activity consumes `node_data` verbatim with no DB re-merge.

**Canvas-aware tools** opt into receiving the parent workflow's `nodes`/`edges` by declaring `needs_canvas: ClassVar[bool] = True` on their `BaseNode` subclass. The F4.B tool dispatch reads this via `services.node_registry.get_node_class(node_type).needs_canvas` and forwards `context.get("nodes")` / `context.get("edges")` into `tool_payload`; default plugins keep the empty-canvas optimisation. Today only `agentBuilder` opts in (it walks edges to resolve its calling agent and mutates the canvas). Operations inside agentBuilder reload via `database.get_workflow(workflow_id)` so in-run duplicate detection sees mutations from earlier calls in the same workflow run — see [agent_builder section in CLAUDE.md](../CLAUDE.md).

**Seven agent activities** are registered by `collect_agent_activities()` for `AgentWorkflow` to schedule by name:

| Activity | Purpose |
|---|---|
| `agent.prepare_payload.v1` | Resolves the DB-backed payload (provider / model / api_key / system_message / user_prompt / tools / memory_node_id / memory_content / memory_window_size / max_iterations / thinking_config / compaction_threshold / auto_rebind_tools). Reads `UserSettings.agent_recursion_limit` + `UserSettings.auto_rebind_tools_after_canvas_change`. Applies the optional `invocation` field (delegation children) after config resolution — per-invocation input always beats stored parameters. |
| `agent.execute_llm_step.v1` | One LLM turn — rebuilds StructuredTools from the workflow's current `tools` list and returns the assistant message + tool_calls. Guards against un-invokable payloads: post-filter system-only message lists raise `ApplicationError(type="EmptyAgentPrompt", non_retryable=True)`. |
| `agent.refresh_tools.v1` | Translates `workflow_ops` add_node ops (component_kind="tool" OR usable_as_tool=True) into fresh `tool_payload` entries via `_build_tool_from_node`. Workflow extends `tools` + `tool_index` from the result. |
| `agent.persist_turn.v1` | Appends the latest human/assistant exchange to memory markdown, trims the window, broadcasts `node.parameters.updated`. |
| `agent.compact_memory.v1` | Token-budget compaction when cumulative tokens hit the threshold. Best-effort: continues with un-compacted history on failure. |
| `agent.store_output.v1` | Writes `output_main` / `output_top` / `output_0` so downstream nodes resolve `{{aiAgent.response}}` via `ParameterResolver`. |
| `agent.broadcast_progress.v1` | Emits `WorkflowEvent.agent_progress` (CloudEvents v1.0) + optional raw-dict `update_node_status` for canvas-glow color. Single helper drives every phase emit. |

**Broadcasts inside the loop** wrap `WorkflowEvent` (CloudEvents v1.0) per RFC §6.4: `agent_progress` events (`com.opencompany.agent.progress`) and `node_parameters_updated` events (`com.opencompany.node.parameters.updated`) flow through the `StatusBroadcaster.broadcast_agent_progress` and `StatusBroadcaster.broadcast_node_parameters_updated` wrappers respectively. The latter is reused by the legacy `routers/websocket.py:handle_save_node_parameters` (user-source) and `services/cli_agent/service.py:_persist_memory` (cli-source) — all three emission sites share the same envelope, distinguished by `source_hint` (`"user"` / `"cli"` / `"agent"`).

`rlm_agent`, `claude_code_agent` are NOT migrated — their internal session state (RLM REPL / Claude CLI `--resume` with stable `cwd`) requires single-process continuity and would break across activity boundaries.

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
| Node WebSocket call fails | Temporal retries (up to 3 attempts with backoff) |
| Worker crashes mid-execution | Temporal reschedules on another worker |
| Node times out (10 min) | Temporal retries with backoff |
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
├── agent_activities.py  # collect_agent_activities() -> the 7 agent.*.v1 activities (F4.B)
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

The Temporal binary + persistence are managed in-process by the plugin-folder pattern at [`server/services/temporal/`](../server/services/temporal/). Single supervised process — the official `temporal` CLI's `server start-dev` mode, per [docs.temporal.io/develop/python/set-up-your-local-python](https://docs.temporal.io/develop/python/set-up-your-local-python). Five sibling files (`_install.py`, `_runtime.py`, `_handlers.py`, `_refresh.py`, `client.py`, plus `__init__.py` for registry wiring) match the [Wave 11 plugin-folder pattern](./plugin_system.md#self-contained-plugin-folders) that `server/nodes/whatsapp/` uses for its Go binary.

**What runs**: one process — `temporal server start-dev --port 7233 --ui-port 8080 --db-filename ~/.opencompany/temporal.db --namespace default`. Both gRPC + Web UI bind to the same process (per docs.temporal.io/cli/server — "all running in a single process"). SQLite-backed durability; history persisted across restarts.

**Modern libs doing the heavy lifting** (zero custom infrastructure code):
- **[`pooch`](https://pypi.org/project/pooch/)** — `services/temporal/_install.py` downloads the official `temporal` CLI archive from `https://temporal.download/cli/archive/latest?platform=<os>&arch=<arch>`. Cross-platform (Windows zip / macOS+Linux tar.gz), cached at `<DATA_DIR>/packages/temporal/` via `_cache_dir() = core.paths.package_dir("temporal")` (pooch's `path=` argument). The retrieve call passes an explicit `downloader=pooch.HTTPDownloader(timeout=300, progressbar=True)`: the timeout is per-socket-read (not total transfer), so arbitrarily slow links can finish the ~114 MB fetch — pooch's 30 s default aborted them — and `progressbar` must live on the downloader because `retrieve()` ignores its own kwarg when `downloader=` is explicit. Failed downloads never poison the cache (pooch writes to a temp file and renames atomically on success). Standalone entry: `python -m services.temporal._install` — invoked **fatally** by `company build` step [6/6] (so first `company start` doesn't pay the download; contract locked by `test_temporal_install_is_fatal_on_failure`) and **non-fatally** by npm postinstall (`scripts/install.js` try/catch — `TemporalServerRuntime._pre_spawn()` re-downloads lazily on first `company start`, so a failed eager fetch never fails `npm install -g`). The binary cache survives `company clean` (`packages` is in `_OPENCOMPANY_KEEP`, `cli/commands/clean.py`). Pre-fix this used `pooch.os_cache("opencompany-temporal")` (`~/.cache/OpenCompany/opencompany-temporal/` etc.) — a separate OS-cache namespace operators reported as "not local"; it now sits under DATA_DIR alongside the Stripe binary and the shared npm tree.
- **`BaseProcessSupervisor` + `BaseSupervisor`** (`server/services/_supervisor/`) — the in-house supervisor base classes that `server/nodes/whatsapp/_runtime.py` also uses. Provides cross-platform signal handling (POSIX `setsid` + Windows Job Objects + `CREATE_NEW_PROCESS_GROUP` for graceful `CTRL_BREAK_EVENT` shutdown), restart policy via tenacity, log draining, status snapshots. We subclass both — zero custom supervisor logic.

**ServiceSpec wiring**: [`cli/commands/_temporal_specs.py`](../cli/commands/_temporal_specs.py) returns one ServiceSpec for the supervised Temporal dev server (env reads happen inside `temporal_specs()` at call time, safe to import before `cli.config.load_config()` runs). Both `start.py` and `dev.py` call it. The generic supervised-runtime shim lives at [`server/services/temporal/_supervised_runtime.py`](../server/services/temporal/_supervised_runtime.py) so the spawned `uv run python -m services.temporal._supervised_runtime services.temporal._runtime:get_temporal_server_runtime` resolves out of the workspace `.venv` without any cross-tree path plumbing.

**WS surface**: `_handlers.py` registers `temporal_status` / `temporal_start` / `temporal_stop` via `services.ws_handler_registry.register_ws_handlers`. `_refresh.py` registers a WS-connect callback via `services.status_broadcaster.register_service_refresh` so the FE health indicator stays current.

**Resumption toggle**: [`TemporalClientWrapper.terminate_running_workflows`](../server/services/temporal/client.py) — runs once at server lifespan startup, between client connect and worker start, gated on `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP` (default `true`). Queries Visibility for every `Running` workflow and calls `handle.terminate(reason="OpenCompany startup: auto-resumption disabled")`. **History is preserved** (UI shows workflows as `Terminated`, not deleted); only active execution stops. Disables resumption while `DeploymentManager` has no boot-time reconcile against Temporal Visibility yet — resumed workflows would otherwise keep executing but be invisible to the OpenCompany UI. Flip to `false` once the reconcile lands.

**Port management**: Temporal owns ports 7233 (gRPC) + 8080 (Web UI). Both bound by the same `temporal.exe` process; both listed in `cli.config.Config.all_ports` so `company stop`'s port-freeing pre-flight covers them.

**Direct CLI access**: the pooch-installed `temporal` binary lives under `<DATA_DIR>/packages/temporal/` (= `~/.opencompany/packages/temporal/` by default, on every OS). Run `temporal --version`, `temporal workflow list`, etc. directly from there.

**Cluster tunables** — all sourced from `.env.template` (canonical defaults; no Python-side fallbacks). Settings fields with `Field(env="...")` require the env var to be present, surfaced via `cli.config.load_config()`'s `.env.template` → `.env` → `os.environ` merge.

| Setting | Env var | `.env.template` default | Purpose |
|---|---|---|---|
| `temporal_enabled` | `TEMPORAL_ENABLED` | `true` | Master toggle. When false, `WorkflowService` falls back to parallel/sequential executor. |
| `temporal_server_address` | `TEMPORAL_SERVER_ADDRESS` | `localhost:7233` | Address the Python SDK client connects to. |
| `temporal_namespace` | `TEMPORAL_NAMESPACE` | `default` | Bootstrapped at server start. |
| `temporal_task_queue` | `TEMPORAL_TASK_QUEUE` | `machina-tasks` | Default task queue for the embedded worker. |
| `temporal_per_type_dispatch` | `TEMPORAL_PER_TYPE_DISPATCH` | `true` | F4.A flag — per-type activity dispatch. |
| `temporal_agent_workflow_enabled` | `TEMPORAL_AGENT_WORKFLOW_ENABLED` | `true` | F4.B flag — agent-as-child-workflow. |
| `temporal_frontend_grpc_port` | `TEMPORAL_FRONTEND_GRPC_PORT` | `7233` | gRPC port (`--port`). Drives the readiness probe. |
| `temporal_ui_port` | `TEMPORAL_UI_PORT` | `8080` | Web UI port (`--ui-port`). CLI default is `--port + 1000 = 8233`; we override to 8080 for muscle memory. |
| `temporal_sqlite_path` | `TEMPORAL_SQLITE_PATH` | `temporal.db` | SQLite file (`--db-filename`). Resolved relative to `DATA_DIR` (= `~/.opencompany/`) unless absolute — flat under `~/.opencompany/` like `credentials.db` / `workflow.db`. |
| `temporal_graceful_shutdown_seconds` | `TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS` | `30` | `CTRL_BREAK_EVENT` (Windows) / `SIGTERM` (POSIX) → tree-kill grace window. Shared with the embedded worker shutdown. |
| `temporal_terminate_running_on_startup` | `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP` | `true` | Resumption toggle (see above). |

One supervisor-build-time env var (read inside `_temporal_specs.py`, not in `Settings`):

| Env var | `.env.template` default | Purpose |
|---|---|---|
| `TEMPORAL_SERVER_READY_TIMEOUT_SECONDS` | `120` | How long the supervisor waits for Temporal's gRPC port to come up. Covers the first-run binary download (~114 MB) if `company build` didn't pre-cache. |

## Debugging

The Web UI is at http://localhost:8080; the UI's HTTP API is served at http://localhost:8233.

```bash
# Temporal Web UI
open http://localhost:8080

# List workflows via the local CLI binary (under <DATA_DIR>/packages/temporal/)
~/.opencompany/packages/temporal/.../temporal.exe workflow list --address localhost:7233

# Re-fetch the binary at any time
uv run python -m services.temporal._install
```
