"""Approval rows (models.approvals.ApprovalRequest): create once per wait,
read, move with a compare-and-swap, cancel, count, delete.

Functions take the ``Database`` and open their own session, like
services.employees.store. They never import ``nodes/``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from models.approvals import ApprovalRequest
from services.approvals.contract import FINAL_STATUSES

#: Fields a gate may set when it creates its row.
CREATE_FIELDS = frozenset(
    {
        "owner_id",
        "workflow_id",
        "node_id",
        "generation",
        "execution_id",
        "runtime",
        "channel",
        "recipient",
        "recipient_label",
        "subject",
        "draft_text",
        "context_excerpt",
        "max_length",
        "expires_at",
    }
)


class ApprovalConflict(ValueError):
    """The row moved first (someone else decided, or it expired)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get(database: Any, approval_id: str) -> Optional[ApprovalRequest]:
    async with database.get_session() as session:
        return await session.get(ApprovalRequest, approval_id)


async def get_by_key(database: Any, idempotency_key: str) -> Optional[ApprovalRequest]:
    async with database.get_session() as session:
        result = await session.execute(select(ApprovalRequest).where(ApprovalRequest.idempotency_key == idempotency_key))
        return result.scalar_one_or_none()


async def get_or_create(database: Any, *, idempotency_key: str, fields: Dict[str, Any]) -> Tuple[ApprovalRequest, bool]:
    """The wait's row: created the first time, found on every retry."""
    unknown = set(fields) - CREATE_FIELDS
    if unknown:
        raise ValueError(f"not approval fields: {sorted(unknown)}")
    existing = await get_by_key(database, idempotency_key)
    if existing is not None:
        return existing, False
    row = ApprovalRequest(id=uuid.uuid4().hex, idempotency_key=idempotency_key, status="pending", **fields)
    try:
        async with database.get_session() as session:
            session.add(row)
            await session.commit()
        return row, True
    except IntegrityError:
        existing = await get_by_key(database, idempotency_key)
        if existing is None:
            raise
        return existing, False


async def settle(
    database: Any,
    approval_id: str,
    *,
    expected_revision: int,
    status: str,
    values: Optional[Dict[str, Any]] = None,
) -> ApprovalRequest:
    """Move a pending row to a final status, if nobody moved it first."""
    if status not in FINAL_STATUSES:
        raise ValueError(f"not a final approval status: {status}")
    now = _utcnow()
    changes = {"status": status, "revision": expected_revision + 1, "updated_at": now, "decided_at": now, **(values or {})}
    async with database.get_session() as session:
        result = await session.execute(
            update(ApprovalRequest)
            .where(
                ApprovalRequest.id == approval_id,
                ApprovalRequest.revision == expected_revision,
                ApprovalRequest.status == "pending",
            )
            .values(**changes)
        )
        await session.commit()
        if not result.rowcount:
            raise ApprovalConflict("approval_conflict")
    row = await get(database, approval_id)
    assert row is not None
    return row


async def cancel_pending(database: Any, *, workflow_id: str, generation: Optional[int] = None) -> List[ApprovalRequest]:
    """Cancel a workflow's waiting drafts (all generations, or one)."""
    async with database.get_session() as session:
        query = select(ApprovalRequest).where(ApprovalRequest.workflow_id == workflow_id, ApprovalRequest.status == "pending")
        if generation is not None:
            query = query.where(ApprovalRequest.generation == generation)
        rows = list((await session.execute(query)).scalars().all())
    cancelled: List[ApprovalRequest] = []
    for row in rows:
        try:
            cancelled.append(await settle(database, row.id, expected_revision=row.revision, status="cancelled"))
        except ApprovalConflict:
            continue
    return cancelled


async def list_approvals(
    database: Any,
    *,
    owner_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[ApprovalRequest]:
    """Newest first."""
    query = select(ApprovalRequest)
    if owner_id is not None:
        query = query.where(ApprovalRequest.owner_id == owner_id)
    if workflow_id is not None:
        query = query.where(ApprovalRequest.workflow_id == workflow_id)
    if status is not None:
        query = query.where(ApprovalRequest.status == status)
    query = query.order_by(ApprovalRequest.created_at.desc()).limit(max(1, min(int(limit), 100)))
    async with database.get_session() as session:
        return list((await session.execute(query)).scalars().all())


async def pending_counts(database: Any, workflow_ids: Iterable[str]) -> Dict[str, int]:
    ids = [str(workflow_id) for workflow_id in workflow_ids if workflow_id]
    if not ids:
        return {}
    async with database.get_session() as session:
        result = await session.execute(
            select(ApprovalRequest.workflow_id, func.count())
            .where(ApprovalRequest.workflow_id.in_(ids), ApprovalRequest.status == "pending")
            .group_by(ApprovalRequest.workflow_id)
        )
        return {workflow_id: int(count) for workflow_id, count in result.all()}


async def list_pending(database: Any) -> List[ApprovalRequest]:
    async with database.get_session() as session:
        result = await session.execute(select(ApprovalRequest).where(ApprovalRequest.status == "pending"))
        return list(result.scalars().all())


async def delete_for_workflow(database: Any, workflow_id: str) -> int:
    async with database.get_session() as session:
        result = await session.execute(delete(ApprovalRequest).where(ApprovalRequest.workflow_id == workflow_id))
        await session.commit()
        return int(result.rowcount or 0)


def _iso(moment: Optional[datetime]) -> Optional[str]:
    if moment is None:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).isoformat()


def summary(row: ApprovalRequest, *, deployment_state: Optional[str] = None) -> Dict[str, Any]:
    """What the employee card shows for one draft (``ApprovalSummary``)."""
    decided = row.status == "approved"
    out: Dict[str, Any] = {
        "approval_id": row.id,
        "workflow_id": row.workflow_id,
        "node_id": row.node_id,
        "status": row.status,
        "channel": row.channel,
        "channel_label": row.channel,
        "recipient": row.recipient,
        "recipient_label": row.recipient_label or row.recipient,
        "body": (row.final_text if decided and row.final_text is not None else row.draft_text),
        "created_at": _iso(row.created_at),
        "expires_at": _iso(row.expires_at),
        "revision": row.revision,
        "max_length": row.max_length,
    }
    subject = row.final_subject if decided and row.final_subject else row.subject
    if subject:
        out["subject"] = subject
    if row.context_excerpt:
        out["context_excerpt"] = row.context_excerpt
    if deployment_state:
        out["deployment_state"] = deployment_state
    return out


__all__ = [
    "ApprovalConflict",
    "cancel_pending",
    "delete_for_workflow",
    "get",
    "get_by_key",
    "get_or_create",
    "list_approvals",
    "list_pending",
    "pending_counts",
    "settle",
    "summary",
]
