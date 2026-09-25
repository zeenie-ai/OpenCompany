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
        async def has_valid_key(self, key):
            return False

        async def get_oauth_tokens(self, provider):
            return None

        async def list_api_key_providers(self):
            return []

    import core.container as container_module
    import services.plugin.deps as deps

    fake = SimpleNamespace(database=lambda: real_database, auth_service=lambda: Auth())
    monkeypatch.setattr(container_module, "container", fake)
    monkeypatch.setattr(deps, "get_auth_service", lambda: Auth())
    return fake


def test_handlers_are_registered_and_not_internal():
    registered = get_ws_handlers()
    for name in ("list_employees", "get_employee"):
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


async def test_get_errors(container):
    missing = await handlers.handle_get_employee({"workflow_id": "nope"}, None)
    assert missing == {"success": False, "error": "not_found", "workflow_id": "nope"}
    bad = await handlers.handle_get_employee({}, None)
    assert bad["success"] is False
    assert "workflow_id" in bad["error"]
