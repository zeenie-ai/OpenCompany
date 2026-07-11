# Agent-to-Agent Delegation (Nested Agents)

How memory, parameters, and execution context flow when one AI agent delegates work to another agent connected as a tool.

> **Related docs:** [tool_building_pipeline.md](./tool_building_pipeline.md) for the underlying `_build_tool_from_node` + `execute_tool` pipeline that backs `delegate_to_*` (delegation tools share the same StructuredTool construction path as other AI tools — what makes them special is the fire-and-forget background task and the `{"status": "delegated"}` lifecycle opt-out documented there).

## Overview

AI Agents can delegate tasks to other agents connected to their `input-tools` handle. The parent agent's LLM decides when to call the child agent by invoking a `delegate_to_<agent>` tool.

**Two execution paths** depending on whether the parent is running under Temporal F4.B:

- **F4.B (default, `TEMPORAL_AGENT_WORKFLOW_ENABLED=true`):** the delegation tool dispatch inside `AgentWorkflow` spawns the child as a **child `AgentWorkflow`** via `workflow.execute_child_workflow`. The LLM's `{task, context}` args travel as `child_context["invocation"]` — the per-invocation input contract: `prepare_agent_payload` applies it AFTER its config resolution (`task` → system_message, `context`-or-`task` → prompt), so the child's stored parameters (including the empty default `prompt` saved on drop) never override the delegated task. Calls with both fields empty are rejected at the parent's call boundary (tool-error to the LLM, no spawn). Parent's `node_id` is passed in `child_context["parent_node_id"]` so the child's `_emit_phase` mirrors progress onto the parent canvas badge. Parent awaits the child's result synchronously through the Temporal child-workflow handle — see [TEMPORAL_ARCHITECTURE.md §F4.B](./TEMPORAL_ARCHITECTURE.md#agent-as-child-workflow-f4b).
- **Legacy / F4.A-only:** the rest of this doc. `_execute_delegated_agent` spawns the child as an independent `asyncio.Task` and the parent continues working immediately — the **fire-and-forget** pattern. Applies the same task/context semantics by overwriting `child_params` AFTER loading them from the DB. Still the path for `rlm_agent` / `claude_code_agent` (excluded from `AGENT_WORKFLOW_TYPES`) and any deployment with F4.B disabled.

In both paths the parent passes only a `task` string and optional `context` string — per-invocation input that always wins over stored configuration. Everything else -- the child's provider, model, memory, skills, and tools -- comes from the child's own configuration and workflow connections.

## Architecture

```
Parent Agent (chatAgent / aiAgent / specialized)
  |
  |  LLM calls: delegate_to_ai_agent(task="...", context="...")
  |
  v
tool_executor callback (closure in ai.py)
  |  Injects: ai_service, database, nodes, edges, workflow_id,
  |           parent_node_id, execution_id
  |
  v
execute_tool() dispatcher (handlers/tools.py)
  |  Routes node_type in AI_AGENT_TYPES to _execute_delegated_agent()
  |
  v
_execute_delegated_agent() (handlers/tools.py)
  |  1. Fetches child_params from DATABASE
  |  2. Injects API key + default model from credential store
  |  3. task -> system_message (directive),
  |     context-or-task -> prompt
  |  4. Builds child_context with all nodes/edges
  |  5. Spawns asyncio.create_task()
  |  6. Returns immediately: {status: "delegated", task_id: "..."}
  |
  v                                           v
Parent continues reasoning            Child runs independently
(non-blocking)                           |
                                         v
                                    plugin_cls().execute(child_node_id, child_params, child_ctx)
                                         |  (BaseNode.execute -> the agent plugin's
                                         |   @Operation method in nodes/agent/<plugin>/)
                                         |
                                    collect_agent_connections(child_node_id)
                                         |  (services/plugin/edge_walker.py)
                                         |  Filters edges by child's ID
                                         |  Finds child's own memory/skills/tools/task
                                         |
                                    AIService.execute_agent() / execute_chat_agent()
                                         |  Uses child's provider/model/system_message
                                         |  Uses child's memory (isolated session)
                                         |  Uses child's skills + tools
                                         |  system_message = delegated task,
                                         |  prompt = delegated context
                                         |
                                    Result broadcast via WebSocket
                                    (NOT returned to parent LLM)
```

## The Delegation Chain (7 Steps)

### Step 1: Parent Builds Tool from Child Agent Node

**File:** `server/services/ai.py`, `_build_tool_from_node()`

When the parent agent starts executing and finds a child agent connected to its `input-tools` handle, `_build_tool_from_node()` is called during tool setup:

```python
for tool_info in tool_data:
    tool, config = await self._build_tool_from_node(tool_info)
    tools.append(tool)
    tool_configs[tool.name] = config
```

For agent node types, the method:

1. Resolves the tool name via `_resolve_default_tool_name_description(node_type)` — a closure inside `_build_tool_from_node()` that reads the plugin class's `tool_name` ClassVar (each agent plugin declares `tool_name = "delegate_to_<x>"`). There is no central `DEFAULT_TOOL_NAMES` dict; the name comes from the plugin class via `services.node_registry.get_node_class`. The delegation branch is gated by `_get_tool_schema()`'s `_AGENT_DELEGATION_TYPES` tuple — the **15-agent delegatable set** (`aiAgent`, `chatAgent`, `android_agent`, `coding_agent`, `web_agent`, `task_agent`, `social_agent`, `travel_agent`, `tool_agent`, `productivity_agent`, `payments_agent`, `consumer_agent`, `autonomous_agent`, `orchestrator_agent`, `ai_employee`) plus the two bypass-loop agents `rlm_agent` and `claude_code_agent`. (`AI_AGENT_TYPES` in `server/constants.py` mirrors this same set; `codex_agent` is not currently in it.)

2. Creates a `DelegateToAgentSchema` Pydantic model in `_get_tool_schema()` with two fields (`task` -> the child's mission directive / system message, `context` -> the child's input data / prompt):
   ```python
   class DelegateToAgentSchema(BaseModel):
       task: str = Field(
           description=f"The mission directive for '{agent_label}'. ..."
       )
       context: Optional[str] = Field(
           default=None,
           description="Input data or specific details the agent needs to work with, ..."
       )
   ```

3. Returns a config dict:
   ```python
   config = {
       'node_type': node_type,          # e.g., 'aiAgent'
       'node_id': node_id,              # child's node ID
       'parameters': node_params,       # child's stored params (from tool_info)
       'label': node_label,
       'connected_services': []
   }
   ```

The LLM sees a tool like `delegate_to_ai_agent(task="...", context="...")`. It does NOT see or fill the child's actual configuration (provider, model, temperature, etc.).

---

### Step 2: Tool Executor Callback Injects Services

**File:** `server/services/ai.py`, `tool_executor` / `chat_tool_executor` closures

Both `execute_agent()` and `execute_chat_agent()` define nearly identical tool executor callbacks as closures. When the parent's LLM decides to call the delegation tool, the callback fires.

The callback captures from outer scope:

| Variable | Source | Purpose |
|----------|--------|---------|
| `tool_configs` | Built in step 1 | Maps tool name to config dict |
| `self` | AIService instance | Provides `.database`, `.auth` |
| `broadcaster` | Function parameter | WebSocket status broadcasts |
| `workflow_id` | Function parameter | Scopes broadcasts to workflow |
| `context` | Function parameter | Contains `nodes` and `edges` arrays |

Before calling `execute_tool()`, the callback injects the service + graph fields into the config:

```python
config["workflow_id"]    = workflow_id
config["ai_service"]     = self               # AIService instance (shared)
config["database"]       = self.database      # Database instance (shared)
config["parent_node_id"] = node_id            # parent's node id (duplicate tracking)
if context:
    config["nodes"]        = context.get("nodes", [])   # ALL workflow nodes
    config["edges"]        = context.get("edges", [])   # ALL workflow edges
    config["workspace_dir"] = context.get("workspace_dir", "")
    config["execution_id"]  = context.get("execution_id")  # stable per-run id
```

These injected fields are what enable the child agent to discover its own connections and execute independently. Without them, the child would have no access to the workflow graph or services.

---

### Step 3: Dispatch to Delegation Handler

**File:** `server/services/handlers/tools.py`, `execute_tool()`

The dispatcher checks `node_type` against the `AI_AGENT_TYPES` frozenset (`server/constants.py`) and routes every agent type to `_execute_delegated_agent()`:

```python
from constants import AI_AGENT_TYPES

if node_type == "_builtin_check_delegated_tasks":
    return await _execute_check_delegated_tasks(tool_args, config)

if node_type in AI_AGENT_TYPES:
    return await _execute_delegated_agent(tool_args, config)
```

`AI_AGENT_TYPES` is the single source of truth: the 15 standard delegatable agents plus `rlm_agent` and `claude_code_agent`.

---

### Step 4: Delegated Agent Execution (Fire-and-Forget)

**File:** `server/services/handlers/tools.py`, `_execute_delegated_agent()`

This is where the actual parameter assembly happens.

**4a. Extract injected services from config:**
```python
ai_service  = config.get("ai_service")
database    = config.get("database")
nodes       = config.get("nodes", [])
edges       = config.get("edges", [])
workflow_id = config.get("workflow_id")
```

A `(parent_node_id, node_id, task_hash)` duplicate guard (`_active_delegations`) short-circuits with `status: "ALREADY_DELEGATED"` if the LLM calls the same delegation twice. A per-child counter (`_active_delegated_nodes`) keeps the child's canvas glow alive past the parent run's end (the background task outlives the `delegate_to_<x>` tool return).

**4b. Fetch child's OWN parameters from database:**
```python
child_params = await database.get_node_parameters(node_id) or {}
```

The child's stored parameters (provider, model, system message, etc.) come from the database -- whatever the user configured in the child node's parameter panel. They are NOT inherited from the parent.

**4c. Inject API key if missing:**
```python
if not child_params.get("api_key"):
    provider = detect_ai_provider(node_type, child_params)
    key = await ai_service.auth.get_api_key(provider, "default")
    if key:
        child_params["api_key"] = key
```

The child gets its API key from the credential store based on its own provider setting. If the child is configured for Anthropic and the parent uses OpenAI, the child gets the Anthropic key.

**4d. Inject default model if not set:**
```python
if not child_params.get("model"):
    provider = detect_ai_provider(node_type, child_params)
    models = await ai_service.auth.get_stored_models(provider, "default")
    if models:
        child_params["model"] = models[0]
```

**4e. Map the delegated task/context onto the child's params:**
```python
# task -> mission directive (system message), context -> input data (prompt)
child_params["system_message"] = task_description
child_params.pop("systemMessage", None)   # drop pre-migration camelCase mirror
child_params["prompt"] = task_context if task_context else task_description
```

These two strings are the **only inputs the parent passes to the child**. `task` becomes the child's `system_message` (its mission directive); `context` becomes its `prompt` (its input data), falling back to the task text when no context is given. Everything else comes from the child's own stored configuration.

**4f. Build child execution context:**
```python
child_context = {
    "nodes": nodes,              # ALL workflow nodes (not just child's)
    "edges": edges,              # ALL workflow edges (not just child's)
    "workflow_id": workflow_id,  # Parent's workflow ID (for status scoping)
    "outputs": {},               # Empty -- child starts fresh
    "parent_task_id": task_id,   # Link back to parent's delegation task
    "execution_id": config.get("execution_id"),  # shared per-run id (session-keyed tools)
}
```

**4g. Spawn as background task and return immediately:**
```python
task = asyncio.create_task(run_child_agent())
_delegated_tasks[task_id] = task

return {
    "success": True,
    "status": "delegated",
    "task_id": task_id,
    "agent_node_id": node_id,
    "agent_name": agent_label,
    "message": f"Task delegated to '{agent_label}'. Agent is now working independently...",
}
```

Inside `run_child_agent()`, the child executes through its own plugin class (Wave 11.E.3 — the legacy `handle_ai_agent` / `handle_chat_agent` imports are gone). Every agent type owns an `@Operation` method that wraps `prepare_agent_call` + `AIService` dispatch, so delegation just goes through `BaseNode.execute`:

```python
from services.node_registry import get_node_class
from services.plugin import NodeContext

plugin_cls = get_node_class(node_type)
instance = plugin_cls()
child_ctx = NodeContext.from_legacy(
    node_id=node_id, node_type=node_type, context=child_context,
)
result = await instance.execute(node_id, child_params, child_ctx)
```

---

### Step 5: Child Handler Collects Its Own Connections

**File:** `server/nodes/agent/_inline.py`, `prepare_agent_call()` (called by every agent plugin's `@Operation`)

The spawned child's `execute()` runs `prepare_agent_call()`, which calls `collect_agent_connections()` (from `server/services/plugin/edge_walker.py`) with **its own node_id**. The function returns a **5-tuple** — `(memory_data, skill_data, tool_data, input_data, task_data)`:

```python
memory_data, skill_data, tool_data, input_data, task_data = await collect_agent_connections(
    node_id,       # CHILD's node ID
    child_context, # Contains ALL nodes/edges
    database,
    log_prefix="[AI Agent]",
)
```

---

### Step 6: Connection Filtering by Node ID

**File:** `server/services/plugin/edge_walker.py`, `collect_agent_connections()`

This function receives ALL nodes/edges but filters by the child's `node_id`:

```python
for edge in edges:
    if edge.get("target") != node_id:   # Only edges pointing TO this child
        continue

    target_handle = edge.get("targetHandle")
    source_node_id = edge.get("source")
```

For each matching edge, it checks the target handle:

| Handle | Action |
|--------|--------|
| `input-memory` | Loads markdown memory content from child's connected `simpleMemory` node |
| `input-skill` | Loads skill instructions; expands masterSkill into individual enabled skills |
| `input-tools` | Discovers tool nodes; for androidTool, finds connected Android services |
| `input-main` / `input-chat` | Reads upstream node output for auto-prompt fallback |
| `input-task` | Collects `taskTrigger` output for the conversational task-report pattern |

The child only gets connections that are physically wired to it in the workflow graph. If the child has no memory node connected, `memory_data` is `None`. If the child has its own tools, it gets those tools -- and can delegate further.

---

### Step 7: Child Executes with Its Own Resources

**File:** `server/services/ai.py`, `execute_agent()` / `execute_chat_agent()`

The child now executes with:

| Resource | Source |
|----------|--------|
| Prompt | Delegated task from parent's LLM |
| System message | Child's own (from its DB params) |
| Provider / model | Child's own (from its DB params) |
| API key | Injected from credential store based on child's provider |
| Temperature / max tokens | Child's own (from its DB params) |
| Memory | Child's own connected simpleMemory node (if any) |
| Skills | Child's own connected skill nodes (if any) |
| Tools | Child's own connected tool nodes (if any) |

The child's result is broadcast to the UI via WebSocket but is NOT fed back into the parent's reasoning loop.

---

## Memory Isolation

Parent and child agents have completely separate memory systems:

| Aspect | Parent Agent | Child Agent |
|--------|-------------|-------------|
| Memory source | Parent's connected simpleMemory | Child's connected simpleMemory |
| Session ID | From parent's memory node params | From child's memory node params |
| Memory content | Parent's conversation markdown | Child's conversation markdown |
| Vector store | `_memory_vector_stores[parent_session]` | `_memory_vector_stores[child_session]` |
| Memory updates | Parent appends its own exchanges | Child appends its own exchanges |
| Shared? | **No** -- completely isolated | **No** -- completely isolated |

If the child has **no memory node connected**, it has no conversation history and executes statelessly with just the delegated task prompt.

---

## Parameter Flow Summary

### What the parent passes to the child

| Parameter | How it arrives |
|-----------|---------------|
| `task` (string) | Parent's LLM generates it via `DelegateToAgentSchema` tool call |
| `context` (optional string) | Parent's LLM generates it via `DelegateToAgentSchema` tool call |
| `workflow_id` | Injected by tool_executor callback from parent's execution context |
| `nodes` / `edges` | Injected by tool_executor callback -- ALL workflow nodes and edges |
| `ai_service` / `database` | Injected by tool_executor callback -- shared instances (by reference) |

### What the child gets from its own config

| Parameter | Source |
|-----------|--------|
| `provider` | Child's DB params (e.g., `'openai'`, `'anthropic'`) |
| `model` | Child's DB params (e.g., `'gpt-4o'`), or first stored model as fallback |
| `systemMessage` | Child's DB params |
| `temperature` | Child's DB params |
| `max_tokens` / `maxTokens` | Child's DB params |
| `thinkingEnabled` / `thinkingBudget` | Child's DB params |
| `api_key` | Credential store, resolved by child's own provider |
| Memory | Child's own connected simpleMemory node |
| Skills | Child's own connected skill nodes |
| Tools | Child's own connected tool nodes |

### What the parent does NOT pass

- Provider, model, temperature, max tokens, system message
- Memory or conversation history
- Skills or skill instructions
- Tool configurations
- API keys

---

## What the Parent Receives Back

### Immediate Response (Fire-and-Forget)

The parent LLM receives this immediate response from the tool call:

```json
{
  "success": true,
  "status": "delegated",
  "task_id": "delegated_node123_a1b2c3d4",
  "agent_node_id": "child_agent_node_123",
  "agent_name": "Research Agent",
  "message": "Task delegated to 'Research Agent'. Agent is now working independently..."
}
```

The parent does NOT wait for the child's result. The child's output is broadcast to the UI via WebSocket (`broadcaster.update_node_status`).

### Blocking Delegation for Bridged Cloud Agents (delegation_wait_seconds)

Fire-and-forget assumes the parent loop can poll cheaply — a local LLM turn costs milliseconds. A bridged cloud agent (`vertex_managed_agent`) pays a full Interactions API round trip per poll, so it opts into a blocking wait instead:

- The bridge sets `config["delegation_wait_seconds"]` (node param, default 600s, clamped to the remaining Temporal activity budget) on the dispatch config for agent-type tools only.
- `_execute_delegated_agent` then awaits the spawned child via `wait_for_delegation` (`asyncio.wait_for(asyncio.shield(task))` — a timeout cancels the WAITER, never the child) and returns the child's REAL result as the function result, shaped by `_delegation_result_reply`.
- The reply carries `delegation_lifecycle: True`; `execute_tool` pops it and skips its terminal `success` broadcast (the child's `run_child_agent` already owns the terminal status — an awaited `error` result must not be stomped by a `success` glow).
- On timeout the child keeps running in the background and the parent receives the standard `status: "delegated"` ack; the vertex bridge declares `check_delegated_tasks` alongside any `delegate_to_*` tool (mirroring the native auto-injection) so the cloud agent can retrieve the result on a later turn. An identical re-call after a timeout hits the `_active_delegations` dedupe and awaits the existing in-flight task instead of duplicating work.
- Absent `delegation_wait_seconds` (all native agents), behavior is byte-identical fire-and-forget.

Contract locked by `TestDelegationWait` (`tests/nodes/test_ai_tools.py`) and the vertex bridge tests (`tests/nodes/test_vertex_agents.py`).

### Result Retrieval (check_delegated_tasks Tool)

When a parent agent has delegation tools, a `check_delegated_tasks` tool is automatically injected. The parent LLM can call this tool to check on child status and retrieve results:

```python
# LLM calls:
check_delegated_tasks(task_ids=["delegated_abc123"])

# Returns:
{
  "total_tasks": 1,
  "completed": 1,
  "running": 0,
  "errors": 0,
  "tasks": [{
    "task_id": "delegated_abc123",
    "status": "completed",
    "agent_name": "Research Agent",
    "result": "The research findings show..."
  }]
}
```

### 3-Layer Result Storage (Celery AsyncResult / Ray ObjectRef Pattern)

Results are stored in a 3-layer hierarchy for fast access and durability:

| Layer | Storage | Survives Restart | Access Speed |
|-------|---------|------------------|--------------|
| 1 | `asyncio.Task` in `_delegated_tasks` | No | O(1), task.result() |
| 2 | `_delegation_results` dict | No | O(1), dict lookup |
| 3 | `NodeOutput` SQLite table | Yes | DB query |

**Layer 1**: Live tasks are tracked in `_delegated_tasks`. When a task completes, `task.result()` extracts the return value.

**Layer 2**: Results are cached in `_delegation_results` dict immediately after child completion/error. This survives the task cleanup in the `finally` block.

**Layer 3**: Results are persisted to the existing `NodeOutput` table with `session_id="delegation_{task_id}"`. This survives server restarts indefinitely.

```python
# On child completion:
_delegation_results[task_id] = {
    "task_id": task_id,
    "status": "completed",
    "agent_name": agent_label,
    "result": result.get('result', {}).get('response', ...),
}

# Persist to NodeOutput table
await database.save_node_output(
    node_id=node_id,
    session_id=f"delegation_{task_id}",
    output_name="delegation_result",
    data={...}
)
```

### Auto-Injection Logic

The `check_delegated_tasks` tool is only injected when the parent has delegation tools:

```python
# In execute_agent() and execute_chat_agent():
if any(name.startswith('delegate_to_') for name in tool_configs):
    check_info = {
        'node_type': '_builtin_check_delegated_tasks',
        'node_id': f'{node_id}_check_tasks',
        'parameters': {},
        'label': 'Check Delegated Tasks',
    }
    check_tool, check_config = await self._build_tool_from_node(check_info)
    if check_tool:
        tools.append(check_tool)
        tool_configs[check_tool.name] = check_config
```

---

## Recursive Delegation

If the child agent has other agents connected to its own `input-tools` handle, it can delegate further. Each level follows the same pattern:

1. Child's `_build_tool_from_node()` creates a `DelegateToAgentSchema` tool for the grandchild
2. Child's `tool_executor` callback injects services and the same `nodes`/`edges` graph
3. Grandchild spawns as its own `asyncio.create_task()`
4. Grandchild collects its own connections by filtering edges with its own `node_id`

There is no hard recursion limit. Each agent in the chain is memory-isolated and independently configured.

---

## Status Broadcasting

Every delegated agent broadcasts status at key phases via WebSocket:

| Phase | When | Broadcast to |
|-------|------|-------------|
| `delegated_task` | Child starts executing | Child's node ID |
| `initializing` | Creating LLM model | Child's node ID |
| `loading_memory` | Parsing markdown history | Child's node ID |
| `building_tools` | Converting tool nodes to StructuredTool | Child's node ID |
| `invoking_llm` | Calling model | Child's node ID |
| `executing_tool` | Running a tool called by child's LLM | Tool node ID |
| `delegated_complete` | Child finishes successfully | Child's node ID |
| `delegated_error` | Child fails | Child's node ID |

All broadcasts include `workflow_id` for UI scoping. The frontend uses these to show real-time execution animations on the child agent node.

---

## Key Files

| File | Role |
|------|------|
| `server/services/ai.py` | `_build_tool_from_node()`, `_get_tool_schema()` (incl. `_AGENT_DELEGATION_TYPES` + `DelegateToAgentSchema`), `_resolve_default_tool_name_description()`, `tool_executor` / `chat_tool_executor` closures, `execute_agent()` / `execute_chat_agent()` |
| `server/services/plugin/edge_walker.py` | `collect_agent_connections()` (5-tuple: memory / skill / tool / input / task) |
| `server/nodes/agent/_inline.py` | `prepare_agent_call()` — the 3-step pre-dispatch flow (connection collection + param prep) every agent plugin's `@Operation` runs |
| `server/nodes/agent/<plugin>/__init__.py` | Per-agent plugin classes (`ai_agent`, `chat_agent`, the specialized agents) with their delegation `tool_name` ClassVar + `@Operation` execute method |
| `server/services/handlers/tools.py` | `execute_tool()` dispatcher, `_execute_delegated_agent()`, `_execute_check_delegated_tasks()`, `_delegated_tasks` / `_delegation_results` / `_active_delegations` tracking |
| `server/constants.py` | `AI_AGENT_TYPES` frozenset, `detect_ai_provider()` |
| `client/src/components/AIAgentNode.tsx` | Frontend rendering of execution phase animations |
---

## Key Findings

1. **Only task/context strings flow from parent to child.** No other parameters (model, temperature, memory, skills) are inherited.

2. **Child configuration comes from its own database params.** Whatever the user set in the child's parameter panel is what the child uses.

3. **Memory is fully isolated.** Parent and child have separate simpleMemory nodes with separate session IDs and separate vector stores.

4. **The child sees the full workflow graph** via `nodes`/`edges`, but `collect_agent_connections()` filters by the child's own `node_id`, so it only gets resources physically connected to it.

5. **Services are shared, not duplicated.** `ai_service` and `database` are passed by reference. They share the same credential store and DB connection.

6. **Delegation is non-blocking.** `asyncio.create_task()` spawns the child; the parent continues immediately. The child's result is NOT returned to the parent's LLM.

7. **Recursive delegation is supported.** If the child has agents connected to its own `input-tools`, it can delegate further with no hard depth limit.

8. **Task Trigger enables workflow-based result handling.** The `taskTrigger` node fires when delegated tasks complete, allowing workflows to process child agent results.

---

## Event-Driven Task Completion (taskTrigger)

In addition to the `check_delegated_tasks` tool (LLM-driven result checking), the system provides an event-driven approach via the `taskTrigger` workflow node. This enables parent workflows to react to child completion without polling.

### Architecture

```
Child Agent completes (success or error)
       ↓
tools.py: _execute_delegated_agent.run_child_agent()
       ↓
broadcaster.send_custom_event('task_completed', event_data)
       ↓
event_waiter dispatch ('task_completed', event_data)
       ↓
Matching taskTrigger nodes resolve their Futures
       ↓
Downstream workflow nodes execute with delegation result
```

> Note (Wave 12/13): `taskTrigger` is one of the canary-registered trigger types. Under the canary path the producer's CloudEvents envelope routes via `dispatch.emit`; the legacy `event_waiter.register` / `send_custom_event` path is retained for the in-process fallback. See [event_framework.md](./event_framework.md) and the trigger-architecture notes in CLAUDE.md.

### Event Data Schema

The `task_completed` event carries:

```python
{
    'task_id': str,           # e.g., "delegated_abc123_xyz"
    'status': str,            # 'completed' or 'error'
    'agent_name': str,        # Label of child agent
    'agent_node_id': str,     # Node ID of child agent
    'parent_node_id': str,    # Node ID of parent that delegated
    'result': str,            # Response text (if completed)
    'error': str,             # Error message (if error)
    'workflow_id': str,       # For scoping
}
```

### Filter Options

The `taskTrigger` node supports filtering to match specific delegation events:

| Filter | Description |
|--------|-------------|
| `task_id` | Exact match on specific task ID |
| `agent_name` | Partial match on child agent name |
| `status_filter` | `all`, `completed`, or `error` |
| `parent_node_id` | Only trigger for delegations from specific parent |

### Usage Example

```
[Parent Agent] --delegates--> [Research Agent]
                                    ↓ (completes)
                              [task_completed event]
                                    ↓
                         [Task Trigger]
                            (filters: agent_name="Research")
                                    ↓
                         [Process Results Node]
                            (uses {{taskTrigger.result}})
```

### Key Files

| File | Changes |
|------|---------|
| `server/services/event_waiter.py` | TriggerConfig, filter builder, FILTER_BUILDERS entry |
| `server/services/handlers/tools.py` | `send_custom_event()` calls (success + error paths) |
| `server/constants.py` | `taskTrigger` in EVENT_TRIGGER_TYPES, WORKFLOW_TRIGGER_TYPES |
| `client/src/components/parameterPanel/InputSection.tsx` | Output schema for draggable variables |

### LLM-Driven vs Event-Driven vs Agent-Handled

| Approach | Tool/Handle | When to Use |
|----------|-------------|-------------|
| **LLM-Driven** | `check_delegated_tasks` | Parent LLM decides when to check results and how to process them |
| **Event-Driven** | `taskTrigger` | Workflow automatically reacts to completion, processes via downstream nodes |
| **Agent-Handled** | `input-task` handle | Parent agent receives task result and reports to user conversationally |

All approaches can coexist. The LLM-driven approach is better for dynamic reasoning about results; the event-driven approach is better for deterministic post-processing workflows; the agent-handled approach is best when you want the parent agent to interpret and communicate results naturally.

---

## Agent Task Input Handle (input-task)

All AI agents (`aiAgent`, `chatAgent`, and the specialized agents — live count via `glob server/nodes/agent/*/__init__.py`) have an `input-task` handle on the left side that can receive task completion events from `taskTrigger` nodes. This enables a conversational pattern where the parent agent reports delegated task results to the user.

### Architecture

```
[Child Agent] completes
       ↓
[taskTrigger] fires (filters by agent_name/status)
       ↓
[Parent Agent] (input-task handle)
       ↓
Agent generates conversational response about completed task
```

### How It Works

1. **Backend Detection**: `collect_agent_connections()` in `server/services/plugin/edge_walker.py` detects nodes connected to the `input-task` handle and returns the collected `task_data` (5th element of the tuple)
2. **Task Data Collection**: Collects output from the connected `taskTrigger` node
3. **Context Injection**: `prepare_agent_call` (`server/nodes/agent/_inline.py`) calls `format_task_context()` (from `edge_walker.py`) to render `task_data` as prompt context
4. **Prompt Prepending**: Task context is prepended to the agent's prompt before execution

### Task Context Format

When a task completes successfully:
```
A delegated task has completed:
- Agent: Research Agent
- Task ID: delegated_abc123_xyz
- Status: Completed Successfully
- Result: [child agent's response]

Please report this result to the user in a conversational way.
```

When a task fails:
```
A delegated task has failed:
- Agent: Research Agent
- Task ID: delegated_abc123_xyz
- Status: Error
- Error: [error message]

Please report this error to the user and suggest next steps if appropriate.
```

### Key Files

| File | Changes |
|------|---------|
| `client/src/components/AIAgentNode.tsx` | Spec-driven; renders the `input-task` handle from the backend NodeSpec (no per-agent config map) |
| `server/services/plugin/edge_walker.py` | `task_data` collection via `collect_agent_connections()`, `format_task_context()` |
| `server/nodes/agent/_inline.py` | `prepare_agent_call()` injects the formatted task context into the prompt |

### Usage Example

```
[Parent Agent] ──delegates──> [Research Agent]
                                     ↓ (completes)
                              [Task Trigger]
                              (filters: agent_name="Research")
                                     ↓
                              [Chat Agent]
                              (input-task handle)
                                     ↓
                              Generates response:
                              "The research task has completed!
                               Here's what was found: ..."
```

### Shared Input/Output Handles (backend SSOT)

Post-Wave-11 the frontend `specializedAgentNodes.ts` definition file no longer exists — handle topology is declared on the backend plugin classes and the frontend renders it from each NodeSpec via `useNodeSpec(type)`. Every specialized agent subclasses `SpecializedAgentBase` (`server/nodes/agent/_specialized.py`), which provides the standard handle set through `std_agent_handles()` (declared in `server/nodes/agent/_handles.py`):

- **Left**: `input-main` (Input), `input-memory` (Memory), `input-task` (Task)
- **Bottom**: `input-skill` (Skill), `input-tools` (Tool)
- **Top**: `output-top` (Output)

Team-lead agents (`orchestrator_agent`, `ai_employee`) add an extra `input-teammates` handle for delegation. `AIAgentNode.tsx` is type-agnostic — it reads `handles` / `icon` / `color` / `displayName` / `uiHints` straight from the spec, with no `AGENT_CONFIGS` map.
