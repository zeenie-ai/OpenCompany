"""User authentication service: registration, login and JWT handling.

It does not initialise, derive or clear the credential encryption key. That
key is server-scoped and initialised once in the ``main.py`` lifespan from
``API_KEY_ENCRYPTION_KEY``. ``UserAuthService`` only exposes the read-only
``is_encryption_initialized()`` check.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

import bcrypt
import jwt
from jwt import PyJWTError
from sqlalchemy.exc import IntegrityError
from sqlmodel import func, select

from core.config import Settings
from core.database import Database
from core.encryption import EncryptionService
from core.credentials_database import CredentialsDatabase
from models.auth import User

logger = logging.getLogger(__name__)

# One generic message for every credential rejection. Distinct messages
# ("Account is disabled") confirm an address exists, which is an account
# enumeration oracle on a public, unauthenticated endpoint.
_INVALID_CREDENTIALS = "Invalid email or password"

# Registration refusals, worded for the owner: the sign-in page shows them as
# they are.
_EMAIL_TAKEN = "An account with this email already exists."
_OWNER_EXISTS = "This OpenCompany already has its owner. Sign in instead."

# Compared against when no user matches, so the unknown-email path costs the
# same ~50-300ms of bcrypt as the known-email path. Without it, response time
# alone reveals which addresses are registered.
#
# Generated out-of-band from a discarded random string; deliberately NOT
# computed at import via bcrypt.gensalt(), which would add a full KDF round
# to every process start (startup timing is instrumented -- see _startup_log).
_DUMMY_PASSWORD_HASH = b"$2b$12$C6UzMDM.H6dfI/f/IKcEe.6Vc/qGgQEHOQKMxLbLQ3vRhBnGYDbXK"


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
        """Get total number of users.

        COUNT(*) rather than materialising every row -- `can_register` calls
        this on every `/api/auth/status` poll in single-owner mode.
        """
        async with self.database.get_session() as session:
            result = await session.execute(select(func.count()).select_from(User))
            return int(result.scalar_one())

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

        The eligibility checks and the INSERT share ONE session. They used to
        span four, so two concurrent first-registrations could both observe
        "no users exist" and both be granted ``is_owner``. The UNIQUE index on
        email is still the final arbiter, but a loser now gets a clean error
        instead of an unhandled IntegrityError surfacing as a 500.
        """
        # Validate before touching the DB.
        if len(password) < 8:
            return None, "Password must be at least 8 characters"

        display_name = (display_name or "").strip()
        if not display_name:
            return None, "Display name is required"
        # The column is max_length=100 and SQLite truncates silently rather
        # than raising, so an over-long name would be stored altered.
        if len(display_name) > 100:
            return None, "Display name must be 100 characters or fewer"

        normalized_email = (email or "").lower().strip()
        if not normalized_email:
            return None, "Email is required"

        async with self.database.get_session() as session:
            # Inline the lookups rather than calling get_user_by_email /
            # get_user_count: those open their own sessions, which would
            # reintroduce the very race this block exists to close.
            if session.bind.dialect.name == "postgresql":
                from sqlalchemy import text
                await session.execute(text("SELECT pg_advisory_xact_lock(763746821)"))
            existing = (
                await session.execute(select(User).where(User.email == normalized_email))
            ).scalars().first()
            if existing:
                return None, _EMAIL_TAKEN

            user_count = int(
                (await session.execute(select(func.count()).select_from(User))).scalar_one()
            )

            if self.settings.auth_mode != "multi" and user_count > 0:
                return None, _OWNER_EXISTS

            is_owner = self.settings.auth_mode == "single" and user_count == 0

            user = User.create(
                email=normalized_email,
                password=password,
                display_name=display_name,
                is_owner=is_owner,
            )

            session.add(user)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                logger.info("Registration lost a race on email uniqueness: %s", normalized_email)
                return None, _EMAIL_TAKEN
            await session.refresh(user)

        logger.info(f"User registered: {normalized_email} (owner={is_owner})")

        return user, None

    async def login(self, email: str, password: str) -> tuple[Optional[User], Optional[str]]:
        """
        Authenticate user and return user object.
        Returns (user, None) on success, (None, error_message) on failure.
        """
        user = await self.get_user_by_email(email)

        if user is None:
            # Burn an equivalent bcrypt round so an unregistered address is
            # not distinguishable by response time. The result is discarded.
            bcrypt.checkpw(password.encode("utf-8"), _DUMMY_PASSWORD_HASH)
            logger.info("Login rejected: no such account")
            return None, _INVALID_CREDENTIALS

        password_ok = user.verify_password(password)

        if not password_ok:
            logger.info("Login rejected: bad password for user id=%s", user.id)
            return None, _INVALID_CREDENTIALS

        if not user.is_active:
            # Same message as a bad password on purpose -- a distinct one
            # confirms the account exists. The reason is logged instead so
            # operators can still tell the two apart.
            logger.info("Login rejected: account disabled, user id=%s", user.id)
            return None, _INVALID_CREDENTIALS

        # Update last login. A failure here must not turn a valid login into
        # a 500 -- the timestamp is bookkeeping, not part of authentication.
        try:
            async with self.database.get_session() as session:
                result = await session.execute(select(User).where(User.id == user.id))
                db_user = result.scalars().first()
                if db_user:
                    db_user.last_login = datetime.now(timezone.utc)
                    await session.commit()
        except Exception:
            logger.warning("Could not record last_login for user id=%s", user.id, exc_info=True)

        logger.info(f"User logged in: {email}")
        return user, None

    def logout(self) -> None:
        """No-op hook kept for symmetry with login.

        There is nothing to tear down here: the encryption key is
        server-scoped (initialised once in ``main.py`` from
        ``API_KEY_ENCRYPTION_KEY``), not derived per session, and there is no
        token denylist. The router clears the session cookie; that is the
        entire logout. See the Known Limitations section of
        ``docs-internal/authentication.md``.
        """
        logger.debug("User logged out")

    def create_access_token(self, user: User) -> str:
        """Create JWT access token for user.

        ``jti`` / ``nbf`` are additive -- unknown claims are ignored on
        decode, so tokens minted before this change keep verifying. ``iss``
        and ``aud`` are deliberately absent: enforcing them would invalidate
        every token currently held by a browser, for negligible benefit in a
        single-audience app with a per-deployment secret.
        """
        now = datetime.now(timezone.utc)
        expire = now + timedelta(minutes=self.settings.jwt_expire_minutes)
        payload = {
            "sub": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "is_owner": user.is_owner,
            "exp": expire,
            "iat": now,
            "nbf": now,
            "jti": uuid.uuid4().hex,
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
        """Get current user from token.

        Rejects a deactivated account. There is no token denylist, so
        ``is_active`` is the only revocation lever available to an operator
        before the token's own expiry.
        """
        payload = self.verify_token(token)
        if not payload:
            return None

        user_id = payload.get("sub")
        if not user_id:
            return None

        try:
            numeric_id = int(user_id)
        except (TypeError, ValueError):
            # A non-numeric `sub` means a malformed or foreign token; a bare
            # int() here raised ValueError and surfaced as a 500.
            logger.debug("Token carried a non-numeric subject")
            return None

        user = await self.get_user_by_id(numeric_id)
        if user is None or not user.is_active:
            return None
        return user

    def get_auth_status(self) -> Dict[str, Any]:
        """Get authentication status and mode info."""
        return {
            "auth_mode": self.settings.auth_mode,
            "registration_enabled": self.settings.auth_mode == "multi",
        }

    def is_encryption_initialized(self) -> bool:
        """Check if encryption is ready for use."""
        return self.encryption.is_initialized()
