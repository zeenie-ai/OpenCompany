"""Capability authority from owner requests, never public app messages."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from sqlmodel import select
from models.employees import EmployeeGrant


async def trusted_owner_request(database: Any, ctx: Any) -> bool:
    """A stored authenticated chat admission is the trust boundary."""
    from services.chat.stream import chat_run_id_of
    from services.chat.ledger import get_run
    from services.employees.store import get_by_workflow
    run_id = chat_run_id_of(ctx.raw)
    if not run_id or not ctx.workflow_id:
        return False
    run = await get_run(database, run_id)
    workflow = await database.get_workflow(str(ctx.workflow_id))
    employee = await get_by_workflow(database, str(ctx.workflow_id))
    owner = employee.owner_id if employee else (workflow.data or {}).get("owner_id", "owner") if workflow else None
    return bool(run and run.workflow_id == ctx.workflow_id and owner == ctx.user_id and run.kind in {"message", "action", "edit"})


async def require_builder_access(database: Any, ctx: Any, capability: str, *, member_id: str = "", account_id: str = "", limits: dict | None = None) -> dict:
    from services.employees.store import get_by_workflow
    from core.container import container
    if hasattr(container, "settings") and not getattr(container.settings(), "employee_capability_updates_enabled", True):
        return {"authorized": False, "required_access": [], "summary": "Changes to employee capabilities are currently turned off."}
    employee = await get_by_workflow(database, str(ctx.workflow_id))
    if not employee:
        # Existing Dev mode editor operations retain their prior scope.
        return {"authorized": True, "required_access": []}
    if not await trusted_owner_request(database, ctx):
        return {"authorized": False, "required_access": [], "summary": "Ask the owner in Talk to change this employee’s access."}
    limits = limits or {}
    configuration = limits.get("parameters") or limits.get("tool_parameters") or {}
    if not account_id and isinstance(configuration, dict):
        account_id = str(configuration.get("account_id") or configuration.get("credential_id") or configuration.get("sender_account") or "")
    # A request identity includes its complete scope; widening one creates
    # a different review request instead of reusing an earlier permission.
    scope = [employee.workflow_id, employee.owner_id, capability, member_id, account_id, limits]
    request_id = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    async with database.reserved_session() as session:
        row = await session.get(EmployeeGrant, request_id)
        if row is not None and row.revoked_at is None and row.limits.get("approved"):
            return {"authorized": True, "request_id": request_id, "required_access": []}
        if row is None:
            row = EmployeeGrant(id=request_id, workflow_id=employee.workflow_id, owner_id=employee.owner_id,
                capability=capability, member_id=member_id, account_id=account_id, limits={**limits, "approved": False})
            session.add(row)
            await session.commit()
    from services.node_registry import get_node_class
    cls = get_node_class(capability)
    app = getattr(cls, "display_name", capability.replace("_", " ")) if cls else capability.replace("_", " ")
    return {"authorized": False, "request_id": request_id,
        "required_access": [{"request_id": request_id, "app": app, "action": "help with their assigned responsibilities", "member_id": member_id}],
        "summary": f"Allow {employee.role or 'this employee'}’s team to use {app} for their assigned responsibilities?"}


async def list_access(database: Any, workflow_id: str, owner_id: str) -> list[dict]:
    async with database.get_session() as session:
        rows = (await session.execute(select(EmployeeGrant).where(EmployeeGrant.workflow_id == workflow_id, EmployeeGrant.owner_id == owner_id))).scalars().all()
        from services.node_registry import get_node_class
        return [{"id": row.id, "capability": row.capability, "member_id": row.member_id,
                 "parent_grant_id": row.limits.get("parent_grant_id"),
                 "scope_apps": [getattr(get_node_class(kind), "display_name", kind.replace("_", " ")) for kind in row.limits.get("tool_types", [])],
                 "app": getattr(get_node_class(row.capability), "display_name", row.capability.replace("_", " ")),
                 "action": str((row.limits.get("parameters") or {}).get("operation") or row.limits.get("purpose") or "help with their assigned responsibilities").replace("_", " "),
                 "account_id": row.account_id, "approved": row.limits.get("approved", row.limits.get("initial_hire", False)),
                 "revoked": row.revoked_at is not None} for row in rows]


async def decide_access(database: Any, request_id: str, owner_id: str, allow: bool) -> bool:
    async with database.reserved_session() as session:
        row = await session.get(EmployeeGrant, request_id)
        if row is None or row.owner_id != owner_id:
            return False
        row.limits = {**row.limits, "approved": allow}
        row.revoked_at = None if allow else datetime.now(timezone.utc)
        await session.commit()
        return True


async def assert_runtime_access(database: Any, workflow_id: str, node_id: str, node_type: str, context: dict) -> None:
    """Revocation stops subsequent calls; it never approves an external send."""
    agent_id = str(context.get("parent_node_id") or context.get("agent_node_id") or "")
    async with database.get_session() as session:
        grants = (await session.execute(select(EmployeeGrant).where(EmployeeGrant.workflow_id == workflow_id))).scalars().all()
    indexed = {grant.id: grant for grant in grants}
    def allowed(grant: EmployeeGrant, visited: set[str]) -> bool:
        if grant.id in visited or grant.revoked_at is not None or not grant.limits.get("approved"):
            return False
        parent_id = grant.limits.get("parent_grant_id")
        if not parent_id:
            return True
        parent = indexed.get(parent_id)
        return bool(parent and parent.owner_id == grant.owner_id and allowed(parent, visited | {grant.id}))
    grants = [grant for grant in grants if grant.capability == node_type]
    relevant = [grant for grant in grants if grant.limits.get("tool_node_id") == node_id or
                (agent_id and grant.member_id == agent_id and not grant.limits.get("tool_node_id"))]
    if relevant and not any(allowed(grant, set()) for grant in relevant):
        from services.plugin.base import NodeUserError
        raise NodeUserError("The owner removed permission to use this app. Ask them to allow access in Settings.", requires_user_action=True)
