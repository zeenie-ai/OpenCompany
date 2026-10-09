"""Runtime binding identity and delegation eligibility stay consistent."""
from types import SimpleNamespace
import pytest
from services.agent_bindings import is_runtime_tool, unique_node_bindings
from services.employees.team_runtime import team_runtime_error
from services.employees.upgrade import team_approval_topology_error
from services.approvals.contract import approved_edge_condition

@pytest.mark.parametrize("kind,usable,editor,expected", [
    ("agent", False, False, True), ("tool", False, False, True),
    ("action", True, False, True), ("model", True, False, False),
    ("tool", False, True, False), ("trigger", False, False, False),
])
def test_runtime_eligibility(kind, usable, editor, expected):
    cls = SimpleNamespace(component_kind=kind, usable_as_tool=usable, ui_hints={"isMasterSkillEditor": editor})
    assert is_runtime_tool(cls) is expected


def test_physical_nodes_dedupe_before_names_but_distinct_collisions_remain():
    tools = [{"node_id": "a", "name": "search"}, {"node_id": "a", "name": "search"}, {"node_id": "b", "name": "search"}]
    assert unique_node_bindings(tools) == [tools[0], tools[2]]
    assert unique_node_bindings(tools, bound=["a"]) == [tools[2]]


def test_temporal_payload_identity():
    tools = [{"tool_node_id": "a", "name": "search"}, {"tool_node_id": "a", "name": "search"}, {"tool_node_id": "b", "name": "search"}]
    assert unique_node_bindings(tools, id_key="tool_node_id", bound=["a"]) == [tools[2]]


def test_the_tools_of_one_node_are_told_apart_by_binding_key():
    tools = [
        {"tool_node_id": "a", "binding_key": "a:lookup"},
        {"tool_node_id": "a", "binding_key": "a:refund"},
        {"tool_node_id": "a", "binding_key": "a:lookup"},
    ]
    assert unique_node_bindings(tools, id_key="binding_key") == tools[:2]
    assert unique_node_bindings(tools, id_key="binding_key", bound=["a:lookup"]) == [tools[1]]


def _bindings(*keys):
    from services.plugin.tool import ToolBinding

    async def bindings(tool_info):
        if tool_info["node_type"] != "mcpConnector":
            return None
        schema = {"type": "object", "properties": {"order": {"type": "string"}}}
        return [ToolBinding(key=key, name=f"orders__{key}", description=f"{key} an order.", schema=schema, parameters={"mcp_tool": key}) for key in keys]

    return bindings


async def test_a_node_that_gives_several_tools_builds_one_per_binding(monkeypatch):
    from services.agent_bindings import node_tools
    from services.plugin import tool as tool_module

    monkeypatch.setattr(tool_module, "node_tool_bindings", _bindings("lookup", "refund"))
    built = await node_tools(
        SimpleNamespace(), {"node_id": "n1", "node_type": "mcpConnector", "parameters": {"mcp_connector": "mcp:orders"}, "label": "Orders"}
    )
    # Each runs with its own settings over the node's.
    assert [(tool.name, config["binding_key"], config["parameters"]) for tool, config in built] == [
        ("orders__lookup", "n1:lookup", {"mcp_connector": "mcp:orders", "mcp_tool": "lookup"}),
        ("orders__refund", "n1:refund", {"mcp_connector": "mcp:orders", "mcp_tool": "refund"}),
    ]
    assert built[0][0].definition.parameters["properties"] == {"order": {"type": "string"}}


async def test_cli_agents_leave_out_a_node_that_gives_several_tools(monkeypatch):
    from services.cli_agent.service import AICliService
    from services.plugin import tool as tool_module

    monkeypatch.setattr(tool_module, "node_tool_bindings", _bindings("lookup"))
    built = []

    class AI:
        async def _build_tool_from_node(self, tool_info):
            built.append(tool_info["node_type"])
            return SimpleNamespace(name="calculator", description="", parameters={}, args_schema=None), {"node_id": tool_info["node_id"]}

    surface = await AICliService._canonical_tool_surface(
        [{"node_type": "mcpConnector", "node_id": "m"}, {"node_type": "calculatorTool", "node_id": "c"}], ai_service=AI()
    )
    assert built == ["calculatorTool"] and [entry["node_type"] for entry in surface] == ["calculatorTool"]


def test_team_readiness_requires_both_execution_engines_and_connection():
    settings = SimpleNamespace(temporal_enabled=True, temporal_agent_workflow_enabled=True)
    client = SimpleNamespace(is_connected=True)
    assert team_runtime_error(settings=settings, temporal_client=client) is None
    settings.temporal_agent_workflow_enabled = False
    assert team_runtime_error(settings=settings, temporal_client=client) == "team_agent_workflow_required"
    settings.temporal_agent_workflow_enabled = True
    client.is_connected = False
    assert team_runtime_error(settings=settings, temporal_client=client) == "team_runtime_not_ready"


def test_external_team_reply_fails_closed_on_gate_bypass():
    roles = {"agent": "lead", "gate": "gate", "reply": "reply"}
    graph = {"nodes": [{"id": "lead", "type": "ai_employee"}, {"id": "gate", "type": "approvalGate"}, {"id": "reply", "type": "googleGmailSend"}],
             "edges": [{"source": "lead", "target": "gate"}, {"source": "gate", "target": "reply", "data": {"condition": approved_edge_condition()}}]}
    assert team_approval_topology_error(graph, roles) is None
    graph["edges"].append({"source": "lead", "target": "reply"})
    assert team_approval_topology_error(graph, roles) == "team_approval_topology_invalid"


def test_reviewed_job_delivery_boundary_accepts_gate_and_rejects_ack_bypass():
    roles = {"agent": "lead", "gate": "gate", "reply": "reply", "job_delivery": "deliver"}
    graph = {"nodes": [{"id": "lead", "type": "ai_employee"}, {"id": "gate", "type": "approvalGate", "data": {"label": "Approval"}}, {"id": "reply", "type": "whatsappSend"}, {"id": "deliver", "type": "employeeJob"}],
             "edges": [{"source": "lead", "target": "deliver"}, {"source": "gate", "target": "reply", "data": {"condition": approved_edge_condition()}}]}
    from services.graph_build import ref, template_key
    from services.employees.builder import _REPLY_FIELDS
    recipient_field = _REPLY_FIELDS["whatsappSend"][0]
    params = {"deliver": {"operation": "deliver", "lead_node_id": "lead", "delivery_node_ids": ["gate", "reply"]}, "reply": {recipient_field: ref(template_key(graph["nodes"][1]), "recipient")}}
    assert team_approval_topology_error(graph, roles, params=params) is None
    graph["edges"].append({"source": "lead", "target": "gate"})
    assert team_approval_topology_error(graph, roles, params=params) == "team_approval_topology_invalid"


async def test_admitted_job_mission_is_server_owned_and_does_not_replace_child_or_review():
    from contextlib import asynccontextmanager
    from services.employees.team_runtime import employee_job_mission
    job = SimpleNamespace(workflow_id="workflow", lead_node_id="lead", origin_execution_id="job:1", mission="Original owner mission")
    class Session:
        async def get(self, model, identifier):
            return job
    class Database:
        @asynccontextmanager
        async def get_session(self):
            yield Session()
    context = {"employee_job_id": "job:1", "workflow_id": "workflow", "node_id": "lead", "execution_id": "job:1", "parameters": {"prompt": "untrusted override"}}
    assert await employee_job_mission(Database(), context) == "Original owner mission"
    assert await employee_job_mission(Database(), {**context, "node_id": "child"}) is None
    assert await employee_job_mission(Database(), {**context, "execution_id": "review:1"}) is None
    with pytest.raises(ValueError, match="does not belong"):
        await employee_job_mission(Database(), {**context, "workflow_id": "other"})


def test_rebind_edges_do_not_leak_specialist_tools_onto_the_lead():
    from services.agent_bindings import rebind_allowed
    operation = {"type": "add_node", "minted_id": "search"}
    edges = [{"source": "search", "target": "specialist", "targetHandle": "input-tools"}]
    assert not rebind_allowed(operation, "lead", edges=edges)
    assert rebind_allowed(operation, "specialist", edges=edges)
    assert not rebind_allowed(operation, "lead", edges=[{"source": "search", "target": "lead", "targetHandle": "input-main"}])


async def test_captured_parameters_stay_frozen_while_new_nodes_read_latest():
    from services.agent_bindings import ParameterSnapshotDatabase
    class Database:
        async def get_node_parameters(self, node_id):
            return {"recipient": "changed"}
    snapshot = ParameterSnapshotDatabase(Database(), {"existing": {"recipient": "original"}})
    assert await snapshot.get_node_parameters("existing") == {"recipient": "original"}
    assert await snapshot.get_node_parameters("new") == {"recipient": "changed"}


async def test_native_tool_dispatch_preserves_server_execution_scope(monkeypatch):
    from services.handlers.tools import _dispatch_tool
    from services import node_registry
    from services.approvals import tool_calls
    captured = {}
    class Tool:
        credentials = ()
        async def execute_as_tool(self, args, params, context):
            captured.update(context.raw)
            return {"success": True}
    async def check(*args):
        return SimpleNamespace(result=None, node_data=None, tool_args=None)
    monkeypatch.setattr(node_registry, "get_node_class", lambda name: Tool)
    monkeypatch.setattr(tool_calls, "check_in_process", check)
    config = {"node_type": "snapshotTest", "node_id": "tool", "run_scope": "owner_chat",
              "generation": 4, "employee_job_id": "job", "parameter_snapshot": {"tool": {"recipient": "original"}}}
    await _dispatch_tool("run", {"run_scope": "forged", "parameter_snapshot": {}}, config)
    for key in ("run_scope", "generation", "employee_job_id", "parameter_snapshot"):
        assert captured[key] == config[key]


def test_refresh_graph_preserves_old_nodes_and_adopts_only_admitted_edges():
    from services.agent_bindings import extend_runtime_graph
    old = {"nodes": [{"id": "lead", "type": "ai_employee", "data": {"label": "Original"}}], "edges": []}
    saved = {"nodes": [{"id": "lead", "type": "ai_employee", "data": {"label": "Changed"}}, {"id": "child", "type": "aiAgent"}, {"id": "unactivated", "type": "telegramSend"}],
             "edges": [{"source": "child", "target": "lead", "targetHandle": "input-teammates"}, {"source": "unactivated", "target": "lead", "targetHandle": "input-tools"}]}
    operations = [{"type": "add_node", "minted_id": "child", "node_type": "aiAgent"}, {"type": "add_edge", "source": "child", "target": "lead", "target_handle": "input-teammates"}]
    refreshed = extend_runtime_graph(old, saved, operations)
    assert refreshed["nodes"][0]["data"]["label"] == "Original"
    assert [node["id"] for node in refreshed["nodes"]] == ["lead", "child"]
    assert len(refreshed["edges"]) == 1
