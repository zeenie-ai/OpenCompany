"""LLM-backed translation, transliteration and language identification.

The provider that proves the Protocol does not care what a provider *does*
internally: there is no translation API behind this one, just a chat model
prompted through ``ChatUnifier``. It satisfies the same three Protocols as
Sarvam's REST client and is selected the same way.

Why it earns its place rather than being a curiosity: it covers every language
pair the dedicated vendors do not, needs no new credential (it reuses the chat
key the user already configured), and is the only option for a language DeepL
and Sarvam both lack.

**Billing is deliberately not recorded here.** This path bills tokens, not
characters, and token cost is already the LLM layer's concern. Reporting a
character count against ``api_pricing`` as well would double-count. The config
records the unit as ``tokens`` and the node skips attribution.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core.logging import get_logger

from .. import _config as translate_config
from .._protocol import (
    DetectRequest,
    DetectResult,
    DetectedLanguage,
    LanguageOption,
    TranslateError,
    TranslateErrorCategory,
    TranslateRequest,
    TranslateResult,
    TranslatedText,
    TransliterateRequest,
    TransliterateResult,
)
from .._registry import (
    DetectProviderSpec,
    TranslateProviderSpec,
    TransliterateProviderSpec,
    register_detect_provider,
    register_translate_provider,
    register_transliterate_provider,
)

logger = get_logger(__name__)

# Asking for JSON rather than bare text is what makes the output parseable
# without heuristics — a model told to "just translate" will sometimes add
# "Here is the translation:".
_JSON_RULE = (
    "Respond with a single JSON object and nothing else. No prose, no code "
    "fence."
)


class LlmTextProvider:
    """Chat-model-backed text-language provider."""

    provider_name = "openai"

    def __init__(self, api_key: str, *, provider_name: Optional[str] = None):
        self.provider_name = provider_name or type(self).provider_name
        self.api_key = api_key

    # ------------------------------------------------------------------
    # capabilities
    # ------------------------------------------------------------------

    async def translate(self, req: TranslateRequest) -> TranslateResult:
        model = req.model or translate_config.default_model(self.provider_name, "translate")
        instructions = [
            f"Translate each input string into {req.target_language or 'English'}.",
            "Preserve meaning, tone and any markup or placeholders exactly.",
        ]
        if req.source_language:
            instructions.append(f"The source language is {req.source_language}.")
        if req.formality:
            instructions.append(f"Use a {req.formality} register.")
        if req.context:
            instructions.append(f"Context for disambiguation: {req.context}")
        instructions.append(
            'Return {"translations": ["..."], "detected_source_language": "<code>"} '
            "with one entry per input, in order."
        )

        payload = await self._ask(model, instructions, req.text)
        texts = payload.get("translations") or []
        detected = payload.get("detected_source_language")
        return TranslateResult(
            translations=[
                TranslatedText(text=str(t), detected_source_language=detected)
                for t in texts
            ]
            or [TranslatedText(text="", detected_source_language=detected)],
            model=model,
            billed_unit="tokens",
        )

    async def transliterate(self, req: TransliterateRequest) -> TransliterateResult:
        model = req.model or translate_config.default_model(
            self.provider_name, "transliterate"
        )
        target = req.target_script or "the Latin alphabet"
        instructions = [
            f"Transliterate each input string into {target}.",
            "This is SCRIPT CONVERSION, not translation — the words and "
            "language must not change, only the writing system.",
            'Return {"results": ["..."]} with one entry per input, in order.',
        ]
        payload = await self._ask(model, instructions, req.text)
        return TransliterateResult(
            results=[str(r) for r in payload.get("results") or []],
            model=model,
            billed_unit="tokens",
        )

    async def detect(self, req: DetectRequest) -> DetectResult:
        model = req.model or translate_config.default_model(self.provider_name, "detect")
        instructions = [
            "Identify the language of each input string.",
            'Return {"detections": [{"language": "<BCP-47 code>", '
            '"script": "<ISO 15924 code>", "confidence": 0.0-1.0}]} with one '
            "entry per input, in order.",
        ]
        payload = await self._ask(model, instructions, req.text)
        detections = []
        for entry in payload.get("detections") or []:
            if not isinstance(entry, dict):
                continue
            detections.append(
                DetectedLanguage(
                    language=str(entry.get("language") or ""),
                    script=entry.get("script"),
                    confidence=_as_float(entry.get("confidence")),
                )
            )
        return DetectResult(detections=detections, model=model, billed_unit="tokens")

    async def languages(self, *, target: bool = True) -> List[LanguageOption]:
        """No live catalogue — an LLM accepts essentially anything.

        The configured shortlist is a dropdown convenience; a user may type
        any code the model understands.
        """
        return [
            LanguageOption(code=code)
            for code in translate_config.languages(self.provider_name, "translate")
        ]

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _ask(
        self, model: str, instructions: List[str], inputs: List[str]
    ) -> Dict[str, Any]:
        """One chat round-trip returning a parsed JSON object."""
        from core.container import container
        from services.llm.protocol import Message

        system = " ".join(instructions + [_JSON_RULE])
        user = json.dumps({"inputs": inputs}, ensure_ascii=False)

        response = await container.chat_unifier().chat(
            provider=self.provider_name,
            api_key=self.api_key,
            messages=[
                Message(role="system", content=system),
                Message(role="user", content=user),
            ],
            model=model,
            # Deterministic: this is a transformation, not a generation. A
            # creative temperature here produces different output for the
            # same input, which is wrong for translation.
            temperature=0.0,
        )
        return _parse_json_object(response.content, self.provider_name)


def _parse_json_object(content: str, provider: str) -> Dict[str, Any]:
    """The model's reply as a JSON object, tolerating a code fence
    (``services.llm.json_reply``); a reply that is not one is a TranslateError."""
    from services.llm.json_reply import parse_json_object

    try:
        return parse_json_object(content)
    except ValueError as exc:
        raise TranslateError(
            message=str(exc),
            provider=provider,
            category=TranslateErrorCategory.SERVER,
        ) from exc


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ``openai:OpenAIError`` is declared because the registry requires a typed
# ref, but it is rarely the path taken: ChatUnifier already translates SDK
# failures into NodeUserError, which propagates through the dispatcher
# untouched and keeps its better message.
_KW = {"provider_name": "openai"}
_REFS = ("openai:OpenAIError",)

register_translate_provider(
    TranslateProviderSpec(
        name="openai", factory=LlmTextProvider, sdk_exception_refs=_REFS, client_kwargs=_KW
    )
)
register_transliterate_provider(
    TransliterateProviderSpec(
        name="openai", factory=LlmTextProvider, sdk_exception_refs=_REFS, client_kwargs=_KW
    )
)
register_detect_provider(
    DetectProviderSpec(
        name="openai", factory=LlmTextProvider, sdk_exception_refs=_REFS, client_kwargs=_KW
    )
)
