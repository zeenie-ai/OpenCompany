"""Attention items: something the owner should look at (services/attention.py).

One row per thing, keyed ``<kind>:<ref>``: ``stuck_retry`` and
``<Temporal workflow id>:<activity id>`` for an activity that keeps
retrying; ``stuck_runs`` and ``temporal`` while runs keep failing their
workflow tasks. Every sighting touches it, and Home shows the items seen in
the last hour, so a row stranded by a Reset or a deletion drops off by
itself.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AttentionItem(SQLModel, table=True):
    __tablename__ = "attention_items"

    #: ``<kind>:<ref>``. Temporal ids run to about 500 characters.
    id: str = Field(primary_key=True, max_length=1100)
    kind: str = Field(max_length=64)
    severity: str = Field(default="warning", max_length=16)
    ref: str = Field(max_length=1024)
    #: ``open`` or ``resolved``.
    state: str = Field(default="open", max_length=16)
    #: Sightings since it last opened.
    count: int = Field(default=0)
    first_seen: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    last_seen: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False, index=True))


__all__ = ["AttentionItem"]
