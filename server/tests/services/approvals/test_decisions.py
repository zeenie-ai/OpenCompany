"""The owner's decisions after Send and Discard: Undo brings a draft back
while its window is open and the gate waits through it; Restore brings a
discarded draft back while it can; a repeated decision is a no-op; and the
live Ask first rule lets a gate's draft go without asking, recorded."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.approvals import rules, store
from tests.services.approvals.test_approval_gate import PARAMS, ctx, decide, pending, run_gate, until_waiting

pytestmark = pytest.mark.asyncio


async def test_undo_brings_a_sent_draft_back_and_the_gate_keeps_waiting(harness):
    task = asyncio.ensure_future(run_gate(harness))
    (row,) = await pending(harness)
    sent = await decide(harness, row.id, "send", key="s1")
    assert sent["approval"]["status"] == "approved" and "undo_until" in sent["approval"]
    undone = await decide(harness, row.id, "undo", key="u1")
    assert undone["success"] is True and undone["approval"]["status"] == "pending"
    await asyncio.sleep(0.3)
    assert not task.done(), "the gate let an undone draft through"
    await until_waiting(row.id)
    await decide(harness, row.id, "send", key="s2", text="Saturday at 10, see you.")
    result = (await asyncio.wait_for(task, 5))["result"]
    assert result["approved"] is True and result["text"] == "Saturday at 10, see you."


async def test_undo_is_too_late_once_the_draft_went(harness):
    task = asyncio.ensure_future(run_gate(harness))
    (row,) = await pending(harness)
    await decide(harness, row.id, "send", key="s1")
    result = (await asyncio.wait_for(task, 5))["result"]
    assert result["approved"] is True
    late = await decide(harness, row.id, "undo", key="u1")
    assert late == {**late, "success": False, "error": "too_late"}
    stored = await store.get(harness.database, row.id)
    assert stored.consumed_at is not None and stored.status == "approved"


async def test_restore_brings_a_discarded_draft_back_while_it_can(harness):
    task = asyncio.ensure_future(run_gate(harness))
    (row,) = await pending(harness)
    discarded = await decide(harness, row.id, "discard", key="d1")
    assert discarded["approval"]["status"] == "discarded" and "restore_until" in discarded["approval"]
    restored = await decide(harness, row.id, "restore", key="r1")
    assert restored["approval"]["status"] == "pending"
    assert not task.done()
    await until_waiting(row.id)
    await decide(harness, row.id, "discard", key="d2")
    result = (await asyncio.wait_for(task, 5))["result"]
    assert result["approved"] is False and result["status"] == "discarded"
    late = await decide(harness, row.id, "restore", key="r2")
    assert late == {**late, "success": False, "error": "too_late"}


async def test_a_repeated_decision_key_changes_nothing(harness):
    task = asyncio.ensure_future(run_gate(harness))
    (row,) = await pending(harness)
    await decide(harness, row.id, "send", key="same")
    await decide(harness, row.id, "undo", key="undo")
    # The send's key again: no new send, the draft stays waiting.
    again = await decide(harness, row.id, "send", key="same")
    assert again["idempotent"] is True and again["approval"]["status"] == "pending"
    await decide(harness, row.id, "discard", key="end")
    await asyncio.wait_for(task, 5)


async def test_a_gate_goes_without_asking_when_ask_first_is_off(harness):
    await rules.set_ask_first(harness.database, "7", False)
    result = (await asyncio.wait_for(run_gate(harness), 5))["result"]
    assert result["approved"] is True and result["automatic"] is True and result["text"] == PARAMS["draft"]
    (row,) = await store.list_approvals(harness.database, status="approved")
    assert row.approved_by == "auto" and row.consumed_at is not None
    lifecycle = [frame["data"]["type"] for frame in harness.broadcaster.frames if frame["type"] == "approval_lifecycle"]
    assert lifecycle == ["com.opencompany.approval.decided"]
    # Turned back on: the next draft waits.
    await rules.set_ask_first(harness.database, "7", True)
    task = asyncio.ensure_future(run_gate(harness, context=ctx({"t": {"text": "another"}})))
    (waiting,) = await pending(harness)
    assert waiting.status == "pending"
    await decide(harness, waiting.id, "discard", key="k")
    await asyncio.wait_for(task, 5)


async def test_the_rule_is_a_compare_and_swap_and_seeds_from_a_default(harness, monkeypatch):
    assert await rules.ask_first(harness.database, "nobody") is None
    monkeypatch.setattr(rules, "_SEEDERS", [lambda database, workflow_id: workflow_id == "hired" or None])
    seeded = await rules.get_rule(harness.database, "hired")
    assert (seeded.ask_first, seeded.revision, seeded.source) == (True, 0, "hire")
    changed = await rules.set_ask_first(harness.database, "hired", False, expected_revision=0)
    assert (changed.ask_first, changed.revision, changed.source) == (False, 1, "owner")
    with pytest.raises(rules.RuleConflict):
        await rules.set_ask_first(harness.database, "hired", True, expected_revision=0)


async def test_set_ask_first_answers_what_it_means(harness, monkeypatch):
    from services.approvals.handlers import handle_get_ask_first, handle_set_ask_first

    monkeypatch.setattr(rules, "_LISTENERS", [lambda database, workflow_id, rule: {"replies_gated": True, "needs_apply": not rule.ask_first}])
    socket = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))
    # Only a saved workflow has a rule to read or set.
    assert (await handle_get_ask_first({"workflow_id": "7"}, socket))["error"] == "not_found"
    await harness.database.save_workflow("7", "Maya", "Maya_1", {"nodes": [], "edges": []})
    assert (await handle_get_ask_first({"workflow_id": "7"}, socket))["ask_first"] is None
    answer = await handle_set_ask_first({"workflow_id": "7", "ask_first": False}, socket)
    assert answer == {**answer, "success": True, "ask_first": False, "revision": 0, "replies_gated": True, "needs_apply": True}
    stale = await handle_set_ask_first({"workflow_id": "7", "ask_first": True, "expected_revision": 5}, socket)
    assert stale == {**stale, "success": False, "error": "rule_conflict", "ask_first": False}
    assert (await handle_set_ask_first({"workflow_id": "7", "ask_first": "yes"}, socket))["error"] == "invalid_request"
    internal = SimpleNamespace(scope={"path": "/ws/internal"}, state=SimpleNamespace(user_id="owner"))
    assert (await handle_set_ask_first({"workflow_id": "7", "ask_first": True}, internal))["error"] == "not_found"
