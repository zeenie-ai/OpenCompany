"""Microsoft Graph credential (OAuth2).

Shared by the Microsoft plugins in this folder — ``msMail`` and
``msCalendar``. Unlike Google (which needs a
``google.oauth2.credentials.Credentials`` object for the googleapiclient
SDK), Microsoft Graph is plain bearer REST, so these plugins use the
:class:`services.plugin.connection.Connection` facade and this credential
only needs the standard :meth:`OAuth2Credential.resolve` /
:meth:`OAuth2Credential.inject` (``Authorization: Bearer <token>``).

OAuth flow (authorization_url / token_url / scopes) is owned by
:mod:`nodes.microsoft._oauth`; this class is the plugin-facing interface
+ Credentials-modal metadata + runtime token injection.
"""

from __future__ import annotations

from typing import ClassVar

from services.plugin.credential import OAuth2Credential

from ._oauth import get_oauth_endpoints

_ENDPOINTS = get_oauth_endpoints()


class MicrosoftCredential(OAuth2Credential):
    id = "microsoft"
    display_name = "Microsoft Graph"
    category = "Productivity"
    authorization_url = _ENDPOINTS["auth_uri"]
    token_url = _ENDPOINTS["token_uri"]
    client_id_api_key = "microsoft_client_id"
    client_secret_api_key = "microsoft_client_secret"
    docs_url = "https://learn.microsoft.com/en-us/graph/overview"

    # Access token rides as a bearer header (OAuth2Credential defaults:
    # token_location="header", token_header="Authorization", prefix="Bearer ").

    # Scope union across the Microsoft plugins. Single source of truth:
    # :data:`nodes.microsoft._oauth.MICROSOFT_GRAPH_SCOPES`, exposed lazily
    # to avoid import-cycle risk during credential auto-discovery.
    scopes: ClassVar[tuple] = ()

    @classmethod
    def get_scopes(cls) -> tuple:
        """Return the live scope list (lazy import)."""
        if not cls.scopes:
            from nodes.microsoft._oauth import MICROSOFT_GRAPH_SCOPES

            cls.scopes = tuple(MICROSOFT_GRAPH_SCOPES)
        return cls.scopes
