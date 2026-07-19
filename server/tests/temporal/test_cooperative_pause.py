"""Cooperative pause flags shared by Temporal orchestration workflows."""

import pytest

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
    ],
)
async def test_pause_and_resume_mutate_durable_workflow_state(workflow_type):
    instance = workflow_type()

    assert instance._control_paused is False
    await instance.pause()
    assert instance._control_paused is True
    await instance.resume()
    assert instance._control_paused is False


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
