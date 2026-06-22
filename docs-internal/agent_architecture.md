# AI Agent Architecture: Skill Injection & Tool Execution

Detailed architecture reference for how AI Agent (`aiAgent`) and Chat Agent (`chatAgent`) discover skills and tools from connected nodes, inject them into the LLM prompt, and execute tools via the plain-async agent loop.

> **Related Documentation:**
> - [Node Creation Guide](./node_creation.md) - Canonical plugin recipe (covers tool nodes, dual-purpose nodes, specialized agents)
> - [Tool Building Pipeline](./tool_building_pipeline.md) - Canonical home for `_build_tool_from_node`, tool discovery, per-type Temporal dispatch
> - [Memory Lifecycle](./memory_lifecycle.md) - Canonical home for markdown memory format, vector store, session resume
> - [CLAUDE.md](../CLAUDE.md) - Project overview and full node inventory

## Table of Contents

1. [End-to-End Data Flow](#end-to-end-data-flow)
2. [Agent Loop](#agent-loop)
3. [Skill Injection Pipeline](#skill-injection-pipeline)
4. [Tool Building Pipeline](#tool-building-pipeline)
5. [Tool Execution Flow](#tool-execution-flow)
6. [Memory Integration](#memory-integration)
7. [execute_agent vs execute_chat_agent](#execute_agent-vs-execute_chat_agent)

---

## End-to-End Data Flow

```
User clicks "Run" on AI Agent
        |
        v
useExecution.executeNode()               client/src/hooks/useExecution.ts
  Sends ALL workflow nodes + edges
        |
        v
WebSocket: handle_execute_node()          server/routers/websocket.py
  Passes nodes[], edges[] to WorkflowService
        |
        v
WorkflowService.execute_node()            server/services/workflow.py
  Builds context = {nodes, edges, session_id, workflow_id}
  Calls NodeExecutor.execute()
        |
        v
NodeExecutor._dispatch()                  server/services/node_executor.py
  Handler registry lookup via functools.partial
  Dispatches to handle_ai_agent() or handle_chat_agent()
        |
        v
_collect_agent_connections()              server/services/handlers/ai.py
  Scans edges where target == node_id
  Groups by targetHandle into 4 buckets:
    input-memory  -> memory_data
    input-skill   -> skill_data[]
    input-tools   -> tool_data[]
    input-main    -> input_data
        |
        v
AIService.execute_agent()                 server/services/ai.py
  1. Inject skills into system message
  2. Build LangChain StructuredTools from tool_data
  3. Call _run_agent_loop() (plain async while loop)
  4. Save memory, return result
        |
        v
_run_agent_loop() execution
  invoke LLM -> if tool_calls: dispatch each via tool_executor -> loop
  Return on final response (no tool_calls) or max_iterations cap.
        |
        v
Result broadcast via WebSocket
```

### Key Files

| File | Responsibility |
|------|---------------|
| `client/src/hooks/useExecution.ts` | Frontend execution trigger, sends nodes + edges |
| `server/routers/websocket.py` | WebSocket handler `handle_execute_node()` |
| `server/services/workflow.py` | Facade, builds context, delegates to NodeExecutor |
| `server/services/node_executor.py` | Handler registry, dispatches via `functools.partial` |
| `server/services/handlers/ai.py` | `_collect_agent_connections()`, `handle_ai_agent()`, `handle_chat_agent()` |
| `server/services/ai.py` | `AIService` -- `_run_agent_loop`, skill injection, tool building |
| `server/services/handlers/tools.py` | `execute_tool()` -- dispatch router for all tool types |
| `server/services/skill_loader.py` | `SkillLoader` -- filesystem/DB skill discovery and loading |

---

## Agent Loop

The agent loop is a plain `for iteration in range(max_iterations):` async function in `server/services/ai.py:_run_agent_loop`. No state machine, no graph DSL — each iteration:

1. Optional `progress_callback(iteration)` so consumers (UI iteration badge) get a per-turn tick.
2. `filter_empty_messages(messages)` strips empty `HumanMessage` content (Gemini / Claude reject them).
3. `response = await chat_model.ainvoke(filtered)` — single LLM call. The full assistant message is appended to `messages` verbatim so provider-specific metadata survives (Gemini `thought_signature`, Anthropic cache markers, OpenAI `reasoning_content`).
4. `extract_thinking_from_response(response)` — accumulates thinking across iterations with the `--- Iteration N ---` separator (multi-step reasoning).
5. If `response.tool_calls` is empty → return `{messages, iteration, thinking_content, truncated: False}`.
6. Otherwise dispatch each `tool_call` via the supplied `tool_executor`, wrap each result as a `ToolMessage`, append. After all calls in this iteration return, inspect each result for a `operations` field (workflow_ops batch from canvas-mutating tools); call `rebind_from_operations(ops)` if wired, extend the local `current_tools` list, and `chat_model.bind_tools(current_tools)` again so the LLM sees the new tools in the NEXT iteration. Loop.

On hitting `max_iterations`, append a terminal `AIMessage` with a truncation note and return `truncated: True`.

### Signature

```python
async def _run_agent_loop(
    chat_model,
    initial_messages: List[BaseMessage],
    *,
    tools: Optional[List[Any]] = None,
    tool_executor: Optional[Callable] = None,
    max_iterations: int = 500,
    progress_callback: Optional[Callable[[int], Any]] = None,
    rebind_from_operations: Optional[Callable[[List[Dict[str, Any]]], Awaitable[List[Any]]]] = None,
) -> Dict[str, Any]:
    """Returns {messages, iteration, thinking_content, truncated}."""
```

Tools bind at loop start via `chat_model.bind_tools(tools)` and **rebind** mid-loop whenever a tool returns workflow_ops (today only `agentBuilder`). The rebound model is the SAME LangChain `chat_model.bind_tools` method every provider honours — no per-provider plumbing.

### Termination

| Trigger | What happens |
|---|---|
| LLM emits no `tool_calls` | Return with `truncated: False`. This is the normal exit. |
| `tool_executor` is `None` but LLM emits tool calls | WARN + return (treat as final). |
| `iteration` reaches `max_iterations` | Append synthetic terminal `AIMessage` + return with `truncated: True`. |

### `max_iterations` precedence

Resolved per-execution by `execute_agent` / `execute_chat_agent` (and `prepare_agent_payload` for F4.B), highest to lowest:

1. **Per-agent-node** `parameters.max_iterations` — set by the user on the agent node itself.
2. **Per-user** `UserSettings.agent_recursion_limit` — Settings tab override (DB-backed).
3. **Env** `Settings.agent_recursion_limit` from `AGENT_RECURSION_LIMIT` (default 200).
4. **JSON** `llm_defaults.json:agent.recursion_limit` — last-resort fallback when Settings can't load.

This is a backstop, not the load-bearing signal — compaction (token-based, post-turn) is the actual termination control. See [memory_compaction.md](memory_compaction.md).

### Hot rebind after canvas mutation

When `agentBuilder` (the only canvas-mutating tool today) spawns a new node mid-run via `add_tool` / `add_skill` / `add_subagent`, the operation returns a `workflow_ops` batch in its result's `operations` field. The loop detects the field, calls `rebind_from_operations(ops)`, and extends the bound tool surface so the LLM can invoke the new tool in the very next iteration — no Run-stop-Run cycle.

Closure responsibilities:

- **`_rebind_from_operations(ops) -> List[StructuredTool]`** (in `execute_agent` / `execute_chat_agent`): filter ops for `add_node` with the plugin class's `component_kind == "tool"` OR `usable_as_tool=True` (excluding `component_kind == "model"`), synthesize a `tool_info` dict, call `self._build_tool_from_node(tool_info)`, return the new StructuredTools. Tool configs get folded into the captured `tool_configs` dict so the `tool_executor` can dispatch the new call.
- The closure is gated on the user toggle: `UserSettings.auto_rebind_tools_after_canvas_change` (default `True`). When off, the LLM is told "Available on your next turn" in the operation summary and the closure isn't wired.

For the F4.B Temporal path, the in-process closure is replaced by the `agent.refresh_tools.v1` activity; see [TEMPORAL_ARCHITECTURE.md](TEMPORAL_ARCHITECTURE.md).

### Where it's called

Two callsites in `server/services/ai.py`:

- `execute_agent` — for `aiAgent` plugins.
- `execute_chat_agent` — for `chatAgent` + all specialized agents + team leads.

Both build `initial_messages` (system + memory history + current prompt + skill injection), build tools via `_build_tool_from_node`, then call `_run_agent_loop` and extract the final assistant message + accumulated thinking + iteration count from the returned dict.

---

## Skill Injection Pipeline

### 1. Edge Scanning

In `_collect_agent_connections()` (`server/services/handlers/ai.py:14-49`), all edges targeting the agent node are scanned:

```python
for edge in edges:
    if edge.get('target') != node_id:
        continue

    target_handle = edge.get('targetHandle')
    source_node_id = edge.get('source')

    if target_handle == 'input-skill':
        # Collect skill data...
```

### 2. Regular Skill Nodes

For standard skill nodes (claudeSkill, whatsappSkill, etc.), a single entry is created:

```python
skill_entry = {
    'node_id': source_node_id,
    'node_type': skill_type,           # e.g., 'whatsappSkill'
    'skill_name': skill_params.get('skillName', skill_type),
    'parameters': skill_params,         # All node parameters from DB
    'label': source_node.get('data', {}).get('label', skill_type)
}
skill_data.append(skill_entry)
```

### 3. Master Skill Expansion

When the connected skill is a `masterSkill`, its `skillsConfig` parameter is expanded into N individual entries:

```python
if skill_type == 'masterSkill':
    skills_config = skill_params.get('skillsConfig', {})
    # Structure: {'whatsapp-skill': {'enabled': True, 'instructions': '...'}, ...}

    for skill_key, skill_cfg in skills_config.items():
        if not skill_cfg.get('enabled', False):
            continue  # Skip disabled skills

        # DB-first: use stored instructions
        instructions = skill_cfg.get('instructions', '')

        if not instructions:
            # Fallback: load from SKILL.md on disk
            skill = skill_loader.load_skill(skill_key)
            if skill:
                instructions = skill.instructions

        skill_data.append({
            'node_id': f"{source_node_id}_{skill_key}",  # Unique composite ID
            'node_type': 'masterSkill',
            'skill_name': skill_key,
            'parameters': {'instructions': instructions, 'skillName': skill_key},
            'label': skill_key
        })
```

One Master Skill node with 5 enabled skills produces 5 separate `skill_data` entries.

### 4. SkillLoader Architecture

Defined in `server/services/skill_loader.py:38+`:

```
SkillLoader
├── _skill_dirs: [server/skills/, .machina/skills/]
├── _database                                  # DI database singleton (user skills live in the DB)
├── _registry: Dict[name -> SkillMetadata]    # Metadata only (~100 tokens each)
├── _cache: Dict[name -> Skill]               # Full content (lazy-loaded)
│
├── scan_skills()           # rglob("SKILL.md") across all dirs, parses frontmatter
├── load_skill(name)        # Filesystem skills: cache -> registry -> SKILL.md
├── load_skill_async(name)  # DB-aware: filesystem first, then database.get_user_skill()
├── get_registry_prompt()   # Generates "## Available Skills" for system message
└── get_skill_instructions()# Shortcut for load_skill().instructions
```

**`scan_skills()`** (`skill_loader.py:63-88`):
- Iterates `_skill_dirs`, uses `rglob("SKILL.md")` for recursive discovery
- Parses YAML frontmatter for each file (`_parse_skill_metadata`)
- Populates `_registry` with `SkillMetadata` (name, description, allowed_tools, path)

**`load_skill(name)`** (`skill_loader.py:174-249`):
1. Check `_cache` -- return immediately if cached
2. Look up `_registry[name]` -- fail if not registered
3. Read `SKILL.md`, strip frontmatter, extract markdown body as `instructions`
4. Load optional `scripts/` and `references/` directories
5. Cache and return `Skill` dataclass

**`get_skill_loader()` is database-wired** (`skill_loader.py`):
- The global loader is constructed with the DI `container.database()` (resolved lazily, late-bound on a subsequent call if the container was not yet ready when the loader was first requested). User-created skills are stored in the database rather than on disk, so the wired DB is what lets them resolve.
- `load_skill_async(name)` is the DB-aware loader: filesystem `_registry` first, then `database.get_user_skill(name)`. The `get_skill_content` WebSocket handler and the agent skill paths call it, so database user skills load just like filesystem skills. `init_skill_loader(database=...)` remains available for explicit eager initialization.

**SKILL.md frontmatter parsing** (`skill_loader.py:126-172`):
```yaml
---
name: http-skill                    # Lowercase with hyphens, validated by regex
description: Make HTTP requests...  # Brief description for LLM visibility
allowed-tools: http-request         # Space-delimited tool names
metadata:
  author: machina
  version: "2.0"
---
```

### 5. System Message Injection

In `execute_agent()` and `execute_chat_agent()` within `server/services/ai.py`:

```python
if skill_data:
    skill_loader = get_skill_loader()
    skill_loader.scan_skills()

    # Extract skill names from collected data
    skill_names = []
    for skill_info in skill_data:
        skill_name = skill_info.get('skill_name') or ...
        skill_names.append(skill_name)

    # Generate structured skill listing
    skill_prompt = skill_loader.get_registry_prompt(skill_names)
    if skill_prompt:
        system_message = f"{system_message}\n\n{skill_prompt}"
```

### 6. Registry Prompt Output

`get_registry_prompt()` in `skill_loader.py:311-341` generates:

```
## Available Skills

You have access to the following skills. When a user's request matches
a skill's purpose, activate it to help them.

- **http-skill**: Make HTTP requests to external APIs
  - Tools: http-request
- **whatsapp-skill**: Send and receive WhatsApp messages
  - Tools: whatsapp-send, whatsapp-db
- **maps-skill**: Location services via Google Maps

To use a skill, identify when the user's request matches its purpose
and apply the skill's instructions.
```

This text is appended to the system message. The full SKILL.md body (instructions) is available via individual skill entries but the registry prompt provides the high-level listing.

### 7. allowed-tools

- **Parsed** from SKILL.md frontmatter as space-delimited list
- **Included** in registry prompt as informational text for the LLM
- **NOT enforced** in code -- the LLM can call any tool connected to `input-tools`
- Purpose: guides the LLM on which tools are relevant to each skill

---

## Tool Building Pipeline

Tool building + dispatch lives in [tool_building_pipeline.md](./tool_building_pipeline.md). The five stages (DISCOVER edges → BUILD StructuredTool → BIND via `chat_model.bind_tools` → INVOKE via LLM tool_calls → DISPATCH through execute_tool) and the dispatch matrix (delegated agents / Android services / dual-purpose plugins / direct tools / search APIs / generic fallback) are documented there. The single-source-of-truth rule — execute_tool owns the tool node lifecycle, parent-agent closures only emit phase broadcasts — is the contract test enforces.

Patterns covered in the canonical doc:

- DEFAULT_TOOL_NAMES map (node_type → LLM-visible tool name)
- Database tool-schema override via ToolSchema model + Tool Schema Editor UI
- Gateway-tool pattern (androidTool aggregates connected service nodes)
- Direct Android service tools (16 entries, skip the toolkit)
- Agent delegation (`delegate_to_*` fire-and-forget background tasks)
- Per-type Temporal activity dispatch (F4.A, when TEMPORAL_PER_TYPE_DISPATCH=true)
- Auto-skill edges (writeTodos, WhatsApp tools bundle a default skill)

---


## Memory Integration

Memory load + save lives in [memory_lifecycle.md](./memory_lifecycle.md). The agent loop reads `memory_data` from `_collect_agent_connections()` (via the `input-memory` edge), prepends parsed history to the system message + current prompt, runs `_run_agent_loop`, then appends the turn + trims the window + archives trimmed text to the vector store. The markdown helpers (`parse_memory_markdown`, `append_to_memory_markdown`, `trim_markdown_window`) and the vector store (`InMemoryVectorStore` with HuggingFace embeddings) are the load-bearing surface — see the canonical doc for signatures, the markdown format, and the engine-specific adapter table (aiAgent / rlm_agent / claude_code_agent native session resume bridge).

---

## execute_agent vs execute_chat_agent

Both methods live in `server/services/ai.py` and follow the same general pattern. Key differences:

| Aspect | `execute_agent()` | `execute_chat_agent()` |
|--------|-------------------|----------------------|
| **Loop call** | Always calls `_run_agent_loop` (even with zero tools — single-turn happy path returns immediately) | Conditional: calls `_run_agent_loop` if tools, simple `await chat_model.ainvoke(messages)` if no tools |
| **Tool failure** | Re-raises exceptions (the loop wraps them as `{"error": ...}` ToolMessages on retry) | Returns `{"error": str(e)}` (softer handling) |
| **No-tool path** | N/A -- always uses the loop | `response = await chat_model.ainvoke(messages)` (skips loop overhead) |
| **Result metadata** | `agent_type: "agent"` | `agent_type: "chat" / "chat_with_skills" / "chat_with_tools" / "chat_with_skills_and_tools"` |

### Specialized Agent Routing

There are **11 specialized agents** plus 2 team leads. Most route to `handle_chat_agent`; `rlm_agent` and `claude_code_agent` have dedicated handlers:

```python
# server/services/node_executor.py
SPECIALIZED_AGENT_TYPES = {
    'android_agent', 'coding_agent', 'web_agent', 'task_agent', 'social_agent',
    'travel_agent', 'tool_agent', 'productivity_agent', 'payments_agent', 'consumer_agent',
    'autonomous_agent', 'orchestrator_agent', 'ai_employee',
    # rlm_agent and claude_code_agent are handled by dedicated handlers, not handle_chat_agent
}

# Most specialized agents map to handle_chat_agent (uses _run_agent_loop)
for agent_type in SPECIALIZED_AGENT_TYPES:
    registry[agent_type] = partial(handle_chat_agent, ai_service=self.ai_service, database=self.database)

# Dedicated handlers for agents with externalised session state
registry['rlm_agent'] = partial(handle_rlm_agent, ai_service=self.ai_service, database=self.database)
registry['claude_code_agent'] = partial(handle_claude_code_agent, ...)
```

### Temporal dispatch routing

Two settings flags route agent execution through different Temporal paths (see [TEMPORAL_ARCHITECTURE.md](TEMPORAL_ARCHITECTURE.md) for the full matrix):

| Flag | Off | On (default) |
|---|---|---|
| `TEMPORAL_PER_TYPE_DISPATCH` | Every node routes through the legacy `execute_node_activity` single dispatcher (WS round-trip to the FastAPI handler). | Each node routes through its per-type activity `node.{type}.v{version}` registered via `BaseNode.as_activity()`. Per-plugin retry / timeout / heartbeat configs apply. |
| `TEMPORAL_AGENT_WORKFLOW_ENABLED` | All specialized + team leads + base agents (`aiAgent` / `chatAgent`) run inside `execute_node_activity` (`_run_agent_loop` in-activity). | The migrating agent types become Temporal **child workflows** (`AgentWorkflow`). LLM steps + tool calls become activities; `agent.prepare_payload.v1` resolves the DB-backed payload as the workflow's first step; `agent.refresh_tools.v1` rebinds the tool surface after canvas mutations. `rlm_agent` / `claude_code_agent` stay on the F4.A per-type activity path (externalised session state). |

Both flags default to `true` in `.env.template`.

**Team leads** (`orchestrator_agent`, `ai_employee`) use the same `handle_chat_agent` routing but add an `input-teammates` handle. Connected agents become `delegate_to_<type>` tools automatically via `_collect_teammate_connections()`. See [agent_teams.md](agent_teams.md).

### RLM Agent Pattern

`rlm_agent` replaces the standard tool-calling loop with a Python REPL executing LM calls recursively. Instead of paying one network round-trip per tool call, the RLM agent writes a code block that orchestrates many model invocations at once:

```python
# The LLM generates code like this, executed by RLMService
results = [llm_query(f"summarize: {url}") for url in urls]
best = rlm_query(f"pick the most relevant: {results}")
FINAL(best)
```

Exposed helpers inside the REPL:

| Helper | Purpose |
|---|---|
| `llm_query(prompt)` | Call the small model connected to `input-model` |
| `rlm_query(prompt)` | Recursively invoke the same RLM agent |
| `FINAL(answer)` | Signal completion and return the final answer |

Routing:

```python
# server/services/node_executor.py
registry['rlm_agent'] = partial(handle_rlm_agent, ai_service=self.ai_service, database=self.database)
```

`handle_rlm_agent` delegates to `RLMService` in `server/services/rlm_service.py`. See [rlm_service.md](rlm_service.md) for full details.

### Chat Agent Conditional Loop

```python
# In execute_chat_agent():
if all_tools:
    # Run the agent loop with bound tools
    final_state = await _run_agent_loop(
        chat_model, messages,
        tools=all_tools,
        tool_executor=chat_tool_executor,
        max_iterations=recursion_limit,
        progress_callback=_emit_progress if broadcaster else None,
    )
else:
    # Simple invoke -- no loop overhead
    response = await chat_model.ainvoke(messages)
```

This optimisation means tool-less Chat Agent conversations skip `_run_agent_loop` entirely for faster response times.
