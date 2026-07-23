# DeepSeek Chat Model (`deepseekChatModel`)

| Field | Value |
|------|-------|
| **Category** | ai_chat_models |
| **Backend handler** | [`server/nodes/model/deepseek_chat_model/__init__.py`](../../../server/nodes/model/deepseek_chat_model/__init__.py) (dispatch via `BaseNode.execute()` -> `@Operation("chat")` in [`server/nodes/model/_base.py`](../../../server/nodes/model/_base.py)) |
| **AI service** | [`server/services/ai.py::AIService.execute_chat`](../../../server/services/ai.py) |
| **Tests** | [`server/tests/nodes/test_ai_chat_models.py`](../../../server/tests/nodes/test_ai_chat_models.py) |
| **Skill (if any)** | n/a |
| **Dual-purpose tool** | no (group `('model',)`) |

## Purpose

DeepSeek V4 models (`deepseek-v4-flash`, `deepseek-v4-pro`); `deepseek-chat` / `deepseek-reasoner` remain as legacy aliases (deprecate 2026-07-24). Uses the OpenAI-compatible DeepSeek endpoint via the `services/llm/providers` layer (native path). The `ChatModelBase.chat` operation calls `AIService.execute_chat`. The plugin docstring describes `deepseek-chat`/`deepseek-reasoner` (V3) but the registry description in `__init__.py` predates the V4 rename - the card's V4 listing reflects current `llm_defaults.json`.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data; not consumed directly |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `prompt` | string | `""` | yes | - | User message |
| `system_prompt` | string | `""` | no | - | System prompt |
| `model` | string | `""` (injected) | no | - | `deepseek-v4-flash` / `deepseek-v4-pro`; `deepseek-chat` / `deepseek-reasoner` legacy aliases (reasoner = always-on CoT) |
| `temperature` | number\|null | `null` | no | - | 0-2 |
| `max_tokens` | number\|null | `null` (8-64K) | no | - | 1-200000 |
| `top_p` | number\|null | `1.0` | no | - | |
| `frequency_penalty` | number\|null | `0.0` | no | - | -2.0 to 2.0 (DeepSeek-specific) |
| `presence_penalty` | number\|null | `0.0` | no | - | -2.0 to 2.0 (DeepSeek-specific) |
| `api_key` | string\|null | `null` (injected) | no | - | `auth_service.get_api_key('deepseek', 'default')` |

(Field names are snake_case on `DeepseekChatModelParams`; unknown keys ignored.)

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-model` | object | Model output; standard envelope payload |

### Output payload

```ts
{
  response: string;
  thinking: string | null;   // reasoning_content from deepseek-reasoner; null for deepseek-chat
  thinking_enabled: boolean;
  model: string;
  provider: 'deepseek';
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
  C --> D{valid key + prompt?}
  D -- no --> X[error envelope]
  D -- yes --> E[detect_ai_provider -> 'deepseek']
  E --> F[Strip 'owner/' prefix]
  F --> G[ChatUnifier.chat -> registry.get_provider deepseek<br/>OpenAI SDK w/ DeepSeek base_url]
  G --> H[provider.chat]
  H --> I[success envelope]
  G -- Exception --> X
```

## Decision Logic

- **Validation**: missing api_key / empty prompt -> error envelope.
- **Provider routing**: `detect_ai_provider` matches `'deepseek' in node_type.lower()` **first** (before kimi/mistral/cerebras/groq/openrouter/anthropic/gemini), so routing is unambiguous.
- **Native path**: uses the OpenAI SDK with DeepSeek's base URL from `llm_defaults.json`. OpenAI-compatible `max_tokens` is passed via `extra_body` to bypass LangChain's `max_completion_tokens` translation (not relevant on the native path but documented for the LangChain-agent path).
- **`deepseek-reasoner` always-on CoT**: reasoning_content is ALWAYS produced, regardless of `thinkingEnabled`. The native provider extracts it into `LLMResponse.thinking`.

## Side Effects

- **Database writes**: none on bare chat path.
- **Broadcasts**: none.
- **External API calls**: `POST https://api.deepseek.com/v1/chat/completions` (via OpenAI SDK with overridden base URL).
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `auth_service.get_api_key('deepseek', 'default')` plus optional `deepseek_proxy`.
- **Services**: `services/llm/providers/openai.py` (reused w/ DeepSeek base_url).
- **Python packages**: `openai`.
- **Environment variables**: none.

## Edge cases & known limits

- **`deepseek-reasoner` always thinks**: `thinkingEnabled=false` does NOT disable the reasoning trace; it just means the UI won't highlight it. The response still contains `reasoning_content`.
- **`thinkingBudget` has no effect**: DeepSeek reasoning is not budget-configurable; the field is silently ignored.
- **128K context, up to 64K output**.
- **OpenAI-compatible but not OpenAI**: features like `response_format: json_object` have subtly different behavior.
- **Errors swallowed into envelope**.

## Related

- **Peer nodes**: see the other chat-model docs in this folder.
- **Architecture docs**: [Native LLM SDK](../../native_llm_sdk.md).
