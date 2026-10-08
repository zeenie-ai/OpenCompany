"""Idempotent history projection. Temporal remains the execution authority."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, or_, and_, select, update
from sqlalchemy.exc import IntegrityError

from models.workspace_tasks import WorkspaceTaskRecord

TERMINAL = ("completed", "failed", "cancelled")
RETENTION_DAYS = 35


def safe_result(result: Any) -> dict:
    """Persist the public answer only, never a tool transcript/configuration."""
    if not isinstance(result, dict):
        return {}
    answer = result.get("result") if isinstance(result.get("result"), dict) else result
    response = answer.get("response")
    safe = {"response": response[:16000]} if isinstance(response, str) else {}
    error_type = answer.get("error_type")
    if error_type in {"BrowserBusy", "BrowserTaskFailed"}:
        safe.update(error_type=error_type, error=("This browser is assigned to another task. Wait for that task to finish."
                    if error_type == "BrowserBusy" else "The browser task failed. Check browser status and credentials."))
    # Only typed relative artifact references, not arbitrary result dictionaries.
    artifacts = answer.get("artifacts")
    if isinstance(artifacts, list):
        from pathlib import PurePosixPath, PureWindowsPath
        safe["artifacts"] = [
            {key: ref[key] for key in ("path", "mime_type", "name", "filename", "workflow_id") if key in ref}
            for ref in artifacts[:32]
            if isinstance(ref, dict) and isinstance(ref.get("path"), str)
            and not PureWindowsPath(ref["path"]).drive
            and not PurePosixPath(ref["path"]).is_absolute()
            and ".." not in PurePosixPath(ref["path"].replace("\\", "/")).parts
        ]
        from urllib.parse import quote
        for ref in safe["artifacts"]:
            if isinstance(ref.get("workflow_id"), str) and ref["workflow_id"]:
                ref["url"] = f"/api/workspace/{quote(ref['workflow_id'], safe='')}/files/{quote(ref['path'], safe='/')}"
    return safe


async def admit(database, payload: dict) -> dict:
    """A unique invocation key makes Activity retries and transport retries safe."""
    values = payload["history_record"]
    async with database.get_session() as session:
        existing = await session.get(WorkspaceTaskRecord, values["invocation_id"])
        if existing:
            if existing.fingerprint != values["fingerprint"] or existing.principal != values["principal"]:
                raise ValueError("Workspace submission identity conflict")
            return {"status": existing.status}
        session.add(WorkspaceTaskRecord(**values))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = await session.get(WorkspaceTaskRecord, values["invocation_id"])
            if not existing or existing.fingerprint != values["fingerprint"] or existing.principal != values["principal"]:
                raise
            return {"status": existing.status}
    return {"status": "queued"}


async def transition(database, payload: dict) -> None:
    status = payload["status"]
    if status not in ("running", *TERMINAL):
        raise ValueError("Invalid Workspace lifecycle status")
    now = datetime.now(timezone.utc)
    values: dict[str, Any] = {"status": status, "updated_at": now}
    if status in TERMINAL:
        values.update(completed_at=now, result=safe_result(payload.get("result")))
    async with database.get_session() as session:
        # Compare-and-set: retries or delayed running updates never regress terminal rows.
        await session.execute(update(WorkspaceTaskRecord).where(
            WorkspaceTaskRecord.invocation_id == payload["invocation_id"],
            WorkspaceTaskRecord.status.not_in(TERMINAL),
        ).values(**values))
        await session.commit()


async def authorize_workflow(database, principal: str, workflow_id: str) -> dict:
    from services.plugin import NodeUserError
    saved = await database.get_workflow(workflow_id)
    if saved is None:
        raise NodeUserError("Workflow not found")
    graph = saved.data if hasattr(saved, "data") else saved.get("data", saved)
    if graph.get("owner_id") and graph["owner_id"] != principal:
        raise NodeUserError("Workflow access denied")
    return graph


def serialize(record: WorkspaceTaskRecord) -> dict:
    return {key: getattr(record, key) for key in (
        "invocation_id", "submission_id", "workflow_id", "node_id", "prompt", "status",
        "result", "created_at", "updated_at", "completed_at",
    )}


async def task_availability(database, owner_ids: list[str]) -> dict:
    import time
    if not owner_ids:
        return {"owner_available": True}
    from services.browser_owners import BrowserOwner
    async with database.get_session() as session:
        rows = list((await session.execute(select(BrowserOwner).where(BrowserOwner.owner_id.in_(owner_ids)))).scalars())
    available = len(rows) == len(set(owner_ids)) and all(row.heartbeat_at > time.time() - 35 for row in rows)
    return {"owner_available": available,
            **({"detail": "Browser unavailable — waiting for its owner."} if not available else {})}


async def list_tasks(database, principal: str, workflow_id: str, *, node_id: str | None = None,
                     cursor: str | None = None, limit: int = 20) -> dict:
    await authorize_workflow(database, principal, workflow_id)
    limit = max(1, min(100, limit))
    async with database.get_session() as session:
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        await session.execute(delete(WorkspaceTaskRecord).where(
            WorkspaceTaskRecord.workflow_id == workflow_id,
            WorkspaceTaskRecord.principal == principal,
            WorkspaceTaskRecord.completed_at < cutoff,
        ))
        await session.commit()
        conditions = [WorkspaceTaskRecord.workflow_id == workflow_id, WorkspaceTaskRecord.principal == principal]
        if node_id:
            conditions.append(WorkspaceTaskRecord.node_id == node_id)
        if cursor:
            marker = await session.get(WorkspaceTaskRecord, cursor)
            if not marker or marker.principal != principal or marker.workflow_id != workflow_id or (node_id and marker.node_id != node_id):
                from services.plugin import NodeUserError
                raise NodeUserError("Invalid task history cursor")
            conditions.append(or_(WorkspaceTaskRecord.created_at < marker.created_at,
                                  and_(WorkspaceTaskRecord.created_at == marker.created_at,
                                       WorkspaceTaskRecord.invocation_id < marker.invocation_id)))
        rows = list((await session.execute(select(WorkspaceTaskRecord).where(*conditions)
            .order_by(WorkspaceTaskRecord.created_at.desc(), WorkspaceTaskRecord.invocation_id.desc())
            .limit(limit + 1))).scalars())
        items = []
        for row in rows[:limit]:
            item = serialize(row)
            item.update(await task_availability(database, row.browser_owner_ids))
            items.append(item)
        return {"items": items, "next_cursor": rows[limit - 1].invocation_id if len(rows) > limit else None}


async def get_task(database, principal: str, workflow_id: str, invocation_id: str) -> WorkspaceTaskRecord | None:
    await authorize_workflow(database, principal, workflow_id)
    async with database.get_session() as session:
        record = await session.get(WorkspaceTaskRecord, invocation_id)
        return record if record and record.principal == principal and record.workflow_id == workflow_id else None
