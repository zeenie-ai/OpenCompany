"""Wave 12 C4 sub-piece A: social-provider registry contract + invariant.

Locks two layers:

1. **Registry surface**: idempotent registration, retrieval, snapshot.

2. **Architectural invariant**: ``nodes/social/_base.py`` does NOT
   import ``handle_whatsapp_send`` (or any other plugin's send
   function) directly. The dispatch must route through
   :func:`services.plugin.social_provider_registry.get_social_send_handler`.
   This is the regression catch for the load-bearing cross-plugin reach
   the migration closed.

Same style as ``test_canary_registry.py`` and
``test_plugin_self_containment.py`` — source-introspection-driven,
no live Temporal cluster needed.
"""

from __future__ import annotations

import inspect
import re
import sys
import types
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest


if "machina" not in sys.modules:
    _machina = types.ModuleType("cli")
    _machina.__path__ = []
    sys.modules["cli"] = _machina
    _machina_tcp = types.ModuleType("cli.tcp")
    _machina_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _machina_tcp


@pytest.fixture
def fresh_registry(monkeypatch):
    """Reset the registry's backing dict so each test runs in isolation.

    The production registry accumulates as plugins import — that's
    correct for runtime. Scope assertions must isolate from accumulated
    state so a future plugin opt-in doesn't silently flip outcomes.
    """
    from services.plugin import social_provider_registry as reg

    # IdempotentRegistry exposes its backing dict via .items() but the
    # field is `_items`. monkeypatching the inner dict is the standard
    # pattern used in other registry tests.
    fresh = type(reg._REGISTRY)(reg._REGISTRY._name)  # type: ignore[attr-defined]
    monkeypatch.setattr(reg, "_REGISTRY", fresh)
    return reg


class TestRegistryContract:
    """Surface API: register, query, snapshot."""

    def test_unregistered_platform_returns_none(self, fresh_registry):
        assert fresh_registry.get_social_send_handler("whatsapp") is None

    @pytest.mark.asyncio
    async def test_register_then_get_returns_handler(self, fresh_registry):
        captured = []

        async def fake_handler(params: Dict[str, Any]):
            captured.append(params)
            return {"sent": True}

        fresh_registry.register_social_send_handler("whatsapp", fake_handler)
        handler = fresh_registry.get_social_send_handler("whatsapp")

        assert handler is fake_handler
        result = await handler({"recipient": "+1234567890"})
        assert result == {"sent": True}
        assert captured == [{"recipient": "+1234567890"}]

    def test_idempotent_register_same_callable(self, fresh_registry):
        async def h(params):
            return {}

        fresh_registry.register_social_send_handler("whatsapp", h)
        fresh_registry.register_social_send_handler("whatsapp", h)
        assert fresh_registry.registered_platforms() == frozenset({"whatsapp"})

    def test_conflicting_register_raises(self, fresh_registry):
        async def h1(params):
            return {}

        async def h2(params):
            return {}

        fresh_registry.register_social_send_handler("whatsapp", h1)
        with pytest.raises(ValueError, match="already registered"):
            fresh_registry.register_social_send_handler("whatsapp", h2)

    def test_multiple_platforms_coexist(self, fresh_registry):
        async def h(params):
            return {}

        for p in ("whatsapp", "telegram", "slack"):
            fresh_registry.register_social_send_handler(p, h)

        assert fresh_registry.registered_platforms() == frozenset({
            "whatsapp", "telegram", "slack",
        })


class TestNoCrossPluginReachInSocialBase:
    """Architectural invariant: ``nodes/social/_base.py`` must NOT
    import any plugin's ``_service`` directly. Dispatch goes through
    ``get_social_send_handler`` from the registry.

    Regression catch: if someone re-introduces the ``from nodes.whatsapp.
    _service import handle_whatsapp_send`` (or any sibling), this test
    fails at import-time with a pointer to the registry pattern.
    """

    _FORBIDDEN_PATTERN = re.compile(
        r"^\s*from\s+nodes\.\w+\._service\s+import",
        re.MULTILINE,
    )

    def test_social_base_does_not_cross_import_service(self):
        from nodes.social import _base as social_base

        src = inspect.getsource(social_base)
        match = self._FORBIDDEN_PATTERN.search(src)
        assert match is None, (
            f"nodes/social/_base.py contains a cross-plugin _service "
            f"import (matched at offset {match.start()}):\n  "
            f"{src[match.start():match.end()].strip()}\n"
            "Route through services.plugin.social_provider_registry."
            "get_social_send_handler('<platform>') instead. Each platform "
            "plugin self-registers from its __init__.py."
        )

    def test_social_base_calls_registry_lookup(self):
        """The dispatcher must query the registry — not just hide
        the cross-plugin import behind a runtime import."""
        from nodes.social import _base as social_base

        src = inspect.getsource(social_base._send_via_whatsapp)
        assert "get_social_send_handler" in src, (
            "_send_via_whatsapp must call get_social_send_handler "
            "from services.plugin.social_provider_registry. Hardcoded "
            "fallback imports defeat the plugin-self-registration pattern."
        )


class TestWhatsappPluginSelfRegistersAsSocialProvider:
    """Importing the whatsapp plugin registers it as the 'whatsapp'
    social send handler.
    """

    def test_whatsapp_plugin_import_populates_registry(self):
        from services.plugin import social_provider_registry as reg

        try:
            __import__("nodes.whatsapp")
        except ImportError as exc:  # pragma: no cover
            pytest.xfail(f"nodes.whatsapp not importable: {exc}")

        handler = reg.get_social_send_handler("whatsapp")
        assert handler is not None, (
            "Importing nodes.whatsapp should call "
            "register_social_send_handler('whatsapp', handle_whatsapp_send). "
            "Check the __init__.py bottom section."
        )
        # And it should be callable (sanity).
        assert callable(handler)
