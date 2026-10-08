"""Browser direct-task contracts and durable history, without external accounts."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.workspace_tasks import WorkspaceTaskRecord
from services.browser_tasks import browser_tool_for_agent
from services.workspace_task_history import admit, transition, list_tasks, safe_result


@pytest.fixture
async def history_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: WorkspaceTaskRecord.__table__.create(sync))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def get_session():
        async with sessions() as session:
            yield session
    database = SimpleNamespace(get_session=get_session,
        get_workflow=AsyncMock(return_value={"data": {"owner_id": "owner", "nodes": []}}))
    yield database
    await engine.dispose()


def record(identity="one", **overrides):
    return {"history_record": {"invocation_id": identity, "submission_id": "submission",
        "principal": "owner", "workflow_id": "wf", "node_id": "deleted-agent", "fingerprint": "hash",
        "prompt": "Read issues", **overrides}}


async def test_retry_admission_and_late_running_cannot_regress_terminal(history_db):
    assert await admit(history_db, record()) == {"status": "queued"}
    assert await admit(history_db, record()) == {"status": "queued"}
    await transition(history_db, {"invocation_id": "one", "status": "completed",
        "result": {"success": True, "result": {"response": "done", "thinking": "private", "api_key": "CANARY"}}})
    await transition(history_db, {"invocation_id": "one", "status": "running"})
    await transition(history_db, {"invocation_id": "one", "status": "failed"})
    rows = (await list_tasks(history_db, "owner", "wf"))["items"]
    assert len(rows) == 1 and rows[0]["status"] == "completed"
    assert rows[0]["result"] == {"response": "done"}
    assert await admit(history_db, record()) == {"status": "completed"}
    with pytest.raises(ValueError, match="conflict"):
        await admit(history_db, record(fingerprint="different"))


async def test_history_survives_node_deletion_and_reset_is_not_a_retention_action(history_db):
    await admit(history_db, record())
    from services.plugin import NodeUserError
    assert (await list_tasks(history_db, "owner", "wf", node_id="deleted-agent"))["items"][0]["prompt"] == "Read issues"
    with pytest.raises(NodeUserError, match="denied"):
        await list_tasks(history_db, "intruder", "wf")


async def test_pagination_ties_and_retention_preserve_active_tasks(history_db):
    now = datetime.now(timezone.utc)
    for identity in ("a", "b", "c"):
        await admit(history_db, record(identity, created_at=now))
    first = await list_tasks(history_db, "owner", "wf", limit=2)
    assert [item["invocation_id"] for item in first["items"]] == ["c", "b"]
    assert first["next_cursor"] == "b"
    assert [item["invocation_id"] for item in (await list_tasks(history_db, "owner", "wf", cursor="b", limit=2))["items"]] == ["a"]
    await admit(history_db, record("old-completed", status="completed", completed_at=now - timedelta(days=36)))
    await admit(history_db, record("old-running", status="running", created_at=now - timedelta(days=40)))
    rows = (await list_tasks(history_db, "owner", "wf"))["items"]
    assert "old-completed" not in {row["invocation_id"] for row in rows}
    assert "old-running" in {row["invocation_id"] for row in rows}


def test_safe_result_rejects_absolute_traversal_and_private_transcripts():
    result = safe_result({"response": "x" * 20000, "thinking": "CANARY", "messages": ["CANARY"],
        "artifacts": [{"path": "screenshots/safe.png", "mime_type": "image/png", "raw": "CANARY"},
                      {"path": "C:\\private.txt"}, {"path": "../private"}, {"path": "/private"}]})
    assert len(result["response"]) == 16000
    assert result["artifacts"] == [{"path": "screenshots/safe.png", "mime_type": "image/png"}]
    assert "CANARY" not in repr(result)


def test_agent_binding_requires_one_enabled_saved_browser():
    from services.plugin import NodeUserError
    graph = {"nodes": [{"id": "a", "type": "browser_agent"}, {"id": "b", "type": "browser"}],
        "edges": [{"source": "b", "target": "a", "targetHandle": "input-tools"}]}
    assert browser_tool_for_agent(graph, "a")["id"] == "b"
    graph["nodes"][1]["data"] = {"disabled": True}
    with pytest.raises(NodeUserError, match="enabled"):
        browser_tool_for_agent(graph, "a")


@pytest.mark.parametrize("extra", ["graph", "owner_url", "cdp_address", "credential_source"])
def test_task_schema_rejects_client_execution_bindings(extra):
    from pydantic import ValidationError
    from routers.browser_tasks import TaskSubmission
    body = {"workflow_id": "wf", "agent_node_id": "agent", "prompt": "Read issues",
            "submission_id": "ddc00a18-4e6e-41ca-b6ec-bca6be408bd5", extra: "untrusted"}
    with pytest.raises(ValidationError):
        TaskSubmission.model_validate(body)


def test_task_schema_requires_a_uuid_submission_identity():
    from pydantic import ValidationError
    from routers.browser_tasks import TaskSubmission
    with pytest.raises(ValidationError):
        TaskSubmission.model_validate({"workflow_id": "wf", "agent_node_id": "agent",
            "prompt": "Read issues", "submission_id": "invalid"})


async def test_task_endpoint_passes_only_saved_identity_prompt_and_normalized_uuid(monkeypatch):
    from routers.browser_tasks import TaskSubmission, submit_task
    submit = AsyncMock(return_value={"status": "accepted"})
    monkeypatch.setattr("services.node_invocations.submit", submit)
    monkeypatch.setattr("services.authz.workflow_node.resolve_workflow_node", AsyncMock(return_value=(None, {}, {"type": "browser_agent"})))
    request = SimpleNamespace(state=SimpleNamespace(user_id="tenant"))
    body = TaskSubmission.model_validate({"workflow_id": "wf", "agent_node_id": "agent", "prompt": "Read issues",
        "submission_id": "DDC00A18-4E6E-41CA-B6EC-BCA6BE408BD5"})
    assert await submit_task(request, body) == {"status": "accepted"}
    submit.assert_awaited_once_with("tenant", "wf", "agent", "Read issues", "ddc00a18-4e6e-41ca-b6ec-bca6be408bd5")


async def test_invalid_status_submission_identity_returns_safe_400(monkeypatch):
    from fastapi import HTTPException
    from routers.browser_tasks import task_status
    monkeypatch.setattr("services.node_invocations.status", AsyncMock(side_effect=ValueError("private invalid value")))
    request = SimpleNamespace(state=SimpleNamespace(user_id="tenant"))
    with pytest.raises(HTTPException) as raised:
        await task_status(request, "invalid", "wf", "agent")
    assert raised.value.status_code == 400 and raised.value.detail == "Invalid submission identity"
