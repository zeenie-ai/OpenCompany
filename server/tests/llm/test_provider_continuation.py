import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.llm.protocol import (
    BINARY_STATE_MARKER,
    Message,
    ThinkingConfig,
    ToolDef,
    message_from_wire,
    message_to_wire,
)


def _usage(**overrides):
    values = {
        "prompt_tokens": 4,
        "completion_tokens": 2,
        "total_tokens": 6,
        "completion_tokens_details": None,
        "prompt_tokens_details": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_openai_preserves_malformed_arguments_and_reasoning_continuation():
    with patch("openai.AsyncOpenAI"):
        from services.llm.providers.openai import OpenAIProvider

        provider = OpenAIProvider("key")

    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="calculate",
            arguments='{"expression":',
        ),
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="",
                    reasoning_content="Need a calculation",
                    refusal=None,
                    tool_calls=[tool_call],
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=_usage(),
    )

    normalized = provider._normalize(response, "gpt-5")
    call = normalized.tool_calls[0]
    assert call.args == {}
    assert call.raw_arguments == '{"expression":'
    assert call.parse_error
    assert normalized.assistant_message.provider_state == {
        "provider": "openai",
        "payload": {"reasoning_content": "Need a calculation"},
    }
    api_message = provider._to_api_message(normalized.assistant_message)
    assert api_message["reasoning_content"] == "Need a calculation"
    assert (
        api_message["tool_calls"][0]["function"]["arguments"]
        == '{"expression":'
    )


def test_openai_compatible_reasoning_variants_round_trip():
    with patch("openai.AsyncOpenAI"):
        from services.llm.providers.openai import OpenAIProvider

        provider = OpenAIProvider("key", provider_name="openrouter")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="",
                    reasoning="Visible reasoning",
                    reasoning_content=None,
                    reasoning_details=[
                        {
                            "type": "reasoning.encrypted",
                            "data": "opaque-signed-state",
                        }
                    ],
                    refusal=None,
                    tool_calls=None,
                ),
                finish_reason="stop",
            )
        ],
        usage=_usage(),
    )

    normalized = provider._normalize(response, "anthropic/claude-sonnet-5")
    assert normalized.thinking == "Visible reasoning"
    assert normalized.assistant_message.provider_state["payload"] == {
        "reasoning": "Visible reasoning",
        "reasoning_details": [
            {
                "type": "reasoning.encrypted",
                "data": "opaque-signed-state",
            }
        ],
    }
    replayed = provider._to_api_message(normalized.assistant_message)
    assert replayed["reasoning"] == "Visible reasoning"
    assert replayed["reasoning_details"][0]["data"] == "opaque-signed-state"


def test_openai_chat_and_responses_refusals_are_visible_text():
    with patch("openai.AsyncOpenAI"):
        from services.llm.providers.openai import OpenAIProvider

        provider = OpenAIProvider("key")

    chat_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    reasoning=None,
                    reasoning_content=None,
                    reasoning_details=None,
                    refusal="I cannot help with that.",
                    tool_calls=None,
                ),
                finish_reason="stop",
            )
        ],
        usage=_usage(),
    )
    chat_normalized = provider._normalize(chat_response, "gpt-5")
    assert chat_normalized.content == "I cannot help with that."
    assert chat_normalized.assistant_message.blocks[0].metadata == {
        "refusal": True
    }

    responses_response = SimpleNamespace(
        id="response-refusal",
        model="gpt-5",
        status="completed",
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        type="refusal",
                        refusal="This request is not allowed.",
                    )
                ],
            )
        ],
        usage=None,
    )
    responses_normalized = provider._normalize_responses(
        responses_response, "gpt-5"
    )
    assert responses_normalized.content == "This request is not allowed."
    assert responses_normalized.assistant_message.blocks[0].metadata == {
        "refusal": True
    }


@pytest.mark.asyncio
async def test_openai_reasoning_tool_turn_uses_stateless_responses_api():
    with patch("openai.AsyncOpenAI"):
        from services.llm.providers.openai import OpenAIProvider

        provider = OpenAIProvider("key")

    reasoning = SimpleNamespace(
        type="reasoning",
        id="reasoning-1",
        encrypted_content="encrypted-state",
        summary=[
            SimpleNamespace(type="summary_text", text="Need the tool")
        ],
    )
    function_call = SimpleNamespace(
        type="function_call",
        id="function-1",
        call_id="call-1",
        name="lookup",
        arguments='{"q":"weather"}',
        status="completed",
    )
    response = SimpleNamespace(
        id="response-1",
        model="gpt-5",
        status="completed",
        output=[reasoning, function_call],
        usage=SimpleNamespace(
            input_tokens=8,
            output_tokens=5,
            total_tokens=13,
            input_tokens_details=SimpleNamespace(cached_tokens=2),
            output_tokens_details=SimpleNamespace(reasoning_tokens=3),
        ),
    )
    provider._client.responses.create = AsyncMock(return_value=response)

    normalized = await provider.chat(
        [Message(role="user", content="What is the weather?")],
        model="gpt-5",
        thinking=ThinkingConfig(enabled=True, effort="high"),
        tools=[
            ToolDef(
                name="lookup",
                description="Look up weather",
                parameters={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
            )
        ],
    )

    params = provider._client.responses.create.call_args.kwargs
    assert params["store"] is False
    assert params["include"] == ["reasoning.encrypted_content"]
    assert params["reasoning"] == {"effort": "high"}
    assert params["tools"][0]["name"] == "lookup"
    state = normalized.assistant_message.provider_state
    assert state["payload"]["api"] == "responses"
    assert state["payload"]["output"][0]["encrypted_content"] == (
        "encrypted-state"
    )

    second_input = provider._to_responses_input(
        [
            Message(role="user", content="What is the weather?"),
            normalized.assistant_message,
            Message(
                role="tool",
                content='{"temperature": 20}',
                tool_call_id="call-1",
                name="lookup",
            ),
        ]
    )
    assert second_input[-1] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": '{"temperature": 20}',
    }
    assert second_input[1]["type"] == "reasoning"
    assert second_input[1]["encrypted_content"] == "encrypted-state"


def test_anthropic_signed_thinking_round_trips_in_original_block_order():
    with patch("anthropic.AsyncAnthropic"):
        from services.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider("key")

    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="thinking",
                thinking="Check the source",
                signature="signed-thinking",
            ),
            SimpleNamespace(
                type="tool_use",
                id="tool-1",
                name="lookup",
                input={"q": "source"},
            ),
            SimpleNamespace(type="text", text="Looking it up"),
        ],
        usage=SimpleNamespace(
            input_tokens=12,
            output_tokens=7,
            cache_creation_input_tokens=2,
            cache_read_input_tokens=3,
        ),
        stop_reason="tool_use",
    )

    normalized = provider._normalize(response, "claude-sonnet-4-6")
    assert [block.type for block in normalized.assistant_message.blocks] == [
        "reasoning",
        "tool_call",
        "text",
    ]
    json.dumps(normalized.assistant_message.provider_state)
    api_message = provider._to_api_message(normalized.assistant_message)
    assert api_message["content"] == [
        {
            "type": "thinking",
            "thinking": "Check the source",
            "signature": "signed-thinking",
        },
        {
            "type": "tool_use",
            "id": "tool-1",
            "name": "lookup",
            "input": {"q": "source"},
        },
        {"type": "text", "text": "Looking it up"},
    ]
    assert normalized.usage.total_tokens == 24


def test_anthropic_parallel_tool_results_are_one_user_turn():
    with patch("anthropic.AsyncAnthropic"):
        from services.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider("key")

    _, api_messages = provider._split_system(
        [
            Message(
                role="tool",
                content='{"result": 1}',
                tool_call_id="tool-1",
                name="lookup",
            ),
            Message(
                role="tool",
                content='{"result": 2}',
                tool_call_id="tool-2",
                name="lookup",
            ),
        ]
    )
    assert api_messages == [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": '{"result": 1}',
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-2",
                    "content": '{"result": 2}',
                },
            ],
        }
    ]


def test_anthropic_redacted_thinking_round_trips():
    with patch("anthropic.AsyncAnthropic"):
        from services.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider("key")

    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="redacted_thinking",
                data="base64-redacted-state",
            ),
            SimpleNamespace(type="text", text="Safe answer"),
        ],
        usage=SimpleNamespace(
            input_tokens=4,
            output_tokens=3,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
        stop_reason="end_turn",
    )

    normalized = provider._normalize(response, "claude-sonnet-4-6")
    json.dumps(normalized.assistant_message.provider_state)
    assert normalized.assistant_message.blocks[0].metadata == {
        "redacted": True
    }
    assert provider._to_api_message(normalized.assistant_message)["content"] == [
        {
            "type": "redacted_thinking",
            "data": "base64-redacted-state",
        },
        {"type": "text", "text": "Safe answer"},
    ]


def test_anthropic_binary_thinking_signature_is_base64_durable():
    with patch("anthropic.AsyncAnthropic"):
        from services.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider("key")

    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="thinking",
                thinking="Binary signature",
                signature=b"\x00signed\xff",
            )
        ],
        usage=SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
        stop_reason="end_turn",
    )

    normalized = provider._normalize(response, "claude-sonnet-4-6")
    wire = json.loads(
        json.dumps(message_to_wire(normalized.assistant_message))
    )
    signature = wire["provider_state"]["payload"]["content"][0]["signature"]
    assert signature == {BINARY_STATE_MARKER: "AHNpZ25lZP8="}

    replayed = message_from_wire(wire)
    api_message = provider._to_api_message(replayed)
    assert api_message["content"][0]["signature"] == b"\x00signed\xff"


def test_anthropic_binary_redacted_thinking_is_base64_durable():
    with patch("anthropic.AsyncAnthropic"):
        from services.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider("key")

    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="redacted_thinking",
                data=b"\x00redacted\xff",
            )
        ],
        usage=SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
        stop_reason="end_turn",
    )

    normalized = provider._normalize(response, "claude-sonnet-4-6")
    wire = json.loads(
        json.dumps(message_to_wire(normalized.assistant_message))
    )
    data = wire["provider_state"]["payload"]["content"][0]["data"]
    assert data == {BINARY_STATE_MARKER: "AHJlZGFjdGVk/w=="}

    replayed = message_from_wire(wire)
    api_message = provider._to_api_message(replayed)
    assert api_message["content"][0]["data"] == b"\x00redacted\xff"


def test_gemini_signature_is_base64_durable_and_duplicate_names_get_unique_ids():
    with patch("google.genai.Client"):
        from services.llm.providers.gemini import GeminiProvider

        provider = GeminiProvider("key")

    parts = [
        SimpleNamespace(
            thought=True,
            text="Need weather",
            function_call=None,
            thought_signature=b"\x00signed\xff",
        ),
        SimpleNamespace(
            thought=False,
            text=None,
            function_call=SimpleNamespace(name="lookup", args={"city": "A"}),
            thought_signature=b"first-call-signature",
        ),
        SimpleNamespace(
            thought=False,
            text=None,
            function_call=SimpleNamespace(name="lookup", args={"city": "B"}),
            thought_signature=b"second-call-signature",
        ),
    ]
    response = SimpleNamespace(
        response_id="response-1",
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=parts),
                finish_reason="STOP",
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
            total_token_count=17,
            cached_content_token_count=4,
            thoughts_token_count=2,
        ),
    )

    normalized = provider._normalize(response, "gemini-2.5-flash")
    assert normalized.tool_calls[0].id != normalized.tool_calls[1].id
    assert normalized.usage.cache_read_tokens == 4
    assert normalized.usage.reasoning_tokens == 2
    state = normalized.assistant_message.provider_state
    json.dumps(state)
    assert state["payload"]["parts"][0]["thought_signature_b64"] == (
        "AHNpZ25lZP8="
    )

    _, contents = provider._split_system_and_contents(
        [normalized.assistant_message]
    )
    assert contents[0]["parts"][0]["thought_signature"] == b"\x00signed\xff"
    assert contents[0]["parts"][1]["function_call"]["name"] == "lookup"
    assert contents[0]["parts"][1]["function_call"]["id"] == (
        normalized.tool_calls[0].id
    )

    _, tool_contents = provider._split_system_and_contents(
        [
            normalized.assistant_message,
            Message(
                role="tool",
                content='{"weather":"sunny"}',
                tool_call_id=normalized.tool_calls[0].id,
                name="lookup",
            ),
            Message(
                role="tool",
                content='{"weather":"rain"}',
                tool_call_id=normalized.tool_calls[1].id,
                name="lookup",
            ),
        ]
    )
    assert tool_contents[1]["role"] == "user"
    assert [
        part["function_response"]["id"]
        for part in tool_contents[1]["parts"]
    ] == [call.id for call in normalized.tool_calls]


def test_gemini_finish_reason_enum_normalizes_to_value():
    from google.genai import types

    with patch("google.genai.Client"):
        from services.llm.providers.gemini import GeminiProvider

        provider = GeminiProvider("key")

    response = SimpleNamespace(
        response_id="response-safety",
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[]),
                finish_reason=types.FinishReason.SAFETY,
            )
        ],
        usage_metadata=None,
    )
    normalized = provider._normalize(response, "gemini-2.5-flash")
    assert normalized.finish_reason == "safety"


def test_gemini_malformed_tool_arguments_are_preserved_without_crash():
    with patch("google.genai.Client"):
        from services.llm.providers.gemini import GeminiProvider

        provider = GeminiProvider("key")

    response = SimpleNamespace(
        response_id="response-malformed",
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            thought=False,
                            text=None,
                            thought_signature=None,
                            function_call=SimpleNamespace(
                                id="call-malformed",
                                name="lookup",
                                args="{not-json",
                            ),
                        )
                    ]
                ),
                finish_reason="STOP",
            )
        ],
        usage_metadata=None,
    )

    normalized = provider._normalize(response, "gemini-2.5-flash")
    call = normalized.tool_calls[0]
    assert call.id == "call-malformed"
    assert call.args == {}
    assert call.raw_arguments == "{not-json"
    assert call.parse_error
    function_state = normalized.assistant_message.provider_state[
        "payload"
    ]["parts"][0]["function_call"]
    assert function_state["raw_arguments"] == "{not-json"
    assert function_state["parse_error"] == call.parse_error
    json.dumps(normalized.assistant_message.provider_state)


@pytest.mark.asyncio
async def test_compat_provider_policy_applies_kimi_and_groq_quirks():
    with patch("openai.AsyncOpenAI"):
        from services.llm.providers.openai import OpenAIProvider

        kimi = OpenAIProvider("key", provider_name="kimi")
        kimi._client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="ok",
                            reasoning_content=None,
                            refusal=None,
                            tool_calls=None,
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=_usage(),
            )
        )
        await kimi.chat(
            [Message(role="user", content="hi")],
            model="kimi-k2.5",
            temperature=0.1,
        )
        kimi_params = kimi._client.chat.completions.create.call_args.kwargs
        assert kimi_params["temperature"] == 0.6
        assert kimi_params["extra_body"]["thinking"] == {"type": "disabled"}
        assert "max_tokens" in kimi_params

        groq = OpenAIProvider("key", provider_name="groq")
        groq._client.chat.completions.create = kimi._client.chat.completions.create
        await groq.chat(
            [Message(role="user", content="hi")],
            model="qwen/qwen3-32b",
            thinking=ThinkingConfig(enabled=True, format="hidden"),
        )
        groq_params = groq._client.chat.completions.create.call_args.kwargs
        assert groq_params["model"] == "qwen/qwen3-32b"
        assert groq_params["extra_body"]["reasoning_format"] == "hidden"

        await groq.chat(
            [Message(role="user", content="hi")],
            model="openai/gpt-oss-120b",
            thinking=ThinkingConfig(enabled=True, effort="high"),
        )
        gpt_oss_params = (
            groq._client.chat.completions.create.call_args.kwargs
        )
        assert gpt_oss_params["model"] == "openai/gpt-oss-120b"
        assert gpt_oss_params["reasoning_effort"] == "high"
        assert "extra_body" not in gpt_oss_params


@pytest.mark.asyncio
async def test_openai_reasoning_capable_model_omits_temperature_without_toggle():
    with patch("openai.AsyncOpenAI"):
        from services.llm.providers.openai import OpenAIProvider

        provider = OpenAIProvider("key")

    provider._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="ok",
                        reasoning=None,
                        reasoning_content=None,
                        reasoning_details=None,
                        refusal=None,
                        tool_calls=None,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=_usage(),
        )
    )

    await provider.chat(
        [Message(role="user", content="hi")],
        model="gpt-5.6-sol",
        temperature=0.2,
    )
    params = provider._client.chat.completions.create.call_args.kwargs
    assert "temperature" not in params
    assert params["max_completion_tokens"] == 4096
    assert "max_tokens" not in params
