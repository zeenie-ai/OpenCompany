"""Shared Cloudflare plugin helpers — subprocess env builders, cf
session probe, login-output parsing.

Auth model — **the cf CLI owns its own auth** (gh/Stripe pattern):
``cf auth login`` (driven by the modal's Login button or run by the
user in a terminal) is a PKCE OAuth flow against
``dash.cloudflare.com/oauth2/*`` with a loopback callback server on
``localhost:8877``; the token lands in cf's user-level config
(``auth.jsonc``) or the OS keyring. OpenCompany never stores or injects
a token — a synthetic ``cli-managed`` marker OAuth row flips the
catalogue's ``stored`` badge, exactly like gh. Ambient
``CLOUDFLARE_API_TOKEN`` in the server's own environment is left
untouched for ops (cf documents env tokens as taking precedence over
the stored OAuth session — this is also the headless path on remote
deployments where the loopback callback is unreachable) — but it MUST
be stripped for ``cf auth login`` / ``whoami`` / ``logout`` (see
:func:`login_env`).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

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


def cf_env() -> Dict[str, str]:
    """Child env for cf op invocations. No token handling: cf reads its
    own config (or an ambient env token, per its own documented
    precedence). ``NO_COLOR`` keeps the chalk-based status output free
    of ANSI codes."""
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    return env


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


# --- Login-output parsing ----------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# cf 0.2.0 prints the authorize URL on stderr (source-verified, both
# variants carry the same URL):
#   Opening a link in your default browser: https://dash.cloudflare.com/oauth2/auth?...
#   Visit this link to authenticate: https://dash.cloudflare.com/oauth2/auth?...
_URL_RE = re.compile(r"https://dash\.(?:staging\.)?cloudflare\.com/oauth2/auth\S+")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def extract_login_url(text: str) -> Optional[str]:
    """The OAuth authorize URL from cf's login banner, or ``None``
    while still waiting for it."""
    m = _URL_RE.search(strip_ansi(text))
    return m.group(0).rstrip(".,;)'\"") if m else None
