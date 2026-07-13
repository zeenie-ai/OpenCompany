"""Separate encrypted database for API keys and OAuth tokens.

This module provides a dedicated SQLite database for storing sensitive credentials
with field-level Fernet encryption. Credentials are encrypted before storage and
decrypted on retrieval.

The database is separate from the main application database (workflow.db) to:
1. Isolate sensitive data from application data
2. Allow different backup/security policies
3. Enable easier credential rotation and management
"""

import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, JSON
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Field, SQLModel, select

from core.encryption import EncryptionService

logger = logging.getLogger(__name__)


# --- SQLModel Definitions ---


class CredentialsMetadata(SQLModel, table=True):
    """Stores encryption salt and other metadata."""

    __tablename__ = "credentials_metadata"

    key: str = Field(primary_key=True, max_length=50)
    value: str = Field(max_length=500)


class EncryptedAPIKey(SQLModel, table=True):
    """API keys with encrypted storage."""

    __tablename__ = "encrypted_api_keys"

    id: str = Field(primary_key=True, max_length=255)  # {session_id}_{provider}
    provider: str = Field(max_length=50, index=True)
    session_id: str = Field(default="default", max_length=255)
    key_encrypted: str = Field(max_length=2000)  # Fernet token
    key_hash: str = Field(max_length=64, index=True)  # SHA256[:16] for lookup
    models: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    is_valid: bool = Field(default=True)
    last_validated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EncryptedOAuthToken(SQLModel, table=True):
    """OAuth tokens with encrypted storage."""

    __tablename__ = "oauth_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(max_length=50, index=True)  # 'google', 'twitter'
    customer_id: str = Field(default="owner", max_length=255, index=True)
    email: Optional[str] = Field(default=None, max_length=255)
    name: Optional[str] = Field(default=None, max_length=255)
    access_token_encrypted: str = Field(max_length=4000)
    refresh_token_encrypted: str = Field(max_length=4000)
    token_expiry: Optional[datetime] = Field(default=None)
    scopes: Optional[str] = Field(default=None, max_length=2000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --- Database Class ---


class CredentialsDatabase:
    """
    Async SQLite database with field-level Fernet encryption.

    Provides secure storage for API keys and OAuth tokens in a separate
    database file from the main application data.

    Usage:
        encryption = EncryptionService()
        credentials_db = CredentialsDatabase("credentials.db", encryption)

        # On app startup
        salt = await credentials_db.initialize()

        # On user login
        encryption.initialize(password, salt)

        # Store/retrieve credentials
        await credentials_db.save_api_key("openai", "sk-xxx", ["gpt-4"])
        api_key = await credentials_db.get_api_key("openai")
    """

    def __init__(self, db_path: str, encryption: EncryptionService):
        """
        Initialize credentials database.

        Args:
            db_path: Path to SQLite database file (e.g., "credentials.db")
            encryption: EncryptionService instance for encrypt/decrypt operations
        """
        self.db_path = db_path
        self.encryption = encryption
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            echo=False,
        )
        self._session_factory = sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self._salt: Optional[bytes] = None

    async def initialize(self) -> bytes:
        """
        Initialize database tables and return encryption salt.

        Creates tables if they don't exist. Generates and stores a new salt
        on first run, or retrieves existing salt on subsequent runs.

        Returns:
            Salt bytes for PBKDF2 key derivation
        """
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # Get or create salt
        salt_hex = await self._get_metadata("encryption_salt")
        if not salt_hex:
            new_salt = EncryptionService.generate_salt()
            await self._set_metadata("encryption_salt", new_salt.hex())
            self._salt = new_salt
            logger.info("Generated new encryption salt for credentials database")
        else:
            self._salt = bytes.fromhex(salt_hex)
            logger.debug("Loaded existing encryption salt")

        return self._salt

    def get_salt(self) -> Optional[bytes]:
        """Get cached salt (available after initialize())."""
        return self._salt

    @asynccontextmanager
    async def get_session(self):
        """Get async database session."""
        async with self._session_factory() as session:
            yield session

    # --- Metadata Operations ---

    async def _get_metadata(self, key: str) -> Optional[str]:
        """Get metadata value by key."""
        async with self.get_session() as session:
            result = await session.execute(select(CredentialsMetadata).where(CredentialsMetadata.key == key))
            row = result.scalars().first()
            return row.value if row else None

    async def _set_metadata(self, key: str, value: str) -> None:
        """Set metadata value."""
        async with self.get_session() as session:
            existing = await session.execute(select(CredentialsMetadata).where(CredentialsMetadata.key == key))
            row = existing.scalars().first()
            if row:
                row.value = value
            else:
                session.add(CredentialsMetadata(key=key, value=value))
            await session.commit()

    # --- API Key Operations ---

    async def save_api_key(
        self,
        provider: str,
        api_key: str,
        models: Optional[List[str]] = None,
        session_id: str = "default",
        model_params: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        """
        Save encrypted API key.

        Args:
            provider: Provider name (e.g., "openai", "anthropic")
            api_key: Plaintext API key to encrypt and store
            models: List of available models for this key
            session_id: Session identifier (default: "default")
            model_params: Optional per-model parameters keyed by model id, e.g.
                ``{"qwen2.5-7b": {"context_length": 8192}}``. Used by local
                providers (Ollama, LM Studio) where the context window depends
                on what the user has loaded — the value is fetched from the
                official SDK during validation. Stored alongside the model
                list under the same JSON column (``model_params`` subkey) so
                ``model_registry.get_context_length()`` can read the real
                ctx instead of a JSON default. Cloud providers leave this
                empty — their per-model params live in ``model_registry.json``
                from OpenRouter.
        """
        key_id = f"{session_id}_{provider}"
        encrypted = self.encryption.encrypt(api_key)
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
        models_blob: Dict[str, Any] = {"models": models or []}
        if model_params:
            models_blob["model_params"] = model_params

        async with self.get_session() as session:
            existing = await session.get(EncryptedAPIKey, key_id)
            now = datetime.now(timezone.utc)

            if existing:
                existing.key_encrypted = encrypted
                existing.key_hash = key_hash
                existing.models = models_blob
                existing.is_valid = True
                existing.last_validated = now
                existing.updated_at = now
            else:
                session.add(
                    EncryptedAPIKey(
                        id=key_id,
                        provider=provider,
                        session_id=session_id,
                        key_encrypted=encrypted,
                        key_hash=key_hash,
                        models=models_blob,
                        is_valid=True,
                        last_validated=now,
                    )
                )
            await session.commit()
            logger.debug(f"Saved encrypted API key for provider: {provider}")

    async def get_api_key_model_params(self, provider: str, session_id: str = "default") -> Dict[str, Dict[str, Any]]:
        """Return the per-model param map stored alongside the model list.

        Empty dict if the provider has no entry, no params were stored,
        or the row predates the ``model_params`` column. The runtime
        path (``model_registry.get_context_length`` etc.) consults this
        before falling back to JSON defaults so local-LLM context comes
        from the actual loaded model, not a guess.
        """
        key_id = f"{session_id}_{provider}"
        async with self.get_session() as session:
            row = await session.get(EncryptedAPIKey, key_id)
            if not row or not row.models:
                return {}
            params = row.models.get("model_params") or {}
            return params if isinstance(params, dict) else {}

    async def get_api_key(self, provider: str, session_id: str = "default") -> Optional[str]:
        """
        Get decrypted API key.

        Args:
            provider: Provider name
            session_id: Session identifier

        Returns:
            Decrypted API key or None if not found
        """
        key_id = f"{session_id}_{provider}"
        async with self.get_session() as session:
            row = await session.get(EncryptedAPIKey, key_id)
            if not row:
                return None
            try:
                return self.encryption.decrypt(row.key_encrypted)
            except ValueError as e:
                logger.error(f"Failed to decrypt API key for {provider}: {e}")
                return None

    async def get_api_key_info(self, provider: str, session_id: str = "default") -> Optional[Dict[str, Any]]:
        """
        Get API key metadata (without decrypting the key).

        Args:
            provider: Provider name
            session_id: Session identifier

        Returns:
            Dict with models, is_valid, last_validated, or None if not found
        """
        key_id = f"{session_id}_{provider}"
        async with self.get_session() as session:
            row = await session.get(EncryptedAPIKey, key_id)
            if not row:
                return None
            return {
                "provider": row.provider,
                "models": row.models.get("models", []) if row.models else [],
                "is_valid": row.is_valid,
                "last_validated": row.last_validated,
            }

    async def delete_api_key(self, provider: str, session_id: str = "default") -> bool:
        """
        Delete API key.

        Args:
            provider: Provider name
            session_id: Session identifier

        Returns:
            True if key was deleted, False if not found
        """
        key_id = f"{session_id}_{provider}"
        async with self.get_session() as session:
            row = await session.get(EncryptedAPIKey, key_id)
            if row:
                await session.delete(row)
                await session.commit()
                logger.debug(f"Deleted API key for provider: {provider}")
                return True
            return False

    async def list_api_keys(self, session_id: str = "default") -> List[str]:
        """
        List all stored provider names for a session.

        Args:
            session_id: Session identifier

        Returns:
            List of provider names
        """
        async with self.get_session() as session:
            result = await session.execute(select(EncryptedAPIKey.provider).where(EncryptedAPIKey.session_id == session_id))
            return [row[0] for row in result.all()]

    async def get_api_key_models(self, provider: str, session_id: str = "default") -> List[str]:
        """
        Get stored models for an API key.

        Args:
            provider: Provider name
            session_id: Session identifier

        Returns:
            List of model names or empty list if not found
        """
        key_id = f"{session_id}_{provider}"
        async with self.get_session() as session:
            row = await session.get(EncryptedAPIKey, key_id)
            if not row or not row.models:
                return []
            return row.models.get("models", [])

    # --- OAuth Token Operations ---

    async def save_oauth_tokens(
        self,
        provider: str,
        access_token: str,
        refresh_token: str,
        email: Optional[str] = None,
        name: Optional[str] = None,
        expiry: Optional[datetime] = None,
        scopes: Optional[str] = None,
        customer_id: str = "owner",
    ) -> None:
        """
        Save encrypted OAuth tokens.

        Args:
            provider: OAuth provider (e.g., "google", "twitter")
            access_token: OAuth access token
            refresh_token: OAuth refresh token
            email: User email from OAuth provider
            name: User display name
            expiry: Token expiration datetime
            scopes: Comma-separated granted scopes
            customer_id: Customer identifier (default: "owner")
        """
        encrypted_access = self.encryption.encrypt(access_token)
        encrypted_refresh = self.encryption.encrypt(refresh_token)

        async with self.get_session() as session:
            existing = await session.execute(
                select(EncryptedOAuthToken).where(
                    EncryptedOAuthToken.provider == provider,
                    EncryptedOAuthToken.customer_id == customer_id,
                )
            )
            row = existing.scalars().first()
            now = datetime.now(timezone.utc)

            if row:
                row.access_token_encrypted = encrypted_access
                row.refresh_token_encrypted = encrypted_refresh
                row.email = email
                row.name = name
                row.token_expiry = expiry
                row.scopes = scopes
                row.updated_at = now
            else:
                session.add(
                    EncryptedOAuthToken(
                        provider=provider,
                        customer_id=customer_id,
                        email=email,
                        name=name,
                        access_token_encrypted=encrypted_access,
                        refresh_token_encrypted=encrypted_refresh,
                        token_expiry=expiry,
                        scopes=scopes,
                    )
                )
            await session.commit()
            logger.debug(f"Saved encrypted OAuth tokens for provider: {provider}")

    async def get_oauth_tokens(self, provider: str, customer_id: str = "owner") -> Optional[Dict[str, Any]]:
        """
        Get decrypted OAuth tokens.

        Args:
            provider: OAuth provider
            customer_id: Customer identifier

        Returns:
            Dict with access_token, refresh_token, email, name, etc.
            or None if not found
        """
        async with self.get_session() as session:
            result = await session.execute(
                select(EncryptedOAuthToken).where(
                    EncryptedOAuthToken.provider == provider,
                    EncryptedOAuthToken.customer_id == customer_id,
                )
            )
            row = result.scalars().first()
            if not row:
                return None

            try:
                return {
                    "access_token": self.encryption.decrypt(row.access_token_encrypted),
                    "refresh_token": self.encryption.decrypt(row.refresh_token_encrypted),
                    "email": row.email,
                    "name": row.name,
                    "token_expiry": row.token_expiry,
                    "scopes": row.scopes,
                }
            except ValueError as e:
                logger.error(f"Failed to decrypt OAuth tokens for {provider}: {e}")
                return None

    async def delete_oauth_tokens(self, provider: str, customer_id: str = "owner") -> bool:
        """
        Delete OAuth tokens.

        Args:
            provider: OAuth provider
            customer_id: Customer identifier

        Returns:
            True if tokens were deleted, False if not found
        """
        async with self.get_session() as session:
            result = await session.execute(
                select(EncryptedOAuthToken).where(
                    EncryptedOAuthToken.provider == provider,
                    EncryptedOAuthToken.customer_id == customer_id,
                )
            )
            row = result.scalars().first()
            if row:
                await session.delete(row)
                await session.commit()
                logger.debug(f"Deleted OAuth tokens for provider: {provider}")
                return True
            return False

    async def list_oauth_providers(self, customer_id: str = "owner") -> List[str]:
        """
        List all OAuth providers with stored tokens.

        Args:
            customer_id: Customer identifier

        Returns:
            List of provider names
        """
        async with self.get_session() as session:
            result = await session.execute(select(EncryptedOAuthToken.provider).where(EncryptedOAuthToken.customer_id == customer_id))
            return [row[0] for row in result.all()]
