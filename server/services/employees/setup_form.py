"""A new employee's setup while the owner has Generated UI off
(``services/genui/switch.py``): the model fills a fixed form, and the server
draws the setup card from it.

The form is ``config/genui_fields_prompt.txt`` (written from
config/genui_catalog.json by client/src/lib/jsonRender/prompts.ts, `bun run
genui:prompts`): plain fields, so no catalogue rides the request. The card
is the core of a setup screen: who they are, their routine, the ground rules
with "Ask me before sending anything", and the Change and Hire buttons. It
goes back as an ordinary setup reply, ``{"text", "spec"}``, so the client
reads, completes and hires it exactly as it does a screen a model wrote
(genui/normalize.ts adds when they work and lays out the footer).

The fields are read as ``hire_employee`` reads a hire
(``HireEmployeeRequest``): a form without a name, a role or a step draws
nothing, and costs the one retry ``setup.py`` allows.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pydantic import ValidationError

from services.employees.context import SetupContext, render_context_block
from services.employees.genui_catalog import limit, load_genui_catalog
from services.employees.hire_request import HireEmployeeRequest
from services.employees.setup_prompt import CHANGE_PREFIX, JOB_PREFIX, PREAMBLE, HistoryTurn
from services.employees.setup_reply import json_object, parse_reply, usable_elements
from services.llm.protocol import Message

PROMPT_PATH = Path(__file__).resolve().parents[2] / "config" / "genui_fields_prompt.txt"

FORM_RETRY_NUDGE = (
    "That reply was cut off or was not valid JSON. Reply again with the complete JSON object only: "
    "single-line minified, no code fences."
)

#: The most ground rules the card shows besides "Ask me before sending anything".
MAX_RULES = 3
#: What a trigger may say; the client snaps its values to a schedule the
#: server runs (genui/hirePayload.ts snapTrigger), as it does a written screen's.
_TRIGGER_KEYS = ("kind", "app", "every", "at", "day")


@lru_cache(maxsize=1)
def fields_prompt() -> str:
    """The form (``config/genui_fields_prompt.txt``)."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def form_messages(
    context: SetupContext,
    job: str,
    *,
    change: Optional[str] = None,
    history: Sequence[HistoryTurn] = (),
) -> List[Message]:
    """The conversation for the form. On a change, the latest setup is read
    back as the form it describes; it already carries every earlier change."""
    system = Message(role="system", content=f"{PREAMBLE}\n\n{fields_prompt()}\n\n{render_context_block(context)}")
    if not change:
        return [system, Message(role="user", content=JOB_PREFIX + job)]
    fields = fields_from_reply(history[-1].reply) if history else {}
    if not fields:
        return [system, Message(role="user", content=f"{JOB_PREFIX}{job}\n\n{CHANGE_PREFIX}{change}")]
    return [
        system,
        Message(role="user", content=JOB_PREFIX + job),
        Message(role="assistant", content=_compact(fields)),
        Message(role="user", content=CHANGE_PREFIX + change),
    ]


def _paths() -> Dict[str, str]:
    return load_genui_catalog()["state_paths"]


def _is_ask_first(label: Any) -> bool:
    """The "Ask me before sending anything" rule itself, which the card always
    has. Only that wording: "Ask for a deposit before booking" is a rule."""
    wording = load_genui_catalog()["ask_first_label"]
    return isinstance(label, str) and " ".join(label.split()).casefold() == wording.casefold()


def _bound_value(bound: Any, state: Any) -> bool:
    """A Toggle's value: written out, or read from the state at its path."""
    if isinstance(bound, bool):
        return bound
    path = bound.get("$bindState") if isinstance(bound, dict) else None
    value = state
    for segment in str(path or "").strip("/").split("/") if path else []:
        value = value.get(segment) if isinstance(value, dict) else None
    return value is True


def fields_from_reply(raw: str) -> Dict[str, Any]:
    """The form a setup reply describes, whether this module drew it or a
    model wrote it while Generated UI was on. Empty when it names no one."""
    parsed = parse_reply(raw)
    state = parsed.spec.get("state") if isinstance(parsed.spec, dict) else None
    ask_first = _paths()["askFirst"]
    fields: Dict[str, Any] = {}
    rules: List[Dict[str, Any]] = []
    for element in usable_elements(parsed.spec):
        kind, props = element["type"], element["props"]
        if kind == "AgentCard" and "name" not in fields:
            fields.update({key: props.get(key) for key in ("name", "role", "description", "apps")})
        elif kind == "Plan" and "steps" not in fields:
            fields["steps"] = props.get("steps")
        elif kind == "Toggle":
            bound = props.get("checked", props.get("value"))
            label = props.get("label")
            path = bound.get("$bindState") if isinstance(bound, dict) else None
            if isinstance(label, str) and path != ask_first and not _is_ask_first(label):
                rules.append({"label": label, "value": _bound_value(bound, state)})
        elif kind == "Button" and element["press"][0] == "hire_employee":
            params = element["press"][1]
            fields.setdefault("trigger", params.get("trigger"))
            fields.setdefault("sends_via", params.get("sendsVia"))
    if not fields.get("name"):
        return {}
    form = {"text": parsed.text, **fields, "rules": rules}
    return {key: value for key, value in form.items() if value not in (None, "", [])}


def fill_form(raw: Any, job: str) -> str:
    """The setup reply drawn from the form a model filled in, or "" when the
    form names no one (no name, role or step)."""
    fields = json_object(raw)
    if fields is None:
        return ""
    rules = fields.get("rules") if isinstance(fields.get("rules"), list) else []
    try:
        hire = HireEmployeeRequest.model_validate(
            {
                "idempotency_key": "setup",
                "job": job,
                "name": fields.get("name"),
                "role": fields.get("role"),
                "description": fields.get("description"),
                "apps": fields.get("apps"),
                "steps": fields.get("steps"),
                "rules": {"items": [{"label": rule.get("label"), "value": rule.get("value") is True} for rule in rules if isinstance(rule, dict)]},
                "trigger": fields.get("trigger"),
                "sends_via": fields.get("sends_via"),
            }
        )
    except ValidationError:
        return ""
    if hire.missing_identity() is not None:
        return ""

    paths = _paths()
    rules_key = paths["rules"].strip("/")
    kept = [item for item in hire.rules.items if item.label and not _is_ask_first(item.label)][:MAX_RULES]
    toggles = {f"rule{n}": item for n, item in enumerate(kept, 1)}
    params: Dict[str, Any] = {"name": hire.name, "role": hire.role, "apps": hire.apps}
    if hire.trigger is not None:
        trigger = fields["trigger"]
        params["trigger"] = {
            key: str(trigger[key])[:40]
            for key in _TRIGGER_KEYS
            if isinstance(trigger.get(key), (str, int)) and not isinstance(trigger.get(key), bool)
        }
    if hire.sends_via:
        params["sendsVia"] = hire.sends_via

    elements: Dict[str, Any] = {
        "root": {"type": "Stack", "props": {"direction": "vertical", "gap": "md"}, "children": ["agent", "plan", "rules", "change", "hire"]},
        "agent": {
            "type": "AgentCard",
            "props": {"name": hire.name, "role": hire.role, "description": hire.description, "apps": hire.apps, "status": "ready"},
            "children": [],
        },
        "plan": {
            "type": "Plan",
            "props": {"title": "Their routine", "steps": [step.model_dump(exclude_none=True) for step in hire.steps if step.title]},
            "children": [],
        },
        "rules": {"type": "Card", "props": {"title": "Ground rules"}, "children": ["askFirst", *toggles]},
        "askFirst": {
            "type": "Toggle",
            "props": {"label": load_genui_catalog()["ask_first_label"], "checked": {"$bindState": paths["askFirst"]}},
            "children": [],
        },
        **{
            key: {"type": "Toggle", "props": {"label": item.label, "checked": {"$bindState": f"{paths['rules']}/{key}"}}, "children": []}
            for key, item in toggles.items()
        },
        "change": {"type": "Button", "props": {"label": "Change something", "variant": "secondary"}, "on": {"press": {"action": "refine"}}, "children": []},
        "hire": {
            "type": "Button",
            "props": {"label": f"Hire {hire.name}", "variant": "primary"},
            "on": {"press": {"action": "hire_employee", "params": params}},
            "children": [],
        },
    }
    state = {rules_key: {paths["askFirst"].rsplit("/", 1)[1]: True, **{key: item.value for key, item in toggles.items()}}}
    text = " ".join(fields["text"].split())[: limit("maxText")] if isinstance(fields.get("text"), str) else ""
    return _compact({"text": text or f"Meet {hire.name}.", "spec": {"root": "root", "state": state, "elements": elements}})


__all__ = ["FORM_RETRY_NUDGE", "MAX_RULES", "fields_from_reply", "fields_prompt", "fill_form", "form_messages"]
