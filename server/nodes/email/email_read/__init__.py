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

    # folder — every operation forwards it (see EmailService.read), so the UI
    # must offer it for all six. Gating it to list/search left move/delete/flag
    # silently pinned to INBOX with no way to change the source folder.
    folder: str = Field(
        default="INBOX",
        description="Mailbox folder to query.",
        json_schema_extra={
            "displayOptions": {
                "show": {"operation": ["list", "search", "read", "move", "delete", "flag"]},
            },
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

    # Pagination (list / search). `limit` and `offset` used to live here too;
    # neither was read by EmailService.read, and because Params feeds the AI
    # tool schema verbatim, an agent could set limit=100 and silently get 20.
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
        description="Messages per page.",
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
    # Reads mail only; re-running a read on a transient failure is safe.
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.MESSAGING
    usable_as_tool = True

    Params = EmailReadParams
    Output = EmailReadOutput

    @Operation("query", cost={"service": "email", "action": "imap", "count": 1})
    async def query(self, ctx: NodeContext, params: EmailReadParams) -> Any:
        # Body inlined from handlers/email.py (Wave 11.D.1).
        from .._service import get_email_service

        return await get_email_service().read(params.model_dump())
