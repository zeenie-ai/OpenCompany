# OpenRouter Chat Model (`openrouterChatModel`)

| Field | Value |
|------|-------|
| **Category** | ai_chat_models |
| **Backend handler** | [`server/nodes/model/openrouter_chat_model/__init__.py`](../../../server/nodes/model/openrouter_chat_model/__init__.py) (dispatch via `BaseNode.execute()` -> `@Operation("chat")` in [`server/nodes/model/_base.py`](../../../server/nodes/model/_base.py)) |
| **AI service** | [`server/services/ai.py::AIService.execute_chat`](../../../server/services/ai.py) |
| **Tests** | [`server/tests/nodes/test_ai_chat_models.py`](../../../server/tests/nodes/test_ai_chat_models.py) |
| **Skill (if any)** | n/a |
| **Dual-purpose tool** | no (group `('model',)`) |

## Purpose

Unified access to 200+ models from multiple providers (OpenAI, Anthropic, Google, Meta, Mistral, etc.) through a single OpenAI-compatible API. The `ChatModelBase.chat` operation calls `AIService.execute_chat`.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data; not consumed directly |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `prompt` | string | `""` | yes (non-empty) | - | User message |
| `system_prompt` | string | `""` | no | - | System prompt |
| `model` | string | `""` (injected) | no | - | `provider/model-id` format, e.g. `anthropic/claude-opus-4.6`, `openai/gpt-4o`. `[FREE] ` prefix stripped before the API call but preserved for dropdown grouping |
| `temperature` | number\|null | `null` | no | - | 0-2 |
| `max_tokens` | number\|null | `null` (varies per model) | no | - | 1-200000 |
| `top_p` | number\|null | `1.0` | no | - | |
| `frequency_penalty` | number\|null | `0.0` | no | - | -2.0 to 2.0; forwarded to downstream provider |
| `presence_penalty` | number\|null | `0.0` | no | - | -2.0 to 2.0; forwarded |
| `thinking_enabled` | boolean | `false` | no | - | Only honored if the routed model supports it |
| `thinking_budget` | number\|null | `2048` | no | - | Inherited base field (no displayOptions); 1024-16000 |
| `reasoning_effort` | enum\|null | `null` | no | - | Inherited base field (no displayOptions); `low`/`medium`/`high` |
| `api_key` | string\|null | `null` (injected) | no | - | `auth_service.get_api_key('openrouter', 'default')` |

(Only `frequency_penalty` / `presence_penalty` are OpenRouter overrides; the thinking/reasoning fields come from the shared base `ChatModelParams` with no displayOptions. Field names are snake_case, unknown keys ignored.)

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-model` | object | Model output; standard envelope payload |

### Output payload

```ts
{
  response: string;
  thinking: string | null;
  thinking_enabled: boolean;
  model: string;
  provider: 'openrouter';
  finish_reason: string;
  timestamp: string;
  input: { prompt: string; system_prompt: string };
}
```

## Logic Flow

```mermaid
flowchart TD
  A[NodeExecutor dispatch -> BaseNode.execute] --> B[ChatModelBase.chat Operation]
  B --> C[AIService.execute_chat]
  C --> D[Strip '[FREE] ' prefix from model]
  D --> E{valid key + prompt?}
  E -- no --> X[error envelope]
  E -- yes --> F[detect_ai_provider -> 'openrouter']
  F --> G[DO NOT strip 'owner/' prefix<br/>provider == openrouter]
  G --> H[Native path: create_provider openrouter<br/>OpenAI SDK w/ base_url=openrouter.ai/api/v1]
  H --> I[provider.chat -> response]
  I --> J[success envelope]
  H -- Exception --> X
```

## Decision Logic

- **Validation**: missing api_key / empty prompt -> error envelope.
- **Provider routing**: matches `'openrouter' in node_type.lower()` in `detect_ai_provider` BEFORE the `anthropic`/`gemini` branches, so model IDs like `anthropic/claude-3.5-sonnet` stay in the OpenRouter lane.
- **Model string rule (important)**: for OpenRouter, the `owner/model` slash-prefix is **kept** (the API expects it). For every other provider the prefix is stripped. See `execute_chat` line: `if provider != 'openrouter' and '/' in model: model = model.split('/', 1)[-1]`.
- **[FREE] prefix**: stripped unconditionally before the API call; exists only for the frontend dropdown grouping.
- **Native provider**: OpenAI SDK reused with `base_url` set to the OpenRouter gateway; `OpenRouterProvider` inherits from `OpenAIProvider`.

## Side Effects

- **Database writes**: none on bare chat path.
- **Broadcasts**: none.
- **External API calls**: `POST https://openrouter.ai/api/v1/chat/completions` via `openai` SDK with overridden `base_url`. Requires `HTTP-Referer` and `X-Title` headers (set by `OpenRouterProvider`).
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `auth_service.get_api_key('openrouter', 'default')` plus optional `openrouter_proxy`.
- **Services**: `services/llm/providers/openrouter.py`.
- **Python packages**: `openai`.
- **Environment variables**: none.

## Edge cases & known limits

- **200+ models, varying capabilities**: thinking support, context windows, temperature ranges, and pricing all vary per routed model. The handler applies generic clamps; mismatches surface as envelope errors from the downstream provider (e.g. "This model does not support the reasoning parameter").
- **`[FREE] ` models are OpenRouter-free but may still cost latency**: routing can queue against capacity.
- **`owner/model` prefix is load-bearing**: removing it breaks routing. Unique among the 11 chat-model nodes.
- **Errors swallowed into envelope**.

## Related

- **Peer nodes**: see the other chat-model docs in this folder.
- **Architecture docs**: [Native LLM SDK](../../native_llm_sdk.md).
