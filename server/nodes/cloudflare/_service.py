"""Shared Cloudflare plugin helpers — subprocess env builders and the
cf session probe.

Auth model — **the cf CLI owns its own auth end-to-end** (gh/Stripe
pattern): ``cf auth login`` (driven by the modal's Login button or run
by the user in a terminal) is a PKCE OAuth flow against
``dash.cloudflare.com/oauth2/*`` with a loopback callback server on
``localhost:8877``, and cf opens the default browser itself — the
handlers never parse or proxy its output. The token lands in cf's
user-level config (``auth.jsonc``) or the OS keyring. OpenCompany never
stores or injects a token — a synthetic ``cli-managed`` marker OAuth
row flips the catalogue's ``stored`` badge, exactly like gh. Ambient
``CLOUDFLARE_API_TOKEN`` in the server's own environment is left
untouched for ops (cf documents env tokens as taking precedence over
the stored OAuth session — this is also the headless path on remote
deployments where the loopback callback is unreachable) — but it MUST
be stripped for ``cf auth login`` / ``whoami`` / ``logout`` (see
:func:`login_env`).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

# api-key row holding the user's optional Cloudflare API token
# (dash.cloudflare.com/profile/api-tokens). Injected as the
# CLOUDFLARE_API_TOKEN env var — never argv, so it stays out of process
# lists. The OAuth login's scope set is FIXED (86 scopes baked into cf's
# PKCE client, no --scopes flag) and omits Web Analytics/RUM and zone
# analytics entirely — an API token with the right permission groups is
# the only way past that ceiling.
#
# Storage key is the PROVIDER ID, not a bespoke name: the catalogue
# field key is the canonical ``apiKey``, which the credentials panel
# maps to the provider id ("cloudflare") for storage, and the base
# ``Credential.validate`` scaffold stores under ``cls.id`` — one
# convention, zero storage-key overrides.
TOKEN_KEY = "cloudflare"
# Companion field for Global API Key auth (X-Auth-Email). Only needed
# when the stored credential is a cfk_ key; scoped tokens ignore it.
EMAIL_KEY = "cloudflare_email"

# Cloudflare's documented scannable credential prefixes
# (developers.cloudflare.com/fundamentals/api/get-started/token-formats/):
# cfk_ = Global API Key (legacy X-Auth-Email/X-Auth-Key pair — full
# account access, works on every endpoint including those the
# account-token compatibility matrix excludes), cfut_ = User API Token,
# cfat_ = Account API Token (both Bearer).
GLOBAL_KEY_PREFIX = "cfk_"
ACCOUNT_TOKEN_PREFIX = "cfat_"

# cf resolves credentials env-first (source-verified against cf 0.2.0's
# auth chunk): CLOUDFLARE_API_TOKEN/CF_API_TOKEN, then the legacy
# global-key pair. All of them mask the OAuth session for login/whoami.
_AMBIENT_CREDENTIAL_VARS = (
    "CLOUDFLARE_API_TOKEN",
    "CF_API_TOKEN",
    "CLOUDFLARE_API_KEY",
    "CF_API_KEY",
    "CLOUDFLARE_EMAIL",
    "CF_EMAIL",
)


def cf_env(token: Optional[str] = None, email: Optional[str] = None) -> Dict[str, str]:
    """Child env for cf op invocations, routed by the stored
    credential's documented prefix:

    - API token (``cfut_``/``cfat_``/legacy) → ``CLOUDFLARE_API_TOKEN``
      (cf's first-priority credential source).
    - Global API Key (``cfk_``) + email → the legacy
      ``CLOUDFLARE_API_KEY`` + ``CLOUDFLARE_EMAIL`` pair cf documents;
      ambient API-token vars are dropped because cf ranks tokens above
      the key pair and would silently override the user's explicit
      choice.

    Explicit user config always beats ambient server env. Without a
    stored credential, cf reads its own config or ambient env vars.
    ``NO_COLOR`` keeps the chalk-based status output free of ANSI
    codes."""
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    if token and token.startswith(GLOBAL_KEY_PREFIX):
        if email:
            env.pop("CLOUDFLARE_API_TOKEN", None)
            env.pop("CF_API_TOKEN", None)
            env["CLOUDFLARE_API_KEY"] = token
            env["CLOUDFLARE_EMAIL"] = email
        # cfk_ without an email is unusable — inject nothing and let cf
        # fall back to its OAuth session.
    elif token:
        env["CLOUDFLARE_API_TOKEN"] = token
    return env


def api_auth_headers(key: Optional[str], email: Optional[str]) -> Optional[Dict[str, str]]:
    """Auth headers for direct api.cloudflare.com calls (GraphQL,
    verify probes) — the two officially documented schemes: ``Bearer``
    for API tokens, the legacy ``X-Auth-Email``/``X-Auth-Key`` pair for
    Global API Keys. ``None`` when no usable credential (cfk_ without
    an email included)."""
    if key and key.startswith(GLOBAL_KEY_PREFIX):
        return {"X-Auth-Email": email, "X-Auth-Key": key} if email else None
    if key:
        return {"Authorization": f"Bearer {key}"}
    return None


async def stored_token() -> Optional[str]:
    """The user's optional API token or Global API Key from the
    credentials DB (the panel's ``apiKey`` field accepts either)."""
    from services.plugin.deps import get_auth_service

    return await get_auth_service().get_api_key(TOKEN_KEY)


async def stored_email() -> Optional[str]:
    """The optional account email companion for Global API Key auth."""
    from services.plugin.deps import get_auth_service

    return await get_auth_service().get_api_key(EMAIL_KEY)


def login_env() -> Dict[str, str]:
    """Env for ``cf auth login`` / ``cf auth whoami`` / ``cf auth
    logout`` — the CLI must consult its OWN stored session, so ambient
    credential env vars are stripped: with ``CLOUDFLARE_API_TOKEN`` set,
    ``cf auth whoami`` reports the env token (``authSource: env``)
    instead of the OAuth session, and login would short-circuit."""
    env = cf_env()
    for var in _AMBIENT_CREDENTIAL_VARS:
        env.pop(var, None)
    return env


def resolve_cf_light() -> Optional[str]:
    """The project-local cf binary WITHOUT triggering an install.
    ``None`` when it has never been installed (status then reports
    disconnected; login / ops install on demand). The system-global cf
    is deliberately never consulted."""
    from ._install import cf_cli_path

    cached = cf_cli_path()
    return str(cached) if cached else None


async def whoami_snapshot() -> Optional[Dict[str, Any]]:
    """The parsed ``cf auth whoami`` JSON when a stored OAuth session is
    live, else ``None``. cf exits 0 in BOTH states (verified on 0.2.0:
    logged-out prints ``{"authenticated": false, "error": "Not logged
    in"}`` with exit 0) — the ``authenticated`` field is the only
    signal, so exit codes are never consulted. Best-effort: ``None``
    when cf isn't installed."""
    from services.events import run_cli_command

    binary = resolve_cf_light()
    if not binary:
        return None
    result = await run_cli_command(
        binary=binary,
        argv=["auth", "whoami"],
        timeout=15.0,
        env=login_env(),
    )
    info = result.get("result")
    if not isinstance(info, dict) or not info.get("authenticated"):
        return None
    if info.get("tokenValid") is False:
        return None
    return info
