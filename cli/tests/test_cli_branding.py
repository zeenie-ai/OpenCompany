"""Executable-name contracts for the OpenCompany CLI distribution."""

from __future__ import annotations

import json
import tomllib

from cli.platform_ import project_root


ROOT = project_root()


def test_npm_bins_expose_company_and_deprecated_machina_only():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["name"] == "opencompany"
    assert package["bin"] == {
        "company": "./bin/cli.js",
        "machina": "./bin/machina.js",
    }
    assert "opencompany" not in package["bin"]


def test_python_scripts_expose_company_and_deprecated_machina_only():
    with (ROOT / "pyproject.toml").open("rb") as source:
        project = tomllib.load(source)["project"]

    assert project["name"] == "opencompany-cli"
    assert project["scripts"] == {
        "company": "cli.cli:app",
        "machina": "cli.cli:app",
    }
    assert "opencompany" not in project["scripts"]


def test_cloud_service_resolves_company_then_legacy_machina():
    template = (ROOT / "cli" / "terraform" / "gcp" / "startup.sh.tftpl").read_text(
        encoding="utf-8"
    )

    assert "npm install -g opencompany@${version}" in template
    assert "command -v company || command -v machina" in template
    assert "command -v opencompany" not in template
    assert "ExecStart=$OPENCOMPANY_BIN serve" in template


def test_node_shims_print_canonical_name_and_deprecation_warning():
    canonical = (ROOT / "bin" / "cli.js").read_text(encoding="utf-8")
    legacy = (ROOT / "bin" / "machina.js").read_text(encoding="utf-8")

    assert "Usage: company <command> [flags]" in canonical
    assert "console.log(`company v${PKG.version}`)" in canonical
    assert "`machina` is deprecated; use `company` instead" in canonical
    assert "OPENCOMPANY_LEGACY_ALIAS = 'machina'" in legacy
    assert "await import('./cli.js')" in legacy


def test_postinstall_makes_both_node_entrypoints_executable():
    postinstall = (ROOT / "scripts" / "postinstall.js").read_text(encoding="utf-8")

    assert "resolve(ROOT, 'bin/cli.js')" in postinstall
    assert "resolve(ROOT, 'bin/machina.js')" in postinstall
