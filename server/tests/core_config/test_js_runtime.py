"""core.js_runtime — the one place the backend resolves bun and extends
the shared packages tree. Locks the contract the JS executor sidecar,
the npm-shipped CLI plugins and the codex provider all rely on: bun is
the only JavaScript runtime the backend ever spawns."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import js_runtime
from tests._source_tree import SERVER_ROOT, server_python_files


def test_override_wins_over_path(monkeypatch, tmp_path):
    fake = tmp_path / ("bun.exe" if js_runtime.sys.platform == "win32" else "bun")
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv(js_runtime.ENV_BUN_BIN, str(fake))
    monkeypatch.setattr(js_runtime.shutil, "which", lambda name: "/elsewhere/bun")
    assert js_runtime.bun_binary() == str(fake)


def test_missing_override_file_falls_back_to_path(monkeypatch, tmp_path):
    monkeypatch.setenv(js_runtime.ENV_BUN_BIN, str(tmp_path / "does-not-exist"))
    monkeypatch.setattr(js_runtime.shutil, "which", lambda name: "/path/bun" if name == "bun" else None)
    assert js_runtime.bun_binary() == "/path/bun"


def test_require_bun_names_the_purpose_and_the_fix(monkeypatch):
    monkeypatch.delenv(js_runtime.ENV_BUN_BIN, raising=False)
    monkeypatch.setattr(js_runtime.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError) as exc:
        js_runtime.require_bun("the JS executor")
    assert "the JS executor" in str(exc.value)
    assert "bun.sh" in str(exc.value)


def test_bin_shim_name_matches_bun_layout(monkeypatch):
    monkeypatch.setattr(js_runtime.sys, "platform", "win32")
    assert js_runtime.bin_shim_name("cf") == "cf.exe"
    monkeypatch.setattr(js_runtime.sys, "platform", "linux")
    assert js_runtime.bin_shim_name("cf") == "cf"


def test_shared_tree_bin_lives_under_packages_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(js_runtime, "packages_dir", lambda: tmp_path)
    expected = tmp_path / "node_modules" / ".bin" / js_runtime.bin_shim_name("vercel")
    assert js_runtime.shared_tree_bin("vercel") == expected


def test_ensure_shared_tree_writes_a_private_manifest(tmp_path):
    root = tmp_path / "packages"
    assert js_runtime.ensure_shared_tree(root) == root
    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    assert manifest["private"] is True
    # idempotent: an existing manifest (with whatever bun added) survives
    (root / "package.json").write_text('{"name":"x","dependencies":{"cf":"0.2.0"}}', encoding="utf-8")
    js_runtime.ensure_shared_tree(root)
    assert "cf" in json.loads((root / "package.json").read_text(encoding="utf-8"))["dependencies"]


def test_installed_version_reads_the_package_manifest(tmp_path):
    assert js_runtime.installed_version("cf", root=tmp_path) is None
    pkg = tmp_path / "node_modules" / "cf"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{"name":"cf","version":"1.0.0-beta.12"}', encoding="utf-8")
    assert js_runtime.installed_version("cf", root=tmp_path) == "1.0.0-beta.12"
    # scoped names resolve through their scope folder
    scoped = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code"
    scoped.mkdir(parents=True)
    (scoped / "package.json").write_text('{"version":"2.1.0"}', encoding="utf-8")
    assert js_runtime.installed_version("@anthropic-ai/claude-code", root=tmp_path) == "2.1.0"
    # an unreadable or version-less manifest counts as "not installed"
    (pkg / "package.json").write_text("{oops", encoding="utf-8")
    assert js_runtime.installed_version("cf", root=tmp_path) is None
    (pkg / "package.json").write_text('{"name":"cf"}', encoding="utf-8")
    assert js_runtime.installed_version("cf", root=tmp_path) is None


def test_add_package_builds_a_bun_add_argv(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    def fake_run(argv, capture_output, text, env):
        seen["argv"] = argv
        seen["env"] = env
        seen["cwd_manifest"] = (tmp_path / "package.json").exists()

        class Done:
            returncode = 0
            stderr = ""
            stdout = ""

        return Done()

    monkeypatch.setenv(js_runtime.ENV_BUN_BIN, "")
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "test-bootstrap-secret")
    monkeypatch.setenv("OP_CONNECT_TOKEN", "test-connect-secret")
    monkeypatch.setenv("OPENCOMPANY_TEST_CHILD_ENV", "keep-me")
    monkeypatch.setattr(js_runtime.shutil, "which", lambda name: "/opt/bun")
    monkeypatch.setattr(js_runtime.subprocess, "run", fake_run)

    js_runtime.add_package("edgymeow@0.0.20", trust=True, root=tmp_path)
    argv = seen["argv"]
    assert argv[:3] == ["/opt/bun", "add", "--cwd"]
    assert Path(argv[3]) == tmp_path
    assert "--trust" in argv
    assert argv[-1] == "edgymeow@0.0.20"
    assert seen["cwd_manifest"] is True, "the tree manifest must exist before bun add runs"
    assert "OP_SERVICE_ACCOUNT_TOKEN" not in seen["env"]
    assert "OP_CONNECT_TOKEN" not in seen["env"]
    assert seen["env"]["OPENCOMPANY_TEST_CHILD_ENV"] == "keep-me"

    js_runtime.add_package("cf@0.2.0", root=tmp_path)
    assert "--trust" not in seen["argv"]


def test_backend_never_spawns_node_or_npm():
    """The whole point of the module: no other runtime resolution exists.
    A regression that reintroduces ``shutil.which("node")`` /
    ``which("npm")`` / an ``npx`` spawn anywhere under the backend tree
    would silently reintroduce a Node dependency."""
    offenders: list[str] = []
    for path in server_python_files("tests", "experiments", "scripts"):
        rel = path.relative_to(SERVER_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in ('which("node")', "which('node')", 'which("npm")', "which('npm')", 'which("npx")', "which('npx')"):
            if needle in text:
                offenders.append(f"{rel}: {needle}")
    assert offenders == []
