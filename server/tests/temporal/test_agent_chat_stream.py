"""The LLM step of an agent answering a chat run (agent.execute_llm_step,
services/temporal/agent_activities.py): its text streams to the run, a run the
owner stopped ends at the next step without paying for it, a stream stopped
midway keeps what was shown as the answer, and the conversation keeps the
turn as far as it went."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from services.llm.protocol import LLMResponse, Message, StreamEvent, ToolCall, Usage, message_to_wire

STREAM = {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf", "reply_message_id": "a_r_1"}


def _payload(**extra: Any) -> Dict[str, Any]:
    return {
        "provider": "anthropic",
        "model": "test-model",
        "api_key": "secret",
        "iteration": 0,
        "messages": [
            message_to_wire(Message(role="system", content="Be brief.")),
            message_to_wire(Message(role="user", content="Book Saturday")),
        ],
        "tools": [],
        "conversation_key": {"workflow_id": "wf", "generation": 1, "agent_node_id": "wf:aiAgent:1"},
        "chat_run_id": "r_1",
        "chat_stream": dict(STREAM),
        "include_finish_reason": True,
        **extra,
    }


@pytest.fixture
def env(monkeypatch, llm_usage):
    """The step's surroundings: the hub, the conversation store and the stop
    flag, all recorded. The step's usage booking is a stand-in (``llm_usage``)."""
    import core.container as container_module
    import services.chat.config as chat_config
    import services.chat.hub as hub_module
    from services.temporal import agent_activities

    published: List[Dict[str, Any]] = []
    saved: List[Dict[str, Any]] = []
    state = {"stopping": False, "polls": 0}

    class Hub:
        def publish(self, **kwargs):
            published.append(kwargs)

    async def save(payload, *, sent, assistant_wire):
        saved.append({"sent": sent, "assistant": assistant_wire})

    async def stopping(run_id):
        state["polls"] += 1
        return state["stopping"]

    real_runs_setting = chat_config.runs_setting
    monkeypatch.setattr(hub_module, "get_chat_hub", lambda: Hub())
    monkeypatch.setattr(agent_activities, "_save_conversation", save)
    monkeypatch.setattr(agent_activities, "_chat_run_stopping", stopping)
    monkeypatch.setattr(chat_config, "runs_setting", lambda name: 0.0 if name == "stop_poll_s" else real_runs_setting(name))
    monkeypatch.setattr(container_module.container, "chat_unifier", lambda: object())
    return type("Env", (), {"published": published, "saved": saved, "state": state})


def _run_step(monkeypatch, step):
    import services.agent_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "run_native_llm_step", step)


def _texts(published):
    return "".join(event["fields"]["delta"] for event in published if event["suffix"] == "text.content")


async def test_the_answer_streams_and_the_result_is_unchanged(env, monkeypatch):
    from services.temporal.agent_activities import _execute_native_llm_step

    async def step(unifier, **kwargs):
        sink = kwargs["on_event"]
        for piece in ["Booked ", "for 10:00."]:
            await sink(StreamEvent("text", piece))
        return LLMResponse(content="Booked for 10:00.", finish_reason="stop", usage=Usage(input_tokens=3, output_tokens=4))

    _run_step(monkeypatch, step)
    result = await _execute_native_llm_step(_payload())
    assert (result["kind"], result["content"], result["finish_reason"]) == ("final", "Booked for 10:00.", "stop")
    assert "stopped" not in result
    assert _texts(env.published) == "Booked for 10:00."
    assert env.published[-1]["suffix"] == "text.ended"
    assert env.published[-1]["fields"] == {"message_id": "r_1.0.1", "final": True, "reply_message_id": "a_r_1"}
    [turn] = env.saved
    assert turn["assistant"]["content"] == "Booked for 10:00."


async def test_text_beside_tool_calls_streams_as_narration(env, monkeypatch):
    from services.temporal.agent_activities import _execute_native_llm_step

    async def step(unifier, **kwargs):
        await kwargs["on_event"](StreamEvent("text", "Checking the calendar."))
        return LLMResponse(
            content="Checking the calendar.",
            tool_calls=[ToolCall(id="call_1", name="calendar", args={})],
            finish_reason="tool_calls",
            usage=Usage(),
        )

    _run_step(monkeypatch, step)
    result = await _execute_native_llm_step(_payload())
    assert result["kind"] == "tool_calls"
    assert env.published[-1]["fields"]["final"] is False


async def test_a_step_that_does_not_answer_the_run_streams_nothing(env, monkeypatch):
    from services.temporal.agent_activities import _execute_native_llm_step

    sinks = []

    async def step(unifier, **kwargs):
        sinks.append(kwargs["on_event"])
        return LLMResponse(content="Done.", usage=Usage())

    _run_step(monkeypatch, step)
    payload = _payload()
    payload.pop("chat_stream")
    payload.pop("chat_run_id")
    await _execute_native_llm_step(payload)
    assert sinks == [None] and env.published == []
    assert env.state["polls"] == 0


async def test_a_stopped_run_ends_before_paying_for_a_step(env, monkeypatch):
    from services.temporal.agent_activities import _execute_native_llm_step

    async def step(unifier, **kwargs):
        raise AssertionError("the model was called for a stopped run")

    _run_step(monkeypatch, step)
    env.state["stopping"] = True
    result = await _execute_native_llm_step(_payload())
    assert (result["kind"], result["content"], result["stopped"], result["finish_reason"]) == ("final", "", True, "stopped")
    assert result["usage"]["total_tokens"] == 0
    # The request is kept (its tool results answer every call); no answer is added.
    [turn] = env.saved
    assert [message["role"] for message in turn["sent"]] == ["system", "user"]
    assert turn["assistant"] is None
    assert env.published == []


async def test_stopping_midway_keeps_what_was_shown(env, monkeypatch):
    from services.temporal.agent_activities import _execute_native_llm_step

    cancelled = asyncio.Event()

    async def step(unifier, **kwargs):
        sink = kwargs["on_event"]
        await sink(StreamEvent("text", "Your Saturday slot is "))
        await sink(StreamEvent("text", "10:00 <follow"))
        env.state["stopping"] = True
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return LLMResponse(content="never", usage=Usage())

    _run_step(monkeypatch, step)
    result = await asyncio.wait_for(_execute_native_llm_step(_payload()), timeout=5)
    assert cancelled.is_set(), "the model call kept running after Stop"
    assert (result["kind"], result["content"], result["stopped"]) == ("final", "Your Saturday slot is 10:00", True)
    # The partial answer is the reply: its segment ends final.
    assert env.published[-1]["suffix"] == "text.ended" and env.published[-1]["fields"]["final"] is True
    [turn] = env.saved
    assert turn["assistant"]["content"] == "Your Saturday slot is 10:00"
    assert turn["assistant"]["blocks"][0]["metadata"] == {"stopped": True}


async def test_a_retried_step_withdraws_the_earlier_attempts_text(env, monkeypatch):
    from services.temporal import agent_activities
    from services.temporal.agent_activities import _execute_native_llm_step

    async def step(unifier, **kwargs):
        await kwargs["on_event"](StreamEvent("text", "Second try."))
        return LLMResponse(content="Second try.", usage=Usage())

    _run_step(monkeypatch, step)
    monkeypatch.setattr(agent_activities, "_llm_activity_attempt", lambda: 2)
    await _execute_native_llm_step(_payload(iteration=3))
    first = env.published[0]
    assert (first["suffix"], first["fields"]["value"]) == ("custom", {"message_id": "r_1.3.1"})
    assert env.published[1]["fields"]["message_id"] == "r_1.3.2"
