"""TriggerNode — long-lived node that waits for external events.

Two lifecycle modes:

- **Event-based** (webhookTrigger, chatTrigger, whatsappReceive,
  telegramReceive) — registers an :mod:`services.event_waiter` Future
  with a filter function built from the node's params.
- **Polling-based** (gmailReceive, emailReceive, twitterReceive) —
  implements :meth:`poll_once` which the executor invokes on a schedule
  (via :class:`TriggerManager` in deployment mode).

Both modes share the TriggerNode contract: the node's single "run"
returns once an event arrives; the returned shape mirrors the event
data. In deployment mode, each arrival spawns a fresh workflow run.

Plugins choose by setting :attr:`mode`:

- ``"event"`` — provide :meth:`build_filter` and :attr:`event_type`
- ``"polling"`` — provide :meth:`poll_once` and :attr:`poll_interval`
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, ClassVar, Dict, Literal, Optional

from pydantic import BaseModel

from core.logging import get_logger
from services.plugin.base import BaseNode
from services.plugin.context import NodeContext
from services.plugin.scaling import TaskQueue, TRIGGER_START_TO_CLOSE

logger = get_logger(__name__)


class TriggerNode(BaseNode, abstract=True):
    """Base class for workflow trigger nodes."""

    component_kind: ClassVar[str] = "trigger"
    task_queue: ClassVar[str] = TaskQueue.TRIGGERS_EVENT
    start_to_close_timeout = TRIGGER_START_TO_CLOSE

    mode: ClassVar[Literal["event", "polling"]] = "event"
    # Event mode: dispatch key in services.event_waiter.TRIGGER_REGISTRY
    event_type: ClassVar[str] = ""
    # Polling mode: default interval seconds; overridden by params.
    default_poll_interval: ClassVar[int] = 60

    # ---- subclass hooks ---------------------------------------------------

    def build_filter(self, params: BaseModel) -> Callable[[Dict[str, Any]], bool]:
        """Event mode — return a callable that accepts an event dict
        and returns True if this trigger should fire. Default: match all.
        """
        return lambda _event: True

    async def poll_once(
        self,
        ctx: NodeContext,
        params: BaseModel,
        state: Dict[str, Any],
    ) -> Awaitable[Optional[Dict[str, Any]]]:
        """Polling mode — check for new events. Return event data dict
        or None if nothing new. ``state`` persists across polls for
        tracking last-seen IDs.
        """
        raise NotImplementedError("Polling triggers must override poll_once")

    # ---- lifecycle --------------------------------------------------------

    async def execute(
        self,
        node_id: str,
        parameters: Dict[str, Any],
        context: NodeContext,
    ) -> Dict[str, Any]:
        """Run-node execution for triggers — registers a one-shot wait
        and returns when the event fires. Used by the "Run" button.

        Deployment-mode long-running watches are driven by
        :class:`TriggerManager`, not this method.
        """
        import time
        from services import event_waiter

        start_time = time.time()

        try:
            params_obj = self._validate_params(parameters)
        except Exception as e:
            return self._wrap_error(
                start_time=start_time,
                error=f"Invalid parameters: {e}",
                error_type="ValidationError",
                exc=e,
            )

        if self.mode == "event":
            # Pre-flight: refuse to register a waiter if event_waiter
            # doesn't recognise this trigger type. Pre-refactor handler
            # contract — keeps Run-button feedback honest when the trigger
            # config registry is missing the event_type entry.
            if event_waiter.get_trigger_config(self.type) is None:
                return self._wrap_error(
                    start_time=start_time,
                    error=f"Unknown trigger type: {self.type}",
                    error_type="NodeUserError",
                )
            waiter = await event_waiter.register(
                node_type=self.type,
                node_id=node_id,
                params=parameters,
            )
            try:
                event_data = await waiter.future
            except Exception as e:
                return self._wrap_error(
                    start_time=start_time,
                    error=str(e),
                    error_type=type(e).__name__,
                    exc=e,
                )
            return self._wrap_success(start_time=start_time, result=event_data)

        # Polling mode: one iteration (deployment mode drives loops).
        state: Dict[str, Any] = {}
        result = await self.poll_once(context, params_obj, state)  # type: ignore[arg-type]
        if result is None:
            return self._wrap_success(start_time=start_time, result={"no_event": True})
        return self._wrap_success(start_time=start_time, result=result)
