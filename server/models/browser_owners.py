"""Durable browser routing and nonsecret control state."""
from datetime import datetime, timezone
from typing import Optional
import socket
from sqlmodel import Field, SQLModel

class BrowserOwner(SQLModel, table=True):
    __tablename__ = "browser_owners"
    owner_id: str = Field(primary_key=True, max_length=120)
    runtime_epoch: str = Field(max_length=40)
    machine_id: str = Field(default_factory=socket.gethostname, max_length=255)
    base_url: str = Field(max_length=1024)
    heartbeat_at: float


class BrowserProfileOwner(SQLModel, table=True):
    __tablename__ = "browser_profile_owners"
    profile_id: str = Field(primary_key=True, max_length=64)
    principal_id: str = Field(index=True, max_length=255)
    owner_id: str = Field(index=True, max_length=120)
    runtime_epoch: Optional[str] = Field(default=None, max_length=40)
    task_id: Optional[str] = Field(default=None, max_length=1024)
    workflow_id: Optional[str] = Field(default=None, index=True, max_length=255)
    browser_node_id: Optional[str] = Field(default=None, index=True, max_length=255)
    sensitive_login: bool = False
    needs_observation: bool = False
    challenge_required: bool = False
    assistance_reason: Optional[str] = Field(default=None, max_length=40)
    assistance_message: Optional[str] = Field(default=None, max_length=500)
    assistance_deadline: Optional[float] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BrowserTransientRoute(SQLModel, table=True):
    """Nonsecret routing for expiring login/import handles; no cookies here."""
    __tablename__ = "browser_transient_routes"
    handle_id: str = Field(primary_key=True, max_length=64)
    principal_id: str = Field(index=True, max_length=255)
    owner_id: str = Field(max_length=120)
    profile_id: Optional[str] = Field(default=None, max_length=64)
    expires_at: float
