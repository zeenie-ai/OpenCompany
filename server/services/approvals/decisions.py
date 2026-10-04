"""The owner's decisions on a draft (``decide_approval``).

- **send** (pending -> approved): the draft, or the owner's edit of its text
  and subject, goes after ``UNDO_SECONDS`` (``grace_until``). A gate lets it
  through then; a held tool call is sent then (``execution.py``).
- **undo** (approved -> pending): before ``grace_until``, and only while
  nothing has taken it on.
- **discard** (pending -> discarded): restorable until ``restore_until``
  (``RESTORE_SECONDS`` for a gate's draft, whose run waits that long; until
  it expires for a held call, which nothing waits on).
- **restore** (discarded -> pending): before ``restore_until``.
- **retry** (failed -> approved): a held call whose send failed goes again
  after a new grace. When it broke off mid-way it may have gone out already,
  so a retry then needs ``confirm``.

Each decision is a compare-and-swap on the row's revision, recorded with its
``decision_key`` in the same transaction (``approval_decisions``): the same
key again returns the row as it is. Never imports ``nodes/``.

A pending row past its expiry ends ``expired`` (:func:`expire_if_due`) when
a decision reaches it, and a held call's row also when the chat watchdog
sweeps (``reconcile.expire_due``); whoever ends it announces it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from models.approvals import ApprovalRequest
from services.approvals import store
from services.approvals.contract import DEFAULT_TIMEOUT_HOURS, RESTORE_SECONDS, UNDO_SECONDS
from services.approvals.listeners import change_of, notify_approval_changed

DECISIONS = ("send", "undo", "discard", "restore", "retry")
#: Why a decision on a row that already moved on was refused.
SETTLED_ERRORS = {
    "approved": "already_decided",
    "sending": "already_decided",
    "sent": "already_decided",
    "failed": "already_decided",
    "discarded": "already_decided",
    "expired": "expired",
    "cancelled": "cancelled",
}


class DecisionRefused(ValueError):
    """A decision that does not apply to the row as it is."""

    def __init__(self, code: str, detail: Optional[str] = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _settled(row: ApprovalRequest) -> DecisionRefused:
    return DecisionRefused(SETTLED_ERRORS.get(row.status, "already_decided"))


async def expire_if_due(database: Any, row: ApprovalRequest, now: datetime) -> ApprovalRequest:
    """A pending row past its expiry ends ``expired``. The caller that ends
    it announces it, and tells the employee when it held a tool call; one
    another caller ended meanwhile comes back as it is."""
    expires = store.aware(row.expires_at)
    if row.status != "pending" or expires is None or expires > now:
        return row
    try:
        expired = await store.settle(database, row.id, expected_revision=row.revision, status="expired")
    except store.ApprovalConflict:
        return await store.get(database, row.id) or row
    await notify_approval_changed(change_of(expired, "expired"))
    if expired.kind == "tool_call":
        from services.approvals.execution import tell_employee

        await tell_employee(database, expired)
    return expired


def _send_values(row: ApprovalRequest, text: Any, subject: Any, now: datetime) -> Dict[str, Any]:
    final = row.draft_text if text is None else str(text).strip()
    editable_body = (row.kind or "gate") == "gate" or bool(row.body_field)
    if text is not None and not editable_body:
        raise DecisionRefused("invalid_request", "This one cannot be edited.")
    if editable_body and not final:
        raise DecisionRefused("invalid_request", "The message is empty.")
    if len(final) > row.max_length:
        raise DecisionRefused("invalid_request", f"Keep it under {row.max_length} characters.")
    final_subject = row.subject if subject is None else (str(subject).strip()[:500] or row.subject)
    values: Dict[str, Any] = {
        "final_text": final,
        "final_subject": final_subject,
        "edited": final != row.draft_text or (final_subject or None) != (row.subject or None),
        "approved_by": "owner",
        "decided_at": now,
        "grace_until": now + timedelta(seconds=UNDO_SECONDS),
        "restore_until": None,
    }
    if (row.kind or "gate") == "tool_call":
        args = dict(row.args or {})
        node_data = dict(row.node_data or {})
        if row.body_field and text is not None:
            args[row.body_field] = final
            node_data[row.body_field] = final
        if row.subject_field and subject is not None and final_subject:
            args[row.subject_field] = final_subject
            node_data[row.subject_field] = final_subject
        values.update(args=args, node_data=node_data)
    return values


async def decide(
    database: Any,
    row: ApprovalRequest,
    *,
    decision: str,
    decision_key: str,
    actor: str = "owner",
    text: Any = None,
    subject: Any = None,
    confirm: bool = False,
    now: Optional[datetime] = None,
) -> Tuple[ApprovalRequest, bool]:
    """Apply a decision. Returns ``(row, idempotent)``; raises
    :class:`DecisionRefused` when it does not apply."""
    if decision not in DECISIONS or not decision_key:
        raise DecisionRefused("invalid_request")
    if await store.find_decision(database, row.id, decision_key) is not None:
        return (await store.get(database, row.id)) or row, True
    now = now or _utcnow()
    row = await expire_if_due(database, row, now)
    record = (decision, decision_key, actor)
    kind = row.kind or "gate"
    if decision == "send":
        if row.status != "pending":
            raise _settled(row)
        values = _send_values(row, text, subject, now)
        values["decision_key"] = decision_key
        moved = await _move(database, row, ("pending",), "approved", values, record)
    elif decision == "undo":
        grace = store.aware(row.grace_until)
        if row.status != "approved" or row.consumed_at is not None or row.claim_token or grace is None or grace <= now:
            raise DecisionRefused("too_late", "It has already gone.")
        values = {"grace_until": None, "decided_at": None, "approved_by": None, "final_text": None, "final_subject": None, "edited": False}
        moved = await _move(database, row, ("approved",), "pending", values, record)
    elif decision == "discard":
        if row.status != "pending":
            raise _settled(row)
        if kind == "gate":
            restore = now + timedelta(seconds=RESTORE_SECONDS)
        else:
            restore = store.aware(row.expires_at) or now + timedelta(hours=DEFAULT_TIMEOUT_HOURS)
        values = {"decided_at": now, "restore_until": restore, "decision_key": decision_key}
        moved = await _move(database, row, ("pending",), "discarded", values, record)
    elif decision == "restore":
        restore = store.aware(row.restore_until)
        if row.status != "discarded" or restore is None or restore <= now:
            raise DecisionRefused("too_late", "It can no longer be restored.")
        moved = await _move(database, row, ("discarded",), "pending", {"restore_until": None, "decided_at": None}, record)
    else:  # retry
        if kind != "tool_call" or row.status != "failed":
            raise DecisionRefused("invalid_request", "Only a send that failed can be tried again.")
        if row.outcome == "unknown" and not confirm:
            raise DecisionRefused("confirm_required", "It may have gone out already. Send it again anyway?")
        values = {
            "grace_until": now + timedelta(seconds=UNDO_SECONDS),
            "claim_token": None,
            "consumed_at": None,
            "outcome": None,
            "outcome_error": None,
            "outcome_at": None,
            "decided_at": now,
            "approved_by": "owner",
        }
        moved = await _move(database, row, ("failed",), "approved", values, record)
    return moved, False


async def _move(database: Any, row: ApprovalRequest, from_statuses, status: str, values: Dict[str, Any], record) -> ApprovalRequest:
    try:
        return await store.transition(
            database, row.id, expected_revision=row.revision, from_statuses=from_statuses, status=status, values=values, decision=record
        )
    except store.ApprovalConflict:
        current = await store.get(database, row.id) or row
        found = await store.find_decision(database, row.id, record[1])
        if found is not None:
            # A concurrent request with the same key got there first.
            raise _Idempotent(current)
        if current.status == row.status:
            raise DecisionRefused("approval_conflict", "It changed meanwhile; look again.")
        raise _settled(current)


class _Idempotent(Exception):
    def __init__(self, row: ApprovalRequest) -> None:
        super().__init__("idempotent")
        self.row = row


async def apply(database: Any, row: ApprovalRequest, **kwargs: Any) -> Tuple[ApprovalRequest, bool]:
    """:func:`decide`, with a same-key race answered as a repeat."""
    try:
        return await decide(database, row, **kwargs)
    except _Idempotent as same:
        return same.row, True


__all__ = ["DECISIONS", "DecisionRefused", "SETTLED_ERRORS", "apply", "decide"]
