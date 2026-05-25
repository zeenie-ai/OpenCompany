"""Simple in-memory conversation storage for AI agents.

Uses standard Python data structures with LangChain message compatibility.
No deprecated APIs - follows LangChain 0.3+ recommendations.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Single conversation message."""

    role: str  # 'human' or 'ai'
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ConversationSession:
    """Conversation session with message history."""

    session_id: str
    messages: List[Message] = field(default_factory=list)
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
    session.messages.append(Message(role=role, content=content))
    logger.info(f"[Memory] Added {role} message to session '{session_id}' (total: {len(session.messages)})")


def get_messages(session_id: str, window_size: Optional[int] = None) -> List[Dict]:
    """Get messages from a session, optionally limited to last N."""
    session = get_session(session_id)
    messages = session.messages
    if window_size and window_size > 0:
        messages = messages[-window_size:]
    return [{"role": m.role, "content": m.content, "timestamp": m.timestamp} for m in messages]


def clear_session(session_id: str) -> int:
    """Clear a session's messages. Returns count of cleared messages."""
    if session_id in _sessions:
        count = len(_sessions[session_id].messages)
        _sessions[session_id].messages = []
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


def get_langchain_messages(session_id: str, window_size: Optional[int] = None):
    """Convert session messages to LangChain message format.

    Uses langchain_core.messages (modern API, not deprecated).
    """
    from langchain_core.messages import HumanMessage, AIMessage

    messages = get_messages(session_id, window_size)
    lc_messages = []
    for m in messages:
        if m["role"] == "human":
            lc_messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "ai":
            lc_messages.append(AIMessage(content=m["content"]))
    return lc_messages
