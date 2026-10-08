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
    from services.browser_owners import unregister_browser_owner
    await unregister_browser_owner()


async def _on_workflow_deleted(database: Any, workflow_id: str) -> None:
    from ._profiles import ProfileStore
    from ._runtime import peek_browser_runtime

    runtime = peek_browser_runtime()
    if runtime is not None:
        for session in runtime.sessions_for_workflow(workflow_id):
            controller = runtime.controller(session.profile_id)
            if controller is not None and controller.task_id is None:
                await controller.release_lease(session.session_id)
                runtime.forget_session(session.session_id)
    # Employee profiles belong to their workflow; shared ones stay.
    store = ProfileStore(database)
    try:
        doomed = await store.employee_profiles_of(workflow_id)
    except Exception:  # noqa: BLE001 - cleanup must not fail the delete
        doomed = []
    for profile in doomed:
        try:
            await _cleanup_deleted_workflow_profile(database, profile.owner_id, profile.id, workflow_id)
        except Exception:
            # Retain files and metadata when their owner cannot confirm cleanup.
            # The authorized profile panel can finish cleanup after recovery.
            continue


async def _cleanup_deleted_workflow_profile(database: Any, principal: str, profile_id: str, workflow_id: str) -> Dict[str, Any]:
    import asyncio
    from services.plugin.base import NodeUserError
    from services.browser_owners import bind_profile, replica_id, assert_runtime_epoch, _persistent, BrowserProfileOwner
    from ._profiles import ProfileStore, remove_profile_files
    from ._runtime import get_browser_runtime
    profile = await ProfileStore(database).get(principal, profile_id)
    if profile.kind != "employee" or profile.workflow_id != workflow_id or await database.get_workflow(workflow_id) is not None:
        raise NodeUserError("Browser cleanup is not associated with a deleted workflow.")
    binding = await bind_profile(database, profile.id, principal)
    if binding["owner_id"] != replica_id():
        if not binding.get("available", False):
            return {"success": False, "deferred": True}
        from ._routing import forward_command
        return await forward_command(binding, principal, "cleanup_workflow_profile", {"profile_id": profile.id, "deleted_workflow_id": workflow_id})
    await assert_runtime_epoch(database)
    runtime = get_browser_runtime()
    controller = runtime.controller(profile.id)
    if controller is not None and controller.task_id is not None:
        return {"success": False, "deferred": True}
    if _persistent(database):
        async with database.get_session() as session:
            ownership = await session.get(BrowserProfileOwner, profile.id)
            if ownership is not None and ownership.task_id is not None:
                return {"success": False, "deferred": True}
    running = runtime.running(profile.id)
    await runtime.stop_profile(profile.id, reason="workflow deleted")
    if running is not None and running.chrome.is_running():
        return {"success": False, "deferred": True}
    await ProfileStore(database).delete(principal, profile.id)
    await asyncio.to_thread(remove_profile_files, profile.id)
    return {"success": True}


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
