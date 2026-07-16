"""Cloudflare CLI (`cf`) auto-installer — npm, pinned, project-local.

The official Cloudflare CLI ships as the npm package ``cf`` (a
Technical Preview — "the next version of Wrangler"). It lands in the
shared OpenCompany npm tree at :func:`core.paths.packages_dir`
(``<DATA_DIR>/packages/``), the same single ``package.json`` +
``node_modules/`` that holds ``@anthropic-ai/claude-code`` / ``vercel``
/ ``agent-browser``. ``npm install <pkg> --prefix <packages_dir>``
extends the shared tree idempotently.

Unlike the vercel installer, the system-global ``cf`` is deliberately
NEVER consulted (the gh philosophy): the preview CLI's command surface
is schema-generated and shifts between versions (0.0.5's ``--ndjson``
is gone in 0.2.0; write ops changed shape), and this plugin's argv
builders are verified against the pinned version only. Auth state is
unaffected by which binary runs — cf's config paths are user-level and
shared, so a session created by the user's own ``cf auth login`` in a
terminal is visible to this pinned binary too.

Pin ``_NPM_SPEC`` when bumping and re-verify every wrapped command via
``cf <cmd> --help-full`` (whatsapp precedent — ``@latest`` makes cold
installs non-reproducible). cf requires Node >= 22, which is already
OpenCompany's engines floor.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from core.logging import get_logger
from core.paths import packages_dir

logger = get_logger(__name__)

_NPM_SPEC = "cf@0.2.0"

_cached_path: Optional[Path] = None
_install_lock = asyncio.Lock()


def cf_cli_path() -> Optional[Path]:
    """Sync getter for the project-local binary — the already-installed
    shim, without installing. ``None`` when never installed."""
    global _cached_path
    if _cached_path and _cached_path.exists():
        return _cached_path
    target = _shared_tree_bin()
    if target.exists():
        _cached_path = target
        return target
    return None


def _shared_tree_bin() -> Path:
    bin_name = "cf.cmd" if sys.platform == "win32" else "cf"
    return packages_dir() / "node_modules" / ".bin" / bin_name


def _npm_install() -> Path:
    """Blocking npm install into the shared tree. Raises on failure."""
    root = packages_dir()
    bin_path = _shared_tree_bin()

    npm_cmd = shutil.which("npm")
    if not npm_cmd:
        raise RuntimeError("npm not on PATH — install Node.js 22+ (the cf CLI ships via npm)")

    logger.info("[Cloudflare] installing %s into shared tree %s", _NPM_SPEC, root)
    root.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [npm_cmd, "install", _NPM_SPEC, "--prefix", str(root), "--no-audit", "--no-fund"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not bin_path.exists():
        raise RuntimeError(f"npm install {_NPM_SPEC} failed: {result.stderr.strip()[:500]}")

    logger.info("[Cloudflare] cf CLI installed at %s", bin_path)
    return bin_path


async def ensure_cf_cli() -> Path:
    """Return absolute path to the project-local cf binary, installing
    the pinned npm release on miss. Idempotent + concurrent-safe."""
    global _cached_path
    existing = cf_cli_path()
    if existing:
        return existing

    async with _install_lock:
        existing = cf_cli_path()
        if existing:
            return existing
        # npm install blocks for tens of seconds — keep the event loop free.
        installed = await asyncio.to_thread(_npm_install)
        _cached_path = installed
        return installed


__all__ = ["ensure_cf_cli", "cf_cli_path"]
