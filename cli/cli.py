"""Typer CLI for ``machina``.

Every verb is registered here as a thin stub that lazy-imports its
implementation on dispatch. Running ``machina clean`` (the recovery
verb) only triggers the import of ``cli.commands.clean`` -- not
``dev`` / ``start`` / temporal specs / daemon verbs / docs / version.

Pattern lifted from gemini-cli's ``gemini.tsx`` + vercel's ``vc.js``
(fast-path ``--version`` and ``--help`` before any heavy import) and
next.js's ``bin/next.ts`` (per-verb ``import('../cli/<verb>.js')``).
Typer's ``add_typer`` requires a concrete sub-app at registration
time, so for grouped verbs (``daemon`` / ``docs`` / ``version``) we
build fresh Typer instances HERE and lazy-wrap each leaf -- avoids
importing ``cli.commands.daemon`` etc. at boot.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="machina",
    help="MachinaOS project supervisor CLI.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """Marks the Typer app as a multi-command group."""


# ----------------------------------------------------------- top-level verbs


@app.command(
    "start",
    help="Start all services in production mode (static client + uvicorn + temporal).",
)
def _start() -> None:
    from cli.commands.start import start_command

    start_command()


@app.command(
    "dev",
    help="Start all services in dev mode (Vite HMR + uvicorn + temporal).",
)
def _dev(
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Bind backend to 0.0.0.0 instead of 127.0.0.1.",
    ),
) -> None:
    from cli.commands.dev import dev_command

    dev_command(daemon=daemon)


@app.command(
    "stop",
    help="Stop all MachinaOS services and free configured ports.",
)
def _stop() -> None:
    from cli.commands.stop import stop_command

    stop_command()


@app.command(
    "clean",
    help="Stop services then remove build artefacts and venvs.",
)
def _clean() -> None:
    from cli.commands.clean import clean_command

    clean_command()


@app.command(
    "build",
    help="Install toolchain + build client + sync Python + verify deps.",
)
def _build() -> None:
    from cli.commands.build import build_command

    build_command()


# --------------------------------------------------------------- daemon group

daemon_app = typer.Typer(
    name="daemon",
    help="Run the MachinaOs backend as a detached process.",
    no_args_is_help=True,
    add_completion=False,
)


@daemon_app.command(
    "start",
    help="Start the backend in the background; write PID file to user-data dir.",
)
def _daemon_start() -> None:
    from cli.commands.daemon.start import start_command

    start_command()


@daemon_app.command(
    "stop",
    help="Stop the backend if running; clear PID file either way.",
)
def _daemon_stop() -> None:
    from cli.commands.daemon.stop import stop_command

    stop_command()


@daemon_app.command(
    "status",
    help="Exit 0 if running (and print PID); exit 1 otherwise.",
)
def _daemon_status() -> None:
    from cli.commands.daemon.status import status_command

    status_command()


@daemon_app.command(
    "restart",
    help="Stop the backend then start it again; convenience wrapper.",
)
def _daemon_restart() -> None:
    from cli.commands.daemon.restart import restart_command

    restart_command()


app.add_typer(daemon_app, name="daemon")


# ----------------------------------------------------------------- docs group

docs_app = typer.Typer(
    name="docs",
    help="Documentation tooling (rebuild indices, completeness checks).",
    no_args_is_help=True,
    add_completion=False,
)


@docs_app.command(
    "nodes",
    help="Rebuild (or --check) the per-node documentation index.",
)
def _docs_nodes(
    check: bool = typer.Option(
        False,
        "--check",
        help="Instead of rewriting, exit 1 if any registered node lacks a doc.",
    ),
) -> None:
    from cli.commands.docs import nodes

    nodes(check=check)


app.add_typer(docs_app, name="docs")


# -------------------------------------------------------------- version group

version_app = typer.Typer(
    name="version",
    help="Version-sync tooling.",
    no_args_is_help=True,
    add_completion=False,
)


@version_app.command(
    "sync",
    help="Sync package.json versions from latest git tag.",
)
def _version_sync(
    tag: str | None = typer.Argument(
        None,
        help="Git tag to use (defaults to latest).",
    ),
) -> None:
    from cli.commands.version import sync

    sync(tag=tag)


app.add_typer(version_app, name="version")


if __name__ == "__main__":
    app()
