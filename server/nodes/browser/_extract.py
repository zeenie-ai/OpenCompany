"""Unpack a Chrome for Testing zip without breaking it.

``pooch.Unzip`` (and a plain ``zipfile.extractall``) writes every entry as a
regular file with default permissions. That drops the exec bit on the Linux
``chrome`` binary and turns the symlinks inside the macOS ``.app`` framework
into small text files, so Chrome will not start. This extractor honours the
Unix mode stored in each entry, recreates symlinks, and refuses any entry or
link that would land outside the destination (zip-slip). On macOS it uses
``ditto``, the system tool that preserves a bundle exactly, and clears the
download quarantine flag.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path


class UnsafeArchiveError(ValueError):
    """An entry or symlink in the archive points outside the destination."""


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def extract_zip(archive: Path, dest: Path) -> None:
    """Extract ``archive`` into ``dest`` (created if missing)."""
    dest.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin" and shutil.which("ditto"):
        subprocess.run(["ditto", "-x", "-k", str(archive), str(dest)], check=True, capture_output=True)
        if shutil.which("xattr"):
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(dest)], check=False, capture_output=True)
        return
    _extract_portable(archive, dest)


def _extract_portable(archive: Path, dest: Path) -> None:
    root = dest.resolve()
    posix = os.name != "nt"
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or any(part == ".." for part in name.split("/")):
                raise UnsafeArchiveError(f"archive entry escapes the destination: {info.filename}")
            target = (root / name).resolve() if not name.endswith("/") else (root / name.rstrip("/")).resolve()
            if target != root and not _inside(root, target):
                raise UnsafeArchiveError(f"archive entry escapes the destination: {info.filename}")

            mode = (info.external_attr >> 16) & 0xFFFF
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)

            if stat.S_ISLNK(mode):
                link = zf.read(info).decode("utf-8")
                resolved = (target.parent / link).resolve()
                if not _inside(root, resolved):
                    raise UnsafeArchiveError(f"symlink escapes the destination: {info.filename} -> {link}")
                if posix:
                    if target.is_symlink() or target.exists():
                        target.unlink()
                    os.symlink(link, target)
                # Windows builds carry no symlinks; nothing to do if one appears.
                continue

            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)
            if posix and mode:
                os.chmod(target, stat.S_IMODE(mode))


__all__ = ["UnsafeArchiveError", "extract_zip"]
