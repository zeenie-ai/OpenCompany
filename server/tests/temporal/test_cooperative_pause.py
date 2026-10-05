"""Cooperative pause flags shared by Temporal orchestration workflows."""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.workflow import ActivityCancellationType

from nodes.scheduler.cron_scheduler._workflow import CronTriggerWorkflow
from services.temporal.agent_workflow import AgentWorkflow, DelegatedTaskWorkflow
from services.temporal.polling_trigger_workflow import PollingTriggerWorkflow
from services.temporal.trigger_listener_workflow import TriggerListenerWorkflow
from services.temporal.workflow import MachinaWorkflow
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "workflow_type",
    [
        MachinaWorkflow,
        AgentWorkflow,
        DelegatedTaskWorkflow,
        TriggerListenerWorkflow,
        PollingTriggerWorkflow,
        CronTriggerWorkflow,
    ],
)
async def test_pause_and_resume_mutate_durable_workflow_state(workflow_type):
    instance = workflow_type()

    assert instance._control_paused is False
    await instance.pause()
    assert instance._control_paused is True
    await instance.resume()
    assert instance._control_paused is False


def test_controller_registers_acknowledged_control_update():
    definition = WorkflowControlWorkflow.__temporal_workflow_definition

    assert "set_control_state" in definition.updates
    assert {"pause", "resume"} <= set(definition.signals)


@pytest.mark.asyncio
async def test_controller_control_update_is_acknowledged_and_idempotent():
    controller = WorkflowControlWorkflow()

    paused = await controller.set_control_state("pause")
    assert paused["state"] == "paused"
    assert paused["revision"] == 1

    repeated = await controller.set_control_state("paused")
    assert repeated["state"] == "paused"
    assert repeated["revision"] == 1

    resumed = await controller.set_control_state("resume")
    assert resumed["state"] == "running"
    assert resumed["revision"] == 2

    repeated = await controller.set_control_state("running")
    assert repeated["state"] == "running"
    assert repeated["revision"] == 2


@pytest.mark.asyncio
async def test_controller_control_update_rejects_unknown_state_without_mutation():
    controller = WorkflowControlWorkflow()

    with pytest.raises(ApplicationError, match="Control state"):
        await controller.set_control_state("stopped")

    assert controller.status()["state"] == "running"
    assert controller.status()["revision"] == 0






async def _wait_for_predicate(predicate, blocked: asyncio.Event) -> None:
    if not predicate():
        blocked.set()
    while not predicate():
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_machina_rechecks_pause_between_ready_child_starts(monkeypatch):
    from services.temporal import workflow as workflow_module

    temporal_workflow = workflow_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="root-run"),
    )

    paused_gate = asyncio.Event()
    monkeypatch.setattr(
        temporal_workflow,
        "wait_condition",
        lambda predicate: _wait_for_predicate(predicate, paused_gate),
    )

    instance = MachinaWorkflow()
    scheduled: list[str] = []

    async def start_child(_name, *, args, **_kwargs):
        node_id = args[0]["node_id"]
        scheduled.append(node_id)
        handle = asyncio.get_running_loop().create_future()
        handle.set_result(
            {"success": True, "node_id": node_id, "result": {"node": node_id}}
        )
        if len(scheduled) == 1:
            instance._control_paused = True
        return handle

    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start_child)
    monkeypatch.setattr(
        MachinaWorkflow,
        "_resolve_dispatch",
        lambda self, _node_type, **_kwargs: {
            "kind": "child_workflow",
            "name": "AgentWorkflow",
        },
    )

    task = asyncio.create_task(
        instance.run(
            {
                "nodes": [
                    {"id": "agent-a", "type": "aiAgent", "data": {}},
                    {"id": "agent-b", "type": "aiAgent", "data": {}},
                ],
                "edges": [],
                "workflow_id": "graph",
                "workflow_slug": "graph",
                "execution_id": "root-run",
            }
        )
    )

    await asyncio.wait_for(paused_gate.wait(), timeout=1)
    assert scheduled == ["agent-a"]

    await instance.resume()
    result = await asyncio.wait_for(task, timeout=1)

    assert scheduled == ["agent-a", "agent-b"]
    assert result["success"] is True


def _poll_listener_data() -> dict:
    return {
        "node_type": "testPoll",
        "version": 1,
        "trigger_node_id": "poll-1",
        "filter_params": {"poll_interval": 1},
        "seen_ids": ["existing"],
        "nodes": [],
        "edges": [],
        "workflow_id": None,
        "session_id": "test",
    }


@pytest.mark.asyncio
async def test_polling_rechecks_pause_after_sleep_before_activity(monkeypatch):
    from services.temporal import polling_trigger_workflow as polling_module

    temporal_workflow = polling_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)

    instance = PollingTriggerWorkflow()
    paused_gate = asyncio.Event()
    activity_started = asyncio.Event()

    async def sleep_then_pause(_duration):
        instance._control_paused = True

    async def execute_activity(*_args, **_kwargs):
        activity_started.set()
        raise asyncio.CancelledError

    monkeypatch.setattr(temporal_workflow, "sleep", sleep_then_pause)
    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        temporal_workflow,
        "wait_condition",
        lambda predicate: _wait_for_predicate(predicate, paused_gate),
    )

    task = asyncio.create_task(instance.run(_poll_listener_data()))
    await asyncio.wait_for(paused_gate.wait(), timeout=1)
    assert not activity_started.is_set()

    await instance.resume()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert activity_started.is_set()


@pytest.mark.asyncio
async def test_polling_rechecks_pause_after_activity_before_spawn(monkeypatch):
    from services.temporal import polling_trigger_workflow as polling_module

    temporal_workflow = polling_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)

    instance = PollingTriggerWorkflow()
    paused_gate = asyncio.Event()
    child_started = asyncio.Event()

    async def no_sleep(_duration):
        return None

    async def poll_activity(*_args, **_kwargs):
        instance._control_paused = True
        return {
            "seen_ids": ["existing", "event-1"],
            "events": [{"id": "event-1"}],
        }

    async def spawn_child(
        _event,
        _listener_data,
        admission_check=None,
        search_attributes=None,
    ):
        assert admission_check is not None
        child_started.set()
        raise asyncio.CancelledError

    monkeypatch.setattr(temporal_workflow, "sleep", no_sleep)
    monkeypatch.setattr(temporal_workflow, "execute_activity", poll_activity)
    monkeypatch.setattr(
        temporal_workflow,
        "wait_condition",
        lambda predicate: _wait_for_predicate(predicate, paused_gate),
    )
    monkeypatch.setattr(instance, "_spawn_child_run", spawn_child)

    task = asyncio.create_task(instance.run(_poll_listener_data()))
    await asyncio.wait_for(paused_gate.wait(), timeout=1)
    assert not child_started.is_set()

    await instance.resume()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert child_started.is_set()


@pytest.mark.asyncio
async def test_polling_child_admission_rechecks_after_idle_broadcast(monkeypatch):
    from services.temporal import polling_trigger_workflow as polling_module
    from services.temporal import trigger_listener_workflow as listener_module

    temporal_workflow = polling_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: False)
    monkeypatch.setattr(
        listener_module,
        "_build_run_graph",
        lambda **_kwargs: ([], []),
    )

    instance = PollingTriggerWorkflow()
    paused_gate = asyncio.Event()
    child_started = asyncio.Event()

    async def broadcast_idle(**_kwargs):
        instance._control_paused = True

    async def broadcast_waiting(**_kwargs):
        return None

    async def start_child(*_args, **_kwargs):
        child_started.set()
        return MagicMock()

    monkeypatch.setattr(listener_module, "_broadcast_trigger_idle", broadcast_idle)
    monkeypatch.setattr(
        listener_module,
        "_broadcast_trigger_waiting",
        broadcast_waiting,
    )
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start_child)
    monkeypatch.setattr(
        temporal_workflow,
        "wait_condition",
        lambda predicate: _wait_for_predicate(predicate, paused_gate),
    )

    task = asyncio.create_task(
        instance._spawn_child_run(
            {"id": "event-1"},
            _poll_listener_data(),
            admission_check=instance._wait_until_resumed,
        )
    )
    await asyncio.wait_for(paused_gate.wait(), timeout=1)
    assert not child_started.is_set()

    await instance.resume()
    await asyncio.wait_for(task, timeout=1)
    assert child_started.is_set()


def _agent_payload() -> dict:
    return {
        "node_id": "agent-1",
        "node_type": "aiAgent",
        "workflow_id": "graph-1",
        "session_id": "session-1",
        "provider": "openai",
        "model": "test-model",
        "max_tokens": 100,
        "temperature": 0,
        "system_message": "Be useful",
        "user_prompt": "do the work",
        "tools": [
            {
                "name": "do_work",
                "definition": {
                    "name": "do_work",
                    "description": "Do work",
                    "parameters": {"type": "object", "properties": {}},
                },
                "node_type": "testTool",
                "version": 1,
                "task_queue": "test",
                "tool_node_id": "tool-1",
                "parameters": {},
                "tool_info": {},
            }
        ],
        "memory_node_id": "",
        "memory_content": "",
        "memory_window_size": 10,
        "max_iterations": 2,
        "thinking_config": None,
        "compaction_threshold": None,
    }


@pytest.mark.asyncio
async def test_agent_rechecks_pause_before_tool_command_batch(monkeypatch):
    from services.temporal import agent_workflow as agent_module

    temporal_workflow = agent_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="agent-run",
            run_id="run-id-12345678",
        ),
    )
    monkeypatch.setattr(
        agent_module,
        "get_node_class",
        lambda _node_type: SimpleNamespace(needs_canvas=False),
    )

    instance = AgentWorkflow()
    paused_gate = asyncio.Event()
    tool_started = asyncio.Event()
    llm_step = 0

    async def execute_activity(name, *, args, **_kwargs):
        nonlocal llm_step
        if name == "agent.prepare_payload":
            return _agent_payload()
        if name == "agent.broadcast_progress":
            return {"emitted": True}
        if name == "agent.execute_llm_step":
            llm_step += 1
            if llm_step == 1:
                instance._control_paused = True
                return {
                    "kind": "tool_calls",
                    "calls": [
                        {
                            "id": "call-1",
                            "name": "do_work",
                            "args": {},
                        }
                    ],
                    "usage": {},
                }
            return {
                "kind": "final",
                "content": "done",
                "usage": {},
            }
        if name == "node.testTool.v1":
            tool_started.set()
            return {"success": True}
        if name == "agent.store_output":
            return {"stored": True}
        if name == "agent.skill.clear":
            return {"cleared": True}
        raise AssertionError(f"Unexpected activity {name}")

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        temporal_workflow,
        "wait_condition",
        lambda predicate: _wait_for_predicate(predicate, paused_gate),
    )

    task = asyncio.create_task(
        instance.run({"node_id": "agent-1", "execution_id": "root-run"})
    )
    await asyncio.wait_for(paused_gate.wait(), timeout=1)
    assert not tool_started.is_set()

    await instance.resume()
    result = await asyncio.wait_for(task, timeout=1)

    assert tool_started.is_set()
    assert result["success"] is True


@pytest.mark.asyncio
async def test_agent_rechecks_pause_after_tool_phase_before_activity(monkeypatch):
    from services.temporal import agent_workflow as agent_module

    temporal_workflow = agent_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="agent-run",
            run_id="run-id-12345678",
        ),
    )
    monkeypatch.setattr(
        agent_module,
        "get_node_class",
        lambda _node_type: SimpleNamespace(needs_canvas=False),
    )

    instance = AgentWorkflow()
    paused_gate = asyncio.Event()
    tool_started = asyncio.Event()
    llm_step = 0

    async def execute_activity(name, *, args, **_kwargs):
        nonlocal llm_step
        if name == "agent.prepare_payload":
            return _agent_payload()
        if name == "agent.broadcast_progress":
            if args[0]["phase"] == "executing_tool":
                instance._control_paused = True
            return {"emitted": True}
        if name == "agent.execute_llm_step":
            llm_step += 1
            if llm_step == 1:
                return {
                    "kind": "tool_calls",
                    "calls": [{"id": "call-1", "name": "do_work", "args": {}}],
                    "usage": {},
                }
            return {"kind": "final", "content": "done", "usage": {}}
        if name == "node.testTool.v1":
            tool_started.set()
            return {"success": True}
        if name == "agent.store_output":
            return {"stored": True}
        if name == "agent.skill.clear":
            return {"cleared": True}
        raise AssertionError(f"Unexpected activity {name}")

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        temporal_workflow,
        "wait_condition",
        lambda predicate: _wait_for_predicate(predicate, paused_gate),
    )

    task = asyncio.create_task(
        instance.run({"node_id": "agent-1", "execution_id": "root-run"})
    )
    await asyncio.wait_for(paused_gate.wait(), timeout=1)
    assert not tool_started.is_set()

    await instance.resume()
    result = await asyncio.wait_for(task, timeout=1)

    assert tool_started.is_set()
    assert result["success"] is True


def _delegation_agent_payload() -> dict:
    payload = _agent_payload()
    payload.update(
        {
            "team_id": "team-1",
            "tools": [
                {
                    "name": "delegate_to_child",
                    "definition": {
                        "name": "delegate_to_child",
                        "description": "Delegate work",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "node_type": "aiAgent",
                    "version": 1,
                    "task_queue": "test",
                    "tool_node_id": "child-agent",
                    "parameters": {},
                    "tool_info": {"label": "Child"},
                }
            ],
        }
    )
    return payload


class _CompletedChildHandle:
    id = "child-workflow"
    first_execution_run_id = "child-run"

    def __await__(self):
        async def result():
            return {
                "success": True,
                "result": {"response": "delegated result"},
            }

        return result().__await__()


class _PendingChildHandle:
    first_execution_run_id = "child-run"

    def __init__(self, child_id: str) -> None:
        self.id = child_id
        self._result = asyncio.get_running_loop().create_future()

    def __await__(self):
        return self._result.__await__()


def _two_delegation_agent_payload() -> dict:
    payload = _delegation_agent_payload()
    template = payload["tools"][0]
    payload["tools"] = [
        {
            **template,
            "name": "delegate_to_child_a",
            "definition": {
                **template["definition"],
                "name": "delegate_to_child_a",
            },
            "tool_node_id": "child-agent-a",
            "tool_info": {"label": "Child A"},
        },
        {
            **template,
            "name": "delegate_to_child_b",
            "definition": {
                **template["definition"],
                "name": "delegate_to_child_b",
            },
            "tool_node_id": "child-agent-b",
            "tool_info": {"label": "Child B"},
        },
    ]
    return payload


@pytest.mark.asyncio
async def test_acquire_activity_compensates_cancelled_attempt(monkeypatch):
    from services import agent_team
    from services.temporal.agent_activities import acquire_subagent_permit

    calls: list[tuple[str, str, str]] = []

    async def acquire(root_id, permit_id, _limit):
        calls.append(("acquire", root_id, permit_id))
        # Model cancellation winning after the durable acquire side effect but
        # before an activity result can be recorded in workflow history.
        raise asyncio.CancelledError

    async def release(root_id, permit_id):
        calls.append(("release", root_id, permit_id))
        return True

    service = SimpleNamespace(
        acquire_subagent_permit=acquire,
        release_subagent_permit=release,
    )
    monkeypatch.setattr(
        agent_team,
        "get_agent_team_service",
        lambda: service,
    )

    with pytest.raises(asyncio.CancelledError):
        await acquire_subagent_permit({
            "root_execution_id": "root-1",
            "permit_id": "permit-1",
            "limit": 3,
        })

    assert calls == [
        ("acquire", "root-1", "permit-1"),
        ("release", "root-1", "permit-1"),
    ]


@pytest.mark.parametrize("patch_enabled", [True])
@pytest.mark.asyncio
async def test_delegated_acquire_cancellation_type_is_replay_patch_guarded(
    monkeypatch,
    patch_enabled,
):
    from services.temporal import agent_workflow as agent_module

    temporal_workflow = agent_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(
        temporal_workflow,
        "patched",
        lambda patch_id: (
            patch_enabled
            if patch_id
            == getattr(agent_module, "DELEGATION_ACQUIRE_CANCELLATION_PATCH", "agent-delegation-acquire-cancellation-v1")
            else False
        ),
    )
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="delegated-runner",
            run_id="runner-run-id",
        ),
    )

    acquire_kwargs: dict = {}

    async def execute_activity(name, *, args, **kwargs):
        if name == "agent.register_task_execution":
            return {}
        if name == "agent.acquire_subagent_permit":
            acquire_kwargs.update(kwargs)
            raise asyncio.CancelledError
        raise AssertionError(f"Unexpected activity {name}")

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)

    with pytest.raises(asyncio.CancelledError):
        await DelegatedTaskWorkflow().run({
            "lifecycle": {
                "team_id": "team-1",
                "team_task_id": "task-1",
                "root_execution_id": "root-1",
            },
            "child_workflow_id": "child-1",
            "child_context": {},
        })

    if patch_enabled:
        assert acquire_kwargs["cancellation_type"] == (
            ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
        )
    else:
        assert "cancellation_type" not in acquire_kwargs


@pytest.mark.parametrize("patch_enabled", [True])
@pytest.mark.asyncio
async def test_agent_acquire_cancellation_type_is_replay_patch_guarded(
    monkeypatch,
    patch_enabled,
):
    from services.temporal import agent_workflow as agent_module

    temporal_workflow = agent_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(
        temporal_workflow,
        "patched",
        lambda patch_id: (
            patch_enabled
            if patch_id
            == getattr(agent_module, "DELEGATION_ACQUIRE_CANCELLATION_PATCH", "agent-delegation-acquire-cancellation-v1")
            else True
        ),
    )
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="agent-run",
            run_id="run-id-12345678",
        ),
    )

    acquire_kwargs: dict = {}

    async def execute_activity(name, *, args, **kwargs):
        if name == "agent.prepare_payload":
            return _delegation_agent_payload()
        if name == "agent.broadcast_progress":
            return {"emitted": True}
        if name == "agent.execute_llm_step":
            return {
                "kind": "tool_calls",
                "calls": [{
                    "id": "delegate-1",
                    "name": "delegate_to_child",
                    "args": {"task": "research this"},
                }],
                "usage": {},
            }
        if name == "agent.queue_delegation":
            return {}
        if name == "agent.acquire_subagent_permit":
            acquire_kwargs.update(kwargs)
            raise asyncio.CancelledError
        raise AssertionError(f"Unexpected activity {name}")

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)

    with pytest.raises(asyncio.CancelledError):
        await AgentWorkflow().run({
            "node_id": "agent-1",
            "execution_id": "root-run",
            "nodes": [],
            "edges": [],
        })

    if patch_enabled:
        assert acquire_kwargs["cancellation_type"] == (
            ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
        )
    else:
        assert "cancellation_type" not in acquire_kwargs


@pytest.mark.parametrize(
    "failing_cleanup",
    [None, "agent.cancel_delegation", "agent.release_subagent_permit"],
)
@pytest.mark.asyncio
async def test_delegated_cancellation_persists_terminal_releases_and_reraises(
    monkeypatch,
    failing_cleanup,
):
    """Cancellation persists a terminal state and releases the permit.

    The terminal transition goes through the dedicated
    ``agent.cancel_delegation.v1`` activity, not ``finish_delegation`` — the
    normal finish path applies the retry/requeue policy, which must not run
    for a cancelled task. Cleanup failures are logged and swallowed so the
    CancelledError still propagates.
    """
    from services.temporal import agent_workflow as agent_module

    temporal_workflow = agent_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="delegated-runner",
            run_id="runner-run-id",
        ),
    )

    child_registered = asyncio.Event()
    calls: list[tuple[str, dict]] = []
    register_count = 0

    async def execute_activity(name, *, args, **_kwargs):
        nonlocal register_count
        calls.append((name, args[0]))
        if name == "agent.register_task_execution":
            register_count += 1
            if register_count == 2:
                child_registered.set()
            return {}
        if name in {
            "agent.acquire_subagent_permit",
            "agent.begin_delegation",
        }:
            return {}
        if name == failing_cleanup:
            raise RuntimeError(f"{name} failed")
        if name in {
            "agent.cancel_delegation",
            "agent.release_subagent_permit",
        }:
            return {}
        raise AssertionError(f"Unexpected activity {name}")

    async def start_child(*_args, **kwargs):
        return _PendingChildHandle(kwargs["id"])

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start_child)

    task = asyncio.create_task(
        DelegatedTaskWorkflow().run({
            "lifecycle": {
                "team_id": "team-1",
                "team_task_id": "task-1",
                "root_execution_id": "root-1",
                "parent_agent_node_id": "agent-1",
                "child_agent_node_id": "agent-2",
            },
            "child_workflow_id": "child-1",
            "child_context": {},
        })
    )
    await asyncio.wait_for(child_registered.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    terminal = next(
        payload
        for name, payload in calls
        if name == "agent.cancel_delegation"
    )
    assert terminal["reason"] == "Delegated task workflow cancelled"
    assert terminal["terminal_event_id"] == "task-1:terminal"
    # The normal finish path applies retry/requeue policy and must never run
    # for a cancellation.
    assert not any(name == "agent.finish_delegation" for name, _ in calls)
    assert any(
        name == "agent.release_subagent_permit"
        for name, _payload in calls
    )


@pytest.mark.parametrize(
    "failed_release_activity_id",
    [None, "release-permit-1-1"],
)
@pytest.mark.asyncio
async def test_agent_cancellation_releases_all_started_delegation_permits(
    monkeypatch,
    failed_release_activity_id,
):
    from services.temporal import agent_workflow as agent_module

    temporal_workflow = agent_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="agent-run",
            run_id="run-id-12345678",
        ),
    )

    children_started = asyncio.Event()
    child_count = 0
    releases: list[tuple[str, str]] = []

    async def execute_activity(name, *, args, **kwargs):
        if name == "agent.prepare_payload":
            return _two_delegation_agent_payload()
        if name == "agent.broadcast_progress":
            return {"emitted": True}
        if name == "agent.execute_llm_step":
            return {
                "kind": "tool_calls",
                "calls": [
                    {
                        "id": "delegate-a",
                        "name": "delegate_to_child_a",
                        "args": {"task": "research A"},
                    },
                    {
                        "id": "delegate-b",
                        "name": "delegate_to_child_b",
                        "args": {"task": "research B"},
                    },
                ],
                "usage": {},
            }
        if name == "agent.release_subagent_permit":
            releases.append(
                (kwargs["activity_id"], args[0]["permit_id"])
            )
            if kwargs["activity_id"] == failed_release_activity_id:
                raise RuntimeError("release failed")
            return {"released": True}
        if name in {
            "agent.queue_delegation",
            "agent.acquire_subagent_permit",
            "agent.begin_delegation",
            "agent.register_task_execution",
        }:
            return {}
        raise AssertionError(f"Unexpected activity {name}")

    async def start_child(*_args, **kwargs):
        nonlocal child_count
        child_count += 1
        if child_count == 2:
            children_started.set()
        return _PendingChildHandle(kwargs["id"])

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start_child)

    task = asyncio.create_task(
        AgentWorkflow().run(
            {
                "node_id": "agent-1",
                "execution_id": "root-run",
                "nodes": [],
                "edges": [],
            }
        )
    )
    await asyncio.wait_for(children_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert releases == [
        (
            "release-permit-1-1",
            "task-root-run-agent-1-1-1",
        ),
        (
            "release-permit-1-2",
            "task-root-run-agent-1-1-2",
        ),
    ]


@pytest.mark.parametrize(
    ("pause_activity", "blocked_command"),
    [
        ("agent.acquire_subagent_permit", "agent.begin_delegation"),
        ("agent.begin_delegation", "start_child_workflow"),
    ],
)
@pytest.mark.asyncio
async def test_agent_rechecks_pause_between_delegation_commands(
    monkeypatch,
    pause_activity,
    blocked_command,
):
    from services.temporal import agent_workflow as agent_module

    temporal_workflow = agent_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="agent-run",
            run_id="run-id-12345678",
        ),
    )

    instance = AgentWorkflow()
    paused_gate = asyncio.Event()
    child_started = asyncio.Event()
    scheduled_commands: list[str] = []
    llm_step = 0

    async def execute_activity(name, *, args, **_kwargs):
        nonlocal llm_step
        if name == "agent.prepare_payload":
            return _delegation_agent_payload()
        if name == "agent.broadcast_progress":
            return {"emitted": True}
        if name == "agent.execute_llm_step":
            llm_step += 1
            if llm_step == 1:
                return {
                    "kind": "tool_calls",
                    "calls": [
                        {
                            "id": "delegate-1",
                            "name": "delegate_to_child",
                            "args": {"task": "research this"},
                        }
                    ],
                    "usage": {},
                }
            return {"kind": "final", "content": "done", "usage": {}}
        if name in {
            "agent.queue_delegation",
            "agent.acquire_subagent_permit",
            "agent.begin_delegation",
            "agent.register_task_execution",
            "agent.release_subagent_permit",
            "agent.finish_delegation",
        }:
            scheduled_commands.append(name)
            if name == pause_activity:
                instance._control_paused = True
            return {}
        if name == "agent.store_output":
            return {"stored": True}
        if name == "agent.skill.clear":
            return {"cleared": True}
        raise AssertionError(f"Unexpected activity {name}")

    async def start_child(*_args, **_kwargs):
        scheduled_commands.append("start_child_workflow")
        child_started.set()
        return _CompletedChildHandle()

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start_child)
    monkeypatch.setattr(
        temporal_workflow,
        "wait_condition",
        lambda predicate: _wait_for_predicate(predicate, paused_gate),
    )

    task = asyncio.create_task(
        instance.run(
            {
                "node_id": "agent-1",
                "execution_id": "root-run",
                "nodes": [],
                "edges": [],
            }
        )
    )
    await asyncio.wait_for(paused_gate.wait(), timeout=1)
    assert blocked_command not in scheduled_commands
    assert not child_started.is_set()

    await instance.resume()
    result = await asyncio.wait_for(task, timeout=1)

    assert child_started.is_set()
    assert result["success"] is True
    assert scheduled_commands == [
        "agent.queue_delegation",
        "agent.acquire_subagent_permit",
        "agent.begin_delegation",
        "start_child_workflow",
        "agent.register_task_execution",
        "agent.release_subagent_permit",
        "agent.finish_delegation",
    ]


def _mixed_preflight_payload() -> dict:
    payload = _delegation_agent_payload()
    payload["tools"].append(
        {
            "name": "manage_tasks",
            "definition": {
                "name": "manage_tasks",
                "description": "Manage team tasks",
                "parameters": {"type": "object", "properties": {}},
            },
            "node_type": "taskManager",
            "version": 1,
            "task_queue": "test",
            "tool_node_id": "task-manager",
            "parameters": {},
            "tool_info": {"label": "Task Manager"},
        }
    )
    return payload


@pytest.mark.asyncio
async def test_agent_rechecks_pause_before_task_manager_preflight_batch(monkeypatch):
    from services.temporal import agent_workflow as agent_module

    temporal_workflow = agent_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="agent-run",
            run_id="run-id-12345678",
        ),
    )

    instance = AgentWorkflow()
    paused_gate = asyncio.Event()
    preflight_started = asyncio.Event()

    async def execute_activity(name, *, args, **kwargs):
        if name == "agent.prepare_payload":
            return _mixed_preflight_payload()
        if name == "agent.broadcast_progress":
            return {"emitted": True}
        if name == "agent.execute_llm_step":
            return {
                "kind": "tool_calls",
                "calls": [
                    {
                        "id": "delegate-1",
                        "name": "delegate_to_child",
                        "args": {"task": "research this"},
                    },
                    {
                        "id": "assign-1",
                        "name": "manage_tasks",
                        "args": {
                            "operation": "assign_task",
                            "task": "follow up",
                        },
                    },
                ],
                "usage": {},
            }
        if (
            name == "agent.release_subagent_permit"
            and kwargs.get("activity_id") == "yield-own-permit-1"
        ):
            instance._control_paused = True
            return {}
        raise AssertionError(f"Unexpected activity {name}")

    def start_activity(*_args, **_kwargs):
        preflight_started.set()
        raise asyncio.CancelledError

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(temporal_workflow, "start_activity", start_activity)
    monkeypatch.setattr(
        temporal_workflow,
        "wait_condition",
        lambda predicate: _wait_for_predicate(predicate, paused_gate),
    )

    task = asyncio.create_task(
        instance.run(
            {
                "node_id": "agent-1",
                "execution_id": "root-run",
                "team_task_id": "own-permit",
                "nodes": [],
                "edges": [],
            }
        )
    )
    await asyncio.wait_for(paused_gate.wait(), timeout=1)
    assert not preflight_started.is_set()

    await instance.resume()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert preflight_started.is_set()


@pytest.mark.asyncio
async def test_agent_rechecks_pause_before_task_manager_child_start(monkeypatch):
    from services.temporal import agent_workflow as agent_module

    temporal_workflow = agent_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="agent-run",
            run_id="run-id-12345678",
        ),
    )
    monkeypatch.setattr(
        agent_module,
        "get_node_class",
        lambda _node_type: SimpleNamespace(needs_canvas=False),
    )

    instance = AgentWorkflow()
    paused_gate = asyncio.Event()
    child_started = asyncio.Event()
    llm_step = 0

    async def execute_activity(name, *, args, **_kwargs):
        nonlocal llm_step
        if name == "agent.prepare_payload":
            return _mixed_preflight_payload()
        if name == "agent.broadcast_progress":
            return {"emitted": True}
        if name == "agent.execute_llm_step":
            llm_step += 1
            if llm_step == 1:
                return {
                    "kind": "tool_calls",
                    "calls": [
                        {
                            "id": "assign-1",
                            "name": "manage_tasks",
                            "args": {
                                "operation": "assign_task",
                                "task": "follow up",
                            },
                        }
                    ],
                    "usage": {},
                }
            return {"kind": "final", "content": "done", "usage": {}}
        if name == "agent.queue_delegation":
            instance._control_paused = True
            return {"queued": True}
        if name == "agent.store_output":
            return {"stored": True}
        if name == "agent.skill.clear":
            return {"cleared": True}
        raise AssertionError(f"Unexpected activity {name}")

    def start_activity(*_args, **_kwargs):
        handle = asyncio.get_running_loop().create_future()
        handle.set_result(
            {
                "delegation_request": {
                    "delegate_name": "delegate_to_child",
                    "assignee_node_id": "child-agent",
                    "team_task_id": "task-1",
                    "task": "follow up",
                    "context": "",
                }
            }
        )
        return handle

    async def start_child(*_args, **_kwargs):
        child_started.set()
        return MagicMock()

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(temporal_workflow, "start_activity", start_activity)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start_child)
    monkeypatch.setattr(
        temporal_workflow,
        "wait_condition",
        lambda predicate: _wait_for_predicate(predicate, paused_gate),
    )

    task = asyncio.create_task(
        instance.run(
            {
                "node_id": "agent-1",
                "execution_id": "root-run",
                "nodes": [],
                "edges": [],
            }
        )
    )
    await asyncio.wait_for(paused_gate.wait(), timeout=1)
    assert not child_started.is_set()

    await instance.resume()
    result = await asyncio.wait_for(task, timeout=1)

    assert child_started.is_set()
    assert result["success"] is True


@pytest.mark.asyncio
async def test_controller_routes_push_events_without_listener_workflow():
    controller = WorkflowControlWorkflow()
    await controller.register_trigger({
        "listener_id": "wf-chat", "workflow_type": "TriggerListenerWorkflow",
        "trigger_node_id": "chat-1", "event_type": "com.opencompany.chat.message.received",
        "event_types": ["com.opencompany.chat.message.received"], "listener_args": {},
    })

    await controller.on_event({
        "id": "event-1", "type": "com.opencompany.chat.message.received", "data": {},
    })
    await controller.on_event({
        "id": "event-1", "type": "com.opencompany.chat.message.received", "data": {},
    })
    await controller.on_event({"id": "event-2", "type": "unrelated", "data": {}})

    assert len(controller._events) == 1
    assert controller._events[0][0] == "wf-chat"
    assert controller.status()["triggers"] == {"wf-chat": "chat-1"}
