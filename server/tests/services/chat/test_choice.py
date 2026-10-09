"""The model and thinking the owner picks in Home's employee chat
(services/chat/choice.py): what a message's run keeps, the refusals, the
picker's rows, the saved choice, and the choice riding a message, an edit and
a retry."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.chat import choice, ledger
from tests.services.chat._helpers import talking

CLAIM = dict(temporal_workflow_id="tw-1", temporal_run_id="tr-1")
SONNET = "anthropic::claude-sonnet-5-5"
OPUS = "anthropic::claude-opus-5-5"
GPT = "openai::gpt-6-sol"
AGENT = "wf:aiAgent:1"
GPT_UNAVAILABLE = "GPT-6 Sol can’t answer right now: OpenAI isn’t connected. Connect it in Settings > Connectors, or pick another model."


@pytest.fixture
def connected(monkeypatch):
    """The providers with a key: Anthropic, unless a test changes the set."""
    providers = {"anthropic"}

    async def fake(auth, principal):
        return set(providers)

    monkeypatch.setattr(choice, "_connected", fake)
    return providers


def talk_graph(agent_type: str = "aiAgent"):
    """Chat -> the agent -> Reply in Chat: the agent answers the owner."""
    return {
        "nodes": [
            {"id": "wf:chatTrigger:1", "type": "chatTrigger"},
            {"id": AGENT, "type": agent_type},
            {"id": "wf:chatReply:1", "type": "chatReply"},
        ],
        "edges": [
            {"id": "e1", "source": "wf:chatTrigger:1", "target": AGENT, "targetHandle": "input-main"},
            {"id": "e2", "source": AGENT, "target": "wf:chatReply:1", "targetHandle": "input-main"},
        ],
    }


async def options(database, raw):
    return await choice.run_options(database, None, raw, principal=None)


async def test_a_run_keeps_web_the_effort_and_a_connected_model(database, connected):
    assert await options(database, None) == {}
    assert await options(database, {"web": False}) == {"web": False}
    assert await options(database, {"web": "no"}) == {}
    assert await options(database, {"model": SONNET, "effort": "high"}) == {"model": SONNET, "effort": "high"}
    connected.add("openai")
    assert await options(database, {"model": GPT, "effort": "low", "web": True}) == {"model": GPT, "effort": "low", "web": True}


async def test_auto_is_the_owners_default_model(database, connected):
    # No default: Auto changes nothing, and the employee answers on its own model.
    assert await options(database, {"model": "auto", "effort": "low"}) == {"effort": "low"}
    await database.save_user_settings({"default_llm_provider": "anthropic", "default_llm_model": "claude-opus-5-5"})
    assert await options(database, {"model": "auto"}) == {"model": OPUS}
    # A default whose provider isn't connected is refused, never swapped.
    await database.save_user_settings({"default_llm_provider": "openai", "default_llm_model": "gpt-6-sol"})
    with pytest.raises(choice.ChoiceRefused) as refused:
        await options(database, {"model": "auto"})
    assert refused.value.detail == (
        "Your default model, GPT-6 Sol, can’t answer right now: OpenAI isn’t connected. "
        "Connect it in Settings > Connectors, or pick another model."
    )


async def test_what_cannot_answer_is_refused(database, connected):
    with pytest.raises(choice.ChoiceRefused) as refused:
        await options(database, {"model": GPT})
    assert refused.value.detail == GPT_UNAVAILABLE
    with pytest.raises(choice.ChoiceRefused, match="isn’t offered"):
        await options(database, {"model": "anthropic::claude-haiku-4-5"})
    with pytest.raises(ValueError, match="Quick, Balanced or Thorough"):
        await options(database, {"effort": "medium"})
    with pytest.raises(ValueError):
        await options(database, {"model": 5})


def test_a_saved_choice_keeps_to_what_the_picker_offers():
    choice.check_settings({"chat_model": "auto", "chat_effort": ""})
    choice.check_settings({"chat_model": SONNET, "chat_effort": "high"})
    choice.check_settings({"theme": "dark"})
    with pytest.raises(choice.ChoiceRefused, match="isn’t offered"):
        choice.check_settings({"chat_model": "openai::gpt-4.1"})
    with pytest.raises(choice.ChoiceRefused, match="Quick, Balanced or Thorough"):
        choice.check_settings({"chat_effort": "max"})


def test_the_model_and_effort_a_run_chose():
    assert choice.model_of({"model": SONNET}) == ("anthropic", "claude-sonnet-5-5")
    assert choice.model_of({}) is None and choice.model_of({"model": "auto"}) is None
    assert choice.effort_of({"effort": "low"}) == "low"
    assert choice.effort_of({"effort": ""}) is None and choice.effort_of({}) is None


async def test_the_picker_lists_connected_models_and_what_auto_uses(database, connected):
    import nodes  # noqa: F401 - the plugin registry (which nodes are agents)

    await database.save_node_parameters(AGENT, {"provider": "gemini", "model": "gemini-3.8-flash"})
    picker = await choice.list_models(database, None, principal=None, graph=talk_graph(), name="Maya")
    auto = picker["auto"]
    assert (auto["id"], auto["name"], auto["description"]) == ("auto", "Auto", "Uses Gemini 3.8 Flash, Maya’s own model")
    assert auto["available"] is True and auto["effort"] is True and auto["effort_note"] is None
    assert [row["id"] for row in picker["models"]] == [SONNET, OPUS]
    sonnet = picker["models"][0]
    assert (sonnet["short"], sonnet["description"], sonnet["available"], sonnet["effort"]) == (
        "Sonnet 5.5",
        "Great for everyday work",
        True,
        True,
    )
    assert [(effort["id"], effort["label"]) for effort in picker["efforts"]] == [("low", "Quick"), ("", "Balanced"), ("high", "Thorough")]

    # A default the owner set; the saved choice shows even while its
    # provider isn't connected, and can't be picked.
    await database.save_user_settings(
        {"default_llm_provider": "openai", "default_llm_model": "gpt-6-sol", "chat_model": GPT, "chat_effort": "high"}
    )
    picker = await choice.list_models(database, None, principal=None, graph=talk_graph(), name="Maya")
    assert picker["auto"]["description"] == "Uses GPT-6 Sol, your default model"
    assert picker["auto"]["available"] is False and "OpenAI isn’t connected" in picker["auto"]["reason"]
    rows = {row["id"]: row for row in picker["models"]}
    assert list(rows) == [SONNET, OPUS, GPT]
    assert rows[GPT]["available"] is False and rows[GPT]["reason"] == GPT_UNAVAILABLE

    # A choice saved before the picker stopped offering it.
    await database.save_user_settings({"chat_model": "openai::gpt-4.1"})
    picker = await choice.list_models(database, None, principal=None, graph=talk_graph(), name="Maya")
    gone = picker["models"][-1]
    assert (gone["id"], gone["available"], gone["reason"]) == ("openai::gpt-4.1", False, "That model isn’t offered any more. Pick another one.")


async def test_a_model_without_effort_says_it_sets_its_own_pace(database, connected):
    import nodes  # noqa: F401

    await database.save_node_parameters(AGENT, {"provider": "anthropic", "model": "claude-haiku-4-5"})
    picker = await choice.list_models(database, None, principal=None, graph=talk_graph(), name="Maya")
    assert picker["auto"]["effort"] is False
    assert picker["auto"]["effort_note"] == "claude-haiku-4-5 sets its own pace"


async def test_the_choice_rides_a_message_an_edit_and_a_retry(chat, connected):
    await talking(chat.database)
    sent = await chat.handlers.handle_send_chat_message(
        {"message": "Book Saturday", "session_id": "wf", "options": {"model": SONNET, "effort": "high", "web": False}}, None
    )
    assert sent["success"] is True
    assert (await ledger.get_run(chat.database, sent["run_id"])).options == {"web": False, "effort": "high", "model": SONNET}
    run = await ledger.start_run(chat.database, run_id=sent["run_id"], **CLAIM)
    await ledger.post_reply(chat.database, run=run, node_id="wf:chatReply:1", text="Booked.", execution_id="gen-1")
    await ledger.finish_run(chat.database, run_id=sent["run_id"], success=True, **CLAIM)

    shown = (await chat.handlers.handle_get_chat_messages({"session_id": "wf"}, None))["messages"]
    retried = await chat.handlers.handle_regenerate_chat_reply(
        {"session_id": "wf", "message_id": shown[1]["id"], "options": {"model": "auto", "effort": "low"}}, None
    )
    assert retried["success"] is True
    # Auto with no default set: the employee's own model, at the effort picked.
    assert (await ledger.get_run(chat.database, retried["run_id"])).options == {"effort": "low"}
    run = await ledger.start_run(chat.database, run_id=retried["run_id"], **CLAIM)
    await ledger.post_reply(chat.database, run=run, node_id="wf:chatReply:1", text="Booked again.", execution_id="gen-1")
    await ledger.finish_run(chat.database, run_id=retried["run_id"], success=True, **CLAIM)

    edited = await chat.handlers.handle_edit_chat_message(
        {"session_id": "wf", "message_id": sent["message_id"], "message": "Book Sunday", "options": {"model": OPUS}}, None
    )
    assert edited["success"] is True
    assert (await ledger.get_run(chat.database, edited["run_id"])).options == {"model": OPUS}


async def test_a_model_that_cannot_answer_saves_nothing(chat, connected):
    await talking(chat.database)
    refused = await chat.handlers.handle_send_chat_message({"message": "Hi", "session_id": "wf", "options": {"model": GPT}}, None)
    assert refused == {"success": False, "error": "model_unavailable", "detail": GPT_UNAVAILABLE}
    assert (await chat.handlers.handle_get_chat_messages({"session_id": "wf"}, None))["messages"] == []
    bad = await chat.handlers.handle_send_chat_message({"message": "Hi", "session_id": "wf", "options": {"effort": "max"}}, None)
    assert bad["success"] is False and bad["error"] == "invalid_request"


async def test_the_picker_and_whether_the_employee_takes_it(chat, connected, monkeypatch):
    import core.config
    import nodes  # noqa: F401

    await chat.database.save_workflow("wf", "Maya", "Maya_1", talk_graph())
    await talking(chat.database)
    picker = await chat.handlers.handle_get_chat_models({"session_id": "wf"}, None)
    assert picker["success"] is True and picker["session_id"] == "wf"
    # The agent has no model saved: its provider's default (OpenAI's) answers.
    assert picker["auto"]["description"] == "Uses GPT-6 Sol, Maya’s own model"
    assert [row["id"] for row in picker["models"]] == [SONNET, OPUS]
    default = await chat.handlers.handle_get_chat_models({"session_id": "default"}, None)
    assert default["success"] is False and default["error"] == "invalid_request"

    # The agent that answers runs as an AgentWorkflow: it applies the choice.
    monkeypatch.setattr(core.config, "Settings", lambda: SimpleNamespace(temporal_agent_workflow_enabled=True))
    context = await chat.handlers.handle_get_chat_context({"session_id": "wf"}, None)
    assert context["capabilities"]["model_choice"] is True
    # Not with the agent workflow off, nor for an agent that never runs as one.
    monkeypatch.setattr(core.config, "Settings", lambda: SimpleNamespace(temporal_agent_workflow_enabled=False))
    assert (await chat.handlers.handle_get_chat_context({"session_id": "wf"}, None))["capabilities"]["model_choice"] is False
    monkeypatch.setattr(core.config, "Settings", lambda: SimpleNamespace(temporal_agent_workflow_enabled=True))
    await chat.database.save_workflow("wf", "Maya", "Maya_1", talk_graph("claude_code_agent"))
    assert (await chat.handlers.handle_get_chat_context({"session_id": "wf"}, None))["capabilities"]["model_choice"] is False
