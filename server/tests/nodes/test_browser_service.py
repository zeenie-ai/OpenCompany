"""Unit tests for BrowserService instance-cap gating (nodes/browser/_service.py).

The cap leans on agent-browser's own primitives -- ``session list --json``
(the daemon's authoritative session registry) and per-session ``close`` --
instead of mirroring daemon state in Python. These tests stub ``_run_sync``
so no subprocess is spawned and drive the gate through ``run()``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import patch

import pytest

from nodes.browser._service import BrowserService


pytestmark = pytest.mark.unit


async def _wait_for_thread_event(event: threading.Event) -> None:
    """Yield deterministically until a worker thread reaches its barrier."""
    async with asyncio.timeout(2):
        while not event.is_set():
            await asyncio.sleep(0)


def _make_fake_run_sync(active_sessions, calls, fail_session_list=False):
    """Stub for ``BrowserService._run_sync``.

    ``session list`` returns the given registry (or raises when
    ``fail_session_list``); every other command returns a generic
    success line. ``calls`` collects each argv list for assertions.
    """

    def fake_run_sync(argv, timeout, stdin_data):
        calls.append(list(argv))
        if "session" in argv and "list" in argv:
            if fail_session_list:
                raise RuntimeError("daemon not running")
            return json.dumps({"success": True, "data": {"sessions": list(active_sessions)}})
        return json.dumps({"success": True, "data": {}})

    return fake_run_sync


def _close_calls(calls):
    return [c for c in calls if "close" in c]


class TestInstanceCap:
    async def test_new_session_under_cap_no_close(self, monkeypatch):
        monkeypatch.setenv("BROWSER_MAX_INSTANCES", "3")
        svc = BrowserService(["agent-browser"])
        calls = []
        fake = _make_fake_run_sync(["existing"], calls)

        with patch.object(BrowserService, "_run_sync", staticmethod(fake)):
            await svc.run(["open", "https://example.com"], "fresh_session")

        assert _close_calls(calls) == []

    async def test_cap_reached_closes_oldest_listed(self, monkeypatch):
        monkeypatch.setenv("BROWSER_MAX_INSTANCES", "3")
        svc = BrowserService(["agent-browser"])
        calls = []
        fake = _make_fake_run_sync(["s1", "s2", "s3"], calls)

        with patch.object(BrowserService, "_run_sync", staticmethod(fake)):
            await svc.run(["open", "https://example.com"], "s4")

        closes = _close_calls(calls)
        assert len(closes) == 1
        assert "--session" in closes[0] and "s1" in closes[0], (
            "the first-listed (oldest) session must be closed to make room"
        )

    async def test_existing_session_reuse_never_closes(self, monkeypatch):
        # Session already in the daemon registry -> reuse, even at cap.
        monkeypatch.setenv("BROWSER_MAX_INSTANCES", "3")
        svc = BrowserService(["agent-browser"])
        calls = []
        fake = _make_fake_run_sync(["s1", "s2", "s3"], calls)

        with patch.object(BrowserService, "_run_sync", staticmethod(fake)):
            await svc.run(["snapshot"], "s2")

        assert _close_calls(calls) == []

    async def test_registry_queried_once_per_session(self, monkeypatch):
        # Fast path: the second command on a gated session must not
        # re-spawn a ``session list`` probe.
        monkeypatch.setenv("BROWSER_MAX_INSTANCES", "3")
        svc = BrowserService(["agent-browser"])
        calls = []
        fake = _make_fake_run_sync([], calls)

        with patch.object(BrowserService, "_run_sync", staticmethod(fake)):
            await svc.run(["open", "https://example.com"], "sess_a")
            await svc.run(["snapshot"], "sess_a")

        list_probes = [c for c in calls if "session" in c and "list" in c]
        assert len(list_probes) == 1

    async def test_session_list_failure_fails_open(self, monkeypatch):
        # Gating must never block an actual browser operation.
        monkeypatch.setenv("BROWSER_MAX_INSTANCES", "3")
        svc = BrowserService(["agent-browser"])
        calls = []
        fake = _make_fake_run_sync([], calls, fail_session_list=True)

        with patch.object(BrowserService, "_run_sync", staticmethod(fake)):
            result = await svc.run(["open", "https://example.com"], "sess_b")

        assert result.get("success") is True
        assert _close_calls(calls) == []

    async def test_cap_does_not_evict_an_active_session(self, monkeypatch):
        monkeypatch.setenv("BROWSER_MAX_INSTANCES", "1")
        svc = BrowserService(["agent-browser"])
        calls = []
        fake = _make_fake_run_sync(["active"], calls)

        active_lock = asyncio.Lock()
        await active_lock.acquire()
        svc._session_locks["active"] = active_lock
        try:
            with patch.object(BrowserService, "_run_sync", staticmethod(fake)):
                result = await svc.run(["open", "https://example.com"], "new")
        finally:
            active_lock.release()

        assert result.get("success") is True
        assert _close_calls(calls) == []


class TestSessionSerialization:
    async def test_delay_and_command_are_one_serialized_operation(self):
        svc = BrowserService(["agent-browser"])
        svc._gated_sessions.add("shared")
        calls = []

        def fake(argv, timeout, stdin_data):
            calls.append(list(argv))
            time.sleep(0.01)
            return json.dumps({"success": True})

        with patch.object(BrowserService, "_run_sync", staticmethod(fake)):
            await asyncio.gather(
                svc.run(["snapshot"], "shared", action_delay=1),
                svc.run(["get", "url"], "shared", action_delay=2),
            )

        command_parts = [
            tuple(part for part in argv if part not in {"agent-browser", "--json", "--session", "shared"})
            for argv in calls
        ]
        assert command_parts in (
            [("wait", "1"), ("snapshot",), ("wait", "2"), ("get", "url")],
            [("wait", "2"), ("get", "url"), ("wait", "1"), ("snapshot",)],
        )

    async def test_same_session_commands_do_not_overlap(self):
        svc = BrowserService(["agent-browser"])
        svc._gated_sessions.add("shared")
        state = {"active": 0, "maximum": 0}
        guard = threading.Lock()

        def fake(argv, timeout, stdin_data):
            with guard:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.03)
            with guard:
                state["active"] -= 1
            return json.dumps({"success": True})

        with patch.object(BrowserService, "_run_sync", staticmethod(fake)):
            await asyncio.gather(
                svc.run(["snapshot"], "shared"),
                svc.run(["get", "url"], "shared"),
            )

        assert state["maximum"] == 1

    async def test_different_sessions_remain_parallel(self):
        svc = BrowserService(["agent-browser"])
        svc._gated_sessions.update({"a", "b"})
        state = {"active": 0, "maximum": 0}
        guard = threading.Lock()

        def fake(argv, timeout, stdin_data):
            with guard:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.03)
            with guard:
                state["active"] -= 1
            return json.dumps({"success": True})

        with patch.object(BrowserService, "_run_sync", staticmethod(fake)):
            await asyncio.gather(
                svc.run(["snapshot"], "a"),
                svc.run(["snapshot"], "b"),
            )

        assert state["maximum"] == 2

    async def test_cancellation_keeps_session_lock_until_thread_finishes(self):
        svc = BrowserService(["agent-browser"])
        svc._gated_sessions.add("shared")
        entered = threading.Event()
        release = threading.Event()

        def blocking(_argv, _timeout, _stdin_data):
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("test worker was not released")
            return json.dumps({"success": True})

        with patch.object(BrowserService, "_run_sync", staticmethod(blocking)):
            command = asyncio.create_task(svc.run(["snapshot"], "shared"))
            await _wait_for_thread_event(entered)
            session_lock = svc._session_locks["shared"]

            command.cancel()
            # Let cancellation reach the guarded to_thread await. The worker
            # is still behind ``release``, so the lock must remain owned.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert command.done() is False
            assert session_lock.locked() is True

            release.set()
            with pytest.raises(asyncio.CancelledError):
                await command

        assert session_lock.locked() is False


class TestCommandTimeout:
    def test_no_output_is_terminated_at_deadline(self):
        release = threading.Event()

        class BlockingStdout:
            def readline(self):
                if not release.wait(timeout=2):
                    raise AssertionError("timed-out process was not terminated")
                return b""

        class EmptyStderr:
            def read(self):
                return b""

        class FakeProcess:
            pid = 4242
            stdin = None
            stdout = BlockingStdout()
            stderr = EmptyStderr()

            @staticmethod
            def poll():
                return None

            @staticmethod
            def wait(timeout=None):
                return 0

            @staticmethod
            def kill():
                release.set()

        process = FakeProcess()

        with (
            patch("nodes.browser._service.subprocess.Popen", return_value=process),
            patch("nodes.browser._service.kill_tree", side_effect=lambda _pid: release.set()) as kill,
        ):
            with pytest.raises(TimeoutError, match="timed out after"):
                BrowserService._run_sync(["agent-browser"], 0.01, None)

        kill.assert_called_once_with(process.pid)
        assert release.is_set()
