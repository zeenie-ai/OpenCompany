"""The setup model's instructions and conversation.

The preamble and the catalogue rules are the prototype's
(design_handoff_opencompany_home/reference/OpenCompany Home.dc.html,
``catalogPrompt()`` and the system line in ``create()``), with these
additions:

- plan steps may name the app they use (``app``);
- ground-rule Toggles bind under ``/rules``, and "Ask me before sending
  anything" binds to ``/rules/askFirst``, on by default; Choices bind under
  ``/choices``;
- the hire button's params also carry ``trigger`` (what starts the work,
  with a schedule's time one of the catalogue's ``trigger.times``) and
  ``sendsVia`` (the app they answer or report through). No kind of trigger
  is preferred: every employee can be talked to on Home, so one that works
  when the owner messages them is as good as any;
- the spec is in json-render's shape (catalogue ``spec_version`` 2): a
  Toggle binds ``checked``, and a Button names its action in the element's
  ``on.press`` (``{"action", "params"}``) beside its props.

The catalogue rules are ``config/genui_prompt.txt``, written from
config/genui_catalog.json by json-render's catalog.prompt()
(client/src/lib/jsonRender/prompts.ts, `bun run genui:prompts`); a component
the client inserts itself (``inserted``) is not offered to the model. The
server writes every message, the "The job: " and "Change the setup: "
prefixes included; the client only sends the owner's words and the earlier
replies.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence

from services.employees.context import SetupContext, render_context_block
from services.llm.protocol import Message

PROMPT_PATH = Path(__file__).resolve().parents[2] / "config" / "genui_prompt.txt"

PREAMBLE = (
    "You set up AI employees inside OpenCompany, an operating system where non-technical people hire AI "
    "employees that do real work with their apps. Speak plainly and warmly: no jargon, no emoji. "
    "Hiring has three steps: describe the job, review it, Hire. Specialist teammates are selected behind the scenes. "
    "Do not ask the owner to choose agents, nodes, models, providers, handles, schemas, or workflow settings. "
    "Describe what the employee does, when they work, the apps they need, and the approval rule. "
    "Keep changes conversational through Change something."
)

JOB_PREFIX = "The job: "
CHANGE_PREFIX = "Change the setup: "

#: History budget: the latest reply plus at most this many earlier pairs,
#: within HISTORY_MAX_CHARS characters of replies and changes.
HISTORY_EARLIER_PAIRS = 2
HISTORY_MAX_CHARS = 40_000


@lru_cache(maxsize=1)
def catalog_prompt() -> str:
    """The catalogue rules (``config/genui_prompt.txt``)."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def system_prompt(context: SetupContext) -> str:
    return f"{PREAMBLE}\n\n{catalog_prompt()}\n\n{render_context_block(context)}"


@dataclass
class HistoryTurn:
    """One earlier reply. ``change`` is what the owner asked to change
    before it, None for the reply to the job itself."""

    reply: str
    change: Optional[str] = None


def trim_history(history: Sequence[HistoryTurn]) -> List[HistoryTurn]:
    """The latest reply, plus up to HISTORY_EARLIER_PAIRS earlier ones
    while they fit in HISTORY_MAX_CHARS. The latest reply already carries
    every earlier change, so dropping older turns loses no decisions."""
    if not history:
        return []
    kept = [history[-1]]
    used = len(history[-1].reply) + len(history[-1].change or "")
    for turn in reversed(history[:-1]):
        if len(kept) > HISTORY_EARLIER_PAIRS:
            break
        size = len(turn.reply) + len(turn.change or "")
        if used + size > HISTORY_MAX_CHARS:
            break
        kept.insert(0, turn)
        used += size
    return kept


def build_messages(
    context: SetupContext,
    job: str,
    *,
    change: Optional[str] = None,
    history: Sequence[HistoryTurn] = (),
) -> List[Message]:
    """The whole conversation. Roles alternate: the first kept reply answers
    the job message even when earlier turns were dropped."""
    messages = [Message(role="system", content=system_prompt(context)), Message(role="user", content=JOB_PREFIX + job)]
    if change:
        kept = trim_history(history)
        if kept:
            messages.append(Message(role="assistant", content=kept[0].reply))
            # A later turn without its change request cannot keep the roles
            # alternating; its reply is superseded by the next one anyway.
            for turn in kept[1:]:
                if turn.change:
                    messages.append(Message(role="user", content=CHANGE_PREFIX + turn.change))
                    messages.append(Message(role="assistant", content=turn.reply))
        messages.append(Message(role="user", content=CHANGE_PREFIX + change))
    return messages


RETRY_NUDGE = (
    "That reply was cut off or was not valid JSON. Reply again with the complete JSON object only: "
    "single-line minified, no code fences, at most 12 elements."
)


__all__ = [
    "CHANGE_PREFIX",
    "HISTORY_EARLIER_PAIRS",
    "HISTORY_MAX_CHARS",
    "HistoryTurn",
    "JOB_PREFIX",
    "PREAMBLE",
    "RETRY_NUDGE",
    "build_messages",
    "catalog_prompt",
    "system_prompt",
    "trim_history",
]
