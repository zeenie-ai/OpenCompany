"""Runtime orchestration without an emulator, SDK, or model request."""

import asyncio
import json

import pytest

from nodes.mobile._runtime import MobileRuntime


async def test_driver_initialization_serializes_concurrent_command(monkeypatch, tmp_path):
    import nodes.mobile._runtime as module

    value = MobileRuntime()
    value.serial = "emulator-test"
    proc = FakeProcess()
    waiting = asyncio.Event()
    original_readline = proc.stdout.readline

    async def readline():
        waiting.set()
        return await original_readline()

    async def spawn(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(module, "mobile_root", lambda: tmp_path)
    monkeypatch.setattr(value, "driver_env", lambda: {})
    monkeypatch.setattr(proc.stdout, "readline", readline)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    starting = asyncio.create_task(value._start_driver())
    await waiting.wait()
    command = asyncio.create_task(value.driver_call("geometry"))
    await asyncio.sleep(0)
    assert not command.done()
    assert b'"operation"' not in proc.stdin.data
    proc.stdout.feed_data(b'{"success":true,"result":{"width":1080,"height":2400}}\n')
    await starting
    proc.stdout.feed_data(b'{"success":true,"result":{"width":2400,"height":1080}}\n')
    assert await command == {"width": 2400, "height": 1080}
    assert not proc.terminated.is_set()


async def test_status_during_startup_does_not_query_driver(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    import nodes.mobile._runtime as module
    from nodes.mobile._router import status

    value = MobileRuntime()
    value.serial = "emulator-test"
    value.process = FakeProcess()
    monkeypatch.setattr(module, "mobile_root", lambda: tmp_path)
    monkeypatch.setattr(module, "get_runtime", lambda: value)
    query = AsyncMock()
    monkeypatch.setattr(value, "driver_call", query)
    async with value.lifecycle_lock:
        result = await status(principal="owner")
    assert result["starting"] is True
    assert result["running"] is False
    query.assert_not_awaited()


async def test_phone_progress_uses_normal_node_status_channel(runtime, monkeypatch):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    import services.status_broadcaster as status
    update = AsyncMock()
    monkeypatch.setattr(status, "get_status_broadcaster", lambda: SimpleNamespace(update_node_status=update))
    runtime.active = {"node_id": "phone", "workflow_id": "wf", "execution_id": "exec", "run_id": "run",
                      "status": "running", "steps": 7, "max_steps": 40}
    await runtime._publish_progress("Waiting for model")
    update.assert_awaited_once_with("phone", "executing", {"iteration": 7, "max_iterations": 40,
        "phase": "Waiting for model", "execution_id": "exec", "run_id": "run"}, workflow_id="wf")
    assert runtime.snapshot()["active"]["phase"] == "Waiting for model"


async def test_status_poll_does_not_queue_behind_input(runtime, monkeypatch):
    from unittest.mock import AsyncMock
    import nodes.mobile._runtime as module
    from nodes.mobile._router import status
    monkeypatch.setattr(module, "get_runtime", lambda: runtime)
    query = AsyncMock()
    monkeypatch.setattr(runtime, "driver_call", query)
    async with runtime.driver_lock:
        await status(principal="owner")
    query.assert_not_awaited()


def test_hand_back_resumes_a_waiting_task_without_holding_the_phone():
    from nodes.mobile._control import MobileError

    value = MobileRuntime()
    # Nothing waits: nothing happens.
    value.resume_waiting()
    assert not value.resume_event.is_set()
    value.active = {"run_id": "run", "status": "awaiting_user"}
    value.viewer = "someone"
    with pytest.raises(MobileError, match="using the phone"):
        value.resume_waiting()
    assert not value.resume_event.is_set()
    value.viewer = None
    value.resume_waiting()
    assert value.resume_event.is_set()


def test_the_snapshot_says_which_phone_it_is():
    from nodes.mobile._install import DEVICE_NAME, VIDEO_MAX_FPS

    assert MobileRuntime().snapshot()["device"] == f"{DEVICE_NAME} · local emulator · up to {VIDEO_MAX_FPS} fps" == "Pixel 7 · local emulator · up to 30 fps"


async def test_a_screenshot_is_saved_to_the_workspace(monkeypatch, tmp_path):
    import base64
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import core.container
    import nodes.mobile._runtime as module
    from nodes.mobile._router import screenshot

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 300
    value = MobileRuntime()
    query = AsyncMock(return_value={"base64": base64.b64encode(png).decode()})
    monkeypatch.setattr(value, "driver_call", query)
    monkeypatch.setattr(module, "get_runtime", lambda: value)
    monkeypatch.setattr(core.container, "container", SimpleNamespace(database=lambda: "db"))

    async def root(workflow_id, database, *, allow_default=True):
        assert (workflow_id, database, allow_default) == ("wf", "db", False)
        return tmp_path

    monkeypatch.setattr("services.workspace_locator.resolve_workspace_root", root)
    result = await screenshot(workflow_id="wf", node_id="phone-node", principal="owner")
    query.assert_awaited_once_with("screenshot")
    ref = result["ref"]
    assert (ref["kind"], ref["mime_type"], ref["size_bytes"], ref["workflow_id"]) == ("image", "image/png", len(png), "wf")
    assert ref["filename"].startswith("phone-") and ref["filename"].endswith(".png")
    assert (tmp_path / ref["path"]).read_bytes() == png


class FakeStdin:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        pass


class FakeProcess:
    def __init__(self):
        self.stdin = FakeStdin()
        self.stdout = asyncio.StreamReader()
        self.returncode = None
        self.terminated = asyncio.Event()

    def terminate(self):
        self.returncode = -15
        self.stdout.feed_eof()
        self.terminated.set()

    def kill(self):
        self.terminate()

    async def wait(self):
        await self.terminated.wait()
        return self.returncode


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    import nodes.mobile._runtime as module

    value = MobileRuntime()
    value.serial = "emulator-5560"
    monkeypatch.setattr(module, "mobile_root", lambda: tmp_path)
    monkeypatch.setattr(value, "driver_env", lambda: {})

    async def geometry(*_args, **_kwargs):
        return {"width": 1080, "height": 1920}

    monkeypatch.setattr(value, "driver_call", geometry)
    return value


def run_args():
    return dict(
        principal="owner",
        workflow_id="workflow",
        node_id="agent",
        run_id="run",
        params={"prompt": "test", "max_steps": 3, "timeout_s": 30},
        model={},
        broker_url="http://127.0.0.1/broker",
    )


async def test_takeover_stops_worker_then_resumes_with_fresh_capability(runtime, monkeypatch):
    processes = []
    spawned = asyncio.Queue()

    async def spawn(*_args, **_kwargs):
        proc = FakeProcess()
        processes.append(proc)
        spawned.put_nowait(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(runtime.run(**run_args()))
    first = await asyncio.wait_for(spawned.get(), 1)
    await asyncio.sleep(0)
    first_config = json.loads(first.stdin.data)
    human = await runtime.takeover("browser")
    assert first.terminated.is_set()
    assert not runtime.capabilities

    assert human.owner == "viewer:browser"
    assert runtime.active["status"] == "awaiting_user"
    runtime.release("browser", resume=True)
    second = await asyncio.wait_for(spawned.get(), 1)
    await asyncio.sleep(0)
    second_config = json.loads(second.stdin.data)
    assert second_config["epoch"] > first_config["epoch"]
    assert second_config["capability"] != first_config["capability"]
    second.stdout.feed_data(b'{"type":"completed","result":"done"}\n')
    second.returncode = 0
    second.stdout.feed_eof()
    second.terminated.set()
    assert (await asyncio.wait_for(task, 1))["response"] == "done"
    assert runtime.active is None
    assert not runtime.capabilities


async def test_cancel_while_waiting_for_human_releases_task(runtime):
    await runtime.takeover("browser")
    task = asyncio.create_task(runtime.run(**run_args()))
    await asyncio.sleep(0)
    assert runtime.active["status"] == "awaiting_user"
    await runtime.cancel("workflow", "agent")
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert runtime.active is None
    assert runtime.queue == []
    assert runtime.control.owner == "viewer:browser"


async def test_queued_cancel_is_scoped_to_run_and_never_starts_worker(runtime, monkeypatch):
    async def unexpected_spawn(*_args, **_kwargs):
        pytest.fail("A cancelled queued task must never spawn a worker")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_spawn)
    await runtime.task_lock.acquire()
    first = asyncio.create_task(runtime.run(**run_args()))
    second_args = {**run_args(), "run_id": "other-run"}
    second = asyncio.create_task(runtime.run(**second_args))
    await asyncio.sleep(0)
    assert len(runtime.queue) == 2
    await runtime.cancel("workflow", "agent", run_id="run")
    assert [(entry["run_id"], entry["status"]) for entry in runtime.queue] == [("other-run", "queued")]
    await runtime.cancel("workflow", "agent", run_id="other-run")
    runtime.serial = None  # Cancellation must win over offline validation.
    runtime.task_lock.release()
    for task in (first, second):
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    assert runtime.queue == []
    assert runtime.active is None


async def test_takeover_waits_for_already_accepted_mutation(runtime):
    lease = await runtime.control.claim("run:run")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def mutation():
        entered.set()
        await finish.wait()

    action = asyncio.create_task(runtime.control.perform(lease, "write", mutation))
    await entered.wait()
    takeover = asyncio.create_task(runtime.takeover("browser"))
    await asyncio.sleep(0)
    assert not takeover.done()
    assert runtime.control.owner is None
    finish.set()
    await action
    assert (await takeover).owner == "viewer:browser"


async def test_cancel_during_geometry_no_spawn(runtime, monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def geometry(*_args, **_kwargs):
        entered.set()
        await finish.wait()
        return {"width": 1080, "height": 1920}

    async def unexpected_spawn(*_args, **_kwargs):
        pytest.fail("Cancelled task must not launch after geometry resolves")

    monkeypatch.setattr(runtime, "driver_call", geometry)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_spawn)
    task = asyncio.create_task(runtime.run(**run_args()))
    await asyncio.wait_for(entered.wait(), 1)
    await runtime.cancel("workflow", "agent")
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert runtime.active is None


async def test_takeover_during_spawn_does_not_send_config(runtime, monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    process = FakeProcess()

    async def spawn(*_args, **_kwargs):
        entered.set()
        await finish.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(runtime.run(**run_args()))
    await asyncio.wait_for(entered.wait(), 1)
    await runtime.takeover("browser")
    finish.set()
    try:
        await asyncio.wait_for(process.terminated.wait(), 1)
        assert process.stdin.data == b""
        assert runtime.control.owner == "viewer:browser"
    finally:
        await runtime.cancel("workflow", "agent")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)


async def test_stop_preserves_epoch_monotonicity_and_clears_viewer(runtime):
    lease = await runtime.takeover("browser")
    runtime.resume_event.set()
    await runtime.stop()
    assert runtime.control.owner is None
    assert runtime.control.epoch > lease.epoch
    assert runtime.viewer is None
    assert not runtime.resume_event.is_set()
    next_lease = await runtime.control.claim("viewer:browser")
    assert next_lease.epoch > lease.epoch


async def test_worker_failure_reaches_diagnostics_and_keeps_provider_code(runtime, monkeypatch):
    import nodes.mobile._runtime as module
    from unittest.mock import Mock
    from nodes.mobile._control import MobileError

    proc = FakeProcess()
    proc.stdout.feed_data(b'{"type":"activity","operation":"observe","state":"started"}\n')
    proc.stdout.feed_data(b'{"type":"activity","operation":"observe","state":"completed","duration_ms":123}\n')
    proc.stdout.feed_data(b'{"type":"activity","operation":"secret-url","state":"started"}\n')
    proc.stdout.feed_data(b'{"type":"diagnostic","stage":"model_request"}\n')
    proc.stdout.feed_data(b'{"type":"failed","error":"Model not found (HTTP 404)","code":"model_not_found","http_status":404,"error_type":"ClientError","stage":"task_execution"}\n')
    proc.returncode = 1
    proc.stdout.feed_eof()
    proc.terminated.set()
    async def spawn(*_args, **_kwargs):
        return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    log = Mock()
    monkeypatch.setattr(module, "event", log)
    args = run_args()
    args.update(execution_id="execution-2", model={"provider": "google", "model": "selected-model"})
    with pytest.raises(MobileError) as failure:
        await runtime.run(**args)
    assert failure.value.code == "model_not_found"
    recorded = [call.kwargs for call in log.call_args_list if call.args == ("engine_failed",)]
    assert len(recorded) == 1
    assert recorded[0]["http_status"] == 404
    assert recorded[0]["execution_id"] == "execution-2"
    assert recorded[0]["node_id"] == "agent"
    assert recorded[0]["model"] == "selected-model"
    assert runtime.active is None
    assert runtime.last_task["status"] == "failed"
    assert runtime.last_task["error_code"] == "model_not_found"
    assert runtime.last_task["activity"][-1]["message"] == "Waiting for model"
    assert runtime.last_task["finished_at"] >= runtime.last_task["started_at"]
    messages = [item["message"] for item in runtime.last_task["activity"]]
    assert "Reading screen and accessibility tree: completed (0.1s)" in messages
    assert not any("secret-url" in item for item in messages)


async def test_activity_is_bounded_and_phase_clock_is_stable(runtime, monkeypatch):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    import services.status_broadcaster as status
    monkeypatch.setattr(status, "get_status_broadcaster", lambda: SimpleNamespace(update_node_status=AsyncMock()))
    runtime.active = {"node_id": "phone", "workflow_id": "wf", "run_id": "run", "status": "running"}
    await runtime._publish_progress("Waiting for model")
    started = runtime.active["phase_started_at"]
    for _ in range(50):
        await runtime._publish_progress("Waiting for model")
    assert runtime.active["phase_started_at"] == started
    assert len(runtime.active["activity"]) == 1
    assert runtime.active["updated_at"] >= started
    for index in range(50):
        await runtime._publish_progress(f"Operation {index}")
    assert len(runtime.active["activity"]) == 40
    await runtime._publish_progress("Tapping screen")
    assert runtime.snapshot()["active"]["activity"][-1]["message"] == "Tapping screen"


async def test_worker_semantic_progress_survives_device_noise_and_model_wait(runtime, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    import nodes.mobile._runtime as module
    import services.status_broadcaster as status

    broadcast = AsyncMock()
    monkeypatch.setattr(status, "get_status_broadcaster", lambda: SimpleNamespace(update_node_status=broadcast))
    log = Mock()
    monkeypatch.setattr(module, "event", log)
    proc = FakeProcess()
    messages = [
        {"type": "progress", "steps": 1},
        {"type": "agent_update", "kind": "plan", "message": "Working on: Find private test app",
         "plan": [{"id": "g1", "description": "Find private test app", "status": "pending", "reason": "Search is visible"}]},
        {"type": "agent_update", "kind": "action", "action_id": "tap1", "state": "started", "message": "Tap · Search", "detail": "Find private test app"},
        *[{"type": "activity", "operation": "observe", "state": "completed", "duration_ms": i} for i in range(50)],
        {"type": "progress", "steps": 2},
        {"type": "agent_update", "kind": "action", "action_id": "tap1", "state": "failed", "message": "Tap · Search",
         "detail": "Find private test app", "duration_ms": 1050, "outcome": "Element not found"},
        {"type": "diagnostic", "stage": "model_request", "role": "cortex"},
        {"type": "progress", "steps": 3},
        {"type": "completed", "result": "done"},
    ]
    for message in messages:
        proc.stdout.feed_data((json.dumps(message) + "\n").encode())
    proc.returncode = 0
    proc.stdout.feed_eof()
    proc.terminated.set()

    async def spawn(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await runtime.run(**run_args())
    task = runtime.last_task
    assert task["current_goal"] == "Find private test app"
    assert task["plan"][0]["reason"] == "Search is visible"
    assert len(task["agent_activity"]) == 2  # start/end share one action row
    action = task["current_action"]
    assert action["step"] == 1
    assert action["state"] == "failed"
    assert action["outcome"] == "Element not found"
    assert action["duration_ms"] == 1050
    assert task["phase"] == "Waiting for model: choosing an action"
    assert task["steps"] == 3
    assert all(item["message"] != "Using phone" for item in task["activity"])
    assert "Find private test app" not in str(log.call_args_list)
    assert "Find private test app" not in str(broadcast.call_args_list)


async def test_semantic_history_is_bounded_and_interruption_is_not_success(runtime, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import services.status_broadcaster as status
    monkeypatch.setattr(status, "get_status_broadcaster", lambda: SimpleNamespace(update_node_status=AsyncMock()))
    runtime.active = {"node_id": "phone", "workflow_id": "wf", "run_id": "run", "status": "running"}
    for index in range(45):
        await runtime._publish_agent_progress({"kind": "action", "action_id": str(index), "state": "started",
                                               "message": "Tap · Search", "detail": "a" * 1000})
    assert len(runtime.active["agent_activity"]) == 40
    assert len(runtime.active["current_action"]["detail"]) == 500
    await runtime._stop_worker()
    assert runtime.active["current_action"]["state"] == "returned"
    assert "interrupted" in runtime.active["current_action"]["outcome"]
    assert all(item["state"] == "returned" for item in runtime.active["agent_activity"])


async def test_plan_completion_clears_current_goal_and_invalid_updates_are_ignored(runtime, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import services.status_broadcaster as status
    monkeypatch.setattr(status, "get_status_broadcaster", lambda: SimpleNamespace(update_node_status=AsyncMock()))
    runtime.active = {"node_id": "phone", "workflow_id": "wf", "run_id": "run", "status": "running", "current_goal": "Open app"}
    await runtime._publish_agent_progress({"kind": "plan", "message": "Plan updated", "plan": [
        {"id": "g", "description": "Open app", "status": "success", "reason": "Home screen visible"}]})
    assert runtime.active["current_goal"] is None
    assert runtime.active["plan"][0]["reason"] == "Home screen visible"
    await runtime._publish_agent_progress({"kind": "arbitrary", "message": "private"})
    await runtime._publish_agent_progress({"kind": "action", "action_id": "a", "message": "private", "state": "success"})
    assert len(runtime.active["agent_activity"]) == 1
