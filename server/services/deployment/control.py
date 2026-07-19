"""Durable, revision-guarded workflow deployment control plane."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from models.database import WorkflowControlExecution
from sqlalchemy.exc import IntegrityError


ACTIVE_STATES = {"starting", "running", "pausing", "paused", "resuming", "resetting"}


def _graph_hash(nodes: list[dict], edges: list[dict]) -> str:
    value = json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(value.encode()).hexdigest()


def serialize_control(control: Optional[WorkflowControlExecution]) -> Dict[str, Any]:
    if control is None:
        return {
            "state": "never_started", "revision": 0, "generation": 0,
            "can_start": True, "can_pause": False, "can_resume": False, "can_reset": False,
        }
    state = "ready" if control.status == "reset" else control.status
    return {
        "workflow_id": control.workflow_id,
        "generation": control.generation,
        "execution_id": control.execution_id,
        "root_execution_id": control.root_execution_id,
        "data_scope_id": control.data_scope_id or control.execution_id,
        "controller_workflow_id": control.controller_workflow_id,
        "controller_run_id": control.controller_run_id,
        "state": state,
        "revision": control.revision,
        "can_start": state == "ready",
        "can_pause": state == "running",
        "can_resume": state == "paused",
        "can_reset": state != "ready",
        "created_at": control.created_at.isoformat() if control.created_at else None,
        "updated_at": control.updated_at.isoformat() if control.updated_at else None,
        "terminal_reason": control.terminal_reason,
    }


class WorkflowControlService:
    def __init__(self, database: Any):
        self.database = database

    async def get_status(self, workflow_id: str) -> Dict[str, Any]:
        return serialize_control(await self.database.get_latest_workflow_control(workflow_id))

    async def begin_generation(
        self, *, workflow_id: str, nodes: list[dict], edges: list[dict], session_id: str,
        idempotency_key: str, reset: bool = False,
    ) -> tuple[WorkflowControlExecution, bool]:
        existing = await self.database.get_workflow_control_by_idempotency_key(workflow_id, idempotency_key)
        if existing:
            return existing, False
        latest = await self.database.get_latest_workflow_control(workflow_id)
        if latest and latest.status != "reset" and not reset:
            raise ValueError("workflow_already_started")
        generation = (latest.generation + 1) if latest else 1
        execution_id = f"{workflow_id}:g{generation}:{uuid.uuid4().hex[:12]}"
        control = WorkflowControlExecution(
            id=f"workflow-control:{workflow_id}:{generation}", workflow_id=workflow_id,
            generation=generation, execution_id=execution_id, root_execution_id=execution_id,
            data_scope_id=execution_id,
            controller_workflow_id=f"workflow-control-{workflow_id}-g{generation}",
            session_id=session_id, graph_hash=_graph_hash(nodes, edges),
            graph_snapshot={"nodes": nodes, "edges": edges}, idempotency_key=idempotency_key,
        )
        try:
            return await self.database.create_workflow_control(control), True
        except IntegrityError:
            # A concurrent request may win either unique key. Idempotent
            # duplicates return its generation; a distinct request conflicts.
            winner = await self.database.get_workflow_control_by_idempotency_key(workflow_id, idempotency_key)
            if winner is not None:
                return winner, False
            raise ValueError("workflow_control_generation_conflict")

    async def transition(
        self, control: WorkflowControlExecution, *, expected_revision: int, from_statuses: set[str],
        status: str, values: Optional[Dict[str, Any]] = None,
    ) -> WorkflowControlExecution:
        updated = await self.database.transition_workflow_control(
            control.id, expected_revision=expected_revision, from_statuses=from_statuses, status=status, values=values,
        )
        if updated is None:
            raise ValueError("control_revision_conflict")
        return updated

    async def fail(self, control: WorkflowControlExecution, reason: str) -> WorkflowControlExecution:
        return await self.transition(
            control, expected_revision=control.revision, from_statuses={control.status}, status="failed",
            values={"terminal_reason": reason, "completed_at": datetime.now(timezone.utc)},
        )
