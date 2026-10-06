"""Real SDK failures retain provider-independent retry and action semantics."""

import anthropic
import httpx
import openai
import pytest

from services.llm.protocol import LLMError, LLMErrorCategory, valid_retry_delay
from unittest.mock import MagicMock


def sdk_error(provider, status, message, *, body=None, headers=None):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.test/v1"), headers=headers)
    sdk = openai if provider == "openai" else anthropic
    error_type = sdk.RateLimitError if status == 429 else sdk.APIStatusError
    return error_type(message, response=response, body=body)


@pytest.mark.parametrize(("provider", "status", "category"), [
    ("openai", 429, LLMErrorCategory.RATE_LIMIT),
    ("anthropic", 429, LLMErrorCategory.RATE_LIMIT),
    ("openai", 500, LLMErrorCategory.SERVER),
    ("anthropic", 529, LLMErrorCategory.SERVER),
])
def test_http_status_overrides_incidental_api_key_text(provider, status, category):
    error = LLMError.from_exception(provider, sdk_error(provider, status, "Rate limit reached for this API key"))
    assert error.category == category
    assert error.retryable
    assert not error.requires_user_action


@pytest.mark.parametrize(("status", "category"), [(401, LLMErrorCategory.AUTHENTICATION), (403, LLMErrorCategory.PERMISSION)])
def test_auth_and_permission_http_failures_remain_terminal(status, category):
    error = LLMError.from_exception("openai", sdk_error("openai", status, "Check your API key"))
    assert error.category == category
    assert not error.retryable


@pytest.mark.parametrize(("provider", "status", "body"), [
    ("openai", 429, {"code": "insufficient_quota"}),
    ("openai", 429, {"error": {"type": "insufficient_quota"}}),
    ("anthropic", 400, {"error": {"type": "insufficient_credits"}}),
    ("openai", 403, {"error": {"status": "billing_hard_limit_reached"}}),
])
def test_structured_billing_signature_wins_over_generic_message(provider, status, body):
    error = LLMError.from_exception(provider, sdk_error(provider, status, "Request rejected", body=body))
    assert error.category == LLMErrorCategory.BILLING
    assert not error.retryable
    assert error.requires_user_action


@pytest.mark.parametrize(("headers", "expected"), [
    ({"retry-after": "2.5", "retry-after-ms": "9000"}, 9),
    ({"retry-after": "20", "retry-after-ms": "1500"}, 20),
    ({"retry-after-ms": "1500.5"}, 1.5005),
    ({"retry-after": "0"}, 0),
    ({"retry-after": "Wed, 21 Oct 2037 07:28:00 GMT", "date": "Wed, 21 Oct 2037 07:27:00 GMT", "retry-after-ms": "90000"}, 90),
    ({"retry-after": "Wed, 21 Oct 2037 07:26:00 GMT", "date": "Wed, 21 Oct 2037 07:27:00 GMT"}, 0),
])
def test_longest_valid_header_hint_is_used(headers, expected):
    error = LLMError.from_exception("openai", sdk_error("openai", 429, "slow down", headers=headers))
    assert error.retry_after == pytest.approx(expected)


@pytest.mark.parametrize("invalid", ["nan", "inf", "-1", "1e300", "315576000001", "tomorrow", ""])
@pytest.mark.parametrize("header", ["retry-after", "retry-after-ms"])
def test_bad_hint_cannot_replace_normal_backoff(invalid, header):
    if header == "retry-after-ms" and invalid == "315576000001":
        invalid = "315576000001000"
    error = LLMError.from_exception("anthropic", sdk_error("anthropic", 429, "slow down", headers={header: invalid}))
    assert error.retry_after is None
    assert error.retryable


@pytest.mark.parametrize("date_header", [None, "not a date"])
def test_http_date_without_valid_response_date_uses_current_utc(monkeypatch, date_header):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import services.llm.protocol as protocol

    now = datetime(2037, 10, 21, 7, 27, tzinfo=timezone.utc)
    monkeypatch.setattr(protocol, "datetime", SimpleNamespace(now=lambda tz: now))
    headers = {"retry-after": "Wed, 21 Oct 2037 07:28:00 GMT"}
    if date_header is not None:
        headers["date"] = date_header
    error = LLMError.from_exception("openai", sdk_error("openai", 429, "slow down", headers=headers))
    assert error.retry_after == 60


@pytest.mark.parametrize("value", [None, True, False, "5", 0, -1, float("nan"), float("inf"), 1e300, 315576000001])
def test_manual_delays_require_a_positive_representable_number(value):
    assert valid_retry_delay(value) is None


@pytest.mark.parametrize("value", [0.001, 5, 315576000000])
def test_valid_manual_delays_preserve_the_provider_minimum(value):
    assert valid_retry_delay(value) == value


@pytest.mark.parametrize("invalid", [None, True, False, 1, 99, 600, -429, 429.5, float("nan"), float("inf"), "429.0", "unknown", MagicMock()])
def test_typed_authentication_survives_malformed_http_status(invalid):
    exc = openai.AuthenticationError("Rejected credential", response=httpx.Response(401,
        request=httpx.Request("POST", "https://example.test/v1")), body=None)
    exc.status_code = invalid
    exc.response.status_code = invalid
    error = LLMError.from_exception("openai", exc)
    assert error.status_code is None
    assert error.category == LLMErrorCategory.AUTHENTICATION
    assert not error.retryable


@pytest.mark.parametrize(("status", "category"), [("429", LLMErrorCategory.RATE_LIMIT), (404, LLMErrorCategory.NOT_FOUND), (500.0, LLMErrorCategory.SERVER)])
def test_genuine_http_status_overrides_conflicting_typed_authentication(status, category):
    exc = openai.AuthenticationError("Rejected API key", response=httpx.Response(401,
        request=httpx.Request("POST", "https://example.test/v1")), body=None)
    exc.status_code = status
    error = LLMError.from_exception("openai", exc)
    assert error.status_code == int(status)
    assert error.category == category


@pytest.mark.parametrize("fallback", ["response", "body"])
def test_malformed_primary_status_does_not_hide_valid_later_status(fallback):
    exc = Exception("Capacity temporarily unavailable")
    exc.status_code = MagicMock()
    exc.code = "not_an_http_status"
    if fallback == "response":
        from types import SimpleNamespace
        exc.response = SimpleNamespace(status_code="429", headers={})
    else:
        exc.body = {"error": {"code": 429}}
    error = LLMError.from_exception("openai", exc)
    assert error.status_code == 429
    assert error.category == LLMErrorCategory.RATE_LIMIT
    assert error.retryable


@pytest.mark.parametrize(("status", "category"), [(404, LLMErrorCategory.NOT_FOUND), (400, LLMErrorCategory.INVALID_REQUEST), (409, LLMErrorCategory.INVALID_REQUEST), (422, LLMErrorCategory.INVALID_REQUEST)])
def test_explicit_http_rejection_is_not_throttling_because_message_mentions_rate_limit(status, category):
    error = LLMError.from_exception("openai", sdk_error("openai", status, "Invalid model setting: rate limit configuration"))
    assert error.category == category
    assert not error.retryable


def test_statusless_typed_rate_limit_retains_retry_semantics():
    exc = sdk_error("openai", 429, "Capacity unavailable")
    exc.status_code = None
    exc.response.status_code = MagicMock()
    error = LLMError.from_exception("openai", exc)
    assert error.status_code is None
    assert error.category == LLMErrorCategory.RATE_LIMIT
    assert error.retryable


def test_context_limit_stays_specific_despite_invalid_request_status():
    error = LLMError.from_exception("openai", sdk_error("openai", 400, "Request exceeds context length"))
    assert error.category == LLMErrorCategory.CONTEXT_LENGTH
    assert not error.retryable
