"""Contract tests for the 9 ai_chat_models nodes.

Covered nodes: openaiChatModel, anthropicChatModel, geminiChatModel,
openrouterChatModel, groqChatModel, cerebrasChatModel, deepseekChatModel,
kimiChatModel, mistralChatModel.

All 9 nodes share the SAME registry binding:
    partial(handle_ai_chat_model, ai_service=self.ai_service)

`handle_ai_chat_model` is a thin pass-through that awaits
`ai_service.execute_chat(node_id, node_type, parameters)`. The harness
fixture pre-wires `ai_service.execute_chat` as an AsyncMock returning a
canned success envelope, so tests focus on:

  1. Parametrized happy path across every provider (envelope + dispatch).
  2. Handler forwards parameters verbatim (prompt, model, thinking flags).
  3. Error-path envelope propagation when execute_chat returns success=False.
  4. Per-provider quirks documented in the *.md files
     (temperature clamp, reasoning params, thinking defaults, etc.).

These tests freeze the behaviour documented in
`docs-internal/node-logic-flows/ai_chat_models/`. A refactor that breaks
any of these indicates the docs (and the user-visible contract) need to
be updated.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.node_contract


ALL_PROVIDERS = [
    ("openaiChatModel", "openai"),
    ("anthropicChatModel", "anthropic"),
    ("geminiChatModel", "gemini"),
    ("openrouterChatModel", "openrouter"),
    ("groqChatModel", "groq"),
    ("cerebrasChatModel", "cerebras"),
    ("deepseekChatModel", "deepseek"),
    ("kimiChatModel", "kimi"),
    ("mistralChatModel", "mistral"),
]


def _canned_success(provider: str, model: str = "test-model", response: str = "hi"):
    """Helper: build the canned LLMResponse envelope execute_chat returns."""
    return {
        "success": True,
        "node_id": "test-node",
        "node_type": f"{provider}ChatModel",
        "result": {
            "response": response,
            "thinking": None,
            "thinking_enabled": False,
            "model": model,
            "provider": provider,
            "finish_reason": "stop",
            "timestamp": "2026-04-15T00:00:00",
            "input": {"prompt": "hello", "system_prompt": ""},
        },
        "execution_time": 0.01,
    }


# ============================================================================
# Shared happy-path sweep - one test, parametrized across all 9 nodes
# ============================================================================


class TestAllChatModelsHappyPath:
    """The happy-path contract is identical for every chat-model node."""

    @pytest.mark.parametrize("node_type,provider", ALL_PROVIDERS)
    async def test_dispatches_to_execute_chat_and_returns_envelope(self, harness, node_type, provider):
        harness.ai_service.execute_chat = AsyncMock(return_value=_canned_success(provider))

        result = await harness.execute(
            node_type,
            {"prompt": "hello", "model": "test-model"},
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(
            result,
            ["response", "thinking", "thinking_enabled", "model", "provider", "finish_reason"],
        )
        assert result["result"]["provider"] == provider

        # One dispatch into execute_chat with the node_type the user chose.
        assert harness.ai_service.execute_chat.await_count == 1
        call = harness.ai_service.execute_chat.await_args
        assert call.args[1] == node_type  # execute_chat(node_id, node_type, params)
        params = call.args[2]
        assert params["prompt"] == "hello"
        assert params["model"] == "test-model"

    @pytest.mark.parametrize("node_type,provider", ALL_PROVIDERS)
    async def test_api_key_auto_injected_by_executor(self, harness, node_type, provider):
        """NodeExecutor._inject_api_keys must fetch 'api_key' per provider."""
        harness.ai_service.execute_chat = AsyncMock(return_value=_canned_success(provider))

        await harness.execute(node_type, {"prompt": "hi"})

        # auth.get_api_key was called with the provider derived from node_type
        calls = [c.args for c in harness.ai_service.auth.get_api_key.await_args_list]
        assert any(c[0] == provider for c in calls), f"expected get_api_key({provider}, ...) for {node_type}, got {calls}"

        # Plugin Params use snake_case throughout; model_dump() preserves field names.
        params = harness.ai_service.execute_chat.await_args.args[2]
        assert params.get("api_key") == "test-api-key"

    @pytest.mark.parametrize("node_type,provider", ALL_PROVIDERS)
    async def test_error_envelope_propagates(self, harness, node_type, provider):
        harness.ai_service.execute_chat = AsyncMock(
            return_value={
                "success": False,
                "node_id": "test-node",
                "node_type": node_type,
                "error": "API key is required",
                "execution_time": 0.0,
                "timestamp": "2026-04-15T00:00:00",
            }
        )

        result = await harness.execute(node_type, {"prompt": "hi"})

        harness.assert_envelope(result, success=False)
        assert "api key" in result["error"].lower()


# ============================================================================
# Parameter forwarding: thinking config flows verbatim through the handler
# ============================================================================


class TestThinkingParamForwarding:
    """The handler does NOT interpret thinking params - it forwards them.
    Interpretation happens inside AIService.execute_chat. We just verify
    that whatever the user sets reaches execute_chat untouched.
    """

    @pytest.mark.parametrize("node_type,provider", ALL_PROVIDERS)
    async def test_thinking_flags_forwarded(self, harness, node_type, provider):
        harness.ai_service.execute_chat = AsyncMock(return_value=_canned_success(provider))

        await harness.execute(
            node_type,
            {
                "prompt": "hi",
                "thinking_enabled": True,
                "thinking_budget": 4096,
                "reasoning_effort": "high",
                "reasoning_format": "parsed",
            },
        )

        params = harness.ai_service.execute_chat.await_args.args[2]
        assert params["thinking_enabled"] is True
        assert params["thinking_budget"] == 4096
        assert params["reasoning_effort"] == "high"
        assert params["reasoning_format"] == "parsed"

    @pytest.mark.parametrize("node_type,provider", ALL_PROVIDERS)
    async def test_system_message_forwarded(self, harness, node_type, provider):
        harness.ai_service.execute_chat = AsyncMock(return_value=_canned_success(provider))

        await harness.execute(
            node_type,
            {"prompt": "hi", "system_prompt": "You are terse."},
        )

        params = harness.ai_service.execute_chat.await_args.args[2]
        # Plugin Params field is system_prompt; model_dump preserves the name.
        assert params["system_prompt"] == "You are terse."


# ============================================================================
# Provider-specific quirks documented in the *.md docs
# ============================================================================


class TestOpenRouterFreePrefix:
    """OpenRouter's docs note the [FREE] prefix is purely cosmetic.
    The handler itself passes it through; stripping happens in execute_chat.
    """

    async def test_free_prefix_reaches_execute_chat_verbatim(self, harness):
        harness.ai_service.execute_chat = AsyncMock(return_value=_canned_success("openrouter"))

        await harness.execute(
            "openrouterChatModel",
            {"prompt": "hi", "model": "[FREE] google/gemma-7b-it"},
        )

        params = harness.ai_service.execute_chat.await_args.args[2]
        # Handler does not strip - that's execute_chat's job
        assert params["model"] == "[FREE] google/gemma-7b-it"


class TestOpenRouterSlashPrefixPreserved:
    """Unique to OpenRouter: owner/model slash is load-bearing.
    execute_chat preserves it when provider=='openrouter', strips it
    otherwise. Here we only confirm the handler passes the model through."""

    async def test_owner_model_slash_passes_through_handler(self, harness):
        harness.ai_service.execute_chat = AsyncMock(return_value=_canned_success("openrouter"))

        await harness.execute(
            "openrouterChatModel",
            {"prompt": "hi", "model": "anthropic/claude-3.5-sonnet"},
        )

        params = harness.ai_service.execute_chat.await_args.args[2]
        assert params["model"] == "anthropic/claude-3.5-sonnet"


class TestAnthropicHyphenatedModelIds:
    """Docs: Anthropic uses hyphens (claude-sonnet-4-6), not dots.
    Handler passes the model string through unchanged - it is execute_chat /
    the Anthropic SDK that ultimately rejects dotted IDs.
    """

    async def test_hyphenated_model_forwarded(self, harness):
        harness.ai_service.execute_chat = AsyncMock(return_value=_canned_success("anthropic"))

        await harness.execute(
            "anthropicChatModel",
            {"prompt": "hi", "model": "claude-sonnet-4-6"},
        )

        params = harness.ai_service.execute_chat.await_args.args[2]
        assert params["model"] == "claude-sonnet-4-6"


class TestKimiThinkingDefault:
    """Docs: Kimi k2.5 defaults thinking=ON. The handler itself does not
    apply the default - it just forwards whatever the frontend submitted.
    This test pins the pass-through behaviour.
    """

    async def test_kimi_handler_forwards_thinking_disabled_verbatim(self, harness):
        harness.ai_service.execute_chat = AsyncMock(return_value=_canned_success("kimi"))

        await harness.execute(
            "kimiChatModel",
            {"prompt": "hi", "thinking_enabled": False},
        )

        params = harness.ai_service.execute_chat.await_args.args[2]
        assert params["thinking_enabled"] is False


class TestGroqReasoningFormat:
    """Docs: only Qwen3-32b honors reasoningFormat. Handler passes both
    the format and the model through unchanged."""

    async def test_reasoning_format_hidden_forwarded(self, harness):
        harness.ai_service.execute_chat = AsyncMock(return_value=_canned_success("groq"))

        await harness.execute(
            "groqChatModel",
            {
                "prompt": "hi",
                "model": "qwen/qwen3-32b",
                "thinking_enabled": True,
                "reasoning_format": "hidden",
            },
        )

        params = harness.ai_service.execute_chat.await_args.args[2]
        assert params["reasoning_format"] == "hidden"
        assert params["model"] == "qwen/qwen3-32b"


class TestDeepseekReasonerAlwaysOnCoT:
    """Docs: deepseek-reasoner produces reasoning_content regardless of
    thinkingEnabled. Handler just forwards. Assert the thinking field in
    the canned response surfaces back through the envelope.
    """

    async def test_thinking_content_surfaces_in_envelope(self, harness):
        canned = _canned_success("deepseek", model="deepseek-reasoner")
        canned["result"]["thinking"] = "step 1: ... step 2: ..."
        canned["result"]["thinking_enabled"] = False  # reasoner CoT is always-on
        harness.ai_service.execute_chat = AsyncMock(return_value=canned)

        result = await harness.execute(
            "deepseekChatModel",
            {"prompt": "hi", "model": "deepseek-reasoner"},
        )

        harness.assert_envelope(result, success=True)
        assert result["result"]["thinking"] == "step 1: ... step 2: ..."
        assert result["result"]["thinking_enabled"] is False


class TestMistralNoThinking:
    """Docs: Mistral does not support thinking. Handler still forwards the
    flag (execute_chat / provider SDK ignore it). Envelope should show
    thinking=null."""

    async def test_thinking_flag_forwarded_but_result_has_null_thinking(self, harness):
        canned = _canned_success("mistral", model="mistral-large-latest")
        canned["result"]["thinking"] = None
        canned["result"]["thinking_enabled"] = False
        harness.ai_service.execute_chat = AsyncMock(return_value=canned)

        result = await harness.execute(
            "mistralChatModel",
            {
                "prompt": "hi",
                "model": "mistral-large-latest",
                "thinking_enabled": True,  # User tries to enable
            },
        )

        harness.assert_envelope(result, success=True)
        # Handler forwarded the user's flag
        params = harness.ai_service.execute_chat.await_args.args[2]
        assert params["thinking_enabled"] is True
        # But envelope reflects Mistral's lack of support
        assert result["result"]["thinking"] is None
        assert result["result"]["thinking_enabled"] is False


# ============================================================================
# Empty prompt / missing api_key behaviour
# ============================================================================


class TestValidationErrors:
    """The handler delegates validation to AIService.execute_chat, which
    raises ValueError on empty prompt or missing api_key and catches it
    into an error envelope. We pin that contract here by having the mock
    mirror the real behaviour.
    """

    async def test_empty_prompt_returns_error_envelope(self, harness):
        harness.ai_service.execute_chat = AsyncMock(
            return_value={
                "success": False,
                "node_id": "test-node",
                "node_type": "openaiChatModel",
                "error": "Prompt cannot be empty",
                "execution_time": 0.0,
                "timestamp": "2026-04-15T00:00:00",
            }
        )

        result = await harness.execute("openaiChatModel", {"prompt": ""})

        harness.assert_envelope(result, success=False)
        assert "prompt" in result["error"].lower()

    async def test_missing_api_key_returns_error_envelope(self, harness):
        # Override auth to return None for the key
        harness.ai_service.auth.get_api_key = AsyncMock(return_value=None)
        harness.ai_service.execute_chat = AsyncMock(
            return_value={
                "success": False,
                "node_id": "test-node",
                "node_type": "openaiChatModel",
                "error": "API key is required",
                "execution_time": 0.0,
                "timestamp": "2026-04-15T00:00:00",
            }
        )

        result = await harness.execute("openaiChatModel", {"prompt": "hi"})

        harness.assert_envelope(result, success=False)
        assert "api key" in result["error"].lower()
