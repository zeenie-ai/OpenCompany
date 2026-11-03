"""What a chat run's tools add to its reply (docs-internal/chat_protocol.md,
"Messages" and "Generated UI").

A tool of the agent answering a run records a **part** on the run
(``record_part``, keyed so an activity retry writes the same row again) and
shows it live through the run's ``activity.*`` events. Generated UI is the
first kind: ``show_ui`` saves the checked spec and publishes it as an empty
``activity.snapshot`` followed by one ``activity.delta`` per patch
(``services/genui/patches.py``); the client paces what arrives.

Sources (``services/chat/sources.py``) are the numbered results of a search
the answering agent ran. Approvals name the drafts it made that wait for the
owner (``services/approvals/tool_calls.py``): the card reads them live from
the approvals store, so the part only names them. Artifacts name the
documents (Canvas notes) it wrote or revised, at the version it left them
(``show_artifact``, called by the canvas tool): a card that opens the
Canvas on it.

When the run ends, ``seal_parts`` copies its parts into the reply message's
``parts`` (``{"ui": [...], "sources": [...], "approvals": [...],
"artifacts": [...]}``), creating an empty reply when the run showed an
interface, made a draft or wrote a document but wrote no text, so a reload
draws what the run showed. Every write here is best effort for the run: a part that cannot be
saved is logged, and the run goes on.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from core.logging import get_logger
from models.chat import ChatRun, ChatRunPart
from models.database import ChatMessage
from services.genui.spec import has_expression

logger = get_logger(__name__)

#: The ``activity_type`` of a generated UI's events.
JSON_RENDER = "json_render"
#: The kinds of part a run collects for its reply.
PART_KINDS = ("ui", "sources", "approvals", "artifacts")


def ui_part_id(run_id: str, tool_call_id: str) -> str:
    """A UI's id: stable across retries of the call that showed it."""
    digest = hashlib.sha256(f"{run_id}\x00{tool_call_id}".encode()).hexdigest()[:16]
    return f"ui_{digest}"


async def record_part(database: Any, run_id: str, *, kind: str, key: str, payload: Dict[str, Any]) -> None:
    """Save a part on its run, replacing an earlier save under the same key."""
    async with database.reserved_session() as session:
        result = await session.execute(select(ChatRunPart).where(ChatRunPart.run_id == run_id, ChatRunPart.key == key))
        part = result.scalar_one_or_none()
        if part is None:
            part = ChatRunPart(run_id=run_id, kind=kind, key=key, payload=dict(payload))
        else:
            part.kind = kind
            part.payload = dict(payload)
        session.add(part)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise


async def run_parts(database: Any, run_id: str) -> List[ChatRunPart]:
    """A run's parts, in the order they were first saved."""
    async with database.get_session() as session:
        result = await session.execute(select(ChatRunPart).where(ChatRunPart.run_id == run_id).order_by(ChatRunPart.id))
        return list(result.scalars().all())


def _publish(stream: Mapping[str, Any], suffix: str, fields: Dict[str, Any], event_key: str) -> None:
    try:
        from services.chat.hub import publish_run_event

        workflow_id = stream.get("workflow_id")
        publish_run_event(
            run_id=stream["run_id"],
            session_id=stream["session_id"],
            workflow_id=workflow_id if isinstance(workflow_id, str) else None,
            suffix=suffix,
            fields=fields,
            event_key=event_key,
        )
    except Exception:  # noqa: BLE001 - the part is saved; the live view is a courtesy
        logger.warning("Chat part event could not be published", run_id=stream.get("run_id"), exc_info=True)


async def show_ui(database: Any, stream: Mapping[str, Any], *, tool_call_id: str, spec: Dict[str, Any]) -> str:
    """Save a checked spec on the run and stream it to the chat. Returns the
    UI's id."""
    from services.genui.patches import spec_to_patches

    run_id = stream["run_id"]
    part_id = ui_part_id(run_id, tool_call_id)
    payload = {
        "part_id": part_id,
        "spec": spec,
        "state": dict(spec.get("state") or {}),
        "state_revision": 0,
        "elements": len(spec.get("elements") or {}),
    }
    await record_part(database, run_id, kind="ui", key=f"ui:{part_id}", payload=payload)
    _publish(
        stream,
        "activity.snapshot",
        {
            "message_id": part_id,
            "activity_type": JSON_RENDER,
            "content": {"root": "", "state": {}, "elements": {}},
            "replace": True,
        },
        f"{part_id}:snapshot",
    )
    for index, patch in enumerate(spec_to_patches(spec)):
        _publish(
            stream,
            "activity.delta",
            {"message_id": part_id, "activity_type": JSON_RENDER, "patch": [patch]},
            f"{part_id}:{index}",
        )
    return part_id


async def show_approval(database: Any, stream: Mapping[str, Any], *, approval_id: str, tool_call_id: Optional[str]) -> None:
    """Name a draft the run made on its reply, and show its card live."""
    payload = {"approval_id": approval_id, "tool_call_id": tool_call_id}
    await record_part(database, stream["run_id"], kind="approvals", key=f"approval:{approval_id}", payload=payload)
    _publish(
        stream,
        "activity.snapshot",
        {"message_id": approval_id, "activity_type": "approval", "content": payload, "replace": True},
        f"{approval_id}:approval",
    )


async def show_artifact(database: Any, stream: Mapping[str, Any], *, artifact: Dict[str, Any]) -> None:
    """Name a document the run wrote on the Canvas (``{workflow_id,
    canvas_node_id, item_id, version, title, format}``) on its reply, and
    show its card live. A newer version of the same item replaces it."""
    item_id = str(artifact.get("item_id") or "")
    if not item_id:
        return
    await record_part(database, stream["run_id"], kind="artifacts", key=f"artifact:{item_id}", payload=dict(artifact))
    _publish(
        stream,
        "activity.snapshot",
        {"message_id": f"artifact_{item_id}", "activity_type": "artifact", "content": dict(artifact), "replace": True},
        f"artifact:{item_id}:{artifact.get('version')}",
    )


#: What names one item of each kind, for merging a run's parts into a reply.
_ITEM_KEYS = {"ui": "part_id", "sources": "n", "approvals": "approval_id", "artifacts": "item_id"}


def grouped_parts(parts: List[ChatRunPart]) -> Dict[str, List[Dict[str, Any]]]:
    """A run's parts as a reply's ``parts`` holds them: ``{"ui": [payload],
    "sources": [item], "approvals": [{approval_id, tool_call_id}]}`` (a
    sources part holds one tool call's items)."""
    grouped: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for part in parts:
        if part.kind not in PART_KINDS:
            continue
        payload = dict(part.payload or {})
        if part.kind == "sources":
            grouped.setdefault("sources", []).extend(dict(item) for item in payload.get("items") or [] if isinstance(item, dict))
        else:
            grouped.setdefault(part.kind, []).append(payload)
    return dict(grouped)


#: Kinds whose run copy is the newer one: a document's card names the latest
#: version the run wrote, and nothing the owner does changes it.
_RUN_COPY_WINS = frozenset({"artifacts"})


def _merged(kind: str, existing: List[Any], incoming: List[Dict[str, Any]]) -> List[Any]:
    """The reply's items of a kind with the run's new ones added. An item the
    reply already holds stays as it is there: the owner may have changed it
    since (an interface's state), and the run's copy is older. A document's
    card is the exception: the run's copy names a newer version."""
    key = _ITEM_KEYS.get(kind)
    if key is None:
        return incoming
    if kind in _RUN_COPY_WINS:
        newer = {item.get(key): item for item in incoming}
        kept = [newer.pop(item.get(key), item) if isinstance(item, dict) else item for item in existing]
        return [*kept, *newer.values()]
    held = {item.get(key) for item in existing if isinstance(item, dict)}
    return [*existing, *[item for item in incoming if item.get(key) not in held]]


async def seal_parts(database: Any, run: ChatRun) -> bool:
    """Put the run's parts on its reply message (creating an empty reply
    when it wrote none). Returns whether the thread changed."""
    try:
        grouped = grouped_parts(await run_parts(database, run.run_id))
        if not grouped:
            return False
        uid = run.reply_message_uid
        async with database.reserved_session() as session:
            reply: Optional[ChatMessage] = None
            if uid:
                found = await session.execute(select(ChatMessage).where(ChatMessage.uid == uid))
                reply = found.scalar_one_or_none()
            if reply is not None:
                current = dict(reply.parts or {})
                merged = {kind: _merged(kind, list(current.get(kind) or []), items) for kind, items in grouped.items()}
                if all(current.get(kind) == items for kind, items in merged.items()):
                    return False
                reply.parts = {**current, **merged}
                session.add(reply)
            else:
                # Sources show only where a reply cites them: alone they
                # are no reason to make one.
                if not (grouped.get("ui") or grouped.get("approvals") or grouped.get("artifacts")):
                    return False
                current = await session.get(ChatRun, run.run_id)
                stopped = current is not None and current.state in ("stopping", "stopped")
                await database.append_chat_row(
                    session,
                    session_id=run.session_id,
                    role="assistant",
                    message="",
                    execution_id=run.run_key,
                    uid=uid,
                    run_id=run.run_id,
                    status="stopped" if stopped else "complete",
                    parts=grouped,
                    meta={"sealed": True},
                )
            await session.commit()
        return True
    except Exception:  # noqa: BLE001 - the run ends either way
        logger.warning("Chat run parts could not be sealed into the reply", run_id=run.run_id, exc_info=True)
        return False


# ----- an interface the owner uses -------------------------------------------

#: Assistant messages searched for an interface, newest first.
_SEARCH_MESSAGES = 200
#: The most a press or a state change may carry.
_MAX_EVENT_BYTES = 4096
_MAX_STATE_BYTES = 16384
_MAX_CHANGES = 32


class UiRefused(ValueError):
    """A press or a state change that does not fit the interface."""


def _state_path(path: Any) -> List[str]:
    from services.genui.spec import canonical_path, load_catalog

    canonical = canonical_path(path, int(load_catalog()["limits"]["max_path_segments"]))
    if canonical is None:
        raise UiRefused(f"{path!r} is not a usable state path")
    return [part.replace("~1", "/").replace("~0", "~") for part in canonical[1:].split("/")]


def _set_path(state: Any, segments: List[str], value: Any) -> Any:
    """``state`` with ``value`` at ``segments``, copied along the way (the
    client's ``writePath``): missing objects are made, an array index may
    reach one past the end."""
    key, rest = segments[0], segments[1:]
    if isinstance(state, list):
        if not key.isdigit() or int(key) > len(state):
            raise UiRefused("a state path points past the end of a list")
        at = int(key)
        out = list(state)
        current = state[at] if at < len(state) else None
        item = _set_path(current, rest, value) if rest else value
        if at == len(out):
            out.append(item)
        else:
            out[at] = item
        return out
    base = dict(state) if isinstance(state, dict) else {}
    base[key] = _set_path(base.get(key), rest, value) if rest else value
    return base


def _size(value: Any) -> int:
    import json

    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


async def find_ui_part(database: Any, session_id: str, part_id: str) -> Optional[Dict[str, Any]]:
    """The interface ``part_id`` in this session: on a saved reply, or on a
    run whose reply is not saved yet. None when this session has none."""
    async with database.get_session() as session:
        result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.id.desc())
            .limit(_SEARCH_MESSAGES)
        )
        for message in result.scalars().all():
            for item in (message.parts or {}).get("ui") or []:
                if isinstance(item, dict) and item.get("part_id") == part_id:
                    return dict(item)
        result = await session.execute(
            select(ChatRunPart, ChatRun)
            .join(ChatRun, ChatRun.run_id == ChatRunPart.run_id)
            .where(ChatRunPart.key == f"ui:{part_id}", ChatRun.session_id == session_id)
        )
        found = result.first()
        return dict(found[0].payload or {}) if found is not None else None


def check_ui_event(part: Mapping[str, Any], element_id: Any, action: Any, params: Any) -> Dict[str, Any]:
    """A button press, checked against the interface it came from: the
    element is a Button whose press runs ``action``, and the params name only
    what its binding declares. A param the binding writes out is the saved
    value whatever the press says; one read from the state is the press's.
    Returns ``{label, params}``."""
    elements = (part.get("spec") or {}).get("elements") or {}
    element = elements.get(element_id) if isinstance(element_id, str) else None
    if not isinstance(element, dict) or element.get("type") != "Button":
        raise UiRefused("the interface has no such button")
    binding = ((element.get("on") or {}).get("press")) or {}
    if not isinstance(binding, dict) or binding.get("action") != action:
        raise UiRefused("that button does not run that action")
    if params is None:
        params = {}
    if not isinstance(params, dict) or _size(params) > _MAX_EVENT_BYTES:
        raise UiRefused("the press carries more than its button declares")
    declared = binding.get("params") or {}
    extra = sorted(set(params) - set(declared))
    if extra:
        raise UiRefused(f"the button does not send {', '.join(extra)}")
    fixed = {key: value for key, value in declared.items() if not has_expression(value)}
    label = (element.get("props") or {}).get("label")
    return {"label": label if isinstance(label, str) else "", "params": {**params, **fixed}}


def ui_event_message(*, part_id: str, element_id: str, label: str, action: str, params: Mapping[str, Any]) -> str:
    """What the employee reads for a button press: one bracketed line, so the
    agent's prompt can say such lines come from OpenCompany, not the owner."""
    import json

    payload = {"ui_id": part_id, "element": element_id, "label": label, "action": action, "params": dict(params)}
    return f"[ui-event]{json.dumps(payload, ensure_ascii=False, separators=(', ', ': '))}[/ui-event]"


def ui_state_message(*, part_id: str, state: Mapping[str, Any]) -> str:
    """What the employee reads for what the owner set in an interface
    without pressing anything: its whole state, one bracketed line."""
    import json

    payload = {"ui_id": part_id, "state": dict(state)}
    return f"[ui-state]{json.dumps(payload, ensure_ascii=False, separators=(', ', ': '), default=str)}[/ui-state]"


async def update_ui_state(database: Any, session_id: str, part_id: str, changes: Any) -> Optional[Dict[str, Any]]:
    """Apply what the owner set (``[{path, value}]``, the last value per path
    winning) to the interface's saved state. Returns ``{state,
    state_revision}``; :class:`UiRefused` for a change that does not fit, or
    None when the session has no such interface."""
    if not isinstance(changes, list) or not changes or len(changes) > _MAX_CHANGES:
        raise UiRefused(f"changes is a list of 1 to {_MAX_CHANGES} {{path, value}}")
    parsed = []
    for change in changes:
        if not isinstance(change, dict) or "path" not in change:
            raise UiRefused("each change is {path, value}")
        if _size(change.get("value")) > _MAX_EVENT_BYTES:
            raise UiRefused("a value is too large")
        parsed.append((_state_path(change["path"]), change.get("value")))

    def applied(item: Mapping[str, Any]) -> Dict[str, Any]:
        state = item.get("state") if isinstance(item.get("state"), dict) else dict((item.get("spec") or {}).get("state") or {})
        for segments, value in parsed:
            state = _set_path(state, segments, value)
        if _size(state) > _MAX_STATE_BYTES:
            raise UiRefused("the interface's state is too large")
        return {**item, "state": state, "state_revision": int(item.get("state_revision") or 0) + 1}

    async with database.reserved_session() as session:
        result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.id.desc())
            .limit(_SEARCH_MESSAGES)
        )
        for message in result.scalars().all():
            items = (message.parts or {}).get("ui") or []
            for index, item in enumerate(items):
                if isinstance(item, dict) and item.get("part_id") == part_id:
                    updated = applied(item)
                    ui = [*items[:index], updated, *items[index + 1:]]
                    message.parts = {**(message.parts or {}), "ui": ui}
                    session.add(message)
                    await session.commit()
                    return {"state": updated["state"], "state_revision": updated["state_revision"]}
        result = await session.execute(
            select(ChatRunPart)
            .join(ChatRun, ChatRun.run_id == ChatRunPart.run_id)
            .where(ChatRunPart.key == f"ui:{part_id}", ChatRun.session_id == session_id)
        )
        part = result.scalar_one_or_none()
        if part is None:
            return None
        updated = applied(part.payload or {})
        part.payload = updated
        session.add(part)
        await session.commit()
        return {"state": updated["state"], "state_revision": updated["state_revision"]}


__all__ = [
    "JSON_RENDER",
    "PART_KINDS",
    "UiRefused",
    "check_ui_event",
    "find_ui_part",
    "grouped_parts",
    "record_part",
    "run_parts",
    "seal_parts",
    "show_approval",
    "show_artifact",
    "show_ui",
    "ui_event_message",
    "ui_part_id",
    "ui_state_message",
    "update_ui_state",
]
