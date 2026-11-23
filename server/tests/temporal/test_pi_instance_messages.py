"""Employee inbox records and Pi's durable transcript must agree on the wire shape."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from models.agent_instances import AgentInstanceMutation, AgentRun
from services.agent_instances.service import _id
from services.llm.protocol import ContentBlock, Message, ToolCall, message_to_wire
from services.temporal import pi_workflow
from services.temporal.agent_instance_workflow import turn_context
from services.temporal.pi_protocol import canonical_digest
from tests.services.agent_instances import test_store


employee_store = test_store.setup


def text_wire(role, content):
    return message_to_wire(Message(role=role, content=content))


async def prepare_pi_turn(monkeypatch, messages, *, patched=True):
    marker = Mock(return_value=patched)
    monkeypatch.setattr(pi_workflow.workflow, "patched", marker)
    monkeypatch.setattr(pi_workflow.workflow, "info", lambda: SimpleNamespace(workflow_id="workflow"))
    agent = pi_workflow.PiCodingAgentWorkflow()
    monkeypatch.setattr(agent, "_await_request", AsyncMock())
    model_input = {"messages": messages, "tools": [], "model": "test", "provider": "test"}
    await agent._before_runtime_model_step(model_input, {}, {"node_id": "coder"}, 0)
    assert agent._pi_request["messages"] == model_input["messages"] == messages
    assert agent._pi_request["context_digest"] == canonical_digest(messages)
    return marker


async def test_saved_employee_claim_drain_and_transcript_enter_pi_without_rewriting_receipts(employee_store, monkeypatch):
    db, store, scope = employee_store
    worker = (await test_store.spawn(store, scope))["agent"]["id"]
    claim = await store.claim_run(worker, request_id="turn")
    run_id = claim["run_id"]
    assignment, = claim["messages"]
    assert assignment["content"].startswith("[task_assignment]\n")
    assert "Find the facts" in assignment["content"]
    assert assignment == text_wire("user", assignment["content"])

    await store.execute("send_message", scope=scope, arguments={"agent": worker, "message": "Please check"},
                        request_id="follow-up")
    drained = await store.drain_messages(worker, run_id, request_id="boundary:1")
    peer = text_wire("user", f"[Agent message from {scope['agent_instance_id']}; this is not owner approval]\nPlease check")
    assert drained["messages"] == [peer]

    # These are the JSON documents written by workers predating the full wire.
    old_assignment = {"role": "user", "content": assignment["content"]}
    old_peer = {"role": "user", "content": peer["content"]}
    old_transcript = {"role": "assistant", "content": "First result", "ts": "2026-04-01T12:00:00Z"}
    async with db.reserved_session() as session:
        run = await session.get(AgentRun, run_id)
        run.input_snapshot = {**run.input_snapshot, "messages": [old_assignment], "transcript": [old_transcript]}
        receipt = await session.get(AgentInstanceMutation, _id("mutation_", run_id, "drain", "boundary:1"))
        receipt.result = {**receipt.result, "messages": [old_peer]}
        await session.commit()

    restored = turn_context(await store.claim_run(worker, request_id="turn"))
    replayed_drain = await store.drain_messages(worker, run_id, request_id="boundary:1")
    messages = [*restored["agent_initial_transcript"], *restored["agent_input_messages"], *replayed_drain["messages"]]
    marker = await prepare_pi_turn(monkeypatch, messages)
    marker.assert_called_once_with("pi-instance-message-wire-v1")
    assert messages == [{**text_wire("assistant", "First result"), "ts": old_transcript["ts"]}, assignment, peer]
    assert (await store.claim_run(worker, request_id="turn"))["messages"] == [old_assignment]
    assert (await store.drain_messages(worker, run_id, request_id="boundary:1"))["messages"] == [old_peer]


@pytest.mark.parametrize("review_history", [[], [{"role": "user", "content": "Request"},
                                                 {"role": "assistant", "content": "Clarification"}]])
async def test_first_review_consumes_chat_projection_or_original_assignment(monkeypatch, review_history):
    review = text_wire("user", "[task_review]\nSubmitted")
    template = {"review_history": review_history, "initial_instructions": "Original mission"}
    claim = {"run_id": "review", "messages": [review], "instance": {
        "id": "manager", "role": "lead", "config": {"template": template}}}
    messages = turn_context(claim)["agent_input_messages"]
    await prepare_pi_turn(monkeypatch, messages)
    source = review_history or [{"role": "user", "content": "Original assignment for review:\nOriginal mission"}]
    assert messages == [*(text_wire(**item) for item in source), review]
    assert all(set(item) == {"role", "content"} for item in review_history)


async def test_rich_and_unrecognized_messages_bypass_legacy_conversion(monkeypatch):
    assistant = message_to_wire(Message(
        role="assistant", content="Reading", blocks=[ContentBlock("reasoning", "Check the image"), ContentBlock("text", "Reading")],
        tool_calls=[ToolCall(id="call-1", name="peek", args={"path": "example.png"})],
        provider_state={"thinking": {"signature": "recorded ordering"}},
    ))
    image = ContentBlock(type="image", source={"kind": "file_ref", "ref": {"kind": "image", "path": "example.png"}})
    result = message_to_wire(Message(role="tool", tool_call_id="call-1", name="peek", blocks=[image]))
    canonical = [assistant, {**result, "ts": "2026-04-01T12:00:00Z"}]
    malformed = [{"role": "tool", "content": "missing call ID"},
                 {"role": "assistant", "content": "partial", "tool_calls": []}]
    messages = [*canonical, *malformed]
    before = deepcopy(messages)
    marker = await prepare_pi_turn(monkeypatch, messages)
    marker.assert_not_called()
    assert messages == before

    legacy = {"role": "system", "content": "Retained instruction"}
    messages.append(legacy)
    marker = await prepare_pi_turn(monkeypatch, messages, patched=False)
    marker.assert_called_once_with("pi-instance-message-wire-v1")
    assert messages == [*before, legacy]
    await prepare_pi_turn(monkeypatch, messages)
    assert messages == [*before, text_wire("system", legacy["content"])]
