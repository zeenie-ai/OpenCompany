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


if "cli" not in sys.modules:
    _cli_stub = types.ModuleType("cli")
    _cli_stub.__path__ = []
    sys.modules["cli"] = _cli_stub
    _opencompany_tcp = types.ModuleType("cli.tcp")
    _opencompany_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _opencompany_tcp


@pytest.fixture
def fresh_registry(monkeypatch):
    """Reset the registry's backing dict so each test runs in isolation."""
    from services.deployment import canary_registry

    monkeypatch.setattr(canary_registry, "_REGISTERED", {})
    return canary_registry


# Dummy CloudEvents types used in registry tests — the real values come
# from each plugin's ``_events.py`` factory.
_CE_WEBHOOK = "com.opencompany.webhook.received"
_CE_CHAT = "com.opencompany.chat.message.received"
_CE_TASK = "com.opencompany.agent.task.completed"


class TestRegistryContract:
    """Surface API: register, query, snapshot, cloudevent_type lookup."""

    def test_unregistered_type_is_not_canary(self, fresh_registry):
        assert fresh_registry.is_canary_trigger_type("webhookTrigger") is False
        assert fresh_registry.cloudevent_type_for("webhookTrigger") is None

    def test_register_then_query_returns_true(self, fresh_registry):
        fresh_registry.register_canary_trigger_type("webhookTrigger", _CE_WEBHOOK)
        assert fresh_registry.is_canary_trigger_type("webhookTrigger") is True
        assert fresh_registry.cloudevent_type_for("webhookTrigger") == _CE_WEBHOOK

    def test_register_is_idempotent_with_same_cloudevent_type(self, fresh_registry):
        fresh_registry.register_canary_trigger_type("chatTrigger", _CE_CHAT)
        fresh_registry.register_canary_trigger_type("chatTrigger", _CE_CHAT)
        fresh_registry.register_canary_trigger_type("chatTrigger", _CE_CHAT)
        assert fresh_registry.canary_trigger_types() == frozenset({"chatTrigger"})
        assert fresh_registry.cloudevent_type_for("chatTrigger") == _CE_CHAT

    def test_register_rejects_conflicting_cloudevent_type(self, fresh_registry):
        """Reload-tolerance is bounded: same type re-registered with a
        diverging CloudEvents type is a loud :class:`ValueError`, not a
        silent overwrite. Catches plugin upgrades that change the
        producer envelope shape without coordinating with the listener."""
        fresh_registry.register_canary_trigger_type("chatTrigger", _CE_CHAT)
        with pytest.raises(ValueError, match="diverging"):
            fresh_registry.register_canary_trigger_type("chatTrigger", "com.opencompany.chat.message.changed")

    def test_multiple_types_coexist(self, fresh_registry):
        for t, ce in (
            ("webhookTrigger", _CE_WEBHOOK),
            ("chatTrigger", _CE_CHAT),
            ("taskTrigger", _CE_TASK),
        ):
            fresh_registry.register_canary_trigger_type(t, ce)
        assert fresh_registry.canary_trigger_types() == frozenset(
            {
                "webhookTrigger",
                "chatTrigger",
                "taskTrigger",
            }
        )
        assert fresh_registry.cloudevent_type_for("taskTrigger") == _CE_TASK

    def test_snapshot_is_immutable(self, fresh_registry):
        """``canary_trigger_types()`` returns a frozenset — mutating it
        must not leak back into the registry."""
        fresh_registry.register_canary_trigger_type("webhookTrigger", _CE_WEBHOOK)
        snap = fresh_registry.canary_trigger_types()
        assert isinstance(snap, frozenset)
        # frozenset has no add() — AttributeError if attempted.
        with pytest.raises(AttributeError):
            snap.add("chatTrigger")  # type: ignore[attr-defined]

    def test_rejects_non_string_node_type(self, fresh_registry):
        with pytest.raises(TypeError):
            fresh_registry.register_canary_trigger_type(None, _CE_WEBHOOK)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            fresh_registry.register_canary_trigger_type(123, _CE_WEBHOOK)  # type: ignore[arg-type]

    def test_rejects_empty_node_type(self, fresh_registry):
        with pytest.raises(TypeError):
            fresh_registry.register_canary_trigger_type("", _CE_WEBHOOK)

    def test_rejects_missing_cloudevent_type(self, fresh_registry):
        with pytest.raises(TypeError):
            fresh_registry.register_canary_trigger_type("webhookTrigger", None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            fresh_registry.register_canary_trigger_type("webhookTrigger", "")


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

    @pytest.mark.parametrize(
        "plugin_module,expected_type,expected_cloudevent_type",
        [
            ("nodes.trigger.webhook_trigger", "webhookTrigger", "com.opencompany.webhook.received"),
            ("nodes.trigger.chat_trigger", "chatTrigger", "com.opencompany.chat.message.received"),
            ("nodes.trigger.task_trigger", "taskTrigger", "com.opencompany.agent.task.completed"),
        ],
    )
    def test_plugin_import_registers_its_type(self, plugin_module, expected_type, expected_cloudevent_type):
        from services.deployment import canary_registry

        try:
            __import__(plugin_module)
        except ImportError as exc:  # pragma: no cover — env-dependent
            pytest.xfail(f"plugin module {plugin_module} not importable: {exc}")

        assert canary_registry.is_canary_trigger_type(expected_type), (
            f"Importing {plugin_module} should call "
            f"register_canary_trigger_type({expected_type!r}, ...). Check the "
            "module's __init__.py for the register_canary_trigger_type "
            "call at module scope."
        )
        # The cloudevent_type MUST match what the producer's _events.py
        # factory puts on outgoing envelopes — otherwise dispatch.emit's
        # Visibility query never finds the listener.
        assert canary_registry.cloudevent_type_for(expected_type) == expected_cloudevent_type, (
            f"{plugin_module} registered {expected_type!r} with the wrong "
            f"cloudevent_type. Expected {expected_cloudevent_type!r} to match "
            f"the producer's WorkflowEvent.type. If the producer factory "
            "changed, update both the factory AND the register_canary_trigger_type "
            "call together — drift here silently breaks the canary signal "
            "fan-out (TriggerListener starts ok, never reacts to events)."
        )


class TestCloudEventTypeMatchesSearchAttribute:
    """Regression: every canary trigger's registered cloudevent_type MUST
    equal the ``event.type`` field a producer-side factory call would
    emit. The deployment manager uses ``cloudevent_type_for(node_type)``
    as the ``EventType`` Search Attribute on the listener workflow; the
    producer side uses ``event.type`` from ``WorkflowEvent`` factories
    as the Visibility query value in ``services.events.dispatch.emit``.

    These two strings MUST agree or the Signal fan-out silently zeroes
    out — symptom is "TriggerListenerWorkflow started but never reacts
    to incoming events". This invariant locks the contract.
    """

    def test_every_registered_type_has_reverse_dns_cloudevent_type(self):
        """All registered cloudevent_types are reverse-DNS strings
        (``com.opencompany.*``) — that's the CloudEvents §3.1.2 type
        convention this codebase declares (see envelope.py:_TYPE_PREFIX).
        Anything else means a plugin registered the legacy snake_case
        event_waiter event_type by mistake."""
        from services.deployment import canary_registry

        for node_type in canary_registry.canary_trigger_types():
            ce_type = canary_registry.cloudevent_type_for(node_type)
            assert ce_type is not None, (
                f"canary_trigger_types includes {node_type!r} but " f"cloudevent_type_for() returned None — registry corrupted."
            )
            assert ce_type.startswith("com.opencompany."), (
                f"{node_type!r} registered with cloudevent_type={ce_type!r}; "
                f"expected a reverse-DNS string starting with 'com.opencompany.'. "
                f"That's the format dispatch.emit's Visibility query "
                f"substitutes from envelope.type — anything else fails to "
                f"match the listener's EventType Search Attribute."
            )

    def test_dispatch_emit_query_uses_event_type_field(self):
        """Locks the contract on dispatch.emit's Visibility query: it
        MUST substitute ``event.type`` (CloudEvents reverse-DNS), not
        any other field. If this regresses to ``event.subject`` /
        legacy event_type / etc. the SA lookup breaks."""
        from services.events import dispatch as dispatch_mod

        src = inspect.getsource(dispatch_mod._signal_running_consumers)
        # The query template MUST format the CloudEvents type into the
        # EventType filter.
        assert "EventType=" in dispatch_mod._RUNNING_CONSUMERS_QUERY, (
            "dispatch.emit's _RUNNING_CONSUMERS_QUERY no longer filters by " "EventType — listener-side SA registration relies on this key."
        )
        assert "event.type" in src or "event_type=event.type" in src, (
            "dispatch.emit no longer substitutes event.type into the "
            "Visibility query. The listener registered SA value must match "
            "what's substituted here; using subject / id / data fields "
            "breaks the contract."
        )

    def test_deployment_manager_uses_cloudevent_type_for_event_type_sa(self):
        """Source-introspection regression on the deployment-side fix:
        ``_start_canary_listener`` must construct the EventType
        SearchAttributePair from ``cloudevent_type_for(node_type)``,
        NOT from ``config.event_type`` (the legacy event_waiter string)."""
        from services.deployment.manager import DeploymentManager

        src = inspect.getsource(DeploymentManager._start_canary_listener)
        assert "cloudevent_type_for" in src, (
            "DeploymentManager._start_canary_listener no longer calls "
            "cloudevent_type_for() to resolve the EventType Search Attribute. "
            "Pre-fix it used event_waiter.TriggerConfig.event_type (legacy "
            "snake_case) which never matched dispatch.emit's Visibility "
            "query (CloudEvents reverse-DNS). Restore the registry lookup."
        )
        # The SearchAttributePair for EventType MUST use the cloudevent_type
        # variable, not the legacy event_type one.
        pair_pattern = re.search(
            r"SearchAttributePair\s*\(\s*event_type_key\s*,\s*(\w+)",
            src,
        )
        assert pair_pattern is not None, (
            "Couldn't find the SearchAttributePair(event_type_key, ...) " "construction. Check _start_canary_listener for the SA wiring."
        )
        sa_value_name = pair_pattern.group(1)
        assert sa_value_name == "cloudevent_type", (
            f"SearchAttributePair(event_type_key, {sa_value_name}) — expected "
            f"'cloudevent_type'. The legacy 'event_type' variable holds the "
            f"snake_case event_waiter string and will silently break the "
            f"Signal fan-out (TriggerListener starts ok, never reacts)."
        )
