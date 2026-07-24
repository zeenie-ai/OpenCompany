"""Simple in-memory conversation storage for AI agents."""

from datetime import datetime
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from services.llm.protocol import Message

logger = logging.getLogger(__name__)


@dataclass
class ConversationSession:
    """Conversation session storing canonical native LLM messages."""

    session_id: str
    messages: List[Message] = field(default_factory=list)
    message_timestamps: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


# Global in-memory store
_sessions: Dict[str, ConversationSession] = {}


def get_session(session_id: str) -> ConversationSession:
    """Get or create a conversation session."""
    if session_id not in _sessions:
        _sessions[session_id] = ConversationSession(session_id=session_id)
        logger.info(f"[Memory] Created new session: {session_id}")
    return _sessions[session_id]


def add_message(session_id: str, role: str, content: str) -> None:
    """Add a message to a session."""
    session = get_session(session_id)
    canonical_role = {
        "human": "user",
        "ai": "assistant",
    }.get(role, role)
    session.messages.append(
        Message(role=canonical_role, content=str(content))
    )
    session.message_timestamps.append(datetime.now().isoformat())
    logger.info(f"[Memory] Added {role} message to session '{session_id}' (total: {len(session.messages)})")


def get_messages(session_id: str, window_size: Optional[int] = None) -> List[Dict]:
    """Get messages from a session, optionally limited to last N."""
    session = get_session(session_id)
    messages = session.messages
    timestamps = session.message_timestamps
    if window_size and window_size > 0:
        messages = messages[-window_size:]
        timestamps = timestamps[-window_size:]
    return [
        {
            "role": message.role,
            "content": message.content,
            "timestamp": timestamp,
        }
        for message, timestamp in zip(messages, timestamps)
    ]


def clear_session(session_id: str) -> int:
    """Clear a session's messages. Returns count of cleared messages."""
    if session_id in _sessions:
        count = len(_sessions[session_id].messages)
        _sessions[session_id].messages = []
        _sessions[session_id].message_timestamps = []
        logger.info(f"[Memory] Cleared {count} messages from session '{session_id}'")
        return count
    return 0


def delete_session(session_id: str) -> bool:
    """Delete a session entirely. Returns True if session existed."""
    if session_id in _sessions:
        del _sessions[session_id]
        logger.info(f"[Memory] Deleted session: {session_id}")
        return True
    return False


def get_all_sessions() -> List[Dict]:
    """Get info about all sessions."""
    return [{"session_id": s.session_id, "message_count": len(s.messages), "created_at": s.created_at} for s in _sessions.values()]


def get_native_messages(
    session_id: str,
    window_size: Optional[int] = None,
) -> List[Message]:
    """Return the canonical messages already stored by the session."""

    messages = list(get_session(session_id).messages)
    if window_size and window_size > 0:
        messages = messages[-window_size:]
    return [
        message
        for message in messages
        if message.role in {"user", "assistant"}
    ]
