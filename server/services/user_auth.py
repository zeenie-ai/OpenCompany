"""User authentication service with JWT handling and encryption initialization."""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

import jwt
from jwt import PyJWTError
from sqlmodel import select

from core.config import Settings
from core.database import Database
from core.encryption import EncryptionService
from core.credentials_database import CredentialsDatabase
from models.auth import User

logger = logging.getLogger(__name__)


class UserAuthService:
    """Handles user authentication, registration, and JWT token management."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        encryption: EncryptionService,
        credentials_db: CredentialsDatabase,
    ):
        self.database = database
        self.settings = settings
        self.encryption = encryption
        self.credentials_db = credentials_db
        self._algorithm = "HS256"

    async def get_user_count(self) -> int:
        """Get total number of users."""
        async with self.database.get_session() as session:
            result = await session.execute(select(User))
            return len(result.scalars().all())

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email address."""
        async with self.database.get_session() as session:
            result = await session.execute(select(User).where(User.email == email.lower().strip()))
            return result.scalars().first()

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        async with self.database.get_session() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            return result.scalars().first()

    async def can_register(self) -> bool:
        """Check if registration is allowed based on auth mode."""
        if self.settings.auth_mode == "multi":
            return True
        # Single-owner mode: only allow if no users exist
        count = await self.get_user_count()
        return count == 0

    async def register(self, email: str, password: str, display_name: str) -> tuple[Optional[User], Optional[str]]:
        """
        Register a new user.
        Returns (user, None) on success, (None, error_message) on failure.
        """
        # Check if registration is allowed
        if not await self.can_register():
            if self.settings.auth_mode == "single":
                return None, "Registration disabled - owner account already exists"
            return None, "Registration is currently disabled"

        # Check if email already exists
        existing = await self.get_user_by_email(email)
        if existing:
            return None, "Email already registered"

        # Validate password strength
        if len(password) < 8:
            return None, "Password must be at least 8 characters"

        # Determine if this is the owner (first user in single-owner mode)
        is_owner = self.settings.auth_mode == "single" and await self.get_user_count() == 0

        # Create user
        user = User.create(
            email=email,
            password=password,
            display_name=display_name,
            is_owner=is_owner,
        )

        async with self.database.get_session() as session:
            session.add(user)
            await session.commit()
            await session.refresh(user)

        logger.info(f"User registered: {email} (owner={is_owner})")

        return user, None

    async def login(self, email: str, password: str) -> tuple[Optional[User], Optional[str]]:
        """
        Authenticate user and return user object.
        Returns (user, None) on success, (None, error_message) on failure.
        """
        user = await self.get_user_by_email(email)
        if not user:
            return None, "Invalid email or password"

        if not user.is_active:
            return None, "Account is disabled"

        if not user.verify_password(password):
            return None, "Invalid email or password"

        # Update last login
        async with self.database.get_session() as session:
            result = await session.execute(select(User).where(User.id == user.id))
            db_user = result.scalars().first()
            if db_user:
                db_user.last_login = datetime.now(timezone.utc)
                await session.commit()

        logger.info(f"User logged in: {email}")
        return user, None

    def logout(self) -> None:
        """Log out user. Encryption key persists (server-scoped, not session-scoped)."""
        logger.debug("User logged out")

    def create_access_token(self, user: User) -> str:
        """Create JWT access token for user."""
        expire = datetime.now(timezone.utc) + timedelta(minutes=self.settings.jwt_expire_minutes)
        payload = {
            "sub": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "is_owner": user.is_owner,
            "exp": expire,
            "iat": datetime.now(timezone.utc),
        }
        return jwt.encode(payload, self.settings.jwt_secret_key, algorithm=self._algorithm)

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify JWT token and return payload.
        Returns None if token is invalid or expired.
        """
        try:
            payload = jwt.decode(token, self.settings.jwt_secret_key, algorithms=[self._algorithm])
            return payload
        except PyJWTError as e:
            logger.debug(f"Token verification failed: {e}")
            return None

    async def get_current_user(self, token: str) -> Optional[User]:
        """Get current user from token."""
        payload = self.verify_token(token)
        if not payload:
            return None

        user_id = payload.get("sub")
        if not user_id:
            return None

        return await self.get_user_by_id(int(user_id))

    def get_auth_status(self) -> Dict[str, Any]:
        """Get authentication status and mode info."""
        return {
            "auth_mode": self.settings.auth_mode,
            "registration_enabled": self.settings.auth_mode == "multi",
        }

    def is_encryption_initialized(self) -> bool:
        """Check if encryption is ready for use."""
        return self.encryption.is_initialized()
