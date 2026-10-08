"""Lazy mobile broker. Owns one persistent emulator and serializes all device I/O."""

from __future__ import annotations
import asyncio
import contextlib
import json
import os
import secrets
import socket
import time
from pathlib import Path
from services.process_environment import without_onepassword_environment
from ._control import DeviceControl, Lease, MobileError
from ._install import AVD_NAME, install_engine, create_device, engine_ready, sdk_tool, sdk_root
from ._paths import mobile_root, runtime_python
from ._process import command, hidden_options
from ._diagnostics import event, recent
from ._emulator import DeviceLock, recover_emulator, boot_error, owned_children, stop_children
from .runtime.progress import plan_summary, summary

READS = frozenset({"geometry", "observe", "date", "packages", "foreground"})
ACTIONS = frozenset({"tap", "swipe", "touch", "text", "erase", "key", "launch", "terminate", "url", "rotate"})


class MobileRuntime:
    def __init__(self):
        self.control = DeviceControl()
        self.process: asyncio.subprocess.Process | None = None
        self.driver: asyncio.subprocess.Process | None = None
        self.worker: asyncio.subprocess.Process | None = None
        self.serial: str | None = None
        self.geometry: dict | None = None
        self.device_lock = None
        self.start_error = ""
        self.driver_lock = asyncio.Lock()
        self.lifecycle_lock = asyncio.Lock()
        self.task_lock = asyncio.Lock()
        self.setup_task: asyncio.Task | None = None
        self.setup_state = "idle"
        self.setup_error = ""
        self.setup_progress: dict = {}
        self._setup_loaded = False
        self._setup_saved = 0.0
        self.active: dict | None = None
        self.last_task: dict | None = None
        self.queue: list[dict] = []
        # Execution handles are deliberately separate from public task snapshots.
        # Completion means run() has drained its worker and admitted device I/O.
        self._run_tasks: dict[str, tuple[dict, asyncio.Task, asyncio.Event]] = {}
        self._worker_stop_tasks: set[asyncio.Task] = set()
        self._reset_runs: set[str] = set()
        self._draining_runs: set[str] = set()
        self._retired_generations: dict[tuple[str, str], int] = {}
        self.capabilities: dict[str, tuple[str, Lease]] = {}
        self.resume_event = asyncio.Event()
        self.viewer: str | None = None
        self.video_viewer: str | None = None
        self.stopping = False
        self.broker_runner = None
        self.broker_url: str | None = None
        self.broker_lock = asyncio.Lock()

    async def ensure_broker(self) -> str:
        """Private engine IPC. Capabilities never appear in URLs or public responses."""
        async with self.broker_lock:
            if self.broker_url:
                return self.broker_url
            from aiohttp import web

            async def dispatch(request):
                try:
                    capability = request.headers.get("Authorization", "").removeprefix("Bearer ")
                    grant = self.capabilities.get(capability)
                    if not grant:
                        return web.json_response({"success": False, "error": "Task capability expired"}, status=403)
                    _, lease = grant
                    data = await request.json()
                    if not isinstance(data, dict) or data.get("epoch") != lease.epoch:
                        raise MobileError("stale_lease", "Task control changed")
                    operation = data.get("operation")
                    parameters = data.get("parameters", {})
                    if not isinstance(parameters, dict):
                        raise MobileError("invalid_action", "Expected command parameters")
                    if operation in READS:
                        # Reads are fenced too: a paused task cannot keep observing passwords.
                        result = await self.control.perform(
                            lease, data.get("operation_id"), lambda: self.driver_call(operation, parameters), cache_result=False
                        )
                    else:
                        result = await self.input(lease, operation, parameters, data.get("operation_id"), data.get("geometry"))
                    return web.json_response({"success": True, "result": result})
                except MobileError as exc:
                    return web.json_response({"success": False, "error": str(exc), "code": exc.code}, status=409)
                except (ValueError, TypeError):
                    return web.json_response({"success": False, "error": "Invalid command"}, status=400)
                except Exception:
                    return web.json_response({"success": False, "error": "Device connection failed"}, status=503)

            app = web.Application(client_max_size=65536)
            app.router.add_post("/control", dispatch)
            runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
            await runner.setup()
            sock = socket.socket()
            try:
                sock.bind(("127.0.0.1", 0))
                sock.setblocking(False)
                port = sock.getsockname()[1]
                await web.SockSite(runner, sock).start()
            except BaseException:
                sock.close()
                await runner.cleanup()
                raise
            self.broker_runner = runner
            self.broker_url = f"http://127.0.0.1:{port}/control"
            return self.broker_url

    def snapshot(self) -> dict:
        self._load_setup()
        return {
            "running": self.process is not None and self.process.returncode is None
            and self.driver is not None and self.driver.returncode is None and self.geometry is not None,
            "starting": self.lifecycle_lock.locked() and self.geometry is None and not self.stopping,
            "serial": self.serial,
            "geometry": self.geometry,
            "device": "Shared Android device",
            "setup": self.setup_state,
            "setup_error": self.setup_error,
            "start_error": self.start_error,
            "setup_progress": self.setup_progress,
            "diagnostics": recent(),
            "active": {k: v for k, v in (self.active or {}).items() if k not in {"prompt"}},
            "last_task": self.last_task,
            "queue": [{k: v for k, v in q.items() if k != "prompt"} for q in self.queue],
            **self.control.snapshot(),
        }

    def _load_setup(self) -> None:
        if self._setup_loaded:
            return
        self._setup_loaded = True
        try:
            record = json.loads((mobile_root() / "setup-status.json").read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                return
            if record.get("state") not in {"installing_engine", "installing_device", "ready", "error", "interrupted"}:
                return
            if not isinstance(record.get("progress"), dict):
                return
            progress = record["progress"]
            events = progress.get("events", [])
            if not isinstance(events, list) or any(
                not isinstance(event, dict) or not isinstance(event.get("message"), str)
                or not isinstance(event.get("at"), (int, float)) for event in events
            ):
                return
            progress["events"] = events[-80:]
            self.setup_state = record["state"]
            self.setup_error = str(record.get("error", ""))[:1500]
            self.setup_progress = record["progress"]
            if self.setup_state.startswith("installing_"):
                self.setup_state = "interrupted"
                self.setup_error = "Setup was interrupted by a backend restart. Retry setup to reuse completed components."
                self.setup_progress["finished_at"] = time.time()
                self._setup_update(self.setup_error, force=True)
        except (OSError, ValueError, TypeError):
            pass

    def _setup_update(self, message: str, *, force: bool = False) -> None:
        now = time.time()
        message = message.strip()[-1500:]
        if not message:
            return
        self.setup_progress.update(message=message, updated_at=now)
        events = self.setup_progress.setdefault("events", [])
        if not events or events[-1]["message"] != message:
            events.append({"at": now, "message": message})
            del events[:-80]
        if not force and time.monotonic() - self._setup_saved < 1:
            return
        try:
            root = mobile_root()
            root.mkdir(parents=True, exist_ok=True)
            temp = root / "setup-status.json.tmp"
            temp.write_text(json.dumps({"state": self.setup_state, "error": self.setup_error, "progress": self.setup_progress}), encoding="utf-8")
            temp.replace(root / "setup-status.json")
            self._setup_saved = time.monotonic()
        except OSError:
            # Disk failures must not erase the live status or abort installation.
            pass

    def setup(self, accepted: bool) -> None:
        if not accepted:
            raise MobileError("license_required", "Read and accept the SDK license before setup")
        if self.setup_task and not self.setup_task.done():
            return
        if self.stopping or self.lifecycle_lock.locked() or self.active or self.serial:
            raise MobileError("device_busy", "Stop the device before changing its runtime")

        self._setup_loaded = True
        self.setup_error = ""
        self.setup_state = "installing_engine"
        self.setup_progress = {"started_at": time.time(), "events": []}
        self._setup_update("Checking the mobile agent runtime", force=True)
        event("setup_started")

        async def run():
            try:
                await install_engine(progress=self._setup_update)
                self.setup_state = "installing_device"
                self._setup_update("Preparing Android tools and the shared phone", force=True)
                await create_device(licenses_accepted=True, progress=self._setup_update)
                self.setup_state = "ready"
                self.setup_progress["finished_at"] = time.time()
                self._setup_update("Setup complete. Start the phone to connect it.", force=True)
                event("setup_completed")
            except asyncio.CancelledError:
                self.setup_state = "interrupted"
                self.setup_error = "Setup stopped because the backend shut down. Retry setup to reuse completed components."
                self.setup_progress["finished_at"] = time.time()
                self._setup_update(self.setup_error, force=True)
                event("setup_interrupted", failed=True)
                raise
            except Exception as exc:
                self.setup_state, self.setup_error = "error", str(exc)[-1500:] or f"Setup failed ({type(exc).__name__}). Retry setup."
                self.setup_progress["finished_at"] = time.time()
                self._setup_update(self.setup_error, force=True)
                event("setup_failed", failed=True, error_type=type(exc).__name__)

        self.setup_task = asyncio.create_task(run())

    async def start(self) -> dict:
        async with self.lifecycle_lock:
            if self.stopping or (self.setup_task and not self.setup_task.done()):
                raise MobileError("device_busy", "Wait for setup or shutdown to finish")
            self.start_error = ""
            try:
                return await self._start_locked()
            except BaseException as exc:
                self.start_error = str(exc) if isinstance(exc, MobileError) else "Phone startup was interrupted. Try Start phone again."
                event("phone_start_failed", failed=True, error_type=type(exc).__name__, code=getattr(exc, "code", "startup_failed"))
                await self._stop_owned()
                raise

    async def _start_locked(self) -> dict:
        if self.stopping or (self.setup_task and not self.setup_task.done()):
            raise MobileError("device_busy", "Wait for setup or shutdown to finish")
        if self.process and self.process.returncode is None:
            if not self.driver or self.driver.returncode is not None or self.geometry is None:
                await self._stop_driver()
                await self._start_driver()
                self.control.revoke()
                self.control.state = "idle"
            return self.snapshot()
        if self.process is not None:
            await self._stop_driver()
            self.process = None
            self.serial = None
        emulator, adb = sdk_tool("emulator"), sdk_tool("adb")
        if not emulator or not adb or not engine_ready() or not (mobile_root() / "resource.json").is_file():
            raise MobileError("setup_required", "Complete Mobile setup before starting the device")
        root = mobile_root()
        root.mkdir(parents=True, exist_ok=True)
        if self.device_lock is None:
            self.device_lock = DeviceLock(root)
        recovered = await recover_emulator(adb, emulator, root / "avd" / f"{AVD_NAME}.avd")
        if recovered:
            self.process, self.serial = recovered
            event("phone_reconnected", serial=self.serial)
            await self._start_driver()
            self.control.revoke()
            self.control.state = "idle"
            return self.snapshot()
        port = None
        for candidate in range(5560, 5680, 2):
            held = []
            try:
                for n in (candidate, candidate + 1):
                    test = socket.socket()
                    held.append(test)
                    test.bind(("127.0.0.1", n))
                port = candidate
                break
            except OSError:
                pass
            finally:
                for test in held:
                    test.close()
        if port is None:
            raise MobileError("ports_busy", "No emulator port pair is free")
        root = mobile_root()
        event("phone_starting")
        root.mkdir(parents=True, exist_ok=True)
        log_path = root / "emulator.log"
        if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
            log_path.replace(root / "emulator.previous.log")
        log = log_path.open("ab")
        log_offset = log.tell()
        env = {**os.environ, "ANDROID_AVD_HOME": str(root / "avd"), "ANDROID_SDK_ROOT": str(sdk_root())}
        try:
            self.process = await asyncio.create_subprocess_exec(
                str(emulator),
                "-avd",
                AVD_NAME,
                "-port",
                str(port),
                "-no-window",
                "-no-audio",
                stdout=log,
                stderr=log,
                env=without_onepassword_environment(env),
                **hidden_options(),
            )
        finally:
            log.close()
        self.serial = f"emulator-{port}"
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.returncode is not None:
                raise boot_error(log_path, log_offset, self.process.returncode)
            try:
                boot = await command([str(adb), "-s", self.serial, "shell", "getprop", "sys.boot_completed"], check=False, timeout=10)
            except TimeoutError:
                event("phone_boot_probe_timeout", serial=self.serial)
                continue
            if boot.strip() == "1":
                break
            await asyncio.sleep(1)
        else:
            raise MobileError("boot_timeout", "Android did not finish booting within three minutes")
        await self._start_driver()
        event("phone_ready", serial=self.serial)
        self.control.revoke()
        self.control.state = "idle"
        return self.snapshot()

    def driver_env(self) -> dict:
        allowed = {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "TMPDIR",
            "APPDATA",
            "LOCALAPPDATA",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "PROGRAMDATA",
            "USERPROFILE",
            "HOME",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
        }
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env.update(
            MOBILE_USE_TELEMETRY_ENABLED="false",
            PYTHON_DOTENV_DISABLED="1",
            PYTHONUTF8="1",
            LANGCHAIN_TRACING_V2="false",
            LANGSMITH_TRACING="false",
            PATH=str(sdk_root() / "platform-tools") + os.pathsep + env.get("PATH", ""),
        )
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(name, None)
        return without_onepassword_environment(env)

    async def _start_driver(self):
        event("driver_starting")
        # Initialization consumes the first protocol reply. It must own the
        # same lock as commands so polling cannot read that reply concurrently.
        async with self.driver_lock:
            try:
                await self._initialize_driver()
                event("driver_ready")
            except BaseException:
                await self._stop_driver()
                raise

    async def _initialize_driver(self):
        entry = Path(__file__).with_name("runtime") / "device_server.py"
        self.driver = await asyncio.create_subprocess_exec(
            str(runtime_python()),
            "-u",
            str(entry),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=8 * 1024 * 1024,
            env=self.driver_env(),
            cwd=str(mobile_root()),
            **hidden_options(),
        )
        self.driver.stdin.write((json.dumps({"serial": self.serial, "source": str(mobile_root() / "engine")}) + "\n").encode())
        await self.driver.stdin.drain()
        data = await asyncio.wait_for(self.driver.stdout.readline(), 60)
        if not data:
            raise MobileError("driver_failed", "Android automation did not initialize")
        reply = json.loads(data)
        if not reply.get("success"):
            raise MobileError("driver_failed", reply.get("error", "Android automation did not initialize"))
        self.geometry = reply["result"]

    async def driver_call(self, operation: str, parameters: dict | None = None):
        async with self.driver_lock:
            if not self.driver or self.driver.returncode is not None:
                raise MobileError("device_offline", "Start the device before using it")
            started = time.monotonic()
            try:
                self.driver.stdin.write((json.dumps({"operation": operation, "parameters": parameters or {}}) + "\n").encode())
                await self.driver.stdin.drain()
                line = await asyncio.wait_for(self.driver.stdout.readline(), 30)
                if not line:
                    raise RuntimeError("Device driver disconnected")
                result = json.loads(line)
            except BaseException as exc:
                event("driver_connection_failed", failed=True, operation=operation, error_type=type(exc).__name__)
                self.control.state = "recovering"
                await self._stop_driver()
                raise
            if not result.get("success"):
                event("device_action_failed", failed=True, operation=operation, code=result.get("code", "device_action_failed"))
                raise MobileError(result.get("code", "device_action_failed"), result.get("error", "Device command failed"))
            if operation not in READS:
                event("device_action_completed", operation=operation, duration_ms=round((time.monotonic() - started) * 1000))
            elif operation != "geometry":
                event("device_read_completed", operation=operation, duration_ms=round((time.monotonic() - started) * 1000))
            if operation == "geometry":
                self.geometry = result["result"]
            elif operation == "observe":
                self.geometry = result["result"]["geometry"]
            return result["result"]

    async def input(self, lease: Lease, operation: str, parameters: dict, operation_id: str, geometry: dict | None = None):
        if operation not in ACTIONS:
            raise MobileError("unsupported_action", "Unsupported device command")
        if operation in {"tap", "swipe", "touch"}:
            parameters = {**parameters, "geometry": geometry}
        return await self.control.perform(lease, operation_id, lambda: self.driver_call(operation, parameters))

    async def install_apk(self, path: Path, lease: Lease, operation_id: str):
        if not lease.owner.startswith("viewer:"):
            raise MobileError("not_controller", "Take manual control before installing an APK")
        if not self.serial or not self.process or self.process.returncode is not None:
            raise MobileError("device_offline", "Start the shared device first")
        uploads = (mobile_root() / "uploads").resolve()
        path = path.resolve()
        if not path.is_relative_to(uploads) or path.suffix.lower() != ".apk" or not path.is_file():
            raise MobileError("invalid_action", "Expected an uploaded APK")

        async def install():
            return await command([str(sdk_tool("adb")), "-s", self.serial, "install", "-r", str(path)], timeout=180)

        return await self.control.perform(lease, operation_id, install)

    async def takeover(self, viewer: str) -> Lease:
        if self.stopping or self.lifecycle_lock.locked():
            raise MobileError("device_busy", "Wait for the device to finish starting or stopping")
        self.control.revoke()
        self.capabilities.clear()
        if self.active:
            self.active["status"] = "awaiting_user"
        await self._stop_worker()
        lease = await self.control.claim("viewer:" + viewer, takeover=True)
        self.viewer = viewer
        event("manual_control_started")
        await self._publish_progress("Waiting for you")
        return lease

    def release(self, viewer: str, *, resume: bool = False):
        if self.viewer != viewer:
            raise MobileError("not_controller", "Another viewer controls the device")
        self.control.revoke("viewer:" + viewer)
        self.viewer = None
        if resume:
            self.resume_event.set()

    async def cancel(self, workflow_id: str, node_id: str, *, run_id: str | None = None):
        def matches(entry):
            return entry["workflow_id"] == workflow_id and entry["node_id"] == node_id and (run_id is None or entry["run_id"] == run_id)

        await self._cancel_matching(matches)

    async def reset_execution_state(self, *, workflow_id: str, node_id: str, generation: int) -> dict:
        if generation < 0:
            raise MobileError("invalid_generation", "Reset generation cannot be negative")
        # Retire admission before the first await so a late graph activity cannot
        # enqueue while reset is draining a previously admitted task.
        if generation > 0:
            key = (workflow_id, node_id)
            self._retired_generations[key] = max(generation, self._retired_generations.get(key, 0))

        def matches(entry):
            return (
                entry["workflow_id"] == workflow_id and entry["node_id"] == node_id
                # A phone-only reset must not cancel a graph generation admitted
                # concurrently by Start. Zero identifies direct Workspace work.
                and (entry.get("generation", 0) == 0 if generation == 0 else entry.get("generation", 0) <= generation)
            )

        await self._cancel_matching(matches, clear=True)
        return {"reset": True}

    async def _cancel_matching(self, matches, *, clear: bool = False) -> None:
        records = [record for record in self._run_tasks.values() if matches(record[0])]
        entries = [entry for entry in self.queue if matches(entry)]
        active = self.active if self.active and matches(self.active) else None
        if active is not None:
            entries.append(active)
        run_ids = {entry["run_id"] for entry in entries}
        for entry in entries:
            entry["status"] = "cancelled"
        if clear:
            self._reset_runs.update(run_ids)
            if self.last_task and matches(self.last_task):
                self.last_task = None
        # Revoke before cancellation/draining. Keep manual and unrelated leases.
        for run_id in run_ids:
            self.control.revoke("run:" + run_id)
        for capability, (run_id, _) in list(self.capabilities.items()):
            if run_id in run_ids:
                self.capabilities.pop(capability, None)
        for entry, task, _ in records:
            # A second reset must not interrupt a first cancellation's finally.
            if not task.done() and not task.cancelling() and entry["run_id"] not in self._draining_runs:
                task.cancel()
        if records:
            await asyncio.gather(*(done.wait() for _, _, done in records))
            if active is not None and self.active is active:
                raise MobileError("reset_failed", "Mobile task cleanup did not finish; retry reset")
        elif active is not None:
            # Also handle state restored/manually owned without a live run handle.
            await self._stop_worker()
            async with self.control.lock:
                pass
            if self.active is active:
                self.active = None
        if clear:
            if self.last_task and matches(self.last_task):
                self.last_task = None
            self._reset_runs.difference_update(run_ids)

    async def run(self, *, principal: str, workflow_id: str, node_id: str, run_id: str, params: dict, model: dict, broker_url: str, execution_id: str | None = None, generation: int = 0) -> dict:
        if generation > 0 and generation <= self._retired_generations.get((workflow_id, node_id), 0):
            raise MobileError("stale_generation", "This mobile task belongs to a reset workflow generation")
        if len(self.queue) >= 32:
            raise MobileError("queue_full", "The shared device queue is full")
        if any(q["run_id"] == run_id for q in self.queue) or (self.active and self.active["run_id"] == run_id):
            raise MobileError("duplicate_run", "This task is already admitted")
        entry = {"run_id": run_id, "workflow_id": workflow_id, "node_id": node_id, "execution_id": execution_id, "generation": generation, "status": "queued", "steps": 0, "max_steps": params["max_steps"]}
        done = asyncio.Event()
        self._run_tasks[run_id] = (entry, asyncio.current_task(), done)
        self.queue.append(entry)
        event("task_queued", run_id=run_id, workflow_id=workflow_id, node_id=node_id)
        try:
            async with self.task_lock:
                self.queue.remove(entry)
                if entry["status"] == "cancelled":
                    raise asyncio.CancelledError()
                if self.stopping:
                    raise MobileError("stopping", "Mobile runtime is shutting down")
                if not self.serial:
                    raise MobileError("device_offline", "Start the shared device in Workspace first")
                self.active = entry
                entry.update(started_at=time.time(), provider=model.get("provider"), model=model.get("model"))
                active_remaining = params["timeout_s"]
                steps_remaining = params["max_steps"]
                attempts = 0
                try:
                    await self._publish_progress("Preparing phone")
                    while True:
                        if entry["status"] == "cancelled":
                            raise asyncio.CancelledError()
                        if self.control.owner and self.control.owner.startswith("viewer:"):
                            entry["status"] = "awaiting_user"
                        if entry["status"] == "awaiting_user":
                            await self._publish_progress("Waiting for you")
                            await asyncio.wait_for(self.resume_event.wait(), 1800)
                            self.resume_event.clear()
                            if entry["status"] == "cancelled":
                                raise asyncio.CancelledError()
                        lease = await self.control.claim("run:" + run_id)
                        entry["status"] = "starting"
                        self.resume_event.clear()
                        capability = secrets.token_urlsafe(32)
                        self.capabilities[capability] = (run_id, lease)
                        geometry = await self.driver_call("geometry")
                        if entry["status"] == "cancelled":
                            raise asyncio.CancelledError()
                        if entry["status"] == "awaiting_user" or lease.owner != self.control.owner or lease.epoch != self.control.epoch:
                            self.capabilities.pop(capability, None)
                            entry["status"] = "awaiting_user"
                            continue
                        entry["status"] = "running"
                        event("task_running", run_id=run_id)
                        config = {
                            **model,
                            "source": str(mobile_root() / "engine"),
                            "serial": self.serial,
                            "platform": "android",
                            "width": geometry["width"],
                            "height": geometry["height"],
                            "epoch": lease.epoch,
                            "capability": capability,
                            "broker_url": broker_url,
                            "prompt": params["prompt"]
                            + (
                                "\nYou are resuming after the user took manual control. Inspect the current screen first. Continue the goal from the current state; do not repeat actions already completed."
                                if attempts
                                else ""
                            ),
                            "max_steps": steps_remaining,
                            "timeout_s": active_remaining,
                        }
                        attempts += 1
                        entry["attempt_steps"] = 0
                        entry["step_offset"] = params["max_steps"] - steps_remaining
                        started = time.monotonic()
                        try:
                            try:
                                result = await self._run_worker(config, active_remaining)
                            except Exception:
                                if entry["status"] not in {"awaiting_user", "cancelled"}:
                                    raise
                                result = {"success": False}
                        finally:
                            active_remaining -= time.monotonic() - started
                            steps_remaining -= max(1, entry.get("attempt_steps", 0))
                            self.capabilities.pop(capability, None)
                            await self._stop_worker()
                        if entry["status"] == "cancelled":
                            raise asyncio.CancelledError()
                        if entry["status"] == "awaiting_user":
                            if active_remaining <= 0 or steps_remaining <= 0:
                                raise MobileError("budget_exhausted", "Task exhausted its time or step budget")
                            continue
                        if not result.get("success"):
                            raise MobileError(result.get("code", "agent_failed"), result.get("error", "Mobile agent failed"))
                        entry["status"] = "completed"
                        event("task_completed", run_id=run_id)
                        return {"response": result.get("result"), "outcome": "completed", "run_id": run_id, "artifacts": []}
                finally:
                    self._draining_runs.add(run_id)
                    for capability, (owner_run, _) in list(self.capabilities.items()):
                        if owner_run == run_id:
                            self.capabilities.pop(capability, None)
                    self.control.revoke("run:" + run_id)
                    await self._stop_worker()
                    # Wait for any previously admitted broker write before reporting completion.
                    async with self.control.lock:
                        pass
                    entry["finished_at"] = time.time()
                    if run_id not in self._reset_runs:
                        self.last_task = entry
                    self.active = None
        except asyncio.CancelledError:
            entry["status"] = "cancelled"
            event("task_cancelled", run_id=run_id)
            raise
        except Exception as exc:
            entry["status"] = "failed"
            entry["error_code"] = getattr(exc, "code", "timeout" if isinstance(exc, asyncio.TimeoutError) else "task_failed")
            event("task_failed", failed=True, run_id=run_id, error_type=type(exc).__name__, code=getattr(exc, "code", "task_failed"))
            raise
        finally:
            if entry in self.queue:
                self.queue.remove(entry)
            self._run_tasks.pop(run_id, None)
            self._draining_runs.discard(run_id)
            done.set()

    async def _publish_progress(self, phase: str, *, record: bool = True) -> None:
        if not self.active:
            return
        entry = self.active
        now = time.time()
        if entry.get("phase") != phase:
            entry["phase_started_at"] = now
        entry["phase"] = phase
        entry["updated_at"] = now
        history = entry.setdefault("activity", [])
        if record and (not history or history[-1]["message"] != phase):
            history.append({"at": now, "message": phase, "step": entry.get("steps", 0)})
        del history[:-40]
        try:
            from services.status_broadcaster import get_status_broadcaster
            await get_status_broadcaster().update_node_status(
                entry["node_id"], "waiting" if entry["status"] == "awaiting_user" else "executing",
                {"iteration": entry.get("steps", 0), "max_iterations": entry.get("max_steps", 0),
                 "phase": phase, "execution_id": entry.get("execution_id"), "run_id": entry["run_id"]},
                workflow_id=entry["workflow_id"],
            )
        except Exception as exc:
            event("progress_delivery_failed", failed=True, error_type=type(exc).__name__)

    async def _publish_agent_progress(self, message: dict) -> None:
        """Detailed summaries stay in the owner-only snapshot, never diagnostic logs."""
        if not self.active or message.get("kind") not in {"plan", "action"}:
            return
        entry = self.active
        now = time.time()
        kind = message["kind"]
        item = {"at": now, "step": entry.get("steps", 0), "kind": kind,
                "message": summary(message.get("message"), 400), "detail": summary(message.get("detail"))}
        if not item["message"]:
            return
        history = entry.setdefault("agent_activity", [])
        if kind == "plan":
            plan = plan_summary(message.get("plan"))
            if plan is None:
                return
            entry["plan"] = plan
            entry["current_goal"] = next((goal["description"] for goal in plan if goal["status"] == "pending"), None)
            history.append(item)
            phase = "Updating task plan"
        else:
            action_id = summary(message.get("action_id"), 100)
            state = message.get("state")
            if not action_id or state not in {"started", "completed", "failed", "returned"}:
                return
            item.update(action_id=action_id, state=state, outcome=summary(message.get("outcome")))
            duration = message.get("duration_ms")
            if isinstance(duration, (int, float)) and 0 <= duration <= 86_400_000:
                item["duration_ms"] = round(duration)
            previous = next((row for row in reversed(history) if row.get("action_id") == action_id), None)
            if previous:
                # Keep one row and the original engine step for each actual tool call.
                item.update(at=previous["at"], step=previous["step"], finished_at=now)
                previous.update(item)
                item = previous
            else:
                history.append(item)
            if state == "started" or not entry.get("current_action") or entry["current_action"].get("action_id") == action_id:
                entry["current_action"] = item
            phase = {"started": "Executing phone action", "completed": "Phone action completed",
                     "failed": "Phone action failed", "returned": "Checking action outcome"}[state]
        del history[:-40]
        await self._publish_progress(phase, record=False)

    def _finish_pending_actions(self) -> None:
        if not self.active:
            return
        for item in self.active.get("agent_activity", []):
            if item.get("state") == "started":
                item.update(state="returned", finished_at=time.time(),
                            outcome="Action interrupted before the tool confirmed its outcome.")

    async def _run_worker(self, config: dict, timeout: float) -> dict:
        entry = Path(__file__).with_name("runtime") / "worker.py"
        self.worker = await asyncio.create_subprocess_exec(
            str(runtime_python()),
            "-u",
            str(entry),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self.driver_env(),
            cwd=str(mobile_root()),
            limit=2 * 1024 * 1024,
            **hidden_options(),
        )
        proc = self.worker
        if config["capability"] not in self.capabilities or not self.active or self.active["status"] != "running":
            await self._stop_worker()
            return {"success": False, "error": "Task admission revoked"}
        self.active.update(plan=[], current_goal=None, current_action=None)
        proc.stdin.write((json.dumps(config) + "\n").encode())
        await proc.stdin.drain()
        proc.stdin.close()

        scope = {"run_id": (self.active or {}).get("run_id"), "workflow_id": (self.active or {}).get("workflow_id"),
                 "node_id": (self.active or {}).get("node_id"), "execution_id": (self.active or {}).get("execution_id"),
                 "provider": config.get("provider"), "model": config.get("model")}
        if config.get("provider") == "google":
            scope["model_backend"] = "vertex_express" if config.get("model_env", {}).get("GOOGLE_GENAI_USE_VERTEXAI") == "true" else "gemini_developer"
        event("engine_starting", **scope)
        await self._publish_progress("Starting Android task")

        pending_models: dict[str, float] = {}

        async def read():
            result = {"success": False, "error": "Mobile worker exited before completing the task"}
            while line := await proc.stdout.readline():
                message = json.loads(line)
                if message.get("type") == "completed":
                    result = {"success": True, "result": message.get("result")}
                elif message.get("type") == "failed":
                    result = {"success": False, "error": message.get("error", "Worker failed"), "code": message.get("code", "engine_failed")}
                    event("engine_failed", failed=True, **scope, engine_trace=message.get("trace", []),
                          **{key: message.get(key) for key in ("error_type", "code", "stage", "http_status", "provider_status")})
                elif message.get("type") == "activity":
                    labels = {"observe": "Reading screen and accessibility tree", "geometry": "Checking screen size",
                              "tap": "Tapping screen", "swipe": "Scrolling", "text": "Typing text",
                              "key": "Pressing navigation key", "launch": "Opening app", "terminate": "Closing app",
                              "url": "Opening link", "erase": "Erasing text", "packages": "Listing apps",
                              "foreground": "Checking current app", "date": "Checking device time"}
                    label = labels.get(message.get("operation"))
                    state = message.get("state")
                    if label and state in {"started", "completed", "failed"}:
                        duration = message.get("duration_ms")
                        suffix = f" ({duration / 1000:.1f}s)" if isinstance(duration, (int, float)) and duration >= 0 else ""
                        await self._publish_progress(label if state == "started" else f"{label}: {state}{suffix}")
                elif message.get("type") == "agent_update":
                    await self._publish_agent_progress(message)
                elif message.get("type") == "diagnostic":
                    event("engine_stage", **scope, stage=message.get("stage"), error_type=message.get("error_type"))
                    phase = {"model_request": "Waiting for model", "model_response": "Model responded",
                             "engine_initialization": "Connecting engine", "task_execution": "Preparing task plan",
                             "engine_cleanup": "Finishing engine cleanup"}.get(message.get("stage"))
                    stage = message.get("stage")
                    request_id = str(message.get("request_id", "model"))
                    if stage == "model_request":
                        pending_models[request_id] = time.monotonic()
                        role = {"planner": "planning", "orchestrator": "coordinating next step",
                                "contextor": "understanding the screen", "cortex": "choosing an action",
                                "executor": "preparing an action", "outputter": "preparing the result",
                                "hopper": "selecting an app"}.get(message.get("role"))
                        if role:
                            phase = f"Waiting for model: {role}"
                    elif stage in {"model_response", "model_error"}:
                        started = pending_models.pop(request_id, None)
                        phase = "Model request failed" if stage == "model_error" else "Model responded"
                        if started is not None:
                            phase += f" ({time.monotonic() - started:.1f}s)"
                        if pending_models:
                            phase += f"; waiting for {len(pending_models)} model request(s)"
                    if phase and self.active:
                        await self._publish_progress(phase)
                elif message.get("type") == "ready":
                    event("engine_ready", **scope)
                elif message.get("type") == "usage" and self.active:
                    self.active["usage"] = message.get("usage")
                elif message.get("type") == "progress" and self.active:
                    steps = max(self.active.get("attempt_steps", 0), int(message.get("steps", 0)))
                    if steps != self.active.get("attempt_steps", 0):
                        event("task_progress", **scope, steps=steps)
                        self.active["steps"] = min(self.active.get("max_steps", steps), self.active.get("step_offset", 0) + steps)
                        await self._publish_progress(self.active.get("phase", "Preparing task plan"), record=False)
                    self.active["attempt_steps"] = steps
            await proc.wait()
            event("engine_exited", failed=proc.returncode not in (0, None), **scope, exit_code=proc.returncode)
            return result

        return await asyncio.wait_for(read(), max(1, timeout) + 15)

    async def _stop_worker(self):
        proc, self.worker = self.worker, None
        if proc and proc.returncode is None:
            task = asyncio.create_task(self._terminate_worker(proc))
            self._worker_stop_tasks.add(task)
            task.add_done_callback(self._worker_stop_tasks.discard)
        if self._worker_stop_tasks:
            # Takeover and reset can overlap. Both must await an already-started
            # worker stop even after self.worker has been detached.
            await asyncio.shield(asyncio.gather(*self._worker_stop_tasks))
        self._finish_pending_actions()

    async def _terminate_worker(self, proc):
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    async def _stop_driver(self):
        proc, self.driver = self.driver, None
        self.geometry = None
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()

    async def _stop_owned(self):
        event("phone_stopping", serial=self.serial)
        self.control.revoke()
        self.capabilities.clear()
        await self._stop_worker()
        await self._stop_driver()
        proc = self.process
        children = owned_children(getattr(proc, "pid", None)) if proc else []
        if proc and proc.returncode is None:
            if self.serial:
                with contextlib.suppress(Exception):
                    await command([str(sdk_tool("adb")), "-s", self.serial, "emu", "kill"], timeout=5)
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await asyncio.wait_for(proc.wait(), 5)
        await stop_children(children)
        self.process = None
        self.serial, self.geometry = None, None
        if self.device_lock is not None:
            self.device_lock.close()
            self.device_lock = None
        async with self.control.lock:
            self.control.state = "idle"
            self.control.results.clear()
        self.viewer = None
        self.resume_event.clear()
        event("phone_stopped")

    async def stop(self):
        if self.active:
            raise MobileError("device_busy", "Cancel the active task before stopping the device")
        async with self.lifecycle_lock:
            await self._stop_owned()
            self.start_error = ""

    async def shutdown(self):
        self.stopping = True
        if self.setup_task and not self.setup_task.done():
            self.setup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.setup_task
        if self.active:
            self.active["status"] = "cancelled"
        self.resume_event.set()
        async with self.lifecycle_lock:
            await self._stop_owned()
        if self.broker_runner:
            await self.broker_runner.cleanup()
            self.broker_runner, self.broker_url = None, None


_runtime: MobileRuntime | None = None


def get_runtime() -> MobileRuntime:
    global _runtime
    if _runtime is None:
        _runtime = MobileRuntime()
    return _runtime


def peek_runtime() -> MobileRuntime | None:
    return _runtime
