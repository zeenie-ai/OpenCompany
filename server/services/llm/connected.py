"""The AI providers the owner can run on now, with their models.

One list for every model picker: the editor's Set Global Model and the
provider defaults (``get_validated_ai_providers``), and the model picker in
Home's employee chat (services/chat/choice.py). Both pickers show the same
menu (client ``components/ui/ModelMenu.tsx``) and offer the same models, a
provider's ``offered`` list. A provider is on it when a key is stored for it
(a saved local server counts), when the OpenCompany Gateway serves it while
connected, or, for a saved OpenAI-compatible endpoint, by its reference
(``openai_compatible:<slug>``). The bare ``openai_compatible`` id holds no
key, so it is never on it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from core.logging import get_logger

logger = get_logger(__name__)


def _offered(popular: Sequence[str], stored: Sequence[str]) -> List[str]:
    """What a model picker offers for a provider: its curated models, else
    the ones stored with its key (a local server's, an endpoint's)."""
    return list(dict.fromkeys(popular or stored))


async def connected_providers(
    auth: Any,
    database: Any,
    *,
    principal: Optional[str],
    session_id: str = "default",
) -> List[Dict[str, Any]]:
    """Each provider the owner can run on now, in ``llm_defaults.json`` order
    and then the named endpoints: ``{provider, display_name, models,
    popular_models, offered, default_model}``. ``models`` are the ones stored
    with its key (its curated list while the gateway serves it),
    ``popular_models`` its curated list in ``llm_defaults.json``, ``offered``
    what a model picker lists for it, and ``default_model`` the owner's
    provider default, else the configured one."""
    from services.gateway.routing import gateway_covers
    from services.llm.config import PROVIDER_CONFIGS, curated_models, get_default_model, provider_display_name
    from services.llm.endpoints import list_endpoints

    providers: List[Dict[str, Any]] = []
    for provider in PROVIDER_CONFIGS:
        if await auth.has_valid_key(provider, session_id, principal=principal):
            models = await auth.get_stored_models(provider, session_id, principal=principal)
        elif await gateway_covers(provider):
            # No key of the install's own; the OpenCompany Gateway serves it.
            models = curated_models(provider)
        else:
            continue
        saved = await database.get_provider_defaults(provider) or {}
        popular = curated_models(provider)
        stored = list(models or [])
        providers.append(
            {
                "provider": provider,
                # Sent so no client needs its own id -> name table: the list
                # can hold a provider with no credential-catalogue entry (xai).
                "display_name": provider_display_name(provider),
                "models": stored,
                "popular_models": popular,
                "offered": _offered(popular, stored),
                "default_model": saved.get("default_model") or get_default_model(provider),
            }
        )

    # Each named endpoint is its own provider (RFC-0003 D13). One that can't
    # be listed leaves the rest of the list standing.
    try:
        endpoints = await list_endpoints(auth, principal=principal)
    except Exception:
        logger.warning("Could not list OpenAI-compatible endpoints", exc_info=True)
        endpoints = []
    for endpoint in endpoints:
        saved = await database.get_provider_defaults(endpoint.ref) or {}
        providers.append(
            {
                "provider": endpoint.ref,
                "display_name": f"{endpoint.label} (OpenAI-compatible)",
                "models": list(endpoint.models),
                "popular_models": [],
                "offered": _offered([], endpoint.models),
                "default_model": saved.get("default_model", ""),
            }
        )
    return providers


__all__ = ["connected_providers"]
