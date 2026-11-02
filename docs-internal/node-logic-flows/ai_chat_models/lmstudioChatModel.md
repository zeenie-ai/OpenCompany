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

Run local LLMs through LM Studio's OpenAI-compatible server (default `http://localhost:1234/v1`). The OpenAI-compatible spec registered in `services/llm/providers/_compat.py` routes it through `OpenAIProvider` — same path as deepseek/kimi/mistral/ollama. The server URL the user saves under Credentials is rooted at save time (`http://localhost:1234` is stored as `http://localhost:1234/v1`, RFC-0003) and kept as the `lmstudio_proxy` credential; the unifier passes it as `proxy_url`, which wins over the `base_url` in `llm_defaults.json`. `LMStudioChatModelNode` uses the shared `ChatModelParams` unchanged. The `ChatModelBase.chat` operation calls `AIService.execute_chat`.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| (none declared) | - | - | Configure `prompt` directly or resolve it through saved parameter templates; the model connection uses `output-model`. |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `prompt` | string | `""` | yes | - | User message |
| `system_prompt` | string | `""` | no | - | System prompt |
| `model` | string | `""` (injected) | no | - | Whatever the user has loaded in the LM Studio UI. Open-world: name not pattern-checked by `is_model_valid_for_provider` |
| `temperature` | number\|null | `null` | no | - | 0-2 |
| `max_tokens` | number\|null | `null` | no | - | 1-200000; default per-loaded-model ctx ÷ 4 (capped 4096) |
| `top_p` | number\|null | `1.0` | no | - | Declared schema field; not forwarded by the standalone chat path |
| `api_key` | string\|null | `null` (injected) | no | - | Optional; local servers usually run with no auth. Without a stored key the declared placeholder is sent (`auth.placeholder_key` in `llm_defaults.json`: `"lm-studio"`, the value LM Studio's docs use) |

(LM Studio uses the shared `ChatModelParams` unchanged; field names are snake_case, unknown keys ignored.)

## Standalone chat parameter boundary

See the [shared standalone chat contract](./_pattern.md) for the declared-but-unforwarded tuning fields, numeric thinking_budget requirement, direct/model/advisor execution paths and provider-policy limits. The Params table is not a promise that every field reaches the provider.

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
  F --> G[ChatUnifier.chat -> registry.get_provider lmstudio -> OpenAIProvider<br/>base_url=resolved URL, key=stored key or placeholder]
  G --> H[provider.chat]
  H --> I[success envelope]
  G -- Exception --> X
```

## Decision Logic

- **Validation**: empty prompt -> error envelope. Once the server is saved, `api_key` is never the blocker: saving stores the user's key or the declared placeholder (`"lm-studio"`) in the `lmstudio` row, so the central "API key required" check in `execute_chat` passes. `LMStudioCredential.resolve()` falls back to the same placeholder.
- **Provider routing**: `detect_ai_provider` MUST list `lmstudio` (in `server/constants.py`) or the node falls through to `'openai'` and `execute_chat` hits api.openai.com with the placeholder key.
- **Open-world model name**: `lmstudio` declares `open_world_models: true` in `llm_defaults.json`, so `is_model_valid_for_provider` does not reject local model names with the cloud-style pattern check.
- **Base URL routing**: the `lmstudio_proxy` credential carries the resolved server URL into the client; nothing is probed at call time, and traffic stays on `localhost`.

## Side Effects

- **Database writes**: per-model context params persist in `EncryptedAPIKey.models["model_params"]` and in `DATA_DIR/local_models.json` at save time (via `_local_validator.save_llm_server` + `ModelRegistryService.register_local_model()`), never in the tracked `config/model_registry.json`, and not on the bare chat path.
- **Broadcasts**: none on the bare chat path.
- **External API calls**: `POST {user_server}/v1/chat/completions` via the `openai` SDK with overridden `base_url` (default `http://localhost:1234/v1`).
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: optional `auth_service.get_api_key('lmstudio')`; user server URL stored as `lmstudio_proxy`.
- **Services**: `services/llm/providers/openai.py` (reused with LM Studio base_url); `services/llm/endpoints.py` (roots the URL at save time); `nodes/model/_local_validator.py` (the save path; SDK probe via `lmstudio.AsyncClient.llm.list_loaded()`).
- **Python packages**: `openai`, `lmstudio>=1.5.0` (validation only).
- **Environment variables**: none.

## Edge cases & known limits

- **Server must be running with a model loaded**: LM Studio reports loaded models via `list_loaded()`; if nothing is loaded the probe returns no models and the save writes nothing.
- **Saving checks the URL first**: LM Studio answers HTTP 200 even to routes it does not serve, so a URL saved without `/v1` used to validate and then fail every run with an empty response (RFC-0003 §2.1). The URL entered is now tried as given, then with `/v1` appended, and adopted only where `GET /models` returns an OpenAI list; a failed save writes nothing and keeps the previous server.
- **API token**: LM Studio can require an API token, but its Credentials panel has only the Base URL field, so a token-protected server cannot be used yet.
- **Provider routing dependency**: `lmstudio` must be present in `detect_ai_provider`, or the node silently falls back to OpenAI cloud. Agents need no edit: their `provider` field lists every registered provider through the `aiProviders` loader.
- **Max output default**: ctx ÷ 4, capped at 4096, unless the user overrides `max_tokens`. Typed `LlmInstanceInfo.context_length` drives this.
- **Error boundary**: typed OpenAI SDK connection/API failures become user-safe `NodeUserError` values in `ChatUnifier` and are re-raised to `BaseNode.execute()`, which produces the standard failure envelope. Unexpected failures are logged and returned by `execute_chat`.

## Related

- **Peer nodes**: [`ollamaChatModel`](./ollamaChatModel.md) (other local-server provider), and the cloud chat-model docs in this folder.
- **Architecture docs**: [Native LLM SDK](../../native_llm_sdk.md).
