"""Managed Codex login lifecycle; no real credentials or subprocesses."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.cli_agent import _handlers as handlers
from services.cli_agent import _codex_auth as auth


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(handlers, "_login_task", None)
    monkeypatch.setattr(handlers, "_login_state", {})
    monkeypatch.setattr(handlers, "_login_native_started", False)
    monkeypatch.setattr(handlers, "_auth_revision", 0)
    monkeypatch.setattr(handlers, "_login_lock", asyncio.Lock())
    monkeypatch.setattr(handlers, "codex_auth_status_info", AsyncMock(return_value={"logged_in": False}))
    monkeypatch.setattr(handlers, "codex_binary_path", Mock(return_value="managed-codex"))
    monkeypatch.setattr(handlers, "codex_auth_logout", AsyncMock(return_value=True))
    monkeypatch.setattr(handlers, "mark_logged_in", AsyncMock())
    monkeypatch.setattr(handlers, "mark_logged_out", AsyncMock())
    monkeypatch.setattr(handlers, "broadcast_credential_event", AsyncMock())
    monkeypatch.setattr(auth, "OPENCOMPANY_CODEX_DIR", tmp_path / "codex")


async def test_existing_auth_reconciles_marker_without_login(monkeypatch):
    monkeypatch.setattr(handlers, "codex_auth_status_info", AsyncMock(return_value={"logged_in": True}))
    login = AsyncMock()
    monkeypatch.setattr(handlers, "run_codex_login", login)
    result = await handlers.handle_codex_cli_login({}, None)
    assert result["success"] and result["connected"] and result["already_logged_in"]
    login.assert_not_called()
    handlers.mark_logged_in.assert_awaited_once_with("codex_cli")


async def test_single_flight_returns_prompt_and_verifies_before_connecting(monkeypatch):
    release = asyncio.Event()
    calls = 0

    async def login(*, on_prompt, device_auth):
        nonlocal calls
        calls += 1
        on_prompt({"url": "https://auth.openai.com/oauth/authorize?state=short-lived"})
        await release.wait()
        return {"success": True}

    monkeypatch.setattr(handlers, "run_codex_login", login)
    monkeypatch.setattr(handlers, "codex_auth_status_info", AsyncMock(side_effect=[{"logged_in": False}, {"logged_in": True}]))
    first = await handlers.handle_codex_cli_login({}, None)
    second = await handlers.handle_codex_cli_login({}, None)
    assert first["pending"] and second["pending"] and first["url"].startswith("https://auth.openai.com/")
    assert calls == 1
    handlers.mark_logged_in.assert_not_awaited()
    release.set()
    await handlers._login_task
    assert handlers._login_state == {"connected": True, "pending": False}
    handlers.mark_logged_in.assert_awaited_once_with("codex_cli")


async def test_device_code_returns_url_and_code(monkeypatch):
    release = asyncio.Event()

    async def login(*, on_prompt, device_auth):
        assert device_auth
        on_prompt({"url": "https://auth.openai.com/codex/device", "verification_code": "ABCD-EFGHJ"})
        await release.wait()
        return {"success": False, "error": "Sign-in declined."}

    monkeypatch.setattr(handlers, "run_codex_login", login)
    result = await handlers.handle_codex_cli_device_login({}, None)
    assert result["verification_code"] == "ABCD-EFGHJ"
    release.set()
    await handlers._login_task


@pytest.mark.parametrize("completed,logged_in", [(False, False), (True, False)])
async def test_failure_and_unverified_exit_never_connect(monkeypatch, completed, logged_in):
    monkeypatch.setattr(handlers, "run_codex_login", AsyncMock(return_value={"success": completed}))
    result = await handlers.handle_codex_cli_login({}, None)
    await handlers._login_task
    result = await handlers.handle_codex_cli_status({}, None)
    assert not result["success"] and not result["pending"] and result["error"]
    handlers.mark_logged_in.assert_not_awaited()


async def test_logout_cancels_login_before_clearing_auth(monkeypatch):
    cancelled = asyncio.Event()

    async def login(*, on_prompt, device_auth):
        on_prompt({"url": "https://auth.openai.com/oauth/authorize?state=secret"})
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(handlers, "run_codex_login", login)
    await handlers.handle_codex_cli_login({}, None)
    result = await handlers.handle_codex_cli_logout({}, None)
    assert result["success"] and cancelled.is_set() and not handlers._login_in_progress()
    handlers.codex_auth_logout.assert_awaited_once()
    handlers.mark_logged_out.assert_awaited_once_with("codex_cli")
    handlers.mark_logged_in.assert_not_awaited()


async def test_failed_logout_does_not_remove_marker(monkeypatch):
    monkeypatch.setattr(handlers, "codex_auth_logout", AsyncMock(return_value=False))
    result = await handlers.handle_codex_cli_logout({}, None)
    assert not result["success"]
    handlers.mark_logged_out.assert_not_awaited()


async def test_status_failure_does_not_attempt_login(monkeypatch):
    monkeypatch.setattr(handlers, "codex_auth_status_info", AsyncMock(return_value={"logged_in": False, "error": "CLI unavailable"}))
    assert not (await handlers.handle_codex_cli_login({}, None))["success"]
    assert handlers._login_task.done()


@pytest.mark.parametrize(
    "envelope,expected",
    [
        ({"success": True, "stderr": "Logged in using ChatGPT"}, {"logged_in": True}),
        ({"success": False, "stderr": "Not logged in"}, {"logged_in": False}),
    ],
)
async def test_status_uses_exit_code_and_stderr(monkeypatch, envelope, expected):
    run = AsyncMock(return_value=envelope)
    monkeypatch.setattr(auth, "_run_auth", run)
    assert await auth.codex_auth_status_info() == expected
    run.assert_awaited_once_with(["login", "status"])


async def test_status_error_does_not_expose_raw_cli_output(monkeypatch):
    monkeypatch.setattr(
        auth,
        "_run_auth",
        AsyncMock(return_value={"success": False, "stderr": "secret-token https://auth.openai.com/oauth/authorize?code=secret"}),
    )
    result = await auth.codex_auth_status_info()
    assert result.get("error") and "secret" not in result["error"]


def test_environment_uses_managed_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", "personal-home")
    assert auth._codex_env()["CODEX_HOME"] == str(tmp_path / "codex")
    assert (tmp_path / "codex").is_dir()


def test_managed_binary_reinstalls_mismatched_version(monkeypatch, tmp_path):
    import core.js_runtime as js

    binary = tmp_path / "codex.exe"
    binary.touch()
    monkeypatch.setattr(js, "shared_tree_bin", lambda _: binary)
    monkeypatch.setattr(js, "installed_version", lambda _: "old")
    install = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(js, "add_package", install)
    assert auth.codex_binary_path() == str(binary)
    install.assert_called_once_with("@openai/codex@0.144.2")


def test_prompt_parser_only_accepts_official_urls():
    assert auth._login_instructions("https://example.com/oauth/authorize?state=secret", device_auth=False) == {}
    prompt = auth._login_instructions("https://auth.openai.com/codex/device\nABCD-EFGHJ", device_auth=True)
    assert prompt == {"url": "https://auth.openai.com/codex/device", "verification_code": "ABCD-EFGHJ"}


async def test_login_timeout_kills_process_and_returns_safe_error(monkeypatch):
    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    proc = SimpleNamespace(stdout=stdout, stderr=stderr, pid=12345, returncode=None)

    async def wait():
        return proc.returncode

    proc.wait = wait
    monkeypatch.setattr(auth, "codex_binary_path", lambda: "managed-codex")
    monkeypatch.setattr(auth.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    killed = Mock(side_effect=lambda _: setattr(proc, "returncode", -9))
    monkeypatch.setattr(auth, "kill_tree", killed)
    monkeypatch.setattr(auth, "LOGIN_TIMEOUT_SECONDS", 0.001)
    result = await auth.run_codex_login(on_prompt=lambda _: None)
    assert not result["success"] and "timed out" in result["error"]
    killed.assert_called_once_with(12345)


async def test_cold_status_does_not_block_login_polling_or_cancel(monkeypatch):
    async def slow_status():
        await asyncio.Event().wait()

    monkeypatch.setattr(handlers, "codex_auth_status_info", slow_status)
    monkeypatch.setattr(handlers, "LOGIN_RESPONSE_WAIT_SECONDS", 0.001)
    result = await handlers.handle_codex_cli_login({}, None)
    assert result["success"] and result["pending"]
    assert (await handlers.handle_codex_cli_status({}, None))["pending"]
    assert (await handlers.handle_codex_cli_logout({}, None))["success"]
    assert not handlers._login_in_progress()
    handlers.codex_auth_logout.assert_not_awaited()


async def test_explicit_refresh_recovers_from_terminal_login_error(monkeypatch):
    handlers._login_state.update(error="Previous sign-in failed.", pending=False)
    monkeypatch.setattr(handlers, "codex_auth_status_info", AsyncMock(return_value={"logged_in": True}))
    assert not (await handlers.handle_codex_cli_status({}, None))["success"]
    handlers.codex_auth_status_info.assert_not_awaited()
    refreshed = await handlers.handle_codex_cli_status({"refresh": True}, None)
    assert refreshed["success"] and refreshed["connected"]
    assert "error" not in refreshed


async def test_signed_out_status_invalidates_marker_after_backend_restart():
    assert handlers._login_state == {}
    result = await handlers.handle_codex_cli_status({"refresh": True}, None)
    assert result["success"] and not result["connected"]
    handlers.mark_logged_out.assert_awaited_once_with("codex_cli")
    handlers.broadcast_credential_event.assert_awaited_once_with("credential.oauth.disconnected", provider="codex_cli")


async def test_slow_status_does_not_block_logout_or_restore_stale_connection(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_status():
        started.set()
        await release.wait()
        return {"logged_in": True}

    monkeypatch.setattr(handlers, "codex_auth_status_info", slow_status)
    probe = asyncio.create_task(handlers.handle_codex_cli_status({"refresh": True}, None))
    try:
        await started.wait()
        logged_out = await asyncio.wait_for(handlers.handle_codex_cli_logout({}, None), timeout=1)
        assert logged_out["success"]
        release.set()
        result = await probe
        assert result["success"] and not result["connected"]
        handlers.mark_logged_in.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(probe, return_exceptions=True)


async def test_old_signed_out_probe_cannot_clear_new_login(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def status():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            return {"logged_in": False}
        return {"logged_in": True}

    monkeypatch.setattr(handlers, "codex_auth_status_info", status)
    probe = asyncio.create_task(handlers.handle_codex_cli_status({"refresh": True}, None))
    try:
        await started.wait()
        logged_in = await asyncio.wait_for(handlers.handle_codex_cli_login({}, None), timeout=1)
        assert logged_in["success"] and logged_in["connected"]
        release.set()
        result = await probe
        assert result["connected"]
        handlers.mark_logged_out.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(probe, return_exceptions=True)


@pytest.mark.parametrize("old_connected,new_connected", [(False, True), (True, False)])
async def test_older_status_cannot_overwrite_newer_probe(monkeypatch, old_connected, new_connected):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def status():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            return {"logged_in": old_connected}
        return {"logged_in": new_connected}

    monkeypatch.setattr(handlers, "codex_auth_status_info", status)
    older = asyncio.create_task(handlers.handle_codex_cli_status({"refresh": True}, None))
    try:
        await started.wait()
        newer = await handlers.handle_codex_cli_status({"refresh": True}, None)
        assert newer["connected"] is new_connected
        release.set()
        assert (await older)["connected"] is new_connected
        if new_connected:
            handlers.mark_logged_in.assert_awaited_once_with("codex_cli")
            handlers.mark_logged_out.assert_not_awaited()
        else:
            handlers.mark_logged_out.assert_awaited_once_with("codex_cli")
            handlers.mark_logged_in.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(older, return_exceptions=True)


async def test_failed_status_probe_keeps_last_verified_marker(monkeypatch):
    monkeypatch.setattr(handlers, "codex_auth_status_info", AsyncMock(return_value={"logged_in": False, "error": "CLI unavailable"}))
    result = await handlers.handle_codex_cli_status({"refresh": True}, None)
    assert not result["success"]
    handlers.mark_logged_out.assert_not_awaited()
    handlers.broadcast_credential_event.assert_not_awaited()


async def test_background_install_failure_is_actionable_and_does_not_connect(monkeypatch):
    monkeypatch.setattr(handlers, "codex_binary_path", Mock(side_effect=RuntimeError("private install output")))
    result = await handlers.handle_codex_cli_login({}, None)
    assert not result["success"]
    assert "managed Codex CLI" in result["error"]
    assert "private" not in result["error"]
    handlers.mark_logged_in.assert_not_awaited()


async def test_shutdown_cancels_owned_login(monkeypatch):
    async def slow_status():
        await asyncio.Event().wait()

    monkeypatch.setattr(handlers, "codex_auth_status_info", slow_status)
    monkeypatch.setattr(handlers, "LOGIN_RESPONSE_WAIT_SECONDS", 0.001)
    await handlers.handle_codex_cli_login({}, None)
    task = handlers._login_task
    await handlers.shutdown_codex_login()
    assert task.cancelled()
    assert handlers._login_task is None
    handlers.codex_auth_logout.assert_not_awaited()


async def test_streamed_login_never_publishes_a_partial_url(monkeypatch):
    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    stderr.feed_eof()
    proc = SimpleNamespace(stdout=stdout, stderr=stderr, pid=12345, returncode=None)

    async def wait():
        proc.returncode = 0
        return 0

    proc.wait = wait
    monkeypatch.setattr(auth, "codex_binary_path", lambda: "managed-codex")
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(auth.asyncio, "create_subprocess_exec", spawn)
    prompt = Mock()
    task = asyncio.create_task(auth.run_codex_login(on_prompt=prompt))
    try:
        async with asyncio.timeout(2):
            while not spawn.called:
                await asyncio.sleep(0)
        stdout.feed_data(b"https://auth.openai.com/oauth/authorize?state=")
        await asyncio.sleep(0)
        prompt.assert_not_called()
        stdout.feed_data(b"complete-state\n")
        stdout.feed_eof()
        assert (await task)["success"]
        prompt.assert_called_once_with({"url": "https://auth.openai.com/oauth/authorize?state=complete-state"})
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_cancelled_native_status_reaps_cli_process(monkeypatch):
    from services.events import cli

    communicating = asyncio.Event()

    async def communicate(**kwargs):
        communicating.set()
        await asyncio.Event().wait()

    proc = SimpleNamespace(pid=12345, communicate=communicate, wait=AsyncMock(return_value=-9))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "managed-codex")
    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    killed = Mock()
    monkeypatch.setattr(cli, "kill_tree", killed)
    task = asyncio.create_task(cli.run_cli_command(binary="codex", argv=["login", "status"]))
    await communicating.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    killed.assert_called_once_with(12345)
    proc.wait.assert_awaited_once()
