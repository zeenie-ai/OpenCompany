"""Connection state for one request: which apps and AI providers are usable.

Wraps ``services.credential_registry.provider_connection_state`` (the same
rules the credential catalogue serves) with a per-request cache, so a team
list that mentions Gmail five times asks the credential store once.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.logging import get_logger
from services.credential_registry import get_credential_registry, provider_connection_state
from services.employees.apps import AppSpec, get_apps

logger = get_logger(__name__)

_DISCONNECTED: Dict[str, Any] = {"stored": False, "connected": False, "account_label": None}


class Connections:
    def __init__(self, auth_service: Any) -> None:
        self._auth = auth_service
        self._registry = get_credential_registry()
        self._states: Dict[str, Dict[str, Any]] = {}

    def provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        return self._registry.get_provider(provider_id)

    async def state(self, provider_id: str) -> Dict[str, Any]:
        cached = self._states.get(provider_id)
        if cached is None:
            provider = self.provider(provider_id)
            cached = dict(_DISCONNECTED)
            if provider is not None:
                try:
                    cached = await provider_connection_state(provider, self._auth)
                except Exception:
                    # One broken credential check must not take down a team
                    # list; the app just reads as not connected.
                    logger.warning("Connection check failed", provider_id=provider_id, exc_info=True)
            self._states[provider_id] = cached
        return cached

    async def is_connected(self, provider_id: str) -> bool:
        return bool((await self.state(provider_id)).get("connected"))

    async def connected_app_ids(self) -> List[str]:
        """Every app whose provider is connected, in registry order."""
        return [app.id for app in get_apps().values() if await self.is_connected(app.provider_id)]

    async def app_ref(self, app: AppSpec) -> Dict[str, Any]:
        """The ``AppRef`` a summary shows for one app."""
        provider = self.provider(app.provider_id) or {}
        return {
            "app_id": app.id,
            "provider_id": app.provider_id,
            "name": app.name,
            "icon_ref": provider.get("icon_ref"),
            "connected": await self.is_connected(app.provider_id),
            "supported": True,
        }

    async def ai_providers(self) -> List[str]:
        """LLM providers an agent could run on right now (a key stored, a
        local server saved, or a named OpenAI-compatible endpoint)."""
        from services.llm.config import PROVIDER_CONFIGS

        usable = []
        for provider_id in PROVIDER_CONFIGS:
            if self.provider(provider_id) is not None and await self.is_connected(provider_id):
                usable.append(provider_id)
        return usable

    async def has_ai(self) -> bool:
        return bool(await self.ai_providers())


__all__ = ["Connections"]
