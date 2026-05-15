"""``machina build`` -- replaces ``scripts/build.js``.

Checks toolchain (node, npm, python, uv, temporal-server), then runs
the 4-step build: ``.env`` bootstrap -> ``pnpm install`` -> client
build -> ``uv sync`` -> verify edgymeow binary.

The ``MACHINAOS_BUILDING`` env var is set so ``scripts/postinstall.js``
skips its own ``install.js`` invocation when build is the orchestrator.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

import typer

from cli.run import capture, run
from cli.colors import console
from cli.platform_ import project_root


# Source dirs / entry-point modules under ``server/`` that ``machina build``
# pre-compiles to optimised bytecode in step [5/6]. Public so tests and
# ``scripts/install.js`` (which mirrors this list) stay in sync — both
# the install pipeline and tests should read this constant rather than
# duplicate the list. Excludes ``.venv/`` (uv compiles deps at install
# time and some site-packages contain non-Python templates) and
# ``tests/`` (ships outside the production tarball).
COMPILEALL_SOURCE_DIRS: tuple[str, ...] = (
    "services",
    "core",
    "nodes",
    "routers",
    "models",
    "middleware",
    "main.py",
    "constants.py",
)


# ---------------------------------------------------------------- helpers

def _which_python() -> str | None:
    """Prefer ``python3`` so we don't pick up Python 2.x on POSIX distros."""
    for cmd in ("python3", "python"):
        if shutil.which(cmd):
            return cmd
    return None


def _check_python(cmd: str) -> bool:
    out = capture([cmd, "--version"])
    if not out:
        return False
    match = re.search(r"Python (\d+)\.(\d+)", out)
    if not match:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    if major >= 3 and minor >= 12:
        console.print(f"  {out}")
        return True
    console.print(f"  {out} [red](too old, need 3.12+)[/]")
    return False


def _ensure_pip(python_cmd: str) -> None:
    if not capture([python_cmd, "-m", "pip", "--version"]):
        console.print("  Installing pip via ensurepip...")
        run([python_cmd, "-m", "ensurepip", "--upgrade"])


def _ensure_uv(python_cmd: str) -> str:
    """Install ``uv`` via pip if missing; return the resolved version string."""
    version = capture(["uv", "--version"])
    if version:
        console.print(f"  uv: {version}")
        return version
    _ensure_pip(python_cmd)
    console.print("  Installing uv via pip...")
    run([python_cmd, "-m", "pip", "install", "uv"])
    version = capture(["uv", "--version"])
    if not version:
        console.print("[red]Error: failed to install uv. See https://docs.astral.sh/uv/[/]")
        raise typer.Exit(code=1)
    console.print(f"  uv: {version}")
    return version


def _ensure_temporal() -> None:
    """Ensure the ``temporal`` CLI (from npm package ``temporal-server``) is on PATH."""
    version = capture(["temporal", "--version"])
    if version:
        console.print(f"  temporal: {version}")
        return
    console.print("  temporal: not found, installing globally...")
    rc = run(["npm", "install", "-g", "temporal-server"], check=False)
    if rc != 0:
        console.print(
            "  [yellow]Warning: temporal install failed. "
            "Distributed execution unavailable.[/]"
        )
        return
    version = capture(["temporal", "--version"])
    if version:
        console.print(f"  temporal: {version}")


# ---------------------------------------------------------------- build

def build_command() -> None:
    root = project_root()

    # Prevent the postinstall orchestrator from re-running install.js when
    # we're orchestrating ourselves (matches the existing JS contract).
    os.environ["MACHINAOS_BUILDING"] = "true"
    os.environ.setdefault("PYTHONUTF8", "1")

    is_postinstall = os.environ.get("npm_lifecycle_event") == "postinstall"
    is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
    if is_ci and is_postinstall:
        console.print("CI environment detected, skipping postinstall build.")
        return

    # ---- toolchain ---------------------------------------------------
    console.print("[bold]Checking dependencies...[/]\n")
    node_version = capture(["node", "--version"])
    console.print(f"  Node.js: {node_version or '[red]not found[/]'}")
    if not node_version:
        console.print("[red]Error: Node.js is required.[/]")
        raise typer.Exit(code=1)

    npm_version = capture(["npm", "--version"])
    console.print(f"  npm: {npm_version or '[red]not found[/]'}")

    python_cmd = _which_python()
    if not python_cmd or not _check_python(python_cmd):
        console.print(
            "[red]Error: Python 3.12+ is required.[/] "
            "Install from https://python.org/downloads/"
        )
        raise typer.Exit(code=1)

    _ensure_uv(python_cmd)
    _ensure_temporal()

    console.print("\n[green]All dependencies ready.[/]\n")

    # ---- build steps -------------------------------------------------
    server_dir = root / "server"
    env_path = root / ".env"
    template_path = root / ".env.template"

    # Step markers go through ``console.log`` so each [N/6] line is
    # timestamped — diff between consecutive timestamps is the wall-clock
    # cost of that step, no manual instrumentation needed.
    if not env_path.exists() and template_path.exists():
        shutil.copy2(template_path, env_path)
        console.log("[0/6] Created .env from template")

    if not is_postinstall:
        console.log("[1/6] Installing dependencies...")
        run(["pnpm", "install"], cwd=root)
    else:
        console.log("[1/6] Dependencies already installed by package manager")

    console.log("[2/6] Building client...")
    run(["pnpm", "--filter", "react-flow-client", "run", "build"], cwd=root)

    # Pre-bundle the Node.js sidecar (server/nodejs) with esbuild so the
    # production `npm start` runs `node dist/index.js` instead of
    # interpreting `tsx src/index.ts`. Saves ~500ms-1s of cold start
    # whenever the executor is launched. The bundle keeps Express
    # external (it stays in node_modules), so the patch flow is intact.
    console.log("[3/6] Building Node.js sidecar...")
    run(["pnpm", "--filter", "machinaos-nodejs-executor", "run", "build"], cwd=root)

    console.log("[4/6] Installing Python dependencies...")
    if not (server_dir / ".venv").exists():
        run(["uv", "venv"], cwd=server_dir)
    run(["uv", "sync"], cwd=server_dir)

    # Pre-compile our Python sources to optimised bytecode (.opt-1.pyc).
    # `-O` strips `assert` statements + `__debug__` branches; `-q`
    # silences per-file output (errors still print); `-j 0` parallelises
    # across all CPU cores. Cuts a measurable few seconds off cold
    # start by avoiding source-to-bytecode work on first import.
    #
    # Scoped to the project's own source dirs — `uv sync` already
    # compiles `.venv/` packages, and `tests/` ships outside the
    # tarball. Compiling `.venv/` is wasted work and fails on cookiecutter
    # template files inside packages like crawlee that aren't real Python.
    console.log("[5/6] Compiling Python bytecode...")
    run(
        ["uv", "run", "python", "-O", "-m", "compileall", "-q", "-j", "0", *COMPILEALL_SOURCE_DIRS],
        cwd=server_dir,
        check=False,  # missing pyc is non-fatal — runtime regenerates as needed
    )

    console.log("[6/6] Verifying edgymeow binary...")
    bin_name = "edgymeow-server.exe" if sys.platform == "win32" else "edgymeow-server"
    edgymeow_bin = root / "node_modules" / "edgymeow" / "bin" / bin_name
    if edgymeow_bin.exists():
        console.print(f"  Binary present: {edgymeow_bin}")
    else:
        console.print(
            "  [yellow]Warning: edgymeow binary not found. "
            "Set WHATSAPP_RUNTIME_ENABLED=false to disable.[/]"
        )

    console.log("[green]Build complete.[/]")
