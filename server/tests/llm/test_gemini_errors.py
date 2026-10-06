"""Gemini's real SDK error envelope must drive retry versus owner action."""

from types import SimpleNamespace

import pytest
from google.genai.errors import ClientError, ServerError

from services.llm.protocol import LLMError, LLMErrorCategory


def gemini_error(*details, message="Resource exhausted", status=429):
    error_type = ServerError if status >= 500 else ClientError
    return error_type(status, {"error": {
        "code": status,
        "status": "INTERNAL" if status >= 500 else "RESOURCE_EXHAUSTED",
        "message": message,
        "details": list(details),
    }})


def retry_info(delay):
    return {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}


def quota_failure(*violations):
    return {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": list(violations)}


def test_logged_internal_error_is_transient_not_quota():
    error = LLMError.from_exception("gemini", gemini_error(status=500, message="Internal error encountered."))
    assert error.category == LLMErrorCategory.SERVER
    assert error.status_code == 500
    assert error.retryable
    assert not error.requires_user_action
    assert error.user_message == "Gemini is temporarily unavailable."


def test_minute_quota_honors_fractional_retry_info():
    error = LLMError.from_exception("gemini", gemini_error(
        quota_failure({"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}),
        retry_info("43.123456789s"),
    ))
    assert error.category == LLMErrorCategory.RATE_LIMIT
    assert error.retryable
    assert not error.requires_user_action
    assert error.retry_after == pytest.approx(43.123456789)
    assert error.retry_after_raw == "43.123456789s"


@pytest.mark.parametrize("details", [(), (quota_failure(),), (quota_failure({"description": "Quota exceeded"}),)])
def test_generic_resource_exhausted_does_not_imply_a_hard_quota(details):
    error = LLMError.from_exception("gemini", gemini_error(*details))
    assert error.category == LLMErrorCategory.RATE_LIMIT
    assert error.retryable


@pytest.mark.parametrize("violation", [
    {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"},
    {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel", "quotaValue": "0"},
    {"quotaValue": 0},
])
def test_daily_or_zero_quota_requires_action_even_with_retry_info(violation):
    error = LLMError.from_exception("gemini", gemini_error(
        quota_failure({"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}, {
            **violation, "subject": "private-project", "description": "secret details",
        }),
        retry_info("39s"),
    ))
    assert error.category == LLMErrorCategory.QUOTA
    assert not error.retryable
    assert error.requires_user_action
    node_error = error.as_node_error().as_dict()
    assert node_error["requires_user_action"]
    assert "quota resets" in node_error["hint"]
    assert "private-project" not in str(node_error)
    assert "secret" not in str(node_error)


@pytest.mark.parametrize(("limit", "retryable"), [("0", False), ("10", True), ("0.5", True)])
def test_message_fallback_only_treats_an_explicit_zero_limit_as_blocked(limit, retryable):
    error = LLMError.from_exception("gemini", gemini_error(message=(
        f"Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: {limit}, model: test"
    )))
    assert error.retryable is retryable


def test_depleted_gemini_prepayment_is_billing_not_a_throttle():
    error = LLMError.from_exception("gemini", gemini_error(message="Prepayment credits are depleted."))
    assert error.category == LLMErrorCategory.BILLING
    assert error.requires_user_action
    assert not error.retryable


@pytest.mark.parametrize(("header", "body", "expected"), [("60", "20s", 60), ("5", "40s", 40)])
def test_retry_wait_never_shortens_either_provider_hint(header, body, expected):
    exc = gemini_error(retry_info(body), retry_info("1s"))
    exc.response = SimpleNamespace(headers={"retry-after": header})
    error = LLMError.from_exception("gemini", exc)
    assert error.retry_after == expected


@pytest.mark.parametrize("delay", [None, {}, 40, "-1s", "nans", "infs", "40", "1.1234567890s"])
def test_malformed_retry_info_uses_normal_backoff(delay):
    error = LLMError.from_exception("gemini", gemini_error(retry_info(delay)))
    assert error.retry_after is None
    assert error.retryable


@pytest.mark.parametrize("header", ["nan", "inf", "-1"])
def test_invalid_header_cannot_poison_a_valid_google_delay(header):
    exc = gemini_error(retry_info("30s"))
    exc.response = SimpleNamespace(headers={"retry-after": header})
    assert LLMError.from_exception("gemini", exc).retry_after == 30


@pytest.mark.parametrize("details", [None, "invalid", [{"violations": "invalid"}], [None]])
def test_malformed_google_details_do_not_hide_the_original_error(details):
    exc = gemini_error()
    exc.details["error"]["details"] = details
    error = LLMError.from_exception("gemini", exc)
    assert error.category == LLMErrorCategory.RATE_LIMIT
    assert error.retry_after is None


def test_google_body_hint_cannot_shorten_a_millisecond_header():
    exc = gemini_error(retry_info("30s"))
    exc.response = SimpleNamespace(headers={"retry-after-ms": "45000"})
    assert LLMError.from_exception("gemini", exc).retry_after == 45


def test_unrepresentable_google_duration_cannot_hide_valid_pacing():
    exc = gemini_error(retry_info("315576000001s"))
    exc.response = SimpleNamespace(headers={"retry-after": "15"})
    assert LLMError.from_exception("gemini", exc).retry_after == 15


@pytest.mark.parametrize("status", [429, 500])
def test_google_status_is_not_authentication_when_throttle_mentions_api_key(status):
    error = LLMError.from_exception("gemini", gemini_error(status=status, message="Capacity unavailable for this API key"))
    assert error.category == (LLMErrorCategory.RATE_LIMIT if status == 429 else LLMErrorCategory.SERVER)
    assert error.retryable


@pytest.mark.parametrize("quota_value", [0.5, "0.5", False, None, "invalid"])
def test_nonzero_or_malformed_quota_value_does_not_imply_terminal_zero_limit(quota_value):
    error = LLMError.from_exception("gemini", gemini_error(quota_failure({"quotaValue": quota_value})))
    assert error.retryable
