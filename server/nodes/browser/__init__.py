"""Plugins for the 'browser' palette group: the Browser node and everything it owns.

The plugin is self-contained (no core-services edits). On import it
registers:

- the ``browser_service`` shutdown hook, which closes every profile's Chrome
  gracefully (so cookies are flushed) and stops the egress proxies;
- the WebSocket handlers for the Browser panels and the Credentials
  "Browser profiles" panel (``_handlers.py``);
- its router: the ``/ws/browser`` live view and the session-file upload
  (``_router.py``);
- the ``browserProfiles`` option loader for the node's profile field;
- a workflow-deleted hook that stops the workflow's browser sessions and
  removes its employee profile;
- the live-state source the Home employee card reads ("needs you in the
  browser").

Eager-imports the ``browser`` subpackage so ``BrowserNode`` registers no
matter how plugin discovery walks the tree. The runtime (Chrome, the
browser-use CLI) is created lazily on first use; importing this package
downloads and starts nothing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.node_output_schemas import register_output_schema
from services.plugin.shutdown_hooks import register_shutdown_hook
from services.workflow_storage.hooks import register_workflow_deleted_hook
from services.ws_handler_registry import register_option_loader, register_router, register_ws_handlers

from . import browser as _browser
from ._handlers import WS_HANDLERS, load_browser_profiles
from ._router import router


async def _shutdown_browser_runtime() -> None:
    from ._runtime import peek_browser_runtime

    runtime = peek_browser_runtime()
    if runtime is not None:
        await runtime.shutdown()


async def _on_workflow_deleted(database: Any, workflow_id: str) -> None:
    import asyncio

    from ._profiles import ProfileStore, remove_profile_files
    from ._runtime import peek_browser_runtime

    runtime = peek_browser_runtime()
    if runtime is not None:
        for session in runtime.sessions_for_workflow(workflow_id):
            controller = runtime.controller(session.profile_id)
            if controller is not None:
                await controller.release_lease(session.session_id)
            runtime.forget_session(session.session_id)
    # Employee profiles belong to their workflow; shared ones stay.
    store = ProfileStore(database)
    try:
        doomed = await store.employee_profiles_of(workflow_id)
    except Exception:  # noqa: BLE001 - cleanup must not fail the delete
        doomed = []
    for profile in doomed:
        if runtime is not None:
            await runtime.stop_profile(profile.id, reason="workflow deleted")
        await store.delete(profile.owner_id, profile.id)
        await asyncio.to_thread(remove_profile_files, profile.id)


def _home_state(workflow_id: str, node_id: str) -> Optional[Dict[str, Any]]:
    from ._runtime import peek_browser_runtime

    runtime = peek_browser_runtime()
    return runtime.state_of(workflow_id, node_id) if runtime is not None else None


def _register_home_state() -> None:
    try:
        from services.employees.node_signals import register_node_state_source
    except ImportError:  # pragma: no cover - Normal mode absent
        return
    register_node_state_source("browser", _home_state)


register_shutdown_hook("browser_service", _shutdown_browser_runtime)
register_ws_handlers(WS_HANDLERS)
register_router(router, name="browser")
register_option_loader("browserProfiles", load_browser_profiles)
register_workflow_deleted_hook(_on_workflow_deleted)
register_output_schema("browser", _browser.BrowserOutput)
_register_home_state()
