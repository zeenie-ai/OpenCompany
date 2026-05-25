"""Tests for GoogleOAuth (Workspace OAuth 2.0 with offline access).

Locks in invariant 11 from docs-internal/credentials_panel.md:
  - Authorization URL contains access_type=offline AND prompt=consent
  - State store consumed exactly once
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from nodes.google import _oauth as google_oauth
from nodes.google._oauth import GoogleOAuth, get_callback_paths


pytestmark = pytest.mark.credentials


@pytest.fixture
def oauth():
    google_oauth._oauth_states.clear()
    return GoogleOAuth(
        client_id="test-client-id.apps.googleusercontent.com",
        client_secret="test-client-secret",
        redirect_uri="http://localhost:3010/api/google/callback",
    )


class TestAuthorizationUrl:
    def test_generates_url_and_state(self, oauth):
        result = oauth.generate_authorization_url()
        assert "url" in result
        assert "state" in result
        assert result["url"].startswith("https://accounts.google.com/o/oauth2/auth")

    def test_url_contains_offline_access_and_consent_prompt(self, oauth):
        """Invariant 11: refresh_token won't be issued without these params."""
        result = oauth.generate_authorization_url()
        params = parse_qs(urlparse(result["url"]).query)

        assert params["access_type"] == ["offline"]
        assert params["prompt"] == ["consent"]

    def test_url_contains_required_scopes(self, oauth):
        result = oauth.generate_authorization_url()
        params = parse_qs(urlparse(result["url"]).query)
        scope = params["scope"][0]
        # A few representative scopes from each Google service must be present
        for required in (
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/drive",
        ):
            assert required in scope

    def test_state_stored_with_redirect_uri_and_verifier(self, oauth):
        result = oauth.generate_authorization_url()
        state = result["state"]
        assert state in google_oauth._oauth_states
        stored = google_oauth._oauth_states[state]
        assert stored["redirect_uri"] == oauth.redirect_uri
        assert "created_at" in stored
        # PKCE verifier may or may not be set depending on google-auth-oauthlib version
        # but the key must be present
        assert "code_verifier" in stored

    def test_state_data_defaults_to_owner_mode(self, oauth):
        result = oauth.generate_authorization_url()
        stored = google_oauth._oauth_states[result["state"]]
        assert stored["data"] == {"mode": "owner"}

    def test_state_data_passes_through(self, oauth):
        result = oauth.generate_authorization_url(state_data={"customer_id": "cust-1", "mode": "customer"})
        stored = google_oauth._oauth_states[result["state"]]
        assert stored["data"]["customer_id"] == "cust-1"
        assert stored["data"]["mode"] == "customer"

    def test_two_calls_produce_different_state(self, oauth):
        a = oauth.generate_authorization_url()
        b = oauth.generate_authorization_url()
        assert a["state"] != b["state"]


class TestExchangeCode:
    async def test_unknown_state_fails(self, oauth):
        # Don't even register a state -- exchange must fail without hitting Google.
        result = await oauth.exchange_code("code", "never-generated-state")
        assert result["success"] is False
        assert "state" in result["error"].lower()

    async def test_state_consumed_on_failure(self, oauth):
        """Even if the token exchange fails, the state must be popped (single use)."""
        auth = oauth.generate_authorization_url()
        state = auth["state"]
        # Exchange will fail because we haven't mocked Google -- network error
        await oauth.exchange_code("invalid-code", state)
        # State must be gone regardless of success
        assert state not in google_oauth._oauth_states


class TestCallbackPaths:
    def test_google_path_present(self):
        paths = get_callback_paths()
        assert "google" in paths
        assert paths["google"].startswith("/api/")

    def test_twitter_path_present(self):
        paths = get_callback_paths()
        assert "twitter" in paths
        assert paths["twitter"].startswith("/api/")
