"""Thinking replay failures expose fixed identifiers without provider text."""

from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from services.llm.protocol import LLMError, LLMErrorCategory


INVALID_SIGNATURE = "messages.12.content.0: Invalid `signature` in `thinking` block."
PREFIX_MISMATCH = f"{INVALID_SIGNATURE} The block is bound to a different conversation."
SECRET = "private prompt sk-secret https://internal.test/?token=secret"


def provider_exception(message, *, status=400, error_type="invalid_request_error"):
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        headers={"request-id": "req-thinking"},
    )
    return anthropic.BadRequestError(
        message,
        response=response,
        body={"error": {"type": error_type, "message": message}},
    )


@pytest.mark.parametrize(("message", "expected"), [
    (PREFIX_MISMATCH, "thinking_prefix_mismatch"),
    (f"{PREFIX_MISMATCH} The system prompt changed: {SECRET}", "thinking_prefix_mismatch"),
    (f"{PREFIX_MISMATCH} New provider explanation: {SECRET}", "thinking_prefix_mismatch"),
    (INVALID_SIGNATURE, "thinking_signature_invalid"),
    ("Invalid `signature` in `thinking` block", "thinking_signature_invalid"),
    (f"{INVALID_SIGNATURE} {SECRET}", "thinking_signature_invalid"),
    (f"{INVALID_SIGNATURE} Unrelated explanation mentions: The block is bound to a different conversation.",
     "thinking_signature_invalid"),
])
def test_recognizes_only_stable_signature_sentences(message, expected):
    error = LLMError.from_exception("anthropic", provider_exception(message))

    assert error.diagnostic_code == expected
    assert error.category == LLMErrorCategory.INVALID_REQUEST
    assert error.retryable is False
    assert error.request_id == "req-thinking"
    assert error.message == message  # Retained internally, never exported below.
    assert SECRET not in error.user_message
    assert SECRET not in error.hint


@pytest.mark.parametrize(("provider", "status", "error_type", "message"), [
    ("openai", 400, "invalid_request_error", PREFIX_MISMATCH),
    ("anthropic", 404, "invalid_request_error", PREFIX_MISMATCH),
    ("anthropic", 400, "other_error", PREFIX_MISMATCH),
    ("anthropic", 400, "invalid_request_error", "Invalid tool schema"),
    ("anthropic", 400, "invalid_request_error", f"Prompt contents include {PREFIX_MISMATCH}"),
    ("anthropic", 400, "invalid_request_error", None),
])
def test_other_failures_have_no_thinking_diagnostic(provider, status, error_type, message):
    exc = provider_exception(message, status=status, error_type=error_type)
    error = LLMError.from_exception(provider, exc)

    assert error.diagnostic_code is None


def test_sdk_text_alone_cannot_introduce_a_diagnostic():
    exc = provider_exception(PREFIX_MISMATCH)
    exc.body = None

    assert LLMError.from_exception("anthropic", exc).diagnostic_code is None


def test_failure_log_reports_fixed_diagnostic_without_raw_provider_text(monkeypatch):
    import services.llm.unifier as unifier_module

    logger = MagicMock()
    monkeypatch.setattr(unifier_module, "logger", logger)
    error = LLMError.from_exception("anthropic", provider_exception(f"{PREFIX_MISMATCH} {SECRET}"))

    unifier_module.ChatUnifier._log_failure("LLM provider request failed", error, None, "anthropic")

    fields = logger.warning.call_args.kwargs
    assert fields["diagnostic_code"] == "thinking_prefix_mismatch"
    assert fields["request_id"] == "req-thinking"
    assert SECRET not in str(logger.warning.call_args)
    assert PREFIX_MISMATCH not in str(logger.warning.call_args)


def test_unrelated_failure_log_omits_diagnostic(monkeypatch):
    import services.llm.unifier as unifier_module

    logger = MagicMock()
    monkeypatch.setattr(unifier_module, "logger", logger)
    error = LLMError.from_exception("anthropic", provider_exception("Invalid tool schema"))

    unifier_module.ChatUnifier._log_failure("LLM provider request failed", error, None, "anthropic")

    assert "diagnostic_code" not in logger.warning.call_args.kwargs
