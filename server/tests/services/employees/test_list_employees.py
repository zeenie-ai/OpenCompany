"""Employee summaries: every workflow is an employee, hired or built in the
editor, with status, task text, apps and control from the same sources the
editor reads."""

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


class FakeAuth:
    def __init__(self, keys=(), oauth=None):
        self.keys = set(keys)
        self.oauth = dict(oauth or {})

    async def has_valid_key(self, key):
        return key in self.keys

    async def get_oauth_tokens(self, provider):
        return self.oauth.get(provider)

    async def list_api_key_providers(self):
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


@pytest.mark.parametrize(
    ("trigger", "text"),
    [
        ({"kind": "schedule", "every": "weekday", "at": "09:00"}, "Every weekday at 09:00"),
        ({"kind": "schedule", "every": "week", "day": "monday", "at": "08:00"}, "Every Monday at 08:00"),
        ({"kind": "schedule", "every": "hour"}, "Every hour"),
        ({"kind": "schedule", "every": "month", "day": "1", "at": "10:00"}, "On day 1 of every month at 10:00"),
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
    from services.workflow_storage.handlers import delete_workflow_with_context_archival

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
