"""Unit tests for ``cli.ports``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import psutil

from machina import ports


def test_kill_pid_no_such_process_returns_false():
    with patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(123)):
        assert ports.kill_pid(123) is False


def test_kill_pid_terminates_then_waits():
    proc = MagicMock()
    with patch.object(psutil, "Process", return_value=proc):
        assert ports.kill_pid(123) is True
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once()


def test_kill_pid_force_kills_on_timeout():
    proc = MagicMock()
    proc.wait.side_effect = psutil.TimeoutExpired(seconds=1)
    with patch.object(psutil, "Process", return_value=proc):
        assert ports.kill_pid(123) is True
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


def test_kill_port_excludes_self():
    """The function must never kill its own PID."""
    import os
    my_pid = os.getpid()
    with patch.object(ports, "find_pids_by_port", side_effect=[{my_pid}, set()]):
        result = ports.kill_port(9999)
    assert result.killed_pids == []
    assert result.port_free is True
