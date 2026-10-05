"""Normal mode's employees: AI employees the owner hires and supervises.

An employee is a workflow; this package holds what Normal mode adds on top
(the hire screen's data, summaries, the app registry, lifecycle events).
It never imports ``nodes/``.

Side-effect import (from main.py) registers the WebSocket handlers, the
cleanup that runs when a workflow is deleted, and the summary builder the
coalesced ``employee_lifecycle`` broadcasts use.
"""

from __future__ import annotations

import time
from typing import Any

from services.approvals.listeners import register_approval_listener as _register_approval_listener
from services.approvals.rules import register_rule_listener as _register_rule_listener
from services.approvals.rules import register_rule_seeder as _register_rule_seeder
from services.deployment.control import register_control_listener as _register_control_listener
from services.workflow_storage.hooks import register_workflow_deleted_hook as _register_deleted_hook
from services.workflow_storage.listeners import register_graph_listener as _register_graph_listener
from services.ws_handler_registry import register_ws_handlers as _register_ws_handlers

from . import events as _events
from .handlers import WS_HANDLERS as _EMPLOYEE_WS_HANDLERS
from .setup import WS_HANDLERS as _SETUP_WS_HANDLERS
from .conversion_handlers import WS_HANDLERS as _CONVERSION_WS_HANDLERS


async def _on_workflow_deleted(database: Any, workflow_id: str) -> None:
    from . import runs, store

    await store.delete_for_workflow(database, workflow_id)
    await runs.delete_runs_for_workflow(database, workflow_id)
    _events.forget_employee(workflow_id)
    # Every workflow is an employee on Home, hired or not.
    await _events.broadcast_employee_event(
        "removed", workflow_id=workflow_id, revision=int(time.time() * 1000), reason="deleted"
    )


async def _seed_ask_first(database: Any, workflow_id: str):
    """An employee's Ask first rule starts as its ground rules said at hire
    (services/approvals/rules.py seeds the workflow's rule from this)."""
    from . import store

    employee = await store.get_by_workflow(database, workflow_id)
    if employee is None:
        return None
    return bool(dict(employee.rules or {}).get("ask_first", True))


async def _on_ask_first_changed(database: Any, workflow_id: str, rule: Any):
    """Keep the employee's ground rules in step with the live rule, show it
    on the card, and say what the change means for this employee: whether
    its app replies wait for the owner (a gate is wired), and whether its
    graph predates the live rule (Apply rebuilds it)."""
    from . import store
    from .builder import LIVE_RULE_BUILDER_VERSION

    employee = await store.get_by_workflow(database, workflow_id)
    if employee is None:
        return None
    rules = {**dict(employee.rules or {}), "ask_first": bool(rule.ask_first)}
    await store.update_employee(database, workflow_id, {"rules": rules})
    _events.employee_changed_now(workflow_id)
    roles = dict(employee.node_roles or {})
    return {
        "replies_gated": bool(roles.get("gate")),
        "needs_apply": int(employee.builder_version or 1) < LIVE_RULE_BUILDER_VERSION,
    }


async def _build_summary(workflow_id: str):
    from core.container import container

    from .summaries import get_employee_summary

    return await get_employee_summary(container.database(), workflow_id, auth_service=container.auth_service())


_register_ws_handlers(_EMPLOYEE_WS_HANDLERS)
_register_ws_handlers(_SETUP_WS_HANDLERS)
_register_ws_handlers(_CONVERSION_WS_HANDLERS)
_register_deleted_hook(_on_workflow_deleted)
# Start / Pause / Resume / Reset / recovery change an employee's status,
# and the owner is usually watching the card when they do: sent at once.
_register_control_listener(_events.employee_changed_now)
_events.set_summary_builder(_build_summary)
# A draft waiting, sent or discarded changes the employee's pending count.
_register_approval_listener(lambda change: _events.employee_changed(change.workflow_id))
# An employee's Ask first rule starts from its ground rules; the chat's Ask
# first chip changes both.
_register_rule_seeder(_seed_ask_first)
_register_rule_listener(_on_ask_first_changed)
# A saved graph can change the summary (a new tool, a change waiting for a
# restart). The editor autosaves often, so this takes the coalesced path.
_register_graph_listener(_events.employee_changed)

__all__ = ["apps", "events", "handlers", "setup", "store", "summaries"]
