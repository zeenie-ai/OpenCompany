"""Cross-platform process manager for long-running subprocesses.

Manages lifecycle (start/stop/restart), streams stdout/stderr to the
Terminal tab via broadcast_terminal_log(), and persists output to temp
log files so AI agents can fetch output selectively.

Uses stdlib asyncio.create_subprocess_exec; process-tree termination is
delegated to ``services._supervisor.util.kill_tree`` (the canonical
psutil-backed helper shared with browser_service and the supervisor base).
"""

import asyncio
import os
import shlex
import shutil
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.ansi import strip_ansi
from core.logging import get_logger
from services._supervisor.util import kill_tree

logger = get_logger(__name__)

MAX_PROCESSES = 10  # Default limit, configurable via Settings panel


# (stream_name, line) -> awaitable. Invoked once per decoded stdout/stderr line.
LineHandler = Callable[[str, str], Awaitable[None]]


@dataclass
class ManagedProcess:
    name: str
    command: str
    argv: List[str]
    pid: int
    status: str  # running, stopped, error
    started_at: str
    workflow_id: str
    working_directory: str
    process: asyncio.subprocess.Process
    log_dir: Path  # temp directory for stdout.log / stderr.log
    stdout_task: Optional[asyncio.Task] = None
    stderr_task: Optional[asyncio.Task] = None
    exit_code: Optional[int] = None
    stdout_lines: int = 0
    stderr_lines: int = 0
    ports: tuple[int, ...] = ()
    extra_env: Optional[Dict[str, str]] = None
    stopped_at: Optional[str] = None
    # Optional per-line callback: framework-level subscribers (e.g. the
    # generalised event source `DaemonEventSource`) install this to ingest
    # the daemon's stdout/stderr without re-tailing the log files we already
    # write for the Terminal tab.
    line_handler: Optional[LineHandler] = None


class ProcessService:
    """Singleton managing long-running subprocesses per workflow.

    Output is written beneath the workflow workspace in
    ``.processes/<name>/stdout.log`` and ``stderr.log``. AI agents read it
    via get_output() with tail/offset.
    Files are cleaned up on stop or shutdown.
    """

    def __init__(self) -> None:
        self._processes: Dict[tuple, ManagedProcess] = {}
        self._broadcaster = None
        self.max_processes: int = MAX_PROCESSES
        # Port preflight and subprocess creation must be one critical section.
        # Without this, two concurrent tool calls can both observe a free port.
        self._start_lock = asyncio.Lock()

    def set_broadcaster(self, broadcaster) -> None:
        self._broadcaster = broadcaster

    def _key(self, workflow_id: str, name: str) -> tuple:
        return (workflow_id, name)

    async def start(
        self,
        name: str,
        command: str,
        workflow_id: str = "default",
        working_directory: str = "",
        *,
        line_handler: Optional[LineHandler] = None,
        ports: Optional[List[int]] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Start a long-running process.

        ``line_handler``: optional ``async (stream_name, line) -> None``
        callback invoked for every decoded stdout/stderr line, AFTER the
        line has been written to the log file and broadcast to the
        Terminal tab. Used by ``DaemonEventSource`` to parse subprocess
        output into typed events without re-tailing the log files.
        """
        if not command:
            return {"success": False, "error": "command is required"}

        # Block destructive commands -- file ops should use the sandboxed shell node
        cmd_lower = command.lower().strip()
        blocked = (
            "rm ",
            "rm\t",
            "rmdir",
            "del ",
            "rd ",
            "remove-item",
            "format ",
            "mkfs",
            "dd if=",
            "shred",
            "> /dev/",
            "chmod 777",
            "chmod -r",
        )
        if any(cmd_lower.startswith(b) or f" {b}" in f" {cmd_lower}" for b in blocked):
            return {
                "success": False,
                "error": "Destructive commands blocked in process_manager. " "Use shell_execute for file operations (sandboxed, no PATH).",
            }

        name = name or f"proc_{id(command) % 100000}"
        key = self._key(workflow_id, name)

        argv = shlex.split(command)
        # Resolve argv[0] to an absolute path. Canonical idiom in this
        # codebase (browser_service, claude_code_service, claude_oauth,
        # himalaya_service): shutil.which honours PATHEXT on Windows so
        # `npm` -> `C:\...\npm.cmd`. Without this, asyncio.create_subprocess_exec
        # fails with `WinError 2` because CreateProcessW does not apply
        # PATHEXT to a bare argv[0]. CreateProcessW launches .cmd/.bat
        # directly when given an absolute path, so no `cmd /c` wrap is
        # needed.
        resolved = shutil.which(argv[0]) if argv else None
        if resolved is None:
            return {
                "success": False,
                "error": (f"Command not found: '{argv[0] if argv else ''}'. " "Check spelling or ensure the binary is on PATH."),
            }
        argv[0] = resolved
        env = {**os.environ, **(extra_env or {}), "PYTHONUNBUFFERED": "1"}
        requested_ports = self._requested_ports(argv, ports or [], extra_env or {})
        from core.config import Settings
        from core.paths import daemons_dir

        workspace_base = Path(Settings().workspace_base_resolved).resolve()
        daemon_base = daemons_dir().resolve()

        if not working_directory:
            working_directory = str(workspace_base / "default")
            os.makedirs(working_directory, exist_ok=True)
        cwd = working_directory

        # Guardrail: cwd must resolve inside one of the OpenCompany-controlled
        # state roots — workspaces (per-workflow scratch for workflow nodes
        # like processManager / code executors) OR daemons (framework event
        # sources like `stripe listen`). Both are siblings under DATA_DIR.
        cwd_resolved = Path(cwd).resolve()
        is_under_workspace = cwd_resolved.is_relative_to(workspace_base)
        is_under_daemons = cwd_resolved.is_relative_to(daemon_base)
        if not (is_under_workspace or is_under_daemons):
            return {
                "success": False,
                "error": (
                    f"Working directory must be inside workspace ({workspace_base}) "
                    f"or daemons ({daemon_base})."
                ),
            }

        # Per-process log directory. For workflow-scoped processes this
        # lands at ``{workspace}/.processes/{name}/`` (operator can find
        # logs alongside the workflow's other files); for daemons it
        # lands at ``{daemons_dir}/.processes/{name}/``. Either way it
        # holds stdout.log + stderr.log.
        log_dir = Path(cwd) / ".processes" / name

        async with self._start_lock:
            running = sum(1 for m in self._processes.values() if m.status == "running")
            if key not in self._processes and running >= self.max_processes:
                return {"success": False, "error": f"Process limit reached ({self.max_processes}). Stop a process first."}

            # Same-name replacement is allowed, but it must fully stop before
            # the port preflight. Differently named/external owners are never killed.
            if key in self._processes and self._processes[key].status == "running":
                await self.stop(name, workflow_id)

            collisions: Dict[int, List[int]] = {}
            for managed_process in self._processes.values():
                if managed_process.status != "running":
                    continue
                for port in set(requested_ports).intersection(managed_process.ports):
                    collisions.setdefault(port, []).append(managed_process.pid)
            for port, pids in self._occupied_ports(requested_ports).items():
                collisions.setdefault(port, []).extend(pid for pid in pids if pid not in collisions.get(port, []))
            if collisions:
                detail = ", ".join(
                    f"{port} (PID{'s' if len(pids) != 1 else ''} {', '.join(map(str, pids)) or 'unknown'})"
                    for port, pids in collisions.items()
                )
                return {
                    "success": False,
                    "code": "PORT_IN_USE",
                    "error": f"Cannot start '{name}': requested port(s) already in use: {detail}.",
                    "ports": list(collisions),
                    "owners": collisions,
                }

            # Do not create or truncate logs until all admission checks pass.
            log_dir.mkdir(parents=True, exist_ok=True)
            for f in ("stdout.log", "stderr.log"):
                (log_dir / f).write_text("")

            logger.info("[Process] Starting: %s (name=%s, cwd=%s, ports=%s, logs=%s)", command[:200], name, cwd, requested_ports, log_dir)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
            except Exception as e:
                logger.error("[Process] Failed to start: %s -> %s", command[:100], e)
                return {"success": False, "error": str(e)}

            # Publish ownership before releasing the admission lock. A second
            # concurrent start must see this process even if it has not bound
            # its listener socket yet.
            managed = ManagedProcess(
                name=name,
                command=command,
                argv=argv,
                pid=proc.pid,
                status="running",
                started_at=datetime.now().isoformat(),
                workflow_id=workflow_id,
                working_directory=cwd,
                process=proc,
                log_dir=log_dir,
                line_handler=line_handler,
                ports=tuple(requested_ports),
                extra_env=dict(extra_env or {}),
            )
            managed.stdout_task = asyncio.create_task(
                self._read_stream(managed, proc.stdout, "stdout"), name=f"proc-stdout-{name}",
            )
            managed.stderr_task = asyncio.create_task(
                self._read_stream(managed, proc.stderr, "stderr"), name=f"proc-stderr-{name}",
            )
            self._processes[key] = managed

        logger.info("[Process] Started: %s (pid=%d)", name, proc.pid)
        return {"success": True, "result": self._info(managed)}

    @staticmethod
    def _requested_ports(argv: List[str], declared: List[int], env: Dict[str, str]) -> List[int]:
        """Return explicit and conventionally-declared listener ports.

        Explicit ``ports`` are authoritative. Common ``--port``/``-p`` CLI
        forms and PORT-like environment variables are included for existing
        workflows. Positional ports remain opt-in because guessing arbitrary
        numeric arguments would create false positives.
        """
        found = {int(port) for port in declared if 1 <= int(port) <= 65535}
        for index, token in enumerate(argv):
            value: Optional[str] = None
            if token in {"--port", "-p"} and index + 1 < len(argv):
                value = argv[index + 1]
            elif token.startswith("--port="):
                value = token.split("=", 1)[1]
            if value and value.isdigit() and 1 <= int(value) <= 65535:
                found.add(int(value))
        for key, value in env.items():
            if (key.upper() == "PORT" or key.upper().endswith("_PORT")) and str(value).isdigit():
                port = int(value)
                if 1 <= port <= 65535:
                    found.add(port)
        return sorted(found)

    @staticmethod
    def _occupied_ports(ports: List[int]) -> Dict[int, List[int]]:
        """Resolve listening owners without mutating unrelated processes."""
        if not ports:
            return {}
        wanted = set(ports)
        owners: Dict[int, set[int]] = {port: set() for port in ports}
        try:
            import psutil

            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port in wanted and conn.status == psutil.CONN_LISTEN:
                    if conn.pid:
                        owners[conn.laddr.port].add(conn.pid)
                    else:
                        owners[conn.laddr.port].add(0)
        except Exception as exc:
            # A bind probe still reliably detects occupancy when process-owner
            # enumeration is restricted by the OS.
            logger.debug("[Process] Port owner enumeration unavailable: %s", exc)
            for port in ports:
                probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    # This is an occupancy probe, not a network service. Keep
                    # it local so the fallback never exposes a listener on
                    # every interface (and still detects wildcard listeners).
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    owners[port].add(0)
                finally:
                    probe.close()
        return {port: sorted(pid for pid in pids if pid) for port, pids in owners.items() if pids}

    async def stop(self, name: str, workflow_id: str = "default") -> Dict[str, Any]:
        """Stop a running process by killing its process tree."""
        key = self._key(workflow_id, name)
        managed = self._processes.get(key)
        if not managed:
            return {"success": False, "error": f"Process '{name}' not found"}

        if managed.status != "running":
            return {"success": True, "result": self._info(managed)}

        logger.info("[Process] Stopping: %s (pid=%d)", name, managed.pid)
        kill_tree(managed.pid)
        managed.status = "stopped"
        managed.stopped_at = datetime.now().isoformat()

        for task in (managed.stdout_task, managed.stderr_task):
            if task and not task.done():
                task.cancel()

        try:
            managed.exit_code = await asyncio.wait_for(managed.process.wait(), timeout=3)
        except asyncio.TimeoutError:
            managed.exit_code = -1

        logger.info("[Process] Stopped: %s (exit=%s)", name, managed.exit_code)
        result = self._info(managed)

        # Schedule cleanup after 60s to allow output reading
        asyncio.get_event_loop().call_later(60, lambda: self._cleanup_completed(workflow_id, name))

        return {"success": True, "result": result}

    async def restart(self, name: str, workflow_id: str = "default") -> Dict[str, Any]:
        """Restart a process with the same command."""
        key = self._key(workflow_id, name)
        managed = self._processes.get(key)
        if not managed:
            return {"success": False, "error": f"Process '{name}' not found"}

        command = managed.command
        cwd = managed.working_directory
        ports = list(managed.ports)
        extra_env = dict(managed.extra_env or {})
        await self.stop(name, workflow_id)
        return await self.start(name, command, workflow_id, cwd, ports=ports, extra_env=extra_env)

    async def send_input(self, name: str, workflow_id: str, text: str) -> Dict[str, Any]:
        """Write text to a process's stdin."""
        key = self._key(workflow_id, name)
        managed = self._processes.get(key)
        if not managed:
            return {"success": False, "error": f"Process '{name}' not found"}
        if managed.status != "running":
            return {"success": False, "error": f"Process '{name}' is {managed.status}"}

        stdin = managed.process.stdin
        if not stdin:
            return {"success": False, "error": "Process has no stdin"}

        data = text if text.endswith("\n") else text + "\n"
        stdin.write(data.encode())
        await stdin.drain()

        logger.info("[Process] Sent input to %s: %s", name, text[:100])
        return {"success": True, "result": {"sent": text}}

    def list_processes(self, workflow_id: str = "default") -> List[Dict[str, Any]]:
        """List all processes for a workflow."""
        return [self._info(m) for (wid, _), m in self._processes.items() if wid == workflow_id]

    def get_output(
        self,
        name: str,
        workflow_id: str = "default",
        stream: str = "stdout",
        tail: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Read output from a process's log file.

        Args:
            stream: 'stdout' or 'stderr'
            tail: Number of lines from the end (0 = all lines)
            offset: Skip first N lines (only when tail=0)
        """
        key = self._key(workflow_id, name)
        managed = self._processes.get(key)
        if not managed:
            return {"lines": [], "total": 0, "file": ""}

        log_file = managed.log_dir / f"{stream}.log"
        if not log_file.exists():
            return {"lines": [], "total": 0, "file": str(log_file)}

        all_lines = log_file.read_text(errors="replace").splitlines()
        total = len(all_lines)

        if tail > 0:
            lines = all_lines[-tail:]
        else:
            lines = all_lines[offset:]

        return {"lines": lines, "total": total, "file": str(log_file)}

    def _cleanup_completed(self, workflow_id: str, name: str) -> None:
        """Remove log files and process entry for a completed process.

        Called automatically 60s after process exits, giving time to read output.
        Also cleans the parent .processes/ dir if empty.
        """
        key = self._key(workflow_id, name)
        managed = self._processes.get(key)
        if not managed or managed.status == "running":
            return

        # Remove log directory
        if managed.log_dir.exists():
            shutil.rmtree(managed.log_dir, ignore_errors=True)

        # Remove parent .processes/ if empty
        parent = managed.log_dir.parent
        if parent.exists() and parent.name == ".processes" and not any(parent.iterdir()):
            parent.rmdir()

        # Remove from tracking dict
        self._processes.pop(key, None)
        logger.info("[Process] Cleaned up: %s (workflow=%s)", name, workflow_id)

    async def stop_workflow(self, workflow_id: str) -> int:
        """Stop all processes for a workflow and clean up immediately."""
        killed = 0
        for key in list(self._processes.keys()):
            wid, name = key
            if wid != workflow_id:
                continue
            managed = self._processes[key]
            if managed.status == "running":
                kill_tree(managed.pid)
                for task in (managed.stdout_task, managed.stderr_task):
                    if task and not task.done():
                        task.cancel()
                killed += 1
                logger.info("[Process] Killed for workflow stop: %s (pid=%d)", name, managed.pid)
            # Clean log files and remove entry
            if managed.log_dir.exists():
                shutil.rmtree(managed.log_dir, ignore_errors=True)
            self._processes.pop(key, None)

        # Clean parent .processes/ dirs if empty
        if killed:
            logger.info("[Process] Stopped %d process(es) for workflow %s", killed, workflow_id)
        return killed

    async def shutdown(self) -> None:
        """Kill all managed processes and clean up log files."""
        if not self._processes:
            return
        logger.info("[Process] Shutting down %d process(es)", len(self._processes))
        for key in list(self._processes.keys()):
            managed = self._processes[key]
            if managed.status == "running":
                kill_tree(managed.pid)
                for task in (managed.stdout_task, managed.stderr_task):
                    if task and not task.done():
                        task.cancel()
            # Clean up this process's log dir
            if managed.log_dir.exists():
                shutil.rmtree(managed.log_dir, ignore_errors=True)
        self._processes.clear()

    async def _read_stream(self, managed: ManagedProcess, stream: asyncio.StreamReader, stream_name: str) -> None:
        """Background task: read lines, write to log file, broadcast to Terminal."""
        level = "info" if stream_name == "stdout" else "error"
        source = f"process:{managed.name}"
        log_file = managed.log_dir / f"{stream_name}.log"

        try:
            with open(log_file, "a", encoding="utf-8", errors="replace") as f:
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    # Strip ANSI colour/cursor codes so the Terminal tab, the
                    # persisted log file, and get_output() all show clean text
                    # (vite/npm/etc. emit colour codes that render as garbage).
                    text = strip_ansi(line.decode(errors="replace")).rstrip()

                    # Write to log file
                    f.write(text + "\n")
                    f.flush()

                    if stream_name == "stdout":
                        managed.stdout_lines += 1
                    else:
                        managed.stderr_lines += 1

                    # Broadcast to Terminal tab
                    if self._broadcaster:
                        await self._broadcaster.broadcast_terminal_log(
                            {
                                "timestamp": datetime.now().isoformat(),
                                "level": level,
                                "message": text,
                                "source": source,
                            }
                        )

                    # Optional framework-level subscriber.
                    if managed.line_handler is not None:
                        try:
                            await managed.line_handler(stream_name, text)
                        except Exception as cb_err:
                            logger.debug(
                                "[Process] line_handler %s/%s raised: %s",
                                managed.name,
                                stream_name,
                                cb_err,
                            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("[Process] Stream reader %s/%s ended: %s", managed.name, stream_name, e)

        # Stream EOF -- capture exit code (only stdout reader does this)
        if managed.status == "running" and stream_name == "stdout":
            try:
                managed.exit_code = await asyncio.wait_for(managed.process.wait(), timeout=5)
            except (asyncio.TimeoutError, Exception):
                managed.exit_code = managed.process.returncode
            managed.status = "stopped" if managed.exit_code == 0 else "error"
            managed.stopped_at = datetime.now().isoformat()
            logger.info("[Process] Exited: %s (exit=%s)", managed.name, managed.exit_code)

            # Schedule auto-cleanup of log files and process entry after delay
            asyncio.get_event_loop().call_later(60, lambda: self._cleanup_completed(managed.workflow_id, managed.name))

    @staticmethod
    def _info(m: ManagedProcess) -> Dict[str, Any]:
        return {
            "name": m.name,
            "command": m.command,
            "pid": m.pid,
            "status": m.status,
            "started_at": m.started_at,
            "stopped_at": m.stopped_at,
            "exit_code": m.exit_code,
            "working_directory": m.working_directory,
            "stdout_lines": m.stdout_lines,
            "stderr_lines": m.stderr_lines,
            "ports": list(m.ports),
            "log_dir": str(m.log_dir),
        }


# -- Singleton --

_instance: Optional[ProcessService] = None


def get_process_service() -> ProcessService:
    global _instance
    if _instance is None:
        _instance = ProcessService()
    return _instance


async def shutdown_process_service() -> None:
    if _instance is not None:
        await _instance.shutdown()
