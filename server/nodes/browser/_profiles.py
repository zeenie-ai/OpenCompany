"""Browser profiles: named Chrome profiles that keep an agent's logins.

A profile is a Chrome ``--user-data-dir`` under
``<DATA_DIR>/browser/profiles/<id>/user-data`` plus one row here. Profiles
are shared by name ("Work", "Personal") or belong to one employee (created
the first time that employee's Browser node runs without a profile chosen).
Only one Chrome can open a profile at a time; ``_fleet.py`` enforces that.

The directory name is always the generated id, never the name the user
typed, so a name like ``../..`` can never reach a path. Profile data is
sensitive (cookies are effectively the user's passwords): directories are
created ``0700`` on POSIX and never included in workflow exports.

Same shape as the Canvas store: this module owns its table instead of adding
methods to ``core.database``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Column, delete, select
from sqlmodel import Field, SQLModel

from core.logging import get_logger

logger = get_logger(__name__)

PROFILE_KINDS = ("shared", "employee", "login")
_NAME_MAX = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrowserProfileRow(SQLModel, table=True):
    __tablename__ = "browser_profiles"

    id: str = Field(primary_key=True, max_length=40)
    owner_id: str = Field(default="owner", index=True, max_length=255)
    name: str = Field(max_length=120)
    kind: str = Field(default="shared", max_length=20)
    workflow_id: Optional[str] = Field(default=None, index=True, max_length=255)
    #: Major version of the last Chrome that opened it. A profile written by
    #: a newer Chrome must not be opened by an older one.
    chrome_major: Optional[int] = Field(default=None)
    #: Domains and cookie counts from the last time we looked (never values),
    #: so the Credentials panel can show them without starting Chrome.
    sites: Optional[List[Dict[str, Any]]] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ProfileError(ValueError):
    """User-correctable profile problem (duplicate name, unknown id)."""


@dataclass(frozen=True)
class Profile:
    id: str
    owner_id: str
    name: str
    kind: str
    workflow_id: Optional[str]
    chrome_major: Optional[int]
    sites: tuple = ()

    def to_wire(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind, "workflow_id": self.workflow_id, "sites": list(self.sites)}


def _from_row(row: BrowserProfileRow) -> Profile:
    return Profile(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        kind=row.kind,
        workflow_id=row.workflow_id,
        chrome_major=row.chrome_major,
        sites=tuple(row.sites or ()),
    )


def profiles_root() -> Path:
    from core.paths import data_path

    return data_path("browser") / "profiles"


def profile_dir(profile_id: str) -> Path:
    """``<root>/<id>``, holding ``user-data/`` (Chrome's) and our lock files."""
    from core.paths import safe_path_component

    path = profiles_root() / safe_path_component(profile_id, fallback="profile")
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(path, 0o700)
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    return path


def user_data_dir(profile_id: str) -> Path:
    path = profile_dir(profile_id) / "user-data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_profile_files(profile_id: str) -> None:
    from core.paths import safe_path_component

    shutil.rmtree(profiles_root() / safe_path_component(profile_id, fallback="profile"), ignore_errors=True)


def clean_name(name: Any) -> str:
    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        raise ProfileError("A profile needs a name")
    if len(cleaned) > _NAME_MAX:
        raise ProfileError(f"A profile name can be at most {_NAME_MAX} characters")
    return cleaned


class ProfileStore:
    _schema_lock = asyncio.Lock()
    _initialized_engines: set[Any] = set()
    _revision = 0

    def __init__(self, database: Any) -> None:
        self.database = database

    @classmethod
    def bump_revision(cls) -> int:
        cls._revision += 1
        return cls._revision

    async def ensure_schema(self) -> None:
        engine = getattr(self.database, "engine", None)
        if engine is None:
            raise RuntimeError("Database is not initialized")
        if engine in self._initialized_engines:
            return
        async with self._schema_lock:
            if engine in self._initialized_engines:
                return
            async with engine.begin() as connection:
                await connection.run_sync(lambda sync: BrowserProfileRow.__table__.create(sync, checkfirst=True))
            self._initialized_engines.add(engine)

    async def list(self, owner_id: str) -> List[Profile]:
        await self.ensure_schema()
        async with self.database.get_session() as session:
            rows = (
                await session.execute(
                    select(BrowserProfileRow).where(BrowserProfileRow.owner_id == owner_id).order_by(BrowserProfileRow.created_at)
                )
            ).scalars()
            return [_from_row(r) for r in rows if r.kind != "login"]

    async def get(self, owner_id: str, profile_id: str) -> Profile:
        await self.ensure_schema()
        async with self.database.get_session() as session:
            row = await session.get(BrowserProfileRow, profile_id)
            if row is None or row.owner_id != owner_id:
                raise ProfileError("Browser profile not found")
            return _from_row(row)

    async def _name_taken(self, session: Any, owner_id: str, name: str, *, exclude_id: Optional[str] = None) -> bool:
        rows = (await session.execute(select(BrowserProfileRow).where(BrowserProfileRow.owner_id == owner_id))).scalars()
        return any(r.name.lower() == name.lower() and r.id != exclude_id for r in rows)

    async def create(self, owner_id: str, name: str, *, kind: str = "shared", workflow_id: Optional[str] = None) -> Profile:
        if kind not in PROFILE_KINDS:
            raise ProfileError(f"Unknown profile kind {kind!r}")
        name = clean_name(name)
        await self.ensure_schema()
        async with self.database.get_session() as session:
            if kind == "shared" and await self._name_taken(session, owner_id, name):
                raise ProfileError(f"A browser profile named {name!r} already exists")
            row = BrowserProfileRow(id=f"bp_{uuid.uuid4().hex[:16]}", owner_id=owner_id, name=name, kind=kind, workflow_id=workflow_id)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            profile = _from_row(row)
        self.bump_revision()
        return profile

    async def rename(self, owner_id: str, profile_id: str, name: str) -> Profile:
        name = clean_name(name)
        await self.ensure_schema()
        async with self.database.get_session() as session:
            row = await session.get(BrowserProfileRow, profile_id)
            if row is None or row.owner_id != owner_id:
                raise ProfileError("Browser profile not found")
            if await self._name_taken(session, owner_id, name, exclude_id=profile_id):
                raise ProfileError(f"A browser profile named {name!r} already exists")
            row.name = name
            row.updated_at = _utcnow()
            session.add(row)
            await session.commit()
            profile = _from_row(row)
        self.bump_revision()
        return profile

    async def delete(self, owner_id: str, profile_id: str) -> None:
        """Remove the row (the caller stops Chrome and removes the files)."""
        await self.ensure_schema()
        async with self.database.get_session() as session:
            row = await session.get(BrowserProfileRow, profile_id)
            if row is None or row.owner_id != owner_id:
                raise ProfileError("Browser profile not found")
            await session.execute(delete(BrowserProfileRow).where(BrowserProfileRow.id == profile_id))
            await session.commit()
        self.bump_revision()

    async def default_for_workflow(self, owner_id: str, workflow_id: str, name: str) -> Profile:
        """The employee profile of a workflow, created on first use."""
        await self.ensure_schema()
        async with self.database.get_session() as session:
            rows = (
                await session.execute(
                    select(BrowserProfileRow).where(
                        BrowserProfileRow.owner_id == owner_id,
                        BrowserProfileRow.workflow_id == workflow_id,
                        BrowserProfileRow.kind == "employee",
                    )
                )
            ).scalars()
            existing = next(iter(rows), None)
            if existing is not None:
                return _from_row(existing)
        return await self.create(owner_id, name, kind="employee", workflow_id=workflow_id)

    async def employee_profiles_of(self, workflow_id: str) -> List[Profile]:
        """Every owner's employee profile of a workflow (for its deletion)."""
        await self.ensure_schema()
        async with self.database.get_session() as session:
            rows = (
                await session.execute(
                    select(BrowserProfileRow).where(BrowserProfileRow.workflow_id == workflow_id, BrowserProfileRow.kind == "employee")
                )
            ).scalars()
            return [_from_row(r) for r in rows]

    async def for_workflow(self, owner_id: str, workflow_id: str) -> List[Profile]:
        await self.ensure_schema()
        async with self.database.get_session() as session:
            rows = (
                await session.execute(
                    select(BrowserProfileRow).where(BrowserProfileRow.owner_id == owner_id, BrowserProfileRow.workflow_id == workflow_id)
                )
            ).scalars()
            return [_from_row(r) for r in rows]

    async def update_sites(self, profile_id: str, sites: List[Dict[str, Any]]) -> None:
        await self.ensure_schema()
        async with self.database.get_session() as session:
            row = await session.get(BrowserProfileRow, profile_id)
            if row is None:
                return
            row.sites = [{"domain": str(s.get("domain")), "cookie_count": int(s.get("cookie_count") or 0)} for s in sites]
            row.updated_at = _utcnow()
            session.add(row)
            await session.commit()
        self.bump_revision()

    async def record_chrome_major(self, profile_id: str, major: int) -> None:
        await self.ensure_schema()
        async with self.database.get_session() as session:
            row = await session.get(BrowserProfileRow, profile_id)
            if row is None:
                return
            if row.chrome_major is None or major > row.chrome_major:
                row.chrome_major = major
                row.updated_at = _utcnow()
                session.add(row)
                await session.commit()


__all__ = [
    "BrowserProfileRow",
    "PROFILE_KINDS",
    "Profile",
    "ProfileError",
    "ProfileStore",
    "clean_name",
    "profile_dir",
    "profiles_root",
    "remove_profile_files",
    "user_data_dir",
]
