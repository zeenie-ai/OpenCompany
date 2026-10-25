"""A sending tool call through the node's own Temporal activity
(``BaseNode.as_activity``): held, it never reaches the node and the model
reads that it waits; with Ask first off it runs and is recorded; a send
the owner approved runs under its claim; a later attempt (the one before
broke off) runs only a call that is safe to repeat, and anything else
leaves the owner a card: a send's (with what a graph node sent, no
secrets) or an ``effect`` card to dismiss."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.testing import ActivityEnvironment

import nodes  # noqa: F401 -- populate plugin registry
from services.approvals import rules, store
from services.node_registry import get_node_class

pytestmark = pytest.mark.asyncio

ARGS = {"recipient_type": "phone", "phone": "447700900123", "message": "On my way."}


def context(**extra):
    return {
        "node_id": "wf:whatsappSend:1",
        "workflow_id": "wf",
        "execution_id": "wf:execution:1",
        "user_id": "owner",
        "parent_node_id": "wf:aiAgent:1",
        "tool_call_id": "call_1",
        "node_data": {"message_type": "text", **ARGS},
        "tool_args": dict(ARGS),
        **extra,
    }


@pytest.fixture
def service(monkeypatch, harness):
    import core.container as container_module

    workflow_service = MagicMock()
    workflow_service.execute_node = AsyncMock(return_value={"success": True, "result": {"message_id": "m1"}})
    monkeypatch.setattr(
        container_module,
        "container",
        SimpleNamespace(database=lambda: harness.database, workflow_service=lambda: workflow_service),
    )
    harness.broadcaster.update_node_output = AsyncMock()
    return workflow_service


async def test_a_held_call_never_reaches_the_node(harness, service):
    await rules.set_ask_first(harness.database, "wf", True)
    activity_fn = get_node_class("whatsappSend").as_activity()
    result = await ActivityEnvironment().run(activity_fn, context())
    service.execute_node.assert_not_awaited()
    assert result["success"] is True and result["result"]["status"] == "waiting_for_owner"
    (row,) = await store.list_approvals(harness.database, status="pending")
    assert result["approval_id"] == row.id
    assert ("wf:whatsappSend:1", "success") in [(node_id, status) for node_id, status, *_ in harness.broadcaster.statuses]


async def test_with_ask_first_off_the_call_runs(harness, service):
    await rules.set_ask_first(harness.database, "wf", False)
    activity_fn = get_node_class("whatsappSend").as_activity()
    result = await ActivityEnvironment().run(activity_fn, context())
    service.execute_node.assert_awaited_once()
    assert result["success"] is True and result["result"] == {"message_id": "m1"}
    # Outside a chat nothing is recorded.
    assert await store.list_approvals(harness.database) == []


async def test_an_approved_send_runs_only_under_its_claim(harness, service):
    await rules.set_ask_first(harness.database, "wf", True)
    activity_fn = get_node_class("whatsappSend").as_activity()
    await ActivityEnvironment().run(activity_fn, context())
    (row,) = await store.list_approvals(harness.database, status="pending")
    approved = await store.transition(harness.database, row.id, expected_revision=row.revision, from_statuses=("pending",), status="approved")
    sending = await store.transition(
        harness.database, row.id, expected_revision=approved.revision, from_statuses=("approved",), status="sending", values={"claim_token": "t1"}
    )
    from services.approvals.execution import node_context

    send = node_context(sending, "t1")
    result = await ActivityEnvironment().run(activity_fn, send)
    service.execute_node.assert_awaited_once()
    assert service.execute_node.await_args.kwargs["extras"]["tool_args"] == {"recipient_type": "phone", "phone": "447700900123", "message": "On my way."}
    assert result["success"] is True
    stale = await ActivityEnvironment().run(activity_fn, node_context(sending, "someone-else"))
    assert stale["success"] is False and stale["error_type"] == "ApprovalNotClaimed"
    service.execute_node.assert_awaited_once()


def attempt(number: int) -> ActivityEnvironment:
    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, attempt=number)
    return env


async def test_a_later_attempt_does_not_send_again(harness, service):
    await rules.set_ask_first(harness.database, "wf", False)
    result = await attempt(2).run(get_node_class("whatsappSend").as_activity(), context())
    service.execute_node.assert_not_awaited()
    assert result["success"] is False and result["error_type"] == "SendOutcomeUnknown"
    assert "may already have gone out" in result["error"]
    # The model reads it; nothing needs the owner to resume a run.
    assert "requires_user_action" not in result
    assert ("wf:whatsappSend:1", "error") in [(node_id, status) for node_id, status, *_ in harness.broadcaster.statuses]
    # The owner gets a card: it may have gone out, with Send again.
    (row,) = await store.list_approvals(harness.database, status="failed")
    assert (row.kind, row.approved_by, row.outcome, row.outcome_error) == ("tool_call", "auto", "unknown", None)
    assert row.idempotency_key == "c:wf:wf:execution:1:call_1"


async def test_a_later_attempt_in_the_chat_is_recorded_as_unknown(harness, service, monkeypatch):
    from services.chat import parts

    async def show_approval(database, stream, **_):
        return None

    monkeypatch.setattr(parts, "show_approval", show_approval)
    await rules.set_ask_first(harness.database, "wf", False)
    stream = {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf"}
    await attempt(3).run(get_node_class("whatsappSend").as_activity(), context(chat_stream=stream))
    service.execute_node.assert_not_awaited()
    (row,) = await store.list_approvals(harness.database, status="failed")
    assert (row.approved_by, row.outcome) == ("auto", "unknown")


async def test_a_later_attempt_still_holds_or_runs_what_does_not_send(harness, service):
    # A draft is made once per call, so holding it again is safe.
    await rules.set_ask_first(harness.database, "wf", True)
    held = await attempt(2).run(get_node_class("whatsappSend").as_activity(), context())
    assert held["result"]["status"] == "waiting_for_owner"
    # A call that does not send (a search) retries as before.
    search = {"operation": "search", "query": "invoice"}
    reading = context(node_id="wf:googleGmail:1", node_data=dict(search), tool_args=dict(search))
    result = await attempt(2).run(get_node_class("googleGmail").as_activity(), reading)
    service.execute_node.assert_awaited_once()
    assert result["success"] is True


async def test_a_later_browser_page_action_is_not_repeated(harness, service):
    # A click that broke off may have happened: whatever the rule, it is not
    # clicked again, and the owner gets one card to check and dismiss.
    browsing = {"operation": "click", "ref": "e3"}
    call = context(node_id="wf:browser:1", node_data=dict(browsing), tool_args=dict(browsing))
    for rule in (None, False, True):
        if rule is not None:
            await rules.set_ask_first(harness.database, "wf", rule)
        result = await attempt(2).run(get_node_class("browser").as_activity(), call)
        assert result["success"] is False and result["error_type"] == "OutcomeUnknown", rule
        assert "may already have happened" in result["error"]
    service.execute_node.assert_not_awaited()
    (row,) = await store.list_approvals(harness.database, status="failed")
    assert (row.kind, row.outcome, row.tool_call_id) == ("effect", "unknown", "call_1")
    assert row.idempotency_key == "e:wf:wf:execution:1:call_1"
    assert row.action.endswith("(click)")


async def test_a_later_browser_request_for_help_waits_again(harness, service):
    # Asking the owner for help is a wait the worker cut short: it repeats.
    asking = {"operation": "request_user", "message": "Please sign in."}
    call = context(node_id="wf:browser:1", node_data=dict(asking), tool_args=dict(asking))
    result = await attempt(2).run(get_node_class("browser").as_activity(), call)
    service.execute_node.assert_awaited_once()
    assert result["success"] is True


async def test_a_later_shell_command_leaves_a_card_to_dismiss(harness, service):
    command = {"command": "git push origin main"}
    call = context(node_id="wf:shell:1", node_data=dict(command), tool_args=dict(command))
    result = await attempt(2).run(get_node_class("shell").as_activity(), call)
    service.execute_node.assert_not_awaited()
    assert result["success"] is False and result["error_type"] == "OutcomeUnknown"
    (row,) = await store.list_approvals(harness.database, status="failed")
    assert (row.kind, row.approved_by, row.outcome) == ("effect", "auto", "unknown")
    assert {"label": "command", "value": "git push origin main"} in row.details


async def test_a_later_get_runs_again(harness, service):
    fetch = {"method": "GET", "url": "https://example.com/status"}
    call = context(node_id="wf:httpRequest:1", node_data=dict(fetch), tool_args=dict(fetch))
    result = await attempt(2).run(get_node_class("httpRequest").as_activity(), call)
    service.execute_node.assert_awaited_once()
    assert result["success"] is True
    assert await store.list_approvals(harness.database) == []


def graph_context(**extra):
    """A graph node's call: no agent made it."""
    call = context(**extra)
    for key in ("parent_node_id", "tool_call_id", "tool_args"):
        call.pop(key)
    return call


async def test_a_later_graph_send_keeps_what_it_sent_and_needs_the_owner(harness, service):
    # What it sent, rebuilt with its templates resolved; a secret it ran with
    # is not kept (the node's settings put it back on Send again).
    sent = {"message_type": "text", **ARGS, "message": "On my way, Ana.", "api_key": "sk-secret"}
    service.prepare_node_parameters = AsyncMock(return_value=sent)
    result = await attempt(2).run(get_node_class("whatsappSend").as_activity(), graph_context())
    service.execute_node.assert_not_awaited()
    assert result["success"] is False and result["error_type"] == "SendOutcomeUnknown"
    # No model reads it: the employee pauses for the owner.
    assert result["requires_user_action"] is True and result["hint"]
    status = next(data for node_id, state, data, _ in harness.broadcaster.statuses if state == "error")
    assert status["requires_user_action"] is True
    (row,) = await store.list_approvals(harness.database, status="failed")
    assert (row.kind, row.agent_node_id, row.tool_call_id, row.args) == ("tool_call", "", "", {})
    assert row.node_data["message"] == "On my way, Ana." and "api_key" not in row.node_data
    assert row.draft_text == "On my way, Ana."
    assert row.idempotency_key.startswith("u:")


async def test_a_graph_card_sends_again_as_it_was(harness):
    from services.approvals.execution import node_context

    graph, _ = await store.get_or_create(
        harness.database,
        idempotency_key="u:run:1:act",
        fields={"owner_id": "owner", "workflow_id": "wf", "node_id": "wf:whatsappSend:1", "kind": "tool_call",
                "node_type": "whatsappSend", "agent_node_id": "", "node_data": {"message": "On my way, Ana."}},
    )
    run = node_context(graph, "t1")
    # Its templates are resolved already: nothing resolves them again, and
    # with no tool arguments a tool node keeps its saved operation.
    assert run["nodes"] is None and run["edges"] is None and "tool_args" not in run
    agent, _ = await store.get_or_create(
        harness.database,
        idempotency_key="c:wf:1:call_1",
        fields={"owner_id": "owner", "workflow_id": "wf", "node_id": "wf:whatsappSend:1", "kind": "tool_call",
                "node_type": "whatsappSend", "agent_node_id": "wf:aiAgent:1", "args": {"message": "Hi"}},
    )
    assert node_context(agent, "t1")["tool_args"] == {"message": "Hi"}


async def test_the_legacy_activity_refuses_a_later_attempt_too(harness):
    from services.temporal.activities import NodeExecutionActivities

    legacy = object.__new__(NodeExecutionActivities)
    legacy._broadcast_status = AsyncMock()
    legacy._execute_via_websocket = AsyncMock(return_value={"success": True})
    result = await attempt(2).run(legacy.execute_node_activity, {**context(), "node_type": "whatsappSend"})
    legacy._execute_via_websocket.assert_not_awaited()
    assert result["success"] is False and result["error_type"] == "SendOutcomeUnknown"
    (row,) = await store.list_approvals(harness.database, status="failed")
    assert row.outcome == "unknown"


async def test_the_first_attempt_sends_as_before(harness, service):
    await rules.set_ask_first(harness.database, "wf", False)
    result = await attempt(1).run(get_node_class("whatsappSend").as_activity(), context())
    service.execute_node.assert_awaited_once()
    assert result["success"] is True
