from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.runtime_tool_context import bind_runtime_context, enrich_runtime_tools, prepare_native_runtime, process_cwd, runtime_facts


@pytest.mark.parametrize("version", [0, 1])
def test_omitted_cwd_keeps_per_tool_folder(version):
    root = str(Path.cwd())
    assert process_cwd(root, "2:processManager:4", version=version) == str(Path(root) / "2_processManager_4")


def test_only_v1_relative_cwd_is_root_relative():
    root = str(Path.cwd())
    assert process_cwd(root, "tool", ".", version=0) == "."
    assert process_cwd(root, "tool", ".", version=1) == root
    assert process_cwd(root, "tool", "shared", version=1) == str(Path(root) / "shared")
    assert process_cwd(root, "tool", root, version=1) == root


def test_facts_distinguish_runtime_and_registry_scope():
    facts = runtime_facts(str(Path.cwd()), "tool")
    assert facts["backend_python"]
    assert facts["list_includes_os_listeners"] is False
    assert facts["localhost_preview_requires_same_browser_host"] is True
    assert "worker" in facts["process_list_scope"]


def test_post_binding_preserves_custom_schema_name_and_description():
    from services.agent_runtime import AgentToolSpec
    from services.llm.protocol import ToolDef
    schema = {"type": "object", "properties": {"operation": {"type": "string"}}}
    tool = AgentToolSpec(ToolDef("custom_process", "My saved description", schema))
    info = {"node_type": "processManager", "node_id": "tool"}
    assert bind_runtime_context(info, {}, "old") is info
    assert enrich_runtime_tools([(tool, {})], info)[0][0].description == "My saved description"
    info = bind_runtime_context(info, {"runtime_behavior_version": 1, "workspace_dir": str(Path.cwd())}, "new")
    config = {}
    enrich_runtime_tools([(tool, config)], info)
    assert tool.name == "custom_process" and tool.parameters == schema
    assert tool.description.startswith("My saved description\n\nTrusted runtime context:")
    assert config["runtime_behavior_version"] == 1


@pytest.mark.asyncio
async def test_native_target_cannot_inherit_parent_or_model_policy():
    database = SimpleNamespace(get_node_parameters=AsyncMock(return_value={}))
    parameters, context, _, _ = await prepare_native_runtime(database, "old", {"runtime_behavior_version": 1},
        {"runtime_behavior_version": 1}, [], [])
    assert "runtime_behavior_version" not in parameters
    assert "runtime_behavior_version" not in context


@pytest.mark.asyncio
async def test_captured_legacy_target_ignores_live_upgrade():
    database = SimpleNamespace(get_node_parameters=AsyncMock(return_value={"runtime_behavior_version": 1}))
    _, context, _, _ = await prepare_native_runtime(database, "old", {},
        {"parameter_snapshot": {"old": {}}}, [], [])
    # Only secrets come back from the saved row, never a later upgrade.
    assert "runtime_behavior_version" not in context


@pytest.mark.asyncio
@pytest.mark.parametrize("cwd", ["", ".", "shared"])
async def test_v1_process_dispatch_uses_effective_root(monkeypatch, cwd):
    from nodes.utility.process_manager import ProcessManagerNode, ProcessManagerParams
    from services.plugin import NodeContext
    service = SimpleNamespace(start=AsyncMock(return_value={"success": True}))
    monkeypatch.setattr("services.process_service.get_process_service", lambda: service)
    root = str(Path.cwd())
    context = NodeContext(node_id="2:processManager:4", node_type="processManager", workflow_id="wf",
        workspace_dir=root, raw={"runtime_behavior_version": 1})
    await ProcessManagerNode().dispatch(context, ProcessManagerParams(operation="start", name="web", command="python -V", cwd=cwd))
    assert service.start.call_args.kwargs["working_directory"] == process_cwd(root, context.node_id, cwd, version=1)


@pytest.mark.asyncio
async def test_v1_port_error_survives_plugin_tool_boundary(monkeypatch):
    from nodes.utility.process_manager import ProcessManagerNode
    from services.plugin import NodeContext
    service = SimpleNamespace(start=AsyncMock(return_value={"success": False, "error": "Port occupied", "code": "PORT_IN_USE", "ports": [8080], "owners": {8080: [123]}}),
        classify_port_owners=lambda owners, workflow: {"8080": [{"classification": "unmanaged_or_unknown", "pid": 123}]})
    monkeypatch.setattr("services.process_service.get_process_service", lambda: service)
    context = NodeContext(node_id="tool", node_type="processManager", workflow_id="wf", workspace_dir=str(Path.cwd()), raw={"runtime_behavior_version": 1})
    result = await ProcessManagerNode().execute_as_tool({"operation": "start", "name": "web", "command": "python -V", "ports": [8080]}, {}, context)
    assert result["code"] == "PORT_IN_USE"
    assert result["ports"] == [8080]
    assert result["owner_scope"]["8080"][0]["classification"] == "unmanaged_or_unknown"
