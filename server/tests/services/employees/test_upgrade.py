"""An employee an older builder made, brought up to the live Ask first rule
(services/employees/upgrade.py): its ungated reply gets a gate (the edge
from the agent goes to the gate, the reply reads the gate), the tools asking
first left out come in for the worker and its talk agent, a read-only
browser is saved whole, and the talk agent gets its talk tools. A graph
already up to date gets nothing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import nodes  # noqa: F401 - the plugin registry, for the policy's lookups
from services.approvals.contract import approved_edge_condition, send_condition
from services.employees.apps import get_app
from services.employees.upgrade import plan_upgrade
from services.graph_build import add_to_graph

EVERYTHING = lambda _node_type: True  # noqa: E731
EMPLOYEE = SimpleNamespace(rules={"ask_first": False, "items": []})


def _node(node_id, node_type, label, x=0, y=0):
    return {"id": node_id, "type": node_type, "position": {"x": x, "y": y}, "data": {"label": label}}


def _edge(source, target, handle="input-main", source_handle="output-main", condition=None):
    edge = {"id": f"e-{source}-{source_handle}-{target}-{handle}", "source": source, "sourceHandle": source_handle, "target": target, "targetHandle": handle}
    if condition:
        edge["data"] = {"condition": condition}
    return edge


GRAPH = {
    "nodes": [
        _node("7:whatsappReceive:1", "whatsappReceive", "WhatsApp"),
        _node("7:aiAgent:1", "aiAgent", "Maya", 360),
        _node("7:whatsappSend:1", "whatsappSend", "Reply on WhatsApp", 720),
        _node("7:browser:1", "browser", "Browser", 120, 440),
        _node("7:chatTrigger:1", "chatTrigger", "Talk", 0, 700),
        _node("7:aiAgent:2", "aiAgent", "Talk with Maya", 360, 700),
        _node("7:chatReply:1", "chatReply", "Reply in Chat", 720, 700),
    ],
    "edges": [
        _edge("7:whatsappReceive:1", "7:aiAgent:1"),
        _edge("7:aiAgent:1", "7:whatsappSend:1", condition=send_condition()),
        _edge("7:whatsappReceive:1", "7:whatsappSend:1"),
        _edge("7:browser:1", "7:aiAgent:1", "input-tools", "output-tool"),
        _edge("7:browser:1", "7:aiAgent:2", "input-tools", "output-tool"),
        _edge("7:chatTrigger:1", "7:aiAgent:2"),
        _edge("7:aiAgent:2", "7:chatReply:1", condition=send_condition()),
    ],
}
ROLES = {
    "trigger": "7:whatsappReceive:1",
    "agent": "7:aiAgent:1",
    "reply": "7:whatsappSend:1",
    "browser": "7:browser:1",
    "talk_trigger": "7:chatTrigger:1",
    "talk_agent": "7:aiAgent:2",
    "talk_reply": "7:chatReply:1",
}
PARAMS = {
    "7:whatsappSend:1": {"recipient_type": "phone", "phone": "{{whatsapp.sender_phone}}", "message_type": "text", "message": "{{maya.response}}"},
    "7:browser:1": {"interaction": "read_only"},
}


def _plan(graph=GRAPH, roles=ROLES, params=PARAMS):
    return plan_upgrade(
        graph,
        employee=EMPLOYEE,
        roles=roles,
        apps=[get_app("whatsapp"), get_app("web"), get_app("google_calendar")],
        params=params,
        owner_values={},
        allowed=EVERYTHING,
    )


def test_an_ungated_reply_gets_a_gate_and_the_rest_comes_in():
    upgrade = _plan()
    placed = add_to_graph("7", GRAPH, upgrade.additions)
    by_ref = {ref: node_id for ref, node_id in placed.node_ids.items()}
    gate = by_ref["gate"]
    assert placed.parameters[gate] == {
        "channel": "WhatsApp",
        "recipient": "{{whatsapp.sender_phone}}",
        "recipient_label": "{{whatsapp.push_name}}",
        "draft": "{{maya.response}}",
        "context_excerpt": "{{whatsapp.text}}",
        "max_length": 4096,
    }
    # The agent's edge to the reply now goes to the gate.
    assert [edge["id"] for edge in placed.removed_edges] == ["e-7:aiAgent:1-output-main-7:whatsappSend:1-input-main"]
    wiring = {(edge["source"], edge["target"]): (edge.get("data") or {}).get("condition") for edge in placed.edges}
    assert wiring[("7:aiAgent:1", gate)] == send_condition()
    assert ("7:whatsappReceive:1", gate) in wiring
    assert wiring[(gate, "7:whatsappSend:1")] == approved_edge_condition()
    assert placed.merges["7:whatsappSend:1"] == {"phone": "{{checkbeforesending.recipient}}", "message": "{{checkbeforesending.text}}"}
    assert upgrade.roles["gate"] == "gate"

    # The calendar tool asking first left out, for the worker and its talk agent.
    calendar = next(node_id for node_id, params in placed.parameters.items() if params.get("calendar_id") == "primary")
    assert {target for source, target in wiring if source == calendar} == {"7:aiAgent:1", "7:aiAgent:2"}
    # The browser is saved whole.
    assert placed.merges["7:browser:1"] == {"interaction": "full"}
    # The talk agent alone gets generated UI and sending on WhatsApp.
    talk_only = {node["type"] for node in placed.nodes if {t for s, t in wiring if s == node["id"]} == {"7:aiAgent:2"}}
    assert talk_only == {"chatUi", "whatsappSend"}


def test_an_up_to_date_graph_gets_nothing_more():
    upgrade = _plan()
    placed = add_to_graph("7", GRAPH, upgrade.additions)
    roles = {**ROLES, "gate": placed.node_ids["gate"]}
    params = {**PARAMS, "7:browser:1": {"interaction": "full"}}
    again = _plan(placed.graph, roles, params)
    assert not again.additions, again.additions


@pytest.mark.parametrize("existing", ["legacy", "connected", "other_lead", "shared", "shared_skills"])
async def test_existing_team_connects_its_assigned_lead_once(real_database, monkeypatch, existing):
    import asyncio
    from unittest.mock import AsyncMock

    from models.database import NodeParameter, Workflow
    from models.employees import Employee
    from services.employees.store import get_by_workflow
    from services.employees.summaries import _team_setup_pending
    from services.employee_team_connection import EMPLOYEE_TEAM_RESOURCE_FIELDS, EmployeeTeamValidationError, employee_skill_parameters, employee_team_edge
    from services.employees.upgrade import upgrade_employee
    from services.graph_build import skill_edge, tool_edge

    lead, contact, spare = "7:ai_employee:1", "7:aiAgent:1", "7:ai_employee:2"
    specialist = "7:tool_agent:1"
    teammate = _edge(specialist, lead, "input-teammates", "output-top")
    graph = {"nodes": [_node(lead, "ai_employee", "Work"), _node(contact, "aiAgent", "Maya"),
                       _node(spare, "ai_employee", "Other team"), _node(specialist, "tool_agent", "Operations")],
             "edges": [teammate]}
    plan = {"version": 2, "talk_node_id": contact, "lead_node_id": lead,
            "members": [{"node_id": specialist, "node_type": "tool_agent", "tools": []}]}
    existing_manager = "7:taskManager:4"
    contact_manager = "7:taskManager:5"
    original_skill, contact_skill = "7:masterSkill:2", "7:masterSkill:3"
    lead_params = {"skill_folder": "assistant", "skills_config": {"owner-edited": {"isCustomized": True, "instructions": "Keep {{taskmanager.output}} verbatim."}}}
    talk_params = {"skill_folder": "assistant", "skills_config": {"custom-chat": {"enabled": False, "instructions": "My exact words"}}}
    route = employee_team_edge(lead, contact).to_dict()
    if existing != "legacy":
        plan["talk_connection_version"] = 1
        graph["edges"].append(route)
        graph["nodes"].append(_node(existing_manager, "taskManager", "My assignments"))
        owner = spare if existing == "other_lead" else lead
        graph["edges"].append(tool_edge(existing_manager, owner).to_dict())
        if existing == "connected":
            # An intermediate builder already recorded the lead manager.
            plan["task_manager_node_id"] = existing_manager
            graph["nodes"].append(_node(contact_manager, "taskManager", "Chat requests"))
            graph["edges"].append(tool_edge(contact_manager, contact).to_dict())
        if existing in {"connected", "shared_skills"}:
            graph["nodes"].append(_node(original_skill, "masterSkill", "My role instructions"))
            graph["edges"].append(skill_edge(original_skill, lead).to_dict())
            if existing == "connected":
                graph["nodes"].append(_node(contact_skill, "masterSkill", "Private chat skills"))
            graph["edges"].append(skill_edge(contact_skill if existing == "connected" else original_skill, contact).to_dict())
        if existing == "shared":
            graph["edges"].append(tool_edge(existing_manager, spare).to_dict())
    async with real_database.reserved_session() as session:
        session.add(Workflow(id="7", name="Maya", slug="maya", data=graph))
        session.add(Employee(id="hire", workflow_id="7", owner_id="owner", builder_version=6 if existing == "legacy" else 7,
                             team_plan=plan, node_roles={"agent": lead, "talk_agent": contact}))
        if existing != "legacy":
            session.add(NodeParameter(node_id=existing_manager, parameters={"description": "Owner's instructions"}))
        if existing in {"connected", "shared_skills"}:
            session.add(NodeParameter(node_id=original_skill, parameters=lead_params))
        if existing == "connected":
            session.add(NodeParameter(node_id=contact_skill, parameters=talk_params))
        await session.commit()
    monkeypatch.setattr("services.workflow_ops.broadcast_workflow_ops", AsyncMock())

    if existing == "shared":
        with pytest.raises(EmployeeTeamValidationError):
            await upgrade_employee(real_database, None, "7")
        assert (await real_database.get_workflow("7")).data == graph
        assert (await get_by_workflow(real_database, "7")).team_plan == plan
        return

    results = await asyncio.gather(*(upgrade_employee(real_database, None, "7") for _ in range(2)))
    assert sorted(results) == [False, True]
    assert not await upgrade_employee(real_database, None, "7")
    saved = await real_database.get_workflow("7")
    employee = await get_by_workflow(real_database, "7")
    manager = employee.node_roles["task_manager"]
    roles = employee.node_roles
    assigned = {field: roles[role] for role, field in EMPLOYEE_TEAM_RESOURCE_FIELDS.items()}
    assert len(set(assigned.values())) == 4
    assert employee.team_plan == {**plan, "talk_connection_version": 1, **assigned}
    assert saved.data["edges"].count(route) == 1
    assert saved.data["edges"].count(tool_edge(manager, lead).to_dict()) == 1
    assert saved.data["edges"].count(tool_edge(roles["talk_task_manager"], contact).to_dict()) == 1
    assert saved.data["edges"].count(skill_edge(roles["lead_skills"], lead).to_dict()) == 1
    assert saved.data["edges"].count(skill_edge(roles["talk_skills"], contact).to_dict()) == 1
    assert len([n for n in saved.data["nodes"] if n["type"] == "taskManager"]) == (3 if existing == "other_lead" else 2)
    assert len([n for n in saved.data["nodes"] if n["type"] == "masterSkill"]) == 2
    if existing == "connected":
        assert manager == existing_manager and saved.data == graph
        assert roles["lead_skills"] == original_skill and roles["talk_skills"] == contact_skill
        assert await real_database.get_node_parameters(contact_skill) == talk_params
    if existing == "shared_skills":
        assert roles["lead_skills"] == original_skill and roles["talk_skills"] != original_skill
        assert await real_database.get_node_parameters(roles["talk_skills"]) == lead_params
    if existing in {"shared_skills", "connected"}:
        assert await real_database.get_node_parameters(original_skill) == lead_params
    else:
        for role in ("lead", "talk"):
            assert await real_database.get_node_parameters(roles[role + "_skills"]) == employee_skill_parameters(role)
    if existing != "legacy":
        assert await real_database.get_node_parameters(existing_manager) == {"description": "Owner's instructions"}
    if existing == "other_lead":
        assert manager != existing_manager
        assert tool_edge(existing_manager, spare).to_dict() in saved.data["edges"]
    live_plan = dict(plan)
    control = SimpleNamespace(status="running", graph_snapshot={"nodes": [{"id": lead, "data": {"employee_team_plan": live_plan}}]})
    assert _team_setup_pending(employee, control)
    live_plan.update(employee.team_plan)
    assert not _team_setup_pending(employee, control)
