"""Chat's WebSocket commands (docs-internal/chat_protocol.md).

Moved here from ``routers/websocket.py`` with the run runtime. Every command
that names a session checks the socket first (``services/chat/access.py``).

``send_chat_message`` saves the owner's message and dispatches it to the
workflow's chat triggers. A workflow's session takes a message only while
its deployment would read it (``delivery``: ``"now"`` while it runs,
``"queued"`` while it is paused); otherwise nothing is saved and the answer
is ``not_running``. When a deployed chat trigger will answer the session,
the message starts a run, admitted in the same transaction
(``services/chat/ledger.py``), and the response names it. The editor's
``"default"`` session is saved and dispatched to every deployment, without a
run, as it always was.

``get_chat_messages`` reads the path shown (``services/chat/branches.py``):
each message with its versions (``siblings``), whether the owner may change
it (``editable``) and their rating. ``edit_chat_message``,
``regenerate_chat_reply`` and ``switch_chat_branch`` move that path, and
``set_chat_feedback`` rates an answer (``services/chat/feedback.py``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import WebSocket

from core.container import container
from core.logging import get_logger
from services.chat import branches, ledger, reducer
from services.chat.access import ChatAccessDenied, authorize_session, session_id_of
from services.chat.attachments import MAX_ATTACHMENTS, AttachmentRefused, check_attachments
from services.chat.feedback import FeedbackRefused, feedback_for, set_feedback
from services.chat.events import MESSAGE_WIRE_ROUTING_KEY, dispatch_chat_message_received
from services.chat.hub import get_chat_hub
from services.chat_thread import (
    DEFAULT_SESSION,
    announce_chat_updated,
    chat_execution_id,
    clear_chat_session,
    delivery_for,
    record_chat_message,
)
from services.ws_handler_registry import ws_handler

logger = get_logger(__name__)

PROTOCOL_VERSION = 2
_DENIED = {"success": False, "error": "access_denied"}
#: A workflow's chat messages travel through Temporal (``dispatch.emit``
#: signals its listeners) and only a Temporal activity claims their runs, so
#: a send while it is unreachable would be lost.
_UNAVAILABLE = {"success": False, "error": "engine_unavailable"}


def _engine_connected() -> bool:
    """Whether Temporal is reachable now (the flag ``/health/ready`` reads)."""
    try:
        wrapper = container.temporal_client()
    except Exception:  # noqa: BLE001 - not wired: nothing can deliver
        return False
    return bool(wrapper is not None and wrapper.is_connected)


def _wire_run(run: Any) -> Dict[str, Any]:
    """The run a message started or answers, as a message carries it: enough
    to show how it ended after a reload (a failed run leaves no reply), what
    the employee did on the way and how long it took."""
    wire: Dict[str, Any] = {"run_id": run.run_id, "state": run.state, "outcome": run.outcome}
    if run.state == "error":
        wire["error"] = {"message": run.error, "code": run.error_code}
        hint = (run.result or {}).get("hint")
        if hint:
            wire["error"]["hint"] = hint
    if run.steps:
        wire["steps"] = [dict(step) for step in run.steps]
    if run.started_at and run.finished_at:
        started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=timezone.utc)
        finished = run.finished_at if run.finished_at.tzinfo else run.finished_at.replace(tzinfo=timezone.utc)
        wire["duration_ms"] = max(0, int((finished - started).total_seconds() * 1000))
    return wire


def _wire_message(
    row: Dict[str, Any],
    runs: Optional[Dict[str, Any]] = None,
    *,
    siblings: Optional[List[str]] = None,
    editable: bool = False,
    feedback: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """A stored message as ``get_chat_messages`` sends it. ``siblings``: its
    versions, oldest first (itself included); ``run_id``: the run answering
    it on the path shown (an owner's message answered again names the newest
    answer's run)."""
    message_id = row.get("uid") or f"m{row.get('id')}"
    versions = siblings if siblings and message_id in siblings else [message_id]
    run_id = run_id or row.get("run_id")
    wire: Dict[str, Any] = {
        "id": message_id,
        "legacy_id": row.get("id"),
        "role": row.get("role"),
        "kind": row.get("kind") or "text",
        "text": row.get("message"),
        "message": row.get("message"),
        "timestamp": row.get("timestamp"),
        "run_key": row.get("execution_id"),
        "run_id": run_id,
        "parent_id": row.get("parent_uid"),
        "status": row.get("status") or "complete",
        "attachments": row.get("attachments") or [],
        "parts": row.get("parts") or {},
        "feedback": feedback,
        "siblings": {"index": versions.index(message_id), "count": len(versions), "ids": list(versions)},
        "editable": editable,
    }
    client_message_id = (row.get("meta") or {}).get("client_message_id")
    if client_message_id:
        wire["client_message_id"] = client_message_id
    run = (runs or {}).get(run_id or "")
    if run is not None:
        wire["run"] = _wire_run(run)
    return wire


async def run_snapshots(database: Any, session_id: str) -> List[Dict[str, Any]]:
    """Snapshots of a session's live runs. The hub is read before the rows,
    so an event published in between carries a newer ``seq`` than the
    snapshot and still reaches a subscriber."""
    hub = get_chat_hub()
    live = hub.session_live(session_id)
    rows = await ledger.live_runs(database, session_id)
    return [await _run_snapshot(database, run, live.get(run.run_id), hub.epoch) for run in rows]


async def _run_snapshot(database: Any, run: Any, live: Any, hub_epoch: str) -> Dict[str, Any]:
    snapshot = {**reducer.merge_snapshot(reducer.snapshot_from_row(run), live), "hub_epoch": hub_epoch}
    control = await ledger.owning_control(database, run)
    if ledger.controlled_generation(control):
        from services.deployment.control import serialize_control

        snapshot["workflow_control"] = serialize_control(control)
    return snapshot


async def _thread_state(database: Any, session_id: str) -> Dict[str, Any]:
    from models.chat import ChatThread

    async with database.get_session() as session:
        thread = await session.get(ChatThread, session_id)
    return {
        "active_leaf_id": thread.active_leaf_uid if thread is not None else None,
        "revision": thread.revision if thread is not None else 0,
    }


async def _answers_session(database: Any, control: Any, session_id: str) -> bool:
    """Whether a chat trigger in the deployed graph accepts this session, so
    a run started for the message will be picked up. Decided from the
    trigger's own filter (``event_waiter.build_filter``), never from a node
    type name."""
    from services import event_waiter

    graph = getattr(control, "graph_snapshot", None) or {}
    for node in graph.get("nodes") or []:
        node_type = str(node.get("type") or "")
        config = event_waiter.get_trigger_config(node_type)
        if config is None or config.event_type != MESSAGE_WIRE_ROUTING_KEY:
            continue
        if (node.get("data") or {}).get("disabled"):
            continue
        params = await database.get_node_parameters(str(node.get("id") or "")) or {}
        try:
            if event_waiter.build_filter(node_type, params)({"session_id": session_id, "message": ""}):
                return True
        except Exception:  # noqa: BLE001 - a broken filter admits, as the listener's does
            return True
    return False


async def _ui_event(database: Any, session_id: str, raw: Any) -> Dict[str, Any]:
    """A button press, checked against the interface it came from. Returns
    ``{label, meta, prompt}``; raises ``ValueError`` saying why not."""
    from services.chat import parts

    if not isinstance(raw, dict):
        raise ValueError("ui_event is {part_id, element_id, action, params}")
    part_id = raw.get("part_id")
    part = await parts.find_ui_part(database, session_id, part_id) if isinstance(part_id, str) and part_id else None
    if part is None:
        raise ValueError("this chat has no such interface")
    try:
        pressed = parts.check_ui_event(part, raw.get("element_id"), raw.get("action"), raw.get("params"))
    except parts.UiRefused as exc:
        raise ValueError(str(exc)) from None
    event = {"part_id": part_id, "element_id": raw["element_id"], "action": raw["action"], "params": pressed["params"]}
    label = pressed["label"] or str(raw["action"])
    return {
        "label": label,
        "meta": {"ui_event": event},
        "prompt": parts.ui_event_message(label=label, **event),
    }


async def _dispatch(
    session_id: str,
    workflow_id: Optional[str],
    *,
    message_uid: str,
    prompt: str,
    run_id: Optional[str],
    timestamp: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Send the owner's message to the workflow's chat triggers: the run it
    starts is the event's id, so the trigger's child workflow is the run's.
    Without a tracked run, the saved message supplies a stable event identity.
    Its attachments ride along (the trigger's output carries them)."""
    event_data: Dict[str, Any] = {"message": prompt, "timestamp": timestamp, "session_id": session_id, "message_id": message_uid}
    if run_id is not None:
        event_data["run_id"] = run_id
    if attachments:
        event_data["attachments"] = list(attachments)
    await dispatch_chat_message_received(event_data, workflow_id=workflow_id, event_id=run_id or message_uid)
    logger.info("Chat message dispatched", session_id=session_id, run_id=run_id)


# ``message`` is checked below: a message may be only files.
@ws_handler()
async def handle_send_chat_message(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save the owner's message, start its run, and dispatch it. Answers
    ``{message_id, run_id, delivery, timestamp}``; ``run_in_progress`` (with
    the live ``run_id``), ``not_running``, ``engine_unavailable`` (Temporal is
    unreachable, nothing saved), ``save_failed``, ``access_denied``,
    ``invalid_request`` or ``ui_event_rejected`` otherwise.

    With ``ui_event`` the message is a button pressed in an interface the
    employee showed: checked against that interface, saved as the owner's
    message reading the button's label (kind ``action``), and sent to the
    employee as a ``[ui-event]`` line naming the press.

    ``attachments`` are files the chat uploaded under the workspace's
    ``uploads/`` (``services/chat/attachments.py``: rebuilt from the files,
    at most six; refused with ``attachment_rejected``); a message may be
    only files. ``options.web: false`` keeps the employee off web search for
    this message."""
    message = data.get("message")
    raw_attachments = data.get("attachments")
    if not isinstance(message, str) or not (message.strip() or raw_attachments):
        return {"success": False, "error": "invalid_request", "detail": "message must be text"}
    if data.get("role", "user") != "user":
        return {"success": False, "error": "invalid_request", "detail": "send_chat_message sends the owner's messages"}
    session_id = session_id_of(data)
    timestamp = data.get("timestamp") or datetime.now(timezone.utc).isoformat()
    database = container.database()
    try:
        scope = await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)

    attachments: List[Dict[str, Any]] = []
    if raw_attachments:
        if scope.workflow_id is None:
            return {"success": False, "error": "invalid_request", "detail": "files can be attached only in a workflow's chat"}
        try:
            attachments = await check_attachments(database, scope.workflow_id, raw_attachments)
        except AttachmentRefused as exc:
            return {"success": False, "error": "attachment_rejected", "detail": str(exc)}

    press: Optional[Dict[str, Any]] = None
    if data.get("ui_event") is not None:
        try:
            press = await _ui_event(database, session_id, data["ui_event"])
        except ValueError as exc:
            return {"success": False, "error": "ui_event_rejected", "detail": str(exc)}
        message = press["label"]

    # The scope rides the envelope's ``workflow_id``, so ``dispatch.emit``
    # signals only this workflow's listeners (without it one workflow's chat
    # fired every deployed chat trigger). The editor's "default" session
    # keeps its unscoped delivery.
    control = None
    delivery: Optional[str] = None
    if scope.workflow_id is not None:
        control = await database.get_latest_workflow_control(session_id)
        delivery = delivery_for(control)
        if delivery is None:
            return {"success": False, "error": "not_running"}
        if not _engine_connected():
            return dict(_UNAVAILABLE)
    track = scope.workflow_id is not None and await _answers_session(database, control, session_id)

    try:
        admission = await ledger.admit_message(
            database,
            session_id=session_id,
            workflow_id=scope.workflow_id,
            execution_id=chat_execution_id(control) if scope.workflow_id is not None else None,
            text=message,
            track=track,
            state="queued" if delivery == "queued" else "pending",
            kind="action" if press is not None else "message",
            message_kind="action" if press is not None else "text",
            client_message_id=data.get("client_message_id"),
            meta=press["meta"] if press is not None else None,
            options=_options(data.get("options")),
            attachments=attachments or None,
        )
    except ledger.RunInProgress as exc:
        return {"success": False, "error": "run_in_progress", "run_id": exc.run.run_id}
    except ValueError as exc:
        return {"success": False, "error": "invalid_request", "detail": str(exc)}
    except Exception:
        logger.warning("Chat message could not be saved", session_id=session_id, exc_info=True)
        return {"success": False, "error": "save_failed"}

    row, run = admission.message, admission.run
    if admission.created:
        # Sent before open threads hear of it, so this answer, which
        # registers the run with the sender, reaches the sender before their
        # thread reads the run: told first, their thread showed it live and
        # asked for it (get_chat_run). Sending never raises (emit is
        # fail-soft), so threads always hear of it.
        await _dispatch(
            session_id,
            scope.workflow_id,
            message_uid=row["uid"],
            prompt=press["prompt"] if press is not None else message,
            run_id=run.run_id if run is not None else None,
            timestamp=timestamp,
            attachments=attachments,
        )
        await announce_chat_updated(session_id, "user")

    response: Dict[str, Any] = {
        "success": True,
        "message": "Chat message sent",
        "timestamp": timestamp,
        "message_id": row["uid"],
        "run_id": run.run_id if run is not None else None,
    }
    if delivery is not None:
        response["delivery"] = delivery
    return response


@ws_handler()
async def handle_get_chat_messages(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """The path shown in a session (``services/chat/branches.py``), oldest
    first, the newest ``limit`` of it, with the thread's state and its live
    runs. The live generation's messages only, unless ``all_generations``
    (Home's thread, which also shows a message from before a Start). Each
    message carries its versions, whether the owner may change it now
    (``editable``: edit theirs, try the latest answer again) and their
    rating. A read that fails answers ``read_failed``, never an empty
    thread."""
    session_id = session_id_of(data)
    limit = data.get("limit")
    database = container.database()
    try:
        scope = await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    try:
        rows = await database.read_chat_messages(session_id)
        thread = await _thread_state(database, session_id)
        control = await database.get_latest_workflow_control(session_id) if scope.workflow_id is not None else None
        path = branches.active_path(rows, thread["active_leaf_id"])
        if not data.get("all_generations") and control is not None:
            path = [] if control.status == "reset" else [row for row in path if row.get("execution_id") == control.root_execution_id]
        runs_list = await ledger.session_runs(database, session_id)
        runs = {run.run_id: run for run in runs_list}
        answering = branches.answering_run_ids(path, runs_list)
        editable = branches.editable_ids(path, runs_list, chat_execution_id(control)) if scope.workflow_id is not None else set()
        siblings = branches.sibling_ids(rows)
        ratings = await feedback_for(database, session_id)
        active_runs = await run_snapshots(database, session_id)
    except Exception:
        logger.warning("Chat messages could not be read", session_id=session_id, exc_info=True)
        return {"success": False, "error": "read_failed", "session_id": session_id}
    if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
        path = path[-limit:]
    return {
        "success": True,
        "protocol_version": PROTOCOL_VERSION,
        "session_id": session_id,
        "messages": [
            _wire_message(
                row,
                runs,
                siblings=siblings.get(row.get("uid") or ""),
                editable=row.get("uid") in editable,
                feedback=ratings.get(row.get("uid") or ""),
                run_id=answering.get(row.get("uid") or ""),
            )
            for row in path
        ],
        "thread": thread,
        "active_runs": active_runs,
    }


@ws_handler()
async def handle_chat_subscribe(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Send the session's run events to this socket from now on. Answers
    the hub's epoch and the live runs' snapshots; events after a snapshot
    carry a higher ``seq`` than it."""
    session_id = session_id_of(data)
    database = container.database()
    try:
        await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    hub = get_chat_hub()
    hub.subscribe(websocket, session_id)
    try:
        active_runs = await run_snapshots(database, session_id)
    except Exception:
        logger.warning("Chat runs could not be read", session_id=session_id, exc_info=True)
        return {"success": False, "error": "read_failed", "session_id": session_id, "hub_epoch": hub.epoch}
    return {"success": True, "session_id": session_id, "hub_epoch": hub.epoch, "active_runs": active_runs}


@ws_handler()
async def handle_chat_unsubscribe(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Stop sending the session's run events to this socket."""
    session_id = session_id_of(data)
    hub = get_chat_hub()
    hub.unsubscribe(websocket, session_id)
    return {"success": True, "session_id": session_id, "hub_epoch": hub.epoch}


@ws_handler("run_id")
async def handle_get_chat_run(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """One run's snapshot, live or ended."""
    database = container.database()
    run = await ledger.get_run(database, str(data["run_id"]))
    if run is None:
        return {"success": False, "error": "not_found"}
    try:
        await authorize_session(database, websocket, run.session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    hub = get_chat_hub()
    live = hub.live_snapshot(run.run_id)
    run = await ledger.get_run(database, run.run_id) or run
    return {"success": True, "run": await _run_snapshot(database, run, live, hub.epoch)}


@ws_handler("run_id")
async def handle_stop_chat_run(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Suspend a controlled run's generation; retain legacy terminal Stop."""
    database = container.database()
    run = await ledger.get_run(database, str(data["run_id"]))
    if run is None:
        return {"success": False, "error": "not_found"}
    try:
        await authorize_session(database, websocket, run.session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    control = await ledger.owning_control(database, run)
    if ledger.controlled_generation(control):
        if run.state not in ledger.LIVE_STATES:
            return {"success": False, "error": "not_stoppable", "run_id": run.run_id, "state": run.state}
        if not isinstance(data.get("idempotency_key"), str) or not data["idempotency_key"].strip():
            return {"success": False, "error": "idempotency_key_required"}
        from services.deployment.handlers import handle_pause_workflow

        result = await handle_pause_workflow(
            {
                "workflow_id": control.workflow_id,
                "expected_root_execution_id": run.run_key,
                "expected_revision": data.get("expected_revision"),
                "idempotency_key": data.get("idempotency_key"),
            },
            websocket,
        )
        return {**result, "run_id": run.run_id, "resumable": True}
    run = await ledger.request_stop(database, run.run_id) or run
    if run.state not in ("stopping", "stopped"):
        return {"success": False, "error": "not_stoppable", "run_id": run.run_id, "state": run.state}
    return {"success": True, "run_id": run.run_id, "state": run.state}


@ws_handler("part_id")
async def handle_chat_ui_state(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """What the owner set in an interface the employee showed
    (``changes: [{path, value}]``, the last value per path winning). Kept on
    the reply (``parts.ui[].state``), so a reload shows it, and told to the
    employee at the start of its next turn (a ``[ui-state]`` note,
    ``services/chat/notes.py``). Answers the state's new ``state_revision``;
    ``not_found`` when the session has no such interface,
    ``invalid_request`` for a change that does not fit."""
    from services.chat import notes, parts

    session_id = session_id_of(data)
    database = container.database()
    try:
        await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    part_id = str(data["part_id"])
    try:
        updated = await parts.update_ui_state(database, session_id, part_id, data.get("changes"))
    except parts.UiRefused as exc:
        return {"success": False, "error": "invalid_request", "detail": str(exc)}
    if updated is None:
        return {"success": False, "error": "not_found"}
    try:
        await notes.upsert_note(
            database,
            session_id=session_id,
            key=f"ui-state:{part_id}",
            kind="ui-state",
            text=parts.ui_state_message(part_id=part_id, state=updated["state"]),
        )
    except Exception:  # noqa: BLE001 - the state is saved and shows; only the note is lost
        logger.warning("The employee could not be told what the owner set", part_id=part_id, exc_info=True)
    return {"success": True, "part_id": part_id, "state_revision": updated["state_revision"]}


def _options(raw: Any) -> Dict[str, Any]:
    """The per-message choices kept on the run: ``web`` (a bool) only."""
    if isinstance(raw, dict) and isinstance(raw.get("web"), bool):
        return {"web": raw["web"]}
    return {}


@ws_handler()
async def handle_get_chat_context(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """What the session's message box offers: slash commands (the generic
    ones in ``chat_defaults.json`` and those its employee's apps add,
    ``employee_apps.json``; ``{name}`` is the workflow's name), whether files
    can be attached (a workflow's chat), whether the Web chip has web search
    to turn off (a search tool in the saved graph), and the limits."""
    from services.chat.config import load_chat_config
    from services.employees.apps import app_for_node_type
    from services.media.limits import MEDIA_MAX_UPLOAD_BYTES
    from services.node_registry import get_node_class

    session_id = session_id_of(data)
    database = container.database()
    try:
        scope = await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    saved = await database.get_workflow(scope.workflow_id) if scope.workflow_id is not None else None
    graph = (getattr(saved, "data", None) or {}) if saved is not None else {}
    name = str(getattr(saved, "name", "") or "the employee")
    node_types = [str(node.get("type") or "") for node in graph.get("nodes") or [] if isinstance(node, dict)]

    configured = (load_chat_config().get("commands") or {}).get("generic") or []
    commands: List[Dict[str, Any]] = []
    seen: set = set()

    def add(command: Any) -> None:
        if not isinstance(command, dict) or command.get("command") in seen or not scope.workflow_id:
            return
        seen.add(command.get("command"))
        commands.append(
            {
                "command": str(command.get("command")),
                "description": str(command.get("description") or "").replace("{name}", name),
                "fill": str(command.get("fill") or "").replace("{name}", name),
                "suggest": bool(command.get("suggest")),
            }
        )

    for node_type in node_types:
        app = app_for_node_type(node_type)
        for command in app.commands if app is not None else ():
            add(dict(command))
    for command in configured:
        add(command)

    def searches(node_type: str) -> bool:
        cls = get_node_class(node_type)
        return cls is not None and "search" in tuple(getattr(cls, "group", ()) or ())

    return {
        "success": True,
        "session_id": session_id,
        "commands": commands,
        "capabilities": {"attachments": scope.workflow_id is not None, "web": any(searches(t) for t in node_types)},
        "limits": {"max_attachments": MAX_ATTACHMENTS, "max_upload_bytes": MEDIA_MAX_UPLOAD_BYTES},
    }


def _expected_revision(data: Dict[str, Any]) -> Optional[int]:
    value = data.get("expected_revision")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected_revision is the thread's revision, a number")
    return value


async def _branch_context(
    database: Any, websocket: WebSocket, data: Dict[str, Any], *, starts_run: bool
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """The workflow session a branch command changes, with its live
    generation and (for a command that starts a run) where a message goes
    now; or the refusal."""
    session_id = session_id_of(data)
    try:
        scope = await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED), {}
    if scope.workflow_id is None:
        return {"success": False, "error": "not_editable", "detail": "this chat keeps no versions"}, {}
    try:
        revision = _expected_revision(data)
    except ValueError as exc:
        return {"success": False, "error": "invalid_request", "detail": str(exc)}, {}
    control = await database.get_latest_workflow_control(session_id)
    live_root = chat_execution_id(control)
    if control is None or live_root is None:
        return {"success": False, "error": "not_running"}, {}
    context: Dict[str, Any] = {
        "session_id": session_id,
        "workflow_id": scope.workflow_id,
        "generation": int(control.generation),
        "live_root": live_root,
        "expected_revision": revision,
        "delivery": None,
        "state": "pending",
    }
    if starts_run:
        delivery = delivery_for(control)
        if delivery is None or not await _answers_session(database, control, session_id):
            return {"success": False, "error": "not_running"}, {}
        if not _engine_connected():
            return dict(_UNAVAILABLE), {}
        context["delivery"] = delivery
        context["state"] = "queued" if delivery == "queued" else "pending"
    return None, context


def _branch_refusal(exc: "branches.BranchRefused") -> Dict[str, Any]:
    return {"success": False, "error": exc.code, "detail": exc.detail}


async def _answer_branch(database: Any, moved: "branches.Moved", context: Dict[str, Any], *, role: Optional[str], timestamp: str) -> Dict[str, Any]:
    """After an edit or a retry committed: settle what the part left made,
    send the message to the chat triggers, then tell open threads."""
    await branches.after_move(database, moved)
    message, run = moved.result["message"], moved.result["run"]
    # Sent first, as a new message is (handle_send_chat_message).
    await _dispatch(
        moved.session_id,
        moved.workflow_id,
        message_uid=message["uid"],
        prompt=moved.result["prompt"],
        run_id=run.run_id,
        timestamp=timestamp,
        attachments=list(message.get("attachments") or []),
    )
    await announce_chat_updated(moved.session_id, role)
    response: Dict[str, Any] = {"success": True, "message_id": message["uid"], "run_id": run.run_id}
    if context.get("delivery") is not None:
        response["delivery"] = context["delivery"]
    return response


@ws_handler("message_id", "message")
async def handle_edit_chat_message(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Edit one of the owner's messages (``message_id``): the edit goes
    beside it as its new version, and the employee answers it remembering the
    conversation as it stood before the original. Answers ``{message_id,
    run_id, delivery}``; a resent edit (the same ``client_message_id``)
    answers the first. Refused with ``not_editable``, ``older_generation``,
    ``revision_conflict`` (``expected_revision`` is not the thread's),
    ``run_in_progress``, ``cannot_rewind``, ``not_found``, ``not_running``,
    ``access_denied``, ``invalid_request`` or ``save_failed``."""
    text = data.get("message")
    if not isinstance(text, str) or not text.strip():
        return {"success": False, "error": "invalid_request", "detail": "message must be text"}
    database = container.database()
    refused, context = await _branch_context(database, websocket, data, starts_run=True)
    if refused is not None:
        return refused
    client_message_id = data.get("client_message_id")
    if client_message_id is not None:
        try:
            uid = ledger.client_message_uid(context["session_id"], client_message_id)
        except ValueError as exc:
            return {"success": False, "error": "invalid_request", "detail": str(exc)}
        sent = await ledger.saved_message(database, uid)
        if sent is not None:
            return {"success": True, "message_id": sent.uid, "run_id": sent.run_id, "delivery": context["delivery"]}
    try:
        moved = await branches.edit_message(
            database,
            session_id=context["session_id"],
            workflow_id=context["workflow_id"],
            generation=context["generation"],
            live_root=context["live_root"],
            message_uid=str(data["message_id"]),
            text=text.strip(),
            expected_revision=context["expected_revision"],
            state=context["state"],
            client_message_id=client_message_id,
        )
    except branches.BranchRefused as exc:
        return _branch_refusal(exc)
    except Exception:
        logger.warning("Chat message could not be edited", session_id=context["session_id"], exc_info=True)
        return {"success": False, "error": "save_failed"}
    timestamp = data.get("timestamp") or datetime.now(timezone.utc).isoformat()
    return await _answer_branch(database, moved, context, role="user", timestamp=timestamp)


@ws_handler("message_id")
async def handle_regenerate_chat_reply(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Try again: answer anew the message the latest answer (``message_id``)
    answered, or the owner's last message when its run gave no answer. The
    new answer goes beside the old one, and the employee writes it
    remembering the conversation as it stood before the old one. Answers
    ``{message_id (the owner's message), run_id, delivery}``. Refused like
    ``edit_chat_message``."""
    database = container.database()
    refused, context = await _branch_context(database, websocket, data, starts_run=True)
    if refused is not None:
        return refused
    try:
        moved = await branches.retry_answer(
            database,
            session_id=context["session_id"],
            workflow_id=context["workflow_id"],
            generation=context["generation"],
            live_root=context["live_root"],
            message_uid=str(data["message_id"]),
            expected_revision=context["expected_revision"],
            state=context["state"],
        )
    except branches.BranchRefused as exc:
        return _branch_refusal(exc)
    except Exception:
        logger.warning("Chat answer could not be tried again", session_id=context["session_id"], exc_info=True)
        return {"success": False, "error": "save_failed"}
    timestamp = data.get("timestamp") or datetime.now(timezone.utc).isoformat()
    return await _answer_branch(database, moved, context, role=None, timestamp=timestamp)


@ws_handler("message_id")
async def handle_switch_chat_branch(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Show another version (``message_id``: a message beside one on the
    path) and the conversation after it, the employee remembering that
    branch. Answers ``{leaf_id}``. Refused with ``branch_unavailable`` (its
    conversations are no longer kept), ``cannot_rewind``,
    ``older_generation``, ``revision_conflict``, ``run_in_progress``,
    ``not_found``, ``not_running``, ``access_denied`` or ``save_failed``."""
    database = container.database()
    refused, context = await _branch_context(database, websocket, data, starts_run=False)
    if refused is not None:
        return refused
    try:
        moved = await branches.switch_branch(
            database,
            session_id=context["session_id"],
            workflow_id=context["workflow_id"],
            generation=context["generation"],
            live_root=context["live_root"],
            message_uid=str(data["message_id"]),
            expected_revision=context["expected_revision"],
        )
    except branches.BranchRefused as exc:
        return _branch_refusal(exc)
    except Exception:
        logger.warning("Chat branch could not be switched", session_id=context["session_id"], exc_info=True)
        return {"success": False, "error": "save_failed"}
    await branches.after_move(database, moved)
    await announce_chat_updated(moved.session_id, None)
    return {"success": True, "leaf_id": moved.result.get("leaf")}


@ws_handler("message_id")
async def handle_set_chat_feedback(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Rate an answer (``value``: ``up`` or ``down``) or take the rating back
    (``null``). Answers where it reaches (``reaches``: ``next_turn``, the
    employee's next turn; none for a rating taken back); ``not_found`` for a
    message that is not an answer in this chat."""
    session_id = session_id_of(data)
    database = container.database()
    try:
        await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    value = data.get("value")
    try:
        reaches = await set_feedback(database, session_id=session_id, message_uid=str(data["message_id"]), value=value)
    except FeedbackRefused as exc:
        return {"success": False, "error": exc.code}
    return {"success": True, "message_id": str(data["message_id"]), "value": value, "reaches": reaches}


@ws_handler()
async def handle_clear_chat_messages(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Clear a session's chat, every generation of it, with its runs. For a
    workflow's session the agent forgets the conversation too
    (``services.chat_thread.clear_chat_session``)."""
    session_id = session_id_of(data)
    database = container.database()
    try:
        await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    count = await clear_chat_session(database, session_id)
    return {"success": True, "message": f"Cleared {count} chat messages", "cleared_count": count}


@ws_handler("message", "role")
async def handle_save_chat_message(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save one message with the given role, without dispatching it."""
    role = data["role"]
    if role not in ("user", "assistant"):
        return {"success": False, "error": "invalid_request", "detail": "role is user or assistant"}
    session_id = session_id_of(data)
    database = container.database()
    try:
        await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    saved = await record_chat_message(database, session_id, role, data["message"])
    return {"success": bool(saved), "message": "Chat message saved" if saved else "Failed to save chat message"}


WS_HANDLERS = {
    "send_chat_message": handle_send_chat_message,
    "get_chat_messages": handle_get_chat_messages,
    "chat_subscribe": handle_chat_subscribe,
    "chat_unsubscribe": handle_chat_unsubscribe,
    "get_chat_run": handle_get_chat_run,
    "stop_chat_run": handle_stop_chat_run,
    "chat_ui_state": handle_chat_ui_state,
    "edit_chat_message": handle_edit_chat_message,
    "regenerate_chat_reply": handle_regenerate_chat_reply,
    "switch_chat_branch": handle_switch_chat_branch,
    "set_chat_feedback": handle_set_chat_feedback,
    "get_chat_context": handle_get_chat_context,
    "clear_chat_messages": handle_clear_chat_messages,
    "save_chat_message": handle_save_chat_message,
}

__all__ = ["DEFAULT_SESSION", "PROTOCOL_VERSION", "WS_HANDLERS", "run_snapshots"]
