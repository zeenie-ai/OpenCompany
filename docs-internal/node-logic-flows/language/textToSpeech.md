# Text to Speech (`textToSpeech`)

| Field | Value |
|------|-------|
| **Category** | language |
| **Backend handler** | [`server/nodes/speech/text_to_speech.py`](../../../server/nodes/speech/text_to_speech.py) |
| **Shared helpers** | [`server/nodes/speech/_base.py`](../../../server/nodes/speech/_base.py), [`_unifier.py`](../../../server/nodes/speech/_unifier.py), [`_providers/`](../../../server/nodes/speech/_providers/) |
| **Capabilities config** | [`server/config/speech_defaults.json`](../../../server/config/speech_defaults.json) |
| **Tests** | [`server/tests/nodes/test_speech.py`](../../../server/tests/nodes/test_speech.py) |
| **Skill (if any)** | [`server/skills/language_agent/speech-skill/SKILL.md`](../../../server/skills/language_agent/speech-skill/SKILL.md) |
| **Dual-purpose tool** | yes — tool name `text_to_speech` |

## Purpose

Synthesize speech through any configured provider. Replaces the vendor-locked
`sarvamTextToSpeech`: the provider is a parameter, so switching vendors never
means swapping the node out of a workflow.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data injected into params |

Handles are declared visible explicitly (`hide_input_handle = False` /
`hide_output_handle = False`) because `usable_as_tool = True` otherwise
auto-hides both, which would break chaining into `speechToText`.

## Parameters

| Name | Type | Default | Required | Description |
|------|------|---------|----------|-------------|
| `tool_name` | string | `text_to_speech` | no | Override name shown to the LLM when used as a tool |
| `tool_description` | string | `"Convert text into spoken audio. Returns a reference to an audio file saved in the workspace, not the audio itself. Use when the user asks for speech, narration, a voiceover or an audio version of some text."` | no | Override description shown to the LLM when used as a tool (`rows: 3`) |
| `provider` | enum | `elevenlabs` | no | Registry-driven: the enum is `tts_providers()` |
| `text` | string | - | yes | Text to speak; capped per provider/model |
| `tts_model` | string | `""` | no | Blank uses the provider default. **Not** named `model` — see below |
| `voice` | string | `""` | no | Loader `speechVoices`; live catalogue on ElevenLabs, config elsewhere |
| `language` | string | `""` | no | Required by Sarvam (e.g. `hi-IN`), auto-detected elsewhere |
| `speed` | number | `null` | no | Clamped to the provider's documented range |
| `output_format` | string | `""` | no | Blank uses the provider default |
| `sample_rate` | number | `null` | no | Where the provider supports it |
| `provider_options` | object | `{}` | no | Vendor-specific keys, passed through untouched |

**`tts_model`, not `model`.** [`ParameterRenderer.tsx:898`](../../../client/src/components/ParameterRenderer.tsx#L898)
overwrites any field literally named `model` or `api_key` with chat-model data
whenever a sibling `provider` field exists — and it never checks that the
provider is an LLM provider. A field named `model` here would be cleared the
moment a user picked ElevenLabs.

## Outputs

| Field | Type | Description |
|-------|------|-------------|
| `audio` | AudioRef | The generated clip; the first when several |
| `files` | AudioRef[] | Every clip |
| `chunk_count` | int | >1 when the provider split long input |
| `provider` / `tts_model` / `voice` | string | What actually ran |
| `request_id` | string | Provider request id, where returned |
| `note` | string | Set when several clips came back |

`AudioRef` carries a path and metadata, never bytes — see
[`services/media/limits.py`](../../../server/services/media/limits.py) for the
measured reason.

## Side effects

- Writes one file per clip into `<workspace>/audio/` via `write_audio`
  (atomic, random-suffixed filename, no collisions across runs)
- Records an `APIUsageMetric` keyed on the provider id, in that provider's
  own billing unit (characters for all three current TTS providers)

## Edge cases

- **Several clips**: Sarvam splits long input into standalone files. Each has
  its own container header, so byte-concatenation produces audio that plays
  only the first chunk. `note` says so explicitly.
- **ElevenLabs without a voice**: refused up front — there is no
  account-wide default voice.
- **Over-cap text**: refused before the paid call, naming the cap.
- **Unsupported provider**: `NodeUserError` listing the registered ones.

## Related

- **Peer nodes**: [`speechToText`](./speechToText.md) — the other half of a voice loop.
- **RFC**: [`speech_provider_rfc.md`](../../speech_provider_rfc.md)
