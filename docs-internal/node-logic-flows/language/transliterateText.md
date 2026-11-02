# Transliterate (`transliterateText`)

| Field | Value |
|------|-------|
| **Category** | language |
| **Backend handler** | [`server/nodes/translate/transliterate_text.py`](../../../server/nodes/translate/transliterate_text.py) |
| **Shared helpers** | [`_base.py`](../../../server/nodes/translate/_base.py), [`_unifier.py`](../../../server/nodes/translate/_unifier.py), [`_providers/`](../../../server/nodes/translate/_providers/) |
| **Capabilities config** | [`server/config/translate_defaults.json`](../../../server/config/translate_defaults.json) |
| **Tests** | [`server/tests/nodes/test_translate.py`](../../../server/tests/nodes/test_translate.py) |
| **Skill (if any)** | [`server/skills/language_agent/translation-skill/SKILL.md`](../../../server/skills/language_agent/translation-skill/SKILL.md) |
| **Dual-purpose tool** | yes |

## Purpose

Convert text between writing systems **without translating it**. Replaces
`sarvamTransliterate`.

The distinction the node exists to preserve: transliteration changes the
script while the words stay the same (`namaste` → `नमस्ते`), where translation
changes the words (`hello` → `नमस्ते`). Both produce Devanagari; only one
changed the meaning. The tool description states this explicitly because
confusing the two is the most common misuse.

## Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `tool_name` | string | `transliterate_text` | Saved callable-name override; check the actual tool catalogue |
| `tool_description` | string | plugin default | Description supplied to the model |
| `provider` | enum | registry-driven | `transliterate_providers()` — `sarvam` / `openai`. **DeepL is absent and unselectable.** |
| `text` | string | - | Text to convert (required) |
| `target_language` | string | provider default | Loader `transliterateLanguages` |
| `source_language` | string | auto-detect | Loader `transliterateSourceLanguages` |
| `target_script` | string | - | Loader `transliterateScripts` |
| `transliterate_model` | string | provider default | |
| `provider_options` | object | `{}` | Vendor-specific |

## Outputs

`transliterated_text`, `provider`, `transliterate_model`, `request_id`.

## Edge cases

- **`spoken-form-in-native`** renders numbers and abbreviations the way they
  would be *spoken* — the right choice when the output feeds `textToSpeech`.
- **DeepL cannot be selected here.** Not merely undocumented: it registers
  only into the translate registry, so an attempt is refused up front with a
  message listing the providers that can.

## Related

- **Peer nodes**: the other two nodes in [`nodes/translate/`](../../../server/nodes/translate/).
- **Pattern**: [Speech Provider RFC §8](../../speech_provider_rfc.md#8-the-pattern-generalised-nodestranslate)
