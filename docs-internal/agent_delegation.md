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
  |  Injects: ai_service, database, nodes, edges, workflow_id
  |
  v
execute_tool() dispatcher (handlers/tools.py)
  |  Routes agent node_types to _execute_delegated_agent()
  |
  v
_execute_delegated_agent() (handlers/tools.py)
  |  1. Fetches child_params from DATABASE
  |  2. Injects API key from credential store
  |  3. OVERWRITES prompt with delegated task
  |  4. Builds child_context with all nodes/edges
  |  5. Spawns asyncio.create_task()
  |  6. Returns immediately: {status: "delegated", task_id: "..."}
  |
  v                                           v
Parent continues reasoning            Child runs independently
(non-blocking)                           |
                                         v
                                    handle_ai_agent() or handle_chat_agent()
                                         |
                                    _collect_agent_connections(child_node_id)
                                         |  Filters edges by child's ID
                                         |  Finds child's own memory/skills/tools
                                         |
                                    execute_agent() or execute_chat_agent()
                                         |  Uses child's provider/model/system_message
                                         |  Uses child's memory (isolated session)
                                         |  Uses child's skills + tools
                                         |  Prompt = delegated task from parent
                                         |
                                    Result broadcast via WebSocket
                                    (NOT returned to parent LLM)
```

## The Delegation Chain (7 Steps)

### Step 1: Parent Builds Tool from Child Agent Node

**File:** `server/services/ai.py`, `_build_tool_from_node()` (line 2041)

When the parent agent starts executing and finds a child agent connected to its `input-tools` handle, `_build_tool_from_node()` is called during tool setup:

```python
for tool_info in tool_data:
    tool, config = await self._build_tool_from_node(tool_info)
    tools.append(tool)
    tool_configs[tool.name] = config
```

For agent node types, the method:

1. Looks up the tool name from `DEFAULT_TOOL_NAMES` (line 2054):
   ```python
   'aiAgent': 'delegate_to_ai_agent',
   'chatAgent': 'delegate_to_chat_agent',
   'android_agent': 'delegate_to_android_agent',
   'coding_agent': 'delegate_to_coding_agent',
   # ... all 12 agent types have entries
   ```

2. Creates a `DelegateToAgentSchema` Pydantic model (line 2622) with two fields:
   ```python
   class DelegateToAgentSchema(BaseModel):
       task: str = Field(
           description=f"The task/instruction to delegate to '{agent_label}'."
       )
       context: Optional[str] = Field(
           default=None,
           description="Additional context or data the child agent needs"
       )
   ```

3. Returns a config dict (line 2202):
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

**File:** `server/services/ai.py`, `tool_executor` (line 1406) / `chat_tool_executor` (line 1835)

Both `execute_agent()` and `execute_chat_agent()` define nearly identical tool executor callbacks as closures. When the parent's LLM decides to call the delegation tool, the callback fires.

The callback captures from outer scope:

| Variable | Source | Purpose |
|----------|--------|---------|
| `tool_configs` | Built in step 1 | Maps tool name to config dict |
| `self` | AIService instance | Provides `.database`, `.auth` |
| `broadcaster` | Function parameter | WebSocket status broadcasts |
| `workflow_id` | Function parameter | Scopes broadcasts to workflow |
| `context` | Function parameter | Contains `nodes` and `edges` arrays |

Before calling `execute_tool()`, the callback injects 5 fields into the config (line 1434-1443):

```python
config['workflow_id'] = workflow_id
config['ai_service']  = self               # AIService instance (shared)
config['database']    = self.database      # Database instance (shared)
if context:
    config['nodes'] = context.get('nodes', [])   # ALL workflow nodes
    config['edges'] = context.get('edges', [])   # ALL workflow edges
```

These injected fields are what enable the child agent to discover its own connections and execute independently. Without them, the child would have no access to the workflow graph or services.

---

### Step 3: Dispatch to Delegation Handler

**File:** `server/services/handlers/tools.py`, `execute_tool()` (line 27)

The dispatcher checks `node_type` and routes all 12 agent types to `_execute_delegated_agent()` (line 120):

```python
if node_type in ('aiAgent', 'chatAgent', 'android_agent', 'coding_agent',
                 'web_agent', 'task_agent', 'social_agent', 'travel_agent',
                 'tool_agent', 'productivity_agent', 'payments_agent',
                 'consumer_agent'):
    return await _execute_delegated_agent(tool_args, config)
```

---

### Step 4: Delegated Agent Execution (Fire-and-Forget)

**File:** `server/services/handlers/tools.py`, `_execute_delegated_agent()` (line 1126)

This is where the actual parameter assembly happens.

**4a. Extract injected services from config** (line 1149-1153):
```python
ai_service  = config.get('ai_service')
database    = config.get('database')
nodes       = config.get('nodes', [])
edges       = config.get('edges', [])
workflow_id = config.get('workflow_id')
```

**4b. Fetch child's OWN parameters from database** (line 1165):
```python
child_params = await database.get_node_parameters(node_id) or {}
```

The child's stored parameters (provider, model, system message, etc.) come from the database -- whatever the user configured in the child node's parameter panel. They are NOT inherited from the parent.

**4c. Inject API key if missing** (line 1169-1175):
```python
if not child_params.get('api_key') and not child_params.get('apiKey'):
    provider = detect_ai_provider(node_type, child_params)
    key = await ai_service.auth.get_api_key(provider, "default")
    if key:
        child_params['api_key'] = key
```

The child gets its API key from the credential store based on its own provider setting. If the child is configured for Anthropic and the parent uses OpenAI, the child gets the Anthropic key.

**4d. Inject default model if not set** (line 1178-1183):
```python
if not child_params.get('model'):
    provider = detect_ai_provider(node_type, child_params)
    models = await ai_service.auth.get_stored_models(provider, "default")
    if models:
        child_params['model'] = models[0]
```

**4e. OVERWRITE prompt with delegated task** (line 1186-1189):
```python
full_prompt = task_description
if task_context:
    full_prompt = f"{task_description}\n\nContext:\n{task_context}"
child_params['prompt'] = full_prompt
```

This is the **only parameter the parent passes to the child** -- the task instruction. Everything else comes from the child's own stored configuration.

**4f. Build child execution context** (line 1192-1198):
```python
child_context = {
    'nodes': nodes,              # ALL workflow nodes (not just child's)
    'edges': edges,              # ALL workflow edges (not just child's)
    'workflow_id': workflow_id,  # Parent's workflow ID (for status scoping)
    'outputs': {},               # Empty -- child starts fresh
    'parent_task_id': task_id   # Link back to parent's delegation task
}
```

**4g. Spawn as background task and return immediately** (line 1269-1281):
```python
task = asyncio.create_task(run_child_agent())
_delegated_tasks[task_id] = task

return {
    "success": True,
    "status": "delegated",
    "task_id": task_id,
    "agent_node_id": node_id,
    "agent_name": agent_label,
    "message": f"Task delegated to '{agent_label}'. Agent is now working independently..."
}
```

Inside `run_child_agent()` (line 1208-1267), the child handler is selected based on node_type:
- `aiAgent` -> `handle_ai_agent()` (agent loop with tools)
- All others -> `handle_chat_agent()` (direct LLM invoke)

---

### Step 5: Child Handler Collects Its Own Connections

**File:** `server/services/handlers/ai.py`, `handle_ai_agent()` (line 225) / `handle_chat_agent()` (line 280)

The spawned child calls `_collect_agent_connections()` with **its own node_id**:

```python
memory_data, skill_data, tool_data, input_data = await _collect_agent_connections(
    node_id,       # CHILD's node ID
    child_context, # Contains ALL nodes/edges
    database,
    log_prefix="[AI Agent]"
)
```

---

### Step 6: Connection Filtering by Node ID

**File:** `server/services/handlers/ai.py`, `_collect_agent_connections()` (line 14)

This function receives ALL nodes/edges but filters by the child's `node_id` (line 65-66):

```python
for edge in edges:
    if edge.get('target') != node_id:   # Only edges pointing TO this child
        continue

    target_handle = edge.get('targetHandle')
    source_node_id = edge.get('source')
```

For each matching edge, it checks the target handle:

| Handle | Action |
|--------|--------|
| `input-memory` | Loads markdown memory content from child's connected `simpleMemory` node (line 77-93) |
| `input-skill` | Loads skill instructions; expands masterSkill into individual enabled skills (line 97-153) |
| `input-tools` | Discovers tool nodes; for androidTool, finds connected Android services (line 156-205) |
| `input-main` / `input-chat` | Reads upstream node output for auto-prompt fallback (line 209-213) |

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
| `server/services/ai.py` | `_build_tool_from_node()`, `_get_tool_schema()`, `DelegateToAgentSchema`, `tool_executor` callback, `DEFAULT_TOOL_NAMES/DESCRIPTIONS` |
| `server/services/handlers/ai.py` | `_collect_agent_connections()`, `handle_ai_agent()`, `handle_chat_agent()` |
| `server/services/handlers/tools.py` | `execute_tool()` dispatcher, `_execute_delegated_agent()`, `_delegated_tasks` tracking |
| `server/constants.py` | `AI_AGENT_TYPES` frozenset, `detect_ai_provider()` |
| `client/src/components/AIAgentNode.tsx` | Frontend rendering of execution phase animations |
---

## Key Findings

1. **Only task/context strings flow from parent to child.** No other parameters (model, temperature, memory, skills) are inherited.

2. **Child configuration comes from its own database params.** Whatever the user set in the child's parameter panel is what the child uses.

3. **Memory is fully isolated.** Parent and child have separate simpleMemory nodes with separate session IDs and separate vector stores.

4. **The child sees the full workflow graph** via `nodes`/`edges`, but `_collect_agent_connections()` filters by the child's own `node_id`, so it only gets resources physically connected to it.

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
event_waiter.dispatch_async('task_completed', event_data)
       ↓
Matching taskTrigger nodes resolve their Futures
       ↓
Downstream workflow nodes execute with delegation result
```

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

All AI agents (aiAgent, chatAgent, and 10 specialized agents) have an `input-task` handle on the left side that can receive task completion events from `taskTrigger` nodes. This enables a conversational pattern where the parent agent reports delegated task results to the user.

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

1. **Backend Detection**: `_collect_agent_connections()` in `handlers/ai.py` detects nodes connected to `input-task` handle
2. **Task Data Collection**: Collects output from connected `taskTrigger` node
3. **Context Injection**: `_format_task_context()` formats task data as prompt context
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
| `client/src/components/AIAgentNode.tsx` | `input-task` handle in leftHandles for all 12 agent configs |
| `server/services/handlers/ai.py` | `task_data` collection, `_format_task_context()`, prompt injection |

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

### Shared Input/Output Constants

Specialized agents use shared constants for consistency:

```typescript
// In specializedAgentNodes.ts
export const AI_AGENT_INPUTS = [
  { name: 'main', displayName: 'Input', ... },
  { name: 'skill', displayName: 'Skill', ... },
  { name: 'memory', displayName: 'Memory', ... },
  { name: 'tools', displayName: 'Tool', ... },
  { name: 'task', displayName: 'Task', ... },  // Task completion events
];

export const AI_AGENT_OUTPUTS = [
  { name: 'main', displayName: 'Output', ... },
];
```

All 10 specialized agents reference these constants:
```typescript
android_agent: {
  inputs: AI_AGENT_INPUTS,
  outputs: AI_AGENT_OUTPUTS,
  properties: AI_AGENT_PROPERTIES
}
```
