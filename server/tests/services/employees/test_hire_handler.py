"""``hire_employee``: a setup becomes a saved, valid workflow with its
employee row; a retry with the same key finds that employee; a changed
payload under the same key is refused, and the same key while its first
attempt is still building is busy; it starts on its own only when nothing
is missing; and an app the graph leaves out never shows as one to
connect."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import nodes  # noqa: F401 - registers every plugin for the validator
import services.employees  # noqa: F401 - registers the handlers
from services.employees import hire, store
from services.employees.hire_request import HireEmployeeRequest
from services.employees.llm import LLMChoice
from services.ws_handler_registry import get_ws_handlers

SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))


def payload(**patch):
    base = {
        "idempotency_key": "hire-1",
        "job": "Help me plan my week",
        "name": "Ada",
        "role": "Assistant",
        "description": "Plans the week with you.",
        "apps": [],
        "steps": [{"title": "Plan the week", "role": "agent"}],
        "rules": {"ask_first": True, "items": []},
        "trigger": {"kind": "manual"},
        "source": {"spec_version": 1},
    }
    base.update(patch)
    return base


class FakeConnections:
    ai = True

    def __init__(self, _auth=None, *, principal=None):
        pass

    async def connected_app_ids(self):
        return []

    async def is_connected(self, provider_id):
        return False

    async def state(self, provider_id):
        return {"connected": False, "account_label": None}

    async def app_ref(self, app):
        return {"app_id": app.id, "provider_id": app.provider_id, "name": app.name, "icon_ref": None, "connected": False, "supported": True}

    async def has_ai(self):
        return self.ai

    async def ai_providers(self):
        return ["openai"] if self.ai else []


@pytest.fixture()
async def harness(monkeypatch, real_database):
    class Auth:
        async def get_api_key(self, key, session_id="default"):
            return None

    import core.container as container_module
    import services.employees.summaries as summaries
    import services.status_broadcaster as status_broadcaster

    frames = []

    class Broadcaster:
        async def broadcast(self, message):
            frames.append(message)

        async def broadcast_workflow_lifecycle(self, stage, **data):
            frames.append({"type": "workflow_lifecycle", "stage": stage, **data})

    runtime_settings = SimpleNamespace(temporal_enabled=True, temporal_agent_workflow_enabled=True)
    monkeypatch.setattr(container_module, "container", SimpleNamespace(
        database=lambda: real_database, auth_service=lambda: Auth(),
        settings=lambda: runtime_settings, temporal_client=lambda: SimpleNamespace(is_connected=True),
    ))
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broadcaster())
    monkeypatch.setattr(hire, "Connections", FakeConnections)
    monkeypatch.setattr(summaries, "Connections", FakeConnections)

    async def choose(*_args, **_kwargs):
        return LLMChoice(provider="openai", model="gpt-x", local=False) if FakeConnections.ai else None

    monkeypatch.setattr(hire, "resolve_llm_choice", choose)
    starts = []
    from services.employees import activation

    async def activate(_database, workflow_id):
        employee = await store.get_by_workflow(_database, workflow_id)
        starts.append((workflow_id, employee.owner_id, employee.idempotency_key))
        return True

    monkeypatch.setattr(activation, "activate_pending", activate)
    FakeConnections.ai = True
    yield SimpleNamespace(database=real_database, frames=frames, starts=starts)
    await asyncio.gather(*tuple(hire._starts), return_exceptions=True)
    FakeConnections.ai = True


def test_hire_and_start_are_registered():
    registered = get_ws_handlers()
    assert "hire_employee" in registered and "start_employee" in registered


async def test_a_hire_saves_a_valid_workflow_and_starts_it(harness):
    result = await hire.handle_hire_employee(payload(), SOCKET)
    assert result["success"] is True, result
    employee = result["employee"]
    workflow_id = employee["workflow_id"]
    assert employee["name"] == "Ada" and employee["role"] == "Assistant"
    assert result["started"] is True and result["activation_state"] == "starting"

    workflow = await harness.database.get_workflow(workflow_id)
    types = {node["type"] for node in workflow.data["nodes"]}
    assert {"chatTrigger", "aiAgent", "ai_employee", "taskTrigger", "context", "console", "writeTodos", "canvas"} <= types
    assert "employeeJob" not in types
    assert workflow.data["owner_id"] == "owner"
    row = await store.get_by_workflow(harness.database, workflow_id)
    assert row.hire_state == "ready" and row.node_roles["agent"].startswith(f"{workflow_id}:ai_employee:")
    assert row.team_plan["lead_node_id"] == row.node_roles["agent"]
    assert row.team_plan["talk_node_id"] == row.node_roles["talk_agent"]
    assert row.node_roles["talk_agent"] != row.node_roles["agent"]
    assert employee["canvas_node_id"] == row.node_roles["canvas"]
    agent_params = await harness.database.get_node_parameters(row.node_roles["agent"])
    assert agent_params["provider"] == "openai" and "You are Ada" in agent_params["system_message"]
    kinds = [frame.get("stage") or frame["data"]["type"] for frame in harness.frames]
    assert "created" in kinds and "com.opencompany.employee.hired" in kinds
    assert harness.starts == [(workflow_id, "owner", "hire-1")]


async def test_the_same_key_finds_the_same_employee(harness):
    first = await hire.handle_hire_employee(payload(), SOCKET)
    again = await hire.handle_hire_employee(payload(), SOCKET)
    assert again["success"] is True and again["idempotent"] is True
    assert again["employee"]["workflow_id"] == first["employee"]["workflow_id"]
    await asyncio.sleep(0)
    assert len(harness.starts) == 1


async def test_scheduled_hire_with_disconnected_whatsapp_replies_to_each_transport_request(harness, monkeypatch):
    import routers.websocket as router

    replies = []

    async def send(_socket, frame):
        replies.append(frame)

    monkeypatch.setattr(router, "_safe_send", send)
    data = payload(
        idempotency_key="hire-arjun",
        name="Arjun",
        role="Weather Reporter",
        job="Monitor Bangalore weather and send a daily forecast to WhatsApp",
        apps=["Web browser", "WhatsApp"],
        steps=[{"title": "Check Bangalore weather", "role": "tool", "app": "Web browser"}],
        trigger={"kind": "schedule", "every": "day", "at": "08:00"},
        sends_via="WhatsApp",
    )
    for request_id in ("wire-first-hire", "wire-retry"):
        await router._execute_handler(hire.handle_hire_employee, {**data, "request_id": request_id}, SOCKET, "hire_employee", request_id)
        reply = replies[-1]
        assert reply["success"] is True, reply
        assert reply["request_id"] == request_id
        assert reply["operation_request_id"] == "hire-arjun"
        assert reply["employee"]["name"] == "Arjun"
        assert reply["started"] is False
        assert any(app["app_id"] == "whatsapp" for app in reply["missing_apps"])

    assert replies[1]["idempotent"] is True
    assert replies[0]["employee"]["workflow_id"] == replies[1]["employee"]["workflow_id"]
    assert len(await harness.database.get_all_workflows()) == 1
    assert harness.starts == []


async def test_a_changed_payload_under_the_same_key_is_refused(harness):
    await hire.handle_hire_employee(payload(), SOCKET)
    changed = await hire.handle_hire_employee(payload(name="Bea"), SOCKET)
    assert changed == {"success": False, "error": "conflict"}


async def test_without_an_ai_model_it_waits(harness):
    FakeConnections.ai = False
    result = await hire.handle_hire_employee(payload(idempotency_key="hire-2"), SOCKET)
    assert result["success"] is True
    assert result["needs_ai"] is True and result["started"] is False
    assert harness.starts == []


async def test_unknown_apps_are_kept_and_do_not_block(harness):
    result = await hire.handle_hire_employee(payload(idempotency_key="hire-3", apps=["QuickBooks"]), SOCKET)
    assert result["success"] is True
    assert result["unsupported_apps"] == ["QuickBooks"]
    row = await store.get_by_workflow(harness.database, result["employee"]["workflow_id"])
    assert row.unsupported_apps == ["QuickBooks"]


async def test_bad_requests_are_refused(harness):
    assert (await hire.handle_hire_employee(payload(name=""), SOCKET))["error"] == "invalid_request"
    assert (await hire.handle_hire_employee(payload(steps=[]), SOCKET))["error"] == "invalid_request"
    assert (await hire.handle_hire_employee(payload(job="x" * 40000), SOCKET))["error"] == "too_large"


async def test_new_hires_get_the_library_skills_that_are_on(harness):
    database = harness.database
    await database.create_user_skill(name="short-reports", display_name="Short reports", description="Short reports.", instructions="Keep it short.")
    await database.create_user_skill(name="old-habit", display_name="Old habit", description="Switched off.", instructions="Nope.")
    await database.update_user_skill(name="old-habit", is_active=False)

    result = await hire.handle_hire_employee(payload(idempotency_key="hire-skills"), SOCKET)
    assert result["success"] is True, result
    row = await store.get_by_workflow(database, result["employee"]["workflow_id"])
    params = await database.get_node_parameters(row.node_roles["skills"])
    assert set(params["skills_config"]) == {"skill", "short-reports"}

    # A later edit to the library leaves this employee as it was hired.
    await database.update_user_skill(name="short-reports", instructions="Changed.")
    params = await database.get_node_parameters(row.node_roles["skills"])
    assert params["skills_config"]["short-reports"]["instructions"] == "Keep it short."


async def test_an_empty_library_gives_no_skills_node(harness):
    result = await hire.handle_hire_employee(payload(idempotency_key="hire-no-skills"), SOCKET)
    row = await store.get_by_workflow(harness.database, result["employee"]["workflow_id"])
    assert "skills" not in row.node_roles


async def _reserve_for(database, request_payload):
    """A reservation an earlier attempt made for this payload and is building."""
    request = HireEmployeeRequest.model_validate(request_payload)
    row, created = await store.reserve(
        database, owner_id="owner", idempotency_key=request.idempotency_key, payload_hash=hire.payload_hash(request), fields={}
    )
    assert created
    return row


async def test_the_same_key_while_it_is_still_building_is_busy(harness):
    data = payload(idempotency_key="hire-busy")
    await _reserve_for(harness.database, data)
    assert await hire.handle_hire_employee(data, SOCKET) == {"success": False, "error": "busy"}
    assert harness.starts == []
    assert (await harness.database.get_all_workflows()) == []


async def test_a_reservation_made_since_the_lookup_is_busy_too(harness, monkeypatch):
    data = payload(idempotency_key="hire-race")
    await _reserve_for(harness.database, data)
    real_lookup = store.get_by_idempotency_key
    calls = []

    async def lookup(*args, **kwargs):
        calls.append(args)
        # The handler's own first look misses: the other attempt reserved
        # the key just after it.
        return None if len(calls) == 1 else await real_lookup(*args, **kwargs)

    monkeypatch.setattr(store, "get_by_idempotency_key", lookup)
    assert await hire.handle_hire_employee(data, SOCKET) == {"success": False, "error": "busy"}
    assert (await harness.database.get_all_workflows()) == []


async def test_a_reservation_left_by_a_stopped_server_is_taken_over(harness):
    data = payload(idempotency_key="hire-stale")
    row = await _reserve_for(harness.database, data)
    # Recovery must expire the actual durable reservation, rather than only
    # changing the handler's preliminary lookup threshold.
    from models.employees import Employee
    async with harness.database.reserved_session() as session:
        abandoned = await session.get(Employee, row.id)
        abandoned.updated_at = datetime.now(timezone.utc) - timedelta(minutes=3)
        await session.commit()
    result = await hire.handle_hire_employee(data, SOCKET)
    assert result["success"] is True, result
    assert (await store.get_by_workflow(harness.database, result["employee"]["workflow_id"])).id == row.id


async def test_an_unexpected_error_leaves_the_key_free_to_retry(harness, monkeypatch):
    real_build = hire.build_employee_graph

    def broken(_inputs):
        raise RuntimeError("builder bug")

    monkeypatch.setattr(hire, "build_employee_graph", broken)
    failed = await hire.handle_hire_employee(payload(idempotency_key="hire-retry"), SOCKET)
    assert failed["success"] is False
    row = await store.get_by_idempotency_key(harness.database, "owner", "hire-retry")
    assert row.hire_state == "failed"

    monkeypatch.setattr(hire, "build_employee_graph", real_build)
    retried = await hire.handle_hire_employee(payload(idempotency_key="hire-retry"), SOCKET)
    assert retried["success"] is True, retried


async def test_an_app_that_spends_stays_while_they_ask_first(harness):
    # Stripe's only tool can spend money: it stays, and each call is refused
    # while they ask first (services/approvals/tool_calls.py).
    result = await hire.handle_hire_employee(payload(idempotency_key="hire-stripe", apps=["Stripe"]), SOCKET)
    assert result["success"] is True, result
    assert [app["app_id"] for app in result["employee"]["apps"]] == ["stripe"]
    assert [app["app_id"] for app in result["missing_apps"]] == ["stripe"]
    assert not any("stays off" in warning for warning in result["warnings"])
    row = await store.get_by_workflow(harness.database, result["employee"]["workflow_id"])
    assert row.apps == ["stripe"]
    # An app to connect first: they start once it is.
    assert result["started"] is False
