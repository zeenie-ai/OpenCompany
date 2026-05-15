"""Entry point for ``python -m cli``.

pnpm scripts call ``python -m cli ...`` from the system Python,
which only has ``uv`` -- not typer / rich / anyio / psutil. On first
run we detect the missing imports, pip-install the declared runtime
deps, and retry, so the user never has to ``pip install -e .`` by hand.

The public CLI command is still ``machina`` (the user-facing name);
``cli`` is just the Python import path after the
``<repo>/machina/ → <repo>/cli/`` source-dir rename.
"""

import subprocess
import sys

_RUNTIME_DEPS = (
    "typer>=0.12",
    "rich>=13.0",
    "anyio>=4.0",
    "psutil>=6.0",
)


def _bootstrap_deps() -> None:
    print("machina: installing runtime dependencies (first run)...", file=sys.stderr)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", *_RUNTIME_DEPS]
    )


try:
    from cli.cli import app
except ImportError:
    _bootstrap_deps()
    from cli.cli import app


if __name__ == "__main__":
    app()
