"""Platform / shell / venv detection + canonical path-prefix helpers.

All on-disk locations the CLI needs (server tree, the server's uv venv,
client static script, build-output index, OS-native user dirs) are
derived from one prefix -- :func:`project_root` -- via the helpers
below. No callsite composes ``root / "server"`` / ``root / ".venv"``
inline; every consumer imports the named helper. This keeps layout
knowledge centralised so a future repo rename / directory relocation
is a single-file edit.

The CLI is **independent of the server's uv environment**: ``company``
is installable via pipx / system Python without involving uv, and
shells out to ``uv run --no-sync ...`` (via :func:`cli.run.uv_run`)
for any server-side command. :func:`server_venv` exists so build /
clean / preflight code can check or report that venv's location
without composing it inline.

``server_venv`` respects uv's documented ``UV_PROJECT_ENVIRONMENT``
override (https://docs.astral.sh/uv/concepts/projects/config/) so
power users with non-default venv locations still work.

OS-native user dirs (data, cache, config, log) are derived through
``platformdirs`` (https://pypi.org/project/platformdirs/) -- the same
library the server uses for its pooch binary cache -- so the two halves
of the project agree on per-OS conventions.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
# ``platform.release()`` returns the kernel version string. On WSL2 it
# always contains ``"microsoft"`` (e.g. ``5.15.167.4-microsoft-standard-WSL2``)
# regardless of whether ``WSL_DISTRO_NAME`` is exported into the current
# subprocess. Stdlib, kernel-level — survives daemonised / cron / systemd
# launches that strip WSL env vars.
IS_WSL = IS_LINUX and "microsoft" in platform.release().lower()
IS_GIT_BASH = IS_WINDOWS and bool(
    os.environ.get("MSYSTEM") or "bash" in (os.environ.get("SHELL") or "")
)


def platform_name() -> str:
    """Human-readable platform label."""
    if IS_GIT_BASH:
        return "Git Bash"
    if IS_WSL:
        return "WSL"
    if IS_WINDOWS:
        return "Windows"
    if IS_MACOS:
        return "macOS"
    return "Linux"


def project_root() -> Path:
    """Resolve the project root from a module under ``cli/``.

    Layout: ``<project_root>/cli/<this_file>`` -> parents[1].
    """
    return Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Path-prefix helpers -- canonical layout derived from ``project_root``.
# ---------------------------------------------------------------------------


def _root(root: Path | None) -> Path:
    return root if root is not None else project_root()


def server_dir(root: Path | None = None) -> Path:
    """The ``server/`` directory -- the Python backend + plugin tree.
    ``server/`` is its own standalone uv project (its ``pyproject.toml``
    declares the dependency closure ``uv sync`` resolves)."""
    return _root(root) / "server"


def server_venv(root: Path | None = None) -> Path:
    """The server's uv-managed project environment directory.

    ``server/`` is a standalone uv project, so uv places its venv at
    ``server/.venv`` by default per https://docs.astral.sh/uv/concepts/
    projects/layout/. The ``UV_PROJECT_ENVIRONMENT`` env var overrides
    this -- relative overrides resolve against ``server/`` (matching
    uv's own behaviour), absolute paths are used verbatim.

    The CLI itself does NOT live in this venv; ``company`` is
    independently installable (pipx / system python). This helper
    exists so build / clean / preflight code can check or report the
    server venv's location without anyone composing the path inline.
    """
    base = server_dir(root)
    override = os.environ.get("UV_PROJECT_ENVIRONMENT")
    if override:
        path = Path(override)
        return path if path.is_absolute() else base / path
    return base / ".venv"


def node_modules_dir(root: Path | None = None) -> Path:
    """The pnpm/npm-installed dependency tree at the workspace root."""
    return _root(root) / "node_modules"


def client_dist_entry(root: Path | None = None) -> Path:
    """The Vite-built client entrypoint -- proof that the client side
    of ``company build`` completed."""
    return _root(root) / "client" / "dist" / "index.html"


def static_client_script(root: Path | None = None) -> Path:
    """The Node.js static-server script that serves the built client
    (used by ``company start``)."""
    return _root(root) / "scripts" / "serve-client.js"


# ---------------------------------------------------------------------------
# OS-native user directories via ``platformdirs``
# (https://pypi.org/project/platformdirs/). ``platformdirs`` is imported
# inside each helper, NOT at module level, so the rest of
# ``cli.platform_`` (path-prefix helpers, project_root, the platform
# booleans) keeps loading even when the wheel hasn't been installed
# yet -- the recovery-verb scenario (``company clean`` against a
# half-broken env). Callers that actually need a user dir trigger the
# import on first call; if missing, the natural ``ModuleNotFoundError``
# surfaces at that callsite, not at every CLI import.
# ---------------------------------------------------------------------------

_APP_NAME = "OpenCompany"
_LEGACY_APP_NAME = "MachinaOs"


def _prefer_existing_legacy(new_path: Path, legacy_path: Path) -> Path:
    """Use an existing pre-rebrand directory until the user migrates it.

    New installations resolve to the OpenCompany path. An upgrade must not
    silently strand databases, daemon state, or Terraform state that already
    lives in a legacy directory. The repository ships
    ``.opencompany/workflows`` seed files, so mere directory existence is not
    evidence that runtime state has migrated: a legacy root with real state
    still wins while the canonical root contains only those seeds.
    """

    def has_runtime_state(path: Path) -> bool:
        if not path.exists():
            return False
        if not path.is_dir():
            return True
        try:
            return any(child.name != "workflows" for child in path.iterdir())
        except OSError:
            # If an existing directory cannot be inspected, do not redirect
            # away from it and risk hiding state behind a permissions issue.
            return True

    if has_runtime_state(legacy_path) and not has_runtime_state(new_path):
        return legacy_path
    return new_path


def user_data_dir() -> Path:
    """User data directory.

    Honours the project's ``DATA_DIR`` env override (see
    ``.env.template``) -- the convention shared with the server's
    ``core.paths.opencompany_root``. Otherwise delegates to platformdirs.
    """
    override = os.environ.get("DATA_DIR")
    if override:
        configured = Path(override).expanduser()
        # Released versions commonly configured ``~/.machina`` or
        # ``<repo>/.machina``. The new defaults use the sibling
        # ``.opencompany`` directory; discover the old sibling until real
        # runtime state is written to the canonical location. Custom DATA_DIR
        # values remain exact operator choices and are never rewritten.
        if configured.name == ".opencompany":
            return _prefer_existing_legacy(
                configured, configured.with_name(".machina")
            )
        return configured
    import platformdirs

    return _prefer_existing_legacy(
        platformdirs.user_data_path(_APP_NAME, appauthor=_APP_NAME),
        platformdirs.user_data_path(_LEGACY_APP_NAME, appauthor=_LEGACY_APP_NAME),
    )


def user_cache_dir() -> Path:
    """User cache directory (downloaded binaries / pooch caches).
    Matches ``server/core/paths.py::packages_dir``."""
    import platformdirs

    return _prefer_existing_legacy(
        platformdirs.user_cache_path(_APP_NAME, appauthor=_APP_NAME),
        platformdirs.user_cache_path(_LEGACY_APP_NAME, appauthor=_LEGACY_APP_NAME),
    )


def user_config_dir() -> Path:
    """User config directory."""
    import platformdirs

    return _prefer_existing_legacy(
        platformdirs.user_config_path(_APP_NAME, appauthor=_APP_NAME),
        platformdirs.user_config_path(_LEGACY_APP_NAME, appauthor=_LEGACY_APP_NAME),
    )


def user_log_dir() -> Path:
    """User log directory."""
    import platformdirs

    return _prefer_existing_legacy(
        platformdirs.user_log_path(_APP_NAME, appauthor=_APP_NAME),
        platformdirs.user_log_path(_LEGACY_APP_NAME, appauthor=_LEGACY_APP_NAME),
    )
