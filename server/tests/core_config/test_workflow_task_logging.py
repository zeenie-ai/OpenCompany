"""A failed workflow task is logged at ERROR (1b.29): the workflow is stuck."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

from core.logging import WorkflowTaskFailureFilter

_SDK_LOGGER = "temporalio.worker._workflow_instance"


def record(identifier=None):
    entry = logging.LogRecord(_SDK_LOGGER, logging.WARNING, __file__, 1, "Failed activation on workflow", (), None)
    if identifier:
        # The SDK passes it through ``extra``; the attribute name is literal.
        setattr(entry, "__temporal_error_identifier", identifier)
    return entry


def test_a_failed_workflow_task_is_raised_to_error():
    entry = record("WorkflowTaskFailure")
    assert WorkflowTaskFailureFilter().filter(entry)
    assert entry.levelno == logging.ERROR
    assert entry.levelname == "ERROR"


def test_other_records_keep_their_level():
    for entry in (record(), record("ActivityFailure")):
        assert WorkflowTaskFailureFilter().filter(entry)
        assert entry.levelno == logging.WARNING
        assert entry.levelname == "WARNING"


def test_configured_sdk_logger_installs_the_filter_once(monkeypatch):
    import core.logging as logging_module

    workflow_logger = logging.getLogger(_SDK_LOGGER)
    monkeypatch.setattr(workflow_logger, "filters", [])
    monkeypatch.setattr(logging.getLogger("temporalio.activity"), "filters", [])
    monkeypatch.setattr(logging_module.logging, "basicConfig", Mock())
    monkeypatch.setattr(logging_module.structlog, "configure", Mock())
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)
    settings = SimpleNamespace(log_level="INFO", log_file=None, log_format="json")
    logging_module.configure_logging(settings)
    logging_module.configure_logging(settings)
    assert len(workflow_logger.filters) == 1
    entry = record("WorkflowTaskFailure")
    assert workflow_logger.filter(entry)
    assert entry.levelno == logging.ERROR
