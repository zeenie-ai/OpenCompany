"""Contract tests for code_fs_process nodes.

Covers: pythonExecutor, javascriptExecutor, typescriptExecutor,
        fileRead, fileModify, shell, fsSearch, processManager.

Frozen behavioural contract for the 8 nodes in
`docs-internal/node-logic-flows/code_fs_process/`. Each test asserts the
standard handler envelope shape and at least one payload detail from the
matching doc; external side-effects (subprocess spawn, Node.js HTTP call,
deepagents backend, Terminal broadcast) are mocked.

Mocking strategy:
  - pythonExecutor runs in-process (no external service) -- tests exercise
    the real exec() path.
  - javascriptExecutor / typescriptExecutor: patch NodeJSClient.execute()
    via the module-level _nodejs_client singleton.
  - filesystem nodes (fileRead, fileModify, shell, fsSearch): patch
    services.handlers.filesystem._get_backend to return a fake backend with
    the methods each mode invokes.
  - processManager: patch services.process_service.get_process_service to
    return a fake ProcessService with the dispatched method set to an
    AsyncMock / MagicMock.
"""

from __future__ import annotations

import asyncio
import os
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


pytestmark = pytest.mark.node_contract


async def _wait_for_thread_event(event: threading.Event) -> None:
    """Yield deterministically until a worker thread reaches its barrier."""
    async with asyncio.timeout(2):
        while not event.is_set():
            await asyncio.sleep(0)


# ============================================================================
# Helpers
# ============================================================================


def _reset_nodejs_singleton():
    """Clear the module-level client singleton in nodes.code._nodejs.

    The plugin caches its NodeJSClient on first use; tests that patch must
    either reset this between cases or patch the module attribute directly.
    """
    try:
        import nodes.code._nodejs as code_mod

        code_mod._client = None  # scaling-branch singleton name
    except (ImportError, AttributeError):
        pass


class _FakeWriteResult(SimpleNamespace):
    """Mimics deepagents WriteResult / EditResult dataclasses."""

    def __init__(self, error: str = "", path: str = "", occurrences: int = 0):
        super().__init__(error=error, path=path, occurrences=occurrences)


class _FakeReadResult(SimpleNamespace):
    """Mimics deepagents ReadResult dataclass (error, file_data)."""

    def __init__(self, error: str | None = None, file_data: dict | None = None):
        super().__init__(error=error, file_data=file_data)


class _FakeExecuteResult(SimpleNamespace):
    def __init__(self, output: str = "", exit_code: int = 0, truncated: bool = False):
        super().__init__(output=output, exit_code=exit_code, truncated=truncated)


class _FakeFileInfo(dict):
    """Dataclass stand-in that `dict(entry)` can iterate."""

    def __iter__(self):
        return iter(self.items())


def _patch_fs_backend(backend: MagicMock):
    """Patch nodes.filesystem._backend.get_backend (shared by all 4 fs plugins)."""
    return patch(
        "nodes.filesystem._backend.get_backend",
        return_value=backend,
    )


def _writable_test_path(name: str) -> Path:
    """Use the writable checkout; the managed sandbox blocks pytest tmp dirs."""
    return Path.cwd() / f".test-{uuid4().hex}-{name}"


# ============================================================================
# pythonExecutor
# ============================================================================


class TestPythonExecutor:
    async def test_happy_path_reads_input_and_sets_output(self, harness):
        # NodeExecutor keys connected_outputs by SOURCE TYPE (not id).
        # See node_executor._get_connected_outputs_with_info (outputs[source_type] = output).
        upstream = {
            "source_A::output_main": {"n": 5},
        }
        nodes = [
            {"id": "py1", "type": "pythonExecutor"},
            {"id": "source_A", "type": "start"},
        ]
        edges = [
            {"source": "source_A", "target": "py1", "sourceHandle": "output-main"},
        ]

        code = "val = input_data.get('start', {}).get('n', 0)\n" "print('doubled', val * 2)\n" "output = {'doubled': val * 2}\n"

        result = await harness.execute(
            "pythonExecutor",
            {"code": code},
            node_id="py1",
            upstream_outputs=upstream,
            nodes=nodes,
            edges=edges,
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["output", "console_output"])
        payload = result["result"]
        assert payload["output"] == {"doubled": 10}
        assert "doubled 10" in payload["console_output"]

    async def test_empty_code_short_circuits(self, harness):
        result = await harness.execute("pythonExecutor", {"code": "   \n\t"})

        harness.assert_envelope(result, success=False)
        assert "no code provided" in result["error"].lower()

    async def test_exception_in_user_code_is_captured(self, harness):
        # Sandbox exposes int() but not Exception class names, so raise via int('boom').
        result = await harness.execute(
            "pythonExecutor",
            {"code": "int('boom')"},
        )

        harness.assert_envelope(result, success=False)
        assert "boom" in result["error"]


# ============================================================================
# javascriptExecutor
# ============================================================================


class TestJavascriptExecutor:
    async def test_happy_path_returns_node_output(self, harness):
        _reset_nodejs_singleton()
        fake_client = MagicMock(name="NodeJSClient")
        fake_client.execute = AsyncMock(
            return_value={
                "success": True,
                "output": {"v": 42},
                "console_output": "running\n",
            }
        )

        with patch(
            "nodes.code._nodejs.get_nodejs_client",
            return_value=fake_client,
        ):
            result = await harness.execute(
                "javascriptExecutor",
                {"code": "output = {v: 42}", "timeout": 15},
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["output", "console_output"])
        payload = result["result"]
        assert payload["output"] == {"v": 42}
        assert "running" in payload["console_output"]

        # timeout: seconds -> milliseconds forwarded to the Node server
        call_kwargs = fake_client.execute.await_args.kwargs
        assert call_kwargs["timeout"] == 15 * 1000
        assert call_kwargs["language"] == "javascript"
        # workspace_dir injected into input_data
        assert "workspace_dir" in call_kwargs["input_data"]

    async def test_empty_code_short_circuits(self, harness):
        result = await harness.execute("javascriptExecutor", {"code": ""})

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_node_server_returns_failure_preserves_console(self, harness):
        _reset_nodejs_singleton()
        fake_client = MagicMock(name="NodeJSClient")
        fake_client.execute = AsyncMock(
            return_value={
                "success": False,
                "error": "SyntaxError: unexpected token",
                "console_output": "partial log\n",
            }
        )

        with patch(
            "nodes.code._nodejs.get_nodejs_client",
            return_value=fake_client,
        ):
            result = await harness.execute("javascriptExecutor", {"code": "oops"})

        harness.assert_envelope(result, success=False)
        assert "syntaxerror" in result["error"].lower()

    async def test_http_exception_wrapped_in_envelope(self, harness):
        _reset_nodejs_singleton()
        fake_client = MagicMock(name="NodeJSClient")
        fake_client.execute = AsyncMock(side_effect=ConnectionRefusedError("Cannot connect to host localhost:3020"))

        with patch(
            "nodes.code._nodejs.get_nodejs_client",
            return_value=fake_client,
        ):
            result = await harness.execute("javascriptExecutor", {"code": "output = 1"})

        harness.assert_envelope(result, success=False)
        assert "localhost:3020" in result["error"]


# ============================================================================
# typescriptExecutor
# ============================================================================


class TestTypescriptExecutor:
    async def test_happy_path_forwards_language_typescript(self, harness):
        _reset_nodejs_singleton()
        fake_client = MagicMock(name="NodeJSClient")
        fake_client.execute = AsyncMock(
            return_value={
                "success": True,
                "output": "typed-output",
                "console_output": "",
            }
        )

        with patch(
            "nodes.code._nodejs.get_nodejs_client",
            return_value=fake_client,
        ):
            result = await harness.execute(
                "typescriptExecutor",
                {"code": "const x: number = 1; output = 'typed-output'"},
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["output"] == "typed-output"
        call_kwargs = fake_client.execute.await_args.kwargs
        assert call_kwargs["language"] == "typescript"

    async def test_empty_code_short_circuits(self, harness):
        result = await harness.execute("typescriptExecutor", {"code": ""})

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_node_server_failure_preserves_console(self, harness):
        _reset_nodejs_singleton()
        fake_client = MagicMock(name="NodeJSClient")
        fake_client.execute = AsyncMock(
            return_value={
                "success": False,
                "error": "TS2304: Cannot find name 'foo'",
                "console_output": "compiling...\n",
            }
        )

        with patch(
            "nodes.code._nodejs.get_nodejs_client",
            return_value=fake_client,
        ):
            result = await harness.execute("typescriptExecutor", {"code": "output = foo"})

        harness.assert_envelope(result, success=False)
        assert "ts2304" in result["error"].lower()


# ============================================================================
# fileRead
# ============================================================================


class TestFileRead:
    async def test_happy_path_returns_content(self, harness):
        # deepagents 0.5.x ``backend.read`` returns a ``ReadResult``
        # dataclass — the plugin must unwrap ``file_data["content"]``
        # instead of leaking the raw object into the output dict
        # (regression: "Object of type ReadResult is not JSON
        # serializable" at node_outputs persistence).
        backend = MagicMock(name="LocalShellBackend")
        backend.read = MagicMock(
            return_value=_FakeReadResult(file_data={"content": "line1\nline2", "encoding": "utf-8"})
        )

        with _patch_fs_backend(backend):
            result = await harness.execute(
                "fileRead",
                {"file_path": "notes.txt", "offset": 0, "limit": 100},
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["content", "file_path"])
        payload = result["result"]
        assert payload["content"] == "line1\nline2"
        assert payload["line_count"] == 2
        # ``normalize_virtual_path`` prepends ``/`` to relative inputs so the
        # path reaches deepagents in its canonical virtual-mode form.
        assert payload["file_path"] == "/notes.txt"
        backend.read.assert_called_once_with("/notes.txt", offset=0, limit=100)
        # The whole envelope must be JSON-serializable — this is exactly
        # what ``database.save_node_output`` needs to persist it.
        import json

        json.dumps(result)

    async def test_backend_error_result_is_user_error(self, harness):
        """A ``ReadResult`` carrying ``error`` is a failed read — it must
        surface as a NodeUserError envelope, not pass as success with a
        raw dataclass payload."""
        backend = MagicMock(name="LocalShellBackend")
        backend.read = MagicMock(return_value=_FakeReadResult(error="File '/missing.txt' not found"))

        with _patch_fs_backend(backend):
            result = await harness.execute("fileRead", {"file_path": "missing.txt"})

        harness.assert_envelope(result, success=False)
        assert "not found" in result["error"].lower()
        assert result.get("error_type") == "NodeUserError"

    async def test_missing_file_path_short_circuits(self, harness):
        # No backend patch: we should short-circuit before reaching it.
        result = await harness.execute("fileRead", {"file_path": ""})

        harness.assert_envelope(result, success=False)
        assert "file_path is required" in result["error"].lower()

    async def test_backend_exception_becomes_error_envelope(self, harness):
        backend = MagicMock(name="LocalShellBackend")
        backend.read = MagicMock(side_effect=FileNotFoundError("No such file: ../../etc/passwd"))

        with _patch_fs_backend(backend):
            result = await harness.execute("fileRead", {"file_path": "../../etc/passwd"})

        harness.assert_envelope(result, success=False)
        # ``normalize_virtual_path`` rejects ``..`` segments before the
        # backend is even called, so the error comes from deepagents'
        # ``validate_path`` helper, not the (unreached) FileNotFoundError.
        assert "path traversal not allowed" in result["error"].lower()
        # LLM-correctable input error: NodeUserError path (single WARN,
        # no traceback) carrying actionable sandbox guidance — not the
        # generic-exception path that leaks deepagents' bare ValueError.
        assert result.get("error_type") == "NodeUserError"
        assert "workspace" in result["error"].lower()

    def test_normalize_virtual_path_traversal_raises_user_error(self):
        """Central contract on the shared helper — all three filesystem
        plugins (fileRead / fileModify / fsSearch) call it outside their
        operation try-blocks, so the conversion to NodeUserError must
        happen inside the helper itself."""
        import pytest

        from nodes.filesystem._backend import normalize_virtual_path
        from services.plugin import NodeUserError

        with pytest.raises(NodeUserError) as excinfo:
            normalize_virtual_path("../../package.json")
        msg = str(excinfo.value).lower()
        assert "path traversal not allowed" in msg
        assert "workspace" in msg

        # Valid flavours still normalise (no raise).
        assert normalize_virtual_path("reports/data.csv") == "/reports/data.csv"
        assert normalize_virtual_path("C:\\tmp\\x.txt") == "/tmp/x.txt"


# ============================================================================
# fileModify
# ============================================================================


class TestFileModify:
    async def test_write_happy_path(self, harness):
        backend = MagicMock(name="LocalShellBackend")
        destination = _writable_test_path("hello.txt")
        backend._resolve_path.return_value = destination

        try:
            with _patch_fs_backend(backend):
                result = await harness.execute(
                    "fileModify",
                    {
                        "operation": "write",
                        "file_path": "hello.txt",
                        "content": "hi there",
                    },
                )

            harness.assert_envelope(result, success=True)
            harness.assert_output_shape(result, ["operation", "file_path"])
            assert result["result"]["operation"] == "write"
            assert result["result"]["file_path"] == "/hello.txt"
            assert destination.read_text(encoding="utf-8") == "hi there"
        finally:
            destination.unlink(missing_ok=True)

    async def test_edit_happy_path_returns_occurrences(self, harness):
        backend = MagicMock(name="LocalShellBackend")
        destination = _writable_test_path("README.md")
        destination.write_text("foo foo foo", encoding="utf-8")
        backend._resolve_path.return_value = destination

        try:
            with _patch_fs_backend(backend):
                result = await harness.execute(
                    "fileModify",
                    {
                        "operation": "edit",
                        "file_path": "README.md",
                        "old_string": "foo",
                        "new_string": "bar",
                        "replace_all": True,
                    },
                )

            harness.assert_envelope(result, success=True)
            harness.assert_output_shape(result, ["operation", "file_path", "occurrences"])
            assert result["result"]["occurrences"] == 3
            assert destination.read_text(encoding="utf-8") == "bar bar bar"
        finally:
            destination.unlink(missing_ok=True)

    async def test_edit_missing_old_string_short_circuits(self, harness):
        backend = MagicMock(name="LocalShellBackend")

        with _patch_fs_backend(backend):
            result = await harness.execute(
                "fileModify",
                {
                    "operation": "edit",
                    "file_path": "x",
                    "old_string": "",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "old_string is required" in result["error"].lower()
        backend.edit.assert_not_called() if hasattr(backend, "edit") else None

    async def test_backend_reports_error_in_result(self, harness):
        backend = MagicMock(name="LocalShellBackend")
        destination = _writable_test_path("x.md")
        destination.write_text("foo foo", encoding="utf-8")
        backend._resolve_path.return_value = destination

        try:
            with _patch_fs_backend(backend):
                result = await harness.execute(
                    "fileModify",
                    {
                        "operation": "edit",
                        "file_path": "x.md",
                        "old_string": "foo",
                        "new_string": "bar",
                        "replace_all": False,
                    },
                )

            harness.assert_envelope(result, success=False)
            assert "2 times" in result["error"]
        finally:
            destination.unlink(missing_ok=True)

    async def test_same_path_mutations_are_serialized(self, harness):
        backend = MagicMock(name="LocalShellBackend")
        destination = _writable_test_path("shared.txt")
        backend._resolve_path.return_value = destination
        state = {"active": 0, "maximum": 0}
        guard = threading.Lock()

        def slow_atomic_write(path, content):
            with guard:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.03)
            path.write_text(content, encoding="utf-8")
            with guard:
                state["active"] -= 1

        try:
            with _patch_fs_backend(backend), patch(
                "nodes.filesystem._backend.atomic_write_text",
                side_effect=slow_atomic_write,
            ):
                first, second = await asyncio.gather(
                    harness.execute(
                        "fileModify",
                        {"operation": "write", "file_path": "shared.txt", "content": "one"},
                    ),
                    harness.execute(
                        "fileModify",
                        {"operation": "write", "file_path": "shared.txt", "content": "two"},
                    ),
                )

            harness.assert_envelope(first, success=True)
            harness.assert_envelope(second, success=True)
            assert state["maximum"] == 1
            assert destination.read_text(encoding="utf-8") in {"one", "two"}
        finally:
            destination.unlink(missing_ok=True)

    async def test_cancellation_keeps_path_lock_until_write_finishes(self, harness):
        from nodes.filesystem._backend import get_path_lock

        backend = MagicMock(name="LocalShellBackend")
        destination = _writable_test_path("cancelled-write.txt")
        backend._resolve_path.return_value = destination
        entered = threading.Event()
        release = threading.Event()

        def blocking_atomic_write(path, content):
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("test write was not released")
            path.write_text(content, encoding="utf-8", newline="")

        try:
            with _patch_fs_backend(backend), patch(
                "nodes.filesystem._backend.atomic_write_text",
                side_effect=blocking_atomic_write,
            ):
                operation = asyncio.create_task(
                    harness.execute(
                        "fileModify",
                        {"operation": "write", "file_path": "cancelled-write.txt", "content": "done"},
                    )
                )
                await _wait_for_thread_event(entered)
                path_lock = get_path_lock(destination)

                operation.cancel()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert operation.done() is False
                assert path_lock.locked() is True

                release.set()
                result = await operation

            harness.assert_envelope(result, success=False)
            assert "cancel" in (result.get("error") or "").lower()
            assert path_lock.locked() is False
            assert destination.read_bytes() == b"done"
        finally:
            release.set()
            destination.unlink(missing_ok=True)

    def test_atomic_write_preserves_lf_bytes(self):
        from nodes.filesystem._backend import atomic_write_text

        destination = _writable_test_path("line-endings.txt")
        try:
            destination.write_bytes(b"old\n")
            atomic_write_text(destination, "first\nsecond\n")
            assert destination.read_bytes() == b"first\nsecond\n"
        finally:
            destination.unlink(missing_ok=True)

    def test_atomic_write_preserves_existing_mode(self):
        from nodes.filesystem._backend import atomic_write_text

        destination = _writable_test_path("mode.txt")
        try:
            destination.write_text("old", encoding="utf-8")
            if os.name != "nt":
                destination.chmod(0o754)
            expected_mode = stat.S_IMODE(destination.stat().st_mode)
            atomic_write_text(destination, "new")
            assert stat.S_IMODE(destination.stat().st_mode) == expected_mode
        finally:
            destination.unlink(missing_ok=True)

    async def test_unknown_operation_returns_error(self, harness):
        backend = MagicMock(name="LocalShellBackend")

        with _patch_fs_backend(backend):
            result = await harness.execute(
                "fileModify",
                {"operation": "delete", "file_path": "x"},
            )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()


# ============================================================================
# shell
# ============================================================================


class TestShell:
    async def test_happy_path_returns_stdout_and_exit_code(self, harness):
        backend = MagicMock(name="LocalShellBackend")
        backend.execute = MagicMock(return_value=_FakeExecuteResult(output="hello world\n", exit_code=0, truncated=False))

        with _patch_fs_backend(backend):
            result = await harness.execute(
                "shell",
                {"command": "echo hello world", "timeout": 10},
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["stdout", "exit_code", "truncated", "command"])
        payload = result["result"]
        assert payload["stdout"] == "hello world\n"
        assert payload["exit_code"] == 0
        assert payload["truncated"] is False
        assert payload["command"] == "echo hello world"
        backend.execute.assert_called_once_with("echo hello world", timeout=10)

    async def test_empty_command_short_circuits(self, harness):
        result = await harness.execute("shell", {"command": ""})

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_timeout_surfaces_exit_124(self, harness):
        backend = MagicMock(name="LocalShellBackend")
        backend.execute = MagicMock(return_value=_FakeExecuteResult(output="partial...", exit_code=124, truncated=True))

        with _patch_fs_backend(backend):
            result = await harness.execute(
                "shell",
                {"command": "sleep 999", "timeout": 1},
            )

        # Per doc: non-zero exit still returns success=True at the envelope
        # level. Users inspect exit_code.
        harness.assert_envelope(result, success=True)
        assert result["result"]["exit_code"] == 124
        assert result["result"]["truncated"] is True

    async def test_backend_exception_becomes_error_envelope(self, harness):
        backend = MagicMock(name="LocalShellBackend")
        backend.execute = MagicMock(side_effect=RuntimeError("backend blew up"))

        with _patch_fs_backend(backend):
            result = await harness.execute("shell", {"command": "ls"})

        harness.assert_envelope(result, success=False)
        assert "backend blew up" in result["error"]


# ============================================================================
# fsSearch
# ============================================================================


class TestFsSearch:
    async def test_ls_mode_returns_entries(self, harness):
        entries = [
            _FakeFileInfo({"name": "a.py", "is_dir": False, "size": 10}),
            _FakeFileInfo({"name": "sub", "is_dir": True, "size": 0}),
        ]
        backend = MagicMock(name="LocalShellBackend")
        backend.ls_info = MagicMock(return_value=entries)

        with _patch_fs_backend(backend):
            result = await harness.execute("fsSearch", {"mode": "ls", "path": "."})

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["path", "entries", "count"])
        assert result["result"]["count"] == 2
        assert result["result"]["entries"][0]["name"] == "a.py"

    async def test_glob_requires_pattern(self, harness):
        backend = MagicMock(name="LocalShellBackend")

        with _patch_fs_backend(backend):
            result = await harness.execute("fsSearch", {"mode": "glob", "path": ".", "pattern": ""})

        harness.assert_envelope(result, success=False)
        assert "pattern is required" in result["error"].lower()

    async def test_glob_happy_path(self, harness):
        matches = [_FakeFileInfo({"name": "x.py", "path": "src/x.py"})]
        backend = MagicMock(name="LocalShellBackend")
        backend.glob_info = MagicMock(return_value=matches)

        with _patch_fs_backend(backend):
            result = await harness.execute(
                "fsSearch",
                {"mode": "glob", "path": "src", "pattern": "**/*.py"},
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["path", "pattern", "matches", "count"])
        assert result["result"]["count"] == 1
        backend.glob_info.assert_called_once_with("**/*.py", path="/src")

    async def test_grep_returns_string_error_as_error_envelope(self, harness):
        # Per doc: grep_raw returns a str on error, list on success.
        backend = MagicMock(name="LocalShellBackend")
        backend.grep_raw = MagicMock(return_value="invalid regex: unbalanced parenthesis")

        with _patch_fs_backend(backend):
            result = await harness.execute(
                "fsSearch",
                {
                    "mode": "grep",
                    "path": ".",
                    "pattern": "foo(",
                    "file_filter": "*.py",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "invalid regex" in result["error"].lower()

    async def test_unknown_mode_returns_error(self, harness):
        backend = MagicMock(name="LocalShellBackend")

        with _patch_fs_backend(backend):
            result = await harness.execute("fsSearch", {"mode": "teleport", "path": "."})

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()


# ============================================================================
# processManager
# ============================================================================


def _fake_process_service():
    svc = MagicMock(name="ProcessService")
    svc.start = AsyncMock(
        return_value={
            "success": True,
            "result": {
                "name": "my-server",
                "command": "python -m http.server 8080",
                "pid": 12345,
                "status": "running",
                "started_at": "2026-04-15T00:00:00",
                "exit_code": None,
                "working_directory": "/workspace/proc_node",
                "stdout_lines": 0,
                "stderr_lines": 0,
                "log_dir": "/workspace/proc_node/.processes/my-server",
            },
        }
    )
    svc.stop = AsyncMock(
        return_value={
            "success": True,
            "result": {"name": "my-server", "status": "stopped", "exit_code": 0},
        }
    )
    svc.restart = AsyncMock(return_value={"success": True, "result": {"name": "my-server", "status": "running"}})
    svc.send_input = AsyncMock(return_value={"success": True, "result": {"sent": "hello"}})
    svc.list_processes = MagicMock(
        return_value=[
            {"name": "my-server", "status": "running", "pid": 12345},
        ]
    )
    svc.get_output = MagicMock(
        return_value={
            "lines": ["listening on :8080"],
            "total": 1,
            "file": "/workspace/proc_node/.processes/my-server/stdout.log",
        }
    )
    return svc


def _patch_process_service(svc):
    return patch(
        "services.process_service.get_process_service",
        return_value=svc,
    )


class TestProcessManager:
    async def test_start_happy_path(self, harness):
        svc = _fake_process_service()

        with _patch_process_service(svc):
            result = await harness.execute(
                "processManager",
                {
                    "operation": "start",
                    "name": "my-server",
                    "command": "python -m http.server 8080",
                    "working_directory": "",
                },
                node_id="proc_node",
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]["result"]
        assert payload["name"] == "my-server"
        assert payload["status"] == "running"
        assert payload["pid"] == 12345

        # Handler passes workflow_id from context and per-node workspace subdir
        svc.start.assert_awaited_once()
        call_kwargs = svc.start.await_args.kwargs
        assert call_kwargs["name"] == "my-server"
        assert call_kwargs["command"] == "python -m http.server 8080"

    async def test_stop_returns_stopped_status(self, harness):
        svc = _fake_process_service()

        with _patch_process_service(svc):
            result = await harness.execute(
                "processManager",
                {"operation": "stop", "name": "my-server"},
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["result"]["status"] == "stopped"
        svc.stop.assert_awaited_once()

    async def test_list_returns_processes_array(self, harness):
        svc = _fake_process_service()

        with _patch_process_service(svc):
            result = await harness.execute("processManager", {"operation": "list"})

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["processes"])
        assert len(result["result"]["processes"]) == 1
        assert result["result"]["processes"][0]["name"] == "my-server"

    async def test_get_output_returns_lines(self, harness):
        svc = _fake_process_service()

        with _patch_process_service(svc):
            result = await harness.execute(
                "processManager",
                {
                    "operation": "get_output",
                    "name": "my-server",
                    # Handler coerces 'None' and empty strings via _clean_arg
                    "stream": "stdout",
                    "tail": 50,
                },
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["lines", "total", "file"])
        payload = result["result"]
        assert payload["total"] == 1
        assert "listening" in payload["lines"][0]
        svc.get_output.assert_called_once()

    async def test_send_input_forwards_text(self, harness):
        svc = _fake_process_service()

        with _patch_process_service(svc):
            result = await harness.execute(
                "processManager",
                {
                    "operation": "send_input",
                    "name": "my-server",
                    "text": "hello",
                },
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["result"]["sent"] == "hello"
        svc.send_input.assert_awaited_once()

    async def test_unknown_operation_returns_error(self, harness):
        svc = _fake_process_service()

        with _patch_process_service(svc):
            result = await harness.execute("processManager", {"operation": "teleport"})

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_start_failure_from_service_surfaces(self, harness):
        svc = _fake_process_service()
        svc.start = AsyncMock(
            return_value={
                "success": False,
                "error": "Destructive commands blocked in process_manager.",
            }
        )

        with _patch_process_service(svc):
            result = await harness.execute(
                "processManager",
                {
                    "operation": "start",
                    "name": "bad",
                    "command": "rm -rf /",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "destructive" in result["error"].lower()
