"""The Connectors page's commands for custom MCP connectors.

- ``mcp_connector_add`` ``{name?, url, sign_in: {kind, token?, header?,
  value?}}``: reads the server (``_client.discover``) and saves it only when
  it answered; ``{ref, tools}``.
- ``mcp_connector_test`` ``{ref}``: whether the server still answers;
  ``{ok, message}``, saving nothing.
- ``mcp_connector_refresh`` ``{ref}``: reads the tools again. A change (tools
  added, removed or changed, or new server instructions) waits as
  ``pending`` for the owner, since a server can change what a tool says it
  does; ``{changes}``, null when nothing changed.
- ``mcp_connector_review`` ``{ref, accept}``: takes or drops that change.
- ``mcp_connector_set_tool`` ``{ref, tool, enabled?, ask?}``: whether
  employees may use one of its tools, and whether that tool asks the owner
  first; ``{tool, enabled, ask}``.
- ``mcp_connector_remove`` ``{ref}``.

``@ws_response``: a failure the owner can fix (a wrong URL, a refused
sign-in) is a ``NodeUserError``, one WARN line, its words shown as they are.
Never from the internal worker socket. Every change is announced as a
credential event, so each open page refetches the catalogue.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from fastapi import WebSocket

from services.plugin.base import NodeUserError
from services.plugin.ws import ws_response

from ._client import ConnectorError, SignIn, changes, discover, read_again, snapshots
from ._store import (
    default_settings,
    describe_new,
    exists,
    get_connector,
    new_slug,
    now,
    read_access,
    ref_of,
    remove,
    save_meta,
    save_new,
    slug_of,
)


def _refused(websocket: WebSocket) -> Optional[Dict[str, Any]]:
    if (getattr(websocket, "scope", {}) or {}).get("path") == "/ws/internal":
        return {"success": False, "error": "Connectors are managed from the app."}
    return None


def _principal(websocket: WebSocket) -> Optional[str]:
    from services.authz.ws_surface import execution_principal

    return execution_principal({}, websocket)


async def _announce(ref: str, stage: str) -> None:
    from services.status_broadcaster import get_status_broadcaster

    await get_status_broadcaster().broadcast_credential_event(f"credential.api_key.{stage}", provider=ref)


@ws_response
async def handle_mcp_connector_add(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    if refused := _refused(websocket):
        return refused
    from services.credentials.onepassword import CredentialSourceError
    from services.plugin.deps import get_auth_service

    try:
        get_auth_service().require_local_credentials()
    except CredentialSourceError as exc:
        raise NodeUserError(str(exc)) from None
    name = " ".join(str(data.get("name") or "").split())[:60]
    url = str(data.get("url") or "").strip()
    sign_in_form = data.get("sign_in")
    try:
        sign_in = SignIn.from_form(sign_in_form if isinstance(sign_in_form, dict) else {})
        slug = new_slug(name, url)
        if not slug:
            raise ConnectorError(
                "Give the connector a name with at least one letter or digit." if name else "Give the server's full URL, starting with https://."
            )
        ref = ref_of(slug)
        if await exists(ref):
            raise ConnectorError(f"There is already a connector called {slug}. Choose another name.")
        found = await discover(url, sign_in)
        meta = describe_new(slug, name or (urlsplit(url).hostname or slug), url, sign_in, found)
        await save_new(ref, url, sign_in, meta)
    except ConnectorError as exc:
        raise NodeUserError(str(exc)) from None
    await _announce(ref, "saved")
    return {"success": True, "ref": ref, "tools": len(meta["tools"])}


@ws_response
async def handle_mcp_connector_test(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    if refused := _refused(websocket):
        return refused
    try:
        connector = await get_connector(str(data.get("ref") or ""), principal=_principal(websocket))
        url, sign_in = await read_access(connector)
        found = await read_again(url, sign_in, connector.meta.get("transport") or "streamable_http")
    except ConnectorError as exc:
        return {"success": True, "ok": False, "message": str(exc)}
    count = len(found.tools)
    return {"success": True, "ok": True, "message": f"{connector.host} answered with {count} tool{'' if count == 1 else 's'}."}


@ws_response
async def handle_mcp_connector_refresh(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    if refused := _refused(websocket):
        return refused
    try:
        connector = await get_connector(str(data.get("ref") or ""), principal=_principal(websocket))
        url, sign_in = await read_access(connector)
        found = await read_again(url, sign_in, connector.meta.get("transport") or "streamable_http")
        tools = snapshots(connector.slug, found.tools)
        diff: Dict[str, Any] = changes(connector.tools, tools)
        diff["instructions"] = found.instructions != connector.meta.get("instructions")
        meta = dict(connector.meta, server=found.server, read_at=now())
        changed = any(diff.values())
        meta["pending"] = {"tools": tools, "instructions": found.instructions, **diff, "read_at": meta["read_at"]} if changed else None
        await save_meta(connector, meta)
    except ConnectorError as exc:
        raise NodeUserError(str(exc)) from None
    await _announce(connector.ref, "saved")
    return {"success": True, "changes": diff if changed else None}


@ws_response
async def handle_mcp_connector_review(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    if refused := _refused(websocket):
        return refused
    accept = data.get("accept") is True
    try:
        connector = await get_connector(str(data.get("ref") or ""), principal=_principal(websocket))
        pending = connector.meta.get("pending")
        if not isinstance(pending, dict):
            raise ConnectorError("There is no change waiting.")
        meta = dict(connector.meta, pending=None)
        if accept:
            tools = pending.get("tools") or []
            meta.update(tools=tools, instructions=pending.get("instructions"), settings=default_settings(tools, kept=connector.settings))
        await save_meta(connector, meta)
    except ConnectorError as exc:
        raise NodeUserError(str(exc)) from None
    await _announce(connector.ref, "saved")
    return {"success": True, "accepted": accept}


@ws_response
async def handle_mcp_connector_set_tool(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    if refused := _refused(websocket):
        return refused
    name = str(data.get("tool") or "")
    try:
        connector = await get_connector(str(data.get("ref") or ""), principal=_principal(websocket))
        tool = next((tool for tool in connector.tools if tool["name"] == name), None)
        if tool is None:
            raise ConnectorError("This connector has no such tool.")
        if not tool["usable"]:
            raise ConnectorError(f"{name} can't be used. {tool['reason']}")
        setting = dict(default_settings([tool], kept=connector.settings)[name])
        setting.update({key: data[key] for key in ("enabled", "ask") if isinstance(data.get(key), bool)})
        await save_meta(connector, dict(connector.meta, settings={**connector.settings, name: setting}))
    except ConnectorError as exc:
        raise NodeUserError(str(exc)) from None
    await _announce(connector.ref, "saved")
    return {"success": True, "tool": name, **setting}


@ws_response
async def handle_mcp_connector_remove(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    if refused := _refused(websocket):
        return refused
    ref = str(data.get("ref") or "")
    if slug_of(ref) is None:
        raise NodeUserError("There is no such connector.")
    await remove(ref, principal=_principal(websocket))
    await _announce(ref, "deleted")
    return {"success": True, "ref": ref}


WS_HANDLERS = {
    "mcp_connector_add": handle_mcp_connector_add,
    "mcp_connector_test": handle_mcp_connector_test,
    "mcp_connector_refresh": handle_mcp_connector_refresh,
    "mcp_connector_review": handle_mcp_connector_review,
    "mcp_connector_set_tool": handle_mcp_connector_set_tool,
    "mcp_connector_remove": handle_mcp_connector_remove,
}

__all__ = ["WS_HANDLERS"]
