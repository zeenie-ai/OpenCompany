"""Live-view recovery with fake CDP; never installs or launches Chrome."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nodes.browser import _stream
from nodes.browser._cdp import CDPDisconnected


def make_hub(*, failures=0, fail_start=False):
    session = SimpleNamespace(target_id="page", on=Mock(return_value=lambda: None), send=AsyncMock(), detach=AsyncMock())
    if fail_start:
        async def send(method, *args, **kwargs):
            if method == "Page.startScreencast":
                raise CDPDisconnected("lost")
        session.send.side_effect = send
    controller = SimpleNamespace(active_target_id="page", controller_viewer=None, state="idle", tabs={}, add_listener=Mock(return_value=lambda: None), viewer_attached=Mock(), viewer_detached=Mock())
    page_session = AsyncMock(side_effect=[*[CDPDisconnected("lost") for _ in range(failures)], session]) if failures else AsyncMock(return_value=session)
    runtime = SimpleNamespace(controller=controller, running=True, page_session=page_session)
    hub = _stream.ScreencastHub(runtime)
    viewer = _stream.Viewer(None, "owner")
    return hub, viewer, runtime, session


async def test_initial_attach_failure_recovers_without_viewer_resize(monkeypatch):
    monkeypatch.setattr(_stream, "_SCREENCAST_RETRY_DELAYS", (0, 0, 0))
    hub, viewer, runtime, session = make_hub(failures=1)
    await hub.add(viewer)
    message = viewer.control.get_nowait()
    assert message["code"] == "screencast" and message["retrying"] is True
    await hub._retry_task
    assert hub._active and runtime.page_session.await_count == 2
    await hub.remove(viewer)
    session.detach.assert_awaited_once()


async def test_start_failure_retries_are_bounded_and_reported(monkeypatch):
    monkeypatch.setattr(_stream, "_SCREENCAST_RETRY_DELAYS", (0, 0, 0))
    hub, viewer, runtime, session = make_hub(fail_start=True)
    await hub.add(viewer)
    await hub._retry_task
    assert not hub._active and runtime.page_session.await_count == 4
    messages = []
    while not viewer.control.empty():
        messages.append(viewer.control.get_nowait())
    assert messages[-1]["retrying"] is False
    assert session.detach.await_count == 4
    await asyncio.sleep(0)
    assert runtime.page_session.await_count == 4
    await hub.remove(viewer)


@pytest.mark.parametrize("end", ["hidden", "removed", "closed"])
async def test_pending_retry_stops_when_no_visible_viewer_or_browser_closes(monkeypatch, end):
    monkeypatch.setattr(_stream, "_SCREENCAST_RETRY_DELAYS", (60,))
    hub, viewer, runtime, _ = make_hub(failures=1)
    await hub.add(viewer)
    retry = hub._retry_task
    if end == "hidden":
        viewer.visible = False
        await hub._refresh()
    elif end == "removed":
        await hub.remove(viewer)
    else:
        await hub._on_controller("closed", {"reason": "stopped"})
    await asyncio.gather(retry, return_exceptions=True)
    assert retry.cancelled() and runtime.page_session.await_count == 1
