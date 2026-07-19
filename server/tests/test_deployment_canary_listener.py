"""Wave 12 C1 canary: DeploymentManager Temporal-listener integration tests.

These tests exercise the canary plumbing inside :class:`DeploymentManager`
WITHOUT a live Temporal cluster — the ``container.temporal_client()``
call is monkeypatched to return a stub whose ``client.start_workflow``
and ``client.list_workflows`` calls are captured and asserted.

The canonical pattern under test:

1. **Deterministic listener id** = ``trigger-listener-{workflow_id}-{node_id}``.
   Re-deploy of the same OpenCompany workflow targets the same Temporal id.
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

# Stub the root `cli` namespace — same pattern as other event-framework tests.
if "cli" not in sys.modules:
    _cli_stub = types.ModuleType("cli")
    _cli_stub.__path__ = []
    sys.modules["cli"] = _cli_stub
    _opencompany_tcp = types.ModuleType("cli.tcp")
    _opencompany_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _opencompany_tcp


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

    monkeypatch.setattr(canary_registry, "_REGISTERED", {})
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
        assert first == second == "wf-1-wh-1"

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

        fresh_canary_registry.register_canary_trigger_type("webhookTrigger", "com.opencompany.webhook.received")

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

        fresh_canary_registry.register_canary_trigger_type("webhookTrigger", "com.opencompany.webhook.received")
        fresh_canary_registry.register_canary_trigger_type("chatTrigger", "com.opencompany.chat.message.received")

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
    async def test_starts_with_deterministic_id_and_use_existing_policy(self, monkeypatch):
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
        listener_id = await mgr._start_canary_listener(node, "wf-abc", params={"path": "hook"})

        # Wave 14: listener id = ``<workflow_slug>-<trigger_label>``.
        # Slug falls back to workflow_id ("wf-abc") when no DB row;
        # label falls back to node.type ("webhookTrigger") when no F2 rename.
        assert listener_id == "wf-abc-webhookTrigger"
        assert len(recorded_start) == 1
        call = recorded_start[0]
        assert call["name"] == "TriggerListenerWorkflow"
        assert call["id"] == "wf-abc-webhookTrigger"
        assert call["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING
        assert call["task_queue"] == "machina-tasks"

    @pytest.mark.asyncio
    async def test_controlled_listener_is_registered_under_controller(self, monkeypatch):
        mgr, _ = _build_manager_with_state(
            "wf-controlled", nodes=[_node("wh-1", "webhookTrigger")], edges=[],
        )
        controller_handle = MagicMock()
        controller_handle.signal = AsyncMock()
        client = MagicMock()
        client.get_workflow_handle.return_value = controller_handle
        client.start_workflow = AsyncMock()
        wrapper = MagicMock(client=client)
        control = MagicMock(
            status="running", controller_workflow_id="workflow-control-wf-controlled-g1",
            controller_run_id="controller-run-1",
        )
        database = MagicMock()
        database.get_latest_workflow_control = AsyncMock(return_value=control)

        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)
        monkeypatch.setattr(container_mod.container, "database", lambda: database)

        listener_id = await mgr._start_canary_listener(
            _node("wh-1", "webhookTrigger"), "wf-controlled", params={},
        )

        assert listener_id == "wf-controlled-webhookTrigger"
        client.start_workflow.assert_not_awaited()
        client.get_workflow_handle.assert_called_once_with(
            "workflow-control-wf-controlled-g1", run_id="controller-run-1",
        )
        signal_name, spec = controller_handle.signal.await_args.args
        assert signal_name == "register_trigger"
        assert spec["listener_id"] == listener_id
        assert spec["workflow_type"] == "TriggerListenerWorkflow"
        assert spec["workflow_id"] == "wf-controlled"

    @pytest.mark.asyncio
    async def test_search_attributes_include_event_workflow_id(self, monkeypatch):
        """Cancel path queries by EventWorkflowId — start must set it."""
        from temporalio.common import (
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

        await mgr._start_canary_listener(_node("wh-1", "webhookTrigger"), "wf-xyz", params={})

        sa = recorded[0]["search_attributes"]
        assert isinstance(sa, TypedSearchAttributes)

        # Extract by key — TypedSearchAttributes is iterable over Pair objects.
        attrs_by_name = {pair.key.name: pair.value for pair in sa}
        assert attrs_by_name["EventWorkflowId"] == "wf-xyz"
        assert attrs_by_name["TriggerNodeId"] == "wh-1"
        # EventTriggerKind derived via _trigger_kind_for (strips "Trigger" /
        # "Receive" suffix) — NOT hardcoded "webhook".
        assert attrs_by_name["EventTriggerKind"] == "webhook"
        # EventType MUST be the CloudEvents reverse-DNS string the
        # producer puts on outgoing envelopes (registered via
        # canary_registry). dispatch.emit's Visibility query substitutes
        # event.type into the EventType filter — if the SA carries the
        # legacy snake_case event_waiter string instead, the query
        # never matches and no signal reaches the listener.
        assert attrs_by_name["EventType"] == "com.opencompany.webhook.received"

    @pytest.mark.asyncio
    async def test_chat_trigger_uses_chat_kind_in_search_attrs(self, monkeypatch):
        """C1 rollout #1: starting a chatTrigger listener picks the
        right EventTriggerKind ('chat', not 'webhook')."""

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

        listener_id = await mgr._start_canary_listener(_node("ct-1", "chatTrigger"), "wf-chat", params={"session_id": "default"})
        assert listener_id == "wf-chat-chatTrigger"

        sa = recorded[0]["search_attributes"]
        attrs_by_name = {pair.key.name: pair.value for pair in sa}
        assert attrs_by_name["EventTriggerKind"] == "chat"
        # CloudEvents reverse-DNS — see test_search_attributes_include_event_workflow_id
        # for the full rationale.
        assert attrs_by_name["EventType"] == "com.opencompany.chat.message.received"

    @pytest.mark.asyncio
    async def test_returns_none_when_temporal_not_connected(self, monkeypatch):
        """Falls through to legacy path; doesn't raise."""

        mgr, _ = _build_manager_with_state("wf-1", nodes=[_node("wh-1", "webhookTrigger")], edges=[])

        wrapper = MagicMock()
        wrapper.client = None

        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        result = await mgr._start_canary_listener(_node("wh-1", "webhookTrigger"), "wf-1", params={})
        assert result is None


# ---------------------------------------------------------------------------
# C1d.4 — _cancel_canary_listeners: Visibility query + handle.cancel()
# ---------------------------------------------------------------------------


class TestCancelCanaryListeners:
    """Cancel uses Visibility query — NO local dict — and graceful cancel()."""

    @pytest.mark.asyncio
    async def test_query_filters_by_workflow_id_and_listener_type(self, monkeypatch):
        mgr, _ = _build_manager_with_state("wf-1", nodes=[], edges=[])

        recorded_queries: List[str] = []
        cancelled_ids: List[str] = []

        async def fake_list_workflows(query):
            recorded_queries.append(query)
            for wf_id in ["wf-1-trigger-wh-1", "wf-1-trigger-wh-2"]:
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

        # Query shape: EventWorkflowId + WorkflowType IN (...) +
        # ExecutionStatus. The IN clause covers both push
        # (TriggerListenerWorkflow) and polling (PollingTriggerWorkflow)
        # listener workflow types since Wave 12 C2 — deployment cancel
        # drains both in one sweep.
        assert len(recorded_queries) == 1
        q = recorded_queries[0]
        assert "EventWorkflowId='wf-1'" in q
        assert "'TriggerListenerWorkflow'" in q
        assert "'PollingTriggerWorkflow'" in q
        assert "WorkflowType IN" in q
        assert "ExecutionStatus='Running'" in q

        assert cancelled == 2
        assert sorted(cancelled_ids) == [
            "wf-1-trigger-wh-1",
            "wf-1-trigger-wh-2",
        ]

    @pytest.mark.asyncio
    async def test_zero_listeners_returns_zero(self, monkeypatch):
        """Visibility query with no results is the steady-state — must not raise."""

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

        mgr, _ = _build_manager_with_state("wf-1", nodes=[], edges=[])

        cancelled_ids: List[str] = []

        async def list_two(query):
            for wf_id in ["wf-1-trigger-wh-1", "wf-1-trigger-wh-2"]:
                yield MagicMock(id=wf_id)

        def get_handle(wf_id):
            handle = MagicMock()
            if wf_id == "wf-1-trigger-wh-1":

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
        assert cancelled_ids == ["wf-1-trigger-wh-2"]

    @pytest.mark.asyncio
    async def test_returns_zero_when_temporal_disconnected(self, monkeypatch):
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


class TestCancelSweepsStuckNodeStatuses:
    """Regression: ``DeploymentManager.cancel`` must broadcast a full
    status cleanup so the FE doesn't leave downstream nodes glowing
    forever after the deployment goes down.

    Pre-fix the cancel only reset trigger nodes (cron + listener) to
    "idle". Downstream agents/tools/actions that were mid-execute when
    cancel hit stayed in "executing" status on every connected client
    because no sweep was issued. Also the toolbar Start/Stop indicator
    stuck at ``executing=True`` because no terminal
    ``workflow_run_ended`` / ``update_workflow_status(executing=False)``
    was emitted.
    """

    def test_cancel_source_calls_stuck_node_sweep_with_include_waiting(self):
        """Source-introspection: ``cancel`` must call
        ``_clear_stuck_node_statuses(..., include_waiting=True)`` so
        every node currently broadcast as ``executing`` OR ``waiting``
        for this deployment goes back to idle on the FE. Without
        ``include_waiting=True`` non-firing trigger siblings (and any
        other ``waiting`` indicators outside the cron/listener buckets
        the manager owns directly) stay glowing."""
        import inspect

        from services.deployment.manager import DeploymentManager

        src = inspect.getsource(DeploymentManager.cancel)
        assert "_clear_stuck_node_statuses" in src, (
            "DeploymentManager.cancel no longer sweeps stuck node "
            "statuses. Downstream nodes mid-execute at cancel-time "
            "will stay glowing on FE forever. Restore the "
            "``_broadcaster._clear_stuck_node_statuses(workflow_id, "
            "include_waiting=True)`` call after the trigger resets."
        )
        assert "include_waiting=True" in src, (
            "DeploymentManager.cancel must pass include_waiting=True to "
            "the stuck-node sweep — explicit user-cancel is the "
            "'every indicator goes quiet' signal. Without it, sibling "
            "trigger nodes (or other waiting indicators) outlive the "
            "deployment."
        )

    def test_cancel_source_broadcasts_terminal_workflow_status(self):
        """Source-introspection: ``cancel`` must emit a final
        ``update_workflow_status(executing=False, workflow_id=...)`` so
        the toolbar Start/Stop indicator reflects the cancel. The
        run-counter eviction in ``workflow_run_ended`` can race against
        in-flight child runs that already incremented the counter."""
        import inspect

        from services.deployment.manager import DeploymentManager

        src = inspect.getsource(DeploymentManager.cancel)
        assert "update_workflow_status" in src, (
            "DeploymentManager.cancel must broadcast "
            "``update_workflow_status(executing=False, workflow_id=...)`` "
            "so the FE toolbar Start/Stop indicator goes quiet after a "
            "deployment is cancelled."
        )
        assert "executing=False" in src, (
            "DeploymentManager.cancel must pass executing=False to the "
            "terminal workflow_status broadcast. Anything else leaves "
            "the toolbar showing the deployment as still active."
        )

    @pytest.mark.asyncio
    async def test_cancel_runtime_calls_sweep_and_terminal_broadcast(self):
        """Runtime smoke: stub the broadcaster + state + trigger_manager
        and assert cancel actually invokes the sweep + terminal status
        broadcast with the right args."""
        from services.deployment.manager import DeploymentManager
        from services.deployment.state import DeploymentState

        sweep_calls: List[Dict[str, Any]] = []
        status_calls: List[Dict[str, Any]] = []

        broadcaster = MagicMock()
        broadcaster.update_node_status = AsyncMock()
        broadcaster.update_workflow_status = AsyncMock(
            side_effect=lambda **kw: status_calls.append(kw),
        )
        broadcaster._clear_stuck_node_statuses = AsyncMock(
            side_effect=lambda workflow_id, include_waiting=False: sweep_calls.append(
                {
                    "workflow_id": workflow_id,
                    "include_waiting": include_waiting,
                }
            )
            or 0,
        )

        database = MagicMock()
        mgr = DeploymentManager(
            database=database,
            execute_workflow_fn=AsyncMock(return_value={"success": True}),
            store_output_fn=AsyncMock(),
            broadcaster=broadcaster,
        )
        # Seed deployment state — the cancel path bails early if
        # the workflow isn't in self._deployments.
        mgr._deployments["wf-1"] = DeploymentState(
            deployment_id="deploy_wf-1",
            workflow_id="wf-1",
            is_running=True,
            nodes=[{"id": "n1", "type": "aiAgent"}],
            edges=[],
            session_id="sess",
        )

        # Patch _cancel_canary_listeners / _cancel_canary_cron_schedules
        # to be no-ops so the test focuses on the sweep + terminal
        # broadcast contract.
        mgr._cancel_canary_listeners = AsyncMock(return_value=0)
        mgr._cancel_canary_cron_schedules = AsyncMock(return_value=0)

        result = await mgr.cancel("wf-1")
        assert result["success"] is True

        # Sweep ran once with include_waiting=True (every indicator
        # goes quiet on explicit user cancel).
        assert len(sweep_calls) == 1, f"Expected one stuck-node sweep on cancel, got " f"{len(sweep_calls)}: {sweep_calls!r}"
        assert sweep_calls[0] == {
            "workflow_id": "wf-1",
            "include_waiting": True,
        }

        # Terminal executing=False broadcast for the toolbar.
        assert {"executing": False, "workflow_id": "wf-1"} in status_calls, (
            f"Expected update_workflow_status(executing=False, " f"workflow_id='wf-1') on cancel; got {status_calls!r}"
        )


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
        assert DeploymentManager._listener_workflow_id("wf", "node") == "wf-node"
@pytest.mark.asyncio
async def test_pause_status_disarms_trigger_nodes_without_touching_agents():
    mgr, broadcaster = _build_manager_with_state(
        "wf-pause",
        nodes=[
            _node("start-1", "start"), _node("chat-1", "chatTrigger"),
            _node("agent-1", "aiAgent"),
        ],
        edges=[],
    )

    changed = await mgr.update_trigger_pause_status("wf-pause", paused=True)

    assert changed == 1
    broadcaster.update_node_status.assert_awaited_once_with(
        "chat-1", "idle", {"paused": True, "message": "Paused"}, workflow_id="wf-pause",
    )
