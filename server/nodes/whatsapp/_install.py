"""``edgymeow`` (WhatsApp Go bridge) local install — landed in the
shared OpenCompany npm tree at :func:`core.paths.packages_dir`
(``<DATA_DIR>/packages/``).

All OpenCompany-managed npm packages (``edgymeow``,
``@anthropic-ai/claude-code``, ``agent-browser``) share a single
``<packages_dir>/node_modules/`` so npm manages them with one
``package.json`` + ``package-lock.json``. ``npm install <pkg>
--prefix <packages_dir>`` extends the shared tree idempotently.

Pre-fix this lived as a top-level ``edgymeow`` dep in the root
``package.json`` and landed at ``<repo>/node_modules/edgymeow/`` via
``pnpm install``. Operators had to re-run ``pnpm install`` after
``company clean`` to recover. After this move, the WhatsApp binary
follows the same on-demand install pattern as every other
OpenCompany-managed CLI.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Optional

from core.logging import get_logger
from core.paths import packages_dir

logger = get_logger(__name__)

# Pinned for reproducible WhatsApp Go-bridge ABI. Bump when the
# bridge's WebSocket / event schema changes.
_NPM_SPEC = "edgymeow@0.0.20"


def edgymeow_binary_path() -> Optional[str]:
    """Return path to the edgymeow Go binary, installing on miss.

    Returns ``None`` if npm is not on PATH or the install failed —
    callers should surface this as a user-visible error (the runtime
    can't spawn without it). Idempotent: subsequent calls hit the
    existing binary on disk.
    """
    root = packages_dir()
    bin_name = "edgymeow-server.exe" if sys.platform == "win32" else "edgymeow-server"
    bin_path = root / "node_modules" / "edgymeow" / "bin" / bin_name

    if bin_path.exists():
        return str(bin_path)

    npm_cmd = shutil.which("npm")
    if not npm_cmd:
        logger.warning("[whatsapp] npm not on PATH; cannot install %s", _NPM_SPEC)
        return None

    logger.info("[whatsapp] installing %s into shared tree %s", _NPM_SPEC, root)
    root.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [npm_cmd, "install", _NPM_SPEC, "--prefix", str(root), "--no-audit", "--no-fund"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not bin_path.exists():
        logger.error("[whatsapp] npm install failed: %s", result.stderr.strip())
        return None

    logger.info("[whatsapp] edgymeow installed at %s", bin_path)
    return str(bin_path)


__all__ = ["edgymeow_binary_path"]
