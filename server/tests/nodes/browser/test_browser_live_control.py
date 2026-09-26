"""Ordering, bounded admission and release barriers without a real browser."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nodes.browser._live_control import LiveControlQueue, MAX_BYTES, MAX_COMMANDS
from nodes.browser._session import BrowserSession, ControlState, ProfileController, SessionKey


def setup_queue():
    controller = ProfileController("profile", "Browser")
    controller._broadcast = AsyncMock()
    controller.active_target_id = "page"
    controller.tabs = {"page": {"url": "https://example.test"}}
    controller.state = ControlState.USER
    controller.controller_viewer = "viewer"
    session = BrowserSession(SessionKey("owner", "workflow", "node"), "profile")
    controller.lease_session = session
    messages = []
    viewer = SimpleNamespace(id="viewer", visible=True, closed=False, send_json=messages.append)
    page = SimpleNamespace(send=AsyncMock(return_value={}))
    hub = SimpleNamespace(controller=controller, session=page, input=AsyncMock(), copy=AsyncMock(), navigate=AsyncMock(), tab=AsyncMock(), dialog_reply=AsyncMock())
    queue = LiveControlQueue(hub)
    return queue, hub, viewer, session, messages


async def drain(queue):
    if queue._worker:
        await asyncio.wait_for(queue._worker, 1)


async def test_only_adjacent_moves_and_wheels_coalesce_without_reordering_keys():
    queue, hub, viewer, session, _ = setup_queue()
    for message in [
        {"type": "mouse", "action": "move", "x": 1},
        {"type": "mouse", "action": "move", "x": 2},
        {"type": "key", "action": "down", "key": "a"},
        {"type": "wheel", "delta_y": 4},
        {"type": "wheel", "delta_y": 6},
    ]:
        assert queue.enqueue(viewer, message, session)
    assert len(queue._queue) == 3
    await drain(queue)
    actual = [call.args[1] for call in hub.input.await_args_list]
    assert [m["type"] for m in actual] == ["mouse", "key", "wheel"]
    assert actual[0]["x"] == 2 and actual[-1]["delta_y"] == 10


async def test_wheels_over_different_scroll_containers_do_not_coalesce():
    queue, hub, viewer, session, _ = setup_queue()
    queue.enqueue(viewer, {"type": "wheel", "x": 10, "y": 10, "delta_y": 5}, session)
    queue.enqueue(viewer, {"type": "wheel", "x": 100, "y": 10, "delta_y": 5}, session)
    assert len(queue._queue) == 2
    await drain(queue)
    assert hub.input.await_count == 2


@pytest.mark.parametrize("limit", ["count", "bytes"])
async def test_overload_is_explicit_bounded_and_safely_releases_control(limit):
    queue, _, viewer, session, messages = setup_queue()
    if limit == "count":
        for _ in range(MAX_COMMANDS):
            assert queue.enqueue(viewer, {"type": "key", "action": "down", "key": "x"}, session)
        rejected = {"type": "key", "action": "up", "key": "x"}
    else:
        rejected = {"type": "insert_text", "text": "x" * MAX_BYTES}
    assert not queue.enqueue(viewer, rejected, session)
    assert len(queue._queue) <= MAX_COMMANDS and queue._bytes <= MAX_BYTES
    assert messages[-1]["code"] == "control_overloaded"
    await drain(queue)
    assert queue.controller.state == ControlState.IDLE


async def test_target_epoch_discards_work_even_if_target_switches_back():
    queue, hub, viewer, session, _ = setup_queue()
    queue.enqueue(viewer, {"type": "key", "action": "down", "key": "a"}, session)
    hub.controller.active_target_id = "other"
    queue.target_changed()
    hub.controller.active_target_id = "page"
    queue.target_changed()
    await drain(queue)
    hub.input.assert_not_awaited()


async def test_release_waits_dispatched_command_and_releases_keys_before_agent_admission():
    queue, hub, viewer, session, _ = setup_queue()
    entered, complete = asyncio.Event(), asyncio.Event()
    events = []

    async def send(method, params, **kwargs):
        if method == "Runtime.evaluate":
            events.append("read-start")
            entered.set()
            await complete.wait()
            events.append("read-finished")
        else:
            events.append(params["type"])
        return {}

    hub.session.send.side_effect = send

    async def key_input(v, message):
        await queue.guarded_session(v, hub.session).send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Shift", "code": "ShiftLeft"})

    async def copy(v):
        await queue.guarded_session(v, hub.session).send("Runtime.evaluate", {})

    hub.input.side_effect, hub.copy.side_effect = key_input, copy
    queue.enqueue(viewer, {"type": "key", "action": "down", "key": "Shift"}, session)
    queue.enqueue(viewer, {"type": "copy"}, session)
    await entered.wait()
    queue.enqueue(viewer, {"type": "key", "action": "down", "key": "z"}, session)
    queue.release(viewer)
    assert not queue.enqueue(viewer, {"type": "key", "action": "down", "key": "late"}, session)
    await asyncio.sleep(0)
    assert hub.controller.state == ControlState.USER
    complete.set()
    await drain(queue)
    assert events == ["keyDown", "read-start", "read-finished", "keyUp"]
    assert hub.controller.state == ControlState.IDLE and not queue._pressed


async def test_automatic_handback_uses_same_dispatched_command_barrier():
    queue, hub, viewer, session, _ = setup_queue()
    entered, complete = asyncio.Event(), asyncio.Event()

    async def copy(v):
        entered.set()
        await complete.wait()

    hub.copy.side_effect = copy
    queue.enqueue(viewer, {"type": "copy"}, session)
    await entered.wait()
    release = asyncio.create_task(hub.controller.hand_back(viewer.id))
    await asyncio.sleep(0)
    assert not release.done() and hub.controller.state == ControlState.USER
    complete.set()
    await release
    await drain(queue)
    assert hub.controller.state == ControlState.IDLE


async def test_hidden_viewer_cannot_receive_pending_takeover_and_requests_are_single_flight():
    queue, hub, viewer, session, _ = setup_queue()
    hub.controller.state = ControlState.AGENT
    hub.controller.controller_viewer = None
    assert queue.enqueue(viewer, {"type": "control_request"}, session)
    assert not queue.enqueue(viewer, {"type": "control_request"}, session)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    viewer.visible = False
    queue.release(viewer)
    await drain(queue)
    assert hub.controller.controller_viewer is None
    assert hub.controller.state == ControlState.AGENT


async def test_guard_rechecks_ownership_between_two_awaited_cdp_commands():
    queue, hub, viewer, session, messages = setup_queue()
    entered, complete = asyncio.Event(), asyncio.Event()

    async def send(method, params, **kwargs):
        entered.set()
        await complete.wait()
        return {}

    hub.session.send.side_effect = send

    async def copy(v):
        page = queue.guarded_session(v, hub.session)
        await page.send("Page.getNavigationHistory", {})
        await page.send("Page.navigateToHistoryEntry", {})

    hub.copy.side_effect = copy
    queue.enqueue(viewer, {"type": "copy"}, session)
    await entered.wait()
    queue.release(viewer)
    complete.set()
    await drain(queue)
    assert hub.session.send.await_count == 1
    assert any(m.get("code") == "control" for m in messages)


async def test_timeout_retires_chrome_before_handback_and_failed_retirement_fails_closed():
    queue, hub, viewer, session, _ = setup_queue()
    hub.runtime = SimpleNamespace(chrome=SimpleNamespace(shutdown=AsyncMock(side_effect=RuntimeError("busy"))))

    async def copy(v):
        await queue.guarded_session(v, hub.session).send("Page.navigate", {})

    hub.copy.side_effect = copy
    hub.session.send.side_effect = TimeoutError("unknown outcome")
    queue.enqueue(viewer, {"type": "copy"}, session)
    await drain(queue)
    assert queue._unsafe
    queue.release(viewer)
    await drain(queue)
    assert hub.controller.state == ControlState.USER
    hub.runtime.chrome.shutdown.side_effect = None
    queue.release(viewer)
    await drain(queue)
    assert hub.controller.state == ControlState.IDLE
    assert not queue._unsafe


async def test_input_owns_a_session_independent_of_screencast_detach():
    queue, hub, viewer, session, _ = setup_queue()
    owned = SimpleNamespace(send=AsyncMock(return_value={}), detach=AsyncMock())
    hub.runtime = SimpleNamespace(page_session=AsyncMock(return_value=owned))

    async def copy(v):
        hub.session = None  # capture resize can retire the picture session
        await queue.input_session(v).send("Runtime.evaluate", {})

    hub.copy.side_effect = copy
    queue.enqueue(viewer, {"type": "copy"}, session)
    await drain(queue)
    owned.send.assert_awaited_once()
    owned.detach.assert_not_awaited()
    await queue.close()
    owned.detach.assert_awaited_once()


async def test_retirement_close_notification_does_not_wait_on_its_own_worker():
    queue, hub, viewer, _, _ = setup_queue()
    queue._unsafe = True

    async def shutdown():
        await queue.close()  # runtime close callback while release is active

    hub.runtime = SimpleNamespace(chrome=SimpleNamespace(shutdown=shutdown))
    queue.release(viewer)
    await drain(queue)
    assert queue._closed and not queue._unsafe
    assert hub.controller.state == ControlState.IDLE


async def test_forced_transfer_releases_original_session_keys_before_new_owner():
    queue, hub, viewer, session, _ = setup_queue()
    other = SimpleNamespace(id="other", visible=True, closed=False, send_json=lambda message: None)
    queue._pressed[(viewer.id, "key", "ShiftLeft")] = (hub.session, {"type": "keyDown", "key": "Shift", "code": "ShiftLeft"})
    queue.enqueue(other, {"type": "control_request", "force": True}, session)
    await drain(queue)
    hub.session.send.assert_awaited_once_with("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Shift", "code": "ShiftLeft", "modifiers": 0}, timeout=5)
    assert hub.controller.controller_viewer == "other"
    assert viewer.id in queue._blocked and not queue._pressed


async def test_navigation_in_same_target_releases_old_pressed_keys_before_next_input():
    queue, hub, viewer, session, _ = setup_queue()
    owned = SimpleNamespace(send=AsyncMock(return_value={}), detach=AsyncMock())
    hub.runtime = SimpleNamespace(page_session=AsyncMock(return_value=owned))
    await queue._ensure_input_session()
    queue._pressed[(viewer.id, "key", "ShiftLeft")] = (owned, {"type": "keyDown", "key": "Shift", "code": "ShiftLeft"})
    hub.controller.tabs["page"]["url"] = "https://example.test/next"
    queue.target_changed()
    queue.enqueue(viewer, {"type": "copy"}, session)
    await drain(queue)
    assert owned.send.await_args.args[1]["type"] == "keyUp"
    assert not queue._pressed
    hub.copy.assert_awaited_once()
    owned.detach.assert_not_awaited()
    await queue.idle_without_viewers()
    owned.detach.assert_awaited_once()
    assert queue._worker.done() and queue._input_session is None


async def test_socket_reader_handles_ack_visibility_and_ping_while_navigation_waits(monkeypatch):
    from fastapi import WebSocketDisconnect
    import core.container
    import services.authz
    import nodes.browser._runtime as browser_runtime
    from nodes.browser import _stream

    queue, hub, _, session, _ = setup_queue()
    entered, complete, acknowledged, hidden, pong = (asyncio.Event() for _ in range(5))
    hub.commands = queue
    hub.dead = False
    hub.controller.controller_viewer = None

    async def add(viewer):
        hub.controller.controller_viewer = viewer.id

    async def navigate(viewer, message, policy):
        entered.set()
        await complete.wait()

    async def remove(viewer):
        queue.release(viewer)

    hub.add, hub.remove = add, remove
    hub.navigate.side_effect = navigate
    hub.state_message = lambda v: {"type": "state", "state": "user", "controller": "you"}
    hub.tabs_message = lambda: {"type": "tabs", "tabs": []}
    hub.schedule_resize = hidden.set
    hub.frame_sent = lambda v: None

    class Viewer(_stream.Viewer):
        def acknowledge(self, seq=None):
            acknowledged.set()

    class Socket:
        def __init__(self):
            self.messages = asyncio.Queue()
        async def accept(self):
            pass
        async def receive_json(self):
            message = await self.messages.get()
            if message is None:
                raise WebSocketDisconnect()
            return message
        async def send_text(self, text):
            if '"pong"' in text:
                pong.set()
        async def close(self, **kwargs):
            pass

    runtime = SimpleNamespace(running=lambda profile: object(), controller=lambda profile: hub.controller)
    monkeypatch.setattr(core.container, "container", SimpleNamespace(settings=lambda: None, user_auth_service=None))
    monkeypatch.setattr(services.authz, "authenticate_ws", AsyncMock(return_value="owner"))
    monkeypatch.setattr(browser_runtime, "get_browser_runtime", lambda: runtime)
    monkeypatch.setattr(_stream, "_resolve_attach", AsyncMock(return_value=session))
    monkeypatch.setattr(_stream, "get_hub", lambda _: hub)
    monkeypatch.setattr(_stream, "Viewer", Viewer)
    socket = Socket()
    socket.messages.put_nowait({"type": "attach", "target": {}})
    socket.messages.put_nowait({"type": "navigate", "action": "reload"})
    task = asyncio.create_task(_stream.browser_live_view(socket))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        for message in ({"type": "ack", "seq": 1}, {"type": "visibility", "visible": False}, {"type": "ping"}):
            socket.messages.put_nowait(message)
        await asyncio.wait_for(asyncio.gather(acknowledged.wait(), hidden.wait(), pong.wait()), 1)
        assert hub.controller.state == ControlState.USER
    finally:
        complete.set()
        socket.messages.put_nowait(None)
        await asyncio.wait_for(task, 1)
        await drain(queue)
