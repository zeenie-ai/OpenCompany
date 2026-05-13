# Deep Agent: LangChain DeepAgents Integration

Specialized agent node powered by [LangChain DeepAgents](https://github.com/langchain-ai/deepagents) -- a batteries-included agent harness with built-in filesystem tools, sub-agent delegation, auto-summarization, and todo planning.

> **Related Documentation:**
> - [Agent Architecture](./agent_architecture.md) - LangGraph skill injection and tool execution
> - [Node Creation Guide](./node_creation.md) - Canonical plugin recipe (covers specialized agents)
> - [Memory Lifecycle](./memory_lifecycle.md) - Canonical home for memory markdown, vector store, compaction triggers
> - [Tool Building Pipeline](./tool_building_pipeline.md) - Canonical home for tool schema construction
> - [Agent Delegation](./agent_delegation.md) - Memory, parameters, and execution context flow

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [DeepAgents Package](#deepagents-package)
4. [MachinaOs Integration](#machinaos-integration)
5. [Tool Execution](#tool-execution)
6. [Memory Integration](#memory-integration)
7. [Node Definition](#node-definition)
8. [Key Files](#key-files)
9. [Comparison with Other Agent Types](#comparison-with-other-agent-types)

---

## Overview

Deep Agent uses `langchain.agents.create_agent()` (the lower-level function from the `deepagents` package) to create a LangGraph agent that uses **only MachinaOs connected tool nodes** -- no built-in middleware tools. This ensures all tool executions show glow effects on the canvas.

**How it differs from chatAgent:**
- Uses `langchain.agents.create_agent()` instead of `build_agent_graph()` for graph creation
- Supports `PatchToolCallsMiddleware` for tool call formatting
- 4 dedicated filesystem/shell tool nodes (fileRead, fileModify, shell, fsSearch) delegate to `deepagents.backends.LocalShellBackend`

**What MachinaOs provides:**
- Provider/model selection from `llm_defaults.json` (9 providers)
- Executable tool node connections with glow effects (fileRead, fileModify, shell, fsSearch, calculator, search, WhatsApp, Android, etc.)
- Skill injection from Master Skill / SKILL.md files
- Memory via Simple Memory node (markdown + vector store)
- Teammate delegation via `input-teammates` handle
- Real-time status broadcasting via WebSocket

---

## Architecture

```
User clicks "Run" on Deep Agent
        |
        v
NodeExecutor._dispatch()                  server/services/node_executor.py
  Registry: 'deep_agent' -> handle_deep_agent (via partial)
        |
        v
handle_deep_agent()                       server/services/handlers/deep_agent.py
  1. _collect_agent_connections()          [REUSE from handlers/ai.py]
     - input-memory  -> memory_data
     - input-skill   -> skill_data[]
     - input-tools   -> tool_data[]
     - input-main    -> input_data
     - input-task    -> task_data
  2. _format_task_context()               [REUSE from handlers/ai.py]
  3. Auto-prompt fallback                 [same pattern as handle_rlm_agent]
  4. _collect_teammate_connections()       [REUSE from handlers/ai.py]
  5. Passes raw tool_data + build_tool_fn to service
        |
        v
DeepAgentService.execute()               server/services/agents/service.py
  Orchestration only -- delegates to adapters and shared helpers:
  - _build_skill_system_prompt()          [ai.py] -> system_message
  - is_model_valid_for_provider()         [ai.py] -> model validation
  - self._create_model()                  [ai.py] -> pre-built BaseChatModel
  - ToolAdapter.build_tools()             [agents/adapters.py] -> executable tools
  - SubAgentAdapter.convert()             [agents/adapters.py] -> deepagents SubAgent dicts
  - _parse_memory_markdown()              [ai.py] -> memory load
  - langchain.agents.create_agent()        [langchain] -> graph creation
  - agent.ainvoke()                        -> graph execution
  - ResponseExtractor.extract()           [agents/adapters.py] -> response + thinking
  - _append_to_memory_markdown()          [ai.py] -> memory save
        |
        v
langchain.agents.create_agent()          langchain package (lower-level)
  model = pre-built BaseChatModel (API key baked in)
  tools = ONLY MachinaOs connected tools (with glow effects via ToolAdapter)
  system_prompt = system_message (includes skills + long-term memory context)
  middleware = [PatchToolCallsMiddleware()] only (no built-in filesystem/todo)
        |
        v
agent.ainvoke(messages)                  LangGraph CompiledStateGraph
  messages = history from SimpleMemory + current prompt
  No built-in middleware tools -- uses only connected MachinaOs tool nodes
        |
        v
ResponseExtractor.extract() -> save memory -> return result
```

---

## DeepAgents Package

MachinaOs depends on `deepagents>=0.4.12` (with `langchain-core`, `langchain`, `langchain-anthropic`, `langchain-google-genai`, `wcmatch`). Upstream API surface (`create_deep_agent`, middleware, sub-agent protocol, backend protocol) is documented at https://github.com/langchain-ai/deepagents — refer there for SDK details.

MachinaOs **does not** call `create_deep_agent()` directly. We use the lower-level `langchain.agents.create_agent()` from the same package, passing a pre-built `BaseChatModel` and no default middleware so only connected MachinaOs tool nodes are used. Filesystem operations route through `LocalShellBackend(root_dir=workspace_dir, virtual_mode=True)` so paths sandbox to the per-workflow workspace.

### Built-in Tools

| Tool | Middleware | Description |
|------|-----------|-------------|
| `write_todos` | TodoListMiddleware | Task breakdown and progress tracking |
| `read_file` | FilesystemMiddleware | Read file contents with offset/limit |
| `write_file` | FilesystemMiddleware | Write/create files |
| `edit_file` | FilesystemMiddleware | Edit files with string replacement |
| `ls` | FilesystemMiddleware | List directory contents |
| `glob` | FilesystemMiddleware | Glob pattern file matching |
| `grep` | FilesystemMiddleware | Search file contents with regex |
| `execute` | FilesystemMiddleware | Run shell commands (requires SandboxBackendProtocol) |
| `task` | SubAgentMiddleware | Delegate work to sub-agents with isolated context |

### Middleware Stack (default order)

1. **TodoListMiddleware** -- Planning tool (`write_todos`)
2. **SkillsMiddleware** (conditional) -- Load skills from file paths
3. **FilesystemMiddleware** -- File operations + shell execution
4. **SubAgentMiddleware** -- Sub-agent spawning via `task` tool
5. **SummarizationMiddleware** -- Auto-summarization when conversations grow long
6. **PatchToolCallsMiddleware** -- Tool call patching
7. **AnthropicPromptCachingMiddleware** -- Prompt caching (Anthropic models only)
8. **MemoryMiddleware** (conditional) -- Persistent memory from AGENTS.md files

Custom tools passed via `tools=` parameter are added alongside (not replacing) the built-in tools. deepagents uses LangGraph's `ToolNode` which calls `tool.ainvoke()` directly -- tools must have real executor functions.

### Backends

Backends control where filesystem tools operate. MachinaOs uses `FilesystemBackend` with `virtual_mode=True` pointed at a per-workflow workspace directory.

| Backend | Description |
|---------|-------------|
| `StateBackend` | Default. Ephemeral files in LangGraph state (in-memory) |
| `FilesystemBackend` | Direct local filesystem under `root_dir`. `virtual_mode=True` sandboxes paths |
| `LocalShellBackend` | Extends FilesystemBackend with `execute` tool (unrestricted shell) |
| `StoreBackend` | Persistent cross-thread storage via LangGraph BaseStore |
| `CompositeBackend` | Route paths to different backends by prefix |

**MachinaOs configuration** (in `services/agents/service.py`):
```python
from deepagents.backends import FilesystemBackend

workspace_dir = parameters.get('workspace_dir')  # From execution context
backend = FilesystemBackend(root_dir=workspace_dir, virtual_mode=True)

agent = create_deep_agent(
    model=chat_model,
    backend=backend,
    ...
)
```

The workspace directory is created per workflow at `data/workspaces/<workflow_id>/`. Nodes (fileDownloader, code executors) save output files there, and the Deep Agent's filesystem tools operate within it. `virtual_mode=True` prevents path traversal outside the workspace.

### Sub-Agent Types

| Type | Description |
|------|-------------|
| `SubAgent` | Declarative spec: name, description, system_prompt, optional tools/model |
| `CompiledSubAgent` | Pre-built runnable (any LangGraph/LangChain Runnable) |
| `AsyncSubAgent` | Remote/background agent via graph_id + url |

---

## MachinaOs Integration

### Execution Path

Deep Agent has a **dedicated handler and modular service package** following the RLMService pattern:

- **Handler** (`handlers/deep_agent.py`): Thin wrapper that collects connections, passes raw `tool_data` to service
- **Service package** (`services/agents/`): Modular execution engine split into:
  - `service.py` -- Orchestration: calls adapters and shared helpers
  - `adapters.py` -- Protocol translation: `ToolAdapter`, `SubAgentAdapter`, `ResponseExtractor`
  - `constants.py` -- Provider mappings and defaults
- **Composition**: `AIService.__init__` creates `self.deep_agent_service = DeepAgentService(auth=self.auth, model_factory=self.create_model)`

```
server/services/agents/              (mirrors services/rlm/)
  __init__.py          - Public API: exports DeepAgentService
  service.py           - Orchestration only
  adapters.py          - ToolAdapter, SubAgentAdapter, ResponseExtractor
  constants.py         - PROVIDER_PREFIX, DEFAULT_MAX_TURNS
```

### Reused Helpers (from ai.py)

| Helper | Used For |
|--------|----------|
| `_build_skill_system_prompt()` | Skill injection into system_message |
| `_build_tool_from_node()` | Schema-only tool stubs from tool_data |
| `is_model_valid_for_provider()` | Model validation against provider |
| `get_default_model_async()` | Default model from llm_defaults.json |
| `_resolve_max_tokens()` | Max tokens from model registry |
| `_resolve_temperature()` | Temperature clamped to provider range |
| `self.create_model()` | Pre-built BaseChatModel with API key |
| `_parse_memory_markdown()` | Parse SimpleMemory markdown to messages |
| `_get_memory_vector_store()` | Long-term vector store retrieval |
| `_append_to_memory_markdown()` | Append exchange to memory |
| `_trim_markdown_window()` | Trim + archive removed messages |
| `extract_thinking_from_response()` | Extract thinking/reasoning content |
| `log_execution_time()`, `log_api_call()` | Metrics tracking |

---

## Tool Execution

### Problem
`_build_tool_from_node()` returns schema-only stubs with `placeholder_func` that echo kwargs back. deepagents' `ToolNode` calls `tool.ainvoke()` directly, so these stubs fail silently.

### Solution
`ToolAdapter` (in `agents/adapters.py`) wraps each schema-only stub with a real async coroutine:

```python
# agents/adapters.py - ToolAdapter._wrap()
async def _execute(**kwargs):
    from services.handlers.tools import execute_tool
    result = await execute_tool(tool_name, kwargs, config)
    return result

return StructuredTool.from_function(
    coroutine=_execute,
    args_schema=tool.get_input_schema(),
)
```

This follows the deepagents pattern: `StructuredTool.from_function(coroutine=async_fn, args_schema=...)`.

The handler passes raw `tool_data` to the service, which calls `ToolAdapter.build_tools()` to build executable tools. Connected MachinaOs tools (calculator, search, WhatsApp, etc.) execute via `handlers/tools.py execute_tool()`, with real-time glow effects broadcast to tool nodes.

---

## Memory Integration

Deep Agent uses MachinaOs's SimpleMemory node like every other agent — the full lifecycle (markdown parse / append / trim, vector store, claude_code_agent native session bridge) lives in [memory_lifecycle.md](./memory_lifecycle.md). Deep-Agent-specific bits: `DeepAgentService.execute()` accepts the parsed history as a LangChain `BaseMessage` list (same shape as aiAgent's input), and the post-execution save attaches a `memory.session_id` / `memory.history_loaded` block to the result envelope. No engine-specific memory format; the markdown is shared across all four agents that use simpleMemory.

---

## Node Definition

### Properties

Inherits all `AI_AGENT_PROPERTIES` via spread:
- **AI Provider** (options: openai, anthropic, gemini, openrouter)
- **Model** (dynamic options based on provider)
- **Prompt** (string, multiline)
- **System Message** (string, multiline)
- **Options** (collection: temperature, maxTokens, thinkingEnabled, thinkingBudget, reasoningEffort)

Plus one additional property:
- **Max Turns** (number, default: 25, range: 1-100) -- Maximum agentic loop iterations

### Input Handles

| Handle | Position | Description |
|--------|----------|-------------|
| `input-main` | Left 30% | Primary input (auto-prompt fallback) |
| `input-memory` | Left 65% | Simple Memory node for conversation history |
| `input-task` | Left 85% | Task completion events from taskTrigger |
| `input-skill` | Bottom 20% | Skill nodes (Master Skill) |
| `input-teammates` | Bottom 50% | Agents for sub-agent delegation |
| `input-tools` | Bottom 80% | Tool nodes for LLM tool calling |

### Output Handle

| Handle | Position | Description |
|--------|----------|-------------|
| `output-top` | Top | Agent output |

### Visual Config

- **Icon:** Brain (U+1F9E0)
- **Theme color:** `dracula.green`
- **Dimensions:** 300x200px
- **Component:** `AIAgentNode` (shared with all specialized agents)

### Output Schema

Matches `execute_chat_agent` format for frontend compatibility:
```python
{
    "response": str,           # AI text response
    "thinking": str | None,    # Extracted thinking/reasoning
    "thinking_enabled": bool,
    "model": str, "provider": str,
    "agent_type": str,         # "deep_agent", "deep_agent_with_tools", etc.
    "iterations": int,         # Tool-call loop count
    "messages_count": int,
    "finish_reason": "stop",
    "timestamp": str,
    "input": {"prompt": str, "system_message": str},
    "memory": {"session_id": str, "history_loaded": int},  # if connected
    "skills": {"connected": [str], "count": int},           # if connected
    "tools": {"connected": [str], "count": int},            # if connected
}
```

---

## Key Files

### Service Package (`server/services/agents/`)
| File | Description |
|------|-------------|
| `server/services/agents/__init__.py` | Public API: exports `DeepAgentService` |
| `server/services/agents/service.py` | Orchestration: calls adapters, shared helpers, `langchain.agents.create_agent()` |
| `server/services/agents/adapters.py` | `ToolAdapter`, `SubAgentAdapter`, `ResponseExtractor` |
| `server/services/agents/constants.py` | `PROVIDER_PREFIX`, `DEFAULT_MAX_TURNS` |

### Handler and Wiring
| File | Description |
|------|-------------|
| `server/services/handlers/deep_agent.py` | Thin handler: collects connections, passes raw tool_data |
| `server/services/handlers/__init__.py` | Import/export `handle_deep_agent` |
| `server/services/ai.py` | AIService.__init__ composes `self.deep_agent_service` |
| `server/services/node_executor.py` | Handler registry entry |
| `server/services/handlers/tools.py` | Delegation tuple |
| `server/constants.py` | `AI_AGENT_TYPES` |

### Frontend
| File | Description |
|------|-------------|
| `client/src/components/AIAgentNode.tsx` | `AGENT_CONFIGS` visual entry |
| `client/src/components/parameterPanel/MiddleSection.tsx` | `AGENT_WITH_SKILLS_TYPES` |
| `client/src/components/parameterPanel/InputSection.tsx` | `AGENT_WITH_SKILLS_TYPES` + `aiAgentTypes` |
| `client/src/contexts/WebSocketContext.tsx` | `LONG_RUNNING_NODE_TYPES` |

### Dependencies
| File | Description |
|------|-------------|
| `server/requirements.txt` | `deepagents>=0.4.12` |
| `server/pyproject.toml` | `deepagents>=0.4.12` |

---

## Filesystem & Shell Tool Nodes

MachinaOs provides 4 dual-purpose tool nodes that delegate to `deepagents.backends.LocalShellBackend`:

| Node | Tool Name | Description |
|------|-----------|-------------|
| `fileRead` | `file_read` | Read file contents with pagination |
| `fileModify` | `file_modify` | Write new files or edit existing files (string replacement) |
| `shell` | `shell_execute` | Execute shell commands with timeout |
| `fsSearch` | `fs_search` | List directories, glob pattern match, or grep file contents |

These work as standalone workflow nodes AND as AI agent tools when connected to any agent's `input-tools` handle. Defined as backend plugins under `server/nodes/filesystem/*.py`.

---

## Comparison with Other Agent Types

| Aspect | chatAgent | deep_agent | claude_code_agent | rlm_agent |
|--------|-----------|------------|-------------------|-----------|
| **Handler** | `handle_chat_agent` | `handle_deep_agent` | `handle_claude_code_agent` | `handle_rlm_agent` |
| **Service** | `AIService.execute_chat_agent()` | `DeepAgentService.execute()` | `ClaudeCodeService.execute()` | `RLMService.execute()` |
| **Graph creation** | `build_agent_graph()` | `langchain.agents.create_agent()` | Claude Code CLI | RLM REPL loop |
| **Built-in tools** | None | None (uses only connected nodes) | 6 (Read, Edit, Bash, etc.) | 3 (llm_query, rlm_query, FINAL) |
| **Connected tools** | Yes | Yes (executable, with glow) | No | Yes (bridged) |
| **Skills** | Yes | Yes | Yes (prompt only) | Yes |
| **Memory** | Yes (markdown + vector) | Yes (markdown + vector) | No | Yes (context only) |
| **Teammates** | Team leads only | Yes | No | No |
| **Providers** | All 9 | All 9 | Anthropic only | All 9 |
| **Middleware** | None | PatchToolCallsMiddleware only | None | None |
| **Auto-summarization** | No | No | No | No |
| **Planning** | No | No | No | No |
| **Tool glow effects** | Yes | Yes | No | No |
