"""``company build`` -- replaces ``scripts/build.js``.

Checks toolchain (node, npm, python, uv), then runs the 6-step
build: ``.env`` bootstrap -> ``pnpm install`` -> client build ->
Node.js sidecar bundle -> ``uv sync`` -> compile Python bytecode
-> pooch-fetch Temporal binary.

Layers ``.env.dev`` (when present in the checkout) BEFORE running
those steps so the build's ``DATA_DIR`` matches the runtime's
expected location. Without that, ``company build`` would pooch
Temporal under ``~/.opencompany/`` and ``company dev`` would re-fetch
it into ``<repo>/.opencompany/`` — a redundant download on every fresh
clone. Production (global-install) operators never have
``.env.dev`` in their checkout, so the layering is a no-op there.

The ``OPENCOMPANY_BUILDING`` env var is set so ``scripts/postinstall.js``
skips its own ``install.js`` invocation when build is the orchestrator.
"""

from __future__ import annotations

import os
import re
import shutil
import sys

import typer

from cli._common import error_block
from cli.run import capture, run, uv_run
from cli.colors import console
from cli.platform_ import node_modules_dir, project_root, server_dir


# Source dirs / entry-point modules under ``server/`` that ``company build``
# pre-compiles to plain .pyc bytecode in step [5/6]. Public so tests and
# ``scripts/install.js`` (which mirrors this list) stay in sync — both
# the install pipeline and tests should read this constant rather than
# duplicate the list. Excludes ``.venv/`` (compiled by ``uv sync`` via
# ``[tool.uv] compile-bytecode = true`` in server/pyproject.toml; some
# site-packages also contain non-Python templates) and ``tests/``
# (ships outside the production tarball).
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
        error_block("failed to install uv.", ["See https://docs.astral.sh/uv/"])
        raise typer.Exit(code=1)
    console.print(f"  uv: {version}")
    return version


# ---------------------------------------------------------------- build


def build_command() -> None:
    root = project_root()

    # Layer ``.env.dev`` (if present) BEFORE the install steps so the
    # build's ``DATA_DIR`` matches what the runtime (``company dev``)
    # will see. Without this, ``company build`` reads
    # ``DATA_DIR=~/.opencompany`` from ``.env.template`` and lands the
    # Temporal CLI under user home, but ``company dev`` then reads
    # ``DATA_DIR=.opencompany`` from ``.env.dev`` and re-downloads into
    # ``<repo>/.opencompany/`` — a cache-miss the operator pays on every
    # fresh checkout.
    #
    # Safe for global installs: ``.env.dev`` is committed to git for
    # repo-clone contributors but is NOT in the npm ``files`` list, so
    # ``npm install -g @zeenie-ai/opencompany`` doesn't ship it. Without
    # ``.env.dev`` on disk, :func:`load_dev_overrides` is a no-op and
    # the build falls through to ``.env.template`` defaults
    # (``DATA_DIR=~/.opencompany``) — identical to today's behaviour and
    # matching what ``company start`` / ``company daemon`` use at
    # runtime.
    from cli.config import load_dev_overrides

    load_dev_overrides(root)

    # Prevent the postinstall orchestrator from re-running install.js when
    # we're orchestrating ourselves (matches the existing JS contract).
    os.environ["OPENCOMPANY_BUILDING"] = "true"
    # Mixed-version installs may still contain the old postinstall hook.
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
        error_block("Node.js is required.", [])
        raise typer.Exit(code=1)

    npm_version = capture(["npm", "--version"])
    console.print(f"  npm: {npm_version or '[red]not found[/]'}")

    python_cmd = _which_python()
    if not python_cmd or not _check_python(python_cmd):
        error_block(
            "Python 3.12+ is required.",
            ["Install from https://python.org/downloads/"],
        )
        raise typer.Exit(code=1)

    _ensure_uv(python_cmd)

    console.print("\n[green]All dependencies ready.[/]\n")

    # ---- build steps -------------------------------------------------
    server_cwd = server_dir(root)
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
    run(["pnpm", "--filter", "opencompany-nodejs-executor", "run", "build"], cwd=root)

    console.log("[4/6] Installing Python dependencies...")
    # ``server/`` is its own standalone uv project; ``uv sync`` at that
    # directory resolves ``server/pyproject.toml`` and materialises
    # ``server/.venv``. The CLI lives in a separate venv (or system
    # python via pipx) and never shares this environment -- see
    # ``cli/platform_.py`` docstring for the rationale. ``uv sync``
    # is idempotent and creates the venv on first run.
    run(["uv", "sync"], cwd=server_cwd)

    # Pre-compile our Python sources to plain .pyc. No `-O`: every
    # runtime (uvicorn via `uv run`, `company serve`'s venv python) runs
    # WITHOUT -O, and per PEP 488 a non-optimized interpreter only loads
    # plain .pyc — `-O`-produced .opt-1.pyc files are never loaded and
    # the sources get recompiled on first import anyway. `-q` silences
    # per-file output (errors still print); `-j 0` parallelises across
    # all CPU cores.
    #
    # Scoped to the project's own source dirs — `.venv/` packages are
    # compiled by `uv sync` itself via `[tool.uv] compile-bytecode = true`
    # in server/pyproject.toml, and `tests/` ships outside the tarball.
    # Compiling `.venv/` here would be duplicate work and fails on
    # cookiecutter template files inside packages like crawlee that
    # aren't real Python.
    console.log("[5/6] Compiling Python bytecode...")
    run(
        uv_run(
            "python", "-m", "compileall", "-q", "-j", "0", *COMPILEALL_SOURCE_DIRS
        ),
        cwd=server_cwd,
        check=False,  # missing pyc is non-fatal — runtime regenerates as needed
    )

    # Temporal is a required runtime dep: fetch + verify the binaries
    # now so first ``company start`` is instant instead of paying the
    # ~90 MB pooch download cost inside ``_pre_spawn``. The ``-m
    # services.temporal._install`` entry runs ``ensure_temporal_binaries``
    # then asserts every extracted path exists; non-zero exit aborts
    # the build via ``run(check=True)``. Idempotent on re-build (pooch
    # cache hit) so this step is sub-second once the tarball is on disk.
    console.log("[6/6] Installing Temporal binaries...")
    run(
        uv_run("python", "-m", "services.temporal._install"),
        cwd=server_cwd,
    )

    console.log("[green]Build complete.[/]")
