"""Normal-mode employee WebSocket handlers (read path).

``list_employees`` -> ``{employees: EmployeeSummary[]}``: the whole team for
the Home sidebar. ``get_employee {workflow_id}`` -> ``{employee}``: one
summary plus what the employee card and its setup screen show. Shapes are
documented in services/employees/summaries.py.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import WebSocket

from services.employees.summaries import get_employee_detail, list_employee_summaries
from services.plugin.base import NodeUserError
from services.plugin.ws import ws_response


@ws_response
async def handle_list_employees(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    employees = await list_employee_summaries(container.database(), auth_service=container.auth_service())
    return {"success": True, "employees": employees}


@ws_response
async def handle_get_employee(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    workflow_id = str(data.get("workflow_id") or "").strip()
    if not workflow_id:
        raise NodeUserError("workflow_id is required")
    from core.container import container

    employee = await get_employee_detail(container.database(), workflow_id, auth_service=container.auth_service())
    if employee is None:
        return {"success": False, "error": "not_found", "workflow_id": workflow_id}
    return {"success": True, "employee": employee}


from services.employees.hire import handle_hire_employee  # noqa: E402
from services.employees.start import handle_start_employee  # noqa: E402

WS_HANDLERS: Dict[str, Any] = {
    "list_employees": handle_list_employees,
    "get_employee": handle_get_employee,
    "hire_employee": handle_hire_employee,
    "start_employee": handle_start_employee,
}


__all__ = ["WS_HANDLERS", "handle_get_employee", "handle_list_employees"]
