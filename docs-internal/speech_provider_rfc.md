# RFC: Multi-provider Speech (TTS / STT)

**Status:** implemented (commit `a29cdb7e`)
**Date:** 2026-07-25
**Supersedes:** the vendor-locked `sarvamTextToSpeech` / `sarvamSpeechToText` nodes

**Reading status:** Sections 1–2 record the pre-implementation problem and
measurements from July 2026. They are historical rationale, not current upload
or retry instructions. Current speech nodes use workspace audio references;
oversized payload failures are non-retryable after Phase 1b.26. See
[Media transport](media_transport.md) for current limits, error mapping and file
handling, and the current per-node cards for the actual schemas.

---

## 1. Problem

Speech exists in exactly one place today: two Sarvam nodes. The pattern does not generalise — a
second vendor means a second pair of nodes, a third a third pair, each with its own params, its own
credential, its own skill file. Chat models solved this years ago with `services/llm/`; speech has
no equivalent.

Adding one is not just a refactor, because **audio is big and the execution engine is not built for
big**. That constraint, measured rather than assumed, drives nearly every decision below.

---

## 2. The measured constraint

| Limit | Value | Source |
|---|---|---|
| Temporal blob **error** — activity result, activity **input**, workflow result | **2,097,152 B** | server default; no custom `DataConverter` or `PayloadCodec` anywhere in the repo |
| Temporal blob **warning** | 524,288 B → `PayloadSizeWarning` | not covered by `pyproject.toml` `filterwarnings`, so it surfaces |
| Retries burned before the failure is reported | **3** | `_PayloadSizeError` absent from `NON_RETRYABLE_ERROR_TYPES` in [`_retry_policies.py`](../server/services/temporal/_retry_policies.py) |
| Legacy `execute_node_activity` internal WS | 4 MiB inbound (aiohttp default, `max_msg_size` unset) | the `ws_connect` call in `NodeExecutionActivities.execute_node_activity` ([`activities.py`](../server/services/temporal/activities.py)) |
| `node_outputs.data` | no cap; written **3×** per store (`output_main`/`output_top`/`output_0`) | `store_node_output_activity` in [`activities.py`](../server/services/temporal/activities.py) |
| WS broadcast | no size guard; retained in `_status` **forever** and replayed to every newly connecting client. Since this RFC, `update_node_output` caches an output larger than `_CACHED_OUTPUT_MAX_BYTES` as a stub (`_elide_for_cache`); `update_node_status` still caches its `data` as given | `StatusBroadcaster._status`, `update_node_status` and `update_node_output` in [`status_broadcaster.py`](../server/services/status_broadcaster.py) |
| In-memory `_outputs` | never evicted except by `clear_all_outputs` | `WorkflowService._outputs` and `clear_all_outputs` in [`workflow.py`](../server/services/workflow.py) |

### What actually happens to a 12 MB base64 TTS result

1. The activity result exceeds 2 MiB → temporalio raises `_PayloadSizeError` at the converter.
2. It is not marked non-retryable → **3 attempts**, each re-invoking the provider and re-billing it.
3. The user sees a generic activity failure, not "audio too large".

And the same limit applies to activity **inputs**, so a ~2 MB audio upload fails before the node
runs at all — today's only file-input path is base64-through-parameters.

### The multiplication

One audio payload is copied at least six ways: `_serialize_result` → `node_outputs` ×3 → WS
broadcast ×2 → `_status` cache (forever) → Temporal activity result → `MachinaWorkflow` aggregate →
every downstream activity input → and, because these nodes are `usable_as_tool`, verbatim into an
LLM message.

**Conclusion: audio cannot travel as bytes.** Not "should not" — the engine will not carry it.

---

## 3. Decisions

### D1 — `AudioRef` is structurally incapable of carrying bytes

A Pydantic model with `extra="forbid"`, a workspace-**relative** path, and no base64 field. Ever.
It serializes to ~400 B, i.e. ~5,200 refs before approaching the Temporal error limit.

Rejected alternative: a capped inline-base64 opt-in, as the current Sarvam TTS node has
(`_MAX_INLINE_B64 = 1_000_000`). The decisive argument is `usable_as_tool` — `execute_as_tool`
returns the flat `Output` dict straight into an LLM message and there is no mechanism to tell a
model "skip this field". A capped opt-in is *sometimes* catastrophic, which is worse than
always-broken because it passes review.

Relative paths (not absolute) because absolute paths embed the mutable workflow slug, leak the
operator's home directory into the DB / WebSocket / LLM context, and cannot be safely served over
HTTP.

### D2 — Two Protocols and two registries, not one `SpeechProvider`

AssemblyAI is STT-only; Cartesia is TTS-only. A single Protocol forces those vendors to ship a dead
method that raises — invisible to the node layer, so the provider would appear in the dropdown and
fail at runtime.

With `TtsProvider` and `SttProvider` registries, **direction capability is registry membership**:
the TTS node's provider enum is literally `tts_providers()` (`server/nodes/speech/_registry.py`). Zero extra machinery.

### D3 — Capability-driven common params + a `provider_options` escape hatch

The verified provider surface diverges much harder than the chat-completions surface does:

| | OpenAI | ElevenLabs | Deepgram |
|---|---|---|---|
| Auth header | `Authorization: Bearer` | `xi-api-key` | `Authorization: Token` |
| Voice selection | body param `voice` | **URL path segment** | encoded in the model name (`aura-2-thalia-en`) |
| Output format | `response_format` enum (`mp3\|opus\|aac\|flac\|wav\|pcm`) | **query** `output_format=mp3_44100_128` (container+rate+bitrate in one token) | separate `encoding` + `container` + `sample_rate` |
| Tuning | `instructions` (free text), `speed` | `voice_settings{stability, similarity_boost, style, use_speaker_boost, speed}` | — |

A per-provider `displayOptions` matrix over 8 providers explodes combinatorially and *still* cannot
express "ElevenLabs `stability` applies only to `eleven_multilingual_v2`". So: ~11 common fields
covering the 90 % case, normalised by each provider adapter, plus a `provider_options: dict`
passed through untouched for vendor specifics.

Keeps the tool schema flat — no `$defs` / `$ref`, which `usable_as_tool` requires.

### D4 — Capabilities declared in JSON, not code

`server/config/speech_defaults.json`, one block per provider per direction, overridable per model —
the same shape `llm_defaults.json` uses for `max_output_tokens`. Drives the provider dropdown, the
voice loader, and validation from one place. `test_plugin_shape.py` bans `if provider == "x"`
branches in shared code; this is how we avoid needing any.

### D5 — First multi-credential node in the repo

`credentials = (OpenAICredential, ElevenLabsCredential, …)` with `ctx.connection(params.provider)`.
Already supported: `_make_connection_factory` ([`base.py:_make_connection_factory`](../server/services/plugin/base.py))
builds a dict over **all** declared credentials and raises only for undeclared ids. No node uses
this today.

Consequence: the node must use imperative `@Operation` bodies — the declarative `routing=` path
hardcodes `self.credentials[0]` ([`BaseNode._run_operation`](../server/services/plugin/base.py)).

### D6 — Extract the generic registry rather than copy it

`ProviderSpec` + lazy `"module:Class"` `sdk_exception_refs` + idempotent registration is entirely
provider-agnostic. Copying it into `services/speech/` would fork the boot-time-import-avoidance
logic that exists specifically to keep ~7 s (warm) / ~45 s (cold) of SDK imports off startup.

`services/provider_registry.py`; `services/llm/registry.py` becomes a shim.
**Success criterion: `server/tests/llm/` passes untouched.** Met — 168 tests at the time, no edits (the suite has since grown; `pytest tests/llm --collect-only -q` is the live count). `_REGISTRY`
survives as an alias bound to the *same dict object* the registry mutates, because several tests
swap provider factories in place through it.

A companion `provider_clients.py` service (the lease-counted client cache) was scoped here and
deliberately **not** built. See §4: speech makes one HTTP call per node execution, so the cache has
no second consumer and would have earned only a shutdown hook in `main.py`.

### D7 — `tinytag` for duration and format

| Candidate | Native deps | Windows | License | Verdict |
|---|---|---|---|---|
| stdlib `wave` | none | ✅ | PSF | WAV only — insufficient alone |
| **`tinytag`** | **none** | ✅ | **MIT** | **chosen** |
| `mutagen` | none | ✅ | **GPL-2.0** | rejected — this repo is MIT; a licensing call, not a technical one |
| `pydub` | ffmpeg on PATH | ❌ | MIT | rejected |
| `ffprobe` | external binary | ❌ | LGPL/GPL | rejected — a pooch-managed binary for a 4-line metadata read |

Fallback chain: `tinytag` → stdlib `wave` → raw-PCM arithmetic → give up.

### D8 — Inspection never fails a workflow

Unknown format returns an all-`None` probe, logs at DEBUG, and still produces a valid `AudioRef`.
Duration-based **rejection only fires when duration is known**; unknown duration falls through to
the byte cap.

A metadata-parser miss on some codec variant must never turn a working workflow into a failing one.
Degrading a billing *estimate* is acceptable; hard-failing a valid file is not.

### D9 — The result-size guard ships WARN-only first

A generic size check in `BaseNode._serialize_result` is the highest-value safety net available: it
converts a 31-second silent triple-retry into an immediate, actionable error. But flipping straight
to hard-fail risks breaking a node that legitimately returns a large result today (a document
parser emitting 3 MB of extracted text).

Ship the warning, read one release of logs, then flip. Both thresholds behind `Settings`.

Implementation note: raise **`NodeUserError`** — it is already in `NON_RETRYABLE_ERROR_TYPES`, so
the failure is immediate with zero new registrations. Do **not** subclass it: Temporal matches
`non_retryable_error_types` on the exception **type-name string**, so a subclass silently starts
retrying again.

---

## 4. Architecture

Speech is a **plugin**, not a service. Everything vendor-specific lives in the plugin folder;
only genuinely shared machinery sits under `services/`.

```
services/provider_registry.py   generic ProviderSpec + registry      (shared with services/llm)

services/media/                 audio transport -- vendor-neutral, reusable for image/video
  refs.py       AudioRef
  workspace.py  write_audio / resolve_media / read_media_bytes / coerce_file_param
  inspect.py    tinytag -> wave -> PCM arithmetic; never raises
  limits.py     every size constant, one place

nodes/speech/                   the whole speech surface
  text_to_speech.py             the two nodes
  speech_to_text.py
  _protocol.py                  TtsProvider / SttProvider, requests, results, SpeechError
  _registry.py                  two registries over the generic one
  _config.py                    reads server/config/speech_defaults.json
  _unifier.py                   dispatch + typed-error -> NodeUserError
  _providers/                   _http.py, _openai_compat.py (openai+groq), elevenlabs.py,
                                deepgram.py, sarvam.py
  _credentials.py               ElevenLabs + Deepgram (the rest are shared with nodes/model)
  _base.py, _option_loaders.py

config/speech_defaults.json     operator-editable capabilities
routers/workspace.py            GET file (Range-capable) + POST upload
```

**Why not `services/speech/`.** An earlier draft put the provider layer under `services/`, mirroring
`services/llm/`. That mirror is misleading: `services/llm` earns its place because agents, chat
models and a dozen nodes all consume it, whereas speech has exactly two consumers and both live in
`nodes/speech/`. Per-vendor code under `services/` is the pattern Wave 11.I explicitly retired --
`services.whatsapp_service`, `services.maps` and `services.nodejs_client` all moved into plugin
folders, and `test_plugin_self_containment.py` names them in a forbidden-import list. Speech would
have been the next entry on it.

**Layering rule.** The speech layer takes credential **id strings**, never `Credential` classes.
`nodes/speech/_config.credential_id(provider)` reads the id from JSON, and a test asserts every
registered provider's id resolves in `CREDENTIAL_REGISTRY`. Cross-plugin *credential* imports are
fine and idiomatic (`from ..model._credentials import OpenAICredential`, exactly what
`nodes/translate/` does today and the since-retired `nodes/sarvam/` did before it); what stays
out is any `services/` module knowing a vendor name.

**No client cache.** `ChatUnifier` keeps a lease-counted LRU because an agent loop makes many model
calls inside one node execution. Speech is the opposite shape -- one HTTP request per execution,
where client setup is invisible next to a multi-second synthesis. Caching would buy nothing and cost
a process-wide singleton plus a shutdown hook wired into `main.py`. This also settles the question
left open when the registry was extracted: no second consumer materialised, so no shared client
cache was built.

---

## 5. Bugs this work closes

1. **Path traversal in `sarvam_speech_to_text`.** `_read_audio` did `Path(workspace_dir) / raw`
   with no containment check, so `audio_file="../../credentials.db"` read the encrypted credential
   store and uploaded it to the provider. `coerce_file_param` closes it by construction, and the
   node that carried the bug no longer exists.
2. **Flat 30-second billing.** The same node charged every transcription as 30 seconds, with the
   comment "we do not decode the clip to measure it". It also justified the figure as a documented
   endpoint limit, which the docs do not actually say (§7). Duration is now measured.
3. **`workspace_root()` resolved by id where the directory is named by slug.** Introduced by this
   work's own first wave: the ctx-less fallback composed `workspaces/<workflow_id>/`, a path that
   never exists, because `WorkflowService._get_workspace_dir` names those directories after
   `Workflow.slug`. It stayed invisible because every caller happened to pass a `NodeContext` — the
   workspace HTTP route is the first that cannot. The fallback now raises rather than guessing, and
   the route does the id → slug lookup itself, since that needs a database read and `services.media`
   is synchronous by contract. `core.paths.workspace_dir`'s parameter was renamed to
   `workflow_slug` so the next reader is not misled the same way.
4. **Workflow rename orphans every workspace file.** `rename_workflow` is a single-row `UPDATE`; the
   directory is never moved, and `_get_workspace_dir` keys on the mutable slug. Pre-existing and not
   audio-specific, but `AudioRef` makes it fixable — a best-effort `os.replace` in the rename path.
   Still outstanding.

### On migrating existing workflows

None was written. The two Sarvam speech nodes shipped only days before this work and no saved
workflow references them, so a `workflow_migrations` entry would have been dead code guarding
against a case that does not exist. Should one appear, the graph rewrite is the easy half; the
parameter half is best-effort, because only 2 of the 5 migration call sites pass `node_parameters`
and neither of those is the load path.

---

## 6. Explicitly out of scope

| Not doing | Why |
|---|---|
| Streaming / realtime speech | `@Operation` returns one Pydantic model; no `AsyncGenerator` support, no SSE anywhere in the repo. A genuinely new operation kind. |
| Keying workspaces on `workflow_id` | Changes the on-disk layout for every existing install, invalidates `--add-dir` paths baked into live Claude sessions, and reverses a documented decision. Best-effort directory rename instead. |
| Deduping the 3× `store_node_output` write | Touches `ParameterResolver`, the `socialReceive` four-handle case, and `store_agent_output` to save ~1.2 KB once refs replace blobs. |
| Removing the double `node_status` + `node_output` broadcast | Frontend-visible contract change. Elide the cached payload instead so the duplication is bounded. |
| Fixing `MachinaWorkflow.run`'s aggregate result | 20 nodes × 900 KB still exceeds 2 MiB even when every individual node passes. Structural Temporal change; the retry-policy fix makes it fail fast instead of slowly. |

---

## 7. Verified provider surface

Confirmed against live vendor documentation on 2026-07-25/26, for the **v1 provider set only**
(OpenAI, Groq, ElevenLabs, Deepgram, Sarvam). Gemini, Azure, AssemblyAI and Cartesia are out of
scope and deliberately unverified.

The set diverges on every axis, which is what makes it a real test of the abstraction rather than
four variations of one shape:

| | auth header | request transport | response transport |
|---|---|---|---|
| OpenAI TTS | `Authorization: Bearer` | JSON body | **raw audio bytes** |
| OpenAI / Groq STT | `Authorization: Bearer` | multipart | JSON |
| ElevenLabs TTS | **`xi-api-key`** (no scheme) | JSON body + **query params** | **raw audio bytes** |
| Deepgram STT | **`Authorization: Token`** | **raw body bytes** + **query params** | JSON |
| Sarvam TTS | `api-subscription-key` | JSON body | **base64 in a JSON array** |
| Sarvam STT | `api-subscription-key` | multipart | JSON |

**OpenAI** -- TTS `POST /v1/audio/speech`; models `gpt-4o-mini-tts` (default), `tts-1`, `tts-1-hd`;
13 voices, of which `tts-1` / `tts-1-hd` support only 9 (`ballad`, `verse`, `marin`, `cedar` are
excluded); `response_format` in `mp3 opus aac flac wav pcm`; `speed` 0.25-4.0; input <=4096 chars;
`instructions` works only on `gpt-4o-mini-tts`. STT `POST /v1/audio/transcriptions` (multipart), and
**`response_format` is model-gated**: `whisper-1` allows `json text srt verbose_json vtt`, the
`gpt-4o-*-transcribe` models allow only `json text`. Since `timestamp_granularities` requires
`verbose_json`, word timestamps are effectively whisper-1 only, and requesting them elsewhere is a
400 -- so the node downgrades rather than sends.

**Groq** -- STT `POST https://api.groq.com/openai/v1/audio/transcriptions`, OpenAI-compatible;
`whisper-large-v3-turbo` (default) and `whisper-large-v3`. **Turbo omits translation entirely.**
`prompt` is capped at 224 tokens, and Groq bills a **10-second minimum per request** regardless of
clip length.

**ElevenLabs** -- TTS `POST /v1/text-to-speech/{voice_id}`; header `xi-api-key`, bare, no scheme
keyword; body `text`, `model_id` (default `eleven_multilingual_v2`), `language_code`,
`voice_settings`, `seed`, `apply_text_normalization`; **`output_format` is a query parameter**
(default `mp3_44100_128`) and is silently ignored if placed in the body. Voices via
`GET /v2/voices`, cursor-paginated (`has_more` + `next_page_token`; the docs say not to rely on
`total_count`). `speed` has **two conflicting official ranges** -- schema 0.5-2.0, best-practices
prose 0.7-1.2, never reconciled; the schema bound is what the API accepts and is what is enforced.
Whether `voice_settings` are honoured on `eleven_v3` is **undocumented**.

**Deepgram** -- STT `POST /v1/listen`; `Authorization: Token <key>`, **not** Bearer; **all options
are query parameters** (`punctuate`, `diarize`, `smart_format`, `detect_language`, `paragraphs`,
`utterances`, ...), with multi-value options as repeated keys rather than comma lists; audio is the
**raw request body** with an `audio/*` content type, not multipart. Transcript at
`results.channels[N].alternatives[N].transcript`. The documented default model is `base-general`, so
an explicit default is configured instead. **No cloud file-size or duration cap is published** -- the
25 MB figure in circulation belongs to their self-hosted SageMaker docs and is not asserted here.

**Sarvam** -- TTS `POST /text-to-speech` returns **base64 inside JSON**, and `audios` is an **array**
of standalone clips. Explicit nulls are rejected where an absent key is accepted. `bulbul:v3` takes
`temperature` and rejects `pitch` / `loudness` / `enable_preprocessing`; `bulbul:v2` is the mirror
image. STT `POST /speech-to-text` (multipart); timestamps and diarization are **batch-API-only** and
come back null here.

### Corrections to earlier drafts

- An earlier version of this section listed an ElevenLabs STT endpoint and a Deepgram TTS endpoint.
  Both products exist, but neither is in the v1 set and neither was re-verified, so neither is
  implemented.
- The Sarvam node asserted that its synchronous STT endpoint "caps at 30 seconds of audio", and
  billed every transcription at that ceiling. The docs' only 30-second reference is about **response
  latency**, not audio duration. The claim was wrong and the billing with it.
- Sarvam's own docs contradict themselves on `speech_sample_rate` (schema `22050`, prose `24000`).
  The configured default is `24000`, matching the prose and the previous implementation.

---

## 8. The pattern generalised: `nodes/translate/`

Written as "what this should absorb next"; done in the following change, which
retired `nodes/sarvam/` entirely. Recorded here because the differences are the
interesting part — they are what a third application should expect to vary.

**Three registries, not two.** Speech splits by *direction* because a vendor may
do only synthesis or only transcription. Translate splits by *capability*, and
the asymmetry is sharper: DeepL translates and offers neither transliteration
nor language identification, while Sarvam and any LLM do all three. The rule
held: membership is the capability, and a test asserts DeepL is absent from the
other two registries rather than merely undocumented there.

**No media transport at all.** Text results are small, so every constraint in
§2 — the reason `AudioRef` exists, the workspace routes, the containment work —
simply does not apply. The whole `services/media` half of this RFC is speech's
alone.

**A provider that is not an HTTP client.** `_providers/llm.py` prompts a chat
model through `ChatUnifier` and satisfies the same three Protocols as Sarvam's
REST client. Structural typing meant this needed no accommodation. Its billing
is deliberately *not* recorded: that path bills tokens, already costed by the
LLM layer, and recording characters here too would double-count the same call.

**Two more things became shared rather than copied**, both under
`services/plugin/` where the framework owns them:

- `capabilities.CapabilityConfig` — the JSON-backed per-provider/per-model
  resolution ladder. `nodes/speech/_config.py` became a thin instance over it
  and its tests passed untouched.
- `params.coerce_blank_params` — the panel-blank coercion. Written for speech,
  needed verbatim by translate, which is the point at which it stopped being
  plugin code.

That is the same D6 argument applied twice more: the second consumer is when a
mechanism gets extracted, not the first.

### Still vendor-named

`sarvamChatModel`, under `nodes/model/`. That one is correct as-is — chat models
are *selected by name* by users and agents, and the LLM layer already abstracts
them; a "chat model" node with a provider dropdown is what `nodes/model/`
collectively already is.
