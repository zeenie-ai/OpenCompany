# Tool Building Pipeline

Canonical home for how AI Agents discover connected tool nodes, build
provider-neutral `AgentToolSpec` / `ToolDef` values, expose them through the
native `ChatUnifier`, and dispatch tool calls back to the right handler.
Replaces the partial explanations previously scattered across
`agent_architecture.md` and `agent_delegation.md`.

> **Related docs:**
> - [agent_architecture.md](./agent_architecture.md) — overall agent loop that owns the current tool surface
> - [agent_delegation.md](./agent_delegation.md) — `delegate_to_*` tools + taskTrigger / input-task patterns
> - [TEMPORAL_ARCHITECTURE.md](./TEMPORAL_ARCHITECTURE.md) — per-type Temporal activity dispatch (F4.A) that backs tool calls when the flag is on
> - [node_creation.md](./node_creation.md) — recipe for adding a new tool node

## 1. Five stages

```
1. DISCOVER     plugin/edge_walker.py:collect_agent_connections
                  → reads edges → tool_data list
                       │
                       ▼
2. BUILD        services/ai.py:AIService._build_tool_from_node
                  → tool_data entry → (AgentToolSpec, config_dict)
                       │
                       ▼
3. EXPOSE       services/agent_runtime.py:run_native_agent_loop
                  → ToolDef list → ChatUnifier → provider schema compiler
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

## 2. Stage 1 — Discovery (plugin/edge_walker.py)

[`collect_agent_connections(node_id, context, database)`](../server/services/plugin/edge_walker.py) walks the workflow edges and returns a 5-tuple per agent:

```
(memory_data, skill_data, tool_data, input_data, task_data)
```

- **`tool_data`** is a list of dicts. Each entry: `{node_id, node_type, parameters, label}`. (The `connected_services` extra belonged to the retired `androidTool` gateway tool — see §7.)
- **MasterSkill expansion** runs at discovery time: a single `masterSkill` connection with `skillsConfig` expands into N individual skill entries, one per enabled skill key.
- **Team-lead expansion** (`orchestrator_agent` / `ai_employee`): agents connected
  to `input-teammates` become authorized descriptors. Internal `delegate_to_*`
  identities remain available to the coordinator, while the LLM delegates via
  Task Manager.

## 3. Stage 2 — Build (services/ai.py:_build_tool_from_node)

[`AIService._build_tool_from_node(tool_info)`](../server/services/ai.py)
returns `(AgentToolSpec, config_dict)` or `(None, None)` on failure.

### 3a. Tool name resolution

The LLM-visible name and description come from the plugin rather than a
central map:

1. A per-node `delegate_tool_name` wins for expanded teammate/delegation
   identities.
2. A database `ToolSchema` override wins when present.
3. Otherwise declared node parameters (`tool_name` / `tool_description`) may
   override the defaults.
4. Plugin class variables `tool_name` / `tool_description` provide the normal
   default; `tool_description` falls back to the plugin's human-facing
   `description`.
5. Built-in pseudo-tools such as `Skill` and `check_delegated_tasks` use the
   small `_PSEUDO_TOOL_FALLBACK` table.
6. The last resort is `tool_<label>` / `Execute <label>`.

The resolved name is normalized to alphanumeric characters and underscores.
Duplicate visible names are rejected before the first billed model call.

### 3b. Schema resolution

```python
schema = self._get_tool_schema(node_type, schema_params)
```

[`_get_tool_schema`](../server/services/ai.py) returns a Pydantic `BaseModel`
subclass.

| Path | Source | When |
|---|---|---|
| **Database override** (preferred) | `ToolSchema` table (`server/models/database.py`) via `db.get_tool_schema(node_id)` | User customized the schema in the Tool Schema Editor UI |
| **Plugin schema** (normal) | Registered plugin class's Pydantic `Params` model | No DB override |
| **Special built-in schema** | Delegation, `Skill`, and `check_delegated_tasks` definitions in `_get_tool_schema` | The LLM invocation contract intentionally differs from a node's configuration model, or no plugin exists |
| **Generic fallback** | One required string `input` field | Unknown non-plugin node type |

`ToolSchema.schema_config` is JSON of `{description, fields: {name: {type, description, required}}}`. CRUD handlers in [`routers/websocket.py`](../server/routers/websocket.py): `get_tool_schema`, `save_tool_schema`, `delete_tool_schema`, `get_all_tool_schemas`.

### 3c. `AgentToolSpec` construction

```python
config = {
    "node_id": node_id,
    "node_type": node_type,
    "parameters": node_params,
    "label": node_label,
    "connected_services": connected_services,
}
parameters = inline_schema_refs(schema.model_json_schema())
tool = AgentToolSpec(
    definition=ToolDef(
        name=tool_name,
        description=tool_description,
        parameters=parameters,
    ),
    args_schema=schema,
    execution=config,
)
```

`ToolDef` is the provider-facing declaration. `AgentToolSpec` retains the
Pydantic validation model and local execution metadata. The returned `config`
is augmented by the agent's callback with services and graph context before it
reaches `execute_tool`.

## 4. Stage 3 — Expose (`run_native_agent_loop`)

[`services/ai.py:execute_agent`](../server/services/ai.py) calls
`run_native_agent_loop(ChatUnifier, ..., tools=tools)`. On each LLM step,
`run_native_llm_step` passes only the `ToolDef` declarations to
`ChatUnifier.chat`. The selected native provider compiles
`ToolDef.parameters` into its SDK's tool schema; there is no mutable
chat-model binding object.

Provider-specific handling for nullable values, unions, enums, nested
objects, and unsupported keywords remains inside the native provider layer.
See [native_llm_sdk.md](./native_llm_sdk.md) for the OpenAI / Anthropic /
Gemini differences.

## 5. Stage 4 — Invoke (LLM response handling)

When the native `LLMResponse` contains tool calls,
`run_native_agent_loop` first appends the exact replayable
`assistant_message`, then validates and dispatches each call through the
supplied `tool_executor` callback. Inside the callback:

1. Broadcast `executing_tool` to the parent agent (phase broadcast, not the tool node's lifecycle).
2. `from services.handlers.tools import execute_tool`
3. `result = await execute_tool(tool_name, tool_args, config)` — this owns the tool node's lifecycle.
4. Broadcast `tool_completed` to the parent agent.
5. Return `result` to the loop → serialized into a native
   `Message(role="tool", tool_call_id=...)`, appended to `messages`, and fed
   back to the LLM on the next turn.

Malformed JSON arguments retain `raw_arguments` plus a parse error. The loop
returns a deterministic tool-error result to the model instead of invoking the
tool or crashing.

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
| `whatsappSend` / `whatsappDb` / `pythonExecutor` / ... | Per-plugin `usable_as_tool=True` handler (the plugin's own `execute()` method) |
| `calculatorTool` / `currentTimeTool` / `taskManager` / `writeTodos` | `_execute_<name>` direct implementations |
| `braveSearch` / `serperSearch` / `perplexitySearch` | `handle_<provider>_search` (httpx async, credential resolution, usage tracking) |
| All else | `_execute_generic` (catch-all) |

## 7. Gateway-tool pattern (retired)

`androidTool` was the only **gateway tool** — a single LLM-visible
`android_device` declaration that fanned out to multiple connected Android
service nodes via a `connected_services` list. The node no longer exists:
Android service plugins expose their own `tool_name` class variables and
connect directly to `input-tools`. Legacy
`service -> androidTool -> agent` graphs are rewritten on load by
`services/workflow_migrations.normalize_legacy_android_toolkit`, and sub-node
exclusion keys solely on the AI-agent config handles (`input-memory` /
`input-tools` / `input-skill` / `input-teammates`) — the
`TOOLKIT_NODE_TYPES` constant is gone.

## 8. Internal delegation identities (`delegate_to_*`)

For team leads these identities are hidden from the model. Task Manager first
persists and authorizes an assignment, then the Temporal or legacy coordinator
uses the matching identity to dispatch the child. Direct calls described below
apply only to compatibility/non-team-lead paths.

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

1. `agent.prepare_payload.v1` records `llm_engine="native"` and
   `message_wire_version=2` for new executions.
2. `AgentWorkflow` (the F4.B child workflow) gets the LLM's tool-calls list
   back from `agent.execute_llm_step.v1`, whose native branch sends `ToolDef`
   declarations through `ChatUnifier`.
3. For each tool call, the workflow schedules
   `f"node.{node_type}.v{version}"` as a Temporal activity on the plugin's
   declared `task_queue`.
4. The per-type activity body in
   [`server/services/plugin/base.py:as_activity`](../server/services/plugin/base.py)
   runs the same pipeline `execute_tool` would have — broadcasts, plugin
   `execute()` call, terminal broadcast — but inside the activity boundary.
5. The result returns to the workflow, is appended to the native message
   history, and the next LLM step sees it.

This means tool calls inside an agent loop get Temporal's retry / timeout / heartbeat semantics independently of the parent agent's. A `code-exec` task burns its own retries; a `browser` task survives past the parent's `start_to_close_timeout` via its own heartbeat. See [TEMPORAL_ARCHITECTURE.md](./TEMPORAL_ARCHITECTURE.md).

Histories recorded before the engine marker existed deterministically replay
the frozen LangChain branch, which rebuilds the historical `StructuredTool`
surface. That compatibility branch is selected only by the missing
`llm_engine` marker; it is not the path for new executions.

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
3. Declare the plugin's `tool_name` and, when the LLM-facing wording differs,
   `tool_description` class variables.
4. (Optional) Add a special schema clause to `_get_tool_schema` only when the
   plugin's `Params` model is not the correct invocation contract.
5. (Optional) Add a dispatch clause to `_dispatch_tool` if your plugin needs
   custom handler logic; otherwise the generic per-plugin path runs
   `cls.execute()` directly.

Steps 4-5 are usually unnecessary — most new tools are fully described by
their plugin class.

See [node_creation.md](./node_creation.md) for the broader recipe and [server/nodes/README.md](../server/nodes/README.md) for the 5-minute walkthrough.

## 13. What NOT to do

- Don't broadcast `executing` / `success` / `error` from inside `_dispatch_tool` or a handler — `execute_tool` owns the tool node's lifecycle. Returning `{"status": "delegated"}` is the documented opt-out.
- Don't bypass `_build_tool_from_node` to hand-craft an `AgentToolSpec` or
  `ToolDef`. Schema dereferencing, validation metadata, execution routing, and
  duplicate-name checks are load-bearing.
- Don't expose a tool without a real Pydantic invocation schema. An empty
  schema makes argument generation unreliable.
- Don't catch exceptions inside the tool callback and return them as `{"error": ...}` — re-raise so `execute_tool` can broadcast `error`. The LLM-sees-tool-errors-and-continues pattern depends on this.
- Don't store per-execution state on `AIService` (it's a singleton). Use `config` or per-call closures.
