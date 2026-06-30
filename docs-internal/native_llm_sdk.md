# Native LLM SDK Architecture

> **Authoring model (post-Wave-11):** each chat-model node is a self-contained Python plugin folder under `server/nodes/model/<provider>_chat_model/` that emits a `NodeSpec`. The frontend reads specs via [client/src/lib/nodeSpec.ts](../client/src/lib/nodeSpec.ts) + [adapters/nodeSpecToDescription.ts](../client/src/adapters/nodeSpecToDescription.ts) and renders through `SquareNode` with zero TS edits. See [plugin_system.md](./plugin_system.md) and [server/nodes/README.md](../server/nodes/README.md) for the plugin model, and "Adding a New Provider" below for the chat-model recipe.

MachinaOS uses a hybrid LLM architecture: a native SDK layer in `server/services/llm/` for direct chat completions and model fetching, and a LangChain-backed agent loop in `server/services/ai.py` (the plain `_run_agent_loop` async function) for agent tool-calling. This document describes the native layer, its design, and how both paths coexist.

## Why a Native Layer

LangChain added a useful abstraction early on, but it introduced three recurring problems:

1. **Windows/Python 3.13 hangs**: `langchain_google_genai` deadlocks on gRPC init. The native Gemini path bypasses LangChain entirely and uses `google.genai.Client` directly.
2. **Parameter translation loss**: LangChain's `max_completion_tokens` rewrite breaks OpenAI-compatible providers (DeepSeek, Kimi, Mistral) that expect `max_tokens`.
3. **Hard-coded URLs**: Each provider class in LangChain hard-codes its base URL. The native layer reads `base_url` from `server/config/llm_defaults.json`, so adding an OpenAI-compatible provider is a config change.

The native layer also gives a single normalized response shape (`LLMResponse`) across providers, which simplifies downstream code for token tracking, cost calculation, and thinking extraction.

## Layer Overview

```
server/services/llm/
|-- __init__.py           Public API exports
|-- protocol.py           Message, ToolDef, ToolCall, Usage, LLMResponse, LLMProvider (Protocol)
|-- config.py             ProviderConfig, PROVIDER_CONFIGS (built from llm_defaults.json)
|-- factory.py            create_provider() lazy-import factory, NATIVE_PROVIDERS set
|-- messages.py           filter_empty_messages, is_valid_message_content
`-- providers/
    |-- __init__.py
    |-- anthropic.py      AnthropicProvider (anthropic SDK)
    |-- openai.py         OpenAIProvider (openai SDK)
    |-- gemini.py         GeminiProvider (google-genai SDK)
    `-- openrouter.py     OpenRouterProvider (extends OpenAIProvider with headers)
```

## Supported Providers

The native layer currently supports **12 providers**, grouped by implementation:

| Provider | Implementation | SDK | Notes |
|---|---|---|---|
| `anthropic` | `providers/anthropic.py` | `anthropic` | Extended thinking via `budget_tokens` |
| `openai` | `providers/openai.py` | `openai` | Reasoning models (o1/o3) and GPT-5 hybrid thinking |
| `gemini` | `providers/gemini.py` | `google-genai` | Bypasses LangChain (Windows hang fix) |
| `openrouter` | `providers/openrouter.py` | `openai` | Sets `HTTP-Referer` + `X-Title` headers |
| `xai` | `providers/openai.py` + base_url | `openai` | OpenAI-compatible at `api.x.ai/v1` |
| `deepseek` | `providers/openai.py` + base_url | `openai` | OpenAI-compatible at `api.deepseek.com` |
| `kimi` | `providers/openai.py` + base_url | `openai` | Moonshot AI, OpenAI-compatible |
| `mistral` | `providers/openai.py` + base_url | `openai` | OpenAI-compatible |
| `ollama` | `providers/openai.py` + `{provider}_proxy` URL | `openai` (chat) + `ollama` (probe) | Local server. Validator probes via `ollama.AsyncClient.ps()` for typed `context_length` per loaded model. Runtime uses `OpenAIProvider` with `base_url={user URL}` so traffic stays on `localhost`. |
| `lmstudio` | `providers/openai.py` + `{provider}_proxy` URL | `openai` (chat) + `lmstudio` (probe) | Local server. Validator probes via `lmstudio.AsyncClient.llm.list_loaded()` for typed `LlmInstanceInfo.context_length`. Same OpenAI-compat runtime path as Ollama. |
| `groq` | LangChain fallback | `langchain-groq` | Not yet on native path |
| `cerebras` | LangChain fallback | `langchain-cerebras` | Not yet on native path |

Source of truth for this list: `server/config/llm_defaults.json` (the `providers` dict) and `server/services/llm/factory.py` (`NATIVE_PROVIDERS` constant).

### Native chat path vs agent dropdown — two different counts

- **Native chat path (`execute_chat` / `fetch_models`) supports 12 providers** (the 10 OpenAI-compat + Anthropic/Gemini natives above; Groq/Cerebras fall back to LangChain inside `execute_chat`). `xai` lives here.
- **The agent dropdown exposes 11 providers** for `aiAgent`, `chatAgent` (Zeenie), and all specialized agents — the `provider` Literal in [`nodes/agent/ai_agent/__init__.py`](../server/nodes/agent/ai_agent/__init__.py), [`chat_agent.py`](../server/nodes/agent/chat_agent/__init__.py), and [`_specialized.py`](../server/nodes/agent/_specialized.py): `openai`, `anthropic`, `gemini`, `openrouter`, `groq`, `cerebras`, `deepseek`, `kimi`, `mistral`, `ollama`, `lmstudio`. **`xai` is native-chat-only and is NOT in the agent Literal** (no agent dropdown entry).
- Groq and Cerebras are available as standalone chat-model nodes AND in the agent dropdown, but use the LangChain path for both chat and agent execution.

### Provider / model reference table

`ModelRegistryService` (`server/services/model_registry.py`) manages the per-model constraints below — fetching from OpenRouter for cloud models and from the user's running local server (`ollama.AsyncClient.ps()` / `lmstudio.AsyncClient.llm.list_loaded()`) for Ollama / LM Studio. Falls back to `llm_defaults.json` only when neither source is available.

| Provider | Key Models | Context | Max Output | Thinking | Temp Range |
|----------|-----------|---------|-----------|----------|------------|
| **OpenAI** | GPT-5.5/5.4/5.2 | 400K–1.05M | 128K | effort (hybrid) | 0-2 |
| **OpenAI** | GPT-4.1/4.1-mini/4.1-nano | 1M | 32K | none | 0-2 |
| **OpenAI** | o3, o4-mini | 200K | 100K | effort (reasoning) | fixed 1.0 |
| **OpenAI** | GPT-4o/4o-mini | 128K | 16K | none | 0-2 |
| **Anthropic** | Claude Fable 5 | 1M | 128K | budget | 0-1 |
| **Anthropic** | Claude Opus 4.8/4.7 | 1M | 128K | budget | 0-1 |
| **Anthropic** | Claude Sonnet 4.6 | 1M | 64K | budget | 0-1 |
| **Anthropic** | Claude Haiku 4.5 | 200K | 64K | budget | 0-1 |
| **Google** | Gemini 3.5-flash, 3.1-pro/flash-lite, 3-flash, 2.5-pro/flash/flash-lite | 1M | 64K | budget | 0-2 |
| **DeepSeek** | deepseek-v4-flash, deepseek-v4-pro (deepseek-chat/reasoner legacy) | 1M | 64K | thinking modes | 0-2 |
| **Kimi** | kimi-k2.6, kimi-k2.5, kimi-k2.7-code | 256K | 96K | on by default (disabled for agents) | fixed 0.6 |
| **Mistral** | mistral-large/medium/small-latest, codestral-latest | 256K | 131K | none | 0-1.5 |
| **Groq** | Llama 3.3-70b, Llama 3.1-8b, GPT-OSS-120b/20b, Qwen3-32b | 131K | 8-131K | format (Qwen3) | 0-2 |
| **OpenRouter** | 200+ models from multiple providers | varies | varies | varies | 0-2 |
| **Cerebras** | GPT-OSS-120b, Qwen-3-235b, GLM-4.7 | 131K | 65K | budget (Qwen) | 0-1.5 |
| **Ollama** | Whatever the user has pulled (qwen2.5, llama3.x, phi-3, deepseek-r1, ...) | per-loaded-model (typed via `ps()`) | ctx ÷ 4 (capped 4096) | none (per-model) | 0-2 |
| **LM Studio** | Whatever the user has loaded in the LM Studio UI | per-loaded-model (typed via `LlmInstanceInfo.context_length`) | ctx ÷ 4 (capped 4096) | none (per-model) | 0-2 |

`_resolve_max_tokens()` in `server/services/ai.py` (a thin wrapper over `services/llm/config.py::resolve_max_tokens`) clamps user-requested `max_tokens` to the model's actual limit.

## Local LLM Providers (Ollama, LM Studio)

Ollama and LM Studio expose an OpenAI-compatible `/v1` HTTP API, so they ride the same `OpenAIProvider` runtime path used by every other OpenAI-compat backend (DeepSeek, Kimi, Mistral). The differences are the **base URL** (the user enters their server's address, e.g. `http://localhost:11434/v1`) and the **per-model parameters** (which depend on what the user has loaded in the local server's UI, not a JSON default).

**Probe layer** ([`server/nodes/model/_local_validator.py`](../server/nodes/model/_local_validator.py)) — when the user clicks "Fetch" in the Credentials Modal:

1. The user's URL is persisted under the existing `{provider}_proxy` credential — same key the OpenAI-style auth-delegation pattern already uses to override `base_url` at runtime.
2. The validator probes via the **official SDK** (`ollama>=0.6.0`, `lmstudio>=1.5.0`) — never raw httpx, never Modelfile-parameters parsing:
   - **Ollama**: `ollama.AsyncClient.ps()` returns `ProcessResponse.Model` per loaded model with typed `context_length` + typed `ModelDetails` (`family`, `parameter_size`, `quantization_level`, `format`).
   - **LM Studio**: `lmstudio.AsyncClient.llm.list_loaded()` returns `AsyncModelHandle` per loaded model; `handle.get_info()` is a typed `LlmInstanceInfo` (`context_length`, `max_context_length`, `vision`, `trained_for_tool_use`, `architecture`, `params_string`, `format`).
3. The probed params are persisted in two places:
   - `EncryptedAPIKey.models["model_params"]` — the same JSON column as the model list, sibling key. Survives DB-backed restart of the validator state.
   - `model_registry.register_local_model()` — populates a `ModelInfo` entry under `<provider>/<model_id>` and writes through to `model_registry.json`. The sync `get_context_length()` / `get_max_output_tokens()` lookups find this entry first, so chat / agent execution honour the **real n_ctx the server is currently serving** instead of a JSON guess. Capability flags (`tools`, `vision`) flow through `ModelInfo.supported_parameters`.

**Both servers must have a model loaded** for the probe to return entries. The validator's "no models loaded" message is symmetric across providers.

**Runtime path** — `execute_chat()` and `execute_agent()` read `{provider}_proxy` via `auth.get_api_key()` and pass it as `proxy_url` to `create_provider()`. `OpenAIProvider.__init__` overrides `kwargs["base_url"]` with the user's URL and forces `api_key="ollama"` (the documented placeholder for unauthenticated local servers). The openai SDK then sends `POST {user_url}/chat/completions` — **traffic stays on the user's machine, never reaches api.openai.com**.

**Provider detection** ([`server/constants.py:detect_ai_provider`](../server/constants.py)) MUST list `ollama` / `lmstudio` substrings, and the agent dropdown's `provider` Literal in [`ai_agent.py`](../server/nodes/agent/ai_agent/__init__.py) / [`chat_agent.py`](../server/nodes/agent/chat_agent/__init__.py) / [`_specialized.py`](../server/nodes/agent/_specialized.py) MUST include `"ollama"` / `"lmstudio"` — otherwise the chat-model node silently falls through to `'openai'` and the runtime calls the OpenAI cloud with the local-server placeholder key.

**Open-world skip in `is_model_valid_for_provider`** — local model names like `qwen/qwen3.6-27b` don't contain provider substrings, so the cloud-style pattern check would always reject them. The function returns `True` for `openrouter` / `ollama` / `lmstudio` without consulting `detection_patterns`. The upstream server still rejects genuinely missing models with a clear 404.

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
```

Downstream code never special-cases a provider SDK shape.

## Factory and Lazy Imports

Each provider is imported only when first used to avoid loading large SDKs at startup:

```python
# server/services/llm/factory.py
def create_provider(provider: str, api_key: str, *, proxy_url: Optional[str] = None) -> LLMProvider:
    if provider == "anthropic":
        from services.llm.providers.anthropic import AnthropicProvider
        return AnthropicProvider(api_key, proxy_url=proxy_url)

    if provider == "openai":
        from services.llm.providers.openai import OpenAIProvider
        return OpenAIProvider(api_key, proxy_url=proxy_url)

    if provider == "gemini":
        from services.llm.providers.gemini import GeminiProvider
        return GeminiProvider(api_key, proxy_url=proxy_url)

    if provider == "openrouter":
        from services.llm.providers.openrouter import OpenRouterProvider
        return OpenRouterProvider(api_key, proxy_url=proxy_url)

    # OpenAI-compatible providers: reuse OpenAIProvider with base_url from config
    from services.llm.config import get_provider_config
    config = get_provider_config(provider)
    if config and config.base_url:
        from services.llm.providers.openai import OpenAIProvider
        return OpenAIProvider(api_key, base_url=config.base_url, proxy_url=proxy_url)

    raise ValueError(f"Unknown provider: {provider}")
```

`is_native_provider(name)` is the gate used by `AIService.execute_chat()` to choose the native path vs LangChain fallback.

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

Adding a new OpenAI-compatible provider is a pure config change:

```json
"deepseek": {
  "default_model": "deepseek-v4-flash",
  "detection_patterns": ["deepseek"],
  "models_endpoint": "https://api.deepseek.com/v1/models",
  "base_url": "https://api.deepseek.com/v1",
  "max_output_tokens": { "_default": 8192 },
  "context_length": { "_default": 128000 },
  "temperature_range": [0.0, 2.0]
}
```

No new provider class, no new import. The factory's fallback branch picks up `base_url` and reuses `OpenAIProvider`.

## Native Path vs LangChain Path

`AIService` in `server/services/ai.py` has two top-level execution methods. They take different routes:

```
execute_chat(parameters, node_id, node_type)          -> native SDK where possible
execute_agent(parameters, node_id)                    -> LangChain chat model + _run_agent_loop
execute_chat_agent(parameters, node_id)               -> LangChain chat model + _run_agent_loop
```

**`execute_chat()` routing:**

```
node_type / model selects provider
        |
        v
is_native_provider(provider)?
        |
        +-- yes --> create_provider(...) -> provider.chat(...) -> LLMResponse
        |
        +-- no  --> self.create_model(...) -> chat_model.invoke(...) -> extract to LLMResponse shape
```

**`execute_agent()` and `execute_chat_agent()` routing:**

All providers go through `create_model()` which returns a LangChain `ChatOpenAI` / `ChatAnthropic` / `ChatGoogleGenerativeAI` / `ChatGroq` / `ChatCerebras` instance. The agent then calls `chat_model.bind_tools(tools)` and runs `_run_agent_loop` (plain async while loop, ~140 LOC). LangChain's chat-model classes are used because they unify `bind_tools` + `usage_metadata` + `content_blocks` extraction across all providers; the native-SDK layer doesn't yet have provider-unified tool binding.

OpenAI-compatible providers like DeepSeek/Kimi/Mistral still use `ChatOpenAI` with `base_url` from `llm_defaults.json` in the agent path, and pass `max_tokens` via `extra_body` to bypass LangChain's `max_completion_tokens` conversion.

## Thinking and Reasoning

The unified `ThinkingConfig` dataclass is translated per provider:

```python
@dataclass
class ThinkingConfig:
    enabled: bool = False
    budget: int = 2048      # Anthropic budget_tokens, Gemini 2.5 thinking_budget
    effort: str = "medium"  # OpenAI reasoning_effort (low/medium/high)
    level: str = "medium"   # Gemini 3+ thinking_level
    format: str = "parsed"  # Groq Qwen3 reasoning_format (parsed/hidden)
```

Each provider's `chat()` method reads only the fields it supports. The extracted thinking text is returned in `LLMResponse.thinking` so downstream nodes can display or drag it into parameters.

### Supported thinking/reasoning providers

| Provider | Models | Parameter | Thinking Type | Notes |
|----------|--------|-----------|---------------|-------|
| **Claude** | All Claude 4.x/3.5 | `thinkingBudget` (1024-16000 tokens) | budget | Requires `max_tokens > budget_tokens`. Temperature auto-set to 1. |
| **Gemini** | gemini-3.x, gemini-2.5-pro/flash | `thinkingBudget` (token count) | budget | Uses `thinking_budget` API parameter |
| **OpenAI** | o1, o3, o3-mini, o4-mini | `reasoningEffort` (low/medium/high) | effort | Reasoning-only models. Temperature fixed at 1.0. |
| **OpenAI** | GPT-5.2/5.1/5/5-mini/5-nano | `reasoningEffort` (low/medium/high/xhigh) | effort | Hybrid reasoning: can operate with or without thinking. |
| **Groq** | qwen3-32b | `reasoningFormat` ('parsed' or 'hidden') | format | 'parsed' returns reasoning, 'hidden' returns only final answer |
| **Cerebras** | qwen-3-235b | `reasoningFormat` ('parsed' or 'hidden') | format | Same format-based reasoning as Groq Qwen |

The thinking/reasoning fields (`thinkingEnabled`, `thinkingBudget`, `reasoningEffort`, `reasoningFormat`) live in the backend NodeSpec for each chat model (`server/nodes/model/<provider>_chat_model/`) and are surfaced through `AIChatModelParams` in `server/models/nodes.py`. The frontend renders them automatically via the universal parameter panel.

### Thinking extraction (LangChain path)

On the LangChain path, `extract_thinking_from_response(response, provider)` in `server/services/ai.py` pulls reasoning text out of provider-specific response shapes:

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

The `thinking` field is exposed to downstream nodes via the backend output schema (`AIAgentOutput` in `server/services/node_output_schemas.py`, shared across every LLM-backed agent + chat model) and rendered in a collapsible `ThinkingBlock` (`client/src/components/ui/NodeOutputPanel.tsx`, default-expanded, provider-aware label).

### Thinking limitations

- **OpenAI o-series**: Reasoning summaries are only available to organizations that have completed verification at platform.openai.com. Without verification, `thinking` is `null`.
- **Claude**: `max_tokens` must be greater than `thinkingBudget`. Temperature is automatically set to 1 when thinking is enabled.
- **Groq**: Only Qwen3-32b supports reasoning (QwQ removed from Groq). Format `hidden` suppresses reasoning output.
- **Cerebras**: Qwen-3-235b supports format-based reasoning (same as Groq Qwen).

See [memory_compaction.md](memory_compaction.md) for how thinking token counts are tracked separately from output tokens.

## Model Max Tokens Resolution

`resolve_max_tokens()` in `services/llm/config.py` implements the clamp/default logic:

```python
def resolve_max_tokens(params: dict, model: str, provider: str) -> int:
    registry = get_model_registry()
    model_max = registry.get_max_output_tokens(model, provider)
    user_val = params.get("max_tokens") or params.get("maxTokens")
    if user_val:
        user_int = int(user_val)
        if user_int > model_max:
            return model_max   # clamp user value to model hard limit
        return user_int
    return model_max
```

Paired with `ModelRegistryService` (`server/services/model_registry.py`), which loads `model_registry.json` (cached from OpenRouter's `/api/v1/models` endpoint) and falls back to `llm_defaults.json` for unknown models. The registry is the single source for:

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
3. Proxy handles authentication (token set to `"ollama"` automatically).
4. No API key storage needed in MachinaOs — auth delegated to proxy.

**Configuration:** proxy URLs are stored in the credentials DB under the `{provider}_proxy` pattern (e.g., `anthropic_proxy`, `openai_proxy`). Falls back to direct API key if no proxy configured. This is the SAME mechanism the native Ollama / LM Studio path uses (see "Local LLM Providers" above) — the validator persists the user's server URL under `{provider}_proxy`, and at runtime it carries into `OpenAIProvider`'s `base_url`.

**Native path:** `create_provider(name, api_key, proxy_url=url)`. **LangChain path:** `create_model()` sets `base_url`:

```python
def create_model(self, provider: str, api_key: str, model: str,
                temperature: float, max_tokens: int,
                thinking: Optional[ThinkingConfig] = None,
                proxy_url: Optional[str] = None):
    # ...
    if proxy_url:
        kwargs['base_url'] = proxy_url
        kwargs[config.api_key_param] = "ollama"  # Ollama-style placeholder token
```

**Use cases:** Claude Code CLI proxy for Anthropic models; native Ollama / LM Studio support; custom auth proxies; dev/testing with mock servers.

## Provider Default Parameters

Users configure default parameter values per LLM provider in the Credentials Modal; defaults apply to new AI nodes using that provider.

**Configurable parameters:**
- `temperature` — range varies by provider (Anthropic 0-1, Cerebras 0-1.5, others 0-2; o-series fixed 1.0)
- `max_tokens` (1-200000) — clamped to the model's actual limit by `_resolve_max_tokens()`
- `thinking_enabled` — extended thinking toggle
- `thinking_budget` (1024-16000) — token budget for thinking (Claude, Gemini)
- `reasoning_effort` (low/medium/high) — OpenAI o-series and GPT-5 hybrid reasoning
- `reasoning_format` (parsed/hidden) — Groq Qwen3 models

```python
# server/models/database.py
class ProviderDefaults(SQLModel, table=True):
    provider: str           # openai, anthropic, gemini, groq, openrouter, cerebras
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
| `server/routers/websocket.py` | `get_provider_defaults`, `save_provider_defaults` handlers |
| `client/src/hooks/useApiKeys.ts` | `getProviderDefaults()`, `saveProviderDefaults()` methods |
| `client/src/components/CredentialsModal.tsx` | Default Parameters UI section |

## Adding a New Provider

> **Post-Wave-11 authoring.** A chat-model provider is a self-contained folder under `server/nodes/model/<provider>_chat_model/` with `__init__.py` declaring a `ChatModelBase` subclass. It auto-registers via `BaseNode.__init_subclass__`; the frontend renders it through `SquareNode` from the emitted NodeSpec with **zero TypeScript changes**. There is no `client/src/nodeDefinitions/`, no `ModelNode.tsx`, no `Dashboard.tsx` switch to edit.

```python
# server/nodes/model/openrouter_chat_model/__init__.py
class OpenRouterChatModel(ChatModelBase):
    type = "openrouterChatModel"
    metadata = NodeMetadata(
        display_name="OpenRouter",
        icon="lobehub:openrouter",   # asset:<key>, lobehub:<brand>, or emoji
        color="#6366F1",
        component_kind="model",       # routes to SquareNode in Dashboard.tsx
    )

    class Params(ChatModelBase.Params):
        # provider-specific overrides; everything else inherits from ChatModelBase
        ...
```

**Backend steps:**

1. **OpenAI-compatible provider** (DeepSeek, Kimi, Mistral pattern):
   - Add an entry to `llm_defaults.json` with `base_url`, `default_model`, `detection_patterns`, `max_output_tokens._default`, `context_length._default`, `temperature_range`.
   - Add the provider name to `NATIVE_PROVIDERS` in `factory.py` if you want `execute_chat()` to route natively.
   - The LangChain agent path needs no Python branching — `create_model()` reuses `ChatOpenAI` with `base_url` from `llm_defaults.json`.

2. **Custom-SDK provider** (Anthropic, Gemini pattern):
   - Create `services/llm/providers/<name>.py` implementing the `LLMProvider` protocol.
   - Add a branch in `create_provider()` in `factory.py`.
   - Add the provider name to the dedicated-provider set and `NATIVE_PROVIDERS`.
   - Add a config entry in `llm_defaults.json` (no `base_url` needed if the SDK handles URLs itself).

3. **Credentials + agent exposure:**
   - Add a `Credential` subclass in `server/nodes/model/_credentials.py` — surfaces in the Credentials Modal automatically.
   - To expose the provider in the **agent dropdown**, add its name to the `provider` Literal in `nodes/agent/ai_agent/__init__.py`, `chat_agent.py`, AND `_specialized.py`, and add the substring to `detect_ai_provider` in `server/constants.py` — otherwise an agent using it silently falls back to `'openai'`.

### Key implementation files

| File | Purpose |
|---|---|
| `server/nodes/model/<provider>_chat_model/__init__.py` | Plugin entry — metadata + Params + auto-registers |
| `server/nodes/model/_credentials.py` | `Credential` subclass per provider |
| `server/config/llm_defaults.json` | base_url + supported_params + temperature constraints (no hardcoded URLs in Python) |
| `server/services/llm/providers/<provider>.py` | Native SDK provider (Protocol-based) |
| `server/services/ai.py` | Routing: native chat path + LangChain agent path |
| `client/src/Dashboard.tsx` | Generic `COMPONENT_BY_KIND` dispatch — no per-provider entry needed |

## Related Docs

- [DESIGN.md](DESIGN.md) - overall backend architecture
- [memory_compaction.md](memory_compaction.md) - token tracking and compaction using this layer
- [pricing_service.md](pricing_service.md) - cost calculation from `LLMResponse.usage`
- [agent_architecture.md](agent_architecture.md) - how agents use LangChain path on top of this layer
