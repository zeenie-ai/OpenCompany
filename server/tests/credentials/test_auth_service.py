"""Tests for AuthService (single point of access for credentials).

Locks in invariants 7 and 8 from docs-internal/credentials_panel.md:
  - store_api_key requires models=[]
  - Memory cache hit path does not query DB on every call
  - clear_cache wipes _api_key_cache, _oauth_cache (one merged cache
    per RFC 9700 + post-Wave-12 dedup; previously the API-key half was
    split across _memory_cache + _models_cache).
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.credentials


class TestApiKeyOps:
    async def test_store_and_get_roundtrip(self, auth_service):
        ok = await auth_service.store_api_key("openai", "sk-foo", models=["gpt-4"])
        assert ok is True
        assert await auth_service.get_api_key("openai") == "sk-foo"

    async def test_get_models_after_store(self, auth_service):
        await auth_service.store_api_key("openai", "sk-foo", models=["gpt-4", "gpt-3.5"])
        assert await auth_service.get_stored_models("openai") == ["gpt-4", "gpt-3.5"]

    async def test_store_with_empty_models_for_non_llm_keys(self, auth_service):
        # Pattern C: save-only API keys (telegram, android_remote, etc.)
        ok = await auth_service.store_api_key("telegram", "123:abc", models=[])
        assert ok is True
        assert await auth_service.get_api_key("telegram") == "123:abc"
        assert await auth_service.get_stored_models("telegram") == []

    async def test_store_requires_models_keyword(self, auth_service):
        # Invariant 7: store_api_key takes `models` as required argument.
        # Calling without it must fail at the Python signature level.
        with pytest.raises(TypeError):
            await auth_service.store_api_key("openai", "sk-foo")  # type: ignore[call-arg]

    async def test_remove_clears_storage(self, auth_service):
        await auth_service.store_api_key("openai", "sk-foo", models=[])
        ok = await auth_service.remove_api_key("openai")
        assert ok is True
        assert await auth_service.get_api_key("openai") is None

    async def test_has_valid_key(self, auth_service):
        assert await auth_service.has_valid_key("openai") is False
        await auth_service.store_api_key("openai", "sk-foo", models=[])
        assert await auth_service.has_valid_key("openai") is True

    async def test_session_isolation(self, auth_service):
        await auth_service.store_api_key("openai", "sk-default", models=[], session_id="default")
        await auth_service.store_api_key("openai", "sk-other", models=[], session_id="other")
        assert await auth_service.get_api_key("openai", "default") == "sk-default"
        assert await auth_service.get_api_key("openai", "other") == "sk-other"


class TestMemoryCache:
    async def test_get_after_store_uses_memory_cache(self, auth_service, monkeypatch):
        await auth_service.store_api_key("openai", "sk-cached", models=[])

        # Spy on the underlying credentials_db.get_api_key to count DB hits.
        call_count = {"n": 0}
        original = auth_service.credentials_db.get_api_key

        async def counting_get(provider, session_id="default"):
            call_count["n"] += 1
            return await original(provider, session_id)

        monkeypatch.setattr(auth_service.credentials_db, "get_api_key", counting_get)

        # First get hits memory cache (no DB call)
        result = await auth_service.get_api_key("openai")
        assert result == "sk-cached"
        assert call_count["n"] == 0

        # Second/third get also hit memory cache
        await auth_service.get_api_key("openai")
        await auth_service.get_api_key("openai")
        assert call_count["n"] == 0

    async def test_get_falls_back_to_db_when_cache_missing(self, auth_service, monkeypatch):
        await auth_service.store_api_key("openai", "sk-foo", models=[])

        # Wipe memory cache to simulate fresh process
        auth_service._api_key_cache.clear()

        call_count = {"n": 0}
        original = auth_service.credentials_db.get_api_key

        async def counting_get(provider, session_id="default"):
            call_count["n"] += 1
            return await original(provider, session_id)

        monkeypatch.setattr(auth_service.credentials_db, "get_api_key", counting_get)

        # First call: DB hit (cache populated as side effect)
        assert await auth_service.get_api_key("openai") == "sk-foo"
        assert call_count["n"] == 1

        # Second call: cache hit, no additional DB call
        assert await auth_service.get_api_key("openai") == "sk-foo"
        assert call_count["n"] == 1

    async def test_clear_cache_wipes_both_caches(self, auth_service):
        await auth_service.store_api_key("openai", "sk-foo", models=["gpt-4"])
        await auth_service.store_oauth_tokens("google", "a", "r", email="u@x.com")

        # Sanity: caches populated
        assert auth_service._api_key_cache
        assert auth_service._oauth_cache

        auth_service.clear_cache()

        assert auth_service._api_key_cache == {}
        assert auth_service._oauth_cache == {}

    async def test_clear_cache_does_not_delete_db_data(self, auth_service):
        await auth_service.store_api_key("openai", "sk-persistent", models=[])
        auth_service.clear_cache()

        # Re-fetch -- value comes back from DB and re-populates cache
        assert await auth_service.get_api_key("openai") == "sk-persistent"


class TestOAuthOps:
    async def test_store_and_get_oauth_tokens(self, auth_service):
        ok = await auth_service.store_oauth_tokens(
            provider="google",
            access_token="access-1",
            refresh_token="refresh-1",
            email="u@example.com",
            name="User",
            scopes="openid email",
        )
        assert ok is True

        tokens = await auth_service.get_oauth_tokens("google")
        assert tokens is not None
        assert tokens["access_token"] == "access-1"
        # Per RFC 9700 the refresh token is NOT returned by
        # get_oauth_tokens — read via get_oauth_refresh_token() instead.
        assert "refresh_token" not in tokens
        assert tokens["email"] == "u@example.com"
        assert tokens["scopes"] == "openid email"

        # The refresh token is reachable through the dedicated helper
        # that always reads from the encrypted DB (no in-memory cache).
        assert await auth_service.get_oauth_refresh_token("google") == "refresh-1"

    async def test_remove_oauth_tokens(self, auth_service):
        await auth_service.store_oauth_tokens("google", "a", "r")
        ok = await auth_service.remove_oauth_tokens("google")
        assert ok is True
        assert await auth_service.get_oauth_tokens("google") is None

    async def test_oauth_cache_isolated_from_api_key_cache(self, auth_service):
        # Invariants 3 and 8: same provider name in both caches must not collide
        await auth_service.store_api_key("google", "api-key-value", models=[])
        await auth_service.store_oauth_tokens("google", "oauth-access", "oauth-refresh")

        assert await auth_service.get_api_key("google") == "api-key-value"

        tokens = await auth_service.get_oauth_tokens("google")
        assert tokens["access_token"] == "oauth-access"

    async def test_get_oauth_tokens_missing_provider_returns_none(self, auth_service):
        assert await auth_service.get_oauth_tokens("never-stored") is None
