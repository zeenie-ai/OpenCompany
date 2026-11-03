"""``add_worktree`` against a real git repository.

A pooled CLI session borrows the repo around its workspace, which in dev is
the OpenCompany checkout itself. Its worktree is detached, so the repo gains
no branch, and a worktree folder deleted by a data reset or a worktree
cleanup can be created again. A leftover ``opencompany/<node>-session``
branch used to fail every later run with "a branch named ... already
exists".
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from services.cli_agent.worktree import add_worktree, remove_worktree


@pytest.fixture
def repo(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    root = tmp_path.resolve() / "repo"
    root.mkdir()

    def run(*args: str) -> str:
        return subprocess.run([git, "-C", str(root), *args], check=True, capture_output=True, text=True).stdout

    run("init", "-q")
    run("-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-q", "--allow-empty", "-m", "start")
    return root, run


def _branches(run) -> str:
    return run("for-each-ref", "--format=%(refname)", "refs/heads/")


@pytest.mark.asyncio
async def test_a_detached_worktree_adds_no_branch_and_comes_back_after_its_folder_is_deleted(repo, tmp_path):
    root, run = repo
    before = _branches(run)
    worktree = tmp_path.resolve() / "workspace" / "node" / "wt_session"

    assert await add_worktree(root, worktree) == worktree
    assert (worktree / ".git").is_file()
    assert _branches(run) == before

    # A data reset deletes the folder; git still lists the worktree.
    shutil.rmtree(worktree)
    assert await add_worktree(root, worktree) == worktree
    assert (worktree / ".git").is_file()
    assert _branches(run) == before

    await remove_worktree(root, worktree)
    assert not worktree.exists()
    listed = [line for line in run("worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]
    assert len(listed) == 1  # only the main checkout


@pytest.mark.asyncio
async def test_a_named_branch_is_still_created(repo, tmp_path):
    root, run = repo
    worktree = tmp_path.resolve() / "workspace" / "node" / "wt_t_1"

    await add_worktree(root, worktree, "opencompany/t_1")

    assert "refs/heads/opencompany/t_1" in _branches(run)
