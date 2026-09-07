"""Temporal activity failure boundary for plugin nodes.

A plugin never raises out of ``BaseNode.execute``: every failure comes
back as the structured envelope ``{success: False, error, error_type,
retryable, ...}``. Until this module existed the per-type activity
returned that envelope as a *successful* completion, so Temporal never
saw a failure and no ``RetryPolicy`` ever engaged. The helpers here turn
the envelope into a typed ``ApplicationError`` on the activity side and
turn that error back into the envelope on the workflow side, so the
orchestrator, the deployment circuit breaker, and the LLM tool message
all keep the plugin's own text.

Design notes (docs.temporal.io/develop/python/failure-detection):

* Temporal stops retrying when EITHER the ``ApplicationError.type`` is in
  the scheduled policy's ``non_retryable_error_types`` OR the error was
  raised with ``non_retryable=True``. The plugin's verdict therefore has
  to be encoded in both places: a retryable failure whose envelope
  ``error_type`` happens to be a non-retryable name (a ``NodeUserError``
  wrapping a rate-limited provider) is raised under ``"<type>.retryable"``
  so the server's name list cannot veto it.
* The activity self-caps from ``activity.info()``. The scheduled policy
  is whatever the workflow recorded when it scheduled the activity, which
  for tool calls made before this change was nothing at all (Temporal's
  default: unlimited attempts). Capping at
  ``min(scheduled, plugin effective)`` protects both those in-flight
  histories and mutating nodes whose first attempt died mid-flight.
* ``details[0]`` carries the full envelope. The Python SDK decodes
  ``ApplicationError.details`` with the data converter, so the workflow
  gets the same dict the activity had.

Import discipline: this module is imported by workflow code
(``MachinaWorkflow`` / ``AgentWorkflow`` unwrap failures with
:func:`activity_failure_envelope`), so its top level touches only the
standard library and ``temporalio``. Anything that reaches into
``services.plugin`` or the DI container is imported inside the
activity-side functions.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional, Tuple, Type

from temporalio import activity
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    CancelledError,
    ChildWorkflowError,
    TimeoutError as TemporalTimeoutError,
)

from ._retry_policies import NON_RETRYABLE_ERROR_TYPES

#: ``ApplicationError.type`` raised when an attempt arrives past the
#: plugin's cap (a re-dispatch after a crash or timeout on a node that
#: must not run twice). Always ``non_retryable``.
RETRY_ATTEMPTS_EXHAUSTED = "RetryAttemptsExhausted"

#: Suffix appended to ``error_type`` when the envelope says retryable but
#: the bare name sits in a non-retryable list. Mirrors the
#: ``LLMError.<category>`` naming used by ``agent_activities``.
RETRYABLE_TYPE_SUFFIX = ".retryable"

# Cause chains are ``ActivityError -> ApplicationError`` (one hop) or
# ``ChildWorkflowError -> ActivityError -> ApplicationError`` (two); the
# guard only exists so a malformed chain cannot loop.
_MAX_UNWRAP_DEPTH = 5


def plugin_failure_retries_enabled() -> bool:
    """Read ``Settings.temporal_plugin_failure_retries`` activity-side.

    Goes through the DI container's settings singleton (the pattern the
    pause-on-failure activity uses) rather than constructing ``Settings``
    per call. Unit tests that run an activity without a wired container
    get the production default (on).
    """
    try:
        from core.container import container

        return bool(getattr(container.settings(), "temporal_plugin_failure_retries", True))
    except Exception:  # noqa: BLE001 -- container not wired (unit tests)
        return True


def effective_max_attempts(node_cls: Optional[Type[Any]]) -> int:
    """Return the plugin's own attempt cap (``effective_retry_policy``)."""
    if node_cls is None or not hasattr(node_cls, "effective_retry_policy"):
        from services.plugin.scaling import DEFAULT_RETRY

        return int(DEFAULT_RETRY.maximum_attempts)
    return int(node_cls.effective_retry_policy().maximum_attempts)


def activity_attempt_info(node_cls: Optional[Type[Any]]) -> Tuple[int, int, Optional[str]]:
    """Return ``(attempt, max_attempts, idempotency_key)`` for this run.

    ``max_attempts`` is the plugin's effective cap, further bounded by the
    policy the workflow scheduled with when that policy is bounded. A
    scheduled policy of ``None`` or ``maximum_attempts == 0`` (Temporal's
    "unlimited") contributes nothing, so the plugin cap alone governs.
    ``idempotency_key`` follows the Temporal Python docs:
    ``f"{workflow_run_id}-{activity_id}"`` -- stable across the attempts
    of one logical execution, distinct across executions.

    Outside an activity context (direct unit tests, the in-process path)
    this is ``(1, cap, None)``.
    """
    cap = effective_max_attempts(node_cls)
    try:
        info = activity.info()
    except RuntimeError:
        return 1, cap, None

    scheduled = getattr(info.retry_policy, "maximum_attempts", None)
    if isinstance(scheduled, int) and scheduled > 0:
        max_attempts = min(scheduled, cap)
    else:
        max_attempts = cap
    idempotency_key = f"{info.workflow_run_id}-{info.activity_id}"
    return int(info.attempt), max_attempts, idempotency_key


def _is_non_retryable_name(error_type: str) -> bool:
    from services.plugin.retryability import NON_RETRYABLE_ENVELOPE_TYPES

    return error_type in NON_RETRYABLE_ENVELOPE_TYPES or error_type in NON_RETRYABLE_ERROR_TYPES


def build_plugin_failure(
    envelope: Dict[str, Any],
    *,
    node_cls: Optional[Type[Any]],
    attempt: Optional[int] = None,
    max_attempts: Optional[int] = None,
) -> ApplicationError:
    """Translate a failure envelope into a typed ``ApplicationError``.

    ``non_retryable`` is set when the failure is permanent OR this attempt
    is the last one the plugin allows, so Temporal never schedules an
    attempt the activity would refuse anyway. The full envelope rides as
    ``details[0]``.
    """
    from services.plugin.retryability import classify_retryable

    error_type = str(envelope.get("error_type") or "Error")
    retryable = envelope.get("retryable")
    if not isinstance(retryable, bool):
        retryable = classify_retryable(None, error_type=error_type)

    if attempt is None or max_attempts is None:
        attempt, max_attempts, _ = activity_attempt_info(node_cls)
    final = (not retryable) or attempt >= max_attempts

    type_name = error_type
    if retryable and _is_non_retryable_name(error_type):
        type_name = f"{error_type}{RETRYABLE_TYPE_SUFFIX}"

    next_retry_delay: Optional[timedelta] = None
    retry_after = envelope.get("retry_after_seconds")
    if not final and isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool) and retry_after > 0:
        next_retry_delay = timedelta(seconds=float(retry_after))

    return ApplicationError(
        str(envelope.get("error") or "Node failed"),
        envelope,
        type=type_name,
        non_retryable=final,
        next_retry_delay=next_retry_delay,
    )


def attempts_exhausted_failure(node_id: str, attempt: int, max_attempts: int) -> ApplicationError:
    """Refuse to run an attempt past the plugin's cap.

    Raised BEFORE the node body so a mutating node whose earlier attempt
    died after its side effect (worker crash, heartbeat timeout) is not
    re-run by a policy that was scheduled with more attempts than the
    plugin allows.
    """
    message = (
        f"Node {node_id} attempt {attempt} exceeds its retry cap of {max_attempts}; "
        "refusing to re-run a non-idempotent activity"
    )
    envelope = {
        "success": False,
        "error": message,
        "error_type": RETRY_ATTEMPTS_EXHAUSTED,
        "retryable": False,
        "node_id": node_id,
        "attempt": attempt,
        "max_attempts": max_attempts,
    }
    return ApplicationError(message, envelope, type=RETRY_ATTEMPTS_EXHAUSTED, non_retryable=True)


def activity_failure_envelope(exc: BaseException) -> Dict[str, Any]:
    """Recover the plugin's failure envelope from a workflow-side exception.

    Walks ``ActivityError`` / ``ChildWorkflowError`` causes to the
    ``ApplicationError`` the activity raised and returns its ``details[0]``
    envelope when present, so the orchestrator sees exactly what the
    activity saw. Timeouts, cancellations, and plain exceptions get a
    minimal envelope with a descriptive ``error_type`` instead of the
    SDK wrapper text "Activity task failed".
    """
    cause: BaseException = exc
    depth = 0
    while isinstance(cause, (ActivityError, ChildWorkflowError)) and depth < _MAX_UNWRAP_DEPTH:
        inner = cause.cause
        if inner is None:
            break
        cause = inner
        depth += 1

    if isinstance(cause, ApplicationError):
        details = list(cause.details) if cause.details else []
        first = details[0] if details else None
        if isinstance(first, dict) and first.get("success") is False:
            envelope = dict(first)
            envelope.setdefault("error", cause.message)
            envelope.setdefault("error_type", cause.type or "Error")
            return envelope
        return {"success": False, "error": cause.message, "error_type": cause.type or "Error"}

    if isinstance(cause, TemporalTimeoutError):
        timeout_kind = getattr(cause.type, "name", None) or str(cause.type)
        return {
            "success": False,
            "error": f"{cause.message} ({timeout_kind})",
            "error_type": "TimeoutError",
            "retryable": True,
        }

    if isinstance(cause, CancelledError):
        return {"success": False, "error": "Cancelled", "error_type": "Cancelled", "retryable": False}

    return {
        "success": False,
        "error": str(cause) or type(cause).__name__,
        "error_type": type(cause).__name__,
    }


__all__ = [
    "RETRYABLE_TYPE_SUFFIX",
    "RETRY_ATTEMPTS_EXHAUSTED",
    "activity_attempt_info",
    "activity_failure_envelope",
    "attempts_exhausted_failure",
    "build_plugin_failure",
    "effective_max_attempts",
    "plugin_failure_retries_enabled",
]
