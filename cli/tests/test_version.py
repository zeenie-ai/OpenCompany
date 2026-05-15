"""Smoke tests for ``cli.commands.version``."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from cli.commands import version


def test_tag_to_version_strips_v_prefix():
    assert version._tag_to_version("v1.2.3") == "1.2.3"
    assert version._tag_to_version("0.0.11") == "0.0.11"


def test_update_package_json_writes_new_version(tmp_path: Path):
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"name": "x", "version": "0.0.1"}, indent=2) + "\n")
    assert version._update_package_json(pkg, "0.0.2") is True
    contents = json.loads(pkg.read_text())
    assert contents["version"] == "0.0.2"


def test_update_package_json_no_op_when_already_correct(tmp_path: Path):
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"name": "x", "version": "1.0.0"}) + "\n")
    assert version._update_package_json(pkg, "1.0.0") is False


def test_update_package_json_preserves_trailing_newline(tmp_path: Path):
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"name": "x", "version": "0.0.1"}) + "\n")
    version._update_package_json(pkg, "0.0.2")
    assert pkg.read_text(encoding="utf-8").endswith("\n")


def test_git_describe_returns_none_without_git():
    with patch.object(version, "capture", return_value=None):
        assert version._git_describe(Path(".")) is None
