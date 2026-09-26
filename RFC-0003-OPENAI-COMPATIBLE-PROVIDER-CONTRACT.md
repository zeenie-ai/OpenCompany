# RFC-0003 — OpenAI-Compatible Provider Contract

Status: Accepted
Created: 2026-09-01
Updated: 2026-09-24 (named endpoints, LiteLLM table, body-shape probe; implemented)
Scope: `server/services/llm/`, `server/nodes/model/`, `server/nodes/agent/`, `server/services/model_registry.py`, the credentials panel
Relationship: independent of RFC-0002 (Context/Memory). Touches no agent state.

## How to read this

If you are **adding a provider**, read §11. If you are **adding a server you
run**, you need no code: §8 says what the credentials panel does. If you are
**reviewing**, §3 and §13 are the whole normative surface; everything else is
rationale.

Files this RFC changes:

| File | Change |
|---|---|
| `services/llm/config.py` | provider references (`split_provider_ref`, `endpoint_ref`); `resolve_credential()`; every `llm_defaults` lookup resolves a reference to its base block; `open_world_models` replaces a hardcoded tuple |
| `services/llm/protocol.py` | `LLMErrorCategory.PROTOCOL`; `LLMError.public_message`; model-scoped messages that read correctly for a named endpoint |
| `services/llm/endpoints.py` | **new** — save-time base-URL resolver; `redact_url`; endpoint listing; `unconfigured_endpoint_message`, the one "not configured" wording (§8.3) |
| `services/llm/unifier.py` | provider lookup by reference; URL row read by reference; empty-key and missing-endpoint guards; `except LLMError`; failure logs carry `url` + `url_source`, written before the error is translated (§10.3) |
| `services/llm/providers/openai.py` | drop `api_key="ollama"`; `url_source`; 2xx-with-error-body guard |
| `services/llm/providers/anthropic.py` | drop the duplicate placeholder hardcode |
| `services/llm/providers/_compat.py` | one `openai_compatible` registration with no `base_url` |
| `nodes/model/_local_validator.py` | one save path for Ollama, LM Studio and every named endpoint; a write the store rejects fails the save (§6.4); every step bounded (§6.5) |
| `nodes/model/_credentials.py` | `OpenAICompatibleCredential` (slug rule, §8.1; `is_configured`); `_LocalLLM.resolve` uses `resolve_credential` |
| `nodes/model/_option_loaders.py`, `nodes/model/__init__.py` | **new** — `aiProviders`, `openaiCompatibleEndpoints`, `openaiCompatibleModels` loaders |
| `nodes/model/openai_compatible_chat_model/` | **new** — chat-model node for a named endpoint |
| `nodes/agent/_provider.py` + three agent Params | one loader-driven `ProviderRef` field replaces three `Literal` lists |
| `services/plugin/credential.py`, `routers/websocket.py` | optional `Credential.catalogue_extras()` hook, merged into the catalogue; `Credential.is_configured()`, defaulting to "anything stored under my id" |
| `services/workflow_validator.py` | MISSING_CREDENTIAL asks `is_configured` with the node's parameters (§8.3) |
| `services/credentials/handlers.py` | `delete_api_key` also clears `{provider}_proxy` and registered models |
| `services/model_registry.py` | local models kept apart and persisted under DATA_DIR; LiteLLM table, whose on-demand fetch is time-boxed and backs off after a failure |
| `services/pricing.py` | reference-aware lookup; a named endpoint's recorded price |
| `services/settings/handlers.py` | each endpoint is a row in the global default-model picker |
| `services/ai.py`, `services/temporal/agent_activities.py` | an unsaved endpoint is reported as not configured, not as a missing key: a `NodeUserError` in process, a non-retryable `MissingAgentProviderCredential` on Temporal (§8.3) |
| `services/rlm/service.py`, `services/rlm/adapters.py` | refuse a provider RLM cannot route, as the agent's own or a connected chat model's (routed by `detect_ai_provider`) |
| `constants.py` | new chat-model type; `detect_ai_provider` reads the node's endpoint |
| `config/llm_defaults.json` | `auth.placeholder_key`, `open_world_models`, the `openai_compatible` block |
| `config/credential_providers.json`, `config/pricing.json` | `openai_compatible` entries |
| `client/.../credentials/panels/ApiKeyPanel.tsx`, `EndpointList.tsx` | add / refresh / remove endpoints |
| `client/.../contexts/WebSocketContext.tsx`, `credentials/useCredentialPanel.ts` | `CREDENTIAL_PROBE_REQUEST_TIMEOUT`: an endpoint save waits 60 s (§6.5) |
| `client/.../SquareNode.tsx`, `lib/credentialProviderId.ts` | a node's status dot reads the credential the node actually declares |
| `client/.../ParameterRenderer.tsx`, `lib/dynamicOptions.ts` | a dependent field (the endpoint's model) moves when its parent changes |
| `client/.../ui/TopToolbar.tsx`, `hooks/useCatalogueQuery.ts` | the global model picker re-fetches when endpoints change (`useStoredCredentialSignature`) |

## 1. Summary

OpenCompany treats "OpenAI-compatible" as one shape. It is a family of
divergent shapes with **no specification behind it**, and the divergences are
invisible from a successful response.

There are only two ways to hold a per-provider fact: declare it, or guess it.
The client used to guess — that a stored base URL is correctly rooted, that a
provider with a `{provider}_proxy` row wants the literal key `"ollama"`, and
that HTTP 200 means the request was understood. It also validated two
providers against a different wire surface than the one execution uses, so a
credential could read green while the runtime was dead.

This RFC makes the endpoint and the credential **resolved facts** — computed
once, at save time, with evidence — and makes capability variance
**declared**. It then uses that contract to let a user add **any number of
named OpenAI-compatible servers** (llama.cpp, vLLM, a LiteLLM proxy, a second
Ollama host) with no code: the LiteLLM `model_list` idea of a label, a base
URL and an optional key, each selectable from every agent.

## 2. Problem

### 2.1 The failure chain (normative; other sections cross-reference here)

A stored LM Studio base URL without `/v1`. The panel read **Connected** with
models discovered. Every run failed with `AI generated empty response`. Line
references are to the code as the draft of this RFC found it.

| # | Step | Site |
|---|---|---|
| 1 | URL persisted verbatim, **before** the probe runs | `_local_validator.py:270-275` |
| 2 | Probe strips a *trailing* `/v1` and the scheme, then talks to LM Studio's **native WebSocket SDK**. With no `/v1` present the strip is a no-op, so the probe cannot see the defect | `_local_validator.py:124-134`, `:201-203` |
| 3 | Probe succeeds. `valid=True` broadcast | `_local_validator.py:314-339` |
| 4 | Runtime reads the same row, for every provider | `unifier.py:296` |
| 5 | `url = proxy_url or base_url` then `AsyncOpenAI(base_url=url)`, key overwritten with `"ollama"` | `providers/openai.py:45-49` |
| 6 | SDK concatenates prefix + endpoint, giving `POST /chat/completions` | `openai/_base_client.py:496` |
| 7 | LM Studio answers **HTTP 200** with an `{"error": ...}` body | vendor behaviour |
| 8 | SDK raises only on 4xx/5xx. `choices` is absent, so it coerces to `None` | `openai/_models.py` |
| 9 | Empty content becomes `AI generated empty response` | `services/ai.py` |

Two structural defects compound. **Validation exercised a surface execution
never touches** — LM Studio serves OpenAI-compat at `/v1/*`, native REST at
`/api/v1/*`, and a WebSocket SDK at `ws://{host}/{namespace}`, all on one port;
Ollama likewise serves `/api/*` beside `/v1/*`. A green Fetch proved host
liveness on the *native* surface and nothing about the prefix the runtime uses.
And **a 2xx was treated as an understood request**, which for this class of
server it is not: LM Studio answers HTTP 200 to routes it does not serve
([lmstudio-bug-tracker#1323](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1323),
[#2085](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/2085)).

### 2.2 Why the endpoint cannot be inferred

`base_url` is a path **prefix**, string-concatenated with the endpoint
(`_prepare_url`: `base_url.raw_path + endpoint.lstrip("/")`,
`openai/_base_client.py:496`) — not `urljoin`. The base path is never
discarded, and the SDK **never inserts `/v1`**; that segment lives in the
default constant `https://api.openai.com/v1`. Override `base_url` and the whole
prefix is yours.

`/v1` is neither universal nor positionally predictable:

| Shape | Providers |
|---|---|
| Root-mounted, versionless | DeepSeek (`/chat/completions` at root; siblings `/beta`, `/anthropic`) |
| Namespaced | Groq `/openai/v1`, Fireworks `/inference/v1`, OpenRouter `/api/v1` |
| Version varies by model | Sarvam `/v1` and `/v2` |
| `/v1` only | vLLM, Ollama, LM Studio, Mistral, xAI, Kimi, Cerebras, Together |
| Both root and `/v1` | llama.cpp, LocalAI, LiteLLM |
| Configurable, may be empty | Jan |

So "append `/v1` when the URL has no path" is wrong for the first three rows,
and it fails in the opposite direction for path-prefix deployments — vLLM
`--root-path`, llama.cpp `--api-prefix`, Open WebUI proxying Ollama under
`/ollama` — where a URL that *has* a path still needs `/v1` appended.

**A path-less base URL is genuinely ambiguous**: indistinguishable from a
deliberately root-mounted server without probing. No string rule resolves it.
That is why §6 probes.

### 2.3 No way to add a server

Pointing an agent at llama.cpp, vLLM, a LiteLLM proxy or a second Ollama host
needed a Python edit: a provider id, a JSON block, a registration, three agent
`Literal` lists, a credential class. Each server the user runs is data, not
code.

## 3. Decisions

| # | Decision | Locked by |
|---|---|---|
| D1 | A configured provider's `base_url` is copied verbatim from vendor docs. The runtime never appends, strips, or rewrites a path segment. | AG1, AG2 |
| D2 | A user-supplied base URL is **resolved by probing at save time**, once, and the resolved value is persisted. The runtime consumes it verbatim. | AG3, AG4 |
| D3 | The probe is the call execution makes (`models.list()` through the openai SDK), against `{base}` then `{base}/v1`. A candidate counts only when the **body is an OpenAI list** (a `data` array): a status code alone proves nothing (§2.1). A connection-level failure stops at the first candidate. | AG3 |
| D4 | Two outcomes: adopted, or failed with a reason in words the user can act on. A 401/403 is a failure reason ("Found an OpenAI-compatible server at X, but it rejected the API key."), not a stored third state. | AG3 |
| D5 | A failed save **writes nothing and broadcasts nothing**. The previous working configuration stays in force. A save that yields no models is a failed save. | AG6 |
| D6 | The credential is resolved by one function. No provider hardcodes a placeholder key. | AG7, AG8 |
| D7 | No code path hands the SDK an unresolved credential — `api_key=None` makes the SDK read `OPENAI_API_KEY` and ship the operator's OpenAI key to a third party. | AG8 |
| D8 | A capability is **declared**, never sniffed. A flag ships only with a consumer. This change ships two: `auth.placeholder_key` and `open_world_models`. | AG9 |
| D9 | A 2xx chat response with no `choices` and an error-shaped body raises `PROTOCOL`, naming the redacted URL called. | AG10 |
| D10 | Every provider-call failure logs the effective URL and `url_source`, redacted. Never the key. | AG11 |
| D11 | `ProviderSpec` gains no field. | AG12 |
| D12 | `_strip_v1_path` survives, scoped to the native enrichment probes only. | AG2 |
| D13 | A named endpoint **is** a provider reference, `openai_compatible:<slug>`, with the same two credential rows Ollama uses: `{ref}` (key, models, per-model params) and `{ref}_proxy` (resolved URL). The reference travels as the ordinary `provider` string through key injection, Temporal payloads and the unifier. | AG15, AG16, AG17 |
| D14 | An endpoint's kind (`ollama`, `lmstudio`, `llamacpp`, `generic`) is **declared** for the Ollama and LM Studio providers and **detected once at save** for endpoints, from native routes by body shape. It is stored and never re-sniffed at run time. | AG13 |
| D15 | Per-model context, output budget and price come from the server first (Ollama and LM Studio SDK probes, llama.cpp `/props`, vLLM `max_model_len`), then the LiteLLM table for `generic` servers only, then the declared default. | AG14, AG18 |
| D16 | Runtime caches live under DATA_DIR, never in the tracked `server/config/`. A model registered from the user's own server survives an OpenRouter refresh. | AG18 |
| D17 | The shape is LiteLLM's and OpenRouter's, not a new one (§8.4). | — |

## 4. Goals and non-goals

**Goals.** One connected verdict across panel and palette. A URL that works
keeps working. A routing mistake is distinguishable from a model failure in the
message the user sees. Adding a compatible *vendor* is a JSON edit plus one
name in a tuple; adding a compatible *server* is a form in the credentials
panel.

**Non-goals.** Not a universal LLM abstraction — `ChatUnifier` stays the single
facade. Not a provider-registry rewrite. Not Anthropic's or Gemini's native wire
format; D6 and D7 apply to their credential handling only, because they share
the defect. Not a parameter-dropping layer: LiteLLM's `drop_params` exists
because LiteLLM passes caller parameters through, and `OpenAIProvider.chat`
sends none of the parameters local servers reject, so a drop list would have
nothing to drop (D8). Not RLM routing (§15).

## 5. The base URL contract

A provider's `base_url` in `llm_defaults.json` is an **opaque, vendor-declared,
verbatim prefix**. It is copied from the vendor's own documentation and is never
synthesized, completed, or normalized by the runtime.

This is why `deepseek: "https://api.deepseek.com"` is **correct** — DeepSeek
documents exactly that, with routes at `/chat/completions`. It is not a missing
`/v1` (§15).

The `base_url` values of the dedicated Anthropic and Gemini blocks are **REST
documentation, not SDK arguments**: the Anthropic SDK appends `/v1` itself, so
feeding it `https://api.anthropic.com/v1` would request `/v1/v1/messages`. The
unifier passes a URL only from the user's `{provider}_proxy` row or from a
compat registration's `client_kwargs`; it never reads `llm_defaults.json`
`base_url` for those two.

Trailing slashes are irrelevant: `_enforce_trailing_slash`
(`openai/_base_client.py:414`) normalizes the base on assignment.

## 6. Endpoint resolution

Applies to every user-supplied URL: the `ollama` and `lmstudio` providers and
every named endpoint. Configured providers are covered by §5.

### 6.1 Rooting

`services/llm/endpoints.py::resolve_base_url(candidate, api_key)` is async and
save-time only. It sends `AsyncOpenAI(base_url=c, api_key=k, max_retries=0,
timeout=10).models.list()` — the exact call `OpenAIProvider.fetch_models`
makes — to `c = candidate`, then `candidate + "/v1"` unless the input already
ends there.

| Answer at a candidate | Outcome |
|---|---|
| a page whose `data` is a list | adopt it (INFO log when `/v1` was appended) |
| 401 / 403 | remember "server found here, key rejected"; try the next |
| connection refused / DNS / timeout | fail now: the other candidate is the same host |
| 404, or a 200 whose body is not a list | try the next |
| nothing adopted | fail, with the most specific reason |

A scheme-less value (`localhost:8080`) is refused with instructions, not
guessed at. The key sent with the probe is already resolved (§7), never
`None`.

This handles path-prefix deployments correctly: `https://host/vllm` probes
`/vllm/models` then `/vllm/v1/models`.

**Two layers, different rules.** Save-time resolution is async and does IO.
Call-time consumption reads the stored value and nothing else. The AG1
tripwire allowlists `endpoints.py` for exactly this reason, and that exclusion
is the module's justification for existing.

### 6.2 Kind (D14)

For a named endpoint only, detected against the host (the resolved base with a
trailing `/v1` stripped). The three routes are asked at once with a 3 s timeout,
and each is accepted **by body shape**:

| Route | Accepted when the body | Kind |
|---|---|---|
| `GET /props` | has a `default_generation_settings` object | `llamacpp` (wins when several answer: llama.cpp also mimics some Ollama routes) |
| `GET /api/v1/models` | has a `models` list | `lmstudio` (LM Studio's current native REST API; v0 is legacy) |
| `GET /api/version` | has a `version` string | `ollama` |
| otherwise | | `generic` |

### 6.3 Models and metadata (D15)

| Kind | Model ids | Context and output budget | Price |
|---|---|---|---|
| `ollama` | loaded models, official SDK `ps()` | the live `context_length` the server loaded | 0 |
| `lmstudio` | loaded models, official SDK `list_loaded()` + `get_info()` | live `context_length` | 0 |
| `llamacpp` | `/models` ids from §6.1 | `/props` `default_generation_settings.n_ctx` | 0 |
| `generic` | `/models` ids from §6.1 | context: vLLM `max_model_len` on the entry, else LiteLLM `max_input_tokens`. Output: LiteLLM `max_output_tokens` when it is below the context, else derived from the context. Else the declared `_default`s | LiteLLM per-token cost × 10⁶; else 0 |

LiteLLM never feeds a local kind: the bound that matters there is the context
the server was started with (Ollama silently truncates past it), not the
model's trained maximum, and a local run costs nothing. The official
`ollama` / `lmstudio` SDKs stay — `pyproject.toml` records choosing them over
hand-rolled probes, and `ollama` also serves embeddings.

### 6.4 Persisting

Only after rooting and describing succeed: write `{ref}_proxy` (the resolved
URL), then `{ref}` (key or placeholder, model ids, per-model params, and a
reserved `_endpoint` entry holding the label, the **redacted** URL, the kind
and when it was probed — that JSON column is not encrypted), then register the models, then
broadcast. Otherwise nothing (D5). A write the credential store rejects fails
the save too, and a rejected `{ref}` write puts the `{ref}_proxy` row back as it
was.

### 6.5 Time budget

A save runs inside one WebSocket request, so every step after rooting is
bounded: the native routes 3 s (asked at once), the Ollama and LM Studio SDK
probes 10 s, and an on-demand LiteLLM fetch 8 s in total, not retried for 10
minutes after a failure. The nominal worst case is about 33 s (two 10 s
rooting candidates, the 3 s kind probes, a 10 s SDK probe). The rooting and
kind timeouts are httpx per-phase timeouts, so a server that trickles its
answer can stretch them; the SDK probe and the LiteLLM fetch are hard caps.
The client waits 60 s (`CREDENTIAL_PROBE_REQUEST_TIMEOUT`) for
`validate_api_key`, so a slow save is not cut off and then completed behind
the user's back.

## 7. Credential resolution

`OpenAIProvider.__init__` used to do `if proxy_url: kwargs["api_key"] =
"ollama"`, duplicated in `anthropic.py`: a dummy key for **any** provider with
a proxy row, which is why a cloud proxy could not work. Both are gone.

One resolver, `services/llm/config.py::resolve_credential(provider, stored)`,
returns the stored key, else the placeholder the vendor documents, declared as
`providers.<name>.auth.placeholder_key`:

| Provider | Placeholder | Source |
|---|---|---|
| `ollama` | `ollama` | Ollama's OpenAI-compatibility docs: "required but ignored" |
| `lmstudio` | `lm-studio` | LM Studio's OpenAI-compatibility docs |
| `openai_compatible` | `sk-no-key-required` | llama.cpp server README's OpenAI client example |

Else it raises. The unifier turns that into a `NodeUserError` before any client
exists.

**D7 is a security rule, not hygiene.** `AsyncOpenAI(api_key=None)` falls back
to `os.environ["OPENAI_API_KEY"]`. Combined with a third-party `base_url`, that
ships the operator's OpenAI key to another vendor. AG8 asserts zero requests
are issued without a resolved key.

## 8. Named endpoints (D13)

### 8.1 Identity and storage

`split_provider_ref(ref) -> (name, slug)` is the only parser. The slug comes
from the label via `python-slugify` (`[a-z0-9-]`, at most 24 characters, so
`openai_compatible:<slug>_proxy` fits the 50-character provider column), or
from the URL's host and port when no label is given (never the whole network
location, which can carry a username and password; a URL with no host and no
label is refused). An existing label is rejected, not auto-suffixed.

The reference is keyed like any provider, which is why endpoints do not use the
Discord `session_id` scoping: every LLM key lookup in the tree is keyed by
`provider` alone (node executor, delegated agents, the Temporal credential
resolver, RLM, the stored-key and provider-defaults handlers). A reference
passes through all of them unchanged; a scope would need each one edited, and
a missed one would silently send the placeholder instead of the user's key.

Everything read from `llm_defaults.json` or `pricing.json` resolves a reference
to the `openai_compatible` block through `split_provider_ref`, so an unsized
endpoint model gets the block's conservative defaults (8192 context, 2048
output: an overestimate overflows, an underestimate only compacts early),
never an unknown provider's fallbacks.

### 8.2 Where an endpoint appears

| Surface | How |
|---|---|
| Every agent's provider dropdown | the `provider` field is a loader-driven `ProviderRef` (`aiProviders`): registered providers in `llm_defaults.json` order, then one option per endpoint |
| The OpenAI-compatible chat-model node | an `endpoint` field (`openaiCompatibleEndpoints`) and a `model` field that depends on it (`openaiCompatibleModels`). Named `endpoint`, not `provider`: a `provider` sibling of `model` triggers the parameter panel's stored-key effect, which would copy the key into the node |
| The model dropdown | unchanged: `get_stored_api_key {provider: ref}` reads the endpoint's row |
| The global default-model picker | one row per endpoint |
| The credentials panel | the catalogue entry's `catalogue_extras()` adds `endpoints` and `stored`; the fields become an add form |

Adding and refreshing go through `validate_api_key` (the Base URL in
`api_key`, the other fields under their catalogue keys, `ref` for a refresh,
which re-reads the stored URL and key); removing goes through `delete_api_key`,
which also clears the `{ref}_proxy` row and the registered models. No new
WebSocket handler, no new registry.

### 8.3 At run time

The unifier looks the provider up by `split_provider_ref(ref)[0]`, reads the
URL row by the full reference, and refuses an `openai_compatible` reference
with no URL row ("not configured") rather than let the SDK default to
api.openai.com. The client cache already keys on the URL and the key
fingerprint, so two endpoints never share a client unless they share both.

A removed or never-chosen endpoint usually shows up earlier, as a missing key:
saving always stores one, so its absence means the rows are gone. Wherever it
is found first, the user gets the same words from
`endpoints.py::unconfigured_endpoint_message` ("The OpenAI-compatible endpoint
'<slug>' is not configured", or "Choose an OpenAI-compatible endpoint" for the
bare id): the in-process agents and chat model raise it as a `NodeUserError`,
the Temporal activities as a non-retryable `MissingAgentProviderCredential`.
The workflow validator's MISSING_CREDENTIAL asks `Credential.is_configured`
with the node's parameters, so an endpoint node is checked against the
endpoint it names, not the bare id, which never holds a row.

### 8.4 Alignment (D17)

| LiteLLM | OpenRouter | Here |
|---|---|---|
| `api_base` (a prefix, `/v1` included: "make sure your api_base has the /v1 postfix") | base `https://openrouter.ai/api/v1` | `base_url`, resolved once (§6.1) |
| a `model_list` entry (`model_name`, `litellm_params`) | — | an endpoint's two rows |
| `custom_llm_provider` | — | `kind` (§6.2) |
| `model_prices_and_context_window.json` | `GET /api/v1/models` (`context_length`, `top_provider.max_completion_tokens`, `pricing`) | the model registry's two feeds (§9) |

## 9. Model metadata (D15, D16)

`services/model_registry.py` holds three sources:

- **OpenRouter**, cached in the tracked `config/model_registry.json`, refreshed
  on a 24 h gate as before.
- **Models from the user's own servers**, registered at save time, kept in their
  own dict and persisted at `DATA_DIR/local_models.json`. They win lookups (they
  carry the loaded context), an OpenRouter refresh never replaces them, and they
  no longer write user model names into a tracked file.
- **LiteLLM's table**, fetched on the same 24 h gate (and on the first endpoint
  save if absent), trimmed to chat-mode entries and the four fields anything
  reads, cached at `DATA_DIR/litellm_models.json`. A failed fetch keeps the old
  copy. A lookup matches the exact key, else a key whose part after the first
  `/` equals the id, **only when exactly one does**: hosts size and price the
  same model differently, so an ambiguous match is no match.

`PricingService.get_pricing` resolves a reference to its block and, for a named
endpoint only, uses the price recorded at save time before the block's zero
default. Every other provider keeps its curated `pricing.json` behaviour.

## 10. Capabilities and the error contract

### 10.1 Declared, never sniffed

This extends `supports_model_listing` rather than adding a parallel mechanism.
**A flag ships only with a consumer.** The flags this change ships:

| Flag | Consumer |
|---|---|
| `auth.placeholder_key` | `resolve_credential` (§7) |
| `open_world_models` | `is_model_valid_for_provider` (replaces a hardcoded tuple) |
| `supports_model_listing` (existing) | `OpenAIProvider.fetch_models` |

`surface.responses` is deferred: nothing on the compat path needs it, and
`_model_policy`'s `provider_name == "openai"` branches are OpenAI's own
semantics, not compat quirks. Tool calling, structured outputs, embeddings and
streaming usage are **declared next, not now**.

### 10.2 The 2xx guard

A 2xx chat response with **no `choices`** and an error-shaped body is a routing
failure, not an empty completion. `OpenAIProvider._normalize` raises
`LLMErrorCategory.PROTOCOL` with a `public_message` naming the redacted URL,
and the unifier translates it to one `NodeUserError`. This is the single change
that would have made the original bug self-diagnosing.

### 10.3 Runtime errors

Every failure logs `url` (redacted by `redact_url`: no userinfo, query or
fragment) and `url_source` (`proxy`, `llm_defaults` or `sdk_default`, recorded
by `OpenAIProvider` when it is built), under the provider reference the call
was made with. The unifier logs before deciding whether to translate the
error, so agent steps, which take it untranslated to keep its structure, log it
too. The key is never logged. Per the repo's
`NodeUserError` contract, a routing or credential failure is user-correctable:
one WARN line, no traceback.

### 10.4 No declared error block

The four per-provider facts are rooting, auth, capability, and error contract.
The first three are declared; the **error contract is handled behaviourally** —
tolerant parsing plus the §10.2 guard. Declaring per-provider error shapes would
be a table with one entry and no way to keep it honest.

## 11. Adding

**A compatible vendor** (a fixed, public API):

1. Copy `base_url` **verbatim** from the vendor's docs. Do not normalize it.
2. Add a `providers.<name>` block to `llm_defaults.json` with `base_url` and any
   flag that differs from the permissive default, with a `_note` citing the doc
   URL for each non-default flag.
3. Add the name to `_COMPAT_PROVIDERS` in `providers/_compat.py`.
4. If the vendor documents no model-list route, set `supports_model_listing: false`.
5. Nothing else. No provider subclass, no `ProviderSpec` field (D11).

**A server you run**: Credentials, OpenAI-compatible, Base URL (with or
without `/v1`), an optional label and key, Add endpoint. No code.

## 12. Phases

Planned as eight phases (0 this RFC, 1 contract primitives, 2 runtime guards,
3 save-time rooting, 4 registry caches and the LiteLLM table, 5 the
`openai_compatible` provider, 6 the credentials panel, 7 docs). No feature flag.
Save-time probing is idempotent and records nothing in Temporal history, so
there is nothing to replay-protect.

It landed on 2026-09-24 as four revertible commits and eleven fixes from
review, merged to `main` as `261cf939`:

| Commit | Phases | Change |
|---|---|---|
| `fdc3b59b` | 1–4 | contract primitives, runtime guards and the 2xx guard, save-time rooting and the shared save path (kind detection and metadata included), registry caches and the LiteLLM table |
| `316d90cf` | 5 | the `openai_compatible` provider: registration, credential, catalogue hook, loaders, node, agent field, settings, pricing, RLM guard |
| `a305d896` | 6 | the credentials panel |
| `9283ada1` | 0, 7 | this RFC accepted; docs |
| `41b85f9c` … `f1375b21` | review | error wording for a named endpoint; the slug from host and port (§8.1); a rejected store write fails the save (§6.4); the time budget (§6.5); failure logs on agent paths (§10.3); one "not configured" wording and `Credential.is_configured` (§8.3); RLM's connected chat models (§15); a dependent model field that follows its endpoint; the global picker's refresh; docs |

No data migration. An existing Ollama or LM Studio row keeps working as
stored; the next Fetch re-roots it through §6 and replaces the old `"ollama"`
key on the LM Studio row with LM Studio's own placeholder.

## 13. Acceptance gates

| # | Assertion | Test |
|---|---|---|
| AG1 | No code appends, strips or rewrites a `/v1` fragment, and no `rstrip("/")`, under `services/llm/` or `nodes/model/` outside the AG2 allowlist | `tests/llm/test_rfc0003_source_contract.py` (AST) |
| AG2 | The allowlist is exactly `endpoints.py` and `_local_validator._strip_v1_path` | same |
| AG3 | Rooting adopts only an OpenAI list; order, rewrite, rejected-key and connection-failure outcomes | `tests/llm/test_endpoint_resolution.py` (respx, real SDK) |
| AG4 | Client construction does no IO; only the save path calls `resolve_base_url` | `test_rfc0003_source_contract.py` |
| AG6 | A failed save writes no row and broadcasts nothing; a rejected key-row write puts the URL row back as it was | `tests/nodes/test_local_llm_save.py` |
| AG7 | No placeholder literal in any provider `__init__` | `test_rfc0003_source_contract.py` |
| AG8 | With `OPENAI_API_KEY` set and no key, keyed providers raise `NodeUserError` and issue zero requests | `tests/llm/test_provider_refs.py` |
| AG9 | Every `llm_defaults.json` provider key has a reader; the known-dead keys are an explicit list | `test_rfc0003_source_contract.py` |
| AG10 | 2xx with no `choices` and an error body raises `PROTOCOL` naming the redacted URL | `tests/llm/test_protocol_guard.py` |
| AG11 | No public message carries userinfo, a query string or `Bearer`; `redact_url` cases; a failure the unifier does not translate still logs `url` and `url_source` under the call's reference | `test_protocol_guard.py`, `test_endpoint_resolution.py` |
| AG12 | `ProviderSpec`'s field set is unchanged | `test_rfc0003_source_contract.py` |
| AG13 | Kind detection by body shape, including an LM Studio 200-with-error that is not Ollama; the three routes asked at once | `test_local_llm_save.py` |
| AG14 | Metadata per kind; LiteLLM never consulted for a local kind; the SDK probes still called, and a hung one fails the save within its bound | `test_local_llm_save.py` |
| AG15 | Endpoint add / refresh / remove / catalogue, against the real encrypted store; the slug never from userinfo; `is_configured` checks the endpoint a node names | `tests/credentials/test_openai_compatible_credential.py` |
| AG16 | A reference resolves config, registry and pricing fallbacks to its block and is open-world; the unifier reads its URL row and refuses a missing one; an unsaved endpoint is reported as not configured on every path: the same words from the in-process agents, the chat model and the Temporal step, and MISSING_CREDENTIAL from the workflow validator | `test_provider_refs.py`, `test_endpoint_resolution.py`, `tests/services/test_native_agent_runtime.py`, `tests/temporal/test_agent_llm_contract.py`, `tests/test_workflow_validator.py` |
| AG17 | `openai_compatible` is registered once, `OpenAIProvider`, no `base_url` | `tests/llm/test_provider_self_registration.py` |
| AG18 | Refresh keeps local models; caches under DATA_DIR only; LiteLLM trimming and matching; the on-demand fetch is time-boxed and not retried at once after a failure | `tests/services/test_model_registry_caches.py` |
| AG19 | Every agent `provider` field is loader-driven and offers every registered provider except the bare endpoint id | `tests/llm/test_plugin_shape.py` |
| AG20 | RLM refuses a provider it cannot route, as its own or a connected chat model's; the node, loaders and `ProviderRef` validation | `tests/nodes/test_openai_compatible_node.py` |
| FE | The endpoint list renders rows and hands the clicked row back; the panel's add, refresh and remove payloads and the probe budget; a node's status dot reads its own credential; a dependent field moves when its parent changes; the global picker re-fetches when endpoints change | `client/.../panels/__tests__/EndpointList.test.tsx`, `ApiKeyPanel.test.tsx`, `lib/__tests__/credentialProviderId.test.ts`, `lib/__tests__/dynamicOptions.test.ts`, `hooks/__tests__/storedCredentialSignature.test.ts` |

AG5 (three verdict states rendered) is retired with D4. The wording of the
model-scoped errors for a named endpoint is locked by
`tests/llm/test_protocol_codec.py`.

## 14. What this RFC does not change

`ChatUnifier` stays the single facade. `ProviderSpec` keeps its shape (D11).
`sdk_exception_refs` stay lazy `"module:ClassName"` strings — no SDK import at
registration, per `tests/llm/test_lazy_sdk_imports.py`. `OpenRouterProvider`
keeps its own `__init__` and its hardcoded base; it is correct per vendor docs.
Temporal workflow code is untouched: a provider reference is an ordinary
string in existing payload fields.

The JSON keys nothing reads are `api_key_param` and `max_tokens_param` (the
draft of this RFC named `models_endpoint`, `api_key_format` and
`extra_headers`; the first and last do have readers, and `api_key_format` is
not a JSON key). They are **not deleted here** — a subtractive change with its
own risk, tracked separately; AG9 lists them so no new one appears.

## 15. Compatibility audit

All nine compat `base_url` values in `llm_defaults.json` match their vendor
docs and need no change; in particular `deepseek` is path-less and correct.
Any future change that "corrects" the JSON toward `/v1` is a regression.

Servers the user runs:

| Server | Rooted at | Kind | Notes |
|---|---|---|---|
| Ollama | `/v1` | `ollama` | placeholder key `ollama`; only loaded models are listed, with their live context |
| LM Studio | `/v1` | `lmstudio` | answers 200 to unknown routes (§2.1); its optional API tokens cannot be used yet, because its panel has no key field |
| llama.cpp | root and `/v1` | `llamacpp` | tool calling needs the server started with `--jinja` (stated in the panel instructions) |
| vLLM | `/v1` | `generic` | `--api-key` protects `/v1`, so a wrong key reads "Found an OpenAI-compatible server at …/v1, but it rejected the API key."; `max_model_len` sizes each model |
| LiteLLM proxy | root and `/v1` | `generic` | a virtual key goes in the API key field; bare model names (`gpt-4o`) are sized and priced from LiteLLM's own table |

**RLM** builds its own clients from `services/rlm/constants.py` for six
providers and never reads a user base URL, so it cannot use a local server or
a named endpoint. It now refuses such a provider with a clear message instead
of sending the request to api.openai.com, both as the agent's own provider and
as a chat model connected to it (whose provider comes from
`detect_ai_provider`: chat-model nodes carry no `provider` field, and reading
one sent every connected chat model to the OpenAI backend). Routing it properly
is separate work.

## 16. References

- openai-python `_base_client.py` — `_prepare_url:496`, `_enforce_trailing_slash:414`
- openai-python `lib/azure.py` — `azure_endpoint` / `base_url` mutual exclusion
- DeepSeek https://api-docs.deepseek.com/ — base `https://api.deepseek.com`
- Groq https://console.groq.com/docs/openai — `/openai/v1`
- OpenRouter https://openrouter.ai/docs/quickstart — `/api/v1`; models API https://openrouter.ai/api/v1/models
- Fireworks https://docs.fireworks.ai/tools-sdks/openai-compatibility — `/inference/v1`
- Sarvam https://docs.sarvam.ai/api/getting-started/models/open-source — `/v1` and `/v2`
- LM Studio https://lmstudio.ai/docs/app/api/endpoints/openai; native list https://lmstudio.ai/docs/developer/rest/list
- LM Studio 200 on unknown routes https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1323, https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/2085
- Ollama https://docs.ollama.com/openai
- vLLM https://docs.vllm.ai/en/latest/cli/serve.html — `--root-path`
- llama.cpp https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — `--api-prefix`, `/props`, `sk-no-key-required`
- LiteLLM https://docs.litellm.ai/docs/providers/openai_compatible; https://docs.litellm.ai/docs/proxy/user_keys; table https://github.com/BerriAI/litellm (`model_prices_and_context_window.json`)
- Open WebUI https://docs.openwebui.com/reference/api-endpoints/
