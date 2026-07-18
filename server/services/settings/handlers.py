"""Settings domain WebSocket handlers.

Extracted from ``routers/websocket.py`` (Wave 13.3). 8 handlers covering:

  - User settings I/O (``get_user_settings`` / ``save_user_settings``).
  - Provider defaults (per-LLM-provider tuning) (``get_provider_defaults``
    / ``save_provider_defaults``).
  - Validated AI providers + global default model
    (``get_validated_ai_providers`` / ``save_global_model``).
  - Memory compaction settings (``get_compaction_stats`` /
    ``configure_compaction``).

Wire shape preserved across the move; frontend handlers continue
working unchanged.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import WebSocket

from core.logging import get_logger
from services.ws_handler_registry import ws_handler

logger = get_logger(__name__)


@ws_handler()
async def handle_get_user_settings(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get user settings from database."""
    from core.container import container

    database = container.database()
    user_id = data.get("user_id", "default")
    settings = await database.get_user_settings(user_id)

    if settings is None:
        settings = {
            "user_id": user_id,
            "auto_save": True,
            "auto_save_interval": 30,
            "sidebar_default_open": True,
            "component_palette_default_open": True,
        }

    return {"settings": settings}


@ws_handler()
async def handle_save_user_settings(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save user settings to database."""
    from core.container import container

    database = container.database()
    user_id = data.get("user_id", "default")
    settings_data = data.get("settings", {})

    success = await database.save_user_settings(settings_data, user_id)

    if success:
        if "max_processes" in settings_data:
            from services.process_service import get_process_service

            get_process_service().max_processes = int(settings_data["max_processes"])

        settings = await database.get_user_settings(user_id)
        return {"settings": settings}
    return {"success": False, "error": "Failed to save settings"}


@ws_handler()
async def handle_get_provider_defaults(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get default parameters for a provider."""
    from core.container import container
    from services.ai import get_default_model
    from services.model_registry import get_model_registry

    database = container.database()
    provider = data.get("provider", "").lower()
    defaults = await database.get_provider_defaults(provider)

    config_default_model = get_default_model(provider)

    if defaults:
        if not defaults.get("default_model"):
            defaults["default_model"] = config_default_model
        return {"provider": provider, "defaults": defaults}

    registry = get_model_registry()
    model_max_tokens = registry.get_max_output_tokens(config_default_model, provider)

    return {
        "provider": provider,
        "defaults": {
            "default_model": config_default_model,
            "temperature": 0.7,
            "max_tokens": model_max_tokens,
            "thinking_enabled": False,
            "thinking_budget": 2048,
            "reasoning_effort": "medium",
            "reasoning_format": "parsed",
        },
    }


@ws_handler()
async def handle_save_provider_defaults(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save default parameters for a provider."""
    from core.container import container

    database = container.database()
    provider = data.get("provider", "").lower()
    defaults = data.get("defaults", {})
    success = await database.save_provider_defaults(provider, defaults)
    return {"success": success, "provider": provider}


@ws_handler()
async def handle_get_validated_ai_providers(
    data: Dict[str, Any],
    websocket: WebSocket,
) -> Dict[str, Any]:
    """Get all AI providers with stored API keys and their popular models.

    Returns providers that have validated keys, their stored models,
    and the current global default provider/model from UserSettings.
    """
    import json
    from pathlib import Path

    from core.container import container

    auth_service = container.auth_service()
    database = container.database()

    from services.ai import PROVIDER_CONFIGS

    AI_PROVIDERS = list(PROVIDER_CONFIGS.keys())

    defaults_path = Path(__file__).parent.parent.parent / "config" / "llm_defaults.json"
    try:
        with open(defaults_path) as f:
            llm_defaults = json.load(f)
    except Exception:
        llm_defaults = {"providers": {}}

    providers = []
    for provider in AI_PROVIDERS:
        api_key = await auth_service.get_api_key(provider, data.get("session_id", "default"))
        if not api_key:
            continue

        stored_models = await auth_service.get_stored_models(provider, data.get("session_id", "default"))

        provider_config = llm_defaults.get("providers", {}).get(provider, {})
        default_model = provider_config.get("default_model", "")
        popular_models = provider_config.get("popular_models") or [
            m for m in provider_config.get("max_output_tokens", {}).keys() if m != "_default"
        ]

        provider_defaults = await database.get_provider_defaults(provider)
        if provider_defaults and provider_defaults.get("default_model"):
            default_model = provider_defaults["default_model"]

        providers.append(
            {
                "provider": provider,
                "models": stored_models or [],
                "popular_models": popular_models,
                "default_model": default_model,
            }
        )

    user_id = data.get("user_id", "default")
    settings = await database.get_user_settings(user_id)
    global_provider = settings.get("default_llm_provider") if settings else None
    global_model = settings.get("default_llm_model") if settings else None

    return {
        "providers": providers,
        "global_provider": global_provider,
        "global_model": global_model,
    }


@ws_handler()
async def handle_save_global_model(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save the global default provider + model to UserSettings."""
    from core.container import container

    database = container.database()
    provider = data.get("provider", "")
    model = data.get("model", "")
    user_id = data.get("user_id", "default")

    success = await database.save_user_settings(
        {
            "default_llm_provider": provider,
            "default_llm_model": model,
        },
        user_id,
    )

    return {"success": success, "provider": provider, "model": model}


@ws_handler("session_id")
async def handle_get_compaction_stats(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get compaction statistics for a session.

    Optional model/provider params enable model-aware threshold (50% of context window).
    """
    from services.compaction import get_compaction_service

    svc = get_compaction_service()
    if not svc:
        return {"success": False, "error": "Compaction service not initialized"}
    return await svc.stats(
        data["session_id"],
        model=data.get("model", ""),
        provider=data.get("provider", ""),
    )


@ws_handler("session_id")
async def handle_configure_compaction(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Configure compaction settings for a session."""
    from services.compaction import get_compaction_service

    svc = get_compaction_service()
    if not svc:
        return {"success": False, "error": "Compaction service not initialized"}
    success = await svc.configure(data["session_id"], data.get("threshold"), data.get("enabled"))
    return {"success": success}


@ws_handler()
async def handle_get_provider_usage_summary(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get aggregated token usage and cost by provider for Credentials Modal.

    Lives in settings/ for now (Wave 13.3). Wave 13.8 may move it to a
    dedicated ``services/pricing/handlers.py`` module alongside
    ``get_pricing_config`` / ``save_pricing_config`` / ``get_api_usage_summary``.
    """
    from core.container import container

    database = container.database()
    providers = await database.get_provider_usage_summary()
    return {"success": True, "providers": providers}


WS_HANDLERS: Dict[str, Any] = {
    "get_user_settings": handle_get_user_settings,
    "save_user_settings": handle_save_user_settings,
    "get_provider_defaults": handle_get_provider_defaults,
    "save_provider_defaults": handle_save_provider_defaults,
    "get_validated_ai_providers": handle_get_validated_ai_providers,
    "save_global_model": handle_save_global_model,
    "get_compaction_stats": handle_get_compaction_stats,
    "configure_compaction": handle_configure_compaction,
    "get_provider_usage_summary": handle_get_provider_usage_summary,
}


__all__ = [
    "WS_HANDLERS",
    "handle_configure_compaction",
    "handle_get_compaction_stats",
    "handle_get_provider_defaults",
    "handle_get_provider_usage_summary",
    "handle_get_user_settings",
    "handle_get_validated_ai_providers",
    "handle_save_global_model",
    "handle_save_provider_defaults",
    "handle_save_user_settings",
]
