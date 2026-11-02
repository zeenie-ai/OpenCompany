"""Temporal server lifecycle.

Single supervisor subclass: :class:`TemporalServerRuntime`. Spawns the
official ``temporal`` CLI (the release pinned in
``server/config/temporal_cli.json``, downloaded by
:mod:`services.temporal._install`) with the ``server start-dev``
subcommand against a SQLite db. Matches the local dev install method
documented at
https://docs.temporal.io/develop/python/set-up-your-local-python.

Uses the singleton accessor pattern (``Class.get_instance()``) from
``BaseSupervisor`` — same idiom :mod:`nodes.whatsapp._runtime` uses for
``WhatsAppRuntime``.
"""

from __future__ import annotations

import asyncio
import json
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
    ``settings.temporal_sqlite_path``. The pinned ``temporal`` CLI binary
    is downloaded by
    :func:`services.temporal._install.ensure_temporal_binaries`, and
    :func:`services.temporal._install.prepare_database` deletes a
    database the pinned server minor did not create before each spawn.

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
        from services.temporal._install import ensure_temporal_binaries, prepare_database

        self._binaries = await ensure_temporal_binaries()
        # Ensure the parent dir for the SQLite file exists before
        # ``temporal server start-dev`` opens it.
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        # A database the pinned server minor did not create is deleted
        # before the server opens it (RFC-0005 E16).
        await asyncio.to_thread(prepare_database, self._sqlite_path)

    def binary_path(self) -> Path:
        # ``_pre_spawn`` (called by ``BaseProcessSupervisor._do_start``
        # before this method) populates ``self._binaries`` via the
        # pooch downloader. Loud failure if that contract regresses.
        assert self._binaries is not None, f"[{self.label}] binary_path() called before _pre_spawn() " "populated self._binaries"
        return self._binaries["temporal"]

    def argv(self) -> list[str]:
        # ``temporal server start-dev`` is the official subcommand for
        # the SQLite-backed dev server. Flags documented at
        # https://docs.temporal.io/cli/server (subset we use):
        #   --port           frontend gRPC port (gates ready-probe)
        #   --ui-port        Web UI port (default ``--port + 1000``)
        #   --db-filename    SQLite file — preserves history AND running
        #                    workflow state across restarts. Running and
        #                    paused deployments resume when the server
        #                    and workers come back; the debug-only
        #                    ``TEMPORAL_TERMINATE_RUNNING_ON_STARTUP``
        #                    sweep (default false) is the one exception,
        #                    and a database from another server minor is
        #                    deleted in ``_pre_spawn`` instead of opened.
        #   --http-port      HTTP API port (TEMPORAL_HTTP_PORT)
        #   --metrics-port   Prometheus port (TEMPORAL_METRICS_PORT). Unset or
        #                    0, either flag picks a random free port, which
        #                    the network policy could not refuse.
        #   --log-level      warn keeps the supervisor log readable
        #   --namespace      default namespace bootstrapped at start
        #   --dynamic-config-value  the pin's dynamic_config
        #                    (server/config/temporal_cli.json), on every boot,
        #                    because start-dev reads dynamic config only from
        #                    its flags: fairness on and one task-queue
        #                    partition (RFC-0006 T17).
        #                    The database marker records the partitions,
        #                    which can be lowered safely only on a new
        #                    database, so ``_pre_spawn`` resets one created
        #                    with other values.
        #   --sqlite-pragma  SQLite tuning, repeatable PRAGMA=VALUE.
        #                    journal_mode=WAL + synchronous=NORMAL +
        #                    busy_timeout let the single-writer SQLite
        #                    store survive the boot-time burst of
        #                    DeleteHistoryEvent (retention GC) timer
        #                    tasks that all come due at once after a
        #                    long downtime — otherwise the rollback
        #                    journal serialises every write and the
        #                    burst fails with "Failed to start
        #                    transaction / context deadline exceeded".
        #                    This exact trio is what temporalio/cli's
        #                    own TestServer_StartDev_SQLitePragma
        #                    asserts the dev server runs cleanly under;
        #                    WAL is durable and synchronous=NORMAL is
        #                    WAL-safe (no corruption). See SQLite WAL
        #                    docs + https://docs.temporal.io/cli/server.
        from core.env_defaults import env_int
        from services.temporal._install import cli_pin

        dynamic_config: list[str] = []
        for key, value in cli_pin()["dynamic_config"].items():
            dynamic_config += ["--dynamic-config-value", f"{key}={json.dumps(value)}"]
        return [
            str(self.binary_path()),
            "server",
            "start-dev",
            "--port",
            str(self.settings.temporal_frontend_grpc_port),
            "--ui-port",
            str(self.settings.temporal_ui_port),
            "--db-filename",
            str(self._sqlite_path),
            "--http-port",
            str(env_int("TEMPORAL_HTTP_PORT")),
            "--metrics-port",
            str(env_int("TEMPORAL_METRICS_PORT")),
            "--log-level",
            "warn",
            "--namespace",
            self.settings.temporal_namespace,
            *dynamic_config,
            "--sqlite-pragma",
            "journal_mode=WAL",
            "--sqlite-pragma",
            "synchronous=NORMAL",
            "--sqlite-pragma",
            "busy_timeout=5000",
        ]

    def cwd(self) -> Path:
        # Land any incidental output / log files next to the SQLite
        # file (when persisting) or under DATA_DIR (when in-memory) —
        # either way, never the supervisor's own working directory.
        return self._sqlite_path.parent

    def env(self) -> dict[str, str]:
        # ``temporal server start-dev`` reads everything from argv flags;
        # inherit parent env only.
        return {**os.environ}

    # Known-benign dev-server noise: the matching/persistence layer logs
    # ERROR when a poller's long-poll context is canceled mid-read (worker
    # restart, poller autoscaling settling, idle partition unload, or the
    # server's own idle background jobs). Staff-confirmed benign upstream
    # (community.temporal.io/t/18635; temporalio/cli#633): tasks are
    # persisted and redelivered, nothing is lost. Both markers must match
    # so genuine dev-server errors keep surfacing at ERROR.
    _BENIGN_STDERR_MARKERS = (
        "Operation failed with internal error.",
        "GetTaskQueue operation failed",
    )

    def stderr_log(self, line: str) -> None:
        if "context canceled" in line and any(marker in line for marker in self._BENIGN_STDERR_MARKERS):
            self._logger.debug(line)
            return
        super().stderr_log(line)

    async def health_check(self) -> bool:
        if not self.is_running():
            return False
        # gRPC frontend port — configured via
        # ``settings.temporal_frontend_grpc_port``. Same shared probe
        # OpenCompany uses for every other supervised TCP service.
        return await _probe_tcp_port(self.settings.temporal_frontend_grpc_port)

    async def ensure_started(self) -> None:
        """Start the dev server unless the gRPC port is already served.

        A listening port means either a previously spawned instance or an
        externally managed Temporal server — both are left alone. Idempotent
        like ``start()``; callers may invoke it on every reconnect attempt.
        """
        if await _probe_tcp_port(self.settings.temporal_frontend_grpc_port):
            return
        await self.start()

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
