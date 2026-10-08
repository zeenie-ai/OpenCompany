"""Per-workflow git worktrees for CLI agent sessions.

Every CLI session runs inside a worktree branched off the repo that
contains the workflow workspace, never in the repo root itself. That keeps
one workflow's edits off another's checkout and gives a pooled session a
stable, workflow-owned cwd. The ``git worktree add`` / ``remove`` calls
lived in ``AICliSession``; they are shared here so the session pool path
uses the same commands.
"""

from __future__ import annotations

from pathlib import Path

import anyio
from services.process_environment import without_onepassword_environment


async def add_worktree(repo_root: Path, worktree_dir: Path, branch: str) -> Path:
    """Create ``worktree_dir`` as a new worktree of ``repo_root`` on a new
    ``branch``. Reuses an existing worktree directory as-is. Raises
    ``RuntimeError`` with git's stderr on failure."""
    if worktree_dir.exists():
        return worktree_dir
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    proc = await anyio.run_process(
        ["git", "-C", str(repo_root), "worktree", "add", str(worktree_dir), "-b", branch],
        check=False,
        env=without_onepassword_environment(),
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git worktree add failed: {err}")
    return worktree_dir


async def remove_worktree(repo_root: Path, worktree_dir: Path) -> None:
    """Best-effort ``git worktree remove --force``."""
    await anyio.run_process(
        ["git", "-C", str(repo_root), "worktree", "remove", "--force", str(worktree_dir)],
        check=False,
        env=without_onepassword_environment(),
    )
