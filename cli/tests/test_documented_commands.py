"""Check repository Bun commands without running installations or services."""

from __future__ import annotations

import json
from pathlib import Path
import re

from markdown_it import MarkdownIt
import pytest

from cli.platform_ import project_root

ROOT = project_root()
GUIDES = (
    "README.md", "CONTRIBUTING.md", "docs-internal/SETUP.md",
    "docs-internal/SCRIPTS.md", "docs-internal/frontend_architecture.md", "desktop/README.md",
)
_RUN = re.compile(r"\bbun\s+(?:--cwd(?:=|\s+)([^\s]+)\s+)?run\s+(?:--filter(?:=|\s+)([^\s]+)\s+)?([A-Za-z0-9_./:-]+)")


def _command_errors(root: Path, doc: Path) -> list[str]:
    packages = {}
    for relative in (".", "client", "server/nodejs", "desktop", "server/pi"):
        manifest = root / relative / "package.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            packages[data["name"]] = (manifest.parent, data)
    default = next((parent for parent in (doc.parent, *doc.parents)
                    if parent.is_relative_to(root) and (parent / "package.json").is_file()), root)
    errors = []
    for token in MarkdownIt("commonmark").parse(doc.read_text(encoding="utf-8")):
        if token.type != "fence" or token.info.strip() not in {"", "bash", "sh", "shell", "powershell"}:
            continue
        cwd = default
        for line in token.content.splitlines():
            if line.lstrip().startswith("#"):
                if re.search(r"from (?:the )?repo root", line, re.I):
                    cwd = root
                continue
            change = re.match(r"\s*cd\s+([^\s;&]+)", line)
            if change:
                destination = change.group(1).strip("\"'")
                cwd = root if destination.lower() == "opencompany" else (cwd / destination).resolve()
            for match in _RUN.finditer(line):
                explicit_cwd, package, script = match.groups()
                directory = (cwd / explicit_cwd.strip("\"'")).resolve() if explicit_cwd else cwd
                if package:
                    if package not in packages:
                        errors.append(f"unknown workspace {package!r}: {line.strip()}")
                        continue
                    directory, manifest = packages[package]
                else:
                    path = directory / "package.json"
                    if not path.is_file():
                        errors.append(f"no package.json in {directory}: {line.strip()}")
                        continue
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                # Bun also runs real .ts/.js files directly, without a script key.
                if script not in manifest.get("scripts", {}) and not (directory / script).is_file():
                    errors.append(f"unknown script {script!r} in {directory.relative_to(root)}: {line.strip()}")
    return errors


@pytest.mark.parametrize("relative", GUIDES)
def test_documented_bun_commands_exist_in_the_correct_package(relative):
    errors = _command_errors(ROOT, ROOT / relative)
    assert not errors, f"{relative}:\n" + "\n".join(errors)


def test_client_command_is_checked_against_client_not_root(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "root", "scripts": {"dev": "supervisor"}}), encoding="utf-8")
    (tmp_path / "client").mkdir()
    (tmp_path / "client/package.json").write_text(json.dumps({"name": "client", "scripts": {"start": "vite"}}), encoding="utf-8")
    doc = tmp_path / "guide.md"
    doc.write_text("```bash\ncd client\nbun run dev\n```", encoding="utf-8")
    assert "unknown script 'dev' in client" in _command_errors(tmp_path, doc)[0]
    doc.write_text("```bash\ncd client\nbun run start\n```", encoding="utf-8")
    assert _command_errors(tmp_path, doc) == []
