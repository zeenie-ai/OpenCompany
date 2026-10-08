"""Employee summaries: every workflow is an employee, hired or built in the
editor, with status, task text, apps, control and Talk from the same
sources the editor reads."""

from __future__ import annotations

import pytest

from models.database import WorkflowControlExecution
from services.employees import store
from services.employees.graph_index import index_graph
from services.employees.summaries import (
    get_employee_detail,
    get_employee_summary,
    list_employee_summaries,
    schedule_text,
)
from services.graph_build import main_edge
from services.workflow_migrations import normalize_workflow_graph
from services.workflow_sanitizer import sanitize_workflow_graph


class FakeAuth:
    def __init__(self, keys=(), oauth=None):
        self.keys = set(keys)
        self.oauth = dict(oauth or {})

    async def has_valid_key(self, key, *, principal=None):
        return key in self.keys

    async def get_oauth_tokens(self, provider):
        return self.oauth.get(provider)

    async def list_api_key_providers(self, *, principal=None):
        return sorted(self.keys)


@pytest.fixture(autouse=True)
def no_saved_endpoints(monkeypatch):
    """The OpenAI-compatible credential lists saved endpoints through the
    global auth service; give it one with none."""
    import services.plugin.deps as deps

    monkeypatch.setattr(deps, "get_auth_service", lambda: FakeAuth())


def node(node_id, node_type, label=None, **data):
    return {"id": node_id, "type": node_type, "position": {"x": 0, "y": 0}, "data": {"label": label or node_type, **data}}


def graph(*nodes):
    return {"nodes": list(nodes), "edges": []}


async def save(database, workflow_id, name, data):
    assert await database.save_workflow(workflow_id=workflow_id, name=name, slug=f"{name.replace(' ', '_')}_{workflow_id}", data=data)


async def control(database, workflow_id, status, generation=1, **extra):
    async with database.get_session() as session:
        session.add(
            WorkflowControlExecution(
                id=f"{workflow_id}-{generation}",
                workflow_id=workflow_id,
                generation=generation,
                execution_id=f"e{workflow_id}{generation}",
                root_execution_id=f"e{workflow_id}{generation}",
                graph_hash="0" * 64,
                status=status,
                idempotency_key=f"i{workflow_id}{generation}",
                **extra,
            )
        )
        await session.commit()


async def hire(database, workflow_id, **fields):
    row, _ = await store.reserve(database, owner_id="owner", idempotency_key=f"k{workflow_id}", payload_hash="h", fields=fields)
    await store.mark_ready(
        database,
        row.id,
        workflow_id=workflow_id,
        node_roles={"agent": f"{workflow_id}:aiAgent:1", "todos": f"{workflow_id}:writeTodos:1"},
    )


RECEPTIONIST = graph(
    node("1:whatsappReceive:1", "whatsappReceive", "WhatsApp messages"),
    node("1:aiAgent:1", "aiAgent", "Maya"),
    node("1:writeTodos:1", "writeTodos", "Todos"),
    node("1:whatsappSend:1", "whatsappSend", "Reply"),
)


def test_graph_index_reads_agents_triggers_and_apps():
    index = index_graph(RECEPTIONIST)
    assert index.agent_ids == ("1:aiAgent:1",)
    assert index.trigger_ids == ("1:whatsappReceive:1",)
    assert index.todo_ids == ("1:writeTodos:1",)
    assert index.app_ids == ("whatsapp",)
    assert index.primary_trigger_app().id == "whatsapp"
    assert index_graph(None).agent_ids == ()
    disabled = index_graph(graph(node("x", "aiAgent", disabled=True)))
    assert disabled.agent_ids == ()


async def test_summary_discovers_all_browser_capabilities_including_disabled(real_database, monkeypatch):
    from types import SimpleNamespace
    import services.employees.graph_index as graph_index

    # A third-party browser is discovered by capability; an unknown plugin
    # called 'browser' is not trusted just because of its name.
    monkeypatch.setattr(graph_index, "get_node_class", lambda kind: SimpleNamespace(ui_hints={"isBrowserPanel": True}) if kind == "customBrowser" else None)
    await save(real_database, "91", "Research", graph(
        node("first", "customBrowser", "Research browser"),
        node("second", "customBrowser", "   ", disabled=True),
        node("not-browser", "browser"),
        node("first", "customBrowser", "Research browser"),
    ))
    summary = await get_employee_summary(real_database, "91", auth_service=FakeAuth())
    assert summary["browser_nodes"] == [
        {"node_id": "first", "label": "Research browser"},
        {"node_id": "second", "label": "Browser"},
    ]
    [listed] = await list_employee_summaries(real_database, auth_service=FakeAuth())
    assert listed["browser_nodes"] == summary["browser_nodes"]


async def test_a_browser_waiting_for_the_owner_is_the_task(real_database, monkeypatch):
    import nodes  # noqa: F401 - registers the browser plugin (found by its isBrowserPanel hint)
    import services.employees.node_signals as node_signals

    states = {"b1": {"state": "awaiting_user", "request": {"reason": "login", "message": "text from the page", "since": 1.5}}}
    monkeypatch.setattr(node_signals, "node_state", lambda kind, workflow_id, node_id: states.get(node_id))
    await save(real_database, "92", "Shopper", graph(node("a1", "aiAgent", "Sam"), node("b1", "browser", "Browser")))

    summary = await get_employee_summary(real_database, "92", auth_service=FakeAuth())
    assert summary["browser_request"] == {"node_id": "b1", "reason": "login", "since": "1970-01-01T00:00:01.500000+00:00"}
    assert summary["task"] == {"label": "Waiting", "text": "Needs you to sign in to a site in the browser"}
    # Summaries reach every socket: the agent's message stays in the live view.
    assert "text from the page" not in str(summary)

    states.clear()
    summary = await get_employee_summary(real_database, "92", auth_service=FakeAuth())
    assert summary["browser_request"] is None


@pytest.mark.parametrize(
    ("trigger", "text"),
    [
        ({"kind": "schedule", "every": "weekday", "at": "09:00"}, "Every weekday at 09:00"),
        ({"kind": "schedule", "every": "week", "day": "monday", "at": "08:00"}, "Every Monday at 08:00"),
        ({"kind": "schedule", "every": "hour"}, "Every hour"),
        ({"kind": "schedule", "every": "month", "day": "1", "at": "10:00"}, "On day 1 of every month at 10:00"),
        ({"kind": "schedule", "every": "month", "day": "L", "at": "09:00"}, "On the last day of every month at 09:00"),
        ({"kind": "schedule"}, "On a schedule"),
    ],
)
def test_schedule_text(trigger, text):
    assert schedule_text(trigger) == text


async def test_a_hired_employee_waiting_on_an_app(real_database):
    await save(real_database, "1", "Maya", RECEPTIONIST)
    await hire(real_database, "1", role="Receptionist", apps=["whatsapp"], trigger={"kind": "app_event", "app": "whatsapp"})
    [summary] = await list_employee_summaries(real_database, auth_service=FakeAuth(keys={"openai"}))
    assert summary["workflow_id"] == "1"
    assert summary["name"] == "Maya"
    assert summary["role"] == "Receptionist"
    assert summary["derived"] is False
    assert summary["status"] == "ready"
    assert summary["needs_ai"] is False
    assert [a["app_id"] for a in summary["missing_apps"]] == ["whatsapp"]
    assert summary["task"] == {"label": "Next", "text": "Connect WhatsApp to start"}
    assert summary["watch_node_ids"] == ["1:aiAgent:1", "1:writeTodos:1"]
    assert summary["control"]["state"] == "never_started"
    assert summary["control"]["can_start"] is True
    assert summary["hired_at"]
    assert summary["revision"] > 0


async def test_a_hire_says_how_its_own_start_went(real_database):
    from models.employees import EmployeeActivation

    await save(real_database, "1", "Maya", RECEPTIONIST)
    await hire(real_database, "1", role="Receptionist", apps=["whatsapp"], trigger={"kind": "app_event", "app": "whatsapp"})
    await save(real_database, "2", "Support bot", graph(node("2:aiAgent:1", "aiAgent", "Support")))
    async with real_database.get_session() as session:
        session.add(EmployeeActivation(id="hire:k1", workflow_id="1", owner_id="owner", state="blocked", detail="missing_apps"))
        await session.commit()
    auth = FakeAuth(keys={"openai"})
    listed = {summary["workflow_id"]: summary for summary in await list_employee_summaries(real_database, auth_service=auth)}
    assert listed["1"]["activation_state"] == "blocked"
    # A workflow built in the editor was never started by a hire.
    assert listed["2"]["activation_state"] is None
    assert (await get_employee_summary(real_database, "1", auth_service=auth))["activation_state"] == "blocked"


async def test_an_editor_workflow_is_described_from_its_graph(real_database):
    await save(
        real_database,
        "2",
        "Support bot",
        graph(node("2:telegramReceive:1", "telegramReceive"), node("2:aiAgent:1", "aiAgent", "Support")),
    )
    summary = await get_employee_summary(real_database, "2", auth_service=FakeAuth(keys={"telegram", "openai"}))
    assert summary["derived"] is True
    assert summary["role"] == "Telegram assistant"
    assert [a["app_id"] for a in summary["apps"]] == ["telegram"]
    assert summary["apps"][0]["connected"] is True
    assert summary["missing_apps"] == []
    assert summary["task"] == {"label": "Next", "text": "Ready to start"}
    assert summary["watch_node_ids"] == ["2:aiAgent:1"]
    assert summary["hired_at"] is None


async def test_status_follows_the_latest_control_generation(real_database):
    await save(real_database, "3", "Maya", RECEPTIONIST)
    await control(real_database, "3", "reset", generation=1)
    await control(real_database, "3", "running", generation=2)
    summary = await get_employee_summary(real_database, "3", auth_service=FakeAuth(keys={"openai"}))
    assert summary["status"] == "working"
    assert summary["task"] == {"label": "Now", "text": "Waiting for new WhatsApp messages"}
    assert summary["control"]["can_pause"] is True
    assert summary["control"]["generation"] == 2


@pytest.mark.parametrize(
    ("status", "expected", "label"),
    [("paused", "paused", "Paused"), ("failed", "attention", "Paused"), ("starting", "working", "Now")],
)
async def test_status_mapping(real_database, status, expected, label):
    await save(real_database, "4", "Maya", RECEPTIONIST)
    await control(real_database, "4", status)
    summary = await get_employee_summary(real_database, "4", auth_service=FakeAuth(keys={"openai"}))
    assert summary["status"] == expected
    assert summary["task"]["label"] == label


async def test_needs_ai_when_no_model_provider_is_usable(real_database):
    await save(real_database, "5", "Maya", RECEPTIONIST)
    summary = await get_employee_summary(real_database, "5", auth_service=FakeAuth(keys={"whatsapp"}))
    assert summary["needs_ai"] is True
    # Apps are still reported; only then does the AI nudge show.
    assert summary["task"]["text"] == "Connect WhatsApp to start"
    summary = await get_employee_summary(real_database, "5", auth_service=FakeAuth(keys={"ollama"}))
    assert summary["needs_ai"] is False


async def test_detail_adds_the_setup_screen(real_database):
    await save(real_database, "6", "Ravi", graph(node("6:cronScheduler:1", "cronScheduler"), node("6:aiAgent:1", "aiAgent")))
    await hire(
        real_database,
        "6",
        role="Morning briefer",
        job="Send me a news summary every weekday",
        plan=[{"title": "Every weekday at 09:00", "role": "trigger"}],
        rules={"ask_first": True, "items": []},
        trigger={"kind": "schedule", "every": "weekday", "at": "09:00"},
    )
    detail = await get_employee_detail(real_database, "6", auth_service=FakeAuth(keys={"openai"}))
    assert detail["job"] == "Send me a news summary every weekday"
    assert detail["plan"][0]["role"] == "trigger"
    assert detail["rules"]["ask_first"] is True
    assert detail["trigger_text"] == "Every weekday at 09:00"
    assert detail["latest_report"] is None
    assert detail["last_run"] is None


async def test_unknown_workflow(real_database):
    assert await get_employee_summary(real_database, "missing", auth_service=FakeAuth()) is None
    assert await get_employee_detail(real_database, "missing", auth_service=FakeAuth()) is None


async def test_list_is_newest_first_and_includes_every_workflow(real_database):
    await save(real_database, "7", "First", RECEPTIONIST)
    await save(real_database, "8", "Second", graph(node("8:chatTrigger:1", "chatTrigger"), node("8:aiAgent:1", "aiAgent")))
    names = [s["name"] for s in await list_employee_summaries(real_database, auth_service=FakeAuth())]
    assert set(names) == {"First", "Second"}


async def test_deleting_the_workflow_removes_the_employee(real_database, monkeypatch):
    import services.employees as employees_package
    from unittest.mock import AsyncMock
    from services.workflow_storage import deletion
    from services.workflow_storage.handlers import delete_workflow_with_context_archival

    monkeypatch.setattr(deletion, "stop_workflow_for_deletion", AsyncMock(return_value=None))
    frames = []

    async def capture(stage, **kwargs):
        frames.append((stage, kwargs["workflow_id"]))

    monkeypatch.setattr(employees_package._events, "broadcast_employee_event", capture)
    await save(real_database, "9", "Maya", RECEPTIONIST)
    await hire(real_database, "9", role="Receptionist")
    result = await delete_workflow_with_context_archival(real_database, "9")
    assert result["success"] is True
    assert await store.get_by_workflow(real_database, "9") is None
    assert ("removed", "9") in frames


async def _hired(database, workflow_id, node_roles):
    row, _ = await store.reserve(database, owner_id="owner", idempotency_key=f"k{workflow_id}", payload_hash="h", fields={"role": "Receptionist"})
    await store.mark_ready(database, row.id, workflow_id=workflow_id, node_roles=node_roles)


async def test_the_workspace_board_is_the_hires_canvas(real_database):
    await save(
        real_database,
        "5",
        "Maya",
        graph(node("5:aiAgent:1", "aiAgent", "Maya"), node("5:canvas:1", "canvas", "Board"), node("5:canvas:2", "canvas", "Canvas")),
    )
    await _hired(real_database, "5", {"agent": "5:aiAgent:1", "canvas": "5:canvas:2"})
    summary = await get_employee_summary(real_database, "5", auth_service=FakeAuth(keys={"openai"}))
    assert summary["canvas_node_id"] == "5:canvas:2"
    assert "5:canvas:2" not in summary["watch_node_ids"]


async def test_a_removed_canvas_falls_back_to_one_the_owner_added(real_database):
    await save(
        real_database,
        "6",
        "Maya",
        graph(node("6:aiAgent:1", "aiAgent", "Maya"), node("6:canvas:9", "canvas", "Off", disabled=True), node("6:canvas:3", "canvas", "Canvas")),
    )
    # The hire's canvas was deleted in the editor; a disabled one is skipped.
    await _hired(real_database, "6", {"agent": "6:aiAgent:1", "canvas": "6:canvas:1"})
    summary = await get_employee_summary(real_database, "6", auth_service=FakeAuth(keys={"openai"}))
    assert summary["canvas_node_id"] == "6:canvas:3"


async def test_an_editor_workflow_shows_its_canvas_and_none_means_no_board(real_database):
    await save(real_database, "7", "Support bot", graph(node("7:aiAgent:1", "aiAgent", "Support"), node("7:canvas:1", "canvas")))
    await save(real_database, "8", "Plain bot", graph(node("8:aiAgent:1", "aiAgent", "Plain")))
    summaries = {s["workflow_id"]: s for s in await list_employee_summaries(real_database, auth_service=FakeAuth(keys={"openai"}))}
    assert summaries["7"]["canvas_node_id"] == "7:canvas:1"
    assert summaries["8"]["canvas_node_id"] is None


# ----- Talk, asking first, changes waiting for a restart -----


def talking(workflow_id, *extra_nodes, reply=True):
    """Chat -> Sam (-> Reply in Chat), with canonical ids."""
    nodes = [
        node(f"{workflow_id}:chatTrigger:1", "chatTrigger", "Chat"),
        node(f"{workflow_id}:aiAgent:1", "aiAgent", "Sam"),
        *extra_nodes,
    ]
    edges = [main_edge(f"{workflow_id}:chatTrigger:1", f"{workflow_id}:aiAgent:1").to_dict()]
    if reply:
        nodes.append(node(f"{workflow_id}:chatReply:1", "chatReply", "Reply in Chat"))
        edges.append(main_edge(f"{workflow_id}:aiAgent:1", f"{workflow_id}:chatReply:1").to_dict())
    return {"nodes": nodes, "edges": edges}


def snapshot(workflow_id, data):
    """The graph a Start admits (normalized and sanitized)."""
    return sanitize_workflow_graph(normalize_workflow_graph(workflow_id, data["nodes"], data["edges"]).graph_data())


async def test_talk_is_read_off_the_saved_graph_when_nothing_runs(real_database):
    await save(real_database, "20", "Sam", talking("20"))
    await save(real_database, "21", "Quiet", talking("21", reply=False))
    await save(real_database, "22", "Maya", RECEPTIONIST)
    summaries = {s["workflow_id"]: s for s in await list_employee_summaries(real_database, auth_service=FakeAuth(keys={"openai"}))}
    assert summaries["20"]["talk"] == {"state": "on", "agent_node_id": "20:aiAgent:1"}
    assert summaries["21"]["talk"] == {"state": "off", "agent_node_id": None}
    assert summaries["22"]["talk"] == {"state": "off", "agent_node_id": None}
    assert summaries["20"]["pending_changes"] is False
    assert summaries["20"]["task"]["text"] == "Ready to start"
    detail = await get_employee_detail(real_database, "20", auth_service=FakeAuth(keys={"openai"}))
    assert detail["trigger_text"] == "When you message them"


async def test_the_card_follows_the_work_and_talk_follows_the_talk_agent(real_database):
    # The conversation shows its own "Thinking...", so the card does not
    # also follow the agent answering the owner.
    data = talking("23")
    data["nodes"].insert(0, node("23:aiAgent:2", "aiAgent", "Worker"))
    data["nodes"].insert(0, node("23:writeTodos:1", "writeTodos", "Checklist"))
    await save(real_database, "23", "Sam", data)
    await _hired(real_database, "23", {"agent": "23:aiAgent:2", "todos": "23:writeTodos:1", "talk_agent": "23:aiAgent:1"})
    summary = await get_employee_summary(real_database, "23", auth_service=FakeAuth(keys={"openai"}))
    assert summary["watch_node_ids"] == ["23:aiAgent:2", "23:writeTodos:1"]
    assert summary["talk"]["agent_node_id"] == "23:aiAgent:1"


async def test_talk_follows_the_live_generation_and_changes_wait_for_a_restart(real_database):
    # Talk was turned on in the saved graph, but the running generation
    # started before it: nobody answers yet, and a restart is waiting.
    await save(real_database, "24", "Sam", talking("24"))
    await control(real_database, "24", "running", graph_snapshot=snapshot("24", talking("24", reply=False)))
    summary = await get_employee_summary(real_database, "24", auth_service=FakeAuth(keys={"openai"}))
    assert summary["talk"] == {"state": "off", "agent_node_id": None}
    assert summary["pending_changes"] is True

    await control(real_database, "24", "running", generation=2, graph_snapshot=snapshot("24", talking("24")))
    summary = await get_employee_summary(real_database, "24", auth_service=FakeAuth(keys={"openai"}))
    assert summary["talk"] == {"state": "on", "agent_node_id": "24:aiAgent:1"}
    assert summary["pending_changes"] is False
    # Waiting for the owner's messages goes without saying with the message
    # box under the card: no task line. Without Talk the line stays.
    assert summary["task"] is None
    await save(real_database, "24", "Sam", talking("24", reply=False))
    await control(real_database, "24", "running", generation=3, graph_snapshot=snapshot("24", talking("24", reply=False)))
    summary = await get_employee_summary(real_database, "24", auth_service=FakeAuth(keys={"openai"}))
    assert summary["task"] == {"label": "Now", "text": "Waiting for your messages"}


async def test_asking_first(real_database):
    await save(real_database, "25", "Ask", RECEPTIONIST)
    await hire(real_database, "25", rules={"ask_first": True})
    await save(real_database, "26", "Free", RECEPTIONIST)
    await hire(real_database, "26", rules={"ask_first": False})
    await save(real_database, "27", "Unset", RECEPTIONIST)
    await hire(real_database, "27")
    await save(real_database, "28", "Gated", graph(node("28:aiAgent:1", "aiAgent"), node("28:approvalGate:1", "approvalGate")))
    await save(real_database, "29", "Open", graph(node("29:aiAgent:1", "aiAgent")))
    summaries = {s["workflow_id"]: s["asks_first"] for s in await list_employee_summaries(real_database, auth_service=FakeAuth())}
    # A hire's rule, missing meaning on; built in the editor: an approval gate.
    assert summaries == {"25": True, "26": False, "27": True, "28": True, "29": False}


async def test_a_hired_employees_apps_are_the_ones_its_graph_uses(real_database):
    # Hired naming Stripe, which asking first left out of the graph: no
    # phantom "Connect Stripe to start".
    await save(real_database, "30", "Maya", RECEPTIONIST)
    await hire(real_database, "30", apps=["whatsapp", "stripe"])
    summary = await get_employee_summary(real_database, "30", auth_service=FakeAuth(keys={"openai"}))
    assert [a["app_id"] for a in summary["apps"]] == ["whatsapp"]
    assert [a["app_id"] for a in summary["missing_apps"]] == ["whatsapp"]


async def test_a_stopped_employee_points_at_dev_mode(real_database):
    await save(real_database, "31", "Maya", RECEPTIONIST)
    await control(real_database, "31", "failed")
    summary = await get_employee_summary(real_database, "31", auth_service=FakeAuth(keys={"openai"}))
    assert summary["task"] == {"label": "Paused", "text": "Stopped after a problem. Open it in Dev mode to see what happened."}
