"""Google Workspace OAuth callback router — factory-built (Wave 11.I, S.2).

Two endpoints:

* ``GET /api/google/callback`` -- built by
  :func:`services.events.oauth_lifecycle.make_oauth_callback_router`.
  ``extra_state_handler`` routes customer-mode logins (where
  ``state_data["mode"] == "customer"`` and a ``customer_id`` is set)
  to the ``google_connections`` table by overriding ``customer_id``
  on ``store_oauth_tokens``. ``redirect_after`` from the same state
  data triggers a 302 to the customer-portal URL.
* ``POST /api/google/customer-auth-url`` -- generates an OAuth URL for
  a specific customer (Google-only multi-tenant feature; Twitter
  ships owner-mode only).

The pre-S file also exposed ``GET /status`` and ``POST /logout`` REST
routes; both duplicated the WS handlers in ``_handlers.py`` and have
been retired in line with the Twitter migration.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

from services.events.oauth_lifecycle import make_oauth_callback_router

from ._handlers import _google_oauth_factory


def _user_info_to_email(info: Dict[str, Any]) -> str:
    return info.get("email", "Unknown") or "Unknown"


def _user_info_to_name(info: Dict[str, Any]) -> str:
    return info.get("name", "") or ""


async def _customer_mode_handler(
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """If the auth flow was launched in customer mode, route the
    token storage to the customer's slot + return a 302 target.

    Owner mode -> returns ``None`` (lifecycle factory keeps its
    defaults).
    """
    state_data = payload.get("state_data") or {}
    if state_data.get("mode") != "customer":
        return None

    customer_id = state_data.get("customer_id")
    if not customer_id:
        return None

    user_info = payload.get("user_info") or {}
    email = user_info.get("email", "Unknown")
    redirect_after = state_data.get("redirect_after")

    overrides: Dict[str, Any] = {"customer_id": customer_id}
    if redirect_after:
        overrides["redirect_after"] = (
            f"{redirect_after}?google_connected=true"
            f"&customer={customer_id}&email={email}"
        )
    return overrides


# The factory mounts ``GET /api/google/callback``. Google-specific
# extras (the customer-auth-url route + the customer-mode hook) layer
# on top of the same router instance.
router: APIRouter = make_oauth_callback_router(
    provider="google",
    oauth_factory=_google_oauth_factory,
    user_info_to_email=_user_info_to_email,
    user_info_to_name=_user_info_to_name,
    extra_state_handler=_customer_mode_handler,
    color_hex="#34a853",
)


@router.post("/customer-auth-url")
async def generate_customer_auth_url(
    request: Request, customer_id: str, redirect_after: Optional[str] = None,
):
    """Generate OAuth URL for a customer to connect their Google account."""
    from services.oauth_utils import get_redirect_uri

    redirect_uri = get_redirect_uri(request, "google")
    oauth = await _google_oauth_factory(redirect_uri=redirect_uri)
    if not oauth.client_id or not oauth.client_secret:
        return {
            "success": False,
            "error": "Google not configured. Add Client ID and Secret.",
        }
    result = oauth.generate_authorization_url(
        state_data={
            "customer_id": customer_id,
            "redirect_after": redirect_after,
            "mode": "customer",
        },
    )
    return {"success": True, "url": result["url"], "state": result["state"]}


@router.get("/customer/{customer_id}/status")
async def get_customer_google_status(customer_id: str):
    """Get Google connection status for a customer."""
    from services.plugin.deps import get_auth_service

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
    from services.plugin.deps import get_auth_service

    auth_service = get_auth_service()
    await auth_service.remove_oauth_tokens("google", customer_id=customer_id)
    return {"success": True, "customer_id": customer_id}
