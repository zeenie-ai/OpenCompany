"""``enable_employee_talk``: Turn on Talk adds the talk line to the saved
workflow in one transaction (announced to open editors), records the new
parts on a hired employee, and applies them without Reset so the line runs.
A retry adds nothing twice; a change under way, or a graph with nothing to
talk to, is refused."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import nodes  # noqa: F401 - registers every plugin
import services.employees  # noqa: F401 - registers the handlers
import services.status_broadcaster as status_broadcaster
from models.database import WorkflowControlExecution
from services.employees import safe_apply
from services.employees import handlers, store
from services.graph_build import context_data, context_edge, main_edge, skill_edge, tool_edge
from services.workflow_migrations import normalize_workflow_graph
from services.workflow_sanitizer import sanitize_workflow_graph
from services.workflow_storage import mutate
from services.ws_handler_registry import get_ws_handlers

SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))


class Auth:
    async def has_valid_key(self, key):
        return key == "openai"

    async def get_oauth_tokens(self, provider):
        return None

    async def list_api_key_providers(self):
        return ["openai"]


def node(node_id, node_type, label, x=0, y=0, **data):
    return {"id": node_id, "type": node_type, "position": {"x": x, "y": y}, "data": {"label": label, **data}}


def receptionist(workflow_id="7"):
    """Maya as a hire from before Talk: WhatsApp in, a worker with tools and
    skills, a reply out; no chat trigger."""
    w = workflow_id
    return {
        "nodes": [
            node(f"{w}:whatsappReceive:1", "whatsappReceive", "New WhatsApp message", 0, 200),
            node(f"{w}:aiAgent:1", "aiAgent", "Maya", 360, 200),
            node(f"{w}:writeTodos:1", "writeTodos", "Checklist", 120, 440),
            node(f"{w}:simpleMemory:1", "simpleMemory", "Memory", 290, 440),
            node(f"{w}:canvas:1", "canvas", "Canvas", 460, 440),
            node(f"{w}:masterSkill:1", "masterSkill", "Skills", -60, 440),
            node(f"{w}:whatsappSend:1", "whatsappSend", "Reply on WhatsApp", 720, 200),
        ],
        "edges": [
            main_edge(f"{w}:whatsappReceive:1", f"{w}:aiAgent:1").to_dict(),
            tool_edge(f"{w}:writeTodos:1", f"{w}:aiAgent:1").to_dict(),
            tool_edge(f"{w}:simpleMemory:1", f"{w}:aiAgent:1").to_dict(),
            tool_edge(f"{w}:canvas:1", f"{w}:aiAgent:1").to_dict(),
            skill_edge(f"{w}:masterSkill:1", f"{w}:aiAgent:1").to_dict(),
            main_edge(f"{w}:aiAgent:1", f"{w}:whatsappSend:1").to_dict(),
        ],
    }


def chat_hire(workflow_id="8"):
    """A Chat hire from before Talk: its agent answers, but nothing posts it."""
    w = workflow_id
    return {
        "nodes": [
            node(f"{w}:chatTrigger:1", "chatTrigger", "Chat", 0, 200),
            node(f"{w}:aiAgent:1", "aiAgent", "Sam", 360, 200),
            node(f"{w}:context:1", "context", "Context", 360, 20, **context_data(f"{w}:aiAgent:1")),
        ],
        "edges": [
            main_edge(f"{w}:chatTrigger:1", f"{w}:aiAgent:1").to_dict(),
            context_edge(f"{w}:context:1", f"{w}:aiAgent:1").to_dict(),
        ],
    }


class FakeApply:
    """Safe handoff records calls and replaces only the operational snapshot."""

    def __init__(self, database):
        self.database = database
        self.calls = []
        self.error = None

    async def __call__(self, database, workflow_id, *, owner_id, key):
        self.calls.append((workflow_id, owner_id, key))
        if self.error:
            return {"success": False, "error": self.error, "activation_state": "failed"}
        latest = await self.database.get_latest_workflow_control(workflow_id)
        if latest is None or latest.status == "reset":
            return {"success": True, "activation_state": "saved"}
        workflow = await self.database.get_workflow(workflow_id)
        async with self.database.reserved_session() as session:
            current = await session.get(WorkflowControlExecution, latest.id)
            current.graph_snapshot = workflow.data
            current.revision += 1
            await session.commit()
        return {"success": True, "activation_state": latest.status}


@pytest.fixture()
def harness(monkeypatch, real_database):
    import core.container as container_module
    import services.plugin.deps as deps

    frames = []

    class Broadcaster:
        async def broadcast(self, message):
            frames.append(message)

    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: real_database, auth_service=lambda: Auth()))
    monkeypatch.setattr(deps, "get_auth_service", lambda: Auth())
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broadcaster())
    changed = []
    monkeypatch.setattr(mutate, "notify_graph_changed", changed.append)
    restart = FakeApply(real_database)
    monkeypatch.setattr(safe_apply, "apply_saved_changes", restart)
    return SimpleNamespace(database=real_database, frames=frames, changed=changed, restart=restart)


async def control(database, workflow_id, status, generation=1, graph=None):
    graph = graph or {"nodes": [], "edges": []}
    snapshot = sanitize_workflow_graph(normalize_workflow_graph(workflow_id, graph["nodes"], graph["edges"]).graph_data())
    async with database.get_session() as session:
        session.add(
            WorkflowControlExecution(
                id=f"{workflow_id}-{generation}",
                workflow_id=workflow_id,
                generation=generation,
                execution_id=f"gen-{generation}",
                root_execution_id=f"gen-{generation}",
                graph_hash="0" * 64,
                graph_snapshot=snapshot,
                status=status,
                idempotency_key=f"k-{generation}",
            )
        )
        await session.commit()


async def hired(database, workflow_id, name, data, **fields):
    assert await database.save_workflow(workflow_id=workflow_id, name=name, slug=f"{name}_{workflow_id}", data=data)
    row, _ = await store.reserve(database, owner_id="owner", idempotency_key=f"k{workflow_id}", payload_hash="h", fields=fields)
    await store.mark_ready(database, row.id, workflow_id=workflow_id, node_roles={"trigger": data["nodes"][0]["id"], "agent": f"{workflow_id}:aiAgent:1"})


async def enable(workflow_id, key="k1"):
    return await handlers.handle_enable_employee_talk({"workflow_id": workflow_id, "idempotency_key": key}, SOCKET)


def test_the_handlers_are_registered():
    registered = get_ws_handlers()
    assert "enable_employee_talk" in registered and "apply_employee_changes" in registered


async def test_a_running_hire_gets_a_talk_line_through_safe_apply(harness):
    database = harness.database
    await database.save_user_settings({"profile_call_name": "Alex"}, "default")
    await hired(
        database,
        "7",
        "Maya",
        receptionist(),
        role="Receptionist",
        job="Answer customer messages on WhatsApp",
        rules={"ask_first": True, "items": []},
        trigger={"kind": "app_event", "app": "whatsapp"},
    )
    assert await database.save_node_parameters("7:aiAgent:1", {"provider": "anthropic", "model": "claude-x", "prompt": "{{newwhatsappmessage.text}}"})
    await control(database, "7", "running", graph=receptionist())
    before = (await handlers.handle_get_employee({"workflow_id": "7"}, SOCKET))["employee"]
    assert before["talk"]["state"] == "off"

    result = await enable("7")
    assert result["success"] is True, result
    employee = result["employee"]
    row = await store.get_by_workflow(database, "7")
    roles = row.node_roles
    assert roles["agent"] == "7:aiAgent:1"
    assert {role: roles[role] for role in ("talk_trigger", "talk_agent", "talk_context", "talk_reply", "builder")} == {
        "talk_trigger": "7:chatTrigger:1",
        "talk_agent": "7:aiAgent:2",
        "talk_context": "7:context:1",
        "talk_reply": "7:chatReply:1",
        "builder": "7:agentBuilder:1",
    }
    # Restarted on the saved graph, so the line runs and the page follows it
    # through `talk` (the card keeps following the worker).
    assert harness.restart.calls == [("7", "owner", "k1")]
    assert employee["talk"] == {"state": "on", "agent_node_id": "7:aiAgent:2"}
    assert employee["pending_changes"] is False
    assert "7:aiAgent:2" not in employee["watch_node_ids"]
    assert employee["revision"] > before["revision"]

    # The talk agent: the worker's model, instructions written from the hire.
    talk = await database.get_node_parameters("7:aiAgent:2")
    assert (talk["provider"], talk["model"], talk["prompt"]) == ("anthropic", "claude-x", "{{talk.message}}")
    assert "You are Maya, receptionist for Alex" in talk["system_message"]
    assert "Alex talks to you in Talk" in talk["system_message"] and "agent_builder" in talk["system_message"]
    assert "check your memory" in talk["system_message"].lower() and "canvas tool" in talk["system_message"]
    assert await database.get_node_parameters("7:chatTrigger:1") == {"session_id": "7"}
    assert await database.get_node_parameters("7:chatReply:1") == {"message": "{{talkwithmaya.response}}"}
    # Open editors adopt the saved batch.
    [ops] = [frame for frame in harness.frames if frame["type"] == "workflow_ops_apply"]
    assert ops["data"]["persisted"] is True and ops["data"]["workflow_id"] == "7"
    assert harness.changed == ["7"]


async def test_a_retry_adds_nothing_twice_and_applies_once(harness):
    database = harness.database
    await hired(database, "7", "Maya", receptionist(), trigger={"kind": "app_event", "app": "whatsapp"})
    await control(database, "7", "running", graph=receptionist())
    assert (await enable("7"))["success"] is True
    count = len((await database.get_workflow("7")).data["nodes"])
    again = await enable("7")
    assert again["success"] is True and again["employee"]["talk"]["state"] == "on"
    assert len((await database.get_workflow("7")).data["nodes"]) == count
    assert len(harness.restart.calls) == 1


async def test_a_schedule_hire_also_posts_its_reports(harness):
    database = harness.database
    schedule = receptionist()
    schedule["nodes"][0] = node("7:cronScheduler:1", "cronScheduler", "Schedule", 0, 200)
    schedule["edges"][0] = main_edge("7:cronScheduler:1", "7:aiAgent:1").to_dict()
    await hired(database, "7", "Nora", schedule, trigger={"kind": "schedule", "every": "day", "at": "09:00"})
    assert (await enable("7"))["success"] is True
    roles = (await store.get_by_workflow(database, "7")).node_roles
    assert await database.get_node_parameters(roles["report_post"]) == {"message": "{{maya.response}}"}
    # Nothing was running: nothing to restart, and it is ready with Talk on.
    employee = (await handlers.handle_get_employee({"workflow_id": "7"}, SOCKET))["employee"]
    assert employee["talk"]["state"] == "on" and employee["control"]["state"] == "never_started"


async def test_a_chat_hire_from_before_talk_gets_only_a_reply(harness):
    database = harness.database
    await hired(database, "8", "Sam", chat_hire(), trigger={"kind": "manual"})
    result = await enable("8")
    assert result["success"] is True and result["employee"]["talk"] == {"state": "on", "agent_node_id": "8:aiAgent:1"}
    types = [n["type"] for n in (await database.get_workflow("8")).data["nodes"]]
    assert types.count("chatTrigger") == 1 and types.count("aiAgent") == 1
    roles = (await store.get_by_workflow(database, "8")).node_roles
    assert (roles["talk_agent"], roles["talk_trigger"], roles["talk_context"]) == ("8:aiAgent:1", "8:chatTrigger:1", "8:context:1")
    assert await database.get_node_parameters(roles["talk_reply"]) == {"message": "{{sam.response}}"}


async def test_a_workflow_built_in_the_editor_gets_a_copy_of_its_agent(harness):
    database = harness.database
    await database.save_user_settings({"profile_full_name": "Alex Kim"}, "default")
    data = receptionist("9")
    assert await database.save_workflow(workflow_id="9", name="Support bot", slug="Support_bot_9", data=data)
    assert await database.save_node_parameters("9:aiAgent:1", {"provider": "openai", "model": "gpt-x", "system_message": "You help customers."})
    result = await enable("9")
    assert result["success"] is True
    saved = await database.get_workflow("9")
    by_label = {n["data"]["label"]: n for n in saved.data["nodes"]}
    talk = await database.get_node_parameters(by_label["Talk with Support bot"]["id"])
    assert talk["system_message"].startswith("You help customers.\n\nHere you are not doing your usual work: Alex Kim is talking to you in Talk.")
    # Built in the editor: no Agent Builder, no reports posted.
    assert "Agent Builder" not in by_label and "Post to Talk" not in by_label
    assert await store.get_by_workflow(database, "9") is None


async def test_talk_already_on_needs_no_new_handoff(harness):
    database = harness.database
    await hired(database, "8", "Sam", chat_hire(), trigger={"kind": "manual"})
    assert (await enable("8", key="k1"))["success"] is True
    graph = (await database.get_workflow("8")).data
    await control(database, "8", "running", graph=graph)
    result = await enable("8", key="k2")
    assert result["success"] is True
    assert harness.restart.calls == [("8", "owner", "k1")]


async def test_the_live_generation_without_the_line_is_applied(harness):
    # Talk is on in the saved graph, but the running generation predates it.
    database = harness.database
    await hired(database, "8", "Sam", chat_hire(), trigger={"kind": "manual"})
    await control(database, "8", "running", graph=chat_hire())
    assert (await enable("8", key="k1"))["success"] is True
    assert harness.restart.calls == [("8", "owner", "k1")]


@pytest.mark.parametrize("status", ["starting", "pausing", "resuming", "resetting"])
async def test_a_change_under_way_is_a_conflict(harness, status):
    database = harness.database
    await hired(database, "7", "Maya", receptionist())
    await control(database, "7", status, graph=receptionist())
    result = await enable("7")
    assert result["success"] is False and result["error"] == "conflict"
    assert len((await database.get_workflow("7")).data["nodes"]) == len(receptionist()["nodes"])
    assert harness.restart.calls == []


async def test_a_failed_handoff_keeps_the_line_and_says_so(harness):
    database = harness.database
    await hired(database, "7", "Maya", receptionist())
    await control(database, "7", "running", graph=receptionist())
    harness.restart.error = "apply_failed"
    result = await enable("7")
    assert result["success"] is False and result["error"] == "apply_failed"
    assert result["employee"]["pending_changes"] is True
    assert (await store.get_by_workflow(database, "7")).node_roles["talk_agent"] == "7:aiAgent:2"


async def test_another_owner_cannot_add_talk_to_an_employee(harness):
    await hired(harness.database, "7", "Maya", receptionist())
    before = (await harness.database.get_workflow("7")).data
    other_socket = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="other-owner"))
    result = await handlers.handle_enable_employee_talk({"workflow_id": "7", "idempotency_key": "cross-owner"}, other_socket)
    assert result["success"] is False and result["error"] == "not_found"
    assert (await harness.database.get_workflow("7")).data == before
    assert not harness.restart.calls


async def test_turning_on_talk_retains_a_paused_employees_generation(harness):
    await hired(harness.database, "8", "Sam", chat_hire(), trigger={"kind": "manual"})
    await control(harness.database, "8", "paused", graph=chat_hire())
    before = await harness.database.get_latest_workflow_control("8")
    result = await enable("8")
    after = await harness.database.get_latest_workflow_control("8")
    assert result["success"] is True and result["activation_state"] == "paused"
    assert after.id == before.id and after.generation == before.generation
    assert after.execution_id == before.execution_id and after.status == "paused"


async def test_nothing_to_talk_to_and_bad_requests(harness):
    database = harness.database
    lonely = {"nodes": [node("5:chatTrigger:1", "chatTrigger", "Chat"), node("5:console:1", "console", "Log")], "edges": []}
    assert await database.save_workflow(workflow_id="5", name="Lonely", slug="Lonely_5", data=lonely)
    unsupported = await enable("5")
    assert unsupported["success"] is False and unsupported["error"] == "unsupported" and unsupported["employee"]["workflow_id"] == "5"
    assert await enable("missing") == {"success": False, "error": "not_found", "workflow_id": "missing"}
    assert await handlers.handle_enable_employee_talk({"workflow_id": "5"}, SOCKET) == {"success": False, "error": "invalid_request"}
