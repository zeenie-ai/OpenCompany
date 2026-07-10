"""``browser-harness`` local install — a uv *tool* (Python), not npm.

Unlike ``agent-browser`` (npm, shared tree) the harness is a PyPI
package installed as an isolated uv tool. It lands under
:func:`core.paths.package_dir`::

    <DATA_DIR>/packages/browser-harness/tools/   (venv-per-tool)
    <DATA_DIR>/packages/browser-harness/bin/     (browser-harness[.exe])

— the same non-npm-binary precedent as ``packages/stripe/`` and
``packages/temporal/``. ``uv tool install`` is idempotent and owns
upgrades; we never hand-manage the venv.

Windows note (verified 2026-07-10 spike): upstream is fully
Windows-aware — daemon IPC is AF_UNIX on POSIX and token-authenticated
TCP loopback on Windows (``browser_harness/_ipc.py`` line 1).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Optional

from core.logging import get_logger
from core.paths import package_dir

logger = get_logger(__name__)

_PYPI_SPEC = "browser-harness"
_PYTHON_PIN = "3.12"  # upstream install.md pins 3.12


def _bin_path():
    name = "browser-harness.exe" if sys.platform == "win32" else "browser-harness"
    return package_dir("browser-harness") / "bin" / name


def browser_harness_binary_path() -> Optional[str]:
    """Return path to the browser-harness CLI, installing on miss."""
    bin_path = _bin_path()
    if bin_path.exists():
        return str(bin_path)

    uv_cmd = shutil.which("uv")
    if not uv_cmd:
        logger.warning("[browser-harness] uv not on PATH; cannot install %s", _PYPI_SPEC)
        return None

    root = package_dir("browser-harness")
    root.mkdir(parents=True, exist_ok=True)
    logger.info("[browser-harness] installing %s into %s", _PYPI_SPEC, root)
    result = subprocess.run(
        [uv_cmd, "tool", "install", "--python", _PYTHON_PIN, "--upgrade", _PYPI_SPEC],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "UV_TOOL_DIR": str(root / "tools"),
            "UV_TOOL_BIN_DIR": str(root / "bin"),
        },
    )
    if result.returncode != 0 or not bin_path.exists():
        logger.error("[browser-harness] uv tool install failed: %s", result.stderr.strip())
        return None

    logger.info("[browser-harness] installed at %s", bin_path)
    return str(bin_path)


__all__ = ["browser_harness_binary_path"]
