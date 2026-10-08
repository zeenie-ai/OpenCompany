"""Nonsecret 1Password enrollment metadata, shared through the application DB."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


class CredentialSource(SQLModel, table=True):
    __tablename__ = "credential_sources"
    __table_args__ = (UniqueConstraint("session_id", "provider"),)

    id: str = Field(primary_key=True, max_length=64)
    session_id: str = Field(default="default", index=True, max_length=255)
    owner_id: str = Field(index=True, max_length=255)
    provider: str = Field(index=True, max_length=255)
    reference: str = Field(max_length=512)
    models: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    model_params: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    # Endpoints are public configuration, never secret-bearing URLs.
    base_url: Optional[str] = Field(default=None, max_length=2048)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BrowserCredentialBinding(SQLModel, table=True):
    __tablename__ = "browser_credential_bindings"

    id: str = Field(primary_key=True, max_length=64)
    owner_id: str = Field(index=True, max_length=255)
    label: str = Field(max_length=128)
    origin: str = Field(max_length=2048)
    username_reference: str = Field(max_length=512)
    password_reference: str = Field(max_length=512)
    profile_id: Optional[str] = Field(default=None, max_length=255)
    employee_id: Optional[str] = Field(default=None, max_length=255)
    workflow_id: Optional[str] = Field(default=None, max_length=255)
    success_origin: str = Field(max_length=2048)
    success_path: str = Field(max_length=1024)
    success_selector: str = Field(default="", max_length=512)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
