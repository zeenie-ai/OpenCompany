"""Cross-platform Temporal CLI binary downloader using pooch.

Downloads the official ``temporal`` CLI from the URL documented at
https://docs.temporal.io/develop/python/set-up-your-local-python
(``temporal.download/cli/archive/latest?platform=<os>&arch=<arch>``).

The binary lands at ``<DATA_DIR>/packages/temporal/`` — the same root
that holds the Stripe and agent-browser binaries (see
:func:`core.paths.package_dir`). Pre-fix this used
``pooch.os_cache("machinaos-temporal")`` which landed the binary
under a separate ``%LOCALAPPDATA%\\machinaos-temporal\\Cache\\``
(Windows) / ``~/.cache/machinaos-temporal/`` (Linux) tree outside
the operator-visible ``~/.machina/`` root.

Pooch still drives archive extraction (zip on Windows, tar.gz
elsewhere); we just point its cache at our DATA_DIR-rooted package
dir. The ``latest`` URL rotates as new CLI versions ship, so the
download is unverified (TLS gives transport integrity); pooch caches
by local filename, not URL contents, so re-runs after the first
fetch are instant cache hits.

The downloaded ``temporal`` CLI powers ``temporal server start-dev``
(the SQLite/in-memory dev server) and ad-hoc workflow / operator
commands.
"""

from __future__ import annotations

import asyncio
import platform
import stat
from pathlib import Path

import pooch

from core.config import Settings
from core.logging import get_logger

logger = get_logger(__name__)

# Official Temporal CLI download URL. Per the docs at
# https://docs.temporal.io/develop/python/set-up-your-local-python the
# CLI archive lives at ``temporal.download/cli/archive/latest`` with
# ``platform`` and ``arch`` query parameters. Contains a single
# ``temporal`` binary.
_CLI_BASE_URL = "https://temporal.download/cli/archive/latest"
_CLI_PLATFORM_MAP: dict[tuple[str, str], tuple[str, str]] = {
    # platform.system, platform.machine -> (URL platform, URL arch)
    ("Linux", "x86_64"): ("linux", "amd64"),
    ("Linux", "aarch64"): ("linux", "arm64"),
    ("Darwin", "x86_64"): ("darwin", "amd64"),
    ("Darwin", "arm64"): ("darwin", "arm64"),
    ("Windows", "AMD64"): ("windows", "amd64"),
    ("Windows", "ARM64"): ("windows", "arm64"),
}

def _cache_dir() -> Path:
    """Per-service install dir under :func:`core.paths.package_dir`.

    Lands at ``<DATA_DIR>/packages/temporal/`` — same root that
    holds ``stripe`` and ``browser`` binaries. Pre-fix this used
    ``pooch.os_cache("machinaos-temporal")`` which landed the binary
    at ``%LOCALAPPDATA%\\machinaos-temporal\\Cache\\`` (Windows) /
    ``~/.cache/machinaos-temporal/`` (Linux) — outside the
    operator-visible ``~/.machina/`` tree.
    """
    from core.paths import package_dir

    p = package_dir("temporal")
    p.mkdir(parents=True, exist_ok=True)
    return p


_cached: dict[str, Path] | None = None
_lock = asyncio.Lock()


async def ensure_temporal_binaries(
    settings: Settings | None = None,
) -> dict[str, Path]:
    """Return ``{"temporal": Path}`` — the official ``temporal`` CLI binary.

    Idempotent — first call downloads, subsequent calls hit the pooch
    cache (XDG / OS-conventional dir). Async-locked so concurrent
    callers don't double-download.

    The ``settings`` argument is accepted for API symmetry but currently
    unused — the official ``latest`` URL has no version slot to override.
    """
    global _cached
    # settings accepted for API symmetry; no fields consulted (the
    # ``latest`` URL has no version slot).
    _ = settings

    async with _lock:
        if _cached is not None:
            return _cached
        cli_path = await asyncio.to_thread(_fetch_cli_sync)
        _cached = {"temporal": cli_path}
        logger.info(
            "[Temporal install] binary ready: %s",
            {k: str(v) for k, v in _cached.items()},
        )
        return _cached


def _fetch_cli_sync() -> Path:
    """Download the official ``temporal`` CLI archive and return the binary path.

    Uses ``pooch.retrieve`` with ``known_hash=None`` because the
    ``temporal.download/cli/archive/latest`` URL rotates as new CLI
    versions ship — pinning a SHA would defeat the "latest" semantics
    the official docs document. TLS gives us transport integrity.
    """
    key = (platform.system(), platform.machine())
    if key not in _CLI_PLATFORM_MAP:
        raise RuntimeError(f"[Temporal install] Unsupported platform for CLI: {key}. " f"Supported: {sorted(_CLI_PLATFORM_MAP.keys())}")
    url_platform, url_arch = _CLI_PLATFORM_MAP[key]
    url = f"{_CLI_BASE_URL}?platform={url_platform}&arch={url_arch}"

    is_windows = platform.system() == "Windows"
    fname = f"temporal_cli_latest_{url_platform}_{url_arch}.{'zip' if is_windows else 'tar.gz'}"
    processor = pooch.Unzip() if is_windows else pooch.Untar()

    extracted = pooch.retrieve(
        url=url,
        known_hash=None,
        path=_cache_dir(),
        fname=fname,
        processor=processor,
        # requests' timeout is per-socket-read, not total download time —
        # only a 300s zero-byte stall aborts, so slow links can finish.
        # pooch's default is 30s, which killed npm installs on slow/WSL
        # links. progressbar must live on the downloader: retrieve()
        # ignores its own progressbar kwarg when downloader= is explicit.
        downloader=pooch.HTTPDownloader(timeout=300, progressbar=True),
    )

    target = "temporal.exe" if is_windows else "temporal"
    match = next((Path(p) for p in extracted if Path(p).name == target), None)
    if match is None:
        raise RuntimeError(
            f"[Temporal install] CLI binary {target!r} not found in archive. " f"Extracted files: {[Path(p).name for p in extracted]}"
        )
    if not is_windows:
        mode = match.stat().st_mode
        match.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return match


__all__ = ["ensure_temporal_binaries"]


def _main() -> int:
    """Standalone entry: ``python -m services.temporal._install``.

    Used by ``machina build`` step [6/6] to materialise the Temporal
    CLI at build time instead of paying the download cost on first
    ``machina start``. Fetches, verifies the binary exists on disk,
    prints the resolved location. Non-zero exit on any failure.
    """
    import sys as _sys

    try:
        paths = asyncio.run(ensure_temporal_binaries())
    except Exception as exc:  # noqa: BLE001 — propagate to non-zero exit
        print(f"[Temporal install] {exc}", file=_sys.stderr)
        return 1

    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        print(
            f"[Temporal install] binaries missing after fetch: {missing}",
            file=_sys.stderr,
        )
        return 1

    for name, path in paths.items():
        print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
