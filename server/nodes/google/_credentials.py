"""Google Workspace credential (Wave 11.E.1 — per-domain).

Used by the seven Google plugins in this folder — gmail, gmail_receive,
calendar, drive, sheets, tasks, contacts. The plugins don't use the
:class:`Connection` facade (they need ``google.oauth2.credentials.Credentials``
to hand to ``googleapiclient.discovery.build``), so :meth:`build_credentials`
returns that object instead of an httpx request.

OAuth flow (authorization_url / token_url / scopes) is owned by
:mod:`nodes.google._oauth`; this class is the plugin-facing interface
+ Credentials-modal metadata.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict

from services.plugin.credential import OAuth2Credential


class GoogleCredential(OAuth2Credential):
    id = "google"
    display_name = "Google Workspace"
    category = "Productivity"
    authorization_url = "https://accounts.google.com/o/oauth2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    client_id_api_key = "google_client_id"
    client_secret_api_key = "google_client_secret"
    docs_url = "https://developers.google.com/workspace"

    # Scope union across all 6 Google plugins. Single source of truth:
    # :data:`nodes.google._oauth.GOOGLE_WORKSPACE_SCOPES`, exposed lazily
    # to avoid import-cycle risk during credential auto-discovery.
    scopes: ClassVar[tuple] = ()

    @classmethod
    def get_scopes(cls) -> tuple:
        """Return the live scope list (lazy import)."""
        if not cls.scopes:
            from nodes.google._oauth import GOOGLE_WORKSPACE_SCOPES

            cls.scopes = tuple(GOOGLE_WORKSPACE_SCOPES)
        return cls.scopes

    @classmethod
    async def build_credentials(
        cls,
        parameters: Dict[str, Any],
        context: Dict[str, Any],
    ):
        """Return a ``google.oauth2.credentials.Credentials`` object.

        Thin wrapper over :func:`nodes.google._auth_helper.get_google_credentials`
        — the existing helper already handles owner-vs-customer token
        sources + proactive refresh. Keeping the implementation there
        (plus this indirection) means the plugin file's import surface
        stays flat and there's a single place to evolve the refresh
        policy.
        """
        from nodes.google._auth_helper import get_google_credentials

        return await get_google_credentials(parameters, context)
