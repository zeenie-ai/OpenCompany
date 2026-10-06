"""Reviewed conversion preserves existing identities and configuration."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
import nodes as _nodes  # noqa: F401 - register shipped plugins

from services.employees.apps import get_app
from services.employees.builder import BuildInputs, build_employee_graph
from services.employees.conversion import _prepare, _recognizable, plan_conversion, apply_conversion, rollback_conversion
from services.employees.hire_request import HireEmployeeRequest
from services.employees.llm import LLMChoice
from services.workflow_validator import validate_workflow
from services.graph_build import tool_edge


def fixture_graph(kind="manual"):
    request = HireEmployeeRequest.model_validate({"idempotency_key": "legacy", "name": "Maya", "role": "Assistant",
        "job": "Research customer messages and book appointments", "apps": ["WhatsApp"] if kind == "app_event" else [],
        "steps": [{"title": "Handle appointments"}], "trigger": {"kind": kind, **({"app": "WhatsApp"} if kind == "app_event" else {"every": "day"} if kind == "schedule" else {})}})
    built = build_employee_graph(BuildInputs(workflow_id="81", request=request, apps=[get_app("whatsapp")] if kind == "app_event" else [],
        llm=LLMChoice(provider="openai", model="existing-model", local=False)))
    employee = SimpleNamespace(id="old-employee", idempotency_key="legacy", role=request.role, job=request.job, description="",
        apps=built.app_ids, plan=[step.model_dump() for step in request.steps], choices=[], rules={"ask_first": True}, trigger=built.trigger,
        node_roles=built.node_roles, team_plan=None, builder_version=3, llm={"provider": "openai", "model": "existing-model"})
    parameters = {node["id"]: built.parameters.get(node["id"], {}) for node in built.nodes}
    source = {"graph": {"nodes": built.nodes, "edges": built.edges, "owner_id": "owner", "graphVersion": 2}, "parameters": parameters,
        "employee": {key: deepcopy(getattr(employee, key)) for key in ("node_roles", "team_plan", "builder_version", "job", "role", "apps", "plan", "trigger", "rules", "llm")}}
    return source, employee


def memory_bindings(graph):
    memories = {node["id"] for node in graph["nodes"] if node["type"] == "simpleMemory"}
    return {edge["target"]: edge["source"] for edge in graph["edges"]
            if edge.get("targetHandle") == "input-tools" and edge["source"] in memories}


def configure_legacy_memory(source, *, shared):
    roles = source["employee"]["node_roles"]
    worker, talk = roles["agent"], roles["talk_agent"]
    memories = memory_bindings(source["graph"])
    worker_memory, talk_memory = memories[worker], memories[talk]
    source["parameters"][worker_memory] = {"max_messages": 23}
    source["parameters"][talk_memory] = {"max_messages": 11}
    for node in source["graph"]["nodes"]:
        if node["id"] in {worker_memory, talk_memory}:
            node["data"]["historical_marker"] = node["id"]
    if shared:
        for edge in source["graph"]["edges"]:
            if edge.get("target") == talk and edge.get("targetHandle") == "input-tools" and edge["source"] == talk_memory:
                edge.update(tool_edge(worker_memory, talk).to_dict())
    return worker_memory, talk_memory


def assert_private_callable_bindings(proposed):
    nodes = {node["id"]: node for node in proposed["graph"]["nodes"]}
    owners, agent_types = {}, set()
    for edge in proposed["graph"]["edges"]:
        if edge.get("targetHandle") != "input-tools":
            continue
        owners.setdefault(edge["source"], set()).add(edge["target"])
        callable_key = (edge["target"], nodes[edge["source"]]["type"])
        assert callable_key not in agent_types, callable_key
        agent_types.add(callable_key)
    assert all(len(targets) == 1 for targets in owners.values()), owners
    for member in proposed["employee"]["team_plan"]["members"]:
        assert set(member["tools"]) == {tool for tool, targets in owners.items() if member["node_id"] in targets}


@pytest.mark.parametrize("kind", ["manual", "app_event", "schedule"])
async def test_conversion_preserves_old_nodes_accounts_models_schedules_and_context(kind):
    source, employee = fixture_graph(kind)
    assert _recognizable(source)
    before = deepcopy(source)
    proposed = _prepare(source, employee, "81", "Maya")
    assert source == before
    existing_ids = {node["id"] for node in source["graph"]["nodes"]}
    nodes = {node["id"]: node for node in proposed["graph"]["nodes"]}
    assert existing_ids <= nodes.keys()
    for node in source["graph"]["nodes"]:
        assert nodes[node["id"]] == node
        params_before = source["parameters"].get(node["id"], {})
        params_after = proposed["parameters"].get(node["id"], {})
        assert {key: value for key, value in params_before.items() if key != "system_message"} == {key: value for key, value in params_after.items() if key != "system_message"}
    assert proposed["employee"]["rules"] == source["employee"]["rules"]
    assert proposed["employee"]["trigger"] == source["employee"]["trigger"]
    assert_private_callable_bindings(proposed)
    original = source["employee"]["node_roles"]["agent"]
    talk = source["employee"]["node_roles"]["talk_agent"]
    assert proposed["employee"]["team_plan"]["talk_node_id"] == talk
    if original != talk:
        assert original in {member["node_id"] for member in proposed["employee"]["team_plan"]["members"]}
    report = await validate_workflow(nodes=proposed["graph"]["nodes"], edges=proposed["graph"]["edges"], parameters_by_id=proposed["parameters"])
    assert report["errors"] == [], report["errors"]


@pytest.mark.parametrize("kind", ["schedule", "app_event"])
@pytest.mark.parametrize("shared", [True, False])
async def test_conversion_isolates_legacy_memory_without_erasing_history(kind, shared):
    source, employee = fixture_graph(kind)
    worker_memory, talk_memory = configure_legacy_memory(source, shared=shared)
    before = deepcopy(source)
    proposed = _prepare(source, employee, "81", "Maya")
    assert source == before
    roles = source["employee"]["node_roles"]
    memories = memory_bindings(proposed["graph"])
    lead = proposed["employee"]["team_plan"]["lead_node_id"]
    assert memories[roles["talk_agent"]] == (worker_memory if shared else talk_memory)
    if shared:
        assert memories[roles["agent"]] != worker_memory
    else:
        assert memories[roles["agent"]] == worker_memory
    assert len(memories.values()) == len(set(memories.values()))
    assert proposed["parameters"][memories[roles["agent"]]] == {"max_messages": 23}
    assert proposed["parameters"][memories[lead]] == {"max_messages": 23}
    for node in source["graph"]["nodes"]:
        assert node in proposed["graph"]["nodes"]
    old_ids = {node["id"] for node in source["graph"]["nodes"]}
    connected = {endpoint for edge in proposed["graph"]["edges"] for endpoint in (edge["source"], edge["target"])}
    assert all(node["id"] in connected for node in proposed["graph"]["nodes"]
               if node["id"] not in old_ids and node["type"] in {"simpleMemory", "currentTimeTool", "writeTodos", "canvas"})
    assert_private_callable_bindings(proposed)
    report = await validate_workflow(nodes=proposed["graph"]["nodes"], edges=proposed["graph"]["edges"], parameters_by_id=proposed["parameters"])
    assert report["errors"] == [], report["errors"]


def test_conversion_keeps_legacy_shared_builder_and_ui_on_talk_only():
    source, employee = fixture_graph("app_event")
    roles = source["employee"]["node_roles"]
    tool_ids = {node["id"] for node in source["graph"]["nodes"] if node["type"] in {"agentBuilder", "chatUi"}}
    assert len(tool_ids) == 2
    source["graph"]["edges"].extend(tool_edge(tool, roles["agent"]).to_dict() for tool in tool_ids)
    proposed = _prepare(source, employee, "81", "Maya")
    for tool in tool_ids:
        assert {edge["target"] for edge in proposed["graph"]["edges"]
                if edge["source"] == tool and edge.get("targetHandle") == "input-tools"} == {roles["talk_agent"]}
    assert_private_callable_bindings(proposed)


def test_custom_main_flow_is_flagged_for_review():
    source, _ = fixture_graph()
    source["graph"]["edges"].append({"source": source["employee"]["node_roles"]["agent"], "target": "custom-output", "targetHandle": "input-main"})
    assert not _recognizable(source)


def test_v2_app_conversion_keeps_approval_valid_without_forwarding_nodes():
    from services.employees.upgrade import team_approval_topology_error
    source, employee = fixture_graph("app_event")
    proposed = _prepare(source, employee, "81", "Maya")
    plan = proposed["employee"]["team_plan"]
    assert plan["version"] == 2
    assert not any(node["type"] == "employeeJob" for node in proposed["graph"]["nodes"])
    assert team_approval_topology_error(proposed["graph"], proposed["employee"]["node_roles"],
        params=proposed["parameters"], team_plan=plan) is None
    lead = next(node for node in proposed["graph"]["nodes"] if node["id"] == plan["lead_node_id"])
    assert lead["data"]["employee_team_plan"] == plan


async def test_disabled_rollout_never_changes_or_reads_an_existing_employee():
    class Database:
        def __getattr__(self, name):
            raise AssertionError("disabled conversion must not access " + name)
    assert await plan_conversion(Database(), "81", "owner") == {"success": False, "error": "conversion_not_enabled"}
    assert await apply_conversion(Database(), "review", "owner") == {"success": False, "error": "conversion_not_enabled"}


async def save_existing(database, kind="manual", shared_memory=None):
    from models.database import Workflow, NodeParameter
    from models.employees import Employee
    source, employee = fixture_graph(kind)
    if shared_memory is not None:
        configure_legacy_memory(source, shared=shared_memory)
    async with database.reserved_session() as session:
        session.add(Workflow(id="81", name="Maya", slug="maya_1", data=deepcopy(source["graph"])))
        session.add(Employee(id=employee.id, workflow_id="81", owner_id="owner", idempotency_key="legacy", hire_state="ready", **deepcopy(source["employee"])))
        for node_id, parameters in source["parameters"].items():
            session.add(NodeParameter(node_id=node_id, parameters=parameters))
        await session.commit()
    return source


async def test_review_is_owner_isolated_and_does_not_mutate_configuration(real_database):
    source = await save_existing(real_database)
    assert await plan_conversion(real_database, "81", "other-owner", enabled=True) == {"success": False, "error": "not_found"}
    planned = await plan_conversion(real_database, "81", "owner", enabled=True)
    assert planned["success"] is True, planned
    assert planned["review_id"]
    assert planned["requires_review"] is True
    assert (await real_database.get_workflow("81")).data == source["graph"]


@pytest.mark.parametrize("shared_memory", [True, False])
async def test_apply_and_rollback_keep_configuration_identity_without_reset(real_database, monkeypatch, shared_memory):
    from services.employees import safe_apply, team_runtime, store
    source = await save_existing(real_database, "app_event", shared_memory=shared_memory)
    monkeypatch.setattr(team_runtime, "team_runtime_error", lambda: None)
    calls = []
    async def apply(_database, workflow_id, **kwargs):
        calls.append((workflow_id, kwargs))
        return {"success": True, "activation_state": "paused"}
    monkeypatch.setattr(safe_apply, "apply_saved_changes", apply)
    review = await plan_conversion(real_database, "81", "owner", enabled=True)
    applied = await apply_conversion(real_database, review["review_id"], "owner", enabled=True)
    assert applied["success"] is True, applied
    assert (await store.get_by_workflow(real_database, "81")).team_plan
    for node in source["graph"]["nodes"]:
        assert node in (await real_database.get_workflow("81")).data["nodes"]
    rolled = await rollback_conversion(real_database, review["review_id"], "owner")
    assert rolled["success"] is True, rolled
    assert (await real_database.get_workflow("81")).data == source["graph"]
    row = await store.get_by_workflow(real_database, "81")
    assert row.team_plan is None and row.node_roles == source["employee"]["node_roles"]
    for node_id, parameters in source["parameters"].items():
        assert await real_database.get_node_parameters(node_id) == parameters
    assert len(calls) == 2


async def test_later_owner_changes_require_new_review_before_applying(real_database, monkeypatch):
    from services.employees import team_runtime
    await save_existing(real_database)
    monkeypatch.setattr(team_runtime, "team_runtime_error", lambda: None)
    review = await plan_conversion(real_database, "81", "owner", enabled=True)
    await real_database.save_node_parameters("81:aiAgent:1", {"prompt": "A later edit"})
    response = await apply_conversion(real_database, review["review_id"], "owner", enabled=True)
    assert response["error"] == "review_stale"
