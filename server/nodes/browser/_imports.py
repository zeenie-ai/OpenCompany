"""Bringing existing logins into a browser profile.

Two sources, one flow (pick sites, then apply):

- **The user's own browser** (Chrome, Edge, Brave, Chromium) on this
  machine. The user turns on ``chrome://inspect/#remote-debugging`` (Chrome
  144+); their browser then writes a ``DevToolsActivePort`` file, we connect
  to that address, the browser asks the user to Allow, and we read cookies
  with ``Storage.getCookies`` and disconnect at once. Chrome's cookie
  database is never read from disk: it is encrypted to the user's browser,
  and reading it is what credential-stealing malware does. Only possible
  when the backend runs on the user's machine (``_host.local_import_available``).
- **An uploaded session file** (Playwright storageState, Cookie-Editor JSON,
  cookies.txt), parsed in memory by ``_cookies.py``.

A parsed jar waits here at most :data:`JOB_TTL_SECONDS` for the user to pick
sites, then is dropped; it is never written to disk. Applying sets the
cookies on the profile's own Chrome with ``Storage.setCookies``, and seeds
localStorage for a storageState file without contacting the site (the seed
page is answered by ``Fetch.fulfillRequest``).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging import get_logger
from services.plugin.base import NodeUserError

from ._cdp import CDPConnection, CDPDisconnected, CDPError, read_devtools_active_port
from ._cookies import CookieFormatError, CookieJar, filter_domains, finalize, from_cdp_cookies, to_cookie_param

logger = get_logger(__name__)

JOB_TTL_SECONDS = 300.0
ALLOW_WAIT_SECONDS = 120.0
_COOKIE_CHUNK = 400

#: Where each browser keeps its user data (DevToolsActivePort lives there).
USER_BROWSERS: Dict[str, Dict[str, str]] = {
    "chrome": {
        "name": "Google Chrome",
        "win32": "Google/Chrome/User Data",
        "darwin": "Library/Application Support/Google/Chrome",
        "linux": ".config/google-chrome",
    },
    "edge": {
        "name": "Microsoft Edge",
        "win32": "Microsoft/Edge/User Data",
        "darwin": "Library/Application Support/Microsoft Edge",
        "linux": ".config/microsoft-edge",
    },
    "brave": {
        "name": "Brave",
        "win32": "BraveSoftware/Brave-Browser/User Data",
        "darwin": "Library/Application Support/BraveSoftware/Brave-Browser",
        "linux": ".config/BraveSoftware/Brave-Browser",
    },
    "chromium": {
        "name": "Chromium",
        "win32": "Chromium/User Data",
        "darwin": "Library/Application Support/Chromium",
        "linux": ".config/chromium",
    },
}


def user_browser_dir(source: str) -> Optional[Path]:
    entry = USER_BROWSERS.get(source)
    if entry is None:
        return None
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / entry["win32"] if base else None
    key = "darwin" if sys.platform == "darwin" else "linux"
    return Path.home() / entry[key]


def detect_user_browsers() -> List[Dict[str, Any]]:
    found = []
    for source, entry in USER_BROWSERS.items():
        directory = user_browser_dir(source)
        if directory is None or not directory.exists():
            continue
        found.append(
            {"id": source, "name": entry["name"], "remote_debugging": read_devtools_active_port(directory) is not None}
        )
    return found


@dataclass
class ImportJob:
    id: str
    owner_id: str
    source: str  # chrome | edge | brave | chromium | file
    state: str = "waiting"  # waiting | ready | failed | applied
    jar: Optional[CookieJar] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.monotonic)
    task: Optional[asyncio.Task] = None

    def to_wire(self) -> Dict[str, Any]:
        return {
            "import_id": self.id,
            "source": self.source,
            "state": self.state,
            "error": self.error,
            "format": self.jar.format if self.jar else None,
            "sites": self.jar.domains() if self.jar else [],
            "skipped": self.jar.skipped if self.jar else 0,
        }


class ImportJobs:
    def __init__(self) -> None:
        self._jobs: Dict[str, ImportJob] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        for job_id, job in list(self._jobs.items()):
            if now - job.created_at > JOB_TTL_SECONDS:
                if job.task is not None and not job.task.done():
                    job.task.cancel()
                self._jobs.pop(job_id, None)

    def add_file(self, owner_id: str, jar: CookieJar) -> ImportJob:
        self._prune()
        job = ImportJob(id=f"imp_{uuid.uuid4().hex[:16]}", owner_id=owner_id, source="file", state="ready", jar=jar)
        self._jobs[job.id] = job
        return job

    def start_browser(self, owner_id: str, source: str) -> ImportJob:
        self._prune()
        directory = user_browser_dir(source)
        if directory is None or not directory.exists():
            raise NodeUserError(f"{USER_BROWSERS.get(source, {}).get('name', source)} is not installed for this user.")
        job = ImportJob(id=f"imp_{uuid.uuid4().hex[:16]}", owner_id=owner_id, source=source)
        job.task = asyncio.create_task(self._read_browser(job, directory))
        job.task.add_done_callback(lambda t: t.cancelled() or t.exception())
        self._jobs[job.id] = job
        return job

    def get(self, owner_id: str, job_id: str) -> ImportJob:
        self._prune()
        job = self._jobs.get(job_id)
        if job is None or job.owner_id != owner_id:
            raise NodeUserError("That import has expired; start it again.")
        return job

    def cancel(self, owner_id: str, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is not None and job.owner_id == owner_id:
            if job.task is not None and not job.task.done():
                job.task.cancel()
            self._jobs.pop(job_id, None)

    def drop(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    async def _read_browser(self, job: ImportJob, directory: Path) -> None:
        name = USER_BROWSERS[job.source]["name"]
        found = read_devtools_active_port(directory)
        if found is None:
            job.state, job.error = "failed", (
                f"Remote debugging is off in {name}. Open chrome://inspect/#remote-debugging in {name}, turn it on, then try again."
            )
            return
        port, path = found
        connection: Optional[CDPConnection] = None
        try:
            # The browser asks the user to Allow this connection; the
            # handshake (or the first command) waits for their answer.
            connection = await CDPConnection.connect(f"ws://127.0.0.1:{port}{path}", open_timeout=ALLOW_WAIT_SECONDS)
            result = await connection.send("Storage.getCookies", timeout=ALLOW_WAIT_SECONDS)
            job.jar = finalize(from_cdp_cookies(result.get("cookies") or []))
            job.state = "ready"
        except asyncio.CancelledError:
            raise
        except CookieFormatError:
            job.state, job.error = "failed", f"{name} has no saved logins to import."
        except Exception as exc:  # noqa: BLE001 - reported to the user
            job.state = "failed"
            job.error = f"{name} did not allow the connection, or it could not be reached ({type(exc).__name__})."
            logger.info("[browser] reading logins from %s failed: %s", name, exc)
        finally:
            if connection is not None:
                await connection.close()


_jobs: Optional[ImportJobs] = None


def get_import_jobs() -> ImportJobs:
    global _jobs
    if _jobs is None:
        _jobs = ImportJobs()
    return _jobs


async def apply_jar(runtime: Any, jar: CookieJar, domains: List[str]) -> Dict[str, Any]:
    """Set the chosen sites' cookies (and localStorage) on a running profile."""
    chosen = filter_domains(jar, domains)
    if not chosen.cookies and not chosen.local_storage:
        raise NodeUserError("None of the chosen sites have anything to import.")
    cdp = runtime.cdp
    params = [to_cookie_param(c) for c in chosen.cookies]
    applied = 0
    for start in range(0, len(params), _COOKIE_CHUNK):
        batch = params[start : start + _COOKIE_CHUNK]
        try:
            await cdp.send("Storage.setCookies", {"cookies": batch}, timeout=30)
            applied += len(batch)
        except CDPError:
            # One bad cookie fails the whole call; set the rest one by one.
            for param in batch:
                try:
                    await cdp.send("Storage.setCookies", {"cookies": [param]}, timeout=10)
                    applied += 1
                except CDPError:
                    continue
    seeded = 0
    for origin, items in chosen.local_storage.items():
        try:
            await _seed_local_storage(cdp, origin, items)
            seeded += 1
        except (CDPError, CDPDisconnected, TimeoutError) as exc:
            logger.info("[browser] could not seed localStorage for %s: %s", origin, exc)
    return {"cookies": applied, "skipped": len(params) - applied, "origins": seeded, "sites": chosen.domains()}


async def _seed_local_storage(cdp: CDPConnection, origin: str, items: List[Any]) -> None:
    """Write localStorage for ``origin`` without loading the real site."""
    seed_url = origin.rstrip("/") + "/__opencompany_seed__"
    created = await cdp.send("Target.createTarget", {"url": "about:blank", "background": True})
    target_id = created["targetId"]
    session = await cdp.attach(target_id)
    try:
        await session.send("Fetch.enable", {"patterns": [{"urlPattern": seed_url + "*"}]})
        paused: asyncio.Future = asyncio.get_running_loop().create_future()
        unsubscribe = session.on("Fetch.requestPaused", lambda p: paused.done() or paused.set_result(p))
        try:
            await session.send("Page.navigate", {"url": seed_url}, timeout=15)
            event = await asyncio.wait_for(paused, timeout=15)
            body = base64.b64encode(b"<!doctype html><title>seed</title>").decode("ascii")
            await session.send(
                "Fetch.fulfillRequest",
                {"requestId": event["requestId"], "responseCode": 200, "responseHeaders": [{"name": "Content-Type", "value": "text/html"}], "body": body},
            )
            await asyncio.sleep(0.3)
            script = "(() => { const items = %s; for (const [k, v] of items) localStorage.setItem(k, v); return items.length; })()" % (
                json.dumps([[str(k), str(v)] for k, v in items])
            )
            await session.send("Runtime.evaluate", {"expression": script, "returnByValue": True}, timeout=10)
        finally:
            unsubscribe()
    finally:
        try:
            await cdp.send("Target.closeTarget", {"targetId": target_id}, timeout=10)
        except (CDPError, CDPDisconnected, TimeoutError):
            pass


async def read_sites(runtime: Any) -> List[Dict[str, Any]]:
    """Domains and cookie counts of a running profile (never values)."""
    try:
        result = await runtime.cdp.send("Storage.getCookies", timeout=15)
    except (CDPError, CDPDisconnected, TimeoutError):
        return []
    return from_cdp_cookies(result.get("cookies") or []).domains()


__all__ = [
    "ImportJob",
    "ImportJobs",
    "JOB_TTL_SECONDS",
    "USER_BROWSERS",
    "apply_jar",
    "detect_user_browsers",
    "get_import_jobs",
    "read_sites",
    "user_browser_dir",
]
