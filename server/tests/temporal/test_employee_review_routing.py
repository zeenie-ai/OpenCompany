"""Review events are private to their assigning employee on a v3 graph."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio import workflow

from services.task_trigger_routing import task_trigger_binding, task_trigger_issues
from services.temporal.trigger_listener_workflow import TriggerListenerWorkflow, _build_run_graph
from services.temporal.workflow import MachinaWorkflow
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow


def graph():
    nodes = [
        {"id": "chat", "type": "ai_employee", "data": {"employee_team_plan": {"version": 3, "talk_node_id": "chat"}}},
        {"id": "lead", "type": "orchestrator_agent"},
        {"id": "worker", "type": "coding_agent"},
        {"id": "chatReview", "type": "taskTrigger", "data": {"label": "Review"}},
        {"id": "leadReview", "type": "taskTrigger", "data": {"label": "Review"}},
        {"id": "observer", "type": "taskTrigger"},
        {"id": "chatReply", "type": "chatReply"},
    ]
    edges = [
        {"source": "chatReview", "target": "chat", "targetHandle": "input-task"},
        {"source": "leadReview", "target": "lead", "targetHandle": "input-task"},
        {"source": "worker", "target": "lead", "targetHandle": "input-teammates"},
        {"source": "lead", "target": "chat", "targetHandle": "input-teammates"},
        {"source": "chat", "target": "chatReply", "targetHandle": "input-main"},
    ]
    return nodes, edges


def test_review_run_cannot_execute_the_assigners_own_manager_or_chat_sink():
    nodes, edges = graph()
    for trigger, employee in (("leadReview", "lead"), ("chatReview", "chat")):
        selected, links = _build_run_graph(trigger_node_id=trigger, trigger_output={"task_id": "t"},
                                           nodes=nodes, edges=edges)
        executable, _ = MachinaWorkflow()._filter_executable_graph(selected, links)
        assert {node["id"] for node in executable} == {trigger, employee}
        assert "worker" in {node["id"] for node in selected}


@pytest.mark.parametrize(("source", "replies"), [("chatTrigger", True), ("cronScheduler", False), ("gmailReceive", False)])
def test_only_chat_intake_enters_the_conversation_reply_branch(source, replies):
    nodes, edges = graph()
    nodes.append({"id": "start", "type": source})
    edges.append({"source": "start", "target": "chat", "targetHandle": "input-main"})
    selected, links = _build_run_graph(trigger_node_id="start", trigger_output={"request": "hello"},
                                       nodes=nodes, edges=edges)
    executable, _ = MachinaWorkflow()._filter_executable_graph(selected, links)
    expected = {"start", "chat"} | ({"chatReply"} if replies else set())
    assert {n["id"] for n in executable} == expected


def test_review_validation_catches_overlap_owner_conflict_and_multiple_owners():
    nodes, edges = graph()
    edges.append({"source": "observer", "target": "lead", "targetHandle": "input-task"})
    filters = {"leadReview": {"status_filter": "completed"}, "observer": {"status_filter": "error"}}
    assert not task_trigger_issues(nodes, edges, filters)
    assert task_trigger_issues(nodes, edges, {})[0]["code"] == "OVERLAPPING_TASK_REVIEW_TRIGGERS"
    filters["observer"]["parent_node_id"] = "chat"
    assert task_trigger_issues(nodes, edges, filters)[0]["code"] == "INVALID_TASK_REVIEW_OWNER"
    edges.append({"source": "leadReview", "target": "chat", "targetHandle": "input-task"})
    assert task_trigger_issues(nodes, edges, filters)[0]["code"] == "INVALID_TASK_REVIEW_OWNER"


@pytest.mark.asyncio
async def test_owner_is_checked_before_admission_and_queued_graph_survives_apply(monkeypatch):
    nodes, edges = graph()
    controller = WorkflowControlWorkflow()
    monkeypatch.setattr(controller, "_maybe_request_rollover", lambda: None)
    controller._event_accumulator_v1 = True
    for trigger in ("chatReview", "leadReview", "observer"):
        args = {"nodes": nodes, "edges": edges, "parameter_snapshot": {}, "node_type": "taskTrigger",
                "trigger_node_id": trigger, "task_trigger_identity": f"wf:{trigger}",
                **task_trigger_binding(trigger, nodes, edges, {})}
        controller._triggers[trigger] = {"workflow_type": "TriggerListenerWorkflow",
            "event_type": "completion", "trigger_node_id": trigger, "listener_args": args}
    event = {"id": "task:attempt:0:revision:1", "type": "completion", "data": {"parent_node_id": "lead"}}
    await controller.on_event(event)
    await controller.on_event(event)
    assert [key for key, _ in controller._events] == ["leadReview", "observer"]

    controller._state = "paused"
    new_edges = [{**edge, "target": "chat"} if edge["source"] == "leadReview" else edge for edge in edges]
    parameters = {"chatReview": {"status_filter": "error"}, "leadReview": {"status_filter": "completed"}}
    await controller.replace_graph({"nodes": nodes, "edges": new_edges, "parameters": parameters})
    assert controller._triggers["leadReview"]["listener_args"]["task_review_owner"] == "chat"
    assert controller._triggers["leadReview"]["listener_args"]["filter_params"] == {"status_filter": "completed"}

    spawn = AsyncMock()
    monkeypatch.setattr(TriggerListenerWorkflow, "_spawn_child_run", spawn)
    key, queued = controller._events[0]
    await controller._spawn_push_run(queued, controller._triggers[key])
    original_event, listener = spawn.await_args.args
    assert original_event == event
    assert listener["task_review_owner"] == "lead"
    assert listener["edges"] == edges


@pytest.mark.asyncio
async def test_mismatched_or_missing_owner_stops_before_filter_activity_or_pulse(monkeypatch):
    forbidden = MagicMock(side_effect=AssertionError("A wrong owner must not schedule any command"))
    monkeypatch.setattr(workflow, "execute_activity", forbidden)
    monkeypatch.setattr(workflow, "patched", forbidden)
    for data in (None, {}, {"parent_node_id": "different"}):
        await TriggerListenerWorkflow()._spawn_child_run({"data": data}, {"task_review_owner": "lead"})
    forbidden.assert_not_called()


@pytest.mark.asyncio
async def test_instance_outcome_signals_exact_generation_retries_with_same_id_and_redacts_ui(monkeypatch):
    from core.container import container
    from nodes.agent._events import publish_instance_task_outcome
    from services import status_broadcaster

    handle = MagicMock(signal=AsyncMock(side_effect=[OSError("offline"), None, None]))
    client = MagicMock()
    client.get_workflow_handle.return_value = handle
    monkeypatch.setattr(container, "temporal_client", lambda: MagicMock(client=client))
    ui = MagicMock(broadcast=AsyncMock())
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: ui)
    outcome = {"status": "submitted", "task_id": "task", "attempt": 2, "revision": 5,
               "agent_name": "Coding", "agent_node_id": "worker", "parent_node_id": "lead",
               "workflow_id": "wf", "generation": 7, "controller_workflow_id": "wf-controller-7",
               "job_id": "job", "team_id": "team", "result": "private result"}
    with pytest.raises(OSError):
        await publish_instance_task_outcome(outcome)
    ui.broadcast.assert_not_awaited()
    await publish_instance_task_outcome(outcome)
    await publish_instance_task_outcome(outcome)
    assert {args.args for args in client.get_workflow_handle.call_args_list} == {("wf-controller-7",)}
    assert {args.args[1]["id"] for args in handle.signal.await_args_list} == {"task:attempt:2:revision:5:completed"}
    data = handle.signal.await_args.args[1]["data"]
    assert data["result"] == "private result" and data["job_id"] == "job" and data["parent_node_id"] == "lead"
    assert "private result" not in str(ui.broadcast.await_args)
