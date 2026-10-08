"""Saved enrollment metadata. Secret values never enter these transactions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from models.credential_sources import BrowserCredentialBinding, CredentialSource
from .onepassword import CredentialSourceError, validate_reference


def exact_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise CredentialSourceError("invalid_origin", "Enter an exact HTTP(S) origin without credentials, path, query or fragment.") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise CredentialSourceError("invalid_origin", "Enter an exact HTTP(S) origin without credentials, path, query or fragment.")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        host += f":{port}"
    return f"{parsed.scheme}://{host}"


def public_base_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError):
        raise CredentialSourceError("invalid_endpoint", "Enter an HTTP(S) endpoint without credentials, query or fragment.") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CredentialSourceError("invalid_endpoint", "Enter an HTTP(S) endpoint without credentials, query or fragment.")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def binding_metadata(row: BrowserCredentialBinding) -> dict[str, Any]:
    return {name: getattr(row, name) for name in ("id", "label", "origin", "profile_id", "employee_id", "workflow_id", "success_origin", "success_path", "success_selector")}


class CredentialSources:
    def __init__(self, database):
        self.database = database

    @property
    def available(self) -> bool:
        return callable(getattr(self.database, "get_session", None))

    async def get(self, provider: str, session_id: str = "default") -> CredentialSource | None:
        if not self.available:
            return None
        async with self.database.get_session() as session:
            return (await session.execute(select(CredentialSource).where(CredentialSource.provider == provider, CredentialSource.session_id == session_id))).scalar_one_or_none()

    async def list(self, *, provider: str | None = None, session_id: str | None = None) -> list[CredentialSource]:
        if not self.available:
            return []
        query = select(CredentialSource)
        if provider is not None:
            query = query.where(CredentialSource.provider == provider)
        if session_id is not None:
            query = query.where(CredentialSource.session_id == session_id)
        async with self.database.get_session() as session:
            return list((await session.execute(query)).scalars().all())

    async def save(self, provider: str, reference: str, *, owner_id: str, session_id: str = "default", models: list[str] | None = None, model_params: dict | None = None, base_url: str | None = None) -> None:
        validate_reference(reference)
        url = public_base_url(base_url)
        if not provider or len(provider) > 255 or not owner_id or not session_id:
            raise CredentialSourceError("invalid_binding", "A provider and authenticated owner are required.")
        for attempt in range(2):
            async with self.database.get_session() as session:
                row = (await session.execute(select(CredentialSource).where(CredentialSource.provider == provider, CredentialSource.session_id == session_id).with_for_update())).scalar_one_or_none()
                if row is not None and row.owner_id != owner_id:
                    raise CredentialSourceError("access_denied", "Credential binding access denied.")
                if row is None:
                    row = CredentialSource(id=uuid.uuid4().hex, provider=provider, owner_id=owner_id, session_id=session_id, reference=reference)
                    session.add(row)
                row.reference, row.models, row.model_params, row.base_url = reference, list(models or []), dict(model_params or {}), url
                row.updated_at = datetime.now(timezone.utc)
                try:
                    await session.commit()
                    return
                except IntegrityError:
                    await session.rollback()
                    if attempt:
                        raise CredentialSourceError("binding_conflict", "The credential binding changed concurrently. Reload and retry.") from None

    async def delete(self, provider: str, session_id: str = "default", principal: str | None = None) -> None:
        if not self.available:
            return
        async with self.database.get_session() as session:
            row = (await session.execute(select(CredentialSource).where(CredentialSource.provider == provider, CredentialSource.session_id == session_id).with_for_update())).scalar_one_or_none()
            if row is not None:
                if principal is not None and row.owner_id != principal:
                    raise CredentialSourceError("access_denied", "Credential binding access denied.")
                await session.delete(row)
                await session.commit()

    async def browser_binding(self, binding_id: str, principal: str, *, profile_id: str | None = None, workflow_id: str | None = None, employee_id: str | None = None) -> BrowserCredentialBinding:
        if not principal or not binding_id or not self.available:
            raise CredentialSourceError("access_denied", "Browser credential binding access denied.")
        async with self.database.get_session() as session:
            row = await session.get(BrowserCredentialBinding, binding_id)
        if row is None or row.owner_id != str(principal) or (row.profile_id and row.profile_id != profile_id) or (row.workflow_id and row.workflow_id != workflow_id) or (row.employee_id and row.employee_id != employee_id):
            raise CredentialSourceError("access_denied", "Browser credential binding access denied.")
        return row

    async def list_browser(self, principal: str) -> list[dict]:
        async with self.database.get_session() as session:
            rows = (await session.execute(select(BrowserCredentialBinding).where(BrowserCredentialBinding.owner_id == principal).order_by(BrowserCredentialBinding.label, BrowserCredentialBinding.id))).scalars().all()
            return [binding_metadata(row) for row in rows]

    async def save_browser(self, principal: str, data: dict) -> dict:
        # caller validates ownership of optional workflow/profile/employee.
        origin = exact_origin(str(data.get("origin") or ""))
        success_origin = exact_origin(str(data.get("success_origin") or origin))
        path = str(data.get("success_path") or "")
        if not path.startswith("/") or "?" in path or "#" in path or len(path) > 1024:
            raise CredentialSourceError("invalid_success", "Configure an exact successful login path without a query or fragment.")
        label = str(data.get("label") or "").strip()
        selector = str(data.get("success_selector") or "")
        if not label or len(label) > 128 or len(selector) > 512:
            raise CredentialSourceError("invalid_binding", "Enter a label and a supported success selector.")
        username = validate_reference(data.get("username_reference"))
        password = validate_reference(data.get("password_reference"))
        async with self.database.get_session() as session:
            binding_id = str(data.get("binding_id") or uuid.uuid4().hex)
            row = await session.get(BrowserCredentialBinding, binding_id, with_for_update=True)
            if row is not None and row.owner_id != principal:
                raise CredentialSourceError("access_denied", "Browser credential binding access denied.")
            if row is None:
                row = BrowserCredentialBinding(id=binding_id, owner_id=principal, label=label, origin=origin, username_reference=username, password_reference=password, success_origin=success_origin, success_path=path)
                session.add(row)
            row.label, row.origin = label, origin
            row.username_reference, row.password_reference = username, password
            row.success_origin, row.success_path, row.success_selector = success_origin, path, selector
            for name in ("profile_id", "employee_id", "workflow_id"):
                setattr(row, name, str(data[name]) if data.get(name) else None)
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return binding_metadata(row)

    async def delete_browser(self, principal: str, binding_id: str) -> None:
        async with self.database.get_session() as session:
            row = await session.get(BrowserCredentialBinding, binding_id, with_for_update=True)
            if row is not None:
                if row.owner_id != principal:
                    raise CredentialSourceError("access_denied", "Browser credential binding access denied.")
                await session.delete(row)
                await session.commit()
