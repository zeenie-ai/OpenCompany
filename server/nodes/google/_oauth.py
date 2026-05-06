"""
Google Workspace OAuth 2.0 using google-auth-oauthlib library.

Unified OAuth for all Google services:
- Gmail (send, search, read emails)
- Google Calendar (create, list, update, delete events)
- Google Drive (upload, download, list, share files)
- Google Sheets (read, write, append data)
- Google Tasks (create, list, complete tasks)
- Google Contacts (create, list, search contacts)

Two access modes:
1. Owner Mode - Your own Google account (Credentials Modal)
2. Customer Mode - Customer's Google account (database storage)

API endpoints loaded from config/google_apis.json
Docs: https://developers.google.com/identity/protocols/oauth2
"""

import json
import os
import time
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

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from core.logging import get_logger

logger = get_logger(__name__)

# Load Google API config from JSON.
# Walk up two parents from server/nodes/google/_oauth.py -> server/, then
# into config/google_apis.json. (Pre-D commit this file lived at
# services/google_oauth.py and used .parent.parent; the migration into
# nodes/google/ shifts the directory depth, so we use parents[2].)
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
    """Return default config if JSON fails to load."""
    return {
        "oauth": {
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "revoke_uri": "https://oauth2.googleapis.com/revoke",
            "userinfo_uri": "https://www.googleapis.com/oauth2/v2/userinfo"
        },
        "scopes": {
            "userinfo": ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"],
            "gmail": ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.modify"],
            "calendar": ["https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/calendar.events"],
            "drive": ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/drive.file"],
            "sheets": ["https://www.googleapis.com/auth/spreadsheets"],
            "tasks": ["https://www.googleapis.com/auth/tasks"],
            "contacts": ["https://www.googleapis.com/auth/contacts", "https://www.googleapis.com/auth/contacts.readonly"]
        }
    }

def get_oauth_endpoints() -> Dict[str, str]:
    """Get OAuth endpoint URLs from config."""
    config = _load_config()
    return config.get("oauth", _get_default_config()["oauth"])

def get_callback_paths() -> Dict[str, str]:
    """Get OAuth callback paths from config."""
    config = _load_config()
    oauth = config.get("oauth", {})
    return {
        "google": oauth.get("google_callback_path", "/api/google/callback"),
        "twitter": oauth.get("twitter_callback_path", "/api/twitter/callback"),
    }


def get_service_config(service: str) -> Dict[str, Any]:
    """Get service-specific config (base_url, version, etc.)."""
    config = _load_config()
    return config.get("services", {}).get(service, {})

def get_all_scopes() -> List[str]:
    """Get combined scopes for all Google Workspace services."""
    config = _load_config()
    scopes_config = config.get("scopes", _get_default_config()["scopes"])
    all_scopes = []
    for scope_list in scopes_config.values():
        all_scopes.extend(scope_list)
    return list(dict.fromkeys(all_scopes))  # Remove duplicates, preserve order

def get_scopes_for_services(services: List[str]) -> List[str]:
    """Get scopes for specific services only."""
    config = _load_config()
    scopes_config = config.get("scopes", _get_default_config()["scopes"])
    scopes = []
    # Always include userinfo
    scopes.extend(scopes_config.get("userinfo", []))
    for service in services:
        scopes.extend(scopes_config.get(service, []))
    return list(dict.fromkeys(scopes))

# Combined scopes for all services (loaded from config)
GOOGLE_WORKSPACE_SCOPES = get_all_scopes()

# Legacy alias for backward compatibility
DEFAULT_SCOPES = GOOGLE_WORKSPACE_SCOPES

# In-memory state store (use Redis in production)
_oauth_states: Dict[str, Dict[str, Any]] = {}


class GoogleOAuth:
    """Google Workspace OAuth 2.0 using google-auth-oauthlib Flow.

    Provides unified OAuth for all Google Workspace services.
    API endpoints loaded from config/google_apis.json for easy updates.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: Optional[List[str]] = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes or GOOGLE_WORKSPACE_SCOPES

        # Get OAuth endpoints from config
        oauth_endpoints = get_oauth_endpoints()

        # Build client config in Google's format
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

    def generate_authorization_url(
        self,
        state_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """
        Generate OAuth authorization URL.

        Args:
            state_data: Optional data (customer_id, mode, redirect_after)

        Returns:
            Dict with url and state
        """
        # Create Flow from client config
        flow = Flow.from_client_config(
            self.client_config,
            scopes=self.scopes,
            redirect_uri=self.redirect_uri,
        )

        # Generate authorization URL with offline access for refresh tokens
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # Force consent to get refresh token
        )

        # Store state data for callback verification
        # PKCE: google-auth-oauthlib auto-generates code_verifier; save it
        # so exchange_code() can restore it on the new Flow instance.
        _oauth_states[state] = {
            "created_at": time.time(),
            "data": state_data or {"mode": "owner"},
            "redirect_uri": self.redirect_uri,
            "code_verifier": getattr(flow, "code_verifier", None),
        }

        logger.info("Generated Google OAuth URL", state=state[:8])

        return {
            "url": authorization_url,
            "state": state,
        }

    def exchange_code(self, code: str, state: str) -> Dict[str, Any]:
        """
        Exchange authorization code for credentials.

        Args:
            code: Authorization code from callback
            state: State for verification

        Returns:
            Dict with tokens and user info
        """
        # Verify state
        oauth_state = _oauth_states.pop(state, None)
        if not oauth_state:
            return {"success": False, "error": "Invalid or expired state"}

        state_data = oauth_state.get("data", {})

        try:
            # Create Flow and fetch token
            flow = Flow.from_client_config(
                self.client_config,
                scopes=self.scopes,
                redirect_uri=self.redirect_uri,
                state=state,
            )
            # PKCE: restore code_verifier so token exchange includes it
            code_verifier = oauth_state.get("code_verifier")
            if code_verifier:
                flow.code_verifier = code_verifier
            flow.fetch_token(code=code)

            # Get credentials from flow
            creds = flow.credentials

            # Get user info
            user_info = self._get_user_info(creds)

            logger.info("Google OAuth successful", email=user_info.get("email", "")[:20])

            return {
                "success": True,
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "expires_in": 3600,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes) if creds.scopes else self.scopes,
                "state_data": state_data,
                "email": user_info.get("email"),
                "name": user_info.get("name"),
                "picture": user_info.get("picture"),
            }

        except Exception as e:
            logger.error("Token exchange failed", error=str(e))
            return {"success": False, "error": str(e)}

    def _get_user_info(self, creds: Credentials) -> Dict[str, Any]:
        """Get user info using credentials."""
        try:
            service = build("oauth2", "v2", credentials=creds)
            user_info = service.userinfo().get().execute()
            return {
                "email": user_info.get("email"),
                "name": user_info.get("name"),
                "picture": user_info.get("picture"),
            }
        except Exception as e:
            logger.error("Failed to get user info", error=str(e))
            return {}

    @staticmethod
    def refresh_credentials(
        refresh_token: str,
        client_id: str,
        client_secret: str,
        token_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Refresh expired credentials.

        Args:
            refresh_token: The refresh token
            client_id: OAuth client ID
            client_secret: OAuth client secret
            token_uri: Token endpoint (loaded from config if not provided)

        Returns:
            Dict with new access_token
        """
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
        except Exception as e:
            logger.error("Token refresh failed", error=str(e))
            return {"success": False, "error": str(e), "needs_reauth": True}

    @staticmethod
    def build_credentials(
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        token_uri: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Credentials:
        """
        Build Credentials object from stored tokens.

        Use this to create credentials for Google API calls.
        """
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

    # Service builders for each Google API
    @staticmethod
    def build_gmail_service(creds: Credentials):
        """Build Gmail API service from credentials."""
        return build("gmail", "v1", credentials=creds)

    @staticmethod
    def build_calendar_service(creds: Credentials):
        """Build Calendar API service from credentials."""
        return build("calendar", "v3", credentials=creds)

    @staticmethod
    def build_drive_service(creds: Credentials):
        """Build Drive API service from credentials."""
        return build("drive", "v3", credentials=creds)

    @staticmethod
    def build_sheets_service(creds: Credentials):
        """Build Sheets API service from credentials."""
        return build("sheets", "v4", credentials=creds)

    @staticmethod
    def build_tasks_service(creds: Credentials):
        """Build Tasks API service from credentials."""
        return build("tasks", "v1", credentials=creds)

    @staticmethod
    def build_people_service(creds: Credentials):
        """Build People API service (Contacts) from credentials."""
        return build("people", "v1", credentials=creds)


# Backward compatibility alias
GmailOAuth = GoogleOAuth


def cleanup_expired_states(max_age_seconds: int = 600):
    """Remove expired OAuth states."""
    current_time = time.time()
    expired = [
        state for state, data in _oauth_states.items()
        if current_time - data["created_at"] > max_age_seconds
    ]
    for state in expired:
        _oauth_states.pop(state, None)


def get_pending_state(state: str) -> Optional[Dict[str, Any]]:
    """Get pending state without removing it."""
    return _oauth_states.get(state)
