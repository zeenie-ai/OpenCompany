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

OpenAI, OpenRouter and Anthropic requests go through each SDK's real
transport, so what is compared is the bytes sent. Gemini's SDK client is a
mock, so there it is the arguments the provider hands the SDK.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anthropic
import httpx
import openai
import pytest

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

PROVIDERS = ("openai chat", "openai responses", "openrouter", "anthropic", "gemini")

TOOLS = [
    ToolDef(
        name="lookup",
        description="Look something up",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )
]

ARGS = {"q": "weather in Pune"}

# ---- what each provider answers: some text and one tool call ----------------

_COMPLETION = {
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

_RESPONSE = {
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


def _anthropic_stream() -> httpx.Response:
    events = [
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
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
         "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]
    data = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)


def _gemini_answer() -> SimpleNamespace:
    parts = [
        SimpleNamespace(thought=False, text="Let me check.", function_call=None, thought_signature=None),
        SimpleNamespace(
            thought=False,
            text=None,
            function_call=SimpleNamespace(id="call-9", name="lookup", args=dict(ARGS)),
            thought_signature=None,
        ),
    ]
    return SimpleNamespace(
        response_id="response-9",
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts), finish_reason="STOP")],
        usage_metadata=None,
    )


@pytest.fixture
async def ask(monkeypatch):
    """``await ask(provider, messages)`` sends one request with tools and
    returns ``(request, answer)``: the body's bytes (for Gemini, the
    arguments handed to the SDK) and the provider's normalized answer."""
    bodies: list[bytes] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if request.url.path.endswith("/responses"):
            return httpx.Response(200, json=_RESPONSE)
        if request.url.path.endswith("/messages"):
            return _anthropic_stream()
        return httpx.Response(200, json=_COMPLETION)

    def over_mock_transport(sdk_client):
        return lambda **kwargs: sdk_client(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )

    monkeypatch.setattr(openai, "AsyncOpenAI", over_mock_transport(openai.AsyncOpenAI))
    monkeypatch.setattr(anthropic, "AsyncAnthropic", over_mock_transport(anthropic.AsyncAnthropic))
    over_http = {
        # gpt-4o takes Chat Completions; gpt-5 with tools takes the Responses API.
        "openai chat": (OpenAIProvider("sk-test", max_retries=0), "gpt-4o"),
        "openai responses": (OpenAIProvider("sk-test", max_retries=0), "gpt-5"),
        "openrouter": (OpenRouterProvider("sk-or-test", max_retries=0), "anthropic/claude-sonnet-5.5"),
        "anthropic": (AnthropicProvider("sk-test", max_retries=0), "claude-sonnet-5-5"),
    }
    with patch("google.genai.Client"):
        gemini = GeminiProvider("key")
    generate = gemini._client.aio.models.generate_content = AsyncMock(return_value=_gemini_answer())

    async def ask(provider: str, messages):
        if provider == "gemini":
            generate.reset_mock()
            answer = await gemini.chat(messages, model="gemini-3-flash", tools=TOOLS)
            sent = generate.call_args.kwargs
            return {**sent, "config": sent["config"].model_dump()}, answer
        client, model = over_http[provider]
        bodies.clear()
        answer = await client.chat(messages, model=model, tools=TOOLS)
        (body,) = bodies
        return body, answer

    try:
        yield ask
    finally:
        for client, _ in over_http.values():
            await client._client.close()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_an_answer_with_tool_calls_is_stored_with_each_call_once(ask, provider):
    request, answer = await ask(provider, [Message(role="user", content="What is the weather in Pune?")])

    assert request
    wire = message_to_wire(answer.assistant_message)
    assert [(call["id"], call["name"], call["args"]) for call in wire["tool_calls"]] == [("call-9", "lookup", ARGS)]
    assert [block["type"] for block in wire["blocks"]] == ["text"]
    assert all(block["tool_call"] is None for block in wire["blocks"])


# ---- a stored conversation, in both shapes ----------------------------------


def _assistant_turns() -> dict:
    """Assistant turns that call tools, by what else they hold."""
    lookup = ToolCall(id="call-1", name="lookup", args=dict(ARGS))
    # A call the provider sent as a string keeps that string (raw_arguments).
    second = ToolCall.from_raw(id="call-2", name="lookup", arguments='{"q": "weather in Mumbai"}')
    malformed = ToolCall.from_raw(id="call-3", name="lookup", arguments='{"q":')
    return {
        "text beside its calls": Message(role="assistant", content="Let me check.", tool_calls=[lookup, second]),
        "calls alone": Message(role="assistant", tool_calls=[lookup, second]),
        "a malformed call": Message(role="assistant", tool_calls=[malformed]),
        "anthropic's signed thinking": Message(
            role="assistant",
            content="Let me check.",
            tool_calls=[lookup],
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
            tool_calls=[lookup],
            blocks=[ContentBlock(type="reasoning", text="Need fresh data")],
            provider_state={"provider": "openrouter", "payload": {
                "reasoning": "Need fresh data",
                "reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque"}],
            }},
        ),
        "gemini's thought signature": Message(
            role="assistant",
            tool_calls=[lookup],
            blocks=[ContentBlock(type="reasoning", text="Need fresh data", metadata={"thought_signature_b64": "c2lnbmVk"})],
            provider_state={"provider": "gemini", "payload": {"parts": [
                {"text": "Need fresh data", "thought": True, "thought_signature_b64": "c2lnbmVk"},
                {"function_call": {"name": "lookup", "args": dict(ARGS), "id": "call-1"}},
            ]}},
        ),
        "openai's encrypted reasoning": Message(
            role="assistant",
            tool_calls=[lookup],
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


def _conversation(assistant: Message) -> list[dict]:
    """A stored conversation around one assistant turn, as it is written now."""
    return [dict(wire) for wire in messages_to_wire([
        Message(role="system", content="You look things up for the owner."),
        Message(role="user", content="What is the weather in Pune?"),
        assistant,
        *(
            Message(role="tool", content=json.dumps({"answer": f"for {call.id}"}), tool_call_id=call.id, name=call.name)
            for call in assistant.tool_calls
        ),
        Message(role="user", content="And tomorrow?"),
    ])]


def _as_stored_before(wire: dict, *, calls_first: bool) -> dict:
    """``wire`` as it was stored before: its calls repeated in ``blocks``.
    A provider's answer kept them in the order they came in, so a copy
    could sit before the text as well as after it."""
    message = message_from_wire(wire)
    copies = [ContentBlock(type="tool_call", tool_call=call) for call in message.tool_calls]
    message.blocks = [*copies, *message.blocks] if calls_first else [*message.blocks, *copies]
    return dict(message_to_wire(message))


@pytest.mark.parametrize("turn", list(_assistant_turns()))
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_the_request_is_the_same_whether_or_not_blocks_repeat_the_calls(ask, provider, turn):
    stored = _conversation(_assistant_turns()[turn])
    request, _ = await ask(provider, filter_empty_messages(messages_from_wire(stored)))

    for calls_first in (False, True):
        stored_before = [_as_stored_before(wire, calls_first=calls_first) for wire in stored]
        # The two shapes differ in what is stored, and only in the blocks.
        assert stored_before != stored
        assert [{**wire, "blocks": []} for wire in stored_before] == [{**wire, "blocks": []} for wire in stored]

        request_before, _ = await ask(provider, filter_empty_messages(messages_from_wire(stored_before)))
        assert request_before == request
