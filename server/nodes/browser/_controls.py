"""Bounded browser action budgets, independent of permission policy.

An admitted action is not necessarily permitted: the caller must still apply
the saved node policy and the profile lease. This ledger limits attempts sent
to a site; it neither changes browser fingerprints nor retries actions.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


_OBSERVATIONS = frozenset({"snapshot", "page_text", "page_info", "screenshot", "diagnose", "webmcp_list", "wait", "credential_bindings"})
_SITE_ACTIONS = frozenset({"navigate", "click", "hover", "type", "press", "select", "scroll", "back", "forward", "reload", "webmcp_call", "evaluate", "run_python", "credential_fill"})
_WINDOW_SECONDS = 60.0


def is_retry_safe(op: str, tab_action: str = "list") -> bool:
    """Only explicit observations can be replayed after an uncertain failure."""
    return op in _OBSERVATIONS or (op == "tabs" and tab_action == "list")


def is_site_action(op: str, tab_action: str = "list") -> bool:
    """Whether this operation consumes a site's action budget."""
    return op in _SITE_ACTIONS or (op == "tabs" and tab_action != "list")


@dataclass(frozen=True)
class GuardDecision:
    error_type: str
    error: str
    retry_after: float
    next_action: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Action:
    at: float
    profile: str
    fingerprint: str


@dataclass
class _Bucket:
    actions: deque[_Action] = field(default_factory=deque)
    last_admitted_at: Optional[float] = None
    expires_at: float = 0.0


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rate_limited(error: str, retry_after: float) -> GuardDecision:
    return GuardDecision("rate_limited", error, max(0.0, retry_after), "wait")


class ActionGuard:
    """In-process, shared-per-origin rolling budgets with no raw action data.

    Call synchronously on the runtime's event loop so admission and recording
    are atomic with respect to other browser operations. ``origin`` must be
    the caller's canonical origin, not a full navigation URL. Only its digest
    is retained. Entries expire after both the rolling window and minimum
    interval have elapsed. Active entries are never evicted to reset a quota.
    """

    def __init__(self, *, max_origins: int = 256, max_records_per_origin: int = 1024) -> None:
        self._max_origins = max(1, int(max_origins))
        self._max_records_per_origin = max(1, int(max_records_per_origin))
        self._buckets: Dict[str, _Bucket] = {}

    def admit(
        self,
        *,
        origin: str,
        profile_id: str,
        operation: str,
        arguments: dict,
        min_interval_ms: int = 1000,
        max_actions_per_minute: int = 30,
        max_repeat_actions: int = 3,
        now: Optional[float] = None,
    ) -> Optional[GuardDecision]:
        """Record an admitted site action, or explain why it must wait.

        Observations and non-site operations do not consume a budget.
        Rejected attempts never change an action count or extend a cooldown.
        ``retry_after`` is measured in seconds; ``now`` enables clock-based
        tests without sleeping.
        """
        if not is_site_action(operation, str(arguments.get("tab_action") or "list")):
            return None
        at = time.monotonic() if now is None else float(now)
        if not math.isfinite(at):
            raise ValueError("Browser action time must be finite")
        interval = max(0, min_interval_ms) / 1000.0
        quota = max(1, int(max_actions_per_minute))
        repeat_limit = max(1, int(max_repeat_actions))

        for key, existing in list(self._buckets.items()):
            if existing.expires_at <= at:
                del self._buckets[key]

        origin_key = _digest(origin)
        bucket = self._buckets.get(origin_key)
        if bucket is None:
            if len(self._buckets) >= self._max_origins:
                retry_after = min(item.expires_at for item in self._buckets.values()) - at
                return _rate_limited("Browser action capacity is busy. Wait before opening another site.", retry_after)
            bucket = _Bucket()

        while bucket.actions and bucket.actions[0].at <= at - _WINDOW_SECONDS:
            bucket.actions.popleft()
        if bucket.last_admitted_at is not None:
            remaining = bucket.last_admitted_at + interval - at
            if remaining > 0:
                return _rate_limited("Browser actions for this site are too close together. Wait before the next action.", remaining)

        effective_quota = min(quota, self._max_records_per_origin)
        if len(bucket.actions) >= effective_quota:
            # If policy tightens mid-window, enough older admissions must
            # expire before another action fits the new budget.
            expires = bucket.actions[len(bucket.actions) - effective_quota].at + _WINDOW_SECONDS
            return _rate_limited("The browser action limit for this site has been reached. Wait before continuing.", expires - at)

        profile_key = _digest(profile_id)
        fingerprint = _digest(json.dumps([operation, arguments], sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False))
        matches = [event for event in bucket.actions if event.profile == profile_key and event.fingerprint == fingerprint]
        if len(matches) >= repeat_limit:
            expires = matches[len(matches) - repeat_limit].at + _WINDOW_SECONDS
            return GuardDecision(
                "repeat_limit",
                "This browser action has already been repeated. Inspect the page or request user help before trying again.",
                max(0.0, expires - at),
                "inspect_page",
            )

        bucket.actions.append(_Action(at, profile_key, fingerprint))
        bucket.last_admitted_at = at
        bucket.expires_at = at + max(_WINDOW_SECONDS, interval)
        self._buckets[origin_key] = bucket
        return None


__all__ = ["ActionGuard", "GuardDecision", "is_retry_safe", "is_site_action"]
