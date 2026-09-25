"""Employee rows (models.employees.Employee): reserve, finish, read, delete.

Functions take the ``Database`` and open their own session, like
services.agent_context.conversation. They never import ``nodes/``.

The hire path is: ``reserve`` (a ``building`` row keyed by the owner's
idempotency key) -> save the workflow -> ``mark_ready`` with its id. A
retried Hire with the same key gets the same row back from ``reserve``;
``created`` tells the caller whether it is the first attempt.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from core.logging import get_logger
from models.employees import Employee

logger = get_logger(__name__)

#: Columns ``reserve`` / ``update_employee`` may set from hire data.
HIRE_FIELDS = frozenset(
    {"role", "description", "job", "color_role", "apps", "unsupported_apps", "plan", "rules", "choices", "trigger", "llm", "builder_version"}
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hire_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    unknown = set(fields) - HIRE_FIELDS
    if unknown:
        raise ValueError(f"not employee hire fields: {sorted(unknown)}")
    return dict(fields)


async def get_by_workflow(database: Any, workflow_id: str) -> Optional[Employee]:
    async with database.get_session() as session:
        result = await session.execute(select(Employee).where(Employee.workflow_id == workflow_id))
        return result.scalar_one_or_none()


async def list_by_workflow_ids(database: Any, workflow_ids: Iterable[str]) -> Dict[str, Employee]:
    """Rows for the given workflows, keyed by workflow id. Workflows without a
    row (built in the editor) are absent."""
    ids = [str(workflow_id) for workflow_id in workflow_ids if workflow_id]
    if not ids:
        return {}
    async with database.get_session() as session:
        result = await session.execute(select(Employee).where(Employee.workflow_id.in_(ids)))
        return {row.workflow_id: row for row in result.scalars().all() if row.workflow_id}


async def get_by_idempotency_key(database: Any, owner_id: str, idempotency_key: str) -> Optional[Employee]:
    async with database.get_session() as session:
        result = await session.execute(
            select(Employee).where(Employee.owner_id == owner_id, Employee.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()


async def reserve(
    database: Any,
    *,
    owner_id: str,
    idempotency_key: str,
    payload_hash: str,
    fields: Dict[str, Any],
) -> Tuple[Employee, bool]:
    """Create the ``building`` row for a hire, or return the one this key
    already made. Returns ``(row, created)``."""
    values = _hire_fields(fields)
    existing = await get_by_idempotency_key(database, owner_id, idempotency_key)
    if existing is not None:
        return existing, False
    row = Employee(
        id=uuid.uuid4().hex,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        hire_state="building",
        **values,
    )
    try:
        async with database.get_session() as session:
            session.add(row)
            await session.commit()
        return row, True
    except IntegrityError:
        # A concurrent attempt with the same key won the insert.
        existing = await get_by_idempotency_key(database, owner_id, idempotency_key)
        if existing is None:
            raise
        return existing, False


async def mark_ready(
    database: Any,
    employee_id: str,
    *,
    workflow_id: str,
    node_roles: Dict[str, Any],
) -> Optional[Employee]:
    """Attach the saved workflow to a reserved row and mark it ``ready``."""
    async with database.get_session() as session:
        row = await session.get(Employee, employee_id)
        if row is None:
            return None
        now = _utcnow()
        row.workflow_id = workflow_id
        row.node_roles = dict(node_roles)
        row.hire_state = "ready"
        row.hired_at = row.hired_at or now
        row.updated_at = now
        await session.commit()
        return row


async def mark_failed(database: Any, employee_id: str) -> None:
    async with database.get_session() as session:
        row = await session.get(Employee, employee_id)
        if row is None or row.hire_state == "ready":
            return
        row.hire_state = "failed"
        row.updated_at = _utcnow()
        await session.commit()


async def update_employee(database: Any, workflow_id: str, fields: Dict[str, Any]) -> Optional[Employee]:
    """Update hire data on an existing employee (a refined setup)."""
    values = _hire_fields(fields)
    async with database.get_session() as session:
        result = await session.execute(select(Employee).where(Employee.workflow_id == workflow_id))
        row = result.scalar_one_or_none()
        if row is None:
            return None
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = _utcnow()
        await session.commit()
        return row


async def delete_for_workflow(database: Any, workflow_id: str) -> int:
    """Remove the employee row of a deleted workflow. Returns rows removed."""
    async with database.get_session() as session:
        result = await session.execute(delete(Employee).where(Employee.workflow_id == workflow_id))
        await session.commit()
        return int(result.rowcount or 0)


__all__ = [
    "HIRE_FIELDS",
    "delete_for_workflow",
    "get_by_idempotency_key",
    "get_by_workflow",
    "list_by_workflow_ids",
    "mark_failed",
    "mark_ready",
    "reserve",
    "update_employee",
]
