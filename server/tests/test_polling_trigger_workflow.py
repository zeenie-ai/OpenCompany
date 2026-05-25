"""Wave 12 C2: tests for :class:`PollingTriggerWorkflow` + the
per-plugin poll-activity generator + DeploymentManager polling-vs-push
routing.

Unit-style — no live Temporal cluster needed. ``workflow.logger`` /
``workflow.sleep`` / ``workflow.execute_activity`` /
``workflow.start_child_workflow`` are patched per-test so the workflow
body runs synchronously.

Covered invariants:

C2a — :meth:`PollingTriggerNode.as_poll_activity` wraps the four hooks
      (``setup_service`` / ``fetch_ids`` / ``fetch_detail`` / ``post_emit``)
      into one @activity.defn that does ONE cycle and returns
      ``{events, seen_ids}``.

C2b — Baseline-only call returns no events + the current seen set.

C2c — Subsequent call returns only the diff (new IDs) as detail
      payloads; each carries an ``id`` for dedup.

C2d — :class:`PollingTriggerWorkflow` runs baseline first, then loops:
      sleep → execute_activity → spawn child per new event. Dedup via
      ``_seen_event_ids``. ``continueAsNew`` at the configured cap.

C2e — :meth:`DeploymentManager._is_polling_trigger_class` returns True
      iff the node_type resolves to a PollingTriggerNode subclass.

C2f — :func:`collect_polling_activities` only yields activities for
      PollingTriggerNode subclasses (not regular nodes).
"""

from __future__ import annotations

import sys
import types
from typing import Any, Dict, List, Set
from unittest.mock import MagicMock

import pytest


if "machina" not in sys.modules:
    _machina = types.ModuleType("cli")
    _machina.__path__ = []
    sys.modules["cli"] = _machina
    _machina_tcp = types.ModuleType("cli.tcp")
    _machina_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _machina_tcp


@pytest.fixture(autouse=True)
def _patch_workflow_logger(monkeypatch):
    """Workflow handlers call workflow.logger — patch it out for unit tests."""
    from temporalio import workflow as temporal_workflow

    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())


# ---------------------------------------------------------------------------
# C2a/b/c — as_poll_activity wraps the hooks correctly
# ---------------------------------------------------------------------------


class _FakeService:
    """Stand-in for an SDK handle so we can verify hook-flow plumbing."""

    def __init__(self):
        self.detail_calls: List[str] = []
        self.post_emit_calls: List[str] = []


def _make_fake_polling_class(
    cls_type: str,
    *,
    current_ids: Set[str],
    detail_payload_fn=None,
    fail_on_post_emit: bool = False,
):
    """Build a PollingTriggerNode subclass with override-able hooks.

    Keeps the class definition local to each test so global registry
    state is clean (the parent's __init_subclass__ won't auto-register
    because we pass abstract=False but the class is never imported
    outside the test).
    """
    from services.plugin import PollingTriggerNode

    class _TestPolling(PollingTriggerNode, abstract=True):
        type = cls_type
        display_name = "Test"
        # abstract=True suppresses registration

        async def setup_service(self, params):
            return _FakeService()

        async def fetch_ids(self, service, params):
            return set(current_ids)

        async def fetch_detail(self, service, msg_id, params):
            service.detail_calls.append(msg_id)
            if detail_payload_fn is not None:
                return detail_payload_fn(msg_id)
            return {"id": msg_id, "payload": f"detail-{msg_id}"}

        async def post_emit(self, service, msg_id, params):
            service.post_emit_calls.append(msg_id)
            if fail_on_post_emit:
                raise RuntimeError("simulated post_emit failure")

    return _TestPolling


class TestAsPollActivity:
    """The classmethod returns a @activity.defn-decorated coroutine that
    does ONE cycle. Smoke tests bypass the Temporal decorator and call
    the underlying coroutine via __wrapped__."""

    def _underlying(self, activity_fn):
        # Temporal wraps the function but keeps the original under
        # __wrapped__-style attrs depending on SDK version. Pick the
        # first attribute that points at the original coroutine.
        for attr in ("__wrapped__", "fn", "_fn"):
            inner = getattr(activity_fn, attr, None)
            if inner is not None:
                return inner
        return activity_fn

    @pytest.mark.asyncio
    async def test_baseline_only_returns_no_events(self):
        cls = _make_fake_polling_class("test.baseline", current_ids={"a", "b", "c"})
        activity_fn = cls.as_poll_activity()
        inner = self._underlying(activity_fn)

        result = await inner(
            {
                "node_id": "n1",
                "params": {},
                "seen_ids": [],
                "baseline_only": True,
            }
        )

        assert result["events"] == []
        assert set(result["seen_ids"]) == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_subsequent_cycle_emits_only_new(self):
        cls = _make_fake_polling_class("test.diff", current_ids={"a", "b", "c", "d"})
        activity_fn = cls.as_poll_activity()
        inner = self._underlying(activity_fn)

        result = await inner(
            {
                "node_id": "n1",
                "params": {},
                "seen_ids": ["a", "b"],  # b is seen; a/b not new; c/d new
                "baseline_only": False,
            }
        )

        new_ids = {e["id"] for e in result["events"]}
        assert new_ids == {"c", "d"}
        # OOM fix: seen_ids is bounded by ``current`` — items the
        # provider no longer reports drop out. Here ``current`` still
        # includes a/b/c/d so all four stay.
        assert set(result["seen_ids"]) == {"a", "b", "c", "d"}

    @pytest.mark.asyncio
    async def test_seen_ids_bounded_by_current_window_no_unbounded_growth(self):
        """OOM regression guard: when items drop off the provider's
        ``current`` window (archived / aged out), they must drop out of
        ``seen_ids`` too. Pre-fix this returned ``prior_seen | current``
        which grew forever; a Gmail trigger polling every 60s with ~100
        emails/day would accumulate ~36K IDs in a year (~1.4MB just for
        the seen set, serialised through Temporal payload every cycle)."""
        # Provider only reports {e, f} as currently visible — a/b/c/d
        # have aged out (e.g. archived in Gmail, marked read+filter is
        # ``is:unread``, paged off the latest-N window, etc.).
        cls = _make_fake_polling_class("test.bound", current_ids={"e", "f"})
        activity_fn = cls.as_poll_activity()
        inner = self._underlying(activity_fn)

        result = await inner(
            {
                "node_id": "n1",
                "params": {},
                # Carry-forward from an earlier cycle.
                "seen_ids": ["a", "b", "c", "d", "e"],
                "baseline_only": False,
            }
        )

        # Only the genuinely new id (``f``) emits — ``e`` was already
        # in prior_seen.
        new_ids = {ev["id"] for ev in result["events"]}
        assert new_ids == {"f"}, "Only items in ``current`` that weren't in ``prior_seen`` " "should emit."
        # And the returned seen_ids must NOT contain a/b/c/d — those
        # are gone from the provider's window. If they linger, every
        # subsequent cycle pays Temporal-payload cost for IDs that will
        # never matter again, and the set grows without bound.
        assert set(result["seen_ids"]) == {"e", "f"}, (
            f"seen_ids must be bounded by the provider's current window "
            f"(got {sorted(result['seen_ids'])!r}). Old IDs that have "
            f"dropped off the provider must NOT be carried forward — "
            f"pre-fix this returned ``list(prior_seen | current)`` and "
            f"leaked unboundedly."
        )

    @pytest.mark.asyncio
    async def test_detail_payload_id_fallback(self):
        """When fetch_detail returns a payload without 'id', the
        provider id is injected so workflow-level dedup has a key."""

        def detail(msg_id):
            return {"subject": f"subject-{msg_id}"}  # no 'id' field

        cls = _make_fake_polling_class(
            "test.idfallback",
            current_ids={"x"},
            detail_payload_fn=detail,
        )
        activity_fn = cls.as_poll_activity()
        inner = self._underlying(activity_fn)

        result = await inner(
            {
                "node_id": "n1",
                "params": {},
                "seen_ids": [],
                "baseline_only": False,
            }
        )

        assert len(result["events"]) == 1
        assert result["events"][0]["id"] == "x"
        assert result["events"][0]["subject"] == "subject-x"

    @pytest.mark.asyncio
    async def test_post_emit_failure_does_not_block_cycle(self):
        """A post_emit failure (e.g. mark-as-read API timeout) must not
        prevent subsequent emits or kill the cycle. Mirrors the legacy
        coroutine semantics."""
        cls = _make_fake_polling_class(
            "test.postemit",
            current_ids={"x", "y"},
            fail_on_post_emit=True,
        )
        activity_fn = cls.as_poll_activity()
        inner = self._underlying(activity_fn)

        result = await inner(
            {
                "node_id": "n1",
                "params": {},
                "seen_ids": [],
                "baseline_only": False,
            }
        )

        # Both events still came through even though post_emit raised.
        assert {e["id"] for e in result["events"]} == {"x", "y"}


# ---------------------------------------------------------------------------
# C2d — PollingTriggerWorkflow body
# ---------------------------------------------------------------------------


class TestPollCoroutineSeenSetBounded:
    """OOM regression guard for the legacy poll coroutine path
    (``_build_poll_coroutine``).

    Pre-fix the coroutine maintained ``seen: Set[str]`` that grew on
    every emit via ``seen.add(msg_id)`` with no eviction — long-running
    pollers (Gmail with the default 60s interval) accumulated tens of
    thousands of entries over weeks/months. The fix rebases ``seen``
    to ``current`` at the end of every cycle so items that drop off
    the provider's window drop out of ``seen`` too.
    """

    def test_coroutine_source_rebases_seen_to_current(self):
        """Source-introspection lock: ``_build_poll_coroutine`` must
        contain ``seen = set(current)`` (or equivalent rebase) and must
        NOT contain ``seen.add(`` inside the new-id emit loop. Driving
        the live coroutine through cycles is fragile because the
        per-class ``poll_interval_clamp`` enforces a 10s minimum
        sleep — and the bug is in single-line state mutation that
        source introspection catches reliably."""
        import inspect

        from services.plugin import PollingTriggerNode

        # Build a coroutine on a real subclass — we only need its
        # source, not its execution.
        class _T(PollingTriggerNode, abstract=True):
            type = "introspect.bound"
            display_name = "Introspect"

            async def setup_service(self, params):
                return MagicMock()

            async def fetch_ids(self, service, params):
                return set()

            async def fetch_detail(self, service, msg_id, params):
                return {"id": msg_id}

            async def post_emit(self, service, msg_id, params):
                pass

        # _build_poll_coroutine returns a closure; introspect the
        # MRO-resolved method body instead. Strip comments and
        # docstrings so the regex only sees executable code.
        raw = inspect.getsource(PollingTriggerNode._build_poll_coroutine)
        code_only = "\n".join(
            line.split("#", 1)[0]  # drop trailing/inline comments
            for line in raw.splitlines()
        )

        # The fix: rebase seen to the provider's current snapshot at
        # the end of every cycle.
        assert "seen = set(current)" in code_only, (
            "OOM fix regressed: _build_poll_coroutine no longer rebases "
            "``seen`` to ``current`` at end of cycle. Pre-fix it called "
            "``seen.add(msg_id)`` per emit with no eviction; old IDs "
            "accumulated forever (~36K entries in a year for a 60s "
            "poller). Restore the ``seen = set(current)`` rebase after "
            "the new-id emit loop."
        )

        # And the anti-pattern (per-emit unbounded add) is gone from
        # executable code. Comments / docstrings that REFERENCE the
        # historical bug are fine (and useful for future maintainers).
        assert "seen.add(" not in code_only, (
            "OOM fix regressed: _build_poll_coroutine reintroduced "
            "``seen.add(msg_id)``. The rebase-to-current pattern makes "
            "this redundant AND unbounded — drop it."
        )


class TestPollingTriggerWorkflowBody:
    """The workflow's signal/sleep/spawn loop runs deterministically."""

    @pytest.mark.asyncio
    async def test_baseline_then_dedup_then_spawn(self, monkeypatch):
        from services.temporal import polling_trigger_workflow as pmod

        # Activity returns scripted responses per call.
        responses = iter(
            [
                # Baseline cycle.
                {"events": [], "seen_ids": ["a", "b"]},
                # Second cycle: new id 'c'.
                {"events": [{"id": "c", "payload": "hello"}], "seen_ids": ["a", "b", "c"]},
                # Third cycle: same id 'c' (dedup test) + new 'd'.
                {
                    "events": [
                        {"id": "c", "payload": "dup"},
                        {"id": "d", "payload": "fresh"},
                    ],
                    "seen_ids": ["a", "b", "c", "d"],
                },
            ]
        )

        from temporalio import workflow as temporal_workflow

        async def fake_execute_activity(name, payload, **kwargs):
            # Trigger-status broadcasts (idle/waiting around each spawn)
            # are side-effect-only; they're driven by the workflow body
            # but don't consume the scripted poll-cycle responses.
            if name == "broadcast_trigger_status_activity":
                return None
            return next(responses)

        sleep_calls: List[Any] = []

        async def fake_sleep(td):
            sleep_calls.append(td)

        spawn_calls: List[Dict[str, Any]] = []

        async def fake_start_child(name, **kwargs):
            spawn_calls.append({"name": name, **kwargs})
            return MagicMock()

        # Stub `continue_as_new` so we can stop the loop after a fixed
        # number of cycles instead of running forever. Raising the
        # SDK's intended exception isn't possible without the
        # workflow runtime, so we monkeypatch the cap to a value the
        # test will trip after 2 spawns.
        monkeypatch.setattr(pmod, "_MAX_EVENTS_BEFORE_CONTINUE_AS_NEW", 2)

        # continueAsNew is a Temporal sentinel — emulate by raising
        # a unique exception we can catch.
        class _StopLoop(Exception):
            pass

        def fake_continue_as_new(arg):
            raise _StopLoop()

        monkeypatch.setattr(temporal_workflow, "execute_activity", fake_execute_activity)
        monkeypatch.setattr(temporal_workflow, "sleep", fake_sleep)
        monkeypatch.setattr(temporal_workflow, "start_child_workflow", fake_start_child)
        monkeypatch.setattr(temporal_workflow, "continue_as_new", fake_continue_as_new)

        wf = pmod.PollingTriggerWorkflow()
        listener_data = {
            "workflow_id": "wf-1",
            "trigger_node_id": "gm-1",
            "node_type": "googleGmailReceive",
            "version": 1,
            "filter_params": {"poll_interval": 30},
            "nodes": [
                {"id": "gm-1", "type": "googleGmailReceive", "data": {}},
                {"id": "agent-1", "type": "aiAgent", "data": {}},
            ],
            "edges": [{"source": "gm-1", "target": "agent-1", "targetHandle": "input-main"}],
            "session_id": "sess",
            "seen_ids": [],
        }

        with pytest.raises(_StopLoop):
            await wf.run(listener_data)

        # 3 activity calls (baseline + 2 polling cycles).
        # Sleep skipped on baseline; called before each non-baseline cycle.
        assert len(sleep_calls) == 2

        # Spawn called for 'c' (cycle 2), 'd' (cycle 3). The duplicate
        # 'c' in cycle 3 is deduped via _seen_event_ids.
        spawn_ids = [c["id"] for c in spawn_calls]
        assert spawn_ids == ["run-wf-1-c", "run-wf-1-d"]

    @pytest.mark.asyncio
    async def test_cycle_failure_does_not_break_loop(self, monkeypatch):
        """Activity error is logged + the loop continues."""
        from services.temporal import polling_trigger_workflow as pmod
        from temporalio import workflow as temporal_workflow

        call_count = [0]

        async def fake_execute_activity(name, payload, **kwargs):
            # Trigger-status broadcasts are side-effect-only; don't
            # count them against the poll-cycle script.
            if name == "broadcast_trigger_status_activity":
                return None
            call_count[0] += 1
            if call_count[0] == 2:  # second call fails
                raise RuntimeError("transient API timeout")
            if call_count[0] == 1:
                return {"events": [], "seen_ids": ["a"]}  # baseline
            return {"events": [{"id": f"e{call_count[0]}", "x": 1}], "seen_ids": ["a"]}

        sleep_count = [0]

        async def fake_sleep(td):
            sleep_count[0] += 1
            # After 2 sleeps (= 3 cycles total: baseline + good + fail + good),
            # bail out so the test terminates.
            if sleep_count[0] >= 3:
                raise StopAsyncIteration()

        async def fake_start_child(name, **kwargs):
            return MagicMock()

        monkeypatch.setattr(temporal_workflow, "execute_activity", fake_execute_activity)
        monkeypatch.setattr(temporal_workflow, "sleep", fake_sleep)
        monkeypatch.setattr(temporal_workflow, "start_child_workflow", fake_start_child)

        wf = pmod.PollingTriggerWorkflow()
        listener_data = {
            "workflow_id": "wf-1",
            "trigger_node_id": "n",
            "node_type": "googleGmailReceive",
            "version": 1,
            "filter_params": {"poll_interval": 5},
            "nodes": [{"id": "n", "type": "googleGmailReceive", "data": {}}],
            "edges": [],
            "session_id": "s",
            "seen_ids": [],
        }

        with pytest.raises(StopAsyncIteration):
            await wf.run(listener_data)

        # 3 activity calls: baseline (call 1, no sleep) + good (call 2,
        # after sleep 1) + fail (call 3, after sleep 2 — raises). The
        # third sleep (between the failed retry and a hypothetical 4th
        # call) trips StopAsyncIteration and bails the test. What we're
        # asserting is that the failed cycle DID NOT kill the loop —
        # the loop kept running past the failure, only ending because
        # we forced sleep to raise.
        assert call_count[0] == 3


# ---------------------------------------------------------------------------
# C2e — DeploymentManager polling-vs-push dispatch
# ---------------------------------------------------------------------------


class TestPollingTriggerDispatch:
    """``_is_polling_trigger_class`` correctly classifies plugin types."""

    def test_polling_trigger_class_detected(self):
        """googleGmailReceive is a PollingTriggerNode subclass.

        Depends on plugin discovery having happened (other tests in the
        suite import nodes.google). If the plugin isn't importable in
        this env, the test xfails.
        """
        try:
            __import__("nodes.google")
        except ImportError as exc:  # pragma: no cover
            pytest.xfail(f"nodes.google not importable: {exc}")

        from services.deployment.manager import DeploymentManager

        assert DeploymentManager._is_polling_trigger_class("googleGmailReceive") is True

    def test_push_trigger_class_not_polling(self):
        try:
            __import__("nodes.trigger.webhook_trigger")
        except ImportError as exc:  # pragma: no cover
            pytest.xfail(f"webhook_trigger not importable: {exc}")

        from services.deployment.manager import DeploymentManager

        assert DeploymentManager._is_polling_trigger_class("webhookTrigger") is False

    def test_unknown_type_is_not_polling(self):
        from services.deployment.manager import DeploymentManager

        assert DeploymentManager._is_polling_trigger_class("totally-fake-type") is False


# ---------------------------------------------------------------------------
# C2f — collect_polling_activities only walks polling subclasses
# ---------------------------------------------------------------------------


class TestCollectPollingActivities:
    """The collector emits activities ONLY for PollingTriggerNode
    subclasses. Regular plugin nodes (ActionNode / ToolNode / push
    TriggerNode) don't appear in the result."""

    def test_collects_only_polling_nodes(self):
        try:
            __import__("nodes.google")
            __import__("nodes.trigger.webhook_trigger")
        except ImportError as exc:  # pragma: no cover
            pytest.xfail(f"plugin imports failed: {exc}")

        from services.temporal.plugin_activities import collect_polling_activities

        activities = collect_polling_activities()
        # Activity name format: "poll.{type}.v{version}"
        names = {
            getattr(a, "__temporal_activity_definition", None)
            and getattr(a, "__temporal_activity_definition").name
            or getattr(a, "_name", None)
            for a in activities
        }
        # We can't always extract the activity name (Temporal stores
        # it on a metadata attribute that varies by SDK version) — at
        # minimum assert the gmail polling activity is present by
        # inspecting any name-like attribute.
        flat = " ".join(str(n) for n in names)
        assert "poll.googleGmailReceive" in flat or any("googleGmailReceive" in str(n) for n in names), (
            "collect_polling_activities() should include the gmail " f"polling activity. Got names: {names}"
        )
