"""Shared GitHub plugin helpers — subprocess env builders, gh session
probe, workspace path resolution.

Auth model — **the gh CLI owns its own auth** (Stripe CLI pattern):
``gh auth login`` (driven by the modal's Login button or run by the
user in a terminal) stores the token in the system credential store;
OpenCompany never stores or injects a token — a synthetic ``cli-managed``
marker OAuth row flips the catalogue's ``stored`` badge, exactly like
Stripe. Users who prefer a PAT pipe it to ``gh auth login
--with-token`` themselves. Ambient ``GH_TOKEN`` in the server's own
environment is left untouched for ops (gh documents env tokens as
taking precedence over stored credentials) — but it MUST be stripped
for ``gh auth login`` / ``status`` / ``logout`` (see
:func:`login_env`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from services.plugin.base import NodeUserError


def gh_env() -> Dict[str, str]:
    """Child env for gh op invocations — the documented automation
    baseline from ``gh help environment``. No token handling: gh reads
    its own credential store (or an ambient env token, per its own
    documented precedence)."""
    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    env["NO_COLOR"] = "1"
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    env["GH_PAGER"] = "cat"
    return env


def login_env() -> Dict[str, str]:
    """Env for ``gh auth login`` / ``gh auth status`` / ``gh auth
    logout`` — the CLI must consult its OWN credential store, so
    ambient env tokens are stripped: ``gh auth login`` aborts outright
    when ``GH_TOKEN``/``GITHUB_TOKEN`` is set ("The value of the …
    environment variable is being used for authentication", source:
    ``pkg/cmd/auth/login/login.go``), and ``gh auth status`` would
    report the env token instead of the stored session. The login flow
    must also be allowed to prompt-print, so ``GH_PROMPT_DISABLED`` is
    dropped here too."""
    env = gh_env()
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_PROMPT_DISABLED", None)
    return env


def resolve_gh_light() -> Optional[str]:
    """The project-local gh binary WITHOUT triggering a download.
    ``None`` when it has never been installed (status then reports
    disconnected; login / ops install on demand). The system-global gh
    is deliberately never consulted."""
    from ._install import gh_cli_path

    cached = gh_cli_path()
    return str(cached) if cached else None


async def cli_logged_in() -> bool:
    """True when a ``gh auth login`` session is live (whether started
    from the modal's Login button or the user's own terminal — gh owns
    the state either way). ``gh auth status`` exits 0 when healthy,
    1 on auth problems. Best-effort: False when gh isn't installed."""
    from services.events import run_cli_command

    binary = resolve_gh_light()
    if not binary:
        return False
    result = await run_cli_command(
        binary=binary,
        argv=["auth", "status"],
        timeout=15.0,
        env=login_env(),
    )
    return bool(result.get("success"))


def resolve_repo_path(workspace_dir: Optional[str], path: str) -> str:
    """Working directory for an operation: explicit param (absolute,
    or relative to the per-workflow workspace) falling back to the
    workspace itself (vercel ``_resolve_deploy_cwd`` idiom)."""
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
