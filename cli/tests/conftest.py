"""Shared fixtures for ``machina`` tests.

The release-pipeline config tests need to read files that live in
pnpm workspace members (``client/``, ``server/nodejs/``) without
hardcoding those filesystem paths in every test. The canonical
oracle for "where does workspace X live" is pnpm itself:

    pnpm list --json --only-projects --recursive --depth 0

That returns every workspace member's name + absolute path. We invoke
it once per test session and expose the result as a name → path map.

Tests then reference workspaces by their npm package name (a stable
identifier defined in each ``package.json``) rather than by path. If
``server/nodejs/`` is later renamed or moved, the test suite keeps
working — pnpm resolves the new location.

For files that don't live inside a workspace (``.github/workflows``,
``scripts/install.js``), tests resolve via the ``root`` fixture which
points at ``cli.platform_.project_root()`` — the project's own
canonical worktree-aware helper.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cli.platform_ import project_root
from cli.run import which_argv


@pytest.fixture(scope="session")
def root() -> Path:
    """The project root, resolved via ``cli.platform_.project_root``."""
    return project_root()


@pytest.fixture(scope="session")
def workspace_members(root: Path) -> dict[str, Path]:
    """Map of pnpm workspace member name → absolute filesystem path.

    Resolved once per session by shelling out to ``pnpm``. The ``--depth 0``
    + ``--only-projects`` flags trim the output to just workspace members
    (no transitive npm deps). Skips with a clear message if pnpm isn't
    installed so the rest of the suite remains runnable.

    Reference: https://pnpm.io/cli/list
    """
    # which_argv resolves Windows .cmd / .bat shims via PATHEXT — the
    # same helper cli.run uses everywhere else for the same reason.
    argv = which_argv(
        ["pnpm", "list", "--json", "--only-projects", "--recursive", "--depth", "0"]
    )
    try:
        out = subprocess.run(
            argv,
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        pytest.skip(f"pnpm workspace listing unavailable ({e}); skipping config tests")
    members = json.loads(out.stdout)
    return {m["name"]: Path(m["path"]) for m in members}
