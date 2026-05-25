"""Integration tests for credential-related WebSocket handlers.

We invoke the handler functions directly (rather than spinning up the full FastAPI
app + DI container) so the suite stays under 30s and doesn't pull in Temporal /
LangChain / native LLM SDKs.

Locks in invariants 1, 5, 6 from docs-internal/credentials_panel.md:
  - WebSocket message types and payload shapes
  - Status responses use camelCase hasKey / apiKey (matches the
    update_api_key_status broadcaster convention)
  - Provider Defaults uses dedicated handler, not save_api_key
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Wave 13.5: API key handlers moved from routers.websocket to
# services.credentials.handlers. Aliasing the new module so the existing
# ``ws_module.handle_xxx`` references below keep working unchanged.
from services.credentials import handlers as ws_module


pytestmark = pytest.mark.credentials


# --- Fake WebSocket ----------------------------------------------------------


class _FakeWebSocket:
    """Stand-in for starlette.WebSocket exposing only base_url + client_state."""

    def __init__(self, base_url: str = "ws://localhost:3010/ws/status"):
        self.base_url = base_url
        # mimic starlette WebSocketState.CONNECTED
        self.client_state = SimpleNamespace(name="CONNECTED")


@pytest.fixture
def fake_ws():
    return _FakeWebSocket()


# --- Container patching ------------------------------------------------------


@pytest.fixture
def patched_container(monkeypatch, auth_service):
    """Wire the real auth_service fixture into the container singleton."""
    monkeypatch.setattr(ws_module.container, "auth_service", lambda: auth_service)

    # ai_service.fetch_models is called by handle_validate_api_key for LLM providers.
    fake_ai = MagicMock()
    fake_ai.fetch_models = AsyncMock(return_value=["model-a", "model-b"])
    monkeypatch.setattr(ws_module.container, "ai_service", lambda: fake_ai)

    # broadcaster is imported by name into routers.websocket -- must patch THERE,
    # not on services.status_broadcaster (the bound name in the handler module).
    fake_broadcaster = MagicMock()
    fake_broadcaster.update_api_key_status = AsyncMock()
    # Wave-12 credential broadcast helper used by save / delete /
    # oauth-logout handlers. Wraps WorkflowEvent (CloudEvents v1.0)
    # and broadcasts as `credential_catalogue_updated`.
    fake_broadcaster.broadcast_credential_event = AsyncMock()
    fake_broadcaster.broadcast = AsyncMock()
    fake_broadcaster._status = {}
    monkeypatch.setattr(ws_module, "get_status_broadcaster", lambda: fake_broadcaster)
    # Also patch on the original module so any late imports (e.g. inside
    # handle_google_oauth_status) get the same fake.
    monkeypatch.setattr(
        "services.status_broadcaster.get_status_broadcaster",
        lambda: fake_broadcaster,
    )
    return SimpleNamespace(auth=auth_service, ai=fake_ai, broadcaster=fake_broadcaster)


# --- Helper to bypass the decorator's required-fields check is not needed:
#     the decorator returns a wrapper we can call directly with the data dict.


async def _call(handler, data, ws):
    """Invoke a @ws_handler-decorated function."""
    return await handler(data, ws)


# --- API Key handlers --------------------------------------------------------


class TestValidateApiKey:
    @patch("services.ai.PROVIDER_CONFIGS", {"openai": object()})
    async def test_validate_stores_key_with_models(self, patched_container, fake_ws):
        result = await _call(
            ws_module.handle_validate_api_key,
            {"provider": "OpenAI", "api_key": "  sk-foo  "},
            fake_ws,
        )

        assert result["success"] is True
        assert result["provider"] == "openai"  # lowercased
        assert result["valid"] is True
        assert result["models"] == ["model-a", "model-b"]

        # Stored
        stored = await patched_container.auth.get_api_key("openai")
        assert stored == "sk-foo"  # whitespace stripped

        # ai_service.fetch_models called with stripped key
        patched_container.ai.fetch_models.assert_awaited_once_with("openai", "sk-foo")

        # Broadcaster notified with hasKey + models
        patched_container.broadcaster.update_api_key_status.assert_awaited_once()

    async def test_validate_unknown_provider_returns_error(self, patched_container, fake_ws):
        # ``handle_validate_api_key`` dispatches via
        # ``CREDENTIAL_REGISTRY``; a provider without a registered
        # ``Credential`` subclass is an explicit error (no fall-through
        # to the default LLM probe — every supported provider must own
        # its credential class).
        result = await _call(
            ws_module.handle_validate_api_key,
            {"provider": "anonymous_provider", "api_key": "any-test"},
            fake_ws,
        )

        assert result["success"] is False
        assert result["valid"] is False
        assert "anonymous_provider" in result["error"]
        patched_container.ai.fetch_models.assert_not_called()

    async def test_missing_required_fields_returns_error(self, patched_container, fake_ws):
        result = await _call(ws_module.handle_validate_api_key, {"provider": "openai"}, fake_ws)
        assert result["success"] is False
        assert "api_key" in result["error"]


class TestGetStoredApiKey:
    async def test_returns_has_key_false_when_absent(self, patched_container, fake_ws):
        result = await _call(ws_module.handle_get_stored_api_key, {"provider": "openai"}, fake_ws)
        assert result["success"] is True
        assert result["hasKey"] is False
        assert "apiKey" not in result

    async def test_returns_key_and_models_when_present(self, patched_container, fake_ws):
        await patched_container.auth.store_api_key("openai", "sk-stored", models=["gpt-4", "gpt-3.5"])

        result = await _call(ws_module.handle_get_stored_api_key, {"provider": "OpenAI"}, fake_ws)

        assert result["hasKey"] is True
        assert result["apiKey"] == "sk-stored"
        assert result["models"] == ["gpt-4", "gpt-3.5"]
        assert result["provider"] == "openai"


class TestSaveApiKey:
    async def test_save_persists_without_validation(self, patched_container, fake_ws):
        result = await _call(
            ws_module.handle_save_api_key,
            {
                "provider": "telegram",
                "api_key": "123:abc",
                "models": [],
            },
            fake_ws,
        )

        assert result["success"] is True
        # No model fetch (Pattern C)
        patched_container.ai.fetch_models.assert_not_called()

        stored = await patched_container.auth.get_api_key("telegram")
        assert stored == "123:abc"

    async def test_save_strips_whitespace_and_lowercases_provider(self, patched_container, fake_ws):
        await _call(
            ws_module.handle_save_api_key,
            {"provider": "ANTHROPIC", "api_key": "  sk-ant-foo \n"},
            fake_ws,
        )
        assert await patched_container.auth.get_api_key("anthropic") == "sk-ant-foo"


class TestDeleteApiKey:
    async def test_delete_removes_stored_key(self, patched_container, fake_ws):
        await patched_container.auth.store_api_key("openai", "sk-x", models=[])
        result = await _call(ws_module.handle_delete_api_key, {"provider": "openai"}, fake_ws)
        assert result["success"] is True
        assert await patched_container.auth.get_api_key("openai") is None


# --- OAuth handlers ----------------------------------------------------------


class TestTwitterOAuthHandlers:
    # Twitter handlers moved to ``nodes/twitter/_handlers.py`` as part
    # of the plugin-extraction migration. The tests now import
    # directly from the plugin folder; the dispatch contract via
    # ``register_ws_handlers`` is exercised by
    # ``test_plugin_self_containment.py``.

    async def test_login_fails_without_client_id(self, patched_container, fake_ws):
        from nodes.twitter._handlers import handle_twitter_oauth_login

        result = await _call(handle_twitter_oauth_login, {}, fake_ws)
        assert result["success"] is False
        assert "Client ID" in result["error"]

    async def test_login_returns_authorization_url(self, patched_container, fake_ws):
        from nodes.twitter._handlers import handle_twitter_oauth_login

        await patched_container.auth.store_api_key("twitter_client_id", "ci-test", models=[])
        await patched_container.auth.store_api_key("twitter_client_secret", "cs-test", models=[])

        result = await _call(handle_twitter_oauth_login, {}, fake_ws)

        assert result["success"] is True
        assert result["url"].startswith("https://x.com/i/oauth2/authorize")
        assert "state" in result

    async def test_status_when_disconnected(self, patched_container, fake_ws):
        from nodes.twitter._handlers import handle_twitter_oauth_status

        result = await _call(handle_twitter_oauth_status, {}, fake_ws)
        assert result["connected"] is False
        assert result["username"] is None

    async def test_logout_clears_oauth_tokens(self, patched_container, fake_ws):
        from nodes.twitter._handlers import handle_twitter_logout

        await patched_container.auth.store_oauth_tokens("twitter", "access", "refresh")
        with patch(
            "nodes.twitter._oauth.TwitterOAuth.revoke_token",
            new=AsyncMock(return_value={"success": True}),
        ):
            result = await _call(handle_twitter_logout, {}, fake_ws)

        assert result["success"] is True
        assert await patched_container.auth.get_oauth_tokens("twitter") is None


class TestGoogleOAuthHandlers:
    # Google OAuth handlers moved to ``nodes/google/_handlers.py`` as
    # part of the plugin-extraction migration. Tests now import from
    # the plugin folder; the dispatch contract is exercised by
    # ``test_plugin_self_containment.py``.

    async def test_login_fails_without_client_credentials(self, patched_container, fake_ws):
        from nodes.google._handlers import handle_google_oauth_login

        result = await _call(handle_google_oauth_login, {}, fake_ws)
        assert result["success"] is False
        assert "Client ID" in result["error"]

    async def test_login_returns_authorization_url(self, patched_container, fake_ws):
        from nodes.google._handlers import handle_google_oauth_login

        await patched_container.auth.store_api_key("google_client_id", "ci.apps.googleusercontent.com", models=[])
        await patched_container.auth.store_api_key("google_client_secret", "cs-test", models=[])

        result = await _call(handle_google_oauth_login, {}, fake_ws)

        assert result["success"] is True
        assert result["url"].startswith("https://accounts.google.com/o/oauth2/auth")
        assert "access_type=offline" in result["url"]
        assert "prompt=consent" in result["url"]

    async def test_status_when_disconnected(self, patched_container, fake_ws):
        from nodes.google._handlers import handle_google_oauth_status

        result = await _call(handle_google_oauth_status, {}, fake_ws)
        assert result["connected"] is False
        assert result["email"] is None

    async def test_logout_removes_oauth_tokens(self, patched_container, fake_ws):
        from nodes.google._handlers import handle_google_logout

        await patched_container.auth.store_oauth_tokens("google", "access", "refresh", email="user@example.com")
        result = await _call(handle_google_logout, {}, fake_ws)
        assert result["success"] is True
        assert await patched_container.auth.get_oauth_tokens("google") is None
