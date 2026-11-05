"""Issue #128: native authentication must reach the real credential catalogue."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.cli_agent import _cli_auth, _codex_auth as auth, _handlers as handlers
from services.credential_registry import CredentialRegistry, provider_connection_state


@pytest.fixture
def catalogue_login(monkeypatch, tmp_path):
    from core.container import container
    import services.status_broadcaster as broadcaster

    tokens = {}

    async def store(**values):
        tokens[values["provider"]] = values

    async def remove(provider):
        tokens.pop(provider, None)

    service = SimpleNamespace(
        distributed_credentials=False,
        store_oauth_tokens=AsyncMock(side_effect=store),
        remove_oauth_tokens=AsyncMock(side_effect=remove),
        get_oauth_tokens=AsyncMock(side_effect=lambda provider: tokens.get(provider)),
    )
    events = SimpleNamespace(broadcast_credential_event=AsyncMock())
    monkeypatch.setattr(container, "auth_service", Mock(return_value=service))
    monkeypatch.setattr(broadcaster, "get_status_broadcaster", lambda: events)
    monkeypatch.setattr(handlers, "_login_task", None)
    monkeypatch.setattr(handlers, "_login_state", {})
    monkeypatch.setattr(handlers, "_login_lock", asyncio.Lock())
    monkeypatch.setattr(handlers, "_login_native_started", False)
    monkeypatch.setattr(handlers, "_auth_revision", 0)
    monkeypatch.setattr(handlers, "mark_logged_in", _cli_auth.mark_logged_in)
    monkeypatch.setattr(handlers, "mark_logged_out", _cli_auth.mark_logged_out)
    monkeypatch.setattr(handlers, "broadcast_credential_event", _cli_auth.broadcast_credential_event)
    monkeypatch.setattr(handlers, "codex_auth_status_info", auth.codex_auth_status_info)
    monkeypatch.setattr(handlers, "codex_binary_path", Mock(return_value="managed-codex"))
    monkeypatch.setattr(auth, "codex_installed_binary_path", lambda: "managed-codex")
    monkeypatch.setattr(auth, "OPENCOMPANY_CODEX_DIR", tmp_path / "codex")
    native = AsyncMock(return_value={"success": True, "stderr": "Logged in using ChatGPT"})
    monkeypatch.setattr(auth, "run_cli_command", native)
    login = AsyncMock()
    monkeypatch.setattr(handlers, "run_codex_login", login)
    provider = CredentialRegistry().get_provider("codex_cli")
    return SimpleNamespace(service=service, events=events, native=native, login=login, tokens=tokens, provider=provider)


async def test_authenticated_native_status_marks_catalogue_connected(catalogue_login):
    ctx = catalogue_login
    before = await provider_connection_state(ctx.provider, ctx.service)
    assert not before["stored"] and not before["connected"]

    result = await handlers.handle_codex_cli_login({}, None)

    assert result["success"] and result["connected"] and result["already_logged_in"]
    ctx.login.assert_not_awaited()
    state = await provider_connection_state(ctx.provider, ctx.service)
    assert state["stored"] and state["connected"]
    assert ctx.tokens["codex_cli"]["access_token"] == _cli_auth.MARKER_TOKEN
    ctx.events.broadcast_credential_event.assert_awaited_once_with("credential.oauth.connected", provider="codex_cli")
    call = ctx.native.await_args
    assert call.kwargs["argv"] == ["login", "status"]
    assert call.kwargs["binary"] == "managed-codex"
    assert call.kwargs["env"]["CODEX_HOME"] == str(auth.OPENCOMPANY_CODEX_DIR)


async def test_headless_device_login_verifies_before_catalogue_connects(catalogue_login, monkeypatch):
    ctx = catalogue_login
    ctx.native.side_effect = [
        {"success": False, "stderr": "Not logged in"},
        {"success": True, "stderr": "Logged in using ChatGPT"},
    ]
    release = asyncio.Event()

    async def device_login(*, on_prompt, device_auth):
        assert device_auth is True
        on_prompt({"url": "https://auth.openai.com/codex/device", "verification_code": "ABCD-EFGHJ"})
        await release.wait()
        return {"success": True}

    monkeypatch.setattr(handlers, "run_codex_login", device_login)
    try:
        pending = await handlers.handle_codex_cli_device_login({}, None)
        assert pending["pending"] and pending["verification_code"] == "ABCD-EFGHJ"
        state = await provider_connection_state(ctx.provider, ctx.service)
        assert not state["stored"] and not state["connected"]
        ctx.events.broadcast_credential_event.assert_not_awaited()
    finally:
        release.set()
        if handlers._login_task is not None:
            await handlers._login_task

    state = await provider_connection_state(ctx.provider, ctx.service)
    assert state["stored"] and state["connected"]
    ctx.events.broadcast_credential_event.assert_awaited_once_with("credential.oauth.connected", provider="codex_cli")
    assert ctx.native.await_count == 2


async def test_signed_out_native_status_clears_persisted_catalogue_marker(catalogue_login):
    ctx = catalogue_login
    await _cli_auth.mark_logged_in("codex_cli")
    assert handlers._login_state == {}  # The durable marker survived a restart.
    assert (await provider_connection_state(ctx.provider, ctx.service))["connected"]
    ctx.native.return_value = {"success": False, "stderr": "Not logged in"}

    result = await handlers.handle_codex_cli_status({"refresh": True}, None)

    assert result["success"] and not result["connected"]
    state = await provider_connection_state(ctx.provider, ctx.service)
    assert not state["stored"] and not state["connected"]
    ctx.events.broadcast_credential_event.assert_awaited_once_with("credential.oauth.disconnected", provider="codex_cli")


async def test_unavailable_probe_preserves_last_verified_catalogue_marker(catalogue_login):
    ctx = catalogue_login
    await _cli_auth.mark_logged_in("codex_cli")
    ctx.native.return_value = {"success": False, "error": "process unavailable"}

    result = await handlers.handle_codex_cli_status({"refresh": True}, None)

    assert not result["success"]
    assert (await provider_connection_state(ctx.provider, ctx.service))["connected"]
    ctx.service.remove_oauth_tokens.assert_not_awaited()
    ctx.events.broadcast_credential_event.assert_not_awaited()
