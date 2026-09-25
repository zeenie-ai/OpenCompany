"""Drafts waiting for the owner: one row per approval-gate wait.

A gate creates its row the first time it runs and finds the same row on
every retry (``idempotency_key`` is unique: ``t:<temporal workflow>:<run>:
<activity>`` on Temporal, ``p:<execution>:<node>`` in-process). The row is
the whole state of the wait; the node only watches it.

``status``: pending -> approved | discarded | expired | cancelled, each
move a compare-and-swap on ``revision``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalRequest(SQLModel, table=True):
    __tablename__ = "approval_requests"

    id: str = Field(primary_key=True, max_length=64)
    idempotency_key: str = Field(unique=True, max_length=255)
    owner_id: str = Field(index=True, max_length=255)
    workflow_id: str = Field(index=True, max_length=255)
    node_id: str = Field(max_length=255)
    generation: int = Field(default=0)
    execution_id: Optional[str] = Field(default=None, max_length=255)
    #: ``temporal`` or ``local``: a local wait cannot outlive its process.
    runtime: str = Field(default="local", max_length=20)
    status: str = Field(default="pending", index=True, max_length=20)

    #: The app the reply goes out through ("WhatsApp").
    channel: str = Field(default="", max_length=60)
    recipient: str = Field(default="", max_length=500)
    recipient_label: str = Field(default="", max_length=200)
    subject: Optional[str] = Field(default=None, max_length=500)
    draft_text: str = Field(default="", max_length=20000)
    #: What goes out: the draft, or the owner's edit of it.
    final_text: Optional[str] = Field(default=None, max_length=20000)
    final_subject: Optional[str] = Field(default=None, max_length=500)
    edited: bool = Field(default=False)
    #: The message being answered, shortened, for the card.
    context_excerpt: Optional[str] = Field(default=None, max_length=2000)
    #: The channel's limit for an edited draft.
    max_length: int = Field(default=20000)
    #: The decide request that settled it: the same key again is a no-op.
    decision_key: Optional[str] = Field(default=None, max_length=128)
    revision: int = Field(default=0)

    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    decided_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


__all__ = ["ApprovalRequest"]
