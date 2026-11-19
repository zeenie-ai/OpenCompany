"""Golden tests for the durable native LLM message contract."""

import json

import pytest

from services.llm.protocol import (
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


def test_message_wire_round_trips_ordered_blocks_and_provider_state():
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
    # One unversioned wire shape — no version discriminator.
    assert "version" not in wire
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


def _block(type: str, **fields) -> dict:
    return {"type": type, "text": "", "tool_call": None, "tool_call_id": None, "name": None, "metadata": {}, "source": None, **fields}


_LOOKUP = {"id": "call-1", "name": "lookup", "args": {"q": "weather"}, "raw_arguments": None, "parse_error": None}

# An assistant turn as it is written now: its call is in ``tool_calls`` only.
_WIRE = {
    "role": "assistant",
    "content": "Checking",
    "blocks": [_block("text", text="Checking")],
    "tool_calls": [_LOOKUP],
    "tool_call_id": None,
    "name": None,
    "provider_state": {},
}

# The same turn as it was written before: the call repeated in ``blocks``.
_WIRE_WITH_CALL_BLOCK = {**_WIRE, "blocks": [*_WIRE["blocks"], _block("tool_call", tool_call=_LOOKUP)]}


def test_a_message_holds_each_tool_call_once():
    call = ToolCall(id="call-1", name="lookup", args={"q": "weather"})

    assert message_to_wire(Message(role="assistant", content="Checking", tool_calls=[call])) == _WIRE
    # A turn that only calls tools has no blocks at all.
    calls_alone = message_to_wire(Message(role="assistant", tool_calls=[call]))
    assert calls_alone["blocks"] == []
    assert calls_alone["tool_calls"] == [_LOOKUP]
    for wire in (_WIRE, calls_alone):
        assert message_to_wire(message_from_wire(json.loads(json.dumps(wire)))) == wire


def test_a_stored_message_with_call_blocks_round_trips_unchanged():
    """Conversations saved before calls were held once repeat each call in
    ``blocks``. Every turn decodes the stored conversation and saves it
    again, so such a message must come back byte for byte: a changed one
    would lose its stamp and break the digest a chat branch rewinds to."""
    decoded = message_from_wire(json.loads(json.dumps(_WIRE_WITH_CALL_BLOCK)))

    assert message_to_wire(decoded) == _WIRE_WITH_CALL_BLOCK
    assert [block.type for block in decoded.blocks] == ["text", "tool_call"]
    # Both wires decode to the same calls and the same text.
    current = message_from_wire(_WIRE)
    assert (decoded.content, decoded.tool_calls) == (current.content, current.tool_calls)


def test_messages_helpers_accept_generators_and_flat_shape():
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
    # The call is held once, in ``tool_calls``; ``blocks`` carries no copy.
    assert [block.type for block in response.assistant_message.blocks] == [
        "reasoning",
        "text",
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


@pytest.mark.parametrize(("status", "message"), [
    (403, "Spend cap breached for project: projects/private-id for service: aiplatform.googleapis.com"),
    (429, "insufficient_quota: private account details"),
    (400, "Your credit balance is too low: secret"),
    (402, "Payment required: secret"),
])
def test_billing_errors_need_owner_action_instead_of_retry(status, message):
    exc = Exception(message)
    exc.status_code = status
    error = LLMError.from_exception("gemini", exc)
    assert error.category == LLMErrorCategory.BILLING
    assert not error.retryable
    node_error = error.as_node_error()
    assert node_error.requires_user_action
    assert "spending" in node_error.hint
    assert "private" not in str(node_error)
    assert "secret" not in str(node_error)
    assert node_error.as_dict()["retryable"] is False


@pytest.mark.parametrize(
    ("provider", "not_found", "context_length"),
    [
        (
            "openai",
            "The configured OpenAI model or endpoint was not found.",
            "The request exceeds the OpenAI model context window.",
        ),
        (
            "openai_compatible:home",
            "The OpenAI-compatible endpoint did not find the configured model.",
            "The request exceeds the context window of the model at the OpenAI-compatible endpoint.",
        ),
        (
            "some-unregistered-provider",
            "The language model provider did not find the configured model.",
            "The request exceeds the context window of the model at the language model provider.",
        ),
    ],
)
def test_model_scoped_messages_read_for_names_and_common_nouns(provider, not_found, context_length):
    def message(category: LLMErrorCategory) -> str:
        return LLMError(message="raw", provider=provider, category=category).user_message

    assert message(LLMErrorCategory.NOT_FOUND) == not_found
    assert message(LLMErrorCategory.CONTEXT_LENGTH) == context_length


@pytest.mark.parametrize("provider", ["openai_compatible:home", "some-unregistered-provider"])
@pytest.mark.parametrize("category", list(LLMErrorCategory))
def test_no_message_doubles_the_article_of_a_common_noun(provider, category):
    text = LLMError(message="raw", provider=provider, category=category).user_message.lower()

    assert "the the " not in text
    assert "configured the " not in text


def test_structured_error_preserves_http_date_retry_after():
    class Response:
        status_code = 429
        headers = {
            "retry-after": "Wed, 21 Oct 2037 07:28:00 GMT",
            "date": "Wed, 21 Oct 2037 07:27:00 GMT",
            "x-request-id": "request-http-date",
        }

    class RateLimitError(Exception):
        status_code = 429
        response = Response()

    error = LLMError.from_exception(
        "openai", RateLimitError("rate limited")
    )

    assert error.retry_after == 60
    assert error.retry_after_raw == "Wed, 21 Oct 2037 07:28:00 GMT"
    assert error.request_id == "request-http-date"
