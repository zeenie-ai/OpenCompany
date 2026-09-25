"""The setup model's instructions and conversation.

The preamble and the catalogue rules are the prototype's
(design_handoff_opencompany_home/reference/OpenCompany Home.dc.html,
``catalogPrompt()`` and the system line in ``create()``), with these
additions:

- plan steps may name the app they use (``app``);
- ground-rule Toggles bind under ``/rules``, and "Ask me before sending
  anything" binds to ``/rules/askFirst``, on by default; Choices bind under
  ``/choices``;
- the hire button's params also carry ``trigger`` (what starts the work)
  and ``sendsVia`` (the app they answer or report through);
- a push toward app or schedule triggers, since a chat-only employee
  cannot be given work from Normal mode yet.

The component and action lines come from config/genui_catalog.json, which
the client's renderer is held to. The server writes every message, the
"The job: " and "Change the setup: " prefixes included; the client only
sends the owner's words and the earlier replies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from services.employees.context import SetupContext, render_context_block
from services.employees.genui_catalog import load_genui_catalog
from services.llm.protocol import Message

PREAMBLE = (
    "You set up AI employees inside OpenCompany, an operating system where non-technical people hire AI "
    "employees that do real work with their apps. Speak plainly and warmly: no jargon, no emoji."
)

JOB_PREFIX = "The job: "
CHANGE_PREFIX = "Change the setup: "

#: History budget: the latest reply plus at most this many earlier pairs,
#: within HISTORY_MAX_CHARS characters of replies and changes.
HISTORY_EARLIER_PAIRS = 2
HISTORY_MAX_CHARS = 40_000


def catalog_prompt(catalog: Optional[Dict[str, Any]] = None) -> str:
    c = catalog or load_genui_catalog()
    paths = c["state_paths"]
    ask_first = c["ask_first_label"]
    kinds = "|".join(f'"{kind}"' for kind in c["trigger"]["kinds"])
    every = "|".join(f'"{value}"' for value in c["trigger"]["every"])
    lines = [
        'Reply ONLY with a JSON object, no code fences: {"text": string, "spec": UISpec}.',
        '"text": one short, warm sentence introducing the new employee by name.',
        '"spec" is the new employee\'s setup screen. Root is a vertical Stack with, in order:',
        "1) AgentCard — a friendly first name, a plain job title as role, one-sentence description, apps they "
        'use, status "ready".',
        '2) Plan titled "Their routine" with 3-5 steps; the first step has role "trigger" (e.g. "When a message '
        'arrives", "Every weekday at 8am"). Step titles are short present-tense actions. A step that uses an app '
        'names it in "app".',
        f'3) Card titled "Ground rules" containing 2-3 Toggles bound to state under "{paths["rules"]}" (always '
        f'include "{ask_first}" bound to "{paths["askFirst"]}", true in "state") and optionally one Choice bound '
        f'under "{paths["choices"]}" (e.g. how often to report).',
        '4) If a needed app is not connected: a Card with tone "trigger" saying so, with a Button (connect_app).',
        '5) A horizontal Stack with Button "Hire {name}" (primary, action hire_employee, actionParams {name, role, '
        'apps, trigger, sendsVia}) and Button "Change something" (secondary, action refine).',
        f'"trigger" is {{"kind": {kinds}, "app"?, "every"?: {every}, "at"?: "HH:MM", "day"?}}: '
        '"app_event" when an app\'s new message or email starts the work, "schedule" for routine work. Prefer '
        'those; use "manual" only when the job is purely on request. "sendsVia" is the app they answer or report '
        "through.",
        'UISpec is flat: {"root": id, "state": {initial values}, "elements": {id: {"type", "props", "children": '
        '[ids], "visible"?: condition}}}. Max 12 elements. Every child id must exist.',
        'Each element looks like {"type":"Text","props":{"text":"…"},"children":[]} — all component props go '
        'INSIDE "props".',
        'Every Stack and Card MUST list its child ids in "children" — elements not listed as someone\'s child are '
        "not shown.",
        'Keep it compact: single-line minified JSON, no code fences, short ids ("a","b","c"…), every string under '
        "70 characters, descriptions one short line, step details under 8 words.",
        "Components (use ONLY these):",
    ]
    lines += [f"- {name} {spec['props']} — {spec['description']}" for name, spec in c["components"].items()]
    lines.append(f"Tones: {', '.join(c['tones'])}.")
    lines.append(
        'Dynamic props: {"$state":"/path"}, {"$template":"Reports ${/freq}"}, '
        '{"$cond":{"$state":"/x","eq":"y"},"$then":a,"$else":b}. Conditions: {"$state":"/path"} with optional '
        '"eq" or "not":true.'
    )
    lines.append("Button actions:")
    lines += [f"- {name} {description}" for name, description in c["actions"].items()]
    lines.append("When asked to change the setup, return the full updated JSON in the same format.")
    return "\n".join(lines)


def system_prompt(context: SetupContext, catalog: Optional[Dict[str, Any]] = None) -> str:
    return f"{PREAMBLE}\n\n{catalog_prompt(catalog)}\n\n{render_context_block(context)}"


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
    catalog: Optional[Dict[str, Any]] = None,
) -> List[Message]:
    """The whole conversation. Roles alternate: the first kept reply answers
    the job message even when earlier turns were dropped."""
    messages = [Message(role="system", content=system_prompt(context, catalog)), Message(role="user", content=JOB_PREFIX + job)]
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
