"""Frame-credit and trailing-delivery regressions without a Chrome dependency."""

import asyncio
import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nodes.browser import _stream
from nodes.browser._cdp import CDPDisconnected
from nodes.browser._stream_metrics import StreamMetrics


def frame(seq):
    return _stream.encode_frame({"seq": seq}, b"jpeg")


def sequence(data):
    return json.loads(data[4:4 + int.from_bytes(data[2:4], "big")])["seq"]


def make_hub():
    session = SimpleNamespace(target_id="page", send=AsyncMock(), detach=AsyncMock(), on=Mock(return_value=lambda: None))
    controller = SimpleNamespace(active_target_id="page", add_listener=Mock(return_value=lambda: None), tabs={})
    runtime = SimpleNamespace(controller=controller, running=True, page_session=AsyncMock(return_value=session))
    hub = _stream.ScreencastHub(runtime)
    hub.session = session
    viewer = _stream.Viewer(None, "owner")
    hub.viewers[viewer.id] = viewer
    return hub, viewer, session


async def stop_writer(viewer, task):
    viewer.closed = True
    viewer.wakeup.set()
    await asyncio.wait_for(task, 1)


async def test_final_frame_inside_fps_interval_is_delivered_without_another_event():
    sent = asyncio.Queue()
    socket = SimpleNamespace(send_text=AsyncMock(), send_bytes=AsyncMock(side_effect=sent.put_nowait))
    viewer = _stream.Viewer(socket, "owner")
    viewer.max_fps = 30
    viewer.last_frame_at = time.monotonic()
    viewer.offer_frame(frame(1))
    task = asyncio.create_task(viewer.writer(lambda _: None))
    try:
        assert sequence(await asyncio.wait_for(sent.get(), 0.5)) == 1
        assert viewer.pending is None
    finally:
        await stop_writer(viewer, task)


async def test_latest_frame_replaces_pending_and_duplicate_ack_never_grants_credit():
    sent = asyncio.Queue()
    viewer = _stream.Viewer(SimpleNamespace(send_text=AsyncMock(), send_bytes=AsyncMock(side_effect=sent.put_nowait)), "owner")
    task = asyncio.create_task(viewer.writer(lambda _: None))
    try:
        for seq in (1, 2):
            viewer.offer_frame(frame(seq))
            await asyncio.wait_for(sent.get(), 1)
        viewer.offer_frame(frame(3))
        viewer.offer_frame(frame(4))
        await asyncio.sleep(0)
        assert viewer.inflight == 2 and sequence(viewer.pending) == 4
        viewer.acknowledge(1)
        assert sequence(await asyncio.wait_for(sent.get(), 1)) == 4
        viewer.acknowledge(1)
        viewer.acknowledge(999)
        assert viewer.inflight == 2
        viewer.acknowledge()  # malformed envelope cannot provide seq
        assert viewer.inflight == 1
    finally:
        await stop_writer(viewer, task)


async def test_control_messages_bypass_frame_throttle():
    control = asyncio.Event()
    viewer = _stream.Viewer(SimpleNamespace(send_text=AsyncMock(side_effect=lambda _: control.set()), send_bytes=AsyncMock()), "owner")
    viewer.max_fps = 1
    viewer.last_frame_at = time.monotonic()
    viewer.offer_frame(frame(1))
    task = asyncio.create_task(viewer.writer(lambda _: None))
    try:
        viewer.send_json({"type": "state"})
        await asyncio.wait_for(control.wait(), .2)
        viewer.websocket.send_bytes.assert_not_awaited()
    finally:
        await stop_writer(viewer, task)


async def test_hidden_viewer_cannot_send_queued_picture():
    viewer = _stream.Viewer(SimpleNamespace(send_text=AsyncMock(), send_bytes=AsyncMock()), "owner")
    viewer.offer_frame(frame(1))
    viewer.visible = False
    task = asyncio.create_task(viewer.writer(lambda _: None))
    await asyncio.sleep(0)
    assert viewer.pending is None
    viewer.websocket.send_bytes.assert_not_awaited()
    await stop_writer(viewer, task)


async def test_slow_viewer_does_not_stop_fast_viewer():
    slow_sent, fast_sent = asyncio.Queue(), asyncio.Queue()
    viewers = [_stream.Viewer(SimpleNamespace(send_text=AsyncMock(), send_bytes=AsyncMock(side_effect=queue.put_nowait)), "owner") for queue in (slow_sent, fast_sent)]
    tasks = [asyncio.create_task(viewer.writer(lambda _: None)) for viewer in viewers]
    try:
        for seq in range(1, 5):
            for viewer in viewers:
                viewer.offer_frame(frame(seq))
            assert sequence(await asyncio.wait_for(fast_sent.get(), 1)) == seq
            viewers[1].acknowledge(seq)
        assert viewers[0].inflight == 2
        assert sequence(viewers[0].pending) == 4
        assert slow_sent.qsize() == 2
    finally:
        for viewer, task in zip(viewers, tasks):
            await stop_writer(viewer, task)


@pytest.mark.parametrize("payload", [base64.b64encode(b"jpeg").decode(), "invalid!"])
async def test_each_chrome_frame_occurrence_is_acked_even_with_same_id(payload):
    hub, _, session = make_hub()
    for _ in range(2):
        hub._on_frame({"sessionId": 7, "data": payload})
    hub._ack_now()
    await asyncio.gather(*hub._ack_tasks)
    assert session.send.await_count == 2
    for call in session.send.await_args_list:
        assert call.args == ("Page.screencastFrameAck", {"sessionId": 7})
    await hub._stop_screencast()
    assert not hub._ack_tasks


async def test_old_capture_debt_and_callback_do_not_ack_replacement_session():
    hub, _, old = make_hub()
    generation = hub._capture_generation
    params = {"sessionId": 1, "data": base64.b64encode(b"jpeg").decode()}
    hub._on_frame(params, old, generation)
    await hub._stop_screencast()
    replacement = SimpleNamespace(send=AsyncMock())
    hub.session = replacement
    hub._on_frame(params, old, generation)
    hub._ack_now()
    await asyncio.sleep(0)
    replacement.send.assert_not_awaited()
    assert not hub._pending_acks and not hub._ack_tasks


async def test_same_viewer_on_replacement_hub_does_not_reuse_old_frame_sequence():
    old_hub, viewer, _ = make_hub()
    sent = asyncio.Queue()
    viewer.websocket = SimpleNamespace(send_text=AsyncMock(), send_bytes=AsyncMock(side_effect=sent.put_nowait))
    task = asyncio.create_task(viewer.writer(lambda _: None))
    params = {"sessionId": 1, "data": base64.b64encode(b"jpeg").decode()}
    new_hub, _, _ = make_hub()
    try:
        old_hub._on_frame(params)
        old_seq = sequence(await asyncio.wait_for(sent.get(), 1))
        await old_hub._stop_screencast()
        new_hub.viewers = {viewer.id: viewer}
        new_hub._on_frame(params)
        new_seq = sequence(await asyncio.wait_for(sent.get(), 1))
        assert new_seq > old_seq
        assert viewer.inflight == 2
        viewer.acknowledge(old_seq)
        viewer.acknowledge(old_seq)
        assert viewer.inflight == 1 and new_seq in viewer._outstanding
    finally:
        await stop_writer(viewer, task)
        await new_hub._stop_screencast()


async def test_failed_ack_starts_fresh_capture_without_retrying_ambiguous_ack():
    hub, _, session = make_hub()
    session.send.side_effect = CDPDisconnected("lost")
    hub._schedule_retry = Mock()
    hub._on_frame({"sessionId": 9, "data": base64.b64encode(b"jpeg").decode()})
    hub._ack_now()
    await asyncio.gather(*hub._ack_tasks)
    session.send.assert_awaited_once()
    hub._schedule_retry.assert_called_once()
    assert not hub._active


def test_diagnostics_are_opt_in_and_bounded(monkeypatch):
    monkeypatch.delenv("OPENCOMPANY_BROWSER_DIAGNOSTICS", raising=False)
    disabled = StreamMetrics()
    disabled.observe("frame_pack", 1)
    assert disabled.snapshot() == {"timings": {}, "counts": {}}
    monkeypatch.setenv("OPENCOMPANY_BROWSER_DIAGNOSTICS", "1")
    enabled = StreamMetrics()
    for _ in range(1000):
        enabled.observe("frame_pack", .001)
    assert enabled.snapshot()["timings"]["frame_pack"] == {"n": 256, "p50_ms": 1, "p95_ms": 1}
