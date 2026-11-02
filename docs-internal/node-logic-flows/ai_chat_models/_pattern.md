# Shared standalone chat-model contract

Status: current implementation, verified against committed `56d4154e`.
Applies to all chat-model plugins inheriting
[ChatModelBase.chat](../../../server/nodes/model/_base.py), with concrete
provider Params/credentials and policy overrides described by their cards.

## Supported execution paths

- **Direct Run:** the normal node pipeline resolves credentials/model and
  parameter templates, then chat performs one nonstreaming completion. A
  nonempty prompt and configured credentials/eligible gateway access are
  required. An explicit model connection is not a previous conversation.
- **Graph/model binding:** output-model connects provider/model configuration
  to an agent; the model class declares no input-main handle. A passive model
  binding is not automatically another scheduled main-flow action.
- **Agent advisor tool:** only OpenAI, Anthropic and Gemini classes explicitly
  declare usable_as_tool and their advisor callable. Shared metadata also
  emits output-tool for them. Other provider nodes are model configurations
  unless their concrete class declares tool support.
- **Temporal:** graph-node scheduling and agent tool scheduling have distinct
  timeout/retry call sites. Cooperative admission, result recording, explicit
  cancellation and Reset follow their actual caller; a cloud request already
  sent is not undone by Stop.

## Standalone parameter forwarding

The cards list the main and provider-specific fields. Every model also inherits
these fields from `ChatModelParams`, except where its concrete Params overrides
them. The complete per-node field inventory is in the core audit ledger.

| Shared field | Schema default and constraint |
|---|---|
| `prompt` | `""`; runtime requires nonempty message content |
| `model` | `""`; normal execution resolves the configured provider/model |
| `temperature` | `null`; number 0-2 when supplied, then native model policy |
| `max_tokens` | `null`; integer 1-200000 when supplied, then native model policy |
| `system_prompt` | `""`; nullable string |
| `api_key` | `null`; injected credential, eligible gateway, or local endpoint auth policy |
| `top_p` | `1.0`; nullable number 0-1 |
| `thinking_enabled` | `false` |
| `thinking_budget` | `null`; nullable integer at least 1. Anthropic, Gemini and Cerebras override this to 2048 with 1024-16000 bounds |
| `reasoning_effort` | `null`; nullable `low`, `medium`, `high`. OpenAI adds `minimal` and defaults to `medium`; Sarvam also defaults to `medium` |
| `reasoning_format` | `null`; nullable `parsed`, `hidden`. Groq overrides the default to `parsed` |

The declared Params schema is broader than the current
[AIService.execute_chat](../../../server/services/ai.py) call. That path
forwards messages, model, temperature, max_tokens and thinking configuration,
but does not forward top_p, top_k, frequency_penalty, presence_penalty,
response_format or safety_settings. Setting those schema fields does not
establish that the provider receives them. This is an existing implementation
gap, not a guarantee about upstream capabilities.

When enabling thinking, supply a numeric thinking_budget where the shared
schema defaults it to null. The current service converts it with int() before
provider dispatch and can fail on null even for an effort-based model. The
provider-specific policy may then omit or reinterpret thinking/sampling fields.
Unknown Params are ignored by the shared model, so older camelCase names do
not enable the corresponding snake_case controls.

## Effects, errors and policy

The call uses the native provider through ChatUnifier. Missing prompt/key,
SDK/user errors and output validation follow the shared node boundary. The
standalone path returns response/thinking/model/provider/finish metadata;
agent conversation continuity and automatic compaction are separate runtime
features. A shared provider base does not imply every cloud model accepts the
same parameters or emits the same reasoning blocks.

Configured thinking/sampling model-prefix lists and per-model limits live in
[llm_defaults.json](../../../server/config/llm_defaults.json) and native
provider policy. Anthropic adaptive versus legacy budget thinking differs;
Cerebras' shipped thinking_models list is empty. Vendor model availability,
subscription tiers and account capabilities were not validated by offline
checks; catalogue statements describe the shipped configuration snapshot.

The source-backed node matrix and offline test evidence are in the
[core node audit](../../audits/CORE_NODES.md).
