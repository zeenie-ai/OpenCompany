"""Central path resolution for MachinaOs's on-disk state.

Single source of truth for every directory the app reads or writes.
The default root lives at ``~/.machina/`` — the user's home directory,
cross-platform via :meth:`pathlib.Path.home` (``$HOME`` on POSIX,
``%USERPROFILE%`` on Windows). Override with ``DATA_DIR`` env var.

Why home-rooted (not project-local): claude's auth, the WhatsApp
session DB, the credentials store, and per-workflow workspaces all
survive ``rm -rf`` of the repo. Multiple MachinaOs checkouts on the
same machine share state instead of each carrying its own copy. The
``~/.machina/`` convention matches Stripe's ``~/.config/stripe/``,
ngrok's ``~/.ngrok2/``, and claude code's own ``~/.claude/``.

Layout (flat, no redundant nesting):

  - ``~/.machina/claude/``       (was ``<repo>/data/claude-machina/``)
  - ``~/.machina/workspaces/``   (was ``<repo>/data/workspaces/``)
  - ``~/.machina/workflows/``    (was ``<repo>/workflows/``)
  - ``~/.machina/whatsapp/``     (was ``<repo>/data/whatsapp/``)
  - ``~/.machina/credentials.db``
  - ``~/.machina/machina.db``

Importable as ``from core.paths import claude_config_dir, workspace_dir, …``
so consumers never have to recompute the root themselves (the old
``Path(__file__).resolve().parents[N] / "data" / ...`` idiom was
duplicated across 4+ files and brittle to file moves).

DATA_DIR resolution rules (see :func:`machina_root`):

  - Starts with ``~`` → ``Path.expanduser()`` (user home).
  - Absolute path → used verbatim.
  - Relative path → resolved under the repo root (back-compat for
    callers who set ``DATA_DIR=data`` to keep the pre-cutover layout).

Migration of pre-cutover ``<repo>/data/`` + ``<repo>/workflows/``
trees is the operator's responsibility — set ``DATA_DIR`` to the
legacy path (e.g. ``DATA_DIR=data``) to keep using the old layout,
or move the contents manually:

  mv server/data/claude-machina  ~/.machina/claude
  mv server/data/workspaces      ~/.machina/workspaces
  mv server/data/workflow.db     ~/.machina/workflow.db
  mv server/data/credentials.db  ~/.machina/credentials.db
  mv workflows                   ~/.machina/workflows
"""

from __future__ import annotations

from pathlib import Path

from core.logging import get_logger

logger = get_logger(__name__)


# Repo root: server/core/paths.py -> parents[2] is the project root.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def project_root() -> Path:
    """Absolute path of the MachinaOs git repo root."""
    return _REPO_ROOT


def machina_root() -> Path:
    """Absolute path of ``~/.machina/`` (or the configured DATA_DIR).

    Resolves :class:`core.config.Settings.data_dir` per the rules in
    the module docstring. Default expands to ``~/.machina/``, i.e.
    the user's home dir — same shape on Windows
    (``%USERPROFILE%/.machina``), macOS, and Linux.

    Settings is instantiated lazily so this module stays a leaf
    dependency. The Settings class itself reads ``../.env`` at
    construction; subsequent calls are cheap (Pydantic caches).
    """
    from core.config import Settings
    settings = Settings()
    raw = settings.data_dir
    p = Path(raw)
    # ``~/...`` shape: expand to the user's home dir cross-platform.
    if raw.startswith("~"):
        return p.expanduser().resolve()
    # Absolute (POSIX or drive-rooted on Windows): use verbatim.
    if p.is_absolute():
        return p.resolve()
    # Relative: keep the pre-cutover repo-local back-compat path.
    return (project_root() / p).resolve()


def claude_config_dir() -> Path:
    """``CLAUDE_CONFIG_DIR`` for spawned claude subprocesses.

    Resolves to ``<machina_root>/claude/``. Single source of truth for
    the plugin's ``MACHINA_CLAUDE_DIR`` constant (re-exported from
    ``nodes/agent/claude_code_agent/_oauth.py`` for back-compat).
    """
    return machina_root() / "claude"


def claude_npm_dir() -> Path:
    """Where ``npm install @anthropic-ai/claude-code`` lands.

    ``<claude_config_dir>/npm/`` — keeps the project-local node_modules
    tree co-located with the rest of claude state.
    """
    return claude_config_dir() / "npm"


def workspaces_dir() -> Path:
    """Root for per-workflow workspaces."""
    return machina_root() / "workspaces"


def workspace_dir(workflow_id: str) -> Path:
    """Per-workflow workspace at ``<machina_root>/workspaces/<workflow_id>/``.

    The workflow executor injects this into the execution context as
    ``ctx.raw["workspace_dir"]`` and the cli_agent service splices it
    into each claude task's ``--add-dir`` so claude can read upstream
    node outputs (``fileDownloader``, ``documentParser``, code
    executors) + materialise its connected skills under
    ``<workspace_dir>/.claude/skills/``.
    """
    return workspaces_dir() / workflow_id


def workflows_dir() -> Path:
    """Where workflow JSON exports live (auto-loaded by example_loader).

    Moved from ``<repo>/workflows/`` to ``<machina_root>/workflows/``
    so the entire MachinaOs working set is contained in one dotted
    directory the user can ignore from their VCS / file browser.
    """
    return machina_root() / "workflows"


def whatsapp_dir() -> Path:
    """Persistent WhatsApp session DB / state dir."""
    return machina_root() / "whatsapp"


def credentials_db_path() -> Path:
    """Encrypted credentials store (Fernet + PBKDF2)."""
    return machina_root() / "credentials.db"


__all__ = [
    "project_root",
    "machina_root",
    "claude_config_dir",
    "claude_npm_dir",
    "workspaces_dir",
    "workspace_dir",
    "workflows_dir",
    "whatsapp_dir",
    "credentials_db_path",
]
