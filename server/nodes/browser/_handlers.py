"""WebSocket API for the Browser panels and the Credentials "Browser profiles" panel.

Security preamble as the Canvas and Memory panels: the unauthenticated
worker socket is refused, the owner comes from the authenticated socket,
and a session request must name a node that belongs to a workflow the
caller owns and that is a browser node (by its ``isBrowserPanel`` hint, not
its type string). Profile requests are scoped to the caller's own profiles.
Nothing here returns a cookie value: sites are domains and counts.

The live picture and the user's input do not go through here; they use the
dedicated ``/ws/browser`` socket (``_stream.py``).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable, Dict, Tuple

from fastapi import WebSocket

from services.plugin import NodeUserError
from services.plugin.deps import get_database
from services.plugin.ws import ws_response

from ._profiles import ProfileError, ProfileStore, remove_profile_files


def _authenticated_owner(websocket: WebSocket) -> str:
    state = getattr(websocket, "state", None)
    for attribute in ("user_id", "principal_id", "subject"):
        value = getattr(state, attribute, None) if state is not None else None
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    return "owner"


def _require_external_socket(websocket: WebSocket) -> None:
    scope = getattr(websocket, "scope", {}) or {}
    if scope.get("path") == "/ws/internal":
        raise NodeUserError("Browser access requires an authenticated client")


def is_browser_node(node: Dict[str, Any]) -> bool:
    from services.node_registry import get_node_class

    node_type = str(node.get("type") or (node.get("data") or {}).get("type") or "")
    cls = get_node_class(node_type)
    return bool(cls is not None and (getattr(cls, "ui_hints", {}) or {}).get("isBrowserPanel"))


async def resolve_browser_node(owner_id: str, workflow_id: str, node_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The workflow graph and the browser node, after the ownership checks."""
    if not workflow_id:
        raise NodeUserError("workflow_id required")
    if not node_id:
        raise NodeUserError("node_id required")
    saved = await get_database().get_workflow(workflow_id)
    if saved is None:
        raise NodeUserError("Workflow not found")
    graph = saved.data if hasattr(saved, "data") else saved.get("data", saved)
    stored_owner = str(graph.get("owner_id") or "") if isinstance(graph, dict) else ""
    if stored_owner and stored_owner != owner_id:
        raise NodeUserError("Workflow access denied")
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    matches = [n for n in nodes if str(n.get("id") or "") == node_id and is_browser_node(n)]
    if len(matches) != 1:
        raise NodeUserError("Browser node does not belong to the requested workflow")
    return graph, matches[0]


async def _session_for_node(owner_id: str, workflow_id: str, node_id: str, *, create: bool) -> Any:
    """The node's browser session, registering it from the saved settings if asked."""
    from ._netpolicy import parse_allowed_domains
    from ._runtime import get_browser_runtime
    from ._session import BrowserSession, SessionKey

    _, node = await resolve_browser_node(owner_id, workflow_id, node_id)
    runtime = get_browser_runtime()
    session = runtime.find_session(workflow_id, node_id)
    if session is not None or not create:
        return runtime, session
    from .browser import BrowserParams

    saved = await get_database().get_node_parameters(node_id) or {}
    cfg = BrowserParams.model_validate(saved)
    store = ProfileStore(get_database())
    try:
        if cfg.profile_id:
            profile = await store.get(owner_id, cfg.profile_id)
        else:
            workflow = await get_database().get_workflow(workflow_id)
            name = getattr(workflow, "name", None) or "Browser"
            profile = await store.default_for_workflow(owner_id, workflow_id, str(name))
    except ProfileError as exc:
        raise NodeUserError(str(exc)) from exc
    policy = runtime.base_policy(allow_private_network=cfg.allow_private_network, allowed_domains=parse_allowed_domains(cfg.allowed_domains))
    label = str((node.get("data") or {}).get("label") or "") or "a Browser node"
    session = runtime.register_session(
        BrowserSession(key=SessionKey(owner_id, workflow_id, node_id), profile_id=profile.id, label=label, policy=policy)
    )
    return runtime, session


def _session_view(runtime: Any, session: Any) -> Dict[str, Any]:
    if session is None:
        return {"state": "idle", "running": False, "session_id": None}
    controller = runtime.controller(session.profile_id)
    running = runtime.running(session.profile_id) is not None
    view: Dict[str, Any] = {"session_id": session.session_id, "running": running, "state": "idle", "profile": None}
    if controller is None:
        return view
    view["profile"] = {"id": controller.profile_id, "name": controller.profile_name}
    held = controller.lease_session is not None and controller.lease_session.session_id == session.session_id
    if held:
        view.update(controller.snapshot())
    else:
        view["held_by_other"] = controller.lease_session is not None
    tab = controller.tabs.get(controller.active_target_id or "") or {}
    view.update(url=tab.get("url"), title=tab.get("title"), tabs=len(controller.tabs))
    return view


# -- sessions -----------------------------------------------------------------


@ws_response
async def handle_browser_session(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    owner = _authenticated_owner(websocket)
    runtime, session = await _session_for_node(owner, str(data.get("workflow_id") or ""), str(data.get("node_id") or ""), create=False)
    return {"success": True, "session": _session_view(runtime, session)}


@ws_response
async def handle_browser_session_open(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Start the node's browser without an agent step (to look, or to log in first)."""
    _require_external_socket(websocket)
    owner = _authenticated_owner(websocket)
    runtime, session = await _session_for_node(owner, str(data.get("workflow_id") or ""), str(data.get("node_id") or ""), create=True)
    store = ProfileStore(get_database())
    try:
        profile = await store.get(owner, session.profile_id)
    except ProfileError as exc:
        raise NodeUserError(str(exc)) from exc
    running = await runtime.open(profile, wait=20.0)
    await running.controller.acquire_lease(session, wait=5.0)
    url = str(data.get("url") or "").strip()
    if url:
        await _navigate(running, session, url)
    return {"success": True, "session": _session_view(runtime, session)}


@ws_response
async def handle_browser_session_stop(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    owner = _authenticated_owner(websocket)
    runtime, session = await _session_for_node(owner, str(data.get("workflow_id") or ""), str(data.get("node_id") or ""), create=False)
    if session is not None:
        await runtime.stop_profile(session.profile_id, reason="ended by the owner")
    return {"success": True, "session": _session_view(runtime, session)}


async def _navigate(running: Any, session: Any, url: str) -> None:
    from ._netpolicy import url_block_reason

    reason = url_block_reason(url, session.policy)
    if reason:
        raise NodeUserError(f"Cannot open {url}: {reason}.")
    page = await running.page_session()
    try:
        await page.send("Page.navigate", {"url": url}, timeout=30)
    finally:
        await page.detach()


# -- profiles -----------------------------------------------------------------


async def _profiles_view(owner: str, websocket: WebSocket) -> Dict[str, Any]:
    from ._host import local_import_available
    from ._imports import detect_user_browsers
    from ._runtime import get_browser_runtime

    runtime = get_browser_runtime()
    profiles = await ProfileStore(get_database()).list(owner)
    out = []
    for profile in profiles:
        entry = profile.to_wire()
        controller = runtime.controller(profile.id)
        holder = controller.lease_session if controller is not None else None
        entry["running"] = runtime.running(profile.id) is not None
        entry["in_use"] = (
            {"kind": holder.kind, "workflow_id": holder.workflow_id, "node_id": holder.node_id, "label": holder.label}
            if holder is not None
            else None
        )
        out.append(entry)
    available, reason = local_import_available(websocket)
    return {
        "profiles": out,
        "imports": {
            "browser": {"available": available, "reason": reason, "browsers": detect_user_browsers() if available else []},
            "session_file": {"available": True, "max_bytes": _max_upload()},
        },
        "runtime": runtime.status(),
    }


def _max_upload() -> int:
    from ._cookies import MAX_UPLOAD_BYTES

    return MAX_UPLOAD_BYTES


async def _broadcast_profiles() -> None:
    from ._events import dispatch_browser_profiles_updated

    await dispatch_browser_profiles_updated(revision=ProfileStore.bump_revision())


@ws_response
async def handle_browser_profiles_list(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    return {"success": True, **(await _profiles_view(_authenticated_owner(websocket), websocket))}


@ws_response
async def handle_browser_profile_create(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    try:
        profile = await ProfileStore(get_database()).create(_authenticated_owner(websocket), str(data.get("name") or ""))
    except ProfileError as exc:
        raise NodeUserError(str(exc)) from exc
    await _broadcast_profiles()
    return {"success": True, "profile": profile.to_wire()}


@ws_response
async def handle_browser_profile_rename(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    try:
        profile = await ProfileStore(get_database()).rename(
            _authenticated_owner(websocket), str(data.get("profile_id") or ""), str(data.get("name") or "")
        )
    except ProfileError as exc:
        raise NodeUserError(str(exc)) from exc
    from ._runtime import get_browser_runtime

    controller = get_browser_runtime().controller(profile.id)
    if controller is not None:
        controller.profile_name = profile.name
    await _broadcast_profiles()
    return {"success": True, "profile": profile.to_wire()}


@ws_response
async def handle_browser_profile_delete(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    from ._runtime import get_browser_runtime

    owner = _authenticated_owner(websocket)
    profile_id = str(data.get("profile_id") or "")
    store = ProfileStore(get_database())
    try:
        profile = await store.get(owner, profile_id)
    except ProfileError as exc:
        raise NodeUserError(str(exc)) from exc
    runtime = get_browser_runtime()
    controller = runtime.controller(profile.id)
    if controller is not None and controller.lease_session is not None:
        raise NodeUserError(f"{profile.name!r} is in use by {controller.lease_session.label}. Stop it first.")
    await runtime.stop_profile(profile.id, reason="profile deleted")
    await store.delete(owner, profile.id)
    await asyncio.to_thread(remove_profile_files, profile.id)
    await _broadcast_profiles()
    return {"success": True}


async def _running_profile(owner: str, profile_id: str, *, wait: float = 30.0) -> Any:
    from ._runtime import get_browser_runtime

    try:
        profile = await ProfileStore(get_database()).get(owner, profile_id)
    except ProfileError as exc:
        raise NodeUserError(str(exc)) from exc
    return profile, await get_browser_runtime().open(profile, wait=wait)


@ws_response
async def handle_browser_profile_clear_site(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    from ._imports import read_sites

    owner = _authenticated_owner(websocket)
    domain = str(data.get("domain") or "").strip().lower().lstrip(".")
    if not domain:
        raise NodeUserError("domain required")
    profile, running = await _running_profile(owner, str(data.get("profile_id") or ""))
    cookies = (await running.cdp.send("Storage.getCookies")).get("cookies") or []
    for cookie in cookies:
        cookie_domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if cookie_domain == domain or cookie_domain.endswith("." + domain):
            await running.cdp.send(
                "Network.deleteCookies",
                {"name": cookie.get("name"), "domain": cookie.get("domain"), "path": cookie.get("path") or "/"},
            )
    sites = await read_sites(running)
    await ProfileStore(get_database()).update_sites(profile.id, sites)
    await _broadcast_profiles()
    return {"success": True, "sites": sites}


# -- logging in inside the app --------------------------------------------------


@ws_response
async def handle_browser_profile_login_start(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Open a profile for the owner to log in to a site through the live view."""
    _require_external_socket(websocket)
    from ._netpolicy import url_block_reason
    from ._runtime import get_browser_runtime
    from ._session import BrowserSession, SessionKey

    owner = _authenticated_owner(websocket)
    url = str(data.get("url") or "").strip()
    runtime = get_browser_runtime()
    policy = runtime.base_policy()
    if url:
        reason = url_block_reason(url, policy)
        if reason:
            raise NodeUserError(f"Cannot open {url}: {reason}.")
    profile, running = await _running_profile(owner, str(data.get("profile_id") or ""))
    login_id = f"login_{uuid.uuid4().hex[:12]}"
    session = runtime.register_session(
        BrowserSession(
            key=SessionKey(owner, "profile_login", login_id),
            profile_id=profile.id,
            label=f"Log in ({profile.name})",
            policy=policy,
            kind="profile_login",
        )
    )
    await running.controller.acquire_lease(session, wait=5.0)
    if url:
        await _navigate(running, session, url)
    return {"success": True, "login_id": login_id, "session_id": session.session_id, "profile": profile.to_wire()}


@ws_response
async def handle_browser_profile_login_end(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    from ._imports import read_sites
    from ._runtime import get_browser_runtime
    from ._session import SessionKey

    owner = _authenticated_owner(websocket)
    login_id = str(data.get("login_id") or "")
    runtime = get_browser_runtime()
    session = runtime.session(SessionKey(owner, "profile_login", login_id).session_id)
    if session is None:
        return {"success": True}
    running = runtime.running(session.profile_id)
    if running is not None:
        sites = await read_sites(running)
        await ProfileStore(get_database()).update_sites(session.profile_id, sites)
        controller = running.controller
        await controller.release_lease(session.session_id)
        # Close Chrome so the new logins are flushed to disk now.
        await runtime.stop_profile(session.profile_id, reason="login finished")
    runtime.forget_session(session.session_id)
    await _broadcast_profiles()
    return {"success": True}


# -- importing ------------------------------------------------------------------


@ws_response
async def handle_browser_import_connect(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Start reading logins from the user's own browser (after they Allow it)."""
    _require_external_socket(websocket)
    from ._host import local_import_available
    from ._imports import get_import_jobs

    available, reason = local_import_available(websocket)
    if not available:
        raise NodeUserError(f"Importing from your browser needs OpenCompany running on this computer ({reason}). Upload a session file instead.")
    job = get_import_jobs().start_browser(_authenticated_owner(websocket), str(data.get("source") or "chrome"))
    return {"success": True, "pending": True, **job.to_wire()}


@ws_response
async def handle_browser_import_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    from ._imports import get_import_jobs

    job = get_import_jobs().get(_authenticated_owner(websocket), str(data.get("import_id") or ""))
    return {"success": True, **job.to_wire()}


@ws_response
async def handle_browser_import_cancel(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    from ._imports import get_import_jobs

    get_import_jobs().cancel(_authenticated_owner(websocket), str(data.get("import_id") or ""))
    return {"success": True}


@ws_response
async def handle_browser_import_commit(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    from ._imports import apply_jar, get_import_jobs, read_sites

    owner = _authenticated_owner(websocket)
    jobs = get_import_jobs()
    job = jobs.get(owner, str(data.get("import_id") or ""))
    if job.state != "ready" or job.jar is None:
        raise NodeUserError("That import is not ready.")
    domains = [str(d) for d in data.get("domains") or [] if str(d).strip()]
    if not domains:
        raise NodeUserError("Pick at least one site to import.")
    profile, running = await _running_profile(owner, str(data.get("profile_id") or ""))
    controller = running.controller
    if controller.lease_session is not None and controller.lease_session.kind == "node" and controller.state.value != "idle":
        raise NodeUserError(f"{profile.name!r} is busy ({controller.lease_session.label}); try again when it is idle.")
    result = await apply_jar(running, job.jar, domains)
    jobs.drop(job.id)
    sites = await read_sites(running)
    await ProfileStore(get_database()).update_sites(profile.id, sites)
    await _broadcast_profiles()
    return {"success": True, **result, "sites": sites}


# -- runtime --------------------------------------------------------------------


@ws_response
async def handle_browser_runtime_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    _require_external_socket(websocket)
    from ._runtime import get_browser_runtime

    return {"success": True, **get_browser_runtime().status()}


@ws_response
async def handle_browser_runtime_prepare(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Download Chrome and the browser tools now instead of on first use."""
    _require_external_socket(websocket)
    from ._install_bu import get_browser_use_installer
    from ._install_chrome import get_chrome_installer
    from ._runtime import get_browser_runtime

    async def _prepare() -> None:
        for step in (get_chrome_installer().ensure(wait=3600), get_browser_use_installer().ensure(wait=3600)):
            try:
                await step
            except Exception:  # noqa: BLE001 - the status shows the failure
                pass

    task = asyncio.create_task(_prepare())
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    return {"success": True, "pending": True, **get_browser_runtime().status()}


WSHandler = Callable[[Dict[str, Any], WebSocket], Awaitable[Dict[str, Any]]]
WS_HANDLERS: Dict[str, WSHandler] = {
    "browser_session": handle_browser_session,
    "browser_session_open": handle_browser_session_open,
    "browser_session_stop": handle_browser_session_stop,
    "browser_profiles_list": handle_browser_profiles_list,
    "browser_profile_create": handle_browser_profile_create,
    "browser_profile_rename": handle_browser_profile_rename,
    "browser_profile_delete": handle_browser_profile_delete,
    "browser_profile_clear_site": handle_browser_profile_clear_site,
    "browser_profile_login_start": handle_browser_profile_login_start,
    "browser_profile_login_end": handle_browser_profile_login_end,
    "browser_import_connect": handle_browser_import_connect,
    "browser_import_status": handle_browser_import_status,
    "browser_import_cancel": handle_browser_import_cancel,
    "browser_import_commit": handle_browser_import_commit,
    "browser_runtime_status": handle_browser_runtime_status,
    "browser_runtime_prepare": handle_browser_runtime_prepare,
}


async def load_browser_profiles(params: Dict[str, Any]) -> list:
    """``loadOptionsMethod: browserProfiles`` for the node's profile field.

    Lists the caller's profiles. The caller comes from the authenticated
    request, never from ``params``, which the client writes.
    """
    from constants import OWNER_PRINCIPAL_ID
    from services.ws_handler_registry import current_load_options_principal

    owner = current_load_options_principal() or OWNER_PRINCIPAL_ID
    profiles = await ProfileStore(get_database()).list(owner)
    options = [{"value": "", "label": "This workflow's own profile", "description": "Created the first time it runs"}]
    options.extend(
        {"value": p.id, "label": p.name, "description": "Shared profile" if p.kind == "shared" else "Employee profile"} for p in profiles
    )
    return options


__all__ = ["WS_HANDLERS", "is_browser_node", "load_browser_profiles", "resolve_browser_node"]
