"""OAuth 2.0 PKCE shared infrastructure (Wave 11.I, milestone S).

Two pieces consumed by every OAuth-using plugin:

1. :class:`OAuthStateStore` -- in-memory PKCE/state store with TTL.
   Both Twitter (hand-rolled PKCE) and Google (``google_auth_oauthlib``-
   wrapped) drive their callback CSRF check through one of these. The
   pre-Wave-11.I plugin folders each hand-rolled their own ``dict`` +
   ``cleanup_expired_states`` helper -- collapsed into this one class.

2. :class:`OAuth2PKCEClient` -- abstract base for plugins that hand-roll
   the OAuth 2.0 PKCE dance themselves (Twitter pattern). Subclass it
   when the upstream API exposes a vanilla token endpoint and the
   `Flow` class from `google_auth_oauthlib` doesn't fit. Override
   :meth:`fetch_user_info` to translate the provider's profile API into
   the unified ``{id, username, name, ...}`` shape consumed by the
   lifecycle helpers in :mod:`services.events.oauth_lifecycle`.

Plugins that compose around an upstream library (Google's
``google_auth_oauthlib.flow.Flow``) skip the subclass and instantiate
:class:`OAuthStateStore` directly. The lifecycle factory in
:mod:`services.events.oauth_lifecycle` is duck-typed against the
methods listed below -- both subclass and composition paths feed it
without changes.

Persistence: in-memory only. State resets on server restart -- a popup
opened across a restart will fail the callback CSRF check. Same as
pre-extraction behaviour. Persistent backing (Redis / DB) is a
documented follow-up.

Cleanup: lazy. The store exposes :meth:`OAuthStateStore.cleanup_expired`
so plugins can prune from a periodic task; called on demand from the
callback path so a flood of abandoned popups eventually self-clears
without a background sweeper.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from core.logging import get_logger

logger = get_logger(__name__)


# ============================================================================
# OAuthStateStore
# ============================================================================


class OAuthStateStore:
    """In-memory PKCE/state store with TTL cleanup.

    One instance per plugin (or one shared module-level instance).
    Stores a payload (typically ``{code_verifier, redirect_uri,
    state_data, created_at}``) keyed by the random ``state`` parameter
    the OAuth flow round-trips through the browser.
    """

    DEFAULT_TTL_SECONDS = 600  # 10 minutes -- auth codes themselves expire faster

    def __init__(self, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._states: Dict[str, Dict[str, Any]] = {}

    def put(self, state: str, payload: Dict[str, Any]) -> None:
        """Store a payload under ``state``. Stamps ``created_at``."""
        record = dict(payload)
        record.setdefault("created_at", time.time())
        self._states[state] = record

    def take(self, state: str) -> Optional[Dict[str, Any]]:
        """Pop and return the payload for ``state``. One-shot.

        This is the callback-path read: the state is consumed exactly
        once when the user lands back on the redirect URI with the
        ``code`` parameter.
        """
        return self._states.pop(state, None)

    def peek(self, state: str) -> Optional[Dict[str, Any]]:
        """Return the payload without removing it (read-only check).

        Used by callback handlers that need to read the redirect_uri /
        state_data BEFORE running the code exchange (which calls
        :meth:`take`).
        """
        return self._states.get(state)

    def cleanup_expired(self) -> int:
        """Remove states older than ``ttl_seconds``. Returns count removed."""
        now = time.time()
        expired = [state for state, record in self._states.items() if now - record.get("created_at", 0) > self.ttl_seconds]
        for state in expired:
            self._states.pop(state, None)
        if expired:
            logger.debug(f"OAuthStateStore: cleaned {len(expired)} expired states")
        return len(expired)

    def __len__(self) -> int:
        return len(self._states)

    def __contains__(self, state: object) -> bool:
        return state in self._states


# ============================================================================
# PKCE helpers (RFC 7636)
# ============================================================================


def _generate_code_verifier() -> str:
    """Cryptographically random PKCE code verifier (43-128 chars)."""
    random_bytes = secrets.token_bytes(96)
    verifier = base64.urlsafe_b64encode(random_bytes).rstrip(b"=").decode("ascii")
    return verifier[:128]


def _generate_code_challenge(code_verifier: str) -> str:
    """S256 code challenge: ``BASE64URL(SHA256(code_verifier))``."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _generate_state() -> str:
    """Random state parameter for CSRF protection (URL-safe)."""
    return secrets.token_urlsafe(32)


# ============================================================================
# OAuth2PKCEClient (subclass-mode -- Twitter pattern)
# ============================================================================


class OAuth2PKCEClient(ABC):
    """Hand-rolled OAuth 2.0 PKCE client.

    Concrete subclasses override :attr:`provider`,
    :attr:`authorization_endpoint`, :attr:`token_endpoint`,
    optionally :attr:`revocation_endpoint`, and the
    :meth:`fetch_user_info` translation. Everything else (PKCE state
    management, code exchange, token refresh, optional revocation)
    lives on this base.

    Plugins that compose around a third-party Flow class (e.g.
    Google's ``google_auth_oauthlib.flow.Flow``) skip this base and
    instantiate :class:`OAuthStateStore` directly. The lifecycle
    factory is duck-typed against these method signatures -- both
    paths feed it identically.
    """

    provider: ClassVar[str] = ""
    authorization_endpoint: ClassVar[str] = ""
    token_endpoint: ClassVar[str] = ""
    revocation_endpoint: ClassVar[str] = ""  # optional

    # Per-class shared state store. Plugins can override at class level
    # if they want isolated TTLs / multiple instances.
    state_store: ClassVar[OAuthStateStore] = OAuthStateStore()

    DEFAULT_SCOPES: ClassVar[List[str]] = []

    def __init__(
        self,
        client_id: str,
        redirect_uri: str,
        client_secret: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> None:
        if not self.provider:
            raise ValueError(f"{type(self).__name__} must set the ``provider`` ClassVar")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = list(scopes) if scopes is not None else list(self.DEFAULT_SCOPES)

    # ---- authorization-url generation ----------------------------------

    def generate_authorization_url(
        self,
        *,
        state_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Return the redirect URL + state + verifier.

        Stashes ``code_verifier`` + ``redirect_uri`` (and any
        caller-supplied ``state_data``) in :attr:`state_store` keyed by
        the random state. The callback handler reads this back to
        complete the exchange.
        """
        state = _generate_state()
        code_verifier = _generate_code_verifier()
        code_challenge = _generate_code_challenge(code_verifier)

        self.state_store.put(
            state,
            {
                "code_verifier": code_verifier,
                "redirect_uri": self.redirect_uri,
                "data": state_data or {},
            },
        )

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if extra_params:
            params.update(extra_params)

        return {
            "url": f"{self.authorization_endpoint}?{urlencode(params)}",
            "state": state,
            "code_verifier": code_verifier,
        }

    # ---- token endpoint helpers ----------------------------------------

    def _token_request_auth(self) -> tuple[Dict[str, str], Dict[str, str]]:
        """Build (extra_body, extra_headers) for the token endpoint.

        Confidential clients (with ``client_secret``) send Basic auth
        in the header. Public clients put ``client_id`` in the body.
        """
        if self.client_secret:
            credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            return {}, {"Authorization": f"Basic {credentials}"}
        return {"client_id": self.client_id}, {}

    async def exchange_code(self, code: str, state: str) -> Dict[str, Any]:
        """Exchange an auth code for tokens. Pops the state."""
        record = self.state_store.take(state)
        if not record:
            logger.error(f"[{self.provider}] Invalid or expired OAuth state")
            return {"success": False, "error": "Invalid or expired state"}

        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": record.get("redirect_uri", self.redirect_uri),
            "code_verifier": record["code_verifier"],
        }
        extra_body, extra_headers = self._token_request_auth()
        body.update(extra_body)
        headers = {"Content-Type": "application/x-www-form-urlencoded", **extra_headers}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.token_endpoint,
                    data=body,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            logger.error(f"[{self.provider}] HTTP error during token exchange: {exc}")
            return {"success": False, "error": str(exc)}

        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            logger.error(
                f"[{self.provider}] Token exchange failed",
                status=response.status_code,
                error=error_data,
            )
            return {
                "success": False,
                "error": error_data.get(
                    "error_description",
                    error_data.get("error", "Token exchange failed"),
                ),
            }

        data = response.json()
        return {
            "success": True,
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expires_in": data.get("expires_in"),
            "scope": data.get("scope"),
            "token_type": data.get("token_type", "Bearer"),
        }

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Trade a refresh token for a fresh access token."""
        body = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        extra_body, extra_headers = self._token_request_auth()
        body.update(extra_body)
        headers = {"Content-Type": "application/x-www-form-urlencoded", **extra_headers}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.token_endpoint,
                    data=body,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            logger.error(f"[{self.provider}] HTTP error during token refresh: {exc}")
            return {"success": False, "error": str(exc)}

        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            return {
                "success": False,
                "error": error_data.get("error_description", "Token refresh failed"),
            }

        data = response.json()
        return {
            "success": True,
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expires_in": data.get("expires_in"),
            "scope": data.get("scope"),
        }

    async def revoke_token(
        self,
        token: str,
        token_type: str = "access_token",
    ) -> Dict[str, Any]:
        """Revoke an access or refresh token (best-effort).

        Subclasses without :attr:`revocation_endpoint` get a no-op
        ``{"success": True, "skipped": True}`` so callers can always
        attempt a revoke without branching.
        """
        if not self.revocation_endpoint:
            return {"success": True, "skipped": True}

        body = {"token": token, "token_type_hint": token_type}
        extra_body, extra_headers = self._token_request_auth()
        body.update(extra_body)
        headers = {"Content-Type": "application/x-www-form-urlencoded", **extra_headers}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.revocation_endpoint,
                    data=body,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            logger.error(f"[{self.provider}] HTTP error during token revoke: {exc}")
            return {"success": False, "error": str(exc)}

        if response.status_code == 200:
            return {"success": True}
        error_data = response.json() if response.text else {}
        return {
            "success": False,
            "error": error_data.get("error_description", "Revocation failed"),
        }

    # ---- subclass override --------------------------------------------

    @abstractmethod
    async def fetch_user_info(self, access_token: str) -> Dict[str, Any]:
        """Translate the provider's profile API into a unified shape.

        MUST return ``{success: bool, ...}`` -- on success, include
        ``id`` and at least one of ``username`` / ``email`` / ``name``.
        The lifecycle factory uses these to populate the broadcast
        envelope's ``subject`` and the connection-status payload.
        """


__all__ = [
    "OAuthStateStore",
    "OAuth2PKCEClient",
]
