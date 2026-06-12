"""Unit tests for BrowserService instance-cap gating (nodes/browser/_service.py).

The cap leans on agent-browser's own primitives -- ``session list --json``
(the daemon's authoritative session registry) and per-session ``close`` --
instead of mirroring daemon state in Python. These tests stub ``_run_sync``
so no subprocess is spawned and drive the gate through ``run()``.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from nodes.browser._service import BrowserService


pytestmark = pytest.mark.unit


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
