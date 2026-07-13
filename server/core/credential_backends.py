"""
Multi-backend credential storage abstraction.

Supports three backends for different deployment scenarios:
- Fernet: Default, uses encrypted SQLite database (Docker, local dev)
- Keyring: OS-native credential storage (Windows Credential Locker, macOS Keychain, Linux Secret Service)
- AWS: AWS Secrets Manager for cloud production deployments

Usage:
    backend = create_backend(settings, credentials_db)
    await backend.store("api_key", "sk-xxx", {"provider": "openai"})
    value = await backend.retrieve("api_key")
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CredentialBackend(ABC):
    """Abstract base class for credential storage backends."""

    @abstractmethod
    async def store(self, key: str, value: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Store a credential with optional metadata."""
        pass

    @abstractmethod
    async def retrieve(self, key: str) -> Optional[str]:
        """Retrieve a credential by key."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete a credential by key."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is available/configured."""
        pass


class FernetBackend(CredentialBackend):
    """
    Fernet-encrypted SQLite backend (default).

    Uses the existing CredentialsDatabase for encrypted storage.
    Best for: Docker deployments, local development.
    """

    def __init__(self, credentials_db):
        self._credentials_db = credentials_db

    async def store(self, key: str, value: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Store credential in encrypted SQLite database."""
        try:
            # Parse key format: {type}_{provider}_{session_id} or {type}_{provider}_{customer_id}
            parts = key.split("_", 2)
            if len(parts) >= 2:
                key_type = parts[0]  # api_key or oauth
                provider = parts[1]

                if key_type == "apikey":
                    session_id = parts[2] if len(parts) > 2 else "default"
                    models = metadata.get("models", []) if metadata else []
                    await self._credentials_db.save_api_key(provider, value, models, session_id)
                elif key_type == "oauth":
                    customer_id = parts[2] if len(parts) > 2 else "owner"
                    await self._credentials_db.save_oauth_tokens(
                        provider=provider,
                        access_token=value,
                        refresh_token=metadata.get("refresh_token", "") if metadata else "",
                        email=metadata.get("email") if metadata else None,
                        name=metadata.get("name") if metadata else None,
                        scopes=metadata.get("scopes") if metadata else None,
                        customer_id=customer_id,
                    )
                else:
                    # Generic key storage
                    await self._credentials_db.save_api_key(key, value, [], "default")
                return True
            else:
                # Simple key-value storage
                await self._credentials_db.save_api_key(key, value, [], "default")
                return True
        except Exception as e:
            logger.error(f"FernetBackend store failed: {e}")
            return False

    async def retrieve(self, key: str) -> Optional[str]:
        """Retrieve credential from encrypted SQLite database."""
        try:
            parts = key.split("_", 2)
            if len(parts) >= 2:
                key_type = parts[0]
                provider = parts[1]

                if key_type == "apikey":
                    session_id = parts[2] if len(parts) > 2 else "default"
                    return await self._credentials_db.get_api_key(provider, session_id)
                elif key_type == "oauth":
                    customer_id = parts[2] if len(parts) > 2 else "owner"
                    tokens = await self._credentials_db.get_oauth_tokens(provider, customer_id)
                    return tokens.get("access_token") if tokens else None
            else:
                return await self._credentials_db.get_api_key(key, "default")
        except Exception as e:
            logger.error(f"FernetBackend retrieve failed: {e}")
            return None

    async def delete(self, key: str) -> bool:
        """Delete credential from encrypted SQLite database."""
        try:
            parts = key.split("_", 2)
            if len(parts) >= 2:
                key_type = parts[0]
                provider = parts[1]

                if key_type == "apikey":
                    session_id = parts[2] if len(parts) > 2 else "default"
                    await self._credentials_db.delete_api_key(provider, session_id)
                elif key_type == "oauth":
                    customer_id = parts[2] if len(parts) > 2 else "owner"
                    await self._credentials_db.delete_oauth_tokens(provider, customer_id)
                return True
            else:
                await self._credentials_db.delete_api_key(key, "default")
                return True
        except Exception as e:
            logger.error(f"FernetBackend delete failed: {e}")
            return False

    def is_available(self) -> bool:
        """Fernet backend is always available."""
        return True


class KeyringBackend(CredentialBackend):
    """
    OS-native credential storage using keyring library.

    Uses:
    - Windows: Credential Locker
    - macOS: Keychain
    - Linux: Secret Service (GNOME Keyring, KWallet)

    Best for: Desktop deployments, single-user installations.
    """

    SERVICE_NAME = "OpenCompany"
    LEGACY_SERVICE_NAME = "MachinaOS"

    def __init__(self):
        self._keyring = None
        self._available = False
        try:
            import keyring

            self._keyring = keyring
            # Test if keyring is working
            self._keyring.get_password(self.SERVICE_NAME, "__test__")
            self._available = True
            logger.info("KeyringBackend initialized successfully")
        except ImportError:
            logger.warning("keyring library not installed, KeyringBackend unavailable")
        except Exception as e:
            logger.warning(f"keyring not working: {e}")

    async def store(self, key: str, value: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Store credential in OS keyring."""
        if not self._available:
            return False
        try:
            # Store value
            self._keyring.set_password(self.SERVICE_NAME, key, value)
            # Store metadata separately if provided
            if metadata:
                self._keyring.set_password(self.SERVICE_NAME, f"{key}__metadata", json.dumps(metadata))
            return True
        except Exception as e:
            logger.error(f"KeyringBackend store failed: {e}")
            return False

    async def retrieve(self, key: str) -> Optional[str]:
        """Retrieve a credential, migrating the legacy service namespace."""
        if not self._available:
            return None
        try:
            value = self._keyring.get_password(self.SERVICE_NAME, key)
            if value is not None:
                return value

            value = self._keyring.get_password(self.LEGACY_SERVICE_NAME, key)
            if value is not None:
                self._keyring.set_password(self.SERVICE_NAME, key, value)
                metadata = self._keyring.get_password(self.LEGACY_SERVICE_NAME, f"{key}__metadata")
                if metadata is not None:
                    self._keyring.set_password(self.SERVICE_NAME, f"{key}__metadata", metadata)
            return value
        except Exception as e:
            logger.error(f"KeyringBackend retrieve failed: {e}")
            return None

    async def delete(self, key: str) -> bool:
        """Delete credential from OS keyring."""
        if not self._available:
            return False
        deleted = False
        for service_name in (self.SERVICE_NAME, self.LEGACY_SERVICE_NAME):
            for item_key in (key, f"{key}__metadata"):
                try:
                    self._keyring.delete_password(service_name, item_key)
                    deleted = True
                except Exception:
                    pass  # Namespace/key may not exist.
        return deleted

    def is_available(self) -> bool:
        """Check if keyring is available."""
        return self._available


class AWSSecretsBackend(CredentialBackend):
    """
    AWS Secrets Manager backend for cloud production deployments.

    Requires:
    - boto3 library
    - AWS credentials configured (IAM role, env vars, or ~/.aws/credentials)
    - aws_secret_arn setting configured

    Best for: AWS production deployments, multi-instance applications.
    """

    def __init__(self, secret_arn: Optional[str] = None, region: Optional[str] = None):
        self._client = None
        self._secret_arn = secret_arn
        self._region = region or "us-east-1"
        self._available = False
        self._cache: Dict[str, str] = {}  # Local cache for performance

        if not secret_arn:
            logger.debug("AWSSecretsBackend: No secret_arn configured")
            return

        try:
            import boto3

            self._client = boto3.client("secretsmanager", region_name=self._region)
            # Test connection
            self._client.describe_secret(SecretId=self._secret_arn)
            self._available = True
            logger.info(f"AWSSecretsBackend initialized with secret: {self._secret_arn}")
        except ImportError:
            logger.warning("boto3 library not installed, AWSSecretsBackend unavailable")
        except Exception as e:
            logger.warning(f"AWS Secrets Manager not accessible: {e}")

    async def store(self, key: str, value: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Store credential in AWS Secrets Manager."""
        if not self._available:
            return False
        try:
            # Get current secret value
            current = await self._get_all_secrets()

            # Update with new key
            current[key] = value
            if metadata:
                current[f"{key}__metadata"] = json.dumps(metadata)

            # Put updated secret
            self._client.put_secret_value(SecretId=self._secret_arn, SecretString=json.dumps(current))

            # Update cache
            self._cache[key] = value
            return True
        except Exception as e:
            logger.error(f"AWSSecretsBackend store failed: {e}")
            return False

    async def retrieve(self, key: str) -> Optional[str]:
        """Retrieve credential from AWS Secrets Manager."""
        if not self._available:
            return None

        # Check cache first
        if key in self._cache:
            return self._cache[key]

        try:
            secrets = await self._get_all_secrets()
            value = secrets.get(key)
            if value:
                self._cache[key] = value
            return value
        except Exception as e:
            logger.error(f"AWSSecretsBackend retrieve failed: {e}")
            return None

    async def delete(self, key: str) -> bool:
        """Delete credential from AWS Secrets Manager."""
        if not self._available:
            return False
        try:
            current = await self._get_all_secrets()

            # Remove key and metadata
            current.pop(key, None)
            current.pop(f"{key}__metadata", None)

            # Put updated secret
            self._client.put_secret_value(SecretId=self._secret_arn, SecretString=json.dumps(current))

            # Update cache
            self._cache.pop(key, None)
            return True
        except Exception as e:
            logger.error(f"AWSSecretsBackend delete failed: {e}")
            return False

    async def _get_all_secrets(self) -> Dict[str, str]:
        """Get all secrets as a dictionary."""
        try:
            response = self._client.get_secret_value(SecretId=self._secret_arn)
            return json.loads(response.get("SecretString", "{}"))
        except Exception:
            return {}

    def is_available(self) -> bool:
        """Check if AWS Secrets Manager is available."""
        return self._available


def create_backend(settings, credentials_db=None) -> CredentialBackend:
    """
    Factory function to create the appropriate credential backend.

    Priority:
    1. Use explicitly configured backend from settings.credential_backend
    2. Auto-detect available backends with fallback to Fernet

    Args:
        settings: Application settings with credential_backend, aws_secret_arn, etc.
        credentials_db: CredentialsDatabase instance (required for Fernet backend)

    Returns:
        Configured CredentialBackend instance
    """
    backend_type = getattr(settings, "credential_backend", "fernet").lower()

    if backend_type == "aws":
        aws_arn = getattr(settings, "aws_secret_arn", None)
        aws_region = getattr(settings, "aws_region", None)
        backend = AWSSecretsBackend(secret_arn=aws_arn, region=aws_region)
        if backend.is_available():
            logger.info("Using AWSSecretsBackend for credential storage")
            return backend
        logger.warning("AWS Secrets Manager not available, falling back to Fernet")

    elif backend_type == "keyring":
        backend = KeyringBackend()
        if backend.is_available():
            logger.info("Using KeyringBackend for credential storage")
            return backend
        logger.warning("OS Keyring not available, falling back to Fernet")

    # Default: Fernet backend
    if credentials_db is None:
        raise ValueError("credentials_db required for Fernet backend")

    logger.info("Using FernetBackend for credential storage")
    return FernetBackend(credentials_db)
