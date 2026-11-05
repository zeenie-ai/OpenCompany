"""What the agent answering the owner's chat reads besides its own system
prompt, and reading its follow-up suggestions back out of its answer
(docs-internal/chat_protocol.md, "Notes to the employee" and "Follow-ups").

``CHAT_REPLY_GUIDE`` is fixed text, the same byte for byte on every turn, so
the agent's system prompt stays stable for prompt caching. It is appended by
``agent.prepare_payload`` only for the agent whose answer is the chat's reply
(the one with a ``chat_stream``). While the owner has Generated UI off
(``services/genui/switch.py``) the agent reads ``CHAT_GUIDE_BASE``, the
same guide without the line asking for follow-up questions.

``split_followups`` takes the ``<followups>`` block off a reply: while the
owner has Generated UI on, the chat shows its questions as buttons
(``parts.followups``), and the block itself never shows (the stream holds it
back too, ``services/chat/stream.py``).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Tuple

from core.logging import get_logger
from services.chat.stream import FOLLOWUPS_OPEN

logger = get_logger(__name__)

FOLLOWUPS_CLOSE = "</followups>"
#: The most suggestions a reply keeps, and how long each may be.
MAX_FOLLOWUPS = 3
MAX_FOLLOWUP_CHARS = 160

CHAT_GUIDE_BASE = (
    "You are answering the owner in a chat.\n"
    "- A line in square brackets at the start of the owner's message comes from OpenCompany, not from the owner: "
    "[ui-event]{...}[/ui-event] is a button they pressed in an interface you showed (with what they picked), and "
    "[ui-state]{...}[/ui-state] is what they set in one without pressing anything; treat both as the owner's choice. "
    "[update]{...}[/update] says what became of something you did (a draft sent or not, a part of the chat the owner "
    "went back from), and [feedback]{...}[/feedback] is how the owner rated one of your answers; learn from it. "
    "[attachments]{...}[/attachments] after their words lists the files they attached, with their paths in your "
    "workspace.\n"
    "- A search result numbered n is a source: cite the ones you rely on as [n] right after what they support."
)
FOLLOWUPS_GUIDE = (
    "- When a few short next questions would help the owner, end your reply with "
    '<followups>["...", "..."]</followups>: at most 3, each a short question in the owner\'s own words. The chat '
    "shows them as buttons; the block itself is never shown."
)
CHAT_REPLY_GUIDE = f"{CHAT_GUIDE_BASE}\n{FOLLOWUPS_GUIDE}"

_LIST_MARK = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def with_chat_guide(system_message: str, *, followups: bool = True) -> str:
    """The system prompt with the guide after it (once); without the
    follow-ups line when ``followups`` is false."""
    guide = CHAT_REPLY_GUIDE if followups else CHAT_GUIDE_BASE
    text = system_message or ""
    if guide in text:
        return text
    return f"{text.rstrip()}\n\n{guide}" if text.strip() else guide


async def chat_turn(database: Any, chat_stream: Mapping[str, Any], *, system_message: str, prompt: str) -> Tuple[str, str]:
    """The answering agent's turn: its system prompt with the guide (asking
    for follow-ups only while Generated UI is on), and the owner's message
    with the session's untold notes ahead of it (claimed for this run).
    Notes that cannot be read are left for the next turn."""
    from services.chat.notes import claim_notes, notes_prompt
    from services.genui.switch import generated_ui_enabled

    system = with_chat_guide(system_message, followups=await generated_ui_enabled(database))
    try:
        claimed = await claim_notes(database, session_id=str(chat_stream["session_id"]), run_id=str(chat_stream["run_id"]))
    except Exception:  # noqa: BLE001 - the turn goes on without them
        logger.warning("Chat notes could not be read for a turn", run_id=chat_stream.get("run_id"), exc_info=True)
        claimed = []
    if claimed:
        lines = notes_prompt(claimed)
        prompt = f"{lines}\n\n{prompt}" if prompt else lines
    from services.chat.attachments import attachments_line

    attached = attachments_line(await run_attachments(database, chat_stream))
    if attached:
        prompt = f"{prompt}\n\n{attached}" if prompt else attached
    return system, prompt


async def run_attachments(database: Any, chat_stream: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The files the owner attached to the message the run answers (none
    when it cannot be read: the turn goes on without them)."""
    from services.chat import ledger

    try:
        run = await ledger.get_run(database, str(chat_stream["run_id"]))
        message = await ledger.saved_message(database, run.user_message_uid) if run and run.user_message_uid else None
    except Exception:  # noqa: BLE001
        logger.warning("Chat attachments could not be read for a turn", run_id=chat_stream.get("run_id"), exc_info=True)
        return []
    return [dict(item) for item in (message.attachments or []) if isinstance(item, dict)] if message is not None else []


def _items(body: str) -> List[str]:
    body = body.strip()
    raw: List[str]
    try:
        parsed = json.loads(body)
        raw = [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []
    except ValueError:
        raw = [_LIST_MARK.sub("", line).strip().strip('"') for line in body.splitlines()]
    kept: List[str] = []
    for item in raw:
        text = " ".join(item.split())[:MAX_FOLLOWUP_CHARS]
        if text and text not in kept:
            kept.append(text)
    return kept[:MAX_FOLLOWUPS]


def split_followups(text: str) -> Tuple[str, List[str]]:
    """``(the reply without its last <followups> block, the suggestions in
    it)``. A block left open runs to the end of the reply."""
    start = text.rfind(FOLLOWUPS_OPEN)
    if start == -1:
        return text, []
    body = text[start + len(FOLLOWUPS_OPEN):]
    end = body.find(FOLLOWUPS_CLOSE)
    inner, after = (body[:end], body[end + len(FOLLOWUPS_CLOSE):]) if end != -1 else (body, "")
    visible = (text[:start] + after).rstrip()
    return visible, _items(inner)


__all__ = [
    "CHAT_GUIDE_BASE",
    "CHAT_REPLY_GUIDE",
    "FOLLOWUPS_CLOSE",
    "FOLLOWUPS_GUIDE",
    "MAX_FOLLOWUPS",
    "chat_turn",
    "run_attachments",
    "split_followups",
    "with_chat_guide",
]
