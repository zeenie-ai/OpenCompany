"""The JavaScript runtime the backend spawns: bun.

One module owns how the backend finds ``bun`` and how it extends the
shared OpenCompany packages tree, so the JS executor sidecar, the CLI
plugins that ship as npm packages (claude-code, edgymeow, agent-browser,
cf, vercel) and the codex provider all resolve the same binary and lay
packages out the same way. There is no Node and no npm anywhere on this
path: bun installs from the npm registry, runs the packages' ``bin``
entries on its own runtime, and shims ``node`` for their lifecycle
scripts.

Resolution: ``OPENCOMPANY_BUN_BIN`` (the desktop shell points it at the
bundled runtime) else the first ``bun`` on PATH. The desktop shell also
prepends its bundled runtime dir to PATH, so the override is belt and
braces.

Shared tree layout (``core.paths.packages_dir()``, ``<DATA_DIR>/packages/``):
one ``package.json`` + ``bun.lock`` + ``node_modules/`` covering every
OpenCompany-managed npm package. ``bun add --cwd <packages_dir> <spec>``
extends it idempotently; the bin shims land in ``node_modules/.bin/``
as ``<name>`` on POSIX and ``<name>.exe`` (plus a ``.bunx`` metadata
file) on Windows.

Stdlib-only apart from :mod:`core.paths`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from services.process_environment import without_onepassword_environment
import sys
from pathlib import Path

from core.paths import packages_dir

ENV_BUN_BIN = "OPENCOMPANY_BUN_BIN"

INSTALL_HINT = "install bun from https://bun.sh (the desktop app bundles it)"

_TREE_MANIFEST = {"name": "opencompany-packages", "private": True}


def bun_binary() -> str | None:
    """Absolute path to ``bun``, or ``None`` when it is not available."""
    override = os.environ.get(ENV_BUN_BIN, "").strip()
    if override and Path(override).is_file():
        return override
    return shutil.which("bun")


def require_bun(purpose: str) -> str:
    """Like :func:`bun_binary` but raises a user-readable error on miss."""
    found = bun_binary()
    if not found:
        raise RuntimeError(f"bun not found on PATH (or {ENV_BUN_BIN}) — {purpose} needs it; {INSTALL_HINT}")
    return found


def bin_shim_name(name: str) -> str:
    """The ``node_modules/.bin`` entry bun writes for a package ``bin``."""
    return f"{name}.exe" if sys.platform == "win32" else name


def shared_tree_bin(name: str) -> Path:
    """``<packages_dir>/node_modules/.bin/<name>[.exe]``."""
    return packages_dir() / "node_modules" / ".bin" / bin_shim_name(name)


def ensure_shared_tree(root: Path | None = None) -> Path:
    """Create the shared tree root with a minimal manifest if missing."""
    tree = root or packages_dir()
    tree.mkdir(parents=True, exist_ok=True)
    manifest = tree / "package.json"
    if not manifest.exists():
        manifest.write_text(json.dumps(_TREE_MANIFEST, indent=2) + "\n", encoding="utf-8")
    return tree


def installed_version(name: str, root: Path | None = None) -> str | None:
    """Version of ``name`` in the shared tree, from its
    ``node_modules/<name>/package.json``; ``None`` when the package is not
    installed or its manifest is unreadable.

    An existing bin shim says nothing about which version it runs, so a
    plugin that pins a version compares against this to notice that its
    pin moved on (``bun add`` of the new pin replaces the old copy).
    """
    tree = root or packages_dir()
    manifest = tree / "node_modules" / name / "package.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version else None


def add_package(spec: str, *, trust: bool = False, root: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Blocking ``bun add`` of ``spec`` into the shared tree.

    ``trust`` opts the package into running its lifecycle scripts (bun
    blocks them by default); pass it only for packages whose postinstall
    materialises something the plugin needs (edgymeow's Go binary).
    Returns the completed process; callers decide what a failure means.
    """
    bun = require_bun(f"installing {spec}")
    tree = ensure_shared_tree(root)
    argv = [bun, "add", "--cwd", str(tree), "--no-progress"]
    if trust:
        argv.append("--trust")
    argv.append(spec)
    return subprocess.run(argv, capture_output=True, text=True, env=without_onepassword_environment())


__all__ = [
    "ENV_BUN_BIN",
    "INSTALL_HINT",
    "add_package",
    "bin_shim_name",
    "bun_binary",
    "ensure_shared_tree",
    "installed_version",
    "require_bun",
    "shared_tree_bin",
]
