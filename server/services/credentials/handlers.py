"""Credential CRUD WS handlers extracted from ``routers/websocket.py`` (Wave 13.5).

4 handlers:
  - ``validate_api_key`` — dispatch to plugin's ``Credential.validate``.
  - ``get_stored_api_key`` — return stored key + catalogue default fallback.
  - ``save_api_key`` — idempotent store + symmetric broadcast.
  - ``delete_api_key`` — idempotent delete + symmetric broadcast.

Per-provider validation logic lives in ``services/plugin/credential.py``'s
``CREDENTIAL_REGISTRY``. Adding a new provider with a special validator
is a single ``_probe`` override on the plugin's ``Credential`` subclass —
this module stays untouched.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import WebSocket

from core.container import container
from core.logging import get_logger
from services.status_broadcaster import get_status_broadcaster
from services.ws_handler_registry import ws_handler

# ``container`` + ``get_status_broadcaster`` re-imported at module scope
# so existing tests that patch ``services.credentials.handlers.container``
# / ``...get_status_broadcaster`` (formerly patched on ``routers.websocket``)
# keep working without rewriting fixtures. ``services.credentials`` is
# imported lazily from ``main.py`` at lifespan startup, AFTER the
# container is fully initialised — no circular-import risk like Wave
# 13.2's deployment handlers.

logger = get_logger(__name__)


@ws_handler("provider", "api_key")
async def handle_validate_api_key(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Validate and store an API key.

    Pure dispatch — looks up the plugin's ``Credential`` subclass in
    ``CREDENTIAL_REGISTRY`` and calls its ``validate`` classmethod. The
    base ``Credential.validate`` (defined in
    ``services/plugin/credential.py``) wires the shared scaffold
    (storage + status broadcast + error classification + response
    envelope) and dispatches the per-provider probe via the
    subclass-supplied ``_probe`` hook.
    """
    from services.plugin.credential import CREDENTIAL_REGISTRY

    provider = data["provider"].lower()
    normalized = dict(data, provider=provider)

    cred_cls = CREDENTIAL_REGISTRY.get(provider)
    if cred_cls is None:
        return {
            "success": False,
            "valid": False,
            "error": f"Unknown provider '{provider}' — no Credential class registered.",
        }
    return await cred_cls.validate(normalized)


def _lookup_credential_default(storage_key: str) -> Optional[str]:
    """Look up a field's catalogue ``default`` for the given storage key.

    Storage keys are either the provider id (``"openai"`` for cloud
    providers whose field key is ``apiKey``) or the field key itself
    (``"lmstudio_proxy"`` etc. for local-LLM providers). Both shapes
    map back to a credential_providers.json field; this helper finds
    the one and returns its ``default`` value if declared.
    """
    from services.credential_registry import get_credential_registry

    registry = get_credential_registry()
    for provider in registry.get_all_providers():
        provider_id = provider.get("id") or provider.get("name", "").lower()
        for field in provider.get("fields") or []:
            field_key = field.get("key")
            if not field_key:
                continue
            field_storage_key = provider_id if field_key == "apiKey" else field_key
            if field_storage_key == storage_key:
                default = field.get("default")
                return default if default else None
    return None


@ws_handler("provider")
async def handle_get_stored_api_key(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get stored API key for a provider.

    Response uses camelCase (``hasKey`` / ``apiKey``) to match the
    ``update_api_key_status`` broadcast shape — every WS payload the
    frontend receives for API key state uses the same convention.

    When nothing is stored AND the catalogue declares a ``default`` for
    this field (e.g. local-LLM canonical Base URL), the default is
    returned in ``apiKey`` with ``hasKey: false``. The frontend renders
    the value but tracks ``stored`` separately via ``hasKey`` so the
    validated/connected badge stays honest.
    """

    auth_service = container.auth_service()
    provider = data["provider"].lower()
    api_key = await auth_service.get_api_key(provider, data.get("session_id", "default"))
    if not api_key:
        default = _lookup_credential_default(provider)
        if default is not None:
            return {"provider": provider, "hasKey": False, "apiKey": default}
        return {"provider": provider, "hasKey": False}
    models = await auth_service.get_stored_models(provider, data.get("session_id", "default"))
    return {
        "provider": provider,
        "hasKey": True,
        "apiKey": api_key,
        "models": models,
        "timestamp": time.time(),
    }


@ws_handler("provider", "api_key")
async def handle_save_api_key(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save an API key (without validation).

    Supports client-side idempotency: if the client supplies a
    ``request_id`` (opaque UUID), duplicate calls within 60 s return the
    cached result instead of re-running the mutation.
    """
    from services.idempotency import get_idempotency_store

    store = get_idempotency_store("credentials")
    provider = data["provider"].lower()

    async def _do_save() -> Dict[str, Any]:
        auth_service = container.auth_service()
        broadcaster = get_status_broadcaster()
        await auth_service.store_api_key(
            provider=provider,
            api_key=data["api_key"].strip(),
            models=data.get("models", []),
            session_id=data.get("session_id", "default"),
        )
        await broadcaster.broadcast_credential_event(
            "credential.api_key.saved",
            provider=provider,
        )
        return {"provider": data["provider"]}

    return await store.run(data.get("request_id"), _do_save)


@ws_handler("provider")
async def handle_delete_api_key(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Delete stored API key. Idempotent on ``request_id``."""
    from services.idempotency import get_idempotency_store

    store = get_idempotency_store("credentials")
    provider = data["provider"].lower()

    async def _do_delete() -> Dict[str, Any]:
        auth_service = container.auth_service()
        broadcaster = get_status_broadcaster()
        await auth_service.remove_api_key(provider, data.get("session_id", "default"))
        await broadcaster.update_api_key_status(
            provider,
            valid=False,
            has_key=False,
            message="deleted",
            models=[],
        )
        await broadcaster.broadcast_credential_event(
            "credential.api_key.deleted",
            provider=provider,
        )
        return {"provider": data["provider"]}

    return await store.run(data.get("request_id"), _do_delete)


WS_HANDLERS: Dict[str, Any] = {
    "validate_api_key": handle_validate_api_key,
    "get_stored_api_key": handle_get_stored_api_key,
    "save_api_key": handle_save_api_key,
    "delete_api_key": handle_delete_api_key,
}


__all__ = [
    "WS_HANDLERS",
    "handle_delete_api_key",
    "handle_get_stored_api_key",
    "handle_save_api_key",
    "handle_validate_api_key",
]
