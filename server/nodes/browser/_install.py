"""``agent-browser`` local install — landed in the shared OpenCompany
npm tree at :func:`core.paths.packages_dir` (``<DATA_DIR>/packages/``).

All OpenCompany-managed npm packages (``agent-browser``,
``@anthropic-ai/claude-code``, ``edgymeow``) live under a single
``<packages_dir>/node_modules/`` so npm manages them with one
``package.json`` + ``package-lock.json`` rather than us carving out
per-service install trees. ``npm install <pkg> --prefix <packages_dir>``
extends the shared tree idempotently.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Optional

from core.logging import get_logger
from core.paths import packages_dir

logger = get_logger(__name__)

_NPM_SPEC = "agent-browser@latest"


def agent_browser_binary_path() -> Optional[str]:
    """Return path to the agent-browser CLI, installing on miss."""
    root = packages_dir()
    bin_name = "agent-browser.cmd" if sys.platform == "win32" else "agent-browser"
    bin_path = root / "node_modules" / ".bin" / bin_name

    if bin_path.exists():
        return str(bin_path)

    npm_cmd = shutil.which("npm")
    if not npm_cmd:
        logger.warning("[browser] npm not on PATH; cannot install %s", _NPM_SPEC)
        return None

    logger.info("[browser] installing %s into shared tree %s", _NPM_SPEC, root)
    root.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [npm_cmd, "install", _NPM_SPEC, "--prefix", str(root), "--no-audit", "--no-fund"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not bin_path.exists():
        logger.error("[browser] npm install failed: %s", result.stderr.strip())
        return None

    # Fetch the Chrome-for-Testing runtime (agent-browser's documented
    # post-install step — downloads ~150MB chromium on first use).
    runtime = subprocess.run(
        [str(bin_path), "install"],
        capture_output=True,
        text=True,
    )
    if runtime.returncode != 0:
        logger.warning("[browser] chromium runtime install failed: %s", runtime.stderr.strip())

    logger.info("[browser] agent-browser installed at %s", bin_path)
    return str(bin_path)


__all__ = ["agent_browser_binary_path"]
