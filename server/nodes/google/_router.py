"""
Google Workspace OAuth 2.0 callback and API routes.

OAuth flow:
1. Frontend calls WebSocket 'google_oauth_login' handler
2. Backend generates authorization URL, opens browser
3. User authorizes on Google
4. Google redirects to /api/google/callback with code
5. Backend exchanges code for tokens, stores them via auth_service
6. Frontend polls WebSocket 'google_oauth_status' for completion

Two access modes:
- Owner Mode: Tokens stored via auth_service (single account)
- Customer Mode: Tokens stored in google_connections table (multi-account)

Supports all Google Workspace services:
- Gmail, Calendar, Drive, Sheets, Tasks, Contacts
"""

from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.logging import get_logger
from nodes.google._oauth import GoogleOAuth, get_pending_state

logger = get_logger(__name__)
router = APIRouter(prefix="/api/google", tags=["google"])


def get_auth_service():
    """Get auth service for API key storage. Imports lazily to avoid the
    nodes/<plugin>/__init__ -> core.container -> nodes.android._dispatcher
    -> nodes.android.__init__ -> _router import cycle."""
    from core.container import container
    return container.auth_service()


def get_database():
    """Get database for customer connections (non-sensitive data only)."""
    from core.container import container
    return container.database()




@router.get("/callback")
async def google_oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    """
    Handle Google OAuth callback.

    Google redirects here after user authorizes (or denies) the app.
    """
    # Handle authorization denied
    if error:
        logger.warning(f"Google OAuth denied: {error} - {error_description}")
        return HTMLResponse(
            content=_callback_html(success=False, error=error_description or error),
            status_code=200,
        )

    # Validate required parameters
    if not code or not state:
        logger.error("Google OAuth callback missing code or state")
        return HTMLResponse(
            content=_callback_html(success=False, error="Missing authorization code or state"),
            status_code=400,
        )

    # Verify state exists (CSRF protection)
    pending_state = get_pending_state(state)
    if not pending_state:
        logger.error("Google OAuth callback with invalid/expired state")
        return HTMLResponse(
            content=_callback_html(success=False, error="Invalid or expired state. Please try again."),
            status_code=400,
        )

    # Get state data
    state_data = pending_state.get("data", {})
    mode = state_data.get("mode", "owner")
    customer_id = state_data.get("customer_id")
    redirect_after = state_data.get("redirect_after")

    # Retrieve redirect_uri from state (set during auth initiation)
    redirect_uri = pending_state.get("redirect_uri")
    if not redirect_uri:
        logger.error("Google OAuth callback missing redirect_uri in state")
        return HTMLResponse(
            content=_callback_html(success=False, error="Invalid state: missing redirect URI. Please try again."),
            status_code=400,
        )

    # Get credentials
    auth_service = get_auth_service()
    client_id = await auth_service.get_api_key("google_client_id") or ""
    client_secret = await auth_service.get_api_key("google_client_secret") or ""

    if not client_id or not client_secret:
        return HTMLResponse(
            content=_callback_html(success=False, error="Google not configured. Add Client ID and Secret in Credentials."),
            status_code=400,
        )

    oauth = GoogleOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )

    # Exchange code for tokens
    result = oauth.exchange_code(code=code, state=state)

    if not result.get("success"):
        logger.error(f"Google token exchange failed: {result.get('error')}")
        return HTMLResponse(
            content=_callback_html(success=False, error=result.get("error", "Token exchange failed")),
            status_code=400,
        )

    email = result.get("email", "Unknown")
    name = result.get("name", "")
    access_token = result.get("access_token")
    refresh_token = result.get("refresh_token")

    # Store tokens via auth_service (single point of access for credentials)
    auth_service = get_auth_service()

    if mode == "customer" and customer_id:
        # Customer mode: store encrypted tokens via auth_service
        await auth_service.store_oauth_tokens(
            provider="google",
            access_token=access_token,
            refresh_token=refresh_token,
            email=email,
            name=name,
            scopes=",".join(result.get("scopes", [])),
            customer_id=customer_id,
        )
        logger.info(f"Google OAuth successful for customer {customer_id}: {email}")

        if redirect_after:
            return RedirectResponse(url=f"{redirect_after}?google_connected=true&customer={customer_id}&email={email}")
    else:
        # Owner mode: store encrypted tokens via auth_service
        await auth_service.store_oauth_tokens(
            provider="google",
            access_token=access_token,
            refresh_token=refresh_token,
            email=email,
            name=name,
            scopes=",".join(result.get("scopes", [])),
            customer_id="owner",
        )
        logger.info(f"Google OAuth successful for {email}")

    # Update persistent status and broadcast completion event
    from services.status_broadcaster import get_status_broadcaster
    broadcaster = get_status_broadcaster()
    broadcaster._status["google"] = {
        "connected": True,
        "email": email,
        "name": name,
    }
    await broadcaster.broadcast({
        "type": "google_oauth_complete",
        "data": {"success": True, "email": email, "name": name, "mode": mode, "customer_id": customer_id},
    })

    return HTMLResponse(content=_callback_html(success=True, email=email), status_code=200)


@router.get("/status")
async def get_google_status():
    """Get Google connection status for owner mode."""
    auth_service = get_auth_service()
    tokens = await auth_service.get_oauth_tokens("google", customer_id="owner")

    if not tokens:
        return {"connected": False, "email": None}

    return {
        "connected": True,
        "email": tokens.get("email"),
        "name": tokens.get("name"),
    }


@router.post("/logout")
async def google_logout():
    """Disconnect Google (owner mode)."""
    auth_service = get_auth_service()
    await auth_service.remove_oauth_tokens("google", customer_id="owner")
    logger.info("Google disconnected")
    return {"success": True, "message": "Google disconnected"}


@router.post("/customer-auth-url")
async def generate_customer_auth_url(request: Request, customer_id: str, redirect_after: Optional[str] = None):
    """Generate OAuth URL for a customer to connect their Google account."""
    from services.oauth_utils import get_redirect_uri

    auth_service = get_auth_service()
    client_id = await auth_service.get_api_key("google_client_id") or ""
    client_secret = await auth_service.get_api_key("google_client_secret") or ""

    if not client_id or not client_secret:
        return {"success": False, "error": "Google not configured. Add Client ID and Secret."}

    redirect_uri = get_redirect_uri(request, "google")

    oauth = GoogleOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )
    result = oauth.generate_authorization_url(state_data={"customer_id": customer_id, "redirect_after": redirect_after, "mode": "customer"})
    return {"success": True, "url": result["url"], "state": result["state"]}


@router.get("/customer/{customer_id}/status")
async def get_customer_google_status(customer_id: str):
    """Get Google connection status for a customer."""
    auth_service = get_auth_service()
    tokens = await auth_service.get_oauth_tokens("google", customer_id=customer_id)
    if not tokens:
        return {"connected": False, "customer_id": customer_id}
    return {
        "connected": True,
        "customer_id": customer_id,
        "email": tokens.get("email"),
        "name": tokens.get("name"),
    }


@router.post("/customer/{customer_id}/disconnect")
async def disconnect_customer_google(customer_id: str):
    """Disconnect a customer's Google account."""
    auth_service = get_auth_service()
    await auth_service.remove_oauth_tokens("google", customer_id=customer_id)
    logger.info(f"Google disconnected for customer {customer_id}")
    return {"success": True, "customer_id": customer_id}


def _callback_html(success: bool, email: str = None, error: str = None) -> str:
    """Generate callback HTML page."""
    if success:
        title, message, color = "Google Connected", f"Successfully connected as {email}!", "#34a853"
    else:
        title, message, color = "Connection Failed", error or "Failed to connect", "#ea4335"

    escaped_error = error.replace("'", "\\'") if error else ""
    return f"""<!DOCTYPE html>
<html>
<head><title>{title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:linear-gradient(135deg,#15202b,#1a1a2e);min-height:100vh;display:flex;align-items:center;justify-content:center;color:#fff}}
.container{{text-align:center;padding:40px;background:rgba(255,255,255,0.05);border-radius:16px;border:1px solid rgba(255,255,255,0.1);max-width:400px}}
.icon{{width:64px;height:64px;margin-bottom:20px;color:{color}}}
h1{{font-size:24px;margin-bottom:12px;color:{color}}}
p{{font-size:16px;color:rgba(255,255,255,0.8);margin-bottom:20px}}
.close-text{{font-size:14px;color:rgba(255,255,255,0.5)}}
</style></head>
<body><div class="container">
<svg class="icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
{"<path stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z'/>" if success else "<path stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z'/>"}
</svg>
<h1>{title}</h1><p>{message}</p><p class="close-text">This window will close automatically...</p>
</div>
<script>
if(window.opener){{window.opener.postMessage({{type:'google_oauth_callback',success:{str(success).lower()},{"email:'"+email+"'," if email else ""}{"error:'"+escaped_error+"'," if error else ""}}},'*')}}
setTimeout(function(){{window.close()}},2000);
</script></body></html>"""
