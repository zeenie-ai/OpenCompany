"""Tests for runtime OAuth redirect URI derivation.

Locks in invariant 12 from docs-internal/credentials_panel.md:
  - get_redirect_uri strips connection.base_url path and converts ws->http
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.oauth_utils import get_base_url, get_redirect_uri


pytestmark = pytest.mark.credentials


def _conn(base_url: str):
    """Mimic Starlette WebSocket / Request which expose .base_url."""
    return SimpleNamespace(base_url=base_url)


class TestGetBaseUrl:
    def test_ws_scheme_becomes_http(self):
        assert get_base_url(_conn("ws://localhost:3010/ws/status")) == "http://localhost:3010"

    def test_wss_scheme_becomes_https(self):
        assert (
            get_base_url(_conn("wss://flow.zeenie.xyz/ws/status"))
            == "https://flow.zeenie.xyz"
        )

    def test_http_scheme_preserved(self):
        assert (
            get_base_url(_conn("http://localhost:3010/api/google"))
            == "http://localhost:3010"
        )

    def test_https_scheme_preserved(self):
        assert get_base_url(_conn("https://example.com/anything")) == "https://example.com"

    def test_strips_trailing_slash(self):
        assert get_base_url(_conn("http://localhost:3010/")) == "http://localhost:3010"

    def test_preserves_non_default_port(self):
        assert (
            get_base_url(_conn("http://localhost:8080/anything"))
            == "http://localhost:8080"
        )


class TestGetRedirectUri:
    """Wave 11.I, X2: callback paths come from the plugin-registered
    `register_oauth_callback_path` registry in
    `services.ws_handler_registry`, not from a JSON config helper in
    `nodes/google/_oauth.py`. Tests patch the registry lookup."""

    @patch("services.oauth_utils.get_oauth_callback_path")
    def test_google_dev_localhost(self, mock_lookup):
        mock_lookup.return_value = "/api/google/callback"
        uri = get_redirect_uri(_conn("ws://localhost:3010/ws/status"), "google")
        assert uri == "http://localhost:3010/api/google/callback"
        mock_lookup.assert_called_once_with("google")

    @patch("services.oauth_utils.get_oauth_callback_path")
    def test_twitter_prod_https(self, mock_lookup):
        mock_lookup.return_value = "/api/twitter/callback"
        uri = get_redirect_uri(_conn("wss://flow.zeenie.xyz/ws/status"), "twitter")
        assert uri == "https://flow.zeenie.xyz/api/twitter/callback"
        mock_lookup.assert_called_once_with("twitter")

    def test_unknown_provider_falls_back_to_default_path(self):
        """`get_oauth_callback_path` (the real one, not mocked) falls
        back to ``/api/<provider>/callback`` for any unregistered
        provider. Test the integrated path end-to-end."""
        uri = get_redirect_uri(
            _conn("http://localhost:3010/anything"), "newprovider"
        )
        assert uri == "http://localhost:3010/api/newprovider/callback"

    def test_paths_registered_by_plugins(self):
        """Smoke test: the real plugin packages must register the
        canonical google + twitter callback paths from their
        ``__init__.py`` (verified by ensuring import side-effect)."""
        import nodes.google  # noqa: F401 -- side effect registers callback path
        import nodes.twitter  # noqa: F401

        from services.ws_handler_registry import get_oauth_callback_path

        assert get_oauth_callback_path("google") == "/api/google/callback"
        assert get_oauth_callback_path("twitter") == "/api/twitter/callback"
