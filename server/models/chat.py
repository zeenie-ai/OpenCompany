"""Chat runs: the answer an employee works on after each owner message.

A run is admitted in the same transaction as the owner's message
(``services/chat/ledger.py``). It moves ``queued`` (the employee is paused)
or ``pending`` -> ``running`` -> ``finished`` | ``error`` | ``stopped``, each
step a compare-and-swap on ``state``; Stop moves a pending or running run to
``stopping`` on the way to ``stopped``. ``kind`` says what started it:
``message`` (the owner wrote), ``edit``, ``regenerate`` or ``action`` (a button
in generated UI).

**The lane.** At most one non-terminal run per session, enforced by a
partial unique index: two runs that loaded the same stored
conversation would each save over the other's (docs-internal/
agent_context_flow.md). A second message while one is live is refused with
``run_in_progress``; the composer shows Stop instead of Send meanwhile.

``ChatRunPart`` collects what a run's tools produce for its reply (generated
UI, sources, artifacts, approvals), keyed so an activity retry writes the
same row again. ``ChatThread`` holds a session's active leaf: the message the
shown path ends at, which branches move, and its source counter.
``ChatNote`` holds what the employee should learn on its next turn.

**Branches** (``services/chat/branches.py``). A run records, for each agent
with a stored conversation that works for it, the conversation as it was
when the run began (``context_cursors``: its length and a digest), so an
edit or a retry can take the agent back to it. ``ChatBranchSnapshot`` keeps
the conversations of a branch the owner moved away from, keyed by the leaf
they left, so moving back restores them. ``ChatFeedback`` is the owner's
rating of an answer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Column, DateTime, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: States that hold the lane.
LIVE_STATES = ("queued", "pending", "running", "stopping")
#: States a run ends in.
TERMINAL_STATES = ("finished", "error", "stopped")

#: Databases made before October 2026 keep ``AND kind != 'resume'`` in it; no
#: run has that kind, so both refuse the same runs.
_LANE_WHERE = "state IN ('queued', 'pending', 'running', 'stopping')"


class ChatThread(SQLModel, table=True):
    __tablename__ = "chat_threads"

    session_id: str = Field(primary_key=True, max_length=255)
    active_leaf_uid: Optional[str] = Field(default=None, max_length=64)
    revision: int = Field(default=0)
    #: The number the conversation's next cited source gets
    #: (services/chat/sources.py), so a number never repeats in it.
    next_source: int = Field(default=1)
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class ChatRun(SQLModel, table=True):
    __tablename__ = "chat_runs"
    __table_args__ = (Index("ux_chat_runs_lane", "session_id", unique=True, sqlite_where=text(_LANE_WHERE)),)

    run_id: str = Field(primary_key=True, max_length=64)
    session_id: str = Field(index=True, max_length=255)
    workflow_id: Optional[str] = Field(default=None, index=True, max_length=255)
    #: The generation's root execution id, like ``chat_messages.execution_id``.
    run_key: Optional[str] = Field(default=None, max_length=255)
    kind: str = Field(default="message", max_length=20)
    state: str = Field(default="pending", index=True, max_length=20)
    user_message_uid: Optional[str] = Field(default=None, max_length=64)
    reply_message_uid: Optional[str] = Field(default=None, max_length=64)
    parent_run_id: Optional[str] = Field(default=None, max_length=64)
    #: Per-message choices the owner made (``web``).
    options: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    #: Working steps as they finished, for "Worked for Ns · N steps" on reload.
    steps: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    #: How a finished run ended: ``success`` or ``stopped``. None until it
    #: ends, and for errors.
    outcome: Optional[str] = Field(default=None, max_length=20)
    #: The terminal event's ``result`` (``reply_message_id`` or ``no_reply``).
    result: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error: Optional[str] = Field(default=None, max_length=500)
    error_code: Optional[str] = Field(default=None, max_length=60)
    temporal_workflow_id: Optional[str] = Field(default=None, max_length=500)
    temporal_run_id: Optional[str] = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    started_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    #: When the owner pressed Stop; the watchdog ends a run still stopping
    #: ``runs.stop_grace_s`` later.
    stop_requested_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    #: Per agent that worked for it with a stored conversation, that
    #: conversation as the run began: ``{agent_node_id: {generation, length,
    #: digest}}`` (the first time each agent was prepared in the run).
    context_cursors: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class ChatRunPart(SQLModel, table=True):
    __tablename__ = "chat_run_parts"
    __table_args__ = (UniqueConstraint("run_id", "key", name="uq_chat_run_parts_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, max_length=64)
    #: ``ui``, ``sources``, ``artifacts``, ``approvals``, ``followups``.
    kind: str = Field(max_length=20)
    #: Unique within the run; a retried activity writes the same key again.
    key: str = Field(max_length=255)
    payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class ChatNote(SQLModel, table=True):
    """Something the employee should learn at the start of its next turn in a
    chat (services/chat/notes.py): one bracketed line, keyed per session so a
    newer note of the same kind replaces an older one not yet told."""

    __tablename__ = "chat_notes"
    __table_args__ = (UniqueConstraint("session_id", "key", name="uq_chat_notes_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, max_length=255)
    key: str = Field(max_length=255)
    #: ``ui-state`` (what the owner set in an interface).
    kind: str = Field(max_length=20)
    text: str = Field(max_length=20000)
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    #: The run whose turn carries it, once claimed.
    claimed_run_id: Optional[str] = Field(default=None, index=True, max_length=64)
    claimed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    #: When that run ended having told it; None while still to tell.
    delivered_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class ChatBranchSnapshot(SQLModel, table=True):
    """An agent's conversation on a branch the owner moved away from, keyed
    by the message that branch ended at (its leaf)."""

    __tablename__ = "chat_branch_snapshots"
    __table_args__ = (UniqueConstraint("session_id", "leaf_uid", "agent_node_id", name="uq_chat_branch_snapshots_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, max_length=255)
    leaf_uid: str = Field(max_length=64)
    agent_node_id: str = Field(max_length=255)
    generation: int = Field(default=0)
    messages: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class ChatFeedback(SQLModel, table=True):
    """The owner's rating of an answer: ``up`` or ``down``."""

    __tablename__ = "chat_feedback"
    __table_args__ = (UniqueConstraint("session_id", "message_uid", name="uq_chat_feedback_message"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, max_length=255)
    message_uid: str = Field(max_length=64)
    value: str = Field(max_length=10)
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


__all__ = [
    "ChatBranchSnapshot",
    "ChatFeedback",
    "ChatNote",
    "ChatRun",
    "ChatRunPart",
    "ChatThread",
    "LIVE_STATES",
    "TERMINAL_STATES",
]
