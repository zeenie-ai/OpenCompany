"""F4.A: per-type activity dispatch resolver invariants.

Locks the orchestrator's `_resolve_activity` contract:
  - flag OFF (default): every node routes to legacy `execute_node_activity`
  - flag ON + registered plugin: routes to `node.{type}.v{version}`
  - flag ON + unknown type: falls back to legacy
  - task_queue is None while `temporal_worker_pool_enabled` is off;
    `cls.task_queue` once the pool flag is on (Wave 16.3 — one
    TemporalWorkerPool worker per declared queue polls it)

If these invariants drift the Temporal worker will either silently lose
per-type activity wiring (regression to single-dispatcher) or schedule
activities on queues no worker polls (workflow hangs). Same coverage
pattern as test_credential_broadcasts / test_status_broadcasts —
introspect the class, exercise the method, assert the shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import nodes  # noqa: F401 -- triggers plugin discovery
from services.temporal.workflow import MachinaWorkflow


@pytest.fixture
def workflow_instance() -> MachinaWorkflow:
    return MachinaWorkflow()


def _set_flag(value: bool, *, pool: bool = False):
    """Patch the ``Settings`` import used by ``_resolve_activity``.

    ``tests/conftest.py`` stubs ``core.config.Settings`` as a MagicMock,
    so we can't go through the real env var. Instead, swap the lazy
    import inside the resolver for a SimpleNamespace returning the
    desired flag values. Same effective contract — the resolver reads
    ``temporal_per_type_dispatch`` + ``temporal_worker_pool_enabled``.
    """

    def fake_settings_factory():
        return SimpleNamespace(
            temporal_per_type_dispatch=value,
            temporal_worker_pool_enabled=pool,
        )

    return patch("core.config.Settings", side_effect=lambda: fake_settings_factory())


class TestResolveActivityFlagOff:
    """Default behavior: legacy dispatcher for every node, no surprises."""

    def test_known_plugin_routes_to_legacy(self, workflow_instance):
        with _set_flag(False):
            name, queue = workflow_instance._resolve_activity("pythonExecutor")
        assert name == "execute_node_activity"
        assert queue is None

    def test_unknown_type_routes_to_legacy(self, workflow_instance):
        with _set_flag(False):
            name, queue = workflow_instance._resolve_activity("nonExistentType")
        assert name == "execute_node_activity"
        assert queue is None

    def test_agent_routes_to_legacy(self, workflow_instance):
        with _set_flag(False):
            name, queue = workflow_instance._resolve_activity("aiAgent")
        assert name == "execute_node_activity"
        assert queue is None


class TestResolveActivityFlagOn:
    """Flag on: per-type for registered plugins, legacy fallback otherwise."""

    def test_known_plugin_routes_per_type(self, workflow_instance):
        with _set_flag(True):
            name, queue = workflow_instance._resolve_activity("pythonExecutor")
        assert name == "node.pythonExecutor.v1"
        # queue stays None while temporal_worker_pool_enabled is off —
        # the single manager worker polls only the default queue.
        assert queue is None

    def test_agent_routes_per_type(self, workflow_instance):
        with _set_flag(True):
            name, queue = workflow_instance._resolve_activity("aiAgent")
        assert name == "node.aiAgent.v1"
        assert queue is None

    def test_unknown_type_falls_back_to_legacy(self, workflow_instance):
        with _set_flag(True):
            name, queue = workflow_instance._resolve_activity("nonExistentType")
        assert name == "execute_node_activity"
        assert queue is None

    def test_specialized_agent_uses_class_version(self, workflow_instance):
        """All 117 plugins default to version=1; the activity name must
        include the version. If a plugin ever overrides cls.version, this
        test catches the schema break-point."""
        with _set_flag(True):
            name, _ = workflow_instance._resolve_activity("coding_agent")
        assert name.startswith("node.coding_agent.v")
        # Extract version; should match the class's declared version.
        from services.node_registry import get_node_class

        cls = get_node_class("coding_agent")
        assert name == f"node.coding_agent.v{cls.version}"


class TestResolveActivityPoolRouting:
    """Wave 16.3: with BOTH flags on, the resolver returns the plugin's
    declared ``cls.task_queue`` so per-type activities land on their
    specialised TemporalWorkerPool worker. Pool flag off = None (the
    rollback channel)."""

    def test_pool_on_routes_to_declared_queue(self, workflow_instance):
        from services.node_registry import get_node_class

        with _set_flag(True, pool=True):
            name, queue = workflow_instance._resolve_activity("pythonExecutor")
        assert name == "node.pythonExecutor.v1"
        assert queue == get_node_class("pythonExecutor").task_queue
        assert queue == "code-exec"

    def test_pool_on_ai_agent_routes_to_ai_heavy(self, workflow_instance):
        with _set_flag(True, pool=True):
            _, queue = workflow_instance._resolve_activity("aiAgent")
        assert queue == "ai-heavy"

    def test_pool_off_is_the_rollback_channel(self, workflow_instance):
        with _set_flag(True, pool=False):
            _, queue = workflow_instance._resolve_activity("pythonExecutor")
        assert queue is None

    def test_pool_on_unknown_type_still_falls_back_to_legacy(self, workflow_instance):
        with _set_flag(True, pool=True):
            name, queue = workflow_instance._resolve_activity("nonExistentType")
        assert name == "execute_node_activity"
        assert queue is None


class TestAgentWorkflowDispatch:
    """F4.B: when the agent-workflow flag is on AND the node type is in
    AGENT_WORKFLOW_TYPES, dispatch must route through the child workflow
    instead of an activity. Excluded types (rlm_agent / claude_code_agent)
    keep using the activity path."""

    def _set_flags(self, *, per_type: bool, agent_wf: bool):
        from types import SimpleNamespace
        from unittest.mock import patch as _patch

        def fake_settings():
            return SimpleNamespace(
                temporal_per_type_dispatch=per_type,
                temporal_agent_workflow_enabled=agent_wf,
                temporal_worker_pool_enabled=False,
            )

        return _patch("core.config.Settings", side_effect=lambda: fake_settings())

    def test_chat_agent_routes_to_child_workflow(self, workflow_instance):
        with self._set_flags(per_type=True, agent_wf=True):
            dispatch = workflow_instance._resolve_dispatch("chatAgent")
        assert dispatch["kind"] == "child_workflow"
        assert dispatch["name"] == "AgentWorkflow"

    def test_ai_agent_routes_to_child_workflow(self, workflow_instance):
        with self._set_flags(per_type=True, agent_wf=True):
            dispatch = workflow_instance._resolve_dispatch("aiAgent")
        assert dispatch["kind"] == "child_workflow"

    def test_specialized_agent_routes_to_child_workflow(self, workflow_instance):
        for t in ("coding_agent", "web_agent", "orchestrator_agent", "ai_employee"):
            with self._set_flags(per_type=True, agent_wf=True):
                dispatch = workflow_instance._resolve_dispatch(t)
            assert dispatch["kind"] == "child_workflow", f"{t} should route to AgentWorkflow when F4.B is on"

    def test_excluded_agents_stay_on_activity_path(self, workflow_instance):
        """rlm_agent / claude_code_agent are NOT migrated (externalised
        session state). They must use the per-type activity path even
        when F4.B is on."""
        for t in ("rlm_agent", "claude_code_agent"):
            with self._set_flags(per_type=True, agent_wf=True):
                dispatch = workflow_instance._resolve_dispatch(t)
            assert dispatch["kind"] == "activity", f"{t} must NOT migrate to AgentWorkflow (externalised session)"
            assert dispatch["name"] == f"node.{t}.v1"

    def test_agent_workflow_flag_off_falls_back_to_activity(self, workflow_instance):
        with self._set_flags(per_type=True, agent_wf=False):
            dispatch = workflow_instance._resolve_dispatch("chatAgent")
        assert dispatch["kind"] == "activity"
        assert dispatch["name"] == "node.chatAgent.v1"

    def test_non_agent_types_stay_on_activity_path(self, workflow_instance):
        """Tool / utility / model nodes shouldn't accidentally route
        through AgentWorkflow even when F4.B is on."""
        for t in ("pythonExecutor", "calculatorTool", "openaiChatModel"):
            with self._set_flags(per_type=True, agent_wf=True):
                dispatch = workflow_instance._resolve_dispatch(t)
            assert dispatch["kind"] == "activity", f"{t} is not an agent type; must NOT route to AgentWorkflow"


class TestPerNodeTypeIconResolution:
    """``get_plugin_icon_path`` resolves per-node-type icons before the
    shared folder icon, so multi-node folders (whatsapp / telegram /
    stripe) can serve distinct icons per node type from one folder."""

    def test_whatsapp_send_resolves_per_node_icon(self):
        from nodes._visuals import get_plugin_icon_path

        path = get_plugin_icon_path("whatsappSend")
        assert path is not None
        assert path.name == "icon_whatsappSend.svg"

    def test_whatsapp_receive_resolves_per_node_icon(self):
        from nodes._visuals import get_plugin_icon_path

        path = get_plugin_icon_path("whatsappReceive")
        assert path is not None
        assert path.name == "icon_whatsappReceive.svg"

    def test_whatsapp_db_resolves_per_node_icon(self):
        from nodes._visuals import get_plugin_icon_path

        path = get_plugin_icon_path("whatsappDb")
        assert path is not None
        assert path.name == "icon_whatsappDb.svg"

    def test_telegram_falls_back_to_shared_icon(self):
        """telegram_send + telegram_receive share one icon.svg —
        per-node icons aren't required when the brand mark is the
        same for both."""
        from nodes._visuals import get_plugin_icon_path

        path = get_plugin_icon_path("telegramSend")
        assert path is not None
        assert path.name == "icon.svg"


class TestPerTypeActivityCollection:
    """`collect_plugin_activities()` must return one callable per registered
    plugin class — TemporalWorkerManager's per-type registration depends on
    this returning the full set, not a queue-filtered subset (F4.A puts
    all per-type entries on the default queue until TemporalWorkerPool
    lands)."""

    def test_collect_returns_one_per_plugin(self):
        from services.node_registry import registered_node_classes
        from services.temporal.plugin_activities import collect_plugin_activities

        activities = collect_plugin_activities()
        # Per-type activity count must equal registered plugin count.
        assert len(activities) == len(registered_node_classes())

    def test_per_type_activity_has_temporal_metadata(self):
        """Every per-type activity must carry the @activity.defn decorator
        with a `node.{type}.v{version}` name — otherwise the worker can't
        register it."""
        from services.temporal.plugin_activities import collect_plugin_activities

        activities = collect_plugin_activities()
        for a in activities:
            # temporalio attaches metadata as `__temporal_activity_definition`.
            defn = getattr(a, "__temporal_activity_definition", None)
            assert defn is not None, f"activity {a} missing Temporal defn"
            assert defn.name.startswith("node."), defn.name
            assert ".v" in defn.name, defn.name
