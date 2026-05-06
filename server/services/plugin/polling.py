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
from typing import Any, Callable, ClassVar, Dict, Optional, Set, Tuple

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
      changing the plugin's primary ``type``. Today's only use case
      is gmail (``type = "googleGmailReceive"`` but downstream
      callers key by ``"gmailReceive"``).
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

    async def fetch_ids(
        self, service: Any, params: Dict[str, Any]
    ) -> Set[str]:
        """Return the current set of visible IDs for one poll cycle.

        Called once for the baseline pass at loop start, then once per
        ``poll_interval`` thereafter. The diff against the previous
        ``seen`` set is the source of truth for "what's new".
        """
        raise NotImplementedError

    async def fetch_detail(
        self, service: Any, msg_id: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Fetch the full event payload for one ID.

        Called once per new ID per cycle. The returned dict goes
        directly onto the deployment manager's queue (no envelope
        wrapping in this commit -- see milestone U / module docstring).
        """
        raise NotImplementedError

    async def post_emit(
        self, service: Any, msg_id: str, params: Dict[str, Any]
    ) -> None:
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

    def _build_poll_coroutine(
        self, node_id: str, params: Dict[str, Any]
    ) -> Callable[[asyncio.Queue, Callable[[], bool]], Any]:
        """Return the bound poll coroutine the deployment manager
        consumes. Closes over ``self``, ``node_id``, and ``params``.
        """

        async def poll(
            queue: asyncio.Queue, is_running_fn: Callable[[], bool]
        ) -> None:
            try:
                service = await self.setup_service(params)
            except Exception as exc:  # noqa: BLE001 -- single setup failure
                logger.error(
                    "Polling trigger setup failed",
                    node_id=node_id, node_type=self.type, error=str(exc),
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
                    node_id=node_id, node_type=self.type, seen=len(seen),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Polling trigger baseline failed; treating all as new",
                    node_id=node_id, node_type=self.type, error=str(exc),
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
                        continue
                    logger.debug(
                        "Polling trigger cycle",
                        node_id=node_id, node_type=self.type, cycle=cycle,
                        current=len(current), seen=len(seen),
                        new=len(new_ids),
                    )
                    for msg_id in new_ids:
                        seen.add(msg_id)
                        payload = await self.fetch_detail(
                            service, msg_id, params
                        )
                        await queue.put(payload)
                        # post_emit failure must NOT block the next emit
                        # nor kill the loop; mirrors pre-migration
                        # try/except at gmail :760-763 / email :799-806.
                        try:
                            await self.post_emit(service, msg_id, params)
                        except Exception:
                            pass
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 -- per-cycle isolation
                    logger.error(
                        "Polling trigger cycle error; retrying next interval",
                        node_id=node_id, node_type=self.type,
                        error=str(exc),
                    )

        return poll
