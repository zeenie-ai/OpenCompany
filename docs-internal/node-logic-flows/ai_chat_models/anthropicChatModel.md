# Anthropic Chat Model (`anthropicChatModel`)

| Field | Value |
|------|-------|
| **Category** | ai_chat_models |
| **Backend handler** | [`server/nodes/model/anthropic_chat_model/__init__.py`](../../../server/nodes/model/anthropic_chat_model/__init__.py) (dispatch via `BaseNode.execute()` -> `@Operation("chat")` in [`server/nodes/model/_base.py`](../../../server/nodes/model/_base.py)) |
| **AI service** | [`server/services/ai.py::AIService.execute_chat`](../../../server/services/ai.py) |
| **Tests** | [`server/tests/nodes/test_ai_chat_models.py`](../../../server/tests/nodes/test_ai_chat_models.py) |
| **Skill (if any)** | n/a |
| **Dual-purpose tool** | yes - tool name `anthropic_chat_model` (advisor; `usable_as_tool = True`, group `('model', 'tool')`) |

## Purpose

Single-turn chat completion against the Anthropic Messages API (Claude family). The `ChatModelBase.chat` operation (in `_base.py`) calls the provider-neutral `AIService.execute_chat`; Anthropic-specific request and response behavior lives in `services/llm/providers/anthropic.py` behind `ChatUnifier`. Also wired as an AI-agent advisor tool (`usable_as_tool = True`) so an agent can consult a stronger model mid-task.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| (none declared) | - | - | Configure `prompt` directly or resolve it through saved parameter templates; the model connection uses `output-model`. |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `prompt` | string | `""` | yes (non-empty) | - | User message |
| `system_prompt` | string | `""` | no | - | System prompt |
| `model` | string | `""` (injected from stored models) | no | - | Anthropic model ID - **hyphenated** form (e.g. `claude-sonnet-4-6`, NOT `claude-sonnet-4.6`) |
| `temperature` | number\|null | `null` -> falls through to `agent.default_temperature` | no | - | Legacy sampling policy clamps 0-1 and uses 1 with budget thinking; configured adaptive generations omit sampling |
| `max_tokens` | number\|null | `null` -> per-model default | no | - | 1-200000. Legacy budget mode raises an insufficient max_tokens above its budget; adaptive mode has no budget_tokens field |
| `top_p` | number\|null | `1.0` | no | - | Declared schema field; not forwarded by the standalone chat path |
| `top_k` | number\|null | `40` | no | - | Declared schema field; not forwarded by the standalone chat path |
| `thinking_enabled` | boolean | `false` | no | - | Enable extended thinking |
| `thinking_budget` | number\|null | `2048` | no | `thinking_enabled=[true]` | 1024-16000 tokens allocated to internal reasoning |
| `api_key` | string\|null | `null` (injected) | no | - | `auth_service.get_api_key('anthropic', 'default')` |

(Field names are snake_case on the `AnthropicChatModelParams` Pydantic model; `model_config = ConfigDict(extra="ignore")` drops unknown keys.)

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
  thinking: string | null;     // Claude extended thinking content, if enabled
  thinking_enabled: boolean;
  model: string;
  provider: 'anthropic';
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
  B --> C[get_ai_service]
  C --> D[AIService.execute_chat]
  D --> E{api_key + non-empty prompt?}
  E -- no --> X[ValueError -> error envelope]
  E -- yes --> F[detect_ai_provider -> 'anthropic']
  F --> G[Build NativeThinkingConfig if thinking_enabled<br/>budget from thinking_budget]
  G --> H[Lookup anthropic_proxy credential]
  H --> I[ChatUnifier.chat -> registry.get_provider anthropic<br/>await provider.chat with thinking]
  I --> J[Return success envelope with response + thinking]
  I -- typed SDK error --> X[NodeUserError -> BaseNode error envelope]
```

## Decision Logic

- **Validation**: missing api_key or empty prompt -> error envelope.
- **Provider routing**: `detect_ai_provider` matches `'anthropic'` on `'anthropic' in node_type.lower()` branch.
- **Native provider**: `ChatUnifier` resolves the self-registered `anthropic` spec - uses the `anthropic` Python SDK.
- **Model ID handling**: strips only the UI-only `[FREE] ` decoration. Provider model IDs are otherwise treated as opaque and preserved.
- **Sampling policy**: the shared resolver computes temperature, but AnthropicProvider omits sampling for model prefixes in `sampling_params_removed`. For legacy sampling-enabled generations, thinking uses temperature=1.
- **Thinking config**: the provider reads JSON model-prefix policy. Adaptive generations use `type: adaptive` with summarized display; legacy budget generations use `type: enabled` and budget_tokens. Configured prefix-binding generations additionally carry the reasoning-block binding controls; stored signed history remains intact.

## Side Effects

- **Database writes**: none on the bare chat path.
- **Broadcasts**: none.
- **External API calls**: Anthropic Messages API via the `anthropic` SDK's default endpoint; an `anthropic_proxy` credential can override the SDK base URL.
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `auth_service.get_api_key('anthropic', 'default')` plus optional `anthropic_proxy`.
- **Services**: `services/llm/providers/anthropic.py`, `AIService`.
- **Python packages**: `anthropic`.
- **Environment variables**: none.

## Edge cases & known limits

- **Model ID format**: Anthropic API uses hyphens (`claude-sonnet-4-6`), NOT dots. Dotted IDs (as used by OpenRouter or Gemini) will 404. The frontend model list already uses the correct form, but template-substituted or hand-entered IDs with dots will fail.
- **Legacy budget versus output**: only in budget mode, an insufficient max_tokens is raised to budget+1024. Adaptive mode does not send budget_tokens.
- **Adaptive thinking/sampling**: see the JSON prefix lists; neither a universal temperature=1 rule nor a universal budget_tokens request describes all current configured generations.
- **1M context**: current Claude models (Opus 5.5 / 5, Fable 5.1 / 5, Sonnet 5, Opus 4.8 / 4.7, Sonnet 4.6) support a 1M context window; configured in `llm_defaults.json` rather than the node.
- **Error boundary**: typed Anthropic SDK failures become user-safe `NodeUserError` values in `ChatUnifier` and are re-raised to `BaseNode.execute()`, which produces the standard failure envelope. Unexpected failures are logged and returned by `execute_chat`.
- **No streaming** / **no pricing tracking** on standalone chat nodes.

## Related

- **Peer nodes**: [`openaiChatModel`](./openaiChatModel.md), [`geminiChatModel`](./geminiChatModel.md), [`openrouterChatModel`](./openrouterChatModel.md), [`groqChatModel`](./groqChatModel.md), [`cerebrasChatModel`](./cerebrasChatModel.md), [`deepseekChatModel`](./deepseekChatModel.md), [`kimiChatModel`](./kimiChatModel.md), [`mistralChatModel`](./mistralChatModel.md).
- **Architecture docs**: [Native LLM SDK](../../native_llm_sdk.md), [Memory Compaction](../../memory_compaction.md).
