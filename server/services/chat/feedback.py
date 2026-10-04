"""The owner's rating of an answer (docs-internal/chat_protocol.md,
"Feedback").

``set_feedback`` keeps it (``chat_feedback``, one per answer) and leaves the
employee a note for its next turn in the chat (``[feedback]{...}[/feedback]``
keyed per answer: changing the rating before that turn replaces the note,
and taking it back drops one not yet told). It answers where the rating
reaches: ``next_turn``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlmodel import select

from models.chat import ChatFeedback
from models.database import ChatMessage

VALUES = ("up", "down")
#: How much of the answer the note quotes.
_EXCERPT_CHARS = 300


class FeedbackRefused(ValueError):
    """A rating that cannot be kept; ``code`` says why."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def feedback_note(answer: str, value: str) -> str:
    """The line the employee reads before its next answer."""
    excerpt = " ".join((answer or "").split())
    if len(excerpt) > _EXCERPT_CHARS:
        excerpt = excerpt[: _EXCERPT_CHARS - 1].rstrip() + "…"
    payload = {"rating": "good" if value == "up" else "bad", "answer": excerpt}
    return f"[feedback]{json.dumps(payload, ensure_ascii=False, separators=(', ', ': '))}[/feedback]"


async def set_feedback(database: Any, *, session_id: str, message_uid: str, value: Optional[str]) -> List[str]:
    """Rate the answer ``message_uid`` (``up``, ``down``), or take the rating
    back (None). Returns where it reaches. Raises :class:`FeedbackRefused`
    (``invalid_request``, ``not_found``)."""
    from services.chat.notes import drop_note, upsert_note

    if value is not None and value not in VALUES:
        raise FeedbackRefused("invalid_request")
    async with database.reserved_session() as session:
        found = await session.execute(
            select(ChatMessage).where(ChatMessage.uid == message_uid, ChatMessage.session_id == session_id)
        )
        message = found.scalar_one_or_none()
        if message is None or message.role != "assistant" or not message.run_id:
            raise FeedbackRefused("not_found")
        existing = (
            await session.execute(
                select(ChatFeedback).where(ChatFeedback.session_id == session_id, ChatFeedback.message_uid == message_uid)
            )
        ).scalar_one_or_none()
        if value is None:
            if existing is not None:
                await session.delete(existing)
        else:
            row = existing or ChatFeedback(session_id=session_id, message_uid=message_uid, value=value)
            row.value = value
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
        row_dict = database.chat_row(message)
        await session.commit()

    key = f"feedback:{message_uid}"
    if value is None:
        await drop_note(database, session_id=session_id, key=key)
        return []
    await upsert_note(database, session_id=session_id, key=key, kind="feedback", text=feedback_note(row_dict["message"], value))
    return ["next_turn"]


async def feedback_for(database: Any, session_id: str) -> Dict[str, str]:
    """The session's ratings, by answer."""
    async with database.get_session() as session:
        result = await session.execute(select(ChatFeedback).where(ChatFeedback.session_id == session_id))
        return {row.message_uid: row.value for row in result.scalars().all()}


__all__ = [
    "FeedbackRefused",
    "VALUES",
    "feedback_for",
    "feedback_note",
    "set_feedback",
]
