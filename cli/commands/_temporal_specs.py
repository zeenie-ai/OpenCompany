"""ServiceSpec for the Temporal stack — shared by ``start`` and ``dev``.

One ServiceSpec running the supervised :class:`TemporalServerRuntime`
singleton via :mod:`services.temporal._supervised_runtime` (a thin
shim that lives next to the runtime factory it imports). The runtime
spawns the official ``temporal`` CLI (downloaded by pooch from
https://temporal.download/cli/archive/latest) with the ``server
start-dev`` subcommand against a SQLite db at
``settings.temporal_sqlite_path``.

Matches the local-dev install method documented at
https://docs.temporal.io/develop/python/set-up-your-local-python.
"""
from __future__ import annotations

import os
from pathlib import Path

from cli.config import Config
from cli.platform_ import server_dir
from cli.run import uv_run
from cli.supervisor import RestartPolicy, ServiceSpec


def temporal_specs(root: Path, cfg: Config) -> list[ServiceSpec]:
    """Return the Temporal ServiceSpec list.

    ``cfg`` is :class:`cli.config.Config`; ``cfg.temporal_port`` drives
    the gRPC readiness probe.

    Readiness-probe + graceful-shutdown windows are read at call time
    from ``os.environ`` (pushed there by :func:`cli.config.load_config`
    from ``.env.template``); ``KeyError`` if missing — broken install.
    Doing the reads here, not at module import, keeps the module safe
    to import before ``load_config()`` runs (tests, REPL, reverse
    import order).
    """
    return [
        ServiceSpec(
            name="temporal",
            argv=uv_run(
                "python", "-m", "services.temporal._supervised_runtime",
                "services.temporal._runtime:get_temporal_server_runtime",
            ),
            cwd=server_dir(root),
            ready_port=cfg.temporal_port,
            ready_timeout=float(os.environ["TEMPORAL_SERVER_READY_TIMEOUT_SECONDS"]),
            restart=RestartPolicy.ON_CRASH,
            terminate_grace_seconds=float(os.environ["TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS"]),
        ),
    ]


__all__ = ["temporal_specs"]
