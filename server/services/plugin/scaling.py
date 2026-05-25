"""Temporal-aligned scaling knobs shared by every plugin node.

Each :class:`BaseNode` subclass declares ``task_queue`` +
``retry_policy`` + ``start_to_close_timeout`` + ``heartbeat_timeout`` as
class attributes. The Temporal dispatcher (11.F) reads these when
spawning activities; the in-process executor ignores them. Defaults
here keep new nodes safe without any explicit configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Sequence


class TaskQueue:
    """Named worker-pool queues. Workers subscribe to one or more; every
    plugin declares which queue runs it. New queues = add a string here
    and provision a worker pool in ``services/temporal/worker.py``.
    """

    DEFAULT = "machina-default"  # catch-all
    REST_API = "rest-api"  # lightweight HTTP calls (gmail, brave, twitter)
    AI_HEAVY = "ai-heavy"  # long-running LLM + agent loops
    CODE_EXEC = "code-exec"  # python/js/ts sandboxed execution
    TRIGGERS_POLL = "triggers-poll"  # polling triggers (long-lived)
    TRIGGERS_EVENT = "triggers-event"  # event-waiter triggers (long-lived)
    ANDROID = "android"  # ADB / relay ops
    BROWSER = "browser"  # agent-browser, Playwright
    MESSAGING = "messaging"  # whatsapp / telegram / twitter send

    ALL = frozenset(
        {
            DEFAULT,
            REST_API,
            AI_HEAVY,
            CODE_EXEC,
            TRIGGERS_POLL,
            TRIGGERS_EVENT,
            ANDROID,
            BROWSER,
            MESSAGING,
        }
    )


@dataclass(frozen=True)
class RetryPolicy:
    """Mirrors ``temporalio.common.RetryPolicy`` so plugins can declare
    it without importing Temporal at class-definition time (the Temporal
    SDK is a runtime-only dependency of the worker).
    """

    initial_interval: timedelta = timedelta(seconds=1)
    backoff_coefficient: float = 2.0
    maximum_interval: timedelta = timedelta(seconds=60)
    maximum_attempts: int = 3
    non_retryable_error_types: Sequence[str] = field(
        default_factory=lambda: (
            "ValidationError",
            "PermissionDeniedError",
            "InvalidParametersError",
            # Wave 12 A2: NodeUserError is the canonical "user-correctable
            # failure" exception (missing input, missing credential, API
            # rejected the payload). Retrying it just burns attempts since
            # the underlying input won't fix itself. Plugins that raise
            # NodeUserError fail fast; the framework surfaces the message
            # to the user / agent loop without traceback noise.
            "NodeUserError",
        )
    )

    def to_temporal(self):
        """Lazy import — only called inside the Temporal worker."""
        from temporalio.common import RetryPolicy as _RP

        return _RP(
            initial_interval=self.initial_interval,
            backoff_coefficient=self.backoff_coefficient,
            maximum_interval=self.maximum_interval,
            maximum_attempts=self.maximum_attempts,
            non_retryable_error_types=list(self.non_retryable_error_types),
        )


# Sensible kind-level defaults. Overridden by subclass attributes.
DEFAULT_START_TO_CLOSE = timedelta(minutes=10)
DEFAULT_HEARTBEAT = timedelta(minutes=2)
DEFAULT_RETRY = RetryPolicy()

# Kind-specific defaults — picked up when a subclass doesn't override.
ACTION_START_TO_CLOSE = timedelta(minutes=10)
AI_START_TO_CLOSE = timedelta(minutes=30)
TRIGGER_START_TO_CLOSE = timedelta(hours=24)  # long-lived
CODE_START_TO_CLOSE = timedelta(minutes=5)
