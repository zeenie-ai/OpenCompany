"""Normal-mode employees: what the hire screen captured about a workflow.

An employee IS a workflow. The workflow row (name, graph) stays the source
of truth for what runs; this table holds only what Normal mode needs to
present and manage it: the job as the owner described it, the routine and
ground rules shown on the setup screen, which node plays which role in the
generated graph, and the hire's idempotency identity.

Workflows built in the editor have no row here; their summaries are
derived from the graph (services.employees.graph_index).

The hire path reserves a row in ``building`` before the workflow exists
(``workflow_id`` is still NULL), so a retried Hire finds its own
reservation through ``UNIQUE(owner_id, idempotency_key)`` instead of
creating a second employee.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

#: ``hire_state`` values. ``building``: reserved, graph not saved yet.
#: ``ready``: the workflow exists and matches this row. ``failed``: the hire
#: could not be completed; a retry with the same key resumes it.
EMPLOYEE_HIRE_STATES = ("building", "ready", "failed")

#: The node-role palette an employee's avatar and chips use.
EMPLOYEE_COLOR_ROLES = ("agent", "model", "tool", "trigger", "workflow")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Employee(SQLModel, table=True):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_employee_hire_key"),)

    id: str = Field(primary_key=True, max_length=64)
    workflow_id: Optional[str] = Field(default=None, unique=True, index=True, max_length=255)
    owner_id: str = Field(index=True, max_length=255)
    #: ``hire``: created by Normal mode's Hire.
    origin: str = Field(default="hire", max_length=20)
    hire_state: str = Field(default="building", index=True, max_length=20)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)
    #: sha256 of the normalized hire payload: a retry with the same key but
    #: a different payload is a client bug, refused rather than merged.
    payload_hash: Optional[str] = Field(default=None, max_length=64)

    role: str = Field(default="", max_length=60)
    description: str = Field(default="", max_length=280)
    job: str = Field(default="", max_length=2000)
    color_role: str = Field(default="agent", max_length=20)

    #: App ids from config/employee_apps.json (plus unsupported names).
    apps: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    unsupported_apps: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    #: The routine shown on the setup screen: [{title, detail, role, app?}].
    plan: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    #: Ground rules: {ask_first, items: [{key, label, value}]}.
    rules: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    choices: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    #: {kind: app_event | schedule | manual, app?, every?, at?, day?}
    trigger: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    #: Which node plays which part: {"agent": id, "trigger": id, "gate": id,
    #: "reply": id, "notify": id, "todos": id, ...}.
    node_roles: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    #: The model the employee runs on: {provider, model}.
    llm: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    builder_version: int = Field(default=1)

    hired_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class WorkflowRunRecord(SQLModel, table=True):
    """One finished run of a deployed workflow, for "N done today".

    Written when a run ends: by the Temporal workflow's
    ``workflow_runs.record_completion`` activity, or by the local deployment
    path when Temporal is off. ``run_id`` makes the write idempotent (an
    activity retry, or both paths seeing the same run, records it once).
    Rows older than the retention window are pruned.
    """

    __tablename__ = "workflow_run_records"

    id: Optional[int] = Field(default=None, primary_key=True)
    workflow_id: str = Field(index=True, max_length=255)
    run_id: str = Field(unique=True, max_length=255)
    generation: int = Field(default=0)
    #: ``success`` or ``failed``.
    status: str = Field(default="success", max_length=20)
    #: ``temporal`` or ``local``.
    runtime: str = Field(default="temporal", max_length=20)
    finished_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False, index=True))


__all__ = ["EMPLOYEE_COLOR_ROLES", "EMPLOYEE_HIRE_STATES", "Employee", "WorkflowRunRecord"]
