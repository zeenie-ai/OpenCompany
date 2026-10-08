"""Dynamic dropdown loaders for the speech nodes.

Both loaders read ``provider`` out of the parameter dict they are handed
rather than declaring ``loadOptionsDependsOn: ["provider"]``. That attribute
is lifted by the client adapter but has no usages anywhere in the codebase,
so it is untested machinery; the parameter dict, by contrast, is how every
existing loader in the repo works and it re-fires when parameters change.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.logging import get_logger

from . import _config as speech_config
from ._registry import stt_providers, tts_providers

logger = get_logger(__name__)


def _direction_for(params: Dict[str, Any]) -> str:
    """Infer direction from which node asked.

    The two nodes have distinct model field names, which doubles as the
    signal for which direction's catalogue to serve.
    """
    if "stt_model" in params or params.get("node_type") == "speechToText":
        return speech_config.STT
    return speech_config.TTS


def _selected_provider(params: Dict[str, Any], direction: str) -> str:
    provider = str(params.get("provider") or "")
    available = tts_providers() if direction == speech_config.TTS else stt_providers()
    if provider in available:
        return provider
    return available[0] if available else ""


async def load_speech_models(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Model ids for the selected provider and direction."""
    direction = _direction_for(params)
    provider = _selected_provider(params, direction)
    if not provider:
        return []

    default = speech_config.default_model(provider, direction)
    options: List[Dict[str, Any]] = []
    for model in speech_config.models(provider, direction):
        option: Dict[str, Any] = {"value": model, "label": model}
        if model == default:
            option["description"] = "Provider default"
        options.append(option)
    return options


async def load_speech_languages(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Language codes for the selected provider and direction.

    A provider with a closed set (Sarvam accepts exactly 11 locales, and
    rejects anything else) serves its own list. Everything else falls back to
    the shared shortlist in ``speech_defaults.json`` — Whisper and Deepgram
    accept far more codes than that, so the list is a convenience rather than
    a constraint, and "Auto-detect" leads so the common case is one click.
    """
    direction = _direction_for(params)
    provider = _selected_provider(params, direction)
    if not provider:
        return []

    codes = speech_config.capability(provider, direction, "languages", default=None)
    if not codes:
        codes = speech_config.SPEECH_DEFAULTS.get("common_languages") or []

    options: List[Dict[str, Any]] = [
        {"value": "", "label": "Auto-detect", "description": "Let the provider decide"}
    ]
    options.extend({"value": code, "label": code} for code in codes)
    return options


async def load_speech_formats(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Output containers the selected synthesis provider accepts."""
    provider = _selected_provider(params, speech_config.TTS)
    if not provider:
        return []

    default = speech_config.capability(
        provider, speech_config.TTS, "default_output_format", default=""
    )
    options: List[Dict[str, Any]] = []
    for fmt in speech_config.output_formats(provider):
        option: Dict[str, Any] = {"value": fmt, "label": fmt}
        if fmt == default:
            option["description"] = "Provider default"
        options.append(option)
    return options


async def load_speech_sample_rates(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Sample rates, for the providers that expose the choice at all.

    Empty for providers that do not — the field is gated by
    ``displayOptions`` so it should not be visible in that case anyway, but
    an empty list is the honest answer rather than a fabricated one.
    """
    provider = _selected_provider(params, speech_config.TTS)
    if not provider:
        return []

    rates = speech_config.capability(
        provider, speech_config.TTS, "sample_rates", default=[]
    )
    default = speech_config.capability(
        provider, speech_config.TTS, "default_sample_rate", default=None
    )
    options: List[Dict[str, Any]] = []
    for rate in rates or []:
        option: Dict[str, Any] = {"value": rate, "label": f"{rate} Hz"}
        if rate == default:
            option["description"] = "Provider default"
        options.append(option)
    return options


async def load_speech_voices(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Voices for the selected synthesis provider.

    Providers with a live catalogue (ElevenLabs) are queried, because their
    voice list is per-account and changes without a release. Everyone else
    answers from config. A live lookup that fails degrades to the configured
    list rather than leaving the user with an empty dropdown and no way to
    proceed.
    """
    provider = _selected_provider(params, speech_config.TTS)
    if not provider:
        return []

    model = str(params.get("tts_model") or "")
    static = [
        {"value": voice, "label": voice.replace("_", " ").title()}
        for voice in speech_config.voices(provider, model=model or None)
    ]

    if not speech_config.capability(provider, speech_config.TTS, "voices_endpoint"):
        return static

    try:
        from services.plugin.deps import get_auth_service
        from services.ws_handler_registry import current_load_options_principal

        from . import _unifier

        api_key = await get_auth_service().resolve_api_key(
            speech_config.credential_id(provider), principal=current_load_options_principal()
        )
        if not api_key:
            return static
        voices = await _unifier.list_voices(provider=provider, api_key=api_key)
        return [voice.as_option() for voice in voices] or static
    except Exception as exc:
        from services.credentials.onepassword import CredentialSourceError
        if isinstance(exc, CredentialSourceError):
            raise
        logger.warning(
            "live voice lookup failed; falling back to the configured list",
            provider=provider,
            error=type(exc).__name__,
        )
        return static


__all__ = ["load_speech_models", "load_speech_voices"]
