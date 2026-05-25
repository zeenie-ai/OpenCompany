"""Gmail — Wave 11.D.4 inlined (multi-op ActionNode)."""

from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from .._credentials import GoogleCredential

from .._base import build_google_service, run_sync, track_google_usage
from .._gmail import fetch_email_details, format_message


class GmailParams(BaseModel):
    operation: Literal["send", "search", "read"] = "send"

    to: str = Field(
        default="",
        json_schema_extra={"displayOptions": {"show": {"operation": ["send"]}}},
    )
    cc: str = Field(default="", json_schema_extra={"displayOptions": {"show": {"operation": ["send"]}}})
    bcc: str = Field(default="", json_schema_extra={"displayOptions": {"show": {"operation": ["send"]}}})
    subject: str = Field(
        default="",
        json_schema_extra={"displayOptions": {"show": {"operation": ["send"]}}},
    )
    body: str = Field(
        default="",
        json_schema_extra={
            "rows": 4,
            "placeholder": "Write your message...",
            "displayOptions": {"show": {"operation": ["send"]}},
        },
    )
    body_type: Literal["text", "html"] = Field(
        default="text",
        json_schema_extra={"displayOptions": {"show": {"operation": ["send"]}}},
    )

    query: str = Field(
        default="",
        json_schema_extra={
            "placeholder": "from:jane subject:meeting",
            "displayOptions": {"show": {"operation": ["search"]}},
        },
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=100,
        json_schema_extra={"displayOptions": {"show": {"operation": ["search"]}}},
    )
    include_body: bool = Field(
        default=False,
        json_schema_extra={"displayOptions": {"show": {"operation": ["search"]}}},
    )

    message_id: str = Field(
        default="",
        json_schema_extra={"displayOptions": {"show": {"operation": ["read"]}}},
    )
    format: Literal["full", "minimal", "raw", "metadata"] = Field(
        default="full",
        json_schema_extra={"displayOptions": {"show": {"operation": ["read"]}}},
    )

    model_config = ConfigDict(extra="ignore")


class GmailOutput(BaseModel):
    operation: Optional[str] = None
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    label_ids: Optional[List[str]] = None
    to: Optional[str] = None
    subject: Optional[str] = None
    messages: Optional[List[dict]] = None
    count: Optional[int] = None
    query: Optional[str] = None
    result_size_estimate: Optional[int] = None
    from_: Optional[str] = Field(default=None)
    date: Optional[str] = None
    body: Optional[str] = None
    snippet: Optional[str] = None
    labels: Optional[List[str]] = None
    attachments: Optional[List[dict]] = None

    model_config = ConfigDict(extra="allow")


class GmailNode(ActionNode):
    type = "googleGmail"
    display_name = "Gmail"
    subtitle = "Email Operations"
    group = ("google", "tool")
    description = "Google Gmail send / search / read (dual-purpose workflow + AI tool)"
    component_kind = "square"
    tool_name = "google_gmail"
    tool_description = (
        "Send, search, and read emails via Gmail. Operations: send (compose email), search (find emails by query), read (get email by ID)."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    credentials = (GoogleCredential,)
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = GmailParams
    Output = GmailOutput

    async def _service(self, ctx: NodeContext, params: GmailParams):
        return await build_google_service(
            "gmail",
            "v1",
            params.model_dump(),
            ctx.raw,
        )

    @Operation("send", cost={"service": "gmail", "action": "send", "count": 1})
    async def send(self, ctx: NodeContext, params: GmailParams) -> GmailOutput:
        if not params.to:
            raise RuntimeError("Recipient email address (to) is required")
        if not params.subject:
            raise RuntimeError("Email subject is required")
        if not params.body:
            raise RuntimeError("Email body is required")

        if params.body_type == "html":
            message = MIMEMultipart("alternative")
            message.attach(MIMEText(params.body, "html"))
        else:
            message = MIMEText(params.body, "plain")
        message["to"] = params.to
        message["subject"] = params.subject
        if params.cc:
            message["cc"] = params.cc
        if params.bcc:
            message["bcc"] = params.bcc

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        svc = await self._service(ctx, params)
        result = await run_sync(
            lambda: svc.users()
            .messages()
            .send(
                userId="me",
                body={"raw": raw},
            )
            .execute()
        )
        await track_google_usage("gmail", ctx.node_id, "send", 1, ctx.raw)
        return GmailOutput(
            operation="send",
            message_id=result.get("id"),
            thread_id=result.get("threadId"),
            label_ids=result.get("labelIds", []),
            to=params.to,
            subject=params.subject,
        )

    @Operation("search", cost={"service": "gmail", "action": "search", "count": 1})
    async def search(self, ctx: NodeContext, params: GmailParams) -> GmailOutput:
        if not params.query:
            raise RuntimeError("Search query is required")
        svc = await self._service(ctx, params)
        listing = await run_sync(
            lambda: svc.users()
            .messages()
            .list(
                userId="me",
                q=params.query,
                maxResults=min(params.max_results, 100),
            )
            .execute()
        )

        messages = listing.get("messages", [])
        formatted = []
        fmt = "full" if params.include_body else "metadata"
        for msg in messages:
            mid = msg.get("id")
            detail = await run_sync(
                lambda m=mid: svc.users()
                .messages()
                .get(
                    userId="me",
                    id=m,
                    format=fmt,
                    metadataHeaders=["From", "To", "Subject", "Date"],
                )
                .execute()
            )
            formatted.append(format_message(detail, include_body=params.include_body))

        await track_google_usage("gmail", ctx.node_id, "search", len(formatted), ctx.raw)
        return GmailOutput(
            operation="search",
            messages=formatted,
            count=len(formatted),
            query=params.query,
            result_size_estimate=listing.get("resultSizeEstimate", 0),
        )

    @Operation("read", cost={"service": "gmail", "action": "read", "count": 1})
    async def read(self, ctx: NodeContext, params: GmailParams) -> GmailOutput:
        if not params.message_id:
            raise RuntimeError("message_id is required")
        svc = await self._service(ctx, params)
        result = await run_sync(
            lambda: svc.users()
            .messages()
            .get(
                userId="me",
                id=params.message_id,
                format=params.format,
            )
            .execute()
        )
        await track_google_usage("gmail", ctx.node_id, "read", 1, ctx.raw)
        formatted = format_message(result, include_body=(params.format == "full"))
        return GmailOutput(operation="read", **{k: v for k, v in formatted.items() if k != "size_estimate"})
