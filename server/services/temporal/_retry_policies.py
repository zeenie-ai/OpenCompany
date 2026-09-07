"""Wave 12 D1: shared Temporal RetryPolicy constants.

Replaces the duplicated inline ``RetryPolicy(1s, 30s, 3 attempts)``
declarations that lived in ``services/temporal/workflow.py:246`` and
``services/temporal/agent_workflow.py:76`` (identical knobs, hand-typed
twice). Adds the missing ``non_retryable_error_types`` declaration so
``NodeUserError`` (user-correctable failures: missing required field,
unknown enum value, bad regex) fails fast instead of burning all three
retry attempts.

Why these specific error types are non-retryable
------------------------------------------------

- ``NodeUserError``: every plugin raises this for user-correctable
  failures (see ``services/plugin/__init__.py``). Retrying just spends
  retry budget — the input won't be different next time. The plugin
  ``cls.retry_policy`` default already lists it as non-retryable
  (Wave 12 A2, ``services/plugin/scaling.py``), but the WORKFLOW-side
  ``workflow.execute_activity(..., retry_policy=...)`` override
  silently lost that contract when the workflow author hand-typed a
  fresh ``RetryPolicy(...)``. This module re-imposes it at the
  workflow callsite.

- ``InvalidEvent``: when ``services/events/dispatch.py:emit`` raises
  on a malformed envelope (missing ``id``, bad ``source``), the
  activity's input is structurally broken and retries can't fix it.

Keep this list short. Adding error types here is a runtime contract
change — every workflow that uses these policies inherits the
non-retryability. Use ``cls.retry_policy.non_retryable_error_types``
on a specific plugin class when only ONE plugin's failure should be
non-retryable.

Refs
----
- https://docs.temporal.io/encyclopedia/retry-policies
- https://python.temporal.io/temporalio.common.RetryPolicy.html
"""

from __future__ import annotations

from datetime import timedelta

from temporalio.common import RetryPolicy


# Error types that surface user mistakes / structural payload bugs —
# retrying makes no difference. Plugin classes that raise their own
# domain-specific non-retryable errors should override
# ``cls.retry_policy`` rather than expand this list.
NON_RETRYABLE_ERROR_TYPES: tuple[str, ...] = (
    "NodeUserError",
    "InvalidEvent",
    # A result that violates the plugin's own declared Output contract is
    # a plugin bug; retrying re-bills the work and fails identically.
    "OutputValidationError",
)


# Default activity policy for the orchestrator (MachinaWorkflow,
# AgentWorkflow, PollingTriggerWorkflow). 3 attempts with 1s→30s
# exponential backoff covers transient API blips (DNS, rate limits,
# brief upstream outages) without holding the whole graph hostage to a
# persistent failure.
DEFAULT_ACTIVITY_RETRY: RetryPolicy = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    non_retryable_error_types=list(NON_RETRYABLE_ERROR_TYPES),
)


# Tighter policy for activities that are themselves cheap operations
# (e.g. ``emit_event_activity``, ``broadcast_progress``). Failing fast
# here surfaces wiring bugs in tests + ops without retry-budget noise.
QUICK_ACTIVITY_RETRY: RetryPolicy = RetryPolicy(
    initial_interval=timedelta(milliseconds=250),
    maximum_interval=timedelta(seconds=5),
    maximum_attempts=2,
    non_retryable_error_types=list(NON_RETRYABLE_ERROR_TYPES),
)


# LLM-step policy: retry transient provider failures indefinitely with
# exponential backoff. A clean provider rejection (429 rate limit, 5xx,
# network refusal) bills nothing and MUST be retried — the previous
# one-shot policy (Wave 17.2) killed the whole run on the first 429 and
# then tripped the deployment circuit breaker. Terminal outcomes still
# fail fast: ``_as_temporal_llm_error`` marks non-retryable categories
# (invalid_request, authentication, ...) with ``non_retryable=True``,
# which overrides this policy, and provider ``retry_after`` hints ride
# ``ApplicationError.next_retry_delay``. Known trade-off, accepted: an
# AMBIGUOUS loss (worker crash / timeout where the call may have
# completed server-side) now also retries and can re-bill a prompt —
# resilience of long-running deployments wins over billing paranoia.
LLM_STEP_RETRY: RetryPolicy = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=0,  # unlimited — liveness comes from heartbeats
    non_retryable_error_types=list(NON_RETRYABLE_ERROR_TYPES),
)


# Terminal-cleanup policy for delegated-task teardown (persisting a
# cancellation, releasing a subagent permit). These run while the workflow
# is already unwinding, so giving up early leaks a permit or leaves a task
# row stuck in a non-terminal state — a durable, user-visible defect that a
# later retry genuinely can fix. Hence a longer budget than the 3-attempt
# default. Deliberately NOT carrying ``non_retryable_error_types``: the
# cleanup activities are idempotent by construction and a NodeUserError here
# means the cleanup itself is broken, which should still be retried while
# the worker is alive.
DELEGATION_CLEANUP_RETRY: RetryPolicy = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=10,
)


# Indefinite wait for a subagent-admission permit. The acquire activity
# is a heartbeating poll-until-admitted loop whose single attempt is
# bounded by its start_to_close timeout; with the previous 3-attempt
# default a delegation queued behind long-running siblings (or a paused
# deployment) for more than ~3h failed permanently instead of waiting.
# ``maximum_attempts=0`` is Temporal's "unlimited" — liveness stays
# guaranteed by the activity's heartbeat, and cancellation still
# propagates normally when the parent is cancelled.
PERMIT_WAIT_RETRY: RetryPolicy = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=0,
    non_retryable_error_types=list(NON_RETRYABLE_ERROR_TYPES),
)


__all__ = [
    "NON_RETRYABLE_ERROR_TYPES",
    "DEFAULT_ACTIVITY_RETRY",
    "QUICK_ACTIVITY_RETRY",
    "LLM_STEP_RETRY",
    "DELEGATION_CLEANUP_RETRY",
    "PERMIT_WAIT_RETRY",
]
