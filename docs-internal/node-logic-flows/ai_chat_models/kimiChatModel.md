# Kimi Chat Model (`kimiChatModel`)

| Field | Value |
|------|-------|
| **Category** | ai_chat_models |
| **Backend handler** | [`server/nodes/model/kimi_chat_model/__init__.py`](../../../server/nodes/model/kimi_chat_model/__init__.py) (dispatch via `BaseNode.execute()` -> `@Operation("chat")` in [`server/nodes/model/_base.py`](../../../server/nodes/model/_base.py)) |
| **AI service** | [`server/services/ai.py::AIService.execute_chat`](../../../server/services/ai.py) |
| **Tests** | [`server/tests/nodes/test_ai_chat_models.py`](../../../server/tests/nodes/test_ai_chat_models.py) |
| **Skill (if any)** | n/a |
| **Dual-purpose tool** | no (group `('model',)`) |

## Purpose

Kimi models by Moonshot AI. `kimi-k3` is the current default with a
1,048,576-token context window and a 131,072-token output ceiling;
`kimi-k2.6`, `kimi-k2.7-code` and `kimi-k2.7-code-highspeed` remain available
as 262K tiers (`kimi-k2.5` left Moonshot's pricing page in September 2026). The node uses Moonshot's OpenAI-compatible endpoint through the native
provider layer and the shared `ChatModelParams`.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| (none declared) | - | - | Configure `prompt` directly or resolve it through saved parameter templates; the model connection uses `output-model`. |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `prompt` | string | `""` | yes | - | User message |
| `system_prompt` | string | `""` | no | - | System prompt |
| `model` | string | `""` (injected) | no | - | `kimi-k3` default; `kimi-k2.6`, `kimi-k2.7-code` and `kimi-k2.7-code-highspeed` also supported |
| `temperature` | number\|null | `null` | no | - | Shared schema field; accepted but not sent, because Moonshot fixes every model's temperature |
| `max_tokens` | number\|null | `null` (model ceiling) | no | - | Up to 131,072 for K3; lower model-specific ceilings for K2 tiers |
| `top_p` | number\|null | `1.0` | no | - | Declared schema field; not forwarded by the standalone chat path |
| `thinking_enabled` | boolean | `false` (Params default) | no | - | When false/unset, the native provider explicitly disables K2.6's default thinking; no effect on K2.7 Code (+ `-highspeed`), which always thinks, or on K3, which always reasons |
| `api_key` | string\|null | `null` (injected) | no | - | `auth_service.get_api_key('kimi', 'default')` |

(Kimi uses the shared `ChatModelParams` unchanged; field names are snake_case, unknown keys ignored.)

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
  thinking: string | null;
  thinking_enabled: boolean;
  model: string;
  provider: 'kimi';
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
  D -- yes --> E[detect_ai_provider -> 'kimi']
  E --> F[Preserve opaque provider model ID]
  F --> G[native_resolve_temperature<br/>clamps to 0-1]
  G --> H[ChatUnifier.chat -> registry.get_provider kimi<br/>OpenAI SDK w/ Moonshot base_url]
  H --> I[provider.chat<br/>sends no temperature]
  I --> J[success envelope]
  H -- Exception --> X
```

## Decision Logic

- **Validation**: missing api_key / empty prompt -> error envelope.
- **Provider routing**: `detect_ai_provider` matches `'kimi' in node_type.lower()`. Ordering guarantees it lands in the kimi lane before groq / openrouter / anthropic / gemini.
- **Temperature policy**: no Kimi request carries a temperature, whether it comes from this node or an agent, direct or through the hosted gateway. Moonshot fixes every model's temperature (1.0 for K3 and K2.7 Code; for K2.6, 1.0 with thinking and 0.6 without) and answers any other value with a 400; its [model page](https://platform.kimi.ai/docs/api/models-overview) says not to pass one. The kimi block's `supported_params` in `llm_defaults.json` leaves out `temperature`, so `OpenAIProvider._model_policy` sends none.
- **Thinking defaults**: K2.6 thinks by default at Moonshot, so the native provider explicitly sends `thinking={"type":"disabled"}` unless the request enables thinking. This keeps ordinary chat and tool-call parsing deterministic. K2.7 Code (+ `-highspeed`) always thinks and Moonshot rejects disabling it, so nothing is sent and the thinking toggle has no effect; K3 always reasons and takes no thinking parameter.
- **Native path**: uses the OpenAI SDK with Moonshot base_url from `llm_defaults.json`.
- **Model ID handling**: only the UI-only `[FREE] ` decoration is stripped. The remaining model ID is preserved.

## Side Effects

- **Database writes**: none on bare chat path.
- **Broadcasts**: none.
- **External API calls**: `POST https://api.moonshot.ai/v1/chat/completions` (via OpenAI SDK with override).
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `auth_service.get_api_key('kimi', 'default')` plus optional `kimi_proxy`.
- **Services**: `services/llm/providers/openai.py` (reused).
- **Python packages**: `openai`.
- **Environment variables**: none.

## Edge cases & known limits

- **Temperature is non-configurable**: any user-supplied value is accepted and dropped for every Kimi model, K3 included; Moonshot applies its own.
- **Default application behavior is thinking off** for K2.6 unless the request explicitly enables it. K2.7 Code always thinks and K3 always reasons, whatever the toggle says.
- **K3 capacity**: 1,048,576-token context and 131,072-token output. K2 tiers use 262,144-token context with model-specific 32K or 96K output ceilings.
- **Error boundary**: typed OpenAI SDK failures become user-safe `NodeUserError` values in `ChatUnifier` and are re-raised to `BaseNode.execute()`, which produces the standard failure envelope. Unexpected failures are logged and returned by `execute_chat`.

## Related

- **Peer nodes**: see the other chat-model docs in this folder.
- **Architecture docs**: [Native LLM SDK](../../native_llm_sdk.md).
