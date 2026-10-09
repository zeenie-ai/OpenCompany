"""``validate_build``: what each verb needs from ``company build``."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from cli.buildenv import validate_build


def _layout(root: Path, *, venv: bool, node_modules: bool, client: bool) -> Path:
    if venv:
        (root / "server" / ".venv").mkdir(parents=True)
    if node_modules:
        (root / "node_modules").mkdir()
    if client:
        (root / "client" / "dist").mkdir(parents=True)
        (root / "client" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _default_venv_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)


def test_registry_install_starts_without_node_modules(tmp_path: Path) -> None:
    """``bun add -g`` leaves no ``node_modules`` next to the package; ``start`` /
    ``serve`` need only the server venv and the built client (errors.md #25)."""
    validate_build(_layout(tmp_path, venv=True, node_modules=False, client=True))


def test_dev_needs_node_modules(tmp_path: Path) -> None:
    root = _layout(tmp_path, venv=True, node_modules=False, client=True)
    with pytest.raises(typer.Exit):
        validate_build(root, dev=True)


def test_dev_runs_without_the_built_client(tmp_path: Path) -> None:
    validate_build(_layout(tmp_path, venv=True, node_modules=True, client=False), dev=True)


def test_start_needs_the_built_client(tmp_path: Path) -> None:
    root = _layout(tmp_path, venv=True, node_modules=True, client=False)
    with pytest.raises(typer.Exit):
        validate_build(root)


@pytest.mark.parametrize("dev", [False, True])
def test_every_verb_needs_the_server_venv(tmp_path: Path, dev: bool) -> None:
    root = _layout(tmp_path, venv=False, node_modules=True, client=True)
    with pytest.raises(typer.Exit):
        validate_build(root, dev=dev)
