"""Builder creation uses the same validated, transactional Hire path."""
from __future__ import annotations
import hashlib
from types import SimpleNamespace
from typing import Any


async def builder_create(ctx: Any, name: str, description: str, request_id: str) -> dict:
    from core.container import container
    from services.employees.permissions import trusted_owner_request
    from services.employees.hire import handle_hire_employee
    if not await trusted_owner_request(container.database(), ctx):
        return {"success": False, "error": "owner_request_required", "required_access": [], "activation_state": "blocked"}
    # A durable tool-call identity scopes retries independently of mutable
    # names. Access starts with no apps; new app capabilities require review.
    identity = hashlib.sha256(f"{ctx.workflow_id}:{ctx.node_id}:{request_id}".encode()).hexdigest()
    socket = SimpleNamespace(scope={"path": "/ws"}, state=SimpleNamespace(user_id=ctx.user_id))
    return await handle_hire_employee({"idempotency_key": f"builder:{identity}", "name": name,
        "role": name, "job": description or name, "description": description,
        "steps": [{"title": "Review the request", "detail": description or name, "role": "agent"},
                  {"title": "Check the team’s work", "role": "agent"}],
        "rules": {"ask_first": True}, "trigger": {"kind": "manual"}}, socket)
