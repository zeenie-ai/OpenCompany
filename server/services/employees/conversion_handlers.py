"""Conversion approval is available only on the authenticated owner surface."""
from typing import Any
from fastapi import WebSocket
from services.authz.ws_surface import execution_principal
from services.plugin.ws import ws_response
from services.employees.conversion import plan_conversion, apply_conversion, rollback_conversion


def _enabled(container):
    return bool(getattr(container.settings(), "employee_team_conversion_enabled", False))


@ws_response
async def plan_employee_team(data: dict[str, Any], websocket: WebSocket) -> dict:
    from core.container import container
    return await plan_conversion(container.database(), str(data.get("workflow_id") or ""), execution_principal(data, websocket), enabled=_enabled(container))


@ws_response
async def give_employee_team(data: dict[str, Any], websocket: WebSocket) -> dict:
    from core.container import container
    return await apply_conversion(container.database(), str(data.get("review_id") or ""), execution_principal(data, websocket), enabled=_enabled(container), stop_work=data.get("stop_work") is True)


@ws_response
async def rollback_employee_team(data: dict[str, Any], websocket: WebSocket) -> dict:
    from core.container import container
    return await rollback_conversion(container.database(), str(data.get("review_id") or ""), execution_principal(data, websocket))


WS_HANDLERS = {"plan_employee_team": plan_employee_team, "give_employee_team": give_employee_team, "rollback_employee_team": rollback_employee_team}
