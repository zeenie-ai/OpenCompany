"""Tests for the BaseSupervisor / BaseProcessSupervisor / BaseClientSupervisor stack."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure server/ on sys.path (mirrors conftest.py)
SERVER_DIR = Path(__file__).resolve().parents[2]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from services._supervisor import (  # noqa: E402
    BaseClientSupervisor,
    BaseProcessSupervisor,
    BaseSupervisor,
    RestartPolicy,
    get_supervisor,
    list_supervisors,
    register_supervisor,
    shutdown_all_supervisors,
)
from services._supervisor.registry import _SUPERVISORS  # noqa: E402
from services._supervisor.util import (  # noqa: E402
    _console_process_ids,
    drain_stream,
    kill_tree,
    send_ctrl_break,
    terminate_then_kill,
)


# --- helpers ---------------------------------------------------------------


class _FakeSupervisor(BaseSupervisor):
    """Minimal concrete supervisor that flips a `running` flag."""

    def __init__(self, name: str = "fake") -> None:
        super().__init__()
        self.name = name
        self._running = False
        self.start_calls = 0
        self.stop_calls = 0
        self.fail_next_start = False

    def is_running(self) -> bool:
        return self._running

    async def _do_start(self) -> None:
        self.start_calls += 1
        if self.fail_next_start:
            self.fail_next_start = False
            raise RuntimeError("boom")
        self._running = True

    async def _do_stop(self) -> None:
        self.stop_calls += 1
        self._running = False


@pytest.fixture(autouse=True)
def _clear_registry():
    """Each test starts with an empty supervisor registry + reset singletons."""
    _SUPERVISORS.clear()
    yield
    _SUPERVISORS.clear()
    # Wipe per-class singletons to avoid cross-test bleed
    for klass in (BaseSupervisor, BaseProcessSupervisor, BaseClientSupervisor, _FakeSupervisor):
        klass._instance = None


# --- BaseSupervisor lifecycle ----------------------------------------------


@pytest.mark.asyncio
async def test_start_stop_idempotent():
    s = _FakeSupervisor()
    await s.start()
    await s.start()  # second call is no-op (lock + is_running guard)
    assert s.start_calls == 1
    assert s.is_running()

    await s.stop()
    await s.stop()
    assert s.stop_calls == 1
    assert not s.is_running()


@pytest.mark.asyncio
async def test_start_failure_records_last_error():
    s = _FakeSupervisor()
    s.fail_next_start = True
    with pytest.raises(RuntimeError, match="boom"):
        await s.start()
    assert s._last_error == "boom"
    assert not s.is_running()


@pytest.mark.asyncio
async def test_status_snapshot_shape():
    s = _FakeSupervisor(name="hello")
    snap = s.status_snapshot()
    assert snap["name"] == "hello"
    assert snap["running"] is False
    assert snap["started_at"] is None
    assert snap["last_error"] is None

    await s.start()
    snap = s.status_snapshot()
    assert snap["running"] is True
    assert snap["started_at"] is not None


@pytest.mark.asyncio
async def test_restart_with_policy_retries():
    s = _FakeSupervisor()
    await s.start()
    s.fail_next_start = True  # first restart attempt fails
    policy = RestartPolicy(attempts=3, min_wait=0.0, max_wait=0.0)
    await s.restart(policy=policy)
    # Stop once, then start failed, then start succeeded
    assert s.stop_calls == 1
    assert s.start_calls >= 2
    assert s.is_running()


# --- Singleton classmethod -------------------------------------------------


def test_get_instance_returns_same_singleton():
    a = _FakeSupervisor.get_instance(name="solo")
    b = _FakeSupervisor.get_instance()  # ignored args after first
    assert a is b
    assert a.name == "solo"


@pytest.mark.asyncio
async def test_reset_instance_clears_singleton():
    a = _FakeSupervisor.get_instance(name="first")
    await _FakeSupervisor.reset_instance()
    b = _FakeSupervisor.get_instance(name="second")
    assert a is not b
    assert b.name == "second"


# --- Registry --------------------------------------------------------------


def test_register_and_list_supervisors():
    s1 = _FakeSupervisor(name="alpha")
    s2 = _FakeSupervisor(name="beta")
    register_supervisor(s1)
    register_supervisor(s2)
    register_supervisor(s1)  # idempotent on same instance
    assert {s.label for s in list_supervisors()} == {"alpha", "beta"}
    assert get_supervisor("alpha") is s1


def test_register_collision_raises():
    register_supervisor(_FakeSupervisor(name="dup"))
    with pytest.raises(ValueError, match="already registered"):
        register_supervisor(_FakeSupervisor(name="dup"))


@pytest.mark.asyncio
async def test_shutdown_all_supervisors_stops_running_only():
    s1 = _FakeSupervisor(name="r")
    s2 = _FakeSupervisor(name="s")
    register_supervisor(s1)
    register_supervisor(s2)
    await s1.start()
    # s2 stays unstarted
    await shutdown_all_supervisors()
    assert s1.stop_calls == 1
    assert s2.stop_calls == 0


@pytest.mark.asyncio
async def test_shutdown_all_supervisors_swallows_errors():
    class _BadStop(_FakeSupervisor):
        async def _do_stop(self) -> None:
            raise RuntimeError("explode")

    s = _BadStop(name="bad")
    register_supervisor(s)
    await s.start()
    await shutdown_all_supervisors()  # must not raise


# --- util.kill_tree --------------------------------------------------------


def test_kill_tree_no_such_process_is_noop():
    # Non-existent PID — should not raise.
    kill_tree(999_999_999)


def test_kill_tree_walks_descendants():
    fake_child = MagicMock()
    fake_parent = MagicMock()
    fake_parent.children.return_value = [fake_child]

    with patch("services._supervisor.util.psutil.Process", return_value=fake_parent):
        kill_tree(123)

    fake_child.kill.assert_called_once()
    fake_parent.kill.assert_called_once()


# --- util.terminate_then_kill ---------------------------------------------


@pytest.mark.asyncio
async def test_terminate_then_kill_returns_early_if_already_dead():
    proc = MagicMock()
    proc.returncode = 0  # already exited
    await terminate_then_kill(proc)
    proc.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_terminate_then_kill_force_kills_after_grace():
    proc = MagicMock()
    proc.returncode = None  # appears alive
    proc.pid = 42
    # The first wait (in move_on_after) exceeds the grace; the post-kill wait returns.
    # Each sleep is awaited inside the call: an AsyncMock returns a coroutine
    # listed in side_effect as its value without awaiting it.
    delays = [10, 0]

    async def wait():
        await asyncio.sleep(delays.pop(0))

    proc.wait = AsyncMock(side_effect=wait)
    proc.terminate = MagicMock()

    with patch("services._supervisor.util.kill_tree") as kill_tree_mock:
        await terminate_then_kill(proc, grace=0.05)

    proc.terminate.assert_called_once()
    kill_tree_mock.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_terminate_then_kill_sends_ctrl_break_without_os_kill():
    """``os.kill(pid, CTRL_BREAK_EVENT)`` hard-kills the child and leaves an
    ``OSError`` pending when Windows refuses the event (Python 3.12.8, gh-58689)."""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 42

    async def exit_after_the_break():
        proc.returncode = 0

    proc.wait = AsyncMock(side_effect=exit_after_the_break)
    with (
        patch("services._supervisor.util.sys.platform", "win32"),
        patch("services._supervisor.util.send_ctrl_break", return_value=True) as send_break,
        patch("services._supervisor.util.kill_tree") as kill_tree_mock,
        patch("os.kill", side_effect=AssertionError("terminate_then_kill must not call os.kill")),
    ):
        await terminate_then_kill(proc, grace=1.0, use_ctrl_break=True)

    send_break.assert_called_once_with(42)
    proc.terminate.assert_not_called()
    kill_tree_mock.assert_not_called()


# --- util.send_ctrl_break (real Win32 calls) --------------------------------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="console control events exist only on Windows")


def _sleeper(creationflags: int) -> subprocess.Popen:
    # The base interpreter: a venv launcher would add a process of its own.
    return subprocess.Popen(
        [sys._base_executable, "-c", "import time; time.sleep(30)"],
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@windows_only
def test_send_ctrl_break_leaves_a_process_outside_this_console_alone():
    sleeper = _sleeper(subprocess.DETACHED_PROCESS)
    try:
        assert send_ctrl_break(sleeper.pid) is False
        with pytest.raises(subprocess.TimeoutExpired):
            sleeper.wait(timeout=0.5)
    finally:
        sleeper.kill()
        sleeper.wait(timeout=10)


@windows_only
def test_send_ctrl_break_stops_a_process_group_on_this_console():
    attached = _console_process_ids()
    if not attached:
        pytest.skip("this test process has no console")
    assert os.getpid() in attached
    sleeper = _sleeper(subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        deadline = time.monotonic() + 10
        while sleeper.pid not in _console_process_ids():
            assert time.monotonic() < deadline, "the sleeper never attached to this console"
            time.sleep(0.05)
        assert send_ctrl_break(sleeper.pid) is True
        sleeper.wait(timeout=10)  # long before its own 30 s sleep ends
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=10)


# --- util.drain_stream -----------------------------------------------------


@pytest.mark.asyncio
async def test_drain_stream_handles_none():
    captured = []
    await drain_stream(None, captured.append)
    assert captured == []


@pytest.mark.asyncio
async def test_drain_stream_forwards_lines_with_prefix():
    class _FakeStream:
        def __init__(self, chunks):
            self._chunks = chunks

        def __aiter__(self):
            self._iter = iter(self._chunks)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    captured = []
    stream = _FakeStream([b"hello\n", b"world\n", b"trailing"])
    await drain_stream(stream, captured.append, prefix="[x] ")
    assert captured == ["[x] hello", "[x] world", "[x] trailing"]


# --- BaseClientSupervisor defaults ----------------------------------------


@pytest.mark.asyncio
async def test_client_supervisor_session_lifecycle():
    class _C(BaseClientSupervisor):
        name = "client"

    c = _C()
    assert not c.is_running()
    await c.start()
    assert c.is_running()
    await c.stop()
    assert not c.is_running()
