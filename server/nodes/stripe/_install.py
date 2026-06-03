"""Stripe CLI auto-installer.

On first use, downloads the official Stripe CLI binary from GitHub
releases (https://github.com/stripe/stripe-cli/releases) and caches
it under :func:`core.paths.package_dir` (``<OS cache>/MachinaOs/stripe/
bin/stripe[.exe]``). A system install on PATH (brew / scoop / apt /
direct binary) is preferred — the download path only fires when no
system binary is found.

Note: the cache directory above is the *binary* install location and
is separate from the ``stripe listen`` daemon's working directory,
which lives at ``<DATA_DIR>/daemons/_stripe/`` (see
:func:`core.paths.daemons_dir`). The two were not always separate —
prior to the daemon-cwd cleanup, the daemon ran from
``<DATA_DIR>/workspaces/_stripe/`` and polluted per-workflow scratch
space with framework state.

Pin a version here when bumping; pre-built archives are signed by
Stripe and served over GitHub's CDN.
"""

from __future__ import annotations

import asyncio
import io
import platform
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Optional, Tuple

import httpx

from core.logging import get_logger

logger = get_logger(__name__)


_VERSION = "1.40.9"
_RELEASE_BASE = f"https://github.com/stripe/stripe-cli/releases/download/v{_VERSION}"

# (system, machine) → (asset filename, archive type, member name to extract)
_ASSETS: dict[Tuple[str, str], Tuple[str, str, str]] = {
    ("Windows", "AMD64"): (f"stripe_{_VERSION}_windows_x86_64.zip", "zip", "stripe.exe"),
    ("Linux", "x86_64"): (f"stripe_{_VERSION}_linux_x86_64.tar.gz", "tar", "stripe"),
    ("Linux", "aarch64"): (f"stripe_{_VERSION}_linux_arm64.tar.gz", "tar", "stripe"),
    ("Linux", "arm64"): (f"stripe_{_VERSION}_linux_arm64.tar.gz", "tar", "stripe"),
    ("Darwin", "x86_64"): (f"stripe_{_VERSION}_mac-os_x86_64.tar.gz", "tar", "stripe"),
    ("Darwin", "arm64"): (f"stripe_{_VERSION}_mac-os_arm64.tar.gz", "tar", "stripe"),
}


_cached_path: Optional[Path] = None
_install_lock = asyncio.Lock()


def _bin_dir() -> Path:
    from core.paths import package_dir

    p = package_dir("stripe") / "bin"
    p.mkdir(parents=True, exist_ok=True)
    return p


def stripe_cli_path() -> Optional[Path]:
    """Sync getter for the resolved binary path. Returns ``None`` if
    :func:`ensure_stripe_cli` hasn't run yet (or never resolved)."""
    return _cached_path


async def ensure_stripe_cli() -> Path:
    """Return absolute path to the stripe binary, downloading it if
    necessary. Idempotent + concurrent-safe.

    Resolution order:
      1. Cached path from a prior call (in-process).
      2. ``shutil.which("stripe")`` — system install on PATH.
      3. Previously-downloaded copy at ``package_dir("stripe")/bin/
         stripe[.exe]`` (OS cache, see :func:`core.paths.package_dir`).
      4. Fresh download from GitHub releases under :data:`_VERSION`
         into the same OS-cache directory.
    """
    global _cached_path
    if _cached_path and _cached_path.exists():
        return _cached_path

    sys_path = shutil.which("stripe")
    if sys_path:
        _cached_path = Path(sys_path)
        logger.info("[Stripe] using system CLI at %s", _cached_path)
        return _cached_path

    binary_name = "stripe.exe" if platform.system() == "Windows" else "stripe"
    target = _bin_dir() / binary_name

    async with _install_lock:
        if _cached_path and _cached_path.exists():
            return _cached_path
        if target.exists():
            _cached_path = target
            return target
        await _download_release(target)
        _cached_path = target
        return target


async def _download_release(target: Path) -> None:
    key = (platform.system(), platform.machine())
    asset = _ASSETS.get(key)
    if asset is None:
        raise RuntimeError(f"No prebuilt Stripe CLI for {key}. Install manually from " "https://stripe.com/docs/stripe-cli#install")
    asset_name, kind, member = asset
    url = f"{_RELEASE_BASE}/{asset_name}"
    logger.info("[Stripe] downloading CLI v%s from %s", _VERSION, url)

    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        archive = resp.content

    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            target.write_bytes(z.read(member))
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as t:
            f = t.extractfile(member)
            if f is None:
                raise RuntimeError(f"Member {member!r} missing from {asset_name}")
            target.write_bytes(f.read())

    if platform.system() != "Windows":
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    logger.info("[Stripe] CLI installed to %s", target)
