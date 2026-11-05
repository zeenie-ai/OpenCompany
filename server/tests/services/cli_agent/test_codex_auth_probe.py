"""Authentication refresh never prepares packages or reads personal credentials."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core import js_runtime
from services.cli_agent import _codex_auth as auth
from services.cli_agent import config


@pytest.fixture
def managed_cli(monkeypatch, tmp_path):
    binary = tmp_path / "codex.exe"
    cfg = SimpleNamespace(binary_name="codex", package_name="@openai/codex", package_version="pinned-version")
    monkeypatch.setattr(config, "get_provider_config", Mock(return_value=cfg))
    monkeypatch.setattr(js_runtime, "shared_tree_bin", Mock(return_value=binary))
    monkeypatch.setattr(js_runtime, "installed_version", Mock(return_value=cfg.package_version))
    monkeypatch.setattr(js_runtime, "add_package", Mock(side_effect=AssertionError("Probe attempted a package install")))
    monkeypatch.setattr(auth, "codex_binary_path", Mock(side_effect=AssertionError("Probe attempted CLI preparation")))
    monkeypatch.setattr(auth, "OPENCOMPANY_CODEX_DIR", tmp_path / "managed-auth")
    return binary


def test_probe_accepts_only_existing_pinned_managed_launcher(managed_cli):
    assert auth.codex_installed_binary_path() is None
    managed_cli.touch()
    assert auth.codex_installed_binary_path() == str(managed_cli)
    js_runtime.shared_tree_bin.assert_called_with("codex")
    js_runtime.installed_version.assert_called_with("@openai/codex")
    js_runtime.add_package.assert_not_called()
    auth.codex_binary_path.assert_not_called()


@pytest.mark.parametrize("version", [None, "outdated-version"])
def test_probe_rejects_outdated_or_unversioned_managed_launcher(managed_cli, version):
    managed_cli.touch()
    js_runtime.installed_version.return_value = version
    assert auth.codex_installed_binary_path() is None
    js_runtime.add_package.assert_not_called()


@pytest.mark.parametrize("cfg", [None, SimpleNamespace(package_version=None)])
def test_probe_rejects_unconfigured_package_without_searching_system_path(managed_cli, cfg):
    config.get_provider_config.return_value = cfg
    assert auth.codex_installed_binary_path() is None
    js_runtime.shared_tree_bin.assert_not_called()
    js_runtime.add_package.assert_not_called()


@pytest.mark.parametrize("outdated", [False, True])
@pytest.mark.parametrize("argv", [["login", "status"], ["logout"]])
async def test_unprepared_probe_never_waits_for_install_lock(monkeypatch, managed_cli, outdated, argv):
    if outdated:
        managed_cli.touch()
        js_runtime.installed_version.return_value = "outdated-version"
    install_lock = threading.Lock()
    monkeypatch.setattr(auth, "_install_lock", install_lock)
    native = AsyncMock()
    monkeypatch.setattr(auth, "run_cli_command", native)
    with install_lock:
        result = await asyncio.wait_for(auth._run_auth(argv), timeout=1)
    assert result["success"] is False
    assert result["cli_missing"] is True
    assert "Click Login" in result["error"]
    native.assert_not_called()
    auth.codex_binary_path.assert_not_called()
    js_runtime.add_package.assert_not_called()


async def test_missing_cli_is_unknown_auth_with_actionable_error(managed_cli):
    result = await auth.codex_auth_status_info()
    assert result["logged_in"] is False
    assert "Click Login" in result["error"]
    assert not auth.OPENCOMPANY_CODEX_DIR.exists()


async def test_status_and_logout_use_same_managed_environment(monkeypatch, managed_cli):
    managed_cli.touch()
    monkeypatch.setenv("CODEX_HOME", "personal-codex-home")
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "private-service-account-token")
    native = AsyncMock(return_value={"success": True, "stderr": "Logged in using ChatGPT"})
    monkeypatch.setattr(auth, "run_cli_command", native)

    assert await auth.codex_auth_status_info() == {"logged_in": True}
    assert await auth.codex_auth_logout() is True

    assert [call.kwargs["argv"] for call in native.await_args_list] == [["login", "status"], ["logout"]]
    environments = [call.kwargs["env"] for call in native.await_args_list]
    assert environments[0] == environments[1]
    assert environments[0]["CODEX_HOME"] == str(auth.OPENCOMPANY_CODEX_DIR)
    assert "OP_SERVICE_ACCOUNT_TOKEN" not in environments[0]
    for call in native.await_args_list:
        assert call.kwargs["binary"] == str(managed_cli)
        assert call.kwargs["timeout"] == auth.ONESHOT_TIMEOUT_SECONDS
    js_runtime.add_package.assert_not_called()
    auth.codex_binary_path.assert_not_called()


@pytest.mark.parametrize(
    "envelope,expected",
    [
        ({"success": True, "stderr": "Logged in using ChatGPT"}, {"logged_in": True}),
        ({"success": True, "stderr": "Logged in using an API key: private-secret"}, {"logged_in": True}),
        ({"success": False, "stderr": "Not logged in"}, {"logged_in": False}),
        ({"success": False, "stdout": "Not logged in"}, {"logged_in": False}),
    ],
)
async def test_native_status_preserves_exit_status_semantics(monkeypatch, managed_cli, envelope, expected):
    managed_cli.touch()
    monkeypatch.setattr(auth, "run_cli_command", AsyncMock(return_value=envelope))
    assert await auth.codex_auth_status_info() == expected


async def test_failed_status_never_exposes_output_or_guesses_login(monkeypatch, managed_cli):
    managed_cli.touch()
    native = AsyncMock(
        return_value={
            "success": False,
            "stdout": "Logged in using an API key: private-secret",
            "stderr": "https://auth.openai.com/oauth/authorize?secret=private-secret",
            "error": "private-secret",
        }
    )
    monkeypatch.setattr(auth, "run_cli_command", native)
    result = await auth.codex_auth_status_info()
    assert result["logged_in"] is False
    assert result["error"]
    assert "private-secret" not in str(result)
    assert "https://" not in str(result)


async def test_probe_start_failure_is_sanitized(monkeypatch, managed_cli):
    managed_cli.touch()
    monkeypatch.setattr(auth, "run_cli_command", AsyncMock(side_effect=OSError("private-secret")))
    result = await auth._run_auth(["login", "status"])
    assert result == {"success": False, "error": "The managed Codex CLI could not be started."}
