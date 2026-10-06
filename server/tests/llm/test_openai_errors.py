"""Responses API failure events must never become successful agent output."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openai.types.responses import Response, ResponseError, ResponseFailedEvent

from services.llm.protocol import LLMError, LLMErrorCategory, Message
from services.llm.providers.openai import OpenAIProvider


def response(status="failed", code="server_error", *, partial=False):
    output = [SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="Unreviewed partial")])] if partial else []
    return Response.model_construct(id="resp-test", model="gpt-test", status=status, output=output,
        error=ResponseError.model_construct(code=code, message="Private provider details") if code else None,
        usage=None, incomplete_details=SimpleNamespace(reason="max_output_tokens") if status == "incomplete" else None)


def provider():
    result = object.__new__(OpenAIProvider)
    result.provider_name = "openai"
    return result


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize(("code", "category", "retryable"), [
    ("server_error", LLMErrorCategory.SERVER, True),
    ("rate_limit_exceeded", LLMErrorCategory.RATE_LIMIT, True),
    ("insufficient_quota", LLMErrorCategory.BILLING, False),
    ("invalid_prompt", LLMErrorCategory.INVALID_REQUEST, False),
    ("overloaded_error", LLMErrorCategory.SERVER, True),
    ("vector_store_timeout", LLMErrorCategory.TIMEOUT, True),
    ("invalid_image", LLMErrorCategory.INVALID_REQUEST, False),
    ("unknown_provider_failure", LLMErrorCategory.UNKNOWN, False),
])
def test_failed_response_is_a_structured_error_even_with_partial_output(code, category, retryable, partial):
    with pytest.raises(LLMError) as raised:
        provider()._normalize_responses(response(code=code, partial=partial), "gpt-test")
    assert raised.value.category == category
    assert raised.value.retryable is retryable
    assert raised.value.provider_code == code
    assert "Private provider details" not in raised.value.user_message


def test_incomplete_max_tokens_remains_a_valid_partial_response():
    result = provider()._normalize_responses(response(status="incomplete", code=None, partial=True), "gpt-test")
    assert result.content == "Unreviewed partial"
    assert result.finish_reason == "incomplete"


def test_failed_response_without_error_details_still_cannot_succeed():
    with pytest.raises(LLMError) as raised:
        provider()._normalize_responses(response(code=None, partial=True), "gpt-test")
    assert not raised.value.retryable


def test_generation_error_preserves_request_identity_and_safe_owner_action():
    failed = response(code="insufficient_quota")
    failed._request_id = "req-test"
    with pytest.raises(LLMError) as raised:
        provider()._normalize_responses(failed, "gpt-test")
    assert raised.value.request_id == "req-test"
    assert raised.value.as_node_error().requires_user_action
    assert "Private provider details" not in str(raised.value.as_node_error().as_dict())


def test_error_on_nonfailed_body_is_never_treated_as_partial_success():
    with pytest.raises(LLMError):
        provider()._normalize_responses(response(status="completed", code="server_error", partial=True), "gpt-test")


@pytest.mark.asyncio
@pytest.mark.parametrize("has_error", [True, False])
async def test_real_sdk_failed_event_does_not_return_successful_completion(has_error):
    client = provider()
    failed = response(status="failed" if has_error else None, code="insufficient_quota" if has_error else None, partial=True)
    async def events():
        yield ResponseFailedEvent.model_construct(type="response.failed", response=failed, sequence_number=1)
    client._client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=events())))
    with pytest.raises(LLMError) as raised:
        await client._chat_responses(messages=[Message(role="user", content="go")], model="gpt-test", max_tokens=100,
            thinking=None, tools=None, context_management=None, on_event=AsyncMock())
    assert raised.value.category == (LLMErrorCategory.BILLING if has_error else LLMErrorCategory.UNKNOWN)
    assert not raised.value.retryable
