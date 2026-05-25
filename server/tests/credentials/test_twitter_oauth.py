"""Tests for TwitterOAuth (PKCE flow).

Locks in invariants 9 and 10 from docs-internal/credentials_panel.md:
  - PKCE state consumed exactly once
  - code_challenge = base64url(sha256(code_verifier))
  - Authorization URL contains code_challenge_method=S256
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from nodes.twitter import _oauth as twitter_oauth
from nodes.twitter._oauth import (
    TwitterOAuth,
    TOKEN_URL,
    REVOKE_URL,
    USER_INFO_URL,
)


pytestmark = pytest.mark.credentials


@pytest.fixture
def oauth():
    """Fresh TwitterOAuth + clean state store per test."""
    twitter_oauth._oauth_states.clear()
    return TwitterOAuth(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://localhost:3010/api/twitter/callback",
    )


@pytest.fixture
def public_oauth():
    """OAuth with no client_secret (public client uses client_id in body)."""
    twitter_oauth._oauth_states.clear()
    return TwitterOAuth(
        client_id="test-client-id",
        redirect_uri="http://localhost:3010/api/twitter/callback",
    )


class TestAuthorizationUrl:
    def test_generates_url_state_and_verifier(self, oauth):
        result = oauth.generate_authorization_url()
        assert "url" in result and "state" in result and "code_verifier" in result
        assert result["url"].startswith("https://x.com/i/oauth2/authorize?")

    def test_url_contains_pkce_s256(self, oauth):
        result = oauth.generate_authorization_url()
        params = parse_qs(urlparse(result["url"]).query)
        assert params["code_challenge_method"] == ["S256"]
        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["test-client-id"]
        assert params["redirect_uri"] == ["http://localhost:3010/api/twitter/callback"]
        assert "code_challenge" in params
        assert "state" in params

    def test_code_challenge_is_sha256_of_verifier(self, oauth):
        """Invariant 10: challenge = BASE64URL(SHA256(verifier))."""
        result = oauth.generate_authorization_url()
        params = parse_qs(urlparse(result["url"]).query)

        verifier = result["code_verifier"]
        expected_digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected_challenge = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode()

        assert params["code_challenge"] == [expected_challenge]

    def test_state_stored_with_verifier(self, oauth):
        result = oauth.generate_authorization_url()
        state = result["state"]
        assert state in twitter_oauth._oauth_states
        stored = twitter_oauth._oauth_states[state]
        assert stored["code_verifier"] == result["code_verifier"]
        assert stored["redirect_uri"] == oauth.redirect_uri
        assert "created_at" in stored

    def test_required_scopes_in_url(self, oauth):
        result = oauth.generate_authorization_url()
        params = parse_qs(urlparse(result["url"]).query)
        scope = params["scope"][0]
        for required in ("tweet.read", "tweet.write", "users.read", "offline.access"):
            assert required in scope

    def test_two_calls_produce_different_state_and_verifier(self, oauth):
        a = oauth.generate_authorization_url()
        b = oauth.generate_authorization_url()
        assert a["state"] != b["state"]
        assert a["code_verifier"] != b["code_verifier"]


class TestExchangeCode:
    @respx.mock
    async def test_successful_exchange(self, oauth):
        auth = oauth.generate_authorization_url()

        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "access-xyz",
                    "refresh_token": "refresh-xyz",
                    "expires_in": 7200,
                    "scope": "tweet.read tweet.write",
                    "token_type": "bearer",
                },
            )
        )

        result = await oauth.exchange_code(code="auth-code", state=auth["state"])

        assert result["success"] is True
        assert result["access_token"] == "access-xyz"
        assert result["refresh_token"] == "refresh-xyz"
        assert result["expires_in"] == 7200

    @respx.mock
    async def test_state_consumed_exactly_once(self, oauth):
        """Invariant 9: a second exchange with the same state must fail."""
        auth = oauth.generate_authorization_url()
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "a", "refresh_token": "r", "expires_in": 7200},
            )
        )

        first = await oauth.exchange_code("code", auth["state"])
        assert first["success"] is True

        second = await oauth.exchange_code("code", auth["state"])
        assert second["success"] is False
        assert "state" in second["error"].lower()

    async def test_unknown_state_fails(self, oauth):
        result = await oauth.exchange_code("code", "never-generated-state")
        assert result["success"] is False
        assert "state" in result["error"].lower()

    @respx.mock
    async def test_token_endpoint_error_propagates(self, oauth):
        auth = oauth.generate_authorization_url()
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "auth code expired",
                },
            )
        )

        result = await oauth.exchange_code("code", auth["state"])
        assert result["success"] is False
        assert "expired" in result["error"]

    @respx.mock
    async def test_confidential_client_uses_basic_auth(self, oauth):
        """Confidential client (with secret) must send Basic auth header."""
        auth = oauth.generate_authorization_url()

        captured = {}

        def _capture(request):
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = request.read().decode()
            return httpx.Response(
                200,
                json={"access_token": "a", "refresh_token": "r", "expires_in": 7200},
            )

        respx.post(TOKEN_URL).mock(side_effect=_capture)

        await oauth.exchange_code("code", auth["state"])

        assert captured["auth"] is not None
        assert captured["auth"].startswith("Basic ")
        # client_id should NOT be in body when Basic auth is used
        assert "client_id=" not in captured["body"]

    @respx.mock
    async def test_public_client_includes_client_id_in_body(self, public_oauth):
        auth = public_oauth.generate_authorization_url()

        captured = {}

        def _capture(request):
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = request.read().decode()
            return httpx.Response(
                200,
                json={"access_token": "a", "refresh_token": "r", "expires_in": 7200},
            )

        respx.post(TOKEN_URL).mock(side_effect=_capture)

        await public_oauth.exchange_code("code", auth["state"])

        assert captured["auth"] is None
        assert "client_id=test-client-id" in captured["body"]


class TestRefreshAndRevoke:
    @respx.mock
    async def test_refresh_access_token(self, oauth):
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 7200,
                },
            )
        )

        result = await oauth.refresh_access_token("old-refresh")
        assert result["success"] is True
        assert result["access_token"] == "new-access"

    @respx.mock
    async def test_revoke_token_success(self, oauth):
        respx.post(REVOKE_URL).mock(return_value=httpx.Response(200, json={}))
        result = await oauth.revoke_token("some-token", "access_token")
        assert result["success"] is True


class TestUserInfo:
    @respx.mock
    async def test_get_user_info(self, oauth):
        respx.get(USER_INFO_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "id": "12345",
                        "username": "testuser",
                        "name": "Test User",
                        "profile_image_url": "https://x.com/img.jpg",
                        "verified": True,
                    }
                },
            )
        )

        info = await oauth.get_user_info("access-token")
        assert info["success"] is True
        assert info["id"] == "12345"
        assert info["username"] == "testuser"
        assert info["verified"] is True

    @respx.mock
    async def test_get_user_info_401_returns_error(self, oauth):
        respx.get(USER_INFO_URL).mock(
            return_value=httpx.Response(
                401,
                json={"title": "Unauthorized", "detail": "token expired"},
            )
        )

        info = await oauth.get_user_info("bad-token")
        assert info["success"] is False
        assert "expired" in info["error"]
