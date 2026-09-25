"""``start_employee``: refused while an app or the AI model is missing,
moves the agent onto a usable model, then starts through the same path as
the editor's Start."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.employees import start


SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))


@pytest.fixture()
def harness(monkeypatch):
    state = SimpleNamespace(summary=None, starts=[], params={"7:aiAgent:1": {"provider": "anthropic", "model": "old", "prompt": "p"}}, saved={})

    class Database:
        async def get_node_parameters(self, node_id):
            return state.params.get(node_id)

        async def save_node_parameters(self, node_id, params):
            state.saved[node_id] = params
            return True

        async def get_workflow(self, workflow_id):
            return None

    import core.container as container_module
    import services.deployment.handlers as deployment_handlers
    import services.employees.summaries as summaries

    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: Database(), auth_service=lambda: object()))

    async def summary(*_args, **_kwargs):
        return state.summary

    monkeypatch.setattr(summaries, "get_employee_summary", summary)

    async def start_saved(workflow_id, **kwargs):
        state.starts.append((workflow_id, kwargs))
        return {"success": True, "state": "starting", "revision": 1}

    monkeypatch.setattr(deployment_handlers, "start_saved_workflow", start_saved)

    class Connections:
        def __init__(self, _auth):
            pass

        async def ai_providers(self):
            return ["openai"]

    monkeypatch.setattr(start, "Connections", Connections)

    async def agents(_database, _workflow_id):
        return ["7:aiAgent:1"]

    monkeypatch.setattr(start, "_agent_ids", agents)

    async def choose(*_args, **_kwargs):
        return SimpleNamespace(provider="openai", model="gpt-x")

    monkeypatch.setattr(start, "resolve_llm_choice", choose)

    async def no_endpoints(_auth):
        return []

    import services.llm.endpoints as endpoints

    monkeypatch.setattr(endpoints, "list_endpoints", no_endpoints)
    return state


def summary(**patch):
    return {"workflow_id": "7", "missing_apps": [], "needs_ai": False, **patch}


async def test_missing_apps_and_ai_are_refused(harness):
    harness.summary = summary(missing_apps=[{"name": "WhatsApp"}])
    result = await start.handle_start_employee({"workflow_id": "7", "expected_revision": 0}, SOCKET)
    assert result == {"success": False, "error": "missing_apps", "missing_apps": [{"name": "WhatsApp"}]}
    harness.summary = summary(needs_ai=True)
    assert (await start.handle_start_employee({"workflow_id": "7"}, SOCKET))["error"] == "needs_ai"
    harness.summary = None
    assert (await start.handle_start_employee({"workflow_id": "7"}, SOCKET))["error"] == "not_found"
    assert harness.starts == []


async def test_it_moves_the_agent_onto_a_usable_model_and_starts(harness):
    harness.summary = summary()
    result = await start.handle_start_employee({"workflow_id": "7", "expected_revision": 3, "idempotency_key": "s1"}, SOCKET)
    assert result["success"] is True
    assert harness.saved["7:aiAgent:1"] == {"provider": "openai", "model": "gpt-x", "prompt": "p"}
    assert harness.starts == [("7", {"owner_id": "owner", "expected_revision": 3, "idempotency_key": "s1"})]


async def test_a_usable_model_is_left_alone(harness):
    harness.summary = summary()
    harness.params["7:aiAgent:1"]["provider"] = "openai"
    await start.handle_start_employee({"workflow_id": "7"}, SOCKET)
    assert harness.saved == {}
