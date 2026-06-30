# Generic Specialized Agent Pattern (`SpecializedAgentBase`)

This document describes the shared behavioural contract for the 13 specialized
agent nodes that share one plugin base class:

`android_agent`, `coding_agent`, `web_agent`, `task_agent`, `social_agent`,
`travel_agent`, `tool_agent`, `productivity_agent`, `payments_agent`,
`consumer_agent`, `autonomous_agent`, `orchestrator_agent`, `ai_employee`.

Post-Wave-11 there is no `handle_chat_agent` handler. Each agent is a
self-contained plugin folder under `server/nodes/agent/<agent>/__init__.py`
declaring a `SpecializedAgentBase` subclass; the body lives in
[`server/nodes/agent/_specialized.py`](../../../server/nodes/agent/_specialized.py).
Dispatch is via `BaseNode.execute()` -> the `@Operation("execute")` method
(`SpecializedAgentBase.execute_op`), which calls
[`prepare_agent_call`](../../../server/nodes/agent/_inline.py) for the shared
pre-dispatch flow then `AIService.execute_chat_agent`. The agents differ only
in frontend presentation (icon, color, subtitle) and intended use case. The
two agents that DON'T use `execute_chat_agent` (`rlm_agent`,
`claude_code_agent`) are documented separately.

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/<agent>/__init__.py`](../../../server/nodes/agent/) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) |
| **Dispatch** | `BaseNode.execute()` -> `@Operation("execute")` -> `prepare_agent_call` -> `AIService.execute_chat_agent` |
| **Connection collection** | [`server/services/plugin/edge_walker.py::collect_agent_connections`](../../../server/services/plugin/edge_walker.py) (5-tuple: memory, skill, tool, input, task) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |
| **Architecture docs** | [Agent Architecture](../../agent_architecture.md), [Agent Delegation](../../agent_delegation.md), [Agent Teams](../../agent_teams.md) |

## Purpose

A specialized agent is an `aiAgent` pre-configured for a specific domain
(Android control, coding, web automation, etc.). From the backend's
perspective it is identical to `chatAgent`: same `execute_chat_agent` path,
same inputs, same output envelope, same delegation semantics. The
specialization is conveyed only through:

1. Connected skills (usually via a Master Skill node seeded with the
   domain-specific folder in `server/skills/`).
2. Connected tools (e.g. `coding_agent` typically has `pythonExecutor` wired
   to `input-tools`).
3. Frontend cosmetics (icon + color from the plugin folder / `visuals.json`,
   plus `display_name` / `subtitle` on the plugin class). The old
   `AGENT_CONFIGS` map was retired -- `AIAgentNode.tsx` reads the NodeSpec.

## Inputs (handles)

All 13 agents share these input handles.

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | input/main | no | Upstream data; auto-prompt fallback when `prompt` param is empty |
| `input-skill` | input/skill | no | Skill node(s) providing SKILL.md context |
| `input-memory` | input/memory | no | `simpleMemory` node for conversation history |
| `input-tools` | input/tools | no | Tool nodes exposed to the LLM via `chat_model.bind_tools` |
| `input-task` | input/task | no | `taskTrigger` events from delegated child agents |
| `input-teammates` | input/teammates | no | **Only on `orchestrator_agent` and `ai_employee`** (via `team_lead_agent_handles()`) -- connected agents become `delegate_to_*` tools |

Handle topology comes from `std_agent_handles()` (or `team_lead_agent_handles()`
for the two team leads) in
[`server/nodes/agent/_handles.py`](../../../server/nodes/agent/_handles.py).

## Parameters

All 13 specialized agents share the `SpecializedAgentParams` Pydantic model in
[`server/nodes/agent/_specialized.py`](../../../server/nodes/agent/_specialized.py).
There are **no** per-agent parameter extras: `orchestrator_agent` and
`ai_employee` reuse the same `Params` (they override only `handles` +
`tool_description`), so there is no `teamMode` / `maxConcurrent` field.

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `prompt` | string | `""` | no | - | User prompt; falls back to upstream output when empty (4 rows) |
| `provider` | enum | `openai` | no | - | `openai` / `anthropic` / `gemini` / `openrouter` / `groq` / `cerebras` / `deepseek` / `kimi` / `mistral` / `ollama` / `lmstudio` |
| `model` | string | `""` | no | - | Model ID (resolved against the provider; empty falls through to default) |
| `system_message` | string\|null | `"You are a helpful assistant"` | no | - | System instructions (3 rows) |
| `temperature` | float\|null | `None` | no | group `options` | 0.0-2.0; `None` -> `agent.default_temperature` in `llm_defaults.json` |
| `max_tokens` | int\|null | `None` | no | group `options` | 1-200000; `None` -> per-model default (not silently capped) |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` / `output-top` / `output-0` | object | Standard agent envelope |

### Output payload

```ts
{
  response: string;          // Final agent message
  thinking?: string;         // Extended thinking content (when enabled)
  model: string;
  provider: string;
  finish_reason?: string;
  timestamp: string;
}
```

Wrapped in the standard envelope:
`{ success: true, result: <payload>, execution_time: number }`.

## Logic Flow

```mermaid
flowchart TD
  A[BaseNode.execute -> execute_op] --> P[prepare_agent_call]
  P --> B[collect_agent_connections]
  B --> B1[memory_data from input-memory]
  B --> B2[skill_data from input-skill<br/>masterSkill expanded to N skills]
  B --> B3[tool_data from input-tools<br/>androidTool sub-nodes discovered]
  B --> B4[input_data from input-main]
  B --> B5[task_data from input-task]
  B1 --> C{task_data present?}
  B2 --> C
  B3 --> C
  B4 --> C
  B5 --> C
  C -- yes + completed/error --> D[format_task_context<br/>prepend to prompt<br/>STRIP ALL tools]
  C -- no --> E{prompt empty<br/>and input_data?}
  D --> E
  E -- yes --> F[prompt = input_data.message/text/content/str]
  E -- no --> G{team lead?<br/>orchestrator / ai_employee}
  F --> G
  G -- yes --> H[collect_teammate_connections<br/>append as delegate_to_* tools]
  G -- no --> I[ai_service.execute_chat_agent]
  H --> I
  I --> J[Return result payload<br/>or raise RuntimeError]
```

## Decision Logic

- **Task completion short-circuit**: if `task_data.status in {completed,
  error}`, the handler strips **all** tools from `tool_data`. This prevents
  Gemini from hallucinating a delegate call when the original prompt says
  "just report the result".
- **Auto-prompt fallback**: when `parameters.prompt` is empty and an
  upstream node is connected to `input-main`, the handler pulls
  `input_data.message` -> `text` -> `content` -> `str(input_data)` in that
  order.
- **Team-lead detection**: `TEAM_LEAD_TYPES = {'orchestrator_agent',
  'ai_employee'}` (in `_inline.py`). Teammates are collected only for these
  two node types and appended to `tool_data` as delegation targets; each
  teammate's own `input-tools` edges are walked to populate `child_tools` so
  the delegation tool description lists what the teammate can do.
- **Master Skill expansion**: `masterSkill` source nodes are expanded in
  `collect_agent_connections` - each enabled entry in `skillsConfig`
  becomes a separate skill entry with `node_id = f"{source_id}_{skill_key}"`.
- **api_key recovery**: `prepare_agent_call` reads the resolved key from
  `context["_raw_parameters"]["api_key"]` (injected before Pydantic strips
  it) and re-attaches it to `parameters` so `execute_chat_agent` sees it.
- **Failure**: `execute_op` raises `RuntimeError(response["error"])` when
  `execute_chat_agent` returns `success=False`.

## Side Effects

- **Database reads**: `database.get_node_parameters(source_id)` for every
  connected skill, memory, tool, and teammate node.
- **Database writes**: `TokenUsageMetric` and potentially `CompactionEvent`
  rows (via `CompactionService` invoked inside `ai_service.execute_chat_agent`).
- **Broadcasts**: `StatusBroadcaster.update_node_status` events for the
  agent's execution phase (`executing`, `executing_tool`, `success`,
  `error`), plus `executing_tool` fired on connected tool nodes when the LLM
  invokes them.
- **External API calls**: one or more calls to the configured LLM provider
  (see `services/ai.py::execute_chat_agent` -> LangChain chat model + `_run_agent_loop`).
- **Subprocess / file I/O**: none directly in the plugin; tools invoked by
  the LLM may trigger those themselves.

## External Dependencies

- **Credentials**: `auth_service.get_api_key(<provider>)` for the
  configured LLM provider.
- **Services**: `AIService.execute_chat_agent`, `CompactionService`,
  `PricingService`, `StatusBroadcaster`.
- **Python packages**: `langchain-core`, plus provider SDKs.

## Edge cases & known limits

- Empty `prompt` with no `input-main` connection still calls
  `execute_chat_agent` -- the LLM may produce a generic response. There is
  no early-return error envelope for missing prompts in this path.
- When a `masterSkill` has zero enabled skills, no skill context is
  injected and the agent runs without domain-specific instructions.
- `input-teammates` is silently ignored for all non-team-lead node types
  (the edge is collected but never expanded).
- Token tracking and compaction happen inside `execute_chat_agent`; if the
  configured provider is unknown, tracking is skipped without an error.

## Related

- **Dedicated-path siblings**: [`rlmAgent`](./rlmAgent.md), [`claudeCodeAgent`](./claudeCodeAgent.md)
- **Per-node variants** (short link-style docs):
  [`androidAgent`](./androidAgent.md), [`codingAgent`](./codingAgent.md),
  [`webAgent`](./webAgent.md), [`taskAgent`](./taskAgent.md),
  [`socialAgent`](./socialAgent.md), [`travelAgent`](./travelAgent.md),
  [`toolAgent`](./toolAgent.md), [`productivityAgent`](./productivityAgent.md),
  [`paymentsAgent`](./paymentsAgent.md), [`consumerAgent`](./consumerAgent.md),
  [`autonomousAgent`](./autonomousAgent.md),
  [`orchestratorAgent`](./orchestratorAgent.md), [`aiEmployee`](./aiEmployee.md)
- **Architecture docs**: [Memory Compaction](../../memory_compaction.md),
  [Pricing Service](../../pricing_service.md)
