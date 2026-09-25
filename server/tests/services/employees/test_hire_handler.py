"""``hire_employee``: a setup becomes a saved, valid workflow with its
employee row; a retry with the same key finds that employee; a changed
payload under the same key is refused; and it starts on its own only when
nothing is missing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import nodes  # noqa: F401 - registers every plugin for the validator
import services.employees  # noqa: F401 - registers the handlers
from services.employees import hire, store
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

    def __init__(self, _auth=None):
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
def harness(monkeypatch, real_database):
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

    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: real_database, auth_service=lambda: Auth()))
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broadcaster())
    monkeypatch.setattr(hire, "Connections", FakeConnections)
    monkeypatch.setattr(summaries, "Connections", FakeConnections)

    async def choose(*_args, **_kwargs):
        return LLMChoice(provider="openai", model="gpt-x", local=False) if FakeConnections.ai else None

    monkeypatch.setattr(hire, "resolve_llm_choice", choose)
    starts = []
    monkeypatch.setattr(hire, "_start_in_background", lambda workflow_id, owner, key: starts.append((workflow_id, owner, key)))
    FakeConnections.ai = True
    yield SimpleNamespace(database=real_database, frames=frames, starts=starts)
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
    assert result["started"] is True and harness.starts == [(workflow_id, "owner", "hire-1")]

    workflow = await harness.database.get_workflow(workflow_id)
    types = {node["type"] for node in workflow.data["nodes"]}
    assert {"chatTrigger", "aiAgent", "context", "console", "writeTodos"} <= types
    assert workflow.data["owner_id"] == "owner"
    row = await store.get_by_workflow(harness.database, workflow_id)
    assert row.hire_state == "ready" and row.node_roles["agent"].startswith(f"{workflow_id}:aiAgent:")
    agent_params = await harness.database.get_node_parameters(row.node_roles["agent"])
    assert agent_params["provider"] == "openai" and "You are Ada" in agent_params["system_message"]
    kinds = [frame.get("stage") or frame["data"]["type"] for frame in harness.frames]
    assert "created" in kinds and "com.opencompany.employee.hired" in kinds


async def test_the_same_key_finds_the_same_employee(harness):
    first = await hire.handle_hire_employee(payload(), SOCKET)
    again = await hire.handle_hire_employee(payload(), SOCKET)
    assert again["success"] is True and again["idempotent"] is True
    assert again["employee"]["workflow_id"] == first["employee"]["workflow_id"]
    assert len(harness.starts) == 1


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
