"""Talking to a custom connector's MCP server, with the official ``mcp`` SDK.

Each connection uses our own httpx client with redirects off, so a server
cannot bounce the owner's sign-in to another host. The sign-in goes as a
header. The transport is streamable HTTP, or the older SSE transport for a
server that refuses it (the MCP spec's own fallback), and the one that
answered when the connector was added is kept.

Before connecting, the server's host is resolved and every address it gives
is checked against ``services/netpolicy.py``. Cloud metadata and
OpenCompany's own ports are never reached. Plain http is allowed only to
this machine or the owner's own network, because over the internet it would
carry the sign-in in clear. The addresses are checked when connecting, not
pinned for the connection.

A tool as an employee sees it is ``<connector>__<tool>``. A tool whose name
or input description no model could use is kept with the reason, never
renamed (``unusable_reason``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from services.netpolicy import (
    NetPolicy,
    address_block_reason,
    host_block_reason,
    is_local_network,
    literal_ip,
    local_addresses,
    own_ports_from_env,
)

if TYPE_CHECKING:
    from mcp import ClientSession
    from mcp.types import CallToolResult, InitializeResult, Tool

Transport = Literal["streamable_http", "sse"]
TRANSPORTS: Tuple[Transport, ...] = ("streamable_http", "sse")

#: A connect, the opening handshake, or one listing of tools.
TIMEOUT_S = 30.0
#: Reading the server's tools, from connecting to the last page, with every
#: transport tried. The app waits 60 s for a credential probe
#: (CREDENTIAL_PROBE_REQUEST_TIMEOUT in WebSocketContext.tsx), so a slow read
#: is never still saving after the app gave up on it.
DISCOVER_S = 45.0
#: The longest a tool call may take to answer.
CALL_TIMEOUT_S = 300.0
#: The most tools one connector reads.
MAX_TOOLS = 500
#: The most of a tool's description kept.
MAX_DESCRIPTION = 4000
#: How a tool is called (``<connector>__<tool>``): the strictest provider's rule.
TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SEPARATOR = "__"

#: Headers a custom sign-in may not set: the transport's own.
_RESERVED_HEADERS = frozenset(
    {"host", "content-length", "content-type", "transfer-encoding", "connection", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id"}
)
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$")


class ConnectorError(Exception):
    """What went wrong, in words to show the owner."""


@dataclass(frozen=True)
class SignIn:
    """How a connector signs in to its server: nothing, a bearer token, or one
    header of the owner's choosing."""

    kind: Literal["none", "bearer", "header"] = "none"
    token: str = ""
    header: str = ""
    value: str = ""

    @classmethod
    def from_form(cls, raw: Dict[str, Any]) -> "SignIn":
        """From the Add form; refuses what cannot be sent."""
        kind = str(raw.get("kind") or "none")
        if kind == "none":
            return cls()
        if kind == "bearer":
            token = str(raw.get("token") or "").strip()
            if not token or any(c in token for c in "\r\n"):
                raise ConnectorError("Paste the token the server gave you.")
            return cls(kind="bearer", token=token)
        if kind == "header":
            header = str(raw.get("header") or "").strip()
            value = str(raw.get("value") or "").strip()
            if not _HEADER_NAME.match(header):
                raise ConnectorError("Give the header a name of letters, digits and dashes, such as X-API-Key.")
            if header.lower() in _RESERVED_HEADERS:
                raise ConnectorError(f"{header} is set by the connection itself; use another header.")
            if not value or any(c in value for c in "\r\n"):
                raise ConnectorError("Give the header its value.")
            return cls(kind="header", header=header, value=value)
        raise ConnectorError("Choose how the connector signs in.")

    @classmethod
    def load(cls, stored: Dict[str, Any]) -> "SignIn":
        kind = stored.get("kind")
        if kind == "bearer":
            return cls(kind="bearer", token=str(stored.get("token") or ""))
        if kind == "header":
            return cls(kind="header", header=str(stored.get("header") or ""), value=str(stored.get("value") or ""))
        return cls()

    def dump(self) -> Dict[str, str]:
        return {"kind": self.kind, "token": self.token, "header": self.header, "value": self.value}

    def public(self) -> Dict[str, str]:
        """What may be shown or kept in plain text: never the token or value."""
        return {"kind": self.kind, **({"header": self.header} if self.kind == "header" else {})}

    def headers(self) -> Dict[str, str]:
        if self.kind == "bearer":
            return {"Authorization": f"Bearer {self.token}"}
        if self.kind == "header":
            return {self.header: self.value}
        return {}


@dataclass
class Discovery:
    """What a server said about itself, and its tools as read."""

    transport: Transport
    server: Dict[str, Any]
    instructions: Optional[str]
    tools: List["Tool"]


def _policy() -> NetPolicy:
    # A connector may reach the owner's own network (a local MCP server),
    # never OpenCompany's own ports or cloud metadata.
    return NetPolicy(allow_private_network=True, blocked_local_ports=own_ports_from_env(), local_addresses=local_addresses())


async def check_server(url: str) -> str:
    """Whether a connector may connect to ``url``; returns its host, or raises
    ``ConnectorError`` saying why not."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise ConnectorError("That isn't a valid URL.") from None
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        raise ConnectorError("Give the server's full URL, starting with https://.")
    if parts.username or parts.password:
        raise ConnectorError("Put the sign-in under Sign-in, not in the URL.")
    port = port or (443 if scheme == "https" else 80)
    policy = _policy()
    reason = host_block_reason(host, policy)
    if reason:
        raise ConnectorError(f"{host} can't be used: {reason}.")
    literal = literal_ip(host)
    addresses = [literal] if literal is not None else await _resolve(host, port)
    for address in addresses:
        reason = address_block_reason(address, port, policy)
        if reason:
            raise ConnectorError(f"{host} can't be used: {reason}.")
    if scheme == "http" and not all(is_local_network(address, policy) for address in addresses):
        raise ConnectorError("Use https:// for a server on the internet: plain http would send its sign-in unencrypted.")
    return host


async def _resolve(host: str, port: int) -> List[Any]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise ConnectorError(f"Couldn't find {host}. Check the URL.") from None
    found = []
    for info in infos:
        try:
            found.append(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        except ValueError:
            continue
    if not found:
        raise ConnectorError(f"Couldn't find {host}. Check the URL.")
    return found


def _client(headers: Dict[str, str], *, transport: Optional[httpx.AsyncBaseTransport]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=headers, timeout=httpx.Timeout(TIMEOUT_S, read=CALL_TIMEOUT_S), follow_redirects=False, transport=transport
    )


@asynccontextmanager
async def open_session(
    url: str, sign_in: SignIn, transport: Transport, *, http_transport: Optional[httpx.AsyncBaseTransport] = None
) -> AsyncIterator[Tuple["ClientSession", "InitializeResult"]]:
    """An initialized session with the server. ``http_transport`` replaces the
    network (tests)."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.types import Implementation

    from core.approot import app_version

    info = Implementation(name="OpenCompany", version=app_version())
    limit = timedelta(seconds=TIMEOUT_S)
    if transport == "streamable_http":
        async with _client(sign_in.headers(), transport=http_transport) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _session_id):
                async with ClientSession(read, write, read_timeout_seconds=limit, client_info=info) as session:
                    yield session, await session.initialize()
        return

    def factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
        # The SDK's own factory follows redirects.
        return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, follow_redirects=False, transport=http_transport)

    async with sse_client(url, headers=sign_in.headers(), timeout=TIMEOUT_S, sse_read_timeout=CALL_TIMEOUT_S, httpx_client_factory=factory) as (
        read,
        write,
    ):
        async with ClientSession(read, write, read_timeout_seconds=limit, client_info=info) as session:
            yield session, await session.initialize()


async def list_tools(session: "ClientSession") -> List["Tool"]:
    """Every page of the server's tools, up to ``MAX_TOOLS``."""
    from mcp.types import PaginatedRequestParams

    tools: List["Tool"] = []
    cursor: Optional[str] = None
    while len(tools) < MAX_TOOLS:
        page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
        tools.extend(page.tools)
        cursor = page.nextCursor
        if not cursor:
            break
    return tools[:MAX_TOOLS]


async def read_server(
    url: str, sign_in: SignIn, transport: Transport, *, http_transport: Optional[httpx.AsyncBaseTransport] = None
) -> Discovery:
    """Connect over ``transport`` and read what the server offers."""
    async with open_session(url, sign_in, transport, http_transport=http_transport) as (session, init):
        tools = await list_tools(session)
    server = init.serverInfo
    return Discovery(
        transport=transport,
        server={"name": server.name, "title": server.title, "version": server.version},
        instructions=(init.instructions or None),
        tools=tools,
    )


async def discover(url: str, sign_in: SignIn, *, http_transport: Optional[httpx.AsyncBaseTransport] = None) -> Discovery:
    """A new connector's first read: streamable HTTP, else the older SSE
    transport, within ``DISCOVER_S`` in all. Raises ``ConnectorError`` with
    the first transport's failure."""
    host = await check_server(url)
    first: Optional[BaseException] = None
    try:
        async with asyncio.timeout(DISCOVER_S):
            for transport in TRANSPORTS:
                try:
                    return await read_server(url, sign_in, transport, http_transport=http_transport)
                except Exception as exc:  # noqa: BLE001 - described below, in words for the owner
                    first = first or exc
    except TimeoutError as exc:
        first = first or exc
    raise ConnectorError(describe(first, host))


async def read_again(
    url: str, sign_in: SignIn, transport: Transport, *, http_transport: Optional[httpx.AsyncBaseTransport] = None
) -> Discovery:
    """Read a saved connector's server again (Test, Refresh), within
    ``DISCOVER_S``."""
    host = await check_server(url)
    try:
        async with asyncio.timeout(DISCOVER_S):
            return await read_server(url, sign_in, transport, http_transport=http_transport)
    except Exception as exc:  # noqa: BLE001 - described below, in words for the owner
        raise ConnectorError(describe(exc, host)) from None


@dataclass
class CallResult:
    """What a tool answered: its text for the model, its structured result
    when it gave one, and whether it says the call failed."""

    text: str
    structured: Optional[Dict[str, Any]]
    is_error: bool


def check_arguments(schema: Dict[str, Any], arguments: Dict[str, Any]) -> None:
    """Refuse arguments the tool's input schema does not accept."""
    from jsonschema import ValidationError
    from jsonschema.validators import validator_for

    try:
        validator_for(schema)(schema).validate(arguments)
    except ValidationError as exc:
        raise ConnectorError(f"The arguments don't fit the tool: {exc.message}") from None


def result_text(result: "CallToolResult") -> str:
    """A tool's answer as text for the model: its text parts, and a line
    naming each other part."""
    parts: List[str] = []
    for block in result.content:
        if block.type == "text":
            parts.append(block.text)
        elif block.type == "resource" and getattr(block.resource, "text", None) is not None:
            parts.append(block.resource.text)
        elif block.type == "resource_link":
            parts.append(f"[{block.name}: {block.uri}]")
        else:
            parts.append(f"[{block.type} not shown]")
    return "\n".join(parts)


async def call_tool(
    url: str,
    sign_in: SignIn,
    transport: Transport,
    name: str,
    arguments: Dict[str, Any],
    *,
    http_transport: Optional[httpx.AsyncBaseTransport] = None,
) -> CallResult:
    """Call one of the server's tools, within ``CALL_TIMEOUT_S``."""
    host = await check_server(url)
    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            async with open_session(url, sign_in, transport, http_transport=http_transport) as (session, _init):
                result = await session.call_tool(name, arguments, read_timeout_seconds=timedelta(seconds=CALL_TIMEOUT_S))
    except Exception as exc:  # noqa: BLE001 - described below, in words for the owner
        raise ConnectorError(describe(exc, host)) from None
    return CallResult(text=result_text(result), structured=result.structuredContent, is_error=bool(result.isError))


def describe(exc: Optional[BaseException], host: str) -> str:
    """A failure to reach or read a server, in words for the owner."""
    from mcp.shared.exceptions import McpError

    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    if isinstance(exc, ConnectorError):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return f"{host} refused the sign-in (HTTP {status}). Check the token or header."
        if status == 404:
            return f"{host} has no MCP server at that address (HTTP 404). Check the URL's path."
        if 300 <= status < 400:
            return f"{host} sent the connection elsewhere (HTTP {status}). Connectors don't follow redirects: use the address it points to."
        return f"{host} answered with an error (HTTP {status})."
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return f"{host} didn't answer in time."
    if isinstance(exc, httpx.ConnectError):
        return f"Couldn't connect to {host}. Check that the server is running."
    if isinstance(exc, McpError):
        return f"{host} refused the request: {exc.error.message}"
    return f"{host} didn't answer as an MCP server."


def snapshot(tool: "Tool") -> Dict[str, Any]:
    """A tool as kept: what the server said about it."""
    annotations = tool.annotations
    return {
        "name": tool.name,
        "title": tool.title or (annotations.title if annotations else None),
        "description": (tool.description or "")[:MAX_DESCRIPTION],
        "input_schema": tool.inputSchema,
        "read_only": bool(annotations and annotations.readOnlyHint is True),
    }


def call_name(slug: str, tool: str) -> str:
    """How an employee calls ``tool`` of the connector ``slug``."""
    return f"{slug}{SEPARATOR}{tool}"


def unusable_reason(slug: str, tool: Dict[str, Any], taken: set) -> Optional[str]:
    """Why no model could call this tool, or None."""
    from jsonschema import SchemaError
    from jsonschema.validators import validator_for

    name = tool.get("name") or ""
    if name in taken:
        return "Another tool on this server has the same name."
    if not TOOL_NAME.match(call_name(slug, name)):
        return "Its name is too long, or has characters a model can't use."
    schema = tool.get("input_schema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return "Its inputs aren't described as an object."
    try:
        validator_for(schema).check_schema(schema)
    except SchemaError:
        return "Its inputs aren't valid JSON Schema."
    return None


def snapshots(slug: str, tools: List["Tool"]) -> List[Dict[str, Any]]:
    """The tools as kept, each saying whether it can be used and why not."""
    out: List[Dict[str, Any]] = []
    taken: set = set()
    for tool in tools:
        item = snapshot(tool)
        reason = unusable_reason(slug, item, taken)
        taken.add(item["name"])
        out.append({**item, "usable": reason is None, "reason": reason})
    return out


def changes(before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """What a refresh found: tools added, removed, or changed in any way."""
    old = {tool["name"]: tool for tool in before}
    new = {tool["name"]: tool for tool in after}
    return {
        "added": [name for name in new if name not in old],
        "removed": [name for name in old if name not in new],
        "changed": [name for name in new if name in old and new[name] != old[name]],
    }


__all__ = [
    "CALL_TIMEOUT_S",
    "CallResult",
    "ConnectorError",
    "Discovery",
    "SEPARATOR",
    "SignIn",
    "Transport",
    "call_name",
    "call_tool",
    "changes",
    "check_arguments",
    "check_server",
    "describe",
    "discover",
    "open_session",
    "read_again",
    "snapshots",
]
