# Sarvam AI Service

> **`server/nodes/sarvam/` no longer exists.** Every capability it served is now
> a *provider* inside an abstracted node, so a workflow picks Sarvam from a
> dropdown rather than picking a Sarvam-specific node:
>
> | Was | Now |
> |---|---|
> | `sarvamTranslate` | `translateText` with `provider: "sarvam"` |
> | `sarvamTransliterate` | `transliterateText` with `provider: "sarvam"` |
> | `sarvamDetectLanguage` | `detectLanguage` with `provider: "sarvam"` |
> | `sarvamTextToSpeech` | `textToSpeech` with `provider: "sarvam"` |
> | `sarvamSpeechToText` | `speechToText` with `provider: "sarvam"` |
>
> The wire logic was ported verbatim into
> [`nodes/translate/_providers/sarvam.py`](../server/nodes/translate/_providers/sarvam.py)
> and [`nodes/speech/_providers/sarvam.py`](../server/nodes/speech/_providers/sarvam.py).
> `sarvamChatModel` under `nodes/model/` is untouched and remains vendor-named,
> which is correct — chat models are selected by name.
>
> This page is retained for the Sarvam-specific API detail (auth, language
> vocabulary, per-model caps, the INR pricing conversion), which is still
> accurate and still lives in those provider modules. For the pattern, see the
> [Speech Provider RFC](./speech_provider_rfc.md).

Indic-first AI: two chat models plus five REST APIs for translation,
transliteration, language identification, speech-to-text and text-to-speech.

Sarvam is unusual in this codebase because **one API key spans two very
different auth styles**. Its chat endpoint is OpenAI-compatible and accepts
`Authorization: Bearer`; every other endpoint accepts only
`api-subscription-key`. Both are served by a single stored credential.

| | |
|---|---|
| **Provider id** | `sarvam` |
| **Base URL** | `https://api.sarvam.ai` (chat at `/v1`) |
| **Auth** | `api-subscription-key: <key>`; the chat route also accepts `Authorization: Bearer <key>` |
| **Credential class** | [`SarvamCredential`](../server/nodes/model/_credentials.py) (`_LLMApiKey` → `ApiKeyCredential`) |
| **Chat plugin** | [`server/nodes/model/sarvam_chat_model/`](../server/nodes/model/sarvam_chat_model/) |
| **Service plugins** | Retired — now providers in [`nodes/translate/_providers/sarvam.py`](../server/nodes/translate/_providers/sarvam.py) and [`nodes/speech/_providers/sarvam.py`](../server/nodes/speech/_providers/sarvam.py) |
| **Palette groups** | `model` (chat), `language` (services) |
| **Official docs** | <https://docs.sarvam.ai> |

---

## Chat models — an OpenAI-compat provider

`sarvamChatModel` is a plain `ChatModelBase` subclass. The provider itself is
registered declaratively in
[`_compat.py`](../server/services/llm/providers/_compat.py) — one entry in
`_COMPAT_PROVIDERS`, `factory=OpenAIProvider`, `base_url` pinned from
`llm_defaults.json`. No `SarvamProvider` class exists and none is needed.

| Model | Context | `max_output_tokens` in JSON | Notes |
|---|---|---|---|
| `sarvam-105b` | 131072 | 4096 | Flagship, `default_model` |
| `sarvam-30b` | 65536 | 4096 | Cheaper tier |

`sarvam-m` is deprecated and removed from the API; it is deliberately absent.

### The output cap is a billing tier, not a model limit

This is the one provider here whose output ceiling depends on the **account**
rather than the model. Sarvam publishes:

| Tier | `sarvam-105b` | `sarvam-30b` |
|---|---|---|
| Starter | 4096 | 4096 |
| Pro | 16384 | 8192 |
| Business | 128000 | 64000 |

Exceeding your tier is a hard 400:

```
max_tokens (65536) exceeds the maximum allowed for sarvam-105b
for your subscription tier (starter): 4096.
```

`resolve_max_tokens` ([`llm/config.py`](../server/services/llm/config.py))
uses this number as **both** the default budget when a node leaves
`max_tokens` unset **and** the ceiling it clamps larger user values down to.
One static value therefore has to hold for every account, so the shipped
figure is the **Starter cap** — the only one that succeeds on all tiers.
Sarvam's own API default is 2048, so 4096 is already generous.

**On Pro or Business**, raise these numbers in `llm_defaults.json` to your
tier's ceiling. Setting `max_tokens` on the node is *not* enough — the
resolver clamps it straight back down to whatever is written here. Do not
simply use the Business figures as the default: they are ~98% of each context
window and leave no room for a prompt.

The context windows are untouched by any of this — those are real model
properties (131072 / 65536) and drive compaction thresholds.

Pinned by `TestSarvamSubscriptionTierCap` in
[`tests/llm/test_max_tokens_resolution.py`](../server/tests/llm/test_max_tokens_resolution.py),
which also guards against the assertions passing off the registry's hardcoded
fallbacks.

### Reasoning is entirely JSON-driven

Sarvam runs reasoning **on by default** at `reasoning_effort: "medium"` and
returns the trace in `message.reasoning_content`. Both halves are already
handled generically:

- `thinking_type: "effort"` → [`openai.py:101-104`](../server/services/llm/providers/openai.py) sets `params["reasoning_effort"]`
- `reasoning_content` → [`openai.py:365-371`](../server/services/llm/providers/openai.py) lifts it into a `reasoning` `ContentBlock`, surfacing as `thinking`

Two keys are **deliberately omitted** from the JSON block, and re-adding either
is a bug:

| Key | Why it must stay out |
|---|---|
| `thinking_default_on` | Emits Moonshot's proprietary `extra_body.thinking = {"type": "disabled"}`. Sarvam documents no way to disable reasoning and never defined that field. |
| `reasoning_models` | Would set `temperature_allowed = False` and pin `temperature` to 1.0. Sarvam accepts temperature alongside reasoning (its own default is 0.5 with reasoning on, 0.2 without). |

With both omitted the behaviour is right on **both** toggle states: thinking
off sends no `reasoning_effort` and Sarvam applies its own default; thinking on
sends ours.

### No model-list endpoint — the `supports_model_listing` flag

**Sarvam ships no `/v1/models` route.** Verified against
<https://docs.sarvam.ai/openapi.json>: 25 paths, none for model listing.

This matters more than it sounds. `OpenAIProvider.fetch_models` calls
`client.models.list()`; the resulting 404 arrives as an `openai.OpenAIError`,
which `ChatUnifier.fetch_models` converts to `NodeUserError`, which
`AIService.fetch_models` **re-raises before reaching its curated fallback**
([`ai.py:696`](../server/services/ai.py)). Unhandled, a perfectly valid Sarvam
key would fail validation in the Credentials modal *and* leave the model
dropdown empty.

The fix is a generic JSON flag rather than a Sarvam-shaped subclass:

```json
"supports_model_listing": false
```

[`OpenAIProvider.fetch_models`](../server/services/llm/providers/openai.py)
reads it via `services.llm.config.supports_model_listing(provider)` and, when
false, serves `curated_models(provider)` and validates the key with a
one-token chat completion instead. An invalid key still raises the typed SDK
error the unifier knows how to translate, so it remains a real credential
check.

The flag **defaults to `true`**, so every other provider is untouched —
`tests/llm/test_model_listing_fallback.py` asserts exactly that, and that
Sarvam is the only opt-out.

`popular_models` is `[]` per the repo's ≥1M-context policy (Sarvam tops out at
131072), so `curated_models` falls through to the `max_output_tokens` keys.
Both the per-node dropdown and the global model selector still list both
models.

---

## Service capabilities (historical layout: `server/nodes/sarvam/`)

Five stateless REST nodes, palette group `language`, all
`usable_as_tool = True` and `TaskQueue.REST_API`. There is no `_service.py`:
nothing here owns long-lived state, so the folder is helpers plus node files.

| Node type | Tool name | Endpoint | Billing unit |
|---|---|---|---|
| `sarvamTranslate` | `sarvam_translate` | `POST /translate` | characters |
| `sarvamTransliterate` | `sarvam_transliterate` | `POST /transliterate` | characters |
| `sarvamDetectLanguage` | `sarvam_detect_language` | `POST /text-lid` | characters |

Sarvam's two speech endpoints are no longer nodes in this folder. They are
reached through the provider-abstracted `textToSpeech` / `speechToText` nodes
in [`server/nodes/speech/`](../server/nodes/speech/), where Sarvam is one
provider among several; the wire logic was ported verbatim into
[`_providers/sarvam.py`](../server/nodes/speech/_providers/sarvam.py). See
[Speech Provider RFC](./speech_provider_rfc.md).

All authenticate through `ctx.connection("sarvam")`, which injects
`api-subscription-key` via `ApiKeyCredential.inject` — no per-node auth code.

### Translate vs transliterate

Routinely confused, and the skill spells it out for the LLM: translate changes
the **language** ("Hello" → "नमस्ते"); transliterate changes the **script**
while preserving the words ("namaste" → "नमस्ते"). The nodes reject an
identical source/target pair rather than burning a paid call on a no-op.

Per-model input caps are enforced client-side (`mayura:v1` 1000 chars,
`sarvam-translate:v1` 2000, `/text-lid` 1000) so the LLM gets an actionable
message instead of an opaque 422.

### Speech: moved to the provider-abstracted nodes

The multipart plumbing this folder introduced is still load-bearing and still
generic: `Connection.request` gained a `files=` parameter
([`connection.py`](../server/services/plugin/connection.py)), threaded into
**both** the initial request and the auth-retry rebuild so a 401 replays the
file parts instead of an empty body. Pass file *bytes*, never an open handle,
for exactly that reason.

Two behaviours that were discovered here and are preserved in the speech
plugin:

- **Sarvam returns TTS audio as base64 inside JSON, and `audios` is an
  array.** Long input comes back as several standalone clips, each with its
  own container header, so concatenating them byte-wise produces audio that
  plays only the first chunk. The node writes one file per clip and says so.
- **Timestamps and diarization are Batch-API-only.** The synchronous endpoint
  returns them as `null` regardless of what is requested.

One claim from the original implementation did **not** survive verification:
it asserted the synchronous endpoint "caps at 30 seconds of audio", and billed
every transcription at that ceiling. Sarvam's only 30-second reference is
about *response latency*, not audio duration. The speech node measures real
duration instead.

### The mark itself

The two SVGs carry Sarvam's **official mandala mark**, taken verbatim from
their published brand asset
(`https://assets.sarvam.ai/assets/brand/logos/sarvam-logo-black.svg`) — the
same geometry they ship as `sarvam.ai/favicon.svg`. Path data was extracted
programmatically, not redrawn.

Sarvam is a **monochrome-logo brand**: the mark is pure black or pure white,
with no coloured variant. That is a problem here, because the frontend
requests `/api/schemas/nodes/<type>/icon` with **no `?variant=` parameter**
(the backend supports `light` / `dark` and `icon.dark.svg`, but nothing calls
it). A single monochrome file would therefore be invisible on roughly half of
the twelve themes.

So the mark is composed onto a rounded tile in Sarvam's primary navy
`#1E2033`, drawn in white — the same "avatar" treatment `@lobehub/icons` uses
for OpenAI, Groq, OpenRouter, Ollama and LM Studio, five of which this repo
already renders. That gives guaranteed contrast on every theme from one file,
with no reliance on `prefers-color-scheme` (which tracks the OS, not the app's
`data-theme`).

If the frontend ever starts requesting `?variant=`, dropping Sarvam's white
logo in as `icon.dark.svg` and the navy one as `icon.svg` would be the more
faithful treatment.

### Colours

| Token | Value | Source |
|---|---|---|
| Icon tile | `#1E2033` | Sarvam's primary navy — their body text colour and hero gradient base |
| Icon mark | `#FFFFFF` | The official white logo |
| Node accent / palette group / catalogue | `#6A88E2` | Sarvam's interactive accent (their `#4250D5` → `#6A88E2` control gradient) |

The navy is deliberately *not* reused as the node accent: `--node-color`
drives the canvas node's border, and `#1E2033` is darker than the dark-theme
canvas background, so the border would disappear. The accent blue reads on
both light and dark surfaces.

---

## Registration checklist

Everything Sarvam touches, for reference when adding the next provider:

| File | What |
|---|---|
| [`llm_defaults.json`](../server/config/llm_defaults.json) | `providers.sarvam` block (`base_url` required, `supports_model_listing: false`) |
| [`_compat.py`](../server/services/llm/providers/_compat.py) | `"sarvam"` in `_COMPAT_PROVIDERS` |
| [`llm/config.py`](../server/services/llm/config.py) | `curated_models()` / `supports_model_listing()` helpers |
| [`llm/providers/openai.py`](../server/services/llm/providers/openai.py) | generic no-listing branch in `fetch_models` |
| [`constants.py`](../server/constants.py) | `AI_CHAT_MODEL_TYPES` + a `detect_ai_provider` branch — **without the latter the node silently calls api.openai.com** |
| [`llm/protocol.py`](../server/services/llm/protocol.py) | `provider_names["sarvam"]` for user-facing errors |
| [`model_registry.py`](../server/services/model_registry.py) | `DEFAULT_TEMP_RANGES["sarvam"]` |
| [`node_output_schemas.py`](../server/services/node_output_schemas.py) | `_CHAT_MODEL_TYPES` |
| [`nodes/model/_credentials.py`](../server/nodes/model/_credentials.py) | `SarvamCredential` |
| [`credential_providers.json`](../server/config/credential_providers.json) | catalogue entry (`extends: "_ai_base"`) |
| [`node_allowlist.json`](../server/config/node_allowlist.json) | `sarvamChatModel` in `enabled_nodes` (normal-mode visible) |
| [`groups.py`](../server/nodes/groups.py) | `language` palette group |
| [`pricing.json`](../server/config/pricing.json) | `llm.sarvam` + `api.sarvam` + `operation_map.sarvam` |
| Agent dropdowns | nothing to edit today: every agent's `provider` field is the loader-driven `ProviderRef` ([_provider.py](../server/nodes/agent/_provider.py), `aiProviders` loader), which lists every registered provider. When Sarvam landed these were three `Literal` lists; `test_plugin_shape.py::test_all_registered_providers_are_agent_selectable` now locks the loader instead |
| Frontend | [`aiModelProviders.ts`](../client/src/lib/aiModelProviders.ts), [`AIProviderIcons.tsx`](../client/src/components/icons/AIProviderIcons.tsx) |

## Tests

| File | Locks |
|---|---|
| [`tests/llm/test_model_listing_fallback.py`](../server/tests/llm/test_model_listing_fallback.py) | The `supports_model_listing` contract both ways, incl. that Sarvam is the only opt-out and every other provider still calls `models.list()` |
| [`tests/nodes/test_translate.py`](../server/tests/nodes/test_translate.py) + [`test_speech.py`](../server/tests/nodes/test_speech.py) | Sarvam is covered as a *provider* now: native auth header, single-input body shape, per-model caps, v2/v3 param gating, and the base64-array TTS response |
| [`tests/services/test_connection_multipart.py`](../server/tests/services/test_connection_multipart.py) | `Connection.request(files=)` incl. the auth-retry replay |
| `tests/llm/test_provider_self_registration.py` | Sarvam in the compat matrix + its pinned `base_url` |
| `tests/llm/test_live_providers.py` | Opt-in live smoke, gated on `SARVAM_API_KEY` |

## Known limits

- Speech-to-text: synchronous endpoint only (Batch API not wired); no timestamps, no diarization.
- No streaming for chat, TTS or STT — the WebSocket/HTTP-stream surfaces are not wired.
- Document digitization, pronunciation dictionaries and voice cloning are out of scope.
- `wiki_grounding` (a Sarvam-specific chat param) is **not exposed**: an extra
  `Params` field cannot reach the API today, because `execute_chat` reads a
  closed key set off `flattened` and drops the rest. Adding it needs a generic
  `provider_options` passthrough seam through `ChatUnifier` → `OpenAIProvider`
  `extra_body`; shipping the field without that seam would render a checkbox
  that does nothing.
