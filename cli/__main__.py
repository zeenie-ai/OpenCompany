"""Entry point for ``python -m cli``.

The npm package ships the ``cli/`` source directory without its
runtime dependencies (``typer`` / ``rich`` / ``anyio`` / ``psutil`` /
``platformdirs`` / ``pywin32`` on Windows). The previous bootstrap
``pip install``ed them against ``sys.executable``, which fails on any
modern PEP 668 distro (Ubuntu 24.04+, Debian 12+, Homebrew Python,
NixOS) with ``error: externally-managed-environment``.

Fix: provision a private venv at ``<package_root>/.cli-venv`` via
``uv`` (a hard install dependency verified by ``scripts/install.js``)
and re-exec under that venv's Python. ``uv pip install`` operates
inside the venv -- PEP 668 only governs the system interpreter, so
this is the canonical workaround documented by
https://peps.python.org/pep-0668/#guide-users-towards-virtual-environments.

``scripts/install.js`` provisions the same venv at postinstall time,
so end users never pay the first-run latency. This module is the
fallback for the source-checkout / broken-install path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_VENV_DIR = _ROOT / ".cli-venv"


def _venv_python() -> Path:
    """Platform-specific path to the venv's Python interpreter."""
    if sys.platform == "win32":
        return _VENV_DIR / "Scripts" / "python.exe"
    return _VENV_DIR / "bin" / "python"


def _running_under_venv() -> bool:
    """True if ``sys.executable`` is already the CLI venv's Python.

    Guards against infinite re-exec loops when the venv's interpreter
    itself can't import the deps (would mean ``uv pip install`` was
    silently no-op'd -- a real bug worth surfacing, not retrying).
    """
    try:
        return Path(sys.executable).resolve() == _venv_python().resolve()
    except (OSError, ValueError):
        return False


def _provision_venv() -> Path | None:
    """Create ``<ROOT>/.cli-venv`` and install CLI deps via ``uv``.

    Returns the venv's Python path on success, ``None`` if ``uv`` is
    missing or the install fails. Output is inherited (not captured)
    so the user sees ``uv``'s progress + any error context.
    """
    uv = shutil.which("uv")
    if not uv:
        return None
    try:
        if not _venv_python().exists():
            print(
                "company: provisioning CLI runtime venv (first run)...",
                file=sys.stderr,
            )
            subprocess.check_call([uv, "venv", "--quiet", str(_VENV_DIR)])
        subprocess.check_call(
            [
                uv,
                "pip",
                "install",
                "--quiet",
                "--python",
                str(_venv_python()),
                "-e",
                str(_ROOT),
            ]
        )
    except subprocess.CalledProcessError:
        return None
    return _venv_python() if _venv_python().exists() else None


def _reexec_in_venv() -> None:
    """Provision the venv if needed, then re-exec ``python -m cli`` under it."""
    venv_py = _venv_python() if _venv_python().exists() else _provision_venv()
    if not venv_py:
        sys.stderr.write(
            "company: runtime dependencies are missing and the CLI venv\n"
            "  could not be provisioned. Install uv\n"
            "  (https://docs.astral.sh/uv/getting-started/installation/)\n"
            "  and re-run, or run `company build` to regenerate the venv.\n"
        )
        sys.exit(1)
    os.execv(str(venv_py), [str(venv_py), "-m", "cli", *sys.argv[1:]])


try:
    from cli.cli import app
except ImportError:
    if _running_under_venv():
        raise
    _reexec_in_venv()


if __name__ == "__main__":
    app()
