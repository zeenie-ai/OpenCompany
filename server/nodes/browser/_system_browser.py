"""Discover an installed browser without opening a personal browser session."""

from __future__ import annotations

import asyncio
import ctypes
import os
from pathlib import Path
import re
import shutil
import sys

from services.plugin.base import NodeUserError


def version_major(version: str) -> int:
    match = re.search(r"\b(\d+)\.\d+\.\d+\.\d+\b", version)
    if not match:
        raise NodeUserError(f"Could not determine the installed browser version: {version!r}")
    return int(match.group(1))


def _candidates():
    if sys.platform == "win32":
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for name, suffix in (("chrome", "Google/Chrome/Application/chrome.exe"), ("edge", "Microsoft/Edge/Application/msedge.exe"), ("chromium", "Chromium/Application/chrome.exe")):
            for root in roots:
                if root:
                    yield Path(root) / suffix, name
    elif sys.platform == "darwin":
        for name, app, binary in (("chrome", "Google Chrome", "Google Chrome"), ("edge", "Microsoft Edge", "Microsoft Edge"), ("chromium", "Chromium", "Chromium")):
            for root in (Path("/Applications"), Path.home() / "Applications"):
                yield root / f"{app}.app/Contents/MacOS/{binary}", name
    else:
        for name, commands in (("chrome", ("google-chrome", "google-chrome-stable")), ("edge", ("microsoft-edge", "microsoft-edge-stable")), ("chromium", ("chromium", "chromium-browser"))):
            for command in commands:
                found = shutil.which(command)
                if found:
                    yield Path(found), name


def discover_browsers(override: str = "") -> list[tuple[Path, str]]:
    """Installed candidates in preference order; an explicit path is exclusive."""
    if override.strip():
        path = Path(override).expanduser()
        if not path.is_file():
            raise NodeUserError(f"BROWSER_CHROME_PATH does not exist: {path}")
        candidates = [(path, "override")]
    else:
        candidates = _candidates()
    found = []
    for path, source in candidates:
        if not path.is_file():
            continue
        if "/snap/" in path.as_posix() or "/snap/" in path.resolve().as_posix():
            if override:
                raise NodeUserError("BROWSER_CHROME_PATH points at a snap browser. Install Chrome/Chromium outside snap so it can access OpenCompany's profile.")
            continue
        if not any(existing == path for existing, _ in found):
            found.append((path, source))
    if found:
        return found
    raise NodeUserError("No installed Chrome, Edge or Chromium was found. Install one or set BROWSER_CHROME_PATH to its executable. To download the managed test browser instead, explicitly set BROWSER_RUNTIME=testing.")


def discover_browser(override: str = "") -> tuple[Path, str]:
    return discover_browsers(override)[0]


async def select_browser(*, min_major: int, override: str = "") -> tuple[Path, str, str]:
    """Prefer Chrome, but skip browsers too old to open the requested profile."""
    rejected = []
    for path, source in discover_browsers(override):
        try:
            version = await browser_version(path)
            if version_major(version) >= min_major:
                return path, source, version
            rejected.append(f"{source}: {version}")
        except (NodeUserError, OSError, TimeoutError) as exc:
            rejected.append(f"{source}: {exc}")
    selection = "BROWSER_CHROME_PATH" if override else "The installed browsers"
    raise NodeUserError(
        f"{selection} cannot open this profile: it requires version {min_major} or newer. "
        f"Found: {'; '.join(rejected)}. Update your browser or select a compatible BROWSER_CHROME_PATH. "
        "The existing profile has been preserved."
    )


def _windows_version(path: Path) -> str:
    # chrome.exe --version can open a GUI on Windows. Read VERSIONINFO instead.
    from ctypes import wintypes

    dll = ctypes.WinDLL("version", use_last_error=True)
    dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    dll.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    dll.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    size = dll.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise NodeUserError(f"Cannot read browser version metadata: {path}")
    data = ctypes.create_string_buffer(size)
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not dll.GetFileVersionInfoW(str(path), 0, size, data) or not dll.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 16:
        raise NodeUserError(f"Cannot read browser version metadata: {path}")
    info = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    if info[0] != 0xFEEF04BD:
        raise NodeUserError(f"Invalid browser version metadata: {path}")
    ms, ls = info[2], info[3]
    return f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"


async def browser_version(path: Path) -> str:
    if sys.platform == "win32":
        return await asyncio.to_thread(_windows_version, path)
    process = await asyncio.create_subprocess_exec(str(path), "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    match = re.search(r"\b\d+\.\d+\.\d+\.\d+\b", stdout.decode(errors="replace"))
    if process.returncode or not match:
        raise NodeUserError(f"Cannot determine browser version from {path}; check BROWSER_CHROME_PATH.")
    return match.group(0)
