"""Owner-only, workflow-scoped Mobile Workspace routes."""

from __future__ import annotations
import hashlib
import hmac
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, Query
from pydantic import BaseModel, ConfigDict, Field
from services.plugin import NodeUserError
from ._control import Lease, MobileError
from ._node import require_mobile_owner

router = APIRouter()
BASE = "/api/mobile/{workflow_id}/{node_id}"


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Setup(StrictBody):
    licenses_accepted: bool = False


class Viewer(StrictBody):
    viewer_id: UUID


class Release(Viewer):
    epoch: int = Field(ge=0)
    resume: bool = False


class DeviceInput(Viewer):
    epoch: int = Field(ge=0)
    operation_id: UUID
    operation: Literal["tap", "swipe", "text", "key", "rotate"]
    parameters: dict = Field(default_factory=dict)
    geometry: dict | None = None


class Task(StrictBody):
    prompt: str = Field(min_length=1, max_length=20000)
    submission_id: UUID


def viewer_identity(principal: str, viewer_id: UUID, cookies: dict) -> str:
    """Tab identity is bound to this authenticated browser session, never a claimed owner."""
    from core.container import container
    from core.auth_cookies import get_session_token

    settings = container.settings()
    session = get_session_token(cookies, settings) or "local-owner"
    message = f"mobile-viewer:{principal}:{session}:{viewer_id}".encode()
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()


async def authorize(request: Request, workflow_id: str, node_id: str) -> str:
    from core.container import container
    from core.auth_cookies import get_session_token
    from constants import OWNER_PRINCIPAL_ID
    from services.authz.workflow_node import resolve_workflow_node
    from services.authz.ws_session import is_allowed_ws_origin

    settings = container.settings()
    if not is_allowed_ws_origin(request.headers.get("origin"), request.headers.get("host"), settings.cors_origins or ()):
        raise HTTPException(403, "Origin not allowed")
    if str(settings.vite_auth_enabled).lower() == "false":
        principal = OWNER_PRINCIPAL_ID
    else:
        token = get_session_token(request.cookies, settings)
        user = await container.user_auth_service().get_current_user(token) if token else None
        if user is None:
            raise HTTPException(401, "Not authenticated")
        principal = str(user.id)
    try:
        await require_mobile_owner(principal)
        await resolve_workflow_node(principal, workflow_id, node_id, workspace_kind="mobile")
    except NodeUserError as exc:
        raise HTTPException(403, str(exc)) from None
    return principal


async def call(awaitable):
    try:
        return await awaitable
    except (MobileError, NodeUserError) as exc:
        raise HTTPException(409, {"code": getattr(exc, "code", "mobile_error"), "message": str(exc)}) from None


@router.get(BASE + "/status")
async def status(principal: str = Depends(authorize)):
    from ._runtime import get_runtime

    runtime = get_runtime()
    # During startup/shutdown return lifecycle status without touching the
    # driver. The serial is assigned before Android and automation are ready.
    if runtime.serial and not runtime.lifecycle_lock.locked() and not runtime.driver_lock.locked():
        try:
            await runtime.driver_call("geometry")
        except Exception:
            # A lost connection is device state, not a failed status endpoint.
            # Return the fresh snapshot so the UI can offer Start/reconnect.
            runtime.start_error = "The phone connection was lost. Click Start phone to reconnect."
    return runtime.snapshot()


@router.get(BASE + "/doctor")
async def doctor(principal: str = Depends(authorize)):
    from ._install import doctor as inspect

    return await inspect()


@router.post(BASE + "/setup")
async def setup(body: Setup, principal: str = Depends(authorize)):
    from ._runtime import get_runtime

    runtime = get_runtime()
    try:
        runtime.setup(body.licenses_accepted)
    except MobileError as exc:
        raise HTTPException(409, str(exc)) from None
    return runtime.snapshot()


@router.post(BASE + "/start")
async def start(principal: str = Depends(authorize)):
    from ._runtime import get_runtime

    return await call(get_runtime().start())


@router.post(BASE + "/stop")
async def stop(principal: str = Depends(authorize)):
    from ._runtime import get_runtime

    await call(get_runtime().stop())
    return get_runtime().snapshot()


@router.post(BASE + "/takeover")
async def takeover(body: Viewer, request: Request, workflow_id: str, node_id: str, principal: str = Depends(authorize)):
    from ._runtime import get_runtime, record_phone_step

    lease = await call(get_runtime().takeover(viewer_identity(principal, body.viewer_id, request.cookies)))
    await record_phone_step(workflow_id, node_id, "You took over the phone")
    return {"owner": lease.owner, "epoch": lease.epoch}


@router.post(BASE + "/release")
async def release(body: Release, request: Request, workflow_id: str, node_id: str, principal: str = Depends(authorize)):
    from ._runtime import get_runtime, record_phone_step

    runtime = get_runtime()
    try:
        if runtime.control.epoch != body.epoch:
            raise MobileError("stale_lease", "Device control changed. Refresh before releasing it.")
        runtime.release(viewer_identity(principal, body.viewer_id, request.cookies), resume=body.resume)
    except MobileError as exc:
        raise HTTPException(409, str(exc)) from None
    await record_phone_step(workflow_id, node_id, "You handed the phone back")
    return runtime.snapshot()


@router.post(BASE + "/resume")
async def resume(principal: str = Depends(authorize)):
    """Let the AI task that waits for the owner go on (the Workspace's Hand
    back), without holding the phone: the view that held it may be gone."""
    from ._runtime import get_runtime

    runtime = get_runtime()
    try:
        runtime.resume_waiting()
    except MobileError as exc:
        raise HTTPException(409, str(exc)) from None
    return runtime.snapshot()


def validate_input(body: DeviceInput) -> dict:
    """Only explicit device operations; no shell, serial, URL, or host path from clients."""
    import math

    p = body.parameters
    allowed = {
        "tap": {"x", "y", "duration"},
        "swipe": {"x", "y", "end_x", "end_y", "duration"},
        "text": {"text"},
        "key": {"key"},
        "rotate": {"orientation"},
    }[body.operation]
    if set(p) - allowed:
        raise HTTPException(422, "Unexpected device input fields")
    if body.operation in {"tap", "swipe"}:
        required = {"x", "y"} | ({"end_x", "end_y"} if body.operation == "swipe" else set())
        if not required <= p.keys() or not body.geometry:
            raise HTTPException(422, "Coordinates and current geometry are required")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in p.values()):
            raise HTTPException(422, "Coordinates must be finite numbers")
        if "duration" in p and not 0 <= p["duration"] <= 3000:
            raise HTTPException(422, "Duration is out of range")
    elif body.operation == "text":
        if not isinstance(p.get("text"), str) or len(p["text"]) > 10000:
            raise HTTPException(422, "Text must be at most 10000 characters")
    elif body.operation == "key" and p.get("key") not in {"home", "back", "enter", "recent", "power"}:
        raise HTTPException(422, "Unsupported key")
    elif body.operation == "rotate" and p.get("orientation") not in {"natural", "left", "right"}:
        raise HTTPException(422, "Unsupported orientation")
    return p


@router.post(BASE + "/input")
async def device_input(body: DeviceInput, request: Request, principal: str = Depends(authorize)):
    from ._runtime import get_runtime

    parameters = validate_input(body)
    viewer = viewer_identity(principal, body.viewer_id, request.cookies)
    result = await call(
        get_runtime().input(Lease("viewer:" + viewer, body.epoch), body.operation, parameters, str(body.operation_id), body.geometry)
    )
    return {"success": True, "result": result}


@router.post(BASE + "/screenshot")
async def screenshot(workflow_id: str, node_id: str, principal: str = Depends(authorize)):
    """The phone's screen, saved as a PNG in the workflow's workspace: its
    file reference (``ref``), which the owner can put on the Canvas
    (``canvas_add``). Reads the screen, so it needs no control of the phone."""
    import base64
    from types import SimpleNamespace

    from core.container import container
    from services.media.workspace import write_media
    from services.workspace_locator import resolve_workspace_root
    from ._runtime import get_runtime

    async def capture():
        shot = await get_runtime().driver_call("screenshot")
        root = await resolve_workspace_root(workflow_id, container.database(), allow_default=False)
        context = SimpleNamespace(workspace_dir=str(root), workflow_id=workflow_id, node_id=node_id)
        return write_media(
            base64.b64decode(shot["base64"]), ctx=context, stem="phone", ext="png", kind="image", mime_type="image/png"
        )

    ref = await call(capture())
    return {"success": True, "ref": ref.model_dump(mode="json")}


@router.post(BASE + "/tasks")
async def submit(body: Task, workflow_id: str, node_id: str, principal: str = Depends(authorize)):
    from services.node_invocations import submit as invoke

    return await call(invoke(principal, workflow_id, node_id, body.prompt, str(body.submission_id)))


@router.post(BASE + "/apk")
async def install_apk(
    request: Request,
    viewer_id: UUID,
    operation_id: UUID,
    epoch: int = Query(..., ge=0),
    filename: str = Query(..., max_length=255),
    principal: str = Depends(authorize),
):
    from uuid import uuid4
    from ._paths import mobile_root
    from ._runtime import get_runtime

    if not filename.lower().endswith(".apk"):
        raise HTTPException(422, "Choose an Android APK file")
    root = mobile_root() / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    path = root / (uuid4().hex + ".apk")
    try:
        size = 0
        with path.open("xb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 256 * 1024 * 1024:
                    raise HTTPException(413, "APK files must be at most 256 MiB")
                output.write(chunk)
        if not size:
            raise HTTPException(422, "APK is empty")
        viewer = viewer_identity(principal, viewer_id, request.cookies)
        await call(get_runtime().install_apk(path, Lease("viewer:" + viewer, epoch), str(operation_id)))
        return {"success": True}
    finally:
        path.unlink(missing_ok=True)


@router.get(BASE + "/tasks/{submission_id}")
async def task_status(submission_id: UUID, workflow_id: str, node_id: str, principal: str = Depends(authorize)):
    from services.node_invocations import status as inspect

    return await call(inspect(principal, workflow_id, node_id, str(submission_id)))


@router.post(BASE + "/tasks/{submission_id}/cancel")
async def cancel(submission_id: UUID, workflow_id: str, node_id: str, principal: str = Depends(authorize)):
    from services.node_invocations import status as inspect, invocation_id
    from ._runtime import get_runtime

    await get_runtime().cancel(workflow_id, node_id, run_id=invocation_id(principal, workflow_id, node_id, str(submission_id)))
    return await call(inspect(principal, workflow_id, node_id, str(submission_id), cancel=True))


@router.websocket("/ws/mobile/{workflow_id}/{node_id}")
async def video(websocket: WebSocket, workflow_id: str, node_id: str, viewer_id: UUID):
    from core.container import container
    from services.authz.ws_session import authenticate_ws
    from services.authz.workflow_node import resolve_workflow_node
    from ._video import stream_video

    principal = await authenticate_ws(websocket, settings=container.settings(), user_auth_service=container.user_auth_service)
    if principal is None:
        return
    try:
        await require_mobile_owner(principal)
        await resolve_workflow_node(principal, workflow_id, node_id, workspace_kind="mobile")
    except NodeUserError:
        await websocket.close(code=4003, reason="Mobile workspace access denied")
        return
    await stream_video(websocket, viewer_identity(principal, viewer_id, websocket.cookies))
