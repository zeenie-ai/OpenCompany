"""Smoke tests for ``cli.commands.daemon`` (verb-per-file package).

Post-refactor, the shared helpers live in :mod:`cli.commands.daemon._state`:

  - ``detached_kwargs()``        -- POSIX vs Windows spawn flags
  - ``pid_file()`` / ``pid_dir()`` / ``log_file()``  -- lazy path lookups
  - ``read_pid()``               -- PID-file → live-pid lookup
  - ``kill_tree(pid)``           -- psutil-backed recursive terminate

The verb entry points live in sibling modules (``start.py`` / ``stop.py``
/ ``status.py`` / ``restart.py``). Patches target whichever module
actually owns the attribute under test -- mirrors how pdm tests its
``commands/venv/`` sub-package.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil
import pytest

from cli.commands.daemon import _state, status, stop


# ---------------------------------------------------------------- helpers


def test_detached_kwargs_windows_uses_creationflags():
    # subprocess.DETACHED_PROCESS and CREATE_NEW_PROCESS_GROUP are only
    # defined on Windows. Patching them in with ``create=True`` lets the
    # test cover the Windows branch of ``detached_kwargs`` on every CI
    # runner (POSIX or Windows) without a skipif that would leave the
    # branch entirely uncovered on the Linux-only CI matrix.
    # Real values from windows.h: DETACHED_PROCESS=0x08, GROUP=0x200.
    fake_detached = 0x00000008
    fake_new_group = 0x00000200
    with (
        patch.object(_state, "IS_WINDOWS", True),
        patch.object(subprocess, "DETACHED_PROCESS", fake_detached, create=True),
        patch.object(
            subprocess, "CREATE_NEW_PROCESS_GROUP", fake_new_group, create=True
        ),
    ):
        kw = _state.detached_kwargs()
    assert "creationflags" in kw
    assert kw["creationflags"] == fake_detached | fake_new_group
    assert "start_new_session" not in kw


def test_detached_kwargs_posix_uses_setsid():
    with patch.object(_state, "IS_WINDOWS", False):
        kw = _state.detached_kwargs()
    assert kw == {"start_new_session": True}


def test_read_pid_returns_none_when_file_missing(tmp_path: Path):
    with patch.object(_state, "pid_file", return_value=tmp_path / "missing.pid"):
        assert _state.read_pid() is None


def test_read_pid_returns_none_for_corrupt_pid_file(tmp_path: Path):
    pid_path = tmp_path / "cli.pid"
    pid_path.write_text("not-a-number")
    with patch.object(_state, "pid_file", return_value=pid_path):
        assert _state.read_pid() is None


def test_read_pid_returns_none_when_process_no_longer_exists(tmp_path: Path):
    pid_path = tmp_path / "cli.pid"
    pid_path.write_text("999999")
    with (
        patch.object(_state, "pid_file", return_value=pid_path),
        patch.object(_state.psutil, "pid_exists", return_value=False),
    ):
        assert _state.read_pid() is None


def test_read_pid_returns_pid_when_alive(tmp_path: Path):
    pid_path = tmp_path / "cli.pid"
    pid_path.write_text("1234\n")
    with (
        patch.object(_state, "pid_file", return_value=pid_path),
        patch.object(_state.psutil, "pid_exists", return_value=True),
    ):
        assert _state.read_pid() == 1234


# ---------------------------------------------------------------- kill_tree


def test_kill_tree_terminates_children_and_parent():
    parent = MagicMock()
    child1 = MagicMock()
    child2 = MagicMock()
    parent.children.return_value = [child1, child2]
    with patch.object(_state.psutil, "Process", return_value=parent):
        _state.kill_tree(123)
    child1.kill.assert_called_once()
    child2.kill.assert_called_once()
    parent.terminate.assert_called_once()
    parent.wait.assert_called_once_with(timeout=5)


def test_kill_tree_force_kills_on_timeout():
    parent = MagicMock()
    parent.children.return_value = []
    parent.wait.side_effect = psutil.TimeoutExpired(seconds=5)
    with patch.object(_state.psutil, "Process", return_value=parent):
        _state.kill_tree(123)
    parent.terminate.assert_called_once()
    parent.kill.assert_called_once()


def test_kill_tree_no_such_process_is_noop():
    with patch.object(_state.psutil, "Process", side_effect=psutil.NoSuchProcess(123)):
        _state.kill_tree(123)


# ---------------------------------------------------------------- verbs


def test_stop_command_clears_pid_file_when_not_running(tmp_path: Path):
    # ``stop`` module imports ``pid_file`` / ``read_pid`` by name from
    # ``_state``, so the patches must target the consumer's namespace
    # (``stop.pid_file``), not the source module (``_state.pid_file``).
    # Classic "where to patch" gotcha -- see
    # https://docs.python.org/3/library/unittest.mock.html#where-to-patch.
    pid_path = tmp_path / "cli.pid"
    pid_path.write_text("999999")
    with (
        patch.object(stop, "pid_file", return_value=pid_path),
        patch.object(stop, "read_pid", return_value=None),
    ):
        stop.stop_command()
    assert not pid_path.exists()


def test_status_command_exits_1_when_not_running():
    import typer

    with (
        patch.object(status, "read_pid", return_value=None),
        pytest.raises(typer.Exit) as exc,
    ):
        status.status_command()
    assert exc.value.exit_code == 1
