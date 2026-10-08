"""The employee WebSocket handlers: registered, reachable only on the
authenticated socket, and shaped as documented."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import services.employees  # noqa: F401 - registers the handlers
from services.authz.ws_surface import INTERNAL_SOCKET_HANDLERS
from services.employees import handlers
from services.ws_handler_registry import get_ws_handlers


@pytest.fixture()
def container(monkeypatch, real_database):
    class Auth:
        async def has_valid_key(self, key, *, principal=None):
            return False

        async def get_oauth_tokens(self, provider):
            return None

        async def list_api_key_providers(self, *, principal=None):
            return []

    import core.container as container_module
    import services.plugin.deps as deps

    fake = SimpleNamespace(database=lambda: real_database, auth_service=lambda: Auth())
    monkeypatch.setattr(container_module, "container", fake)
    monkeypatch.setattr(deps, "get_auth_service", lambda: Auth())
    return fake


def test_handlers_are_registered_and_not_internal():
    registered = get_ws_handlers()
    for name in ("list_employees", "get_employee", "get_employee_usage", "rename_employee", "set_employee_photo"):
        assert name in registered
        assert name not in INTERNAL_SOCKET_HANDLERS


async def test_list_and_get(container, real_database):
    graph = {"nodes": [{"id": "1:aiAgent:1", "type": "aiAgent", "data": {"label": "Maya"}}], "edges": []}
    assert await real_database.save_workflow(workflow_id="1", name="Maya", slug="Maya_1", data=graph)

    listed = await handlers.handle_list_employees({}, None)
    assert listed["success"] is True
    assert [e["workflow_id"] for e in listed["employees"]] == ["1"]

    got = await handlers.handle_get_employee({"workflow_id": "1"}, None)
    assert got["success"] is True
    assert got["employee"]["name"] == "Maya"
    assert "trigger_text" in got["employee"]


async def test_usage_counts_this_months_successes(container, real_database, monkeypatch):
    from services.employees import events, runs

    monkeypatch.setattr(events, "employee_changed", lambda _workflow_id: None)

    async def no_prune(_database):
        return None

    monkeypatch.setattr(runs, "_maybe_prune", no_prune)
    await runs.record_run(real_database, workflow_id="1", run_id="r1", status="success", runtime="local")
    await runs.record_run(real_database, workflow_id="1", run_id="r2", status="failed", runtime="local")
    assert await handlers.handle_get_employee_usage({}, None) == {"success": True, "tasks_this_month": 1}


async def test_get_errors(container):
    missing = await handlers.handle_get_employee({"workflow_id": "nope"}, None)
    assert missing == {"success": False, "error": "not_found", "workflow_id": "nope"}
    bad = await handlers.handle_get_employee({}, None)
    assert bad["success"] is False
    assert "workflow_id" in bad["error"]


async def test_reads_only_return_the_authenticated_owners_employees(container, real_database):
    from services.employees import store
    from services.employees.summaries import list_employee_summaries

    for workflow_id, owner in (("mine", "owner"), ("theirs", "other-owner")):
        assert await real_database.save_workflow(workflow_id=workflow_id, name=workflow_id, slug=workflow_id,
            data={"owner_id": owner, "nodes": [], "edges": []})
    # Old hire graphs may predate graph ownership; their employee row is authoritative.
    assert await real_database.save_workflow(workflow_id="old", name="Old", slug="Old", data={"nodes": [], "edges": []})
    row, _ = await store.reserve(real_database, owner_id="other-owner", idempotency_key="old", payload_hash="h", fields={})
    await store.mark_ready(real_database, row.id, workflow_id="old", node_roles={})
    socket = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))
    listed = await handlers.handle_list_employees({"user_id": "other-owner"}, socket)
    assert [employee["workflow_id"] for employee in listed["employees"]] == ["mine"]
    for workflow_id in ("theirs", "old"):
        assert await handlers.handle_get_employee({"workflow_id": workflow_id, "user_id": "other-owner"}, socket) == {
            "success": False, "error": "not_found", "workflow_id": workflow_id}
    assert (await handlers.handle_get_employee({"workflow_id": "mine"}, socket))["success"] is True
    # Internal callers retain the existing unfiltered service API.
    all_employees = await list_employee_summaries(real_database, auth_service=container.auth_service())
    assert {employee["workflow_id"] for employee in all_employees} == {"mine", "theirs", "old"}
