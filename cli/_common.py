"""Cross-verb helpers shared by ``company`` commands.

Centralises the four patterns that previously appeared inlined in
``start.py`` / ``dev.py`` / ``stop.py`` / ``clean.py`` / ``build.py``:

  - :func:`preflight`        -- ``load_config()`` + ``project_root()``
                                preamble every verb opens with.
  - :func:`free_all_ports`   -- ``for port in cfg.all_ports: kill_port``
                                loop; lazy-imports ``cli.ports`` so a
                                broken ``psutil`` install doesn't take
                                down the recovery verb.
  - :func:`build_backend_spec` -- the shared uvicorn ServiceSpec used
                                  by both ``start`` and ``dev``.
  - :func:`error_block`      -- multi-line ``[red]Error: ...[/]``
                                formatter; lazy-imports ``cli.colors``.

No third-party packages are imported at module load time. Importing
this module from ``cli/commands/clean.py`` is safe even when
``rich`` / ``psutil`` / ``platformdirs`` are missing -- only the
helper that actually needs the dep pulls it in.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from cli.config import Config, load_config
from cli.platform_ import project_root, server_dir

if TYPE_CHECKING:
    from cli.ports import KillResult
    from cli.supervisor import ServiceSpec

# ``cli.run`` (and ``cli.supervisor`` via ``cli.colors``) transitively
# pull in ``rich``. They're only needed by ``build_backend_spec`` /
# ``error_block`` which themselves lazy-import. Keeping them out of the
# top-level import list means ``company clean`` (which calls only
# ``preflight`` and ``free_all_ports``) loads even when ``rich`` /
# ``psutil`` / ``platformdirs`` are unavailable.


def preflight(cfg: Config | None = None) -> tuple[Config, Path]:
    """Standard verb preamble: ``(load_config(), project_root())``.

    Pass an explicit ``cfg`` to avoid the lookup (e.g. when the caller
    already has one). ``load_config`` is ``@lru_cache``d, so repeated
    calls inside the same process are free regardless.
    """
    return cfg or load_config(), project_root()


def free_all_ports(cfg: Config) -> list[KillResult]:
    """Kill anything listening on ``cfg.all_ports``; return per-port
    results.

    Returns a list (not a count) so callers can render per-port status
    -- ``company stop`` shows ``[OK]`` / ``[!!]`` markers and the killed
    PIDs, ``company start`` and ``clean`` just want them gone. The
    helper centralises the iteration, callers pick the rendering.

    ``cli.ports`` is imported lazily so this module loads even when
    ``psutil`` (a transitive dep) is missing -- important for the
    recovery verb ``company clean``.
    """
    from cli.ports import kill_port

    grace = backend_shutdown_grace_seconds(cfg)
    return [
        kill_port(port, backend_graceful_timeout=grace)
        for port in cfg.all_ports
    ]


# Seconds uvicorn waits for open connections / in-flight handler tasks before
# running the lifespan shutdown. uvicorn's default is "forever": a browser
# WebSocket that dies with a TCP reset can leave its handler task lingering,
# and then the backend never reaches the teardown that reaps Temporal, the
# JS executor sidecar (bun) and the WhatsApp bridge. This is only the connection-drain
# phase, not the full backend shutdown budget. Same value the desktop shell passes.
UVICORN_GRACEFUL_SHUTDOWN_SECONDS = 5
BACKEND_CLEANUP_GRACE_SECONDS = 30


def backend_shutdown_grace_seconds(cfg: Config) -> float:
    """Allow connection drain, Temporal teardown, then other cleanup.

    ``preflight``/``load_config`` has already merged the env files into
    ``os.environ``, matching the backend's Settings without importing its
    dependencies. The workers drain together (one grace window), then the
    local Temporal runtime stops (another); the third window is headroom.
    Other plugin/resource cleanup gets an additional bounded allowance.
    """
    temporal_grace = 0
    if cfg.temporal_enabled:
        temporal_grace = int(os.environ["TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS"])
        if temporal_grace < 1:
            raise ValueError("TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS must be at least 1")
    return float(
        UVICORN_GRACEFUL_SHUTDOWN_SECONDS
        + 3 * temporal_grace
        + BACKEND_CLEANUP_GRACE_SECONDS
    )


def build_backend_spec(
    cfg: Config,
    *,
    host: str,
    root: Path | None = None,
    port: int | None = None,
    env: dict[str, str] | None = None,
) -> "ServiceSpec":
    """The Python backend ``ServiceSpec`` shared by ``start`` and ``dev``.

    Invokes the server venv's interpreter directly — the same idiom
    ``company serve`` uses (no resident ``uv run`` parent process,
    no runtime dependency on uv being on PATH; ``company build`` owns
    sync semantics). ``host`` is the only differentiator (``start``
    picks ``127.0.0.1`` on Windows/WSL vs ``0.0.0.0`` elsewhere;
    ``dev`` honours its ``--daemon`` flag).

    ``cli.supervisor`` is lazy-imported so this module stays importable
    when its transitive ``rich`` dep is missing -- ``company clean``
    doesn't call this and shouldn't pay for it.
    """
    from cli.platform_ import server_venv_python
    from cli.supervisor import ServiceSpec

    bind_port = cfg.backend_port if port is None else port
    return ServiceSpec(
        name="server",
        argv=[
            str(server_venv_python(root)),
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            host,
            "--port",
            str(bind_port),
            "--log-level",
            "warning",
            "--timeout-graceful-shutdown",
            str(UVICORN_GRACEFUL_SHUTDOWN_SECONDS),
        ],
        cwd=server_dir(root),
        env=env or {},
        ready_port=bind_port,
        terminate_grace_seconds=backend_shutdown_grace_seconds(cfg),
    )


def error_block(title: str, lines: list[str]) -> None:
    """Print a multi-line ``[red]Error: ...[/]`` block, indented body.

    ``cli.colors`` is imported lazily so this module remains
    importable without ``rich``. Anything heavier (e.g., remediation
    URLs, code snippets) goes in ``lines`` -- it's rendered as plain
    ``console.print`` calls so rich BBCode in callers' strings still
    interprets.
    """
    from cli.colors import console

    console.print(f"[red]Error: {title}[/]")
    for line in lines:
        console.print(f"  {line}")
