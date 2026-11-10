"""Claude Code sign-in through the CLI's own ``claude auth login`` (GitHub #129).

One login at a time; the card gets the URL of the CLI's sign-in page, the
code that page shows reaches the waiting CLI, the card polls the outcome,
and Cancel stops the CLI. No real CLI runs here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nodes.agent.claude_code_agent import _handlers as handlers

SIGN_IN_URL = "https://claude.ai/oauth/authorize?code=true&state=short-lived"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(handlers, "_login_task", None)
    monkeypatch.setattr(handlers, "_login_codes", None)
    monkeypatch.setattr(handlers, "_login_state", {})
    monkeypatch.setattr(handlers, "claude_auth_status_info", AsyncMock(return_value={"loggedIn": False}))
    monkeypatch.setattr(handlers, "claude_auth_logout", AsyncMock(return_value=True))
    monkeypatch.setattr(handlers, "mark_logged_in", AsyncMock())
    monkeypatch.setattr(handlers, "mark_logged_out", AsyncMock())
    monkeypatch.setattr(handlers, "broadcast_credential_event", AsyncMock())


async def test_login_answers_with_the_cli_sign_in_page(monkeypatch):
    release = asyncio.Event()

    async def login(*, on_prompt, codes):
        on_prompt({"url": SIGN_IN_URL})
        await release.wait()
        return {"success": False, "error": "stopped"}

    monkeypatch.setattr(handlers, "run_claude_login", login)
    result = await handlers.handle_claude_code_login({}, None)
    assert result["success"] and result["pending"] and not result["connected"]
    assert result["url"] == SIGN_IN_URL
    assert "paste it here" in result["message"]
    release.set()
    await handlers._login_task


async def test_second_click_does_not_spawn_second_login(monkeypatch):
    started = 0
    release = asyncio.Event()

    async def login(*, on_prompt, codes):
        nonlocal started
        started += 1
        await release.wait()
        return {"success": False, "error": "stopped"}

    monkeypatch.setattr(handlers, "run_claude_login", login)
    monkeypatch.setattr(handlers, "LOGIN_RESPONSE_WAIT_SECONDS", 0.01)

    first = await handlers.handle_claude_code_login({}, None)
    second = await handlers.handle_claude_code_login({}, None)

    assert first["success"] and first["pending"]
    assert second["pending"]
    assert started == 1
    assert handlers._login_in_progress() is True

    release.set()
    await handlers._login_task
    assert handlers._login_in_progress() is False


async def test_new_login_allowed_after_previous_finishes(monkeypatch):
    calls = 0

    async def login(*, on_prompt, codes):
        nonlocal calls
        calls += 1
        return {"success": False, "error": "stopped"}

    monkeypatch.setattr(handlers, "run_claude_login", login)

    await handlers.handle_claude_code_login({}, None)
    await handlers._login_task
    await handlers.handle_claude_code_login({}, None)
    await handlers._login_task
    assert calls == 2


async def test_pasted_code_reaches_the_cli_and_connects(monkeypatch):
    received = []

    async def login(*, on_prompt, codes):
        on_prompt({"url": SIGN_IN_URL})
        received.append(await codes.get())
        return {"success": True, "error": None}

    monkeypatch.setattr(handlers, "run_claude_login", login)
    monkeypatch.setattr(
        handlers,
        "claude_auth_status_info",
        AsyncMock(side_effect=[{"loggedIn": False}, {"loggedIn": True, "email": "owner@example.com", "orgName": "Owner"}]),
    )

    await handlers.handle_claude_code_login({}, None)
    submitted = await handlers.handle_claude_code_submit_code({"code": " abc\n#def "}, None)
    assert submitted["success"] and submitted["pending"]
    await handlers._login_task

    assert received == ["abc#def"]
    handlers.mark_logged_in.assert_awaited_once_with("claude_code", email="owner@example.com", name="Owner")
    handlers.broadcast_credential_event.assert_awaited_once_with("credential.oauth.connected", provider="claude_code")
    assert await handlers.handle_claude_code_status({}, None) == {"success": True, "connected": True, "pending": False}


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ({"success": False, "error": "Claude sign-in timed out. Click Login to start again."}, "timed out"),
        ({"success": True, "error": None}, "did not finish"),
    ],
)
async def test_failed_sign_in_shows_its_error_and_never_connects(monkeypatch, outcome, expected):
    monkeypatch.setattr(handlers, "run_claude_login", AsyncMock(return_value=outcome))
    await handlers.handle_claude_code_login({}, None)
    await handlers._login_task

    result = await handlers.handle_claude_code_status({}, None)
    assert not result["success"] and not result["pending"] and expected in result["error"]
    handlers.mark_logged_in.assert_not_awaited()

    refreshed = await handlers.handle_claude_code_status({"refresh": True}, None)
    assert refreshed["success"] and "error" not in refreshed


async def test_cancel_stops_the_waiting_sign_in_without_signing_out(monkeypatch):
    cancelled = asyncio.Event()

    async def login(*, on_prompt, codes):
        on_prompt({"url": SIGN_IN_URL})
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(handlers, "run_claude_login", login)
    await handlers.handle_claude_code_login({}, None)

    result = await handlers.handle_claude_code_logout({}, None)
    assert result == {"success": True, "connected": False, "pending": False}
    assert cancelled.is_set() and not handlers._login_in_progress()
    handlers.claude_auth_logout.assert_not_awaited()
    handlers.mark_logged_out.assert_not_awaited()


async def test_incomplete_code_is_not_sent_and_the_sign_in_keeps_waiting(monkeypatch):
    release = asyncio.Event()
    received = []

    async def login(*, on_prompt, codes):
        on_prompt({"url": SIGN_IN_URL})
        await release.wait()
        while not codes.empty():
            received.append(codes.get_nowait())
        return {"success": False, "error": "stopped"}

    monkeypatch.setattr(handlers, "run_claude_login", login)
    await handlers.handle_claude_code_login({}, None)
    result = await handlers.handle_claude_code_submit_code({"code": "only-the-first-half"}, None)
    assert result["success"] and result["pending"] and "incomplete" in result["message"]
    release.set()
    await handlers._login_task
    assert received == []


async def test_code_without_a_waiting_sign_in_is_refused():
    result = await handlers.handle_claude_code_submit_code({"code": "abc"}, None)
    assert not result["success"] and not result["pending"] and result["error"]


async def test_signed_in_cli_refreshes_marker_without_login(monkeypatch):
    monkeypatch.setattr(
        handlers,
        "claude_auth_status_info",
        AsyncMock(return_value={"loggedIn": True, "email": "owner@example.com", "orgName": "Owner"}),
    )
    login = AsyncMock()
    monkeypatch.setattr(handlers, "run_claude_login", login)

    result = await handlers.handle_claude_code_login({}, None)
    assert result["connected"] and result["already_logged_in"] and not result["pending"]
    login.assert_not_called()
    handlers.mark_logged_in.assert_awaited_once_with("claude_code", email="owner@example.com", name="Owner")


async def test_logout_signs_out_when_no_sign_in_is_waiting():
    assert await handlers.handle_claude_code_logout({}, None) == {"success": True}
    handlers.claude_auth_logout.assert_awaited_once()
    handlers.mark_logged_out.assert_awaited_once_with("claude_code")
    handlers.broadcast_credential_event.assert_awaited_once_with("credential.oauth.disconnected", provider="claude_code")


async def test_shutdown_stops_a_waiting_sign_in(monkeypatch):
    async def login(*, on_prompt, codes):
        await asyncio.Event().wait()

    monkeypatch.setattr(handlers, "run_claude_login", login)
    monkeypatch.setattr(handlers, "LOGIN_RESPONSE_WAIT_SECONDS", 0.01)
    await handlers.handle_claude_code_login({}, None)
    task = handlers._login_task

    await handlers.shutdown_claude_login()
    assert task.cancelled()
    assert handlers._login_task is None


def test_card_names_the_registered_commands():
    config_path = Path(handlers.__file__).parents[3] / "config" / "credential_providers.json"
    card = json.loads(config_path.read_text(encoding="utf-8"))["providers"]["claude_code"]
    assert card["ws"] == {
        "login": "claude_code_login",
        "logout": "claude_code_logout",
        "status": "claude_code_status",
        "submit_code": "claude_code_submit_code",
    }
    assert set(card["ws"].values()) <= set(handlers.WS_HANDLERS)
