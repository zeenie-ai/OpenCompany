"""Reply in Chat (``chatReply``): posts an answer to the thread of the
workflow it runs in, as the assistant, in the live generation, and says so
on ``chat.updated``; posts nothing for an empty answer or NO_REPLY. In a run
the owner's chat message started (``run_scope``), the answer is that chat
run's reply (tests/services/chat/ cover the ledger itself)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.node_contract


@pytest.fixture
def thread(harness):
    """The live generation and the frames broadcast."""
    frames: list = []

    class Broadcaster:
        async def broadcast(self, message):
            frames.append(message)

    harness.database.get_latest_workflow_control = AsyncMock(return_value=SimpleNamespace(status="running", root_execution_id="gen-2"))
    harness.database.add_chat_message = AsyncMock(return_value={"uid": "m_1", "message": "saved"})
    with patch("services.status_broadcaster.get_status_broadcaster", return_value=Broadcaster()):
        yield SimpleNamespace(database=harness.database, frames=frames)


async def _reply(harness, message, *, workflow_id="wf-1", run_scope=None):
    context = harness.build_context(workflow_id=workflow_id)
    if run_scope is not None:
        context["run_scope"] = run_scope
    return await harness.execute("chatReply", {"message": message}, context=context)


async def test_the_answer_lands_in_the_workflows_thread(harness, thread):
    result = await _reply(harness, "  Booked you for 3pm.  ")
    harness.assert_envelope(result, success=True)
    assert result["result"] == {"posted": True, "message": "Booked you for 3pm.", "message_id": "m_1", "run_id": None}
    thread.database.add_chat_message.assert_awaited_once_with(
        "wf-1", "assistant", "Booked you for 3pm.", execution_id="gen-2",
        uid=None, run_id=None, kind="text", status="complete", parts=None,
    )
    [frame] = thread.frames
    assert frame["type"] == "chat.updated"
    assert frame["data"]["type"] == "com.opencompany.chat.updated"
    assert frame["data"]["data"] == {"workflow_id": "wf-1", "session_id": "wf-1", "role": "assistant"}


@pytest.mark.parametrize("message", ["", "   ", "NO_REPLY", " NO_REPLY\n", None])
async def test_nothing_to_say_posts_nothing(harness, thread, message):
    result = await _reply(harness, message)
    harness.assert_envelope(result, success=True)
    assert result["result"]["posted"] is False
    thread.database.add_chat_message.assert_not_awaited()
    assert thread.frames == []


async def test_with_no_message_it_posts_what_the_connected_agent_answered(harness, thread):
    # Wired from an agent in the editor without a template, the answer used
    # to stream in and then vanish: the node posted nothing.
    nodes = [{"id": "a", "type": "aiAgent"}, {"id": "r", "type": "chatReply"}]
    edges = [{"source": "a", "target": "r", "sourceHandle": "output-main", "targetHandle": "input-main"}]

    async def reply(answer):
        context = harness.build_context(
            workflow_id="wf-1", nodes=nodes, edges=edges, upstream_outputs={"a::output_main": {"response": answer, "model": "m"}}
        )
        return await harness.execute("chatReply", {"message": ""}, node_id="r", context=context)

    result = await reply("  Booked you for 3pm.  ")
    harness.assert_envelope(result, success=True)
    assert thread.database.add_chat_message.await_args.args[2] == "Booked you for 3pm."
    # An agent with nothing to say still posts nothing.
    thread.database.add_chat_message.reset_mock()
    result = await reply("NO_REPLY")
    assert result["result"]["posted"] is False
    thread.database.add_chat_message.assert_not_awaited()


async def test_an_answer_that_is_not_text_is_posted_as_text(harness, thread):
    # A whole-value template keeps the upstream value's type.
    result = await _reply(harness, {"items": ["milk", "eggs"]})
    harness.assert_envelope(result, success=True)
    assert thread.database.add_chat_message.await_args.args[2] == '{"items": ["milk", "eggs"]}'


async def test_it_needs_a_saved_workflow(harness, thread):
    result = await _reply(harness, "hello", workflow_id=None)
    harness.assert_envelope(result, success=False)
    assert "save the workflow" in result["error"]
    thread.database.add_chat_message.assert_not_awaited()


async def test_a_failed_save_is_an_error_and_announces_nothing(harness, thread):
    thread.database.add_chat_message = AsyncMock(return_value=None)
    result = await _reply(harness, "hello")
    harness.assert_envelope(result, success=False)
    assert thread.frames == []


async def test_in_a_chat_run_the_answer_is_the_runs_reply(harness, thread):
    run = SimpleNamespace(run_id="r_1", session_id="wf-1", reply_message_uid="a_r_1")
    with (
        patch("services.chat.ledger.get_run", AsyncMock(return_value=run)) as get_run,
        patch("services.chat.ledger.post_reply", AsyncMock(return_value={"uid": "a_r_1"})) as post_reply,
    ):
        result = await _reply(harness, "Booked.", run_scope={"run_id": "r_1", "session_id": "wf-1"})
    harness.assert_envelope(result, success=True)
    assert result["result"] == {"posted": True, "message": "Booked.", "message_id": "a_r_1", "run_id": "r_1"}
    assert get_run.await_args.args[1] == "r_1"
    kwargs = post_reply.await_args.kwargs
    assert (kwargs["run"], kwargs["text"], kwargs["execution_id"]) == (run, "Booked.", "gen-2")
    assert kwargs["node_id"]
    thread.database.add_chat_message.assert_not_awaited()
    assert [frame["data"]["data"]["role"] for frame in thread.frames] == ["assistant"]


async def test_a_run_whose_conversation_was_cleared_posts_nothing(harness, thread):
    with (
        patch("services.chat.ledger.get_run", AsyncMock(return_value=None)),
        patch("services.chat.ledger.post_reply", AsyncMock()) as post_reply,
    ):
        result = await _reply(harness, "Too late.", run_scope={"run_id": "r_gone", "session_id": "wf-1"})
    harness.assert_envelope(result, success=True)
    assert result["result"] == {"posted": False, "message": None, "message_id": None, "run_id": "r_gone"}
    post_reply.assert_not_awaited()
    thread.database.add_chat_message.assert_not_awaited()
    assert thread.frames == []


def test_it_is_a_sink_the_hire_builder_may_use():
    import nodes  # noqa: F401
    from services.node_allowlist import CONFIG_PATH, NodeAllowlistService
    from services.node_spec import get_node_spec

    spec = get_node_spec("chatReply")
    assert spec["displayName"] == "Reply in Chat"
    assert [handle["name"] for handle in spec["handles"]] == ["input-main"]
    assert spec.get("hideOutputHandle") is True
    assert isinstance(spec["uiHints"]["executionTimeoutMs"], int)
    assert "isConsoleSink" not in spec["uiHints"]
    service = NodeAllowlistService(config_path=CONFIG_PATH)
    assert service.is_hire_allowed("chatReply") and service.is_hire_allowed("agentBuilder")
