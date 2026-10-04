"""A run's snapshot: what a client needs to draw a run without its events.

``snapshot_from_row`` shapes a ``ChatRun`` row; ``apply_event`` folds one
run event into a snapshot the way the client folds the stream, so a client
that subscribes mid-run starts from the same state the events before would
have built. The hub keeps one live snapshot per run it publishes for
(``services/chat/hub.py``); ``merge_snapshot`` lays it over the stored row.

Lifecycle, step, text and activity events are folded: an activity (a part
the run is building, such as a generated UI) keeps its content, replaced by
``activity.snapshot`` and patched by ``activity.delta``
(``services/genui/patches.py``).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from services.chat.events import run_event_suffix

#: Run states a client draws as working.
LIVE_STATES = frozenset({"queued", "pending", "running", "stopping"})


def _iso(moment: Optional[datetime]) -> Optional[str]:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.isoformat()


def empty_snapshot(run_id: str) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "session_id": None,
        "workflow_id": None,
        "kind": "message",
        "state": "pending",
        "seq": 0,
        "user_message_id": None,
        "reply_message_id": None,
        "parent_run_id": None,
        "created_at": None,
        "started_at": None,
        "finished_at": None,
        "steps": [],
        "segments": [],
        "activities": [],
        "outcome": None,
        "result": {},
        "error": None,
        "error_code": None,
    }


def snapshot_from_row(run: Any) -> Dict[str, Any]:
    """A stored run as a snapshot. Steps come from the row (saved as they
    finish); text is not stored until the reply is, so a run read after a
    restart has no segments."""
    snapshot = empty_snapshot(run.run_id)
    snapshot.update(
        {
            "session_id": run.session_id,
            "workflow_id": run.workflow_id,
            "kind": run.kind,
            "state": run.state,
            "user_message_id": run.user_message_uid,
            "reply_message_id": run.reply_message_uid,
            "parent_run_id": run.parent_run_id,
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "steps": [dict(step) for step in (run.steps or [])],
            "outcome": {"type": run.outcome} if run.outcome else None,
            "result": dict(run.result or {}),
            "error": run.error,
            "error_code": run.error_code,
        }
    )
    return snapshot


def merge_snapshot(stored: Mapping[str, Any], live: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The stored run with the hub's live view laid over it. The stored state
    wins once it has ended: the row commits before its terminal event goes
    out, so the row can be ahead of the hub, never behind it."""
    merged = deepcopy(dict(stored))
    if not live:
        return merged
    for key in ("seq", "steps", "segments", "activities"):
        merged[key] = deepcopy(live.get(key, merged.get(key)))
    if stored.get("state") in LIVE_STATES and live.get("state") not in (None, "pending"):
        merged["state"] = live["state"]
    return merged


def _step(snapshot: Dict[str, Any], step_id: str) -> Dict[str, Any]:
    for step in snapshot["steps"]:
        if step.get("step_id") == step_id:
            return step
    step: Dict[str, Any] = {"step_id": step_id}
    snapshot["steps"].append(step)
    return step


def _segment(snapshot: Dict[str, Any], message_id: str) -> Dict[str, Any]:
    for segment in snapshot["segments"]:
        if segment.get("message_id") == message_id:
            return segment
    segment: Dict[str, Any] = {"message_id": message_id, "text": "", "final": None}
    snapshot["segments"].append(segment)
    return segment


def _activity(snapshot: Dict[str, Any], message_id: str, activity_type: str) -> Dict[str, Any]:
    for activity in snapshot["activities"]:
        if activity.get("message_id") == message_id:
            return activity
    activity: Dict[str, Any] = {"message_id": message_id, "activity_type": activity_type, "content": None, "patches": 0}
    snapshot["activities"].append(activity)
    return activity


def apply_event(snapshot: Mapping[str, Any], event: Mapping[str, Any]) -> Dict[str, Any]:
    """Fold one ``chat_run_event`` envelope (as a dict) into ``snapshot``.

    Returns a new snapshot; the input is not modified. An event whose ``seq``
    is not newer than the snapshot's is a duplicate and changes nothing."""
    data = event.get("data") or {}
    suffix = run_event_suffix(str(event.get("type") or ""))
    seq = int(data.get("seq") or 0)
    if suffix is None or seq <= int(snapshot.get("seq") or 0):
        return dict(snapshot)
    out: Dict[str, Any] = deepcopy(dict(snapshot))
    out["seq"] = seq
    for key in ("session_id", "workflow_id"):
        if data.get(key) is not None:
            out[key] = data[key]

    if suffix == "started":
        out["state"] = "running"
        for key in ("kind", "parent_run_id", "user_message_id", "reply_message_id", "started_at"):
            if data.get(key) is not None:
                out[key] = data[key]
    elif suffix == "finished":
        outcome = dict(data.get("outcome") or {"type": "success"})
        out["outcome"] = outcome
        out["result"] = dict(data.get("result") or {})
        out["state"] = "stopped" if outcome.get("type") == "stopped" else "finished"
    elif suffix == "failed":
        out["state"] = "error"
        out["error"] = data.get("message")
        out["error_code"] = data.get("code")
    elif suffix == "step.started":
        step = _step(out, str(data.get("step_id")))
        step.update({"name": data.get("step_name"), "state": "running"})
        if data.get("icon"):
            step["icon"] = data["icon"]
    elif suffix == "step.finished":
        step = _step(out, str(data.get("step_id")))
        step.update({"name": data.get("step_name") or step.get("name"), "state": data.get("state") or "done"})
        for key in ("detail", "duration_ms", "narration"):
            if data.get(key) is not None:
                step[key] = data[key]
    elif suffix == "text.started":
        _segment(out, str(data.get("message_id")))
    elif suffix == "text.content":
        segment = _segment(out, str(data.get("message_id")))
        segment["text"] += str(data.get("delta") or "")
    elif suffix == "text.ended":
        segment = _segment(out, str(data.get("message_id")))
        segment["final"] = bool(data.get("final"))
    elif suffix == "activity.snapshot":
        activity = _activity(out, str(data.get("message_id")), str(data.get("activity_type") or ""))
        if data.get("replace", True) or activity.get("content") is None:
            activity.update(
                {"activity_type": data.get("activity_type"), "content": deepcopy(data.get("content")), "patches": 0}
            )
    elif suffix == "activity.delta":
        from services.genui.patches import apply_patch

        activity = _activity(out, str(data.get("message_id")), str(data.get("activity_type") or ""))
        patch = data.get("patch") if isinstance(data.get("patch"), list) else []
        activity["content"] = apply_patch(activity.get("content"), patch)
        activity["patches"] = int(activity.get("patches") or 0) + len(patch)
    elif suffix == "custom":
        name = data.get("name")
        value = data.get("value") or {}
        if name == "opencompany.segment_discarded":
            discarded = value.get("message_id")
            out["segments"] = [segment for segment in out["segments"] if segment.get("message_id") != discarded]
        elif name == "opencompany.stopping":
            out["state"] = "stopping"
    return out


def replay(run_id: str, events: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Fold a run's events in order, from an empty snapshot."""
    snapshot = empty_snapshot(run_id)
    for event in events:
        snapshot = apply_event(snapshot, event)
    return snapshot


__all__ = ["LIVE_STATES", "apply_event", "empty_snapshot", "merge_snapshot", "replay", "snapshot_from_row"]
