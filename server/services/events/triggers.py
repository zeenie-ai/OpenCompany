"""Generic TriggerNode base classes backed by EventSources.

Plugins subclass :class:`WebhookTriggerNode` and declare:

    type, display_name, group, ...      (standard plugin metadata)
    webhook_source: ClassVar[Type[WebhookSource]]
    Params, Output                      (Pydantic models)
    shape_output(event)  (optional)     reshape WorkflowEvent -> Output dict
    _check_precondition() (optional)    return error str if not ready
    _extra_filter(params) (optional)    additional filter on top of event_type

The base provides:
    - event_type derived from webhook_source.type
    - build_filter combining CloudEvents type-glob + _extra_filter
    - execute() with precheck + shape passthrough
    - the standard Operation("wait") stub
"""

from __future__ import annotations

import time
from typing import Any, Callable, ClassVar, Dict, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, Operation, TaskQueue, TriggerNode

from .envelope import WorkflowEvent
from .webhook import WebhookSource


class BaseTriggerParams(BaseModel):
    """Default Params for WebhookTriggerNode subclasses. Subclass and add
    provider-specific filters."""

    event_type_filter: str = Field(
        default="all",
        description=("Event type to match. 'all' for every event, exact name, " "or wildcard prefix (e.g. 'foo.*')."),
    )

    model_config = ConfigDict(extra="ignore")


class WebhookTriggerNode(TriggerNode):
    """TriggerNode whose events arrive through a :class:`WebhookSource`.

    The framework owns the wait/dispatch flow; subclasses describe only
    the differences from the generic shape.
    """

    webhook_source: ClassVar[Optional[Type[WebhookSource]]] = None
    # Auto-prepended to ``event_type_filter`` so users can type "charge.*"
    # instead of "stripe.charge.*". Empty disables the convenience.
    event_type_prefix: ClassVar[str] = ""
    mode: ClassVar[str] = "event"
    task_queue: ClassVar[str] = TaskQueue.TRIGGERS_EVENT
    Params: ClassVar[Type[BaseModel]] = BaseTriggerParams

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.webhook_source is not None and not getattr(cls, "event_type", ""):
            cls.event_type = cls.webhook_source.type

    def build_filter(self, params: BaseModel) -> Callable[[Any], bool]:
        type_filter = (getattr(params, "event_type_filter", "") or "all").strip()
        if type_filter and type_filter != "all" and self.event_type_prefix and not type_filter.startswith(self.event_type_prefix):
            type_filter = self.event_type_prefix + type_filter
        extras = self._extra_filter(params)

        def matches(event: Any) -> bool:
            ev = event if isinstance(event, WorkflowEvent) else WorkflowEvent(**event)
            if not ev.matches_type(type_filter):
                return False
            return extras(ev) if extras else True

        return matches

    def _extra_filter(self, params: BaseModel) -> Optional[Callable[[WorkflowEvent], bool]]:
        """Override for filters beyond event-type matching (e.g. livemode)."""
        return None

    async def _check_precondition(self) -> Optional[str]:
        """Override to short-circuit. Return error string or None."""
        return None

    async def execute(
        self,
        node_id: str,
        parameters: Dict[str, Any],
        context,
    ) -> Dict[str, Any]:
        err = await self._check_precondition()
        if err:
            return self._wrap_error(start_time=time.time(), error=err)
        result = await super().execute(node_id, parameters, context)
        if result.get("success"):
            event = result.get("result") or {}
            ev = event if isinstance(event, WorkflowEvent) else WorkflowEvent(**event)
            result["result"] = self.shape_output(ev)
        return result

    def shape_output(self, event: WorkflowEvent) -> Dict[str, Any]:
        """Default: dump the CloudEvent. Override for provider-shaped output."""
        return event.model_dump(mode="json")

    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: BaseModel):
        raise NotImplementedError("Event triggers return via TriggerNode.execute, not the op body")
