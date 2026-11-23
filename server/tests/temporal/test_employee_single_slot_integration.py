"""Run the hired hierarchy on a local Temporal worker, including its real model tool loop."""
import asyncio
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select
from temporalio import activity
from temporalio.common import SearchAttributeKey
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from models.agent_instances import AgentRun
from models.database import ChatMessage, SubagentConcurrencyPermit, TeamTask
from models.employees import EmployeeJob
from services.agent_instances.runtime import wake_instances
from services.chat import ledger
from services.llm.protocol import Message, ToolCall, message_to_wire
from services.plugin.scaling import TaskQueue
from services.temporal.agent_activities import collect_agent_activities, prepare_agent_payload
from services.temporal.agent_instance_workflow import AgentInstanceSessionWorkflow, INSTANCE_ACTIVITIES
from services.temporal.agent_workflow import AgentWorkflow
from services.temporal.employee_job_workflow import EmployeeJobWorkflow
from tests.temporal.test_agent_workflow_replay import _broadcast_progress, _clear_skills, _store_output
from tests.temporal.test_employee_hierarchy_runtime import call_tool, hierarchy, hierarchy_graph


async def test_nested_job_releases_single_slot_and_chat_delivers_once(real_database, hierarchy, monkeypatch):
    from core.container import container

    cli = os.environ.get("TEMPORAL_TEST_CLI")
    if not cli or not Path(cli).is_file():
        pytest.skip("Set TEMPORAL_TEST_CLI to the local Temporal CLI binary")
    db = real_database
    built, control, transport = hierarchy
    container.settings().temporal_task_queue = TaskQueue.DEFAULT
    container.settings().agent_recursion_limit = 12
    graph = control.graph_snapshot
    chat, orch = built.team_plan["talk_node_id"], built.team_plan["orchestrator_node_id"]
    manager, = [role["node_id"] for role in built.team_plan["members"] if role["parent_node_id"] == orch]
    analyst, = [role["node_id"] for role in built.team_plan["members"] if role["parent_node_id"] == manager]
    mission = "Verify the quarterly sample."
    admitted = await ledger.admit_message(db, session_id="7", workflow_id="7", execution_id="chat",
                                          text=mission, track=True)
    run = await ledger.start_run(db, run_id=admitted.run.run_id, temporal_workflow_id="chat", temporal_run_id="r1")
    trigger = built.node_roles["talk_trigger"]
    ctx = {"workflow_id": "7", "node_id": chat, "node_type": "ai_employee", "nodes": graph["nodes"],
           "edges": graph["edges"], "outputs": {trigger: {"message": mission}}, "user_id": "owner",
           "session_id": "7", "generation": 1, "execution_id": "chat", "root_execution_id": "chat",
           "agent_lifecycle_version": 1, "agent_protocol_version": 1, "run_scope": {"run_id": run.run_id},
           "controller_workflow_id": control.controller_workflow_id, "data_scope_id": control.data_scope_id,
           "parameter_snapshot": graph["parameters"]}
    receipt = await call_tool(ctx, await prepare_agent_payload(ctx), "submit_job", {"instructions": mission}, "assign")
    (workflow_name, job_context), _ = transport.start_workflow.await_args
    await ledger.finish_run(db, run_id=run.run_id, temporal_workflow_id="chat", temporal_run_id="r1", success=True)
    model_turns = []
    final_text = "I verified your quarterly sample contains 14 observations."
    replies = {chat: final_text, orch: "The research manager verified the evidence.",
               manager: "I checked the analyst's observations.", analyst: "I counted 14 observations."}

    @activity.defn(name="agent.execute_llm_step")
    async def model(step: dict) -> dict:
        node = step["node_id"]
        messages = step["messages"]
        prompt = next(message["content"] for message in reversed(messages) if message["role"] == "user")
        last = messages[-1]
        tool_name, arguments = None, {}
        if step["iteration"] == 0:
            model_turns.append(node)
            if "[task_review]" in prompt:
                task_id = re.search(r"Task (\S+) is submitted", prompt).group(1)
                tool_name, arguments = "get_task", {"task_ref": task_id}
            elif node in {orch, manager}:
                tool_name, = [tool["name"] for tool in step["tools"] if tool["name"].startswith("spawn_")]
                arguments = {"instructions": "Check the sample evidence."}
        elif last["role"] == "tool" and last["name"] == "get_task":
            task = json.loads(last["content"])["task"]
            tool_name, arguments = "accept_task", {"task_ref": task["task_ref"]}
        if tool_name:
            call = {"id": f"call-{step['iteration']}", "name": tool_name, "args": arguments}
            return {"kind": "tool_calls", "calls": [call],
                    "assistant_message": message_to_wire(Message(role="assistant", tool_calls=[ToolCall(**call)]))}
        return {"kind": "final", "content": replies[node],
                "assistant_message": message_to_wire(Message(role="assistant", content=replies[node]))}

    overrides = [model, _broadcast_progress, _store_output, _clear_skills]
    names = {activity._Definition.from_callable(fn).name for fn in overrides}
    activities = [fn for fn in collect_agent_activities() if activity._Definition.from_callable(fn).name not in names]
    monkeypatch.setattr("services.agent_instances.runtime.wake_instances", wake_instances)
    # Deployment fanout is tested separately. Here the committed inbox wakes the actual parent session.
    publish = AsyncMock()
    monkeypatch.setattr("nodes.agent._events.publish_instance_task_outcome", publish)
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=cli, ip="127.0.0.1",
            search_attributes=[SearchAttributeKey.for_keyword("EventWorkflowId")]) as environment:
        client = environment.client
        monkeypatch.setattr(container, "temporal_client", lambda: SimpleNamespace(is_connected=True, client=client))
        async with Worker(client, task_queue=container.settings().temporal_task_queue,
                          workflows=[EmployeeJobWorkflow, AgentWorkflow, AgentInstanceSessionWorkflow],
                          activities=[*activities, *overrides, *INSTANCE_ACTIVITIES]):
            job_id = receipt["job_id"]
            handle = await client.start_workflow(workflow_name, job_context, id=job_id,
                                                 task_queue=container.settings().temporal_task_queue)
            first_dispatch = await asyncio.wait_for(handle.result(), timeout=30)
            assert first_dispatch["instance_managed"]
            async with asyncio.timeout(50):
                while True:
                    for event in publish.await_args_list:
                        assert event.args[0]["status"] == "submitted", event.args[0]
                    async with db.get_session() as session:
                        job = await session.get(EmployeeJob, job_id)
                        if job.state in {"delivered", "completed", "failed", "review_blocked"}:
                            break
                    await asyncio.sleep(.1)
            assert job.result == final_text, job
            assert model_turns == [orch, manager, analyst, manager, orch, chat]
            assert [(call.args[0]["agent_node_id"], call.args[0]["parent_node_id"])
                    for call in publish.await_args_list] == [(analyst, manager), (manager, orch), (orch, chat)]
            async with db.get_session() as session:
                tasks = (await session.execute(select(TeamTask).where(TeamTask.team_id == job.team_id))).scalars().all()
                assert len(tasks) == 3 and all(task.status == "accepted" for task in tasks)
                messages = (await session.execute(select(ChatMessage).where(ChatMessage.message == final_text))).scalars().all()
                assert len(messages) == 1 and messages[0].session_id == "7"
                leases = (await session.execute(select(SubagentConcurrencyPermit).where(
                    SubagentConcurrencyPermit.root_execution_id == job.id).order_by(SubagentConcurrencyPermit.acquired_at))).scalars().all()
                assert len(leases) == 5 and all(lease.status == "released" for lease in leases)
                for earlier, later in zip(leases, leases[1:]):
                    assert earlier.released_at <= later.acquired_at
                runs = (await session.execute(select(AgentRun))).scalars().all()
            # Replay both the real instance session and the child model workflow from this execution.
            worker_run, = [row for row in runs if row.task_id and row.task_id == next(
                task.id for task in tasks if task.parent_agent_id == manager)]
            parent = client.get_workflow_handle(worker_run.temporal_workflow_id, run_id=worker_run.temporal_run_id)
            await asyncio.wait_for(parent.result(), timeout=10)
            for recorded, implementation in [(parent, AgentInstanceSessionWorkflow),
                    (client.get_workflow_handle(f"agent-instance-run:{worker_run.id}"), AgentWorkflow)]:
                await Replayer(workflows=[implementation]).replay_workflow(await recorded.fetch_history())
