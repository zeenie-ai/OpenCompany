"""LLM provider credentials (Wave 11.E.1 — per-domain).

One :class:`ApiKeyCredential` per provider. Used by the chat-model
plugins in this folder (openai, anthropic, gemini, openrouter, groq,
cerebras, deepseek, kimi, mistral, ollama, lmstudio) plus the xAI
credential referenced by agent plugins. At execution time the plugin's
The native SDK client pulls the key directly from
:mod:`services.auth`; this class is the Credentials-modal + discovery
manifest, not the runtime client.

Servers the user runs (Ollama, LM Studio, and named OpenAI-compatible
endpoints) store their Base URL under ``{provider}_proxy`` and need no
key unless the server enforces one; without one, the placeholder the
vendor documents is sent (``auth.placeholder_key`` in llm_defaults.json,
resolved by :func:`services.llm.config.resolve_credential`). All three
save through one path, ``_local_validator.save_llm_server`` (RFC-0003 §6).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from services.plugin.credential import ApiKeyCredential, ProbeResult


class _LLMApiKey(ApiKeyCredential):
    """Shared defaults. Subclasses only set id / display_name / icon.

    The :meth:`_probe` override calls ``ai_service.fetch_models`` —
    every cloud LLM provider in this file inherits it, so adding a new
    OpenAI-compatible provider is purely declarative (id + base_url in
    JSON; no validator code). The local-server credential override
    (:class:`_LocalLLM`) supersedes ``validate`` entirely because its
    side-effect ordering differs (URL stored under ``{id}_proxy``
    before the probe + per-model context registration after).
    """

    category = "AI"
    key_name = "Authorization"
    key_location = "bearer"

    @classmethod
    async def _probe(cls, api_key: str) -> ProbeResult:
        """Default LLM probe: fetch the provider's model list.

        Hits ``GET /v1/models`` (or the provider equivalent) via
        :meth:`AIService.fetch_models`. Returns a populated
        :class:`ProbeResult` on success; raises ``httpx``/``openai``
        exceptions for the base ``Credential.validate`` to classify.
        """
        from services.plugin.deps import get_ai_service

        ai_service = get_ai_service()
        models = await ai_service.fetch_models(cls.id, api_key)
        return ProbeResult(
            valid=True,
            message="API key validated",
            models=models,
        )


# NOTE: ``icon`` is intentionally NOT declared on the LLM credentials
# below. ``cls.icon`` is documentation-only — nothing reads it at
# runtime; the catalogue (``server/config/credential_providers.json``
# ``icon_ref`` field) is the FE-visible source of truth. Each LLM
# provider's icon_ref uses ``lobehub:<brand>`` (the @lobehub/icons
# React component). Per RFC F7 cleanup, the stale ``asset:<key>``
# declarations were dropped — they pointed at frontend SVGs that
# didn't exist for any LLM provider.


class OpenAICredential(_LLMApiKey):
    id = "openai"
    display_name = "OpenAI"
    docs_url = "https://platform.openai.com/api-keys"


class AnthropicCredential(_LLMApiKey):
    id = "anthropic"
    display_name = "Anthropic"
    docs_url = "https://console.anthropic.com/settings/keys"
    # Anthropic uses ``x-api-key`` not Bearer.
    key_name = "x-api-key"
    key_location = "header"


class GeminiCredential(_LLMApiKey):
    id = "gemini"
    display_name = "Google Gemini"
    docs_url = "https://ai.google.dev/gemini-api/docs/api-key"
    key_name = "key"
    key_location = "query"


class OpenRouterCredential(_LLMApiKey):
    id = "openrouter"
    display_name = "OpenRouter"
    docs_url = "https://openrouter.ai/keys"


class GroqCredential(_LLMApiKey):
    id = "groq"
    display_name = "Groq"
    docs_url = "https://console.groq.com/keys"


class CerebrasCredential(_LLMApiKey):
    id = "cerebras"
    display_name = "Cerebras"
    docs_url = "https://cloud.cerebras.ai/"


class DeepSeekCredential(_LLMApiKey):
    id = "deepseek"
    display_name = "DeepSeek"
    docs_url = "https://platform.deepseek.com/api_keys"


class KimiCredential(_LLMApiKey):
    id = "kimi"
    display_name = "Kimi (Moonshot)"
    docs_url = "https://platform.moonshot.cn"


class MistralCredential(_LLMApiKey):
    id = "mistral"
    display_name = "Mistral AI"
    docs_url = "https://console.mistral.ai/api-keys/"


class XaiCredential(_LLMApiKey):
    id = "xai"
    display_name = "xAI (Grok)"
    docs_url = "https://console.x.ai"


class SarvamCredential(_LLMApiKey):
    """One subscription key for every Sarvam API.

    Sarvam's chat endpoint is OpenAI-compatible and accepts the key as a
    Bearer token, which is what the openai SDK sends — so the chat path
    never reads ``key_name``. Its other APIs (translate / transliterate /
    text-lid / speech-to-text / text-to-speech, all under
    ``server/nodes/sarvam/``) accept ONLY ``api-subscription-key``, and
    those nodes authenticate through ``ctx.connection("sarvam")`` ->
    :meth:`ApiKeyCredential.inject`. Declaring the native header here
    means a single stored key serves both surfaces.

    Same override shape as :class:`AnthropicCredential`'s ``x-api-key``.
    """

    id = "sarvam"
    display_name = "Sarvam AI"
    docs_url = "https://dashboard.sarvam.ai"
    key_name = "api-subscription-key"
    key_location = "header"


class _LocalLLM(_LLMApiKey):
    """Base for local-server credentials (Ollama, LM Studio).

    Same shape as :class:`_LLMApiKey`, but ``resolve()`` returns the
    vendor's documented placeholder when no key is stored instead of
    raising. The server address rides on the ``{id}_proxy`` row, which
    the unifier reads before building the client.
    """

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        from services.llm.config import resolve_credential
        from services.plugin.deps import get_auth_service

        api_key = await get_auth_service().resolve_api_key(cls.id, principal=user_id)
        return {"api_key": resolve_credential(cls.id, api_key)}

    @classmethod
    async def validate(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Root the URL, list the loaded models, and save both rows.

        Overrides the base ``Credential.validate`` because the value is a
        Base URL, not a key, and two rows are written (``{id}_proxy`` and
        ``{id}``). See ``_local_validator.save_llm_server``.
        """
        from ._local_validator import validate_local_llm

        return await validate_local_llm(dict(data, provider=cls.id))


class OllamaCredential(_LocalLLM):
    id = "ollama"
    display_name = "Ollama"
    icon = "lobehub:ollama"
    docs_url = "https://ollama.com/download"


class LMStudioCredential(_LocalLLM):
    id = "lmstudio"
    display_name = "LM Studio"
    icon = "lobehub:lmstudio"
    docs_url = "https://lmstudio.ai/docs/local-server"


# Mirrors the provider column limit: "openai_compatible:" + slug + "_proxy"
# must fit in EncryptedAPIKey.provider (max 50 characters).
_ENDPOINT_SLUG_MAX = 24
_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")


def _endpoint_slug(label: str, base_url: str) -> str:
    """Slug for a new endpoint: from its label, else from the URL's host and port.

    Never from the whole network location, which can carry a username and
    password: the slug is the endpoint's name, stored and shown in plain
    text. Empty when there is neither a label nor a host to name it by.
    """
    from slugify import slugify

    source = label
    if not source:
        parts = urlsplit(base_url)
        try:
            port = parts.port
        except ValueError:  # malformed port: name it by the host alone
            port = None
        source = " ".join(str(part) for part in (parts.hostname, port) if part)
    return slugify(source, max_length=_ENDPOINT_SLUG_MAX, separator="-")


def _rejected(message: str) -> Dict[str, Any]:
    return {"provider": OpenAICompatibleCredential.id, "success": True, "valid": False, "message": message, "models": []}


class OpenAICompatibleCredential(_LLMApiKey):
    """Named OpenAI-compatible endpoints (RFC-0003 D13).

    Any number of servers, each saved under its own provider reference
    ``openai_compatible:<slug>`` with the same two rows Ollama uses
    (``{ref}`` for the key, ``{ref}_proxy`` for the resolved URL). The
    LiteLLM ``model_list`` shape: a label, a base URL, an optional key.

    Saved and refreshed through the standard ``validate_api_key`` message:
    ``api_key`` carries the Base URL, the other catalogue fields ride
    alongside under their own keys, and ``ref`` marks a refresh of an
    existing endpoint. Removed through ``delete_api_key`` with the
    endpoint's reference, which also clears its URL row.
    """

    id = "openai_compatible"
    display_name = "OpenAI-compatible"
    docs_url = "https://docs.litellm.ai/docs/providers/openai_compatible"

    # Field keys of this provider's entry in config/credential_providers.json.
    label_field = "openai_compatible_label"
    key_field = "openai_compatible_api_key"

    @classmethod
    async def validate(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        from services.llm.config import endpoint_ref, split_provider_ref
        from services.llm.endpoints import FULL_URL_REQUIRED, SERVER_META_KEY, base_url_key
        from services.plugin.deps import get_auth_service

        from ._local_validator import save_llm_server

        auth = get_auth_service()
        label = (data.get(cls.label_field) or "").strip()
        user_key: Optional[str] = (data.get(cls.key_field) or "").strip() or None
        ref = (data.get("ref") or "").strip()

        if ref:
            # Refresh. The stored URL is authoritative: the panel only ever
            # sees the redacted form, which may have lost userinfo.
            name, slug = split_provider_ref(ref)
            if name != cls.id or not _SLUG_PATTERN.match(slug):
                return _rejected(f"Unknown endpoint {ref!r}.")
            candidate = await auth.get_api_key(base_url_key(ref))
            if not candidate:
                return _rejected(f"The endpoint '{slug}' no longer exists.")
            user_key = user_key or await auth.get_api_key(ref)
            if not label:
                meta = (await auth.get_model_params(ref)).get(SERVER_META_KEY) or {}
                label = meta.get("label") or slug
        else:
            candidate = (data.get("api_key") or "").strip()
            slug = _endpoint_slug(label, candidate)
            if not slug:
                return _rejected(
                    "Give the endpoint a label with at least one letter or digit." if label else FULL_URL_REQUIRED
                )
            ref = endpoint_ref(slug)
            if await auth.get_api_key(ref):
                return _rejected(
                    f"An endpoint named '{slug}' already exists. Choose another label, "
                    "or refresh the existing endpoint."
                )
            label = label or slug

        return await save_llm_server(ref, candidate, user_key, display=label, label=label)

    @classmethod
    async def catalogue_extras(cls, *, principal: Optional[str] = None) -> Dict[str, Any]:
        """``stored`` (any endpoint saved) plus the endpoint list for the panel."""
        from services.llm.endpoints import list_endpoints
        from services.plugin.deps import get_auth_service

        endpoints = [
            {"ref": e.ref, "label": e.label, "base_url": e.base_url, "kind": e.kind, "model_count": len(e.models)}
            for e in await list_endpoints(get_auth_service(), **({"principal": principal} if principal is not None else {}))
        ]
        return {"stored": bool(endpoints), "endpoints": endpoints}

    @classmethod
    async def is_configured(cls, auth_service: Any, parameters: Dict[str, Any]) -> bool:
        """The endpoint the node names is saved. Nothing is stored under the bare id."""
        ref = str(parameters.get("endpoint") or "")
        return bool(ref) and bool(await auth_service.has_valid_key(ref))
