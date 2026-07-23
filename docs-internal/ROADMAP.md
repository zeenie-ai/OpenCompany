# Execution Engine Roadmap

## Overview

This document tracks the implementation status of the robust workflow execution engine based on industry-standard patterns from Netflix Conductor, Prefect 3.0, and Redis Streams.

---

## Completed Features

### Phase 1: State Machine + Caching
- [x] `TaskStatus` enum (PENDING, SCHEDULED, RUNNING, COMPLETED, FAILED, CACHED, CANCELLED, WAITING, SKIPPED)
- [x] `WorkflowStatus` enum (PENDING, RUNNING, PAUSED, COMPLETED, FAILED, CANCELLED)
- [x] `ExecutionContext` dataclass with isolated state per workflow run
- [x] `NodeExecution` dataclass for tracking individual node state
- [x] `execute_node_cached()` with input hashing for idempotency (Prefect pattern)
- [x] `hash_inputs()` and `generate_cache_key()` utilities
- [x] Redis HASH storage for execution state

### Phase 2: Decide Pattern + Parallel Execution
- [x] `workflow_decide()` with distributed locking (Conductor pattern)
- [x] `execute_parallel_nodes()` using `asyncio.gather()` for Fork/Join (since removed; superseded by continuous scheduling via `asyncio.wait(FIRST_COMPLETED)`)
- [x] `_compute_execution_layers()` for DAG topological sort
- [x] `_find_ready_nodes()` to detect nodes with satisfied dependencies
- [x] Redis distributed locks via SETNX with TTL

### Phase 4: Crash Recovery
- [x] `RecoverySweeper` background task
- [x] Heartbeat tracking for running nodes
- [x] `scan_on_startup()` to find interrupted executions
- [x] Automatic recovery trigger for stuck nodes
- [x] Integration in `main.py` lifespan

### Phase 5: Event History
- [x] Event storage in Redis Streams per execution
- [x] Event types: workflow_started, node_started, node_completed, node_failed, workflow_completed
- [x] `add_event()` and `get_events()` methods in ExecutionCache

### Phase 6: Integration with Existing Handlers
- [x] `_node_executor_adapter()` bridging WorkflowExecutor to existing handlers
- [x] `_get_workflow_executor()` for lazy initialization with status callback
- [x] `execute_workflow()` dispatches to parallel or sequential based on settings
- [x] `_execute_workflow_parallel()` using new WorkflowExecutor
- [x] `_execute_workflow_sequential()` as fallback when Redis unavailable
- [x] `use_parallel_executor` deployment setting (default: True)

---

## Completed Features (Continued)

### Phase 3: Runtime Conditional Branching [COMPLETED]

**Goal**: Enable dynamic workflow paths based on node output values at runtime.

**Status**: COMPLETED - Full implementation of Prefect-style runtime conditional branching.

**Implementation**:
- Created `server/services/execution/conditions.py` with 20+ condition operators
- Updated `executor.py` `_find_ready_nodes()` to evaluate edge conditions
- Added `TaskStatus.SKIPPED` handling for unmatched conditional branches
- Created `client/src/types/EdgeCondition.ts` with TypeScript types and utilities
- Created `client/src/components/EdgeConditionEditor.tsx` for condition UI
- Created `client/src/components/ConditionalEdge.tsx` for custom edge rendering
- Integrated ConditionalEdge into Dashboard with edgeTypes registration

#### Implementation Tasks

1. **Edge Condition Schema**
   ```typescript
   // Frontend: client/src/types/
   interface EdgeCondition {
     field: string;           // Output field to check (e.g., "status", "result.success")
     operator: "eq" | "neq" | "gt" | "lt" | "gte" | "lte" | "contains" | "exists";
     value: any;              // Value to compare against
   }

   interface ConditionalEdge extends Edge {
     data?: {
       condition?: EdgeCondition;
       label?: string;        // Display label for the edge
     };
   }
   ```

2. **Condition Evaluator** (`server/services/execution/conditions.py`)
   ```python
   def evaluate_condition(condition: dict, output: dict) -> bool:
       """Evaluate edge condition against node output.

       Args:
           condition: {field, operator, value}
           output: Node execution output

       Returns:
           True if condition matches
       """
       field_value = get_nested_value(output, condition["field"])
       operator = condition["operator"]
       target_value = condition["value"]

       if operator == "eq":
           return field_value == target_value
       elif operator == "neq":
           return field_value != target_value
       elif operator == "gt":
           return field_value > target_value
       # ... etc
   ```

3. **Update `decide_next_node()` in executor.py**
   ```python
   def decide_next_node(self, ctx: ExecutionContext, current_node_id: str,
                        output: dict) -> List[str]:
       """Determine next nodes based on edge conditions.

       Returns list of node IDs to execute next (may be multiple for fork).
       """
       next_nodes = []
       for edge in ctx.edges:
           if edge.get("source") != current_node_id:
               continue

           condition = edge.get("data", {}).get("condition")
           if condition:
               if evaluate_condition(condition, output):
                   next_nodes.append(edge["target"])
           else:
               # No condition = always follow
               next_nodes.append(edge["target"])

       return next_nodes
   ```

4. **Frontend Edge Condition UI** (`client/src/components/EdgeConditionEditor.tsx`)
   - Modal to configure edge conditions
   - Field selector (from source node output schema)
   - Operator dropdown
   - Value input (type-aware based on field)
   - Visual indicator on conditional edges (different color/style)

5. **Edge Label Display** (`client/src/components/ConditionalEdge.tsx`)
   - Custom React Flow edge component
   - Shows condition summary as label
   - Different styling for conditional vs unconditional edges

#### Files Created/Modified

| File | Status | Description |
|------|--------|-------------|
| `server/services/execution/conditions.py` | DONE | Condition evaluation logic (20+ operators) |
| `server/services/execution/executor.py` | DONE | Updated `_find_ready_nodes()` with condition evaluation |
| `server/services/execution/models.py` | DONE | Added SKIPPED status handling |
| `client/src/types/EdgeCondition.ts` | DONE | Edge condition TypeScript types |
| `client/src/components/EdgeConditionEditor.tsx` | DONE | UI for editing conditions |
| `client/src/components/ConditionalEdge.tsx` | DONE | Custom edge rendering |
| `client/src/Dashboard.tsx` | DONE | Integrated edgeTypes for conditional edges |

---

### Phase 6: Integration - Connect WorkflowExecutor to Existing Handlers [COMPLETED]

**Goal**: Replace the sequential execution in `workflow.py` with the new parallel executor.

**Status**: COMPLETED - Parallel executor integrated with toggle for sequential fallback.

**Implementation**:
- Added `_node_executor_adapter()` to bridge WorkflowExecutor to existing handlers
- Added `_get_workflow_executor()` for lazy initialization with status callback
- Modified `execute_workflow()` to dispatch to parallel or sequential based on settings
- Added `_execute_workflow_parallel()` using new WorkflowExecutor
- Renamed original method to `_execute_workflow_sequential()` as fallback
- Added `use_parallel_executor` to deployment settings (default: True)
- Parallel execution automatically enabled when Redis is available

#### Implementation Tasks

1. **Create Adapter Function**
   ```python
   # server/services/workflow.py

   async def _node_executor_adapter(self, node_id: str, node_type: str,
                                    parameters: dict, context: dict) -> dict:
       """Adapter to bridge WorkflowExecutor to existing node handlers."""
       # Reuse existing execute_node logic
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

2. **Initialize WorkflowExecutor in WorkflowService**
   ```python
   def __init__(self, ...):
       # ... existing init ...

       # Initialize execution engine
       from services.execution import WorkflowExecutor, ExecutionCache
       self._execution_cache = ExecutionCache(cache)
       self._workflow_executor = WorkflowExecutor(
           cache=self._execution_cache,
           node_executor=self._node_executor_adapter,
           status_callback=self._broadcast_node_status,
       )
   ```

3. **Replace execute_workflow Implementation**
   ```python
   async def execute_workflow(self, nodes, edges, session_id, status_callback):
       """Execute workflow using parallel executor."""
       workflow_id = f"workflow_{session_id}"

       return await self._workflow_executor.execute_workflow(
           workflow_id=workflow_id,
           nodes=nodes,
           edges=edges,
           session_id=session_id,
           enable_caching=True,
       )
   ```

4. **Update deploy_workflow for Concurrent Executions**
   ```python
   async def deploy_workflow(self, nodes, edges, session_id, status_callback):
       """Deploy workflow with support for concurrent executions."""
       # Generate unique execution_id per deployment iteration
       # Track multiple active executions
       # Use WorkflowExecutor for each iteration
   ```

5. **Wire Status Callback to WebSocket Broadcaster**
   ```python
   async def _broadcast_node_status(self, node_id: str, status: str, data: dict):
       """Broadcast node status via WebSocket."""
       from services.status_broadcaster import get_status_broadcaster
       broadcaster = get_status_broadcaster()
       await broadcaster.update_node_status(node_id, status, data)
   ```

#### Files to Modify

| File | Changes |
|------|---------|
| `server/services/workflow.py` | Add WorkflowExecutor integration, adapter function |
| `server/routers/websocket.py` | Update handlers to use new executor |
| `server/core/container.py` | Add ExecutionCache to DI container |

---

### Phase 7: Retry Policies + Dead Letter Queue [COMPLETED]

**Goal**: Implement robust error handling with automatic retries and failed execution quarantine.

**Status**: COMPLETED - Full retry policies with exponential backoff and DLQ for failed executions.

**Implementation**:

#### RetryPolicy Dataclass (`server/services/execution/models.py`)
```python
@dataclass
class RetryPolicy:
    """Retry configuration for node execution."""
    max_attempts: int = 3
    initial_delay: float = 1.0       # seconds
    max_delay: float = 60.0          # seconds
    backoff_multiplier: float = 2.0
    retry_on_timeout: bool = True
    retry_on_connection_error: bool = True
    retry_on_server_error: bool = True  # 5xx errors

    def calculate_delay(self, attempt: int) -> float:
        """Exponential backoff: min(initial * multiplier^attempt, max_delay)"""

    def should_retry(self, error: str, attempt: int) -> bool:
        """Check if error is retryable based on type and attempt count"""
```

#### Default Retry Policies
```python
DEFAULT_RETRY_POLICIES = {
    "httpRequest": RetryPolicy(max_attempts=3, initial_delay=2.0),
    "webhookTrigger": RetryPolicy(max_attempts=1),  # Don't retry triggers
    "whatsappReceive": RetryPolicy(max_attempts=1),  # Don't retry triggers
    "aiAgent": RetryPolicy(max_attempts=2, initial_delay=5.0, max_delay=30.0),
    "openaiChatModel": RetryPolicy(max_attempts=2, initial_delay=5.0),
    "anthropicChatModel": RetryPolicy(max_attempts=2, initial_delay=5.0),
    "googleChatModel": RetryPolicy(max_attempts=2, initial_delay=5.0),
}
```

#### DLQEntry Dataclass (`server/services/execution/models.py`)
```python
@dataclass
class DLQEntry:
    """Dead Letter Queue entry for failed node executions."""
    id: str
    execution_id: str
    workflow_id: str
    node_id: str
    node_type: str
    error: str
    inputs: Dict[str, Any]
    retry_count: int
    created_at: float
    last_error_at: float
```

#### ExecutionCache DLQ Methods (`server/services/execution/cache.py`)
- `add_to_dlq(entry)` - Add failed node to DLQ with multi-index storage
- `get_dlq_entry(entry_id)` - Get single DLQ entry
- `get_dlq_entries(workflow_id, node_type, limit)` - Query entries with filtering
- `remove_from_dlq(entry_id)` - Remove after successful replay
- `update_dlq_entry(entry_id, retry_count, error)` - Update after retry attempt
- `get_dlq_stats()` - Get DLQ statistics (total, by node type, by workflow)
- `purge_dlq(workflow_id, node_type, older_than)` - Bulk remove entries

#### WorkflowExecutor Retry Integration (`server/services/execution/executor.py`)
- `_execute_node_with_retry()` - Wraps node execution with retry loop
- `_add_to_dlq()` - Adds failed nodes to DLQ after exhausted retries
- `replay_dlq_entry()` - Re-execute failed node from DLQ
- `_execute_parallel_nodes()` - Updated to use retry wrapper (helper since removed; continuous scheduling is the only implementation)
- `_execute_single_node()` - Updated to use retry wrapper

#### WebSocket API Handlers (`server/routers/websocket.py`)
| Handler | Description |
|---------|-------------|
| `get_dlq_entries` | Get DLQ entries with filtering |
| `get_dlq_entry` | Get single entry by ID |
| `get_dlq_stats` | Get DLQ statistics |
| `replay_dlq_entry` | Re-execute failed node |
| `remove_dlq_entry` | Remove entry without replay |
| `purge_dlq` | Bulk remove entries |

#### Redis Key Schema (DLQ)
```
dlq:entries:{id}          -> HASH (entry data)
dlq:workflow:{workflow_id} -> LIST (entry IDs for workflow)
dlq:node_type:{node_type}  -> LIST (entry IDs by type)
dlq:all                    -> SET (all entry IDs)
```

#### Files Modified
| File | Changes |
|------|---------|
| `server/services/execution/models.py` | Added RetryPolicy, DLQEntry, DEFAULT_RETRY_POLICIES |
| `server/services/execution/cache.py` | Added DLQ methods (add, get, remove, update, purge, stats) |
| `server/services/execution/executor.py` | Added retry wrapper, DLQ integration, replay method |
| `server/services/execution/__init__.py` | Export new classes and functions |
| `server/routers/websocket.py` | Added 6 DLQ WebSocket handlers |

---

### Phase 8: Temporal Distributed Execution [COMPLETED]

**Goal**: Enable horizontal scaling via Temporal for production deployments.

**Status**: COMPLETED - Optional Temporal integration with per-node activities.

**Implementation**:

#### Architecture
When `TEMPORAL_ENABLED=true`, each workflow node executes as an independent Temporal activity:
- Per-node retry policy (3 attempts with exponential backoff)
- Per-node timeout (10 min default)
- Horizontal scaling across worker pool
- FIRST_COMPLETED pattern for dependency resolution

#### Key Components
| File | Purpose |
|------|---------|
| `server/services/temporal/workflow.py` | MachinaWorkflow orchestrator (pure orchestration, no business logic) |
| `server/services/temporal/activities.py` | NodeExecutionActivities class with connection pooling |
| `server/services/temporal/executor.py` | TemporalExecutor interface matching WorkflowExecutor |
| `server/services/temporal/worker.py` | TemporalWorkerManager + `run_standalone_worker()` |
| `server/services/temporal/client.py` | Client wrapper with runtime heartbeat disabled |

#### Configuration
```env
TEMPORAL_ENABLED=true
TEMPORAL_SERVER_ADDRESS=localhost:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=machina-tasks
```

#### Execution Routing (`workflow.py`)
1. If `TEMPORAL_ENABLED=true` and Temporal configured -> `_execute_temporal()`
2. Else -> `_execute_sequential()` (fallback)

(The Redis-parallel `_execute_parallel()` branch is unreachable in all shipped configs; effective routing is Temporal -> sequential.)

---

### Phase 9: Expanded Node Types [COMPLETED]

**Goal**: Comprehensive workflow automation coverage.

**Status**: COMPLETED - 58 total nodes across 14 categories.

**Implementation**:

#### Node Categories (58 total)
| Category | Count | Nodes |
|----------|-------|-------|
| AI Chat Models | 6 | openai, anthropic, gemini, openrouter, groq, cerebras |
| AI Agents & Memory | 3 | aiAgent, chatAgent, simpleMemory |
| AI Skills | 9 | claude, whatsapp, memory, maps, http, scheduler, android, code, custom |
| AI Tools | 4 | calculator, currentTime, webSearch, androidTool (androidTool since retired; services connect directly to `input-tools`) |
| Android Services | 16 | battery, network, system, location, apps(2), automation(6), sensors(2), media(2) |
| Android Device | 1 | androidDeviceSetup |
| WhatsApp | 4 | send, connect, receive, chatHistory |
| Location/Maps | 3 | createMap, addLocations, showNearbyPlaces |
| Utility | 5 | httpRequest, webhookTrigger, webhookResponse, chatTrigger, console |
| Code | 2 | pythonExecutor, javascriptExecutor |
| Scheduler | 2 | timer, cronScheduler |
| Chat | 2 | chatSend, chatHistory |
| Workflow | 1 | start |

#### WebSocket Handlers (51 total)
- Execution: 4 handlers
- Parameters: 7 handlers
- Status & Variables: 6 handlers
- AI Services: 6 handlers
- Android Services: 7 handlers
- Deployment: 4 handlers
- Triggers & Events: 3 handlers
- Workflow Management: 4 handlers
- Dead Letter Queue: 5 handlers
- WhatsApp: 8 handlers
- Maps: 1 handler
- Skills & Chat: 6 handlers

---

## Pending Features

### Future Enhancements

#### Sub-Workflow Support
- Execute workflow as a node within another workflow
- Parent-child execution tracking
- Output propagation from sub-workflow

#### Workflow Versioning
- Track workflow definition versions
- Support running specific versions
- Migration between versions

#### Metrics & Observability
- Execution duration histograms
- Success/failure rates
- Node-level performance tracking
- Integration with Prometheus/Grafana

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        WebSocket Router                          │
│                   (routers/websocket.py)                        │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      WorkflowService                             │
│                   (services/workflow.py)                         │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Node Handler Registry                                    │   │
│  │ - AI handlers, Android handlers, HTTP handlers, etc.     │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    WorkflowExecutor                              │
│              (services/execution/executor.py)                    │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ workflow_    │  │ execute_     │  │ _compute_execution_  │  │
│  │ decide()     │  │ parallel_    │  │ layers()             │  │
│  │              │  │ nodes()      │  │                      │  │
│  │ Conductor    │  │              │  │ DAG topological      │  │
│  │ pattern      │  │ asyncio.     │  │ sort                 │  │
│  │              │  │ gather()     │  │                      │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ExecutionCache                               │
│               (services/execution/cache.py)                      │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Result       │  │ Distributed  │  │ Event History        │  │
│  │ Caching      │  │ Locks        │  │ (Redis Streams)      │  │
│  │ (Prefect)    │  │ (Conductor)  │  │                      │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Redis                                    │
│                                                                  │
│  execution:{id}:state     execution:{id}:events                 │
│  result:{exec}:{node}     lock:execution:{id}                   │
│  heartbeat:{exec}:{node}  executions:active                     │
│  dlq:entries:{id}         dlq:all                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Testing Checklist

### Unit Tests
- [ ] `test_models.py` - TaskStatus, WorkflowStatus, ExecutionContext serialization
- [ ] `test_cache.py` - Result caching, distributed locks, event storage
- [ ] `test_executor.py` - Parallel execution, decide pattern, layer computation
- [ ] `test_recovery.py` - Sweeper detection, startup scan
- [ ] `test_conditions.py` - Edge condition evaluation

### Integration Tests
- [ ] Execute simple linear workflow
- [ ] Execute workflow with parallel branches
- [ ] Execute workflow with conditional edges
- [ ] Crash recovery simulation
- [ ] Concurrent workflow execution

### Performance Tests
- [ ] Parallel execution speedup vs sequential
- [ ] Redis operation latency
- [ ] Large workflow (100+ nodes) execution time

---

## References

- **Netflix Conductor**: [Architecture](https://conductor-oss.github.io/conductor/devguide/architecture/index.html)
- **Prefect 3.0**: [Task Caching](https://docs.prefect.io/v3/develop/task-caching)
- **Redis Streams**: [Consumer Groups](https://redis.io/docs/data-types/streams/)
- **Plan Document**: `C:\Users\Tgroh\.claude\plans\buzzing-gathering-pie.md`
