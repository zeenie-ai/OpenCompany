"""Twitter / X credential (Wave 11.E.1 — per-domain).

Used by the four twitter plugins in this folder (twitter_send, twitter_search,
twitter_user, twitter_receive).

OAuth 2.0 with PKCE. The refresh flow is non-trivial (custom code exchange
in :mod:`nodes.twitter._oauth`), so :meth:`build_client` returns an
authenticated XDK ``Client`` — that's what the four twitter plugins
actually hand to the XDK API methods. Use :func:`nodes.twitter._base.call_with_retry`
on top; it transparently refreshes on 401/403 via this class.
"""

from __future__ import annotations


from services.plugin.credential import OAuth2Credential


class TwitterCredential(OAuth2Credential):
    id = "twitter"
    display_name = "Twitter / X"
    category = "Social"
    authorization_url = "https://twitter.com/i/oauth2/authorize"
    token_url = "https://api.twitter.com/2/oauth2/token"
    client_id_api_key = "twitter_client_id"
    client_secret_api_key = "twitter_client_secret"
    scopes = (
        "tweet.read",
        "tweet.write",
        "users.read",
        "follows.read",
        "follows.write",
        "like.read",
        "like.write",
        "offline.access",
    )
    docs_url = "https://developer.x.com/en/docs/authentication/oauth-2-0"
