"""Webhook Trigger — Wave 11.B reference migration (event-based trigger).

Listens for inbound HTTP requests dispatched into ``services.event_waiter``
by ``routers/webhook.py``. Filter narrows to a specific ``path``. No
credentials, no HTTP egress — the routing layer injects the event.

Replaces:
- ``server/nodes/triggers.py:webhookTrigger`` metadata-only registration
  (kept in place in this module is removed; triggers.py entry deleted).
- Filter builder ``services.event_waiter.build_webhook_filter`` stays
  wired to ``FILTER_BUILDERS`` because the waiter registers BEFORE
  ``TriggerNode.build_filter`` is available — 11.F unifies.

Note: ``TRIGGER_REGISTRY`` entry in ``event_waiter.py`` continues to
own the ``event_type`` mapping. A plugin-class method
(:meth:`WebhookTriggerNode.event_type`) could eventually claim that,
but it touches deployment-mode dispatch — deferred to 11.F.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, Operation, TriggerNode, TaskQueue


class WebhookTriggerParams(BaseModel):
    path: str = Field(
        default="",
        description="URL path fragment — becomes /webhook/{path}",
    )
    method: Literal["GET", "POST", "PUT", "DELETE", "ALL"] = Field(
        default="POST",
        description="HTTP method to accept. ALL accepts any method.",
    )
    response_mode: Literal["immediate", "responseNode"] = Field(
        default="immediate",
        description="immediate: return 200 OK right away. responseNode: wait for a webhookResponse node downstream.",
    )
    authentication: Literal["none", "header"] = Field(
        default="none",
        description="none: no auth. header: require a matching header on inbound requests.",
    )
    header_name: str = Field(
        default="X-API-Key",
        description="Header name to check when authentication=header.",
        json_schema_extra={"displayOptions": {"show": {"authentication": ["header"]}}},
    )
    header_value: str = Field(
        default="",
        description="Expected header value when authentication=header.",
        json_schema_extra={
            "password": True,
            "displayOptions": {"show": {"authentication": ["header"]}},
        },
    )

    model_config = ConfigDict(extra="ignore")


class WebhookTriggerOutput(BaseModel):
    method: Optional[str] = None
    path: Optional[str] = None
    headers: Optional[dict] = None
    query: Optional[dict] = None
    body: Optional[str] = None
    json_: Optional[dict] = Field(default=None)

    model_config = ConfigDict(extra="allow")


class WebhookTriggerNode(TriggerNode):
    type = "webhookTrigger"
    display_name = "Webhook Trigger"
    subtitle = "HTTP Inbound"
    group = ("trigger",)
    description = "Start workflow when HTTP request is received"
    component_kind = "trigger"
    handles = (
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    task_queue = TaskQueue.TRIGGERS_EVENT
    mode = "event"
    event_type = "webhook_received"

    Params = WebhookTriggerParams
    Output = WebhookTriggerOutput

    def build_filter(self, params: WebhookTriggerParams) -> Callable[[Dict[str, Any]], bool]:
        """Narrow dispatched events to this trigger's path."""
        expected_path = params.path or ""

        def matches(event: Dict[str, Any]) -> bool:
            if expected_path and event.get("path") != expected_path:
                return False
            return True

        return matches

    # Event triggers don't run a body — the base class handles the
    # ``event_waiter.register()`` + ``await future`` flow. Declare the
    # stub operation so invariants see it.
    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: WebhookTriggerParams) -> WebhookTriggerOutput:
        raise NotImplementedError(
            "Event triggers return via TriggerNode.execute, not the op body"
        )
