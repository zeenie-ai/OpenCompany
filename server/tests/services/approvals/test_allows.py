"""Always-allow rules (services/approvals/allows.py, RFC-0007 §4.4): a card
its turn waits on offers the rule its call would make, scoped by host,
recipient, operation or the whole tool, and never for money; Allow with
``always`` saves it; in a v1 generation a call a rule matches runs unasked.
The employee's list shows its rules and its money switches; removing a rule
or deleting the employee ends it. An MCP tool whose own Ask first is off
counts as a rule."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import nodes  # noqa: F401 -- populate plugin registry
from services.approvals import allows, execution, handlers, rules, store, tool_calls
from services.node_registry import get_node_class

pytestmark = pytest.mark.asyncio

SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))
WHATSAPP = {"recipient_type": "phone", "phone": "447700900123", "message": "On my way."}
POST = {"method": "POST", "url": "https://api.example/orders", "body": "{}"}


def call(node_type, tool_args, node_data=None, *, call_id="call_1", waits=True, v1=True):
    return {
        "node_id": f"wf:{node_type}:1",
        "workflow_id": "wf",
        "execution_id": "wf:execution:1",
        "user_id": "owner",
        "parent_node_id": "wf:aiAgent:1",
        "tool_call_id": call_id,
        "node_data": {**(node_data or {}), **tool_args},
        "tool_args": tool_args,
        **({"approval_wait_version": 1} if v1 else {}),
        **({"approval_wait": True} if waits else {}),
    }


async def check(node_type, tool_args, node_data=None, **options):
    return await tool_calls.check(call(node_type, tool_args, node_data, **options), get_node_class(node_type))


async def held(harness, node_type, tool_args, node_data=None, **options):
    checked = await check(node_type, tool_args, node_data, **options)
    return await store.get(harness.database, checked.result["approval_id"])


async def decide(row, decision, key, **extra):
    return await handlers.handle_decide_approval({"approval_id": row.id, "decision": decision, "decision_key": key, **extra}, SOCKET)


@pytest.fixture
async def asking(harness, monkeypatch):
    async def start_send(row):
        return None

    monkeypatch.setattr(execution, "start_send", start_send)
    monkeypatch.setattr(rules, "_LISTENERS", [])
    await rules.set_mode(harness.database, "wf", "everything")
    return harness


@pytest.mark.parametrize(
    ("node_type", "args", "node_data", "offer"),
    [
        ("whatsappSend", WHATSAPP, {"message_type": "text"}, ("recipient", "Send a WhatsApp message to 447700900123")),
        ("httpRequest", POST, None, ("host", "HTTP Request: POST requests to api.example")),
        ("browser", {"operation": "navigate", "url": "https://Shop.Example/cart"}, None, ("host", "Web browser on shop.example")),
        (
            "googleCalendar",
            {"operation": "create", "title": "Sync", "send_updates": "none"},
            None,
            ("operation", "Google Calendar: create"),
        ),
        ("shell", {"command": "ls"}, None, ("tool", "Shell")),
        ("stripeAction", {"command": "customers list"}, None, None),  # money: made in the list
    ],
)
async def test_a_card_offers_the_rule_its_call_would_make(asking, node_type, args, node_data, offer):
    row = await held(asking, node_type, args, node_data)
    always = store.summary(row)["always"]
    assert (None if always is None else (always["scope"], always["label"])) == offer


async def test_a_draft_offers_no_rule(asking):
    # Only a row its turn waits on is known to be v1, where rules apply.
    row = await held(asking, "shell", {"command": "ls"}, waits=False)
    assert store.summary(row)["always"] is None


async def test_allow_always_saves_the_rule_and_the_next_call_runs(asking):
    row = await held(asking, "whatsappSend", WHATSAPP, {"message_type": "text"})
    answer = await decide(row, "send", "s1", always=True)
    assert answer["success"] and answer["approval"]["status"] == "approved"
    (rule,) = await allows.list_for_workflow(asking.database, "wf")
    assert (rule.node_type, rule.scope_kind, rule.scope_value) == ("whatsappSend", "recipient", "447700900123")
    # The same recipient again runs; anyone else still asks.
    assert await check("whatsappSend", WHATSAPP, {"message_type": "text"}, call_id="call_2") is tool_calls.RUN
    other = {**WHATSAPP, "phone": "447700900999"}
    assert (await check("whatsappSend", other, {"message_type": "text"}, call_id="call_3")).result is not None
    # Rules apply only in a v1 generation.
    assert (await check("whatsappSend", WHATSAPP, {"message_type": "text"}, call_id="call_4", v1=False)).result is not None


async def test_always_is_refused_on_money(asking):
    row = await held(asking, "stripeAction", {"command": "refunds create --charge ch_1"})
    assert (await decide(row, "send", "s1", always=True))["error"] == "invalid_request"
    assert await allows.list_for_workflow(asking.database, "wf") == []


async def test_a_host_rule_keeps_to_its_method(asking):
    row = await held(asking, "httpRequest", {**POST, "method": "GET"})
    await decide(row, "send", "s1", always=True)
    assert await check("httpRequest", {**POST, "method": "GET", "url": "https://api.example/refunds"}, call_id="c2") is tool_calls.RUN
    assert (await check("httpRequest", POST, call_id="c3")).result is not None
    assert (await check("httpRequest", {**POST, "method": "DELETE"}, call_id="c4")).result is not None


async def test_an_mcp_tool_whose_ask_first_is_off_runs(asking):
    tool = {"mcp_connector": "docs", "mcp_tool": "search", "mcp_ask": False}
    assert await check("mcpConnector", {"query": "pricing"}, tool) is tool_calls.RUN
    assert (await check("mcpConnector", {"query": "pricing"}, {**tool, "mcp_ask": True}, call_id="c2")).result is not None


async def test_the_list_shows_the_rules_and_the_money_switches(asking):
    from services.approvals.handlers import handle_add_approval_allow, handle_list_approval_allows, handle_remove_approval_allow

    graph = {
        "nodes": [
            {"id": "wf:stripeAction:1", "type": "stripeAction"},
            {"id": "wf:googleCalendar:1", "type": "googleCalendar"},
            {"id": "wf:shell:1", "type": "shell"},
        ],
        "edges": [],
    }
    await asking.database.save_workflow("wf", "Maya", "Maya_1", graph)
    listed = await handle_list_approval_allows({"workflow_id": "wf"}, SOCKET)
    assert listed["allows"] == []
    money = {(item["node_type"], item["operation"]): item for item in listed["money"]}
    assert set(money) == {("stripeAction", ""), ("googleCalendar", "delete")}
    assert money[("stripeAction", "")]["allow_id"] is None

    added = await handle_add_approval_allow({"workflow_id": "wf", "node_type": "stripeAction"}, SOCKET)
    assert added["success"] and added["allow"]["label"] == "Stripe"
    assert await check("stripeAction", {"command": "customers list"}) is tool_calls.RUN
    # Only the employee's money switches are added this way.
    refused = await handle_add_approval_allow({"workflow_id": "wf", "node_type": "shell"}, SOCKET)
    assert refused["error"] == "invalid_request"

    listed = await handle_list_approval_allows({"workflow_id": "wf"}, SOCKET)
    (rule,) = listed["allows"]
    assert {item["node_type"]: item["allow_id"] for item in listed["money"]}["stripeAction"] == rule["id"]
    assert (await handle_remove_approval_allow({"workflow_id": "wf", "allow_id": rule["id"]}, SOCKET))["success"] is True
    assert (await check("stripeAction", {"command": "customers list"}, call_id="c2")).result is not None


async def test_deleting_the_employee_deletes_its_rules(asking):
    row = await held(asking, "shell", {"command": "ls"})
    await decide(row, "send", "s1", always=True)
    assert len(await allows.list_for_workflow(asking.database, "wf")) == 1
    await handlers.on_workflow_deleted(asking.database, "wf")
    assert await allows.list_for_workflow(asking.database, "wf") == []
