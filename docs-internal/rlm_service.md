# RLM Service -- Recursive Language Model Agent Integration

> **Related docs:** [memory_lifecycle.md](./memory_lifecycle.md) for the shared markdown memory format (RLM uses the same simpleMemory surface as aiAgent); [tool_building_pipeline.md](./tool_building_pipeline.md) for how connected tool nodes are bound to the REPL via `ToolBridgeAdapter`.

## Overview

The RLM (Recursive Language Models) service integrates the `rlms` library into MachinaOS as a specialized agent node (`rlm_agent`). Unlike other specialized agents that route to `handle_chat_agent` and use LangGraph, the RLM agent uses its own REPL-based code execution loop where the LM writes Python code blocks that are `exec()`-ed in a sandboxed environment.

**Library**: [rlms](https://pypi.org/project/rlms/) (pip install rlms)
**Paper**: https://arxiv.org/abs/2512.24601
**Docs**: https://alexzhang13.github.io/rlm/

### How RLM Differs from LangGraph Agents

| Aspect | LangGraph Agents (aiAgent, chatAgent, etc.) | RLM Agent (rlm_agent) |
|--------|----------------------------------------------|----------------------|
| Execution model | LLM -> tool call -> LLM -> tool call | LLM -> `\`\`\`repl` code block -> exec() -> stdout -> LLM |
| Tool interface | LangChain StructuredTool with Pydantic schemas | Python functions injected into REPL namespace |
| State management | LangGraph StateGraph with message accumulation | Python variables in REPL namespace + `context` variable |
| Completion signal | LLM stops making tool calls | LLM calls `FINAL(answer)` or `FINAL_VAR(variable_name)` |
| Recursion | Agent delegation (fire-and-forget) | `rlm_query()` spawns child RLM with own REPL |
| Strengths | Structured tool calling, provider-agnostic | Complex reasoning, code-driven decomposition, recursive sub-problems |

---

## Architecture

```
MachinaOS Node Execution System
================================

[chatTrigger] --input-main--+
[simpleMemory] --input-memory--+
[masterSkill] --input-skill--+
                              v
                       +-----------+
                       | rlm_agent |
                       +-----+-----+
                             |
                      input-tools handle
                     /       |        \
  [openaiChatModel]  [pythonExecutor]  [braveSearch]
  (small LM,          (tool node,       (tool node,
   depth>=1)           bridged to        bridged to
                       custom_tool)      custom_tool)


Execution Flow
================================

NodeExecutor._dispatch()
  -> handle_rlm_agent()                    [server/services/handlers/rlm.py]
       |
       +-- _collect_agent_connections()     [REUSE from handlers/ai.py]
       +-- _format_task_context()           [REUSE from handlers/ai.py]
       +-- tool stripping on completion     [REUSE pattern]
       +-- auto-prompt fallback             [REUSE pattern]
       |
       v
     ai_service.rlm_service.execute()      [server/services/rlm/service.py]
       |
       +-- _build_skill_system_prompt()     [REUSE from ai.py]
       +-- is_model_valid_for_provider()    [REUSE from ai.py]
       +-- get_default_model_async()        [REUSE from ai.py]
       +-- self.auth.get_api_key()          [REUSE from AIService]
       |
       +-- BackendAdapter.adapt()           [server/services/rlm/adapters.py]
       +-- ChatModelExtractor.extract()     [server/services/rlm/adapters.py]
       +-- ToolBridgeAdapter.bridge()       [server/services/rlm/adapters.py]
       |
       v
     asyncio.to_thread(RLM.completion())    [rlm library, sync -> async bridge]
       |
       v
     Result formatting + memory save + broadcast
```

---

## File Structure

```
server/services/rlm/
  __init__.py          # Exports RLMService
  constants.py         # Provider maps, base URLs, default parameters
  adapters.py          # BackendAdapter, ChatModelExtractor, ToolBridgeAdapter
  service.py           # RLMService class with execute() method

server/services/handlers/
  rlm.py               # Thin handler (~70 lines, mirrors handle_chat_agent)
```

---

## LM Architecture

### Main LLM (depth=0)
The `rlm_agent` node's own provider/model configuration drives the outer REPL loop. This is the LM that writes `\`\`\`repl` code blocks and receives exec() output.

Configured via the standard AI agent parameters:
- `provider`: openai, anthropic, gemini, groq, openrouter, cerebras
- `model`: The model name (e.g., gpt-4o, claude-sonnet-4-20250514)
- `api_key`: From MachinaOS credential store

### Small LMs (depth>=1)
Connected `AI_CHAT_MODEL_TYPES` nodes (openaiChatModel, anthropicChatModel, etc.) provide backends for `llm_query()` and `rlm_query()` calls made from within the REPL code.

These are extracted by `ChatModelExtractor` and passed to RLM as `other_backends` / `other_backend_kwargs`.

**Example workflow**:
```
rlm_agent (gpt-4o)  +  openaiChatModel (gpt-4o-mini)
     |                        |
  depth=0                  depth>=1
  (writes REPL code)      (used by llm_query() inside REPL)
```

---

## Adapters

### BackendAdapter

Maps MachinaOS provider/model/api_key to RLM backend constructor arguments.

```python
BackendAdapter.adapt("openai", "gpt-4o", "sk-...")
# -> ("openai", {"model_name": "gpt-4o", "api_key": "sk-..."})

BackendAdapter.adapt("groq", "llama-3.3-70b-versatile", "gsk-...")
# -> ("openai", {"model_name": "llama-3.3-70b-versatile", "api_key": "gsk-...", "base_url": "https://api.groq.com/openai/v1"})
```

**Provider mapping** (constants.py):

| MachinaOS Provider | RLM Backend | Base URL Override |
|--------------------|-------------|-------------------|
| openai | openai | -- |
| anthropic | anthropic | -- |
| gemini | gemini | -- |
| groq | openai | https://api.groq.com/openai/v1 |
| openrouter | openrouter | https://openrouter.ai/api/v1 |
| cerebras | openai | https://api.cerebras.ai/v1 |

### ChatModelExtractor

Scans `tool_data` (nodes connected to `input-tools`) for `AI_CHAT_MODEL_TYPES` nodes. Extracts their provider/model/api_key and converts via `BackendAdapter` into RLM's `other_backends` format.

Currently RLM supports one `other_backend` (for depth>=1 calls). If multiple chat model nodes are connected, only the first is used.

### ToolBridgeAdapter

Bridges non-agent, non-chat-model tool nodes into RLM `custom_tools` dict format.

**How it works**:
1. Filters out `AI_AGENT_TYPES` (handled via delegation) and `AI_CHAT_MODEL_TYPES` (handled by ChatModelExtractor)
2. For each remaining tool node, creates a sync callable wrapper
3. The wrapper uses `asyncio.run_coroutine_threadsafe()` to call MachinaOS's `execute_tool()` dispatcher from RLM's synchronous REPL thread
4. Returns dict in RLM custom_tools format: `{"tool_name": {"tool": callable, "description": str}}`

**Why sync wrappers**: RLM's LocalREPL uses `exec()` which runs synchronously. MachinaOS tool handlers are async. The bridge uses `asyncio.run_coroutine_threadsafe()` with a 60-second timeout to cross the sync/async boundary.

**Supported tool nodes** (auto-bridged, no per-tool code needed):

| Tool Node | Bridged Name | REPL Usage |
|-----------|-------------|------------|
| pythonExecutor | python_executor | `python_executor(code="output = 2+2")` |
| httpRequest | http_request | `http_request(url="...", method="GET")` |
| duckduckgoSearch | duckduckgo_search | `duckduckgo_search(query="...")` |
| braveSearch | brave_search | `brave_search(query="...")` |
| serperSearch | serper_search | `serper_search(query="...")` |
| perplexitySearch | perplexity_search | `perplexity_search(query="...")` |
| calculatorTool | calculator_tool | `calculator_tool(operation="add", a=1, b=2)` |
| currentTimeTool | current_time_tool | `current_time_tool(timezone="UTC")` |
| crawleeScraper | crawlee_scraper | `crawlee_scraper(url="...")` |
| gmail | gmail | `gmail(operation="send", ...)` |
| sheets | sheets | `sheets(operation="read", ...)` |

Any tool node connected to `input-tools` is automatically bridged -- no per-tool configuration needed.

---

## RLM-Specific Parameters

These are extracted from the node's `options` object:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `maxIterations` | int | 30 | Maximum REPL loop iterations before stopping |
| `maxDepth` | int | 1 | Maximum recursion depth for `rlm_query()` calls |
| `maxBudget` | float | null | Maximum USD spend across all LM calls (optional) |
| `maxTimeout` | float | null | Maximum seconds for entire execution (optional) |
| `maxTokens` | int | null | Maximum tokens across all LM calls (optional) |
| `verbose` | bool | false | Enable detailed REPL output logging |

---

## Handler Pattern

The handler (`handle_rlm_agent`) follows `handle_chat_agent` exactly:

```python
# 1. Collect connections (same as handle_chat_agent)
memory_data, skill_data, tool_data, input_data, task_data = await _collect_agent_connections(
    node_id, context, database, log_prefix="[RLM Agent]"
)

# 2. Task context injection (same pattern)
if task_data:
    task_context = _format_task_context(task_data)
    parameters = {**parameters, 'prompt': f"{task_context}\n\n{parameters.get('prompt', '')}"}

    # 3. Tool stripping on task completion (same pattern)
    if task_status in ('completed', 'error') and tool_data:
        tool_data = []

# 4. Auto-prompt fallback (same pattern)
if not parameters.get('prompt') and input_data:
    prompt = input_data.get('message') or input_data.get('text') or ...

# 5. Delegate to RLM service (differs from handle_chat_agent)
return await ai_service.rlm_service.execute(node_id, parameters, ...)
```

---

## Registration Points

| File | Change |
|------|--------|
| `server/constants.py` | `'rlm_agent'` in `AI_AGENT_TYPES` frozenset |
| `server/services/node_executor.py` | `'rlm_agent': partial(handle_rlm_agent, ...)` in `_build_handler_registry()` |
| `server/services/handlers/__init__.py` | `from .rlm import handle_rlm_agent` |
| `server/services/ai.py` | `self.rlm_service = RLMService(auth=self.auth)` in `AIService.__init__()` |
| `server/requirements.txt` | `rlms>=0.1.1` |
| `server/pyproject.toml` | `"rlms>=0.1.1"` in `[project] dependencies` |

---

## Reused MachinaOS Functions

| Function | Source | Purpose in RLM |
|----------|--------|----------------|
| `_collect_agent_connections()` | `handlers/ai.py:17` | Discover memory, skills, tools, input, task connections |
| `_format_task_context()` | `handlers/ai.py:352` | Format delegated task results as prompt context |
| `_build_skill_system_prompt()` | `ai.py:833` | Build system prompt from connected skill nodes |
| `is_model_valid_for_provider()` | `ai.py:362` | Validate model name for provider |
| `get_default_model_async()` | `ai.py:377` | Look up default model from DB or config |
| `self.auth.get_api_key()` | AIService | Retrieve API key from credential store |
| `save_message()` | `memory_store.py` | Persist conversation to memory |
| `execute_tool()` | `handlers/tools.py:34` | Dispatch tool execution (used by ToolBridgeAdapter) |
| `broadcaster.update_node_status()` | `status_broadcaster.py` | Real-time UI status updates |

---

## RLM Concepts Quick Reference

### REPL Code Blocks
The LM writes Python code inside `\`\`\`repl` fenced blocks. These are extracted via regex and executed with `exec()` in a sandboxed namespace.

### Available Functions in REPL
- `llm_query(prompt)` -- Plain LM call (uses depth+1 backend)
- `rlm_query(prompt)` -- Recursive child RLM with its own REPL
- `FINAL(answer)` -- Signal completion with answer string
- `FINAL_VAR(variable_name)` -- Signal completion, answer is the value of a Python variable
- `SHOW_VARS()` -- Print current namespace variables (debugging)
- `context` -- Python variable containing the input prompt text

### Execution Loop
```
1. Build prompt from iteration history
2. Call LM -> get response
3. Extract ```repl code blocks via regex
4. Execute code in sandboxed REPL via exec()
5. Check for FINAL/FINAL_VAR in output
6. If found -> return answer
7. Feed stdout back as next iteration context
8. Repeat until FINAL or limits exceeded
```

### Resource Limits
- `max_iterations` -- Loop iteration cap (default 30)
- `max_depth` -- Recursion depth cap for rlm_query() (default 1)
- `max_budget` -- USD spend cap
- `max_timeout` -- Wall-clock time cap (seconds)
- `max_tokens` -- Total token cap across all LM calls
- `max_errors` -- Consecutive execution error cap

Each child RLM (via `rlm_query`) gets its own LMHandler + LocalREPL on a separate port. Resource limits propagate: children get remaining budget/timeout, not the original totals.

---

## Status Broadcasting Phases

The RLM service broadcasts these phases via WebSocket for UI animations:

| Phase | When | Details |
|-------|------|---------|
| `initializing` | Before RLM creation | provider, model |
| `building_tools` | Before tool bridge | message |
| `executing` | During RLM.completion() | max_iterations, max_depth, tool_count |
| `completed` | After successful completion | iterations count, execution time |
| `error` | On failure | error message |

---

## Future Phases

### Delegation Support (Phase 4)
Add `'rlm_agent'` to:
- `DEFAULT_TOOL_NAMES` -> `'delegate_to_rlm_agent'`
- `DEFAULT_TOOL_DESCRIPTIONS` -> ONE-SHOT delegation description
- `_get_tool_schema()` -> `DelegateToAgentSchema` condition
- `execute_tool()` dispatch tuple in `handlers/tools.py`

### Frontend (Phase 5)
- Node definition in `specializedAgentNodes.ts`
- `AGENT_CONFIGS` entry in `AIAgentNode.tsx`
- Parameter panel integration in `MiddleSection.tsx` and `InputSection.tsx`

### RLM Skill (Phase 6)
- `server/skills/rlm_agent/rlm-reasoning-skill/SKILL.md`
- REPL usage instructions, available functions, decomposition strategies
