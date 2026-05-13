"""Email Send — Wave 11.C migration. SMTP via Himalaya CLI."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class EmailSendParams(BaseModel):
    provider: Literal[
        "gmail", "outlook", "yahoo", "icloud",
        "protonmail", "fastmail", "custom",
    ] = "gmail"
    to: str = Field(...)
    subject: str = Field(...)
    body: str = Field(default="", json_schema_extra={"rows": 6})
    cc: str = Field(default="")
    bcc: str = Field(default="")
    body_type: Literal["text", "html"] = Field(default="text")

    model_config = ConfigDict(extra="ignore")


class EmailSendOutput(BaseModel):
    sent: Optional[bool] = None
    message_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class EmailSendNode(ActionNode):
    type = "emailSend"
    display_name = "Email Send"
    subtitle = "SMTP Outbound"
    group = ("email", "tool")
    description = "Send emails via SMTP (Gmail, Outlook, Yahoo, iCloud, ProtonMail, Fastmail, custom)"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.MESSAGING
    usable_as_tool = True

    Params = EmailSendParams
    Output = EmailSendOutput

    @Operation("send", cost={"service": "email", "action": "send", "count": 1})
    async def send(self, ctx: NodeContext, params: EmailSendParams) -> Any:
        # Body inlined from handlers/email.py (Wave 11.D.1).
        from .._service import get_email_service
        return await get_email_service().send(params.model_dump(by_alias=False))
