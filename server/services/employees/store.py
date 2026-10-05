"""Employee rows (models.employees.Employee): reserve, finish, read, update,
delete.

Functions take the ``Database`` and open their own session, like
services.agent_context.conversation. They never import ``nodes/``.

The hire path is: ``reserve`` (a ``building`` row keyed by the owner's
idempotency key) -> save the workflow -> ``mark_ready`` with its id. A
retried Hire with the same key gets the same row back from ``reserve``;
``created`` tells the caller whether it is the first attempt. Parts added
to the graph later (Turn on Talk) join ``node_roles`` through
``merge_node_roles``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from core.logging import get_logger
from models.employees import Employee

logger = get_logger(__name__)

#: Columns ``reserve`` / ``update_employee`` may set from hire data.
HIRE_FIELDS = frozenset(
    {"role", "description", "job", "color_role", "apps", "unsupported_apps", "plan", "rules", "choices", "trigger", "llm", "builder_version", "team_plan"}
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
    row = Employee(id=uuid.uuid4().hex, owner_id=owner_id, idempotency_key=idempotency_key, payload_hash=payload_hash, hire_state="building", **values)
    try:
        async with database.get_session() as session:
            session.add(row)
            await session.commit()
        return row, True
    except IntegrityError:
        existing = await get_by_idempotency_key(database, owner_id, idempotency_key)
        if existing is None:
            raise
        return existing, False


async def claim_hire(database: Any, *, owner_id: str, idempotency_key: str, payload_hash: str, fields: Dict[str, Any]) -> Tuple[Employee, Optional[str]]:
    """Claim before reading an attempt. Only the current lease holder can commit.

    Allocate a stable workflow identity once, including when an attempt is
    recovered. BEGIN IMMEDIATE serializes stale-lease takeovers across workers.
    """
    workflow_id = await database.allocate_workflow_id()
    async with database.reserved_session() as session:
        result = await session.execute(select(Employee).where(Employee.owner_id == owner_id, Employee.idempotency_key == idempotency_key))
        row = result.scalar_one_or_none()
        now = _utcnow()
        if row is not None:
            lease = row.lease_until
            if lease is not None and lease.tzinfo is None:
                lease = lease.replace(tzinfo=timezone.utc)
            updated = row.updated_at.replace(tzinfo=timezone.utc) if row.updated_at and row.updated_at.tzinfo is None else row.updated_at
            legacy_busy = row.hire_state == "building" and not row.claim_token and updated and now - updated < timedelta(minutes=2)
            if row.payload_hash != payload_hash or row.hire_state == "ready" or legacy_busy or (row.claim_token and lease and lease > now):
                return row, None
        else:
            row = Employee(id=uuid.uuid4().hex, owner_id=owner_id, idempotency_key=idempotency_key, payload_hash=payload_hash, **_hire_fields(fields))
            session.add(row)
        row.workflow_id = row.workflow_id or workflow_id
        row.hire_state = "building"
        row.claim_token = uuid.uuid4().hex
        row.lease_until = now + timedelta(minutes=2)
        row.updated_at = now
        await session.commit()
        return row, row.claim_token


async def renew_claim(database: Any, employee_id: str, claim_token: str) -> bool:
    async with database.reserved_session() as session:
        row = await session.get(Employee, employee_id)
        if row is None or row.claim_token != claim_token or row.hire_state != "building":
            return False
        row.lease_until = _utcnow() + timedelta(minutes=2)
        await session.commit()
        return True


async def commit_hire(database: Any, employee_id: str, claim_token: str, *, name: str, description: str, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], parameters: Dict[str, Dict[str, Any]], node_roles: Dict[str, str], fields: Dict[str, Any]) -> str:
    """Graph, parameters, metadata, initial authority and activation in one commit."""
    from models.database import NodeParameter, Workflow
    from models.employees import EmployeeActivation, EmployeeGrant
    from services.workflow_migrations import normalize_workflow_graph
    from services.workflow_sanitizer import sanitize_workflow_graph
    from services.workflow_naming import slugify_name

    async with database.reserved_session() as session:
        row = await session.get(Employee, employee_id)
        lease = row.lease_until if row else None
        if lease is not None and lease.tzinfo is None:
            lease = lease.replace(tzinfo=timezone.utc)
        if row is None or row.claim_token != claim_token or row.hire_state != "building" or not lease or lease <= _utcnow():
            raise RuntimeError("hire_claim_lost")
        workflow_id = str(row.workflow_id)
        normalized = normalize_workflow_graph(workflow_id, nodes, edges, parameters)
        if normalized.state_imports:
            raise RuntimeError("new_employee_contains_legacy_state")
        graph = sanitize_workflow_graph({**normalized.graph_data(), "owner_id": row.owner_id})
        base = slugify_name(name)
        slug = f"{base}_1"
        suffix = 1
        while (await session.execute(select(Workflow.id).where(Workflow.slug == slug))).first():
            suffix += 1
            slug = f"{base[:55]}_{suffix}"
        session.add(Workflow(id=workflow_id, name=name, slug=slug, description=description or None, data=graph))
        for node_id, params in normalized.node_parameters.items():
            session.add(NodeParameter(node_id=node_id, parameters=dict(params or {})))
        row.node_roles = {role: normalized.aliases.get(node_id, node_id) for role, node_id in node_roles.items()}
        for key, value in _hire_fields(fields).items():
            if key == "team_plan":
                def canonical(item):
                    if isinstance(item, dict):
                        return {name: canonical(entry) for name, entry in item.items()}
                    if isinstance(item, list):
                        return [canonical(entry) for entry in item]
                    return normalized.aliases.get(item, item) if isinstance(item, str) else item
                value = canonical(value)
            setattr(row, key, value)
        row.hire_state = "ready"
        row.claim_token = None
        row.lease_until = None
        row.updated_at = row.hired_at = _utcnow()
        session.add(EmployeeActivation(id=f"hire:{row.id}", workflow_id=workflow_id, owner_id=row.owner_id))
        node_types = {node["id"]: node["type"] for node in normalized.nodes}
        for member in (row.team_plan or {}).get("members", []):
            for capability in member.get("capabilities", member.get("tools", [])):
                tool_id = capability if isinstance(capability, str) else capability.get("node_id", "")
                cap = node_types.get(tool_id, tool_id) if isinstance(capability, str) else capability.get("type", "")
                if cap:
                    session.add(EmployeeGrant(id=uuid.uuid4().hex, workflow_id=workflow_id, owner_id=row.owner_id, capability=cap, member_id=member.get("node_id", ""), limits={"initial_hire": True, "approved": True, "tool_node_id": tool_id, "parameters": normalized.node_parameters.get(tool_id, {})}))
        await session.commit()
        return workflow_id
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


async def mark_failed(database: Any, employee_id: str, claim_token: Optional[str] = None) -> None:
    async with database.reserved_session() as session:
        row = await session.get(Employee, employee_id)
        if row is None or row.hire_state == "ready" or (claim_token is not None and row.claim_token != claim_token):
            return
        row.hire_state = "failed"
        row.claim_token = None
        row.lease_until = None
        row.updated_at = _utcnow()
        await session.commit()


async def merge_node_roles(database: Any, workflow_id: str, roles: Dict[str, str]) -> Optional[Employee]:
    """Add node roles to a hired employee (parts added after the hire, such
    as a talk line), keeping the others; one named again is replaced."""
    async with database.get_session() as session:
        result = await session.execute(select(Employee).where(Employee.workflow_id == workflow_id))
        row = result.scalar_one_or_none()
        if row is None:
            return None
        row.node_roles = {**(row.node_roles or {}), **roles}
        row.updated_at = _utcnow()
        await session.commit()
        return row


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


async def set_photo(database: Any, workflow_id: str, path: Optional[str]) -> Optional[Employee]:
    """Set or clear the photo an employee shows (a workspace path). None when
    the workflow has no employee row (one built in the editor)."""
    async with database.get_session() as session:
        result = await session.execute(select(Employee).where(Employee.workflow_id == workflow_id))
        row = result.scalar_one_or_none()
        if row is None:
            return None
        row.photo_path = path
        row.updated_at = _utcnow()
        await session.commit()
        return row


async def delete_for_workflow(database: Any, workflow_id: str) -> int:
    """Remove the employee row of a deleted workflow. Returns rows removed."""
    from models.employees import EmployeeActivation, EmployeeGrant, EmployeeJob, EmployeeApply, WorkflowQueuedEvent
    from models.employee_conversion import EmployeeConversion
    async with database.get_session() as session:
        result = await session.execute(delete(Employee).where(Employee.workflow_id == workflow_id))
        count = int(result.rowcount or 0)
        for model in (EmployeeActivation, EmployeeGrant, EmployeeJob, EmployeeApply, EmployeeConversion):
            await session.execute(delete(model).where(model.workflow_id == workflow_id))
        await session.execute(delete(WorkflowQueuedEvent).where(WorkflowQueuedEvent.controller_id.startswith(f"workflow-control-{workflow_id}-g")))
        await session.commit()
        return count


__all__ = [
    "HIRE_FIELDS",
    "delete_for_workflow",
    "get_by_idempotency_key",
    "get_by_workflow",
    "list_by_workflow_ids",
    "mark_failed",
    "mark_ready",
    "merge_node_roles",
    "reserve",
    "set_photo",
    "update_employee",
]
