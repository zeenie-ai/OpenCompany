"""Typer CLI for ``machina``.

Each subcommand lives under ``cli.commands``; this module just
mounts them and exposes ``app`` as the entry point.
"""

from __future__ import annotations

import typer

from cli.commands.build import build_command
from cli.commands.clean import clean_command
from cli.commands.daemon import app as daemon_app
from cli.commands.dev import dev_command
from cli.commands.docs import app as docs_app
from cli.commands.start import start_command
from cli.commands.stop import stop_command
from cli.commands.version import app as version_app


app = typer.Typer(
    name="machina",
    help="MachinaOS project supervisor CLI.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """Marks the Typer app as a multi-command group."""


app.command("start", help="Start all services in production mode (static client + uvicorn + temporal).")(
    start_command
)
app.command("dev", help="Start all services in dev mode (Vite HMR + uvicorn + temporal).")(
    dev_command
)
app.command("stop", help="Stop all MachinaOS services and free configured ports.")(
    stop_command
)
app.command("clean", help="Stop services then remove build artefacts and venvs.")(
    clean_command
)
app.command("build", help="Install toolchain + build client + sync Python + verify deps.")(
    build_command
)
app.add_typer(version_app, name="version")
app.add_typer(docs_app, name="docs")
app.add_typer(daemon_app, name="daemon")


if __name__ == "__main__":
    app()
