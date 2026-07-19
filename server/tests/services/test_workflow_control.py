import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from services.deployment.control import WorkflowControlService, serialize_control
from services.deployment.runtime_state import archive_and_reset_node_state


@pytest.fixture
async def control_database():
    module_name = "tests._real_workflow_control_database"
    spec = importlib.util.spec_from_file_location(
        module_name, Path(__file__).resolve().parents[2] / "core" / "database.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    db_path = Path.cwd() / f".workflow-control-{uuid.uuid4().hex}.db"
    database = module.Database(SimpleNamespace(
        database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        database_echo=False, database_pool_size=5, database_max_overflow=5,
    ))
    await database.startup()
    try:
        yield database
    finally:
        await database.shutdown()
        sys.modules.pop(module_name, None)
        for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            candidate.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_begin_generation_is_idempotent():
    db = AsyncMock()
    db.get_workflow_control_by_idempotency_key.side_effect = [None, None]
    db.get_latest_workflow_control.return_value = None
    db.create_workflow_control.side_effect = lambda control: control
    service = WorkflowControlService(db)

    control, created = await service.begin_generation(
        workflow_id="wf", nodes=[], edges=[], session_id="s", idempotency_key="request-1"
    )

    assert created is True
    assert control.generation == 1
    assert control.status == "starting"
    assert control.data_scope_id == control.execution_id
    assert serialize_control(control)["data_scope_id"] == control.execution_id
    assert serialize_control(control)["can_pause"] is False


@pytest.mark.asyncio
async def test_generation_atomically_creates_and_archives_isolated_data_scope(control_database):
    service = WorkflowControlService(control_database)
    control, created = await service.begin_generation(
        workflow_id="wf-scope",
        nodes=[{"id": "agent-1", "type": "aiAgent", "data": {"label": "Researcher"}}],
        edges=[], session_id="browser-session", idempotency_key="scope-1",
    )
    assert created is True
    scope = await control_database.get_workflow_run_data_scope(control.data_scope_id)
    assert scope.execution_id == control.execution_id
    assert scope.source_session_id == "browser-session"
    assert scope.node_data["agent-1"]["data"]["label"] == "Researcher"
    assert scope.runtime_data["nodes"]["agent-1"] == {
        "type": "aiAgent",
        "canvas_data": {"label": "Researcher"},
        "parameters": {},
    }
    assert scope.status == "active"

    await control_database.update_workflow_run_data_scope(
        scope.id, temporal_run_id="temporal-run-1", status="archived",
    )
    archived = await control_database.get_workflow_run_data_scope(scope.id)
    assert archived.temporal_workflow_id == control.controller_workflow_id
    assert archived.temporal_run_id == "temporal-run-1"
    assert archived.status == "archived"

    await control_database.add_chat_message("wf-scope", "user", "archived", execution_id=scope.id)
    await control_database.add_chat_message("wf-scope", "user", "new", execution_id="new-scope")
    current_chat = await control_database.get_chat_messages(
        "wf-scope", execution_id="new-scope",
    )
    assert [item["message"] for item in current_chat] == ["new"]

    await control_database.add_console_log({
        "node_id": "console-1", "label": "Console", "workflow_id": "wf-scope",
        "execution_id": scope.id, "data": {"value": "archived"}, "formatted": "archived",
    })
    await control_database.add_console_log({
        "node_id": "console-1", "label": "Console", "workflow_id": "wf-scope",
        "execution_id": "new-scope", "data": {"value": "new"}, "formatted": "new",
    })
    current_logs = await control_database.get_console_logs(
        workflow_id="wf-scope", execution_id="new-scope",
    )
    assert [item["formatted"] for item in current_logs] == ["new"]


@pytest.mark.asyncio
async def test_reset_archives_every_node_then_calls_plugin_lifecycle(monkeypatch):
    database = AsyncMock()
    database.get_node_parameters.side_effect = [
        {"configured": True},
    ]
    database.get_workflow_run_data_scope.return_value = SimpleNamespace(
        runtime_data={"existing": True},
    )
    broadcaster = SimpleNamespace(
        broadcast_node_parameters_updated=AsyncMock(),
    )
    lifecycle = AsyncMock(return_value={
        "reset": True, "parameters": {"configured": True, "runtime": "empty"},
    })
    node_class = SimpleNamespace(reset_execution_state=lifecycle)
    monkeypatch.setattr("services.node_registry.get_node_class", lambda _node_type: node_class)
    control = SimpleNamespace(
        workflow_id="wf",
        execution_id="execution-1",
        data_scope_id="scope-1",
        graph_snapshot={
            "nodes": [
                {"id": "stateful-1", "type": "statefulPlugin", "data": {"label": "Node"}},
            ],
            "edges": [],
        },
    )

    result = await archive_and_reset_node_state(control, database, broadcaster)

    assert result == {"archived_nodes": 1, "reset_nodes": ["stateful-1"]}
    archived = database.update_workflow_run_data_scope.await_args.kwargs["runtime_data"]
    assert archived["nodes"]["stateful-1"]["parameters"] == {"configured": True}
    lifecycle.assert_awaited_once()
    broadcaster.broadcast_node_parameters_updated.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_insert_rereads_idempotent_winner():
    db = AsyncMock()
    winner = AsyncMock()
    winner.generation = 1
    db.get_workflow_control_by_idempotency_key.side_effect = [None, winner]
    db.get_latest_workflow_control.return_value = None
    db.create_workflow_control.side_effect = IntegrityError("insert", {}, Exception("unique"))
    service = WorkflowControlService(db)

    control, created = await service.begin_generation(
        workflow_id="wf", nodes=[], edges=[], session_id="s", idempotency_key="same-key"
    )

    assert control is winner
    assert created is False


@pytest.mark.asyncio
async def test_transition_rejects_stale_revision():
    db = AsyncMock()
    db.transition_workflow_control.return_value = None
    service = WorkflowControlService(db)
    control = AsyncMock(id="control", revision=2)

    with pytest.raises(ValueError, match="control_revision_conflict"):
        await service.transition(
            control, expected_revision=1, from_statuses={"running"}, status="pausing"
        )


@pytest.mark.asyncio
async def test_reset_generation_is_ready_for_explicit_start():
    reset_control = AsyncMock(status="reset", generation=3)
    reset_control.workflow_id = "wf"
    reset_control.revision = 9
    reset_control.execution_id = "old-execution"
    reset_control.root_execution_id = "old-execution"
    reset_control.data_scope_id = "old-execution"
    reset_control.controller_workflow_id = "old-controller"
    reset_control.controller_run_id = "old-run"
    reset_control.created_at = None
    reset_control.updated_at = None
    reset_control.terminal_reason = "workflow_reset"
    status = serialize_control(reset_control)
    assert status["state"] == "ready"
    assert status["can_start"] is True
    assert status["can_reset"] is False

    db = AsyncMock()
    db.get_workflow_control_by_idempotency_key.return_value = None
    db.get_latest_workflow_control.return_value = reset_control
    db.create_workflow_control.side_effect = lambda control: control
    control, created = await WorkflowControlService(db).begin_generation(
        workflow_id="wf", nodes=[], edges=[], session_id="s", idempotency_key="start-after-reset"
    )
    assert created is True
    assert control.generation == 4
