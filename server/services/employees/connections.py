"""Connection state for one request: which apps and AI providers are usable.

Wraps ``services.credential_registry.provider_connection_state`` (the same
rules the credential catalogue serves) with a per-request cache, so a team
list that mentions Gmail five times asks the credential store once. The
owner's apps (the registry's and the ones they saved, such as custom
connectors; apps.py) are read once per request too.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from core.logging import get_logger
from services.credential_registry import get_credential_registry, provider_connection_state
from services.employees.apps import AppSpec, get_apps, source_apps

logger = get_logger(__name__)

_DISCONNECTED: Dict[str, Any] = {"stored": False, "connected": False, "account_label": None}


class Connections:
    def __init__(self, auth_service: Any, *, principal: Optional[str] = None) -> None:
        self._auth = auth_service
        self.principal = principal
        self._registry = get_credential_registry()
        self._states: Dict[str, Dict[str, Any]] = {}
        self._apps: Optional[Dict[str, AppSpec]] = None
        self._saved: frozenset = frozenset()

    async def apps(self) -> Mapping[str, AppSpec]:
        """Every app the owner's employees can use: the registry's, in its
        order, then the ones the owner saved (apps.source_apps)."""
        if self._apps is None:
            apps = dict(get_apps())
            saved = [app for app in await source_apps(self.principal) if app.id not in apps]
            apps.update((app.id, app) for app in saved)
            self._saved = frozenset(app.id for app in saved)
            self._apps = apps
        return self._apps

    async def app(self, app_id: str) -> Optional[AppSpec]:
        return (await self.apps()).get(app_id)

    async def app_connected(self, app: AppSpec) -> bool:
        """Whether ``app`` can be used now. An app the owner saved exists
        only while it is saved, so it is connected."""
        await self.apps()
        return app.id in self._saved or await self.is_connected(app.provider_id)

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
        """Every app that is connected, in ``apps`` order."""
        return [app.id for app in (await self.apps()).values() if await self.app_connected(app)]

    async def app_ref(self, app: AppSpec) -> Dict[str, Any]:
        """The ``AppRef`` a summary shows for one app."""
        provider = self.provider(app.provider_id) or {}
        return {
            "app_id": app.id,
            "provider_id": app.provider_id,
            "name": app.name,
            "icon_ref": provider.get("icon_ref") or app.icon_ref,
            "connected": await self.app_connected(app),
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
