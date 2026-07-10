"""GitHub CLI (`gh`) downloader — pooch-driven, project-local.

Mirrors :mod:`services.temporal._install` (the repo's shared pattern
for fetching official CLI binaries): ``pooch.retrieve`` handles the
download, caching (re-runs are instant cache hits keyed by filename),
and archive extraction; we contribute only the pinned release URL and
the per-platform asset map.

The binary lands under ``<DATA_DIR>/packages/gh/`` — the same
:func:`core.paths.package_dir` root that holds the Stripe and Temporal
binaries. The system-global gh is deliberately never consulted, so
node behavior doesn't depend on whatever version is on the operator's
PATH. Auth state is unaffected by which binary runs: gh's config +
credential-store paths are user-level and shared, so a session created
by the user's own ``gh auth login`` in a terminal is visible to this
local binary too.
"""

from __future__ import annotations

import asyncio
import platform
import stat
from pathlib import Path
from typing import Optional, Tuple

import pooch

from core.logging import get_logger

logger = get_logger(__name__)

_VERSION = "2.96.0"
_RELEASE_BASE = f"https://github.com/cli/cli/releases/download/v{_VERSION}"

# (system, machine) -> (asset filename, binary name inside the archive's bin/).
# Note gh's release naming: linux assets are tar.gz, windows AND macOS are
# zips, with capital-S `macOS` in the asset name.
_ASSETS: dict[Tuple[str, str], Tuple[str, str]] = {
    ("Windows", "AMD64"): (f"gh_{_VERSION}_windows_amd64.zip", "gh.exe"),
    ("Windows", "ARM64"): (f"gh_{_VERSION}_windows_arm64.zip", "gh.exe"),
    ("Linux", "x86_64"): (f"gh_{_VERSION}_linux_amd64.tar.gz", "gh"),
    ("Linux", "aarch64"): (f"gh_{_VERSION}_linux_arm64.tar.gz", "gh"),
    ("Linux", "arm64"): (f"gh_{_VERSION}_linux_arm64.tar.gz", "gh"),
    ("Darwin", "x86_64"): (f"gh_{_VERSION}_macOS_amd64.zip", "gh"),
    ("Darwin", "arm64"): (f"gh_{_VERSION}_macOS_arm64.zip", "gh"),
}

_cached_path: Optional[Path] = None
_install_lock = asyncio.Lock()


def _package_root() -> Path:
    from core.paths import package_dir

    p = package_dir("gh")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _platform_asset() -> Tuple[str, str]:
    key = (platform.system(), platform.machine())
    asset = _ASSETS.get(key)
    if asset is None:
        raise RuntimeError(f"No prebuilt gh CLI for {key}. Supported: {sorted(_ASSETS)}. See https://cli.github.com")
    return asset


def _extracted_binary_path() -> Path:
    """Deterministic post-extraction location: pooch's Unzip/Untar
    processors extract next to the archive under ``<fname>.unzip`` /
    ``<fname>.untar``, preserving the archive's inner
    ``gh_<V>_<os>_<arch>/bin/`` layout."""
    asset_name, binary_name = _platform_asset()
    suffix = ".unzip" if asset_name.endswith(".zip") else ".untar"
    inner_root = asset_name.removesuffix(".zip").removesuffix(".tar.gz")
    return _package_root() / f"{asset_name}{suffix}" / inner_root / "bin" / binary_name


def gh_cli_path() -> Optional[Path]:
    """Sync getter for the project-local binary — the already-installed
    copy, without downloading. ``None`` when never installed."""
    global _cached_path
    if _cached_path and _cached_path.exists():
        return _cached_path
    target = _extracted_binary_path()
    if target.exists():
        _cached_path = target
        return target
    return None


async def ensure_gh_cli() -> Path:
    """Return absolute path to the project-local gh binary, downloading
    the pinned release on miss. Idempotent + concurrent-safe."""
    global _cached_path
    existing = gh_cli_path()
    if existing:
        return existing

    async with _install_lock:
        existing = gh_cli_path()
        if existing:
            return existing
        binary = await asyncio.to_thread(_fetch_cli_sync)
        _cached_path = binary
        return binary


def _fetch_cli_sync() -> Path:
    """Download + extract the pinned gh release via pooch.

    ``known_hash=None``: the release URL is version-pinned and served
    over TLS from GitHub's CDN (temporal precedent — transport
    integrity without hand-maintaining per-platform hashes).
    """
    asset_name, binary_name = _platform_asset()
    url = f"{_RELEASE_BASE}/{asset_name}"
    logger.info("[GitHub] downloading gh CLI v%s from %s", _VERSION, url)

    processor = pooch.Unzip() if asset_name.endswith(".zip") else pooch.Untar()
    extracted = pooch.retrieve(
        url=url,
        known_hash=None,
        path=_package_root(),
        fname=asset_name,
        processor=processor,
        # requests' timeout is per-socket-read, not total download time
        # (temporal precedent: pooch's 30s default killed slow links).
        downloader=pooch.HTTPDownloader(timeout=300, progressbar=True),
    )

    match = next((Path(p) for p in extracted if Path(p).name == binary_name), None)
    if match is None:
        raise RuntimeError(f"[GitHub] gh binary {binary_name!r} not found in {asset_name}. Extracted: {[Path(p).name for p in extracted]}")
    if platform.system() != "Windows":
        match.chmod(match.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    logger.info("[GitHub] gh CLI installed at %s", match)
    return match


__all__ = ["ensure_gh_cli", "gh_cli_path"]
