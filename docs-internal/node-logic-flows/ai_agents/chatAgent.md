# Zeenie (`chatAgent`)

| Field | Value |
|------|-------|
| **Category** | ai_agents / agent |
| **Backend handler** | [`server/services/handlers/ai.py::handle_chat_agent`](../../../server/services/handlers/ai.py) |
| **Tests** | [`server/tests/nodes/test_ai_agents.py`](../../../server/tests/nodes/test_ai_agents.py) |
| **Skill (if any)** | n/a (consumes skills via `input-skill`) |
| **Dual-purpose tool** | no |

## Purpose

`chatAgent` (display name **Zeenie**) is the conversational variant of
`aiAgent`. Same connection model, same `_collect_agent_connections` helper,
same tool-calling stack - it differs from `aiAgent` in the frontend icon /
system prompt and in the service method it calls
(`AIService.execute_chat_agent` instead of `AIService.execute_agent`). It is
also the handler used by every **specialized agent** node
(`android_agent`, `coding_agent`, `web_agent`, `task_agent`, `social_agent`,
`travel_agent`, `tool_agent`, `productivity_agent`, `payments_agent`,
`consumer_agent`, `autonomous_agent`, `orchestrator_agent`, `ai_employee`) -
the node registry wires all of those to `handle_chat_agent` via
`functools.partial`.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data. Auto-prompt fallback when `prompt` is empty. |
| `input-skill` | main | no | Skill nodes (including `masterSkill` aggregation). |
| `input-memory` | main | no | `simpleMemory` node for conversation history. |
| `input-tools` | main | no | Tool nodes bound to the LLM via `chat_model.bind_tools`. |
| `input-task` | main | no | `taskTrigger` output - formatted and prepended to the prompt. |
| `input-teammates` | main | no | **Team-lead only** (`orchestrator_agent`, `ai_employee`). Agents on this handle become `delegate_to_<name>` tools. |

## Parameters

Identical to [`aiAgent`](./aiAgent.md) (`provider`, `model`, `prompt`,
`systemMessage`, `options.*`). The only frontend differences:

- `prompt` default is `""` (empty) rather than `{{ $json.chatInput }}` - so
  Zeenie relies on the auto-prompt fallback when wired to a chat trigger.
- `systemMessage` default is `"You are a helpful assistant."` (Zeenie persona
  is layered on top by connected skills).

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | `{ response, thinking?, model, provider, timestamp, ... }` envelope from `execute_chat_agent`. |

## Logic Flow

```mermaid
flowchart TD
  A[handle_chat_agent] --> B[_collect_agent_connections]
  B --> C{task_data?}
  C -- yes --> D[Prepend task_context to prompt]
  D --> E{status completed/error<br/>AND tool_data?}
  E -- yes --> F[Strip ALL tools]
  E -- no --> G
  F --> G
  C -- no --> G{prompt empty AND<br/>input_data present?}
  G -- yes --> H[prompt := input_data.message<br/>or .text or .content or str]
  G -- no --> I
  H --> I{node_type in<br/>TEAM_LEAD_TYPES?}
  I -- yes --> J[_collect_teammate_connections<br/>-> append each teammate to tool_data]
  I -- no --> K
  J --> K[await ai_service.execute_chat_agent<br/>memory/skill/tool/broadcaster/context/db]
  K --> L[Return envelope]
```

## Decision Logic

- **Connection collection** is delegated to `_collect_agent_connections`; same
  rules as `aiAgent` (see that doc for memory session derivation,
  `masterSkill` expansion, Android toolkit, child-agent tool discovery).
- **Task context injection** mirrors `aiAgent`: `_format_task_context` wraps
  the task result as a plain-English instruction that the LLM must "report
  naturally", then all tools are stripped if the task has already completed
  or errored.
- **Auto-prompt fallback** is identical to `aiAgent` - `message` wins over
  `text` which wins over `content`, falling back to `str(input_data)`.
- **Team mode** (`orchestrator_agent`, `ai_employee`): after collection, the
  handler calls `_collect_teammate_connections(node_id, context, database)`
  to find nodes wired to `input-teammates`. Each teammate whose `node_type`
  is in `AI_AGENT_TYPES` is appended to `tool_data` as a synthetic tool
  entry. The AIService then exposes them to the LLM as `delegate_to_<type>`
  tools (see [Agent Teams](../../agent_teams.md)).

## Side Effects

- **Database writes**: none directly. `execute_chat_agent` writes
  `token_usage_metrics` and updates the connected `simpleMemory` node's
  `memoryContent` via `database.save_node_parameters`.
- **Broadcasts**: `StatusBroadcaster` is fetched and passed down, enabling
  `update_node_status` events (`thinking`, `executing_tool`, `success`), plus
  `token_usage_update` from the compaction tracker.
- **External API calls**: provider LLM SDKs, plus whatever tools the LLM
  decides to call.
- **File I/O / subprocess**: only via tool nodes (filesystem, shell, process
  manager, browser, code executors).

## External Dependencies

- **Credentials**: provider API keys via `auth_service.get_api_key`.
- **Services**: `AIService`, `Database`, `StatusBroadcaster`,
  `CompactionService`, `PricingService`, `SkillLoader`.
- **Python packages**: same as `aiAgent`.
- **Environment variables**: none read directly.

## Edge cases & known limits

- **Specialized agents inherit every quirk**: any bug in `handle_chat_agent`
  (e.g. the blanket tool-strip on task completion) affects 13 node types.
- **Team-lead teammates are appended after existing tools**: if a parent
  wires both regular tool nodes and teammates, tool order depends on edge
  scan order (not stable across clients).
- **Teammate tool entries carry `parameters`**: the teammate's saved node
  parameters are included so `AIService` can resolve provider/model for
  delegation. An unconfigured teammate (no `model`) will surface the failure
  at delegate-time, not at collection-time.
- **Input fallback field order is identical** to `aiAgent`: `message` >
  `text` > `content` > `str(dict)`.
- **Empty `teammates` on a team-lead is benign** - the handler simply runs
  without delegation tools, so an `orchestrator_agent` with nothing wired to
  `input-teammates` behaves exactly like `chatAgent`.
- **`rlm_agent` and `claude_code_agent` do NOT route here** - they have dedicated
  handlers. All other specialized agents do.

## Related

- **Twin node**: [`aiAgent`](./aiAgent.md) (same collection helper,
  different service method).
- **Memory node**: [`simpleMemory`](./simpleMemory.md)
- **Architecture docs**: [Agent Architecture](../../agent_architecture.md),
  [Agent Delegation](../../agent_delegation.md),
  [Agent Teams](../../agent_teams.md),
  [Memory Compaction](../../memory_compaction.md)
