# Native LLM SDK Architecture

> **Authoring model (post-Wave-11):** each chat-model node is a self-contained Python plugin folder under `server/nodes/model/<provider>_chat_model/` that emits a `NodeSpec`. The frontend reads specs via [client/src/lib/nodeSpec.ts](../client/src/lib/nodeSpec.ts) + [adapters/nodeSpecToDescription.ts](../client/src/adapters/nodeSpecToDescription.ts) and renders through `SquareNode` with zero TS edits. See [plugin_system.md](./plugin_system.md) and [server/nodes/README.md](../server/nodes/README.md) for the plugin model, and "Adding a New Provider" below for the chat-model recipe.

OpenCompany routes chat-model nodes and every agent execution through the
native SDK layer in `server/services/llm/`. The shared buffered tool loop lives
in `server/services/agent_runtime.py`.

## Why a Native Layer

Talking to each vendor SDK directly avoids three problems a translation layer kept reintroducing:

1. **Windows/Python 3.13 hangs**: the wrapped Google adapter deadlocked on gRPC init. The native Gemini path uses `google.genai.Client` directly.
2. **Parameter translation loss**: a `max_completion_tokens` rewrite breaks OpenAI-compatible providers (DeepSeek, Kimi, Mistral) that expect `max_tokens`.
3. **Endpoint control**: OpenAI-compatible endpoint URLs are declared in
   `server/config/llm_defaults.json` and registered through `_compat.py`.
   Adding one requires the JSON entry plus one `_COMPAT_PROVIDERS` entry, not a
   provider-specific adapter class. A server the user runs needs no code at
   all: it is a named endpoint (see "Named OpenAI-compatible endpoints").

The native layer also gives a single normalized response shape (`LLMResponse`) across providers, which simplifies downstream code for token tracking, cost calculation, and thinking extraction.

## Layer Overview

```
server/services/llm/
|-- __init__.py           Public API exports
|-- protocol.py           Message, ToolDef, ToolCall, Usage, LLMResponse, LLMProvider (Protocol)
|-- config.py             ProviderConfig, PROVIDER_CONFIGS (built from llm_defaults.json);
|                         provider references (split_provider_ref) and resolve_credential
|-- endpoints.py          Save-time only: resolve_base_url (roots a user URL by asking the
|                         server), redact_url, list_endpoints (RFC-0003)
|-- registry.py           ProviderSpec + register_provider() — the live provider entry point;
|                         sdk_exception_refs are lazy "module:Class" strings resolved at except time.
|                         THIN SHIM since the speech work: the mechanism lives in
|                         services/provider_registry.py (generic, shared). `_REGISTRY` survives here
|                         as an alias bound to the SAME dict object the registry mutates, because
|                         several tests swap provider factories in place through it.
|-- unifier.py            ChatUnifier — the dispatch facade execute_chat delegates every provider to;
|                         translates typed SDK errors -> NodeUserError
|-- schema.py             Provider-aware tool JSON-schema compilation
|-- vertex.py             Vertex / Agent-Platform key handling
|-- messages.py           filter_empty_messages, is_valid_message_content
|-- media.py              hydrate_image_blocks / provider_supports_vision (see "Multimodal image input")
`-- providers/
    |-- __init__.py
    |-- anthropic.py      AnthropicProvider (anthropic SDK)
    |-- openai.py         OpenAIProvider (openai SDK)
    |-- gemini.py         GeminiProvider (google-genai SDK)
    |-- openrouter.py     OpenRouterProvider (extends OpenAIProvider with headers)
    `-- _compat.py        Registers the 9 OpenAI-compatible providers (xai, deepseek, kimi,
                          mistral, groq, cerebras, ollama, lmstudio, sarvam) via base_url specs,
                          plus openai_compatible (no base_url) for every named endpoint
```

Each provider module calls `register_provider(ProviderSpec(...))` at module
bottom. Shipped provider modules are imported from `providers/__init__.py` so
their registration side effects run at service import. OpenAI-compatible
providers share `_compat.py` and its explicit `_COMPAT_PROVIDERS` tuple.

## Supported Providers

The native layer currently registers **14 providers**, grouped by implementation.
The last, `openai_compatible`, is not one server: it serves any number of
user-named endpoints, each its own provider reference `openai_compatible:<slug>`.

| Provider | Implementation | SDK | Notes |
|---|---|---|---|
| `anthropic` | `providers/anthropic.py` | `anthropic` | Extended thinking: `{"type":"adaptive"}` on the 4.7+ flagships, `budget_tokens` on Sonnet 4.6 / Haiku 4.5 (JSON-driven `_model_policy`) |
| `openai` | `providers/openai.py` | `openai` | Reasoning-only o-series (`o3`, `o4-mini`) and GPT-5.x hybrid `reasoning_effort` |
| `gemini` | `providers/gemini.py` | `google-genai` | Direct SDK (Windows hang fix) |
| `openrouter` | `providers/openrouter.py` | `openai` | Sets `HTTP-Referer` + `X-Title` headers |
| `xai` | `providers/_compat.py` + base_url | `openai` | OpenAI-compatible at `api.x.ai/v1` |
| `deepseek` | `providers/_compat.py` + base_url | `openai` | OpenAI-compatible at `api.deepseek.com` (root-mounted, no `/v1`) |
| `kimi` | `providers/_compat.py` + base_url | `openai` | Moonshot AI, OpenAI-compatible |
| `mistral` | `providers/_compat.py` + base_url | `openai` | OpenAI-compatible |
| `ollama` | `providers/_compat.py` + `{provider}_proxy` URL | `openai` (chat) + `ollama` (probe) | Local server. Saving roots the URL through the OpenAI surface, then `ollama.AsyncClient.ps()` gives the typed `context_length` of each loaded model. Runtime passes the resolved URL to `OpenAIProvider` as `proxy_url` (it wins over the `llm_defaults.json` `base_url`), so traffic stays on `localhost`. |
| `lmstudio` | `providers/_compat.py` + `{provider}_proxy` URL | `openai` (chat) + `lmstudio` (probe) | Local server. Same save path; `lmstudio.AsyncClient.llm.list_loaded()` gives the typed `LlmInstanceInfo.context_length`. Same OpenAI-compat runtime path as Ollama. |
| `openai_compatible` | `providers/_compat.py` (no `base_url`) + one `{ref}_proxy` URL per endpoint | `openai` | Named endpoints: llama.cpp, vLLM, a LiteLLM proxy, a second Ollama host. Added in the Credentials Modal; see "Named OpenAI-compatible endpoints". |
| `groq` | `providers/_compat.py` + base_url | `openai` | OpenAI-compatible |
| `cerebras` | `providers/_compat.py` + base_url | `openai` | OpenAI-compatible |
| `sarvam` | `providers/_compat.py` + base_url | `openai` | Indic-first (`sarvam-105b` 128K, `sarvam-30b` 64K) at `api.sarvam.ai/v1`. Ships **no model-list route**, so it sets `supports_model_listing: false` — see below. Reasoning is on by default and returns in `reasoning_content`, which `OpenAIProvider._normalize` already reads. |

Source of truth for this list: `server/config/llm_defaults.json` (the `providers` dict) and the `register_provider(ProviderSpec(...))` calls in `server/services/llm/providers/` (each provider module registers itself at import; the legacy `factory.py` was removed).

### Native chat path vs agent dropdown — two different counts

- **Native chat path (`execute_chat` / `fetch_models`) supports every registered provider, all native** — Anthropic and Gemini via their own SDKs; OpenAI and OpenRouter via the openai SDK; and the rest through the shared OpenAI-compatible client (xai, deepseek, kimi, mistral, groq, cerebras, ollama, lmstudio, sarvam with a per-provider `base_url`, and every named endpoint with its own resolved URL — all registered in `providers/_compat.py`). `execute_chat` delegates every provider to `ChatUnifier.chat`; there is no per-provider branch. `xai` lives here.
- **The agent dropdown offers every registered provider, then each saved
  endpoint**, for `aiAgent`, `chatAgent` (Zeenie) and all specialized agents.
  The `provider` field is one loader-driven `ProviderRef`
  (`nodes/agent/_provider.py`, options from the `aiProviders` loader in
  `nodes/model/_option_loaders.py`, ordered as `llm_defaults.json` lists the
  providers), so registering a provider needs no agent edit.
  `test_plugin_shape.py` asserts the loader offers exactly the registry minus
  the bare `openai_compatible` id.
- Groq, Cerebras, xAI, and the other compatible endpoints use the same native
  OpenAI SDK adapter for both standalone chat and agent tool calls.

### Provider / model reference table

`ModelRegistryService` (`server/services/model_registry.py`) manages the per-model constraints below — fetching from OpenRouter for cloud models and from the user's running local server (`ollama.AsyncClient.ps()` / `lmstudio.AsyncClient.llm.list_loaded()`) for Ollama / LM Studio, and — for a named endpoint — from the server itself or LiteLLM's model table at save time (see [Named OpenAI-compatible endpoints](#named-openai-compatible-endpoints)). Falls back to `llm_defaults.json` only when none of these has a figure.

| Provider | Key Models | Context | Max Output | Thinking | Temp Range |
|----------|-----------|---------|-----------|----------|------------|
| **OpenAI** | GPT-6 Astra (+ `-pro`), GPT-5.6 Sol/Terra/Luna (+ `-pro`; default `gpt-5.6-sol`), GPT-5.5/5.4 | 1.05M | 128K | effort | omitted for reasoning models |
| **OpenAI** | GPT-4.1 | ~1.05M | 32K | none | 0-2 |
| **Anthropic** | Claude Opus 5 (default `claude-opus-5`), Fable 5.1 / 5, Sonnet 5 | 1M | 128K | adaptive | omitted (`temperature` / `top_p` / `top_k` rejected — `sampling_params_removed`) |
| **Anthropic** | Claude Opus 4.8/4.7 | 1M | 128K | adaptive | omitted (`sampling_params_removed`) |
| **Anthropic** | Claude Sonnet 4.6 | 1M | 128K | budget | 0-1 |
| **Anthropic** | Claude Haiku 4.5 | 200K | 64K | budget | 0-1 |
| **Google** | Gemini 3.8-flash (default), 3.7/3.6/3.5-flash, 3.5-flash-lite, 3.1-pro-preview/flash-lite, 3-flash-preview, 2.5-pro/flash/flash-lite | 1M | 64K | budget (`thinking_level` on 3.x when set explicitly) | 0-2 |
| **xAI** | Grok 4.20/4.20-multi-agent, 4.6, 4.5, 4.3, 3 | 131K-1M | 131K | model/provider dependent | 0-2 |
| **DeepSeek** | deepseek-flash (default; V4.1-Flash), deepseek-v4.1-flash, deepseek-v4-pro (deepseek-v4-flash is a retired alias served by V4.1-Flash; chat/reasoner discontinued) | 1M | 384K | thinking modes | 0-2 |
| **Kimi** | kimi-k3 (default), kimi-k2.6, kimi-k2.7-code (+ `-highspeed`) | 1M (K3); 256K (K2) | 131K (K3); 32K/96K (K2) | K2 provider default explicitly disabled unless requested | K2 fixed 0.6; K3 0-1 |
| **Mistral** | mistral-large/medium/small-latest (Large 3 / Medium 3.5 / Small 4), codestral-latest | 256K | 32K-131K | none | 0-1.5 |
| **Groq** | GPT-OSS-120b/20b, Llama 3.x tiers, qwen3.8-27b + minimax-m2.7 (preview) | 131K-196K | 16K-131K | effort (GPT-OSS), format (Qwen3) | 0-2 |
| **OpenRouter** | 400+ models from multiple providers | varies | varies | varies | 0-2 |
| **Cerebras** | GPT-OSS-120b (default), qwen-3.8-27b — the only two public-endpoint models | 131K | 40K | none (`thinking_models` empty since zai-glm-4.7 left the public endpoints) | 0-1.5 |
| **Ollama** | Whatever the user has pulled (qwen2.5, llama3.x, phi-3, deepseek-r1, ...) | per-loaded-model (typed via `ps()`) | ctx ÷ 4 (capped 4096) | none (per-model) | 0-2 |
| **LM Studio** | Whatever the user has loaded in the LM Studio UI | per-loaded-model (typed via `LlmInstanceInfo.context_length`) | ctx ÷ 4 (capped 4096) | none (per-model) | 0-2 |
| **Named endpoints** | Whatever each server lists at `/models` | llama.cpp `/props` `n_ctx`; vLLM `max_model_len`; else the LiteLLM table; else 8192 | LiteLLM figure, else ctx ÷ 4 (capped 4096), else 2048 | none (per-model) | 0-2 |

`_resolve_max_tokens()` in `server/services/ai.py` (a thin wrapper over `services/llm/config.py::resolve_max_tokens`) clamps user-requested `max_tokens` to the model's actual limit.

## Local LLM Providers (Ollama, LM Studio)

Ollama and LM Studio expose an OpenAI-compatible `/v1` HTTP API, so they ride the same `OpenAIProvider` runtime path used by every other OpenAI-compat backend (DeepSeek, Kimi, Mistral). The differences are the **base URL** (the user enters their server's address, e.g. `http://localhost:11434/v1`) and the **per-model parameters** (which depend on what the user has loaded in the local server's UI, not a JSON default).

**Save path** ([`server/nodes/model/_local_validator.py`](../server/nodes/model/_local_validator.py), RFC-0003 §6) — when the user clicks "Fetch" in the Credentials Modal:

1. **Root the URL** through the OpenAI surface the runtime uses: `services/llm/endpoints.py::resolve_base_url` sends the SDK's own `models.list()` to the entered URL, then to it plus `/v1`, and adopts the first answer whose body is an OpenAI list. A status code alone proves nothing — LM Studio answers HTTP 200 to routes it does not serve — so a URL entered without `/v1` is resolved rather than saved broken. This replaces the old order (persist, then probe the *native* API), which let a `/v1`-less LM Studio URL read Connected and fail every run.
2. **Describe the loaded models** via the **official SDK** (`ollama>=0.6.0`, `lmstudio>=1.5.0`) — never raw httpx, never Modelfile-parameters parsing:
   - **Ollama**: `ollama.AsyncClient.ps()` returns `ProcessResponse.Model` per loaded model with typed `context_length` + typed `ModelDetails` (`family`, `parameter_size`, `quantization_level`, `format`).
   - **LM Studio**: `lmstudio.AsyncClient.llm.list_loaded()` returns `AsyncModelHandle` per loaded model; `handle.get_info()` is a typed `LlmInstanceInfo` (`context_length`, `max_context_length`, `vision`, `trained_for_tool_use`, `architecture`, `params_string`, `format`).
3. **Persist only on success**: `{provider}_proxy` gets the resolved URL, then `{provider}` gets the vendor's documented placeholder key (`auth.placeholder_key` in `llm_defaults.json`, resolved by `services/llm/config.py::resolve_credential`), the model list and `model_params`. The params are also registered with `model_registry.register_local_model()`, which keeps them apart from the OpenRouter snapshot and persists them under DATA_DIR (`local_models.json`), so the sync `get_context_length()` / `get_max_output_tokens()` lookups honour the **real n_ctx the server is currently serving**, an OpenRouter refresh cannot wipe them, and user model names never land in the tracked `config/model_registry.json`.

A failed save writes nothing and broadcasts nothing: a typo in a re-Fetch leaves a working configuration in force. That includes a write the credential store rejects: a rejected key row puts the URL row back as it was. **Both servers must have a model loaded** for the probe to return entries; "no models loaded" is a failed save.

Every step after rooting is bounded so a save fits its WebSocket request: native routes 3 s (asked at once), the SDK probes 10 s, an on-demand LiteLLM fetch 8 s (not retried for 10 minutes after a failure). The client waits 60 s for `validate_api_key` (`CREDENTIAL_PROBE_REQUEST_TIMEOUT` in `WebSocketContext.tsx`), above the worst case of about 33 s.

**Runtime path** — `ChatUnifier` reads `{provider}_proxy` and passes it to the
registered provider factory. `OpenAIProvider` uses it as `base_url` and sends
the stored key as stored (no provider rewrites a key any more). The OpenAI SDK
then sends to the configured local endpoint — traffic never reaches
api.openai.com.

**Provider detection** ([`server/constants.py:detect_ai_provider`](../server/constants.py)) MUST list `ollama` / `lmstudio` substrings, or a chat-model node falls through to `'openai'`. Agents need no edit: their `provider` field is loader-driven.

**Open-world providers** — local model names like `qwen/qwen3.6-27b` don't contain provider substrings, so the cloud-style pattern check would always reject them. A provider block declares `"open_world_models": true` (openrouter, groq, ollama, lmstudio, openai_compatible) and `is_model_valid_for_provider` accepts any id for it. The upstream server still rejects genuinely missing models with a clear 404.

## Named OpenAI-compatible endpoints

Any server that speaks the OpenAI API — llama.cpp, vLLM, a LiteLLM proxy,
SGLang, LocalAI, another Ollama or LM Studio host — is added in the Credentials
Modal under **OpenAI-compatible** with a Base URL (with or without `/v1`), an
optional label and an optional key. No code. RFC-0003 D13–D17 is the contract.

- **Identity.** Each endpoint is the provider reference
  `openai_compatible:<slug>` (slug from the label, else from the host and port,
  never from userinfo in the URL).
  It is stored exactly like Ollama: `{ref}` holds the key (or the placeholder
  `sk-no-key-required`), the model list and per-model params; `{ref}_proxy`
  holds the resolved URL. The reference travels as the ordinary `provider`
  string, so key injection, Temporal payloads and the unifier need nothing new;
  everything read from `llm_defaults.json` or `pricing.json` resolves it to the
  shared `openai_compatible` block through `split_provider_ref`.
- **Saving** runs the same path as Ollama, plus a one-time kind detection from
  native routes by body shape, asked at once (`/props` → llama.cpp, which wins
  when several answer; `/api/v1/models` → LM Studio; `/api/version` → Ollama;
  else generic). Context and price come from the
  server first (Ollama / LM Studio SDK probes, llama.cpp `/props` `n_ctx`, vLLM
  `max_model_len`), then — for generic servers only — LiteLLM's
  `model_prices_and_context_window.json` (fetched by `ModelRegistryService` on
  the OpenRouter refresh gate, trimmed, cached under DATA_DIR, matched only when
  unambiguous), then the block's conservative defaults (8192 / 2048).
- **Selecting.** Every agent's provider dropdown lists each endpoint after the
  registered providers. The `openaiCompatibleChatModel` node picks one in its
  `endpoint` field — deliberately not `provider`, which would trigger the
  parameter panel's stored-key effect — and its `model` dropdown lists that
  endpoint's models; changing the endpoint moves the model to one the new
  endpoint serves. The global default-model picker lists each endpoint too.
- **Removing** goes through `delete_api_key` with the reference, which also
  clears the URL row and the registered models. A workflow still pointing at a
  removed endpoint fails with "The OpenAI-compatible endpoint '<slug>' is not
  configured" (`endpoints.py::unconfigured_endpoint_message`, on every path),
  never a call to OpenAI, and the workflow validator flags the node, because
  `Credential.is_configured` checks the endpoint the node names.
- **Failures** log the redacted URL and `url_source` under the endpoint's
  reference, on agent steps as well as direct chat.
- **RLM** builds its own clients and cannot use an endpoint (or a local
  server); it refuses one with a clear message, whether it is the agent's
  provider or a chat model connected to it.

## Provider Protocol

Every native provider implements a structural Protocol with two methods:

```python
@runtime_checkable
class LLMProvider(Protocol):
    provider_name: str

    async def chat(
        self,
        messages: List[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: Optional[ThinkingConfig] = None,
        tools: Optional[List[ToolDef]] = None,
    ) -> LLMResponse: ...

    async def fetch_models(self, api_key: str) -> List[str]: ...
```

All providers return the same `LLMResponse` dataclass, regardless of SDK:

```python
@dataclass
class LLMResponse:
    content: str = ""
    thinking: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    finish_reason: str = "stop"
    raw: Any = None
    assistant_message: Optional[Message] = None  # canonical replay envelope
```

`assistant_message` is the durable source of truth. The flat `content`,
`thinking`, and `tool_calls` fields remain convenience views; `raw` is never
written to memory or Temporal history.

## Registry, Unifier, and Lazy SDK Clients

The lightweight provider modules are imported eagerly so the registry is
complete at service startup. Heavy SDK imports, exception-class resolution,
and client construction are deferred until first use; `ChatUnifier` then keeps
clients in its bounded cache. This boundary is locked by
`tests/llm/test_lazy_sdk_imports.py`:

```python
# server/services/llm/providers/anthropic.py — bottom of the module
register_provider(
    ProviderSpec(
        name="anthropic",
        factory=lambda api_key, **kw: AnthropicProvider(api_key, **kw),   # invoked when a client is first needed
        sdk_exception_refs=("anthropic:APIError",),                        # "module:Class" string, resolved at except time
    )
)
```

At call time `ChatUnifier.chat(provider=...)` looks the spec up via
`registry.get_provider(name)` and invokes `spec.factory(...)`. The unifier
also owns error translation: typed SDK exceptions (resolved lazily from
`sdk_exception_refs` via `pkgutil.resolve_name`) become `NodeUserError`
with a user-correctable message. There is no per-provider branch anywhere
in `ai.py`.

The legacy `factory.py` (`create_provider` / `is_native_provider` /
`NATIVE_PROVIDERS`) was **removed** — `ChatUnifier` + `registry.py` is the
only dispatch layer.

## Config-Driven Base URLs

`ProviderConfig` is built at import time from `llm_defaults.json`:

```python
@dataclass
class ProviderConfig:
    name: str
    default_model: str
    detection_patterns: Tuple[str, ...]
    models_endpoint: str
    api_key_header: str              # "Authorization", "x-api-key"
    api_key_format: str = "Bearer {key}"
    extra_headers: Dict[str, str] = field(default_factory=dict)
    base_url: str = ""               # OpenAI-compatible base URL
```

Adding a new OpenAI-compatible provider requires a config entry:

```json
"deepseek": {
  "default_model": "deepseek-flash",
  "detection_patterns": ["deepseek"],
  "models_endpoint": "https://api.deepseek.com/models",
  "base_url": "https://api.deepseek.com",
  "max_output_tokens": { "_default": 8192 },
  "context_length": { "_default": 131072 },
  "temperature_range": [0.0, 2.0]
}
```

Then add the same provider name to `_COMPAT_PROVIDERS` in
`providers/_compat.py`. No new provider class or module import is needed: the
compat loop reuses `OpenAIProvider` and pins the JSON `base_url` in
`ProviderSpec.client_kwargs`.

At client creation, a stored `{provider}_proxy` URL takes precedence over that
configured compat URL. Plain OpenAI uses the SDK default endpoint unless
`openai_proxy` is set. OpenRouter uses its adapter's built-in endpoint unless
`openrouter_proxy` is set. Ollama and LM Studio rely on their stored proxy URL
at runtime so requests go to the selected local server; `openai_compatible`
has no configured URL at all, and the unifier refuses an endpoint whose URL row
is missing rather than let the SDK default to api.openai.com.

The `base_url` values in the dedicated `anthropic` and `gemini` blocks are REST
documentation, not SDK arguments (the Anthropic SDK appends `/v1` itself), so
nothing passes them to those SDKs.

## Unified Native Execution Path

```
execute_chat / execute_agent / execute_chat_agent
        |
        v
ChatUnifier.chat(provider=..., Message[], ToolDef[])
        |
        +--> registry.get_provider(name)
        +--> official SDK or OpenAI-compatible base URL
        +--> LLMResponse(assistant_message=lossless MessageWire)
```

`run_native_agent_loop` appends the provider's exact assistant envelope before
executing tools. This preserves Gemini thought signatures, Anthropic signed and
redacted thinking blocks, and OpenAI reasoning continuation state across
in-process and Temporal turns. There is one wire standard (`MessageWire`) and
no engine or wire-version discriminator is recorded (see the end of this
document).

## Thinking and Reasoning

The unified `ThinkingConfig` dataclass is translated per provider:

```python
@dataclass
class ThinkingConfig:
    enabled: bool = False
    budget: int = 2048      # Anthropic budget_tokens, Gemini 2.5 thinking_budget
    effort: str = "medium"  # OpenAI reasoning_effort (low/medium/high)
    level: Optional[str] = None  # Gemini 3+ thinking_level, only when explicit
    format: str = "parsed"  # Groq Qwen3 reasoning_format (parsed/hidden)
```

Each provider's `chat()` method reads only the fields it supports. The extracted thinking text is returned in `LLMResponse.thinking` so downstream nodes can display or drag it into parameters.

### Supported thinking/reasoning providers

| Provider | Models | Parameter | Thinking Type | Notes |
|----------|--------|-----------|---------------|-------|
| **Claude** (adaptive) | `claude-opus-5`, `claude-fable-5*`, `claude-mythos-5`, `claude-sonnet-5`, `claude-opus-4-8`, `claude-opus-4-7` (`adaptive_thinking_models`, prefix-matched) | `thinkingEnabled` only — the budget is ignored | adaptive | Sends `{"type":"adaptive","display":"summarized"}` when enabled; when disabled the field is omitted (`disabled` is itself rejected on Fable/Mythos). `budget_tokens` is a 400 on these generations. No `temperature` / `top_p` / `top_k` is sent at all (`sampling_params_removed`). `anthropic.py:72-92`. |
| **Claude** (budget) | `claude-sonnet-4-6`, `claude-haiku-4-5` and older 4.x/3.5 | `thinkingBudget` (1024-16000 tokens) | budget | `{"type":"enabled","budget_tokens":N}`; provider bumps `max_tokens` to `budget + 1024` if it is not already larger. Temperature auto-set to 1. |
| **Gemini** | gemini-3.x, gemini-2.5-pro/flash | `thinkingBudget` (token count); `thinkingLevel` on 3.x when set explicitly | budget | Uses `thinking_budget` API parameter (`thinking_level` only when the user set it — Vertex rejects an unsolicited level on 2.5-era models) |
| **OpenAI** | `o3`, `o4-mini` (reasoning-only; both on the API-shutdown path per `_models_note`, `o1`/`o3`/`o4` prefixes still detected) | `reasoningEffort` (low/medium/high) | effort | Reasoning-only models. Temperature omitted. |
| **OpenAI** | GPT-5.6 sol/terra/luna (+ `-pro`), GPT-5.5, GPT-5.4 (`thinking_models: ["gpt-5"]`) | `reasoningEffort` (low/medium/high/xhigh) | effort | Hybrid reasoning: can operate with or without thinking. |
| **Groq** | qwen/qwen3.8-27b (`thinking_models: ["qwen3"]`, prefix match) | `reasoningFormat` ('parsed' or 'hidden') | format | 'parsed' returns reasoning, 'hidden' returns only final answer. GPT-OSS models on Groq use `reasoning_effort` instead (`openai.py:671-675`). |
| **Cerebras** | none — `thinking_models` is empty since zai-glm-4.7 left the public endpoints (2026-09) | `thinkingBudget` | budget | Would be sent as `extra_body.thinking_budget` (`openai.py:111-114`) if a thinking model were curated again. The qwen-3-235b variants are shut down upstream. |

The thinking/reasoning fields (`thinkingEnabled`, `thinkingBudget`, `reasoningEffort`, `reasoningFormat`) live in the backend NodeSpec for each chat model (`server/nodes/model/<provider>_chat_model/`) and are declared once on the shared `ChatModelParams` model in `server/nodes/model/_base.py:24`. The frontend renders them automatically via the universal parameter panel.

### Thinking extraction (legacy replay path)

Native providers normalize reasoning directly into `LLMResponse.thinking` and
ordered `Message.blocks`. The helper below remains only for pre-cutover
Temporal histories executing the legacy branch:

```python
def extract_thinking_from_response(response, provider: str) -> Optional[str]:
    """Extract thinking/reasoning from AI response based on provider."""
    # Claude: content_blocks with type='thinking'
    # Gemini: response_metadata.candidates[0].content.parts with thought=True
    # Groq: additional_kwargs.reasoning or response_metadata.reasoning
    # OpenAI o-series: requires organization verification
```

The agent/chat result envelope carries `thinking` alongside the answer:

```python
{
    "success": True,
    "result": {
        "response": "The final answer text",
        "thinking": "The model's internal reasoning (if available)",
        "model": "claude-3-5-sonnet-20241022",
        "provider": "anthropic",
        "finish_reason": "stop",
        "timestamp": "2025-01-23T...",
    }
}
```

The `thinking` field is exposed to downstream nodes via the backend output schema (`AIAgentOutput` in `server/services/node_output_schemas.py`, shared across every LLM-backed agent + chat model) and rendered as a `Thinking` section by the active output renderer (`client/src/components/output/OutputPanel.tsx:196-200`; the earlier `ThinkingBlock` / `NodeOutputPanel.tsx` no longer exist).

### Thinking limitations

- **OpenAI o-series**: Reasoning summaries are only available to organizations that have completed verification at platform.openai.com. Without verification, `thinking` is `null`.
- **Claude (budget models)**: `max_tokens` must be greater than `thinkingBudget`; the provider raises `max_tokens` to `budget + 1024` when it is not. Temperature is automatically set to 1 when thinking is enabled.
- **Claude (adaptive models)**: `thinkingBudget` is ignored — the model picks its own depth. No sampling parameters are sent; a user-set temperature is silently dropped for these models rather than rejected.
- **Groq**: Only the Qwen3 family (`qwen/qwen3.8-27b` today; qwen3-32b was retired) supports format-based reasoning. Format `hidden` suppresses reasoning output. GPT-OSS models take `reasoning_effort`.
- **Cerebras**: No curated thinking model since `zai-glm-4.7` left the public endpoints; `thinking_budget` support stays in the provider for when one returns.

See [memory_compaction.md](memory_compaction.md) for how thinking token counts are tracked separately from output tokens.

## Model Max Tokens Resolution

`resolve_max_tokens()` in `services/llm/config.py` implements the clamp/default logic:

```python
def resolve_max_tokens(params: dict, model: str, provider: str) -> int:
    registry = get_model_registry()
    model_max = registry.get_max_output_tokens(model, provider)
    user_val = params.get("max_tokens")   # snake_case only; the camelCase fallback is gone
    if user_val:
        user_int = int(user_val)
        if user_int > model_max:
            return model_max   # clamp user value to model hard limit
        return user_int
    return model_max
```

Paired with `ModelRegistryService` (`server/services/model_registry.py`), which loads `model_registry.json` (cached from OpenRouter's `/api/v1/models` endpoint) plus the models registered from the user's own servers (`DATA_DIR/local_models.json`; they win lookups), and falls back to `llm_defaults.json` for unknown models. LiteLLM's table (`DATA_DIR/litellm_models.json`) is read only when a generic endpoint is saved; its figures reach lookups through those registered entries. The registry is the single source for:

- `max_output_tokens`
- `context_length`
- `temperature_range`
- `is_reasoning_model` (fixes temperature to 1.0)
- `supports_thinking`
- `thinking_type`

## Proxy-Based Authentication (Ollama Pattern)

AI providers support optional proxy-based authentication — requests route through a local proxy server that handles auth, following the [Ollama Claude Code integration](https://docs.ollama.com/integrations/claude-code) pattern.

**How it works:**
1. User configures a proxy URL in the Credentials Modal (e.g., `http://localhost:11434`).
2. Requests route through the proxy instead of directly to the provider API.
3. The stored key is sent as stored. A keyless server gets the placeholder its
   vendor documents (`auth.placeholder_key`); no provider rewrites a key, and an
   empty key never reaches an SDK (it would read `OPENAI_API_KEY` and send it to
   the proxy — RFC-0003 D7).
4. Auth can be delegated to the proxy; OpenCompany then stores no real key.

**Configuration:** proxy URLs are stored in the credentials DB under the `{provider}_proxy` pattern (e.g., `anthropic_proxy`, `openai_proxy`). Falls back to direct API key if no proxy configured. This is the SAME mechanism the native Ollama / LM Studio path uses (see "Local LLM Providers" above) — the validator persists the user's server URL under `{provider}_proxy`, and at runtime it carries into `OpenAIProvider`'s `base_url`.

For every chat and agent run, `proxy_url` flows through `ChatUnifier` into the
cached native provider factory.

**Use cases:** Claude Code CLI proxy for Anthropic models; native Ollama / LM Studio support; custom auth proxies; dev/testing with mock servers.

## Provider Default Parameters

Users configure default parameter values per LLM provider in the Credentials Modal; defaults apply to new AI nodes using that provider.

**Configurable parameters:**
- `temperature` — range varies by provider (Anthropic 0-1 on budget-era models, omitted entirely for the adaptive flagships; Cerebras 0-1.5; others 0-2; o-series omitted)
- `max_tokens` (1-200000) — clamped to the model's actual limit by `_resolve_max_tokens()`
- `thinking_enabled` — extended thinking toggle
- `thinking_budget` (1024-16000) — token budget for thinking (Claude budget-era models, Gemini, Cerebras GLM; ignored by adaptive Claude models)
- `reasoning_effort` (low/medium/high) — OpenAI o-series and GPT-5 hybrid reasoning
- `reasoning_format` (parsed/hidden) — Groq Qwen3 models

```python
# server/models/database.py
class ProviderDefaults(SQLModel, table=True):
    provider: str           # openai, anthropic, gemini, groq, openrouter, cerebras
    default_model: str      # per-provider default model ("" = use llm_defaults.json)
    temperature: float
    max_tokens: int
    thinking_enabled: bool
    thinking_budget: int
    reasoning_effort: str   # low, medium, high
    reasoning_format: str   # parsed, hidden
```

| File | Description |
|------|-------------|
| `server/models/database.py` | `ProviderDefaults` SQLModel |
| `server/core/database.py` | `get_provider_defaults()`, `save_provider_defaults()` CRUD |
| `server/services/settings/handlers.py` | `handle_get_provider_defaults` (line 73), `handle_save_provider_defaults` (line 108) — registered into `MESSAGE_HANDLERS` by `routers/websocket.py` |
| `client/src/hooks/useApiKeys.ts` | `getProviderDefaults()`, `saveProviderDefaults()` methods |
| `client/src/components/credentials/sections/ProviderDefaultsSection.tsx` | Default Parameters UI section (the root `CredentialsModal.tsx` is a one-line re-export of `credentials/CredentialsModal.tsx`) |

## Adding a New Provider

> **Post-Wave-11 authoring.** A chat-model provider is a self-contained folder under `server/nodes/model/<provider>_chat_model/` with `__init__.py` declaring a `ChatModelBase` subclass. It auto-registers via `BaseNode.__init_subclass__`; the frontend renders it through `SquareNode` from the emitted NodeSpec with **zero TypeScript changes**. There is no `client/src/nodeDefinitions/`, no `ModelNode.tsx`, no `Dashboard.tsx` switch to edit.

```python
# server/nodes/model/openrouter_chat_model/__init__.py
class OpenRouterChatModel(ChatModelBase):
    type = "openrouterChatModel"
    display_name = "OpenRouter"
    component_kind = "model"          # routes to SquareNode via COMPONENT_BY_KIND

    class Params(ChatModelBase.Params):
        # provider-specific overrides; everything else inherits from ChatModelBase
        ...
```

Icon and color are NOT class attributes (removed in F1): drop `icon.svg` in
the plugin folder or add a `visuals.json` entry (`"openrouterChatModel":
{"icon": "lobehub:openrouter"}`), and put the color in the folder's
`meta.json`.

**Backend steps:**

1. **OpenAI-compatible provider** (DeepSeek, Kimi, Mistral, Sarvam pattern):
   - Add an entry to `llm_defaults.json` with `base_url`, `default_model`, `detection_patterns`, `max_output_tokens._default`, `context_length._default`, `temperature_range`.
   - Add the provider name to the compat list in `services/llm/providers/_compat.py` — its import loop calls `register_provider(ProviderSpec(...))` for every listed name.
   - Chat and agent calls both use the native OpenAI SDK with that configured
     `base_url`; do not add a branch in `AIService`.
   - **If the endpoint has no `/v1/models` route**, set `"supports_model_listing": false` on the JSON block. `OpenAIProvider.fetch_models` then serves the curated list (`popular_models`, else the `max_output_tokens` keys) and validates the key with a one-token completion instead. Without the flag the 404 surfaces as an `openai.OpenAIError`, which `ChatUnifier` converts to `NodeUserError` and `AIService.fetch_models` re-raises **before** its curated fallback — breaking credential validation and the model dropdown for a valid key. Sarvam is the reference case; the flag defaults to `true` so no other provider is affected. Locked by `tests/llm/test_model_listing_fallback.py`.

2. **Custom-SDK provider** (Anthropic, Gemini pattern):
   - Create `services/llm/providers/<name>.py` implementing the `LLMProvider` protocol.
   - At module bottom call `register_provider(ProviderSpec(name=..., factory=..., sdk_exception_refs=("sdk_module:ErrorClass",)))` and add the module to the side-effect imports in `services/llm/providers/__init__.py`.
   - Keep the SDK import inside provider construction or call methods. The eager provider-module import registers the spec; the factory and string exception refs keep SDK loading and client construction lazy (locked by `tests/llm/test_lazy_sdk_imports.py`).
   - Add a config entry in `llm_defaults.json` (no `base_url` needed if the SDK handles URLs itself).
   - There is no `factory.py` to touch — the legacy factory module was removed; registration via `register_provider` is the only entry point.

3. **Credentials + agent exposure:**
   - Add a `Credential` subclass in `server/nodes/model/_credentials.py` — surfaces in the Credentials Modal automatically.
   - The **agent dropdown** picks it up with no edit: the `aiProviders` loader lists every registered provider. Add its substring to `detect_ai_provider` in `server/constants.py` for its chat-model node — otherwise that node silently falls back to `'openai'`.

A **server the user runs** is not a new provider: add it as a named endpoint in the Credentials Modal (see "Named OpenAI-compatible endpoints").

### Key implementation files

| File | Purpose |
|---|---|
| `server/nodes/model/<provider>_chat_model/__init__.py` | Plugin entry — metadata + Params + auto-registers |
| `server/nodes/model/_credentials.py` | `Credential` subclass per provider |
| `server/config/llm_defaults.json` | OpenAI-compatible `base_url` values plus supported parameters and model constraints; dedicated SDK providers may use SDK defaults, and OpenRouter owns its gateway fallback in Python |
| `server/services/llm/providers/<provider>.py` | Native SDK provider (Protocol-based) + `register_provider(ProviderSpec)` at module bottom |
| `server/services/llm/registry.py` | `ProviderSpec` / `register_provider` — the provider registration contract |
| `server/services/llm/unifier.py` | `ChatUnifier` — chat dispatch facade + typed-error translation |
| `server/services/agent_runtime.py` | Shared native agent step/loop and `AgentToolSpec` |
| `server/services/ai.py` | Node orchestration (`execute_chat` / `execute_agent` / `execute_chat_agent`); every provider call goes through `ChatUnifier` — the legacy factory is gone (see "Registry, Unifier, and Lazy SDK Clients") |
| `client/src/Dashboard.tsx` | Generic `COMPONENT_BY_KIND` dispatch — no per-provider entry needed |

## Related Docs

- [DESIGN.md](DESIGN.md) - overall backend architecture
- [memory_compaction.md](memory_compaction.md) - token tracking and compaction using this layer
- [pricing_service.md](pricing_service.md) - cost calculation from `LLMResponse.usage`
- [agent_architecture.md](agent_architecture.md) - agent execution architecture

## Temporal migration and dependency lifecycle

`agent.prepare_payload` builds the payload; there is one engine and one wire
standard. The `llm_engine` / `message_wire_version` discriminators and the
`InvalidAgentLLMEngine` refusal path from the cutover period were purged, and
`tests/llm/test_single_wire_standard.py` fails the build if any of those
identifiers reappear in production source. Pre-cutover deployments are handled
by Reset, which starts a fresh generation.

`tests/llm/test_langchain_removed.py` enforces the dependency end state via an
AST walk over production sources, the `pyproject.toml` declarations, runtime
importability, and a guarded `core.container` boot.


## Multimodal image input (first increment)

Image input rides the same normalized protocol rather than a parallel path.
`ContentBlock` carries an optional `source` dict, discriminated by `kind`:
durable `{"kind": "file_ref", "ref": <FileRef kind=image>, "detail": ...}`
(~450 B) or transient `{"kind": "bytes", "media_type", "data_b64"}`. The wire
codec's `_durable_source` **raises** on a bytes-kind source, so hydrated
request material can never enter the journal or a Temporal payload — the
never-bytes rule from [media_transport.md](media_transport.md), enforced
structurally.

Flow: a tool result opts in with `llm_media: [{ref, detail}]` (max 8 entries,
png/jpeg/webp/gif, `ref.workflow_id` required). Both agent loops attach
ref-only image blocks to the tool message. `run_native_llm_step` calls
`services/llm/media.py::hydrate_image_blocks` once per step, on deep copies:
bytes are read through the contained media reader, fitted to a visual-token
budget (`services/media/image_fit.py`, detail low/auto/high -> small/normal/
large), and dropped after the HTTP call. Originals never mutate.

**Capability gate**: `provider_supports_vision` reads
`llm_defaults.json providers.<p>.vision.enabled`; unknown or absent means
False and the model gets a text placeholder instead — never emit a block a
model is not confirmed to accept, because a rejected block would be journaled
and resent every turn. **Encoder status**: Anthropic renders hydrated images
inside `tool_result.content` (the documented block-list shape). OpenAI
(Responses `input_image` inside `function_call_output`; Chat Completions
hoist-to-user-turn) and Gemini (`inline_data` parts beside
`function_response`) are pending — their `vision.enabled` stays `false` until
those encoders land, so bytes are never hydrated only to be dropped.
Text-only providers keep the `visionAnalyze` delegate tool as their vision
path (see [data_node.md](data_node.md)). Locked by
`tests/llm/test_media_blocks.py`.
