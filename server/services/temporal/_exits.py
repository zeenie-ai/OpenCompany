"""Exit cleanup never runs for a bug in workflow code (RFC-0006 section 18.2).

A plain exception raised by workflow code fails the workflow task, not the
run: the run stays open, the task is retried, and a fixed release replays it
from where it stopped. Cleanup that schedules commands on the way out would
first record them in an activation that completes, and the fixed release
could then no longer replay that history. So exit cleanup and per-event
handlers re-raise a bug instead of recording cleanup or skipping the event.
"""

import asyncio

from temporalio.exceptions import FailureError


def is_workflow_bug(exc: BaseException) -> bool:
    """True for an exception the SDK answers by failing the task, not the run.

    This is the SDK's rule when nothing configures failure exception types
    (``workflow_is_failure_exception`` in temporalio's worker): a
    ``FailureError`` or an ``asyncio.TimeoutError`` fails the run, any other
    ``Exception`` fails the task. Cancellation, continue-as-new and eviction
    are ``BaseException``s, so none of them is a bug. Kept pure (no workflow
    event loop needed); ``tests/temporal/test_workflow_bug_guard.py`` checks
    that nothing configures failure types.
    """
    return isinstance(exc, Exception) and not isinstance(exc, (FailureError, asyncio.TimeoutError))
