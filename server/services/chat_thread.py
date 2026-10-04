"""A workflow's chat thread: the one write path for chat rows.

The thread is the chat session whose id is the workflow id (``"default"``
is the editor's chat with no workflow open). The owner's messages come in
through ``send_chat_message``; answers come from the "Reply in Chat" node
(``chatReply``). Normal mode shows the thread on the employee's page as
Talk, and the editor shows it in its chat pane.

- ``delivery_for``: where a message sent now goes, by the deployment's
  latest control state: ``"now"`` while it runs (or is starting or
  resuming), ``"queued"`` while it is paused or pausing (the controller
  keeps the message and starts a run on Resume), None otherwise: nothing
  would read it.
- ``record_chat_message``: add a row stamped with the session's live
  generation (``root_execution_id``, as ``chat_execution_id`` says; the
  editor's chat reads one generation, so a row without it would never show
  there), then announce it. A reply names its chat run (``run_id``) and is
  saved under the run's reply id, so a retried save writes it once. The
  owner's messages are written with their runs by ``services/chat/ledger.py``,
  which announces them the same way.
- ``clear_chat_thread``: delete the session's rows, every generation, and
  its chat runs, then announce it when there were any. Subscribers watching
  a run that was still live are told it ended (``reset``), and drafts the
  runs made that still wait for the owner are cancelled.
- ``clear_chat_session``: the owner's Clear. The thread goes (live runs end
  with ``cleared``), and the listeners registered with
  ``register_chat_cleared_listener`` forget what it held: the Context plugin
  clears the workflow's conversations, so the agent starts over with the
  chat.

A workflow's thread lives as long as its generation and its workflow. A
Reset (every restart, Home's Apply and Turn on Talk included) clears it
through the chat nodes' ``reset_execution_state`` (``chatTrigger`` and
``chatReply``), in the same Reset in which the Context node forgets the
conversation, so no screen shows a conversation the agent no longer has.
Deleting the workflow deletes its thread (a workflow-deleted hook the
``chatReply`` plugin registers).

Every insert, and every clear that removed rows, is announced as
``chat.updated`` (CloudEvent type
``com.opencompany.chat.updated``, data ``{workflow_id, session_id, role}``,
``role`` None for a clear). Identity only: the frame reaches every socket,
and clients refetch the thread through ``get_chat_messages``. Broadcast
directly through the status broadcaster, not ``services.events.dispatch
.emit``: no trigger consumes it, so the canary path would run a Visibility
query that matches nothing on every message (the same reason as
``context.updated``).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.logging import get_logger
from services.events.envelope import WorkflowEvent

logger = get_logger(__name__)

#: ``await listener(database=..., workflow_id=...)`` after the owner clears
#: a workflow's chat. Plugins register (this module never imports
#: ``nodes/``); a Reset and a workflow delete forget the conversation
#: through their own paths.
ChatClearedListener = Callable[..., Awaitable[None]]
_CLEARED_LISTENERS: List[ChatClearedListener] = []

#: The editor's chat when no workflow is open: not a workflow's thread.
DEFAULT_SESSION = "default"
WIRE_KEY = "chat.updated"
SOURCE = "opencompany://services/chat_thread"

#: Control states whose controller takes a message now.
_TAKES_NOW = frozenset({"starting", "running", "resuming"})
#: Control states whose controller keeps a message for Resume.
_KEEPS_FOR_RESUME = frozenset({"pausing", "paused"})


def chat_execution_id(control: Any) -> Optional[str]:
    """The generation a chat row belongs to: the live one's
    ``root_execution_id``, or None when nothing was started since the last
    Reset."""
    return control.root_execution_id if control is not None and control.status != "reset" else None


def delivery_for(control: Any) -> Optional[str]:
    """``"now"``, ``"queued"``, or None when no controller would read a
    message sent now."""
    status = control.status if control is not None else None
    if status in _TAKES_NOW:
        return "now"
    if status in _KEEPS_FOR_RESUME:
        return "queued"
    return None


def chat_updated(*, session_id: str, role: Optional[str]) -> WorkflowEvent:
    """A thread gained a message (``role``) or was cleared (``role`` None)."""
    return WorkflowEvent(
        source=SOURCE,
        type="com.opencompany.chat.updated",
        subject=session_id,
        data={
            "workflow_id": session_id if session_id != DEFAULT_SESSION else None,
            "session_id": session_id,
            "role": role,
        },
    )


async def announce_chat_updated(session_id: str, role: Optional[str]) -> None:
    """Send ``chat.updated`` for a session: a message was added (``role``),
    or the thread was cleared or a run in it ended (``role`` None). Never
    raises."""
    from services.chat.relay import active_relay
    from services.status_broadcaster import get_status_broadcaster

    event = chat_updated(session_id=session_id, role=role)
    data = event.model_dump(mode="json", exclude_none=True)
    relay = active_relay()
    if relay is not None:
        # A standalone worker: the backend sends it (services/chat/relay.py).
        relay.offer_broadcast(WIRE_KEY, data, session_id=session_id)
        return
    try:
        await get_status_broadcaster().broadcast({"type": WIRE_KEY, "data": data})
    except Exception:
        logger.warning("chat.updated broadcast failed", session_id=session_id, exc_info=True)


async def record_chat_message(
    database: Any,
    session_id: str,
    role: str,
    message: str,
    *,
    uid: Optional[str] = None,
    run_id: Optional[str] = None,
    kind: str = "text",
    status: str = "complete",
    parts: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Add ``message`` to the thread, in the session's live generation, and
    announce it. Returns the saved row, or None when it could not be saved
    (nothing is announced). A ``uid`` already saved returns that row; the
    repeated announcement only makes clients read the thread again."""
    control = await database.get_latest_workflow_control(session_id)
    saved = await database.add_chat_message(
        session_id, role, message, execution_id=chat_execution_id(control),
        uid=uid, run_id=run_id, kind=kind, status=status, parts=parts,
    )
    if saved:
        await announce_chat_updated(session_id, role)
    return saved or None


async def clear_chat_thread(database: Any, session_id: str, *, reason: str = "reset") -> int:
    """Delete the thread, every generation of it, with its chat runs, and
    announce it when there was anything to delete (a Reset runs one clear
    per chat node). Runs still live end for their subscribers with
    ``reason`` as the error code. Returns the messages deleted."""
    from services.chat import ledger

    try:
        live = await ledger.live_runs(database, session_id)
    except Exception:
        logger.warning("Could not read live chat runs before a clear", session_id=session_id, exc_info=True)
        live = []
    await _cancel_drafts(database, session_id)
    count = await database.clear_chat_messages(session_id)
    ledger.publish_cleared(live, code=reason)
    if count:
        await announce_chat_updated(session_id, None)
    return count


async def _cancel_drafts(database: Any, session_id: str) -> None:
    """Drafts the conversation's runs made that still wait go with it: a
    send nobody can see any more must not go out later. So do its failed
    sends: kept, their card stayed in the empty chat, and its Try again
    would send from a conversation that is gone."""
    from services.chat import ledger

    try:
        from services.approvals import store, waiter
        from services.approvals.listeners import change_of, notify_approval_changed

        run_ids = await ledger.session_run_ids(database, session_id)
        if not run_ids:
            return
        for row in await store.cancel_open(database, workflow_id=session_id, run_ids=run_ids, failed=True):
            waiter.notify(row.id)
            await notify_approval_changed(change_of(row, "cancelled"))
    except Exception:
        logger.warning("Could not cancel a cleared conversation's drafts", session_id=session_id, exc_info=True)


def register_chat_cleared_listener(listener: ChatClearedListener) -> None:
    """Run ``await listener(database=..., workflow_id=...)`` after the owner
    clears a workflow's chat. Registering the same listener twice is a
    no-op."""
    if listener not in _CLEARED_LISTENERS:
        _CLEARED_LISTENERS.append(listener)


async def clear_chat_session(database: Any, session_id: str) -> int:
    """The owner's Clear: delete the thread, then let the listeners forget
    what it held, so the agent starts over with the chat. Only a workflow's
    session has listeners to tell (``"default"`` is no workflow's). A
    failing listener is logged and never fails the clear. Returns the rows
    deleted."""
    count = await clear_chat_thread(database, session_id, reason="cleared")
    if session_id == DEFAULT_SESSION:
        return count
    for listener in list(_CLEARED_LISTENERS):
        try:
            await listener(database=database, workflow_id=session_id)
        except Exception:
            logger.warning(
                "Chat-cleared listener failed",
                listener=getattr(listener, "__qualname__", repr(listener)),
                workflow_id=session_id,
                exc_info=True,
            )
    return count


__all__ = [
    "DEFAULT_SESSION",
    "WIRE_KEY",
    "ChatClearedListener",
    "announce_chat_updated",
    "chat_execution_id",
    "chat_updated",
    "clear_chat_session",
    "clear_chat_thread",
    "delivery_for",
    "record_chat_message",
    "register_chat_cleared_listener",
]
