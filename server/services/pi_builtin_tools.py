"""Execute one Pi built-in tool inside an admitted node activity.

The maintenance driver never performs these effects. Each invocation owns a
separate Bun process, a bounded protocol exchange and cancellation cleanup.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any

from services.plugin import NodeUserError
from services.process_environment import without_onepassword_environment


TOOLS = frozenset({"read", "write", "edit", "bash", "powershell", "grep", "find", "ls"})
FRAME_LIMIT = 16 * 1024 * 1024
STARTUP_GRACE_SECONDS = 5.0
ABORT_GRACE_SECONDS = 2.0
KILL_GRACE_SECONDS = 2.0


def _environment(host: Path) -> dict[str, str]:
    from services.pi_runtime import pi_package_dir

    # Shell tools retain the ordinary command environment, including Windows
    # ProgramFiles for Git Bash discovery. Do not inherit JavaScript loaders
    # into the trusted protocol host or 1Password bootstrap credentials.
    environment = without_onepassword_environment()
    for key in list(environment):
        if key.upper() in {"NODE_OPTIONS", "NODE_PATH", "BUN_OPTIONS"}:
            environment.pop(key)
    environment.update(PI_OFFLINE="1", PI_PACKAGE_DIR=str(pi_package_dir(host)), NODE_PATH="")
    return environment


async def _discard_stderr(stream: asyncio.StreamReader) -> None:
    # A failed tool may print arbitrary command output or secrets here. Drain
    # incrementally without retaining it in memory or putting it in errors.
    while await stream.read(65536):
        pass


def _kill_owned_process(proc: asyncio.subprocess.Process) -> None:
    import psutil
    from services._supervisor.util import kill_tree

    if proc.returncode is not None:
        return
    try:
        kill_tree(proc.pid)
    except (OSError, RuntimeError, psutil.Error):
        # An inaccessible descendant must not prevent killing our own host.
        pass
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except (ProcessLookupError, PermissionError):
                pass


async def _close_host(proc: asyncio.subprocess.Process) -> None:
    """Bound every wait, including cleanup after a descendant retains pipes."""
    if proc.returncode is not None:
        return
    try:
        async with asyncio.timeout(ABORT_GRACE_SECONDS):
            if proc.stdin is not None:
                proc.stdin.write(b'{"type":"abort"}\n')
                await proc.stdin.drain()
                proc.stdin.close()
            await proc.wait()
            return
    except (TimeoutError, ConnectionError, BrokenPipeError):
        pass
    _kill_owned_process(proc)
    try:
        await asyncio.wait_for(proc.wait(), KILL_GRACE_SECONDS)
    except (TimeoutError, ProcessLookupError):
        # asyncio's process wait can itself wait on an inherited pipe after
        # process exit. Closing its transport releases those pipes; there is
        # no public Process.close() in asyncio.
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            transport.close()


async def _finish_cleanup(proc: asyncio.subprocess.Process, stderr_task: asyncio.Task) -> None:
    try:
        await _close_host(proc)
    finally:
        stderr_task.cancel()
        await asyncio.gather(stderr_task, return_exceptions=True)
        # A process can already have an exit code while inherited pipe handles
        # keep its transport alive. Explicitly release those handles as well.
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            transport.close()


async def _exchange(proc: asyncio.subprocess.Process, request: bytes) -> dict[str, Any]:
    if proc.stdin is None or proc.stdout is None:
        raise NodeUserError("Pi tool runtime did not provide its protocol streams.")
    proc.stdin.write(request)
    await proc.stdin.drain()
    try:
        line = await proc.stdout.readline()
    except ValueError as exc:
        raise NodeUserError("Pi tool output exceeds the 16 MiB transport limit.") from exc
    if not line:
        raise NodeUserError("Pi tool runtime exited before returning a result.")
    if len(line) > FRAME_LIMIT or not line.endswith(b"\n"):
        raise NodeUserError("Pi tool runtime returned an incomplete or oversized result.")
    try:
        response = json.loads(line)
    except (ValueError, UnicodeError) as exc:
        raise NodeUserError("Pi tool runtime returned invalid JSON.") from exc
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        raise NodeUserError("Pi tool runtime returned an invalid response.")
    if not response["ok"]:
        error = response.get("error")
        raise NodeUserError(error[:16000] if isinstance(error, str) and error else "Pi tool execution failed.")
    result = response.get("result")
    if not isinstance(result, dict):
        raise NodeUserError("Pi tool runtime returned an invalid tool result.")
    # Result delivery does not prove that the host has disposed its resources.
    # EOF asks the single-invocation host to stop; the caller bounds its wait.
    proc.stdin.close()
    return result


async def execute_pi_tool(
    tool: str,
    args: dict,
    workspace: str | Path,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    """Run the SDK tool and retain its native content, details and error flag."""
    from core.js_runtime import require_bun
    from services.pi_runtime import resolve_pi_tool_host
    from temporalio.exceptions import ApplicationError

    if tool not in TOOLS:
        raise NodeUserError(f"Unknown Pi tool: {tool}")
    if not isinstance(args, dict):
        raise NodeUserError("Pi tool arguments must be an object.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise NodeUserError("Pi tool timeout must be a positive finite number.")
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise NodeUserError("Pi tool workspace does not exist.")
    arguments = dict(args)
    if tool in {"bash", "powershell"}:
        # Pi itself has no default shell timeout. The node's admitted deadline
        # is authoritative even if a caller bypasses its Pydantic parameters.
        arguments["timeout"] = timeout
    try:
        request = (json.dumps({"protocol": 1, "tool": tool, "cwd": str(root), "args": arguments},
                              ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise NodeUserError("Pi tool arguments must be finite JSON values.") from exc
    if len(request) > FRAME_LIMIT:
        raise NodeUserError("Pi tool input exceeds the 16 MiB transport limit.")
    try:
        host = resolve_pi_tool_host()
        bun = require_bun("Pi tool nodes")
    except (ApplicationError, RuntimeError) as exc:
        raise NodeUserError(str(exc)) from exc
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    proc = None
    stderr_task = None
    spawn_task = asyncio.create_task(asyncio.create_subprocess_exec(
        bun, str(host), cwd=str(root), env=_environment(host),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        limit=FRAME_LIMIT + 1, **kwargs,
    ))
    try:
        async with asyncio.timeout(timeout + STARTUP_GRACE_SECONDS):
            # Shield creation so cancellation cannot lose the newly started
            # process before its handle is returned to our cleanup path.
            proc = await asyncio.shield(spawn_task)
            stderr_task = asyncio.create_task(_discard_stderr(proc.stderr))
            result = await _exchange(proc, request)
            await asyncio.wait_for(proc.wait(), ABORT_GRACE_SECONDS)
            if proc.returncode != 0:
                raise NodeUserError("Pi tool runtime exited unsuccessfully after returning its result.")
            return result
    except TimeoutError as exc:
        raise NodeUserError(f"Pi {tool} exceeded its {timeout:g}-second execution deadline.") from exc
    except (OSError, ConnectionError) as exc:
        raise NodeUserError(f"Pi tool runtime could not run ({type(exc).__name__}).") from exc
    finally:
        if proc is None:
            # Process creation is short but may complete during cancellation.
            # Keep its eventual handle owned even if it exceeds the grace.
            try:
                proc = await asyncio.wait_for(asyncio.shield(spawn_task), ABORT_GRACE_SECONDS)
            except (TimeoutError, OSError):
                def reap_late_spawn(task):
                    if not task.cancelled() and task.exception() is None:
                        _kill_owned_process(task.result())
                spawn_task.add_done_callback(reap_late_spawn)
        if proc is not None:
            if stderr_task is None:
                stderr_task = asyncio.create_task(_discard_stderr(proc.stderr))
            cleanup = asyncio.create_task(_finish_cleanup(proc, stderr_task))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # A second cancellation must still await bounded cleanup.
                await cleanup
                raise
