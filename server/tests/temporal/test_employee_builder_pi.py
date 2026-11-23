"""An owner adds a Coding Agent in Talk, then assigns it a real file through its manager."""
import asyncio
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlmodel import select
from temporalio import activity
from temporalio.common import SearchAttributeKey
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

import nodes  # noqa: F401 - register the actual Pi Write and Agent Builder plugins
from models.agent_instances import AgentDelivery, AgentInstanceConversation, AgentRun
from models.database import ChatMessage, TeamTask
from models.employees import EmployeeJob
from services import pi_blobs
from services.agent_instances.runtime import wake_instances
from services.ai import AIService
from services.approvals.rules import set_ask_first
from services.chat import ledger
from services.coding_agent_recipe import default_pi_tool_types
from services.employees.jobs import deliver_job
from services.employees.safe_apply import apply_saved_changes
from services.handlers.tools import execute_tool
from services.llm.protocol import Message, ToolCall, message_to_wire
from services.pi_builtin_tools import execute_pi_tool
from services.plugin import NodeContext
from services.plugin.scaling import TaskQueue
from services.temporal.agent_activities import collect_agent_activities, prepare_agent_payload
from services.temporal.agent_instance_workflow import AgentInstanceSessionWorkflow, INSTANCE_ACTIVITIES
from services.temporal.agent_workflow import AgentWorkflow
from services.temporal.employee_job_workflow import EmployeeJobWorkflow
from services.temporal.pi_activities import PiControlActivities, PiDriverActivities, pi_put_blob
from services.temporal.pi_protocol import DRIVER_QUEUE
from services.temporal.pi_workflow import PiCodingAgentWorkflow
from services.temporal.plugin_activities import collect_plugin_activities
from services.task_trigger_routing import accepts_task_owner, task_trigger_binding
from services.workflow import WorkflowService
from tests.temporal.test_agent_workflow_replay import _broadcast_progress, _clear_skills, _store_output
from tests.temporal.test_employee_hierarchy_runtime import call_tool, hierarchy, hierarchy_graph  # noqa: F401 - fixtures
from tests.temporal.test_pi_workflow_integration import FAULTS, LostResponseClient


MISSION = "Write the approved evidence to evidence.txt."
EVIDENCE = "Verified by the Coding Agent.\n"
REPLY = "The team verified the contents of evidence.txt."


async def chat_turn(db, built, control, key, text):
    admission = await ledger.admit_message(db, session_id="7", workflow_id="7", execution_id=key, text=text, track=True)
    claim = {"run_id": admission.run.run_id, "temporal_workflow_id": key, "temporal_run_id": key}
    assert await ledger.start_run(db, **claim)
    graph = control.graph_snapshot
    return claim, {"workflow_id": "7", "node_id": built.team_plan["talk_node_id"], "node_type": "ai_employee",
        "nodes": graph["nodes"], "edges": graph["edges"], "user_id": "owner", "session_id": "7",
        "execution_id": key, "root_execution_id": key, "generation": 1, "data_scope_id": control.data_scope_id,
        "controller_workflow_id": control.controller_workflow_id, "agent_lifecycle_version": 1,
        "agent_protocol_version": 1, "pi_harness_version": 1, "run_scope": {"run_id": claim["run_id"]},
        "outputs": {built.node_roles["talk_trigger"]: {"message": text}}, "parameter_snapshot": graph["parameters"]}


async def add_and_apply(db, built, control, manager):
    await set_ask_first(db, "7", False)
    claim, ctx = await chat_turn(db, built, control, "build", "Add a Coding Agent to my team.")
    builder = built.node_roles["builder"]
    result = await execute_tool("agent_builder", {"operation": "add_subagent", "agent_type": "coding_agent",
        "target_member_id": manager}, {**ctx, "parent_node_id": ctx["node_id"], "node_id": builder,
        "tool_node_id": builder, "node_type": "agentBuilder", "tool_call_id": "add-coder",
        "parameters": ctx["parameter_snapshot"].get(builder, {})})
    assert result.get("activation_state") == "saved" and not result.get("required_access"), result
    coder = result["member_id"]
    assert all(node["id"] != coder for node in control.graph_snapshot["nodes"])
    assert await ledger.finish_run(db, success=True, **claim)
    applied = await apply_saved_changes(db, "7", owner_id="owner", key="add-coder")
    assert applied == {"success": True, "activation_state": "running"}, applied
    control = await db.get_latest_workflow_control("7")
    graph = control.graph_snapshot
    types = {node["id"]: node["type"] for node in graph["nodes"]}
    assert set(default_pi_tool_types()) == {types[edge["source"]] for edge in graph["edges"]
        if edge["target"] == coder and edge.get("targetHandle") == "input-tools"}
    assert [edge["target"] for edge in graph["edges"] if edge["source"] == coder
            and edge.get("targetHandle") == "input-teammates"] == [manager]
    return control, coder


@pytest.mark.parametrize("nested", [False, True], ids=["orchestrator", "nested-employee"])
async def test_builder_coder_submits_file_to_its_assigner(real_database, tmp_path, monkeypatch, hierarchy, nested):  # noqa: F811
    from core.container import container

    cli = os.environ.get("TEMPORAL_TEST_CLI")
    if not cli or not Path(cli).is_file():
        pytest.skip("Set TEMPORAL_TEST_CLI to exercise the packaged Pi driver with a local Temporal server")
    db = real_database
    monkeypatch.setattr(pi_blobs, "get_database", lambda: db)
    built, control, transport = hierarchy
    settings = container.settings()
    settings.temporal_task_queue = TaskQueue.DEFAULT
    settings.agent_recursion_limit = 12
    settings.tool_result_max_chars = 100_000
    auth = container.auth_service()
    auth.get_credential_source = AsyncMock(return_value=None)
    monkeypatch.setattr(container, "auth_service", lambda: auth)
    ai = AIService(auth_service=auth, database=db, cache=None, settings=settings)
    monkeypatch.setattr(container, "ai_service", lambda: ai)
    deployment = SimpleNamespace(_deployments={}, pause=Mock(), resume=AsyncMock())
    service = WorkflowService(database=db, ai_service=ai, maps_service=None, text_service=None,
                              android_service=None, cache=None, settings=settings)
    service._get_workspace_dir = lambda slug: str(tmp_path / slug)
    service.get_deployment_status = lambda _: {}
    service._get_deployment_manager = lambda: deployment
    monkeypatch.setattr(container, "workflow_service", lambda: service)
    workspace = tmp_path / "Maya_1"
    workspace.mkdir()
    writer = AsyncMock(wraps=execute_pi_tool)
    monkeypatch.setattr("services.pi_builtin_tools.execute_pi_tool", writer)
    monkeypatch.setattr("services.deployment.handlers._controller_handle", lambda _: SimpleNamespace(execute_update=AsyncMock()))
    monkeypatch.setattr("services.deployment.handlers._set_cron_pause", AsyncMock())
    signal = AsyncMock(return_value=False)
    monkeypatch.setattr("services.events.dispatch.signal_generation_event", signal)
    chat, orch = built.team_plan["talk_node_id"], built.team_plan["orchestrator_node_id"]
    manager = next(member["node_id"] for member in built.team_plan["members"]
                   if member["node_type"] == "ai_employee") if nested else orch
    with monkeypatch.context() as applying:
        applying.setattr(container, "temporal_client", lambda: SimpleNamespace(is_connected=True, client=None))
        control, coder = await add_and_apply(db, built, control, manager)
    claim, ctx = await chat_turn(db, built, control, "assign", MISSION)
    prepared = await prepare_agent_payload(ctx)
    result = await call_tool(ctx, prepared, "submit_job", {"instructions": MISSION}, "assign")
    assert "job_id" in result, result
    (workflow_name, job_context), _ = transport.start_workflow.await_args
    assert await ledger.finish_run(db, success=True, **claim)
    origin = await ledger.get_run(db, claim["run_id"])
    turns = []

    @activity.defn(name="agent.execute_llm_step")
    async def model(step: dict) -> dict:
        node = step["node_id"]
        last = step["messages"][-1]
        name, args = None, {}
        if step["iteration"] == 0:
            turns.append(node)
            prompt = next(m["content"] for m in reversed(step["messages"]) if m["role"] == "user")
            if "[task_review]" in prompt:
                task = re.search(r"Task (\S+) is submitted", prompt)
                assert task, prompt
                name, args = "get_task", {"task_ref": task[1]}
            elif node == coder:
                assert "[task_assignment]" in prompt, prompt
                name, args = "pi_write", {"path": "evidence.txt", "content": EVIDENCE}
            else:
                spawn = "spawn_ai_employee_" if nested and node == orch else "spawn_coding_agent_"
                name, = [tool["name"] for tool in step["tools"] if tool["name"].startswith(spawn)]
                args = {"instructions": MISSION}
        elif last["role"] == "tool" and last["name"] == "get_task":
            task = json.loads(last["content"])["task"]
            name, args = "accept_task", {"task_ref": task["task_ref"]}
        elif node == coder:
            assert last["role"] == "tool" and last["name"] == "pi_write"
            assert "Successfully wrote" in last["content"], last["content"]
        if name:
            call = {"id": f"call-{step['iteration']}", "name": name, "args": args}
            return {"kind": "tool_calls", "calls": [call], "assistant_message": message_to_wire(
                Message(role="assistant", tool_calls=[ToolCall(**call)]))}
        answer = REPLY if node == chat else "The evidence file is ready."
        return {"kind": "final", "content": answer, "assistant_message": message_to_wire(Message(role="assistant", content=answer))}

    overrides = [model, _broadcast_progress, _store_output, _clear_skills]
    names = {activity._Definition.from_callable(fn).name for fn in overrides}
    activities = [fn for fn in collect_agent_activities() if activity._Definition.from_callable(fn).name not in names]
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=cli, ip="127.0.0.1",
            search_attributes=[SearchAttributeKey.for_keyword("EventWorkflowId")]) as env:
        client = env.client
        monkeypatch.setattr(container, "temporal_client", lambda: SimpleNamespace(is_connected=True, client=client))
        monkeypatch.setattr("services.agent_instances.runtime.wake_instances", wake_instances)
        driver = PiDriverActivities(LostResponseClient(client, "fault-leaf"))
        async with Worker(client, task_queue=TaskQueue.DEFAULT, workflows=[AgentWorkflow, PiCodingAgentWorkflow,
                    AgentInstanceSessionWorkflow, EmployeeJobWorkflow], activities=[*activities, *overrides,
                    *INSTANCE_ACTIVITIES, pi_put_blob, *PiControlActivities(client).activities(),
                    *collect_plugin_activities(include_types=["piWrite"])]), Worker(client, task_queue=DRIVER_QUEUE,
                    activities=[driver.drive_turn], max_concurrent_activities=1):
            job_id = result["job_id"]
            handle = await client.start_workflow(workflow_name, job_context, id=job_id, task_queue=TaskQueue.DEFAULT)
            assert (await asyncio.wait_for(handle.result(), 40))["instance_managed"]
            async with asyncio.timeout(60):
                while True:
                    for call in signal.await_args_list:
                        assert call.args[0].data["status"] == "completed", call.args[0].data
                    async with db.get_session() as session:
                        job = await session.get(EmployeeJob, job_id)
                        if job.state in {"delivered", "completed", "failed", "review_blocked"}:
                            break
                    await asyncio.sleep(.1)
            assert job.result == REPLY, job
            expected_nodes = [orch, manager, coder, manager, orch, chat] if nested else [orch, coder, orch, chat]
            assert turns == expected_nodes
            expected_edges = [(coder, manager), (manager, orch), (orch, chat)] if nested else [(coder, orch), (orch, chat)]
            graph = control.graph_snapshot
            listeners = [task_trigger_binding(node["id"], graph["nodes"], graph["edges"], graph["parameters"][node["id"]])
                         for node in graph["nodes"] if node["type"] == "taskTrigger"]
            events = [call.args[0].model_dump(mode="json") for call in signal.await_args_list]
            assert [(event["data"]["agent_node_id"], event["data"]["parent_node_id"]) for event in events] == expected_edges
            for event in events:
                owners = [listener["task_review_owner"] for listener in listeners if accepts_task_owner(listener, event)]
                assert owners == [event["data"]["parent_node_id"]]
            delivery = NodeContext.from_legacy(chat, "ai_employee", {
                **job_context, "outputs": {chat: {"team_id": job.team_id, "response": REPLY}}})
            assert (await deliver_job(db, delivery, lead_node_id=chat))["delivered"]
            async with db.get_session() as session:
                [task] = (await session.execute(select(TeamTask).where(TeamTask.team_id == job.team_id,
                    TeamTask.assigned_to == coder))).scalars().all()
                assert task.status == "accepted" and task.parent_agent_id == manager
                assigner = await session.get(AgentRun, task.assigning_run_id)
                reviews = (await session.execute(select(AgentDelivery).where(AgentDelivery.kind == "task_review",
                    AgentDelivery.team_id == job.team_id))).scalars().all()
                assert [row.recipient_id for row in reviews if row.payload.get("task_id") == task.id] == [assigner.instance_id]
                transcript = await session.get(AgentInstanceConversation, task.assigned_instance_id)
                assert transcript.revision > 0 and any(m.get("name") == "pi_write" and m["role"] == "tool" for m in transcript.messages)
                [reply] = (await session.execute(select(ChatMessage).where(ChatMessage.message == REPLY))).scalars().all()
                assert (reply.session_id, reply.run_id, reply.parent_uid) == ("7", origin.run_id, origin.user_message_uid)
            child_id = f"agent-instance-run:{task.assigned_run_id}"
            history = await client.get_workflow_handle(child_id).fetch_history()
            writes = [event for event in history.events if event.activity_task_scheduled_event_attributes.activity_type.name == "node.piWrite.v1"]
            assert len(writes) == 1 and FAULTS.pop(f"{child_id}:fault-leaf", 0) == 1
            writer.assert_awaited_once()
            assert writer.await_args.args[0] == "write"
            assert (workspace / "evidence.txt").read_text() == EVIDENCE
            await Replayer(workflows=[PiCodingAgentWorkflow]).replay_workflow(history)
