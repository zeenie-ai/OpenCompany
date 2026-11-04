"""Exercise Codex's real subprocess lifecycle without a model or network."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import psutil
import pytest

from services.cli_agent.providers.openai_codex import OpenAICodexProvider
from services.cli_agent.session import AICliSession
from services.cli_agent.types import CodexTaskSpec


@pytest.fixture
def make_session(tmp_path, monkeypatch):
    sessions = []

    def make(code: str, *, sink=None, isolated_env=False):
        provider = OpenAICodexProvider()
        monkeypatch.setattr(provider, "binary_path", lambda: Path(sys.executable))
        monkeypatch.setattr(provider, "interactive_argv", lambda *args, **kwargs: [sys.executable, "-u", "-c", code])
        session = AICliSession(
            provider=provider,
            task=CodexTaskSpec(prompt="test"),
            repo_root=tmp_path,
            workspace_dir=tmp_path,
            node_id="node",
            workflow_id="workflow",
            broadcaster=None,
            defaults={},
            mcp_port=5678,
            batch_token="test-token",
            memory_bound=True,
            context_event_sink=sink,
        )
        if not isolated_env:
            monkeypatch.setattr(session, "env", lambda: os.environ.copy())
        sessions.append(session)
        return session

    return make


def emit(event):
    return f"print({json.dumps(json.dumps(event))}, flush=True)\n"


def test_codex_environment_matches_login_and_keeps_batch_token_out_of_argv(make_session, monkeypatch, tmp_path):
    from services.cli_agent import _codex_auth
    from services.cli_agent.providers.openai_codex import MCP_TOKEN_ENV

    managed_home = tmp_path / "managed-codex"
    monkeypatch.setattr(_codex_auth, "OPENCOMPANY_CODEX_DIR", managed_home)
    monkeypatch.setenv("CODEX_HOME", "personal-home")
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "private-account-token")
    session = make_session("pass", isolated_env=True)
    env = session.env()
    assert env["CODEX_HOME"] == str(managed_home)
    assert managed_home.is_dir()
    assert env[MCP_TOKEN_ENV] == "test-token"
    assert "OP_SERVICE_ACCOUNT_TOKEN" not in env
    assert "test-token" not in " ".join(session.argv())


COMPLETED = {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}}
MESSAGE = {"type": "item.completed", "item": {"id": "msg", "type": "agent_message", "text": "done"}}


@pytest.mark.asyncio
async def test_codex_consumes_chunked_jsonl_and_final_line_after_terminal_event(make_session):
    # The final line has no newline, and arrives after turn.completed.
    # Waiting for that event alone would lose this response.
    raw = json.dumps(MESSAGE)
    code = (
        "import sys, time\n"
        + "print('noise'); print('[]'); print('null')\n"
        + emit({"type": "thread.started", "thread_id": "thread"})
        + emit(COMPLETED)
        + f"sys.stdout.write({raw[:12]!r}); sys.stdout.flush(); time.sleep(0.05)\n"
        + f"sys.stdout.write({raw[12:]!r}); sys.stdout.flush()\n"
    )
    sink = AsyncMock()
    session = make_session(code, sink=sink)
    try:
        await session.start()
        result = await session.wait_for_completion(5)
        assert result.success
        assert result.response == "done"
        assert result.session_id == "thread"
        assert result.canonical_usage.output_tokens == 2
        assert sink.await_count == 3
    finally:
        await session.cleanup()
    assert session._codex_proc is None
    assert session._codex_completion is None


@pytest.mark.asyncio
async def test_codex_nonzero_exit_preserves_stderr_and_drains_large_stream(make_session):
    session = make_session(
        "import sys\n"
        + "sys.stderr.write('x' * 100000 + 'Authentication required'); sys.stderr.flush()\n"
        + emit(COMPLETED)
        + "sys.exit(3)\n"
    )
    try:
        await session.start()
        result = await session.wait_for_completion(5)
        assert not result.success
        assert "Authentication required" in result.error
        assert len(session._codex_stderr) <= 16384
    finally:
        await session.cleanup()


@pytest.mark.asyncio
async def test_codex_premature_stream_end_is_failure(make_session):
    session = make_session(emit({"type": "turn.started"}))
    try:
        await session.start()
        result = await session.wait_for_completion(5)
        assert not result.success
        assert "without turn.completed" in result.error
    finally:
        await session.cleanup()


@pytest.mark.asyncio
async def test_codex_timeout_reaps_process(make_session):
    session = make_session("import time; time.sleep(60)")
    await session.start()
    proc = session._codex_proc
    result = await session.wait_for_completion(0.1)
    assert not result.success
    assert "timeout" in result.error
    assert proc.returncode is not None
    assert not session.is_running()
    await session.cleanup()


@pytest.mark.asyncio
async def test_codex_cancellation_reaps_wrapper_and_descendant(make_session):
    code = (
        "import subprocess, sys, time, json\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(json.dumps({'type':'child', 'pid':child.pid}), flush=True)\n"
        "time.sleep(60)\n"
    )
    session = make_session(code)
    await session.start()
    proc = session._codex_proc
    waiter = asyncio.create_task(session.wait_for_completion(60))
    try:
        async with asyncio.timeout(5):
            while not session._events:
                await asyncio.sleep(0.01)
        child_pid = session._events[0]["pid"]
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert proc.returncode is not None
        assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
        assert not session.is_running()
    finally:
        if not waiter.done():
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
        await session.cleanup()


@pytest.mark.asyncio
async def test_codex_context_capture_failure_ends_child_immediately(make_session):
    sink = AsyncMock(side_effect=RuntimeError("journal unavailable"))
    session = make_session(
        "import time\n" + emit({"type": "turn.started"}) + "time.sleep(60)\n",
        sink=sink,
    )
    try:
        await session.start()
        proc = session._codex_proc
        result = await session.wait_for_completion(5)
        assert not result.success
        assert "context_event_persistence_failed" in result.error
        assert proc.returncode is not None
    finally:
        await session.cleanup()


@pytest.mark.asyncio
async def test_codex_failed_spawn_still_cleans_worktree(make_session, monkeypatch):
    from services.cli_agent import worktree

    session = make_session("pass")
    session._memory_bound = False
    monkeypatch.setattr(worktree, "add_worktree", AsyncMock())
    remove = AsyncMock()
    monkeypatch.setattr(worktree, "remove_worktree", remove)

    async def fail(*args, **kwargs):
        raise OSError("spawn refused")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    with pytest.raises(OSError, match="spawn refused"):
        await session.start()
    await session.cleanup()
    remove.assert_awaited_once()
    assert session._codex_proc is None
