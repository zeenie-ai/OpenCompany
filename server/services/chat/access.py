"""Who may use a chat session.

A workflow's session (its id) answers only the workflow's owner, compared
with the socket's principal as the canvas and Memory panels do, and the
internal worker socket may use none of chat's commands. The editor's
``"default"`` session belongs to no workflow. A session that names no saved
workflow is refused, as those panels refuse one: nobody may write into the
chat of a workflow that does not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from services.authz.ws_surface import execution_principal
from services.chat_thread import DEFAULT_SESSION


class ChatAccessDenied(Exception):
    """The socket may not use this session."""

    def __init__(self) -> None:
        super().__init__("access_denied")


@dataclass(frozen=True)
class ChatScope:
    session_id: str
    #: The workflow the session belongs to; None for ``"default"``.
    workflow_id: Optional[str]


def _owner_of(saved: Any) -> str:
    data = getattr(saved, "data", None)
    if data is None and isinstance(saved, dict):
        data = saved.get("data", saved)
    return str(data.get("owner_id") or "") if isinstance(data, dict) else ""


def session_id_of(data: Any) -> str:
    """The request's session, ``"default"`` when it names none."""
    value = data.get("session_id") if isinstance(data, dict) else None
    text = str(value).strip() if value is not None else ""
    return text or DEFAULT_SESSION


async def authorize_session(database: Any, websocket: Any, session_id: str) -> ChatScope:
    """The scope a socket may use, or :class:`ChatAccessDenied`."""
    scope = getattr(websocket, "scope", None)
    if isinstance(scope, dict) and scope.get("path") == "/ws/internal":
        raise ChatAccessDenied()
    if session_id == DEFAULT_SESSION:
        return ChatScope(session_id=session_id, workflow_id=None)
    saved = await database.get_workflow(session_id)
    if saved is None:
        raise ChatAccessDenied()
    owner = _owner_of(saved)
    if owner and owner != execution_principal({}, websocket):
        raise ChatAccessDenied()
    return ChatScope(session_id=session_id, workflow_id=session_id)


__all__ = ["ChatAccessDenied", "ChatScope", "authorize_session", "session_id_of"]
