# Ollama Chat Model (`ollamaChatModel`)

| Field | Value |
|------|-------|
| **Category** | ai_chat_models |
| **Backend handler** | [`server/nodes/model/ollama_chat_model/__init__.py`](../../../server/nodes/model/ollama_chat_model/__init__.py) (dispatch via `BaseNode.execute()` -> `@Operation("chat")` in [`server/nodes/model/_base.py`](../../../server/nodes/model/_base.py)) |
| **AI service** | [`server/services/ai.py::AIService.execute_chat`](../../../server/services/ai.py) |
| **Tests** | [`server/tests/nodes/test_ai_chat_models.py`](../../../server/tests/nodes/test_ai_chat_models.py) |
| **Skill (if any)** | n/a |
| **Dual-purpose tool** | no (group `('model',)`) |

## Purpose

Run local LLMs (llama, mistral, qwen, deepseek-r1, ...) through a locally-running Ollama server. Ollama exposes an OpenAI-shaped `/v1` endpoint, so the OpenAI-compatible spec registered in `services/llm/providers/_compat.py` hands it to `OpenAIProvider` — same path as deepseek/kimi/mistral. The server URL the user saves under Credentials is rooted at save time (`http://localhost:11434` is stored as `http://localhost:11434/v1`, RFC-0003) and kept as the `ollama_proxy` credential; the unifier passes it as `proxy_url`, which wins over the `base_url` in `llm_defaults.json`. `OllamaChatModelNode` uses the shared `ChatModelParams` unchanged. The `ChatModelBase.chat` operation calls `AIService.execute_chat`.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data; not consumed directly |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `prompt` | string | `""` | yes | - | User message |
| `system_prompt` | string | `""` | no | - | System prompt |
| `model` | string | `""` (injected) | no | - | A model Ollama had loaded when the server was last fetched (`ps()` lists loaded models only), e.g. `qwen2.5`, `llama3.x`, `deepseek-r1`. Open-world: name not pattern-checked by `is_model_valid_for_provider` |
| `temperature` | number\|null | `null` | no | - | 0-2 |
| `max_tokens` | number\|null | `null` | no | - | 1-200000; default per-loaded-model ctx ÷ 4 (capped 4096) |
| `top_p` | number\|null | `1.0` | no | - | |
| `api_key` | string\|null | `null` (injected) | no | - | Optional; local servers usually run with no auth. Without a stored key the declared placeholder is sent (`auth.placeholder_key` in `llm_defaults.json`: `"ollama"`, the value Ollama's docs use) |

(Ollama uses the shared `ChatModelParams` unchanged; field names are snake_case, unknown keys ignored.)

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-model` | object | Model output (also feeds an agent's `input-model` handle); standard envelope payload |

### Output payload

```ts
{
  response: string;
  thinking: string | null;   // per-model; Ollama has no generic thinking knob
  thinking_enabled: boolean;
  model: string;
  provider: 'ollama';
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
  D -- yes --> E[detect_ai_provider -> 'ollama']
  E --> F[Lookup ollama_proxy credential -> base_url override]
  F --> G[ChatUnifier.chat -> registry.get_provider ollama -> OpenAIProvider<br/>base_url=resolved URL, key=stored key or placeholder]
  G --> H[provider.chat]
  H --> I[success envelope]
  G -- Exception --> X
```

## Decision Logic

- **Validation**: empty prompt -> error envelope. Once the server is saved, `api_key` is never the blocker: saving stores the user's key or the declared placeholder (`"ollama"`) in the `ollama` row, so the central "API key required" check in `execute_chat` passes. `OllamaCredential.resolve()` falls back to the same placeholder.
- **Provider routing**: `detect_ai_provider` MUST list `ollama` (in `server/constants.py`) or the node falls through to `'openai'` and `execute_chat` hits api.openai.com with the placeholder key.
- **Open-world model name**: `ollama` declares `open_world_models: true` in `llm_defaults.json`, so `is_model_valid_for_provider` does not reject local model names like `qwen/qwen3.6-27b` with the cloud-style pattern check.
- **Base URL routing**: the `ollama_proxy` credential carries the resolved server URL into the client; nothing is probed at call time, and traffic stays on `localhost`.
- **No thinking knob**: the shared `thinking_enabled` field is present but generic Ollama has no per-call thinking parameter; reasoning is per-model.

## Side Effects

- **Database writes**: per-model context params persist in `EncryptedAPIKey.models["model_params"]` and in `DATA_DIR/local_models.json` at save time (via `_local_validator.save_llm_server` + `ModelRegistryService.register_local_model()`), never in the tracked `config/model_registry.json`, and not on the bare chat path.
- **Broadcasts**: none on the bare chat path.
- **External API calls**: `POST {user_server}/v1/chat/completions` via the `openai` SDK with overridden `base_url` (default `http://localhost:11434/v1`).
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: optional `auth_service.get_api_key('ollama')`; user server URL stored as `ollama_proxy`.
- **Services**: `services/llm/providers/openai.py` (reused with Ollama base_url); `services/llm/endpoints.py` (roots the URL at save time); `nodes/model/_local_validator.py` (the save path; SDK probe via `ollama.AsyncClient.ps()`).
- **Python packages**: `openai`, `ollama>=0.6.0` (validation only).
- **Environment variables**: none.

## Edge cases & known limits

- **Server must be running**: requests go to the user's local Ollama server; if it is down the request surfaces as a connection error in the envelope.
- **Saving checks the URL first**: the URL entered is tried as given, then with `/v1` appended, and adopted only where `GET /models` returns an OpenAI list. A save that finds no such list, or no loaded model, writes nothing and keeps the previous server.
- **Provider routing dependency**: `ollama` must be present in `detect_ai_provider`, or the node silently falls back to OpenAI cloud. Agents need no edit: their `provider` field lists every registered provider through the `aiProviders` loader.
- **Max output default**: ctx ÷ 4, capped at 4096, unless the user overrides `max_tokens`.
- **Per-model params survive restart**: written through to `DATA_DIR/local_models.json`, which an OpenRouter refresh does not touch, so context length is known without re-clicking Fetch.
- **Error boundary**: typed OpenAI SDK connection/API failures become user-safe `NodeUserError` values in `ChatUnifier` and are re-raised to `BaseNode.execute()`, which produces the standard failure envelope. Unexpected failures are logged and returned by `execute_chat`.

## Related

- **Peer nodes**: [`lmstudioChatModel`](./lmstudioChatModel.md) (other local-server provider), and the cloud chat-model docs in this folder.
- **Architecture docs**: [Native LLM SDK](../../native_llm_sdk.md).
