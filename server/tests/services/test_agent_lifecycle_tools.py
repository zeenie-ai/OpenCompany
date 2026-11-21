"""The public contract does not require models to construct tracking records."""
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from services.agent_instances.tools import SpawnInput, build_bindings, execute_lifecycle_tool


def runtime():
    return {"agent_lifecycle_version": 1, "agent_instance_id": "lead-instance", "role": "lead",
            "teammates": [{"node_id": "2:web:1", "node_type": "web_agent", "label": "Research"}]}


def test_spawn_requires_only_instructions_and_never_exposes_scope():
    bindings = build_bindings(runtime=runtime())
    spawn = next(binding for binding in bindings if binding.name.startswith("spawn_"))
    assert spawn.schema["required"] == ["instructions"]
    assert set(spawn.schema["properties"]) == {"instructions", "after"}
    assert spawn.schema["additionalProperties"] is False
    assert spawn.args_schema.model_validate({"instructions": "Find primary sources."}).instructions == "Find primary sources."
    assert "starts_child" in spawn.execution_traits
    assert spawn.parameters["_agent_template"]["node_id"] == "2:web:1"
    exact = "  Preserve this indentation.\n\n"
    assert SpawnInput(instructions=exact).instructions == exact


@pytest.mark.parametrize("arguments", [{}, {"instructions": " "}, {"instructions": "Work", "team_id": "other"},
    {"operation": "assign_task", "title": "Mission: Research. assignee_node_id is web-agent"}])
def test_invalid_assignment_cannot_pass_the_exposed_validator(arguments):
    with pytest.raises(ValidationError):
        SpawnInput.model_validate(arguments)


def test_names_do_not_retarget_when_labels_change_and_peers_cannot_review():
    first = runtime()
    second = runtime()
    second["teammates"][0]["label"] = "New name"
    assert [binding.name for binding in build_bindings(runtime=first)] == [binding.name for binding in build_bindings(runtime=second)]
    peers = {binding.name for binding in build_bindings(runtime={"agent_instance_id": "peer", "role": "teammate"})}
    assert "send_message" in peers
    assert not peers.intersection({"accept_task", "finish_team", "assign_task"})
    peer_template = {**runtime(), "role": "teammate"}
    assert not any(binding.name.startswith("spawn_") for binding in build_bindings(runtime=peer_template))


@pytest.mark.parametrize("instance", [None, "contact-instance"])
def test_talk_can_admit_and_read_jobs_without_selecting_a_template(instance):
    bindings = build_bindings(runtime={"agent_lifecycle_version": 1, "role": "talk", "agent_instance_id": instance,
                                       "teammates": runtime()["teammates"]})
    assert [binding.name for binding in bindings] == ["submit_job", "get_job_status"]
    assert bindings[0].schema["required"] == ["instructions"]
    assert set(bindings[0].schema["properties"]) == {"instructions"}
    assert set(bindings[1].schema["properties"]) == {"job_id"}
    assert not bindings[1].execution_traits


def test_instance_capture_excludes_parent_history_services_and_secrets():
    from services.agent_instances.protocol import capture_runtime_context
    result = capture_runtime_context({"workflow_id": "wf", "max_concurrent_subagents": 5,
        "parameter_snapshot": {"worker": {"model": "configured", "api_key": "secret"}},
        "agent_initial_transcript": [{"role": "user", "content": "parent"}],
        "agent_input_messages": [{"role": "user", "content": "current"}],
        "chat_run_id": "talk-run", "database": object()})
    assert result == {"workflow_id": "wf", "max_concurrent_subagents": 5,
                      "parameter_snapshot": {"worker": {"model": "configured"}},
                      "agent_lifecycle_version": 1, "agent_protocol_version": 1}


def test_instance_execution_outputs_have_separate_sessions_from_talk_and_siblings():
    from services.temporal.agent_instance_workflow import turn_context
    scope = {"workflow_id": "wf", "job_id": "job", "team_id": "team", "generation": 1}
    original = {"session_id": "owner-talk", "context_session_id": "owner-talk", "outputs": {"input": "handoff"},
                "run_scope": {"kind": "chat", "run_id": "owner-run"}, "chat_run_id": "owner-run"}
    def context(identifier):
        return turn_context({"run_id": "run", "instance": {"id": identifier, "template_node_id": "same-template",
            "config": {"scope": scope, "template": {"node_id": "same-template"}, "runtime_context": original}}})
    first, second = context("first"), context("second")
    assert first["session_id"] == first["context_session_id"] == "agent-instance:first"
    assert second["session_id"] != first["session_id"]
    assert first["outputs"] == original["outputs"]
    assert "run_scope" not in first and "chat_run_id" not in first
    assert original["session_id"] == "owner-talk"


@pytest.mark.asyncio
async def test_talk_submission_reaches_existing_job_authorization_without_an_instance():
    from services.temporal.agent_instance_workflow import execute_instance_tool
    config = {"name": "submit_job", "agent_operation": "submit_job", "tool_node_id": "manager",
              "agent_runtime": {"role": "talk", "agent_lifecycle_version": 1}}
    with patch("services.handlers.tools.execute_tool", new_callable=AsyncMock, return_value={"job_id": "job"}) as dispatch:
        result = await execute_instance_tool({"agent_tool_config": config, "tool_args": {"instructions": "Research"},
            "workflow_id": "wf", "parent_node_id": "talk", "tool_call_id": "call"})
    assert result == {"job_id": "job"}
    assert dispatch.await_args.args[0:2] == ("submit_job", {"instructions": "Research"})
    assert dispatch.await_args.args[2]["parent_node_id"] == "talk"


@pytest.mark.asyncio
async def test_talk_status_reaches_existing_job_authorization_without_an_instance():
    from services.temporal.agent_instance_workflow import execute_instance_tool
    config = {"name": "get_job_status", "agent_operation": "get_job_status", "tool_node_id": "manager",
              "agent_runtime": {"role": "talk", "agent_lifecycle_version": 1}}
    with patch("services.handlers.tools.execute_tool", new_callable=AsyncMock, return_value={"job": None}) as dispatch:
        result = await execute_instance_tool({"agent_tool_config": config, "tool_args": {},
            "workflow_id": "wf", "parent_node_id": "talk", "tool_call_id": "call"})
    assert result == {"job": None}
    assert dispatch.await_args.args[0:2] == ("get_job_status", {})
    assert dispatch.await_args.args[2]["parent_node_id"] == "talk"


@pytest.mark.asyncio
async def test_invalid_input_is_returned_before_storage_or_wakeup():
    result = await execute_lifecycle_tool("spawn_web", {"title": "Mission: find sources"}, {"agent_operation": "spawn"})
    assert result["error_type"] == "invalid_tool_input"
    assert "instructions" in result["error"]


@pytest.mark.asyncio
async def test_three_invalid_requests_halt_before_a_fourth_tool_dispatch():
    from services.agent_instances.admission import get_gate
    from services.handlers.tools import execute_tool
    config = {"agent_run_id": "malformed-log-replay", "agent_operation": "spawn"}
    gate = get_gate(config, create=True)
    assert gate is not None
    # Same malformed structure as the recorded loop, with distinct tool IDs.
    arguments = {"operation": "assign_task", "title": "Mission: Research. assignee_node_id is web-agent"}
    with patch("services.status_broadcaster.get_status_broadcaster", return_value=None):
        for i in range(3):
            result = await execute_tool("spawn_web", arguments, {**config, "tool_call_id": f"call-{i}"})
            assert result["error_type"] == "invalid_tool_input"
        assert result["turn_halted"] is True
        with patch("services.handlers.tools._dispatch_tool", new_callable=AsyncMock) as dispatch:
            result = await execute_tool("spawn_web", {"instructions": "Valid work"}, {**config, "tool_call_id": "call-4"})
        dispatch.assert_not_called()
    assert result["error_type"] == "agent_turn_halted"
    assert gate.invalid_count == 3


@pytest.mark.asyncio
async def test_bound_spawn_dispatch_preserves_frozen_template_and_team_scope(real_database):
    from models.database import AgentTeam, TeamMember
    from services.agent_instances.service import AgentInstanceService
    from services.agent_bindings import node_tools
    from services.handlers.tools import execute_tool

    database = real_database
    async with database.reserved_session() as session:
        session.add(AgentTeam(id="binding-team", workflow_id="wf", team_lead_node_id="lead", execution_id="team-exec"))
        session.add(TeamMember(team_id="binding-team", agent_node_id="worker", agent_type="aiAgent", role="teammate"))
        await session.commit()
    service = AgentInstanceService(database)
    scope = {"workflow_id": "wf", "team_id": "binding-team", "job_id": "job", "generation": 1,
             "lead_node_id": "lead", "execution_id": "team-exec", "root_execution_id": "job"}
    lead = await service.ensure_lead(scope=scope, template={"node_id": "lead", "node_type": "ai_employee", "initial_instructions": "Coordinate this job."}, request_id="lead")
    scope["agent_instance_id"] = lead["id"]
    lead_run = await service.claim_run(lead["id"], request_id="lead-turn")
    graph = {
        "nodes": [{"id": "lead", "type": "ai_employee"}, {"id": "worker", "type": "aiAgent"}],
        "edges": [{"source": "worker", "target": "lead", "targetHandle": "input-teammates"}],
        "parameter_snapshot": {"worker": {"model": "configured-model", "temperature": 0.2, "api_key": "never-store-me"}},
        "agent_initial_transcript": [{"role": "assistant", "content": "Private parent history"}],
    }
    runtime_data = {**scope, "agent_instance_scope": scope, "agent_run_id": lead_run["run_id"], "agent_lifecycle_version": 1,
                    "role": "lead", "runtime_context": graph,
                    "teammates": [{"node_id": "worker", "node_type": "aiAgent", "label": "Worker"}]}
    bindings = await node_tools(None, {"node_type": "taskManager", "node_id": "manager", "agent_runtime": runtime_data})
    tool, binding = next(item for item in bindings if item[1].get("agent_operation") == "spawn")
    config = {**binding, "database": database, "execution_id": "current-physical-turn", "tool_call_id": "spawn-call"}
    with patch("services.status_broadcaster.get_status_broadcaster", return_value=None), \
         patch("services.agent_instances.runtime.wake_instances", new_callable=AsyncMock):
        result = await execute_tool(tool.name, {"instructions": "  Find primary evidence.\n"}, config)
        duplicate = await execute_tool(tool.name, {"instructions": "  Find primary evidence.\n"}, config)
    assert result == duplicate
    assert result["task"]["mission"] == "  Find primary evidence.\n"
    instance = await service.get_instance(result["agent"]["id"])
    assert instance["config"]["template"]["parameters"] == {"model": "configured-model", "temperature": 0.2}
    assert instance["config"]["scope"]["execution_id"] == "team-exec"
    assert "agent_initial_transcript" not in instance["config"]["runtime_context"]
