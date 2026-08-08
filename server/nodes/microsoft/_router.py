"""Microsoft Graph OAuth callback router — factory-built.

Mounts ``GET /api/microsoft/callback`` via
:func:`services.events.oauth_lifecycle.make_oauth_callback_router`. The
factory owns the code exchange, token storage, XSS-safe success/error
page, and the ``credential.oauth.connected`` broadcast.
"""

from __future__ import annotations

from typing import Any, Dict

from services.events.oauth_lifecycle import make_oauth_callback_router

from ._handlers import _microsoft_oauth_factory


def _user_info_to_email(info: Dict[str, Any]) -> str:
    return info.get("email", "Unknown") or "Unknown"


router = make_oauth_callback_router(
    provider="microsoft",
    oauth_factory=_microsoft_oauth_factory,
    user_info_to_email=_user_info_to_email,
    color_hex="#0078D4",
)
