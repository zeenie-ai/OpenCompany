"""``apply_employee_changes``: safely hand off to the latest saved graph
and answer with its fresh summary; handoff conflicts and
failures come back as error codes, still with the summary. Changes to one
employee run one at a time."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import services.employees  # noqa: F401 - registers the handlers
from services.employees import handlers, safe_apply

SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))


class Auth:
    async def has_valid_key(self, key, *, principal=None):
        return False

    async def get_oauth_tokens(self, provider):
        return None

    async def list_api_key_providers(self, *, principal=None):
        return []


@pytest.fixture()
def harness(monkeypatch, real_database):
    import core.container as container_module
    import services.plugin.deps as deps

    state = SimpleNamespace(database=real_database, calls=[], result={"success": True, "activation_state": "running"})

    async def restart(_database, workflow_id, *, owner_id, key):
        state.calls.append((workflow_id, owner_id, key))
        return state.result

    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: real_database, auth_service=lambda: Auth()))
    monkeypatch.setattr(deps, "get_auth_service", lambda: Auth())
    monkeypatch.setattr(safe_apply, "apply_saved_changes", restart)
    return state


async def saved(database, workflow_id="7"):
    graph = {"nodes": [{"id": f"{workflow_id}:aiAgent:1", "type": "aiAgent", "data": {"label": "Maya"}}], "edges": []}
    assert await database.save_workflow(workflow_id=workflow_id, name="Maya", slug=f"Maya_{workflow_id}", data=graph)


async def apply(workflow_id="7", key="k1"):
    return await handlers.handle_apply_employee_changes({"workflow_id": workflow_id, "idempotency_key": key}, SOCKET)


@pytest.mark.parametrize("outcome", ["running", "paused", "saved", "waiting"])
async def test_it_safely_applies_the_saved_graph(harness, outcome):
    await saved(harness.database)
    harness.result = {"success": True, "activation_state": outcome}
    result = await apply()
    assert result["success"] is True and result["employee"]["workflow_id"] == "7"
    assert harness.calls == [("7", "owner", "k1")]


@pytest.mark.parametrize("error", ["conflict", "apply_failed"])
async def test_a_restart_that_did_not_finish_says_why(harness, error):
    await saved(harness.database)
    harness.result = {"success": False, "error": error}
    result = await apply()
    assert (result["success"], result["error"]) == (False, error)
    assert result["employee"]["workflow_id"] == "7"


async def test_bad_requests(harness):
    assert await apply("missing") == {"success": False, "error": "not_found"}
    assert await handlers.handle_apply_employee_changes({"idempotency_key": "k"}, SOCKET) == {"success": False, "error": "invalid_request"}
    assert await handlers.handle_apply_employee_changes({"workflow_id": "7"}, SOCKET) == {"success": False, "error": "invalid_request"}
    assert harness.calls == []


async def test_changes_to_one_employee_run_one_at_a_time(harness, monkeypatch):
    await saved(harness.database)
    order = []
    release = asyncio.Event()

    async def slow_restart(_database, workflow_id, *, owner_id, key):
        order.append(f"start {key}")
        if key == "k1":
            await release.wait()
        order.append(f"end {key}")
        return {"success": True, "activation_state": "running"}

    monkeypatch.setattr(safe_apply, "apply_saved_changes", slow_restart)
    first = asyncio.ensure_future(apply(key="k1"))
    second = asyncio.ensure_future(apply(key="k2"))
    await asyncio.sleep(0.05)
    assert order == ["start k1"]
    release.set()
    await asyncio.gather(first, second)
    assert order == ["start k1", "end k1", "start k2", "end k2"]


async def test_an_employee_an_older_builder_made_is_upgraded_first(harness):
    from services.employees import store
    from tests.services.employees.test_upgrade import GRAPH, PARAMS, ROLES

    database = harness.database
    assert await database.save_workflow(workflow_id="7", name="Maya", slug="Maya_7", data=GRAPH)
    for node_id, params in PARAMS.items():
        assert await database.save_node_parameters(node_id, params)
    row, _ = await store.reserve(
        database,
        owner_id="owner",
        idempotency_key="k7",
        payload_hash="h",
        fields={"role": "Receptionist", "apps": ["whatsapp", "web"], "rules": {"ask_first": False, "items": []}, "builder_version": 2},
    )
    await store.mark_ready(database, row.id, workflow_id="7", node_roles=ROLES)

    result = await apply()

    assert result["success"] is True and harness.calls == [("7", "owner", "k1")]
    employee = await store.get_by_workflow(database, "7")
    assert employee.builder_version == 3
    gate = employee.node_roles["gate"]
    graph = (await database.get_workflow("7")).data
    assert any(node["id"] == gate and node["type"] == "approvalGate" for node in graph["nodes"])
    assert not any(edge["source"] == "7:aiAgent:1" and edge["target"] == "7:whatsappSend:1" for edge in graph["edges"])
    assert (await database.get_node_parameters("7:whatsappSend:1"))["message"] == "{{checkbeforesending.text}}"
    assert (await database.get_node_parameters("7:browser:1"))["interaction"] == "full"
    # Once is enough: the next Apply adds nothing.
    nodes_before = len(graph["nodes"])
    await apply(key="k2")
    assert len((await database.get_workflow("7")).data["nodes"]) == nodes_before
