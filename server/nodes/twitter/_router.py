"""
Twitter/X OAuth 2.0 callback and API routes.

OAuth flow:
1. Frontend calls WebSocket 'twitter_oauth_login' handler
2. Backend generates authorization URL, opens browser
3. User authorizes on Twitter
4. Twitter redirects to /api/twitter/callback with code
5. Backend exchanges code for tokens, stores them via auth_service
6. Frontend polls WebSocket 'twitter_oauth_status' for completion

Tokens stored as API keys with provider prefixes:
- twitter_access_token: OAuth access token (expires in 2 hours)
- twitter_refresh_token: OAuth refresh token (for token renewal)
"""

from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from core.logging import get_logger
from nodes.twitter._oauth import TwitterOAuth, get_pending_state

logger = get_logger(__name__)
router = APIRouter(prefix="/api/twitter", tags=["twitter"])


def get_auth_service():
    """Get auth service for API key storage. Imports lazily to avoid the
    nodes/<plugin>/__init__ -> core.container -> nodes.android._dispatcher
    -> nodes.android.__init__ -> _router import cycle."""
    from core.container import container
    return container.auth_service()


@router.get("/callback")
async def twitter_oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    """
    Handle Twitter OAuth callback.

    Twitter redirects here after user authorizes (or denies) the app.
    We exchange the code for tokens and store them via auth_service.
    """
    # Handle authorization denied
    if error:
        logger.warning(f"Twitter OAuth denied: {error} - {error_description}")
        return HTMLResponse(
            content=_callback_html(
                success=False,
                error=error_description or error,
            ),
            status_code=200,
        )

    # Validate required parameters
    if not code or not state:
        logger.error("Twitter OAuth callback missing code or state")
        return HTMLResponse(
            content=_callback_html(
                success=False,
                error="Missing authorization code or state parameter",
            ),
            status_code=400,
        )

    # Verify state exists (CSRF protection)
    pending_state = get_pending_state(state)
    if not pending_state:
        logger.error("Twitter OAuth callback with invalid/expired state")
        return HTMLResponse(
            content=_callback_html(
                success=False,
                error="Invalid or expired authorization state. Please try again.",
            ),
            status_code=400,
        )

    # Retrieve redirect_uri from state (set during auth initiation)
    redirect_uri = pending_state.get("redirect_uri")
    if not redirect_uri:
        logger.error("Twitter OAuth callback missing redirect_uri in state")
        return HTMLResponse(
            content=_callback_html(
                success=False,
                error="Invalid state: missing redirect URI. Please try again.",
            ),
            status_code=400,
        )

    # Get stored client credentials to create OAuth instance
    auth_service = get_auth_service()
    client_id = await auth_service.get_api_key("twitter_client_id") or ""
    client_secret = await auth_service.get_api_key("twitter_client_secret")

    oauth = TwitterOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )

    # Exchange code for tokens
    result = await oauth.exchange_code(code=code, state=state)

    if not result.get("success"):
        logger.error(f"Twitter token exchange failed: {result.get('error')}")
        return HTMLResponse(
            content=_callback_html(
                success=False,
                error=result.get("error", "Token exchange failed"),
            ),
            status_code=400,
        )

    # Get user info to display and store
    access_token = result.get("access_token")
    refresh_token = result.get("refresh_token")
    user_info = await oauth.get_user_info(access_token)

    if not user_info.get("success"):
        logger.warning(f"Failed to get Twitter user info: {user_info.get('error')}")
        username = "Unknown"
    else:
        username = user_info.get("username", "Unknown")

    # Store encrypted OAuth tokens via auth_service
    await auth_service.store_oauth_tokens(
        provider="twitter",
        access_token=access_token,
        refresh_token=refresh_token or "",
        email=f"@{username}",  # Use @username as identifier (Twitter doesn't provide email)
        name=user_info.get("name", "") if user_info.get("success") else "",
        scopes=",".join(result.get("scope", "").split()) if result.get("scope") else "",
        customer_id="owner",
    )

    # Broadcast completion event to frontend
    from services.status_broadcaster import get_status_broadcaster
    broadcaster = get_status_broadcaster()

    await broadcaster.broadcast({
        "type": "twitter_oauth_complete",
        "data": {
            "success": True,
            "username": username,
            "user_id": user_info.get("id"),
            "name": user_info.get("name"),
            "profile_image_url": user_info.get("profile_image_url"),
        }
    })

    logger.info(f"Twitter OAuth successful for @{username}")

    return HTMLResponse(
        content=_callback_html(
            success=True,
            username=username,
        ),
        status_code=200,
    )


@router.get("/status")
async def get_twitter_status(request: Request):
    """
    Get Twitter connection status.

    Returns whether the user is authenticated with Twitter.
    """
    from services.oauth_utils import get_redirect_uri

    auth_service = get_auth_service()

    # Try to get stored tokens via auth_service
    tokens = await auth_service.get_oauth_tokens("twitter", customer_id="owner")

    if not tokens:
        return {
            "connected": False,
            "username": None,
            "user_id": None,
        }

    access_token = tokens.get("access_token")

    # Get stored client credentials (these remain in auth_service as they're app-level)
    client_id = await auth_service.get_api_key("twitter_client_id") or ""
    client_secret = await auth_service.get_api_key("twitter_client_secret")
    redirect_uri = get_redirect_uri(request, "twitter")

    oauth = TwitterOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )

    # Verify token is still valid by getting user info
    user_info = await oauth.get_user_info(access_token)

    if not user_info.get("success"):
        # Token may be expired, try to refresh — refresh_token is read
        # from the DB on demand (RFC 9700; not cached in memory).
        refresh_token = await auth_service.get_oauth_refresh_token(
            "twitter", customer_id="owner"
        )
        if refresh_token:
            refresh_result = await oauth.refresh_access_token(refresh_token)
            if refresh_result.get("success"):
                # Store new tokens via auth_service
                await auth_service.store_oauth_tokens(
                    provider="twitter",
                    access_token=refresh_result["access_token"],
                    refresh_token=refresh_result.get("refresh_token") or refresh_token,
                    email=tokens.get("email"),
                    name=tokens.get("name"),
                    scopes=tokens.get("scopes"),
                    customer_id="owner",
                )

                # Retry user info
                user_info = await oauth.get_user_info(refresh_result["access_token"])

    if not user_info.get("success"):
        return {
            "connected": False,
            "username": None,
            "user_id": None,
            "error": user_info.get("error"),
        }

    return {
        "connected": True,
        "username": user_info.get("username"),
        "user_id": user_info.get("id"),
        "name": user_info.get("name"),
        "profile_image_url": user_info.get("profile_image_url"),
        "verified": user_info.get("verified"),
    }


@router.post("/logout")
async def twitter_logout(request: Request):
    """
    Disconnect Twitter by revoking tokens and clearing stored credentials.
    """
    from services.oauth_utils import get_redirect_uri

    auth_service = get_auth_service()

    # Get stored tokens via auth_service
    tokens = await auth_service.get_oauth_tokens("twitter", customer_id="owner")

    if tokens:
        access_token = tokens.get("access_token")
        # refresh_token is not cached in memory (RFC 9700) — read from DB.
        refresh_token = await auth_service.get_oauth_refresh_token(
            "twitter", customer_id="owner"
        )

        # Get client credentials for revocation (app-level, in auth_service)
        client_id = await auth_service.get_api_key("twitter_client_id") or ""
        client_secret = await auth_service.get_api_key("twitter_client_secret")
        redirect_uri = get_redirect_uri(request, "twitter")

        oauth = TwitterOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )

        # Revoke tokens
        if access_token:
            await oauth.revoke_token(access_token, "access_token")

        if refresh_token:
            await oauth.revoke_token(refresh_token, "refresh_token")

    # Clear stored credentials via auth_service
    await auth_service.remove_oauth_tokens("twitter", customer_id="owner")

    logger.info("Twitter disconnected and tokens revoked")

    return {"success": True, "message": "Twitter disconnected"}


def _callback_html(success: bool, username: str = None, error: str = None) -> str:
    """Generate callback HTML page that closes itself and notifies parent."""
    if success:
        title = "Twitter Connected"
        message = f"Successfully connected as @{username}!"
        color = "#00ba7c"  # Green
    else:
        title = "Connection Failed"
        message = error or "Failed to connect to Twitter"
        color = "#f4212e"  # Red

    escaped_error = error.replace("'", "\\'") if error else ""
    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #15202b 0%, #1a1a2e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #fff;
        }}
        .container {{
            text-align: center;
            padding: 40px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 16px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            max-width: 400px;
        }}
        .icon {{
            width: 64px;
            height: 64px;
            margin-bottom: 20px;
            color: {color};
        }}
        h1 {{
            font-size: 24px;
            margin-bottom: 12px;
            color: {color};
        }}
        p {{
            font-size: 16px;
            color: rgba(255, 255, 255, 0.8);
            margin-bottom: 20px;
        }}
        .close-text {{
            font-size: 14px;
            color: rgba(255, 255, 255, 0.5);
        }}
    </style>
</head>
<body>
    <div class="container">
        <svg class="icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            {"<path stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z'/>" if success else "<path stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z'/>"}
        </svg>
        <h1>{title}</h1>
        <p>{message}</p>
        <p class="close-text">This window will close automatically...</p>
    </div>
    <script>
        // Notify parent window and close
        if (window.opener) {{
            window.opener.postMessage({{
                type: 'twitter_oauth_callback',
                success: {str(success).lower()},
                {"username: '" + username + "'," if username else ""}
                {"error: '" + escaped_error + "'," if error else ""}
            }}, '*');
        }}
        // Close after 2 seconds
        setTimeout(function() {{ window.close(); }}, 2000);
    </script>
</body>
</html>
"""
