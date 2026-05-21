"""Temporal server lifecycle.

Single supervisor subclass: :class:`TemporalServerRuntime`. Spawns the
official ``temporal`` CLI (downloaded by :mod:`services.temporal._install`
from https://temporal.download/cli/archive/latest) with the
``server start-dev`` subcommand against a SQLite db. Matches the local
dev install method documented at
https://docs.temporal.io/develop/python/set-up-your-local-python.

Uses the singleton accessor pattern (``Class.get_instance()``) from
``BaseSupervisor`` — same idiom :mod:`nodes.whatsapp._runtime` uses for
``WhatsAppRuntime``.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Optional

from core.config import Settings
from core.logging import get_logger
from services._supervisor import BaseProcessSupervisor

logger = get_logger(__name__)

# How long each TCP-readiness probe waits per attempt. Sub-second so a
# stalled subprocess fails health fast.
_PROBE_TIMEOUT_SECONDS = 1.0


async def _probe_tcp_port(port: int, host: str = "127.0.0.1") -> bool:
    """Return ``True`` iff a TCP connection to ``host:port`` succeeds
    within :data:`_PROBE_TIMEOUT_SECONDS`. Loopback-friendly readiness
    check used by ``health_check``. Mirrors :func:`cli.tcp.probe_tcp_port`
    but keeps server-side modules independent of the ``cli`` CLI package."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, OSError):
            pass
        return True
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return False


class TemporalServerRuntime(BaseProcessSupervisor):
    """Temporal dev server, supervised via BaseProcessSupervisor.

    Spawns ``temporal server start-dev`` against a SQLite db at
    ``settings.temporal_sqlite_path``. The ``temporal`` CLI binary is
    downloaded by :func:`services.temporal._install.ensure_temporal_binaries`
    from the official URL at
    https://temporal.download/cli/archive/latest.

    Path is env-driven via ``settings.temporal_sqlite_path``, resolved
    relative to ``settings.data_dir`` unless already absolute.
    """

    name = "temporal"
    pipe_streams = True
    graceful_shutdown = sys.platform == "win32"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        super().__init__()
        if settings is None:
            settings = Settings()
        self.settings = settings
        # SIGTERM grace = settings.temporal_graceful_shutdown_seconds —
        # same knob already documented for the embedded Temporal worker.
        self.terminate_grace_seconds = float(
            settings.temporal_graceful_shutdown_seconds,
        )
        self._binaries: Optional[dict[str, Path]] = None

    @property
    def _sqlite_path(self) -> Path:
        """Env-driven SQLite db path (``TEMPORAL_SQLITE_PATH``),
        resolved relative to ``DATA_DIR`` unless absolute."""
        return Path(self.settings._resolve_under_data(self.settings.temporal_sqlite_path))

    # ---- BaseProcessSupervisor overrides ---------------------------------

    async def _pre_spawn(self) -> None:
        from services.temporal._install import ensure_temporal_binaries

        self._binaries = await ensure_temporal_binaries(self.settings)
        # Ensure the parent dir for the SQLite file exists before
        # ``temporal server start-dev`` opens it.
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    def binary_path(self) -> Path:
        # ``_pre_spawn`` (called by ``BaseProcessSupervisor._do_start``
        # before this method) populates ``self._binaries`` via the
        # pooch downloader. Loud failure if that contract regresses.
        assert self._binaries is not None, (
            f"[{self.label}] binary_path() called before _pre_spawn() "
            "populated self._binaries"
        )
        return self._binaries["temporal"]

    def argv(self) -> list[str]:
        # ``temporal server start-dev`` is the official subcommand for
        # the SQLite-backed dev server. Flags documented at
        # https://docs.temporal.io/cli/server (subset we use):
        #   --port           frontend gRPC port (gates ready-probe)
        #   --ui-port        Web UI port (default ``--port + 1000``)
        #   --db-filename    SQLite file (omit for in-memory)
        #   --metrics-port   0 disables the Prometheus endpoint
        #   --log-level      warn keeps the supervisor log readable
        #   --namespace      default namespace bootstrapped at start
        return [
            str(self.binary_path()), "server", "start-dev",
            "--port", str(self.settings.temporal_frontend_grpc_port),
            "--ui-port", str(self.settings.temporal_ui_port),
            "--db-filename", str(self._sqlite_path),
            "--metrics-port", "0",
            "--log-level", "warn",
            "--namespace", self.settings.temporal_namespace,
        ]

    def cwd(self) -> Path:
        # cwd is the parent of the SQLite file so any default
        # output / log files land alongside the db rather than in the
        # supervisor's working directory.
        return self._sqlite_path.parent

    def env(self) -> dict[str, str]:
        # ``temporal server start-dev`` reads everything from argv flags;
        # inherit parent env only.
        return {**os.environ}

    async def health_check(self) -> bool:
        if not self.is_running():
            return False
        # gRPC frontend port — configured via
        # ``settings.temporal_frontend_grpc_port``. Same shared probe
        # MachinaOS uses for every other supervised TCP service.
        return await _probe_tcp_port(self.settings.temporal_frontend_grpc_port)

    def _extra_status(self) -> dict[str, Any]:
        base = super()._extra_status()
        return {
            **base,
            "grpc_port": self.settings.temporal_frontend_grpc_port,
            "ui_port": self.settings.temporal_ui_port,
            "sqlite_path": str(self._sqlite_path),
        }


# ---- module-level singleton accessor ------------------------------------

def get_temporal_server_runtime(
    settings: Optional[Settings] = None,
) -> TemporalServerRuntime:
    """Return the Temporal server runtime singleton."""
    return TemporalServerRuntime.get_instance(settings)


__all__ = [
    "TemporalServerRuntime",
    "get_temporal_server_runtime",
]
