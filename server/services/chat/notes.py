"""Notes to the employee: what it should learn at the start of its next turn
in a chat (docs-internal/chat_protocol.md, "Notes to the employee").

A note is one bracketed line, keyed per session so a newer note of the same
kind replaces an older one not yet told (``[ui-state]`` for an interface the
owner changed without pressing anything). The next run's payload claims the
session's untold notes (``claim_notes``, from ``agent.prepare_payload``) and
puts them ahead of the owner's message; the run's end marks them told
(``deliver_notes``) when it finished or stopped. A run that failed leaves
them untold, so the next turn offers them again. A note changed after it was
claimed stays untold too: the change takes the claim away, which holds even
when the claim and the change fall in one tick of the clock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

from sqlmodel import select

from core.logging import get_logger
from models.chat import ChatNote

logger = get_logger(__name__)

#: The most notes one turn carries; older ones wait for the next.
MAX_NOTES_PER_TURN = 8


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_note(database: Any, *, session_id: str, key: str, kind: str, text: str) -> None:
    """Keep ``text`` as the note under ``key``: a new note, or the newer
    version of one (told again even when the older one was told). The run
    that claimed the older version no longer holds the note."""
    now = _utcnow()
    async with database.reserved_session() as session:
        found = await session.execute(select(ChatNote).where(ChatNote.session_id == session_id, ChatNote.key == key))
        note = found.scalar_one_or_none()
        if note is None:
            note = ChatNote(session_id=session_id, key=key, kind=kind, text=text, created_at=now, updated_at=now)
        else:
            note.kind = kind
            note.text = text
            note.updated_at = now
            note.delivered_at = None
            note.claimed_run_id = None
            note.claimed_at = None
        session.add(note)
        await session.commit()


async def claim_notes(database: Any, *, session_id: str, run_id: str) -> List[ChatNote]:
    """The session's untold notes, oldest first, claimed by ``run_id`` (a
    retried claim by the same run returns the same notes)."""
    now = _utcnow()
    async with database.reserved_session() as session:
        found = await session.execute(
            select(ChatNote)
            .where(ChatNote.session_id == session_id, ChatNote.delivered_at.is_(None))
            .order_by(ChatNote.updated_at, ChatNote.id)
            .limit(MAX_NOTES_PER_TURN)
        )
        notes = list(found.scalars().all())
        for note in notes:
            note.claimed_run_id = run_id
            note.claimed_at = now
            session.add(note)
        await session.commit()
        for note in notes:
            session.expunge(note)
        return notes


async def deliver_notes(database: Any, run_id: str) -> int:
    """Mark the notes ``run_id`` still holds as told. Returns how many. A
    note changed since the claim is not among them (``upsert_note``)."""
    async with database.reserved_session() as session:
        found = await session.execute(select(ChatNote).where(ChatNote.claimed_run_id == run_id, ChatNote.delivered_at.is_(None)))
        held = list(found.scalars().all())
        now = _utcnow()
        for note in held:
            note.delivered_at = now
            session.add(note)
        await session.commit()
        return len(held)


async def drop_note(database: Any, *, session_id: str, key: str) -> None:
    """Forget the note under ``key`` if it was not told yet (what it said no
    longer holds, such as a rating taken back)."""
    from sqlalchemy import delete

    async with database.get_session() as session:
        await session.execute(
            delete(ChatNote).where(ChatNote.session_id == session_id, ChatNote.key == key, ChatNote.delivered_at.is_(None))
        )
        await session.commit()


async def clear_notes(database: Any, session_id: str) -> None:
    """Forget a session's notes (its conversation was cleared or reset)."""
    from sqlalchemy import delete

    async with database.get_session() as session:
        await session.execute(delete(ChatNote).where(ChatNote.session_id == session_id))
        await session.commit()


def notes_prompt(notes: List[ChatNote]) -> str:
    """The notes as the lines ahead of the owner's message."""
    return "\n".join(note.text for note in notes)


__all__ = ["MAX_NOTES_PER_TURN", "claim_notes", "clear_notes", "deliver_notes", "drop_note", "notes_prompt", "upsert_note"]
