"""An approved send and its employee's state (1b.5): it never goes while
its generation is stopped and starts within one watchdog round of Resume; a
send whose generation ended is cancelled and its employee told; Undo works
until the send is claimed; a manual run's send is not held."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.approvals import execution, handlers, reconcile, rules, store, tool_calls
from services.chat import notes
from services.node_registry import get_node_class

pytestmark = pytest.mark.asyncio

SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))
WHATSAPP = {"recipient_type": "phone", "phone": "447700900123", "message": "Running late, there at 10."}


def later():
    """Five minutes from now, past the start grace. A module constant would go
    stale: the full suite reaches this file long after importing it."""
    return datetime.now(timezone.utc) + timedelta(minutes=5)


@pytest.fixture
def control(harness, monkeypatch):
    """The workflow's latest control row, as the test sets it."""
    import nodes  # noqa: F401 - the plugin registry

    current = SimpleNamespace(generation=1, status="paused")

    async def latest(workflow_id):
        return current

    monkeypatch.setattr(harness.database, "get_latest_workflow_control", latest)
    return current


@pytest.fixture
def starts(monkeypatch):
    started = []

    async def start_send(row):
        started.append(row.id)

    monkeypatch.setattr(execution, "start_send", start_send)
    return started


async def decide(row_id, decision, key):
    return await handlers.handle_decide_approval({"approval_id": row_id, "decision": decision, "decision_key": key}, SOCKET)


async def approved(harness, *, generation=1):
    """A draft held for the owner, then sent."""
    await rules.set_ask_first(harness.database, "wf", True)
    context = {
        "node_id": "wf:whatsappSend:1",
        "workflow_id": "wf",
        "execution_id": "wf:execution:1",
        "user_id": "owner",
        "parent_node_id": "wf:aiAgent:1",
        "tool_call_id": "call_1",
        "node_data": {"message_type": "text", **WHATSAPP},
        "tool_args": WHATSAPP,
        **({"generation": generation} if generation else {}),
    }
    await tool_calls.check(context, get_node_class("whatsappSend"))
    (row,) = await store.list_approvals(harness.database, status="pending")
    answer = await decide(row.id, "send", "s1")
    return answer, await store.get(harness.database, row.id)


async def test_a_send_waits_while_its_employee_is_stopped(harness, control, starts, monkeypatch):
    async def paused(database, workflow_id):
        return "paused"

    monkeypatch.setattr(handlers, "_deployment_state", paused)
    answer, row = await approved(harness)
    assert answer["will_send_on_resume"] is True and starts == [row.id]
    # The send the decision started refuses, and the row keeps waiting.
    assert await execution.claim_send(harness.database, row.id, row.revision, "t-1") == {"claimed": False, "reason": "stopped"}
    assert (await store.get(harness.database, row.id)).status == "approved"
    for state in ("pausing", "paused", "resuming"):
        control.status = state
        assert await reconcile.recover_sends(harness.database, now=later()) == []
    # Once its employee runs, the round starts it, and the claim goes.
    control.status = "running"
    assert await reconcile.recover_sends(harness.database, now=later()) == [row.id]
    assert starts == [row.id, row.id]
    assert (await execution.claim_send(harness.database, row.id, row.revision, "t-2"))["claimed"] is True


@pytest.mark.parametrize(
    "ended",
    [
        SimpleNamespace(generation=1, status="reset"),
        SimpleNamespace(generation=1, status="failed"),
        SimpleNamespace(generation=2, status="running"),
    ],
    ids=["reset", "failed", "newer generation"],
)
async def test_a_send_whose_generation_ended_is_cancelled_and_the_employee_told(harness, control, starts, ended):
    _, row = await approved(harness)
    control.generation, control.status = ended.generation, ended.status
    assert await reconcile.recover_sends(harness.database, now=later()) == [row.id]
    assert (await store.get(harness.database, row.id)).status == "cancelled"
    [note] = await notes.claim_notes(harness.database, session_id="wf", run_id="r_next")
    assert '"result": "cancelled unsent"' in note.text
    # Nothing starts it again.
    assert await reconcile.recover_sends(harness.database, now=later()) == []


async def test_a_claim_for_an_ended_generation_cancels_the_send(harness, control, starts):
    _, row = await approved(harness)
    control.status = "reset"
    assert await execution.claim_send(harness.database, row.id, row.revision, "t-1") == {"claimed": False, "reason": "cancelled"}
    assert (await store.get(harness.database, row.id)).status == "cancelled"


async def test_a_waiting_send_can_be_undone_until_it_is_claimed(harness, control, starts):
    _, row = await approved(harness)
    await asyncio.sleep(0.25)  # past its Undo window
    undone = await decide(row.id, "undo", "u1")
    assert undone["success"] is True and undone["approval"]["status"] == "pending"
    await decide(row.id, "send", "s2")
    control.status = "running"
    resent = await store.get(harness.database, row.id)
    assert (await execution.claim_send(harness.database, row.id, resent.revision, "t-1"))["claimed"] is True
    assert (await decide(row.id, "undo", "u2"))["error"] == "too_late"


async def test_a_manual_runs_send_goes_while_the_deployment_is_paused(harness, control, starts):
    _, row = await approved(harness, generation=0)
    assert (await execution.claim_send(harness.database, row.id, row.revision, "t-1"))["claimed"] is True
