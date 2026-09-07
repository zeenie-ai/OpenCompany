"""Retryability classification for plugin failures.

Temporal-free on purpose: this module is imported by ``BaseNode`` and
``NodeExecutor`` on every execution path, including the canvas Run
button and the in-process agent loop, neither of which has the Temporal
SDK in play. The Temporal activity boundary
(``services/temporal/_failures.py``) reads the verdict stamped here and
turns it into a typed ``ApplicationError``.

Why classify at the source instead of by ``error_type`` name alone
------------------------------------------------------------------

The failure envelope carries only a string ``error_type``. Two failures
with the same name can deserve opposite treatment: an
``httpx.HTTPStatusError`` for a 404 is permanent, one for a 503 is
transient; a ``NodeUserError`` raised by ``services/llm/unifier.py``
from a rate-limited provider wraps an ``LLMError(retryable=True)`` and
should retry even though ``NodeUserError`` itself is non-retryable. Only
the raising site still has the exception object, so the verdict is
computed there and travels with the envelope as ``retryable``.

Rules, in precedence order (docs.temporal.io/encyclopedia/retry-policies:
"surface permanent failures, retry transient ones"):

1. An explicit boolean ``retryable`` attribute on the exception or on
   its ``__cause__`` chain wins (``LLMError.retryable``).
2. ``error_type`` in :data:`NON_RETRYABLE_ENVELOPE_TYPES` is permanent.
3. ``httpx.HTTPStatusError``: 5xx and 408 / 425 / 429 are transient,
   every other 4xx is permanent.
4. ``httpx.TransportError`` (timeouts, connection refused, DNS) is
   transient.
5. Anything else is transient -- an unknown exception is the exact case
   a retry exists for.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Iterator, Optional

# Envelope ``error_type`` values that describe permanent failures: the
# input, the credential, the operation name, or the plugin's own output
# contract is wrong, and none of that changes between attempts. Mirrors
# ``services/plugin/scaling.RetryPolicy.non_retryable_error_types`` and
# ``services/temporal/_retry_policies.NON_RETRYABLE_ERROR_TYPES``; the
# two Temporal-side lists are the second layer the server enforces by
# name, this set is what the source-side verdict starts from.
NON_RETRYABLE_ENVELOPE_TYPES: frozenset[str] = frozenset(
    {
        "NodeUserError",
        "ValidationError",
        "PermissionDeniedError",
        "InvalidParametersError",
        "OutputValidationError",
        "Cancelled",
    }
)

# 4xx statuses that are transient by definition: request timeout, too
# early, and rate limited. Every other 4xx says the request itself is
# wrong and will be wrong again.
RETRYABLE_HTTP_4XX: frozenset[int] = frozenset({408, 425, 429})

# Depth guard for the ``__cause__`` walk. Real chains are two or three
# deep; the cap only exists so a pathological cycle cannot hang a node.
_MAX_CAUSE_DEPTH = 5


def _cause_chain(exc: Optional[BaseException]) -> Iterator[BaseException]:
    """Yield ``exc`` and its explicit ``__cause__`` ancestors.

    Only ``raise ... from`` chaining is followed. ``__context__`` is
    deliberately ignored: it records whatever exception happened to be
    in flight in an ``except`` block, which is unrelated to what the
    plugin meant to report.
    """
    seen: set[int] = set()
    depth = 0
    current = exc
    while current is not None and depth < _MAX_CAUSE_DEPTH and id(current) not in seen:
        yield current
        seen.add(id(current))
        depth += 1
        current = current.__cause__


def explicit_retryable(exc: Optional[BaseException]) -> Optional[bool]:
    """Return the first boolean ``retryable`` attribute on the cause chain.

    ``None`` when no exception in the chain declares one.
    """
    for current in _cause_chain(exc):
        value = getattr(current, "retryable", None)
        if isinstance(value, bool):
            return value
    return None


def retry_after_of(exc: Optional[BaseException]) -> Optional[timedelta]:
    """Return a provider ``retry_after`` hint found on the cause chain.

    Accepts a ``timedelta`` or a positive number of seconds. The activity
    boundary forwards it as ``ApplicationError.next_retry_delay`` so a
    rate-limited call waits as long as the provider asked instead of the
    policy's fixed backoff.
    """
    for current in _cause_chain(exc):
        value = getattr(current, "retry_after", None)
        if isinstance(value, timedelta):
            return value if value.total_seconds() > 0 else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return timedelta(seconds=float(value))
    return None


def _httpx_module():
    try:
        import httpx  # noqa: WPS433 -- optional at import time

        return httpx
    except ImportError:  # pragma: no cover -- httpx is a hard dependency today
        return None


def classify_retryable(exc: Optional[BaseException], *, error_type: str) -> bool:
    """Decide whether a plugin failure is worth re-running.

    ``exc`` may be ``None`` when only the envelope is available (a
    handler that bypassed ``BaseNode._execute_body``); the verdict then
    rests on ``error_type`` alone.
    """
    explicit = explicit_retryable(exc)
    if explicit is not None:
        return explicit

    if error_type in NON_RETRYABLE_ENVELOPE_TYPES:
        return False

    httpx = _httpx_module()
    if httpx is not None and exc is not None:
        for current in _cause_chain(exc):
            if isinstance(current, httpx.HTTPStatusError):
                status = current.response.status_code
                return status >= 500 or status in RETRYABLE_HTTP_4XX
            if isinstance(current, httpx.TransportError):
                return True

    return True


__all__ = [
    "NON_RETRYABLE_ENVELOPE_TYPES",
    "RETRYABLE_HTTP_4XX",
    "classify_retryable",
    "explicit_retryable",
    "retry_after_of",
]
