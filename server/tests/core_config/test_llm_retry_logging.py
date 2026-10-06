"""Planned Temporal retries remain visible; genuine failures keep traces."""

from datetime import timedelta
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from temporalio.exceptions import ApplicationError

from core.logging import ExpectedLLMRetryFilter


def record(error, name="temporalio.activity"):
    return logging.LogRecord(name, logging.WARNING, __file__, 1,
                             "Completing activity as failed", (), (type(error), error, None))


@pytest.mark.parametrize("category", ["rate_limit", "server", "timeout", "connection"])
def test_planned_retry_has_safe_warning_and_keeps_activity_context(category):
    error = ApplicationError("Provider is temporarily unavailable.", {"retryable": True},
                             type=f"LLMError.{category}", next_retry_delay=timedelta(seconds=30))
    entry = record(error)
    entry.temporal_activity = {"workflow_id": "employee-run", "attempt": 2}
    assert ExpectedLLMRetryFilter().filter(entry)
    assert entry.getMessage() == "Language model request will retry: Provider is temporarily unavailable."
    assert entry.exc_info is None
    assert entry.levelno == logging.WARNING
    assert entry.temporal_activity == {"workflow_id": "employee-run", "attempt": 2}


@pytest.mark.parametrize("error", [
    RuntimeError("Unexpected bug"),
    ApplicationError("Quota exhausted", {"retryable": False}, type="LLMError.quota", non_retryable=True),
    ApplicationError("Invalid credentials", {"retryable": False}, type="LLMError.authentication", non_retryable=True),
    ApplicationError("Not scheduled", {"retryable": True}, type="LLMError.rate_limit"),
])
def test_terminal_and_unexpected_errors_keep_their_traceback(error):
    entry = record(error)
    original = entry.exc_info
    assert ExpectedLLMRetryFilter().filter(entry)
    assert entry.exc_info is original
    assert entry.getMessage() == "Completing activity as failed"


def test_filter_does_not_change_other_loggers():
    error = ApplicationError("retry", {"retryable": True}, type="LLMError.rate_limit", next_retry_delay=timedelta(seconds=5))
    entry = record(error, "services.other")
    assert ExpectedLLMRetryFilter().filter(entry)
    assert entry.exc_info is not None


def test_configured_sdk_logger_installs_filter_once_and_applies_it(monkeypatch):
    import core.logging as logging_module

    activity_logger = logging.getLogger("temporalio.activity")
    monkeypatch.setattr(activity_logger, "filters", [])
    monkeypatch.setattr(logging_module.logging, "basicConfig", Mock())
    monkeypatch.setattr(logging_module.structlog, "configure", Mock())
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)
    settings = SimpleNamespace(log_level="INFO", log_file=None, log_format="json")
    logging_module.configure_logging(settings)
    logging_module.configure_logging(settings)
    assert len(activity_logger.filters) == 1
    error = ApplicationError("Provider is rate-limiting requests.", {"retryable": True},
                             type="LLMError.rate_limit", next_retry_delay=timedelta(seconds=30))
    entry = record(error)
    assert activity_logger.filter(entry)
    assert entry.exc_info is None
    assert "will retry" in entry.getMessage()
