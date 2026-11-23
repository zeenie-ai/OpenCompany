"""App and schedule intakes keep their mission, reviewed content and original route."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from models.employees import EmployeeJob
from services.parameter_resolver import ParameterResolver
from services.temporal.agent_instance_workflow import bootstrap_instance, claim_instance
from services.temporal.trigger_listener_workflow import _build_run_graph
from tests.services.employees.test_team_plan_admission import hierarchical_inbox_assistant
from tests.temporal.test_employee_hierarchy_runtime import hierarchy


@pytest.fixture
def hierarchy_graph(request):
    trigger = {"kind": "schedule", "every": "day", "at": "08:00"} if request.param == "schedule" else None
    return hierarchical_inbox_assistant(trigger)


@pytest.mark.parametrize("hierarchy_graph", ["gmail", "schedule"], indirect=True)
async def test_triggered_mission_is_admitted_once_with_its_original_destination(real_database, hierarchy, monkeypatch):
    from core.container import container

    built, control, _ = hierarchy
    db, graph = real_database, control.graph_snapshot
    roles = built.node_roles
    trigger, chat = roles["trigger"], roles["talk_agent"]
    resolver = ParameterResolver(db, AsyncMock(return_value=None))
    monkeypatch.setattr(container, "workflow_service", lambda: SimpleNamespace(_param_resolver=resolver))
    email = built.trigger["kind"] == "app_event"
    output = ({"from": "customer@example.com", "subject": "Quarterly update", "body_text": "How many orders?",
               "body": "How many orders?", "thread_id": "thread-19"} if email else {"timestamp": "2030-02-01T08:00:00Z"})
    nodes, edges = _build_run_graph(trigger_node_id=trigger, trigger_output=output, nodes=graph["nodes"], edges=graph["edges"])
    assert roles["talk_reply"] not in {node["id"] for node in nodes}
    context = {"node_id": chat, "node_type": "ai_employee", "nodes": nodes, "edges": edges,
               "outputs": {trigger: output}, "workflow_id": "7", "execution_id": "event-19",
               "root_execution_id": "event-19", "session_id": "7", "user_id": "owner", "generation": 1,
               "data_scope_id": control.data_scope_id, "controller_workflow_id": control.controller_workflow_id,
               "agent_lifecycle_version": 1, "agent_protocol_version": 1, "parameter_snapshot": graph["parameters"]}
    started = await bootstrap_instance(context)
    assert started["applicable"] and (await bootstrap_instance(context))["instance_id"] == started["instance_id"]
    handoff = await claim_instance({"instance_id": started["instance_id"], "request_id": "handoff"})
    assert handoff["result"]["task_id"]
    async with db.get_session() as session:
        [job] = (await session.execute(select(EmployeeJob))).scalars().all()
    assert job.lead_node_id == chat
    assert job.source["outputs"][trigger] == output
    assert job.delivery["node_ids"] == built.team_plan["delivery_node_ids"]
    assert roles["job_reply"] not in job.delivery["node_ids"]
    assert not job.source.get("chat_context")
    reviewed = "There were 14 orders."
    outputs = {**job.source["outputs"], chat: {"response": reviewed}}
    if email:
        assert "How many orders?" in job.mission and "Quarterly update" in job.mission
        destination = roles["gate"]
        assert destination in job.delivery["node_ids"]
        gate = await resolver.resolve(job.source["parameters"][destination], destination, job.source["nodes"],
                                      job.source["edges"], "7", run_outputs=outputs)
        assert gate["recipient"] == "customer@example.com" and gate["draft"] == reviewed
    else:
        assert "08:00" in job.mission and "routine" in job.mission
        destination = roles["report_post"]
        assert destination in job.delivery["node_ids"]
        report = await resolver.resolve(job.source["parameters"][destination], destination, job.source["nodes"],
                                        job.source["edges"], "7", run_outputs=outputs)
        assert report["message"] == reviewed
