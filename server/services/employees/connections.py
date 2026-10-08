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
    def __init__(self, auth_service: Any, *, principal: Optional[str] = None) -> None:
        self._auth = auth_service
        self.principal = principal
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
                    cached = await provider_connection_state(provider, self._auth, **({"principal": self.principal} if self.principal is not None else {}))
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
        """LLM providers an agent could run on right now: one with a key
        stored or a local server saved, and each saved OpenAI-compatible
        endpoint by its reference (``openai_compatible:<slug>``). The bare
        ``openai_compatible`` id is never one: an agent set to it has no
        server to call."""
        from services.llm.config import ENDPOINT_PROVIDER, PROVIDER_CONFIGS

        usable = []
        for provider_id in PROVIDER_CONFIGS:
            if provider_id == ENDPOINT_PROVIDER:
                continue
            if self.provider(provider_id) is not None and await self.is_connected(provider_id):
                usable.append(provider_id)
        try:
            from services.llm.endpoints import list_endpoints

            usable += [endpoint.ref for endpoint in await list_endpoints(self._auth, **({"principal": self.principal} if self.principal is not None else {}))]
        except Exception:
            logger.warning("Could not list OpenAI-compatible endpoints", exc_info=True)
        return usable

    async def has_ai(self) -> bool:
        return bool(await self.ai_providers())


__all__ = ["Connections"]
