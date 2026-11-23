"""Replay recorded Pi updates on both sides of the employee inbox format change."""
import asyncio
import os

from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.llm.protocol import Message, message_to_wire
from services.temporal.pi_protocol import DRIVER_QUEUE, canonical_digest
from services.temporal.pi_workflow import PiCodingAgentWorkflow
from tests.temporal.test_pi_workflow_integration import ProtocolPeer, _broadcast, _clear, _llm, _prepare, _store


PATCH = "pi-instance-message-wire-v1"
SAVED_MESSAGE = {"role": "user", "content": "[task_assignment]\nReview the sample.", "ts": "2026-11-23T08:40:00Z"}


@activity.defn(name="agent.prepare_payload")
async def _prepare_saved_conversation(context):
    payload = await _prepare(context)
    return {**payload, "conversation": [SAVED_MESSAGE], "user_prompt": ""}


async def test_recorded_pi_context_digests_replay_before_and_after_inbox_patch(monkeypatch):
    cli = os.environ.get("TEMPORAL_TEST_CLI")
    environment = (WorkflowEnvironment.start_local(dev_server_existing_path=cli, ip="127.0.0.1")
                   if cli else WorkflowEnvironment.start_time_skipping())
    queue = "pi-inbox-replay"
    context = {"node_id": "agent", "node_type": "coding_agent", "pi_harness_version": 1}
    histories = []
    async with await environment as env:
        for patched in (False, True):
            with monkeypatch.context() as recorder:
                if not patched:
                    # Record a worker that predates this marker. Other version decisions stay real.
                    original = workflow.patched
                    recorder.setattr(workflow, "patched", lambda name: False if name == PATCH else original(name))
                async with Worker(env.client, task_queue=queue, workflows=[PiCodingAgentWorkflow],
                                  activities=[_prepare_saved_conversation, _llm, _broadcast, _clear, _store]), \
                        Worker(env.client, task_queue=DRIVER_QUEUE, activities=[ProtocolPeer(env.client).drive]):
                    handle = await env.client.start_workflow(PiCodingAgentWorkflow.run, context,
                                                             id=f"inbox-wire-{patched}", task_queue=queue)
                    result = await asyncio.wait_for(handle.result(), 30)
                    assert result["success"], result
                    history = await handle.fetch_history()
            markers = [event.marker_recorded_event_attributes for event in history.events
                       if event.HasField("marker_recorded_event_attributes")]
            assert any(PATCH.encode() in marker.SerializeToString() for marker in markers) == patched
            requests = {}
            for event in history.events:
                if event.HasField("activity_task_scheduled_event_attributes"):
                    scheduled = event.activity_task_scheduled_event_attributes
                    if scheduled.activity_type.name in {"coding.pi_drive_turn", "agent.execute_llm_step"}:
                        [request] = await env.client.data_converter.decode(scheduled.input.payloads)
                        requests[scheduled.activity_type.name] = request
            driver = requests["coding.pi_drive_turn"]
            assert driver["messages"] == requests["agent.execute_llm_step"]["messages"]
            assert driver["context_digest"] == canonical_digest(driver["messages"])
            expected = {**message_to_wire(Message(role="user", content=SAVED_MESSAGE["content"])), "ts": SAVED_MESSAGE["ts"]}
            assert driver["messages"][-1] == (expected if patched else SAVED_MESSAGE)
            histories.append(history)

    # Both histories contain digest-bearing driver updates and a completed checkpoint.
    for history in histories:
        replay = await Replayer(workflows=[PiCodingAgentWorkflow]).replay_workflow(history)
        assert replay.replay_failure is None
