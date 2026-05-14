"""Wave 12 C1: tests for the plugin-self-registered canary registry.

Locks two contracts:

1. The registry itself: idempotent registration, membership query,
   type-validation at register time.

2. The architectural invariant: ``services/deployment/manager.py``
   does NOT carry a framework-side allowlist of canary trigger types.
   Each plugin opts in from its own ``__init__.py``. This catches
   future regressions where someone re-introduces
   ``_CANARY_LISTENER_TRIGGER_TYPES = frozenset([...])`` as a
   convenience.

The pattern mirrors the existing ``test_plugin_self_containment.py``
invariants — source-introspection-driven, no live Temporal cluster
needed.
"""

from __future__ import annotations

import inspect
import re
import sys
import types
from unittest.mock import MagicMock

import pytest


if "machina" not in sys.modules:
    _machina = types.ModuleType("machina")
    _machina.__path__ = []
    sys.modules["machina"] = _machina
    _machina_tcp = types.ModuleType("machina.tcp")
    _machina_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["machina.tcp"] = _machina_tcp


@pytest.fixture
def fresh_registry(monkeypatch):
    """Reset the registry's backing set so each test runs in isolation."""
    from services.deployment import canary_registry

    monkeypatch.setattr(canary_registry, "_REGISTERED", set())
    return canary_registry


class TestRegistryContract:
    """Surface API: register, query, snapshot."""

    def test_unregistered_type_is_not_canary(self, fresh_registry):
        assert fresh_registry.is_canary_trigger_type("webhookTrigger") is False

    def test_register_then_query_returns_true(self, fresh_registry):
        fresh_registry.register_canary_trigger_type("webhookTrigger")
        assert fresh_registry.is_canary_trigger_type("webhookTrigger") is True

    def test_register_is_idempotent(self, fresh_registry):
        fresh_registry.register_canary_trigger_type("chatTrigger")
        fresh_registry.register_canary_trigger_type("chatTrigger")
        fresh_registry.register_canary_trigger_type("chatTrigger")
        assert fresh_registry.canary_trigger_types() == frozenset({"chatTrigger"})

    def test_multiple_types_coexist(self, fresh_registry):
        for t in ("webhookTrigger", "chatTrigger", "taskTrigger"):
            fresh_registry.register_canary_trigger_type(t)
        assert fresh_registry.canary_trigger_types() == frozenset({
            "webhookTrigger", "chatTrigger", "taskTrigger",
        })

    def test_snapshot_is_immutable(self, fresh_registry):
        """``canary_trigger_types()`` returns a frozenset — mutating it
        must not leak back into the registry."""
        fresh_registry.register_canary_trigger_type("webhookTrigger")
        snap = fresh_registry.canary_trigger_types()
        assert isinstance(snap, frozenset)
        # frozenset has no add() — TypeError if attempted.
        with pytest.raises(AttributeError):
            snap.add("chatTrigger")  # type: ignore[attr-defined]

    def test_rejects_non_string(self, fresh_registry):
        with pytest.raises(TypeError):
            fresh_registry.register_canary_trigger_type(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            fresh_registry.register_canary_trigger_type(123)   # type: ignore[arg-type]

    def test_rejects_empty_string(self, fresh_registry):
        with pytest.raises(TypeError):
            fresh_registry.register_canary_trigger_type("")


class TestNoFrameworkSideAllowlist:
    """Architectural invariant: DeploymentManager must NOT carry a
    framework-side allowlist of canary trigger types. Each plugin owns
    its own opt-in via ``register_canary_trigger_type``.

    Regression catch: if someone re-introduces a frozenset / list /
    set literal of trigger types in ``services/deployment/manager.py``
    to "make things easier," this test fails at import time with a
    pointer to the canary registry pattern instead.
    """

    def test_manager_does_not_declare_canary_listener_trigger_types(self):
        from services.deployment import manager as manager_mod

        # The frozenset would appear either as a module-level attribute
        # (``_CANARY_LISTENER_TRIGGER_TYPES``) or inside the class. Both
        # paths reject.
        assert not hasattr(manager_mod, "_CANARY_LISTENER_TRIGGER_TYPES"), (
            "services/deployment/manager.py declared _CANARY_LISTENER_TRIGGER_TYPES "
            "again. Canary opt-in lives in the plugin folders via "
            "services.deployment.canary_registry.register_canary_trigger_type — "
            "the manager queries via is_canary_trigger_type. Drop the constant "
            "and call the registry function instead."
        )

        # Also catch the regex'd literal form — even if someone names it
        # differently. Heuristic: look for a frozenset / set literal
        # whose members include any current canary trigger string.
        src = inspect.getsource(manager_mod)
        suspicious = re.search(
            r"frozenset\s*\(\s*\[?\s*[\"'](webhookTrigger|chatTrigger|taskTrigger)",
            src,
        )
        assert suspicious is None, (
            f"services/deployment/manager.py contains a hardcoded canary "
            f"trigger-type literal (matched at offset {suspicious.start()}). "
            "Use services.deployment.canary_registry instead — plugins opt "
            "in from their own __init__.py."
        )

    def test_manager_queries_via_registry_function(self):
        """The gate helper must route through is_canary_trigger_type so
        the framework-vs-plugin boundary is preserved."""
        from services.deployment import manager as manager_mod

        src = inspect.getsource(manager_mod.DeploymentManager._canary_listener_enabled_for)
        assert "is_canary_trigger_type" in src, (
            "DeploymentManager._canary_listener_enabled_for must query "
            "is_canary_trigger_type from services.deployment.canary_registry. "
            "Hardcoded membership checks defeat the plugin-self-registration "
            "pattern (regression source: tribal allowlist re-creation)."
        )


class TestPluginSelfRegistration:
    """Smoke: importing each canary plugin populates the registry.

    These tests intentionally do NOT use ``fresh_registry`` — they
    verify that real plugin import wires the registry. Run order
    matters only insofar as the plugin modules must be importable
    in the test env. If they aren't (heavy deps), the test xfails
    rather than masking the import failure.
    """

    @pytest.mark.parametrize("plugin_module,expected_type", [
        ("nodes.trigger.webhook_trigger", "webhookTrigger"),
        ("nodes.trigger.chat_trigger", "chatTrigger"),
        ("nodes.trigger.task_trigger", "taskTrigger"),
    ])
    def test_plugin_import_registers_its_type(self, plugin_module, expected_type):
        from services.deployment import canary_registry

        try:
            __import__(plugin_module)
        except ImportError as exc:  # pragma: no cover — env-dependent
            pytest.xfail(f"plugin module {plugin_module} not importable: {exc}")

        assert canary_registry.is_canary_trigger_type(expected_type), (
            f"Importing {plugin_module} should call "
            f"register_canary_trigger_type({expected_type!r}). Check the "
            "module's __init__.py for the register_canary_trigger_type "
            "call at module scope."
        )
