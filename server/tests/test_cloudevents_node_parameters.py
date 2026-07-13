"""Lock the CloudEvents contract for ``node_parameters_updated`` broadcasts.

Pre-CloudEvents-cleanup three sites emitted ``node_parameters_updated``
as raw dicts (``routers/websocket.py:handle_save_node_parameters``,
``services/cli_agent/service.py:_persist_memory``, and F4.B's
``services/temporal/agent_activities.py:persist_agent_turn``). Per RFC
§6.4 these are NEW broadcasts and must use ``WorkflowEvent`` envelopes
— raw-dict is reserved for telemetry-high-frequency carve-outs only.

This invariant pins:

- ``WorkflowEvent.node_parameters_updated(...)`` factory exists with the
  canonical reverse-DNS type ``com.opencompany.node.parameters.updated``.
- ``StatusBroadcaster.broadcast_node_parameters_updated(...)`` wrapper
  exists and routes through the factory.
- The three legacy emission sites all call the broadcaster wrapper, not
  ``broadcaster.broadcast({...})`` directly.

Same ``inspect.getsource`` introspection pattern as
``test_credential_broadcasts.py`` + ``test_status_broadcasts.py``.
"""

from __future__ import annotations

import inspect


class TestFactoryShape:
    def test_factory_exists_and_returns_workflow_event(self):
        from services.events import WorkflowEvent

        event = WorkflowEvent.node_parameters_updated(
            "test_node_id",
            parameters={"foo": "bar"},
            workflow_id="wf_abc",
            source_hint="agent",
        )
        # CloudEvents envelope shape.
        assert event.source == "opencompany://services/parameters"
        assert event.type == "com.opencompany.node.parameters.updated"
        assert event.subject == "test_node_id"
        # workflow_id extension is preserved.
        assert event.workflow_id == "wf_abc"
        # data carries the inner payload.
        assert event.data["node_id"] == "test_node_id"
        assert event.data["parameters"] == {"foo": "bar"}
        assert event.data["source"] == "agent"

    def test_factory_defaults(self):
        from services.events import WorkflowEvent

        event = WorkflowEvent.node_parameters_updated(
            "test_node_id",
            parameters={},
        )
        assert event.data["version"] == 1
        assert event.data["source"] == "user"
        assert event.workflow_id is None


class TestBroadcasterWrapper:
    def test_wrapper_method_exists(self):
        from services.status_broadcaster import StatusBroadcaster

        assert hasattr(StatusBroadcaster, "broadcast_node_parameters_updated"), (
            "StatusBroadcaster must expose broadcast_node_parameters_updated "
            "(RFC §6.4 cross-cutting broadcaster wrapper for the factory)."
        )

    def test_wrapper_routes_through_factory(self):
        """The wrapper must build a WorkflowEvent (not raw dict). Source
        introspection prevents accidental regression to raw-dict
        broadcasts when this method is rewritten."""
        from services.status_broadcaster import StatusBroadcaster

        src = inspect.getsource(StatusBroadcaster.broadcast_node_parameters_updated)
        assert "WorkflowEvent" in src, (
            "broadcast_node_parameters_updated must build a " "WorkflowEvent envelope; raw-dict broadcasts violate RFC §6.4."
        )
        assert "node_parameters_updated" in src, (
            "Wire-format key node_parameters_updated must stay in " "the broadcast payload (FE back-compat)."
        )


class TestEmissionSites:
    """No new raw-dict ``node_parameters_updated`` emissions. All three
    callers route through the broadcaster wrapper."""

    EMISSION_SITES = [
        ("routers.websocket", "handle_save_node_parameters"),
        ("services.cli_agent.service", "AICliService._persist_memory"),
        ("services.temporal.agent_activities", "persist_agent_turn"),
    ]

    def test_all_sites_use_broadcaster_wrapper(self):
        import importlib

        for module_name, attr in self.EMISSION_SITES:
            module = importlib.import_module(module_name)
            # Resolve the attribute — could be a function or a method on
            # a class (``_persist_memory`` is a method on AICliService).
            target = module
            for part in attr.split("."):
                target = getattr(target, part)

            src = inspect.getsource(target)
            assert "broadcast_node_parameters_updated" in src, (
                f"{module_name}.{attr} must call "
                f"broadcaster.broadcast_node_parameters_updated, not the "
                f"raw broadcast({{type: 'node_parameters_updated', ...}}) "
                f"pattern. Raw-dict emissions violate RFC §6.4 CloudEvents "
                f"discipline."
            )

    def test_no_raw_dict_node_parameters_in_source(self):
        """Defensive grep: search the three module sources for the raw-dict
        pattern. Catches future regressions where someone copy-pastes the
        old emission style."""
        import importlib

        bad_pattern = '"type": "node_parameters_updated"'
        for module_name, _ in self.EMISSION_SITES:
            module = importlib.import_module(module_name)
            src = inspect.getsource(module)
            assert bad_pattern not in src, (
                f"{module_name} contains a raw-dict node_parameters_updated "
                f"emission. Use broadcaster.broadcast_node_parameters_updated "
                f"so the wire payload is a WorkflowEvent envelope."
            )
