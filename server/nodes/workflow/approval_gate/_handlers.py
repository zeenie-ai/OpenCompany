"""The owner's side of the approval step: see drafts, send or discard them.

``list_approvals {workflow_id?, status?: "pending", limit <= 100}`` ->
``{approvals: ApprovalSummary[], counts: {workflow_id: pending}, server_time}``.

``decide_approval {approval_id, decision: "send" | "discard", text?,
subject?, decision_key}`` -> ``{approval, will_send_on_resume}``. A decision
is a compare-and-swap on the pending row, so two clicks (or two tabs)
settle it once; the same ``decision_key`` again returns the settled row.
An edited text must fit the channel (``max_length``). Errors:
``already_decided``, ``expired``, ``cancelled``, ``not_found``,
``invalid_request``.

Only the owner's own drafts are visible or decidable; any other id reads
as not found.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import WebSocket

from core.logging import get_logger
from services.approvals import store, waiter
from services.approvals.listeners import change_of, notify_approval_changed
from services.approvals.reconcile import reconcile_once
from services.authz.ws_surface import execution_principal
from services.plugin.ws import ws_response

logger = get_logger(__name__)

MAX_LIST = 100
_SETTLED_ERRORS = {"approved": "already_decided", "discarded": "already_decided", "expired": "expired", "cancelled": "cancelled"}


async def _deployment_state(database: Any, workflow_id: str) -> str | None:
    try:
        control = await database.get_latest_workflow_control(workflow_id)
    except Exception:
        return None
    return getattr(control, "status", None)


@ws_response
async def handle_list_approvals(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    database = container.database()
    await reconcile_once(database)
    owner = execution_principal(data, websocket)
    workflow_id = str(data.get("workflow_id") or "").strip() or None
    status = data.get("status")
    status = status if status in ("pending", "approved", "discarded", "expired", "cancelled") else "pending"
    try:
        limit = max(1, min(int(data.get("limit") or 50), MAX_LIST))
    except (TypeError, ValueError):
        limit = 50
    rows = await store.list_approvals(database, owner_id=owner, workflow_id=workflow_id, status=status, limit=limit)
    states: Dict[str, Any] = {}
    approvals = []
    for row in rows:
        if row.workflow_id not in states:
            states[row.workflow_id] = await _deployment_state(database, row.workflow_id)
        approvals.append(store.summary(row, deployment_state=states[row.workflow_id]))
    counts: Dict[str, int] = {}
    for row in rows:
        if row.status == "pending":
            counts[row.workflow_id] = counts.get(row.workflow_id, 0) + 1
    return {
        "success": True,
        "approvals": approvals,
        "counts": counts,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@ws_response
async def handle_decide_approval(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    approval_id = str(data.get("approval_id") or "").strip()
    decision = data.get("decision")
    decision_key = str(data.get("decision_key") or "").strip()[:128]
    if not approval_id or decision not in ("send", "discard") or not decision_key:
        return {"success": False, "error": "invalid_request"}
    database = container.database()
    owner = execution_principal(data, websocket)
    row = await store.get(database, approval_id)
    if row is None or row.owner_id != owner:
        return {"success": False, "error": "not_found", "approval_id": approval_id}
    if row.decision_key and row.decision_key == decision_key:
        return {"success": True, "idempotent": True, "approval": store.summary(row), "will_send_on_resume": False}
    if row.status != "pending":
        return {"success": False, "error": _SETTLED_ERRORS.get(row.status, "already_decided"), "approval": store.summary(row)}

    values: Dict[str, Any] = {"decision_key": decision_key}
    if decision == "send":
        text = data.get("text")
        final = row.draft_text if text is None else str(text).strip()
        if not final:
            return {"success": False, "error": "invalid_request", "detail": "The message is empty."}
        if len(final) > row.max_length:
            return {"success": False, "error": "invalid_request", "detail": f"Keep it under {row.max_length} characters."}
        subject = data.get("subject")
        final_subject = row.subject if subject is None else (str(subject).strip()[:500] or row.subject)
        values.update({"final_text": final, "final_subject": final_subject, "edited": final != row.draft_text})
        status = "approved"
    else:
        status = "discarded"
    try:
        settled = await store.settle(database, row.id, expected_revision=row.revision, status=status, values=values)
    except store.ApprovalConflict:
        current = await store.get(database, row.id) or row
        if current.decision_key == decision_key:
            return {"success": True, "idempotent": True, "approval": store.summary(current), "will_send_on_resume": False}
        return {"success": False, "error": _SETTLED_ERRORS.get(current.status, "already_decided"), "approval": store.summary(current)}
    waiter.notify(settled.id)
    await notify_approval_changed(change_of(settled, "decided"))
    state = await _deployment_state(database, settled.workflow_id)
    logger.info("Draft decided", approval_id=settled.id, workflow_id=settled.workflow_id, status=settled.status, edited=settled.edited)
    return {
        "success": True,
        "approval": store.summary(settled, deployment_state=state),
        # A paused employee's run holds the approved draft until Resume.
        "will_send_on_resume": settled.status == "approved" and state in ("paused", "pausing"),
    }


async def on_workflow_deleted(database: Any, workflow_id: str) -> None:
    removed = await store.delete_for_workflow(database, workflow_id)
    if removed:
        logger.info("Removed a deleted workflow's drafts", workflow_id=workflow_id, count=removed)


WS_HANDLERS: Dict[str, Any] = {
    "list_approvals": handle_list_approvals,
    "decide_approval": handle_decide_approval,
}


__all__ = ["WS_HANDLERS", "handle_decide_approval", "handle_list_approvals", "on_workflow_deleted"]
