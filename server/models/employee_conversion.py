"""Reviewed conversion snapshots, independent of employee runtime state."""
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, SQLModel


class EmployeeConversion(SQLModel, table=True):
    __tablename__ = "employee_conversions"
    id: str = Field(primary_key=True, max_length=64)
    workflow_id: str = Field(index=True, max_length=255)
    owner_id: str = Field(index=True, max_length=255)
    state: str = Field(default="review", index=True, max_length=20)
    source_hash: str = Field(max_length=64)
    source: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    proposed: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), nullable=False))
