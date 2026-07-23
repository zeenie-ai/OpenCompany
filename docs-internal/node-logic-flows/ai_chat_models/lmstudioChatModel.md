# LM Studio Chat Model (`lmstudioChatModel`)

| Field | Value |
|------|-------|
| **Category** | ai_chat_models |
| **Backend handler** | [`server/nodes/model/lmstudio_chat_model/__init__.py`](../../../server/nodes/model/lmstudio_chat_model/__init__.py) (dispatch via `BaseNode.execute()` -> `@Operation("chat")` in [`server/nodes/model/_base.py`](../../../server/nodes/model/_base.py)) |
| **AI service** | [`server/services/ai.py::AIService.execute_chat`](../../../server/services/ai.py) |
| **Tests** | [`server/tests/nodes/test_ai_chat_models.py`](../../../server/tests/nodes/test_ai_chat_models.py) |
| **Skill (if any)** | n/a |
| **Dual-purpose tool** | no (group `('model',)`) |

## Purpose

Run local LLMs through LM Studio's OpenAI-compatible server (default `http://localhost:1234/v1`). The OpenAI-compatible spec registered in `services/llm/providers/_compat.py` routes it through `OpenAIProvider` with `base_url` from `llm_defaults.json` — same path as deepseek/kimi/mistral/ollama. The user's custom server URL is stored as the `lmstudio_proxy` credential. `LMStudioChatModelNode` uses the shared `ChatModelParams` unchanged. The `ChatModelBase.chat` operation calls `AIService.execute_chat`.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data; not consumed directly |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `prompt` | string | `""` | yes | - | User message |
| `system_prompt` | string | `""` | no | - | System prompt |
| `model` | string | `""` (injected) | no | - | Whatever the user has loaded in the LM Studio UI. Open-world: name not pattern-checked by `is_model_valid_for_provider` |
| `temperature` | number\|null | `null` | no | - | 0-2 |
| `max_tokens` | number\|null | `null` | no | - | 1-200000; default per-loaded-model ctx ÷ 4 (capped 4096) |
| `top_p` | number\|null | `1.0` | no | - | |
| `api_key` | string\|null | `null` -> placeholder `"ollama"` | no | - | Optional; local servers usually run with no auth. `LMStudioCredential.resolve()` returns `"ollama"` when none stored |

(LM Studio uses the shared `ChatModelParams` unchanged; field names are snake_case, unknown keys ignored.)

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-model` | object | Model output (also feeds an agent's `input-model` handle); standard envelope payload |

### Output payload

```ts
{
  response: string;
  thinking: string | null;   // per-model
  thinking_enabled: boolean;
  model: string;
  provider: 'lmstudio';
  finish_reason: string;
  timestamp: string;
  input: { prompt: string; system_prompt: string };
}
```

Wrapped in `{ success, node_id, node_type, result, execution_time }`.

## Logic Flow

```mermaid
flowchart TD
  A[NodeExecutor dispatch -> BaseNode.execute] --> B[ChatModelBase.chat Operation]
  B --> C[AIService.execute_chat]
  C --> D{valid key + prompt?}
  D -- no --> X[error envelope]
  D -- yes --> E[detect_ai_provider -> 'lmstudio']
  E --> F[Lookup lmstudio_proxy credential -> base_url override]
  F --> G[ChatUnifier.chat -> registry.get_provider lmstudio -> OpenAIProvider<br/>base_url=user server, api_key='ollama']
  G --> H[provider.chat]
  H --> I[success envelope]
  G -- Exception --> X
```

## Decision Logic

- **Validation**: empty prompt -> error envelope. `api_key` is never the blocker — `LMStudioCredential.resolve()` returns the placeholder `"ollama"` when no key is stored, so the central "API key required" check in `execute_chat` passes.
- **Provider routing**: `detect_ai_provider` MUST list `lmstudio` (in `server/constants.py`) or the node falls through to `'openai'` and `execute_chat` hits api.openai.com with the placeholder key.
- **Open-world model name**: `is_model_valid_for_provider` returns `True` for `lmstudio` so local model names are not rejected by the cloud-style pattern check.
- **Base URL routing**: `lmstudio_proxy` credential carries the user's server URL into `OpenAIProvider.base_url`; traffic stays on `localhost`.

## Side Effects

- **Database writes**: per-model context params persist in `EncryptedAPIKey.models["model_params"]` and `model_registry.json` at credential-validation time (via `_local_validator.py` + `model_registry.register_local_model()`), not on the bare chat path.
- **Broadcasts**: none on the bare chat path.
- **External API calls**: `POST {user_server}/v1/chat/completions` via the `openai` SDK with overridden `base_url` (default `http://localhost:1234/v1`).
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: optional `auth_service.get_api_key('lmstudio')`; user server URL stored as `lmstudio_proxy`.
- **Services**: `services/llm/providers/openai.py` (reused with LM Studio base_url); `nodes/model/_local_validator.py` (SDK probe via `lmstudio.AsyncClient.llm.list_loaded()`).
- **Python packages**: `openai`, `lmstudio>=1.5.0` (validation only).
- **Environment variables**: none.

## Edge cases & known limits

- **Server must be running with a model loaded**: LM Studio reports loaded models via `list_loaded()`; if nothing is loaded the probe returns no models.
- **Provider routing dependency**: `lmstudio` must be present in `detect_ai_provider` AND in each agent's `provider` Literal, or the node silently falls back to OpenAI cloud.
- **Max output default**: ctx ÷ 4, capped at 4096, unless the user overrides `max_tokens`. Typed `LlmInstanceInfo.context_length` drives this.
- **Errors swallowed into envelope** on the chat path.

## Related

- **Peer nodes**: [`ollamaChatModel`](./ollamaChatModel.md) (other local-server provider), and the cloud chat-model docs in this folder.
- **Architecture docs**: [Native LLM SDK](../../native_llm_sdk.md).
