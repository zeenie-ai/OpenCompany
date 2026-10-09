"""Approval rows (models.approvals.ApprovalRequest): create once per wait or
held call, read, move with a compare-and-swap, cancel, count, delete.

Functions take the ``Database`` and open their own session, like
services.employees.store. They never import ``nodes/``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import delete, func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from models.approvals import ApprovalDecision, ApprovalRequest
from services.approvals.contract import FINAL_STATUSES

#: Fields a row may be created with.
CREATE_FIELDS = frozenset(
    {
        "owner_id",
        "workflow_id",
        "node_id",
        "generation",
        "execution_id",
        "runtime",
        "kind",
        "status",
        "channel",
        "action",
        "recipient",
        "recipient_label",
        "subject",
        "draft_text",
        "final_text",
        "final_subject",
        "context_excerpt",
        "max_length",
        "details",
        "expires_at",
        "node_type",
        "tool_node_id",
        "tool_call_id",
        "agent_node_id",
        "run_id",
        "ui_part_id",
        "node_data",
        "args",
        "original_args",
        "body_field",
        "subject_field",
        "approved_by",
        "decided_at",
        "consumed_at",
        "outcome",
        "outcome_error",
        "outcome_at",
    }
)

#: Rows the owner may still act on, or that are on their way.
_OPEN_CANDIDATES = ("pending", "approved", "sending", "failed", "discarded")


class ApprovalConflict(ValueError):
    """The row moved first (someone else decided, or it expired)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(moment: Optional[datetime]) -> Optional[datetime]:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


async def get(database: Any, approval_id: str) -> Optional[ApprovalRequest]:
    async with database.get_session() as session:
        return await session.get(ApprovalRequest, approval_id)


async def get_by_key(database: Any, idempotency_key: str) -> Optional[ApprovalRequest]:
    async with database.get_session() as session:
        result = await session.execute(select(ApprovalRequest).where(ApprovalRequest.idempotency_key == idempotency_key))
        return result.scalar_one_or_none()


async def get_or_create(database: Any, *, idempotency_key: str, fields: Dict[str, Any]) -> Tuple[ApprovalRequest, bool]:
    """The wait's (or the held call's) row: created the first time, found on
    every retry."""
    unknown = set(fields) - CREATE_FIELDS
    if unknown:
        raise ValueError(f"not approval fields: {sorted(unknown)}")
    existing = await get_by_key(database, idempotency_key)
    if existing is not None:
        return existing, False
    values = {"status": "pending", **fields}
    row = ApprovalRequest(id=uuid.uuid4().hex, idempotency_key=idempotency_key, **values)
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


async def transition(
    database: Any,
    approval_id: str,
    *,
    expected_revision: int,
    from_statuses: Sequence[str],
    status: str,
    values: Optional[Dict[str, Any]] = None,
    decision: Optional[Tuple[str, str, str]] = None,
) -> ApprovalRequest:
    """Move a row that is in one of ``from_statuses`` at ``expected_revision``
    to ``status``, if nobody moved it first. ``decision`` (``(decision,
    decision_key, actor)``) is recorded in the same transaction, so a
    decision is kept exactly when it took effect."""
    now = _utcnow()
    changes = {"status": status, "revision": expected_revision + 1, "updated_at": now, **(values or {})}
    async with database.get_session() as session:
        result = await session.execute(
            update(ApprovalRequest)
            .where(
                ApprovalRequest.id == approval_id,
                ApprovalRequest.revision == expected_revision,
                ApprovalRequest.status.in_(tuple(from_statuses)),
            )
            .values(**changes)
        )
        if not result.rowcount:
            await session.rollback()
            raise ApprovalConflict("approval_conflict")
        if decision is not None:
            kind, key, actor = decision
            session.add(ApprovalDecision(approval_id=approval_id, decision=kind, decision_key=key, actor=actor, revision=expected_revision + 1))
        try:
            await session.commit()
        except IntegrityError:
            # The same decision key, recorded by a concurrent request first.
            await session.rollback()
            raise ApprovalConflict("approval_conflict")
    row = await get(database, approval_id)
    assert row is not None
    return row


async def settle(
    database: Any,
    approval_id: str,
    *,
    expected_revision: int,
    status: str,
    values: Optional[Dict[str, Any]] = None,
) -> ApprovalRequest:
    """Move a pending row to a final status (expired, cancelled), if nobody
    moved it first."""
    if status not in FINAL_STATUSES:
        raise ValueError(f"not a final approval status: {status}")
    return await transition(
        database,
        approval_id,
        expected_revision=expected_revision,
        from_statuses=("pending",),
        status=status,
        values={"decided_at": _utcnow(), **(values or {})},
    )


async def find_decision(database: Any, approval_id: str, decision_key: str) -> Optional[ApprovalDecision]:
    async with database.get_session() as session:
        result = await session.execute(
            select(ApprovalDecision).where(ApprovalDecision.approval_id == approval_id, ApprovalDecision.decision_key == decision_key)
        )
        return result.scalar_one_or_none()


def is_open(row: ApprovalRequest, now: Optional[datetime] = None) -> bool:
    """Still the owner's to act on, or on its way: waiting, sent but
    undoable, sending, failed (to retry), or discarded and restorable."""
    now = now or _utcnow()
    if row.status in ("pending", "sending", "failed"):
        return True
    if row.status == "approved":
        return row.consumed_at is None
    if row.status == "discarded":
        restore = aware(row.restore_until)
        return restore is not None and restore > now
    return False


def cancellable(row: ApprovalRequest, now: Optional[datetime] = None, *, failed: bool = False) -> bool:
    """What a Reset, a branch or a deleted run cancels: everything open
    except a send already under way and a failure (nothing left to stop),
    unless ``failed``: clearing a conversation ends its failures too."""
    if row.status == "failed":
        return failed
    return row.status != "sending" and is_open(row, now)


async def cancel_open(
    database: Any,
    *,
    workflow_id: str,
    generation: Optional[int] = None,
    run_ids: Optional[Iterable[str]] = None,
    failed: bool = False,
) -> List[ApprovalRequest]:
    """Cancel a workflow's drafts that still wait (all generations, one, or
    those of some chat runs): waiting, approved but not handed on yet, or
    discarded and restorable; with ``failed``, failed sends as well."""
    statuses = ("pending", "approved", "discarded", "failed") if failed else ("pending", "approved", "discarded")
    async with database.get_session() as session:
        query = select(ApprovalRequest).where(
            ApprovalRequest.workflow_id == workflow_id,
            ApprovalRequest.status.in_(statuses),
        )
        if generation is not None:
            query = query.where(ApprovalRequest.generation == generation)
        if run_ids is not None:
            ids = [run_id for run_id in run_ids if run_id]
            if not ids:
                return []
            query = query.where(ApprovalRequest.run_id.in_(ids))
        rows = list((await session.execute(query)).scalars().all())
    now = _utcnow()
    cancelled: List[ApprovalRequest] = []
    for row in rows:
        if not cancellable(row, now, failed=failed):
            continue
        try:
            cancelled.append(
                await transition(
                    database,
                    row.id,
                    expected_revision=row.revision,
                    from_statuses=(row.status,),
                    status="cancelled",
                    values={"decided_at": now, "grace_until": None, "restore_until": None},
                )
            )
        except ApprovalConflict:
            continue
    return cancelled


async def cancel_pending(database: Any, *, workflow_id: str, generation: Optional[int] = None) -> List[ApprovalRequest]:
    """Older name for :func:`cancel_open`."""
    return await cancel_open(database, workflow_id=workflow_id, generation=generation)


async def list_approvals(
    database: Any,
    *,
    owner_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    status: Optional[str] = None,
    statuses: Optional[Sequence[str]] = None,
    run_id: Optional[str] = None,
    kind: Optional[str] = None,
    ids: Optional[Sequence[str]] = None,
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
    if statuses is not None:
        query = query.where(ApprovalRequest.status.in_(tuple(statuses)))
    if run_id is not None:
        query = query.where(ApprovalRequest.run_id == run_id)
    if kind is not None:
        query = query.where(or_(ApprovalRequest.kind == kind, ApprovalRequest.kind.is_(None)) if kind == "gate" else ApprovalRequest.kind == kind)
    if ids is not None:
        query = query.where(ApprovalRequest.id.in_(tuple(ids)))
    query = query.order_by(ApprovalRequest.created_at.desc()).limit(max(1, min(int(limit), 100)))
    async with database.get_session() as session:
        return list((await session.execute(query)).scalars().all())


async def list_open(database: Any, *, owner_id: Optional[str] = None, workflow_id: Optional[str] = None, limit: int = 50) -> List[ApprovalRequest]:
    """What the owner may still act on (``is_open``), newest first."""
    rows = await list_approvals(database, owner_id=owner_id, workflow_id=workflow_id, statuses=_OPEN_CANDIDATES, limit=100)
    now = _utcnow()
    return [row for row in rows if is_open(row, now)][: max(1, min(int(limit), 100))]


async def pending_counts(database: Any, workflow_ids: Iterable[str]) -> Dict[str, int]:
    """Drafts waiting for the owner, per workflow."""
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


async def list_unfinished_sends(database: Any) -> List[ApprovalRequest]:
    """Approved held calls not handed on, and sends that never reported."""
    async with database.get_session() as session:
        result = await session.execute(
            select(ApprovalRequest).where(ApprovalRequest.kind == "tool_call", ApprovalRequest.status.in_(("approved", "sending")))
        )
        return list(result.scalars().all())


async def delete_for_workflow(database: Any, workflow_id: str) -> int:
    async with database.get_session() as session:
        ids = select(ApprovalRequest.id).where(ApprovalRequest.workflow_id == workflow_id)
        await session.execute(delete(ApprovalDecision).where(ApprovalDecision.approval_id.in_(ids)))
        result = await session.execute(delete(ApprovalRequest).where(ApprovalRequest.workflow_id == workflow_id))
        await session.commit()
        return int(result.rowcount or 0)


def _iso(moment: Optional[datetime]) -> Optional[str]:
    moment = aware(moment)
    return moment.isoformat() if moment is not None else None


def _outcome_labels(row: ApprovalRequest) -> Tuple[str, str]:
    """What the card says once it went, and when it did not: a held tool
    call's plugin words it (its ``approval`` spec); a gate's draft is a
    message."""
    from services.node_registry import get_node_class
    from services.plugin.approval import MESSAGE_OUTCOME, approval_spec

    spec = approval_spec(get_node_class(row.node_type)) if row.kind == "tool_call" and row.node_type else None
    return spec.outcome_labels if spec is not None else MESSAGE_OUTCOME


def summary(row: ApprovalRequest, *, deployment_state: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """What a card shows for one row (``ApprovalSummary``,
    docs-internal/chat_protocol.md)."""
    now = now or _utcnow()
    decided = row.status in ("approved", "sending", "sent", "failed")
    kind = row.kind or "gate"
    out: Dict[str, Any] = {
        "approval_id": row.id,
        "workflow_id": row.workflow_id,
        "node_id": row.node_id,
        "kind": kind,
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
        "editable": row.status == "pending" and (kind == "gate" or bool(row.body_field)),
        "details": [dict(item) for item in (row.details or []) if isinstance(item, dict)],
    }
    sent, failed = _outcome_labels(row)
    out["outcome_labels"] = {"sent": sent, "failed": failed}
    if row.action:
        out["action"] = row.action
    subject = row.final_subject if decided and row.final_subject else row.subject
    if subject:
        out["subject"] = subject
    if row.context_excerpt:
        out["context_excerpt"] = row.context_excerpt
    if row.approved_by:
        out["approved_by"] = row.approved_by
    if row.edited:
        out["edited"] = True
    grace = aware(row.grace_until)
    if row.status == "approved" and row.consumed_at is None and grace is not None:
        out["undo_until"] = grace.isoformat()
    restore = aware(row.restore_until)
    if row.status == "discarded" and restore is not None and restore > now:
        out["restore_until"] = restore.isoformat()
    if row.consumed_at is not None:
        out["consumed_at"] = _iso(row.consumed_at)
    if row.outcome:
        outcome: Dict[str, Any] = {"certainty": row.outcome}
        if row.outcome_error:
            outcome["error"] = row.outcome_error
        if row.outcome_at:
            outcome["at"] = _iso(row.outcome_at)
        out["outcome"] = outcome
    for key in ("run_id", "tool_call_id", "ui_part_id", "agent_node_id"):
        value = getattr(row, key, None)
        if value:
            out[key] = value
    if deployment_state:
        out["deployment_state"] = deployment_state
    return out


__all__ = [
    "ApprovalConflict",
    "aware",
    "cancel_open",
    "cancel_pending",
    "cancellable",
    "delete_for_workflow",
    "find_decision",
    "get",
    "get_by_key",
    "get_or_create",
    "is_open",
    "list_approvals",
    "list_open",
    "list_pending",
    "list_unfinished_sends",
    "pending_counts",
    "settle",
    "summary",
    "transition",
]
