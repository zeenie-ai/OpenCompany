"""ServiceSpecs for the Temporal stack — shared by ``start`` and ``dev``.

Returns the right Temporal ``ServiceSpec`` set based on
``cfg.temporal_backend`` (sourced from ``TEMPORAL_BACKEND`` in
``.env.template`` -> ``.env`` -> ``os.environ`` by
:func:`cli.config.load_config`):

  - ``postgres`` (default) — two specs running the supervised runtimes:
    ``temporal-postgres`` (pgserver) then ``temporal-server`` (Temporal
    binary with YAML config). Both wrap ``BaseSupervisor`` singletons
    via ``services.temporal._supervised_runtime`` (a thin shim that
    lives next to the runtime factories it imports). Data lands under
    ``DATA_DIR/postgres/``; binaries come from a pooch-managed cache
    (no system install required).

  - ``sqlite`` — single spec running ``temporal api`` against an
    in-memory SQLite store. Requires the standalone ``temporal`` CLI on
    PATH. Lighter dev path; state is lost on restart.

The supervisor's TCP readiness probes (``ready_port``) order the
postgres → temporal startup automatically — Temporal won't start until
Postgres is accepting connections.

No backend default lives in this module: ``cfg.temporal_backend``
always carries a concrete value loaded from the env files (see
:mod:`cli.config`). Mismatch with ``core.config.Settings.
temporal_backend`` would mean the supervisor disagrees with the runtime
about which spec is active -- the v0.0.79 regression that crashed
``machina start`` after ``npm install -g machinaos``.
"""
from __future__ import annotations

import os
from pathlib import Path

from cli.platform_ import server_dir
from cli.run import uv_run
from cli.supervisor import RestartPolicy, ServiceSpec

# Backend names — must match ``core.config.Settings.temporal_backend``
# enum (``"sqlite" | "postgres"``). The active backend comes from
# ``cfg.temporal_backend`` (env-file driven via ``cli.config``);
# no default lives here.
_BACKEND_SQLITE = "sqlite"
_BACKEND_POSTGRES = "postgres"

# Readiness-probe windows. ``temporal-server`` may need to download
# its binary on first run (~90 MB tarball); the Postgres init step
# runs schema migrations once. These are advanced tuning knobs left as
# pure-process-env overrides (commented hints in ``.env.template``):
# overriding them is rare and per-environment, not worth a typed
# Config field.
_PG_READY_TIMEOUT_SECONDS = float(
    os.environ.get("TEMPORAL_PG_READY_TIMEOUT_SECONDS", "60"),
)
_SERVER_READY_TIMEOUT_SECONDS = float(
    os.environ.get("TEMPORAL_SERVER_READY_TIMEOUT_SECONDS", "120"),
)

# Graceful-shutdown grace. ``TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS`` is
# declared in ``.env.template`` and pushed into ``os.environ`` by
# ``cli.config.load_config``, so this read sees the file value.
_GRACE_SECONDS = float(
    os.environ.get("TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS", "30"),
)


def _runtime_argv(factory_path: str) -> list[str]:
    """Build the argv for one BaseSupervisor singleton, invoked via the
    generic supervised-runtime shim. ``factory_path`` is the dotted
    ``module:attr`` accessor.

    Composes the uv invocation through :func:`cli.run.uv_run` so every
    ``uv run --no-sync`` callsite in the CLI shares the same flag set.
    The shim itself lives at ``services.temporal._supervised_runtime``
    (next to the runtime factories), so the spawned python only needs
    to resolve modules out of the workspace ``.venv``.
    """
    return uv_run(
        "python", "-m", "services.temporal._supervised_runtime",
        factory_path,
    )


def temporal_specs(root: Path, cfg) -> list[ServiceSpec]:
    """Return the Temporal ServiceSpec list for the current backend.

    ``cfg`` is :class:`cli.config.Config`; ``cfg.temporal_backend``
    picks the spec set and ``cfg.temporal_port`` drives the gRPC
    readiness probe (same port for both ``sqlite`` and ``postgres``).
    """
    backend = cfg.temporal_backend.strip().lower()
    server_cwd = server_dir(root)

    if backend == _BACKEND_POSTGRES:
        return [
            ServiceSpec(
                name="postgres",
                argv=_runtime_argv("services.temporal._runtime:get_postgres_runtime"),
                cwd=server_cwd,
                # pgserver picks a dynamic port; skip the TCP probe here
                # and rely on the temporal spec to gate readiness via
                # gRPC (it can't connect until Postgres accepts).
                ready_port=None,
                ready_timeout=_PG_READY_TIMEOUT_SECONDS,
                restart=RestartPolicy.ON_CRASH,
                terminate_grace_seconds=_GRACE_SECONDS,
            ),
            ServiceSpec(
                name="temporal",
                argv=_runtime_argv(
                    "services.temporal._runtime:get_temporal_server_runtime",
                ),
                cwd=server_cwd,
                ready_port=cfg.temporal_port,
                ready_timeout=_SERVER_READY_TIMEOUT_SECONDS,
                restart=RestartPolicy.ON_CRASH,
                terminate_grace_seconds=_GRACE_SECONDS,
            ),
        ]

    # sqlite — single spec wrapping the pooch-installed ``temporal``
    # CLI's ``server start-dev`` subcommand against a SQLite db at
    # ``settings.temporal_sqlite_path``. Same supervised-runtime shim
    # as the postgres backend; ``TemporalServerRuntime`` switches on
    # ``settings.temporal_backend`` to pick the right binary + argv
    # (see ``server/services/temporal/_runtime.py``).
    return [
        ServiceSpec(
            name="temporal",
            argv=_runtime_argv(
                "services.temporal._runtime:get_temporal_server_runtime",
            ),
            cwd=server_cwd,
            ready_port=cfg.temporal_port,
            ready_timeout=_SERVER_READY_TIMEOUT_SECONDS,
            restart=RestartPolicy.ON_CRASH,
            terminate_grace_seconds=_GRACE_SECONDS,
        ),
    ]


__all__ = ["temporal_specs"]
