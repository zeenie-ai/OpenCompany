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
TOKEN_KEY = "cloudflare_api_token"

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


def cf_env(token: Optional[str] = None) -> Dict[str, str]:
    """Child env for cf op invocations. ``token`` (the stored optional
    API token) is injected as ``CLOUDFLARE_API_TOKEN`` and takes
    precedence over both the OAuth session (cf's own documented
    resolution order) and any ambient server env token (explicit user
    config beats environment). Without a token, cf reads its own config
    or an ambient env token. ``NO_COLOR`` keeps the chalk-based status
    output free of ANSI codes."""
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    if token:
        env["CLOUDFLARE_API_TOKEN"] = token
    return env


async def stored_token() -> Optional[str]:
    """The user's optional API token from the credentials DB."""
    from services.plugin.deps import get_auth_service

    return await get_auth_service().get_api_key(TOKEN_KEY)


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
