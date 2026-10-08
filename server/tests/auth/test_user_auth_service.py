"""Tests for UserAuthService.

This surface previously had zero coverage: bcrypt hashing, JWT minting and
verification, owner bootstrap, and the single-vs-multi registration gate were
all untested, which is how a user-enumeration timing oracle and a TOCTOU on
the owner account both went unnoticed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from tests.auth.conftest import make_settings

pytestmark = pytest.mark.unit


async def _register(user_auth, email="owner@example.com", password="hunter2hunter2", name="Owner"):
    return await user_auth.register(email=email, password=password, display_name=name)


class TestRegister:
    async def test_first_user_becomes_owner(self, user_auth):
        user, error = await _register(user_auth)
        assert error is None
        assert user.is_owner is True
        assert user.email == "owner@example.com"

    async def test_single_mode_closes_after_first_user(self, user_auth):
        await _register(user_auth)
        user, error = await _register(user_auth, email="second@example.com")
        assert user is None
        assert error == "This OpenCompany already has its owner. Sign in instead."

    async def test_multi_mode_allows_more_users(self, database):
        from services.user_auth import UserAuthService

        from types import SimpleNamespace

        svc = UserAuthService(
            database=database,
            settings=make_settings(auth_mode="multi"),
            encryption=SimpleNamespace(is_initialized=lambda: True),
            credentials_db=SimpleNamespace(),
        )
        first, _ = await _register(svc)
        second, error = await _register(svc, email="second@example.com")
        assert error is None
        assert second is not None
        # Only single-owner mode confers ownership.
        assert first.is_owner is False

    async def test_duplicate_email_rejected(self, user_auth):
        await _register(user_auth)
        user, error = await _register(user_auth, name="Impostor")
        assert user is None
        assert error == "An account with this email already exists."

    async def test_email_is_normalized(self, user_auth):
        await _register(user_auth, email="  Owner@Example.COM  ")
        found = await user_auth.get_user_by_email("owner@example.com")
        assert found is not None

    async def test_concurrent_first_registration_yields_one_owner(self, database):
        """The four-session TOCTOU: both callers could observe an empty table
        and both be granted is_owner. The UNIQUE index is the final arbiter,
        but a loser must get a clean error rather than a 500."""
        from types import SimpleNamespace

        from services.user_auth import UserAuthService

        svc = UserAuthService(
            database=database,
            settings=make_settings(),
            encryption=SimpleNamespace(is_initialized=lambda: True),
            credentials_db=SimpleNamespace(),
        )

        results = await asyncio.gather(
            svc.register(email="race@example.com", password="hunter2hunter2", display_name="A"),
            svc.register(email="race@example.com", password="hunter2hunter2", display_name="B"),
        )

        created = [u for u, e in results if u is not None]
        errors = [e for u, e in results if u is None]
        assert len(created) == 1, "exactly one registration may win"
        assert all(isinstance(e, str) for e in errors), "the loser must get a message, not an exception"
        assert await svc.get_user_count() == 1

    @pytest.mark.parametrize("password", ["", "short", "1234567"])
    async def test_short_password_rejected(self, user_auth, password):
        user, error = await _register(user_auth, password=password)
        assert user is None
        assert "at least 8" in error

    async def test_blank_display_name_rejected(self, user_auth):
        user, error = await _register(user_auth, name="   ")
        assert user is None
        assert "Display name" in error

    async def test_overlong_display_name_rejected(self, user_auth):
        """The column is max_length=100 and SQLite truncates silently, so an
        over-long name would be stored altered rather than rejected."""
        user, error = await _register(user_auth, name="x" * 101)
        assert user is None
        assert "100 characters" in error

    async def test_display_name_at_limit_accepted(self, user_auth):
        user, error = await _register(user_auth, name="x" * 100)
        assert error is None
        assert user is not None


class TestLogin:
    async def test_happy_path(self, user_auth):
        await _register(user_auth)
        user, error = await user_auth.login("owner@example.com", "hunter2hunter2")
        assert error is None
        assert user.email == "owner@example.com"

    async def test_wrong_password_rejected(self, user_auth):
        await _register(user_auth)
        user, error = await user_auth.login("owner@example.com", "wrong-password")
        assert user is None
        assert error == "Invalid email or password"

    async def test_unknown_user_and_wrong_password_are_indistinguishable(self, user_auth):
        """Distinct messages are an account-enumeration oracle on a public,
        unauthenticated endpoint."""
        await _register(user_auth)
        _, unknown_error = await user_auth.login("nobody@example.com", "hunter2hunter2")
        _, wrong_error = await user_auth.login("owner@example.com", "wrong-password")
        assert unknown_error == wrong_error == "Invalid email or password"

    async def test_disabled_account_uses_the_same_message(self, user_auth, database):
        """"Account is disabled" confirmed the address existed."""
        from sqlmodel import select

        from models.auth import User

        await _register(user_auth)
        async with database.get_session() as session:
            row = (await session.execute(select(User))).scalars().first()
            row.is_active = False
            await session.commit()

        user, error = await user_auth.login("owner@example.com", "hunter2hunter2")
        assert user is None
        assert error == "Invalid email or password"

    async def test_unknown_user_still_runs_a_bcrypt_comparison(self, user_auth, monkeypatch):
        """Returning before bcrypt made an unregistered address measurably
        faster to reject -- a timing oracle. Assert the work happens rather
        than timing it, which would be flaky under CI load."""
        import services.user_auth as module

        calls = []
        original = module.bcrypt.checkpw

        def _spy(password, hashed):
            calls.append(hashed)
            return original(password, hashed)

        monkeypatch.setattr(module.bcrypt, "checkpw", _spy)

        await user_auth.login("nobody@example.com", "some-password")
        assert calls, "unknown-user path must still pay for a bcrypt comparison"
        assert calls[0] == module._DUMMY_PASSWORD_HASH

    async def test_dummy_hash_never_matches(self, user_auth):
        """A dummy hash of a guessable password would let anyone authenticate
        as a non-existent user if the branch were ever restructured."""
        import bcrypt

        import services.user_auth as module

        for guess in (b"", b"password", b"123456", b"dummy", b"changeme"):
            assert not bcrypt.checkpw(guess, module._DUMMY_PASSWORD_HASH)

    async def test_last_login_failure_does_not_break_login(self, user_auth, monkeypatch):
        """The timestamp is bookkeeping; a DB hiccup must not turn a valid
        login into a 500."""
        await _register(user_auth)

        real_get_session = user_auth.database.get_session
        state = {"calls": 0}

        def _flaky():
            state["calls"] += 1
            if state["calls"] > 1:
                raise RuntimeError("transient DB failure")
            return real_get_session()

        monkeypatch.setattr(user_auth.database, "get_session", _flaky)
        user, error = await user_auth.login("owner@example.com", "hunter2hunter2")
        assert error is None
        assert user is not None


class TestTokens:
    async def test_token_round_trip(self, user_auth):
        user, _ = await _register(user_auth)
        token = user_auth.create_access_token(user)
        payload = user_auth.verify_token(token)
        assert payload["sub"] == str(user.id)
        assert payload["email"] == user.email

    async def test_token_carries_jti_and_nbf(self, user_auth):
        user, _ = await _register(user_auth)
        payload = user_auth.verify_token(user_auth.create_access_token(user))
        assert payload["jti"]
        assert payload["nbf"]

    async def test_jti_is_unique_per_token(self, user_auth):
        user, _ = await _register(user_auth)
        first = user_auth.verify_token(user_auth.create_access_token(user))["jti"]
        second = user_auth.verify_token(user_auth.create_access_token(user))["jti"]
        assert first != second

    async def test_expired_token_rejected(self, user_auth):
        user, _ = await _register(user_auth)
        expired = jwt.encode(
            {
                "sub": str(user.id),
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
            user_auth.settings.jwt_secret_key,
            algorithm="HS256",
        )
        assert user_auth.verify_token(expired) is None

    async def test_token_signed_with_another_key_rejected(self, user_auth):
        user, _ = await _register(user_auth)
        forged = jwt.encode(
            {"sub": str(user.id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "a-different-secret-key-that-is-long-enough",
            algorithm="HS256",
        )
        assert user_auth.verify_token(forged) is None


class TestGetCurrentUser:
    async def test_happy_path(self, user_auth):
        user, _ = await _register(user_auth)
        token = user_auth.create_access_token(user)
        assert (await user_auth.get_current_user(token)).id == user.id

    async def test_non_numeric_subject_returns_none(self, user_auth):
        """A bare int() here raised ValueError and surfaced as a 500."""
        token = jwt.encode(
            {
                "sub": "not-a-number",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            user_auth.settings.jwt_secret_key,
            algorithm="HS256",
        )
        assert await user_auth.get_current_user(token) is None

    async def test_deactivated_user_is_rejected(self, user_auth, database):
        """is_active is the only revocation lever, since there is no denylist
        and logout only clears the cookie."""
        from sqlmodel import select

        from models.auth import User

        user, _ = await _register(user_auth)
        token = user_auth.create_access_token(user)
        assert await user_auth.get_current_user(token) is not None

        async with database.get_session() as session:
            row = (await session.execute(select(User))).scalars().first()
            row.is_active = False
            await session.commit()

        assert await user_auth.get_current_user(token) is None

    async def test_unknown_user_id_returns_none(self, user_auth):
        token = jwt.encode(
            {"sub": "99999", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            user_auth.settings.jwt_secret_key,
            algorithm="HS256",
        )
        assert await user_auth.get_current_user(token) is None


class TestUserCount:
    async def test_empty(self, user_auth):
        assert await user_auth.get_user_count() == 0

    async def test_counts_rows(self, database):
        from types import SimpleNamespace

        from services.user_auth import UserAuthService

        svc = UserAuthService(
            database=database,
            settings=make_settings(auth_mode="multi"),
            encryption=SimpleNamespace(is_initialized=lambda: True),
            credentials_db=SimpleNamespace(),
        )
        for i in range(3):
            await _register(svc, email=f"user{i}@example.com")
        assert await svc.get_user_count() == 3
