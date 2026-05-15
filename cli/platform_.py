"""Platform / shell / venv detection helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
IS_WSL = IS_LINUX and ("WSL_DISTRO_NAME" in os.environ or "WSLENV" in os.environ)
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
    """Resolve the project root from a module under ``machina/``.

    Layout: ``<project_root>/machina/<this_file>`` -> parents[1].
    """
    return Path(__file__).resolve().parents[1]
