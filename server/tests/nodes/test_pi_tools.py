"""Pi palette nodes use SDK leaves with native scope and result contracts."""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import nodes  # noqa: F401

from services.node_registry import get_node_class
from services.plugin import NodeContext
from services import pi_builtin_tools


@pytest.fixture
def workspace():
    # Inherited ACLs keep scratch paths usable under the Windows test token.
    parent = Path.cwd().resolve()
    root = parent / f".pi-node-test-{uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        assert root.resolve().parent == parent
        shutil.rmtree(root)


def context(root: Path, kind: str) -> NodeContext:
    return NodeContext.from_legacy("pi-node", kind, {"workflow_id": "workflow", "workspace_dir": str(root)})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,tool,args", [
    ("piRead", "read", {"path": "note.txt", "offset": 2}),
    ("piWrite", "write", {"path": "note.txt", "content": "hello"}),
    ("piEdit", "edit", {"path": "note.txt", "edits": [{"oldText": "hello", "newText": "hi"}]}),
    ("piBash", "bash", {"command": "echo hello"}),
    ("piPowerShell", "powershell", {"command": "Write-Output hello"}),
    ("piGrep", "grep", {"pattern": "hello", "ignoreCase": True}),
    ("piFind", "find", {"pattern": "*.txt"}),
    ("piLs", "ls", {}),
])
async def test_nodes_dispatch_actual_sdk_identity_with_trusted_scope(monkeypatch, workspace, kind, tool, args):
    execute = AsyncMock(return_value={"content": [{"type": "text", "text": "SDK output"}], "details": {"native": True}})
    monkeypatch.setattr(pi_builtin_tools, "execute_pi_tool", execute)
    cls = get_node_class(kind)
    result = await cls().execute_as_tool({**args, "cwd": "outside", "working_directory": "outside"}, {}, context(workspace, kind))
    assert result["content"][0]["text"] == "SDK output"
    assert result["details"] == {"native": True}
    call = execute.await_args
    assert call.args[0] == tool
    assert call.args[2] == workspace
    assert "cwd" not in call.args[1] and "working_directory" not in call.args[1]
    if tool not in {"bash", "powershell"}:
        assert Path(call.args[1]["path"]).is_relative_to(workspace)
    assert cls.tool_name == f"pi_{tool}"
    assert cls.usable_as_tool
    assert cls.as_activity() is not None
    assert any(handle["name"] == "output-tool" for handle in cls._metadata_dict()["handles"])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,args", [
    ("piRead", {"path": "note.txt", "offset": 0}),
    ("piEdit", {"path": "note.txt", "oldText": "old", "newText": "new"}),
    ("piEdit", {"path": "note.txt", "edits": []}),
    ("piBash", {"command": "echo hello", "timeout": 601}),
    ("piPowerShell", {"command": "echo hello", "timeout": 0}),
])
async def test_invalid_sdk_arguments_fail_before_process_launch(monkeypatch, workspace, kind, args):
    execute = AsyncMock()
    monkeypatch.setattr(pi_builtin_tools, "execute_pi_tool", execute)
    result = await get_node_class(kind)().execute_as_tool(args, {}, context(workspace, kind))
    assert result["success"] is False
    execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../outside.txt", "~/outside.txt", "C:/outside.txt"])
async def test_paths_cannot_escape_workspace(monkeypatch, workspace, path):
    execute = AsyncMock()
    monkeypatch.setattr(pi_builtin_tools, "execute_pi_tool", execute)
    result = await get_node_class("piWrite")().execute_as_tool({"path": path, "content": "bad"}, {}, context(workspace, "piWrite"))
    assert result["success"] is False
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_workspace_never_falls_back_to_server_directory(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(pi_builtin_tools, "execute_pi_tool", execute)
    result = await get_node_class("piLs")().execute_as_tool({}, {}, NodeContext(node_id="pi", node_type="piLs"))
    assert result["success"] is False
    assert "workspace" in result["error"].lower()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_shell_failure_preserves_exit_code_and_partial_output(monkeypatch, workspace):
    execute = AsyncMock(return_value={"isError": True, "content": [{"type": "text", "text": "failed output"}],
        "structuredContent": {"output": "failed output", "exit_code": 2, "truncated": False, "wall_time_seconds": 0.1}})
    monkeypatch.setattr(pi_builtin_tools, "execute_pi_tool", execute)
    result = await get_node_class("piBash")().execute_as_tool({"command": "exit 2"}, {}, context(workspace, "piBash"))
    assert result["success"] is False
    assert result["error_type"] == "PiToolError"
    assert result["isError"] is True
    assert result["exit_code"] == 2
    assert result["stdout"] == "failed output"
    assert execute.await_args.kwargs["timeout"] == 30
    assert get_node_class("piBash").start_to_close_timeout.total_seconds() > 600


@pytest.mark.asyncio
async def test_large_shell_output_is_not_duplicated_beyond_temporal_limit(monkeypatch, workspace):
    execute = AsyncMock(return_value={"content": [{"type": "text", "text": "tail"}],
        "details": {"truncation": {"truncated": True}},
        "structuredContent": {"output": "x" * (1024 * 1024), "exit_code": 0, "truncated": True}})
    monkeypatch.setattr(pi_builtin_tools, "execute_pi_tool", execute)
    result = await get_node_class("piBash")().execute_as_tool({"command": "unused"}, {}, context(workspace, "piBash"))
    assert "error" not in result
    assert result["stdout"] == "tail"
    assert len(json.dumps(result).encode()) < 2 * 1024 * 1024


@pytest.mark.asyncio
async def test_read_image_is_persisted_as_reference_without_duplicate_base64(monkeypatch, workspace):
    from services.media import workspace as media_workspace

    encoded = base64.b64encode(b"test image bytes").decode()
    execute = AsyncMock(return_value={"content": [{"type": "text", "text": "Image read"},
        {"type": "image", "data": encoded, "mimeType": "image/png"}],
        "structuredContent": {"type": "image", "data": encoded, "mimeType": "image/png", "note": "Image read"}})
    monkeypatch.setattr(pi_builtin_tools, "execute_pi_tool", execute)
    ref = {"kind": "image", "path": "media/pi.png", "workflow_id": "workflow", "mime_type": "image/png"}
    writes = []

    def write(data, **kwargs):
        writes.append((data, kwargs))
        return SimpleNamespace(model_dump=lambda **_: ref)

    monkeypatch.setattr(media_workspace, "write_media", write)
    result = await get_node_class("piRead")().execute_as_tool({"path": "image.png"}, {}, context(workspace, "piRead"))
    assert len(writes) == 1 and writes[0][0] == b"test image bytes"
    assert result["llm_media"] == [{"ref": ref, "detail": "auto"}]
    assert result["structuredContent"]["ref"] == ref
    assert encoded not in json.dumps(result)


@pytest.mark.asyncio
async def test_pi_write_and_edit_share_legacy_path_lock(monkeypatch, workspace):
    from nodes.filesystem._backend import get_path_lock

    entered = asyncio.Event()

    async def execute(*args, **kwargs):
        entered.set()
        return {"content": [{"type": "text", "text": "done"}]}

    monkeypatch.setattr(pi_builtin_tools, "execute_pi_tool", execute)
    lock = get_path_lock(workspace / "note.txt")
    async with lock:
        task = asyncio.create_task(get_node_class("piWrite")().execute_as_tool(
            {"path": "note.txt", "content": "hello"}, {}, context(workspace, "piWrite")))
        await asyncio.sleep(0)
        assert not entered.is_set()
    result = await asyncio.wait_for(task, 2)
    assert result["content"][0]["text"] == "done"
