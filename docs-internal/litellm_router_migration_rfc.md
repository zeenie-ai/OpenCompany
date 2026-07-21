# LiteLLM Router Migration RFC

**Status:** Draft · **Owner:** trohitg · **Created:** 2026-07-02 · **Target:** `server/services/llm/` + the chat/agent LLM paths in `server/services/ai.py`

---

## 1. Abstract

OpenCompany maintains a fully custom multi-provider LLM layer: a `ChatUnifier` facade over
12 hand-written provider integrations, plus a parallel LangChain path for the agent loop.
This RFC proposes replacing the provider *internals* with [LiteLLM](https://github.com/BerriAI/litellm)
(`litellm.acompletion`, in-process, exact-pinned, lazy-imported) while keeping every
architectural seam OpenCompany actually relies on: the `ChatUnifier` facade, the
`NodeUserError` error contract, `llm_defaults.json` as the quirk SSOT, our
temperature/max-tokens resolution, `fetch_models`, and the pricing/model-registry/
compaction services.

The migration runs in two flag-gated stages:

- **Stage A — chat path**: swap the 4 dedicated provider classes + 8 OpenAI-compat
  registrations behind `ChatUnifier` for one LiteLLM adapter. Net ~-420 production LOC;
  per-provider quirk knowledge (message reshaping, system-split, role mapping, reasoning
  extraction) is outsourced upstream.
- **Stage B — agent loop**: replace `create_model` + LangChain message shapes in
  `_run_agent_loop` / `execute_chat_agent` / Temporal agent activities with the same
  adapter on protocol types. Deletes the 4-format thinking extractor, the ~20.8s-cold
  `langchain_openai` import class, and 5 dependency pins. Gated on Stage A soak.

LiteLLM's officially announced Rust rewrite (Router Sep 15 2026, full server Dec 1 2026,
client-API stability promised) means adopting the SDK surface now inherits the
performance work later without a second migration.

---

## 2. Current architecture (verified inventory, 2026-07-02)

### 2.1 The two LLM paths

| Path | Entry | Mechanism | Providers |
|---|---|---|---|
| Chat (single completion) | `execute_chat` ([ai.py:1400](../server/services/ai.py#L1400)) → `ChatUnifier.chat()` | Native SDKs behind `ProviderSpec` registry | 12 |
| Agent loop (tool-calling) | `execute_agent` / `execute_chat_agent` ([ai.py:2105](../server/services/ai.py#L2105)) → `_run_agent_loop` ([ai.py:733](../server/services/ai.py#L733)) with `create_model` ([ai.py:1206](../server/services/ai.py#L1206)) | LangChain classes (`ChatOpenAI`, `ChatAnthropic`, `ChatGoogleGenerativeAI`, `ChatGroq`, `ChatCerebras`) + `bind_tools` | 11 |

### 2.2 Size of the custom layer

| Component | LOC | Files |
|---|---|---|
| `services/llm/` core (unifier, registry, protocol, config, factory, messages, vertex) | 796 | unifier 132, config 217, protocol 113, registry 111, factory 75, `__init__` 73, messages 47, vertex 28 |
| `services/llm/providers/` | 905 | anthropic 203, gemini 268, openai 200, openrouter 99, `_compat` 109, `__init__` 26 |
| Provider-path code in `services/ai.py` | ~595 of 2,987 | execute_chat, fetch_models, create_model, 4-format thinking extraction, resolution glue |
| Dedicated tests `tests/llm/` | 1,771 | 11 files (+~480 adjacent chat-path test LOC elsewhere) |
| Hand-curated metadata | ~630 lines | `llm_defaults.json` 291, `pricing.json` 338 (+ `model_registry.json` 10,536-line OpenRouter-refreshed cache) |

**Total: ~2,300 production LOC + ~2,250 test LOC + ~630 lines of hand-maintained JSON**
doing what a maintained OSS library ships as its entire job.

### 2.3 The facade (what stays)

`ChatUnifier` ([services/llm/unifier.py](../server/services/llm/unifier.py), 132 LOC):

- `chat(provider, api_key, messages, model, temperature, max_tokens, thinking, tools) -> LLMResponse`
- `fetch_models(provider, api_key) -> List[str]` with the `incompatible_models` JSON
  filter (unifier.py:95-98)
- Typed SDK exceptions (`ProviderSpec.sdk_exception_types`) → `NodeUserError` at exactly
  two catch sites (unifier.py:78-79, 93-94)
- `{provider}_proxy` credential lookup from the encrypted store (unifier.py:122)
- Wired once into the DI container (`core/container.py`, `_build_chat_unifier`)

Protocol types ([services/llm/protocol.py](../server/services/llm/protocol.py)):
`ThinkingConfig(enabled, budget=2048, effort="medium", level=None, format="parsed")`,
`Message`, `ToolDef`, `ToolCall`, `Usage(input/output/total, cache_creation_tokens,
cache_read_tokens, reasoning_tokens)`, `LLMResponse(content, thinking, tool_calls,
usage, model, finish_reason, raw)`.

Resolution ([services/llm/config.py](../server/services/llm/config.py)):
`resolve_max_tokens` (user → ModelRegistry → llm_defaults → 4096, lines 158-172);
`resolve_temperature` (reasoning → 1.0, anthropic+thinking → 1.0, `fixed_temperature`
prefix match, provider-range clamp, lines 175-206); `detect_provider_from_model`
(fallback → openai); `is_model_valid_for_provider` (open-world for
openrouter/ollama/lmstudio).

### 2.4 The 16 encoded provider quirks

1. Gemini `thinking_level` is None-by-default — forwarding a fabricated default 400s on
   Vertex 2.5-era models (`providers/gemini.py:72-79`).
2. Anthropic auto-bumps `max_tokens` to `budget + 1024` when `max_tokens <= budget`
   (`providers/anthropic.py:59-62` — API-required).
3. Kimi fixed temperature 0.6 for the k2.x product line
   (`llm_defaults.json:227`; prefix-matched so one entry covers the family).
4. Kimi thinks by default (`thinking_default_on`, `llm_defaults.json:226`); explicit
   disable sends `extra_body={"thinking":{"type":"disabled"}}` — agent path only today.
5. OpenAI o-series/gpt-5 use `max_completion_tokens`, not `max_tokens`
   (`providers/openai.py:58-64`).
6. Gemini model dropdown is a curated list (keys of `max_output_tokens`) because Vertex
   rejects `models.list` with API keys (`providers/gemini.py:97-138`,
   `llm_defaults.json:79`).
7. Ollama/LM Studio have no `default_model` — the dropdown reflects user-installed
   models; empty list signals "server offline" (`llm_defaults.json:267-289`).
8. `proxy_url` (from the `{provider}_proxy` encrypted credential) always overrides
   `base_url`; placeholder `api_key="ollama"` when a proxy/local relay is active.
9. Thinking extraction handles 4 response formats (`ai.py:621-730`): LangChain
   `content_blocks`, `additional_kwargs.reasoning_content`, `response_metadata` output
   arrays, raw content list.
10. OpenRouter `[FREE] ` prefix is display-only and stripped before the API call.
11. Provider detection falls back to `openai` on unknown patterns — a documented
    foot-gun class ("401 from OpenAI when I picked LM Studio",
    `constants.py:414-439`).
12. `fixed_temperature` uses prefix matching so one override covers a model family.
13. Reasoning models force temperature 1.0 AND omit the parameter from the API call.
14. Vertex Express keys detected by `"AQ."` prefix (`services/llm/vertex.py:23`) — flips
    google-genai to `vertexai=True`, ignores proxy_url, and is shared with the LangChain
    path (`ai.py:1256`).
15. Compaction ratio precedence: per-session > per-user > env > `llm_defaults.json`.
16. `register_local_model` persists ollama/lmstudio context metadata to
    `model_registry.json` across restarts.

### 2.5 Parity nuances (what "current behavior" actually is)

These matter because parity must be defined against reality, not aspiration:

1. The 4-format thinking extractor is **agent-path only** (callers: `ai.py:792`,
   `ai.py:2481`, `services/temporal/agent_activities.py:119,208`). The chat path already
   reads `LLMResponse.thinking` from the unifier.
2. On the chat path today, groq `reasoning_format` and cerebras `thinking_budget` are
   **silently dropped** — `OpenAIProvider.chat` only applies `reasoning_effort` for
   o-series/gpt-5. Those knobs work only on the LangChain agent path (`ai.py:1336-1347`).
3. Kimi's thinking-disable (`extra_body`) exists **only** in `create_model`
   (`ai.py:1287-1291`), not on the unifier path.
4. `_track_token_usage` ([ai.py:1091](../server/services/ai.py#L1091)) is agent-path
   only and consumes LangChain `usage_metadata`; the chat path does not track tokens
   today. `CompactionService.track()` already accepts `cache_read_tokens` /
   `cache_creation_tokens` / `reasoning_tokens` keys (`compaction.py:133-145`).

---

## 3. Problems with the current layer (Motivation)

### 3.1 Cost of adding provider 13 today

| Step | File | Cost |
|---|---|---|
| Provider config block (base_url, endpoints, token limits, thinking type, temp range, quirks) | `server/config/llm_defaults.json` | ~25-40 lines, every value hand-researched |
| Provider implementation | `_compat.py` (OpenAI-compat) or a new dedicated module (cf. gemini.py = 268 LOC) | 1 line best case; ~250 LOC + a new SDK dependency worst case |
| Detection branch | `constants.py:414-439` — order-sensitive substring matching with a documented bug class | ~5 lines, high foot-gun density |
| Node package | `server/nodes/model/<provider>_chat_model/` | new package (product surface — no library removes this) |
| Pricing entries | `server/config/pricing.json` | hand-copied from the provider's pricing page; goes stale silently |
| Tests | `tests/llm/` | ~50-150 LOC |

LiteLLM removes the implementation row entirely and replaces the hand-curated
token-limit/pricing research with its maintained metadata. The node package and
detection rows remain OpenCompany product surface — migration cuts per-provider cost from
"~250 LOC + research" to "config-only", not to zero.

### 3.2 Drift risk

- Every provider API change is our emergency, discovered in production: o-series
  `max_completion_tokens`, Anthropic thinking temp=1 + budget bump, Vertex rejecting
  unsolicited `thinking_level` — each hand-patched after breakage.
- The 4-format thinking extractor grows a new probe branch per model family
  (gemini-3 `thinking_level`, kimi `thinking_default_on` are recent in-repo examples).
- `llm_defaults.json` carries a `last_updated` field — an explicit admission it is a
  manually refreshed artifact. `pricing.json` carries per-provider `_default` fallbacks
  precisely because entries lag reality. `model_registry.json` (10.5K lines) plus
  `services/model_registry.py` (656 LOC) exist largely to compensate.

### 3.3 Missing capabilities (verified absent)

| Capability | Evidence |
|---|---|
| Retries | `unifier.py:69-79` — a single `try: await client.chat(...)`. One transient 429/503 = user-visible failure. |
| Fallbacks / cooldowns | No fallback logic anywhere in the chat path. |
| Streaming | `execute_chat` awaits one complete response (`ai.py:1483`); no token-streaming path exists for chat nodes. |
| Context-window fallback | `context_length` is declared but nothing routes on overflow — the typed error just surfaces. |
| Maintained cost/metadata map | `pricing.json` hand-edited vs LiteLLM's continuously updated `model_prices_and_context_window.json` + `completion_cost()` + `get_model_info()`. |

### 3.4 Duplicated commodity code

Every line of `services/llm/providers/` re-implements what LiteLLM ships and maintains
weekly for 100+ providers: param normalization, typed exception taxonomy, reasoning
normalization, tool-call normalization, message reshaping (system-split, role mapping).

---

## 4. LiteLLM overview (official sources, fetched 2026-07-02)

### 4.1 What it is

Three usage modes:

1. **SDK** — `litellm.completion()` / `litellm.acompletion()`: in-process, OpenAI-shaped
   request/response for 100+ providers.
2. **Router class** — in-process load balancing across multiple deployments, with
   retries, fallbacks, cooldowns, context-window fallbacks. Requires a pre-declared
   `model_list`.
3. **Proxy server (AI Gateway)** — standalone OpenAI-compatible HTTP service with
   virtual keys, budgets, spend tracking, admin UI; database-backed.

Current: **v1.90.0** (2026-06-26), weekly-ish release cadence, Python >=3.9 (OpenCompany
server pins >=3.11,<3.13 — compatible). License: MIT core with a separately-licensed
`enterprise/` directory (bundled in the wheel; never imported by us — dead weight, not
license risk).

### 4.2 The Rust rewrite (the "Rust-based router" premise, verified)

LiteLLM has an **official, in-progress Rust migration**
([blog](https://docs.litellm.ai/blog/litellm-rust-launch),
[tracking issue #31263](https://github.com/BerriAI/litellm/issues/31263)):

| Date | Milestone |
|---|---|
| Aug 15 2026 | `litellm.ocr()` for Mistral |
| Sep 1 2026 | `/messages` + `/chat/completions` endpoints in Rust |
| Sep 15 2026 | **Router (load balancing, failover) in Rust** |
| Dec 1 2026 | Full server in pure Rust (axum) |

Published targets: 453 → 6,782 RPS throughput, 359MB → 32MB memory, 7.5ms → 0.05ms
per-request overhead, <1ms gateway overhead at ~65MB binary. `config.yaml` and all
client APIs are promised stable through the transition; FastAPI remains the HTTP
terminator for auth/rate-limiting/callbacks in proxy mode.

As of 2026-07-02 the main branch is ~85.9% Python / 0.4% Rust — **the Rust router is not
shipped yet**. The Rust gains land primarily in the *proxy server*; the in-process SDK
call path this RFC adopts is the least-changed, stability-promised surface. Adopting now
positions OpenCompany to inherit the Rust work without a second migration.

Rust-native gateway alternatives considered and rejected (all are sidecars, not
in-process Python SDKs): TensorZero (pure Rust, <1ms P99), Helicone AI Gateway (Rust),
Bifrost (Go, ~11us overhead). See section 5.

### 4.3 Provider coverage for OpenCompany's 12 providers

All 12 are supported. The prefix mapping this RFC adds to `llm_defaults.json`:

| OpenCompany provider | `litellm_prefix` | api_base sent |
|---|---|---|
| openai | `openai` | only if proxy_url set |
| anthropic | `anthropic` | only if proxy_url set |
| gemini (AIza keys) | `gemini` | only if proxy_url set |
| gemini (Vertex "AQ." keys) | `vertex_ai` | never (proxy ignored, as today) |
| openrouter | `openrouter` | always (`https://openrouter.ai/api/v1`) |
| xai / deepseek / mistral / groq / cerebras | same name | always (our JSON base_url) |
| kimi | `moonshot` | always (`https://api.moonshot.ai/v1` — our JSON decides .ai vs .cn) |
| ollama | `openai` | always (`http://localhost:11434/v1`) |
| lmstudio | `openai` | always (`http://localhost:1234/v1`) |

Ollama/LM Studio deliberately route through LiteLLM's `openai/` provider with the
existing `/v1` base URLs rather than the native `ollama/` route: byte-for-byte parity
with today's `_compat.py` behavior, no `/v1`-suffix foot-gun (LiteLLM's native ollama
route expects api_base WITHOUT `/v1`), and `nodes/model/_local_validator.py` stays
untouched.

### 4.4 Feature parity

| Need (from current layer) | LiteLLM answer |
|---|---|
| Reasoning/thinking normalization | `reasoning_effort` + `thinking={type,budget_tokens}` request params; standardized `reasoning_content` (+ Anthropic `thinking_blocks`) in responses ([docs](https://docs.litellm.ai/docs/reasoning_content)) |
| Typed exception translation | OpenAI-style exception hierarchy with `llm_provider` populated — LiteLLM exceptions subclass the OpenAI SDK classes, so the existing `sdk_exception_types -> NodeUserError` catch keeps working with one tuple: `(openai.OpenAIError,)` ([docs](https://docs.litellm.ai/docs/exception_mapping)) |
| Tool-call normalization | OpenAI-shaped `tool_calls` across providers; `modify_params=True` auto-fixes provider message-ordering constraints ([docs](https://docs.litellm.ai/docs/completion/function_call)) |
| Unsupported-param safety | `drop_params=True` global + per-call `additional_drop_params` ([docs](https://docs.litellm.ai/docs/completion/drop_params)) |
| Usage detail | `prompt_tokens_details.cached_tokens`, `completion_tokens_details.reasoning_tokens`, Anthropic `cache_creation_input_tokens` ([docs](https://docs.litellm.ai/docs/completion/prompt_caching)) |
| Streaming (future unlock) | Uniform `stream=True` + async iteration across providers |
| Cost/metadata cross-check | `completion_cost()`, `get_model_info()`, `register_model()` ([docs](https://docs.litellm.ai/docs/completion/token_usage)) |

### 4.5 Known caveats (from official sources)

- ~28MB install; **200MB+ RSS on import**; proxy code ships bundled with the SDK
  ([issue #15262](https://github.com/BerriAI/litellm/issues/15262)). Lazy import is
  mandatory here (section 6.8).
- `fastuuid` transitive dependency can require a Rust toolchain where no wheel matches
  ([issue #14145](https://github.com/BerriAI/litellm/issues/14145)) — Windows wheel
  availability is a Phase 0 gate.
- Weekly releases → exact pinning required; the o-series `max_tokens` →
  `max_completion_tokens` mapping has regressed repeatedly across releases
  ([#8213](https://github.com/BerriAI/litellm/issues/8213),
  [#10066](https://github.com/BerriAI/litellm/issues/10066),
  [PR #13390](https://github.com/BerriAI/litellm/pull/13390)) — we do not depend on it
  (section 6.4).
- SDK telemetry must be disabled for a local-first product: `litellm.telemetry = False`.
- LiteLLM has **no per-account model listing** — `fetch_models` stays ours (section 6.7).

---

## 5. Decision

**Adopt LiteLLM now, in-process, as `litellm.acompletion` behind the retained
`ChatUnifier` facade — chat path first (Stage A), agent loop second (Stage B).**

Scored against OpenCompany's constraints (local-first, single-user, Windows-primary,
2.90s cold-start budget, one API key per provider):

| Option | Effort | Runtime perf | Cold-start | Maintenance saved | Features | Risk | Verdict |
|---|---|---|---|---|---|---|---|
| (a) Status quo | 5 | 4 | 5 | 1 | 1 | 3 | Pays ~2.3K LOC + quirk-chasing tax forever |
| **(b) acompletion behind ChatUnifier** | **4** | 4 | 4 (lazy import) | 4 | 4 | **2 (lowest)** | **Chosen** |
| (c) litellm.Router in-process | 3 | 4 | 3-4 | 4 | 5 | 3 | Rejected for now — see below |
| (d) LiteLLM proxy sidecar | 2 | 3 today → 5 post-Rust | 2 | 4 | 3 | 4 | Wrong topology for a single-user local app; revisit at Rust GA |
| (e) Rust gateway today (TensorZero et al.) | 1 | 5 | 2 | 3 | 3 | 4 | Perf solves a problem we don't have; costs the one we do (integration effort) |
| (f) Wait for Rust GA (Dec 2026) | 5 now | — | — | 1 for ~6 months | 1 | 3 | The SDK surface we'd adopt is the stability-promised one; waiting buys ~nothing |

Why not the Router class (c), specifically:

- Router's core value is load balancing across multiple deployments per model — OpenCompany
  has one key per provider; the machinery is structurally unused.
- Router requires a pre-declared `model_list`; OpenCompany model names are dynamic (users
  pick anything `fetch_models` returns — OpenRouter alone returns hundreds). Wildcard
  deployments would duplicate `llm_defaults.json` as a second config SSOT.
- Context-window fallback silently switches models under a result payload that reports
  model provenance (`ai.py:1497-1509`) — behavior we explicitly do not want.
- Retries are not Router-exclusive: `num_retries=2` per `acompletion` call covers
  transient 5xx/timeouts (LiteLLM does not retry auth/400-class errors).
- Router internals are exactly what the Rust rewrite replaces Sep-Dec 2026; the thin
  `acompletion` surface minimizes churn exposure. Router remains a config-only upgrade
  *inside* the same facade if needs grow.

Why not replace the facade itself: it is 132 LOC and carries the `NodeUserError`
translation site, the `incompatible_models` filter, and the `{provider}_proxy`
credential lookup — plus ~420 LOC of contract tests that pass unchanged if it stays.

---

## 6. Design — Stage A: chat path

### 6.1 Shape

- **New** `server/services/llm/litellm_adapter.py` (~400 LOC): one `LiteLLMAdapter`
  class implementing the existing `LLMProvider` protocol
  (`chat(messages, *, model, temperature, max_tokens, thinking, tools) -> LLMResponse`).
  Constructed per-call by the `ProviderSpec` factory exactly like today's providers:
  `LiteLLMAdapter(provider=name, api_key=..., proxy_url=..., defaults=LLM_DEFAULTS)`.
- **The `ProviderRegistry` stays** (it is the contract for
  `test_provider_self_registration.py`, `unifier.is_registered()`, and
  NodeUserError-on-unknown-provider). Under the flag, registration becomes one
  data-driven loop over `LLM_DEFAULTS["providers"]` registering
  `ProviderSpec(name=n, factory=partial(LiteLLMAdapter, provider=n),
  sdk_exception_refs=("openai:OpenAIError",), client_kwargs={})`.
  (Since 2026-07-14 the spec takes lazy `"module:Class"` refs — the
  resolved-classes surface remains the `sdk_exception_types` property.)
- **`unifier.py` changes ~15 lines**: a flag branch in `_build_client`
  (unifier.py:114) plus `fetch_models` delegation to the retained HTTP lister
  (section 6.7). Public signatures unchanged — `test_wiring.py`,
  `test_unifier_typed_errors.py`, `test_unifier_incompatible_models_filter.py` pass
  without edits.
- Exception strategy: LiteLLM's typed exceptions inherit from the OpenAI SDK hierarchy,
  so `(openai.OpenAIError,)` is one tuple for all 12 providers; `openai` is already
  eagerly imported (`ai.py:42`) and rides in as a LiteLLM dependency regardless. Phase 0
  asserts the subclassing; if any LiteLLM exception escapes the hierarchy, add
  `litellm.exceptions.APIError` to the tuple inside the adapter module.

### 6.2 Model-name prefixing

`litellm_model = f"{litellm_prefix}/{model}"`, with `litellm_prefix` as a new per-provider
field in `llm_defaults.json` (table in section 4.3). The adapter contains zero hardcoded
provider names. Models arrive post-`[FREE]`-strip (double-guarded: `ai.py:1412-1414` and
the adapter re-strips for openrouter). Nested slashes in OpenRouter IDs are fine —
LiteLLM splits on the first `/`.

### 6.3 ThinkingConfig mapping

One pure function `_thinking_params(provider, model, thinking, max_tokens) -> dict`,
dispatching on `ModelRegistryService.get_thinking_type(model, provider)` (the same
branch `create_model` uses at `ai.py:1318`):

| thinking_type / case | Mapping |
|---|---|
| budget / anthropic | `thinking={"type":"enabled","budget_tokens":budget}`; **keep our `max_tokens <= budget -> budget + 1024` bump** (`anthropic.py:59-62`) — do not delegate to LiteLLM; temperature already forced to 1 by our resolver |
| budget / gemini 2.5-era | `thinking={"type":"enabled","budget_tokens":budget}`; Phase 0 golden test pins the emitted `thinkingConfig.thinkingBudget`; fallback spelling is the provider-specific `thinkingConfig` kwarg if translation is absent on the pinned version |
| level / gemini 3.x (XOR budget) | if `thinking.level` set: send effort-style (`reasoning_effort=thinking.level`) and omit budget; else budget and never a level. `ThinkingConfig.level` stays None-by-default — the Vertex-400 guard (`gemini.py:74-80`) is preserved as a contract |
| effort / openai o-series + gpt-5 | `reasoning_effort=thinking.effort` |
| format / groq qwen3 | `extra_body={"reasoning_format": thinking.format}` guarded by a Phase 0 passthrough check — strictly >= current chat-path behavior (dropped today) |
| kimi thinks-by-default | Stage A = parity (do nothing; unifier path lets kimi think and captures reasoning today). The `extra_body` disable stays agent-path until Stage B aligns both |

### 6.4 Temperature and max_tokens: our resolvers remain the authority

`resolve_temperature` / `resolve_max_tokens` (`config.py:158-206`) stay at the ai.py
call sites, untouched. The adapter additionally:

- **omits `temperature` entirely** when `is_reasoning_model(model, provider)` — matching
  both current providers and `create_model` (`ai.py:1312-1315`);
- **emits `max_completion_tokens` itself** for reasoning models and `max_tokens`
  otherwise — LiteLLM's own o-series auto-mapping works on v1.90.0 but has a regression
  history (#8213, #10066, #13390), so it is not the mechanism of record.

`litellm.drop_params = True` is set globally as a belt-and-braces measure against other
per-provider param rejections — never as the primary mechanism (a callback logs whenever
it fires, so silent drops are visible in DEBUG logs).

### 6.5 api_base, proxy_url, placeholder keys

Single resolution method: `api_base = proxy_url or (base_url per section 4.3 table) or
None`. When `proxy_url` is set, `api_key` is replaced with `"ollama"` — byte-for-byte
today's behavior (`openai.py:31-36`, `anthropic.py:27-30`). Gemini-with-proxy in Vertex
mode logs-and-ignores the proxy exactly as `gemini.py:33-36`. No `/v1` special-casing is
needed because of the 6.2/4.3 routing decisions.

### 6.6 Vertex Express ("AQ.") keys

`services/llm/vertex.py` (`is_vertex_express_key`, 28 LOC) **stays as the detection
SSOT** — contract-tested (`tests/llm/test_vertex_key.py`, 357 LOC) and shared with the
agent path. The adapter routes AQ. keys to the `vertex_ai/` prefix.

**Stated uncertainty:** LiteLLM's `vertex_ai/` route with a bare Express API key (no
service-account `vertex_credentials`) is the one mapping this design cannot guarantee
from documentation — it is the top Phase 0 spike item.

**Pre-decided contingency:** if LiteLLM cannot drive Express keys on the pinned version,
the gemini integration retains a ~60-LOC native google-genai fallback *for AQ. keys
only* (extracted from `gemini.py:28-42,86-91`), and everything else still migrates. This
isolates the only genuinely exotic auth path without blocking the other 11 providers.

### 6.7 fetch_models stays ours

LiteLLM has no per-account model listing (`get_valid_models(check_provider_endpoint=True)`
is sync, env-var oriented, and ignores our proxy/curation semantics). New
`server/services/llm/models_http.py` (~130 LOC), called by `ChatUnifier.fetch_models`
when the flag is on:

- Generic: `GET providers.<name>.models_endpoint` with `build_headers()` headers
  (anthropic's `anthropic-version` header moves into the JSON `extra_headers` block),
  honoring `proxy_url`; parse `data[].id`, sorted.
- OpenRouter: `[FREE]` tagging + free-first sort ported verbatim (`openrouter.py:56-80`).
- Gemini: curated-list + key-probe design ported (`gemini.py:97-138`); the AQ. probe
  becomes a `max_tokens=1` adapter chat call against the first curated model (sub-cent,
  exercises the exact route users hit) — unless the 6.6 contingency retains google-genai,
  in which case `count_tokens` stays.
- The curated-list fallback in `ai.py:1392-1398` and the credential-probe flow
  (`nodes/model/_credentials.py:55`) are untouched.

### 6.8 Import and module settings (cold-start protection)

Lazy import is **mandatory**, not optional: the repo's cold-start budget is 2.90s to
"Application startup complete" ([performance.md](./performance.md)), and the layer
already fought this war (eager `langchain_openai` cost ~20.8s cold on Windows; fixed via
`functools.cache` lazy getters — the pattern to copy, `ai.py:55-96`).

```python
@functools.cache
def _litellm():
    import litellm
    litellm.drop_params = True        # safety net for per-provider param rejection
    litellm.modify_params = True      # provider-required message reshaping (e.g. Anthropic first-message-must-be-user)
    litellm.telemetry = False         # SDK telemetry kill switch — mandatory for a local-first product
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    return litellm
```

Nothing at `services.llm` import time may touch litellm — `core/container.py` imports
`services.llm` during container boot. `sdk_exception_refs=("openai:OpenAIError",)`
deliberately avoids importing litellm (or any SDK) at registration — the same lazy-ref
contract `tests/llm/test_lazy_sdk_imports.py` already enforces for openai/anthropic/
google-genai. New guard test
`tests/llm/test_lazy_litellm.py`: import `services.llm`, construct `ChatUnifier`, assert
`"litellm" not in sys.modules`. Phase 0 asserts the three module-setting attribute names
on the pinned version so a rename fails in CI, not production.

### 6.9 Response normalization

One `_normalize(resp, model) -> LLMResponse`:

- `content` = `choices[0].message.content or ""`; `finish_reason` defaulted to `"stop"`;
  `model` = the **user-facing** model string (not the prefixed one) — preserves the
  result-payload contract (`ai.py:1497-1509`); `raw` = the litellm response.
- `thinking` = `message.reasoning_content`; for Anthropic, if empty but
  `thinking_blocks` present, join `block["thinking"]` with `"\n\n"` (matches current
  formatting). This deletes per-provider extraction wholesale.
- `tool_calls` mapped as today (`openai.py:149-157`). The chat path never passes tools
  today (`ai.py:1483-1491`) — this is protocol completeness plus Stage B groundwork.
  (Gemini synthetic tool-call IDs will differ from the current name-as-id hack — an
  accepted no-consumer change.)
- `Usage` mapping:
  - `input/output/total` ← `usage.prompt_tokens / completion_tokens / total_tokens`
  - `cache_read_tokens` ← `usage.prompt_tokens_details.cached_tokens` (fallback:
    `cache_read_input_tokens` attr for Anthropic-style placement)
  - `reasoning_tokens` ← `usage.completion_tokens_details.reasoning_tokens`
  - `cache_creation_tokens` ← `cache_creation_input_tokens` (Anthropic; read
    defensively from both `usage` and `provider_specific_fields` — Phase 0 asserts the
    exact location; all other providers stay 0, same as today)
- All *input* message shaping collapses to the OpenAI dict format; LiteLLM performs the
  Anthropic system-split and Gemini role/parts translation internally — deleting
  `anthropic.py:_split_system/_to_api_message` and
  `gemini.py:_split_system_and_contents`.

---

## 7. Design — Stage B: agent loop

Per the owner's scope decision, the agent loop is an **active stage**, not an indefinite
deferral. It begins only after the Stage B gate (section 10, Phase 4).

### 7.1 What changes

Replace `create_model` (`ai.py:1206-1353`) and LangChain message shapes in
`_run_agent_loop` (`ai.py:733-894`), `execute_chat_agent` (`ai.py:2105-2626`), and
`services/temporal/agent_activities.py:119,208` with adapter calls on protocol types:

| Concern | Mapping |
|---|---|
| Messages | `SystemMessage/HumanMessage/AIMessage/ToolMessage` ↔ OpenAI dicts; `AIMessage.tool_calls[{name,args,id}]` → assistant `tool_calls` with JSON-encoded arguments; `ToolMessage.tool_call_id` → role `tool` |
| Tool binding | `bind_tools(StructuredTool)` → `ToolDef` JSON schemas passed per call (hot-rebind after canvas mutations becomes list mutation — simpler than re-binding a LangChain model) |
| Memory | `_parse_memory_markdown` (`ai.py` memory glue) returns protocol `Message` objects instead of LangChain messages |
| Thinking | the 4-format `extract_thinking_from_response` (`ai.py:621-730`) collapses to `reasoning_content` / `thinking_blocks` |
| Token tracking | `_track_token_usage` (`ai.py:1091`) switches from LangChain `usage_metadata` to our `Usage` — cache/reasoning tokens start flowing into `CompactionService.track()` (which already accepts those keys, `compaction.py:133-145`) — a strict cost-accuracy improvement for Anthropic caching |
| Compaction | native context-management configs (incl. the `compact-2026-01-12` beta header) pass via litellm `extra_headers` |
| Kimi alignment | the `extra_body` thinking-disable moves into the shared adapter, unifying quirk handling across both paths |

### 7.2 What explicitly stays

The deepagents-based Deep Agent node remains LangChain — `deepagents==0.6.9` pulls
`langchain-core` itself, so those pins survive Stage B; *our* loop simply stops using
them. Removable after Stage B: `langchain-openai`, `langchain-anthropic`,
`langchain-google-genai`, `langchain-groq`, `langchain-cerebras` pins (and with them the
~20.8s-cold-import class and the Windows/Py3.13 gemini gRPC hang workaround).

### 7.3 Stage B gate criteria

1. Stage A default-on and stable for 2+ releases (repo ships ~weekly).
2. Gemini `thought_signature` multi-turn tool round-trip verified on the pinned LiteLLM
   (assistant-turn reasoning fidelity is the known open risk).
3. Streaming/broadcast integration for agent progress events mapped.

---

## 8. Config, flag, rollout, dependency policy

- New Settings field (`core/config.py`, `{SERVICE}_ENABLED` convention; model:
  `event_framework_enabled`): `litellm_enabled: bool = Field(default=False,
  env="LITELLM_ENABLED")` + `.env.template` row.
- **One boolean, evaluated per-request** in `ChatUnifier._build_client` /
  `fetch_models` — not per-provider flags (the 12x2 matrix is the complexity being
  deleted). Documented escape hatch if a single provider regresses post-cutover: a
  5-line `LITELLM_PROVIDERS` CSV allowlist addition.
- Wiring: `_build_chat_unifier` (`core/container.py`) gains `settings`; rollback =
  `LITELLM_ENABLED=false` + restart. Both stacks stay installed and registered until
  deletion, so rollback is config-only with zero deploys.
- Legacy deletion trigger: default flipped to `true` + one release soak with no
  provider regressions.
- Dependency: `litellm==1.90.0` **exact pin** in `server/pyproject.toml` (weekly
  upstream releases + the Rust-rewrite window make range pins reckless), then `uv lock`
  and regenerate `server/requirements.txt` via the documented
  `uv export --frozen --no-emit-project --no-hashes --no-dev -o requirements.txt`.
  Bump policy: monthly or CVE-triggered, always through the parity suite (matches the
  existing pin-comment precedent in pyproject).

---

## 9. Risks and mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Cold-start regression from import weight (200MB+ RSS, heavy module tree) vs the 2.90s budget | High | Mandatory lazy import (6.8); `test_lazy_litellm.py` CI guard; cold-start measured per performance.md method in Phase 2 |
| R2 | Behavioral regression across the 16 encoded quirks | High | Golden-request wire-parity suite: capture exact provider-SDK kwargs the current layer produces for a (provider x thinking x model-family) matrix, assert LiteLLM emits equivalent wire params; live smoke stays the gate |
| R3 | fetch_models gap (LiteLLM lists no per-account models) | Medium | Keep our HTTP listing (~130 LOC, section 6.7); scope LiteLLM to the completion path only |
| R4 | Usage-shape mismatch breaks Pricing/Compaction persistence (`cache_*`, `reasoning_tokens` are DB columns) | Medium | One adapter mapping function + per-provider fixtures (anthropic/openai/gemini); compaction beta-header pass-through verified before Stage B |
| R5 | Split-brain window: chat on LiteLLM, agent loop on LangChain until Stage B | Medium | Bounded by the Stage B gate, not indefinite; quirk SSOT stays `llm_defaults.json` so fixes are configured once; both layers already coexist today |
| R6 | Weekly upstream churn imports regressions | Medium | Exact pin + monthly/CVE bump policy behind the parity suite |
| R7 | Adopting immediately before LiteLLM's Rust rewrite | Medium | The `acompletion` SDK surface is the stability-promised, least-changed path; hard pin; re-evaluate topology at Dec 2026 GA |
| R8 | `fastuuid` transitive dep needs a Rust toolchain where wheels are missing (Windows is the primary platform) | Low-Med | Phase 0 blocker check: `uv add litellm==1.90.0` must resolve binary wheels on win_amd64 / py3.11-3.12 |
| R9 | `drop_params` silently masks misconfiguration | Low-Med | Our resolvers remain authoritative and run before the call; drop_params is a logged safety net only |
| R10 | Test-suite rewrite cost (~2,250 LOC surface) | Medium | ~40-50% rewrite concentrated in provider-shape tests (`test_providers.py`, `test_plugin_shape.py`, mocks); ~50-60% retained (resolution, filter, vertex detection, live smoke) |
| R11 | Supply chain / telemetry | Low | MIT core; enterprise/ dir never imported; `litellm.telemetry=False` at init; advisory-driven bump policy (pyproject precedent) |

**Top-5 riskiest quirks** (rank-ordered for parity-test priority):

1. Vertex Express "AQ." key routing — possible outright capability gap in LiteLLM's
   `vertex_ai/` route; pre-decided native fallback (6.6).
2. Gemini `thinking_level` None-default — LiteLLM must not inject defaults; wire-capture
   test pins it.
3. Anthropic `max_tokens > budget` bump — kept in our adapter code, never delegated.
4. Ollama/LM Studio `{provider}_proxy` credential + placeholder keys + offline
   empty-list UX — mappable but easy to miss.
5. Kimi `extra_body` thinking-disable + fixed temperature — extra_body must pass through
   verbatim; fixed temp applied by our resolver before the call.

(o-series `max_completion_tokens` and reasoning temp-omission rank lower — handled
natively by LiteLLM *and* enforced by our adapter regardless.)

---

## 10. Phased implementation plan

### Stage A — chat path

**Phase 0 — spike + bench (scratch only, go/no-go appended here).**
1. `uv add litellm==1.90.0` resolution on Windows/py3.12 — fastuuid wheel check;
   measure `import litellm` wall time + RSS.
2. Exception hierarchy asserts: `issubclass(litellm.exceptions.AuthenticationError,
   openai.OpenAIError)` etc. for realistically-raised classes.
3. Vertex Express spike: `acompletion(model="vertex_ai/gemini-2.5-flash",
   api_key="AQ. ...")` — the 6.6 contingency trigger.
4. Golden request captures: o-series `max_completion_tokens` + temperature omission;
   anthropic `thinking` + bump; gemini budget-XOR-level; kimi/groq `extra_body`
   passthrough; openrouter headers. Test-infra gotcha to resolve here: LiteLLM may use
   its aiohttp transport on some routes while respx intercepts httpx only — pin
   `litellm.disable_aiohttp_transport = True` in the golden-test fixture and verify the
   attribute name on the pinned version.
5. Live smoke via a throwaway adapter for every keyed provider.
6. Assert `litellm.drop_params` / `modify_params` / `telemetry` attribute names; assert
   the `cache_creation_input_tokens` location on an anthropic cache-hit response.

**Phase 1 — core swap behind the flag (default off).**
- New: `services/llm/litellm_adapter.py` (~400), `services/llm/models_http.py` (~130).
- Edit: `services/llm/unifier.py` (flag branch + fetch_models delegation),
  `services/llm/__init__.py` (data-driven registration loop when flag on),
  `config/llm_defaults.json` (`litellm_prefix` x12 + anthropic `extra_headers`),
  `core/config.py` (+flag), `core/container.py` (settings into `_build_chat_unifier`),
  `server/pyproject.toml` + `uv.lock` + `requirements.txt`, `.env.template`.
- Untouched: `ai.py`, `nodes/model/*`, `protocol.py`, `config.py` resolvers, pricing/
  registry/compaction services.
- New tests: `test_litellm_adapter.py` (mapping units: prefix table, thinking matrix
  incl. gemini XOR, temperature/max_tokens branches, usage normalization from synthetic
  responses), `test_litellm_golden_requests.py` (respx request-shape contracts),
  `test_lazy_litellm.py`.

**Phase 2 — parity + cutover.**
- Parametrize `tests/llm/test_live_providers.py` with a fixture flipping
  `litellm_enabled`, so one `pytest -m live` run exercises both stacks and diffs
  `LLMResponse` fields (content non-empty, thinking presence, usage > 0).
- Cold-start regression per the performance.md measurement method; add the result as a
  new optimisation-history row (post-merge follow-up, section 12).
- Flip `litellm_enabled` default to `true`; soak one release.

**Phase 3 — delete dead code.**
- Delete: `providers/openai.py` (200), `anthropic.py` (203), `gemini.py` (268 — minus
  the AQ. fallback if the contingency fired), `openrouter.py` (99), `_compat.py` (109),
  provider imports in `providers/__init__.py`, `factory.py` (75), the flag + legacy
  branch in `unifier.py`, and the `create_provider` / `is_native_provider` /
  `NATIVE_PROVIDERS` exports.
- Tests: delete `test_providers.py` + `test_factory.py`; rewrite
  `test_provider_self_registration.py` (data-driven registration) and the
  provider-construction halves of `test_vertex_key.py` (detection + curated-list halves
  survive); update `test_plugin_shape.py` grep-guards. Surviving unchanged:
  `test_wiring`, `test_unifier_typed_errors`, `test_unifier_incompatible_models_filter`,
  `test_max_tokens_resolution`, `test_messages`.
- Do NOT remove `anthropic` / `google-genai` / `langchain-*` deps yet — the agent path
  still needs them until Stage B (`services/plugin/credential.py` also touches openai
  exception classes).

### Stage B — agent loop (starts after the section 7.3 gate)

**Phase 4 — agent-loop swap behind the same flag pattern**: `create_model` replacement,
message mapping, memory parsing to protocol Messages, tool binding, extractor collapse,
`_track_token_usage` switch, Temporal `agent_activities.py` touchpoints.

**Phase 5 — agent parity**: memory round-trip, multi-turn tool loops per provider
(gemini `thought_signature` fidelity is the named risk), compaction beta headers, agent
progress broadcasts, Temporal activity heartbeat behavior.

**Phase 6 — LangChain glue deletion**: the 4-format extractor (~110 LOC), `create_model`
(~148 LOC), LangChain message glue; remove `langchain-openai` / `langchain-anthropic` /
`langchain-google-genai` / `langchain-groq` / `langchain-cerebras` pins (deepagents keeps
`langchain-core` alive); docs updates.

### LOC accounting

| | Deleted | Added | Net |
|---|---|---|---|
| Stage A, `services/llm/` | ~1,010 | ~600 (adapter ~400, models_http ~130, registration ~40, flag/DI ~20, JSON ~15) | **~ -420** (1,701 → ~1,240) |
| Stage A, tests | ~385 deleted + ~150 rewritten | ~490 new | ~ +100 (coverage shifts from SDK-translation internals to request-shape contracts) |
| Stage B | ~700+ in ai.py + 5 dep pins | small | **~ -700** |

---

## 11. Acceptance criteria

1. Cold start stays <= 2.90s warm ("Application startup complete", performance.md
   method), with a startup-timeline regression check.
2. The 16-quirk golden-request parity suite passes; live smoke green on all 12 providers
   including a Vertex "AQ."-key case.
3. Provider-layer production LOC decreases >= 40% at Stage A end.
4. `NodeUserError` user-facing error contract and the model-dropdown UX (including the
   ollama/lmstudio offline empty-state) are byte-identical.
5. `uv sync` succeeds with binary wheels only on Windows / py3.11-3.12.

---

## 12. Open questions and follow-ups

Open questions:
1. **Vertex Express negotiability** — if LiteLLM cannot serve "AQ." keys, is the
   permanent native-gemini carve-out acceptable, or does Vertex Express support get
   dropped instead?
2. **Pin-bump ownership** — who owns the monthly litellm bump-and-smoke chore?
3. **Stage B streaming ambitions** — LiteLLM unlocks uniform streaming; do chat nodes /
   agent progress events adopt it in Stage B or a later wave?

Follow-ups (post-merge; currently blocked on concurrent doc edits by another session):
- Add the doc-table row for this RFC to `CLAUDE.md`.
- Rewrite `docs-internal/native_llm_sdk.md` after Stage A lands ("adding a provider =
  JSON edit incl. `litellm_prefix`").
- Add the Phase 2 cold-start measurement as a `docs-internal/performance.md` history row.

---

## 13. Sources

LiteLLM (official, fetched 2026-07-02):
- Repo: https://github.com/BerriAI/litellm · License: https://github.com/BerriAI/litellm/blob/main/LICENSE
- Rust launch blog: https://docs.litellm.ai/blog/litellm-rust-launch · Tracking issue: https://github.com/BerriAI/litellm/issues/31263
- Routing/Router: https://docs.litellm.ai/docs/routing · Providers: https://docs.litellm.ai/docs/providers (openai, anthropic, gemini, openrouter, xai, deepseek, mistral, groq, cerebras, ollama, openai_compatible pages)
- Reasoning: https://docs.litellm.ai/docs/reasoning_content · Exceptions: https://docs.litellm.ai/docs/exception_mapping · drop_params: https://docs.litellm.ai/docs/completion/drop_params
- Usage/cost: https://docs.litellm.ai/docs/completion/token_usage · Prompt caching: https://docs.litellm.ai/docs/completion/prompt_caching · Streaming: https://docs.litellm.ai/docs/completion/stream
- Known issues cited: #15262 (SDK/proxy bundling), #14145 (fastuuid), #8213 / #10066 / PR #13390 (o-series max_tokens regressions)

Repo anchors (verified this session): `services/llm/unifier.py` (78-79, 93-94, 95-98,
114, 122), `services/llm/providers/anthropic.py:59-62`, `services/llm/providers/gemini.py:72-80`,
`services/llm/vertex.py:23`, `services/ai.py` (621, 733, 1091, 1206, 1365, 1400, 2105),
`constants.py:414`, `config/llm_defaults.json:226-227`, `services/compaction.py:133-145`,
`docs-internal/performance.md`.
