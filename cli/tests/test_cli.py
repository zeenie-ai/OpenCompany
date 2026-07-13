"""Smoke tests for the Typer CLI surface."""

from __future__ import annotations

from typer.testing import CliRunner

from cli.cli import app


runner = CliRunner()


def test_help_lists_stop():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "company" in result.output
    assert "stop" in result.output


def test_stop_help():
    result = runner.invoke(app, ["stop", "--help"])
    assert result.exit_code == 0
    assert "Stop all OpenCompany services" in result.output
