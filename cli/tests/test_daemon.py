"""Smoke tests for ``cli.commands.daemon``."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil
import pytest

from cli.commands import daemon


# venv-python discovery is now centralised in cli.buildenv; covered
# by tests/test_start.py (test_venv_python_finds_*).


def test_detached_kwargs_windows_uses_creationflags():
    # subprocess.DETACHED_PROCESS and CREATE_NEW_PROCESS_GROUP are only
    # defined on Windows. Patching them in with `create=True` lets the
    # test cover the Windows branch of `_detached_kwargs` on every CI
    # runner (POSIX or Windows) without resorting to a skipif that would
    # leave the branch entirely uncovered on the Linux-only CI matrix.
    # Real values from windows.h: DETACHED_PROCESS=0x08, GROUP=0x200.
    fake_detached = 0x00000008
    fake_new_group = 0x00000200
    with patch.object(daemon, "IS_WINDOWS", True), \
         patch.object(subprocess, "DETACHED_PROCESS", fake_detached, create=True), \
         patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", fake_new_group, create=True):
        kw = daemon._detached_kwargs()
    assert "creationflags" in kw
    assert kw["creationflags"] == fake_detached | fake_new_group
    assert "start_new_session" not in kw


def test_detached_kwargs_posix_uses_setsid():
    with patch.object(daemon, "IS_WINDOWS", False):
        kw = daemon._detached_kwargs()
    assert kw == {"start_new_session": True}


def test_read_pid_returns_none_when_file_missing(tmp_path: Path):
    with patch.object(daemon, "_PID_FILE", tmp_path / "missing.pid"):
        assert daemon._read_pid() is None


def test_read_pid_returns_none_for_corrupt_pid_file(tmp_path: Path):
    pid_file = tmp_path / "cli.pid"
    pid_file.write_text("not-a-number")
    with patch.object(daemon, "_PID_FILE", pid_file):
        assert daemon._read_pid() is None


def test_read_pid_returns_none_when_process_no_longer_exists(tmp_path: Path):
    pid_file = tmp_path / "cli.pid"
    pid_file.write_text("999999")
    with patch.object(daemon, "_PID_FILE", pid_file), \
         patch.object(daemon.psutil, "pid_exists", return_value=False):
        assert daemon._read_pid() is None


def test_read_pid_returns_pid_when_alive(tmp_path: Path):
    pid_file = tmp_path / "cli.pid"
    pid_file.write_text("1234\n")
    with patch.object(daemon, "_PID_FILE", pid_file), \
         patch.object(daemon.psutil, "pid_exists", return_value=True):
        assert daemon._read_pid() == 1234


def test_kill_tree_terminates_children_and_parent():
    parent = MagicMock()
    child1 = MagicMock()
    child2 = MagicMock()
    parent.children.return_value = [child1, child2]
    with patch.object(daemon.psutil, "Process", return_value=parent):
        daemon._kill_tree(123)
    child1.kill.assert_called_once()
    child2.kill.assert_called_once()
    parent.terminate.assert_called_once()
    parent.wait.assert_called_once_with(timeout=5)


def test_kill_tree_force_kills_on_timeout():
    parent = MagicMock()
    parent.children.return_value = []
    parent.wait.side_effect = psutil.TimeoutExpired(seconds=5)
    with patch.object(daemon.psutil, "Process", return_value=parent):
        daemon._kill_tree(123)
    parent.terminate.assert_called_once()
    parent.kill.assert_called_once()


def test_kill_tree_no_such_process_is_noop():
    with patch.object(daemon.psutil, "Process", side_effect=psutil.NoSuchProcess(123)):
        daemon._kill_tree(123)


def test_stop_command_clears_pid_file_when_not_running(tmp_path: Path):
    pid_file = tmp_path / "cli.pid"
    pid_file.write_text("999999")
    with patch.object(daemon, "_PID_FILE", pid_file), \
         patch.object(daemon, "_read_pid", return_value=None):
        daemon.stop_command()
    assert not pid_file.exists()


def test_status_command_exits_1_when_not_running():
    import typer
    with patch.object(daemon, "_read_pid", return_value=None), \
         pytest.raises(typer.Exit) as exc:
        daemon.status_command()
    assert exc.value.exit_code == 1
