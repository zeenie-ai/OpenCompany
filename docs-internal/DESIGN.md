# Execution Engine Design Document

## Overview

The OpenCompany execution engine implements a robust workflow orchestration system combining industry-standard patterns from **Netflix Conductor**, **Prefect 3.0**, **Temporal**, and **Redis Streams**. This document details the architectural decisions, design patterns, and standards used.

Related docs:
- [TEMPORAL_ARCHITECTURE.md](TEMPORAL_ARCHITECTURE.md) - distributed execution via Temporal activities
- [event_waiter_system.md](event_waiter_system.md) - push-based trigger waiters
- [native_llm_sdk.md](native_llm_sdk.md) - LLM provider layer
- [rlm_service.md](rlm_service.md) - Recursive Language Model agent execution
- [proxy_service.md](proxy_service.md) - residential proxy provider management
- [workflow-schema.md](workflow-schema.md) - workflow JSON schema and node catalog
- [frontend_architecture.md](frontend_architecture.md) - current frontend architecture (React 19 + Vite + Tailwind v4 + shadcn/ui + Radix + RHF/zod + TanStack Query + Zustand). Tokens, primitives, state, forms, credentials exemplar.
- [plugin_system.md](plugin_system.md) - Wave 11 class-based plugin architecture (target state for the config-driven research that lived in deleted planning docs)
- [schema_source_of_truth_rfc.md](schema_source_of_truth_rfc.md) - backend NodeSpec / icon / palette wire format
- [ui_migration_plan.md](ui_migration_plan.md) - antd → shadcn/ui migration plan and completion log

---

## Execution Modes

`WorkflowService` in `server/services/workflow.py` is a thin facade (~460 lines) that routes every workflow run through one of three execution modes. Selection is based on available infrastructure:

```
workflow.execute(workflow_id, workflow_data)
            |
            v
    +---------------------+
    | TEMPORAL_ENABLED    |  yes --> _execute_temporal()
    | and Temporal up?    |             |
    +---------------------+             v
            | no                   TemporalExecutor
            v                      (per-node activities,
    +---------------------+         retries, horizontal scaling)
    | Redis available?    |
    +---------------------+
            | yes --> _execute_parallel()
            |             |
            v             v
            |         WorkflowExecutor
            |         (decide loop, Kahn layers,
            |          distributed locking)
            |
            | no --> _execute_sequential()
            v
        Sequential fallback
        (simple topological walk)
```

### 1. Temporal Distributed (primary production mode)

When `TEMPORAL_ENABLED=true` and the Temporal server is reachable, every workflow node executes through Temporal. Three dispatch paths coexist (legacy `execute_node_activity` / per-type `node.{type}.v{version}` / Agent-as-child-workflow `AgentWorkflow`) gated by `TEMPORAL_PER_TYPE_DISPATCH` and `TEMPORAL_AGENT_WORKFLOW_ENABLED` flags. Per-node retries, per-node timeouts, horizontal scaling via worker pool, and the FIRST_COMPLETED orchestrator pattern.

Full dispatch matrix + activity inventory (legacy + per-type + the 5 F4.B agent activities) + worker configuration + heartbeat semantics live in [TEMPORAL_ARCHITECTURE.md](TEMPORAL_ARCHITECTURE.md). Tool-call dispatch under F4.A: [tool_building_pipeline.md §9](./tool_building_pipeline.md).

### 2. Parallel Local (Redis)

When Temporal is not enabled but Redis is available, workflows use `WorkflowExecutor` in `services/execution/executor.py`:

- Conductor's decide pattern in `_workflow_decide()` under distributed Redis SETNX locks
- Fork/join via `asyncio.gather()` for concurrent node execution
- Prefect-style result caching keyed by `hash_inputs()` (see `services/execution/cache.py`)
- Crash recovery via `RecoverySweeper` with heartbeats
- Dead Letter Queue for failed nodes (`services/execution/dlq.py`)

This is the local-development default when `REDIS_ENABLED=true` is set in `server/.env`.

### 3. Sequential Fallback

When neither Temporal nor Redis is available, workflow execution falls back to a simple topological walk with no parallelism, no caching, and no retry. Intended only for minimal environments and CI smoke tests.

---

## RLM Agent Execution

`rlm_agent` is the only agent node type that does not go through `handle_chat_agent`. It has a dedicated handler `handle_rlm_agent` that routes to `RLMService` in `services/rlm_service.py`.

The Recursive Language Model pattern replaces the standard tool-calling loop with a Python REPL (`exec()`) where the agent writes code that can call:

- `llm_query(prompt)` - invoke the small model connected to `input-model`
- `rlm_query(prompt)` - recursively invoke the agent itself
- `FINAL(answer)` - signal completion

This yields 81-98% token savings on tool-heavy workflows because the agent can orchestrate multiple LLM calls in a single code block instead of paying the round-trip cost of tool calling.

See [rlm_service.md](rlm_service.md) for the full design.

---

## Architecture Principles

### 1. Isolated Execution Contexts
Each workflow execution has its own `ExecutionContext` - no shared global state between concurrent runs.

```
┌─────────────────────────────────────────────────────────────┐
│                    WorkflowService                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Execution    │  │ Execution    │  │ Execution    │      │
│  │ Context A    │  │ Context B    │  │ Context C    │      │
│  │ (workflow 1) │  │ (workflow 2) │  │ (workflow 1) │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

### 2. Separation of Concerns
- **Models** (`models.py`): Pure data structures, no business logic
- **Cache** (`cache.py`): Persistence layer abstraction
- **Executor** (`executor.py`): Orchestration logic
- **Recovery** (`recovery.py`): Fault tolerance

### 3. Dependency Injection
Services receive dependencies via constructor, enabling testing and flexibility:

```python
class WorkflowExecutor:
    def __init__(self, cache: ExecutionCache,
                 node_executor: Callable,
                 status_callback: Callable = None):
        self.cache = cache
        self.node_executor = node_executor
        self.status_callback = status_callback
```

---

## Design Patterns

### 1. Conductor's Decide Pattern

**Source**: Netflix Conductor OSS

The decide pattern is the core orchestration loop. It evaluates current state, finds ready tasks, executes them, and recurses until completion.

```python
async def _workflow_decide(self, ctx: ExecutionContext):
    """Core orchestration loop - Conductor's decide pattern."""
    async with distributed_lock(f"execution:{ctx.execution_id}:decide"):
        # 1. Find nodes ready to execute (dependencies satisfied)
        ready_nodes = self._find_ready_nodes(ctx)

        if not ready_nodes:
            return  # All done or stuck

        # 2. Execute batch (parallel if multiple)
        if len(ready_nodes) > 1:
            await self._execute_parallel_nodes(ctx, ready_nodes)
        else:
            await self._execute_single_node(ctx, ready_nodes[0])

        # 3. Checkpoint and persist
        await self.cache.save_execution_state(ctx)

        # 4. Recurse for next batch
        await self._decide_iteration(ctx)
```

**Benefits**:
- Single point of orchestration logic
- Distributed lock prevents race conditions
- Natural support for parallel execution
- Easy to add features (retries, timeouts)

### 2. Fork/Join Parallel Execution

**Source**: Netflix Conductor + Java ForkJoinPool

Independent nodes (no dependencies on each other) execute in parallel using `asyncio.gather()`.

```python
async def _execute_parallel_nodes(self, ctx, nodes):
    """Fork/Join pattern with asyncio.gather."""
    tasks = [
        self._execute_node_with_caching(ctx, node)
        for node in nodes
    ]

    # True parallelism - all tasks run concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Join - process all results
    for node, result in zip(nodes, results):
        if isinstance(result, Exception):
            ctx.status = WorkflowStatus.FAILED
```

**DAG Layer Computation** (Kahn's Algorithm):

```
Layer 0: [Start]           ← No dependencies
Layer 1: [A, B, C]         ← Depend only on Start (parallel)
Layer 2: [D]               ← Depends on A and B
Layer 3: [E, F]            ← Depend on D (parallel)
Layer 4: [End]             ← Depends on E and F
```

### 3. Prefect-Style Task Caching

**Source**: Prefect 3.0 Task Caching

Results are cached by hashing inputs, enabling idempotent execution.

```python
async def _execute_node_with_caching(self, ctx, node, enable_caching):
    # Generate cache key from inputs
    inputs = self._gather_node_inputs(ctx, node.node_id)

    if enable_caching:
        # Check cache first
        cached = await self.cache.get_cached_result(
            ctx.execution_id, node.node_id, inputs
        )
        if cached:
            node.status = TaskStatus.CACHED
            return cached

    # Execute and cache result
    result = await self.node_executor(...)

    if enable_caching and result.get("success"):
        await self.cache.set_cached_result(
            ctx.execution_id, node.node_id, inputs, result
        )

    return result
```

**Cache Key Generation**:

```python
def hash_inputs(inputs: Dict[str, Any]) -> str:
    """Create deterministic hash of inputs for cache key."""
    # Sort keys for determinism
    sorted_json = json.dumps(inputs, sort_keys=True, default=str)
    return hashlib.sha256(sorted_json.encode()).hexdigest()[:16]

def generate_cache_key(execution_id: str, node_id: str, inputs: Dict) -> str:
    """Generate full cache key."""
    input_hash = hash_inputs(inputs)
    return f"result:{execution_id}:{node_id}:{input_hash}"
```

### 4. State Machine Pattern

**Source**: Standard FSM + Conductor TaskStatus

Tasks and workflows follow explicit state machines:

```
TaskStatus State Machine:
                    ┌─────────────────┐
                    │     PENDING     │
                    └────────┬────────┘
                             │ schedule
                    ┌────────▼────────┐
                    │    SCHEDULED    │
                    └────────┬────────┘
                             │ start
          ┌──────────────────▼──────────────────┐
          │               RUNNING               │
          └──┬──────────┬──────────┬──────────┬─┘
             │          │          │          │
        complete    cache hit    fail      cancel
             │          │          │          │
    ┌────────▼───┐ ┌────▼────┐ ┌──▼───┐ ┌───▼─────┐
    │ COMPLETED  │ │ CACHED  │ │FAILED│ │CANCELLED│
    └────────────┘ └─────────┘ └──────┘ └─────────┘

WorkflowStatus State Machine:
    PENDING → RUNNING → COMPLETED
                ↓           ↓
              PAUSED      FAILED
                ↓           ↓
            CANCELLED ←────┘
```

### 5. Distributed Locking

**Source**: Redis SETNX pattern

Prevents concurrent decide operations on the same execution:

```python
@asynccontextmanager
async def distributed_lock(self, lock_name: str, timeout: int = 60):
    """Acquire distributed lock using Redis SETNX."""
    lock_key = f"lock:{lock_name}"
    lock_token = str(uuid.uuid4())

    # Try to acquire lock
    acquired = await self.cache.redis.set(
        lock_key, lock_token,
        nx=True,  # Only set if not exists
        ex=timeout  # Auto-expire after timeout
    )

    if not acquired:
        raise TimeoutError(f"Could not acquire lock: {lock_name}")

    try:
        yield
    finally:
        # Release lock (only if we own it)
        current = await self.cache.redis.get(lock_key)
        if current == lock_token:
            await self.cache.redis.delete(lock_key)
```

### 6. Event Sourcing (Partial)

**Source**: Event Sourcing pattern + Redis Streams

All state changes are recorded as immutable events:

```python
# Event types
EVENT_TYPES = [
    "workflow_started",
    "node_started",
    "node_completed",
    "node_failed",
    "node_cached",
    "workflow_completed",
    "workflow_failed",
    "workflow_cancelled"
]

# Store event
await cache.add_event(execution_id, "node_completed", {
    "node_id": node.node_id,
    "execution_time": duration,
})

# Events stored in Redis Stream
# Key: execution:{id}:events
# Enables: replay, debugging, audit trail
```

### 7. Heartbeat Pattern

**Source**: Conductor + Distributed Systems

Running nodes send periodic heartbeats for crash detection:

```python
# During execution
await cache.update_heartbeat(execution_id, node_id)

# Recovery sweeper checks
for node_id, node_exec in ctx.node_executions.items():
    if node_exec.status == TaskStatus.RUNNING:
        last_heartbeat = await cache.get_heartbeat(execution_id, node_id)

        if time.time() - last_heartbeat > HEARTBEAT_TIMEOUT:
            # Node is stuck - trigger recovery
            needs_recovery = True
```

### 8. Adapter Pattern

**Source**: Gang of Four

Bridges new executor to existing node handlers without modification:

```python
async def _node_executor_adapter(self, node_id, node_type, parameters, context):
    """Adapter bridges WorkflowExecutor to existing execute_node()."""
    return await self.execute_node(
        node_id=node_id,
        node_type=node_type,
        parameters=parameters,
        nodes=context.get("nodes"),
        edges=context.get("edges"),
        session_id=context.get("session_id"),
        execution_id=context.get("execution_id"),
    )
```

---

## Redis Key Schema

```
# Execution State (HASH)
execution:{id}:state
  - status: WorkflowStatus
  - workflow_id: string
  - session_id: string
  - started_at: timestamp
  - completed_at: timestamp
  - current_layer: int
  - checkpoints: JSON array

# Node States (HASH)
execution:{id}:nodes
  - {node_id}: JSON(NodeExecution)

# Node Outputs (HASH)
execution:{id}:outputs
  - {node_id}: JSON(output data)

# Result Cache (STRING with TTL)
result:{execution_id}:{node_id}:{input_hash}
  - JSON(cached result)
  - TTL: 3600 seconds

# Event History (STREAM)
execution:{id}:events
  - Immutable append-only log
  - Fields: event_type, timestamp, data

# Heartbeats (STRING with TTL)
heartbeat:{execution_id}:{node_id}
  - timestamp
  - TTL: 60 seconds

# Distributed Locks (STRING with TTL)
lock:execution:{id}:decide
  - lock_token (UUID)
  - TTL: 60 seconds

# Active Executions (SET)
executions:active
  - Set of execution_ids currently running
```

---

## Data Models

### ExecutionContext

```python
@dataclass
class ExecutionContext:
    """Isolated state for a single workflow execution."""

    # Identity
    execution_id: str          # Unique ID (auto-generated)
    workflow_id: str           # Workflow definition ID
    session_id: str            # User session

    # State
    status: WorkflowStatus     # Current workflow status
    node_executions: Dict[str, NodeExecution]  # Per-node state
    outputs: Dict[str, Any]    # Node outputs for template resolution

    # Workflow Definition (immutable during execution)
    nodes: List[Dict]          # Node definitions
    edges: List[Dict]          # Edge definitions

    # Workspace (per-workflow filesystem for nodes and agents)
    workspace_dir: str         # Absolute path to <DATA_DIR>/workspaces/<workflow_slug>/
                               # (Wave 14 — keyed by the human-readable slug,
                               # not the UUID id; see CLAUDE.md "Workflow Naming")

    # Execution Progress
    execution_order: List[List[str]]  # Computed layers
    current_layer: int         # Current layer index
    checkpoints: List[str]     # Completed node IDs

    # Timing
    started_at: float
    completed_at: float
    updated_at: float

    # Errors
    errors: List[Dict]         # Error records
```

### NodeExecution

```python
@dataclass
class NodeExecution:
    """State for a single node within an execution."""

    node_id: str
    node_type: str
    status: TaskStatus

    # Execution details
    input_hash: str            # For cache key
    output: Dict[str, Any]
    error: str

    # Timing
    started_at: float
    completed_at: float

    # Retry tracking (future)
    retry_count: int = 0
```

---

## Execution Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                         execute_workflow()                           │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  1. Create ExecutionContext                                          │
│     - Generate unique execution_id                                   │
│     - Initialize node_executions (all PENDING)                       │
│     - Compute execution layers (DAG analysis)                        │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  2. Persist Initial State                                            │
│     - Save to Redis: execution:{id}:state                           │
│     - Add to executions:active set                                   │
│     - Emit workflow_started event                                    │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  3. workflow_decide() Loop                                           │
│     ┌─────────────────────────────────────────────────────────────┐ │
│     │  a. Acquire distributed lock                                 │ │
│     │  b. Find ready nodes (deps satisfied)                        │ │
│     │  c. Execute batch (parallel if multiple)                     │ │
│     │  d. Update node states                                       │ │
│     │  e. Checkpoint to Redis                                      │ │
│     │  f. Release lock                                             │ │
│     │  g. Recurse until no ready nodes                             │ │
│     └─────────────────────────────────────────────────────────────┘ │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  4. Finalize                                                         │
│     - Set final workflow status                                      │
│     - Remove from executions:active                                  │
│     - Emit workflow_completed/failed event                           │
│     - Return results                                                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Recovery Mechanism

### Crash Detection

```
┌─────────────────────────────────────────────────────────────────────┐
│                      RecoverySweeper (Background)                    │
│                                                                      │
│  Every 60 seconds:                                                   │
│  1. Get all execution IDs from executions:active                     │
│  2. For each execution:                                              │
│     a. Load state from Redis                                         │
│     b. Check if already complete → remove from active                │
│     c. Find RUNNING nodes                                            │
│     d. Check heartbeat age                                           │
│        - If > 5 minutes → mark as stuck                              │
│  3. Trigger recovery callback for stuck executions                   │
└─────────────────────────────────────────────────────────────────────┘
```

### Startup Recovery

```python
async def scan_on_startup(self) -> List[str]:
    """Find executions that need recovery on server restart."""
    needs_recovery = []

    for execution_id in await cache.get_active_executions():
        ctx = await cache.load_execution_state(execution_id)

        if ctx.status == WorkflowStatus.RUNNING:
            age = time.time() - ctx.updated_at
            if age > HEARTBEAT_TIMEOUT:
                needs_recovery.append(execution_id)

    return needs_recovery
```

---

## Thread Safety

### Cross-Thread Event Dispatch

Historical pattern (Wave 15 note): APScheduler thread-pool callbacks were
the original motivation; APScheduler was retired in Wave 15.2 and the
Redis-Streams `dispatch_async` sibling in Wave 15.3. `event_waiter`
still captures the main loop at startup (`capture_main_loop()` in
`main.py`) so any future thread-context caller can hop onto it with
`asyncio.run_coroutine_threadsafe(...)`; every production caller today
runs on the main event loop and uses the sync `dispatch()` directly.

---

## Configuration

### Deployment Settings

```python
_deployment_settings = {
    "delay_between_runs": 1.0,      # Seconds between iterations
    "stop_on_error": False,          # Stop on first error
    "max_iterations": 0,             # 0 = unlimited
    "use_parallel_executor": True    # Use new parallel engine
}
```

### Execution Toggle

```python
# Automatic fallback
if use_parallel and settings.redis_enabled:
    return await self._execute_workflow_parallel(...)
else:
    return await self._execute_workflow_sequential(...)
```

---

## Comparison: Before vs After

| Aspect | Sequential (Before) | Parallel (After) |
|--------|---------------------|------------------|
| Execution | BFS traversal, one-by-one | DAG layers, concurrent |
| State | In-memory only | Redis-persisted |
| Recovery | None (lost on crash) | Automatic via sweeper |
| Idempotency | None | Input-hashed caching |
| Concurrency | Global flag blocks | Isolated contexts |
| Locking | None | Distributed Redis locks |
| Events | None | Redis Streams |

---

## Implemented Features

### Runtime Conditional Branching (conditions.py)
Edge conditions are evaluated at runtime using Prefect-style dynamic branching:
- 20+ operators: eq, neq, gt, lt, gte, lte, contains, exists, matches, in, starts_with, etc.
- Supports nested field access via dot notation (e.g., `result.status`)
- AND/OR condition groups with recursive evaluation

### Dead Letter Queue (dlq.py)
Failed nodes (after all retries exhausted) are stored for inspection and replay:
- `DLQHandler` - Active handler storing failed nodes with full context
- `NullDLQHandler` - No-op handler when DLQ disabled (Null Object pattern)
- WebSocket handlers: `get_dlq_entries`, `replay_dlq_entry`, `remove_dlq_entry`, `get_dlq_stats`

### Temporal Distributed Execution (Optional)
When `TEMPORAL_ENABLED=true`, each node executes as an independent Temporal activity:
- Per-node retry (3 attempts) and timeout (10 min default)
- Horizontal scaling across worker pool
- FIRST_COMPLETED pattern for dependency resolution
- Connection pooling for WebSocket activity execution

---

## Future Enhancements

### Planned
1. **Sub-Workflows** - Execute workflow as a node
2. **Metrics** - Prometheus integration
3. **Workflow Versioning** - Track definition versions

### Potential
1. **Distributed Workers** - Redis Streams consumer groups
2. **Priority Queues** - Task prioritization

---

## References

- **Netflix Conductor**: [Architecture](https://conductor-oss.github.io/conductor/devguide/architecture/index.html)
- **Prefect 3.0**: [Task Caching](https://docs.prefect.io/v3/develop/task-caching)
- **Redis Streams**: [Consumer Groups](https://redis.io/docs/data-types/streams/)
- **Kahn's Algorithm**: [Topological Sort](https://en.wikipedia.org/wiki/Topological_sorting#Kahn's_algorithm)
- **Event Sourcing**: [Martin Fowler](https://martinfowler.com/eaaDev/EventSourcing.html)
