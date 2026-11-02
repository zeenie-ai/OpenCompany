# Groq Chat Model (`groqChatModel`)

| Field | Value |
|------|-------|
| **Category** | ai_chat_models |
| **Backend handler** | [`server/nodes/model/groq_chat_model/__init__.py`](../../../server/nodes/model/groq_chat_model/__init__.py) (dispatch via `BaseNode.execute()` -> `@Operation("chat")` in [`server/nodes/model/_base.py`](../../../server/nodes/model/_base.py)) |
| **AI service** | [`server/services/ai.py::AIService.execute_chat`](../../../server/services/ai.py) |
| **Tests** | [`server/tests/nodes/test_ai_chat_models.py`](../../../server/tests/nodes/test_ai_chat_models.py) |
| **Skill (if any)** | n/a |
| **Dual-purpose tool** | no (group `('model',)`) |

## Purpose

Ultra-fast inference via Groq's LPU hardware. Models include Llama 3.x / 4,
Qwen3-32b, GPT-OSS. The `ChatModelBase.chat` operation calls
`AIService.execute_chat`, which routes through `ChatUnifier`; Groq is one of
the nine OpenAI-compatible providers registered in `providers/_compat.py`.
Bare chat and current agent executions share this native provider path.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| (none declared) | - | - | Configure `prompt` directly or resolve it through saved parameter templates; the model connection uses `output-model`. |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `prompt` | string | `""` | yes | - | User message |
| `system_prompt` | string | `""` | no | - | System prompt |
| `model` | string | `""` (injected) | no | - | e.g. `openai/gpt-oss-120b` (default), `llama-3.3-70b-versatile`, `qwen/qwen3.8-27b` |
| `temperature` | number\|null | `null` | no | - | 0-2 |
| `max_tokens` | number\|null | `null` (8-131K per model) | no | - | 1-200000 |
| `top_p` | number\|null | `1.0` | no | - | Declared schema field; not forwarded by the standalone chat path |
| `thinking_enabled` | boolean | `false` | no | - | Only the Qwen3 family supports format-based reasoning; GPT-OSS takes `reasoning_effort` |
| `reasoning_format` | enum | `parsed` | no | `thinking_enabled=[true]` | `parsed` (returns reasoning) or `hidden` (suppresses it) |
| `api_key` | string\|null | `null` (injected) | no | - | `auth_service.get_api_key('groq', 'default')` |

(Field names are snake_case on `GroqChatModelParams`; unknown keys ignored.)

## Standalone chat parameter boundary

See the [shared standalone chat contract](./_pattern.md) for the declared-but-unforwarded tuning fields, numeric thinking_budget requirement, direct/model/advisor execution paths and provider-policy limits. The Params table is not a promise that every field reaches the provider.

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-model` | object | Model output; standard envelope payload |

### Output payload

```ts
{
  response: string;
  thinking: string | null;   // provider-normalized reasoning content, when returned
  thinking_enabled: boolean;
  model: string;
  provider: 'groq';
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
  D -- yes --> E[detect_ai_provider -> 'groq']
  E --> F[Preserve opaque provider model ID]
  F --> G[ChatUnifier.chat provider='groq']
  G --> H[registry.get_provider groq<br/>_compat.py spec: OpenAIProvider + Groq base_url]
  H --> I[await provider.chat -> LLMResponse]
  I --> K[success envelope]
  H -- typed SDK error --> X
```

## Decision Logic

- **Validation**: missing api_key / empty prompt -> error envelope.
- **Provider routing**: matches `'groq' in node_type.lower()`. Note the ordering in `detect_ai_provider`: deepseek/kimi/mistral/cerebras checked first, so none of those accidentally resolve to groq.
- **Native OpenAI-compatible path**: `ChatUnifier` resolves the `groq` spec
  registered in `providers/_compat.py` (reuses `OpenAIProvider` with the
  `base_url` from `llm_defaults.json`) for both chat and agent requests.
- **Reasoning**: the shipped provider policy uses `reasoning_format` for configured Qwen prefixes and `reasoning_effort` for configured GPT-OSS prefixes. Live model availability and acceptance are outside the offline audit.
- **Model ID handling**: only the UI-only `[FREE] ` decoration is stripped. Owner-qualified IDs such as `openai/gpt-oss-120b` and `qwen/qwen3.8-27b` are preserved because Groq requires them.

## Side Effects

- **Database writes**: none on bare chat path.
- **Broadcasts**: none.
- **External API calls**: `POST https://api.groq.com/openai/v1/chat/completions` via the native `openai` SDK with `base_url` override.
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `auth_service.get_api_key('groq', 'default')` plus optional `groq_proxy`.
- **Services**: `ChatUnifier` + `OpenAIProvider` with the Groq `base_url`.
- **Python packages**: `openai`.
- **Environment variables**: none.

## Edge cases & known limits

- **Thinking controls are policy-driven**: reasoning_format is emitted for configured Qwen prefixes; other model families use only the controls their local provider policy declares. Do not infer live model availability from an older model-name example.
- **`reasoning_format=hidden`**: the policy sends this control for eligible format-based models; the returned `thinking` still reflects whatever reasoning content the provider actually emits.
- **Finish reason**: passed through from the API response (`"stop"` fallback when absent).
- **Error boundary**: typed OpenAI SDK failures become user-safe `NodeUserError` values in `ChatUnifier` and are re-raised to `BaseNode.execute()`, which produces the standard failure envelope. Unexpected failures are logged and returned by `execute_chat`.

## Related

- **Peer nodes**: see the other chat-model docs in this folder.
- **Architecture docs**: [Native LLM SDK](../../native_llm_sdk.md).
