"""Automatic tools preserve canonical schemas and trusted workspace scope."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import nodes  # noqa: F401

from services.coding_workspace_tools import (
    build_workspace_tools, execute_workspace_tool, validate_tool_names, workspace_binding_id,
)
from services.temporal.agent_activities import _native_tool_definition, _tool_activity_policy


def canonical_builder():
    from services.ai import AIService
    ai = object.__new__(AIService)
    ai.database = SimpleNamespace(get_tool_schema=AsyncMock(return_value=None))
    return ai


@pytest.mark.asyncio
async def test_workspace_tools_use_canonical_plugin_pipeline_with_no_schema_rows():
    ai = canonical_builder()
    tools = await build_workspace_tools(ai, "coding", [], definition=_native_tool_definition, policy=_tool_activity_policy)
    assert [tool["name"] for tool in tools] == ["file_read", "file_modify", "fs_search", "shell_execute"]
    assert tools[0]["definition"]["parameters"]["required"] == ["file_path"]
    assert tools[0]["definition"]["parameters"]["properties"]["offset"]["default"] == 0
    assert tools[1]["definition"]["parameters"]["properties"]["operation"]["enum"] == ["write", "edit"]
    assert "Nushell" in tools[3]["definition"]["description"]
    assert "sandbox" in tools[3]["definition"]["description"]
    assert all(tool["virtual_workspace"] and tool["activity_name"] == "coding.workspace_tool" for tool in tools)
    assert all(tool["parameters"] == {} and not tool["tool_info"]["node_id"] for tool in tools)
    assert all(tool["tool_node_id"].startswith("coding-workspace:coding:") for tool in tools)
    ai.database.get_tool_schema.assert_not_awaited()


@pytest.mark.asyncio
async def test_connected_type_suppresses_automatic_tool_even_with_custom_name():
    connected = [{"node_type": "fileRead", "name": "read_reports", "parameters": {"offset": 12}}]
    tools = await build_workspace_tools(canonical_builder(), "coding", connected, definition=_native_tool_definition, policy=_tool_activity_policy)
    assert "fileRead" not in [tool["node_type"] for tool in tools]
    assert connected[0]["parameters"] == {"offset": 12}


@pytest.mark.asyncio
async def test_unbuildable_connected_type_does_not_grant_an_automatic_replacement():
    tools = await build_workspace_tools(canonical_builder(), "coding", [], definition=_native_tool_definition,
                                       policy=_tool_activity_policy, connected_types=["shell"])
    assert "shell" not in [tool["node_type"] for tool in tools]


@pytest.mark.asyncio
async def test_custom_connected_name_cannot_shadow_an_automatic_name():
    with pytest.raises(Exception) as error:
        await build_workspace_tools(canonical_builder(), "coding", [{"node_type": "httpRequest", "name": "file_read"}],
                                    definition=_native_tool_definition, policy=_tool_activity_policy)
    assert error.value.type == "CodingToolNameConflict"
    assert error.value.non_retryable is True


def test_connected_tool_names_are_checked_before_billing():
    with pytest.raises(Exception) as error:
        validate_tool_names([{"name": "same"}, {"name": "same"}])
    assert error.value.type == "CodingToolNameConflict"


@pytest.fixture
def workspace_runtime(monkeypatch):
    from pathlib import Path
    from uuid import uuid4
    import shutil
    from core.container import container
    from services.approvals import tool_calls
    from services.employees import permissions
    from services.chat import steps
    from services import status_broadcaster

    # Pytest's chmod(0700) temp roots are inaccessible to the restricted
    # Windows test token. Use an ordinary inherited-ACL workspace directory.
    test_root = Path.cwd().resolve()
    temporary_root = test_root / f".coding-workspace-test-{uuid4().hex}"
    assert temporary_root.resolve().parent == test_root
    temporary_root.mkdir()
    workspace = temporary_root / "managed"
    workspace.mkdir()
    database = MagicMock()
    service = SimpleNamespace(_get_workspace_dir=MagicMock(return_value=str(workspace)), _store_output=AsyncMock())
    monkeypatch.setattr(container, "workflow_service", lambda: service)
    monkeypatch.setattr(container, "database", lambda: database)
    check_access = AsyncMock()
    monkeypatch.setattr(permissions, "assert_runtime_access", check_access)
    monkeypatch.setattr(tool_calls, "check", AsyncMock(return_value=tool_calls.Checked()))
    monkeypatch.setattr(tool_calls, "redo_refusal", AsyncMock(return_value=None))
    monkeypatch.setattr(steps, "run_stopped", AsyncMock(return_value=False))
    monkeypatch.setattr(steps, "begin", lambda *args: None)
    monkeypatch.setattr(steps, "end", AsyncMock())
    broadcaster = SimpleNamespace(broadcast_agent_capability=AsyncMock(), update_node_status=AsyncMock(), update_node_output=AsyncMock())
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: broadcaster)
    try:
        yield SimpleNamespace(workspace=workspace, service=service, database=database, broadcaster=broadcaster, check_access=check_access)
    finally:
        assert temporary_root.resolve().parent == test_root
        shutil.rmtree(temporary_root)


def payload(node_type, args):
    return {"node_id": workspace_binding_id("coding", node_type), "node_type": node_type,
            "parent_node_id": "coding", "pi_harness_version": 1,
            "workflow_id": "wf", "workflow_slug": "workflow", "execution_id": "run", "user_id": "owner",
            "tool_call_id": "call", "tool_args": args,
            "node_data": {"file_path": "ignored", "content": "ignored"},
            "workspace_dir": "untrusted-payload-path"}


@pytest.mark.asyncio
async def test_virtual_write_uses_parent_scope_and_never_stores_canvas_output(workspace_runtime):
    request = payload("fileModify", {"file_path": "note.txt", "content": "native plugin write"})
    result = await execute_workspace_tool(request)
    assert "error" not in result
    assert (workspace_runtime.workspace / "note.txt").read_text() == "native plugin write"
    workspace_runtime.service._get_workspace_dir.assert_called_once_with("workflow")
    access = workspace_runtime.check_access.await_args
    assert access.args[2:4] == (workspace_binding_id("coding", "fileModify"), "fileModify")
    assert access.args[4]["parent_node_id"] == "coding"
    workspace_runtime.service._store_output.assert_not_awaited()
    workspace_runtime.broadcaster.update_node_output.assert_not_awaited()
    workspace_runtime.broadcaster.update_node_status.assert_not_awaited()
    assert [call.args[0] for call in workspace_runtime.broadcaster.broadcast_agent_capability.await_args_list] == ["coding", "coding"]


@pytest.mark.asyncio
async def test_virtual_validation_failure_has_no_filesystem_effect(workspace_runtime):
    answer = await execute_workspace_tool(payload("fileModify", {"content": "missing path"}))
    assert "Invalid parameters" in answer["error"]
    assert list(workspace_runtime.workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_workspace_paths_cannot_escape_managed_root(workspace_runtime):
    answer = await execute_workspace_tool(payload("fileModify", {"file_path": "../outside.txt", "content": "no"}))
    assert "error" in answer
    assert not (workspace_runtime.workspace.parent / "outside.txt").exists()


@pytest.mark.asyncio
async def test_stale_or_forged_virtual_scope_fails_without_any_effect(workspace_runtime):
    request = payload("fileModify", {"file_path": "note.txt", "content": "no"})
    request["parent_node_id"] = "another-agent"
    with pytest.raises(Exception) as error:
        await execute_workspace_tool(request)
    assert error.value.type == "CodingWorkspaceScopeMismatch"
    assert list(workspace_runtime.workspace.iterdir()) == []
    workspace_runtime.check_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_workspace_capability_stops_before_execution(workspace_runtime):
    from services.plugin import NodeUserError
    workspace_runtime.check_access.side_effect = NodeUserError("Permission removed", requires_user_action=True)
    answer = await execute_workspace_tool(payload("fileModify", {"file_path": "note.txt", "content": "no"}))
    assert answer["requires_user_action"] is True
    assert list(workspace_runtime.workspace.iterdir()) == []
