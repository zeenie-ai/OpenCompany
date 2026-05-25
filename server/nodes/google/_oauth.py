"""Google Workspace OAuth 2.0 (composition pattern -- Wave 11.I, S.2).

Composes around ``google_auth_oauthlib.flow.Flow`` rather than
subclassing :class:`services.plugin.oauth.OAuth2PKCEClient`. The Flow
library handles the PKCE flow, scope handling, and offline-access
mechanics that Google's token endpoint expects -- hand-rolling those
would lose the ``OAUTHLIB_RELAX_TOKEN_SCOPE=1`` workaround for
oauthlib upstream issue #562.

What we DO share with the Twitter (subclass) path:

* :class:`OAuthStateStore` from :mod:`services.plugin.oauth` --
  identical TTL + cleanup contract, deduplicates the state dict +
  ``cleanup_expired_states`` helper that pre-S lived here too.
* The async method shape (``async exchange_code``,
  ``async fetch_user_info``, ``async refresh_access_token``,
  ``async revoke_token``) consumed by
  :func:`services.events.oauth_lifecycle.make_oauth_lifecycle_handlers`
  and :func:`make_oauth_callback_router`. Sync calls into Flow /
  Credentials wrap through ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import json
import os
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

# Google's OAuth token endpoint legitimately returns a wider scope set
# than was requested when the OAuth Client's "Data Access" page lists
# extra scopes (e.g. ``cloud-platform``) or when ``include_granted_scopes``
# replays a previously-granted scope. ``oauthlib`` does an exact
# set-equality comparison and aborts with ``Warning: Scope has changed``.
# As of 2026 (google-auth-oauthlib 1.2.4, oauthlib still tracking
# upstream issue #562) there is no constructor flag, context manager,
# or ``expected_scopes`` argument -- ``OAUTHLIB_RELAX_TOKEN_SCOPE`` is
# still the only documented relief, read once when
# ``oauthlib.oauth2.rfc6749.parameters`` is imported. Set BEFORE the
# ``google_auth_oauthlib.flow`` import below so the env var is in place
# by the time oauthlib is loaded transitively. ``setdefault`` so an
# operator can still flip it off via the environment for diagnostics.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
# The relax flag stops the abort but the library still emits the
# warning via ``warnings.warn``; silence it to keep the operator log
# usable. Match by class + message regex; anything else (transport
# errors, deprecation notices) keeps surfacing.
warnings.filterwarnings("ignore", message=r"Scope has changed.*")

import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from core.logging import get_logger
from services.plugin.oauth import OAuthStateStore

logger = get_logger(__name__)


# ============================================================================
# Config loaders (consumed by _auth_helper, oauth_utils, _credentials, tests)
# ============================================================================

# Walk up two parents from server/nodes/google/_oauth.py -> server/, then
# into config/google_apis.json.
_config_path = Path(__file__).resolve().parents[2] / "config" / "google_apis.json"
_google_config: Dict[str, Any] = {}


def _load_config() -> Dict[str, Any]:
    """Load Google API config from JSON file."""
    global _google_config
    if not _google_config:
        try:
            with open(_config_path, "r", encoding="utf-8") as f:
                _google_config = json.load(f)
            logger.debug("Loaded Google API config", version=_google_config.get("version"))
        except Exception as e:
            logger.error("Failed to load Google API config", error=str(e))
            _google_config = _get_default_config()
    return _google_config


def _get_default_config() -> Dict[str, Any]:
    """Default config if JSON load fails."""
    return {
        "oauth": {
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "revoke_uri": "https://oauth2.googleapis.com/revoke",
            "userinfo_uri": "https://www.googleapis.com/oauth2/v2/userinfo",
        },
        "scopes": {
            "userinfo": [
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
            ],
            "gmail": [
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.modify",
            ],
            "calendar": [
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/calendar.events",
            ],
            "drive": [
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/drive.file",
            ],
            "sheets": ["https://www.googleapis.com/auth/spreadsheets"],
            "tasks": ["https://www.googleapis.com/auth/tasks"],
            "contacts": [
                "https://www.googleapis.com/auth/contacts",
                "https://www.googleapis.com/auth/contacts.readonly",
            ],
        },
    }


def get_oauth_endpoints() -> Dict[str, str]:
    """OAuth endpoint URLs from config."""
    config = _load_config()
    return config.get("oauth", _get_default_config()["oauth"])


def get_callback_paths() -> Dict[str, str]:
    """OAuth callback paths from config."""
    config = _load_config()
    oauth = config.get("oauth", {})
    return {
        "google": oauth.get("google_callback_path", "/api/google/callback"),
        "twitter": oauth.get("twitter_callback_path", "/api/twitter/callback"),
    }


def get_service_config(service: str) -> Dict[str, Any]:
    """Service-specific config (base_url, version, etc.)."""
    config = _load_config()
    return config.get("services", {}).get(service, {})


def get_all_scopes() -> List[str]:
    """Combined scopes for all Google Workspace services."""
    config = _load_config()
    scopes_config = config.get("scopes", _get_default_config()["scopes"])
    all_scopes = []
    for scope_list in scopes_config.values():
        all_scopes.extend(scope_list)
    return list(dict.fromkeys(all_scopes))  # dedupe, preserve order


def get_scopes_for_services(services: List[str]) -> List[str]:
    """Scopes for specific services only."""
    config = _load_config()
    scopes_config = config.get("scopes", _get_default_config()["scopes"])
    scopes = list(scopes_config.get("userinfo", []))
    for service in services:
        scopes.extend(scopes_config.get(service, []))
    return list(dict.fromkeys(scopes))


GOOGLE_WORKSPACE_SCOPES = get_all_scopes()
DEFAULT_SCOPES = GOOGLE_WORKSPACE_SCOPES  # legacy alias


# ============================================================================
# GoogleOAuth (composition wrapper)
# ============================================================================


class GoogleOAuth:
    """Google Workspace OAuth 2.0 client (composition wrapper).

    Conforms to the duck-typed protocol consumed by
    :func:`services.events.oauth_lifecycle.make_oauth_lifecycle_handlers`
    and :func:`make_oauth_callback_router`: shared :class:`OAuthStateStore`,
    async ``exchange_code`` / ``fetch_user_info`` /
    ``refresh_access_token`` / ``revoke_token``.

    Internally, ``Flow.from_client_config`` does the PKCE dance and
    Flow.fetch_token does the token exchange under the
    ``OAUTHLIB_RELAX_TOKEN_SCOPE=1`` env var.
    """

    # Plugin-scoped state store -- isolated from Twitter's instance.
    state_store = OAuthStateStore()

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: Optional[List[str]] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes or GOOGLE_WORKSPACE_SCOPES

        oauth_endpoints = get_oauth_endpoints()
        self.client_config = {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": oauth_endpoints["auth_uri"],
                "token_uri": oauth_endpoints["token_uri"],
                "redirect_uris": [redirect_uri],
            }
        }
        self.token_uri = oauth_endpoints["token_uri"]
        self.revoke_uri = oauth_endpoints.get(
            "revoke_uri",
            "https://oauth2.googleapis.com/revoke",
        )

    def generate_authorization_url(
        self,
        *,
        state_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """Build the Google authorization URL + register state."""
        flow = Flow.from_client_config(
            self.client_config,
            scopes=self.scopes,
            redirect_uri=self.redirect_uri,
        )
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # force consent to get refresh token
        )
        self.state_store.put(
            state,
            {
                "data": state_data or {"mode": "owner"},
                "redirect_uri": self.redirect_uri,
                "code_verifier": getattr(flow, "code_verifier", None),
            },
        )
        return {"url": authorization_url, "state": state}

    async def exchange_code(self, code: str, state: str) -> Dict[str, Any]:
        """Exchange an auth code for credentials (async wrapper)."""
        record = self.state_store.take(state)
        if not record:
            return {"success": False, "error": "Invalid or expired state"}

        state_data = record.get("data", {})
        code_verifier = record.get("code_verifier")

        def _exchange_sync() -> Dict[str, Any]:
            flow = Flow.from_client_config(
                self.client_config,
                scopes=self.scopes,
                redirect_uri=self.redirect_uri,
                state=state,
            )
            if code_verifier:
                flow.code_verifier = code_verifier
            flow.fetch_token(code=code)
            creds = flow.credentials
            user_info = self._get_user_info_sync(creds)
            return {
                "success": True,
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "expires_in": 3600,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes) if creds.scopes else self.scopes,
                "scope": " ".join(creds.scopes) if creds.scopes else "",
                "state_data": state_data,
                "email": user_info.get("email"),
                "name": user_info.get("name"),
                "picture": user_info.get("picture"),
            }

        try:
            return await asyncio.to_thread(_exchange_sync)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[google] Token exchange failed: {exc}")
            return {"success": False, "error": str(exc)}

    async def fetch_user_info(self, access_token: str) -> Dict[str, Any]:
        """Build credentials from an access token + fetch user info."""
        creds = self.build_credentials(
            access_token=access_token,
            refresh_token="",  # not needed for a single read
            client_id=self.client_id,
            client_secret=self.client_secret,
            token_uri=self.token_uri,
            scopes=self.scopes,
        )
        try:
            info = await asyncio.to_thread(self._get_user_info_sync, creds)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        if not info:
            return {"success": False, "error": "Failed to read user info"}
        return {"success": True, **info}

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Async wrapper around :meth:`refresh_credentials`."""
        return await asyncio.to_thread(
            self.refresh_credentials,
            refresh_token,
            self.client_id,
            self.client_secret,
            self.token_uri,
        )

    async def revoke_token(
        self,
        token: str,
        token_type: str = "access_token",
    ) -> Dict[str, Any]:
        """Best-effort token revocation via Google's revoke endpoint."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    self.revoke_uri,
                    data={"token": token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            logger.warning(f"[google] revoke_token network error: {exc}")
            return {"success": False, "error": str(exc)}
        if response.status_code == 200:
            return {"success": True}
        return {
            "success": False,
            "error": (response.json() if response.text else {}).get(
                "error_description",
                "Revocation failed",
            ),
        }

    # ---- internal helpers ----------------------------------------------

    def _get_user_info_sync(self, creds: Credentials) -> Dict[str, Any]:
        """Sync user-info read; called inside ``asyncio.to_thread``."""
        try:
            service = build("oauth2", "v2", credentials=creds)
            info = service.userinfo().get().execute()
            return {
                "email": info.get("email"),
                "name": info.get("name"),
                "picture": info.get("picture"),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[google] Failed to get user info: {exc}")
            return {}

    # ---- statics: kept for _auth_helper.py + other consumers ----------

    @staticmethod
    def refresh_credentials(
        refresh_token: str,
        client_id: str,
        client_secret: str,
        token_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Refresh expired credentials. Returns ``{success, access_token, ...}``."""
        if not token_uri:
            token_uri = get_oauth_endpoints()["token_uri"]
        try:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri=token_uri,
                client_id=client_id,
                client_secret=client_secret,
            )
            creds.refresh(Request())
            return {
                "success": True,
                "access_token": creds.token,
                "expires_in": 3600,
            }
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[google] Token refresh failed: {exc}")
            return {"success": False, "error": str(exc), "needs_reauth": True}

    @staticmethod
    def build_credentials(
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        token_uri: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Credentials:
        """Build Credentials object from stored tokens (used by _auth_helper)."""
        if not token_uri:
            token_uri = get_oauth_endpoints()["token_uri"]
        return Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes or GOOGLE_WORKSPACE_SCOPES,
        )

    @staticmethod
    def build_gmail_service(creds: Credentials):
        return build("gmail", "v1", credentials=creds)

    @staticmethod
    def build_calendar_service(creds: Credentials):
        return build("calendar", "v3", credentials=creds)

    @staticmethod
    def build_drive_service(creds: Credentials):
        return build("drive", "v3", credentials=creds)

    @staticmethod
    def build_sheets_service(creds: Credentials):
        return build("sheets", "v4", credentials=creds)

    @staticmethod
    def build_tasks_service(creds: Credentials):
        return build("tasks", "v1", credentials=creds)

    @staticmethod
    def build_people_service(creds: Credentials):
        return build("people", "v1", credentials=creds)


# Legacy alias.
GmailOAuth = GoogleOAuth


# Module-level alias to the state store's backing dict so the contract
# tests in tests/credentials/test_google_oauth.py can ``_oauth_states.clear()``.
# Same trick the Twitter migration uses.
_oauth_states = GoogleOAuth.state_store._states


__all__ = [
    "GoogleOAuth",
    "GmailOAuth",
    "GOOGLE_WORKSPACE_SCOPES",
    "DEFAULT_SCOPES",
    "get_oauth_endpoints",
    "get_callback_paths",
    "get_service_config",
    "get_all_scopes",
    "get_scopes_for_services",
]
