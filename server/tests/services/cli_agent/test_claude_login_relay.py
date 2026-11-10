"""``run_claude_login`` relays the CLI's sign-in page and code (GitHub #129).

Fake processes only: the real ``claude`` binary never runs, and the CLI's
config directory is a temporary one.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nodes.agent.claude_code_agent import _oauth as oauth


class _Stdin:
    def __init__(self) -> None:
        self.written = bytearray()

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None


def _cli():
    """A fake ``claude auth login`` process and a function that ends it."""
    proc = SimpleNamespace(
        stdout=asyncio.StreamReader(),
        stderr=asyncio.StreamReader(),
        stdin=_Stdin(),
        pid=4321,
        returncode=None,
    )
    exited = asyncio.Event()

    async def wait():
        await exited.wait()
        return proc.returncode

    def end(code: int) -> None:
        proc.returncode = code
        proc.stdout.feed_eof()
        proc.stderr.feed_eof()
        exited.set()

    proc.wait = wait
    return proc, end


@pytest.fixture
async def cli(monkeypatch, tmp_path):
    proc, end = _cli()
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(oauth, "claude_binary_path", lambda: "managed-claude")
    monkeypatch.setattr(oauth, "OPENCOMPANY_CLAUDE_DIR", tmp_path / "claude")
    monkeypatch.setattr(oauth.asyncio, "create_subprocess_exec", spawn)
    killed = Mock(side_effect=lambda _pid: end(-9))
    monkeypatch.setattr(oauth, "kill_tree", killed)
    return SimpleNamespace(proc=proc, end=end, spawn=spawn, killed=killed)


async def _until(condition) -> None:
    async with asyncio.timeout(2):
        while not condition():
            await asyncio.sleep(0)


async def test_relays_the_sign_in_page_and_writes_the_code(cli):
    prompts = []
    codes: asyncio.Queue[str] = asyncio.Queue()
    task = asyncio.create_task(oauth.run_claude_login(on_prompt=prompts.append, codes=codes))
    try:
        await _until(lambda: cli.spawn.called)
        cli.proc.stdout.feed_data(b"Opening browser to sign in\nVisit: https://claude.ai/oauth/authorize?code=true&state=")
        await asyncio.sleep(0)
        assert prompts == []
        cli.proc.stdout.feed_data(b"complete\nPaste code here if prompted > ")
        await _until(lambda: prompts)
        assert prompts == [{"url": "https://claude.ai/oauth/authorize?code=true&state=complete"}]

        codes.put_nowait("the-code#complete")
        await _until(lambda: cli.proc.stdin.written)
        assert bytes(cli.proc.stdin.written) == b"the-code#complete\n"

        cli.end(0)
        assert await task == {"success": True, "error": None}
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    args, kwargs = cli.spawn.await_args
    assert args == ("managed-claude", "auth", "login")
    assert kwargs["stdin"] is asyncio.subprocess.PIPE
    assert kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(oauth.OPENCOMPANY_CLAUDE_DIR)
    cli.killed.assert_not_called()


async def test_failed_sign_in_never_returns_cli_output(cli):
    task = asyncio.create_task(oauth.run_claude_login(on_prompt=lambda _: None, codes=asyncio.Queue()))
    await _until(lambda: cli.spawn.called)
    cli.proc.stderr.feed_data(b"OAuth error: Invalid code secret-value\n")
    cli.end(1)
    result = await task
    assert not result["success"] and result["error"] == oauth.LOGIN_FAILED
    assert "secret" not in result["error"]


async def test_timeout_stops_the_cli(cli, monkeypatch):
    monkeypatch.setattr(oauth, "LOGIN_TIMEOUT_SECONDS", 0.01)
    result = await oauth.run_claude_login(on_prompt=lambda _: None, codes=asyncio.Queue())
    assert not result["success"] and "timed out" in result["error"]
    cli.killed.assert_called_once_with(cli.proc.pid)


async def test_cancel_stops_the_cli(cli):
    task = asyncio.create_task(oauth.run_claude_login(on_prompt=lambda _: None, codes=asyncio.Queue()))
    await _until(lambda: cli.spawn.called)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cli.killed.assert_called_once_with(cli.proc.pid)


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Visit https://claude.ai/oauth/authorize?code=true&state=s", "https://claude.ai/oauth/authorize?code=true&state=s"),
        ("\x1b[36mhttps://platform.claude.com/oauth/authorize?state=s\x1b[39m.", "https://platform.claude.com/oauth/authorize?state=s"),
        ("https://claude.com/cai/oauth/authorize?state=s", "https://claude.com/cai/oauth/authorize?state=s"),
        # What claude 2.1.258 prints, plain and as an OSC 8 hyperlink.
        (
            "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=s",
            "https://claude.com/cai/oauth/authorize?code=true&state=s",
        ),
        (
            "If the browser didn't open, visit: \x1b]8;;https://claude.com/cai/oauth/authorize?code=true&state=s\x07"
            "\x1b[94mhttps://claude.com/cai/oauth/authorize?code=true&state=s\x1b[39m\x1b]8;;\x07",
            "https://claude.com/cai/oauth/authorize?code=true&state=s",
        ),
        ("https://console.anthropic.com/oauth/authorize?state=s", "https://console.anthropic.com/oauth/authorize?state=s"),
        ("https://example.com/oauth/authorize?state=s", None),
        ("https://claude.ai.example.com/oauth/authorize?state=s", None),
        ("https://notclaude.ai/oauth/authorize?state=s", None),
        ("http://claude.ai/oauth/authorize?state=s", None),
        ("https://claude.ai/login", None),
    ],
)
def test_only_claude_sign_in_pages_reach_the_browser(line, expected):
    assert oauth._sign_in_url(line) == expected
