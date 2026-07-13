"""Typer CLI for ``company``.

Every verb is registered here as a thin stub that lazy-imports its
implementation on dispatch. Running ``company clean`` (the recovery
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

import sys
from pathlib import Path

import typer

app = typer.Typer(
    name="company",
    help="OpenCompany project supervisor CLI.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """Marks the Typer app as a multi-command group."""
    if Path(sys.argv[0]).stem.casefold() == "machina":
        typer.echo(
            "Warning: `machina` is deprecated; use `company` instead.",
            err=True,
        )


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
    help="Stop all OpenCompany services and free configured ports.",
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


@app.command(
    "serve",
    help="Run on a single public port (API + WebSocket + built SPA + sidecar). Used by `company deploy`.",
)
def _serve(
    port: int | None = typer.Option(
        None,
        "--port",
        help="Public port (default: $PORT, else PYTHON_BACKEND_PORT).",
    ),
) -> None:
    from cli.commands.serve import serve_command

    serve_command(port=port)


# --------------------------------------------------------------- daemon group

daemon_app = typer.Typer(
    name="daemon",
    help="Run the OpenCompany backend as a detached process.",
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


# --------------------------------------------------------------- deploy group

deploy_app = typer.Typer(
    name="deploy",
    help="Provision a fresh cloud VM running OpenCompany (via Terraform).",
    no_args_is_help=True,
    add_completion=False,
)


@deploy_app.command(
    "up",
    help="Create an OpenCompany VM, install OpenCompany behind the login gate, and run it.",
)
def _deploy_up(
    provider: str = typer.Option("gcp", "--provider", help="Cloud provider: gcp (aws is a follow-on)."),
    owner_email: str = typer.Option(..., "--owner-email", help="Login email for the owner account."),
    owner_password: str | None = typer.Option(
        None, "--owner-password", help="Login password (>=8 chars). Generated + printed once if omitted."
    ),
    source: str = typer.Option("local", "--source", help="Install source: local (npm pack) or release (npm registry)."),
    version: str = typer.Option("latest", "--version", help="opencompany version when --source release."),
    machine_type: str = typer.Option("e2-standard-2", "--machine-type", help="VM size."),
    port: int = typer.Option(8080, "--port", help="Public port the app binds + the firewall opens."),
    allow_cidr: str = typer.Option("0.0.0.0/0", "--allow-cidr", help="Firewall source range (restrict to your IP/32)."),
    region: str | None = typer.Option(None, "--region", help="Cloud region (provider default if omitted)."),
    zone: str | None = typer.Option(None, "--zone", help="Cloud zone (provider default if omitted)."),
    project: str | None = typer.Option(None, "--project", help="GCP project (defaults to gcloud config)."),
) -> None:
    from cli.commands.deploy.up import up_command

    up_command(
        provider=provider,
        region=region,
        zone=zone,
        machine_type=machine_type,
        port=port,
        owner_email=owner_email,
        owner_password=owner_password,
        source=source,
        version=version,
        allow_cidr=allow_cidr,
        project=project,
    )


@deploy_app.command(
    "status",
    help="Show the OpenCompany deployment's URL + health.",
)
def _deploy_status() -> None:
    from cli.commands.deploy.status import status_command

    status_command()


@deploy_app.command(
    "destroy",
    help="Terraform-destroy the OpenCompany deployment and remove its local state.",
)
def _deploy_destroy(
    keep_state: bool = typer.Option(False, "--keep-state", help="Keep the local Terraform state dir."),
) -> None:
    from cli.commands.deploy.destroy import destroy_command

    destroy_command(keep_state=keep_state)


app.add_typer(deploy_app, name="deploy")


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
