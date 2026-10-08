"""Small, non-transcript projection of durable Workspace task executions."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, JSON
from sqlmodel import Field, SQLModel


class WorkspaceTaskRecord(SQLModel, table=True):
    __tablename__ = "workspace_task_records"

    invocation_id: str = Field(primary_key=True, max_length=256)
    submission_id: str = Field(max_length=36)
    principal: str = Field(index=True, max_length=255)
    workflow_id: str = Field(index=True, max_length=255)
    node_id: str = Field(index=True, max_length=255)
    fingerprint: str = Field(max_length=128)
    prompt: str = Field(max_length=20000)
    status: str = Field(default="queued", index=True, max_length=32)
    result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    browser_owner_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), index=True))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True)))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), index=True))
