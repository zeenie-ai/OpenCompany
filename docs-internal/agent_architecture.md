# AI Agent Architecture: Skill Injection & Tool Execution

Detailed architecture reference for how AI Agent (`aiAgent`) and Chat Agent (`chatAgent`) discover skills and tools from connected nodes, inject them into the LLM prompt, and execute tools via the plain-async agent loop.

> **Related Documentation:**
> - [Node Creation Guide](./node_creation.md) - Canonical plugin recipe (covers tool nodes, dual-purpose nodes, specialized agents)
> - [Tool Building Pipeline](./tool_building_pipeline.md) - Canonical home for `_build_tool_from_node`, tool discovery, per-type Temporal dispatch
> - [Agent Context Flow](./agent_context_flow.md) - Canonical home for conversation continuity (RFC-0002 Context store). The retired markdown memory model is archived at [ARCHIVE/memory_lifecycle.md](./ARCHIVE/memory_lifecycle.md) and is not an SSOT for anything current.
> - [CLAUDE.md](../CLAUDE.md) - Project overview and full node inventory

## Table of Contents

1. [End-to-End Data Flow](#end-to-end-data-flow)
2. [Agent Loop](#agent-loop)
3. [Skill Injection Pipeline](#skill-injection-pipeline)
4. [Tool Building Pipeline](#tool-building-pipeline)
5. [Tool Execution Flow](#tool-execution-flow)
6. [Context and Memory Integration](#context-and-memory-integration)
7. [execute_agent vs execute_chat_agent](#execute_agent-vs-execute_chat_agent)

---

## End-to-End Data Flow

```
User clicks "Run" on AI Agent
        |
        v
ExecutionService.executeNodeViaWebSocket() client/src/services/executionService.ts
  Called from ParameterPanel.tsx:81
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
  Plugin handler registry lookup (plain dict: self._handlers.get(node_type))
  Dispatches via BaseNode.execute() to the agent plugin's execute_op()
  (server/nodes/agent/<plugin>/__init__.py — Wave 11 deleted handle_ai_agent /
   handle_chat_agent; agent logic moved into the plugin folder)
        |
        v
collect_agent_connections()               server/services/plugin/edge_walker.py
  (called by nodes/agent/_inline.prepare_agent_call)
  Scans edges where target == node_id
  Groups by targetHandle into 5 buckets (returns a 5-tuple
   context_data, skill_data, tool_data, input_data, task_data —
   edge_walker.py:181-199; the first element is the Context descriptor,
   or the legacy Memory descriptor on `input-memory` graphs):
    input-context            -> context_data
    input-skill              -> skill_data[]
    input-tools              -> tool_data[]
    input-main / input-chat  -> input_data
    input-task               -> task_data
        |
        v
AIService.execute_agent() / execute_chat_agent()   server/services/ai.py
  1. Inject personality-skill bodies into the system message
     (_build_skill_system_prompt -> services/skill_prompt.py); standard
     skills ride the Skill tool instead
  2. Build provider-neutral AgentToolSpecs from tool_data
  3. Call run_native_agent_loop(ChatUnifier, ...)
  4. Save the conversation (Context store; legacy memory markdown
     only for `input-memory` graphs), return result
        |
        v
run_native_agent_loop() execution
  ChatUnifier -> native SDK -> if tool_calls: dispatch via tool_executor -> loop
  Return on final response (no tool_calls) or max_iterations cap.
        |
        v
Result broadcast via WebSocket
```

### Key Files

| File | Responsibility |
|------|---------------|
| `client/src/services/executionService.ts` | Frontend execution trigger, sends nodes + edges (`executeNodeViaWebSocket`, called from `ParameterPanel.tsx`) |
| `server/routers/websocket.py` | WebSocket handler `handle_execute_node()` |
| `server/services/workflow.py` | Facade, builds context, delegates to NodeExecutor |
| `server/services/node_executor.py` | Plugin handler registry, plain dict dispatch (`self._handlers.get(node_type)`) |
| `server/services/plugin/edge_walker.py` | `collect_agent_connections()` (the renamed `_collect_agent_connections`), `format_task_context()` |
| `server/nodes/agent/_inline.py` | `prepare_agent_call()` — task-context injection + auto-prompt fallback + teammate collection; calls `collect_agent_connections` then `ai_service.execute_[chat_]agent` |
| `server/nodes/agent/<plugin>/__init__.py` | Per-plugin `execute_op()` (Wave 11 replaced `handle_ai_agent` / `handle_chat_agent`) |
| `server/services/ai.py` | `AIService` facade -- skill injection (`_build_skill_system_prompt`), tool building, context/memory and execution-boundary handling |
| `server/services/skill_prompt.py` | `build_skill_system_prompt` -- the only skill-to-system-message path (personality bodies only) |
| `server/services/agent_runtime.py` | `AgentToolSpec`, `run_native_llm_step`, and `run_native_agent_loop` |
| `server/services/llm/` | `ChatUnifier`, provider-neutral messages/tool definitions, and native provider SDK adapters |
| `server/services/handlers/tools.py` | `execute_tool()` -- dispatch router for all tool types |
| `server/services/skill_loader.py` | `SkillLoader` -- filesystem/DB skill discovery and loading |

---

## Agent Loop

The agent loop is the plain async
`services.agent_runtime.run_native_agent_loop`. No state machine or graph DSL
is involved — each iteration:

1. Optional `progress_callback(iteration)` so consumers (UI iteration badge) get a per-turn tick.
2. `filter_empty_messages(messages)` strips empty native message content
   rejected by providers.
3. `run_native_llm_step` calls `ChatUnifier.chat`, which invokes the selected
   native provider SDK and returns `LLMResponse`.
4. The canonical `LLMResponse.assistant_message` is appended verbatim before
   any tool runs, preserving Gemini thought signatures, Anthropic
   signed/redacted thinking, and OpenAI continuation metadata. Normalized
   `Usage` is accumulated across iterations.
5. Thinking text is accumulated across iterations with the
   `--- Iteration N ---` separator.
6. If `response.tool_calls` is empty → return
   `{messages, iteration, thinking_content, truncated: False, usage,
   response}`.
7. Otherwise validate each call against its `AgentToolSpec.args_schema`,
   dispatch it through `tool_executor`, and append a native
   `Message(role="tool", tool_call_id=...)`. Malformed arguments become a
   deterministic tool error containing `raw_arguments` rather than crashing.
   An external tool's result text is cut to `tool_output_limit` characters
   before it is appended (see Signature below).
   Results containing workflow `operations` are passed to
   `rebind_from_operations`; returned `AgentToolSpec` values extend
   `current_tools` for the next request.

On hitting `max_iterations`, append a terminal native assistant `Message`
with a truncation note and return `truncated: True`.

### Signature

```python
async def run_native_agent_loop(
    chat_unifier,
    *,
    provider: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int,
    initial_messages: Sequence[Message],
    thinking: Optional[ThinkingConfig] = None,
    tools: Optional[Sequence[AgentToolSpec]] = None,
    tool_executor: Optional[Callable[[str, Dict[str, Any]], Awaitable[Any]]] = None,
    max_iterations: int = 500,
    progress_callback: Optional[Callable[[int], Awaitable[Any]]] = None,
    rebind_from_operations: Optional[
        Callable[[List[Dict[str, Any]]], Awaitable[List[AgentToolSpec]]]
    ] = None,
    context_management: Optional[Dict[str, Any]] = None,
    compaction_pause_callback: Optional[
        Callable[[int, LLMResponse, List[Message]], Awaitable[Optional[List[Message]]]]
    ] = None,
    conversation_saver: Optional[Callable[[List[Message]], Awaitable[None]]] = None,
    tool_output_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Returns messages, iteration, thinking, truncation, usage, and response."""
```

(`run_native_agent_loop` in `services/agent_runtime.py`.) `context_management`,
`compaction_pause_callback` and `conversation_saver` are the RFC-0002 hooks:
`context_management` is forwarded to the provider step (the Anthropic
compaction edit), `compaction_pause_callback` lets the caller swap the
message list after a provider-side compaction pause, and `conversation_saver`
persists the full transcript after every turn through the plain conversation
store (see [agent_context_flow.md](agent_context_flow.md)).
`tool_output_limit` caps, in characters, what one external tool result adds
to `messages` (`bound_tool_output` in `services/tool_output.py`; `None` or `0`
keeps results whole). Delegated-agent answers, skill loads and Task Manager
results are exempt (`tool_output_is_capped`). `execute_agent` /
`execute_chat_agent` pass the resolved Tool Result Limit; see
[agent_context_flow.md → Transcript size](agent_context_flow.md).

There is no mutable model binding step. Each native LLM request derives its
provider-facing declarations from the current `AgentToolSpec.definition`
values. When a canvas-mutating tool returns workflow operations (today only
`agentBuilder`), newly built specs extend that list and are visible on the
next iteration.

### Termination

| Trigger | What happens |
|---|---|
| LLM emits no `tool_calls` | Return with `truncated: False`. This is the normal exit. |
| `finish_reason == "compaction"` with no `tool_calls` | **Not** an exit. Anthropic's durability pause returns only a compaction block; it has already been saved, so the loop calls `compaction_pause_callback` (when given, its non-`None` return replaces `messages`) and continues to the next iteration. |
| `tool_executor` is `None` but LLM emits tool calls | WARN + return (treat as final). |
| `iteration` reaches `max_iterations` | Append a synthetic native assistant `Message` + return with `truncated: True`. |

### `max_iterations` precedence

Resolved per-execution, and the two runtimes differ. **Temporal** (`prepare_agent_payload`, `services/temporal/agent_activities.py`) applies tiers 1-3 below and falls back to a hardcoded 200 if `Settings` cannot instantiate; it never reads `llm_defaults.json`. **In-process** (`execute_agent` / `execute_chat_agent` in `services/ai.py`) applies tiers 2-4 through `get_model_registry().get_agent_defaults()`, where the env value wins and the JSON is the last resort; only tier 1 is unreachable there. Highest to lowest:

1. **Per-agent-node** `parameters.max_iterations` — set by the user on the agent node itself. Temporal path only; no agent plugin currently declares a `max_iterations` Params field, so this tier is reachable only from a hand-written graph.
2. **Per-user** `UserSettings.agent_recursion_limit` — Settings tab override (DB-backed).
3. **Env** `Settings.agent_recursion_limit` from `AGENT_RECURSION_LIMIT` (default 200).
4. **JSON** `llm_defaults.json:agent.recursion_limit` — in-process only, reached when `Settings` cannot instantiate; the Temporal path uses a hardcoded 200 instead.

The iteration limit is the termination backstop. Compaction is a post-turn
context-pressure control (on the Temporal path for every agent, in-process for
agents with connected memory); it summarizes active history so a loop can
continue within the model's context window, but it does not decide when the
loop terminates. Tool results are bounded separately, per call, by the Tool
Result Limit. See [memory_compaction.md](memory_compaction.md) and
[agent_context_flow.md → Transcript size](agent_context_flow.md).

### Hot rebind after canvas mutation

When `agentBuilder` (the only canvas-mutating tool today) adds a tool (`add_tool`) or a teammate (`add_subagent`) mid-run, it saves the change (`apply_graph_additions`) and returns the saved `workflow_ops` batch in its result's `operations` field. The loop detects the field, calls `rebind_from_operations(ops)`, and extends the bound tool surface so the LLM can invoke the new tool in the very next iteration — no Run-stop-Run cycle. A deployed run starts from the generation's frozen snapshot, so a tool saved in an earlier run can be missing from a later one; `add_tool` then returns that saved node alone (a bind-only batch, nothing saved) for the loop to bind. `add_skill` returns no operations: a skill is not bound as a tool, and a merge into an existing Skills node applies from the next run.

Closure responsibilities:

- **`_rebind_from_operations(ops) -> List[AgentToolSpec]`** (in
  `execute_agent` / `execute_chat_agent`): filter ops for `add_node` with the
  plugin class's `component_kind == "tool"` OR `usable_as_tool=True`
  (excluding `component_kind == "model"`, and the Skills node, whose
  `uiHints.isMasterSkillEditor` marks it: tool-kind, but it feeds
  `input-skill`), skip a node already bound, synthesize a `tool_info` dict,
  call `self._build_tool_from_node(tool_info)`, and return the new specs. Tool
  configs get folded into the captured `tool_configs` dict so
  `tool_executor` can dispatch the new call.
- The closure is gated on the user toggle: `UserSettings.auto_rebind_tools_after_canvas_change` (default `True`). When off, the LLM is told "Available on your next turn" in the operation summary and the closure isn't wired.

For the F4.B Temporal path, the closure is replaced by `agent.refresh_tools`.
The `agent-node-binding-refresh-v2` path passes bound node IDs, the run's graph,
and parameter snapshot, then filters repeated node bindings. A distinct node
that would introduce a conflicting provider-visible tool name is rejected
with conflict details. Older histories preserve their recorded refresh path.
Version 1 preparation/refresh also records plugin Activity policies for each
bound tool. See [TEMPORAL_ARCHITECTURE.md](TEMPORAL_ARCHITECTURE.md).

### Where it's called

Two callsites in `server/services/ai.py`:

- `execute_agent` — for `aiAgent` plugins.
- `execute_chat_agent` — for `chatAgent` + all specialized agents + team leads.

Both build native `initial_messages` (system, stored conversation history,
current prompt, and personality-skill injection), build tools via `_build_tool_from_node`, then call
`run_native_agent_loop` and extract the final assistant message, accumulated
thinking, usage, and iteration count from the returned dict.

---

## Skill Injection Pipeline

### 1. Edge Scanning

In `collect_agent_connections()` (`server/services/plugin/edge_walker.py`; the renamed Wave-11 successor to `_collect_agent_connections` — the old `handlers/ai.py` was deleted), all edges targeting the agent node are scanned:

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
    'skill_name': skill_params.get('skill_name', skill_type),
    'parameters': skill_params,         # All node parameters from DB
    'label': source_node.get('data', {}).get('label', skill_type)
}
skill_data.append(skill_entry)
```

### 3. Master Skill Expansion

When the connected skill is a `masterSkill`, its `skills_config` parameter is
expanded into N individual entries. The expansion lives in the skill plugin
folder — `nodes/skill/_expander.py`, registered into the edge walker via
`register_master_skill_expander` — so `edge_walker.py` carries no knowledge of
the Master Skill parameter shape:

```python
# nodes/skill/_expander.py
skills_config = skill_params.get('skills_config', {})
# Structure: {'whatsapp-skill': {'enabled': True, 'instructions': '...'}, ...}

for skill_key, skill_cfg in skills_config.items():
    if not skill_cfg.get('enabled', False):
        continue  # Skip disabled skills

    skill_data.append({
        'node_id': f"{source_node_id}_{skill_key}",  # Unique composite ID
        'master_skill_node_id': source_node_id,
        'node_type': 'masterSkill',
        'skill_name': skill_key,          # snake_case is canonical; a
                                          # 'skillName' mirror is kept for
                                          # legacy readers
        'description': metadata.description,
        # Customized instructions remain authoritative but are not put in
        # the initial prompt for standard skills.
        'parameters': {'instructions': skill_cfg.get('instructions', '')},
        'label': skill_key
    })
```

One Master Skill node with 5 enabled skills produces 5 separate `skill_data` entries.

Standard entries use progressive disclosure. They do not alter the agent system
prompt. Their bounded name/description catalogue is carried by the dynamically
bound provider-neutral `Skill` tool, using the same contract as other connected
tool nodes.

The Assistant folder contains a required `skill` entry whose editable SKILL.md
body teaches use of that tool. Master Skill defaults and legacy expansion keep
it enabled, so the row appears checked as **Skill** beside the other Assistant
skills. This makes the usage prompt visible/configurable on the node instead of
hardcoding it into every agent prompt.
`Skill.load` returns authoritative instructions and a resource manifest;
`read_resource` and `search_resource` provide bounded access to declared text
files after loading. Personality skills are the sole eager-body exception.
Duplicate enabled names across connected Master Skill nodes fail with
`DUPLICATE_CONNECTED_SKILL_NAME` before the first model call.

The runtime revalidates the connected descriptor on each call, keys loaded-body
deduplication by workflow execution and agent, and emits sanitized CloudEvents
`com.opencompany.agent.skill.loading|loaded|resource_read|failed|cleared`.
Ordinary tools emit the parallel
`com.opencompany.agent.tool.started|completed|failed` contract. `subject` and
`data.author_node_id` both identify the exact invoking agent;
`data.target_node_id` identifies only its connected capability node. Temporal
event IDs are deterministic per model tool call and lifecycle stage, and
consumers deduplicate by `(source, id)`. Bodies and resource contents appear
only in tool results, never status broadcasts.
The generic loop, chat/specialized-agent loop, RLM bridge, Claude/Codex native
MCP bridge, and Temporal AgentWorkflow all emit the same parent-agent
capability phases. Consequently
every agent component renders `skill <name>` and `tool <name>` consistently;
this behavior is not owned by the team-lead implementation.

### 4. SkillLoader Architecture

Defined in `server/services/skill_loader.py:54+`:

```
SkillLoader
├── _skill_dirs: [server/skills/, <cwd>/.machina/skills/, <cwd>/.opencompany/skills/]
│                                             # _default_skill_dirs(), later dirs win
├── _database                                  # DI database singleton (user skills live in the DB)
├── _registry: Dict[name -> SkillMetadata]    # Metadata only (~100 tokens each)
├── _cache: Dict[name -> Skill]               # Full content (lazy-loaded)
│
├── scan_skills()           # rglob("SKILL.md") across all dirs, parses frontmatter
├── load_skill(name)        # Filesystem skills: cache -> registry -> SKILL.md
├── load_skill_async(name)  # DB-aware: filesystem first, then database.get_user_skill()
├── get_registry_prompt()   # "## Available Skills" text — DEAD: zero callers (see §5)
└── get_skill_instructions()# Shortcut for load_skill().instructions
```

**`scan_skills()`** (`skill_loader.py:80-105`):
- Iterates `_skill_dirs`, uses `rglob("SKILL.md")` for recursive discovery
- Parses YAML frontmatter for each file (`_parse_skill_metadata`)
- Populates `_registry` with `SkillMetadata` (name, description, allowed_tools, path)

**`load_skill(name)`** (`skill_loader.py:228-298`):
1. Check `_cache` -- return immediately if cached
2. Look up `_registry[name]` -- fail if not registered
3. Read `SKILL.md`, strip frontmatter, extract markdown body as `instructions`
4. Discover optional `scripts/` and `references/`; the Skill tool returns a
   manifest and reads their text separately rather than injecting them eagerly
5. Cache and return `Skill` dataclass

**`get_skill_loader()` is database-wired** (`skill_loader.py`):
- The global loader is constructed with the DI `container.database()` (resolved lazily, late-bound on a subsequent call if the container was not yet ready when the loader was first requested). User-created skills are stored in the database rather than on disk, so the wired DB is what lets them resolve.
- `load_skill_async(name)` is the DB-aware loader: filesystem `_registry` first, then `database.get_user_skill(name)`. The `get_skill_content` WebSocket handler and the agent skill paths call it, so database user skills load just like filesystem skills. `init_skill_loader(database=...)` remains available for explicit eager initialization.

**SKILL.md frontmatter parsing** (`_parse_skill_metadata`, `skill_loader.py:144-227`):
```yaml
---
name: http-skill                    # Lowercase with hyphens, validated by regex
description: Make HTTP requests...  # Brief description for LLM visibility
allowed-tools: http-request         # Space-delimited tool names
metadata:
  author: opencompany
  version: "2.0"
---
```

### 5. System Message Injection

The only path from `skill_data` to the system message is
`_build_skill_system_prompt` (module-level in `server/services/ai.py`), a thin
wrapper that imports and calls `services/skill_prompt.py::build_skill_system_prompt`.
It is called from `execute_agent`, `execute_chat_agent`, the RLM service
(`services/rlm/service.py`) and the Temporal `agent.prepare_payload` activity
(`services/temporal/agent_activities.py`):

```python
# server/services/ai.py
def _build_skill_system_prompt(skill_data, log_prefix="[Agent]") -> tuple:
    from services.skill_prompt import build_skill_system_prompt
    return build_skill_system_prompt(skill_data, log_prefix)

# execute_agent / execute_chat_agent
skill_prompt, has_personality = _build_skill_system_prompt(skill_data, log_prefix="[AIAgent]")
```

`build_skill_system_prompt` walks `skill_data` and injects **only personality
skills** (names ending in `-personality`) — their full SKILL.md
`parameters.instructions` body is appended verbatim to the system message,
and `has_personality=True` tells the caller to drop the default system
message. Every other skill is counted and logged but contributes **nothing**
to the prompt: standard skills reach the model through the dynamically bound
`Skill` tool (name/description catalogue, `load` for the body,
`read_resource` / `search_resource` for declared files), as described in §3.

### 6. Registry prompt (unused)

`SkillLoader.get_registry_prompt()` (`skill_loader.py:361-391`) still exists
and renders a `## Available Skills` markdown listing (name, description,
`- Tools:` line per skill), but it has **zero callers** in the codebase. No
agent path appends a skill listing to the system message; the Skill tool's
catalogue replaced it. Treat the method as dead code — do not build new
prompt features on it.

### 7. allowed-tools

- **Parsed** from SKILL.md frontmatter as a space-delimited list into `SkillMetadata.allowed_tools`
- **Surfaced** to the model through the Skill tool's catalogue / `load` result (and the `listSkills` / `getSkill` MCP tools for CLI agents), not through the system message
- **NOT enforced** in code -- the LLM can call any tool connected to `input-tools`
- Purpose: guides the LLM on which tools are relevant to each skill; also drives palette icon/color resolution for SKILL.md entries (first tool in `allowed-tools`)

---

## Tool Building Pipeline

Tool building + dispatch lives in
[tool_building_pipeline.md](./tool_building_pipeline.md). The five stages
(DISCOVER edges → BUILD `AgentToolSpec` / `ToolDef` → EXPOSE through
`ChatUnifier` → INVOKE via native tool calls → DISPATCH through
`execute_tool`) and the dispatch matrix (delegated agents / Android services /
dual-purpose plugins / direct tools / search APIs / generic fallback) are
documented there. The single-source-of-truth rule — `execute_tool` owns the
tool node lifecycle, parent-agent closures only emit phase broadcasts — is the
contract tests enforce.

Patterns covered in the canonical doc:

- Plugin `tool_name` / `tool_description` class variables and database
  overrides (node type → LLM-visible identity)
- Database tool-schema override via ToolSchema model + Tool Schema Editor UI
- Direct tool pattern (each Android service connects to the agent independently)
- Direct Android service tools (16 entries, skip the toolkit)
- Durable team delegation through Task Manager; `delegate_to_*` identities are
  retained internally for compatibility dispatch
- Per-type Temporal activity dispatch (F4.A, when TEMPORAL_PER_TYPE_DISPATCH=true)
- Auto-skill edges (writeTodos, WhatsApp tools bundle a default skill)

---


## Context and Memory Integration

Conversation continuity is the RFC-0002 Context store, documented in
[agent_context_flow.md](./agent_context_flow.md) and
[RFC-0002](../RFC-0002-AGENT-CONTEXT-AND-MEMORY.md). The agent reads
`context_data` — the first element of the `collect_agent_connections()`
5-tuple (`server/services/plugin/edge_walker.py:181-199`), populated from the
`input-context` edge to a `context` node — and `AIService._prepare_context`
resolves it into an `_AgentContextRuntime{key, history, database}`. Stored
history is seeded into `initial_messages` (system messages filtered), the
loop runs with `conversation_saver=runtime.save` so every turn persists the
full transcript, and nothing else about the request changes because a Context
node is attached.

`simpleMemory` ("Memory") is **not** conversation history: it is a `ToolNode`
on `input-tools` that the agent calls explicitly (its `operation` argument
takes the `MemoryOperation` values in `server/nodes/tool/simple_memory/`:
`remember` / `recall` / `list` / `get` / `update` / `forget`). The
`input-memory` handle is retired — no agent declares
it; `normalize_workflow_graph` rewrites legacy `simpleMemory -> input-memory`
edges into a Context node plus an ordinary tool edge. The legacy markdown
memory path (`memory_data`, `parse_memory_markdown`,
`append_to_memory_markdown`, `trim_markdown_window`, the vector store) is
reached only for `input-memory` graphs: `execute_agent` passes the tuple's
first element as `context_data` to `_prepare_context` and sets `memory_data =
None` whenever a Context runtime resolves (the `context_runtime is not None`
branch right after the `_prepare_context` call). Token tracking
and compaction thresholds are in [memory_compaction.md](memory_compaction.md).

---

## execute_agent vs execute_chat_agent

Both methods live in `server/services/ai.py` and follow the same general pattern. Key differences:

| Aspect | `execute_agent()` | `execute_chat_agent()` |
|--------|-------------------|----------------------|
| **Loop call** | Calls `run_native_agent_loop` with the resolved recursion limit | Calls `run_native_agent_loop` with the resolved recursion limit when tools exist and with `max_iterations=1` otherwise |
| **Tool failure** | Callback re-raises; the shared loop serializes the exception into a native tool-result message | Callback returns `{"error": str(e)}`; the shared loop serializes that result into the same native tool-result shape |
| **No-tool path** | The first native turn normally returns immediately | One native loop iteration through `ChatUnifier` |
| **Result metadata** | `agent_type: "agent"` | `agent_type: "chat" / "chat_with_skills" / "chat_with_tools" / "chat_with_skills_and_tools"` |

### Specialized Agent Routing

There is no hardcoded routing table for agents. Wave 11.C deleted the
`SPECIALIZED_AGENT_TYPES → partial(handle_chat_agent, ...)` registry that
node_executor used to build by hand. Every agent variant is now a
self-contained plugin folder under `server/nodes/agent/<plugin>/` whose
`BaseNode` subclass registers itself into `services.node_registry`
(`_HANDLER_REGISTRY`) at import time via `register_node(...)`. `NodeExecutor._dispatch()`
does a single registry lookup — `self._handlers.get(node_type)` — and the
plugin handler that won the merge runs the node's `@Operation("execute")`
`execute_op` method (`server/services/node_executor.py:_dispatch`,
`_build_handler_registry`).

Each agent's `execute_op` calls the matching `AIService` method:

```python
# server/nodes/agent/_specialized.py — every specialized agent + team lead
from ._inline import prepare_agent_call
kwargs = await prepare_agent_call(...)                  # collect_agent_connections + prompt/task prep
response = await ai_service.execute_chat_agent(ctx.node_id, **kwargs)

# server/nodes/agent/ai_agent/__init__.py    -> ai_service.execute_agent(...)
# server/nodes/agent/chat_agent/__init__.py  -> ai_service.execute_chat_agent(...)
# server/nodes/agent/rlm_agent/__init__.py   -> ai_service.rlm_service.execute(...)
# server/nodes/agent/claude_code_agent/__init__.py / codex_agent/__init__.py
#   -> AICliService (CLI agent runtime) via the plugin's own execute_op
```

`prepare_agent_call` (in `server/nodes/agent/_inline.py`) chooses
`execute_agent` vs `execute_chat_agent` based on `self.type` and shapes the
shared keyword bundle; `_specialized.py` is the single body shared by all
chat-agent-path specialized agents. Glob `server/nodes/agent/` for the
authoritative agent list, or read `AI_AGENT_TYPES` in
[`server/constants.py`](../server/constants.py) — do not hand-maintain a
count here. The frozenset currently spans: the two
base agents (`aiAgent`, `chatAgent`), the specialized agents
(`android_agent`, `coding_agent`, `web_agent`, `task_agent`, `social_agent`,
`travel_agent`, `tool_agent`, `productivity_agent`, `payments_agent`,
`consumer_agent`, `autonomous_agent`), the 2 team leads
(`orchestrator_agent`, `ai_employee`), the 2 CLI/REPL agents that bypass
the standard loop (`rlm_agent`, `claude_code_agent`), and
`vertex_managed_agent` (Vertex-hosted, own dispatch path). `codex_agent` is
a sibling plugin folder on the same CLI-agent path but is not in the
frozenset.

### Temporal dispatch routing

Two settings flags route agent execution through different Temporal paths (see [TEMPORAL_ARCHITECTURE.md](TEMPORAL_ARCHITECTURE.md) for the full matrix):

| Flag | Off | On (default) |
|---|---|---|
| `TEMPORAL_PER_TYPE_DISPATCH` | Ordinary nodes use the legacy `execute_node_activity` dispatcher (WS round-trip); enabled agent child-Workflow routing still takes precedence for supported types. | Ordinary nodes use per-type `node.{type}.v{version}` Activities registered by `BaseNode.as_activity()`. Graph dispatch honors plugin retry/queue; Workspace-task patches and version 1 agent-tool dispatch also use plugin timeout/heartbeat. |
| `TEMPORAL_AGENT_WORKFLOW_ENABLED` | Supported agents run the in-process loop inside a node Activity: per-type when per-type dispatch is on, otherwise the legacy single dispatcher. | Types in `AGENT_WORKFLOW_TYPES` become **child Workflows** (`AgentWorkflow`). LLM steps and ordinary tools are Activities; preparation resolves configuration and explicit refresh binds canvas changes. `rlm_agent`, `claude_code_agent`, and `vertex_managed_agent` remain whole-node Activities. |

Both flags default to `true` in `.env.template`; the starter freezes their
values and the worker-pool routing flag into each execution's input.

`AgentWorkflow` executions carry messages in the single `MessageWire` shape;
there is one engine and one wire standard, with no `llm_engine` or
`message_wire_version` discriminator recorded (locked by
`tests/llm/test_single_wire_standard.py`). Native executions never fall back
after a provider request starts, and changing the environment does not alter an
execution whose `agent.prepare_payload` result is already in history.

### Version 1 cooperative Stop and Resume

New generation manifests enable `execution_control_version=1`, separately
from the dispatch flags. Workflow gates close before model requests, tools,
compaction, explicit binding refresh, polling fetches, and child starts. Stop
drains already-admitted parallel work and its normal result bookkeeping;
unstarted actions wait. If a recorded response requested A and B, stopping
during A retains A's result and leaves B pending. Resume continues with B,
preserving the current model response and transcript.

The controller tracks graph/cron/job roots and detached delegation runners
that can outlive their parent. Attached agent descendants are discovered by
native execution descriptions after their child-start acknowledgement fence.
Completed Updates acknowledge admission closure and drain; Queries report
state. Resume holds producers while descendants/roots are released, then
releases controller, local, and schedule producers. Revision and chain checks
prevent stale intent or reused Workflow IDs from controlling another execution.

At clean continue-as-new boundaries, version 1 carries prepared configuration,
resolved tools and plugin timeout/retry/heartbeat/queue declarations, transcript,
thinking, iteration, usage, execution identity, and control state. A stopped
agent parks before rollover. The complete encoded next input is checked against
1,900,000 bytes; an oversized continuation raises `AgentContinuationTooLarge`
instead of returning to the opening prompt. Old generations retain their
legacy command paths and capacity fallback for replay compatibility.

Unlimited transient LLM retries can keep a Stop in `pausing`. A tool's retry
and idempotency contract still governs effects lost before recorded completion.
Opaque CLI/REPL/managed-agent nodes stop after the whole Activity settles;
heartbeats do not checkpoint their internal sessions. Direct Workspace tasks
and separately approved sends retain independent ownership. See
[Temporal workflow control](temporal-workflow-control.md) for lifecycle/API
details and [Temporal architecture](TEMPORAL_ARCHITECTURE.md#cooperative-generation-stop-and-resume)
for accounting, topology, and continuation.

**Team leads** (`orchestrator_agent`, `ai_employee`) share the supported agent
runtime and add an `input-teammates` handle. Connected agents become authorized
Task Manager assignees via `collect_teammate_connections()` (in
`server/services/plugin/edge_walker.py`, called from
`server/nodes/agent/_inline.py:prepare_agent_call`). See [agent_teams.md](agent_teams.md).

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

Routing: `rlm_agent` is a plugin folder like every other agent. Its
`execute_op` (`server/nodes/agent/rlm_agent/__init__.py`) delegates to
`ai_service.rlm_service.execute(...)` rather than `run_native_agent_loop`. See
[rlm_service.md](rlm_service.md) for full details.

### Chat Agent Conditional Loop

```python
# In execute_chat_agent():
if all_tools:
    final_state = await run_native_agent_loop(
        self.chat_unifier,
        provider=provider,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        initial_messages=messages,
        tools=all_tools,
        tool_executor=chat_tool_executor,
        max_iterations=recursion_limit,
    )
else:
    final_state = await run_native_agent_loop(
        self.chat_unifier,
        provider=provider,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        initial_messages=messages,
        max_iterations=1,
    )
```

The no-tool path uses the same native step contract but is explicitly bounded
to one iteration.

---

## AI Agent vs Chat Agent (Zeenie)

`aiAgent` and `chatAgent` (display name "Zeenie") are near-identical in
capability — both run `run_native_agent_loop` through `ChatUnifier`, support
context / skills / tools / task input, and can delegate asynchronously. The
differences are which backend method they call and the softness of callback
error handling.

| Feature | AI Agent (`aiAgent`) | Zeenie (`chatAgent`) |
|---------|----------------------|----------------------|
| Tool Calling | Yes (agent loop) | Yes (agent loop) |
| Memory Support | Yes | Yes |
| Skill Support | Yes | Yes |
| Task Input | Yes (`input-task`) | Yes (`input-task`) |
| Bottom Handles | Skill, Tools | Skill, Tools |
| Left Handles | Input, Context, Task | Input, Context, Task |
| Backend Method | `execute_agent()` | `execute_chat_agent()` |
| No-tool path | Native loop returns after the first final response | Native loop is bounded to one iteration |
| Tool-failure handling | Callback re-raises; loop creates an error tool-result message | Callback returns `{"error": str(e)}`; loop creates the tool-result message |
| Async Delegation | Yes (fire-and-forget) | Yes (fire-and-forget) |

See the full method comparison in [execute_agent vs execute_chat_agent](#execute_agent-vs-execute_chat_agent) above.

---

## Agent Input Methods

Both agents resolve their prompt the same way (logic in `server/nodes/agent/_inline.py:prepare_agent_call`):

1. **Template Variable (Explicit)** — set the Prompt field to a template like `{{chatTrigger.message}}` or `{{whatsappReceive.text}}`.
   - Templates are resolved by `ParameterResolver` before the plugin's `execute_op` runs.
   - Supports nested paths: `{{nodeName.nested.field}}`.

2. **Auto-Fallback (Implicit)** — leave the Prompt field empty (`prepare_agent_call` Step 2, lines ~85-93):
   - When `not parameters.get("prompt") and input_data`, the agent reads the output of the node wired to its `input-main` / `input-chat` handle (`input_data`, surfaced by `collect_agent_connections`).
   - Extraction order: `input_data["message"]` → `input_data["text"]` → `input_data["content"]` → `str(input_data)` (whole-output fallback).

**Task-context injection (Step 1)** runs before the prompt fallback: when an `input-task` edge supplies `task_data`, `format_task_context(task_data)` is prepended to the prompt. Tools are deliberately **kept**: the injected guidance tells a lead to `list_tasks` / `accept_task` / `reassign_task`, which needs the Task Manager tool (`nodes/agent/_inline.py` module docstring). The only agent that strips tools on task completion is `rlm_agent` (`nodes/agent/rlm_agent/__init__.py`, status in `("completed", "error")`).

**Example workflow:**
```
Chat Trigger → Zeenie ← HTTP Skill (SKILL.md context)
                       ← HTTP Request (tool node)
```
Zeenie loads SKILL.md instructions from connected skill nodes, builds
`AgentToolSpec` values from connected tool nodes (httpRequest,
calculatorTool, …), and uses `run_native_agent_loop` for LLM execution.

---

## Handle Topology

Handle layouts are declared in `server/nodes/agent/_handles.py` (local to `nodes/agent/`, not a global lookup). Two layouts cover every agent variant (glob `server/nodes/agent/` for the live count):

**`std_agent_handles()`** — shared by every agent variant except the two team leads:

| Handle | Kind | Position | Offset | Role |
|---|---|---|---|---|
| `input-skill` | input | bottom | 25% | skill |
| `input-tools` | input | bottom | 75% | tools |
| `input-main` | input | left | 25% | main |
| `input-context` | input | left | 50% | context |
| `input-task` | input | left | 75% | task |
| `output-main` | output | right | 50% | main |
| `output-top` | output | top | — | main |

**`team_lead_agent_handles()`** — `orchestrator_agent` and `ai_employee` only; adds an `input-teammates` handle (bottom, 80%) and shifts the bottom skill/tool offsets to 20%/50%. Connected agents become authorized Task Manager assignees via `collect_teammate_connections()` (in `server/services/plugin/edge_walker.py`); their delegate identities are hidden from the model and retained for trusted child dispatch. See [agent_teams.md](agent_teams.md).

`AIAgentNode.tsx` is type-agnostic: it calls `useNodeSpec(type)` and renders whatever handles / icon / color the spec returns — there is no `AGENT_CONFIGS` map (retired Wave 10.D). Specialized-agent visuals all come from the NodeSpec; per-type display name / subtitle / description live on the plugin class, icon from `<plugin>/icon.svg` (visuals.json emoji fallback), color from `<plugin>/meta.json`. Glob `server/nodes/agent/` for the authoritative agent list — do not hand-maintain one.

---

## Async Agent Delegation (overview)

AI Agents delegate to other agents wired to their `input-tools` handle, enabling hierarchical agent trees. There are two execution paths depending on whether the parent runs under Temporal F4.B:

- **F4.B (default, `TEMPORAL_AGENT_WORKFLOW_ENABLED=true`)**: direct non-team delegation starts attached `AgentWorkflow` children when the target type is supported. Parent identity mirrors progress and per-invocation `{task, context}` wins over saved configuration. The deterministic child ID includes parent Workflow ID, child node ID, iteration, and call index. Team leads use Task Manager instead: assignment persists, a detached `DelegatedTaskWorkflow` owns the permit and attached child, and the lead receives `queued` without waiting. Version 1 detached runners independently enroll with generation control; attached children are controlled through the native parent tree.
- **Legacy / F4.A-only**: fire-and-forget via `asyncio.create_task`. Still the path for `rlm_agent` / `claude_code_agent` (excluded from `AGENT_WORKFLOW_TYPES`) and any deployment with `TEMPORAL_AGENT_WORKFLOW_ENABLED=false`. The `delegate_to_*` tool spawns the child as a background task and returns immediately with `{"status": "delegated", "task_id": "..."}`; the parent keeps working while the child executes independently and broadcasts its own status (executing / success / error).

Design decisions (legacy path): **memory isolation** (child uses its own connected memory, not the parent's), **error isolation** (child errors are logged + broadcast, never propagated to the parent), **task tracking** (background tasks live in `_delegated_tasks`, cleaned up on completion).

Key files: `server/services/ai.py` (`DelegateToAgentSchema` in `_get_tool_schema()`, injects `ai_service` / `database` / `nodes` / `edges` into tool config), `server/services/handlers/tools.py` (`_execute_delegated_agent()`, `get_delegated_task_status()`). Full plumbing — memory / parameter / execution-context flow — is in [agent_delegation.md](agent_delegation.md); the multi-agent team-lead variant is in [agent_teams.md](agent_teams.md).


## Multimodal tool results

A tool result may opt into vision by including `llm_media: [{"ref": <FileRef
kind=image>, "detail": "auto"}]` next to its normal payload. The agent loop
(`agent_runtime.py`) and the Temporal activity path (`agent_activities.py`)
attach ref-only image blocks to the tool message; `run_native_llm_step`
hydrates them per provider call, gated by `provider_supports_vision` (unknown
providers degrade to a text placeholder, so non-vision models keep working
unchanged). Producers today: the `dataSource` image tier. For text-only host
models the `visionAnalyze` delegate tool provides image understanding
instead. Details: [native_llm_sdk.md](native_llm_sdk.md) and
[data_node.md](data_node.md).
