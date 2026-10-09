"""Single source of truth for where the OpenCompany *application tree* lives.

The backend ships as a sibling layout that the npm tarball, a source
checkout, and the desktop app bundle all share::

    <app root>/
      .env.template            canonical env defaults (SSOT for ports etc.)
      .env                     operator overrides (optional)
      package.json             published version (``company version sync``)
      .opencompany/workflows/  shipped example workflow seeds
      client/dist/             built SPA the backend serves on one port
      server/                  this code

Historically every module that needed the root climbed
``Path(__file__).resolve().parents[N]`` on its own. That works only while
the code sits at a fixed depth inside a checkout. A desktop shell relocates
the tree into an app bundle and needs to point user-writable files (the
``.env`` overrides) somewhere the bundle is not, so the root and the two
env files are resolved *here* and nowhere else:

- ``OPENCOMPANY_APP_ROOT``    overrides :func:`app_root` (default: the
  checkout root, i.e. two levels above this file).
- ``OPENCOMPANY_CLIENT_DIST`` overrides :func:`client_dist`
  (default: ``<app root>/client/dist``).
- ``OPENCOMPANY_ENV_FILE``    overrides :func:`env_file_path`
  (default: ``<app root>/.env``). The desktop shell points this at a file
  in its writable data directory because the bundle is read-only.
- ``OPENCOMPANY_ENV_TEMPLATE`` overrides :func:`env_template_path`
  (default: ``<app root>/.env.template``).

Stdlib-only and import-side-effect-free: ``core.env_defaults`` and
``core.config`` import this at module load, before logging exists, and
the stubbed-core test environment file-loads it directly.

``tests/test_no_root_climb.py`` locks the contract: no other module under
``server/`` may climb above ``server/`` with ``parents[N]`` or a
``.parent`` chain.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path

# server/core/approot.py -> parents[1] is server/, parents[2] is the checkout root.
_SERVER_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_APP_ROOT = _SERVER_ROOT.parent


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def app_root() -> Path:
    """Absolute path of the application tree root (see module docstring)."""
    return _env_path("OPENCOMPANY_APP_ROOT") or _DEFAULT_APP_ROOT


def server_root() -> Path:
    """Absolute path of the ``server/`` package directory.

    Always the directory this code actually runs from — never derived from
    :func:`app_root`, so a mis-set ``OPENCOMPANY_APP_ROOT`` cannot make the
    backend look for its own config files somewhere else.
    """
    return _SERVER_ROOT


def client_dist() -> Path:
    """Directory holding the built SPA (``index.html`` + ``assets/``)."""
    return _env_path("OPENCOMPANY_CLIENT_DIST") or (app_root() / "client" / "dist")


def resolve_static_asset(base_dir: Path, relative: str) -> str | None:
    """Return the absolute path of ``relative`` inside ``base_dir``, or ``None``.

    Built for the SPA fallback route, which maps a request path onto the
    built client directory. The request path is attacker-controlled, so the
    joined path is normalised with :func:`os.path.normpath` and then
    required to start with the (real) base directory plus a separator; a
    ``..`` escape, an absolute path, or a prefix-sibling such as
    ``client/dist2`` all fail that test. ``None`` also covers a path that
    resolves inside the directory but is not a regular file, so callers
    fall through to their default (the SPA shell).
    """
    if not relative:
        return None
    root = os.path.realpath(str(base_dir))
    candidate = os.path.normpath(os.path.join(root, relative))
    if not candidate.startswith(root + os.sep):
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


def env_template_path() -> Path:
    """The canonical ``.env.template`` (baseline for every env var)."""
    return _env_path("OPENCOMPANY_ENV_TEMPLATE") or (app_root() / ".env.template")


def env_file_path() -> Path:
    """The operator's ``.env`` overrides. May not exist."""
    return _env_path("OPENCOMPANY_ENV_FILE") or (app_root() / ".env")


def package_json_path() -> Path:
    """The root ``package.json`` carrying the published version."""
    return app_root() / "package.json"


@functools.lru_cache(maxsize=1)
def app_version() -> str:
    """The published OpenCompany version, read from the root ``package.json``.

    That file is the single source of truth (``company version sync`` writes it
    from the git tag), and it ships inside the npm package one level above
    ``server/``. Never hardcode a literal here: ``/health`` once reported a
    stale ``3.3.0`` while the package was ``0.1.1``.
    """
    try:
        pkg = json.loads(package_json_path().read_text(encoding="utf-8"))
        return str(pkg.get("version") or "0.0.0")
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


def example_workflows_root() -> Path:
    """Parent of the shipped seed workflows (``<app root>/.opencompany``)."""
    return app_root() / ".opencompany"


__all__ = [
    "app_root",
    "server_root",
    "client_dist",
    "resolve_static_asset",
    "env_template_path",
    "env_file_path",
    "package_json_path",
    "app_version",
    "example_workflows_root",
]
