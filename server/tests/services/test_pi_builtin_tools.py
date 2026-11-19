"""The Pi effect runner must release activities even when process cleanup stalls."""

import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from services import pi_builtin_tools as runner, pi_runtime
from services.plugin import NodeUserError


class FakeInput:
    def __init__(self, proc):
        self.proc = proc
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(data)
        if data == b'{"type":"abort"}\n' and self.proc.graceful_abort:
            self.proc.stop(0)

    async def drain(self):
        pass

    def close(self):
        self.closed = True
        if self.proc.finish_on_close:
            self.proc.stop(0)


class FakeProcess:
    def __init__(self, response=None, *, finish_on_close=True, graceful_abort=True):
        self.pid = 12345
        self.returncode = None
        self.finish_on_close = finish_on_close
        self.graceful_abort = graceful_abort
        self.stdin = FakeInput(self)
        self.stdout = asyncio.StreamReader(limit=runner.FRAME_LIMIT + 1)
        self.stderr = asyncio.StreamReader()
        self.exited = asyncio.Event()
        self.killed = False
        if response is not None:
            self.stdout.feed_data(response)

    def stop(self, code):
        self.returncode = code
        self.exited.set()

    async def wait(self):
        await self.exited.wait()
        return self.returncode

    def kill(self):
        self.killed = True
        self.stop(-9)


@pytest.fixture
def runtime(monkeypatch):
    import core.js_runtime
    import services._supervisor.util

    monkeypatch.setattr(core.js_runtime, "require_bun", lambda purpose: "bundled-bun")
    monkeypatch.setattr(pi_runtime, "resolve_pi_tool_host", lambda: Path.cwd() / "pi" / "src" / "tool-host.ts")
    killed = []
    monkeypatch.setattr(services._supervisor.util, "kill_tree", killed.append)
    monkeypatch.setattr(runner, "STARTUP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(runner, "ABORT_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(runner, "KILL_GRACE_SECONDS", 0.01)
    return SimpleNamespace(killed=killed, workspace=Path.cwd())


def install_process(monkeypatch, proc):
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", spawn)
    return spawn


@pytest.mark.asyncio
async def test_native_result_survives_protocol_and_shell_has_an_explicit_deadline(runtime, monkeypatch):
    result = {"content": [{"type": "text", "text": "failed command"}], "details": {"exitCode": 2},
              "structuredContent": {"exit_code": 2}, "isError": True}
    proc = FakeProcess((json.dumps({"ok": True, "result": result}) + "\n").encode())
    spawn = install_process(monkeypatch, proc)
    answer = await runner.execute_pi_tool("bash", {"command": "exit 2", "timeout": 600}, runtime.workspace, timeout=20)
    assert answer == result
    request = json.loads(proc.stdin.writes[0])
    assert request == {"protocol": 1, "tool": "bash", "cwd": str(runtime.workspace),
                       "args": {"command": "exit 2", "timeout": 20}}
    assert proc.returncode == 0 and proc.stdin.closed
    assert spawn.await_args.kwargs["limit"] == runner.FRAME_LIMIT + 1
    assert spawn.await_args.kwargs["env"]["PI_OFFLINE"] == "1"
    assert runtime.killed == []


@pytest.mark.asyncio
async def test_timeout_aborts_then_kills_before_returning(runtime, monkeypatch):
    proc = FakeProcess(finish_on_close=False, graceful_abort=False)
    install_process(monkeypatch, proc)
    with pytest.raises(NodeUserError, match="execution deadline"):
        await runner.execute_pi_tool("bash", {"command": "unused"}, runtime.workspace, timeout=0.01)
    assert b'{"type":"abort"}\n' in proc.stdin.writes
    assert proc.killed and proc.returncode == -9
    assert runtime.killed == [proc.pid]


@pytest.mark.asyncio
async def test_cancellation_waits_for_owned_process_cleanup(runtime, monkeypatch):
    proc = FakeProcess(finish_on_close=False, graceful_abort=False)
    spawn = install_process(monkeypatch, proc)
    task = asyncio.create_task(runner.execute_pi_tool("ls", {}, runtime.workspace, timeout=10))
    while not spawn.await_count or not proc.stdin.writes:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed and proc.returncode == -9
    assert runtime.killed == [proc.pid]


@pytest.mark.asyncio
async def test_cancellation_during_spawn_does_not_lose_process(runtime, monkeypatch):
    proc = FakeProcess(finish_on_close=False, graceful_abort=False)
    started = asyncio.Event()
    release = asyncio.Event()

    async def spawn(*args, **kwargs):
        started.set()
        await release.wait()
        return proc

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(runner.execute_pi_tool("read", {"path": "note"}, runtime.workspace))
    await started.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed and runtime.killed == [proc.pid]


@pytest.mark.asyncio
async def test_exited_host_with_inherited_pipes_cannot_block_cleanup(runtime, monkeypatch):
    proc = FakeProcess(b'{"ok":true,"result":{"content":[]}}\n')
    proc._transport = SimpleNamespace(close=MagicMock())

    async def stuck_pipe_wait():
        await asyncio.Event().wait()

    proc.wait = stuck_pipe_wait
    install_process(monkeypatch, proc)
    with pytest.raises(NodeUserError, match="execution deadline"):
        await asyncio.wait_for(runner.execute_pi_tool("ls", {}, runtime.workspace), 1)
    assert proc.returncode == 0
    proc._transport.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("wire, message", [
    (b"not-json\n", "invalid JSON"),
    (b'{"ok":true,"result":[]}\n', "invalid tool result"),
    (b'{"ok":1,"result":{}}\n', "invalid response"),
    (b'{"ok":false,"error":"Path escapes the workspace"}\n', "Path escapes"),
])
async def test_protocol_failures_dispose_the_host(runtime, monkeypatch, wire, message):
    proc = FakeProcess(wire)
    install_process(monkeypatch, proc)
    with pytest.raises(NodeUserError, match=message):
        await runner.execute_pi_tool("ls", {}, runtime.workspace)
    assert proc.returncode == 0
    assert b'{"type":"abort"}\n' in proc.stdin.writes


@pytest.mark.asyncio
async def test_transport_input_bound_prevents_spawn(runtime, monkeypatch):
    monkeypatch.setattr(runner, "FRAME_LIMIT", 128)
    spawn = AsyncMock()
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(NodeUserError, match="input exceeds"):
        await runner.execute_pi_tool("write", {"path": "a", "content": "x" * 200}, runtime.workspace)
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_output_bound_disposes_process(runtime, monkeypatch):
    monkeypatch.setattr(runner, "FRAME_LIMIT", 256)
    proc = FakeProcess(b"x" * 300 + b"\n")
    install_process(monkeypatch, proc)
    with pytest.raises(NodeUserError, match="transport limit"):
        await runner.execute_pi_tool("ls", {}, runtime.workspace)
    assert proc.returncode == 0


def test_environment_preserves_git_bash_discovery_and_removes_loaders(monkeypatch):
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "private")
    monkeypatch.setenv("NODE_OPTIONS", "--require arbitrary-loader")
    monkeypatch.setenv("BUN_OPTIONS", "--preload arbitrary-loader")
    environment = runner._environment(Path.cwd() / "pi" / "src" / "tool-host.ts")
    assert {key.upper(): value for key, value in environment.items()}["PROGRAMFILES"] == r"C:\Program Files"
    assert not {"OP_SERVICE_ACCOUNT_TOKEN", "NODE_OPTIONS", "BUN_OPTIONS"} & environment.keys()
    assert environment["NODE_PATH"] == ""


@pytest.fixture
def pi_tree(monkeypatch):
    # Inherited ACLs allow the restricted Windows test token to read files.
    parent = Path.cwd().resolve()
    scratch = parent / f".pi-tool-runtime-test-{uuid4().hex}"
    scratch.mkdir()
    pi = scratch / "server" / "pi"
    pi.mkdir(parents=True)
    monkeypatch.setattr(pi_runtime, "__file__", str(scratch / "server" / "services" / "pi_runtime.py"))
    try:
        yield pi
    finally:
        assert scratch.resolve().parent == parent
        shutil.rmtree(scratch)


def test_tool_bundle_is_resolved_independently_from_maintenance_bundle(pi_tree):
    (pi_tree / "dist").mkdir()
    host = pi_tree / "dist" / "tool-host.js"
    host.write_text("export {};", encoding="utf-8")
    assert pi_runtime.resolve_pi_tool_host() == host
    assert pi_runtime.pi_package_dir(host) == pi_tree / "dist"


def test_missing_new_bundle_can_use_pinned_source_next_to_old_bundle(pi_tree):
    (pi_tree / "dist").mkdir()
    (pi_tree / "dist" / "index.js").write_text("export {};", encoding="utf-8")
    (pi_tree / "src").mkdir()
    source = pi_tree / "src" / "tool-host.ts"
    source.write_text("export {};", encoding="utf-8")
    package = pi_tree / "node_modules" / "@earendil-works" / "pi-coding-agent"
    package.mkdir(parents=True)
    (package / "package.json").write_text('{"version":"1.1.0"}', encoding="utf-8")
    assert pi_runtime.resolve_pi_tool_host() == source
    assert pi_runtime.pi_package_dir(source) == package


def test_packaged_install_without_tool_bundle_requires_rebuild(pi_tree):
    from temporalio.exceptions import ApplicationError

    (pi_tree / "dist").mkdir()
    (pi_tree / "dist" / "index.js").write_text("export {};", encoding="utf-8")
    with pytest.raises(ApplicationError, match="Rebuild OpenCompany") as error:
        pi_runtime.resolve_pi_tool_host()
    assert error.value.non_retryable


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("PI_TOOL_INTEGRATION") != "1", reason="Set PI_TOOL_INTEGRATION=1 for real Bun subprocess tests")
async def test_real_source_host_files_shell_timeout_and_cancellation(monkeypatch):
    """Exercise the Python boundary and actual SDK, including owned children.

    Commands affect only this temporary workspace. Each test child self-exits
    after four seconds even if cleanup regresses, so failures cannot leave a
    persistent daemon behind.
    """
    import psutil
    from core.js_runtime import require_bun

    source = Path(__file__).resolve().parents[2] / "pi" / "src" / "tool-host.ts"
    assert source.is_file()
    bun = require_bun("Pi tool integration test")
    monkeypatch.setattr(pi_runtime, "resolve_pi_tool_host", lambda: source)
    parent = Path.cwd().resolve()
    workspace = parent / f".pi-tool-integration-{uuid4().hex}"
    workspace.mkdir()
    tasks = []

    def text(result):
        return "\n".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")

    async def child_started(task, filename):
        path = workspace / filename
        async with asyncio.timeout(15):
            while not path.exists():
                if task.done():
                    await task
                    pytest.fail("The shell returned before starting its test child")
                await asyncio.sleep(0.02)
        return int(path.read_text())

    try:
        written = await runner.execute_pi_tool("write", {"path": "note.txt", "content": "before\n"}, workspace)
        assert not written.get("isError") and (workspace / "note.txt").read_text() == "before\n"
        read = await runner.execute_pi_tool("read", {"path": "note.txt"}, workspace)
        assert "before" in text(read)
        edited = await runner.execute_pi_tool("edit", {"path": "note.txt", "edits": [{"oldText": "before", "newText": "after"}]}, workspace)
        assert not edited.get("isError") and (workspace / "note.txt").read_text() == "after\n"
        listing = await runner.execute_pi_tool("ls", {}, workspace)
        assert "note.txt" in text(listing)
        marker = await runner.execute_pi_tool("bash", {"command": "printf pi-native-marker"}, workspace, timeout=10)
        assert "pi-native-marker" in text(marker)

        (workspace / "child.cjs").write_text(
            "const fs=require('node:fs');fs.writeFileSync(process.argv[2],String(process.pid));setTimeout(()=>{},4000);",
            encoding="utf-8",
        )
        executable = shlex.quote(Path(bun).as_posix())
        timed = asyncio.create_task(runner.execute_pi_tool("bash", {"command": f"{executable} child.cjs timeout.pid"}, workspace, timeout=1))
        tasks.append(timed)
        timed_pid = await child_started(timed, "timeout.pid")
        start = time.monotonic()
        with pytest.raises(NodeUserError, match="(?i)timed out|timeout|deadline"):
            await timed
        assert time.monotonic() - start < 3, "The shell waited for the child to self-exit instead of enforcing its timeout"
        assert not psutil.pid_exists(timed_pid), "Timed-out command child survived the tool result"

        cancelled = asyncio.create_task(runner.execute_pi_tool("bash", {"command": f"{executable} child.cjs cancellation.pid"}, workspace, timeout=10))
        tasks.append(cancelled)
        cancelled_pid = await child_started(cancelled, "cancellation.pid")
        start = time.monotonic()
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert time.monotonic() - start < 3, "Cancellation waited for the child to self-exit"
        assert not psutil.pid_exists(cancelled_pid), "Cancelled command child survived activity cleanup"
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        assert workspace.resolve().parent == parent
        shutil.rmtree(workspace)
