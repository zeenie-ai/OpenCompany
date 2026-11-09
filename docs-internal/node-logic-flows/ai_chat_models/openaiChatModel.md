# OpenAI Chat Model (`openaiChatModel`)

| Field | Value |
|------|-------|
| **Category** | ai_chat_models |
| **Backend handler** | [`server/nodes/model/openai_chat_model/__init__.py`](../../../server/nodes/model/openai_chat_model/__init__.py) (dispatch via `BaseNode.execute()` -> `@Operation("chat")` in [`server/nodes/model/_base.py`](../../../server/nodes/model/_base.py)) |
| **AI service** | [`server/services/ai.py::AIService.execute_chat`](../../../server/services/ai.py) |
| **Tests** | [`server/tests/nodes/test_ai_chat_models.py`](../../../server/tests/nodes/test_ai_chat_models.py) |
| **Skill (if any)** | n/a |
| **Dual-purpose tool** | yes - tool name `openai_chat_model` (advisor; `usable_as_tool = True`, group `('model', 'tool')`) |

## Purpose

Single-turn chat completion against the OpenAI API. Used either as a standalone workflow node (user enters a prompt, the node produces a response), wired into an AI Agent via the `input-model` handle, or connected as an advisor tool (`usable_as_tool = True`). Every chat-model node runs through the shared `ChatModelBase.chat` operation (in `_base.py`), which calls provider-neutral `AIService.execute_chat`; OpenAI-specific request policy lives in the native provider and shared model-policy helpers.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| (none declared) | - | - | Configure `prompt` directly or resolve it through saved parameter templates; the model connection uses `output-model`. |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `prompt` | string | `""` | yes (non-empty) | - | User message content sent to the model |
| `system_prompt` | string | `""` | no | - | System prompt |
| `model` | string | `""` (injected from stored models) | no | - | Model ID. `[FREE] ` prefix is stripped before the API call |
| `temperature` | number\|null | `null` -> `agent.default_temperature` | no | - | Schema 0-2; configured reasoning-model policy can resolve a fixed value, and the native provider omits unsupported sampling fields |
| `max_tokens` | number\|null | `null` -> per-model default | no | - | 1-200000; clamped via `_resolve_max_tokens` to the model's actual ceiling |
| `top_p` | number\|null | `1.0` | no | - | Declared schema field; not forwarded by the standalone chat path |
| `frequency_penalty` | number\|null | `0.0` | no | - | Declared schema field; not forwarded by the standalone chat path |
| `presence_penalty` | number\|null | `0.0` | no | - | Declared schema field; not forwarded by the standalone chat path |
| `response_format` | enum | `text` | no | - | Declared schema field; not forwarded by the standalone chat path |
| `thinking_enabled` | boolean | `false` | no | - | Turn on extended reasoning (GPT-5 hybrid / o-series) |
| `reasoning_effort` | enum | `medium` | no | `thinking_enabled=[true]` | `minimal` / `low` / `medium` / `high` (OpenAI override adds `minimal`) |
| `api_key` | string\|null | `null` (injected) | no | - | `auth_service.get_api_key('openai', 'default')` |

(Field names are snake_case on `OpenAIChatModelParams`; `model_config = ConfigDict(extra="ignore")` drops unknown keys.)

## Standalone chat parameter boundary

See the [shared standalone chat contract](./_pattern.md) for the declared-but-unforwarded tuning fields, numeric thinking_budget requirement, direct/model/advisor execution paths and provider-policy limits. The Params table is not a promise that every field reaches the provider.

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-model` | object | Model output (also feeds an agent's `input-model` handle); standard envelope payload (see below) |

### Output payload

```ts
{
  response: string;
  thinking: string | null;
  thinking_enabled: boolean;
  model: string;
  provider: 'openai';
  finish_reason: string;
  timestamp: string;  // ISO 8601
  input: { prompt: string; system_prompt: string };
}
```

Wrapped in the standard envelope: `{ success: true, node_id, node_type, result: <payload>, execution_time: number }`.

## Logic Flow

```mermaid
flowchart TD
  A[NodeExecutor dispatch -> BaseNode.execute] --> B[ChatModelBase.chat Operation<br/>params validated by Pydantic]
  B --> C[get_ai_service]
  C --> D[AIService.execute_chat]
  D --> E[Flatten options into flat params dict]
  E --> F{api_key present?}
  F -- no --> X[raise ValueError -> error envelope]
  F -- yes --> G{prompt non-empty?}
  G -- no --> X
  G -- yes --> H[detect_ai_provider -> 'openai']
  H --> I[Build NativeThinkingConfig if thinking_enabled]
  I --> J[Lookup openai_proxy credential]
  J --> K[ChatUnifier.chat provider='openai']
  K --> L[registry.get_provider openai + OpenAI SDK<br/>await provider.chat]
  L --> M[Build response dict<br/>response / thinking / model / provider / finish_reason]
  M --> N[Return success envelope]
  L -- Exception --> X
```

## Decision Logic

- **Validation**: missing `api_key` -> ValueError; empty/whitespace `prompt` (via `is_valid_message_content`) -> ValueError. Both produce a `success=false` envelope.
- **Provider routing**: `detect_ai_provider(node_type)` returns `'openai'` for this node; falls through the else branch in `constants.detect_ai_provider`.
- **Native path**: `ChatUnifier` resolves the self-registered `openai` spec, so the native `openai` SDK serves both current chat and agent executions. Chat and agent turns use the same native message and tool contracts.
- **Model ID handling**: strips only the UI-only `[FREE] ` decoration. Provider model IDs are otherwise opaque and preserved.
- **Thinking config**: only built when `thinking_enabled` is truthy; passed through to the provider's `chat()` method.
- **System prompt**: reads the schema-canonical `system_prompt` field and prepends a native `Message(role="system")` when it is non-empty.
- **Error path**: typed SDK failures are translated by `ChatUnifier` into `NodeUserError` and re-raised to `BaseNode.execute()`; unexpected exceptions are logged and returned as `success=false`.

## Side Effects

- **Database writes**: none from `execute_chat` itself; agent token/context tracking is a separate caller path.
- **Broadcasts**: none from `execute_chat` itself.
- **External API calls**: the standalone chat node uses OpenAI Chat Completions through the native SDK. Native agent turns that need reasoning continuation state use the Responses API; ordinary agent turns use Chat Completions. An `openai_proxy` credential can override the SDK endpoint.
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `auth_service.get_api_key('openai', 'default')` (API key); optional `auth_service.get_api_key('openai_proxy')` for proxy URL override.
- **Services**: `AIService` (wired via `NodeExecutor.__init__`), `services/llm/providers/openai.py` (native provider).
- **Python packages**: `openai` (native SDK).
- **Environment variables**: none (keys are DB-stored).

## Edge cases & known limits

- **Reasoning-model temperature**: the native provider omits `temperature` for configured OpenAI reasoning models instead of forwarding an unsupported value.
- **GPT-5 hybrid reasoning**: `reasoning_effort` (`minimal` / `low` / `medium` / `high`) is consumed via `NativeThinkingConfig.effort` and forwarded to the provider SDK. Without `thinking_enabled=true` the effort value is ignored.
- **Reasoning summary availability**: the node returns the provider's normalized reasoning content when present; enabling thinking does not guarantee a non-null summary. Account eligibility and live provider policy were not verified by this offline audit.
- **`max_tokens` clamp**: clamped to the model's actual output ceiling by `native_resolve_max_tokens`. Exceeding the limit silently caps without error.
- **Error boundary**: typed OpenAI SDK failures become user-safe `NodeUserError` values in `ChatUnifier` and are re-raised to `BaseNode.execute()`, which produces the standard failure envelope. Unexpected failures are logged and returned by `execute_chat`.
- **No streaming**: `execute_chat` is single-shot; the full response is awaited before returning.
- **Pricing / token tracking**: standalone chat model nodes do NOT trigger `_track_token_usage` - that lives on the agent code paths. Cost attribution for bare chat-model executions is therefore not recorded.

## Related

- **Peer nodes** (same handler): [`anthropicChatModel`](./anthropicChatModel.md), [`geminiChatModel`](./geminiChatModel.md), [`openrouterChatModel`](./openrouterChatModel.md), [`groqChatModel`](./groqChatModel.md), [`cerebrasChatModel`](./cerebrasChatModel.md), [`deepseekChatModel`](./deepseekChatModel.md), [`kimiChatModel`](./kimiChatModel.md), [`mistralChatModel`](./mistralChatModel.md).
- **Architecture docs**: [Native LLM SDK](../../native_llm_sdk.md), [Credentials Encryption](../../credentials_encryption.md), [Pricing Service](../../pricing_service.md).
