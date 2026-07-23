# Cerebras Chat Model (`cerebrasChatModel`)

| Field | Value |
|------|-------|
| **Category** | ai_chat_models |
| **Backend handler** | [`server/nodes/model/cerebras_chat_model/__init__.py`](../../../server/nodes/model/cerebras_chat_model/__init__.py) (dispatch via `BaseNode.execute()` -> `@Operation("chat")` in [`server/nodes/model/_base.py`](../../../server/nodes/model/_base.py)) |
| **AI service** | [`server/services/ai.py::AIService.execute_chat`](../../../server/services/ai.py) |
| **Tests** | [`server/tests/nodes/test_ai_chat_models.py`](../../../server/tests/nodes/test_ai_chat_models.py) |
| **Skill (if any)** | n/a |
| **Dual-purpose tool** | no (group `('model',)`) |

## Purpose

Ultra-fast inference on Cerebras' custom AI hardware. Models include Llama 3.1, GPT-OSS-120b, Qwen-3-235b. The `ChatModelBase.chat` operation calls `AIService.execute_chat`, which routes through `ChatUnifier`. Like Groq, Cerebras is one of the eight OpenAI-compatible providers registered in `providers/_compat.py` (the chat-path LangChain fallback was retired; LangChain `ChatCerebras` remains only on the agent path).

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data; not consumed directly |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `prompt` | string | `""` | yes | - | User message |
| `system_prompt` | string | `""` | no | - | System prompt |
| `model` | string | `""` (injected) | no | - | e.g. `llama3.1-8b`, `gpt-oss-120b`, `qwen-3-235b-a22b` |
| `temperature` | number\|null | `null` | no | - | Narrower range than OpenAI (0-1.5 rather than 0-2) |
| `max_tokens` | number\|null | `null` (up to 8K) | no | - | 1-200000 |
| `top_p` | number\|null | `1.0` | no | - | |
| `thinking_enabled` | boolean | `false` | no | - | Only Qwen-3-235b supports format-based reasoning |
| `thinking_budget` | number\|null | `2048` | no | `thinking_enabled=[true]` | 1024-16000 (Cerebras Qwen budget) |
| `reasoning_format` | enum | `parsed` | no | - | `parsed` / `hidden` - same semantics as Groq Qwen (inherited base field) |
| `api_key` | string\|null | `null` (injected) | no | - | `auth_service.get_api_key('cerebras', 'default')` |

(Field names are snake_case on `CerebrasChatModelParams`; unknown keys ignored.)

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
  provider: 'cerebras';
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
  D -- yes --> E[detect_ai_provider -> 'cerebras']
  E --> F[Strip 'owner/' prefix]
  F --> G[ChatUnifier.chat provider='cerebras']
  G --> H[registry.get_provider cerebras<br/>_compat.py spec: OpenAIProvider + base_url=api.cerebras.ai/v1]
  H --> I[await provider.chat -> LLMResponse]
  I --> J[success envelope]
  H -- typed SDK error --> X
```

## Decision Logic

- **Validation**: missing api_key / empty prompt -> error envelope.
- **Provider routing**: `detect_ai_provider` matches `'cerebras' in node_type.lower()` **before** the groq branch, so routing is unambiguous.
- **Native OpenAI-compatible path**: `ChatUnifier` resolves the `cerebras` spec registered in `providers/_compat.py` (reuses `OpenAIProvider` with the Cerebras base_url). No LangChain on the chat path.
- **Reasoning**: same parsed/hidden mechanism as Groq Qwen - only the Qwen-3-235b variant honors it.
- **Temperature range**: narrower (0-1.5 clamp) than OpenAI/Groq. `_resolve_temperature` applies the clamp.

## Side Effects

- **Database writes**: none on bare chat path.
- **Broadcasts**: none.
- **External API calls**: `POST https://api.cerebras.ai/v1/chat/completions` via the native `openai` SDK with `base_url` override.
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `auth_service.get_api_key('cerebras', 'default')` plus optional `cerebras_proxy`.
- **Services**: `ChatUnifier` + `OpenAIProvider` (base_url-ed) on the chat path; `langchain_cerebras.ChatCerebras` on the agent path.
- **Python packages**: `openai` (chat path), `langchain-cerebras` (agent path, optional on Python >= 3.13).
- **Environment variables**: none.

## Edge cases & known limits

- **Temperature capped at 1.5**, not 2.
- **Reasoning only on Qwen-3-235b**.
- **Small max output**: 8K for most models; exceeding this surfaces as a provider-side error in the envelope.
- **Errors swallowed into envelope**.

## Related

- **Peer nodes**: see the other chat-model docs in this folder.
- **Architecture docs**: [Native LLM SDK](../../native_llm_sdk.md).
