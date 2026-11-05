"""Reading a model's setup reply on the server: is it worth showing?

The client reads replies (genui/parse.ts) and makes them safe to render
(genui/normalize.ts). The server only needs to know whether the client
will find anything renderable, to decide whether to spend one retry, and
which app names the reply mentions, to resolve them for the client. This
is a port of the client's parse and its "anything renderable" rule, kept
in step by the shared corpus in
``client/src/features/home/genui/__fixtures__/replies.json``
(tests/services/employees/test_setup_salvage_parity.py).

The rules, as the client applies them: take the first ``{`` to the last
``}``; failing that, cut back to the last complete value and close what is
open. A reply is renderable when its ``spec`` has an ``elements`` object
with at least one usable element: a known component with a usable id,
where a Button needs a label and a known action and an AgentCard needs a
name. A Button's action is read as the client reads it: the element's
``on.press`` (one binding, or the first of a list with a known action),
else ``on`` written inside its props, else the older ``action`` /
``actionParams`` props.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.employees.genui_catalog import canonical_action, component_types, limit

_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)
_QUOTED_TEXT = re.compile(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"')
_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_FORBIDDEN_KEYS = frozenset({"__proto__", "constructor", "prototype"})
# Element keys that are never props; the client's normalize.ts META_KEYS.
_META_KEYS = frozenset({"type", "props", "children", "visible", "watch", "id", "on", "repeat", "slots"})
_MAX_REPAIR_CUTS = 80
_MAX_RAW_ELEMENTS = 64


def _reject_constant(name: str) -> Any:
    # JSON.parse refuses NaN and Infinity; Python's json accepts them.
    raise ValueError(f"not JSON: {name}")


def _loads(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_constant)


def _js_truthy(value: Any) -> bool:
    """JavaScript truthiness: empty objects and arrays count as true."""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value != ""
    return True


@dataclass
class ParsedReply:
    text: str
    spec: Any
    failed: bool


def _as_reply(value: Any) -> Optional[ParsedReply]:
    if not isinstance(value, dict):
        return None
    if not _js_truthy(value.get("text")) and not _js_truthy(value.get("spec")):
        return None
    spec = value.get("spec")
    spec = spec if isinstance(spec, (dict, list)) else None
    text = value.get("text")
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    return ParsedReply(text=text, spec=spec, failed=spec is None)


def _open_state(source: str) -> tuple:
    stack: List[str] = []
    in_string = False
    escaped = False
    for ch in source:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    return stack, in_string


def repair_json(source: str) -> Any:
    """Parse a JSON object that was cut off: try every point where a value
    ended, latest first, closing the brackets still open."""
    cuts: List[int] = []
    in_string = False
    escaped = False
    for i, ch in enumerate(source):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == ",":
            cuts.append(i)
        elif ch in "}]":
            cuts.append(i + 1)
    for tried, cut in enumerate(reversed(cuts)):
        if tried >= _MAX_REPAIR_CUTS:
            break
        prefix = re.sub(r",\s*$", "", source[:cut])
        stack, in_string = _open_state(prefix)
        if in_string:
            continue
        closers = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
        try:
            return _loads(prefix + closers)
        except (ValueError, RecursionError):
            continue
    return None


def parse_reply(raw: Any) -> ParsedReply:
    reply = str(raw if raw is not None else "")[: limit("maxReplyChars")]
    unfenced = _FENCE.sub("", reply)
    start = unfenced.find("{")
    if start >= 0:
        body = unfenced[start:]
        end = body.rfind("}")
        if end > 0:
            try:
                whole = _as_reply(_loads(body[: end + 1]))
                if whole is not None:
                    return whole
            except (ValueError, RecursionError):
                pass
        repaired = _as_reply(repair_json(body))
        if repaired is not None:
            return repaired
    quoted = _QUOTED_TEXT.search(reply)
    if quoted:
        try:
            return ParsedReply(text=_loads(f'"{quoted.group(1)}"'), spec=None, failed=True)
        except ValueError:
            pass
    if start < 0 and reply.strip() and not re.search(r"[{}\[\]]", reply):
        return ParsedReply(text=reply.strip(), spec=None, failed=True)
    return ParsedReply(text="", spec=None, failed=True)


def json_object(raw: Any) -> Optional[Dict[str, Any]]:
    """The JSON object a reply holds, read the way ``parse_reply`` reads one
    (fences and chatter around it ignored, a cut-off one repaired), or None
    when it holds none."""
    reply = _FENCE.sub("", str(raw if raw is not None else "")[: limit("maxReplyChars")])
    start = reply.find("{")
    if start < 0:
        return None
    body = reply[start:]
    end = body.rfind("}")
    value: Any = None
    if end > 0:
        try:
            value = _loads(body[: end + 1])
        except (ValueError, RecursionError):
            value = None
    if not isinstance(value, dict):
        value = repair_json(body)
    return value if isinstance(value, dict) else None


def _text(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return str(value).strip()


def _usable_id(element_id: str) -> bool:
    return (
        len(element_id) <= limit("maxIdLength")
        and bool(_ID.match(element_id))
        and element_id not in _FORBIDDEN_KEYS
    )


def _props(element: Dict[str, Any]) -> Dict[str, Any]:
    props = {key: value for key, value in element.items() if key not in _META_KEYS and key not in _FORBIDDEN_KEYS}
    inner = element.get("props")
    if isinstance(inner, dict):
        props.update({key: value for key, value in inner.items() if key not in _FORBIDDEN_KEYS})
    return props


def _pressed(on: Any) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The first binding under ``on.press`` (one, or a list) whose action is known."""
    if not isinstance(on, dict):
        return None
    press = on.get("press")
    for binding in press if isinstance(press, list) else [press]:
        if isinstance(binding, dict):
            action = canonical_action(binding.get("action"))
            if action:
                params = binding.get("params")
                return action, params if isinstance(params, dict) else {}
    return None


def button_press(element: Dict[str, Any], props: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """A Button's action and params: ``on.press`` beside its props, or
    inside them, else the older ``action`` / ``actionParams`` props."""
    pressed = _pressed(element.get("on")) or _pressed(props.get("on"))
    if pressed:
        return pressed
    action = canonical_action(props.get("action"))
    if action is None:
        return None
    params = props.get("actionParams")
    return action, params if isinstance(params, dict) else {}


def usable_elements(spec: Any) -> List[Dict[str, Any]]:
    """The elements the client would keep, with their props merged (and a
    Button's ``press``: its action and params)."""
    if not isinstance(spec, dict) or not isinstance(spec.get("elements"), dict):
        return []
    known = component_types()
    kept: List[Dict[str, Any]] = []
    for element_id, element in spec["elements"].items():
        if len(kept) >= _MAX_RAW_ELEMENTS:
            break
        if not isinstance(element_id, str) or not _usable_id(element_id) or not isinstance(element, dict):
            continue
        kind = element.get("type")
        if kind not in known:
            continue
        props = _props(element)
        usable: Dict[str, Any] = {"type": kind, "props": props}
        if kind == "Button":
            press = button_press(element, props)
            if not _text(props.get("label")) or press is None:
                continue
            usable["press"] = press
        if kind == "AgentCard" and not _text(props.get("name")):
            continue
        kept.append(usable)
    return kept


def is_salvageable(raw: Any) -> bool:
    """Would the client find anything to render in this reply?"""
    parsed = parse_reply(raw)
    return not parsed.failed and bool(usable_elements(parsed.spec))


def _strings(values: Any) -> Iterable[str]:
    if isinstance(values, list):
        for value in values:
            text = _text(value)
            if text:
                yield text


def app_names(raw: Any) -> List[str]:
    """Every app name the reply mentions, in first-seen order: the agent's
    apps, the steps' apps, connect buttons, and the hire button's apps,
    trigger app and delivery app."""
    parsed = parse_reply(raw)
    seen: Dict[str, str] = {}

    def add(name: Any) -> None:
        text = _text(name)[:40]
        if text and text.lower() not in seen:
            seen[text.lower()] = text

    for element in usable_elements(parsed.spec):
        props = element["props"]
        if element["type"] == "AgentCard":
            for app in _strings(props.get("apps")):
                add(app)
        elif element["type"] == "Plan" and isinstance(props.get("steps"), list):
            for step in props["steps"]:
                if isinstance(step, dict):
                    add(step.get("app"))
        elif element["type"] == "Button":
            action, params = element["press"]
            if action == "connect_app":
                add(params.get("app"))
            elif action == "hire_employee":
                for app in _strings(params.get("apps")):
                    add(app)
                trigger = params.get("trigger")
                if isinstance(trigger, dict):
                    add(trigger.get("app"))
                add(params.get("sendsVia"))
    return list(seen.values())


__all__ = [
    "ParsedReply",
    "app_names",
    "button_press",
    "is_salvageable",
    "json_object",
    "parse_reply",
    "repair_json",
    "usable_elements",
]
