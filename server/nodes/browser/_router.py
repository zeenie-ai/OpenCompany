"""HTTP and WebSocket routes the Browser plugin mounts (``register_router``).

- ``/ws/browser``: the live view (``_stream.py``).
- ``POST /api/browser/profiles/{profile_id}/session-file``: a session file to
  import logins from. ``AuthMiddleware`` covers it like every ``/api/`` route.
  The file is read in bounded chunks (the declared length is not trusted),
  parsed in memory and never written to disk; the response lists the sites
  it holds (domains and counts only) and an ``import_id``. Nothing is applied
  until the owner picks sites over the WebSocket (``browser_import_commit``).
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, File, HTTPException, Path as PathParam, Request, UploadFile

from ._stream import browser_live_view

router = APIRouter()
# Registered directly: include_router would add a pathless entry here.
router.add_api_websocket_route("/ws/browser", browser_live_view)
router.add_api_websocket_route("/ws/browser/owner", browser_live_view)


@router.post("/api/browser/owner/command")
async def route_owner_command(request: Request) -> Dict[str, Any]:
    from ._routing import owner_command
    from services.plugin.base import NodeUserError
    try:
        return await owner_command(request)
    except (NodeUserError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Browser owner request refused.") from None

_CHUNK = 256 * 1024


def _request_owner(request: Request) -> str:
    """The principal the WebSocket side uses: the JWT subject, else the owner."""
    from constants import OWNER_PRINCIPAL_ID

    user_id = getattr(request.state, "user_id", None)
    if user_id in (None, 0, "0", ""):
        return OWNER_PRINCIPAL_ID
    return str(user_id)


@router.post("/api/browser/profiles/{profile_id}/session-file")
async def upload_session_file(
    request: Request,
    profile_id: str = PathParam(..., min_length=1, max_length=64),
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    from services.plugin.deps import get_database

    from ._cookies import MAX_UPLOAD_BYTES, CookieFormatError, parse_session_file
    from ._imports import get_import_jobs
    from ._profiles import ProfileError, ProfileStore

    owner = _request_owner(request)
    try:
        await ProfileStore(get_database()).get(owner, profile_id)
    except ProfileError:
        raise HTTPException(status_code=404, detail="Browser profile not found") from None

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"Session files can be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        chunks.append(chunk)
    if not total:
        raise HTTPException(status_code=400, detail="The file is empty.")
    from services.browser_owners import bind_profile, replica_id, settings
    if getattr(settings(), "distributed_mode", False) is True:
        from ._routing import forward_command
        import base64
        binding = await bind_profile(get_database(), profile_id, owner)
        if binding["owner_id"] != replica_id():
            return await forward_command(binding, owner, "session_file", {"profile_id": profile_id, "payload": base64.b64encode(b"".join(chunks)).decode()})
    try:
        jar = parse_session_file(b"".join(chunks))
    except CookieFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    finally:
        chunks.clear()
    job = get_import_jobs().add_file(owner, jar)
    from ._routing import record_handle
    await record_handle(job.id, owner, profile_id=profile_id)
    return {"success": True, "profile_id": profile_id, **job.to_wire()}


__all__ = ["router"]
