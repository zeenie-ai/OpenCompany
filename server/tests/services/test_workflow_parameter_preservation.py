"""Saving or reading a workflow must not edit node configuration.

Two live data-loss bugs motivated this file.

1. ``persist_parameter_aliases`` stripped a set of "legacy runtime fields"
   from *every* node whenever a context import had completed. ``session_id``
   is in that set and is a declared, load-bearing parameter on ``chatTrigger``
   / ``chatSend`` / ``chatHistory`` — so an ordinary read silently widened a
   chat trigger to match every session. It also injected ``reset_policy`` into
   node types that have no such concept.

2. ``handle_save_workflow`` omitted ``description`` from its ``save_workflow``
   kwargs while ``handle_get_workflow`` passed it, so every save nulled the
   workflow description.

Both were reachable from the editor's ordinary save/open cycle.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


CHAT_NODE_TYPES = ("chatTrigger", "chatSend", "chatHistory")


class _ParameterRecordingDatabase:
    """In-memory store that remembers every parameter mutation."""

    def __init__(self) -> None:
        self._rows: Dict[str, SimpleNamespace] = {}
        self._params: Dict[str, Dict[str, Any]] = {}
        self._next_workflow_id = 1
        self.deleted_parameter_ids: List[str] = []

    # -- workflow rows ----------------------------------------------------
    async def allocate_workflow_id(self) -> str:
        workflow_id = str(self._next_workflow_id)
        self._next_workflow_id += 1
        return workflow_id

    async def get_workflow(self, workflow_id: str):
        return self._rows.get(workflow_id)

    async def save_workflow(
        self,
        workflow_id: str,
        name: str,
        slug: str,
        data: Dict[str, Any],
        description: Optional[str] = None,
        context_id_aliases: Optional[Dict[str, str]] = None,
        *,
        require_existing: bool = False,
    ) -> bool:
        if require_existing and workflow_id not in self._rows:
            return False
        self._rows[workflow_id] = SimpleNamespace(
            id=workflow_id,
            name=name,
            slug=slug,
            description=description,
            data=data,
            created_at=None,
            updated_at=None,
        )
        return True

    async def list_workflow_slugs(self) -> List[Tuple[str, str]]:
        return [(r.id, r.slug) for r in self._rows.values()]

    # -- node parameters --------------------------------------------------
    async def get_node_parameters(self, node_id: str) -> Dict[str, Any]:
        return dict(self._params.get(node_id) or {})

    async def save_node_parameters(self, node_id: str, parameters: Dict[str, Any]) -> bool:
        self._params[node_id] = dict(parameters or {})
        return True

    async def delete_node_parameters(self, node_id: str) -> bool:
        self.deleted_parameter_ids.append(node_id)
        self._params.pop(node_id, None)
        return True

    # -- context archive outbox -------------------------------------------
    async def list_workflow_context_archive_outbox(self, workflow_id: str) -> List[Any]:
        return []

    async def complete_workflow_context_archive_outbox(self, *args: Any, **kwargs: Any) -> bool:
        return True


@pytest.fixture
def recording_db():
    return _ParameterRecordingDatabase()


@pytest.fixture
def patched_handlers(recording_db, tmp_path):
    """Wire the storage handlers to the recording DB.

    Mirrors the fixture in ``test_workflow_rename.py``: ``status_broadcaster``
    is stubbed at the ``sys.modules`` level because the real module imports
    ``orjson``, which the unit-test env does not install.
    """
    from services.workflow_storage import handlers

    broadcaster_spy = MagicMock()
    broadcaster_spy.broadcast_workflow_lifecycle = AsyncMock()

    stub_module = types.ModuleType("services.status_broadcaster")
    stub_module.get_status_broadcaster = lambda: broadcaster_spy
    sentinel = object()
    original = sys.modules.get("services.status_broadcaster", sentinel)
    sys.modules["services.status_broadcaster"] = stub_module

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    settings_stub = MagicMock()
    settings_stub.workspace_base_resolved = str(workspace_root)

    with patch.object(handlers, "container") as mock_container, patch.object(
        handlers, "Settings", return_value=settings_stub
    ):
        mock_container.database.return_value = recording_db
        try:
            yield SimpleNamespace(db=recording_db, handlers=handlers)
        finally:
            if original is sentinel:
                sys.modules.pop("services.status_broadcaster", None)
            else:
                sys.modules["services.status_broadcaster"] = original


def _caller(user_id: str = "owner") -> SimpleNamespace:
    """A websocket-shaped stub.

    A bare ``MagicMock`` would make owner resolution return
    ``str(<MagicMock ...>)`` — stable per instance, garbage across instances.
    """
    return SimpleNamespace(state=SimpleNamespace(user_id=user_id))


def _chat_graph(node_type: str) -> Dict[str, Any]:
    return {
        "nodes": [{"id": "chat-1", "type": node_type, "position": {"x": 0, "y": 0}, "data": {}}],
        "edges": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("node_type", CHAT_NODE_TYPES)
async def test_session_id_survives_save_then_read(patched_handlers, node_type):
    """The bug: an ordinary open destroyed a configured ``session_id``.

    A ``chatTrigger`` whose ``session_id`` is stripped silently reverts to
    matching every session, which is a routing change the user never made.
    """
    handlers = patched_handlers.handlers
    db = patched_handlers.db

    saved = await handlers.handle_save_workflow(
        {"workflow_id": "new", "name": "Chat", "data": _chat_graph(node_type)},
        _caller(),
    )
    assert saved["success"] is True
    workflow_id = saved["workflow_id"]

    node_id = saved["data"]["nodes"][0]["id"]
    await db.save_node_parameters(node_id, {"session_id": "sales"})

    read = await handlers.handle_get_workflow({"workflow_id": workflow_id}, _caller())
    assert read["success"] is True

    stored = await db.get_node_parameters(node_id)
    assert stored.get("session_id") == "sales"
    assert "reset_policy" not in stored


@pytest.mark.asyncio
async def test_read_does_not_inject_reset_policy_into_unrelated_nodes(patched_handlers):
    """``reset_policy`` belongs to the Memory tool and nothing else."""
    handlers = patched_handlers.handlers
    db = patched_handlers.db

    saved = await handlers.handle_save_workflow(
        {"workflow_id": "new", "name": "Chat", "data": _chat_graph("chatTrigger")},
        _caller(),
    )
    node_id = saved["data"]["nodes"][0]["id"]
    await db.save_node_parameters(node_id, {"session_id": "sales", "window_size": 12})

    await handlers.handle_get_workflow({"workflow_id": saved["workflow_id"]}, _caller())

    stored = await db.get_node_parameters(node_id)
    assert "reset_policy" not in stored
    # Leftover legacy keys are inert (SimpleMemoryParams declares
    # extra="ignore"), so preserving them is correct and cheaper than a
    # per-node retirement rule that cannot tell them apart from real fields.
    assert stored.get("window_size") == 12


@pytest.mark.asyncio
async def test_repeated_reads_are_stable(patched_handlers):
    """Reading twice must not drift configuration."""
    handlers = patched_handlers.handlers
    db = patched_handlers.db

    saved = await handlers.handle_save_workflow(
        {"workflow_id": "new", "name": "Chat", "data": _chat_graph("chatSend")},
        _caller(),
    )
    workflow_id = saved["workflow_id"]
    node_id = saved["data"]["nodes"][0]["id"]
    await db.save_node_parameters(node_id, {"session_id": "support"})

    await handlers.handle_get_workflow({"workflow_id": workflow_id}, _caller())
    first = await db.get_node_parameters(node_id)
    await handlers.handle_get_workflow({"workflow_id": workflow_id}, _caller())
    second = await db.get_node_parameters(node_id)

    assert first == second == {"session_id": "support"}


def live_generation(database, monkeypatch):
    """A deploy arms only a live controlled generation (1b.46)."""
    monkeypatch.setattr(database, "get_latest_workflow_control",
                        AsyncMock(return_value=SimpleNamespace(generation=1, status="running")), raising=False)


@pytest.mark.asyncio
async def test_failed_deploy_validation_does_not_rekey_parameters(recording_db, monkeypatch):
    """A rejected deploy must leave node configuration untouched.

    The rekey ran before the validation gate, so a deploy that failed
    validation had already renamed parameter rows to canonical ids and deleted
    the originals — while the stored graph kept its old ids. The next read
    looked up ids that no longer existed and the configuration was gone.
    """
    import core.container
    from services.deployment import handlers as deployment_handlers

    workflow_service = MagicMock()
    workflow_service.is_workflow_deployed = MagicMock(return_value=False)
    container_stub = MagicMock()
    container_stub.database.return_value = recording_db
    container_stub.workflow_service.return_value = workflow_service
    monkeypatch.setattr(core.container, "container", container_stub)

    broadcaster_stub = types.ModuleType("services.status_broadcaster")
    broadcaster_stub.get_status_broadcaster = lambda: MagicMock()
    monkeypatch.setitem(sys.modules, "services.status_broadcaster", broadcaster_stub)

    import services.workflow_validator as validator_module

    monkeypatch.setattr(
        validator_module,
        "validate_workflow",
        AsyncMock(return_value={"errors": [{"code": "BROKEN"}], "warnings": []}),
    )

    await recording_db.save_node_parameters("legacy-node", {"prompt": "keep me"})
    live_generation(recording_db, monkeypatch)

    result = await deployment_handlers.handle_deploy_workflow(
        {
            "workflow_id": "1",
            "nodes": [{"id": "legacy-node", "type": "console", "position": {"x": 0, "y": 0}, "data": {}}],
            "edges": [],
            "generation": 1,
        },
        _caller(),
    )

    assert result["success"] is False
    assert result["error"] == "validation_failed"
    assert recording_db.deleted_parameter_ids == []
    assert await recording_db.get_node_parameters("legacy-node") == {"prompt": "keep me"}


@pytest.mark.asyncio
async def test_restart_rearm_keeps_saved_node_secrets(recording_db, monkeypatch):
    """The boot re-arm deploys a generation from its sanitized snapshot.

    That deploy wrote the snapshot over every saved node row, so after the
    first restart a header-authenticated webhook had lost its header value and
    answered 401 to every request, and an edit saved while paused went back to
    its Start value.
    """
    import core.container
    from services.deployment import handlers as deployment_handlers

    workflow_service = SimpleNamespace(
        is_workflow_deployed=lambda _workflow_id: False,
        deploy_workflow=AsyncMock(return_value={"success": True, "triggers_setup": [], "deployment_id": "deployment"}),
    )
    container_stub = MagicMock()
    container_stub.database.return_value = recording_db
    container_stub.workflow_service.return_value = workflow_service
    monkeypatch.setattr(core.container, "container", container_stub)

    broadcaster = SimpleNamespace(
        lock_workflow=AsyncMock(return_value=True), unlock_workflow=AsyncMock(), update_node_status=AsyncMock(),
        update_workflow_status=AsyncMock(), update_deployment_status=AsyncMock(),
    )
    broadcaster_stub = types.ModuleType("services.status_broadcaster")
    broadcaster_stub.get_status_broadcaster = lambda: broadcaster
    monkeypatch.setitem(sys.modules, "services.status_broadcaster", broadcaster_stub)

    import services.workflow_validator as validator_module

    monkeypatch.setattr(
        validator_module,
        "validate_workflow",
        AsyncMock(return_value={"errors": [], "warnings": []}),
    )
    monkeypatch.setattr(deployment_handlers, "_deployment_tasks", {})

    node_id = "1:webhookTrigger:1"
    saved = {"path": "orders", "header_name": "X-Token", "header_value": "kept-secret"}
    await recording_db.save_node_parameters(node_id, saved)
    live_generation(recording_db, monkeypatch)

    result = await deployment_handlers.handle_deploy_workflow(
        {
            "workflow_id": "1",
            "nodes": [{"id": node_id, "type": "webhookTrigger", "position": {"x": 0, "y": 0}, "data": {}}],
            "edges": [],
            # The generation's snapshot, as the re-arm passes it: no secrets.
            "parameters_by_id": {node_id: {"path": "orders", "header_name": "X-Token"}},
            "generation": 1,
        },
        None,
    )
    await deployment_handlers._deployment_tasks["1"]

    assert result["success"] is True
    assert await recording_db.get_node_parameters(node_id) == saved
    assert recording_db.deleted_parameter_ids == []


@pytest.mark.asyncio
async def test_start_hands_deploy_a_snapshot_without_secrets(recording_db, monkeypatch):
    """A generation's runs carry its copy of the nodes' settings into
    Temporal, so the copy keeps no secret; each node gets its saved ones
    back when it runs (RFC-0006 1b.31). The saved rows stay as they are."""
    import core.container
    from services.deployment import handlers as deployment_handlers

    workflow_service = SimpleNamespace(
        is_workflow_deployed=lambda _workflow_id: False,
        deploy_workflow=AsyncMock(return_value={"success": True, "triggers_setup": [], "deployment_id": "deployment"}),
    )
    container_stub = MagicMock()
    container_stub.database.return_value = recording_db
    container_stub.workflow_service.return_value = workflow_service
    monkeypatch.setattr(core.container, "container", container_stub)

    broadcaster = SimpleNamespace(
        lock_workflow=AsyncMock(return_value=True), unlock_workflow=AsyncMock(), update_node_status=AsyncMock(),
        update_workflow_status=AsyncMock(), update_deployment_status=AsyncMock(),
    )
    broadcaster_stub = types.ModuleType("services.status_broadcaster")
    broadcaster_stub.get_status_broadcaster = lambda: broadcaster
    monkeypatch.setitem(sys.modules, "services.status_broadcaster", broadcaster_stub)

    import services.workflow_validator as validator_module

    monkeypatch.setattr(
        validator_module,
        "validate_workflow",
        AsyncMock(return_value={"errors": [], "warnings": []}),
    )
    monkeypatch.setattr(deployment_handlers, "_deployment_tasks", {})

    trigger_id, http_id = "1:webhookTrigger:1", "1:httpRequest:1"
    trigger = {"path": "orders", "header_name": "X-Token", "header_value": "kept-secret"}
    http = {"url": "https://api.example/orders", "headers": {"Authorization": "Bearer saved", "Accept": "application/json"}}
    await recording_db.save_node_parameters(trigger_id, trigger)
    await recording_db.save_node_parameters(http_id, http)
    live_generation(recording_db, monkeypatch)

    result = await deployment_handlers.handle_deploy_workflow(
        {
            "workflow_id": "1",
            "nodes": [
                {"id": trigger_id, "type": "webhookTrigger", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": http_id, "type": "httpRequest", "position": {"x": 200, "y": 0}, "data": {}},
            ],
            "edges": [],
            # The saved settings, as Start passes them.
            "parameters_by_id": {trigger_id: trigger, http_id: http},
            "generation": 1,
        },
        None,
    )
    await deployment_handlers._deployment_tasks["1"]

    assert result["success"] is True
    snapshot = workflow_service.deploy_workflow.await_args.kwargs["parameter_snapshot"]
    assert snapshot[trigger_id] == {"path": "orders", "header_name": "X-Token"}
    assert snapshot[http_id] == {"url": "https://api.example/orders", "headers": {"Accept": "application/json"}}
    assert await recording_db.get_node_parameters(trigger_id) == trigger
    assert await recording_db.get_node_parameters(http_id) == http


@pytest.mark.asyncio
async def test_save_preserves_description(patched_handlers):
    """Saving nulled the description because the kwarg was never passed."""
    handlers = patched_handlers.handlers
    db = patched_handlers.db

    saved = await handlers.handle_save_workflow(
        {"workflow_id": "new", "name": "Described", "data": _chat_graph("chatTrigger")},
        _caller(),
    )
    workflow_id = saved["workflow_id"]

    row = await db.get_workflow(workflow_id)
    row.description = "why this workflow exists"

    await handlers.handle_save_workflow(
        {"workflow_id": workflow_id, "name": "Described", "data": _chat_graph("chatTrigger")},
        _caller(),
    )

    assert (await db.get_workflow(workflow_id)).description == "why this workflow exists"
