"""What waits for the owner's OK, and what the owner decided.

``ApprovalRequest`` is one thing to send, or one that may have happened, of
one of three kinds (``kind``):

- ``gate``: an approvalGate node holds its run until the owner decides. It
  creates its row the first time it runs and finds the same row on every
  retry (``idempotency_key``: ``t:<temporal workflow>:<run>:<activity>`` on
  Temporal, ``p:<execution>:<node>:<inputs>`` in-process). The gate lets the
  draft (or the owner's edit) through to the reply node after it.
- ``tool_call``: a call the employee made to a tool that sends (a plugin's
  ``approval`` spec, services/plugin/approval.py) while Ask first was on. The
  call was answered "waiting for the owner" and the run went on; Send runs it
  later (``ApprovedToolCallWorkflow``), once. Its key is
  ``c:<workflow>:<execution>:<tool call id>``. With Ask first off the call
  runs at once, and a Talk call still leaves a row (``approved_by: auto``) so
  the owner sees what went out. A send that broke off midway leaves one too
  (``failed``, ``outcome: unknown``); a graph node's (key ``u:<temporal
  workflow>:<run>:<activity>``) keeps what it sent, without secrets.
- ``effect``: any other call that broke off midway and may have happened
  (services/approvals/tool_calls.py ``record_unknown``), keyed ``e:`` plus
  the call's or the activity's identity. It starts ``failed`` with
  ``outcome: unknown``; the owner checks it and dismisses it.

``status``::

    pending -> approved -> (gate) let through: approved, consumed_at set
                        -> (tool call) sending -> sent | failed
    approved -> pending           Undo, before grace_until
    pending -> discarded          Discard
    discarded -> pending          Restore, before restore_until
    failed -> approved            Retry (asks to confirm when the outcome is unknown)
    failed -> cancelled           Dismiss (an effect row)
    pending | approved | discarded -> expired | cancelled

Every move is a compare-and-swap on ``revision``. ``ApprovalDecision`` keeps
the owner's decisions; the same ``decision_key`` again is a no-op.

``WorkflowRule`` is a workflow's Ask first rule, read when something would
send: one row per workflow, seeded from an employee's ground rules, changed
from the chat's Ask first chip (``set_ask_first``). A workflow without a row
(the editor's own workflows) gates no tool calls. Its ``mode`` says what asks
in a v1 generation (RFC-0007 §4.2); NULL follows ``ask_first``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalRequest(SQLModel, table=True):
    __tablename__ = "approval_requests"

    id: str = Field(primary_key=True, max_length=64)
    idempotency_key: str = Field(unique=True, max_length=255)
    owner_id: str = Field(index=True, max_length=255)
    workflow_id: str = Field(index=True, max_length=255)
    node_id: str = Field(max_length=255)
    generation: int = Field(default=0)
    execution_id: Optional[str] = Field(default=None, max_length=255)
    #: ``temporal`` or ``local``: a local wait cannot outlive its process.
    runtime: str = Field(default="local", max_length=20)
    status: str = Field(default="pending", index=True, max_length=20)
    #: ``gate``, ``tool_call`` or ``effect`` (see the module docstring).
    kind: str = Field(default="gate", max_length=20)

    #: The app it goes out through ("WhatsApp").
    channel: str = Field(default="", max_length=60)
    #: What the card says it does ("Send a WhatsApp message").
    action: Optional[str] = Field(default=None, max_length=200)
    recipient: str = Field(default="", max_length=500)
    recipient_label: str = Field(default="", max_length=200)
    subject: Optional[str] = Field(default=None, max_length=500)
    draft_text: str = Field(default="", max_length=20000)
    #: What goes out: the draft, or the owner's edit of it.
    final_text: Optional[str] = Field(default=None, max_length=20000)
    final_subject: Optional[str] = Field(default=None, max_length=500)
    edited: bool = Field(default=False)
    #: The message being answered, shortened, for the card.
    context_excerpt: Optional[str] = Field(default=None, max_length=2000)
    #: The channel's limit for an edited draft.
    max_length: int = Field(default=20000)
    #: The card's other lines: [{label, value}].
    details: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=True))
    #: The decide request that settled it last (kept for older readers;
    #: ``approval_decisions`` holds them all).
    decision_key: Optional[str] = Field(default=None, max_length=128)
    revision: int = Field(default=0)

    # ---- a held tool call ----
    node_type: Optional[str] = Field(default=None, max_length=100)
    tool_node_id: Optional[str] = Field(default=None, max_length=255)
    tool_call_id: Optional[str] = Field(default=None, max_length=255)
    agent_node_id: Optional[str] = Field(default=None, max_length=255)
    #: The chat run the call was made in, and the interface it came from.
    run_id: Optional[str] = Field(default=None, index=True, max_length=64)
    ui_part_id: Optional[str] = Field(default=None, max_length=64)
    #: What runs on Send: the node's parameters with the call's arguments
    #: over them, and the arguments alone. An edit lands in both.
    node_data: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=True))
    args: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=True))
    #: The arguments as the employee wrote them.
    original_args: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=True))
    #: The arguments the owner may edit on the card.
    body_field: Optional[str] = Field(default=None, max_length=100)
    subject_field: Optional[str] = Field(default=None, max_length=100)

    # ---- the decision and what came of it ----
    #: ``owner``, or ``auto`` when Ask first was off.
    approved_by: Optional[str] = Field(default=None, max_length=20)
    #: Undo works until this; then it goes.
    grace_until: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    #: A discarded draft can be restored until this.
    restore_until: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    #: When it was handed on: the gate let it through, or a send claimed it.
    consumed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    claim_token: Optional[str] = Field(default=None, max_length=64)
    #: How the send went: ``sent``, ``not_sent`` or ``unknown`` (the call
    #: broke off mid-way; it may have gone out).
    outcome: Optional[str] = Field(default=None, max_length=20)
    outcome_error: Optional[str] = Field(default=None, max_length=1000)
    outcome_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))

    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    decided_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class ApprovalDecision(SQLModel, table=True):
    """One decision the owner made on a row: what, under which key, and the
    revision it left the row at."""

    __tablename__ = "approval_decisions"
    __table_args__ = (UniqueConstraint("approval_id", "decision_key", name="uq_approval_decisions_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    approval_id: str = Field(index=True, max_length=64)
    #: send | undo | discard | restore | retry | dismiss
    decision: str = Field(max_length=20)
    decision_key: str = Field(max_length=128)
    actor: str = Field(default="owner", max_length=255)
    revision: int = Field(default=0)
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class WorkflowRule(SQLModel, table=True):
    """A workflow's Ask first rule (see the module docstring)."""

    __tablename__ = "workflow_rules"

    workflow_id: str = Field(primary_key=True, max_length=255)
    ask_first: bool = Field(default=True)
    #: ``outside`` | ``everything`` | ``money_only`` (services/approvals/
    #: rules.py); NULL follows ``ask_first``.
    mode: Optional[str] = Field(default=None, max_length=20)
    revision: int = Field(default=0)
    #: ``hire`` (seeded from the employee's ground rules) or ``owner``.
    source: str = Field(default="owner", max_length=20)
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


__all__ = ["ApprovalDecision", "ApprovalRequest", "WorkflowRule"]
