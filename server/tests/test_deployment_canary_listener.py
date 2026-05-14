"""Wave 12 C1 canary: DeploymentManager Temporal-listener integration tests.

These tests exercise the canary plumbing inside :class:`DeploymentManager`
WITHOUT a live Temporal cluster — the ``container.temporal_client()``
call is monkeypatched to return a stub whose ``client.start_workflow``
and ``client.list_workflows`` calls are captured and asserted.

The canonical pattern under test:

1. **Deterministic listener id** = ``trigger-listener-{workflow_id}-{node_id}``.
   Re-deploy of the same MachinaOs workflow targets the same Temporal id.
2. **WorkflowIDConflictPolicy.USE_EXISTING** — Temporal returns the existing
   handle instead of erroring on a duplicate-id start. Idempotent re-deploy.
3. **TypedSearchAttributes set at start** — ``EventType``, ``TriggerNodeId``,
   ``EventWorkflowId``, ``EventTriggerKind``. These ARE the registry the
   cancel path queries.
4. **Cancel via ``list_workflows(query=...) + handle.cancel()``** — no
   instance-state dict, no handle caching. The Temporal server's
   Visibility store is the source of truth.

Cross-confirmed across three official sources (Temporal docs, samples-python,
Inngest/Prefect/n8n source analysis). Locking the implementation against
these invariants here prevents future drift back to tribal in-memory tracking.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub `machina` namespace — same pattern as other event-framework tests.
if "machina" not in sys.modules:
    _machina = types.ModuleType("machina")
    _machina.__path__ = []
    sys.modules["machina"] = _machina
    _machina_tcp = types.ModuleType("machina.tcp")
    _machina_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["machina.tcp"] = _machina_tcp


@pytest.fixture
def fresh_canary_registry(monkeypatch):
    """Reset the canary-registry backing store to a known empty state.

    The production registry accumulates as plugin modules import — that's
    correct for runtime. Tests that assert canary scope must isolate
    from accumulating module state so a future plugin opt-in doesn't
    silently flip a test outcome. This fixture monkeypatches the
    underlying ``_REGISTERED`` set; ``monkeypatch`` restores it after
    the test.
    """
    from services.deployment import canary_registry

    monkeypatch.setattr(canary_registry, "_REGISTERED", set())
    return canary_registry


# ---------------------------------------------------------------------------
# Helpers — build a DeploymentManager with the minimum scaffolding to drive
# the canary code paths. Database / status broadcaster are pure mocks.
# ---------------------------------------------------------------------------


def _build_manager_with_state(workflow_id: str, nodes, edges, session_id="sess"):
    """Build a DeploymentManager populated with one in-flight deployment.

    The canary methods read ``self._deployments[workflow_id]`` for nodes/edges
    snapshots, so we register that state explicitly.
    """
    from services.deployment.manager import DeploymentManager
    from services.deployment.state import DeploymentState

    database = MagicMock()
    database.get_node_parameters = AsyncMock(return_value={})
    broadcaster = MagicMock()
    broadcaster.update_node_status = AsyncMock()

    mgr = DeploymentManager(
        database=database,
        execute_workflow_fn=AsyncMock(return_value={"success": True}),
        store_output_fn=AsyncMock(),
        broadcaster=broadcaster,
    )
    mgr._deployments[workflow_id] = DeploymentState(
        deployment_id=f"deploy_{workflow_id}",
        workflow_id=workflow_id,
        is_running=True,
        nodes=nodes,
        edges=edges,
        session_id=session_id,
    )
    return mgr, broadcaster


def _node(node_id: str, node_type: str) -> Dict[str, Any]:
    return {"id": node_id, "type": node_type, "data": {}}


# ---------------------------------------------------------------------------
# C1d.1 — deterministic listener id
# ---------------------------------------------------------------------------


class TestDeterministicListenerId:
    """Re-deploy targets the same Temporal id; downstream USE_EXISTING
    policy handles the duplicate gracefully."""

    def test_id_format_is_stable(self):
        from services.deployment.manager import DeploymentManager

        first = DeploymentManager._listener_workflow_id("wf-1", "wh-1")
        second = DeploymentManager._listener_workflow_id("wf-1", "wh-1")
        assert first == second == "trigger-listener-wf-1-wh-1"

    def test_id_differs_per_node_in_same_workflow(self):
        from services.deployment.manager import DeploymentManager

        a = DeploymentManager._listener_workflow_id("wf-1", "wh-1")
        b = DeploymentManager._listener_workflow_id("wf-1", "wh-2")
        assert a != b


# ---------------------------------------------------------------------------
# C1d.2 — canary scope: flag + type
# ---------------------------------------------------------------------------


class TestCanaryScope:
    """Canary only activates for trigger types opted in via
    :func:`register_canary_trigger_type` AND when the feature flag is on.
    Uses ``fresh_canary_registry`` so each test starts with an empty
    registry — no cross-test pollution from accumulated plugin imports."""

    @pytest.mark.asyncio
    async def test_disabled_when_flag_off(self, monkeypatch, fresh_canary_registry):
        from services.deployment.manager import DeploymentManager

        fresh_canary_registry.register_canary_trigger_type("webhookTrigger")

        class _Off:
            event_framework_enabled = False

        import core.config
        monkeypatch.setattr(core.config, "Settings", lambda: _Off())

        result = await DeploymentManager._canary_listener_enabled_for("webhookTrigger")
        assert result is False

    @pytest.mark.asyncio
    async def test_disabled_for_unregistered_type(self, monkeypatch, fresh_canary_registry):
        """Plugin must opt in via register_canary_trigger_type; types
        not in the registry stay on the legacy path even with the flag on."""
        from services.deployment.manager import DeploymentManager

        # Registry is empty (fresh fixture); whatsappReceive not registered.
        class _On:
            event_framework_enabled = True

        import core.config
        monkeypatch.setattr(core.config, "Settings", lambda: _On())

        result = await DeploymentManager._canary_listener_enabled_for("whatsappReceive")
        assert result is False

    @pytest.mark.asyncio
    async def test_enabled_when_registered_and_flag_on(self, monkeypatch, fresh_canary_registry):
        from services.deployment.manager import DeploymentManager

        fresh_canary_registry.register_canary_trigger_type("webhookTrigger")
        fresh_canary_registry.register_canary_trigger_type("chatTrigger")

        class _On:
            event_framework_enabled = True

        import core.config
        monkeypatch.setattr(core.config, "Settings", lambda: _On())

        assert await DeploymentManager._canary_listener_enabled_for("webhookTrigger") is True
        assert await DeploymentManager._canary_listener_enabled_for("chatTrigger") is True
        # Sanity: still respects the registry boundary.
        assert await DeploymentManager._canary_listener_enabled_for("randomThing") is False


class TestTriggerKindDerivation:
    """``EventTriggerKind`` Search Attribute is derived from node_type
    by stripping the ``Trigger`` / ``Receive`` suffix. Locks the mapping
    so future trigger types (e.g. ``slackReceive``) get a sensible
    default kind without per-type code changes in DeploymentManager."""

    def test_strips_trigger_suffix(self):
        from services.deployment.manager import DeploymentManager

        assert DeploymentManager._trigger_kind_for("webhookTrigger") == "webhook"
        assert DeploymentManager._trigger_kind_for("chatTrigger") == "chat"
        assert DeploymentManager._trigger_kind_for("taskTrigger") == "task"

    def test_strips_receive_suffix(self):
        from services.deployment.manager import DeploymentManager

        assert DeploymentManager._trigger_kind_for("telegramReceive") == "telegram"
        assert DeploymentManager._trigger_kind_for("whatsappReceive") == "whatsapp"
        assert DeploymentManager._trigger_kind_for("emailReceive") == "email"

    def test_unknown_suffix_returns_node_type_verbatim(self):
        from services.deployment.manager import DeploymentManager

        # Defensive fallback — never crashes, just emits whatever the
        # node_type was. Ops dashboards see the raw value and can
        # update the chip-mapping when needed.
        assert DeploymentManager._trigger_kind_for("customListener") == "customListener"


# ---------------------------------------------------------------------------
# C1d.3 — _start_canary_listener: deterministic id + USE_EXISTING + search attrs
# ---------------------------------------------------------------------------


class TestStartCanaryListener:
    """The canonical start-workflow shape: deterministic id +
    WorkflowIDConflictPolicy.USE_EXISTING + TypedSearchAttributes."""

    @pytest.mark.asyncio
    async def test_starts_with_deterministic_id_and_use_existing_policy(
        self, monkeypatch
    ):
        from services.deployment.manager import DeploymentManager
        from temporalio.common import WorkflowIDConflictPolicy

        mgr, _ = _build_manager_with_state(
            "wf-abc",
            nodes=[_node("wh-1", "webhookTrigger"), _node("agent-1", "aiAgent")],
            edges=[{"source": "wh-1", "target": "agent-1", "targetHandle": "input-main"}],
        )

        recorded_start: List[Dict[str, Any]] = []

        async def fake_start_workflow(workflow_name, **kwargs):
            recorded_start.append({"name": workflow_name, **kwargs})
            return MagicMock()

        client = MagicMock()
        client.start_workflow = fake_start_workflow

        wrapper = MagicMock()
        wrapper.client = client

        from core import container as container_mod
        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        node = _node("wh-1", "webhookTrigger")
        listener_id = await mgr._start_canary_listener(
            node, "wf-abc", params={"path": "hook"}
        )

        assert listener_id == "trigger-listener-wf-abc-wh-1"
        assert len(recorded_start) == 1
        call = recorded_start[0]
        assert call["name"] == "TriggerListenerWorkflow"
        assert call["id"] == "trigger-listener-wf-abc-wh-1"
        assert call["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING
        assert call["task_queue"] == "machina-tasks"

    @pytest.mark.asyncio
    async def test_search_attributes_include_event_workflow_id(self, monkeypatch):
        """Cancel path queries by EventWorkflowId — start must set it."""
        from services.deployment.manager import DeploymentManager
        from temporalio.common import (
            SearchAttributeKey,
            TypedSearchAttributes,
        )

        mgr, _ = _build_manager_with_state(
            "wf-xyz",
            nodes=[_node("wh-1", "webhookTrigger")],
            edges=[],
        )

        recorded: List[Dict[str, Any]] = []

        async def fake_start_workflow(workflow_name, **kwargs):
            recorded.append(kwargs)
            return MagicMock()

        client = MagicMock()
        client.start_workflow = fake_start_workflow
        wrapper = MagicMock()
        wrapper.client = client

        from core import container as container_mod
        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        await mgr._start_canary_listener(
            _node("wh-1", "webhookTrigger"), "wf-xyz", params={}
        )

        sa = recorded[0]["search_attributes"]
        assert isinstance(sa, TypedSearchAttributes)

        # Extract by key — TypedSearchAttributes is iterable over Pair objects.
        attrs_by_name = {pair.key.name: pair.value for pair in sa}
        assert attrs_by_name["EventWorkflowId"] == "wf-xyz"
        assert attrs_by_name["TriggerNodeId"] == "wh-1"
        # EventTriggerKind derived via _trigger_kind_for (strips "Trigger" /
        # "Receive" suffix) — NOT hardcoded "webhook".
        assert attrs_by_name["EventTriggerKind"] == "webhook"
        # EventType comes from event_waiter.TRIGGER_REGISTRY['webhookTrigger'].
        assert attrs_by_name["EventType"] == "webhook_received"

    @pytest.mark.asyncio
    async def test_chat_trigger_uses_chat_kind_in_search_attrs(self, monkeypatch):
        """C1 rollout #1: starting a chatTrigger listener picks the
        right EventTriggerKind ('chat', not 'webhook')."""
        from services.deployment.manager import DeploymentManager
        from temporalio.common import TypedSearchAttributes

        mgr, _ = _build_manager_with_state(
            "wf-chat",
            nodes=[_node("ct-1", "chatTrigger"), _node("agent-1", "aiAgent")],
            edges=[{"source": "ct-1", "target": "agent-1", "targetHandle": "input-main"}],
        )

        recorded: List[Dict[str, Any]] = []

        async def fake_start_workflow(workflow_name, **kwargs):
            recorded.append(kwargs)
            return MagicMock()

        client = MagicMock()
        client.start_workflow = fake_start_workflow
        wrapper = MagicMock()
        wrapper.client = client

        from core import container as container_mod
        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        listener_id = await mgr._start_canary_listener(
            _node("ct-1", "chatTrigger"), "wf-chat", params={"session_id": "default"}
        )
        assert listener_id == "trigger-listener-wf-chat-ct-1"

        sa = recorded[0]["search_attributes"]
        attrs_by_name = {pair.key.name: pair.value for pair in sa}
        assert attrs_by_name["EventTriggerKind"] == "chat"
        assert attrs_by_name["EventType"] == "chat_message_received"

    @pytest.mark.asyncio
    async def test_returns_none_when_temporal_not_connected(self, monkeypatch):
        """Falls through to legacy path; doesn't raise."""
        from services.deployment.manager import DeploymentManager

        mgr, _ = _build_manager_with_state(
            "wf-1", nodes=[_node("wh-1", "webhookTrigger")], edges=[]
        )

        wrapper = MagicMock()
        wrapper.client = None

        from core import container as container_mod
        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        result = await mgr._start_canary_listener(
            _node("wh-1", "webhookTrigger"), "wf-1", params={}
        )
        assert result is None


# ---------------------------------------------------------------------------
# C1d.4 — _cancel_canary_listeners: Visibility query + handle.cancel()
# ---------------------------------------------------------------------------


class TestCancelCanaryListeners:
    """Cancel uses Visibility query — NO local dict — and graceful cancel()."""

    @pytest.mark.asyncio
    async def test_query_filters_by_workflow_id_and_listener_type(self, monkeypatch):
        from services.deployment.manager import DeploymentManager

        mgr, _ = _build_manager_with_state("wf-1", nodes=[], edges=[])

        recorded_queries: List[str] = []
        cancelled_ids: List[str] = []

        async def fake_list_workflows(query):
            recorded_queries.append(query)
            for wf_id in ["trigger-listener-wf-1-wh-1", "trigger-listener-wf-1-wh-2"]:
                yield MagicMock(id=wf_id)

        handles_by_id: Dict[str, MagicMock] = {}

        def fake_get_handle(wf_id):
            handle = MagicMock()

            async def fake_cancel():
                cancelled_ids.append(wf_id)

            handle.cancel = fake_cancel
            handles_by_id[wf_id] = handle
            return handle

        client = MagicMock()
        client.list_workflows = fake_list_workflows
        client.get_workflow_handle = fake_get_handle

        wrapper = MagicMock()
        wrapper.client = client

        from core import container as container_mod
        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        cancelled = await mgr._cancel_canary_listeners("wf-1")

        # Query shape: EventWorkflowId + WorkflowType + ExecutionStatus.
        assert len(recorded_queries) == 1
        q = recorded_queries[0]
        assert "EventWorkflowId='wf-1'" in q
        assert "WorkflowType='TriggerListenerWorkflow'" in q
        assert "ExecutionStatus='Running'" in q

        assert cancelled == 2
        assert sorted(cancelled_ids) == [
            "trigger-listener-wf-1-wh-1",
            "trigger-listener-wf-1-wh-2",
        ]

    @pytest.mark.asyncio
    async def test_zero_listeners_returns_zero(self, monkeypatch):
        """Visibility query with no results is the steady-state — must not raise."""
        from services.deployment.manager import DeploymentManager

        mgr, _ = _build_manager_with_state("wf-1", nodes=[], edges=[])

        async def empty_list(query):
            if False:
                yield None  # make this an async generator

        client = MagicMock()
        client.list_workflows = empty_list
        wrapper = MagicMock()
        wrapper.client = client

        from core import container as container_mod
        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        cancelled = await mgr._cancel_canary_listeners("wf-1")
        assert cancelled == 0

    @pytest.mark.asyncio
    async def test_per_listener_failure_doesnt_block_sweep(self, monkeypatch):
        """One listener failing to cancel shouldn't strand the others —
        each handle.cancel() is wrapped in its own try/except."""
        from services.deployment.manager import DeploymentManager

        mgr, _ = _build_manager_with_state("wf-1", nodes=[], edges=[])

        cancelled_ids: List[str] = []

        async def list_two(query):
            for wf_id in ["trigger-listener-wf-1-wh-1", "trigger-listener-wf-1-wh-2"]:
                yield MagicMock(id=wf_id)

        def get_handle(wf_id):
            handle = MagicMock()
            if wf_id == "trigger-listener-wf-1-wh-1":
                async def boom():
                    raise RuntimeError("simulated handle.cancel failure")
                handle.cancel = boom
            else:
                async def ok():
                    cancelled_ids.append(wf_id)
                handle.cancel = ok
            return handle

        client = MagicMock()
        client.list_workflows = list_two
        client.get_workflow_handle = get_handle
        wrapper = MagicMock()
        wrapper.client = client

        from core import container as container_mod
        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        cancelled = await mgr._cancel_canary_listeners("wf-1")
        # Only the second one succeeded.
        assert cancelled == 1
        assert cancelled_ids == ["trigger-listener-wf-1-wh-2"]

    @pytest.mark.asyncio
    async def test_returns_zero_when_temporal_disconnected(self, monkeypatch):
        from services.deployment.manager import DeploymentManager

        mgr, _ = _build_manager_with_state("wf-1", nodes=[], edges=[])

        wrapper = MagicMock()
        wrapper.client = None

        from core import container as container_mod
        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        cancelled = await mgr._cancel_canary_listeners("wf-1")
        assert cancelled == 0


# ---------------------------------------------------------------------------
# C1d.5 — invariant: DeploymentManager has NO instance state for canary listeners
# ---------------------------------------------------------------------------


class TestNoInstanceStateForCanaryListeners:
    """Lock the architectural decision: DeploymentManager does NOT keep
    a Python dict of canary listener handles or ids. Temporal's Visibility
    store IS the registry. Adding such state would defeat the entire
    durability guarantee (the dict dies on FastAPI restart, same gap the
    canary is meant to close)."""

    def test_no_canary_listeners_attribute(self):
        from services.deployment.manager import DeploymentManager

        database = MagicMock()
        broadcaster = MagicMock()

        mgr = DeploymentManager(
            database=database,
            execute_workflow_fn=AsyncMock(),
            store_output_fn=AsyncMock(),
            broadcaster=broadcaster,
        )

        forbidden_attrs = {
            "_canary_listeners",
            "_listener_handles",
            "_temporal_handles",
            "_active_listener_workflows",
        }
        present = {a for a in forbidden_attrs if hasattr(mgr, a)}
        assert not present, (
            f"DeploymentManager has tribal listener-tracking state: {present}. "
            "Use Visibility API queries instead — Temporal's server IS the registry. "
            "Otherwise FastAPI restart drops the dict and the canary's durability "
            "guarantee evaporates."
        )

    def test_helper_methods_are_static_or_stateless(self):
        """``_listener_workflow_id`` and ``_canary_listener_enabled_for``
        are intentionally @staticmethod — they don't read instance state.
        Helper code that needs to know the listener id reconstructs it
        from (workflow_id, node_id) deterministically.
        """
        from services.deployment.manager import DeploymentManager

        # Both helpers can be called on the class without an instance.
        assert (
            DeploymentManager._listener_workflow_id("wf", "node")
            == "trigger-listener-wf-node"
        )
