"""Durable, revision-guarded workflow deployment control plane."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from core.logging import get_logger
from models.database import WORKFLOW_CONTROL_ACTIVE_STATES, WorkflowControlExecution
from sqlalchemy.exc import IntegrityError

logger = get_logger(__name__)


# Re-exported name kept for existing importers; the canonical definition
# lives beside the model so the terminate-sweep guard shares it.
ACTIVE_STATES = WORKFLOW_CONTROL_ACTIVE_STATES


def _graph_hash(nodes: list[dict], edges: list[dict]) -> str:
    value = json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(value.encode()).hexdigest()


# Canvas editability is a server-owned capability, like can_start/can_pause:
# the frontend renders it, never re-derives it from state strings. Editable
# while nothing is armed (ready / failed) and while PAUSED — controlled
# generations execute their immutable admitted snapshot, so edits cannot
# corrupt in-flight runs, and paused is exactly the "fix it, then resume"
# posture the recovery policies produce. Locked while starting / running /
# transitional so a live deployment cannot be rewired mid-flight.
_EDITABLE_STATES = frozenset({"ready", "paused", "failed"})


def serialize_control(control: Optional[WorkflowControlExecution]) -> Dict[str, Any]:
    if control is None:
        return {
            "state": "never_started", "revision": 0, "generation": 0,
            "can_start": True, "can_pause": False, "can_resume": False, "can_reset": False,
            "can_edit": True,
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
        "can_edit": state in _EDITABLE_STATES,
        "created_at": control.created_at.isoformat() if control.created_at else None,
        "updated_at": control.updated_at.isoformat() if control.updated_at else None,
        "terminal_reason": control.terminal_reason,
        # Set when the generation paused on its own (see the model); getattr
        # because tests build controls without the newer columns.
        "pause_reason": getattr(control, "pause_reason", None),
        "pause_detail": getattr(control, "pause_detail", None),
    }


#: Automatic pause reasons (``WorkflowControlExecution.pause_reason``).
PAUSE_REASON_FAILURES = "failures"
PAUSE_REASON_RECOVERY = "recovery"
PAUSE_REASON_CONTROLLER_MISSING = "controller_missing"

#: What running again clears.
CLEAR_PAUSE_REASON: Dict[str, Any] = {"pause_reason": None, "pause_detail": None}


ControlListener = Callable[[str], None]
_CONTROL_LISTENERS: List[ControlListener] = []


def register_control_listener(listener: ControlListener) -> None:
    """Call ``listener(workflow_id)`` after every control-status broadcast
    (start, pause, resume, reset, recovery). For state derived from the
    control plane that must not go stale, such as Normal mode's employee
    summaries. Listeners are synchronous and must not block: schedule any
    I/O. Registering the same listener twice is a no-op."""
    if listener not in _CONTROL_LISTENERS:
        _CONTROL_LISTENERS.append(listener)


def notify_control_changed(workflow_id: str) -> None:
    for listener in list(_CONTROL_LISTENERS):
        try:
            listener(workflow_id)
        except Exception:
            logger.warning("Control listener failed", workflow_id=workflow_id, exc_info=True)


class WorkflowControlService:
    def __init__(self, database: Any):
        self.database = database

    async def get_status(self, workflow_id: str) -> Dict[str, Any]:
        return serialize_control(await self.database.get_latest_workflow_control(workflow_id))

    async def begin_generation(
        self, *, workflow_id: str, nodes: list[dict], edges: list[dict], session_id: str,
        idempotency_key: str, reset: bool = False, graph_version: int = 0,
        owner_id: str = "owner",
    ) -> tuple[WorkflowControlExecution, bool]:
        from services.workflow_sanitizer import sanitize_runtime_payload

        safe_graph = sanitize_runtime_payload(
            {
                "graphVersion": max(0, int(graph_version or 0)),
                "owner_id": str(owner_id or "owner"),
                "nodes": nodes,
                "edges": edges,
            }
        )
        nodes = list(safe_graph.get("nodes") or [])
        edges = list(safe_graph.get("edges") or [])
        existing = await self.database.get_workflow_control_by_idempotency_key(workflow_id, idempotency_key)
        if existing:
            return existing, False
        latest = await self.database.get_latest_workflow_control(workflow_id)
        if latest and latest.status != "reset" and not reset:
            raise ValueError("workflow_already_started")
        generation = (latest.generation + 1) if latest else 1
        execution_id = await self.database.allocate_execution_id(workflow_id)
        control = WorkflowControlExecution(
            id=f"workflow-control:{workflow_id}:{generation}", workflow_id=workflow_id,
            generation=generation, execution_id=execution_id, root_execution_id=execution_id,
            data_scope_id=execution_id,
            controller_workflow_id=f"workflow-control-{workflow_id}-g{generation}",
            session_id=session_id, graph_hash=_graph_hash(nodes, edges),
            graph_snapshot={
                "graphVersion": safe_graph["graphVersion"],
                "owner_id": safe_graph["owner_id"],
                "nodes": nodes,
                "edges": edges,
            },
            idempotency_key=idempotency_key,
            # Revisions are monotonic across generations so a delayed request
            # from an archived generation cannot pass CAS against a newer one
            # that happens to be at the same lifecycle step.
            revision=(latest.revision + 1) if latest else 0,
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
