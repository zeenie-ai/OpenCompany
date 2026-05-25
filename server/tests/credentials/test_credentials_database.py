"""Tests for CredentialsDatabase (encrypted SQLite storage).

Locks in the two-table separation (invariants 3 and 8 in docs-internal/credentials_panel.md):
  - get_api_key for a provider only stored in the OAuth table returns None
  - get_oauth_tokens for a provider only stored as API key returns None
  - Stored ciphertext is never plaintext
"""

from __future__ import annotations

import pytest

from core.credentials_database import (
    CredentialsDatabase,
    EncryptedAPIKey,
    EncryptedOAuthToken,
)
from sqlmodel import select

pytestmark = pytest.mark.credentials


class TestApiKeyCrud:
    async def test_save_then_get_roundtrip(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_api_key("openai", "sk-test-key", models=["gpt-4"])
        assert await credentials_db.get_api_key("openai") == "sk-test-key"

    async def test_get_nonexistent_returns_none(self, credentials_db: CredentialsDatabase):
        assert await credentials_db.get_api_key("nonexistent") is None

    async def test_stored_value_is_encrypted(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_api_key("openai", "sk-very-secret-plaintext")

        async with credentials_db.get_session() as session:
            row = await session.get(EncryptedAPIKey, "default_openai")
            assert row is not None
            assert "sk-very-secret-plaintext" not in row.key_encrypted
            assert row.key_encrypted != "sk-very-secret-plaintext"

    async def test_save_overwrites_existing_key(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_api_key("openai", "sk-old", models=["a"])
        await credentials_db.save_api_key("openai", "sk-new", models=["b", "c"])

        assert await credentials_db.get_api_key("openai") == "sk-new"
        models = await credentials_db.get_api_key_models("openai")
        assert models == ["b", "c"]

    async def test_delete_removes_key(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_api_key("openai", "sk-test")
        deleted = await credentials_db.delete_api_key("openai")
        assert deleted is True
        assert await credentials_db.get_api_key("openai") is None

    async def test_delete_nonexistent_returns_false(self, credentials_db: CredentialsDatabase):
        assert await credentials_db.delete_api_key("nonexistent") is False

    async def test_session_isolation(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_api_key("openai", "sk-default", session_id="default")
        await credentials_db.save_api_key("openai", "sk-bob", session_id="bob")

        assert await credentials_db.get_api_key("openai", "default") == "sk-default"
        assert await credentials_db.get_api_key("openai", "bob") == "sk-bob"

    async def test_list_api_keys_returns_providers_only_for_session(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_api_key("openai", "k1")
        await credentials_db.save_api_key("anthropic", "k2")
        await credentials_db.save_api_key("openai", "k3", session_id="other")

        listed = await credentials_db.list_api_keys()
        assert sorted(listed) == ["anthropic", "openai"]

    async def test_get_api_key_info_does_not_decrypt(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_api_key("openai", "sk-secret", models=["gpt-4", "gpt-3.5"])
        info = await credentials_db.get_api_key_info("openai")
        assert info is not None
        assert info["provider"] == "openai"
        assert info["models"] == ["gpt-4", "gpt-3.5"]
        assert info["is_valid"] is True
        # Crucially: no key/key_encrypted exposed
        assert "key" not in info
        assert "key_encrypted" not in info


class TestOAuthCrud:
    async def test_save_then_get_roundtrip(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_oauth_tokens(
            provider="google",
            access_token="ya29.access-token",
            refresh_token="1//refresh-token",
            email="user@example.com",
            name="Test User",
            scopes="openid email profile",
        )
        tokens = await credentials_db.get_oauth_tokens("google")
        assert tokens is not None
        assert tokens["access_token"] == "ya29.access-token"
        assert tokens["refresh_token"] == "1//refresh-token"
        assert tokens["email"] == "user@example.com"
        assert tokens["name"] == "Test User"
        assert tokens["scopes"] == "openid email profile"

    async def test_stored_tokens_are_encrypted(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_oauth_tokens(
            provider="twitter",
            access_token="plaintext-access",
            refresh_token="plaintext-refresh",
        )

        async with credentials_db.get_session() as session:
            result = await session.execute(select(EncryptedOAuthToken).where(EncryptedOAuthToken.provider == "twitter"))
            row = result.scalars().first()
            assert row is not None
            assert "plaintext-access" not in row.access_token_encrypted
            assert "plaintext-refresh" not in row.refresh_token_encrypted

    async def test_save_overwrites_existing_for_provider(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_oauth_tokens("google", "a1", "r1", email="old@x.com")
        await credentials_db.save_oauth_tokens("google", "a2", "r2", email="new@x.com")

        tokens = await credentials_db.get_oauth_tokens("google")
        assert tokens["access_token"] == "a2"
        assert tokens["refresh_token"] == "r2"
        assert tokens["email"] == "new@x.com"

    async def test_delete_removes_tokens(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_oauth_tokens("google", "a", "r")
        assert await credentials_db.delete_oauth_tokens("google") is True
        assert await credentials_db.get_oauth_tokens("google") is None

    async def test_delete_nonexistent_returns_false(self, credentials_db: CredentialsDatabase):
        assert await credentials_db.delete_oauth_tokens("nonexistent") is False

    async def test_customer_id_isolation(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_oauth_tokens("google", "a-owner", "r-owner", customer_id="owner")
        await credentials_db.save_oauth_tokens("google", "a-cust", "r-cust", customer_id="cust-1")

        owner = await credentials_db.get_oauth_tokens("google", "owner")
        cust = await credentials_db.get_oauth_tokens("google", "cust-1")
        assert owner["access_token"] == "a-owner"
        assert cust["access_token"] == "a-cust"

    async def test_list_oauth_providers(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_oauth_tokens("google", "a", "r")
        await credentials_db.save_oauth_tokens("twitter", "a", "r")

        providers = await credentials_db.list_oauth_providers()
        assert sorted(providers) == ["google", "twitter"]


class TestTwoTableSeparation:
    """Invariants 3 and 8 -- the two storage systems must not bleed into each other."""

    async def test_oauth_tokens_invisible_via_get_api_key(self, credentials_db: CredentialsDatabase):
        await credentials_db.save_oauth_tokens("google", "access", "refresh")
        # An API-key lookup for the same provider name finds nothing
        assert await credentials_db.get_api_key("google") is None

    async def test_api_key_invisible_via_get_oauth_tokens(self, credentials_db: CredentialsDatabase):
        # google_client_id is stored as an API key (Pattern C)
        await credentials_db.save_api_key("google_client_id", "client-abc")
        # Looking it up as an OAuth token finds nothing
        assert await credentials_db.get_oauth_tokens("google_client_id") is None

    async def test_provider_can_have_both_api_key_and_oauth_tokens(self, credentials_db: CredentialsDatabase):
        # Real-world: google_client_id (API key) AND google OAuth tokens coexist
        await credentials_db.save_api_key("google_client_id", "client-id")
        await credentials_db.save_api_key("google_client_secret", "client-sec")
        await credentials_db.save_oauth_tokens("google", "access", "refresh")

        assert await credentials_db.get_api_key("google_client_id") == "client-id"
        assert await credentials_db.get_api_key("google_client_secret") == "client-sec"

        tokens = await credentials_db.get_oauth_tokens("google")
        assert tokens["access_token"] == "access"
        assert tokens["refresh_token"] == "refresh"


class TestSalt:
    async def test_initialize_persists_salt(self, tmp_path):
        from core.encryption import EncryptionService

        db_path = tmp_path / "salt-test.db"

        # First instance: generates salt
        enc1 = EncryptionService()
        db1 = CredentialsDatabase(str(db_path), enc1)
        salt1 = await db1.initialize()
        await db1.engine.dispose()

        # Second instance: must load the same salt
        enc2 = EncryptionService()
        db2 = CredentialsDatabase(str(db_path), enc2)
        salt2 = await db2.initialize()
        await db2.engine.dispose()

        assert salt1 == salt2
        assert len(salt1) == 32
