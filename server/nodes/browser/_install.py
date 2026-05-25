"""``agent-browser`` local install — mirrors ``claude_binary_path``.

Same pattern as ``nodes/agent/claude_code_agent/_oauth.py``: ``npm
install <pkg> --prefix <package_dir("browser")/npm>`` on first call,
return the resolved binary path. No coupling to the workspace
``package.json``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Optional

from core.logging import get_logger
from core.paths import package_dir

logger = get_logger(__name__)

_NPM_SPEC = "agent-browser@latest"


def agent_browser_binary_path() -> Optional[str]:
    """Return path to the agent-browser CLI, installing on miss."""
    npm_root = package_dir("browser") / "npm"
    bin_name = "agent-browser.cmd" if sys.platform == "win32" else "agent-browser"
    bin_path = npm_root / "node_modules" / ".bin" / bin_name

    if bin_path.exists():
        return str(bin_path)

    npm_cmd = shutil.which("npm")
    if not npm_cmd:
        logger.warning("[browser] npm not on PATH; cannot install %s", _NPM_SPEC)
        return None

    logger.info("[browser] installing %s into %s", _NPM_SPEC, npm_root)
    npm_root.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [npm_cmd, "install", _NPM_SPEC, "--prefix", str(npm_root), "--no-audit", "--no-fund"],
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
