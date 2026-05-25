"""Shared Google OAuth helper for all Google Workspace handlers.

Provides centralized token retrieval, credential building, and proactive
token refresh with persistence. All Google handler files (gmail, calendar,
drive, sheets, tasks, contacts) should use get_google_credentials() instead
of duplicating auth logic.
"""

import asyncio
from typing import Any, Dict

from google.oauth2.credentials import Credentials

from core.logging import get_logger

logger = get_logger(__name__)


async def get_google_credentials(
    parameters: Dict[str, Any],
    context: Dict[str, Any],
) -> Credentials:
    """Get authenticated Google OAuth credentials.

    Supports two modes:
    - Owner mode: Uses tokens from auth_service OAuth store (Credentials Modal)
    - Customer mode: Uses tokens from google_connections table

    After building credentials, proactively attempts a token refresh if a
    refresh_token is available and persists the new access_token back to the
    database so subsequent calls don't need to re-refresh.

    Args:
        parameters: Node parameters (may include account_mode, customer_id)
        context: Execution context

    Returns:
        google.oauth2.credentials.Credentials ready for API use
    """
    from services.plugin.deps import get_auth_service, get_database

    account_mode = parameters.get("account_mode", "owner")
    customer_id = "owner"

    if account_mode == "customer":
        customer_id = parameters.get("customer_id")
        if not customer_id:
            raise ValueError("customer_id required for customer mode")

        db = get_database()
        connection = await db.get_google_connection(customer_id)
        if not connection:
            raise ValueError(f"No Google connection for customer: {customer_id}")

        if not connection.is_active:
            raise ValueError(f"Google connection inactive for customer: {customer_id}")

        access_token = connection.access_token
        refresh_token = connection.refresh_token

        await db.update_google_last_used(customer_id)

    else:
        auth_service = get_auth_service()
        tokens = await auth_service.get_oauth_tokens("google", customer_id="owner")

        if not tokens or not tokens.get("access_token"):
            raise ValueError("Google Workspace not connected. Please authenticate via Credentials.")

        access_token = tokens["access_token"]
        # refresh_token is read from DB directly (RFC 9700; not cached).
        refresh_token = await auth_service.get_oauth_refresh_token("google", customer_id="owner")

    auth_service = get_auth_service()
    client_id = await auth_service.get_api_key("google_client_id") or ""
    client_secret = await auth_service.get_api_key("google_client_secret") or ""

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
    )

    # Proactively refresh if the token is expired and we have a refresh_token.
    # The google-auth library marks tokens expired when expiry is set and passed,
    # but since we don't store expiry, we rely on the Credentials object's
    # built-in auto-refresh during API calls. This proactive refresh is a
    # best-effort optimization that persists the new token to the DB.
    if refresh_token and client_id and client_secret:
        try:
            await _try_refresh_and_persist(creds, auth_service, customer_id, account_mode)
        except Exception as e:
            # Non-fatal: the Credentials object will auto-refresh on 401 anyway
            logger.debug(f"Proactive token refresh skipped: {e}")

    return creds


async def _try_refresh_and_persist(
    creds: Credentials,
    auth_service,
    customer_id: str,
    account_mode: str,
) -> None:
    """Attempt to refresh credentials and persist the new token.

    Only refreshes if the token appears expired (expired flag or no expiry set
    meaning we can't tell -- in that case we skip proactive refresh and let
    the google-auth library handle it lazily on 401).
    """
    from google.auth.transport.requests import Request

    if not creds.expired and creds.valid:
        return

    # Token might be expired or expiry unknown -- try refresh
    def do_refresh():
        creds.refresh(Request())

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, do_refresh)

    if not creds.token:
        return

    # Persist refreshed token back to the correct store
    if account_mode == "owner":
        tokens = await auth_service.get_oauth_tokens("google", customer_id="owner")
        await auth_service.store_oauth_tokens(
            provider="google",
            access_token=creds.token,
            refresh_token=creds.refresh_token or "",
            email=tokens.get("email") if tokens else None,
            name=tokens.get("name") if tokens else None,
            customer_id="owner",
        )
        logger.debug("Proactively refreshed and persisted Google access token")
