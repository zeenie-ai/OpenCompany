"""ServiceSpecs for the Temporal stack — shared by ``start`` and ``dev``.

Returns the right Temporal ``ServiceSpec`` set based on the
``TEMPORAL_BACKEND`` env var:

  - ``sqlite`` (default, dev) — single spec running ``temporal start-dev``
    against a SQLite file. Same behaviour as before this sprint, but the
    binary now comes from a pooch-managed cache (downloaded on first
    run by the Python backend's ``services.temporal._install``) rather
    than the deprecated ``temporal-server`` npm package.

  - ``postgres`` (prod) — two specs running the supervised runtimes:
    ``temporal-postgres`` (pgserver) then ``temporal-server`` (Temporal
    binary with YAML config). Both wrap ``BaseSupervisor`` singletons
    via ``cli.commands._supervised_runtime``.

The supervisor's TCP readiness probes (``ready_port``) order the
postgres → temporal startup automatically — Temporal won't start until
Postgres is accepting connections.
"""
from __future__ import annotations

import os
from pathlib import Path

from cli.supervisor import RestartPolicy, ServiceSpec

# Backend names — must match ``core.config.Settings.temporal_backend``
# enum (``"sqlite" | "postgres"``). Read from ``TEMPORAL_BACKEND`` env
# var at supervisor-build time; the matching Settings field flows
# through to the runtimes via :class:`core.config.Settings`.
_BACKEND_SQLITE = "sqlite"
_BACKEND_POSTGRES = "postgres"
_BACKEND_ENV_VAR = "TEMPORAL_BACKEND"

# Readiness-probe windows. ``temporal-server`` may need to download
# its binary on first run (~90 MB tarball); the Postgres init step
# runs schema migrations once. Override via env if your environment
# needs different headroom.
_PG_READY_TIMEOUT_SECONDS = float(
    os.environ.get("TEMPORAL_PG_READY_TIMEOUT_SECONDS", "60"),
)
_SERVER_READY_TIMEOUT_SECONDS = float(
    os.environ.get("TEMPORAL_SERVER_READY_TIMEOUT_SECONDS", "120"),
)

# Graceful-shutdown grace. Read directly from the same setting the
# runtime uses (``TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS``) so the
# supervisor and the Python runtime stay in lockstep.
_GRACE_SECONDS = float(
    os.environ.get("TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS", "30"),
)


def _runtime_argv(factory_path: str) -> list[str]:
    """Build the argv for one BaseSupervisor singleton, invoked via the
    generic supervised-runtime shim. ``factory_path`` is the dotted
    ``module:attr`` accessor."""
    return [
        "uv", "run", "python", "-m",
        "cli.commands._supervised_runtime",
        factory_path,
    ]


def temporal_specs(root: Path, cfg) -> list[ServiceSpec]:
    """Return the Temporal ServiceSpec list for the current backend.

    ``cfg`` is ``cli.config.Config``; ``cfg.temporal_port`` drives
    the gRPC readiness probe regardless of backend (same port for
    both ``sqlite`` and ``postgres`` paths).
    """
    backend = os.environ.get(_BACKEND_ENV_VAR, _BACKEND_SQLITE).strip().lower()
    server_dir = root / "server"

    if backend == _BACKEND_POSTGRES:
        return [
            ServiceSpec(
                name="temporal-postgres",
                argv=_runtime_argv("services.temporal._runtime:get_postgres_runtime"),
                cwd=server_dir,
                # pgserver picks a dynamic port; skip the TCP probe here
                # and rely on the temporal-server spec to gate readiness
                # via gRPC (it can't connect until Postgres accepts).
                ready_port=None,
                ready_timeout=_PG_READY_TIMEOUT_SECONDS,
                restart=RestartPolicy.ON_CRASH,
                terminate_grace_seconds=_GRACE_SECONDS,
            ),
            ServiceSpec(
                name="temporal-server",
                argv=_runtime_argv(
                    "services.temporal._runtime:get_temporal_server_runtime",
                ),
                cwd=server_dir,
                ready_port=cfg.temporal_port,
                ready_timeout=_SERVER_READY_TIMEOUT_SECONDS,
                restart=RestartPolicy.ON_CRASH,
                terminate_grace_seconds=_GRACE_SECONDS,
            ),
        ]

    # sqlite (default) — single spec wrapping the binary in
    # ``temporal api`` (the bundled SQLite dev server).
    return [
        ServiceSpec(
            name="temporal",
            argv=["temporal", "api"],
            cwd=root,
            ready_port=cfg.temporal_port,
            restart=RestartPolicy.ON_CRASH,
        ),
    ]


__all__ = ["temporal_specs"]
