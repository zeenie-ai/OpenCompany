"""Simple in-process circuit breaker.

Phase 7.5c of the credentials-scaling plan. Generalizes to any upstream
call where repeated failures should stop cascading into the rest of the
system (OAuth refresh, third-party API calls, LLM provider endpoints).

Semantics
---------
A breaker has three states:

- **closed**   — normal operation; calls pass through to the wrapped
  coroutine. On each failure the breaker increments a per-key counter.
- **open**     — all calls short-circuit immediately with
  :class:`CircuitBreakerOpen`. Opens when the failure counter crosses
  ``failure_threshold`` within a ``failure_window`` rolling window.
- **half_open**— after ``cooldown_seconds``, the next call is allowed
  through as a probe. Success closes the breaker; failure reopens it
  and resets the cooldown.

Design decisions
----------------
- **Per-key**: each breaker instance is keyed on a free-form scope
  (e.g. ``"twitter"``, ``"google"``). One provider failing does not trip
  the breaker for another provider.
- **Process-local**: single-instance self-hosted tool. No Redis, no
  distributed consensus. At deployment scale, upgrade to Redis-backed
  counters.
- **Opt-in**: services call ``breaker.run(func)`` explicitly. Legacy
  code paths that don't opt in get the old behavior unchanged.
- **Exception caching**: when open, the breaker raises
  :class:`CircuitBreakerOpen` with the last recorded failure as
  ``__cause__``, so callers can introspect why it opened.

Usage
-----
    from services.circuit_breaker import get_circuit_breaker, CircuitBreakerOpen

    async def refresh_google_tokens() -> TokenPair:
        breaker = get_circuit_breaker("google_oauth_refresh")
        try:
            return await breaker.run(_actually_refresh_with_google)
        except CircuitBreakerOpen as e:
            logger.warning("google OAuth refresh circuit open: %s", e)
            raise

Tunables (defaults)
-------------------
- ``failure_threshold``: 3
- ``failure_window``:    60.0 seconds
- ``cooldown_seconds``:  30.0 seconds
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Dict, List, Optional, TypeVar

from core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(RuntimeError):
    """Raised when a call short-circuits because the breaker is open."""

    def __init__(self, scope: str, retry_after_seconds: float) -> None:
        super().__init__(f"circuit breaker {scope!r} is open; retry after ~{retry_after_seconds:.0f}s")
        self.scope = scope
        self.retry_after_seconds = retry_after_seconds


@dataclass
class _CircuitState:
    state: CircuitState = CircuitState.CLOSED
    failures: List[float] = field(default_factory=list)
    opened_at: Optional[float] = None
    last_exception: Optional[BaseException] = None


class CircuitBreaker:
    """One breaker instance scoped to a named upstream."""

    def __init__(
        self,
        scope: str,
        *,
        failure_threshold: int = 3,
        failure_window: float = 60.0,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self._scope = scope
        self._failure_threshold = failure_threshold
        self._failure_window = failure_window
        self._cooldown = cooldown_seconds
        self._state = _CircuitState()
        self._lock = asyncio.Lock()

    # ----- public API -----

    async def run(self, func: Callable[[], Awaitable[T]]) -> T:
        """Execute ``func`` under breaker supervision.

        - Closed or half-open: runs normally. Success resets the counter
          and closes the breaker. Failure increments the counter and may
          open the breaker.
        - Open: short-circuits immediately with
          :class:`CircuitBreakerOpen`, unless the cooldown has elapsed
          (in which case the breaker moves to half-open and runs the
          call as a probe).
        """
        async with self._lock:
            self._maybe_half_open(time.monotonic())
            if self._state.state is CircuitState.OPEN:
                retry_after = self._retry_after()
                exc = CircuitBreakerOpen(self._scope, retry_after)
                if self._state.last_exception is not None:
                    exc.__cause__ = self._state.last_exception
                raise exc

        try:
            result = await func()
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            async with self._lock:
                self._record_failure(time.monotonic(), exc)
            raise

        async with self._lock:
            self._record_success()
        return result

    def state(self) -> CircuitState:
        return self._state.state

    def stats(self) -> Dict[str, object]:
        now = time.monotonic()
        cutoff = now - self._failure_window
        recent_failures = sum(1 for t in self._state.failures if t >= cutoff)
        return {
            "scope": self._scope,
            "state": self._state.state.value,
            "failure_threshold": self._failure_threshold,
            "failure_window_seconds": self._failure_window,
            "cooldown_seconds": self._cooldown,
            "recent_failures": recent_failures,
            "opened_at": self._state.opened_at,
            "retry_after_seconds": self._retry_after() if self._state.state is CircuitState.OPEN else 0,
        }

    def reset(self) -> None:
        """Force-close the breaker. Tests or manual admin endpoint only."""
        self._state = _CircuitState()

    # ----- internals -----

    def _maybe_half_open(self, now: float) -> None:
        if self._state.state is not CircuitState.OPEN:
            return
        if self._state.opened_at is None:
            return
        if now - self._state.opened_at < self._cooldown:
            return
        # Cooldown elapsed → allow one probe call.
        logger.info("circuit_breaker[%s]: cooldown elapsed, moving to half_open", self._scope)
        self._state.state = CircuitState.HALF_OPEN

    def _record_failure(self, now: float, exc: BaseException) -> None:
        self._state.last_exception = exc
        # Prune failures outside the rolling window.
        cutoff = now - self._failure_window
        self._state.failures = [t for t in self._state.failures if t >= cutoff]
        self._state.failures.append(now)

        if self._state.state is CircuitState.HALF_OPEN:
            # Probe failed → reopen immediately and restart cooldown.
            logger.warning(
                "circuit_breaker[%s]: half_open probe failed, reopening: %s",
                self._scope,
                type(exc).__name__,
            )
            self._state.state = CircuitState.OPEN
            self._state.opened_at = now
            return

        if len(self._state.failures) >= self._failure_threshold:
            logger.warning(
                "circuit_breaker[%s]: threshold reached (%d/%d failures in %ds), opening",
                self._scope,
                len(self._state.failures),
                self._failure_threshold,
                int(self._failure_window),
            )
            self._state.state = CircuitState.OPEN
            self._state.opened_at = now

    def _record_success(self) -> None:
        if self._state.state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
            logger.info("circuit_breaker[%s]: closed after successful call", self._scope)
        self._state.state = CircuitState.CLOSED
        self._state.failures.clear()
        self._state.opened_at = None
        self._state.last_exception = None

    def _retry_after(self) -> float:
        if self._state.opened_at is None:
            return 0.0
        elapsed = time.monotonic() - self._state.opened_at
        return max(0.0, self._cooldown - elapsed)


# ----- module-level registry -----

_BREAKERS: Dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    scope: str,
    *,
    failure_threshold: int = 3,
    failure_window: float = 60.0,
    cooldown_seconds: float = 30.0,
) -> CircuitBreaker:
    """Return (creating on first call) the shared breaker for ``scope``.

    Tunable parameters only apply on first creation; subsequent calls
    with the same scope return the existing instance and ignore new
    params. This keeps scope-to-breaker identity stable across a
    process lifetime.
    """
    breaker = _BREAKERS.get(scope)
    if breaker is None:
        breaker = CircuitBreaker(
            scope,
            failure_threshold=failure_threshold,
            failure_window=failure_window,
            cooldown_seconds=cooldown_seconds,
        )
        _BREAKERS[scope] = breaker
    return breaker


def all_breaker_stats() -> List[Dict[str, object]]:
    """Return stats for every known breaker (observability endpoint)."""
    return [b.stats() for b in _BREAKERS.values()]
