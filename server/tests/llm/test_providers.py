"""Test provider message formatting and response normalization.

These tests verify the internal conversion logic without hitting real APIs.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm.protocol import Message, ThinkingConfig, ToolCall, ToolDef


# ---------------------------------------------------------------------------
# Anthropic: message formatting + response normalization
# ---------------------------------------------------------------------------


class TestAnthropicProvider:
    @pytest.fixture
    def provider(self):
        with patch("anthropic.AsyncAnthropic"):
            from services.llm.providers.anthropic import AnthropicProvider

            return AnthropicProvider("sk-test")

    def test_system_extracted_from_messages(self, provider):
        msgs = [
            Message(role="system", content="Be helpful"),
            Message(role="user", content="Hi"),
        ]
        system, api_msgs = provider._split_system(msgs)
        assert system == "Be helpful"
        assert len(api_msgs) == 1
        assert api_msgs[0]["role"] == "user"

    def test_tool_result_sent_as_user_role(self, provider):
        msg = Message(role="tool", content="42", tool_call_id="tc_1")
        result = provider._to_api_message(msg)
        assert result["role"] == "user"
        assert result["content"][0]["type"] == "tool_result"
        assert result["content"][0]["tool_use_id"] == "tc_1"

    def test_tool_def_uses_input_schema(self, provider):
        td = ToolDef(name="calc", description="Math", parameters={"type": "object"})
        result = provider._to_api_tool(td)
        assert result["name"] == "calc"
        assert result["input_schema"] == {"type": "object"}

    def test_normalize_text_response(self, provider):
        resp = MagicMock()
        resp.content = [MagicMock(type="text", text="Hello")]
        resp.usage = MagicMock(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        resp.stop_reason = "end_turn"

        result = provider._normalize(resp, "claude-sonnet-4-6")
        assert result.content == "Hello"
        assert result.model == "claude-sonnet-4-6"
        assert result.usage.input_tokens == 10

    def test_normalize_thinking_response(self, provider):
        resp = MagicMock()
        thinking_block = MagicMock(type="thinking", thinking="Let me think...")
        text_block = MagicMock(type="text", text="Answer")
        resp.content = [thinking_block, text_block]
        resp.usage = MagicMock(input_tokens=50, output_tokens=30, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        resp.stop_reason = "end_turn"

        result = provider._normalize(resp, "claude-sonnet-4-6")
        assert result.thinking == "Let me think..."
        assert result.content == "Answer"

    @pytest.mark.asyncio
    async def test_chat_sets_thinking_params(self, provider):
        """When thinking enabled, budget_tokens and temperature=1 are set."""
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="ok")]
        mock_resp.usage = MagicMock(input_tokens=5, output_tokens=2, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        mock_resp.stop_reason = "end_turn"
        provider._client.messages.create = AsyncMock(return_value=mock_resp)

        thinking = ThinkingConfig(enabled=True, budget=4096)
        await provider.chat(
            [Message(role="user", content="test")],
            model="claude-sonnet-4-6",
            thinking=thinking,
        )

        call_kwargs = provider._client.messages.create.call_args[1]
        assert call_kwargs["thinking"]["budget_tokens"] == 4096
        assert call_kwargs["temperature"] == 1

    @pytest.mark.asyncio
    async def test_chat_clamps_thinking_budget_and_expands_max_tokens(
        self, provider
    ):
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="ok")]
        mock_resp.usage = MagicMock(
            input_tokens=5,
            output_tokens=2,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        mock_resp.stop_reason = "end_turn"
        provider._client.messages.create = AsyncMock(return_value=mock_resp)

        await provider.chat(
            [Message(role="user", content="test")],
            model="claude-sonnet-4-6",
            max_tokens=500,
            thinking=ThinkingConfig(enabled=True, budget=100),
        )

        call_kwargs = provider._client.messages.create.call_args.kwargs
        assert call_kwargs["thinking"]["budget_tokens"] == 1024
        assert call_kwargs["max_tokens"] == 2048


# ---------------------------------------------------------------------------
# OpenAI: message formatting + response normalization
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    @pytest.fixture
    def provider(self):
        with patch("openai.AsyncOpenAI"):
            from services.llm.providers.openai import OpenAIProvider

            return OpenAIProvider("sk-test")

    def test_user_message_format(self, provider):
        msg = Message(role="user", content="Hi")
        result = provider._to_api_message(msg)
        assert result == {"role": "user", "content": "Hi"}

    def test_tool_message_format(self, provider):
        msg = Message(role="tool", content="42", tool_call_id="tc_1")
        result = provider._to_api_message(msg)
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "tc_1"

    def test_assistant_tool_calls_format(self, provider):
        tc = ToolCall(id="tc_1", name="calc", args={"x": 1})
        msg = Message(role="assistant", content="", tool_calls=[tc])
        result = provider._to_api_message(msg)
        assert result["role"] == "assistant"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["function"]["name"] == "calc"
        assert json.loads(result["tool_calls"][0]["function"]["arguments"]) == {"x": 1}

    def test_normalize_basic_response(self, provider):
        choice = MagicMock()
        choice.message.content = "Hello"
        choice.message.reasoning_content = None
        choice.message.tool_calls = None
        choice.finish_reason = "stop"

        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15, completion_tokens_details=None)

        result = provider._normalize(resp, "gpt-5.2")
        assert result.content == "Hello"
        assert result.thinking is None
        assert result.usage.input_tokens == 10

    def test_normalize_reasoning_response(self, provider):
        choice = MagicMock()
        choice.message.content = "Answer"
        choice.message.reasoning_content = "Thinking..."
        choice.message.tool_calls = None
        choice.finish_reason = "stop"

        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = MagicMock(
            prompt_tokens=10, completion_tokens=5, total_tokens=15, completion_tokens_details=MagicMock(reasoning_tokens=20)
        )

        result = provider._normalize(resp, "o3-mini")
        assert result.thinking == "Thinking..."
        assert result.usage.reasoning_tokens == 20

    @pytest.mark.asyncio
    async def test_chat_reasoning_model_uses_max_completion_tokens(self, provider):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="ok", reasoning_content=None, tool_calls=None), finish_reason="stop")]
        mock_resp.usage = MagicMock(prompt_tokens=5, completion_tokens=2, total_tokens=7, completion_tokens_details=None)
        provider._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        await provider.chat(
            [Message(role="user", content="test")],
            model="o3-mini",
        )

        call_kwargs = provider._client.chat.completions.create.call_args[1]
        assert "max_completion_tokens" in call_kwargs
        assert "max_tokens" not in call_kwargs


# ---------------------------------------------------------------------------
# Gemini: message formatting + thinking config
# ---------------------------------------------------------------------------


class TestGeminiProvider:
    @pytest.fixture
    def provider(self):
        with patch("google.genai.Client"):
            from services.llm.providers.gemini import GeminiProvider

            return GeminiProvider("key")

    def test_split_system_and_contents(self, provider):
        msgs = [
            Message(role="system", content="Be helpful"),
            Message(role="user", content="Hi"),
            Message(role="assistant", content="Hello"),
        ]
        system, contents = provider._split_system_and_contents(msgs)
        assert system == "Be helpful"
        assert len(contents) == 2
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "model"

    def test_tool_response_format(self, provider):
        msgs = [
            Message(
                role="tool",
                content="42",
                name="calc",
                tool_call_id="call-1",
            ),
            Message(
                role="tool",
                content="7",
                name="calc",
                tool_call_id="call-2",
            ),
        ]
        _, contents = provider._split_system_and_contents(msgs)
        assert len(contents) == 1
        assert contents[0]["role"] == "user"
        assert len(contents[0]["parts"]) == 2
        assert "function_response" in contents[0]["parts"][0]
        assert contents[0]["parts"][0]["function_response"]["id"] == "call-1"
        assert contents[0]["parts"][1]["function_response"]["id"] == "call-2"

    def test_normalize_text(self, provider):
        part = MagicMock(thought=False, function_call=None, text="Hello")
        candidate = MagicMock()
        candidate.content.parts = [part]
        candidate.finish_reason = "STOP"

        resp = MagicMock()
        resp.candidates = [candidate]
        resp.usage_metadata = MagicMock(prompt_token_count=10, candidates_token_count=5, total_token_count=15)

        result = provider._normalize(resp, "gemini-2.5-flash")
        assert result.content == "Hello"
        assert result.usage.total_tokens == 15

    def test_normalize_thinking_part(self, provider):
        thinking_part = MagicMock(thought=True, function_call=None, text="Thinking...")
        text_part = MagicMock(thought=False, function_call=None, text="Answer")

        candidate = MagicMock()
        candidate.content.parts = [thinking_part, text_part]
        candidate.finish_reason = "STOP"

        resp = MagicMock()
        resp.candidates = [candidate]
        resp.usage_metadata = None

        result = provider._normalize(resp, "gemini-2.5-pro")
        assert result.thinking == "Thinking..."
        assert result.content == "Answer"

    @pytest.mark.asyncio
    async def test_chat_thinking_level_takes_precedence_over_budget(self, provider):
        """Gemini 3 level and Gemini 2.5 budget are mutually exclusive."""
        mock_types = MagicMock()
        mock_resp = MagicMock()
        mock_resp.candidates = []
        mock_resp.usage_metadata = None
        provider._client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        with patch("google.genai.types", mock_types):
            thinking = ThinkingConfig(enabled=True, budget=4096, level="high")
            await provider.chat(
                [Message(role="user", content="test")],
                model="gemini-2.5-flash",
                thinking=thinking,
            )

        call_kwargs = mock_types.ThinkingConfig.call_args[1]
        assert call_kwargs["include_thoughts"] is True
        assert call_kwargs["thinking_level"] == "high"
        assert "thinking_budget" not in call_kwargs

    @pytest.mark.asyncio
    async def test_chat_no_thinking_when_disabled(self, provider):
        """No thinking_config when thinking is disabled."""
        mock_types = MagicMock()
        mock_resp = MagicMock()
        mock_resp.candidates = []
        mock_resp.usage_metadata = None
        provider._client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        with patch("google.genai.types", mock_types):
            await provider.chat(
                [Message(role="user", content="test")],
                model="gemini-2.5-flash",
            )

        mock_types.ThinkingConfig.assert_not_called()


# ---------------------------------------------------------------------------
# OpenRouter: [FREE] prefix handling
# ---------------------------------------------------------------------------


class TestOpenRouterProvider:
    @pytest.fixture
    def provider(self):
        with patch("openai.AsyncOpenAI"):
            from services.llm.providers.openrouter import OpenRouterProvider

            return OpenRouterProvider("or-key")

    @pytest.mark.asyncio
    async def test_strips_free_prefix(self, provider):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="ok", reasoning_content=None, tool_calls=None), finish_reason="stop")]
        mock_resp.usage = MagicMock(prompt_tokens=5, completion_tokens=2, total_tokens=7, completion_tokens_details=None)
        provider._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        await provider.chat(
            [Message(role="user", content="test")],
            model="[FREE] meta-llama/llama-3-8b",
        )

        call_kwargs = provider._client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "meta-llama/llama-3-8b"
