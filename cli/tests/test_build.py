"""Smoke tests for ``cli.commands.build`` + ``cli.run`` shared helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import typer

from cli import run as run_module
from cli.commands import build


# --- shared helpers in cli.run -----------------------------------------


def test_capture_returns_none_when_command_missing():
    with patch.object(run_module.subprocess, "run", side_effect=FileNotFoundError):
        assert run_module.capture(["does-not-exist"]) is None


def test_capture_returns_stdout_when_present():
    fake = MagicMock(stdout="v1.2.3\n", stderr="")
    with patch.object(run_module.subprocess, "run", return_value=fake):
        assert run_module.capture(["tool", "--version"]) == "v1.2.3"


def test_run_raises_typer_exit_on_nonzero_when_check():
    fake = MagicMock(returncode=1)
    with (
        patch.object(run_module.subprocess, "run", return_value=fake),
        pytest.raises(typer.Exit),
    ):
        run_module.run(["false"])


def test_run_returns_code_when_check_disabled():
    fake = MagicMock(returncode=42)
    with patch.object(run_module.subprocess, "run", return_value=fake):
        assert run_module.run(["x"], check=False) == 42


# --- build-specific helpers -------------------------------------------------


def test_check_python_accepts_3_12_plus():
    with patch.object(build, "capture", return_value="Python 3.12.5"):
        assert build._check_python("python") is True


def test_check_python_rejects_3_11():
    with patch.object(build, "capture", return_value="Python 3.11.9"):
        assert build._check_python("python") is False


def test_check_python_rejects_unparseable():
    with patch.object(build, "capture", return_value="garbage output"):
        assert build._check_python("python") is False
