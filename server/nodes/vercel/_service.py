"""Shared Vercel plugin helpers — config-dir pinning, login sniff,
env/argv builders, login-output parsing.

Auth-state isolation: every ``vercel`` invocation passes
``--global-config <DATA_DIR>/vercel/`` so the CLI's ``auth.json`` /
``config.json`` live in a MachinaOs-owned directory instead of the
platform-varying ``com.vercel.cli`` default. Same philosophy as
``CLAUDE_CONFIG_DIR = data_path("claude")`` in
``nodes/agent/claude_code_agent/_oauth.py`` — MachinaOs-managed auth
never collides with the user's own system-wide ``vercel login``, and
the pinned path makes :func:`is_logged_in` deterministic across
platforms. Composed inline per the ``core.paths`` rule (generic
helpers only — no plugin subpaths in ``core/paths.py``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from core.logging import get_logger
from core.paths import data_path

logger = get_logger(__name__)

# api-key row holding the user's optional Vercel access token
# (vercel.com/account/tokens). Injected as the VERCEL_TOKEN env var —
# never argv, so it stays out of process lists.
TOKEN_KEY = "vercel_token"


def vercel_config_dir() -> Path:
    """MachinaOs-pinned ``--global-config`` directory."""
    p = data_path("vercel")
    p.mkdir(parents=True, exist_ok=True)
    return p


def vercel_auth_path() -> Path:
    return vercel_config_dir() / "auth.json"


def is_logged_in() -> bool:
    """Filesystem sniff — ``auth.json`` in the pinned config dir holds a
    token. Cheap and sync (stripe's ``config.toml`` sniff idiom). True
    for *any* prior login, which is why the login handler pairs it with
    an mtime-advance check."""
    auth = vercel_auth_path()
    try:
        return auth.exists() and "token" in auth.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def global_argv(argv: List[str]) -> List[str]:
    """Append the uniform global flags every invocation needs."""
    return [*argv, "--global-config", str(vercel_config_dir()), "--no-color"]


def vercel_env(token: Optional[str] = None) -> Dict[str, str]:
    """Child environment: parent env + ``NO_COLOR`` (+ ``VERCEL_TOKEN``)."""
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    if token:
        env["VERCEL_TOKEN"] = token
    return env


async def stored_token() -> Optional[str]:
    """The user's optional access token from the credentials DB."""
    from services.plugin.deps import get_auth_service

    return await get_auth_service().get_api_key(TOKEN_KEY)


# --- Login-output parsing ----------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_URL_RE = re.compile(r"https://\S+")
# Device-flow user codes render as grouped alphanumerics, e.g. ABCD-EFGH.
_CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def extract_login_url(text: str) -> Optional[str]:
    """First https URL in the CLI's login banner. Prefers a link that
    embeds the device code (``code=`` query param) because the frontend
    only opens ``url`` — it never renders a separate code."""
    urls = [u.rstrip(".,;)'\"") for u in _URL_RE.findall(strip_ansi(text))]
    if not urls:
        return None
    for url in urls:
        if "code=" in url:
            return url
    return urls[0]


def extract_verification_code(text: str) -> Optional[str]:
    m = _CODE_RE.search(strip_ansi(text))
    return m.group(1) if m else None
