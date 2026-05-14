# Tool Building Pipeline

Canonical home for how AI Agents discover connected tool nodes, build LangChain `StructuredTool` instances, bind them to the LLM, and dispatch tool calls back to the right handler. Replaces the partial explanations previously scattered across `agent_architecture.md` and `agent_delegation.md`.

> **Related docs:**
> - [agent_architecture.md](./agent_architecture.md) — overall agent loop that holds the bound tools
> - [agent_delegation.md](./agent_delegation.md) — `delegate_to_*` tools + taskTrigger / input-task patterns
> - [TEMPORAL_ARCHITECTURE.md](./TEMPORAL_ARCHITECTURE.md) — per-type Temporal activity dispatch (F4.A) that backs tool calls when the flag is on
> - [node_creation.md](./node_creation.md) — recipe for adding a new tool node

## 1. Five stages

```
1. DISCOVER     handlers/ai.py:_collect_agent_connections
                  → reads edges → tool_data list
                       │
                       ▼
2. BUILD        services/ai.py:AIService._build_tool_from_node
                  → tool_data entry → (StructuredTool, config_dict)
                       │
                       ▼
3. BIND         services/ai.py:_run_agent_loop
                  → chat_model.bind_tools(tools)
                       │
                       ▼
4. INVOKE       LLM emits tool_calls in its response
                  → loop dispatches each via tool_executor
                       │
                       ▼
5. DISPATCH     services/handlers/tools.py:execute_tool
                  → owns node lifecycle broadcasts
                  → _dispatch_tool routes by node_type to a handler
```

## 2. Stage 1 — Discovery (handlers/ai.py)

[`_collect_agent_connections(node_id, context, database)`](../server/services/handlers/ai.py) walks the workflow edges and returns a 4-tuple per agent:

```
(memory_data, skill_data, tool_data, input_data, task_data)
```

- **`tool_data`** is a list of dicts. Each entry: `{node_id, node_type, parameters, label, connected_services?}`. The `connected_services` field is only populated for `androidTool` (the gateway-tool pattern — see §6).
- **MasterSkill expansion** runs at discovery time: a single `masterSkill` connection with `skillsConfig` expands into N individual skill entries, one per enabled skill key.
- **Team-lead expansion** (`orchestrator_agent` / `ai_employee`): agents connected to `input-teammates` become `delegate_to_*` entries automatically.

## 3. Stage 2 — Build (services/ai.py:_build_tool_from_node)

[`AIService._build_tool_from_node(tool_info)`](../server/services/ai.py#L2609) at line 2609 returns `(StructuredTool, config_dict)` or `(None, None)` on failure.

### 3a. Tool name resolution

The hardcoded `DEFAULT_TOOL_NAMES` map at [`services/ai.py:2622-2691`](../server/services/ai.py#L2622) maps `node_type` → LLM-visible tool name:

| `node_type` | Tool name | Category |
|---|---|---|
| `calculatorTool` | `calculator` | Dedicated tool |
| `currentTimeTool` | `get_current_time` | Dedicated tool |
| `duckduckgoSearch` | `web_search` | Dedicated tool |
| `pythonExecutor` | `python_code` | Dual-purpose |
| `whatsappSend` | `whatsapp_send` | Dual-purpose |
| `whatsappDb` | `whatsapp_db` | Dual-purpose |
| `aiAgent` → `delegate_to_ai_agent` | (18 agent types total) | Agent delegation |
| `batteryMonitor` → `android_battery` | (16 service types) | Direct Android service |
| ... | | |

Adding a new tool type requires adding a row here. The frontend never reads this map — it's a backend-internal mapping.

### 3b. Schema resolution

```python
schema = self._get_tool_schema(node_type, schema_params)
```

[`_get_tool_schema`](../server/services/ai.py#L2888) at line 2888 returns a Pydantic `BaseModel` subclass.

Two paths:

| Path | Source | When |
|---|---|---|
| **Database override** (preferred) | `ToolSchema` table (`server/models/database.py`) via `db.get_tool_schema(node_id)` | User customized the schema in the Tool Schema Editor UI |
| **Dynamic generation** (fallback) | Hardcoded per-type schemas inside `_get_tool_schema` | No DB override; default behavior |

`ToolSchema.schema_config` is JSON of `{description, fields: {name: {type, description, required}}}`. CRUD handlers in [`routers/websocket.py`](../server/routers/websocket.py): `get_tool_schema`, `save_tool_schema`, `delete_tool_schema`, `get_all_tool_schemas`.

### 3c. StructuredTool construction

```python
tool = StructuredTool.from_function(
    name=tool_name,
    description=description,
    args_schema=schema,
    func=_make_callback(node_id, node_type, ...),  # closure → execute_tool
    coroutine=_make_callback_async(...),
)
config = {
    "node_id": node_id,
    "node_type": node_type,
    "workflow_id": workflow_id,
    "parameters": parameters,
    "ai_service": self,
    "database": database,
    "nodes": nodes,
    "edges": edges,
    # ... per-tool extras (e.g., connected_services for androidTool)
}
```

The returned `config` is threaded down to `execute_tool` so handlers can read injected services + graph context without going through globals.

## 4. Stage 3 — Bind (`_run_agent_loop`)

[`services/ai.py:execute_agent`](../server/services/ai.py) calls `_run_agent_loop(chat_model, ...)`, which does `chat_model.bind_tools(tools)` once at the top before the iteration loop. `bind_tools` reads each tool's `args_schema` to render a JSON Schema in the LLM's tool-use prompt.

Per-provider quirks live in the chat model class, not here — see [native_llm_sdk.md](./native_llm_sdk.md) for the OpenAI / Anthropic / Gemini differences.

## 5. Stage 4 — Invoke (LLM response handling)

When the LLM emits `tool_calls` in its response, `_run_agent_loop` iterates them and dispatches each via the supplied `tool_executor` callback — the `_make_callback_async` closure created in stage 2 — and inside that closure:

1. Broadcast `executing_tool` to the parent agent (phase broadcast, not the tool node's lifecycle).
2. `from services.handlers.tools import execute_tool`
3. `result = await execute_tool(tool_name, tool_args, config)` — this owns the tool node's lifecycle.
4. Broadcast `tool_completed` to the parent agent.
5. Return `result` to the loop → wrapped as a `ToolMessage`, appended to `messages`, fed back to the LLM on the next turn.

**Single-source-of-truth rule.** `execute_tool` owns `executing` / `success` / `error` broadcasts for the **tool node**. The parent-agent closure in `services/ai.py` only emits `executing_tool` / `tool_completed` phase events for the **parent agent**. Don't duplicate either.

## 6. Stage 5 — Dispatch (handlers/tools.py)

[`execute_tool(tool_name, tool_args, config)`](../server/services/handlers/tools.py#L73) at line 73:

```python
1. broadcaster.update_node_status(node_id, "executing", ...)
2. result = await _dispatch_tool(tool_name, tool_args, config)
3a. If result.status in {"delegated", "ALREADY_DELEGATED"}: handler owns
    its own success/error lifecycle (delegated agents broadcast from
    inside their background task). Skip terminal broadcast.
3b. Else: broadcaster.update_node_status(node_id, "success", {result})
4. Return result. Exceptions trigger an "error" broadcast and re-raise.
```

[`_dispatch_tool`](../server/services/handlers/tools.py#L138) at line 138 is pure routing — no broadcasting. Routes by `config["node_type"]`:

| Pattern | Handler |
|---|---|
| `aiAgent` / `chatAgent` / `<specialized>_agent` | `_execute_delegated_agent` (fire-and-forget background task) |
| `_builtin_check_delegated_tasks` | `_execute_check_delegated_tasks` |
| `batteryMonitor` / `wifiAutomation` / ... (16 service types) | `_execute_android_service` (snake_case service ID via SERVICE_ID_MAP) |
| `androidTool` (gateway) | `_execute_android_toolkit` (dispatches to a connected Android service) |
| `whatsappSend` / `whatsappDb` / `pythonExecutor` / ... | Per-plugin `usable_as_tool=True` handler (the plugin's own `execute()` method) |
| `calculatorTool` / `currentTimeTool` / `taskManager` / `writeTodos` | `_execute_<name>` direct implementations |
| `braveSearch` / `serperSearch` / `perplexitySearch` | `handle_<provider>_search` (httpx async, credential resolution, usage tracking) |
| All else | `_execute_generic` (catch-all) |

## 7. Gateway-tool pattern (Android Toolkit)

`androidTool` is the only **gateway tool** today — a single `StructuredTool` that the LLM sees as `android_device`, but which fans out to multiple connected Android service nodes:

```
[Battery Monitor] ──┐
[WiFi Automation] ──┼──► [Android Toolkit] ──► [AI Agent]
[Location]        ──┘
```

The LLM sees one tool with `{service_id, action, parameters}` arguments. `_execute_android_toolkit` looks up the matching connected service, asserts it's actually connected to this toolkit instance, and forwards to the service handler. The toolkit's `connected_services` list (populated during stage 1) drives schema generation in `_get_tool_schema` so the LLM only sees actions for services that are present.

Direct Android service tools (16 individual entries in `DEFAULT_TOOL_NAMES`) are an alternative path — connect a service node directly to `input-tools` without using the toolkit aggregator. Both paths exist; the toolkit is preferred when you want one cohesive `android_device` tool.

## 8. Delegation tools (`delegate_to_*`)

Specialized agents connected to an agent's `input-tools` handle become delegation tools. Schema is fixed:

```python
class DelegateToAgentSchema(BaseModel):
    task: str
    context: Optional[str] = None
```

[`_execute_delegated_agent`](../server/services/handlers/tools.py#L317) at line 317:

1. Generate a `task_id` (`uuid4().hex`).
2. Spawn `asyncio.create_task(<child agent execute>)` — fire and forget.
3. Track in `_active_delegated_nodes` so repeated calls during in-flight delegations short-circuit.
4. Return `{"status": "delegated", "task_id": task_id}` IMMEDIATELY (parent agent continues without waiting).
5. From inside the background task: broadcast the child's `executing`/`success`/`error` timeline + emit a `task_completed` CloudEvents event when the child finishes (success or error).

Parent agents see results via three paths — see [agent_delegation.md](./agent_delegation.md) for the full pattern matrix.

## 9. Per-type Temporal dispatch (F4.A)

When `TEMPORAL_PER_TYPE_DISPATCH=true` and the run is happening inside a Temporal workflow, tool calls do NOT go through `execute_tool` directly. Instead:

1. `AgentWorkflow` (the F4.B child workflow) gets the LLM's tool_calls list back from `agent.execute_llm_step.v1`.
2. For each tool call, the workflow schedules `f"node.{node_type}.v{version}"` as a Temporal activity on the plugin's declared `task_queue`.
3. The per-type activity body in [`server/services/plugin/base.py:as_activity`](../server/services/plugin/base.py) runs the same pipeline `execute_tool` would have — broadcasts, plugin `execute()` call, terminal broadcast — but inside the activity boundary.
4. Result returns to the workflow, gets appended to the messages history, next LLM step sees it.

This means tool calls inside an agent loop get Temporal's retry / timeout / heartbeat semantics independently of the parent agent's. A `code-exec` task burns its own retries; a `browser` task survives past the parent's `start_to_close_timeout` via its own heartbeat. See [TEMPORAL_ARCHITECTURE.md](./TEMPORAL_ARCHITECTURE.md).

## 10. Auto-skill edges

Some tools bundle a default skill — `writeTodos` ships with `write-todos-skill`, the WhatsApp tools ship with their respective skills. The frontend `useAutoSkillEdges.ts` hook detects these connections at canvas-edit time and auto-creates a phantom skill connection so the LLM gets both the tool schema (for invocation) and the skill instructions (for usage guidance) without the user having to wire two edges.

`MASTER_SKILL_NODE_TYPE` constant resolves via `getCachedNodeSpec(type)?.uiHints?.isMasterSkillEditor === true`, not a hardcoded string match.

## 11. Pytest invariants

```
server/tests/
├── test_status_broadcasts.py          # locks execute_tool's lifecycle broadcast contract
├── test_credential_broadcasts.py      # auth tools' CloudEvents envelope
├── nodes/test_ai_agents.py            # end-to-end agent + tool exercise
└── temporal/test_dispatch.py          # F4.A per-type activity dispatch (when flag on)
```

`_LEGACY_RAW_DICT_CALLSITES` in `test_status_broadcasts.py` is closed — new tool implementations cannot emit raw-dict broadcasts. Use `WorkflowEvent` factories + `StatusBroadcaster` wrappers; see [status_broadcaster.md](./status_broadcaster.md).

## 12. Adding a new tool node

The shortest path:

1. Create the plugin folder: `server/nodes/tool/<plugin>/__init__.py` extending `ToolNode` (or `ActionNode` with `usable_as_tool = True` for dual-purpose).
2. Declare `type`, `display_name`, `Params` (the schema the LLM sees), `Output`, `@Operation`-decorated `execute()`.
3. Add a row to `DEFAULT_TOOL_NAMES` in `services/ai.py:2622-2691` — `node_type` → LLM-visible tool name.
4. (Optional) Add a clause to `_get_tool_schema` if the dynamic-generation default isn't what you want.
5. (Optional) Add a dispatch clause to `_dispatch_tool` if your plugin needs custom handler logic; otherwise the generic per-plugin path runs `cls.execute()` directly.

Steps 3-5 are the only edits OUTSIDE the plugin folder. Steps 4-5 are usually unnecessary — most new tools use the dynamic default.

See [node_creation.md](./node_creation.md) for the broader recipe and [server/nodes/README.md](../server/nodes/README.md) for the 5-minute walkthrough.

## 13. What NOT to do

- Don't broadcast `executing` / `success` / `error` from inside `_dispatch_tool` or a handler — `execute_tool` owns the tool node's lifecycle. Returning `{"status": "delegated"}` is the documented opt-out.
- Don't bypass `_build_tool_from_node` to hand-craft a `StructuredTool`. The closure shape and `config` dict contents are load-bearing — agents inject services through them.
- Don't add a new entry to `DEFAULT_TOOL_NAMES` without also handling the schema generation. The tool will register but the LLM will see `{}` for the schema and hallucinate arguments.
- Don't catch exceptions inside the tool callback and return them as `{"error": ...}` — re-raise so `execute_tool` can broadcast `error`. The LLM-sees-tool-errors-and-continues pattern depends on this.
- Don't store per-execution state on `AIService` (it's a singleton). Use `config` or per-call closures.
