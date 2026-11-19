"""Locate the Pi host OpenCompany ships: one Bun bundle with its SDK inlined."""

from __future__ import annotations

import json
from pathlib import Path

from temporalio.exceptions import ApplicationError


def _missing(message: str):
    return ApplicationError(message, type="PiRuntimeMissing", non_retryable=True)


def resolve_pi_host() -> Path:
    try:
        return _resolve_pi_host()
    except ApplicationError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise _missing("The packaged Pi runtime is unreadable or incompatible. Rebuild OpenCompany.") from exc


def _resolve_pi_host() -> Path:
    root = Path(__file__).resolve().parent.parent / "pi"
    # server/pi/scripts/build.ts writes the bundle. It needs no node_modules,
    # so there is nothing to verify, unpack or cache before running it.
    bundle = root / "dist" / "index.js"
    if bundle.is_file():
        return bundle
    # A source checkout that has not been built runs the host from source.
    source = root / "src" / "index.ts"
    package = root / "node_modules" / "@earendil-works" / "pi-coding-agent" / "package.json"
    if source.is_file() and package.is_file():
        metadata = json.loads(package.read_text(encoding="utf-8"))
        if isinstance(metadata, dict) and metadata.get("version") == "1.1.0":
            return source
    raise _missing("Pi runtime assets are missing. Rebuild OpenCompany before running Coding Agent.")


def resolve_pi_tool_host() -> Path:
    """Locate the separate effect runner used by Pi's canvas tool nodes.

    An older checkout may already contain the maintenance bundle but not the
    tool bundle. Its installed, pinned SDK still permits running tool source.
    Packaged installs have no source dependencies and must ship the new bundle.
    """
    try:
        root = Path(__file__).resolve().parent.parent / "pi"
        bundle = root / "dist" / "tool-host.js"
        if bundle.is_file():
            return bundle
        source = root / "src" / "tool-host.ts"
        package = root / "node_modules" / "@earendil-works" / "pi-coding-agent" / "package.json"
        if source.is_file() and package.is_file():
            metadata = json.loads(package.read_text(encoding="utf-8"))
            if isinstance(metadata, dict) and metadata.get("version") == "1.1.0":
                return source
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise _missing("Pi tool runtime assets are unreadable or incompatible. Rebuild OpenCompany.") from exc
    raise _missing("Pi tool runtime assets are missing. Rebuild OpenCompany before using Pi tool nodes.")


def pi_package_dir(host: Path) -> Path:
    """The folder the SDK reads its own package.json from (``PI_PACKAGE_DIR``)."""
    if host.suffix == ".ts":
        return host.parent.parent / "node_modules" / "@earendil-works" / "pi-coding-agent"
    # The build copies the SDK's manifest next to the bundle.
    return host.parent
