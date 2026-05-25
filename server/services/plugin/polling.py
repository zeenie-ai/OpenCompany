"""PollingTriggerNode -- template-method polling-trigger base class.

Wave 11.I, milestone L. Subclass owns the four divergence points
(``setup_service``, ``fetch_ids``, ``fetch_detail``, optional
``post_emit``); the base owns the loop body, the seen-id baseline,
the cancellation surface, and per-cycle error isolation.

Auto-registers a poll-coroutine factory in
:mod:`services.deployment.poll_registry` so
``DeploymentManager._setup_event_trigger`` looks the plugin up by
node type instead of branching on a hardcoded list.

Design choice (over lift-and-shift): the gmail and email pollers in
``services/deployment/manager.py`` had identical structure -- 80 LOC
vs 37 LOC, only the four seams differed. Pulling the loop into a
mixin collapses ~120 LOC of duplicated polling control flow into one
~40 LOC body, plus ~15 LOC per concrete poller.

Cancellation contract
---------------------
Mirrors the existing ``setup_polling_trigger`` consumer in
``services/deployment/triggers.py``:

- The deployment manager passes ``is_running_fn``; the loop checks
  it before every sleep and after wake.
- ``asyncio.CancelledError`` is re-raised, never swallowed (per
  https://docs.python.org/3/library/asyncio-task.html#task-cancellation).

Per-cycle error isolation: a transient ``Exception`` from
``fetch_ids`` or ``fetch_detail`` logs at ERROR and the loop sleeps
to the next interval. Permanent errors (auth revoked, account
deleted) currently keep retrying indefinitely with the same log
line; ``tenacity``-backed classification is a follow-up extension
point documented inline.

Out of scope (commit-stage)
---------------------------
- ``WorkflowEvent.message(...)`` envelope wrapping: ``setup_polling_trigger``
  consumes raw dicts today; envelope adoption is the U milestone test pass.
- Persistent watermark cursor: ``seen_ids`` is in-memory and resets
  on restart. Backlog dropped on restart -- existing behaviour.
- Twitter polling: ``twitterReceive`` declares ``mode="polling"`` but
  has no concrete subclass. Subclassing this base is the conversion path.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, ClassVar, Dict, Set, Tuple

from core.logging import get_logger
from services.plugin.trigger import TriggerNode

logger = get_logger(__name__)


class PollingTriggerNode(TriggerNode, abstract=True):
    """Polling trigger with a unified loop body.

    Subclass MUST override:

    - :meth:`setup_service` -- build the auth client / SDK handle.
    - :meth:`fetch_ids` -- return the current visible IDs (set[str]).
    - :meth:`fetch_detail` -- return the event payload for one ID.

    Subclass MAY override:

    - :meth:`post_emit` -- side effect after enqueue (e.g. mark-as-read).
    - :attr:`poll_interval_clamp` -- (min, max) seconds for the
      ``poll_interval`` param. Default (10, 3600).
    - :attr:`type_alias` -- a second registration key for plugins
      that want to be reachable by a legacy type name without
      changing the plugin's primary ``type``. Currently unused after
      Wave 11.I milestone P retired the gmail alias; kept on the base
      class as a documented escape hatch for future renames.
    """

    mode: ClassVar[str] = "polling"

    # (min_seconds, max_seconds) for the user-supplied poll_interval.
    poll_interval_clamp: ClassVar[Tuple[int, int]] = (10, 3600)

    # Optional secondary registration key. Registers under both
    # ``cls.type`` and ``cls.type_alias`` for plugins with a legacy
    # alias.
    type_alias: ClassVar[str] = ""

    # ---- subclass hooks ------------------------------------------------

    async def setup_service(self, params: Dict[str, Any]) -> Any:
        """Build the auth client / service handle that ``fetch_ids`` and
        ``fetch_detail`` will close over.

        Plugins that don't need a long-lived handle can return any
        opaque value (or the ``params`` dict itself). The returned
        value is passed unchanged to the other hooks.
        """
        raise NotImplementedError

    async def fetch_ids(self, service: Any, params: Dict[str, Any]) -> Set[str]:
        """Return the current set of visible IDs for one poll cycle.

        Called once for the baseline pass at loop start, then once per
        ``poll_interval`` thereafter. The diff against the previous
        ``seen`` set is the source of truth for "what's new".
        """
        raise NotImplementedError

    async def fetch_detail(self, service: Any, msg_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch the full event payload for one ID.

        Called once per new ID per cycle. The returned dict goes
        directly onto the deployment manager's queue (no envelope
        wrapping in this commit -- see milestone U / module docstring).
        """
        raise NotImplementedError

    async def post_emit(self, service: Any, msg_id: str, params: Dict[str, Any]) -> None:
        """Optional side effect AFTER the payload was enqueued.

        Default no-op. Override for "mark-as-read" or similar
        post-processing that should not block the emit and whose
        failure shouldn't kill the loop.
        """
        return None

    # ---- registration --------------------------------------------------

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        super().__init_subclass__(abstract=abstract, **kwargs)
        if abstract or not cls.type:
            return

        # Auto-register a poll-coroutine factory for the deployment
        # manager (the in-process Run-button path uses :meth:`execute`
        # which we override below). Idempotent on re-import via
        # IdempotentRegistry.
        from services.deployment.poll_registry import (
            register_poll_coroutine_factory,
        )

        def factory(node_id: str, params: Dict[str, Any]) -> Callable:
            instance = cls()
            return instance._build_poll_coroutine(node_id, params)

        register_poll_coroutine_factory(cls.type, factory)
        if cls.type_alias:
            # Same factory under the legacy alias so consumers that
            # key by the alias (manager.py POLLING_TRIGGER_TYPES) still
            # find the factory until the rename lands.
            register_poll_coroutine_factory(cls.type_alias, factory)

    # ---- shared loop ---------------------------------------------------

    def _clamp_interval(self, raw: Any) -> int:
        lo, hi = self.poll_interval_clamp
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = self.default_poll_interval
        return max(lo, min(hi, value))

    # ---- Wave 12 C2: per-cycle Temporal activity --------------------------
    #
    # The asyncio coroutine version above feeds the legacy
    # ``setup_polling_trigger`` collector/processor task pair (dies on
    # FastAPI restart). The Temporal-durable version below — yielded by
    # :meth:`as_poll_activity` — does ONE cycle and returns events,
    # called per ``workflow.sleep`` tick in
    # :class:`services.temporal.polling_trigger_workflow.PollingTriggerWorkflow`.
    # The seen-id baseline survives via workflow state (carried across
    # ``continueAsNew``) rather than living inside the asyncio task.

    @classmethod
    def as_poll_activity(cls):
        """Return a ``@activity.defn`` wrapping one poll cycle.

        Stable activity name: ``poll.{type}.v{version}``.

        Activity payload (in)::

            {
                "node_id": str,
                "params": Dict[str, Any],
                "seen_ids": List[str],   # provider-side IDs from prior cycle
                "baseline_only": bool,    # True on the first call after start
            }

        Activity result (out)::

            {
                "events": List[Dict],     # NEW event payloads this cycle
                "seen_ids": List[str],    # union of prior seen + current
            }

        Determinism: every operation runs inside the activity (network
        I/O, mutable state) — the workflow body only sees the serialised
        result so replay stays deterministic. Per-cycle errors raise
        out of the activity; the workflow's ``RetryPolicy`` / try-except
        owns retry semantics.
        """
        from temporalio import activity

        activity_name = f"poll.{cls.type}.v{cls.version}"

        @activity.defn(name=activity_name)
        async def _poll_cycle_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
            node_id = payload.get("node_id", "")
            params = payload.get("params", {}) or {}
            prior_seen: Set[str] = set(payload.get("seen_ids") or [])
            baseline_only = bool(payload.get("baseline_only"))

            instance = cls()
            try:
                service = await instance.setup_service(params)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Polling activity setup failed",
                    node_id=node_id,
                    node_type=cls.type,
                    error=str(exc),
                )
                raise

            current = await instance.fetch_ids(service, params)

            if baseline_only:
                # First call after workflow start: establish the seen
                # baseline without emitting anything. Matches the
                # pre-Temporal collector's baseline pass.
                return {"events": [], "seen_ids": list(current)}

            new_ids = current - prior_seen
            events: list[Dict[str, Any]] = []
            for msg_id in new_ids:
                try:
                    detail = await instance.fetch_detail(service, msg_id, params)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Polling activity fetch_detail failed; skipping id",
                        node_id=node_id,
                        node_type=cls.type,
                        msg_id=msg_id,
                        error=str(exc),
                    )
                    continue
                # The workflow needs a stable event.id for cross-cycle
                # dedup. Fall back to the provider id when fetch_detail
                # doesn't supply one.
                if "id" not in detail:
                    detail["id"] = msg_id
                events.append(detail)
                # post_emit side effect (e.g. mark-as-read). Failures
                # MUST NOT block the next emit or kill the cycle —
                # mirror the legacy coroutine semantics.
                try:
                    await instance.post_emit(service, msg_id, params)
                except Exception:
                    pass

            # OOM fix: bound ``seen_ids`` to what the provider currently
            # reports as visible — old IDs that have dropped off the
            # provider's window (archived / deleted / aged out) fall
            # out of seen too. Pre-fix this was ``prior_seen | current``
            # which grew forever (every poll appended new IDs; nothing
            # ever got evicted). At Gmail's ~100 msgs/day cadence the
            # set hit ~36K entries in a year — ~1.4MB just for IDs,
            # serialised through Temporal payload on every cycle. Now
            # bounded by the provider's natural window size.
            return {"events": events, "seen_ids": list(current)}

        return _poll_cycle_activity

    def _build_poll_coroutine(self, node_id: str, params: Dict[str, Any]) -> Callable[[asyncio.Queue, Callable[[], bool]], Any]:
        """Return the bound poll coroutine the deployment manager
        consumes. Closes over ``self``, ``node_id``, and ``params``.
        """

        async def poll(queue: asyncio.Queue, is_running_fn: Callable[[], bool]) -> None:
            try:
                service = await self.setup_service(params)
            except Exception as exc:  # noqa: BLE001 -- single setup failure
                logger.error(
                    "Polling trigger setup failed",
                    node_id=node_id,
                    node_type=self.type,
                    error=str(exc),
                )
                return

            interval = self._clamp_interval(params.get("poll_interval"))

            # Baseline -- avoid re-emitting items the user has had since
            # before the deployment was live. On baseline failure, fall
            # through with an empty set: the next cycle will emit
            # everything currently visible (matches pre-migration gmail
            # behaviour at line 728-736 of services/deployment/manager.py).
            seen: Set[str] = set()
            try:
                seen = await self.fetch_ids(service, params)
                logger.info(
                    "Polling trigger baseline established",
                    node_id=node_id,
                    node_type=self.type,
                    seen=len(seen),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Polling trigger baseline failed; treating all as new",
                    node_id=node_id,
                    node_type=self.type,
                    error=str(exc),
                )

            cycle = 0
            while is_running_fn():
                await asyncio.sleep(interval)
                if not is_running_fn():
                    break
                cycle += 1
                try:
                    current = await self.fetch_ids(service, params)
                    new_ids = current - seen
                    if not new_ids:
                        # OOM fix: even when nothing new arrives we
                        # still rebase ``seen`` to ``current`` so old
                        # IDs that dropped off the provider's window
                        # don't linger forever.
                        seen = set(current)
                        continue
                    logger.debug(
                        "Polling trigger cycle",
                        node_id=node_id,
                        node_type=self.type,
                        cycle=cycle,
                        current=len(current),
                        seen=len(seen),
                        new=len(new_ids),
                    )
                    for msg_id in new_ids:
                        payload = await self.fetch_detail(service, msg_id, params)
                        await queue.put(payload)
                        # post_emit failure must NOT block the next emit
                        # nor kill the loop; mirrors pre-migration
                        # try/except at gmail :760-763 / email :799-806.
                        try:
                            await self.post_emit(service, msg_id, params)
                        except Exception:
                            pass
                    # OOM fix: rebase ``seen`` to the provider's current
                    # window. Pre-fix this added every new ID to ``seen``
                    # forever (``seen.add(msg_id)`` inside the loop, no
                    # eviction). Long-running pollers (Gmail with the
                    # default 60s interval) accumulated tens of thousands
                    # of entries over weeks/months. Now bounded by the
                    # provider's natural window size — items that have
                    # been emitted are still in ``current`` until they
                    # age/archive out, at which point dropping them is
                    # correct (the provider will never re-surface them
                    # under the same filter).
                    seen = set(current)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 -- per-cycle isolation
                    logger.error(
                        "Polling trigger cycle error; retrying next interval",
                        node_id=node_id,
                        node_type=self.type,
                        error=str(exc),
                    )

        return poll
