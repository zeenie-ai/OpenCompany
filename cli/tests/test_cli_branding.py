"""Executable-name contracts for the OpenCompany CLI distribution."""

from __future__ import annotations

import json
import tomllib

from cli.platform_ import project_root


ROOT = project_root()


def test_npm_bins_expose_company_and_deprecated_machina_only():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["name"] == "@zeenie-ai/opencompany"
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

    assert "npm install -g @zeenie-ai/opencompany@${version}" in template
    assert "npm uninstall -g machinaos" in template
    assert "command -v company || command -v machina" in template
    assert "command -v opencompany" not in template
    assert "ExecStart=$OPENCOMPANY_BIN serve" in template


def test_installers_target_scoped_package_without_touching_unscoped_package():
    installers = {
        "install.sh": (ROOT / "install.sh").read_text(encoding="utf-8"),
        "install.ps1": (ROOT / "install.ps1").read_text(encoding="utf-8"),
        "gcp startup": (
            ROOT / "cli" / "terraform" / "gcp" / "startup.sh.tftpl"
        ).read_text(encoding="utf-8"),
    }

    assert "npm install -g '@zeenie-ai/opencompany'" in installers["install.sh"]
    assert 'npm install -g "@zeenie-ai/opencompany"' in installers["install.ps1"]
    assert "npm install -g @zeenie-ai/opencompany@${version}" in installers["gcp startup"]

    for source in installers.values():
        assert "npm install -g opencompany" not in source
        assert "npm uninstall -g opencompany" not in source
        assert "npm uninstall -g machinaos" in source


def test_uninstaller_removes_only_scoped_and_official_legacy_packages():
    uninstaller = (ROOT / "uninstall.sh").read_text(encoding="utf-8")

    assert "remove_global_package '@zeenie-ai/opencompany'" in uninstaller
    assert "remove_global_package 'machinaos'" in uninstaller
    assert "npm uninstall -g opencompany" not in uninstaller


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
