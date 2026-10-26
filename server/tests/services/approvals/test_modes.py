"""What asks in a v1 generation (RFC-0007 §4.2, §4.3): the workflow's mode
and the call's class decide, for every tool an agent calls. A read never
asks; a call the mode does not ask runs, and leaves a record in the chat
when it reaches out; an asked call is held, on a generic card when its tool
has no spec and as a turn row when its turn waits on it; the Browser is held
only when it navigates, and browses read-only otherwise. A v0 generation
keeps Ask first and the plugins' specs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text

import nodes  # noqa: F401 -- populate plugin registry
from services.approvals import rules, store, tool_calls
from services.node_registry import get_node_class

pytestmark = pytest.mark.asyncio

WHATSAPP = {"recipient_type": "phone", "phone": "447700900123", "message": "On my way."}
WRITE_FILE = {"operation": "write", "path": "notes.md", "content": "Plan"}
SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))


def call(node_type, tool_args, node_data=None, *, v1=True, **extra):
    return {
        "node_id": f"wf:{node_type}:1",
        "node_type": node_type,
        "workflow_id": "wf",
        "execution_id": "wf:execution:1",
        "user_id": "owner",
        "parent_node_id": "wf:aiAgent:1",
        "tool_call_id": f"call_{node_type}",
        "node_data": {**(node_data or {}), **tool_args},
        "tool_args": tool_args,
        **({"approval_wait_version": 1} if v1 else {}),
        **extra,
    }


async def check(node_type, tool_args, node_data=None, **extra):
    return await tool_calls.check(call(node_type, tool_args, node_data, **extra), get_node_class(node_type))


async def test_the_mode_follows_ask_first_until_the_owner_sets_one(harness):
    assert await rules.ask_mode(harness.database, "wf") is None
    await rules.set_ask_first(harness.database, "wf", False)
    assert await rules.ask_mode(harness.database, "wf") == "money_only"
    rule = await rules.set_mode(harness.database, "wf", "everything", expected_revision=0)
    assert (rule.mode, rule.ask_first, rule.revision) == ("everything", True, 1)
    # A v0 generation reads Ask first: off only for money and deletes.
    rule = await rules.set_mode(harness.database, "wf", "money_only")
    assert (rule.mode, rule.ask_first) == ("money_only", False)
    with pytest.raises(rules.RuleConflict):
        await rules.set_mode(harness.database, "wf", "outside", expected_revision=0)
    with pytest.raises(ValueError):
        await rules.set_mode(harness.database, "wf", "never")
    # The Ask first switch makes the mode follow it again.
    rule = await rules.set_ask_first(harness.database, "wf", True)
    assert rule.mode is None and await rules.ask_mode(harness.database, "wf") == "outside"
    # A workflow without a rule gets one.
    assert (await rules.set_mode(harness.database, "other", "outside")).ask_first is True


async def test_an_older_rules_table_gets_the_mode_column(harness):
    async with harness.database.engine.begin() as conn:
        await conn.execute(text("DROP TABLE workflow_rules"))
        await conn.execute(
            text(
                "CREATE TABLE workflow_rules (workflow_id VARCHAR(255) PRIMARY KEY, ask_first BOOLEAN NOT NULL,"
                " revision INTEGER NOT NULL, source VARCHAR(20) NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
        await conn.execute(text("INSERT INTO workflow_rules VALUES ('wf', 0, 3, 'owner', '2026-01-01 10:00:00')"))
    await harness.database._migrate_approvals()
    await harness.database._migrate_approvals()  # runs again at every startup; changes nothing
    assert await rules.ask_mode(harness.database, "wf") == "money_only"
    assert (await rules.set_mode(harness.database, "wf", "everything", expected_revision=3)).mode == "everything"


async def test_a_v0_generation_keeps_ask_first_and_the_specs(harness):
    await rules.set_ask_first(harness.database, "wf", True)
    assert await check("shell", {"command": "ls"}, v1=False) is tool_calls.RUN
    refused = await check("stripeAction", {"command": "customers list"}, v1=False)
    assert refused.result["result"]["status"] == "not_run"


@pytest.mark.parametrize(
    ("mode", "node_type", "args", "held"),
    [
        ("outside", "whatsappSend", WHATSAPP, True),
        ("outside", "googleGmail", {"operation": "search", "query": "invoice"}, False),
        ("outside", "fileModify", WRITE_FILE, False),
        ("outside", "shell", {"command": "ls"}, True),
        ("outside", "stripeAction", {"command": "customers list"}, True),  # held, not refused (D8)
        ("everything", "fileModify", WRITE_FILE, True),
        ("everything", "googleGmail", {"operation": "search", "query": "invoice"}, False),
        ("money_only", "whatsappSend", WHATSAPP, False),
        ("money_only", "stripeAction", {"command": "customers list"}, True),
        ("money_only", "googleCalendar", {"operation": "delete", "event_id": "e1", "send_updates": "none"}, True),
    ],
)
async def test_the_mode_says_which_classes_ask(harness, mode, node_type, args, held):
    await rules.set_mode(harness.database, "wf", mode)
    checked = await check(node_type, args)
    assert (checked.result is not None) is held
    if held:
        assert checked.result["result"]["status"] == "waiting_for_owner"
        assert "allow" in checked.result["result"]["message"]


async def test_a_tool_without_a_spec_is_held_on_a_generic_card(harness):
    await rules.set_mode(harness.database, "wf", "outside")
    args = {"method": "POST", "headers": {"Authorization": "Bearer s", "Accept": "application/json"}, "body": "{}"}
    await check("httpRequest", args, {"url": "https://api.example/orders", "timeout": 30})
    (row,) = await store.list_approvals(harness.database, status="pending")
    summary = store.summary(row)
    assert (row.channel, row.action, row.draft_text, summary["editable"]) == ("HTTP Request", "Use HTTP Request (POST)", "", False)
    assert (summary["generic"], summary["approval_class"], summary["waits"]) == (True, "send", False)
    assert summary["outcome_labels"] == {"sent": "Done", "failed": "Not done"}
    # The call's own arguments first, then what it runs with; no header secret.
    lines = {line["label"]: line["value"] for line in row.details}
    assert list(lines)[:3] == ["method", "headers", "body"] and lines["url"] == "https://api.example/orders"
    assert "Authorization" not in lines["headers"]


async def test_a_call_its_turn_waits_on_is_a_turn_row(harness):
    await rules.set_mode(harness.database, "wf", "outside")
    await check("shell", {"command": "ls"}, approval_wait=True)
    await check("shell", {"command": "pwd"}, tool_call_id="call_2")
    rows = {row.tool_call_id: row for row in await store.list_approvals(harness.database, status="pending")}
    assert rows["call_shell"].runtime == "turn" and store.summary(rows["call_shell"])["waits"] is True
    assert rows["call_2"].runtime == "temporal" and store.summary(rows["call_2"])["waits"] is False


async def test_the_browser_is_held_only_when_it_navigates(harness):
    await rules.set_mode(harness.database, "wf", "outside")
    navigating = await check("browser", {"operation": "navigate", "url": "https://shop.example"})
    assert navigating.result["result"]["status"] == "waiting_for_owner"
    # The card says where it goes.
    (row,) = await store.list_approvals(harness.database, status="pending")
    assert row.details[0] == {"label": "Address", "value": "https://shop.example"}
    clicking = await check(
        "browser", {"operation": "click", "ref": "e3", "interaction": "full"}, {"interaction": "full"}, tool_call_id="c2"
    )
    assert clicking.result is None
    assert clicking.node_data["interaction"] == "read_only" and "interaction" not in clicking.tool_args
    # Judged on the call itself: without an operation it takes a snapshot,
    # whatever the node's settings say.
    assert await check("browser", {}, {"operation": "navigate"}, tool_call_id="c3") is tool_calls.RUN


async def test_an_unreadable_rule_asks_before_everything(harness, monkeypatch):
    async def broken(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(rules, "ask_mode", broken)
    assert (await check("fileModify", WRITE_FILE)).result["result"]["status"] == "waiting_for_owner"


async def test_in_the_chat_a_call_that_reaches_out_is_recorded(harness, monkeypatch):
    from services.chat import parts

    async def show_approval(*_args, **_kwargs):
        return None

    monkeypatch.setattr(parts, "show_approval", show_approval)
    stream = {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf"}
    await rules.set_mode(harness.database, "wf", "money_only")
    sent = await check("whatsappSend", WHATSAPP, chat_stream=stream)
    assert sent.result is None and sent.record is not None
    # A write is the employee's own work, not something it sent.
    await rules.set_mode(harness.database, "wf", "outside")
    assert await check("fileModify", WRITE_FILE, chat_stream=stream) is tool_calls.RUN


async def test_a_graph_node_is_never_held(harness):
    await rules.set_mode(harness.database, "wf", "everything")
    assert await check("whatsappSend", WHATSAPP, parent_node_id=None) is tool_calls.RUN


async def test_the_control_status_says_whether_a_generation_asks_by_mode():
    # The chat shows the mode menu only where modes apply.
    from services.deployment.control import serialize_control

    control = SimpleNamespace(
        workflow_id="wf",
        generation=1,
        execution_id="e",
        root_execution_id="e",
        data_scope_id=None,
        controller_workflow_id=None,
        controller_run_id=None,
        status="running",
        revision=1,
        created_at=None,
        updated_at=None,
        terminal_reason=None,
        resource_manifest={"execution_control_version": 1, "approval_wait_version": 1},
    )
    assert serialize_control(control)["approval_wait_version"] == 1
    control.resource_manifest = {"execution_control_version": 1}
    assert serialize_control(control)["approval_wait_version"] == 0


async def test_the_ask_first_commands_read_and_set_the_mode(harness, monkeypatch):
    from services.approvals.handlers import handle_get_ask_first, handle_set_ask_first

    monkeypatch.setattr(rules, "_LISTENERS", [])
    await harness.database.save_workflow("7", "Maya", "Maya_1", {"nodes": [], "edges": []})
    assert (await handle_get_ask_first({"workflow_id": "7"}, SOCKET))["mode"] is None
    answer = await handle_set_ask_first({"workflow_id": "7", "mode": "everything"}, SOCKET)
    assert answer == {**answer, "success": True, "mode": "everything", "ask_first": True, "revision": 0}
    assert (await handle_get_ask_first({"workflow_id": "7"}, SOCKET))["mode"] == "everything"
    stale = await handle_set_ask_first({"workflow_id": "7", "mode": "outside", "expected_revision": 5}, SOCKET)
    assert stale == {**stale, "success": False, "error": "rule_conflict", "mode": "everything"}
    assert (await handle_set_ask_first({"workflow_id": "7", "mode": "never"}, SOCKET))["error"] == "invalid_request"
