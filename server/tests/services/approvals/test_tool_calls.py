"""A tool call that sends, while the workflow asks first: held as a draft
(once, however often the activity retries), shown in the chat it was made
in, never with the identity the model chose; run as usual with no rule, run
and recorded with Ask first off; refused or restricted for tools that
cannot wait; sent after Send, once, under its claim."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.approvals import execution, rules, store, tool_calls
from services.approvals.handlers import handle_decide_approval
from services.node_registry import get_node_class

pytestmark = pytest.mark.asyncio

SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))


def call(node_type="whatsappSend", tool_args=None, node_data=None, **extra):
    args = tool_args if tool_args is not None else {"recipient_type": "phone", "phone": "447700900123", "message": "Running late, there at 10."}
    return {
        "node_id": f"wf:{node_type}:1",
        "node_type": node_type,
        "workflow_id": "wf",
        "execution_id": "wf:execution:1",
        "user_id": "owner",
        "parent_node_id": "wf:aiAgent:1",
        "tool_call_id": "call_1",
        "node_data": {**(node_data or {"message_type": "text"}), **args},
        "tool_args": args,
        **extra,
    }


@pytest.fixture
def nodes_loaded():
    import nodes  # noqa: F401 - the plugin registry

    return get_node_class


async def test_a_workflow_without_a_rule_runs_its_calls(harness, nodes_loaded):
    checked = await tool_calls.check(call(), get_node_class("whatsappSend"))
    assert checked is tool_calls.RUN
    assert await store.list_approvals(harness.database, status="pending") == []


async def test_asking_first_holds_the_call_once(harness, nodes_loaded):
    await rules.set_ask_first(harness.database, "wf", True)
    node_cls = get_node_class("whatsappSend")
    first = await tool_calls.check(call(), node_cls)
    again = await tool_calls.check(call(), node_cls)
    assert first.result["result"]["status"] == "waiting_for_owner"
    assert first.result["approval_id"] == again.result["approval_id"]
    (row,) = await store.list_approvals(harness.database, status="pending")
    assert (row.kind, row.channel, row.recipient, row.draft_text, row.body_field) == (
        "tool_call",
        "WhatsApp",
        "447700900123",
        "Running late, there at 10.",
        "message",
    )
    assert row.tool_call_id == "call_1" and row.agent_node_id == "wf:aiAgent:1" and row.node_type == "whatsappSend"
    assert store.summary(row)["outcome_labels"] == {"sent": "Message sent", "failed": "Message not sent"}
    # Only calls that send are held, and only an agent's.
    assert await tool_calls.check({**call(), "tool_call_id": None}, node_cls) is tool_calls.RUN
    assert await tool_calls.check(call("googleGmail", {"operation": "search", "query": "x"}), get_node_class("googleGmail")) is tool_calls.RUN


async def test_the_model_never_chooses_who_sends(harness, nodes_loaded):
    await rules.set_ask_first(harness.database, "wf", True)
    args = {"operation": "send", "to": "ana@example.com", "subject": "Hi", "body": "Hello", "mailbox": "boss@example.com"}
    await tool_calls.check(call("msMail", args, node_data={"mailbox": "me@example.com"}), get_node_class("msMail"))
    (row,) = await store.list_approvals(harness.database, status="pending")
    assert "mailbox" not in row.args and "mailbox" not in row.node_data
    assert row.original_args["mailbox"] == "boss@example.com"
    assert (row.subject, row.subject_field) == ("Hi", "subject")
    # The plugin words what happened.
    assert store.summary(row)["outcome_labels"] == {"sent": "Email sent", "failed": "Email not sent"}


async def test_a_call_is_judged_on_the_settings_it_runs_with(harness, nodes_loaded):
    # A locked field the model set is put back to the node's own setting
    # before the call is judged, so it cannot talk a call out of a hold.
    args = {"operation": "send", "to": "ana@example.com", "body": "Hello", "mailbox": "boss@example.com"}
    node_cls = get_node_class("msMail")
    saved = {"wf:msMail:1": {"mailbox": "me@example.com"}}
    data = await tool_calls.as_it_runs(call("msMail", args, node_data={"mailbox": "me@example.com"}, parameter_snapshot=saved), node_cls)
    assert data["mailbox"] == "me@example.com"
    # With nothing saved, the model's value is left out and the default applies.
    data = await tool_calls.as_it_runs(call("msMail", args, parameter_snapshot={"wf:msMail:1": {}}), node_cls)
    assert "mailbox" not in data


async def test_a_call_in_the_chat_shows_its_card_there(harness, nodes_loaded, monkeypatch):
    from services.chat import parts

    shown = []

    async def show_approval(database, stream, *, approval_id, tool_call_id):
        shown.append((stream["run_id"], approval_id, tool_call_id))

    monkeypatch.setattr(parts, "show_approval", show_approval)
    await rules.set_ask_first(harness.database, "wf", True)
    stream = {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf"}
    checked = await tool_calls.check(call(chat_stream=stream, chat_run_id="r_1"), get_node_class("whatsappSend"))
    (row,) = await store.list_approvals(harness.database, status="pending")
    assert row.run_id == "r_1" and shown == [("r_1", checked.result["approval_id"], "call_1")]


async def test_with_ask_first_off_a_chat_call_runs_and_is_recorded(harness, nodes_loaded, monkeypatch):
    from services.chat import parts

    async def show_approval(database, stream, **_):
        return None

    monkeypatch.setattr(parts, "show_approval", show_approval)
    await rules.set_ask_first(harness.database, "wf", False)
    node_cls = get_node_class("whatsappSend")
    assert await tool_calls.check(call(), node_cls) is tool_calls.RUN
    stream = {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf"}
    checked = await tool_calls.check(call(chat_stream=stream), node_cls)
    assert checked.result is None and checked.record is not None
    await checked.record({"success": True, "result": {"sent": True}})
    (row,) = await store.list_approvals(harness.database, status="sent")
    assert (row.approved_by, row.outcome) == ("auto", "sent")


async def test_tools_that_cannot_wait_are_refused_or_restricted(harness, nodes_loaded):
    await rules.set_ask_first(harness.database, "wf", True)
    refused = await tool_calls.check(call("stripeAction", {"command": "refunds create --charge ch_1"}), get_node_class("stripeAction"))
    assert refused.result["result"]["status"] == "not_run"
    browsing = call("browser", {"operation": "click", "ref": "e3", "interaction": "full"}, node_data={"interaction": "full"})
    restricted = await tool_calls.check(browsing, get_node_class("browser"))
    assert restricted.result is None
    assert restricted.node_data["interaction"] == "read_only" and "interaction" not in restricted.tool_args
    assert await store.list_approvals(harness.database) == []


async def test_a_held_call_past_its_expiry_ends_on_its_own(harness, nodes_loaded):
    from datetime import timedelta

    from services.approvals import reconcile
    from services.chat import notes

    await rules.set_ask_first(harness.database, "wf", True)
    await tool_calls.check(call(), get_node_class("whatsappSend"))
    (held,) = await store.list_approvals(harness.database, status="pending")
    # A gate's draft past its expiry is its own node's to end.
    gate, _ = await store.get_or_create(
        harness.database,
        idempotency_key="g:due",
        fields={"owner_id": "owner", "workflow_id": "wf", "node_id": "wf:approvalGate:1", "draft_text": "hi", "expires_at": held.expires_at},
    )
    later = store.aware(held.expires_at) + timedelta(hours=1)
    harness.broadcaster.frames.clear()

    assert await reconcile.expire_due(harness.database, now=later) == [held.id]
    assert (await store.get(harness.database, held.id)).status == "expired"
    assert (await store.get(harness.database, gate.id)).status == "pending"
    lifecycle = [frame["data"]["type"] for frame in harness.broadcaster.frames if frame["type"] == "approval_lifecycle"]
    assert lifecycle == ["com.opencompany.approval.expired"]
    (note,) = await notes.claim_notes(harness.database, session_id="wf", run_id="r_next")
    assert note.kind == "update" and "expired unsent" in note.text
    # Ended once.
    assert await reconcile.expire_due(harness.database, now=later) == []


class Ran:
    """Stands in for a plugin's execute_as_tool: records what ran."""

    def __init__(self) -> None:
        self.calls = []

    async def __call__(self, tool_args, node_params, ctx):
        self.calls.append((dict(tool_args), dict(node_params)))
        return {"sent": True}


async def dispatch(node_type, tool_args, params=None, **config):
    from services.handlers.tools import _dispatch_tool

    return await _dispatch_tool(
        node_type,
        tool_args,
        {
            "node_type": node_type,
            "node_id": f"wf:{node_type}:1",
            "workflow_id": "wf",
            "parent_node_id": "wf:claudeCodeAgent:1",
            "parameters": dict(params or {}),
            **config,
        },
    )


async def test_an_agent_outside_agentworkflow_sends_only_when_it_need_not_ask(harness, nodes_loaded, monkeypatch):
    whatsapp, gmail = get_node_class("whatsappSend"), get_node_class("googleGmail")
    ran = Ran()
    monkeypatch.setattr(whatsapp, "execute_as_tool", ran)
    monkeypatch.setattr(gmail, "execute_as_tool", ran)
    args = {"recipient_type": "phone", "phone": "447700900123", "message": "On my way."}

    # No rule (a workflow built in the editor), then Ask first off: it runs.
    assert await dispatch("whatsappSend", args, {"message_type": "text"}) == {"sent": True}
    await rules.set_ask_first(harness.database, "wf", False)
    assert await dispatch("whatsappSend", args, {"message_type": "text"}) == {"sent": True}
    # Ask first on: refused, nothing kept, since nothing could send it later.
    await rules.set_ask_first(harness.database, "wf", True)
    refused = await dispatch("whatsappSend", args, {"message_type": "text"})
    assert refused["status"] == "not_run" and "WhatsApp" in refused["message"]
    assert len(ran.calls) == 2 and await store.list_approvals(harness.database) == []
    # A call that doesn't send, and a call no agent made, run as before.
    assert await dispatch("googleGmail", {"operation": "search", "query": "invoice"}) == {"sent": True}
    assert await dispatch("whatsappSend", args, {"message_type": "text"}, parent_node_id=None) == {"sent": True}


async def test_an_agent_outside_agentworkflow_browses_read_only_while_asking(harness, nodes_loaded, monkeypatch):
    browser = get_node_class("browser")
    ran = Ran()
    monkeypatch.setattr(browser, "execute_as_tool", ran)
    await rules.set_ask_first(harness.database, "wf", True)
    await dispatch("browser", {"operation": "click", "ref": "e3", "interaction": "full"}, {"interaction": "full"})
    [(args, params)] = ran.calls
    assert params["interaction"] == "read_only" and "interaction" not in args


async def test_an_unreadable_rule_refuses_rather_than_sends(harness, nodes_loaded, monkeypatch):
    whatsapp = get_node_class("whatsappSend")
    ran = Ran()
    monkeypatch.setattr(whatsapp, "execute_as_tool", ran)

    async def broken(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(rules, "ask_first", broken)
    refused = await dispatch("whatsappSend", {"recipient_type": "phone", "phone": "447700900123", "message": "Hi"}, {"message_type": "text"})
    assert refused["status"] == "not_run" and ran.calls == []


async def test_send_starts_the_send_and_puts_it_back_when_nothing_can(harness, nodes_loaded, monkeypatch):
    await rules.set_ask_first(harness.database, "wf", True)
    await tool_calls.check(call(), get_node_class("whatsappSend"))
    (row,) = await store.list_approvals(harness.database, status="pending")
    started = []

    async def start_send(approved):
        started.append((approved.id, approved.revision))

    monkeypatch.setattr(execution, "start_send", start_send)
    sent = await handle_decide_approval(
        {"approval_id": row.id, "decision": "send", "decision_key": "k1", "text": "Running late, there at 10:15."}, SOCKET
    )
    assert sent["success"] is True and sent["approval"]["status"] == "approved"
    stored = await store.get(harness.database, row.id)
    assert started == [(row.id, stored.revision)]
    assert stored.args["message"] == "Running late, there at 10:15." == stored.node_data["message"]

    async def unavailable(approved):
        raise RuntimeError("no engine")

    undone = await handle_decide_approval({"approval_id": row.id, "decision": "undo", "decision_key": "k2"}, SOCKET)
    assert undone["approval"]["status"] == "pending"
    monkeypatch.setattr(execution, "start_send", unavailable)
    failed = await handle_decide_approval({"approval_id": row.id, "decision": "send", "decision_key": "k3"}, SOCKET)
    assert failed["error"] == "send_unavailable" and failed["approval"]["status"] == "pending"


async def test_a_send_runs_once_under_its_claim(harness, nodes_loaded, monkeypatch):
    await rules.set_ask_first(harness.database, "wf", True)
    stream = {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf"}
    from services.chat import parts

    async def show_approval(database, stream, **_):
        return None

    monkeypatch.setattr(parts, "show_approval", show_approval)
    await tool_calls.check(call(chat_stream=stream), get_node_class("whatsappSend"))
    (row,) = await store.list_approvals(harness.database, status="pending")

    async def start_send(approved):
        return None

    monkeypatch.setattr(execution, "start_send", start_send)
    await handle_decide_approval({"approval_id": row.id, "decision": "send", "decision_key": "k1"}, SOCKET)
    approved = await store.get(harness.database, row.id)

    # A claim for an older revision (an Undo and a new Send came after it) is refused.
    assert (await execution.claim_send(harness.database, row.id, approved.revision - 1, "t-old"))["claimed"] is False
    claim = await execution.claim_send(harness.database, row.id, approved.revision, "t-1")
    assert claim["claimed"] is True and claim["activity"] == f"node.whatsappSend.v{get_node_class('whatsappSend').version}"
    assert claim["context"]["approval_execution"] == {"approval_id": row.id, "claim_token": "t-1"}
    assert claim["context"]["tool_args"]["phone"] == "447700900123"
    # The same claim again (an activity retry) finds itself; another does not.
    assert (await execution.claim_send(harness.database, row.id, approved.revision, "t-1"))["claimed"] is True
    assert (await execution.claim_send(harness.database, row.id, approved.revision, "t-2"))["claimed"] is False

    # The node runs only under that claim.
    node_cls = get_node_class("whatsappSend")
    assert await tool_calls.check(claim["context"], node_cls) is tool_calls.RUN
    other = {**claim["context"], "approval_execution": {"approval_id": row.id, "claim_token": "t-2"}}
    assert (await tool_calls.check(other, node_cls)).result["error_type"] == "ApprovalNotClaimed"

    settled = await execution.record_outcome(harness.database, row.id, "t-1", "sent", None)
    assert settled.status == "sent" and settled.outcome == "sent"
    assert await execution.record_outcome(harness.database, row.id, "t-1", "sent", None) is None
    from services.chat import notes

    (note,) = await notes.claim_notes(harness.database, session_id="wf", run_id="r_next")
    assert note.kind == "update" and '"result": "sent"' in note.text


async def test_a_send_that_broke_off_asks_before_trying_again(harness, nodes_loaded, monkeypatch):
    await rules.set_ask_first(harness.database, "wf", True)
    await tool_calls.check(call(), get_node_class("whatsappSend"))
    (row,) = await store.list_approvals(harness.database, status="pending")

    async def start_send(approved):
        return None

    monkeypatch.setattr(execution, "start_send", start_send)
    await handle_decide_approval({"approval_id": row.id, "decision": "send", "decision_key": "k1"}, SOCKET)
    approved = await store.get(harness.database, row.id)
    await execution.claim_send(harness.database, row.id, approved.revision, "t-1")
    failed = await execution.record_outcome(harness.database, row.id, "t-1", "unknown", "TimeoutError: no answer")
    assert failed.status == "failed" and failed.outcome == "unknown"
    ask = await handle_decide_approval({"approval_id": row.id, "decision": "retry", "decision_key": "k2"}, SOCKET)
    assert ask["error"] == "confirm_required"
    again = await handle_decide_approval({"approval_id": row.id, "decision": "retry", "decision_key": "k3", "confirm": True}, SOCKET)
    assert again["success"] is True and again["approval"]["status"] == "approved" and "outcome" not in again["approval"]


async def test_clearing_the_chat_cancels_its_drafts(harness, nodes_loaded, monkeypatch):
    from services.chat import ledger, parts
    from services.chat_thread import clear_chat_thread

    async def show_approval(database, stream, **_):
        return None

    monkeypatch.setattr(parts, "show_approval", show_approval)
    monkeypatch.setattr(ledger, "session_run_ids", lambda database, session_id: _async(["r_1"]))
    await rules.set_ask_first(harness.database, "wf", True)
    await tool_calls.check(call(chat_stream={"run_id": "r_1", "session_id": "wf"}, chat_run_id="r_1"), get_node_class("whatsappSend"))
    await tool_calls.check({**call(), "tool_call_id": "call_2"}, get_node_class("whatsappSend"))
    await clear_chat_thread(harness.database, "wf")
    statuses = sorted((row.tool_call_id, row.status) for row in await store.list_approvals(harness.database))
    assert statuses == [("call_1", "cancelled"), ("call_2", "pending")]


async def test_clearing_the_chat_ends_its_failed_sends_too(harness, nodes_loaded, monkeypatch):
    # Kept, a failure's card stayed in the emptied chat, and its Try again
    # would send from a conversation that is gone.
    from services.chat import ledger, parts
    from services.chat_thread import clear_chat_thread

    async def show_approval(database, stream, **_):
        return None

    async def start_send(approved):
        return None

    monkeypatch.setattr(parts, "show_approval", show_approval)
    monkeypatch.setattr(execution, "start_send", start_send)
    monkeypatch.setattr(ledger, "session_run_ids", lambda database, session_id: _async(["r_1"]))
    await rules.set_ask_first(harness.database, "wf", True)
    await tool_calls.check(call(chat_stream={"run_id": "r_1", "session_id": "wf"}, chat_run_id="r_1"), get_node_class("whatsappSend"))
    (row,) = await store.list_approvals(harness.database, status="pending")
    await handle_decide_approval({"approval_id": row.id, "decision": "send", "decision_key": "k1"}, SOCKET)
    approved = await store.get(harness.database, row.id)
    await execution.claim_send(harness.database, row.id, approved.revision, "t-1")
    failed = await execution.record_outcome(harness.database, row.id, "t-1", "not_sent", "Bad request")
    assert failed.status == "failed"
    # Leaving its branch keeps the failure, which shows again on the way back.
    assert await store.cancel_open(harness.database, workflow_id="wf", run_ids=["r_1"]) == []
    await clear_chat_thread(harness.database, "wf")
    assert (await store.get(harness.database, row.id)).status == "cancelled"


async def _async(value):
    return value


async def test_a_call_made_after_a_button_press_is_linked_to_its_form(harness, nodes_loaded, monkeypatch):
    from services.chat import ledger, parts

    async def show_approval(database, stream, **_):
        return None

    monkeypatch.setattr(parts, "show_approval", show_approval)
    await rules.set_ask_first(harness.database, "wf", True)
    press = {"part_id": "ui_quote", "element_id": "send", "action": "sendQuote", "params": {}}
    pressed = await ledger.admit_message(
        harness.database, session_id="wf", workflow_id="wf", execution_id="gen-1", text="Send the quote",
        track=True, kind="action", message_kind="action", meta={"ui_event": press},
    )
    stream = {"run_id": pressed.run.run_id, "session_id": "wf", "workflow_id": "wf"}
    await tool_calls.check(call(chat_stream=stream, chat_run_id=pressed.run.run_id), get_node_class("whatsappSend"))
    (row,) = await store.list_approvals(harness.database, status="pending")
    assert row.ui_part_id == "ui_quote"
    # A call in a run the owner's own words started has no form to link.
    assert await ledger.ui_part_of_run(harness.database, "r_missing") is None
