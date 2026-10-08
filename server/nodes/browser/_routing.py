"""Authenticated backend-to-owner routing for the existing Browser protocols."""
from __future__ import annotations

import asyncio
import json
import time
from functools import wraps
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from services.browser_owners import (BrowserOwner, BrowserTransientRoute, bind_profile,
                                    replica_id, routing_for_node, settings, sign_forward, verify_forward)
from services.plugin.base import NodeUserError
from services.plugin.deps import get_database

COMMAND_PATH = "/api/browser/owner/command"
VIEW_PATH = "/ws/browser/owner"


async def forward_command(binding: dict, principal: str, command: str, data: dict) -> dict:
    import httpx
    body = json.dumps({"command": command, "data": data}, separators=(",", ":")).encode()
    headers = {"X-Browser-Forward": sign_forward(principal, "POST", COMMAND_PATH, body), "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=65, follow_redirects=False) as client:
            response = await client.post(binding["base_url"] + COMMAND_PATH, content=body, headers=headers)
            if response.status_code != 200:
                raise NodeUserError("Browser unavailable — waiting for its owner.")
            return response.json()
    except (httpx.HTTPError, ValueError):
        raise NodeUserError("Browser unavailable — waiting for its owner.") from None


async def record_handle(handle_id: str, principal: str, *, profile_id: str | None = None) -> None:
    if getattr(settings(), "distributed_mode", False) is not True:
        return
    async with get_database().get_session() as session:
        row = BrowserTransientRoute(handle_id=handle_id, principal_id=principal, owner_id=replica_id(), profile_id=profile_id, expires_at=time.time() + (300 if handle_id.startswith("imp_") else 3600))
        session.add(row)
        await session.commit()


async def handle_binding(handle_id: str, principal: str) -> dict:
    async with get_database().get_session() as session:
        row = await session.get(BrowserTransientRoute, handle_id)
        if row is None or row.principal_id != principal or row.expires_at < time.time():
            raise NodeUserError("That browser login or import has expired.")
        owner = await session.get(BrowserOwner, row.owner_id)
        if owner is None:
            raise NodeUserError("Browser unavailable — waiting for its owner.")
        return {"profile_id": row.profile_id, "owner_id": owner.owner_id, "base_url": owner.base_url}


async def command_binding(principal: str, data: dict) -> dict | None:
    if data.get("workflow_id") and data.get("node_id"):
        return await routing_for_node(get_database(), str(data["workflow_id"]), str(data["node_id"]), principal)
    if data.get("import_id") or data.get("login_id"):
        return await handle_binding(str(data.get("import_id") or data["login_id"]), principal)
    if data.get("profile_id"):
        from ._profiles import ProfileStore
        profile = await ProfileStore(get_database()).get(principal, str(data["profile_id"]))
        return await bind_profile(get_database(), profile.id, principal)
    return None


def route_handler(name: str, handler: Any) -> Any:
    @wraps(handler)
    async def routed(data: dict, websocket: Any) -> dict:
        if getattr(settings(), "distributed_mode", False) is not True:
            return await handler(data, websocket)
        from ._handlers import _authenticated_owner, _require_external_socket
        _require_external_socket(websocket)
        principal = _authenticated_owner(websocket)
        binding = await command_binding(principal, data)
        if binding is not None and binding["owner_id"] != replica_id():
            if getattr(websocket.state, "browser_forwarded", False):
                raise NodeUserError("Browser owner routing changed; retry after recovery.")
            return await forward_command(binding, principal, name, data)
        from services.browser_owners import assert_runtime_epoch
        await assert_runtime_epoch(get_database())
        result = await handler(data, websocket)
        if result.get("success"):
            handle = result.get("import_id") or result.get("login_id")
            if handle:
                await record_handle(str(handle), principal, profile_id=data.get("profile_id"))
        return result
    return routed


async def owner_command(request: Any) -> dict:
    body = await request.body()
    if len(body) > 24 * 1024 * 1024:
        raise NodeUserError("Browser forwarding request is too large.")
    principal = verify_forward(request.headers.get("X-Browser-Forward", ""), "POST", COMMAND_PATH, body)
    payload = json.loads(body)
    command, data = payload.get("command"), payload.get("data")
    if not isinstance(data, dict):
        raise NodeUserError("Invalid browser command.")
    if command == "cleanup_workflow_profile":
        binding = await command_binding(principal, data)
        if binding is None or binding["owner_id"] != replica_id():
            raise NodeUserError("Browser unavailable — waiting for its owner.")
        from . import _cleanup_deleted_workflow_profile
        return await _cleanup_deleted_workflow_profile(get_database(), principal, str(data.get("profile_id") or ""), str(data.get("deleted_workflow_id") or ""))
    if command == "cleanup_task":
        from services.browser_owners import cleanup_browser_task
        binding = await command_binding(principal, data)
        if binding is None or binding["owner_id"] != replica_id():
            raise NodeUserError("Browser unavailable — waiting for its owner.")
        return await cleanup_browser_task(get_database(), {**binding, "principal_id": principal}, str(data.get("task_id") or ""))
    if command == "session_file":
        import base64
        from ._cookies import MAX_UPLOAD_BYTES, parse_session_file
        from ._imports import get_import_jobs
        binding = await command_binding(principal, data)
        if binding is None or binding["owner_id"] != replica_id():
            raise NodeUserError("Browser unavailable — waiting for its owner.")
        raw = base64.b64decode(data.get("payload", ""), validate=True)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise NodeUserError("Session file is too large.")
        job = get_import_jobs().add_file(principal, parse_session_file(raw))
        await record_handle(job.id, principal, profile_id=data["profile_id"])
        return {"success": True, "profile_id": data["profile_id"], **job.to_wire()}
    from ._handlers import WS_HANDLERS
    if command not in WS_HANDLERS:
        raise NodeUserError("Unknown browser command.")
    socket = SimpleNamespace(state=SimpleNamespace(user_id=principal, browser_forwarded=True), scope={"path": COMMAND_PATH}, headers={}, client=None)
    return await WS_HANDLERS[command](data, socket)


async def proxy_view(websocket: Any, first: dict, principal: str, binding: dict) -> None:
    from websockets.asyncio.client import connect
    body = json.dumps(first, separators=(",", ":")).encode()
    parts = urlsplit(binding["base_url"])
    url = urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc, VIEW_PATH, "", ""))
    try:
        async with connect(url, additional_headers={"X-Browser-Forward": sign_forward(principal, "WS", VIEW_PATH, body)}, max_size=16 * 1024 * 1024, compression=None, open_timeout=10) as upstream:
            await upstream.send(body.decode())
            async def upstream_reader() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)
            async def downstream_reader() -> None:
                while True:
                    message = await websocket.receive_text()
                    if len(message) > 128 * 1024:
                        raise NodeUserError("Browser input is too large.")
                    await upstream.send(message)
            tasks = [asyncio.create_task(upstream_reader()), asyncio.create_task(downstream_reader())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        await websocket.close(code=1013, reason="Browser unavailable — waiting for its owner.")
