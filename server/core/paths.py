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

State layout under ``~/.machina/`` (flat, no redundant nesting):

  - ``~/.machina/claude/``       OAuth state for spawned claude subprocesses
  - ``~/.machina/workspaces/``   per-workflow scratch dirs
  - ``~/.machina/whatsapp/``     persistent WhatsApp session DB
  - ``~/.machina/temporal/``     temporal-server cwd + SQLite db when persist=true
  - ``~/.machina/credentials.db``  Fernet-encrypted secrets
  - ``~/.machina/workflow.db``     SQLite app DB

Binaries that MachinaOs downloads on first use (Stripe CLI,
``agent-browser``'s npm tree) live separately, under the
OS-conventional cache dir (see :func:`packages_dir`). Resolved via
:func:`platformdirs.user_cache_path` — the standard cross-platform
resolver every XDG-aware tool already uses:

  - Linux:   ``~/.cache/MachinaOs/<service>/``
  - macOS:   ``~/Library/Caches/MachinaOs/<service>/``
  - Windows: ``%LOCALAPPDATA%\\MachinaOs\\Cache\\<service>\\``

Claude Code is the deliberate exception: its npm tree lives at
``<DATA_DIR>/claude/npm/`` (see :func:`claude_npm_dir`) alongside the
``CLAUDE_CONFIG_DIR`` auth state at ``<DATA_DIR>/claude/``. Keeping
the entire claude footprint (binary + tokens + IDE lockfiles + session
JSONL) under one operator-visible subtree means a single
``mv ~/.machina/claude /backup`` carries everything claude needs;
every operation requires both anyway, so the binary-vs-state split
that's useful for Stripe / browser hurts here. Supervised
event-source daemons (``stripe listen``, future plugins) keep their
cwd under :func:`daemons_dir` — a sibling of :func:`workspaces_dir`
so per-workflow scratch (workspaces) is never mixed with framework
state (daemons).

Out of scope here: the downloaded Temporal CLI binary (pooch cache
under :func:`packages_dir`, see ``services/temporal/_install.py``),
Himalaya (``cargo``/``brew`` system install), and npm packages
declared in ``package.json`` whose binaries pnpm manages directly
under ``node_modules/`` (e.g. ``whatsapp-rpc``'s ``edgymeow``).

The split keeps ``machina clean`` semantics clear: wiping
:func:`packages_dir` forces a fresh redownload of MachinaOs-managed
binaries without touching auth tokens, session DBs, or workspaces.

Shipped example workflows live at ``<repo>/.machina/workflows/`` —
git-tracked seed JSONs auto-imported on first launch by
``services.example_loader``. The path is namespaced under ``.machina``
to share the runtime convention; the rest of ``<repo>/.machina/`` is
gitignored. ``example_workflows_dir()`` resolves to that fixed seed
location regardless of ``DATA_DIR`` — operators do NOT relocate the
shipped seeds.

Importable as ``from core.paths import claude_config_dir, workspace_dir, …``
so consumers never have to recompute the root themselves (the old
``Path(__file__).resolve().parents[N] / "data" / ...`` idiom was
duplicated across 4+ files and brittle to file moves).

DATA_DIR resolution rules (see :func:`machina_root`):

  - Starts with ``~`` → ``Path.expanduser()`` (user home).
  - Absolute path → used verbatim.
  - Relative path → resolved under the repo root (back-compat for
    callers who set ``DATA_DIR=data`` to keep the pre-cutover layout).

Migration of pre-cutover ``<repo>/data/`` trees is the operator's
responsibility — set ``DATA_DIR`` to the legacy path (e.g.
``DATA_DIR=data``) to keep using the old layout, or move the
contents manually:

  mv server/data/claude-machina  ~/.machina/claude
  mv server/data/workspaces      ~/.machina/workspaces
  mv server/data/workflow.db     ~/.machina/workflow.db
  mv server/data/credentials.db  ~/.machina/credentials.db
"""

from __future__ import annotations

from pathlib import Path

import platformdirs

from core.logging import get_logger

logger = get_logger(__name__)


# Repo root: server/core/paths.py -> parents[2] is the project root.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def project_root() -> Path:
    """Absolute path of the MachinaOs git repo root."""
    return _REPO_ROOT


def _resolve_data_path(base: str, subpath: str = "") -> Path:
    """Canonical state-path resolution primitive. Always returns absolute.

    Shared by :func:`data_path` (public entry point) and
    ``Settings._resolve_under_data`` (the Pydantic-side mirror used by
    state-path properties like ``credentials_db_resolved``). One
    implementation so the two never drift.

    Rules:
      - ``Path.expanduser()`` on ``base`` — no-op when no ``~`` present.
      - Absolute resolved ``base`` → used verbatim.
      - Relative ``base`` → resolved under :func:`project_root` (so
        dev mode's ``DATA_DIR=.machina`` lands at ``<repo>/.machina/``
        regardless of the subprocess's cwd).
      - Empty ``subpath`` → returns the resolved base.
      - Absolute ``subpath`` → returned verbatim.
      - Relative ``subpath`` → joined onto the resolved base.
    """
    b = Path(base).expanduser()
    if not b.is_absolute():
        b = project_root() / b
    if not subpath:
        return b.resolve()
    p = Path(subpath)
    return p.resolve() if p.is_absolute() else (b / p).resolve()


def data_path(subpath: str | Path = "") -> Path:
    """Absolute path of ``<DATA_DIR>/<subpath>``.

    Single entry point for every state location MachinaOS reads or
    writes. Reads ``Settings.data_dir`` (env var ``DATA_DIR``) — so
    the dev / daemon split (``.env.dev`` vs ``.env.template``) moves
    every state path together. Pass an empty ``subpath`` to get the
    DATA_DIR root.

    Settings is instantiated lazily — Pydantic caches the result.
    Resolution rules: see :func:`_resolve_data_path`.
    """
    from core.config import Settings

    return _resolve_data_path(Settings().data_dir, str(subpath))


def machina_root() -> Path:
    """Absolute path of the configured DATA_DIR (= ``data_path()``)."""
    return data_path()


def packages_dir() -> Path:
    """OS-conventional cache root for binaries we download on first use.

    Delegates to :func:`platformdirs.user_cache_path` — the standard
    cross-platform resolver (used internally by ``pooch.os_cache``,
    which is how the Temporal binary downloader already finds its
    cache). Resolves to:

      - Linux:   ``$XDG_CACHE_HOME/MachinaOs/`` or ``~/.cache/MachinaOs/``
      - macOS:   ``~/Library/Caches/MachinaOs/``
      - Windows: ``%LOCALAPPDATA%\\MachinaOs\\Cache``

    Why OS cache (not :func:`machina_root`): downloaded binaries are
    pure cache — wiping them forces a clean redownload, never
    drops auth tokens or session state. The OS-conventional cache
    location is the standard place tools like Stripe CLI, Temporal,
    and Claude Code expect their binaries to land; matches every
    XDG-aware system installer.

    See :func:`package_dir` for the per-service accessor.
    """
    return platformdirs.user_cache_path("MachinaOs", appauthor=False)


def package_dir(name: str) -> Path:
    """Per-service install folder under :func:`packages_dir`.

    Canonical layout — each plugin's installer (``ensure_stripe_cli``,
    ``ensure_temporal_binaries``, ``ensure_local_claude_install``, …)
    drops its tree here. Examples::

        package_dir("stripe")   -> ~/.cache/MachinaOs/stripe/        (Linux)
        package_dir("temporal") -> ~/Library/Caches/MachinaOs/temporal/  (macOS)
        package_dir("claude")   -> %LOCALAPPDATA%\\MachinaOs\\Cache\\claude\\  (Windows)

    Caller is responsible for ``mkdir(parents=True, exist_ok=True)``
    so this helper stays side-effect-free and safe to call during
    import-time path resolution.
    """
    return packages_dir() / name


def claude_config_dir() -> Path:
    """``CLAUDE_CONFIG_DIR`` for spawned claude subprocesses.

    Resolves to ``<DATA_DIR>/claude/``. Single source of truth for
    the plugin's ``MACHINA_CLAUDE_DIR`` constant (re-exported from
    ``nodes/agent/claude_code_agent/_oauth.py`` for back-compat).
    Stores OAuth tokens + session state — distinct from
    :func:`claude_npm_dir`, which holds the downloaded CLI binary.
    """
    return data_path("claude")


def claude_npm_dir() -> Path:
    """Where ``npm install @anthropic-ai/claude-code`` lands.

    Resolves to ``<DATA_DIR>/claude/npm/`` — sibling of
    :func:`claude_config_dir` (the OAuth state dir, exposed to the
    CLI via ``CLAUDE_CONFIG_DIR``). Keeps the entire claude footprint
    (binary + auth state + IDE lockfiles + session JSONL) under one
    operator-visible tree, so a single ``mv ~/.machina/claude /backup``
    carries everything claude needs. Deliberate exception to the
    binary-vs-state split documented on :func:`packages_dir` — see
    the module docstring.
    """
    return data_path("claude") / "npm"


def daemons_dir() -> Path:
    """Root for supervised event-source daemons (``stripe listen``, etc.).

    Resolves to ``<DATA_DIR>/daemons/`` — sibling of
    :func:`workspaces_dir`. Daemons are framework-owned, long-lived
    processes whose cwd is just a place to drop log / state files;
    workspaces are per-workflow scratch owned by workflow nodes
    (one subdir per ``Workflow.slug``). Conflating the two clutters
    workspaces with framework state, which is what
    ``DaemonEventSource.workdir()`` used to do before this helper
    existed.
    """
    return data_path("daemons")


def workspaces_dir() -> Path:
    """Root for per-workflow workspaces.

    Routes through ``data_path(Settings().workspace_base_dir)`` (env
    var ``WORKSPACE_BASE_DIR``) so the path stays in lockstep with
    every other state location — no hardcoded ``"workspaces"`` literal
    that drifts when the env var changes.
    """
    from core.config import Settings

    return data_path(Settings().workspace_base_dir)


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


def example_workflows_dir() -> Path:
    """Shipped example workflow JSONs, auto-imported on first launch.

    Fixed at ``<repo>/.machina/workflows/`` — these are git-tracked
    seed JSONs that ship with the repo (see ``services.example_loader``
    for the import flow). The path is intentionally NOT under
    :func:`machina_root` because the seeds must survive ``machina
    clean`` (which wipes the rest of ``.machina/``) and must be the
    same location regardless of the operator's ``DATA_DIR`` setting.
    """
    return project_root() / ".machina" / "workflows"


def whatsapp_dir() -> Path:
    """Persistent WhatsApp session DB / state dir.

    Routes through ``data_path(Settings().whatsapp_data_subdir)``
    (env var ``WHATSAPP_DATA_SUBDIR``) so the WhatsApp tree moves
    with ``DATA_DIR``.
    """
    from core.config import Settings

    return data_path(Settings().whatsapp_data_subdir)


def credentials_db_path() -> Path:
    """Encrypted credentials store (Fernet + PBKDF2).

    Routes through ``data_path(Settings().credentials_db_path)`` (env
    var ``CREDENTIALS_DB_PATH``) so the DB moves with ``DATA_DIR``.
    """
    from core.config import Settings

    return data_path(Settings().credentials_db_path)


__all__ = [
    "project_root",
    "machina_root",
    "data_path",
    "packages_dir",
    "package_dir",
    "claude_config_dir",
    "claude_npm_dir",
    "daemons_dir",
    "workspaces_dir",
    "workspace_dir",
    "example_workflows_dir",
    "whatsapp_dir",
    "credentials_db_path",
]
