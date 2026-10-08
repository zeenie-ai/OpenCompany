"""Browser profile metadata. Profile files and cookie values stay outside SQL."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Column, UniqueConstraint, Index, func, text
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrowserProfileRow(SQLModel, table=True):
    __tablename__ = "browser_profiles"
    __table_args__ = (UniqueConstraint("owner_id", "workflow_id", "kind", name="uq_browser_profile_workflow"),)
    id: str = Field(primary_key=True, max_length=40)
    owner_id: str = Field(default="owner", index=True, max_length=255)
    name: str = Field(max_length=120)
    kind: str = Field(default="shared", max_length=20)
    workflow_id: Optional[str] = Field(default=None, index=True, max_length=255)
    chrome_major: Optional[int] = Field(default=None)
    sites: Optional[List[Dict[str, Any]]] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


Index("uq_browser_profile_shared_name", BrowserProfileRow.owner_id, func.lower(BrowserProfileRow.name),
      unique=True, sqlite_where=text("kind = 'shared'"), postgresql_where=text("kind = 'shared'"))
