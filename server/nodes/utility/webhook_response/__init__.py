"""Webhook Response — Wave 11.C migration. Reads connected_outputs."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class WebhookResponseParams(BaseModel):
    status_code: int = Field(default=200, ge=100, le=599)
    body: Any = Field(default=None)
    headers: Dict[str, str] = Field(default_factory=dict)
    content_type: str = Field(default="application/json")

    model_config = ConfigDict(extra="allow")


class WebhookResponseOutput(BaseModel):
    sent: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


class WebhookResponseNode(ActionNode):
    type = "webhookResponse"
    display_name = "Webhook Response"
    subtitle = "HTTP Reply"
    group = ("utility",)
    description = "Send custom response back to webhook caller with configurable status code, body, and headers"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.REST_API

    Params = WebhookResponseParams
    Output = WebhookResponseOutput

    @Operation("respond")
    async def respond(self, ctx: NodeContext, params: WebhookResponseParams) -> Any:
        """Inlined from handlers/http.py (Wave 11.D.3).

        Resolves ``{{input.key}}`` and ``{{nodeType.key}}`` templates
        against upstream outputs, then calls into ``routers.webhook``
        to release the pending HTTP request.
        """
        import json as json_module
        from core.logging import get_logger
        from routers.webhook import resolve_webhook_response

        log = get_logger(__name__)
        outputs = ctx.raw.get("connected_outputs") or {}
        body = params.body
        if isinstance(body, str) and outputs:
            for node_type_key, output_data in outputs.items():
                if isinstance(output_data, dict):
                    for key, value in output_data.items():
                        body = body.replace(f"{{{{input.{key}}}}}", str(value))
                        body = body.replace(f"{{{{{node_type_key}.{key}}}}}", str(value))
        if not body and outputs:
            first_output = next(iter(outputs.values()), {})
            body = json_module.dumps(first_output, default=str)

        body_text = body if isinstance(body, str) else json_module.dumps(body, default=str)
        log.info(
            "[Webhook Response] Sending", node_id=ctx.node_id,
            status_code=params.status_code, content_type=params.content_type,
            body_length=len(body_text),
        )
        resolve_webhook_response(ctx.node_id, {
            "statusCode": params.status_code,
            "body": body_text,
            "contentType": params.content_type,
        })
        return {
            "sent": True,
            "statusCode": params.status_code,
            "contentType": params.content_type,
            "bodyLength": len(body_text),
        }
