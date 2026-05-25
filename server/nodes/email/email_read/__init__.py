"""Email Read — Wave 11.C migration. IMAP via Himalaya CLI."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class EmailReadParams(BaseModel):
    provider: Literal[
        "gmail",
        "outlook",
        "yahoo",
        "icloud",
        "protonmail",
        "fastmail",
        "custom",
    ] = Field(default="gmail", description="IMAP provider preset.")
    operation: Literal["list", "search", "read", "folders", "move", "delete", "flag"] = Field(
        default="list",
        description="IMAP action. folders lists all mailbox folders (no other params needed).",
    )

    # folder (used by list/search and move source; optional for others)
    folder: str = Field(
        default="INBOX",
        description="Mailbox folder to query.",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["list", "search"]}},
        },
    )
    query: str = Field(
        default="",
        description="IMAP search query (e.g. from:alice subject:meeting).",
        json_schema_extra={"displayOptions": {"show": {"operation": ["search"]}}},
    )
    message_id: str = Field(
        default="",
        description="Message ID (required for read/move/delete/flag).",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["read", "move", "delete", "flag"]}},
        },
    )
    target_folder: str = Field(
        default="",
        description="Destination folder for move.",
        json_schema_extra={"displayOptions": {"show": {"operation": ["move"]}}},
    )
    flag: Literal["", "Seen", "Answered", "Flagged", "Draft", "Deleted"] = Field(
        default="",
        description="Flag to toggle.",
        json_schema_extra={"displayOptions": {"show": {"operation": ["flag"]}}},
    )
    flag_action: Literal["add", "remove"] = Field(
        default="add",
        description="add applies the flag; remove clears it.",
        json_schema_extra={"displayOptions": {"show": {"operation": ["flag"]}}},
    )

    # Pagination (list / search)
    limit: int = Field(
        default=20,
        ge=1,
        le=500,
        description="Max envelopes per page.",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["list", "search"]}},
        },
    )
    page: int = Field(
        default=1,
        ge=1,
        description="Page number (1-indexed).",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["list", "search"]}},
        },
    )
    page_size: int = Field(
        default=20,
        ge=1,
        le=500,
        description="Items per page (overrides limit when paginating).",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["list", "search"]}},
        },
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Alternative to page-based pagination — skip this many messages.",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["list", "search"]}},
        },
    )

    model_config = ConfigDict(extra="ignore")


class EmailReadOutput(BaseModel):
    operation: Optional[str] = None
    messages: Optional[list] = None
    folders: Optional[list] = None
    body: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class EmailReadNode(ActionNode):
    type = "emailRead"
    display_name = "Email Read"
    subtitle = "IMAP Read/Manage"
    group = ("email", "tool")
    description = "Read and manage emails via IMAP - list, search, read, move, delete, flag"
    component_kind = "square"
    tool_name = "email_read"
    tool_description = "Read and manage emails via IMAP. Operations: list (envelopes), search (query), read (message by ID), folders (list), move, delete, flag."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.MESSAGING
    usable_as_tool = True

    Params = EmailReadParams
    Output = EmailReadOutput

    @Operation("query", cost={"service": "email", "action": "imap", "count": 1})
    async def query(self, ctx: NodeContext, params: EmailReadParams) -> Any:
        # Body inlined from handlers/email.py (Wave 11.D.1).
        from .._service import get_email_service

        return await get_email_service().read(params.model_dump())
