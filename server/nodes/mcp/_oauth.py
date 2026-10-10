"""Signing a custom connector in with OAuth, through the official ``mcp`` SDK's
``OAuthClientProvider``: it finds the server's authorization server,
registers a client, signs in with an authorization code and PKCE, and
refreshes the token.

Signing in (``start_sign_in``) runs in the background, because the owner
finishes it in their browser:

1. A first request to the server (an MCP ``initialize``, sent as a plain
   POST: inside an MCP session its 30 s read timeout would cut the owner
   off) is refused with 401. The provider registers a client and hands over
   the address of the server's sign-in page, which the command that started
   it returns for the app to open.
2. The owner signs in, and the authorization server sends their browser to
   ``CALLBACK_PATH`` (``_router.py``) with a code and the sign-in's
   ``state``. ``finish`` hands the code to the waiting provider, which
   exchanges it for tokens.
3. ``on_signed_in``, from the command that started it, saves the result (a
   new connector with its tools, or a saved one's new sign-in) and says how
   it went, which the callback page shows.

What a sign-in keeps: its tokens in the OAuth token store
(``AuthService.store_oauth_tokens``, keyed by the connector's reference,
with their expiry), and in the connector's encrypted sign-in
(``SignIn.oauth``) the client it registered and the endpoints it found.

A later connection (``connection_auth``) builds a provider from those, with
the token's expiry and the endpoints set before first use. The SDK itself
loads only the tokens and the client: without the expiry it would send an
expired token and answer the 401 with a sign-in no one is there to finish.
With them it refreshes an expired token, and the new one is kept. When the
refresh is refused, only the owner can sign in again: the connection fails
with ``SignInNeeded``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlsplit

import httpx

from core.logging import get_logger

from ._client import DISCOVER_S, TIMEOUT_S, ConnectorError, SignIn, check_server, describe, http_client

if TYPE_CHECKING:
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

logger = get_logger(__name__)

CALLBACK_PATH = "/api/mcp/oauth/callback"
#: How long a sign-in waits for the owner to finish it in their browser.
SIGN_IN_S = 600.0


class SignInNeeded(ConnectorError):
    """Only the owner can sign the connector in again."""


def _auth() -> Any:
    from services.plugin.deps import get_auth_service

    return get_auth_service()


def _initialize() -> Dict[str, Any]:
    """The opening of an MCP session, as a sign-in's first request."""
    from mcp.types import LATEST_PROTOCOL_VERSION

    from core.approot import app_version

    return {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "OpenCompany", "version": app_version()}},
    }


# ----- what the SDK stores through -----


class _Memory:
    """The SDK's token storage while signing in: kept only once it is done."""

    def __init__(self) -> None:
        self.tokens: Optional["OAuthToken"] = None
        self.client: Optional["OAuthClientInformationFull"] = None

    async def get_tokens(self) -> Optional["OAuthToken"]:
        return self.tokens

    async def set_tokens(self, tokens: "OAuthToken") -> None:
        self.tokens = tokens

    async def get_client_info(self) -> Optional["OAuthClientInformationFull"]:
        return self.client

    async def set_client_info(self, client_info: "OAuthClientInformationFull") -> None:
        self.client = client_info


class _Saved:
    """A saved connector's tokens, as read when its connection opened, and
    the client it registered. A refreshed token is kept at once."""

    def __init__(self, ref: str, client: "OAuthClientInformationFull", tokens: Optional["OAuthToken"]) -> None:
        self.ref = ref
        self.client = client
        self.tokens = tokens

    async def get_tokens(self) -> Optional["OAuthToken"]:
        return self.tokens

    async def set_tokens(self, tokens: "OAuthToken") -> None:
        await save_tokens(self.ref, tokens)
        self.tokens = tokens

    async def get_client_info(self) -> Optional["OAuthClientInformationFull"]:
        return self.client

    async def set_client_info(self, client_info: "OAuthClientInformationFull") -> None:
        self.client = client_info


async def save_tokens(ref: str, tokens: "OAuthToken") -> None:
    """Keep a connector's tokens with their expiry. A refresh that sends no
    refresh token keeps the one it had."""
    from mcp.shared.auth_utils import calculate_token_expiry

    auth = _auth()
    refresh = tokens.refresh_token or await auth.get_oauth_refresh_token(ref) or ""
    expires = calculate_token_expiry(tokens.expires_in)
    kept = await auth.store_oauth_tokens(
        provider=ref,
        access_token=tokens.access_token,
        refresh_token=refresh,
        scopes=tokens.scope,
        expiry=datetime.fromtimestamp(expires, timezone.utc) if expires is not None else None,
    )
    if not kept:
        raise ConnectorError("Couldn't keep the sign-in. Try again.")


# ----- signing in -----


@dataclass
class SignedIn:
    """A finished sign-in: the provider, signed in, to read the server with
    now; what the connector keeps of it; and its tokens."""

    auth: "OAuthClientProvider"
    sign_in: SignIn
    tokens: "OAuthToken"


def _kept(provider: "OAuthClientProvider") -> Dict[str, Any]:
    """What a sign-in keeps besides its tokens (``SignIn.oauth``)."""
    context = provider.context

    def dump(model: Any) -> Optional[Dict[str, Any]]:
        return model.model_dump(mode="json", exclude_none=True) if model is not None else None

    return {
        "client": dump(context.client_info),
        "authorization_server": dump(context.oauth_metadata),
        "resource": dump(context.protected_resource_metadata),
        "auth_server_url": context.auth_server_url,
        "scope": context.client_metadata.scope,
    }


async def sign_in(
    url: str,
    *,
    redirect_uri: str,
    redirect_handler: Callable[[str], Awaitable[None]],
    callback_handler: Callable[[], Awaitable[Tuple[str, Optional[str]]]],
    http_transport: Optional[httpx.AsyncBaseTransport] = None,
) -> SignedIn:
    """Sign in to the server at ``url``: steps 1 and 2 above."""
    from mcp.client.auth import OAuthClientProvider, OAuthFlowError
    from mcp.shared.auth import OAuthClientMetadata

    host = await check_server(url)
    storage = _Memory()
    provider = OAuthClientProvider(
        server_url=url,
        client_metadata=OAuthClientMetadata(client_name="OpenCompany", redirect_uris=[redirect_uri]),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        timeout=SIGN_IN_S,
    )
    try:
        async with asyncio.timeout(SIGN_IN_S + 2 * TIMEOUT_S):
            async with http_client({}, transport=http_transport, auth=provider) as http:
                async with http.stream("POST", url, json=_initialize(), headers={"Accept": "application/json, text/event-stream"}):
                    pass
    except ConnectorError:
        raise
    except OAuthFlowError as exc:
        raise ConnectorError(f"Signing in to {host} failed: {exc}") from None
    except Exception as exc:  # noqa: BLE001 - described in words for the owner
        raise ConnectorError(describe(exc, host)) from None
    if storage.tokens is None:
        raise ConnectorError(f"{host} didn't ask to sign in. Choose None, or the sign-in it uses.")
    return SignedIn(auth=provider, sign_in=SignIn(kind="oauth", oauth=_kept(provider)), tokens=storage.tokens)


@dataclass(eq=False)
class _Waiting:
    """A sign-in waiting for the owner's browser to come back."""

    code: "asyncio.Future[Tuple[str, Optional[str]]]"
    task: "Optional[asyncio.Task[str]]" = None


#: Sign-ins under way, by their ``state``.
_waiting: Dict[str, _Waiting] = {}
#: Kept referenced, so a sign-in is not garbage collected while it waits.
_running: Set["asyncio.Task[str]"] = set()


def _ended(task: "asyncio.Task[str]") -> None:
    _running.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if isinstance(error, ConnectorError):
        logger.info("A connector's sign-in ended", reason=str(error))
    elif error is not None:
        logger.warning("A connector's sign-in failed", exc_info=error)


async def start_sign_in(
    url: str,
    *,
    redirect_uri: str,
    on_signed_in: Callable[[SignedIn], Awaitable[str]],
    http_transport: Optional[httpx.AsyncBaseTransport] = None,
) -> str:
    """Start signing in to ``url`` in the background, and return the address
    of the server's sign-in page for the owner to open, once the server
    asked for it (within ``DISCOVER_S``). ``on_signed_in`` saves the result
    and says how it went."""
    loop = asyncio.get_running_loop()
    opened: "asyncio.Future[str]" = loop.create_future()
    waiting = _Waiting(code=loop.create_future())
    states: List[str] = []

    async def redirect(address: str) -> None:
        parts = urlsplit(address)
        state = (parse_qs(parts.query).get("state") or [""])[0]
        if parts.scheme not in ("http", "https") or not state:
            raise ConnectorError("The server's sign-in page has no web address to open.")
        _waiting[state] = waiting
        states.append(state)
        if not opened.done():
            opened.set_result(address)

    async def callback() -> Tuple[str, Optional[str]]:
        try:
            return await asyncio.wait_for(waiting.code, SIGN_IN_S)
        except TimeoutError:
            raise ConnectorError("The sign-in wasn't finished in time. Start it again.") from None

    async def run() -> str:
        try:
            signed_in = await sign_in(url, redirect_uri=redirect_uri, redirect_handler=redirect, callback_handler=callback, http_transport=http_transport)
            return await on_signed_in(signed_in)
        finally:
            for state in states:
                _waiting.pop(state, None)

    task = asyncio.create_task(run())
    waiting.task = task
    _running.add(task)
    task.add_done_callback(_ended)
    await asyncio.wait({opened, task}, timeout=DISCOVER_S, return_when=asyncio.FIRST_COMPLETED)
    if opened.done():
        return opened.result()
    if task.done():
        task.result()
    task.cancel()
    raise ConnectorError(f"{urlsplit(url).hostname or url} didn't answer in time.")


async def finish(state: str, *, code: Optional[str], error: Optional[str]) -> Tuple[bool, str]:
    """The owner's browser came back to ``CALLBACK_PATH``: hand the code to
    the sign-in waiting for it, then wait for it to be saved. Returns whether
    it worked, and what the page says."""
    waiting = _waiting.get(state)
    if waiting is None or waiting.task is None:
        return False, "This sign-in has ended. Start it again from the Connectors page."
    if not waiting.code.done():
        if error:
            waiting.code.set_exception(ConnectorError(f"The sign-in was refused: {error}"))
        elif not code:
            waiting.code.set_exception(ConnectorError("The server sent no sign-in code. Start it again."))
        else:
            waiting.code.set_result((code, state))
    try:
        return True, await asyncio.wait_for(asyncio.shield(waiting.task), DISCOVER_S + 2 * TIMEOUT_S)
    except ConnectorError as exc:
        return False, str(exc)
    except TimeoutError:
        return True, "Still finishing: the connector shows on the Connectors page once it is done."
    except Exception:  # noqa: BLE001 - logged by _ended; the page says it failed
        return False, "The sign-in failed. Start it again from the Connectors page."


# ----- connections -----


def _needs_sign_in(name: str) -> Callable[..., Awaitable[Any]]:
    async def refuse(*_args: Any) -> Any:
        raise SignInNeeded(f"{name} needs you to sign in again: open it on the Connectors page and press Sign in again.")

    return refuse


def _timestamp(value: Any) -> Optional[float]:
    """A stored expiry as a Unix time (SQLite gives it back without its UTC zone)."""
    if not isinstance(value, datetime):
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()


async def connection_auth(ref: str, name: str, url: str, sign_in: SignIn) -> Optional["OAuthClientProvider"]:
    """How a saved connector's connection signs in with OAuth (None for any
    other sign-in): the provider, with what its sign-in kept restored."""
    if sign_in.kind != "oauth":
        return None
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata, OAuthToken, ProtectedResourceMetadata
    from pydantic import ValidationError

    kept = sign_in.oauth
    try:
        client = OAuthClientInformationFull.model_validate(kept.get("client") or {})
        server = OAuthMetadata.model_validate(kept["authorization_server"]) if kept.get("authorization_server") else None
        resource = ProtectedResourceMetadata.model_validate(kept["resource"]) if kept.get("resource") else None
    except ValidationError:
        raise SignInNeeded(f"{name} needs you to sign in again: open it on the Connectors page and press Sign in again.") from None
    stored = await _auth().get_stored_oauth_tokens(ref)
    tokens = (
        OAuthToken(access_token=stored["access_token"], refresh_token=stored.get("refresh_token") or None, scope=stored.get("scopes") or None)
        if stored and stored.get("access_token")
        else None
    )
    refuse = _needs_sign_in(name)
    provider = OAuthClientProvider(
        server_url=url,
        client_metadata=OAuthClientMetadata(redirect_uris=client.redirect_uris, scope=kept.get("scope")),
        storage=_Saved(ref, client, tokens),
        redirect_handler=refuse,
        callback_handler=refuse,
    )
    context = provider.context
    context.token_expiry_time = _timestamp(stored.get("token_expiry")) if stored else None
    context.oauth_metadata = server
    context.protected_resource_metadata = resource
    context.auth_server_url = kept.get("auth_server_url")
    return provider


__all__ = [
    "CALLBACK_PATH",
    "SIGN_IN_S",
    "SignInNeeded",
    "SignedIn",
    "connection_auth",
    "finish",
    "save_tokens",
    "sign_in",
    "start_sign_in",
]
