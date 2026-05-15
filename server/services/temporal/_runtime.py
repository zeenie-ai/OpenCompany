"""Postgres + Temporal server lifecycle.

Two supervisor subclasses:

  - :class:`PostgresRuntime` subclasses :class:`BaseSupervisor` directly.
    pgserver manages its own subprocess (start / stop / port binding),
    so we don't drive ``anyio.open_process`` — we just wrap its
    lifecycle in the uniform start/stop/status surface.

  - :class:`TemporalServerRuntime` subclasses :class:`BaseProcessSupervisor`.
    Fires the binary downloaded by :mod:`services.temporal._install`
    against the YAML config rendered by :mod:`services.temporal._config`.

Both use the singleton accessor pattern (``Class.get_instance()``)
from ``BaseSupervisor`` — same idiom :mod:`nodes.whatsapp._runtime`
uses for ``WhatsAppRuntime``.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Optional

from core.config import Settings
from core.logging import get_logger
from services._supervisor import BaseProcessSupervisor, BaseSupervisor
from services.temporal._config import parse_postgres_uri

logger = get_logger(__name__)

# Default Postgres port; only used as a fallback in ``PostgresRuntime.port``
# before pgserver has actually started and reported its dynamic port.
_PG_DEFAULT_PORT = 5432

# How long each TCP-readiness probe waits per attempt. Mirrors
# ``cli.tcp.probe_tcp_port``'s semantics inside the server-side
# supervisor — sub-second so a stalled subprocess fails health fast.
_PROBE_TIMEOUT_SECONDS = 1.0


async def _probe_tcp_port(port: int, host: str = "127.0.0.1") -> bool:
    """Return ``True`` iff a TCP connection to ``host:port`` succeeds
    within :data:`_PROBE_TIMEOUT_SECONDS`. Loopback-friendly readiness
    check used by both runtimes' ``health_check`` overrides. Mirrors
    :func:`cli.tcp.probe_tcp_port` but keeps server-side modules
    independent of the ``cli`` CLI package."""
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


class PostgresRuntime(BaseSupervisor):
    """pgserver-managed PostgreSQL 16.2.

    pgserver (https://pypi.org/project/pgserver/) bundles full Postgres
    binaries cross-platform via pip and exposes a Python API for the
    process lifecycle. We wrap that API in BaseSupervisor's start/stop
    surface so the rest of the system (status broadcasts, supervisor,
    FastAPI lifespan) treats it uniformly with every other supervised
    binary.
    """

    name = "temporal-postgres"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        super().__init__()
        if settings is None:
            settings = Settings()
        self.settings = settings
        self._pg: Any = None  # pgserver.Server instance

    # ---- subclass surface (BaseSupervisor) -------------------------------

    def is_running(self) -> bool:
        return self._pg is not None

    async def _do_start(self) -> None:
        # Lazy import: pgserver pulls a heavy native dep tree; only
        # load it when the Postgres backend is actually selected.
        import pgserver

        data_dir = Path(self.settings.data_dir) / "postgres"
        data_dir.mkdir(parents=True, exist_ok=True)

        def _start_sync() -> Any:
            return pgserver.get_server(str(data_dir), cleanup_mode=None)

        self._pg = await asyncio.to_thread(_start_sync)
        logger.info("[%s] pgserver ready at %s", self.label, self._pg.get_uri())

    async def _do_stop(self) -> None:
        pg = self._pg
        self._pg = None
        if pg is None:
            return

        def _stop_sync() -> None:
            try:
                pg.cleanup()
            except Exception as exc:  # noqa: BLE001 — best-effort shutdown
                logger.warning("[postgres] cleanup raised %r (continuing)", exc)

        await asyncio.to_thread(_stop_sync)
        logger.info("[%s] stopped", self.label)

    async def health_check(self) -> bool:
        if not self.is_running():
            return False
        # pgserver may report running before the socket is accepting;
        # use the shared TCP probe — same idiom every supervised
        # service uses for `ready_port` readiness checks.
        return await _probe_tcp_port(self.port)

    def _extra_status(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "port": self.port,
            "data_dir": str(Path(self.settings.data_dir) / "postgres"),
        }

    # ---- public read-only properties -------------------------------------

    @property
    def uri(self) -> Optional[str]:
        return self._pg.get_uri() if self._pg else None

    @property
    def port(self) -> int:
        # pgserver picks a free port at start; fall back to the default
        # when called before _do_start (e.g. early status snapshots).
        if self._pg is None:
            return _PG_DEFAULT_PORT
        return parse_postgres_uri(self._pg.get_uri())["port"]


class TemporalServerRuntime(BaseProcessSupervisor):
    """Temporal server binary, supervised via BaseProcessSupervisor.

    The binary lands on disk via :func:`services.temporal._install.ensure_temporal_binaries`
    (pooch-cached); the YAML config is rendered by
    :func:`services.temporal._config.render_temporal_config` pointing at
    the running :class:`PostgresRuntime` instance.

    BaseProcessSupervisor handles all signal, restart, and tree-kill
    semantics — we only override the four required methods plus
    ``_pre_spawn`` (download + bootstrap) and ``health_check``
    (gRPC port probe).
    """

    name = "temporal-server"
    pipe_streams = True
    graceful_shutdown = sys.platform == "win32"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        postgres: Optional[PostgresRuntime] = None,
    ) -> None:
        super().__init__()
        if settings is None:
            settings = Settings()
        self.settings = settings
        # SIGTERM grace = settings.temporal_graceful_shutdown_seconds —
        # same knob already documented for the embedded Temporal worker.
        # Reusing it avoids inventing a parallel ``temporal_server_grace_seconds``.
        self.terminate_grace_seconds = float(
            settings.temporal_graceful_shutdown_seconds,
        )
        self._postgres = postgres or PostgresRuntime.get_instance(settings)
        self._binaries: Optional[dict[str, Path]] = None
        self._config_path: Optional[Path] = None

    # ---- BaseProcessSupervisor overrides ---------------------------------

    async def _pre_spawn(self) -> None:
        from services.temporal._install import ensure_temporal_binaries
        from services.temporal._config import (
            bootstrap_temporal_schemas,
            render_temporal_config,
        )

        if self._postgres.uri is None:
            raise RuntimeError(
                f"[{self.label}] Postgres runtime not started; "
                "schedule the postgres ServiceSpec before this one"
            )

        # 1. Download / cache temporal-server + temporal-sql-tool.
        #    Version pin lives in settings.temporal_binary_version.
        self._binaries = await ensure_temporal_binaries(self.settings)

        # 2. Idempotent schema bootstrap.
        await bootstrap_temporal_schemas(
            sql_tool=self._binaries["temporal-sql-tool"],
            postgres_uri=self._postgres.uri,
            binary_path=self._binaries["temporal-server"],
        )

        # 3. Render YAML config pointing at the Postgres URI.
        self._config_path = render_temporal_config(
            settings=self.settings,
            postgres_uri=self._postgres.uri,
        )

    def binary_path(self) -> Path:
        if self._binaries is None:
            # Called before _pre_spawn — e.g. if BaseProcessSupervisor
            # tries to validate the binary exists. Return a placeholder;
            # _pre_spawn populates the real path before the existence
            # check in _do_start runs.
            return Path("temporal-server")
        return self._binaries["temporal-server"]

    def argv(self) -> list[str]:
        return [str(self.binary_path()), "start", "--config", str(self._config_path)]

    def cwd(self) -> Path:
        return Path(self.settings.data_dir) / "_temporal"

    def env(self) -> dict[str, str]:
        # Temporal reads everything from YAML; inherit parent env only.
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
            "config_path": str(self._config_path) if self._config_path else None,
            "binary_version": self.settings.temporal_binary_version,
        }


# ---- module-level singleton accessors -----------------------------------

def get_postgres_runtime(settings: Optional[Settings] = None) -> PostgresRuntime:
    """Return the Postgres runtime singleton."""
    return PostgresRuntime.get_instance(settings)


def get_temporal_server_runtime(
    settings: Optional[Settings] = None,
) -> TemporalServerRuntime:
    """Return the Temporal server runtime singleton."""
    return TemporalServerRuntime.get_instance(settings)


__all__ = [
    "PostgresRuntime",
    "TemporalServerRuntime",
    "get_postgres_runtime",
    "get_temporal_server_runtime",
]
