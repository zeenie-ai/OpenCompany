"""The pinned Temporal CLI, and the database its dev server may open.

Downloads the official ``temporal`` CLI release named in
``server/config/temporal_cli.json`` (version, server version and each
asset's SHA-256) into ``<DATA_DIR>/packages/temporal/<version>/`` — the
same root that holds the Stripe and agent-browser binaries (see
:func:`core.paths.package_dir`). pooch verifies the archive against the
pinned hash before extracting it (zip on Windows, tar.gz elsewhere), so
a re-run is a cache hit and a tampered or truncated download fails.

A new Temporal server minor starts from an empty database (RFC-0005 E16).
``temporal.db.server.json`` beside the database names the server that
created it and the task-queue partitions it was created with, and
:func:`prepare_database` runs before every spawn. A database with no
marker, with one naming another server minor than the pin, or with other
partition values than the pin's ``dynamic_config`` (partitions can be
lowered safely only on a new database, RFC-0006 T17), is deleted together
with its ``-wal`` and ``-shm`` files, and every CLI file that is not the
pin's goes with it. Nothing is migrated: generations whose controller lived
in the old database fail with ``controller_execution_missing``, and owners
Reset and Start them. A patch release of the same minor keeps the database.

The downloaded ``temporal`` CLI powers ``temporal server start-dev``
(the SQLite dev server) and ad-hoc workflow / operator commands.
"""

from __future__ import annotations

import asyncio
import json
import platform
import re
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pooch

from core.logging import get_logger

logger = get_logger(__name__)

# The one place the CLI version, its server version and the per-platform
# archive hashes live. Bumping it is a JSON edit plus checksums.txt.
PIN_PATH = Path(__file__).resolve().parents[2] / "config" / "temporal_cli.json"

_CLI_PLATFORM_MAP: dict[tuple[str, str], tuple[str, str]] = {
    # platform.system, platform.machine -> (release os, release arch)
    ("Linux", "x86_64"): ("linux", "amd64"),
    ("Linux", "aarch64"): ("linux", "arm64"),
    ("Darwin", "x86_64"): ("darwin", "amd64"),
    ("Darwin", "arm64"): ("darwin", "arm64"),
    ("Windows", "AMD64"): ("windows", "amd64"),
    ("Windows", "ARM64"): ("windows", "arm64"),
}


def cli_pin() -> dict[str, Any]:
    """The pinned release: ``version``, ``server_version``, ``base``, ``assets``."""
    return json.loads(PIN_PATH.read_text(encoding="utf-8"))


def _cache_dir() -> Path:
    """Per-service install root under :func:`core.paths.package_dir`.

    Lands at ``<DATA_DIR>/packages/temporal/`` — same root that holds
    ``stripe`` and ``browser`` binaries. Each pinned version gets its own
    subfolder.
    """
    from core.paths import package_dir

    p = package_dir("temporal")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _binary_name() -> str:
    return "temporal.exe" if platform.system() == "Windows" else "temporal"


def binary_installed() -> bool:
    """True when the pinned CLI is already on disk, so starting needs no download."""
    root = _cache_dir() / cli_pin()["version"]
    return root.is_dir() and any(p.is_file() for p in root.rglob(_binary_name()))


_cached: dict[str, Path] | None = None
_lock = asyncio.Lock()


async def ensure_temporal_binaries() -> dict[str, Path]:
    """Return ``{"temporal": Path}`` — the pinned ``temporal`` CLI binary.

    Idempotent — first call downloads, subsequent calls hit the pooch
    cache under ``<DATA_DIR>/packages/temporal/<version>/``.
    Async-locked so concurrent callers don't double-download.
    """
    global _cached
    async with _lock:
        if _cached is not None and _cached["temporal"].is_file() and _cached["temporal"].resolve().is_relative_to(_cache_dir().resolve()):
            return _cached
        cli_path = await asyncio.to_thread(_fetch_cli_sync)
        _cached = {"temporal": cli_path}
        logger.info(
            "[Temporal install] binary ready: %s",
            {k: str(v) for k, v in _cached.items()},
        )
        return _cached


def _fetch_cli_sync() -> Path:
    """Download the pinned CLI archive and return the binary path.

    ``known_hash`` makes pooch check the archive against the pinned
    SHA-256: a mismatch raises instead of extracting, and a cached archive
    that no longer matches is downloaded again.
    """
    key = (platform.system(), platform.machine())
    if key not in _CLI_PLATFORM_MAP:
        raise RuntimeError(f"[Temporal install] Unsupported platform for CLI: {key}. " f"Supported: {sorted(_CLI_PLATFORM_MAP.keys())}")
    release_os, release_arch = _CLI_PLATFORM_MAP[key]
    pin = cli_pin()
    asset = pin["assets"][f"{release_os}_{release_arch}"]
    install_dir = _cache_dir() / pin["version"]

    extracted = pooch.retrieve(
        url=pin["base"].format(version=pin["version"]) + asset["file"],
        known_hash=f"sha256:{asset['sha256']}",
        path=install_dir,
        fname=asset["file"],
        processor=pooch.Unzip() if asset["file"].endswith(".zip") else pooch.Untar(),
        # requests' timeout is per-socket-read, not total download time —
        # only a 300s zero-byte stall aborts, so slow links can finish.
        # pooch's default is 30s, which killed npm installs on slow/WSL
        # links. progressbar must live on the downloader: retrieve()
        # ignores its own progressbar kwarg when downloader= is explicit.
        downloader=pooch.HTTPDownloader(timeout=300, progressbar=True),
    )

    target = _binary_name()
    match = next((Path(p) for p in extracted if Path(p).name == target), None)
    if match is None:
        raise RuntimeError(
            f"[Temporal install] CLI binary {target!r} not found in archive. " f"Extracted files: {[Path(p).name for p in extracted]}"
        )
    if platform.system() != "Windows":
        mode = match.stat().st_mode
        match.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return match


def marker_path(db_path: Path) -> Path:
    """The file beside the database that names the server that created it."""
    return db_path.with_name(db_path.name + ".server.json")


# The dynamic-config values a database keeps from its creation: lowering read
# partitions on an existing database strands the tasks in the removed ones.
_CREATION_KEYS = ("matching.numTaskqueueReadPartitions", "matching.numTaskqueueWritePartitions")


def _partitions(pin: dict[str, Any]) -> dict[str, Any]:
    return {key: pin["dynamic_config"][key] for key in _CREATION_KEYS}


def prepare_database(db_path: Path) -> None:
    """Start from an empty database unless the pin's server minor created it.

    Runs before every spawn. A database whose marker names the pin's server
    minor and partition values is kept. Otherwise the database, its ``-wal``
    and ``-shm`` files and every CLI file that is not the pin's are deleted,
    and the pin's marker is written, so ``start-dev`` creates an empty
    database for the pinned server. A database that cannot be deleted stops
    the start: the pinned server must never open another minor's schema.
    """
    pin = cli_pin()
    marker = marker_path(db_path)
    recorded = _read_marker(marker)
    if (
        recorded is not None
        and _server_minor(recorded.get("server_version")) == _server_minor(pin["server_version"])
        and recorded.get("partitions") == _partitions(pin)
    ):
        return
    stale = [path for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")) if path.exists()]
    for path in stale:
        path.unlink()
    if stale:
        logger.warning(
            "[Temporal install] Deleted a Temporal database the pinned server %s did not create (RFC-0005 E16); "
            "employees whose controller lived in it need Reset and Start: %s",
            pin["server_version"],
            db_path,
        )
    _remove_unpinned_clis(pin["version"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "cli_version": pin["version"],
        "server_version": pin["server_version"],
        "partitions": _partitions(pin),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    marker.write_text(json.dumps(record), encoding="utf-8")


def _remove_unpinned_clis(version: str) -> None:
    """Delete every CLI file in the package root except the pinned version's folder.

    These are unused once the database is reset, so a file that cannot be
    deleted is logged and left for the next reset rather than stopping the start.
    """
    for entry in _cache_dir().iterdir():
        if entry.name == version:
            continue
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError as exc:
            logger.warning("[Temporal install] Could not delete an old Temporal CLI file %s: %s", entry, exc)


def _read_marker(marker: Path) -> dict[str, Any] | None:
    """The marker's record, or ``None`` when it is missing or unreadable."""
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _server_minor(version: object) -> str | None:
    if not isinstance(version, str):
        return None
    return ".".join(version.split(".")[:2]) if re.fullmatch(r"\d+\.\d+\.\d+", version) else None


__all__ = ["binary_installed", "cli_pin", "ensure_temporal_binaries", "marker_path", "prepare_database"]


def _main() -> int:
    """Standalone entry: ``python -m services.temporal._install``.

    Used by ``company build`` step [6/6] to materialise the Temporal
    CLI at build time instead of paying the download cost on first
    ``company start``. Fetches, verifies the binary exists on disk,
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
