# RLM Service -- Recursive Language Model Agent Integration

> **Related docs:** [agent_context_flow.md](./agent_context_flow.md) for conversation continuity — RLM is a specialized provider, so a connected Context node reaches it through `SpecializedAgentContextBridge` (`services/cli_agent/context_bridge.py`); [tool_building_pipeline.md](./tool_building_pipeline.md) for how connected tool nodes are bound to the REPL via `ToolBridgeAdapter`. [memory_lifecycle.md](./ARCHIVE/memory_lifecycle.md) describes only the retired markdown memory path.

## Overview

The RLM (Recursive Language Models) service integrates the `rlms` library into OpenCompany as a specialized agent node (`rlm_agent`). Unlike other specialized agents that dispatch through `SpecializedAgentBase` and the standard native agent loop, the RLM agent uses its own REPL-based code execution loop where the LM writes Python code blocks that are `exec()`-ed in a sandboxed environment.

**Library**: [rlms](https://pypi.org/project/rlms/) (pip install rlms)
**Paper**: https://arxiv.org/abs/2512.24601
**Docs**: https://alexzhang13.github.io/rlm/

### Temporal generation Stop/Resume boundary

The RLM REPL and recursive provider/tool calls execute inside one node Activity;
`rlm_agent` is outside `AGENT_WORKFLOW_TYPES`. In new controlled generations
(`execution_control_version=1`), Stop lets that admitted Activity settle under
its scheduled policy, consumes its result normally, and fences the next graph
or parent-agent action. Resume releases the existing continuation without
relaunching the recorded completed RLM work. It does not pause at individual
REPL iterations or recursive calls, and heartbeats do not serialize a REPL
checkpoint. Failure recovery and unrecorded effects remain subject to the
Activity/tool retry and idempotency contract. Legacy generation behavior is
preserved. See [Temporal workflow control](temporal-workflow-control.md) and
[RLM node control boundary](node-logic-flows/specialized_agents/rlm_agent.md#temporal-stopresume-boundary).

### How RLM Differs from Standard Agents

| Aspect | Standard Agents (aiAgent, chatAgent, etc.) | RLM Agent (rlm_agent) |
|--------|---------------------------------------------|----------------------|
| Execution model | LLM -> tool call -> LLM -> tool call | LLM -> `\`\`\`repl` code block -> exec() -> stdout -> LLM |
| Tool interface | Provider-neutral `AgentToolSpec` / `ToolDef` values with Pydantic validation | Python functions injected into REPL namespace |
| State management | In-process `run_native_agent_loop` or durable Temporal `AgentWorkflow` transcript | Python variables in REPL namespace + `context` variable |
| Completion signal | LLM stops making tool calls | LLM calls `FINAL(answer)` or `FINAL_VAR(variable_name)` |
| Recursion | Attached Temporal children, detached Task Manager runners, or process-local delegation according to routing | `rlm_query()` spawns child RLM with own REPL |
| Strengths | Structured tool calling, provider-agnostic | Complex reasoning, code-driven decomposition, recursive sub-problems |

---

## Architecture

```
OpenCompany Node Execution System
================================

[chatTrigger] --input-main--+
[context]     --input-context--+     (RFC-0002 Context node; the retired
[masterSkill] --input-skill--+        input-memory handle no longer exists)
                              v
                       +-----------+
                       | rlm_agent |
                       +-----+-----+
                             |
                      input-tools handle
                     /       |        \         \
  [openaiChatModel]  [pythonExecutor]  [braveSearch]  [simpleMemory]
  (small LM,          (tool node,       (tool node,    (Memory is an
   depth>=1)           bridged to        bridged to     ordinary tool)
                       custom_tool)      custom_tool)


Execution Flow
================================

NodeExecutor._dispatch()
  -> RLMAgentNode.execute_op()             [server/nodes/agent/rlm_agent/__init__.py]
       |                                    (post-Wave-11 replacement for the
       |                                     retired server/services/handlers/rlm.py)
       +-- collect_agent_connections()      [services/plugin/edge_walker.py]
       +-- format_task_context()            [services/plugin/edge_walker.py]
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

server/nodes/agent/rlm_agent/
  __init__.py          # RLMAgentNode plugin + execute_op orchestration
  meta.json            # Plugin metadata
```

---

## LM Architecture

### Main LLM (depth=0)
The `rlm_agent` node's own provider/model configuration drives the outer REPL loop. This is the LM that writes `\`\`\`repl` code blocks and receives exec() output.

Configured via the standard AI agent parameters:
- `provider`: openai, anthropic, gemini, groq, openrouter, cerebras
- `model`: The model name (e.g., gpt-4o, claude-sonnet-4-20250514)
- `api_key`: From OpenCompany credential store

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

Maps OpenCompany provider/model/api_key to RLM backend constructor arguments.

```python
BackendAdapter.adapt("openai", "gpt-4o", "sk-...")
# -> ("openai", {"model_name": "gpt-4o", "api_key": "sk-..."})

BackendAdapter.adapt("groq", "llama-3.3-70b-versatile", "gsk-...")
# -> ("openai", {"model_name": "llama-3.3-70b-versatile", "api_key": "gsk-...", "base_url": "https://api.groq.com/openai/v1"})
```

**Provider mapping** (constants.py):

| OpenCompany Provider | RLM Backend | Base URL Override |
|--------------------|-------------|-------------------|
| openai | openai | -- |
| anthropic | anthropic | -- |
| gemini | gemini | -- |
| groq | openai | https://api.groq.com/openai/v1 |
| openrouter | openrouter | https://openrouter.ai/api/v1 |
| cerebras | openai | https://api.cerebras.ai/v1 |

### ChatModelExtractor

Scans `tool_data` (nodes connected to `input-tools`) for `AI_CHAT_MODEL_TYPES` nodes. Takes each node's provider from `detect_ai_provider` (chat-model nodes carry no `provider` field: the type names it, or the `endpoint` parameter for an OpenAI-compatible node) plus its model and api_key, and converts them via `BackendAdapter` into RLM's `other_backends` format. A provider missing from the mapping above (Ollama, LM Studio, xAI, a named endpoint, ...) is refused with a clear error rather than sent to the OpenAI backend, since RLM never reads a user base URL.

Currently RLM supports one `other_backend` (for depth>=1 calls). If multiple chat model nodes are connected, only the first is used.

### ToolBridgeAdapter

Bridges non-agent, non-chat-model tool nodes into RLM `custom_tools` dict format.

**How it works**:
1. Filters out `AI_AGENT_TYPES` (handled via delegation) and `AI_CHAT_MODEL_TYPES` (handled by ChatModelExtractor)
2. For each remaining tool node, creates a sync callable wrapper
3. The wrapper uses `asyncio.run_coroutine_threadsafe()` to call OpenCompany's `execute_tool()` dispatcher from RLM's synchronous REPL thread
4. Returns dict in RLM custom_tools format: `{"tool_name": {"tool": callable, "description": str}}`

**Why sync wrappers**: RLM's LocalREPL uses `exec()` which runs synchronously. OpenCompany tool handlers are async. The bridge uses `asyncio.run_coroutine_threadsafe()` with a 60-second timeout to cross the sync/async boundary.

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

## Plugin Execution Pattern

`RLMAgentNode.execute_op` shares the standard edge-collection and task/input
preparation helpers, then delegates to the independent RLM service:

```python
# 1. Collect connected context, skills, tools, input, and task data
#    (nodes/agent/rlm_agent/__init__.py:61-72)
context_data, skill_data, tool_data, input_data, task_data = await collect_agent_connections(
    node_id, ctx.raw, database, log_prefix="[RLM Agent]"
)
# The first element is a Context descriptor (kind == "context") when a
# Context node is wired, or the legacy Memory descriptor on input-memory
# graphs. The service receives them as separate kwargs:
# context_data=context_descriptor, memory_data=legacy.
context_descriptor = context_data if (context_data or {}).get("kind") == "context" else None
memory_data = context_data if context_data and not context_descriptor else None

# 2. Inject task context and strip tools for terminal task notifications
if task_data:
    task_context = format_task_context(task_data)
    parameters = {**parameters, 'prompt': f"{task_context}\n\n{parameters.get('prompt', '')}"}
    if task_status in ('completed', 'error') and tool_data:
        tool_data = []

# 3. Apply the same text-shaped auto-prompt fallback as standard agents
if not parameters.get('prompt') and input_data:
    prompt = input_data.get('message') or input_data.get('text') or ...

# 4. Invoke the RLM-specific REPL runtime
return await ai_service.rlm_service.execute(node_id, parameters, ...)
```

---

## Registration Points

| File | Change |
|------|--------|
| `server/constants.py` | `'rlm_agent'` in `AI_AGENT_TYPES` frozenset |
| `server/nodes/agent/rlm_agent/__init__.py` | `RLMAgentNode` auto-registers through the plugin base and owns `execute_op` |
| `server/services/ai.py` | `self.rlm_service = RLMService(auth=self.auth)` in `AIService.__init__()` |
| `server/pyproject.toml` / generated requirements | `rlms` runtime dependency |

---

## Reused OpenCompany Functions

| Function | Source | Purpose in RLM |
|----------|--------|----------------|
| `collect_agent_connections()` | `services/plugin/edge_walker.py` | Discover context, skills, tools, input, task connections (renamed Wave-11 successor to `handlers/ai.py::_collect_agent_connections`; the old handler file is gone) |
| `SpecializedAgentContextBridge` | `services/cli_agent/context_bridge.py` | Context continuity for a connected Context node: `resolve` loads the stored conversation (`rlm/service.py:48-56`), `augment_prompt` renders it into the REPL prompt (`:202-203`), `record_turn(original_prompt, response)` saves the turn (`:230-234`) |
| `prepare_agent_call()` | `nodes/agent/_inline.py` | Shared pre-dispatch helper — collects connections + injects task context + auto-prompt from upstream input |
| `_build_skill_system_prompt()` | `services/ai.py` | Build system prompt from connected skill nodes |
| `is_model_valid_for_provider()` | `services/ai.py` | Validate model name for provider |
| `get_default_model_async()` | `services/ai.py` | Look up default model from DB or config |
| `self.auth.get_api_key()` | AIService | Retrieve API key from credential store |
| `add_message()` | `services/memory_store.py:35` | Legacy V1 only — appends user/assistant turns to the in-memory session store when a V1 `memory_data` descriptor is present (`rlm/service.py:241-246`) |
| `execute_tool()` | `services/handlers/tools.py` | Dispatch tool execution (used by ToolBridgeAdapter) |
| `broadcaster.update_node_status()` | `status_broadcaster.py` | Real-time UI status updates |

---

## RLM Concepts Quick Reference

### REPL Code Blocks
The LM writes Python code inside `\`\`\`repl` fenced blocks. These are extracted via regex and executed with `exec()` in a sandboxed namespace.

### Available Functions in REPL
(provided by the upstream `rlm` package's REPL, not defined under `server/`)
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

## Later phases (all shipped)

The three follow-up phases this doc originally listed as future work have
landed; the mechanisms differ from the original plan because the intervening
Wave 11 / Wave 12 D5 refactors removed the surfaces they named.

### Delegation support (was Phase 4)
`rlm_agent` is delegatable. The `DEFAULT_TOOL_NAMES` /
`DEFAULT_TOOL_DESCRIPTIONS` dicts no longer exist (locked by
`tests/test_tool_registry.py`); the delegation identity comes from
`BaseNode.__init_subclass__`, which stamps `tool_name =
f"delegate_to_{cls.type}"` on every agent plugin (`services/plugin/base.py:257`).
`rlm_agent` is listed in both `_AGENT_DELEGATION_TYPES` (`services/ai.py:2509`,
the `DelegateToAgentSchema` gate) and `AI_AGENT_TYPES` (`server/constants.py:32`,
the `execute_tool` dispatch gate). It stays on the legacy fire-and-forget path
— excluded from `AGENT_WORKFLOW_TYPES` because its REPL state is externalised.

### Frontend (was Phase 5)
There is nothing per-agent to add. `specializedAgentNodes.ts` and the
`AGENT_CONFIGS` map were retired in Wave 10.D / Wave 11: `AIAgentNode.tsx`
renders handles / icon / colour / display name from the backend NodeSpec via
`useNodeSpec(type)`, and the parameter panel is schema-driven from the plugin's
`Params`. Visuals come from `nodes/agent/rlm_agent/meta.json` (+ `icon.svg` or
the `visuals.json` fallback).

### RLM skill (was Phase 6)
`server/skills/rlm_agent/rlm-reasoning-skill/SKILL.md` exists and covers REPL
usage, the available functions, and decomposition strategies.
