"""The owner's side of approvals: see drafts, decide them, and the Ask first
rule (docs-internal/chat_protocol.md, "Approvals").

``list_approvals {workflow_id?, status?, run_id?, kind?, limit <= 100}`` ->
``{approvals: ApprovalSummary[], counts: {workflow_id: pending}, server_time}``.
``status`` is a row status, ``open`` (what the owner may still act on, or is
on its way) or ``recent`` (everything, newest first); default ``pending``.

``get_approvals {approval_ids: [...]}`` -> ``{approvals, server_time}``.

``decide_approval {approval_id, decision, decision_key, text?, subject?,
confirm?, note?}`` -> ``{approval, idempotent?, will_send_on_resume}``.
Decisions: ``send``, ``undo``, ``discard`` (with the owner's ``note``),
``restore``, ``retry``, ``dismiss`` (services/approvals/decisions.py).
Sending a held tool call starts its send (services/approvals/execution.py),
unless its agent's turn waits on it (``runtime: turn``) and runs it itself.
Errors: ``already_decided``,
``expired``, ``cancelled``, ``too_late``, ``confirm_required``,
``approval_conflict``, ``send_unavailable``, ``not_found``,
``invalid_request``.

``get_ask_first {workflow_id}`` -> ``{ask_first, mode, revision}`` (both
null for a workflow with no rule). ``set_ask_first {workflow_id, ask_first |
mode, expected_revision?}`` -> ``{ask_first, mode, revision, replies_gated,
needs_apply}``; ``rule_conflict`` when it moved. ``mode`` is ``outside``,
``everything`` or ``money_only`` (services/approvals/rules.py): setting it
keeps Ask first in step, and setting Ask first makes the mode follow it.

Only the owner's own drafts and workflows are visible; any other id reads as
not found.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import WebSocket

from core.logging import get_logger
from services.approvals import decisions, execution, rules, store, waiter
from services.approvals.contract import APPROVAL_KINDS
from services.approvals.listeners import change_of, notify_approval_changed
from services.approvals.reconcile import reconcile_once
from services.authz.ws_surface import execution_principal
from services.plugin.ws import ws_response

logger = get_logger(__name__)

MAX_LIST = 100
_ROW_STATUSES = ("pending", "approved", "sending", "sent", "failed", "discarded", "expired", "cancelled")
#: The lifecycle stage each decision announces.
_STAGES = {"send": "decided", "discard": "decided", "undo": "undone", "restore": "restored", "retry": "decided", "dismiss": "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _deployment_state(database: Any, workflow_id: str) -> str | None:
    try:
        control = await database.get_latest_workflow_control(workflow_id)
    except Exception:
        return None
    return getattr(control, "status", None)


async def _summaries(database: Any, rows: List[Any]) -> List[Dict[str, Any]]:
    states: Dict[str, Any] = {}
    out = []
    for row in rows:
        if row.workflow_id not in states:
            states[row.workflow_id] = await _deployment_state(database, row.workflow_id)
        out.append(store.summary(row, deployment_state=states[row.workflow_id]))
    return out


def _limit(data: Dict[str, Any]) -> int:
    try:
        return max(1, min(int(data.get("limit") or 50), MAX_LIST))
    except (TypeError, ValueError):
        return 50


@ws_response
async def handle_list_approvals(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    database = container.database()
    await reconcile_once(database)
    owner = execution_principal(data, websocket)
    workflow_id = str(data.get("workflow_id") or "").strip() or None
    run_id = str(data.get("run_id") or "").strip() or None
    kind = data.get("kind") if data.get("kind") in APPROVAL_KINDS else None
    status = data.get("status")
    limit = _limit(data)
    if status == "open":
        rows = await store.list_open(database, owner_id=owner, workflow_id=workflow_id, limit=limit)
        rows = [row for row in rows if (run_id is None or row.run_id == run_id) and (kind is None or (row.kind or "gate") == kind)]
    else:
        filters: Dict[str, Any] = {}
        if status in _ROW_STATUSES:
            filters["status"] = status
        elif status != "recent":
            filters["status"] = "pending"
        rows = await store.list_approvals(database, owner_id=owner, workflow_id=workflow_id, run_id=run_id, kind=kind, limit=limit, **filters)
    counts: Dict[str, int] = {}
    for row in rows:
        if row.status == "pending":
            counts[row.workflow_id] = counts.get(row.workflow_id, 0) + 1
    return {"success": True, "approvals": await _summaries(database, rows), "counts": counts, "server_time": _now()}


@ws_response
async def handle_get_approvals(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    ids = data.get("approval_ids")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_LIST:
        return {"success": False, "error": "invalid_request"}
    database = container.database()
    owner = execution_principal(data, websocket)
    rows = await store.list_approvals(database, owner_id=owner, ids=[str(item) for item in ids], limit=MAX_LIST)
    return {"success": True, "approvals": await _summaries(database, rows), "server_time": _now()}


_RESTORED_FIELDS = (
    "grace_until",
    "decided_at",
    "approved_by",
    "final_text",
    "final_subject",
    "edited",
    "args",
    "node_data",
    "claim_token",
    "consumed_at",
    "outcome",
    "outcome_error",
    "outcome_at",
)


async def _start_or_revert(database: Any, row: Any, original: Any) -> Any:
    """Start a held call's send; put the row back as it was when nothing
    can send it."""
    try:
        await execution.start_send(row)
        return row
    except Exception as exc:
        logger.warning("Could not start a send", approval_id=row.id, error=str(exc))
        try:
            reverted = await store.transition(
                database,
                row.id,
                expected_revision=row.revision,
                from_statuses=("approved",),
                status=original.status,
                values={name: getattr(original, name) for name in _RESTORED_FIELDS},
            )
            await notify_approval_changed(change_of(reverted, "undone"))
        except store.ApprovalConflict:
            pass
        raise execution.SendUnavailable("send_unavailable") from exc


@ws_response
async def handle_decide_approval(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    approval_id = str(data.get("approval_id") or "").strip()
    decision = data.get("decision")
    decision_key = str(data.get("decision_key") or "").strip()[:128]
    if not approval_id or decision not in decisions.DECISIONS or not decision_key:
        return {"success": False, "error": "invalid_request"}
    database = container.database()
    owner = execution_principal(data, websocket)
    row = await store.get(database, approval_id)
    if row is None or row.owner_id != owner:
        return {"success": False, "error": "not_found", "approval_id": approval_id}
    try:
        decided, idempotent = await decisions.apply(
            database,
            row,
            decision=decision,
            decision_key=decision_key,
            actor=owner,
            text=data.get("text"),
            subject=data.get("subject"),
            confirm=bool(data.get("confirm")),
            note=data.get("note"),
        )
    except decisions.DecisionRefused as refused:
        current = await store.get(database, approval_id) or row
        out: Dict[str, Any] = {"success": False, "error": refused.code, "approval": store.summary(current)}
        if refused.detail:
            out["detail"] = refused.detail
        return out
    if idempotent:
        return {"success": True, "idempotent": True, "approval": store.summary(decided), "will_send_on_resume": False}
    waiter.notify(decided.id)
    await notify_approval_changed(change_of(decided, _STAGES.get(decision, "decided")))
    if decided.status == "discarded":
        await execution.tell_employee(database, decided)
    if (decided.kind or "gate") == "tool_call" and decision in ("send", "retry") and decided.runtime != "turn":
        try:
            decided = await _start_or_revert(database, decided, row)
        except execution.SendUnavailable:
            current = await store.get(database, approval_id) or decided
            return {
                "success": False,
                "error": "send_unavailable",
                "detail": "Nothing can send it right now. Try again in a moment.",
                "approval": store.summary(current),
            }
    state = await _deployment_state(database, decided.workflow_id)
    logger.info("Draft decided", approval_id=decided.id, workflow_id=decided.workflow_id, decision=decision, status=decided.status)
    return {
        "success": True,
        "approval": store.summary(decided, deployment_state=state),
        # A paused employee's run holds the approved draft until Resume.
        "will_send_on_resume": ((decided.kind or "gate") == "gate" or decided.runtime == "turn")
        and decided.status == "approved"
        and state in ("paused", "pausing"),
    }


async def _authorize_workflow(database: Any, websocket: Any, workflow_id: str) -> bool:
    from services.chat.access import ChatAccessDenied, authorize_session

    try:
        await authorize_session(database, websocket, workflow_id)
    except ChatAccessDenied:
        return False
    return True


@ws_response
async def handle_get_ask_first(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    workflow_id = str(data.get("workflow_id") or "").strip()
    if not workflow_id:
        return {"success": False, "error": "invalid_request"}
    database = container.database()
    if not await _authorize_workflow(database, websocket, workflow_id):
        return {"success": False, "error": "not_found"}
    rule = await rules.get_rule(database, workflow_id)
    return {
        "success": True,
        "workflow_id": workflow_id,
        "ask_first": None if rule is None else bool(rule.ask_first),
        "mode": None if rule is None else rules.mode_of(rule),
        "revision": 0 if rule is None else rule.revision,
    }


@ws_response
async def handle_set_ask_first(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    workflow_id = str(data.get("workflow_id") or "").strip()
    value = data.get("ask_first")
    mode = data.get("mode")
    known_mode = isinstance(mode, str) and mode in rules.ASKS
    if not workflow_id or (mode is None and not isinstance(value, bool)) or (mode is not None and not known_mode):
        return {"success": False, "error": "invalid_request"}
    expected = data.get("expected_revision")
    if expected is not None and not isinstance(expected, int):
        return {"success": False, "error": "invalid_request"}
    database = container.database()
    if not await _authorize_workflow(database, websocket, workflow_id):
        return {"success": False, "error": "not_found"}
    try:
        if mode is not None:
            rule = await rules.set_mode(database, workflow_id, mode, expected_revision=expected)
        else:
            rule = await rules.set_ask_first(database, workflow_id, value, expected_revision=expected)
    except rules.RuleConflict as conflict:
        current = conflict.rule
        return {
            "success": False,
            "error": "rule_conflict",
            "ask_first": None if current is None else bool(current.ask_first),
            "mode": None if current is None else rules.mode_of(current),
            "revision": 0 if current is None else current.revision,
        }
    effects = await rules.notify_rule_changed(database, workflow_id, rule)
    logger.info("Ask first changed", workflow_id=workflow_id, ask_first=rule.ask_first, mode=rule.mode)
    return {
        "success": True,
        "workflow_id": workflow_id,
        "ask_first": bool(rule.ask_first),
        "mode": rules.mode_of(rule),
        "revision": rule.revision,
        "replies_gated": bool(effects.get("replies_gated", False)),
        "needs_apply": bool(effects.get("needs_apply", False)),
    }


async def on_workflow_deleted(database: Any, workflow_id: str) -> None:
    removed = await store.delete_for_workflow(database, workflow_id)
    await rules.delete_rule(database, workflow_id)
    if removed:
        logger.info("Removed a deleted workflow's drafts", workflow_id=workflow_id, count=removed)


WS_HANDLERS: Dict[str, Any] = {
    "list_approvals": handle_list_approvals,
    "get_approvals": handle_get_approvals,
    "decide_approval": handle_decide_approval,
    "get_ask_first": handle_get_ask_first,
    "set_ask_first": handle_set_ask_first,
}


__all__ = [
    "WS_HANDLERS",
    "handle_decide_approval",
    "handle_get_approvals",
    "handle_get_ask_first",
    "handle_list_approvals",
    "handle_set_ask_first",
    "on_workflow_deleted",
]
