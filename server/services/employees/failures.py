"""Bounded, public reasons for failed employee lead execution.

Exception text can contain prompts or credentials. Preserve a useful category
through Temporal's wrappers without publishing arbitrary exception details.
"""
from __future__ import annotations

NO_ASSIGNMENTS_CODE = "lead_no_assignments"
NO_ASSIGNMENTS_MESSAGE = "The lead completed without assigning work to a teammate."

FAILURE_MESSAGES = {
    "lead_task_manager_unavailable": "The lead could not use its assigned Task Manager. Check the Task Manager and specialist connections on the employee's graph.",
    "lead_payload_too_large": "The team lead could not run because its runtime payload exceeded the size limit. This needs a runtime fix; waiting or submitting the same job again will not resolve it.",
    "lead_runtime_unavailable": "The team lead could not run because its runtime is unavailable. Restore the runtime before trying again.",
    "lead_turn_halted": "The team lead stopped before completing its turn. Check the runtime before trying again.",
    "lead_execution_failed": "The team lead failed while processing this request. Check the runtime failure before trying again.",
}


def lead_failure(value) -> dict[str, str]:
    """Classify a result or nested SDK failure with a fixed, safe vocabulary."""
    kind = ""
    if isinstance(value, dict):
        kind = str(value.get("error_type") or value.get("failure_code") or "")
    else:
        seen = set()
        for _ in range(12):
            if not isinstance(value, BaseException) or id(value) in seen:
                break
            seen.add(id(value))
            candidate = str(getattr(value, "type", "") or type(value).__name__)
            if candidate in {"PayloadsTooLarge", "ImportError", "ModuleNotFoundError", "agent_turn_halted", "EmployeeLeadNoAssignments", "EmployeeTaskManagerUnavailable"}:
                kind = candidate
                break
            value = getattr(value, "cause", None) or value.__cause__
    code = {
        "PayloadsTooLarge": "lead_payload_too_large",
        "ImportError": "lead_runtime_unavailable",
        "ModuleNotFoundError": "lead_runtime_unavailable",
        "agent_turn_halted": "lead_turn_halted",
        "EmployeeLeadNoAssignments": NO_ASSIGNMENTS_CODE,
        "EmployeeTaskManagerUnavailable": "lead_task_manager_unavailable",
        NO_ASSIGNMENTS_CODE: NO_ASSIGNMENTS_CODE,
    }.get(kind, kind if kind in FAILURE_MESSAGES else "lead_execution_failed")
    return {"code": code, "message": NO_ASSIGNMENTS_MESSAGE if code == NO_ASSIGNMENTS_CODE else FAILURE_MESSAGES[code]}
