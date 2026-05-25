"""OAuth lifecycle factory helpers (Wave 11.I, milestone S).

Three factories that collapse the duplicated WS-handler / HTTP-router
plumbing across :mod:`nodes.twitter` and :mod:`nodes.google`. Each
plugin's ``_handlers.py`` and ``_router.py`` shrink to a single
factory call.

Companion to :mod:`services.events.lifecycle` (which factories the
EventSource pattern Stripe uses). OAuth's lifecycle differs:

* **EventSource** (Stripe): ``connect`` / ``disconnect`` / ``reconnect``
  / ``status`` -- daemon-shaped.
* **OAuth** (Twitter / Google): ``oauth_login`` / ``oauth_status`` /
  ``logout`` -- browser-grant-shaped, plus an HTTP ``/callback`` route
  the OAuth provider redirects the user back to.

The factories are duck-typed against an ``oauth_factory()`` callable
that returns either an :class:`OAuth2PKCEClient` subclass (Twitter,
hand-rolled PKCE) or a composition wrapper around an upstream library
(Google's ``google_auth_oauthlib.flow.Flow``). The methods consumed
are:

* ``generate_authorization_url(*, state_data=None) -> {url, state, ...}``
* ``async exchange_code(code, state) -> {success, access_token,
  refresh_token, expires_in, scope, ...}``
* ``async fetch_user_info(access_token) -> {success, id, ...}``
* ``async refresh_access_token(refresh_token) -> {success,
  access_token, refresh_token, ...}``
* ``async revoke_token(token, token_type) -> {success, ...}`` -- optional

Persistence: in-memory state store. Same caveat as
:mod:`services.plugin.oauth` -- popups opened across a server restart
fail the callback CSRF check.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, Protocol

from fastapi import APIRouter, Query, Request, WebSocket
from fastapi.responses import HTMLResponse

from core.logging import get_logger

logger = get_logger(__name__)


# ============================================================================
# OAuthLike protocol -- duck-typed factory return value
# ============================================================================


class OAuthLike(Protocol):
    """The minimal surface :func:`make_oauth_lifecycle_handlers` /
    :func:`make_oauth_callback_router` need.

    Both ``OAuth2PKCEClient`` (subclass mode) and Google's composition
    wrapper conform.
    """

    def generate_authorization_url(
        self,
        *,
        state_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]: ...

    async def exchange_code(self, code: str, state: str) -> Dict[str, Any]: ...

    async def fetch_user_info(self, access_token: str) -> Dict[str, Any]: ...

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]: ...

    async def revoke_token(
        self,
        token: str,
        token_type: str = "access_token",
    ) -> Dict[str, Any]: ...


OAuthFactory = Callable[..., Awaitable[OAuthLike]]
"""Async callable that builds an OAuth client. Accepts ``redirect_uri``
keyword (passed by the lifecycle helpers). Implementations typically
await ``auth_service.get_api_key`` for client_id / client_secret then
construct the OAuth client. Plugins that don't need request context
can ignore the kwarg."""


# ============================================================================
# make_oauth_lifecycle_handlers
# ============================================================================


def make_oauth_lifecycle_handlers(
    *,
    provider: str,
    oauth_factory: OAuthFactory,
    user_info_to_subject: Callable[[Dict[str, Any]], str],
    legacy_status_broadcast: Optional[str] = None,
    extra_logout: Optional[Callable[[], Awaitable[None]]] = None,
) -> Dict[str, Callable]:
    """Build the 3 WS handlers for an OAuth provider.

    Returns ``{
        f"{provider}_oauth_login": ...,
        f"{provider}_oauth_status": ...,
        f"{provider}_logout": ...,
    }`` ready to feed :func:`services.ws_handler_registry.register_ws_handlers`.

    Parameters:
        provider:
            Lowercase plugin id (``"twitter"``, ``"google"``). Used as
            credential provider, broadcast subject, and message-type
            prefix.
        oauth_factory:
            Builds an :class:`OAuthLike` instance per call. Receives
            ``redirect_uri`` keyword. Implementations typically read
            stored client_id / client_secret from ``auth_service`` and
            compose the OAuth client.
        user_info_to_subject:
            Maps the unified user-info dict (returned by
            ``fetch_user_info``) to the broadcast ``subject``: Twitter
            uses ``f"@{username}"``, Google uses the email.
        legacy_status_broadcast:
            Optional wire-format type for a legacy status broadcast
            that pre-dated the unified ``credential_catalogue_updated``
            envelope. Google ships ``"google_status"`` for back-compat
            with frontend code that grew up reading that frame.
        extra_logout:
            Optional async callback fired AFTER the standard logout
            (revoke + remove tokens + broadcast). Twitter uses this to
            drop legacy API-key entries from the pre-OAuth layout.
    """

    async def login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
        from core.container import container
        from services.oauth_utils import get_redirect_uri

        auth_service = container.auth_service()
        client_id = await auth_service.get_api_key(f"{provider}_client_id")
        if not client_id:
            return {
                "success": False,
                "error": (f"{provider.capitalize()} Client ID not configured. " f"Add your {provider.capitalize()} API credentials first."),
            }

        redirect_uri = get_redirect_uri(websocket, provider)
        oauth = await oauth_factory(redirect_uri=redirect_uri)
        auth_data = oauth.generate_authorization_url()

        return {
            "success": True,
            "message": f"Opening {provider.capitalize()} authorization in browser...",
            "url": auth_data["url"],
            "state": auth_data["state"],
        }

    async def status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
        from core.container import container
        from services.oauth_utils import get_redirect_uri

        auth_service = container.auth_service()
        tokens = await auth_service.get_oauth_tokens(provider, customer_id="owner")
        if not tokens or not tokens.get("access_token"):
            # Match pre-S handler shape -- frontends key on these
            # exact field names. Both Twitter (username/user_id) and
            # Google (email/name) field sets included so a single
            # disconnected payload satisfies every provider.
            payload = _disconnected_payload()
            await _maybe_legacy_broadcast(legacy_status_broadcast, payload)
            return payload

        access_token = tokens["access_token"]
        redirect_uri = get_redirect_uri(websocket, provider)
        oauth = await oauth_factory(redirect_uri=redirect_uri)
        user_info = await oauth.fetch_user_info(access_token)

        # Silent refresh on token failure (RFC 9700: refresh_token not
        # cached in memory; pull from DB on demand).
        if not user_info.get("success"):
            refresh_token = await auth_service.get_oauth_refresh_token(
                provider,
                customer_id="owner",
            )
            if refresh_token:
                refresh = await oauth.refresh_access_token(refresh_token)
                if refresh.get("success"):
                    await auth_service.store_oauth_tokens(
                        provider=provider,
                        access_token=refresh["access_token"],
                        refresh_token=refresh.get("refresh_token") or refresh_token,
                        email=tokens.get("email", ""),
                        name=tokens.get("name", ""),
                        scopes=tokens.get("scopes", ""),
                        customer_id="owner",
                    )
                    user_info = await oauth.fetch_user_info(refresh["access_token"])

        if not user_info.get("success"):
            payload = {
                **_disconnected_payload(),
                "error": user_info.get("error"),
            }
            await _maybe_legacy_broadcast(legacy_status_broadcast, payload)
            return payload

        payload = {
            "connected": True,
            **{k: v for k, v in user_info.items() if k != "success"},
        }
        await _maybe_legacy_broadcast(legacy_status_broadcast, payload)
        return payload

    async def logout(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
        from core.container import container
        from services.oauth_utils import get_redirect_uri
        from services.status_broadcaster import get_status_broadcaster

        auth_service = container.auth_service()
        tokens = await auth_service.get_oauth_tokens(provider, customer_id="owner")
        access_token = tokens.get("access_token") if tokens else None
        refresh_token = await auth_service.get_oauth_refresh_token(provider, customer_id="owner") if tokens else None

        if access_token or refresh_token:
            redirect_uri = get_redirect_uri(websocket, provider)
            oauth = await oauth_factory(redirect_uri=redirect_uri)
            if access_token:
                await oauth.revoke_token(access_token, "access_token")
            if refresh_token:
                await oauth.revoke_token(refresh_token, "refresh_token")

        await auth_service.remove_oauth_tokens(provider, customer_id="owner")

        if extra_logout is not None:
            try:
                await extra_logout()
            except Exception as exc:  # noqa: BLE001 -- best-effort cleanup
                logger.warning(f"[{provider}] extra_logout failed: {exc}")

        broadcaster = get_status_broadcaster()
        await broadcaster.broadcast_credential_event(
            "credential.oauth.disconnected",
            provider=provider,
            customer_id="owner",
        )

        return {"success": True, "message": f"{provider.capitalize()} disconnected"}

    return {
        f"{provider}_oauth_login": login,
        f"{provider}_oauth_status": status,
        f"{provider}_logout": logout,
    }


async def _maybe_legacy_broadcast(
    broadcast_type: Optional[str],
    payload: Dict[str, Any],
) -> None:
    """Optional legacy status broadcast (Google's ``google_status`` frame)."""
    if not broadcast_type:
        return
    from services.status_broadcaster import get_status_broadcaster

    await get_status_broadcaster().broadcast({"type": broadcast_type, "data": payload})


def _disconnected_payload() -> Dict[str, Any]:
    """Disconnected status payload with the union of Twitter / Google
    field names. Frontends read what they need; unknown None values
    are ignored. Easier than parameterising the field list per plugin."""
    return {
        "connected": False,
        "username": None,
        "user_id": None,
        "email": None,
        "name": None,
    }


# ============================================================================
# make_oauth_callback_router
# ============================================================================


def make_oauth_callback_router(
    *,
    provider: str,
    oauth_factory: OAuthFactory,
    user_info_to_email: Callable[[Dict[str, Any]], str],
    user_info_to_name: Callable[[Dict[str, Any]], str] = lambda info: info.get("name", "") or "",
    extra_state_handler: Optional[Callable[[Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]] = None,
    color_hex: str = "#1DA1F2",
) -> APIRouter:
    """Build the FastAPI router with the OAuth callback endpoint.

    Mounts ``GET /api/{provider}/callback`` -- the URI the OAuth
    provider redirects to after the user authorises (or denies).

    Parameters:
        provider:
            Lowercase plugin id; used as path prefix and broadcast type.
        oauth_factory:
            Builds an :class:`OAuthLike`. Same contract as for the
            lifecycle handlers.
        user_info_to_email:
            Maps the unified user-info dict to the email/identifier
            stored alongside the OAuth tokens. Twitter uses ``"@{username}"``;
            Google uses ``info["email"]``.
        user_info_to_name:
            Display name extractor. Defaults to ``info.get("name", "")``.
        extra_state_handler:
            Optional async hook called with the decoded ``state_data``
            from the state store BEFORE token storage. Returns an
            override dict to mutate token storage args
            (``customer_id``, ``redirect_after``, ...). Google uses
            this to route customer-mode logins to the
            ``google_connections`` table.
        color_hex:
            Brand colour for the success-page UI. Twitter ``#00ba7c``;
            Google ``#4285F4``.
    """

    router = APIRouter(prefix=f"/api/{provider}", tags=[provider])

    @router.get("/callback")
    async def oauth_callback(
        request: Request,
        code: Optional[str] = Query(None),
        state: Optional[str] = Query(None),
        error: Optional[str] = Query(None),
        error_description: Optional[str] = Query(None),
    ):
        if error:
            logger.warning(f"{provider} OAuth denied: {error} - {error_description}")
            return HTMLResponse(
                content=render_oauth_callback_html(
                    provider,
                    status="error",
                    message=error_description or error,
                    color_hex=color_hex,
                ),
                status_code=200,
            )

        if not code or not state:
            return HTMLResponse(
                content=render_oauth_callback_html(
                    provider,
                    status="error",
                    message="Missing authorization code or state parameter",
                    color_hex=color_hex,
                ),
                status_code=400,
            )

        oauth = await oauth_factory()  # any redirect_uri override comes from state
        # peek at state to read redirect_uri / state_data BEFORE the
        # exchange (which pops it).
        state_record = oauth.state_store.peek(state) if hasattr(oauth, "state_store") else None
        redirect_uri = state_record.get("redirect_uri") if state_record else None
        state_data = state_record.get("data") if state_record else {}

        if redirect_uri:
            # rebuild factory with the right redirect_uri so exchange_code
            # POSTs the same URI the auth request used.
            oauth = await oauth_factory(redirect_uri=redirect_uri)

        result = await oauth.exchange_code(code, state)
        if not result.get("success"):
            return HTMLResponse(
                content=render_oauth_callback_html(
                    provider,
                    status="error",
                    message=result.get("error", "Token exchange failed"),
                    color_hex=color_hex,
                ),
                status_code=400,
            )

        access_token = result.get("access_token")
        refresh_token = result.get("refresh_token") or ""

        user_info = await oauth.fetch_user_info(access_token)
        email = user_info_to_email(user_info) if user_info.get("success") else "Unknown"
        name = user_info_to_name(user_info) if user_info.get("success") else ""

        # Customer-mode hook (Google). Returns overrides for store_oauth_tokens
        # plus an optional ``redirect_after`` URL.
        store_overrides: Dict[str, Any] = {}
        redirect_after: Optional[str] = None
        if extra_state_handler is not None:
            override = await extra_state_handler(
                {
                    "state_data": state_data,
                    "user_info": user_info,
                    "tokens": result,
                }
            )
            if override:
                redirect_after = override.pop("redirect_after", None)
                store_overrides.update(override)

        from core.container import container

        auth_service = container.auth_service()
        await auth_service.store_oauth_tokens(
            provider=provider,
            access_token=access_token,
            refresh_token=refresh_token,
            email=email,
            name=name,
            scopes=",".join((result.get("scope") or "").split()),
            customer_id=store_overrides.pop("customer_id", "owner"),
            **store_overrides,
        )

        # Broadcast completion (legacy provider-specific event for the
        # popup-listening frontend) plus the symmetric catalogue-event.
        from services.status_broadcaster import get_status_broadcaster

        broadcaster = get_status_broadcaster()
        await broadcaster.broadcast(
            {
                "type": f"{provider}_oauth_complete",
                "data": {
                    "success": True,
                    **{k: v for k, v in user_info.items() if k != "success"},
                },
            }
        )
        await broadcaster.broadcast_credential_event(
            "credential.oauth.connected",
            provider=provider,
            customer_id=store_overrides.get("customer_id", "owner"),
        )

        if redirect_after:
            from fastapi.responses import RedirectResponse

            return RedirectResponse(url=redirect_after, status_code=302)

        return HTMLResponse(
            content=render_oauth_callback_html(
                provider,
                status="success",
                message=f"Successfully connected as {email}!",
                color_hex=color_hex,
            ),
            status_code=200,
        )

    return router


# ============================================================================
# render_oauth_callback_html -- pure renderer
# ============================================================================


_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #15202b 0%, #1a1a2e 100%);
            min-height: 100vh; display: flex; align-items: center;
            justify-content: center; color: #fff;
        }}
        .container {{
            text-align: center; padding: 40px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 16px; max-width: 400px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }}
        .icon {{ width: 64px; height: 64px; margin-bottom: 20px; color: {color_hex}; }}
        h1 {{ font-size: 24px; margin-bottom: 12px; color: {color_hex}; }}
        p {{ font-size: 16px; color: rgba(255, 255, 255, 0.8); margin-bottom: 20px; }}
        .close-text {{ font-size: 14px; color: rgba(255, 255, 255, 0.5); }}
    </style>
</head>
<body>
    <div class="container">
        <svg class="icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            {icon_path}
        </svg>
        <h1>{title}</h1>
        <p>{message}</p>
        <p class="close-text">This window will close automatically...</p>
    </div>
    <script>
        if (window.opener) {{
            window.opener.postMessage({{
                type: '{provider}_oauth_callback',
                success: {success_js},
                message: '{message_js}',
            }}, '*');
        }}
        setTimeout(function() {{ window.close(); }}, 2000);
    </script>
</body>
</html>"""

_SUCCESS_ICON = (
    "<path stroke-linecap='round' stroke-linejoin='round' stroke-width='2' " "d='M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z'/>"
)
_ERROR_ICON = (
    "<path stroke-linecap='round' stroke-linejoin='round' stroke-width='2' "
    "d='M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z'/>"
)


def render_oauth_callback_html(
    provider: str,
    *,
    status: str,
    message: str,
    color_hex: str,
) -> str:
    """Render the small auto-closing callback page.

    ``status`` MUST be ``"success"`` or ``"error"``. The page posts a
    ``{provider}_oauth_callback`` message to ``window.opener`` (the
    popup's parent) and auto-closes after 2 seconds.
    """
    is_success = status == "success"
    title = f"{provider.capitalize()} Connected" if is_success else "Connection Failed"
    return _HTML_TEMPLATE.format(
        title=title,
        message=message,
        color_hex=color_hex,
        icon_path=_SUCCESS_ICON if is_success else _ERROR_ICON,
        provider=provider,
        success_js=str(is_success).lower(),
        message_js=message.replace("'", "\\'"),
    )


__all__ = [
    "OAuthLike",
    "OAuthFactory",
    "make_oauth_lifecycle_handlers",
    "make_oauth_callback_router",
    "render_oauth_callback_html",
]
