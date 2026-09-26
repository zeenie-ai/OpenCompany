"""Select installed Chrome/Edge/Chromium, or explicitly install a testing build.

The default system provider never downloads Chrome. ``BROWSER_CHROME_PATH``
overrides discovery in either provider, and the selected binary's actual
version is checked before a profile is opened. ``BROWSER_RUNTIME=testing``
opts into the downloader described below.

The version and each platform's MD5 and size come from
``server/config/browser_runtime.json``. The build lands under
``<DATA_DIR>/packages/chrome-for-testing/<version>/``, next to the other
downloaded tools, and is marked complete only after it is fully extracted,
so a crash mid-install never leaves a half-unpacked Chrome that looks
installed.

The first install downloads about 200 MB, so it must never block the event
loop: the in-process Temporal worker shares it, and a stalled loop misses
activity heartbeats. One background task does the work (``to_thread``) and
every caller waits on it with its own time budget; a caller that runs out of
budget gets a "still installing" error while the install carries on. A
failed install is retried after a cooldown, never cached for good.
``BROWSER_CHROME_PATH`` replaces the download with a Chrome of your own.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.logging import get_logger
from services.plugin.base import NodeUserError

from ._config import ChromePin, ChromePlatform, chrome_platform_for_host, get_config
from ._extract import extract_zip

logger = get_logger(__name__)

_FAILURE_COOLDOWN_SECONDS = (10.0, 30.0, 60.0, 300.0)
_PROGRESS_BROADCAST_SECONDS = 0.5


class InstallCancelled(Exception):
    """The install was cancelled from outside the download thread."""


@dataclass
class InstallState:
    phase: str = "idle"  # idle | downloading | extracting | ready | failed
    percent: Optional[int] = None
    version: str = ""
    error: Optional[str] = None
    failures: int = 0
    failed_at: float = 0.0
    exe: Optional[str] = None
    source: str = ""
    bytes_done: int = 0
    bytes_total: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict:
        return {"phase": self.phase, "percent": self.percent, "version": self.version, "error": self.error, "exe": self.exe, "source": self.source}


class _Progress:
    """pooch progress hook: records bytes and aborts when cancelled."""

    def __init__(self, state: InstallState) -> None:
        self._state = state
        self.total = 0

    def update(self, n: int) -> None:
        if self._state.cancel.is_set():
            raise InstallCancelled("Chrome download cancelled")
        self._state.bytes_done += int(n)
        total = self.total or self._state.bytes_total
        if total:
            self._state.bytes_total = total
            self._state.percent = min(99, int(self._state.bytes_done * 100 / total))

    def reset(self) -> None:
        self._state.bytes_done = 0

    def close(self) -> None:
        return None


def _root() -> Path:
    from core.paths import package_dir

    path = package_dir("chrome-for-testing")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _marker(version_dir: Path, platform_key: str) -> Path:
    return version_dir / f".complete-{platform_key}"


def _grant_sandbox_access(directory: Path) -> None:
    """Let Chrome's Windows sandbox read its own files.

    Chrome's installer grants "ALL APPLICATION PACKAGES" and "ALL RESTRICTED
    APPLICATION PACKAGES" read/execute on its folder; an unpacked zip in the
    user's profile has neither, so the network-service sandbox logs "cannot
    access executable" and restarts the service. Best effort: Chrome still
    runs without it.
    """
    import subprocess

    try:
        subprocess.run(
            ["icacls", str(directory), "/grant", "*S-1-15-2-1:(OI)(CI)(RX)", "/grant", "*S-1-15-2-2:(OI)(CI)(RX)", "/T", "/Q"],
            check=False,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("[browser] could not grant Chrome's sandbox access to %s: %s", directory, exc)


class ChromeInstaller:
    def __init__(self, pin: Optional[ChromePin] = None, root: Optional[Path] = None) -> None:
        self._pin = pin
        self._root = root
        self.state = InstallState()
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # -- facts ------------------------------------------------------------

    @property
    def pin(self) -> ChromePin:
        return self._pin or get_config().chrome

    @property
    def root(self) -> Path:
        return self._root or _root()

    def platform(self) -> ChromePlatform:
        plat = chrome_platform_for_host(self.pin)
        if plat is None:
            raise NodeUserError(
                "There is no Chrome for Testing build for this machine. Set BROWSER_CHROME_PATH to a Chrome or Chromium of your own."
            )
        return plat

    def installed_exe(self) -> Optional[Path]:
        """The pinned build's executable if it is fully installed, without installing."""
        plat = self.platform()
        version_dir = self.root / self.pin.version
        exe = version_dir / plat.exe
        if _marker(version_dir, plat.key).exists() and exe.exists():
            return exe
        return None

    def status(self) -> dict:
        snap = self.state.snapshot()
        snap["provider"] = self.provider()
        return snap

    def provider(self) -> str:
        from core.container import container

        provider = str(getattr(container.settings(), "browser_runtime", "system") or "system").lower()
        if provider not in {"system", "testing"}:
            raise NodeUserError("BROWSER_RUNTIME must be system or testing.")
        return provider

    async def _selected(self, exe: Path, source: str, *, min_major: int = 0, version: Optional[str] = None) -> Path:
        from ._system_browser import browser_version, version_major

        version = version or await browser_version(exe)
        required_major = max(min_major, self.pin.min_major)
        if version_major(version) < required_major:
            raise NodeUserError(f"The selected browser is version {version}; this profile requires version {required_major} or newer. Update your browser or select another BROWSER_CHROME_PATH.")
        self.state.phase, self.state.exe, self.state.version = "ready", str(exe), version
        self.state.source, self.state.error = source, None
        return exe

    # -- install ----------------------------------------------------------

    async def ensure(self, *, wait: float, min_major: int = 0) -> Path:
        """Path to a supported browser; only testing mode may download it.

        Testing downloads wait at most ``wait`` seconds and keep running in
        the background when the caller gives up. Version preflight is separate.
        """
        from core.container import container

        from ._system_browser import select_browser

        provider = self.provider()
        raw = str(getattr(container.settings(), "browser_chrome_path", "") or "").strip()
        if raw or provider == "system":
            try:
                family = str(getattr(container.settings(), "browser_family", "chrome") or "chrome").lower()
                exe, source, version = await select_browser(min_major=max(min_major, self.pin.min_major), override=raw, family=family)
                return await self._selected(exe, source, min_major=min_major, version=version)
            except Exception as exc:
                self.state.phase, self.state.error = "failed", str(exc)
                self.state.version, self.state.exe, self.state.source = "", None, ""
                raise
        exe = self.installed_exe()
        if exe is not None:
            return await self._selected(exe, "testing", min_major=min_major)

        task = await self._start()
        try:
            exe = await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, wait))
        except asyncio.TimeoutError:
            pct = self.state.percent
            progress = f" ({pct}%)" if pct is not None else ""
            raise NodeUserError(f"Installing the browser{progress}. This happens once; try again in a minute.") from None
        return await self._selected(exe, "testing", min_major=min_major)

    async def _start(self) -> asyncio.Task:
        async with self._lock:
            if self._task is not None and not self._task.done():
                return self._task
            if self.state.phase == "failed":
                wait = _FAILURE_COOLDOWN_SECONDS[min(self.state.failures, len(_FAILURE_COOLDOWN_SECONDS)) - 1]
                remaining = self.state.failed_at + wait - time.monotonic()
                if remaining > 0:
                    raise NodeUserError(
                        f"The browser install failed ({self.state.error}). It will be retried in {int(remaining) + 1} s."
                    )
            self._task = asyncio.create_task(self._run(), name="browser-chrome-install")
            # A caller that stopped waiting never awaits the outcome; retrieve
            # it here so a later failure is not logged as "never retrieved".
            self._task.add_done_callback(lambda t: t.cancelled() or t.exception())
            return self._task

    async def _announce(self) -> None:
        """Broadcast the current phase (best effort; the panel also polls)."""
        from ._events import dispatch_browser_runtime_progress

        try:
            await dispatch_browser_runtime_progress(
                component="chrome",
                phase=self.state.phase,
                percent=self.state.percent,
                version=self.pin.version,
                error=self.state.error,
            )
        except Exception:  # noqa: BLE001 - progress must never fail the install
            logger.debug("[browser] install progress broadcast failed", exc_info=True)

    async def _run(self) -> Path:
        from core.container import container

        timeout = float(getattr(container.settings(), "browser_install_timeout_seconds", 900) or 900)
        plat = self.platform()
        self.state.cancel = threading.Event()
        self.state.phase, self.state.percent, self.state.error = "downloading", 0, None
        self.state.version, self.state.bytes_done, self.state.bytes_total = self.pin.version, 0, plat.size_bytes

        async def ticker() -> None:
            last = None
            while True:
                snap = (self.state.phase, self.state.percent)
                if snap != last:
                    last = snap
                    await self._announce()
                await asyncio.sleep(_PROGRESS_BROADCAST_SECONDS)

        ticking = asyncio.create_task(ticker())
        try:
            exe = await asyncio.wait_for(asyncio.to_thread(self._install_sync, plat), timeout=timeout)
        except asyncio.TimeoutError:
            self.state.cancel.set()
            self._fail(f"timed out after {int(timeout)} s")
            await self._announce()
            raise NodeUserError(f"The browser download timed out after {int(timeout)} s; it will be retried.") from None
        except Exception as exc:  # noqa: BLE001 - reported, then retried after a cooldown
            self._fail(str(exc) or type(exc).__name__)
            logger.warning("[browser] Chrome for Testing install failed: %s", exc)
            await self._announce()
            raise NodeUserError(f"The browser could not be installed: {exc}") from exc
        finally:
            ticking.cancel()
        self.state.phase, self.state.percent, self.state.exe, self.state.failures = "ready", 100, str(exe), 0
        await self._announce()
        return exe

    def _fail(self, message: str) -> None:
        self.state.phase, self.state.error = "failed", message
        self.state.failures += 1
        self.state.failed_at = time.monotonic()

    def _install_sync(self, plat: ChromePlatform) -> Path:
        import pooch

        pin = self.pin
        root = self.root
        downloads = root / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        fname = f"chrome-{plat.key}-{pin.version}.zip"
        known_hash = f"md5:{plat.md5}" if plat.md5 else None
        url = pin.url(plat.key)
        logger.info("[browser] downloading Chrome for Testing %s (%s) from %s", pin.version, plat.key, url)
        archive = Path(
            pooch.retrieve(
                url=url,
                known_hash=known_hash,
                path=downloads,
                fname=fname,
                processor=None,
                downloader=pooch.HTTPDownloader(timeout=300, progressbar=_Progress(self.state)),
            )
        )

        self.state.phase, self.state.percent = "extracting", 99
        version_dir = root / pin.version
        staging = root / f".staging-{uuid.uuid4().hex[:8]}"
        try:
            extract_zip(archive, staging)
            top = plat.exe.split("/", 1)[0]
            extracted_top = staging / top
            if not (staging / plat.exe).exists():
                raise RuntimeError(f"the archive has no {plat.exe}")
            version_dir.mkdir(parents=True, exist_ok=True)
            final_top = version_dir / top
            if final_top.exists():
                shutil.rmtree(final_top, ignore_errors=True)
            os.replace(extracted_top, final_top)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        exe = version_dir / plat.exe
        if os.name != "nt":
            exe.chmod(exe.stat().st_mode | 0o111)
        else:
            _grant_sandbox_access(version_dir)
        _marker(version_dir, plat.key).write_text(
            json.dumps({"version": pin.version, "platform": plat.key, "md5": plat.md5}), encoding="utf-8"
        )
        try:
            archive.unlink()
        except OSError:
            pass
        self._prune(keep={pin.version})
        logger.info("[browser] Chrome for Testing %s installed at %s", pin.version, exe)
        return exe

    def _prune(self, keep: set[str]) -> None:
        """Remove older builds beyond ``keep_versions`` (best effort: a build
        still in use on Windows cannot be deleted and simply stays)."""
        root = self.root
        versions = sorted(
            (p for p in root.iterdir() if p.is_dir() and p.name[:1].isdigit()),
            key=lambda p: tuple(int(x) if x.isdigit() else 0 for x in p.name.split(".")),
            reverse=True,
        )
        kept = [v for v in versions if v.name in keep]
        for candidate in versions:
            if candidate.name in keep:
                continue
            if len(kept) < self.pin.keep_versions:
                kept.append(candidate)
                continue
            shutil.rmtree(candidate, ignore_errors=True)


_installer: Optional[ChromeInstaller] = None


def get_chrome_installer() -> ChromeInstaller:
    global _installer
    if _installer is None:
        _installer = ChromeInstaller()
    return _installer


__all__ = ["ChromeInstaller", "InstallCancelled", "InstallState", "get_chrome_installer"]
