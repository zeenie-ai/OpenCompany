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

Auto-migration of existing ``<repo>/data/`` and ``<repo>/workflows/``
trees happens in :func:`migrate_legacy_layout` — see
``server/main.py``'s startup event.
"""

from __future__ import annotations

import shutil
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


# ---------------------------------------------------------------------------
# One-time auto-migration on app startup
# ---------------------------------------------------------------------------

# Map of legacy on-disk locations → new locations under ``.machina/``.
# Each entry is renamed exactly once: only when the source exists and
# the destination does not. Subsequent boots are no-ops.
_LEGACY_RENAMES: list[tuple[Path, Path]] = []


def _legacy_renames() -> list[tuple[Path, Path]]:
    """Build the rename plan from current settings (lazy — needs Settings).

    Sources are always under the repo root (where the pre-cutover code
    wrote them); destinations are under whatever ``machina_root()``
    currently resolves to (``~/.machina/`` by default). Renames may
    cross filesystems on multi-disk setups — :func:`migrate_legacy_layout`
    falls back to ``shutil.move`` (copy + delete) when ``Path.rename``
    fails with ``OSError``.
    """
    root = machina_root()
    src_root = project_root()
    return [
        # 1. workflows/  →  ~/.machina/workflows/
        (src_root / "workflows", root / "workflows"),
        # 2. data/  →  ~/.machina/  (handled below by per-subdir moves
        # to keep the flat layout; the bare-rename of ``data/`` itself
        # would re-introduce the nested structure)
        # 2a. data/claude-machina/  →  ~/.machina/claude/
        (src_root / "data" / "claude-machina", root / "claude"),
        # 2b. data/workspaces/  →  ~/.machina/workspaces/
        (src_root / "data" / "workspaces", root / "workspaces"),
        # 2c. data/whatsapp/  →  ~/.machina/whatsapp/
        (src_root / "data" / "whatsapp", root / "whatsapp"),
        # 2d. data/credentials.db  →  ~/.machina/credentials.db
        (src_root / "data" / "credentials.db", root / "credentials.db"),
        # 2e. data/machina.db  →  ~/.machina/machina.db
        (src_root / "data" / "machina.db", root / "machina.db"),
    ]


def _merge_into_destination(src: Path, dst: Path, log_label: str) -> int:
    """Recursively move ``src`` contents into ``dst`` skipping conflicts.

    Three cases:

    1. **``dst`` doesn't exist** — atomic ``rename`` (with ``shutil.move``
       fallback for cross-fs). Returns ``1``.
    2. **Both exist as files** — log WARNING and skip (the operator
       picks which to keep). Returns ``0``.
    3. **Both exist as dirs** — recurse into ``src``'s children;
       any child that doesn't exist in ``dst`` gets moved. Empty
       ``src`` afterwards is ``rmdir``'d. Returns the count of
       child items successfully moved.

    This solves the real-user breakage where ``~/.machina/claude/``
    might already exist with a stale ``npm/`` subdir (from a smoke
    test or partial install) while the actual auth + session JSONLs
    still live in ``<repo>/data/claude-machina/``. The plain
    ``rename`` would skip; recursive merge moves the real data
    into the new location item-by-item.
    """
    if not src.exists():
        return 0

    if not dst.exists():
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                src.rename(dst)
            except OSError:
                shutil.move(str(src), str(dst))
            logger.info("[%s] moved %s -> %s", log_label, src, dst)
            return 1
        except Exception as exc:
            logger.warning(
                "[%s] failed move %s -> %s: %s", log_label, src, dst, exc,
            )
            return 0

    # Destination exists.
    if src.is_file() or dst.is_file():
        logger.warning(
            "[%s] conflict %s vs %s — both exist; manual merge required",
            log_label, src, dst,
        )
        return 0

    # Both are dirs — recurse into src.
    merged = 0
    try:
        children = list(src.iterdir())
    except OSError as exc:
        logger.warning(
            "[%s] cannot list %s: %s — skipping merge", log_label, src, exc,
        )
        return 0
    for item in children:
        merged += _merge_into_destination(item, dst / item.name, log_label)

    # Try removing src if empty now (all children either moved or
    # conflicting). Leave in place otherwise so the operator can see
    # what wasn't migrated.
    try:
        src.rmdir()
    except OSError:
        # Not empty — leave it. The earlier WARNINGs identify what
        # held it back.
        pass
    return merged


def migrate_legacy_layout() -> int:
    """Migrate pre-cutover ``<repo>/data/`` + ``<repo>/workflows/`` into
    the new ``machina_root()`` layout.

    Called once on app startup from ``main.py``. Idempotent: a marker
    file at ``<machina_root>/.migrated`` short-circuits subsequent
    calls. Returns the count of items moved.

    Recursive merge semantics (see :func:`_merge_into_destination`):
    when both source and destination exist as directories, walks into
    src and moves any child that doesn't conflict with dst. Files in
    both locations are flagged at WARNING and left for manual
    resolution. This handles the real-user case where the destination
    has been partially populated by a smoke-test / earlier upgrade
    attempt — the actual user data still in ``data/claude-machina/``
    gets moved into ``~/.machina/claude/`` item-by-item instead of
    silently skipped.
    """
    root = machina_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "[paths] cannot create machina root %s: %s — skipping migration",
            root, exc,
        )
        return 0

    stamp = root / ".migrated"
    if stamp.exists():
        logger.debug(
            "[paths] migration already ran (stamp %s present); skipping",
            stamp,
        )
        return 0

    moved = 0
    for src, dst in _legacy_renames():
        moved += _merge_into_destination(src, dst, log_label="paths")

    # If ``data/`` is empty after per-subdir merges, remove it so the
    # user's tree is clean. Don't force-remove if anything's still
    # in there (could be user-authored content).
    legacy_data = project_root() / "data"
    if legacy_data.is_dir():
        try:
            legacy_data.rmdir()  # only succeeds if empty
            logger.info("[paths] removed empty legacy data/ dir")
        except OSError:
            # Not empty — leave it alone.
            pass

    # Write the stamp regardless of whether anything moved. On a
    # fresh install with no legacy layout, this still prevents future
    # boots from re-walking the empty source dirs.
    try:
        stamp.write_text(
            "MachinaOs migrated this directory from the pre-cutover "
            "<repo>/data/ + <repo>/workflows/ layout. Delete this file "
            "to force migration to re-run on next startup.\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.debug("[paths] could not write migration stamp: %s", exc)

    if moved:
        logger.info(
            "[paths] migration complete: %d items moved into %s",
            moved, root,
        )
    else:
        logger.debug(
            "[paths] migration complete: nothing to migrate (fresh install "
            "or already-migrated state)",
        )
    return moved


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
    "migrate_legacy_layout",
]
