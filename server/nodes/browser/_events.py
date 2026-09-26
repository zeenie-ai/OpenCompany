"""Metadata-only Browser lifecycle events.

UI notifications, not workflow triggers: no node registers a canary consumer
for ``com.opencompany.browser.*``, so routing them through
``services.events.dispatch.emit`` would run a Temporal Visibility query that
matches nothing. They go straight to connected WebSocket clients, like the
Canvas and Context events.

Every payload is identity plus a state enum. None carries a URL, a page
title, a reason or any cookie data, because a broadcast reaches every
connected socket; panels read the details through the authorized
``browser_session`` / ``browser_profiles_list`` handlers and the live-view
socket, which check ownership. Workflow scope rides inside ``data`` (new
CloudEvents contracts never add top-level extension attributes).
"""

from __future__ import annotations

from typing import Optional

from services.events.envelope import WorkflowEvent

_SOURCE = "opencompany://nodes/browser"
SESSION_WIRE_KEY = "browser_updated"
PROFILES_WIRE_KEY = "browser_profiles_updated"
RUNTIME_WIRE_KEY = "browser_runtime"


def browser_updated(
    *,
    workflow_id: Optional[str],
    node_id: str,
    session_id: str,
    state: str,
    revision: int,
) -> WorkflowEvent:
    return WorkflowEvent(
        source=_SOURCE,
        type="com.opencompany.browser.updated",
        subject=session_id,
        data={
            "workflow_id": workflow_id,
            "node_id": node_id,
            "session_id": session_id,
            "state": state,
            "revision": revision,
        },
    )


def browser_profiles_updated(*, revision: int) -> WorkflowEvent:
    return WorkflowEvent(
        source=_SOURCE,
        type="com.opencompany.browser.profiles.updated",
        subject="profiles",
        data={"revision": revision},
    )


def browser_runtime_progress(*, component: str, phase: str, percent: Optional[int], version: str, error: Optional[str]) -> WorkflowEvent:
    return WorkflowEvent(
        source=_SOURCE,
        type="com.opencompany.browser.runtime.progress",
        subject=component,
        data={"component": component, "phase": phase, "percent": percent, "version": version, "error": error},
    )


async def _broadcast(wire_key: str, event: WorkflowEvent) -> None:
    from services.status_broadcaster import get_status_broadcaster

    await get_status_broadcaster().broadcast({"type": wire_key, "data": event.model_dump(mode="json", exclude_none=True)})


async def dispatch_browser_updated(*, workflow_id: Optional[str], node_id: str, session_id: str, state: str, revision: int) -> None:
    await _broadcast(
        SESSION_WIRE_KEY,
        browser_updated(workflow_id=workflow_id, node_id=node_id, session_id=session_id, state=state, revision=revision),
    )


async def dispatch_browser_profiles_updated(*, revision: int) -> None:
    await _broadcast(PROFILES_WIRE_KEY, browser_profiles_updated(revision=revision))


async def dispatch_browser_runtime_progress(*, component: str, phase: str, percent: Optional[int], version: str, error: Optional[str] = None) -> None:
    await _broadcast(
        RUNTIME_WIRE_KEY,
        browser_runtime_progress(component=component, phase=phase, percent=percent, version=version, error=error),
    )


__all__ = [
    "PROFILES_WIRE_KEY",
    "RUNTIME_WIRE_KEY",
    "SESSION_WIRE_KEY",
    "browser_profiles_updated",
    "browser_runtime_progress",
    "browser_updated",
    "dispatch_browser_profiles_updated",
    "dispatch_browser_runtime_progress",
    "dispatch_browser_updated",
]
