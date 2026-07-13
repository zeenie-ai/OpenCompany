"""Session-cookie naming with a one-way OpenCompany migration bridge."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Optional, Protocol


LEGACY_SESSION_COOKIE_NAME = "machina_token"


class _CookieSettings(Protocol):
    jwt_cookie_name: str


def session_cookie_names(settings: _CookieSettings) -> tuple[str, ...]:
    """Canonical cookie first, followed by the legacy name when distinct."""

    names = [settings.jwt_cookie_name]
    if LEGACY_SESSION_COOKIE_NAME not in names:
        names.append(LEGACY_SESSION_COOKIE_NAME)
    return tuple(names)


def get_session_token(cookies: Mapping[str, str], settings: _CookieSettings) -> Optional[str]:
    """Read the canonical session cookie, falling back to ``machina_token``."""

    return next((cookies.get(name) for name in session_cookie_names(settings) if cookies.get(name)), None)


__all__ = ["LEGACY_SESSION_COOKIE_NAME", "get_session_token", "session_cookie_names"]
