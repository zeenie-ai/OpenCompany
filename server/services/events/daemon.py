"""DaemonEventSource — long-lived subprocess (or SDK loop) that emits
events into the framework.

Used by stripe-listen, future telegram/whatsapp daemon migrations, and
any plugin that wraps a CLI tool. The class delegates lifecycle to
:class:`services.process_service.ProcessService` (already battle-tested:
PATHEXT-aware, ``kill_tree`` cleanup, log capture, terminal broadcast)
and subscribes to its standard per-line callback hook for typed event
emission. We do NOT re-tail the on-disk log files — that's the same
data ProcessService already streamed to us via ``line_handler``.

Subclasses contribute:

- :meth:`build_command(secrets) -> str` — full command string
- :meth:`parse_line(stream, line) -> Optional[WorkflowEvent]` — turn one
  line into one event (or ``None`` for log-only lines)
- :attr:`binary_name` (optional) — pre-flight ``shutil.which`` check
  with a clear error if the binary isn't on PATH
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, AsyncIterator, ClassVar, Dict, Optional

from core.logging import get_logger
from services.process_service import get_process_service

from .envelope import WorkflowEvent
from .source import EventSource

logger = get_logger(__name__)


class DaemonEventSource(EventSource):
    """Long-lived subprocess driver. Subclasses provide:

    * :attr:`process_name` — unique name for ProcessService key
    * :attr:`binary_name` — the executable to look up via PATH
    * :meth:`build_command(secrets)` — full command string
    * :meth:`parse_line(stream, line)` — turn output lines into events
      (return ``None`` to suppress; e.g. log-only lines)
    * :meth:`install_hint` (optional) — install URL surfaced in the
      "binary not on PATH" error
    """

    process_name: ClassVar[str] = ""
    binary_name: ClassVar[str] = ""
    workflow_namespace: ClassVar[str] = "_daemon"
    install_hint: ClassVar[str] = ""

    def __init__(self) -> None:
        super().__init__()
        self._pid: Optional[int] = None
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue[WorkflowEvent] = asyncio.Queue()

    @property
    def pid(self) -> Optional[int]:
        return self._pid

    def build_command(self, secrets: Dict) -> str:
        raise NotImplementedError

    def parse_line(self, stream: str, line: str) -> Optional[WorkflowEvent]:
        """Override to emit events from output lines. ``stream`` is
        ``"stdout"`` or ``"stderr"``. Returning ``None`` swallows the line."""
        return None

    def workdir(self) -> Path:
        from core.paths import daemons_dir

        cwd = daemons_dir() / self.workflow_namespace
        cwd.mkdir(parents=True, exist_ok=True)
        return cwd

    async def _resolve_secrets(self) -> Dict[str, Any]:
        if self.credential is None:
            return {}
        try:
            return await self.credential.resolve()
        except PermissionError:
            return {}

    async def has_credential(self) -> bool:
        secrets = await self._resolve_secrets()
        return bool(secrets.get("api_key"))

    async def status(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "running": self._started,
            "pid": self._pid,
        }

    async def start(self) -> Dict[str, Any]:
        async with self._lock:
            if self._started:
                logger.info("[%s] start() noop — already running (pid=%s)", self.type, self._pid)
                return {"success": True, "message": "already running", "status": await self.status()}

            logger.info("[%s] start() entered: resolving secrets + checking credential gate", self.type)
            secrets = await self._resolve_secrets()
            # Gate via :meth:`has_credential` so subclasses that authenticate
            # without an ``api_key`` field (Stripe — auth lives in the CLI's
            # config file, signalled by ``is_logged_in()``) can override the
            # check. The default ``has_credential`` still tests
            # ``secrets["api_key"]`` so api-key plugins keep working.
            if self.credential is not None and not await self.has_credential():
                logger.warning(
                    "[%s] start() blocked: has_credential() returned False — refusing to spawn daemon",
                    self.type,
                )
                return {"success": False, "error": f"{self.type}: credential required"}

            if self.binary_name and shutil.which(self.binary_name) is None:
                hint = f" Install: {self.install_hint}" if self.install_hint else ""
                return {"success": False, "error": f"{self.binary_name!r} not on PATH.{hint}"}

            cmd = self.build_command(secrets)
            cwd = self.workdir()
            result = await get_process_service().start(
                name=self.process_name,
                command=cmd,
                workflow_id=self.workflow_namespace,
                working_directory=str(cwd),
                line_handler=self._on_line,
            )
            if not result.get("success"):
                return result

            self._pid = (result.get("result") or {}).get("pid")
            self._started = True
            self._stopped = False
            logger.info("[%s] daemon started pid=%s", self.type, self._pid)
            return {"success": True, "message": f"started (pid {self._pid})", "status": await self.status()}

    async def stop(self) -> Dict[str, Any]:
        async with self._lock:
            self._stopped = True
            await get_process_service().stop(
                name=self.process_name,
                workflow_id=self.workflow_namespace,
            )
            self._started = False
            self._pid = None
            return {"success": True, "message": "disconnected"}

    async def restart(self) -> Dict[str, Any]:
        await self.stop()
        return await self.start()

    async def _on_line(self, stream: str, line: str) -> None:
        """ProcessService callback: one decoded stdout/stderr line.
        Subclasses' :meth:`parse_line` decides whether to emit an event."""
        event = self.parse_line(stream, line)
        if event is not None:
            await self._queue.put(event)

    async def emit(self) -> AsyncIterator[WorkflowEvent]:
        while not self._stopped:
            event = await self._queue.get()
            yield event
