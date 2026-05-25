"""Shared cli-managed-marker auth helpers for CLI plugins.

Used by both claude (``nodes/agent/claude_code_agent/_handlers.py``)
and codex (``services/cli_agent/_handlers.py`` until ``codex_agent``
adopts the plugin-folder layout). The marker token + storage +
broadcast plumbing is CLI-agnostic — each provider just decides
when to call ``mark_logged_in`` / ``mark_logged_out`` /
``broadcast_credential_event`` based on its own CLI status flow.

Pattern matches Stripe (``nodes/stripe/_handlers.py``): the catalogue's
generic ``stored`` check flips when the synthetic ``"cli-managed"``
marker is present in ``auth_service``, so the FE Credentials modal
shows "Connected" without per-provider FE code.
"""

from __future__ import annotations

from typing import Optional

# Synthetic marker stored in ``auth_service`` after a successful CLI
# login. Matches ``nodes/stripe/_handlers.py::_MARKER_TOKEN``.
MARKER_TOKEN = "cli-managed"


async def mark_logged_in(
    catalogue_key: str,
    *,
    email: Optional[str] = None,
    name: Optional[str] = None,
) -> None:
    """Store the cli-managed marker for ``catalogue_key``.

    Lazy-imports the container so this module stays a leaf service.
    """
    from core.container import container

    await container.auth_service().store_oauth_tokens(
        provider=catalogue_key,
        access_token=MARKER_TOKEN,
        refresh_token=MARKER_TOKEN,
        email=email,
        name=name,
    )


async def mark_logged_out(catalogue_key: str) -> None:
    """Drop the cli-managed marker for ``catalogue_key``."""
    from core.container import container

    await container.auth_service().remove_oauth_tokens(catalogue_key)


async def broadcast_credential_event(event_type: str, provider: str) -> None:
    """Fire a CloudEvents-shaped catalogue-invalidation. Frontend listens
    via ``WebSocketContext`` and re-fetches the catalogue."""
    from services.status_broadcaster import get_status_broadcaster

    await get_status_broadcaster().broadcast_credential_event(
        event_type,
        provider=provider,
    )
