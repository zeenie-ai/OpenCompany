"""Recover saved activation and job admissions without repeating creation."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Any
from sqlmodel import select
from models.employees import EmployeeActivation, EmployeeJob
from core.logging import get_logger

logger = get_logger(__name__)


async def activate_pending(database: Any, workflow_id: str | None = None) -> None:
    from core.container import container
    from services.employees.summaries import get_employee_summary
    from services.employees.team_runtime import team_runtime_error
    from services.deployment.handlers import start_saved_workflow
    async with database.get_session() as session:
        query = select(EmployeeActivation).where(EmployeeActivation.state.in_(["saved", "blocked", "starting"]))
        if workflow_id:
            query = query.where(EmployeeActivation.workflow_id == workflow_id)
        rows = (await session.execute(query)).scalars().all()
    for intent in rows:
        summary = await get_employee_summary(database, intent.workflow_id, auth_service=container.auth_service())
        from services.employees.store import get_by_workflow
        employee = await get_by_workflow(database, intent.workflow_id)
        team_issue = team_runtime_error() if employee and employee.team_plan else None
        blocked = team_issue or ("missing_apps" if summary and summary.get("missing_apps") else None) or ("needs_ai" if not summary or summary.get("needs_ai") else None)
        state, detail = "blocked", blocked
        if not blocked:
            from services.employees.start import heal_agent_models
            from services.employees.connections import Connections
            await heal_agent_models(database, container.auth_service(), Connections(container.auth_service(), principal=intent.owner_id), intent.workflow_id)
            # The deployment control service arbitrates concurrent admissions
            # with this stable identity; no external calls inside our DB txn.
            result = await start_saved_workflow(intent.workflow_id, owner_id=intent.owner_id, idempotency_key=intent.id)
            state = "running" if result.get("success") else "failed"
            detail = str(result.get("error") or "") or None
        changed = False
        async with database.reserved_session() as session:
            current = await session.get(EmployeeActivation, intent.id)
            if current and current.state != "running":
                changed = (current.state, current.detail) != (state, detail)
                current.state, current.detail = state, detail
                current.updated_at = datetime.now(timezone.utc)
                await session.commit()
        # The summary says how the hire's start went (``activation_state``).
        # A start that is blocked or fails before Start made a control row
        # changes nothing else the page hears about, so it is announced here.
        if changed:
            from services.employees.events import employee_changed_now
            employee_changed_now(intent.workflow_id)


async def recovery_loop(database: Any) -> None:
    while True:
        try:
            await activate_pending(database)
            from services.employees.safe_apply import recover_applies
            await recover_applies(database)
            from services.employees.delivery_resolution import recover_delivery_resolutions
            await recover_delivery_resolutions(database)
            from services.employees.jobs import dispatch_job
            async with database.get_session() as session:
                jobs = (await session.execute(select(EmployeeJob.id).where(EmployeeJob.state == "queued"))).scalars().all()
            for job_id in jobs:
                await dispatch_job(database, job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Employee activation recovery will retry", exc_info=True)
        await asyncio.sleep(10)
