"""Stripe Receive — fires when StripeWebhookSource accepts a verified
forwarded event."""

from __future__ import annotations

from typing import Callable, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.events import BaseTriggerParams, WebhookTriggerNode, WorkflowEvent

from ._credentials import StripeCredential
from ._source import StripeWebhookSource


_PREFIX = "stripe."


class StripeReceiveParams(BaseTriggerParams):
    livemode_filter: Literal["all", "test", "live"] = Field(
        default="all",
        description="Filter by livemode flag on the event.",
    )


class StripeReceiveOutput(BaseModel):
    event_id: Optional[str] = None
    event_type: Optional[str] = None
    created: Optional[int] = None
    livemode: Optional[bool] = None
    api_version: Optional[str] = None
    request_id: Optional[str] = None
    account: Optional[str] = None
    data: Optional[dict] = None

    model_config = ConfigDict(extra="allow")


class StripeReceiveNode(WebhookTriggerNode):
    type = "stripeReceive"
    display_name = "Stripe Receive"
    subtitle = "Webhook Event"
    group = ("payments", "trigger")
    description = "Trigger workflow when Stripe webhook event arrives"
    component_kind = "trigger"
    handles = ({"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},)
    credentials = (StripeCredential,)
    webhook_source = StripeWebhookSource
    event_type_prefix = _PREFIX
    Params = StripeReceiveParams
    Output = StripeReceiveOutput

    def _extra_filter(
        self,
        params: StripeReceiveParams,
    ) -> Optional[Callable[[WorkflowEvent], bool]]:
        if params.livemode_filter == "all":
            return None
        target_live = params.livemode_filter == "live"

        def matches(ev: WorkflowEvent) -> bool:
            payload = ev.data if isinstance(ev.data, dict) else {}
            return bool(payload.get("livemode")) is target_live

        return matches

    async def _check_precondition(self) -> Optional[str]:
        from ._source import get_listen_source

        if not get_listen_source()._started:
            return "Stripe daemon not running. Add Stripe API key in Credentials and connect."
        return None

    def shape_output(self, event: WorkflowEvent) -> Dict:
        payload = event.data if isinstance(event.data, dict) else {}
        stripe_type = event.type[len(_PREFIX) :] if event.type.startswith(_PREFIX) else event.type
        request = payload.get("request")
        request_id = request.get("id") if isinstance(request, dict) else request
        nested = payload.get("data")
        return {
            "event_id": event.id,
            "event_type": stripe_type,
            "created": payload.get("created"),
            "livemode": payload.get("livemode"),
            "api_version": payload.get("api_version"),
            "request_id": request_id,
            "account": payload.get("account") or (event.source.split("://", 1)[1] if "://" in event.source else None),
            "data": nested if isinstance(nested, dict) else payload,
        }
