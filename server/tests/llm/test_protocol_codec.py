"""Golden tests for the durable native LLM message contract."""

import json

import pytest

from services.llm.protocol import (
    MESSAGE_WIRE_VERSION,
    ContentBlock,
    LLMError,
    LLMErrorCategory,
    LLMResponse,
    Message,
    ToolCall,
    Usage,
    message_from_wire,
    message_to_wire,
    messages_from_wire,
    messages_to_wire,
)


def test_message_wire_v2_round_trips_ordered_blocks_and_provider_state():
    good_call = ToolCall(id="call-1", name="lookup", args={"q": "weather"})
    malformed_call = ToolCall.from_raw(
        id="call-2",
        name="calculate",
        arguments='{"expression":',
    )
    original = Message(
        role="assistant",
        content="I will check.",
        tool_calls=[good_call, malformed_call],
        blocks=[
            ContentBlock(
                type="reasoning",
                text="Need fresh data",
                metadata={"signature": "signed-value"},
            ),
            ContentBlock(type="text", text="I will check."),
            ContentBlock(type="tool_call", tool_call=good_call),
            ContentBlock(type="tool_call", tool_call=malformed_call),
        ],
        provider_state={
            "provider": "anthropic",
            "payload": {
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Need fresh data",
                        "signature": "signed-value",
                    }
                ]
            },
        },
    )

    wire = message_to_wire(original)
    assert wire["version"] == MESSAGE_WIRE_VERSION
    # The payload must be safe for Temporal's ordinary JSON payload codec.
    json.dumps(wire)

    decoded = message_from_wire(json.loads(json.dumps(wire)))
    assert [block.type for block in decoded.blocks] == [
        "reasoning",
        "text",
        "tool_call",
        "tool_call",
    ]
    assert decoded.provider_state == original.provider_state
    assert decoded.tool_calls[1].args == {}
    assert decoded.tool_calls[1].raw_arguments == '{"expression":'
    assert decoded.tool_calls[1].parse_error.startswith(
        "Invalid JSON tool arguments"
    )


def test_messages_helpers_accept_generators_and_pre_version_flat_shape():
    encoded = messages_to_wire(
        Message(role="user", content=str(index)) for index in range(2)
    )
    assert [message.content for message in messages_from_wire(encoded)] == [
        "0",
        "1",
    ]
    legacy = message_from_wire(
        {
            "role": "tool",
            "content": "42",
            "tool_call_id": "call-1",
            "name": "calculate",
        }
    )
    assert legacy.role == "tool"
    assert legacy.blocks[0].type == "tool_result"


def test_provider_state_rejects_sdk_objects_and_round_trips_large_payloads():
    with pytest.raises(TypeError, match="JSON values"):
        Message(
            role="assistant",
            provider_state={"provider": "test", "payload": {"raw": object()}},
        )

    blob = "x" * (300 * 1024)
    original = Message(
        role="assistant",
        provider_state={
            "provider": "test",
            "payload": {"blob": blob},
        },
    )
    wire = message_to_wire(original)
    decoded = message_from_wire(json.loads(json.dumps(wire)))
    assert decoded.provider_state["payload"]["blob"] == blob


def test_response_always_has_replayable_assistant_message():
    call = ToolCall(id="call-1", name="lookup", args={"q": "x"})
    response = LLMResponse(
        content="Checking",
        thinking="Use the lookup",
        tool_calls=[call],
    )
    assert response.assistant_message is not None
    assert response.assistant_message.content == "Checking"
    assert response.assistant_message.tool_calls == [call]
    assert [block.type for block in response.assistant_message.blocks] == [
        "reasoning",
        "text",
        "tool_call",
    ]


def test_usage_is_safe_and_additive():
    first = Usage(input_tokens="10", output_tokens=5, total_tokens=None)
    second = Usage(
        input_tokens=-1,
        output_tokens=2,
        cache_read_tokens=3,
        reasoning_tokens=4,
    )
    total = first + second
    assert first.total_tokens == 15
    assert second.input_tokens == 0
    assert total.total_tokens == 17
    assert total.cache_read_tokens == 3
    assert total.reasoning_tokens == 4


def test_structured_error_classifies_retryable_sdk_failures():
    class RateLimitError(Exception):
        status_code = 429
        request_id = "request-123"

    error = LLMError.from_exception("openai", RateLimitError("slow down"))
    assert error.category == LLMErrorCategory.RATE_LIMIT
    assert error.retryable is True
    assert error.status_code == 429
    assert error.request_id == "request-123"


def test_structured_error_exposes_only_category_based_user_message():
    raw_message = (
        "POST https://private-gateway.internal/v1 "
        'payload={"authorization":"Bearer secret"}'
    )
    error = LLMError(
        message=raw_message,
        provider="openai",
        category=LLMErrorCategory.AUTHENTICATION,
    )

    assert error.message == raw_message
    assert error.user_message == (
        "OpenAI authentication failed. Check the configured API key."
    )
    assert "private-gateway" not in error.user_message
    assert "Bearer secret" not in error.user_message


def test_structured_error_preserves_http_date_retry_after():
    class Response:
        status_code = 429
        headers = {
            "retry-after": "Wed, 21 Oct 2037 07:28:00 GMT",
            "x-request-id": "request-http-date",
        }

    class RateLimitError(Exception):
        status_code = 429
        response = Response()

    error = LLMError.from_exception(
        "openai", RateLimitError("rate limited")
    )

    assert error.retry_after is None
    assert error.retry_after_raw == "Wed, 21 Oct 2037 07:28:00 GMT"
    assert error.request_id == "request-http-date"
