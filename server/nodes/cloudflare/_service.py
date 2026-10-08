"""Shared Cloudflare plugin helpers — subprocess env builders, the
neutral working directory and the cf session probe.

Auth model — **the cf CLI owns its own auth end-to-end** (gh/Stripe
pattern): ``cf auth login`` is an OAuth 2.0 Device Authorization Grant
(RFC 8628) against ``dash.cloudflare.com/oauth2/device/auth``. It
prints a verification URL and a one-time code on stderr and polls until
the user approves (at most 5 minutes); there is no callback server, so
it works from any browser on any machine. The handlers relay the URL +
code to the credentials modal (gh's device-flow shape). The token lands
in cf's own profile store (``<xdg-config>/cloudflare/config/default.json``;
``%APPDATA%\\xdg.config`` on Windows). OpenCompany never stores or
injects that token — a synthetic ``cli-managed`` marker OAuth row flips
the catalogue's ``stored`` badge, exactly like gh.

cf resolves credentials env-first (verified against cf 1.0.0-beta.12):
``CLOUDFLARE_API_TOKEN`` (deprecated alias ``CF_API_TOKEN``), then the
OAuth profile. It does NOT accept a Global API Key — the
``CLOUDFLARE_API_KEY`` + ``CLOUDFLARE_EMAIL`` pair is ignored — so a
stored ``cfk_`` key serves only the direct API calls in this plugin
(GraphQL Analytics, the Validate probe). Ambient tokens in the server's
own environment are left in place for ops (the documented headless
path) but stripped for login / whoami / logout (see :func:`login_env`).
"""

from __future__ import annotations

import os
from services.process_environment import without_onepassword_environment
from pathlib import Path
from typing import Any, Dict, Optional

# api-key row holding the user's optional Cloudflare API token
# (dash.cloudflare.com/profile/api-tokens). Injected as the
# CLOUDFLARE_API_TOKEN env var — never argv, so it stays out of process
# lists — and it takes precedence over the CLI's OAuth login.
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
# account access; the cf CLI refuses it), cfut_ = User API Token,
# cfat_ = Account API Token (both Bearer).
GLOBAL_KEY_PREFIX = "cfk_"
ACCOUNT_TOKEN_PREFIX = "cfat_"

# The credential env vars cf reads; they mask the OAuth profile for
# login / whoami / logout.
_AMBIENT_CREDENTIAL_VARS = (
    "CLOUDFLARE_API_TOKEN",
    "CF_API_TOKEN",
)


def cf_workdir() -> Path:
    """Neutral working directory for cf invocations (``<DATA_DIR>/cloudflare``).

    cf reads its working directory: API commands load
    ``CLOUDFLARE_API_TOKEN`` / ``CLOUDFLARE_ACCOUNT_ID`` /
    ``CLOUDFLARE_ZONE_ID`` from ``.env`` files there, walk up from it for
    a ``cloudflare.config.ts`` (whose loader fails under bun), and cache
    the selected account in ``.cloudflare/cache/`` beneath it. Running
    from the server's own cwd would pick up whatever ``.env`` happens to
    sit there; this directory holds nothing but cf's account cache."""
    from core.paths import data_path

    p = data_path("cloudflare")
    p.mkdir(parents=True, exist_ok=True)
    return p


def cf_env(token: Optional[str] = None, account_id: Optional[str] = None) -> Dict[str, str]:
    """Child env for cf invocations.

    - A stored API token (``cfut_``/``cfat_``/legacy) rides
      ``CLOUDFLARE_API_TOKEN`` — cf's first-priority credential source,
      so explicit user config beats both the OAuth login and an ambient
      server token. A stored ``cfk_`` Global API Key is NOT injected
      (cf ignores the key pair); cf then falls back to ambient env or
      its OAuth login.
    - ``account_id`` rides ``CLOUDFLARE_ACCOUNT_ID`` — cf has no
      ``--account-id`` flag, and with several accounts a non-interactive
      run fails unless the account is named.
    - ``CF_DELEGATION=1`` stops cf from handing the command to another
      cf copy it can resolve (under bun that includes bun's install
      cache), so the pinned, verified version is the one that runs.
    - ``CF_SEND_TELEMETRY=false`` opts out of cf's usage telemetry (on by
      default); ``NO_COLOR`` keeps output free of ANSI codes."""
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["CF_DELEGATION"] = "1"
    env["CF_SEND_TELEMETRY"] = "false"
    if token and not token.startswith(GLOBAL_KEY_PREFIX):
        env["CLOUDFLARE_API_TOKEN"] = token
    if account_id:
        env["CLOUDFLARE_ACCOUNT_ID"] = account_id
    return without_onepassword_environment(env)


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
    logout`` — the CLI must consult its OWN profile store, so ambient
    token vars are stripped: with ``CLOUDFLARE_API_TOKEN`` set, ``cf auth
    whoami`` reports the env token instead of the OAuth login, and
    ``cf auth login`` refuses to start ("CLOUDFLARE_API_TOKEN is set and
    takes precedence")."""
    env = cf_env()
    for var in _AMBIENT_CREDENTIAL_VARS:
        env.pop(var, None)
    return without_onepassword_environment(env)


def resolve_cf_light() -> Optional[str]:
    """The project-local cf binary at the pinned version WITHOUT
    triggering an install. ``None`` when it has never been installed or
    is still an older version (status then reports disconnected; login /
    ops install on demand). The system-global cf is deliberately never
    consulted."""
    from ._install import cf_cli_path

    cached = cf_cli_path()
    return str(cached) if cached else None


async def whoami_snapshot() -> Optional[Dict[str, Any]]:
    """The parsed ``cf auth whoami`` JSON when the OAuth login is live,
    else ``None``. cf exits 0 in BOTH states (logged out prints
    ``{"authenticated": false, "error": "Not logged in"}``) — the
    ``authenticated`` field is the only signal, so exit codes are never
    consulted. Logged in, it reports ``email``, ``accounts`` and
    ``scopes``. Best-effort: ``None`` when cf isn't installed."""
    from services.events import run_cli_command

    binary = resolve_cf_light()
    if not binary:
        return None
    result = await run_cli_command(
        binary=binary,
        argv=["auth", "whoami"],
        # The status handler answers a 30 s frontend request.
        timeout=20.0,
        env=login_env(),
        cwd=str(cf_workdir()),
    )
    info = result.get("result")
    if not isinstance(info, dict) or not info.get("authenticated"):
        return None
    if info.get("tokenValid") is False:
        return None
    return info
