"""A provider request never depends on a copy of the tool calls in ``blocks``.

A message's tool calls are its ``tool_calls``. Messages written before
2026-11-19 also repeated each call in ``blocks``, which no request encoder
read and which doubled the arguments' bytes in every stored transcript.
Messages written since carry no such copy, but conversations saved earlier
and the histories of runs still going do, so both shapes meet in one request.

Locked here, for every provider:

* an answer with tool calls is stored with each call once;
* the request built from a stored conversation is the same whether or not
  its assistant turns repeat their calls in ``blocks``.

OpenAI, OpenRouter and Anthropic run their real SDK over a mocked HTTP layer
(respx), so what is compared is the request body sent. Gemini's SDK client is
a mock, so there it is the arguments the provider hands the SDK.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from services.llm.messages import filter_empty_messages
from services.llm.protocol import (
    ContentBlock,
    Message,
    ToolCall,
    ToolDef,
    message_from_wire,
    message_to_wire,
    messages_from_wire,
    messages_to_wire,
)
from services.llm.providers.anthropic import AnthropicProvider
from services.llm.providers.gemini import GeminiProvider
from services.llm.providers.openai import OpenAIProvider
from services.llm.providers.openrouter import OpenRouterProvider

TOOLS = [
    ToolDef(
        name="lookup",
        description="Look something up",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )
]
ARGS = {"q": "weather in Pune"}

# Each provider with a model that takes its usual route: gpt-4o goes to Chat
# Completions, and gpt-5 with tools goes to the Responses API.
HTTP_PROVIDERS = {
    "openai chat": (OpenAIProvider, "gpt-4o"),
    "openai responses": (OpenAIProvider, "gpt-5"),
    "openrouter": (OpenRouterProvider, "anthropic/claude-sonnet-5.5"),
    "anthropic": (AnthropicProvider, "claude-sonnet-5-5"),
}
PROVIDERS = [*HTTP_PROVIDERS, "gemini"]


# ---- what each provider answers: some text and one tool call ----------------

CHAT_COMPLETION = {
    "id": "gen-9",
    "object": "chat.completion",
    "created": 0,
    "model": "any",
    "choices": [
        {
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "Let me check.",
                "tool_calls": [
                    {"id": "call-9", "type": "function", "function": {"name": "lookup", "arguments": json.dumps(ARGS)}}
                ],
            },
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

RESPONSE = {
    "id": "resp_9",
    "object": "response",
    "created_at": 0,
    "model": "gpt-5",
    "status": "completed",
    "output": [
        {
            "type": "message",
            "id": "msg_9",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "Let me check.", "annotations": []}],
        },
        {
            "type": "function_call",
            "id": "fc_9",
            "call_id": "call-9",
            "name": "lookup",
            "arguments": json.dumps(ARGS),
            "status": "completed",
        },
    ],
    "parallel_tool_calls": True,
    "tool_choice": "auto",
    "tools": [],
    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
}

ANTHROPIC_EVENTS = [
    {"type": "message_start", "message": {
        "id": "msg_9", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Let me check."}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {
        "type": "tool_use", "id": "call-9", "name": "lookup", "input": {},
    }},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": json.dumps(ARGS)}},
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 5}},
    {"type": "message_stop"},
]


def anthropic_stream() -> httpx.Response:
    lines = [f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in ANTHROPIC_EVENTS]
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(lines))


def gemini_answer() -> SimpleNamespace:
    text = SimpleNamespace(thought=False, text="Let me check.", function_call=None, thought_signature=None)
    call = SimpleNamespace(
        thought=False,
        text=None,
        function_call=SimpleNamespace(id="call-9", name="lookup", args=dict(ARGS)),
        thought_signature=None,
    )
    candidate = SimpleNamespace(content=SimpleNamespace(parts=[text, call]), finish_reason="STOP")
    return SimpleNamespace(response_id="response-9", candidates=[candidate], usage_metadata=None)


@pytest.fixture
def provider_api():
    """The providers' HTTP APIs, each answering with some text and one tool call."""
    with respx.mock(assert_all_called=False) as api:
        api.post("https://api.openai.com/v1/chat/completions").respond(json=CHAT_COMPLETION)
        api.post("https://api.openai.com/v1/responses").respond(json=RESPONSE)
        api.post("https://openrouter.ai/api/v1/chat/completions").respond(json=CHAT_COMPLETION)
        api.post("https://api.anthropic.com/v1/messages").mock(return_value=anthropic_stream())
        yield api


# ---- sending one request ----------------------------------------------------


async def send_over_http(api, name, messages):
    """Send one request with tools; return the body sent and the answer."""
    provider_class, model = HTTP_PROVIDERS[name]
    provider = provider_class("sk-test", max_retries=0)
    answer = await provider.chat(messages, model=model, tools=TOOLS)
    await provider._client.close()
    return api.calls.last.request.content, answer


async def send_to_gemini(messages):
    """The same for Gemini: return what the provider handed the SDK and the answer."""
    with patch("google.genai.Client"):
        provider = GeminiProvider("key")
    generate_content = AsyncMock(return_value=gemini_answer())
    provider._client.aio.models.generate_content = generate_content
    answer = await provider.chat(messages, model="gemini-3-flash", tools=TOOLS)
    sent = generate_content.call_args.kwargs
    request = {"model": sent["model"], "contents": sent["contents"], "config": sent["config"].model_dump()}
    return request, answer


async def send(api, name, messages):
    if name == "gemini":
        return await send_to_gemini(messages)
    return await send_over_http(api, name, messages)


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_an_answer_with_tool_calls_is_stored_with_each_call_once(provider_api, provider):
    question = [Message(role="user", content="What is the weather in Pune?")]

    _, answer = await send(provider_api, provider, question)

    wire = message_to_wire(answer.assistant_message)
    assert [call["id"] for call in wire["tool_calls"]] == ["call-9"]
    assert wire["tool_calls"][0]["args"] == ARGS
    assert [block["type"] for block in wire["blocks"]] == ["text"]


# ---- a stored conversation, in both shapes ----------------------------------

LOOKUP = ToolCall(id="call-1", name="lookup", args=dict(ARGS))
# A call the provider sent as a string keeps that string (raw_arguments).
SECOND_LOOKUP = ToolCall.from_raw(id="call-2", name="lookup", arguments='{"q": "weather in Mumbai"}')
MALFORMED_LOOKUP = ToolCall.from_raw(id="call-3", name="lookup", arguments='{"q":')

# Assistant turns that call tools, by what else they hold.
ASSISTANT_TURNS = {
    "text beside its calls": Message(role="assistant", content="Let me check.", tool_calls=[LOOKUP, SECOND_LOOKUP]),
    "calls alone": Message(role="assistant", tool_calls=[LOOKUP, SECOND_LOOKUP]),
    "a malformed call": Message(role="assistant", tool_calls=[MALFORMED_LOOKUP]),
    "anthropic's signed thinking": Message(
        role="assistant",
        content="Let me check.",
        tool_calls=[LOOKUP],
        blocks=[
            ContentBlock(type="reasoning", text="Need fresh data", metadata={"signature": "signed"}),
            ContentBlock(type="text", text="Let me check."),
        ],
        provider_state={"provider": "anthropic", "payload": {"content": [
            {"type": "thinking", "thinking": "Need fresh data", "signature": "signed"},
            {"type": "tool_use", "id": "call-1", "name": "lookup", "input": dict(ARGS)},
            {"type": "text", "text": "Let me check."},
        ]}},
    ),
    "openrouter's reasoning details": Message(
        role="assistant",
        tool_calls=[LOOKUP],
        blocks=[ContentBlock(type="reasoning", text="Need fresh data")],
        provider_state={"provider": "openrouter", "payload": {
            "reasoning": "Need fresh data",
            "reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque"}],
        }},
    ),
    "gemini's thought signature": Message(
        role="assistant",
        tool_calls=[LOOKUP],
        blocks=[ContentBlock(type="reasoning", text="Need fresh data", metadata={"thought_signature_b64": "c2lnbmVk"})],
        provider_state={"provider": "gemini", "payload": {"parts": [
            {"text": "Need fresh data", "thought": True, "thought_signature_b64": "c2lnbmVk"},
            {"function_call": {"name": "lookup", "args": dict(ARGS), "id": "call-1"}},
        ]}},
    ),
    "openai's encrypted reasoning": Message(
        role="assistant",
        tool_calls=[LOOKUP],
        blocks=[ContentBlock(type="reasoning", text="Need the tool")],
        provider_state={"provider": "openai", "payload": {
            "api": "responses",
            "response_id": "resp_1",
            "output": [
                {"type": "reasoning", "id": "rs_1", "encrypted_content": "encrypted",
                 "summary": [{"type": "summary_text", "text": "Need the tool"}]},
                {"type": "function_call", "id": "fc_1", "call_id": "call-1", "name": "lookup",
                 "arguments": json.dumps(ARGS), "status": "completed"},
            ],
        }},
    ),
}


def conversation_around(assistant: Message) -> list:
    """A stored conversation around one assistant turn, as it is written now."""
    tool_results = [
        Message(role="tool", content=json.dumps({"answer": f"for {call.id}"}), tool_call_id=call.id, name=call.name)
        for call in assistant.tool_calls
    ]
    messages = [
        Message(role="system", content="You look things up for the owner."),
        Message(role="user", content="What is the weather in Pune?"),
        assistant,
        *tool_results,
        Message(role="user", content="And tomorrow?"),
    ]
    return [dict(wire) for wire in messages_to_wire(messages)]


def with_calls_repeated_in_blocks(wire: dict) -> dict:
    """``wire`` as it was stored before: each of its calls also a block."""
    message = message_from_wire(wire)
    for call in message.tool_calls:
        message.blocks.append(ContentBlock(type="tool_call", tool_call=call))
    return dict(message_to_wire(message))


@pytest.mark.parametrize("turn", ASSISTANT_TURNS)
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_the_request_is_the_same_whether_or_not_blocks_repeat_the_calls(provider_api, provider, turn):
    stored = conversation_around(ASSISTANT_TURNS[turn])
    stored_before = [with_calls_repeated_in_blocks(wire) for wire in stored]
    assert stored_before != stored

    request, _ = await send(provider_api, provider, filter_empty_messages(messages_from_wire(stored)))
    request_before, _ = await send(provider_api, provider, filter_empty_messages(messages_from_wire(stored_before)))

    assert request
    assert request_before == request
