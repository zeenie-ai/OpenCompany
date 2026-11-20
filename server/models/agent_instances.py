"""Instance-scoped agent execution, plain transcripts and durable inboxes.

Graph node IDs identify templates. None of these records use them as a
conversation or execution identity. The legacy conversation table is retained.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Index, JSON, UniqueConstraint, text
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(timezone.utc)


class AgentInstance(SQLModel, table=True):
    __tablename__ = "agent_instances"
    id: str = Field(primary_key=True, max_length=255)
    workflow_id: str = Field(index=True, max_length=255)
    generation: int = Field(index=True)
    team_id: str = Field(index=True, max_length=255)
    job_id: str = Field(index=True, max_length=255)
    template_node_id: str = Field(index=True, max_length=255)
    parent_instance_id: str | None = Field(default=None, max_length=255)
    role: str = Field(default="teammate", max_length=30)
    display_name: str = Field(default="Agent", max_length=255)
    state: str = Field(default="idle", index=True, max_length=30)
    halt_reason: str | None = Field(default=None, max_length=255)
    active_run_id: str | None = Field(default=None, max_length=255)
    sequence: int = Field(default=0)
    fence: int = Field(default=0)
    revision: int = Field(default=0)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    continuation: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_instance_runs"
    __table_args__ = (Index("ux_agent_instance_active_run", "instance_id", unique=True,
                           sqlite_where=text("status IN ('running', 'draining')"),
                           postgresql_where=text("status IN ('running', 'draining')")),)
    id: str = Field(primary_key=True, max_length=255)
    instance_id: str = Field(index=True, max_length=255)
    request_id: str = Field(max_length=255)
    status: str = Field(default="running", index=True, max_length=30)
    fence: int
    task_id: str | None = Field(default=None, index=True, max_length=255)
    attempt: int | None = None
    chain_id: str | None = Field(default=None, max_length=255)
    temporal_workflow_id: str | None = Field(default=None, max_length=500)
    temporal_run_id: str | None = Field(default=None, max_length=255)
    input_snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    invalid_requests: int = Field(default=0)
    invalid_request_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    permit_leases: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    permits_pending: bool = Field(default=False, index=True)
    last_permit_reconcile_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=now)
    completed_at: datetime | None = None


class AgentInstanceConversation(SQLModel, table=True):
    """One plain MessageWire list per instance, with a fenced writer."""
    __tablename__ = "agent_instance_conversations"
    instance_id: str = Field(primary_key=True, max_length=255)
    revision: int = Field(default=0)
    owning_run_id: str | None = Field(default=None, max_length=255)
    messages: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    updated_at: datetime = Field(default_factory=now)


class AgentDelivery(SQLModel, table=True):
    __tablename__ = "agent_instance_deliveries"
    __table_args__ = (UniqueConstraint("recipient_id", "sequence", name="uq_agent_delivery_sequence"),)
    id: str = Field(primary_key=True, max_length=255)
    recipient_id: str = Field(index=True, max_length=255)
    sender_id: str | None = Field(default=None, max_length=255)
    team_id: str = Field(index=True, max_length=255)
    generation: int
    sequence: int
    kind: str = Field(default="peer", max_length=30)
    content: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    fingerprint: str = Field(max_length=64)
    state: str = Field(default="queued", index=True, max_length=30)
    run_id: str | None = Field(default=None, max_length=255)
    task_id: str | None = Field(default=None, max_length=255)
    attempt: int | None = None
    chain_id: str | None = Field(default=None, index=True, max_length=255)
    hold_reason: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=now)
    consumed_at: datetime | None = None


class AgentMessageChain(SQLModel, table=True):
    __tablename__ = "agent_message_chains"
    id: str = Field(primary_key=True, max_length=255)
    team_id: str = Field(index=True, max_length=255)
    induced_turns: int = Field(default=0)
    allowance: int = Field(default=8)


class AgentInstanceMutation(SQLModel, table=True):
    """Replay-stable mutation receipts, including argument fingerprints."""
    __tablename__ = "agent_instance_mutations"
    id: str = Field(primary_key=True, max_length=255)
    fingerprint: str = Field(max_length=64)
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=now)
