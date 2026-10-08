"""Shared Google Cloud plugin helpers — subprocess env builders and the
gcloud session probe.

Auth model — **the gcloud CLI owns its own auth end-to-end** (gh/Stripe
pattern): ``gcloud auth login`` (driven by the modal's Login button)
runs Google's OAuth flow with a loopback callback on a RANDOM port and
opens the default browser itself — the handlers never parse or proxy
its output. Credentials land in gcloud's own credential store under the
pinned config dir. OpenCompany never stores or reads a token — a
synthetic ``cli-managed`` marker OAuth row flips the catalogue's
``stored`` badge, exactly like gh/cloudflare.

Config isolation — every invocation (ops AND auth) pins
``CLOUDSDK_CONFIG`` to ``<DATA_DIR>/gcloud/`` (the vercel
``--global-config`` / ``CLAUDE_CONFIG_DIR`` idiom), so node auth state
never collides with the operator's own ``~/.config/gcloud`` /
``%APPDATA%\\gcloud``. A terminal session against the operator's global
config is therefore NOT visible to this node — by design.

ADC note: ``gcloud auth login`` mints gcloud USER credentials only.
gcloud CLI commands never read Application Default Credentials, so the
node needs nothing more; code using Google client libraries won't see
this session via ADC. The documented escape hatch is the node's
``custom`` op running ``auth application-default login`` (ADC then
lands isolated at ``<DATA_DIR>/gcloud/application_default_credentials.json``).
"""

from __future__ import annotations

import os
from services.process_environment import without_onepassword_environment
from typing import Any, Dict, Optional

# Ambient credential/config env vars that would mask the pinned-config
# session probe or short-circuit login (gcloud resolves
# CLOUDSDK_AUTH_ACCESS_TOKEN and account/project overrides env-first).
_AMBIENT_CREDENTIAL_VARS = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "CLOUDSDK_AUTH_ACCESS_TOKEN",
    "CLOUDSDK_CORE_ACCOUNT",
    "CLOUDSDK_CORE_PROJECT",
    "GOOGLE_CLOUD_PROJECT",
)


def _config_dir() -> str:
    from core.paths import data_path

    p = data_path("gcloud")
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def gcloud_env() -> Dict[str, str]:
    """Child env for every gcloud invocation — pinned isolated config
    dir plus the documented automation baseline (no interactive
    prompts, no update nags, no usage reporting, no ANSI)."""
    env = os.environ.copy()
    env["CLOUDSDK_CONFIG"] = _config_dir()
    env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"
    env["CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK"] = "1"
    env["CLOUDSDK_CORE_DISABLE_USAGE_REPORTING"] = "1"
    env["NO_COLOR"] = "1"
    return without_onepassword_environment(env)


def login_env() -> Dict[str, str]:
    """Env for ``gcloud auth login`` / ``auth list`` / ``auth revoke``
    — the CLI must consult its OWN credential store under the pinned
    config dir, so ambient credential/account overrides are stripped:
    with ``CLOUDSDK_AUTH_ACCESS_TOKEN`` (or an account override) set,
    the session probe would report the ambient identity instead of the
    stored login."""
    env = gcloud_env()
    for var in _AMBIENT_CREDENTIAL_VARS:
        env.pop(var, None)
    return without_onepassword_environment(env)


def resolve_gcloud_light() -> Optional[str]:
    """The project-local gcloud entry point WITHOUT triggering the
    (heavy) install. ``None`` when it has never been installed (status
    then reports disconnected; login / ops install on demand). The
    system-global gcloud is deliberately never consulted."""
    from ._install import gcloud_cli_path

    cached = gcloud_cli_path()
    return str(cached) if cached else None


def resolve_workdir(workspace_dir: Optional[str], path: str) -> str:
    """Working directory for an operation: explicit param (absolute, or
    relative to the per-workflow workspace) falling back to the
    workspace itself (github ``resolve_repo_path`` idiom)."""
    from pathlib import Path

    from services.plugin.base import NodeUserError

    if path:
        p = Path(path)
        if not p.is_absolute():
            if not workspace_dir:
                raise NodeUserError(f"Relative path {path!r} needs a workflow workspace — run inside a workflow or pass an absolute path")
            p = Path(workspace_dir) / p
        if not p.is_dir():
            raise NodeUserError(f"Path does not exist or is not a directory: {p}")
        return str(p)
    if not workspace_dir:
        raise NodeUserError("No path given and no workflow workspace available — set the 'path' parameter")
    return str(workspace_dir)


async def active_account() -> Optional[Dict[str, Any]]:
    """The ACTIVE credentialed account from ``gcloud auth list`` (under
    the pinned config dir), else ``None``. gcloud exits 0 with an empty
    JSON array when logged out — the non-empty array is the only
    signal, so exit codes are never consulted. Best-effort: ``None``
    when gcloud isn't installed.

    30s timeout (vs the usual 15s probes): the first-ever gcloud call
    pays Python interpreter startup plus config-dir creation, which is
    slow on Windows.
    """
    from services.events import run_cli_command

    binary = resolve_gcloud_light()
    if not binary:
        return None
    result = await run_cli_command(
        binary=binary,
        argv=["auth", "list", "--filter=status:ACTIVE", "--format=json"],
        timeout=30.0,
        env=login_env(),
    )
    accounts = result.get("result")
    if not isinstance(accounts, list) or not accounts:
        return None
    first = accounts[0]
    return first if isinstance(first, dict) else None
