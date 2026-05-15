"""Smoke tests for ``cli.commands.start``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from machina import buildenv
from cli.commands import start
from cli.config import Config


def _cfg() -> Config:
    return Config()


def test_venv_python_returns_none_when_missing(tmp_path: Path):
    assert buildenv.venv_python(tmp_path) is None


def test_venv_python_finds_windows_layout(tmp_path: Path):
    win_py = tmp_path / "server" / ".venv" / "Scripts" / "python.exe"
    win_py.parent.mkdir(parents=True)
    win_py.write_text("")
    assert buildenv.venv_python(tmp_path) == win_py


def test_venv_python_finds_posix_layout(tmp_path: Path):
    posix_py = tmp_path / "server" / ".venv" / "bin" / "python"
    posix_py.parent.mkdir(parents=True)
    posix_py.write_text("")
    assert buildenv.venv_python(tmp_path) == posix_py


def test_temporal_running_false_when_cli_missing():
    with patch.object(start.subprocess, "run", side_effect=FileNotFoundError):
        assert start._temporal_running() is False


def test_build_specs_skips_temporal_when_already_running(tmp_path: Path):
    cfg = _cfg()
    specs = start._build_specs(tmp_path, cfg, temporal_running=True)
    assert {s.name for s in specs} == {"client", "server"}


def test_build_specs_includes_temporal_when_not_running(tmp_path: Path):
    cfg = _cfg()
    specs = start._build_specs(tmp_path, cfg, temporal_running=False)
    assert {s.name for s in specs} == {"client", "server", "temporal"}


def test_build_specs_assigns_ready_ports(tmp_path: Path):
    cfg = _cfg()
    specs = start._build_specs(tmp_path, cfg, temporal_running=False)
    by_name = {s.name: s for s in specs}
    assert by_name["client"].ready_port == 3000
    assert by_name["server"].ready_port == 3010
    assert by_name["temporal"].ready_port == 7233
