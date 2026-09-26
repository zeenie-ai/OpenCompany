"""The live view: ``/ws/browser``.

One WebSocket per open live view, on the app's own port under ``/ws/`` (so
the Vite dev proxy, nginx, Docker's single published port and the desktop
app all carry it unchanged). The handshake runs the same ``Origin`` and
session checks as ``/ws/status``; the first message must attach to a target
the caller owns (a Browser node of their workflow, or their own profile-login
session).

Server to viewer: page frames as binary messages
(``u8 version | u8 kind | u16 header length | JSON header | JPEG``) and JSON
messages for everything else (state, tabs, page, dialog, clipboard, errors).
Viewer to server: JSON only (attach, viewport, visibility, ack, control
request and release, mouse, wheel, key, text, navigation, tab, dialog reply).

One :class:`ScreencastHub` per running profile holds its own CDP session on
the page the agent is on and runs ``Page.startScreencast`` only while some
viewer is visible. Each viewer has a window of two unacknowledged frames and
one "latest frame" slot that newer frames overwrite, so a slow viewer drops
frames instead of stalling Chrome or the others; Chrome's own acknowledgement
goes out as soon as the frame enters those bounded slots. Input reaches
the page only from the viewer that holds control, in the USER state.
"""

from __future__ import annotations

import asyncio
import base64
import json
import itertools
import struct
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

from core.logging import get_logger

from ._cdp import CDPDisconnected, CDPError, CDPSession
from ._chrome import VIEWPORT_HEIGHT, VIEWPORT_WIDTH
from ._stream_metrics import StreamMetrics

logger = get_logger(__name__)

FRAME_VERSION = 1
FRAME_KIND_JPEG = 1
_WINDOW = 2
_MAX_FRAME_EDGE = 1920
_CONTROL_QUEUE = 256
_INPUT_RATE = 200.0  # messages per second
_INPUT_BURST = 400.0
_TEXT_LIMIT = 10_000
_CLIPBOARD_LIMIT = 100_000
_ATTACH_TIMEOUT = 5.0
_IDLE_POLL = 2.0
_SCREENCAST_RETRY_DELAYS = (1.0, 2.0, 4.0)
# Viewers can outlive a stopped profile and attach to a replacement hub on the
# same socket. Never reuse sequence IDs while old frame ACKs are in transit.
_FRAME_SEQUENCES = itertools.count(1)

CLOSE_UNAUTHENTICATED = 4001
CLOSE_ATTACH = 4002
CLOSE_FORBIDDEN = 4003
CLOSE_GONE = 4004
CLOSE_RATE = 4029

_MODIFIER_KEYS = {"Shift", "Control", "Alt", "Meta", "CapsLock"}


def encode_frame(header: Dict[str, Any], jpeg: bytes) -> bytes:
    head = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return struct.pack(">BBH", FRAME_VERSION, FRAME_KIND_JPEG, len(head)) + head + jpeg


class _Bucket:
    def __init__(self, rate: float, burst: float) -> None:
        self.rate, self.capacity, self.tokens, self.at = rate, burst, burst, time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class Viewer:
    """One live-view socket."""

    def __init__(self, websocket: WebSocket, owner_id: str) -> None:
        self.websocket = websocket
        self.owner_id = owner_id
        self.id = f"v_{uuid.uuid4().hex[:10]}"
        self.visible = True
        self.width = 800
        self.height = 500
        self.dpr = 1.0
        self.max_fps: Optional[float] = None
        self.inflight = 0
        self.pending: Optional[bytes] = None
        self.last_frame_at = 0.0
        self.control: asyncio.Queue = asyncio.Queue(maxsize=_CONTROL_QUEUE)
        self.wakeup = asyncio.Event()
        self.bucket = _Bucket(_INPUT_RATE, _INPUT_BURST)
        self.closed = False
        self.metrics = StreamMetrics()
        self._pending_at = 0.0
        self._outstanding: Dict[int, float] = {}
        self._started_at = time.monotonic()
        self._first_frame = True

    def send_json(self, message: Dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            self.control.put_nowait(message)
        except asyncio.QueueFull:
            self.closed = True
        self.wakeup.set()

    def offer_frame(self, frame: bytes) -> bool:
        """Keep the latest frame, including the final update inside an FPS interval."""
        if self.closed or not self.visible:
            return False
        if self.pending is not None:
            self.metrics.count("replaced")
        self.pending = frame
        self._pending_at = time.monotonic()
        self.wakeup.set()
        return True

    def acknowledge(self, seq: Any = None) -> None:
        # A corrupt envelope cannot yield a sequence. Its ACK consumes the oldest
        # outstanding frame; well-formed duplicate/stale ACKs never grant credit.
        if seq is None:
            seq = next(iter(self._outstanding), None)
        if not isinstance(seq, int):
            return
        sent = self._outstanding.pop(seq, None)
        if sent is not None:
            self.inflight = len(self._outstanding)
            self.metrics.observe("viewer_ack", time.monotonic() - sent)
            self.wakeup.set()

    async def writer(self, on_sent: Any) -> None:
        while not self.closed:
            self.wakeup.clear()
            while not self.control.empty():
                await self.websocket.send_text(json.dumps(self.control.get_nowait(), default=str))
            delay = None
            if self.visible and self.pending is not None and self.inflight < _WINDOW:
                delay = max(0.0, self.last_frame_at + (1.0 / self.max_fps if self.max_fps else 0) - time.monotonic())
                if delay == 0:
                    frame, self.pending = self.pending, None
                    head_end = 4 + struct.unpack_from(">H", frame, 2)[0]
                    header = json.loads(frame[4:head_end])
                    seq = header["seq"]
                    self.last_frame_at = time.monotonic()
                    self._outstanding[seq] = self.last_frame_at
                    self.inflight = len(self._outstanding)
                    self.metrics.observe("frame_queue", self.last_frame_at - self._pending_at)
                    captured_at = header.get("ts")
                    if isinstance(captured_at, (int, float)):
                        self.metrics.observe("capture_to_send", time.time() - captured_at)
                    await self.websocket.send_bytes(frame)
                    self.metrics.observe("socket_send", time.monotonic() - self.last_frame_at)
                    if self._first_frame:
                        self._first_frame = False
                        self.metrics.observe("first_frame", time.monotonic() - self._started_at)
                    on_sent(self)
                    continue
            if not self.visible:
                self.pending = None
            if delay is None:
                await self.wakeup.wait()
            else:
                try:
                    await asyncio.wait_for(self.wakeup.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass


class ScreencastHub:
    """The live picture and input for one running profile."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.controller = runtime.controller
        self.viewers: Dict[str, Viewer] = {}
        self.session: Optional[CDPSession] = None
        self.target_id: Optional[str] = None
        self.seq = 0
        self._active = False
        self._size = (0, 0)
        self._resize_task: Optional[asyncio.Task] = None
        self._retry_task: Optional[asyncio.Task] = None
        self._pending_acks: List[tuple[CDPSession, int]] = []
        self._ack_tasks: set[asyncio.Task] = set()
        self._capture_generation = 0
        self.metrics = StreamMetrics()
        self._unsubscribers: List[Any] = []
        self._lock = asyncio.Lock()
        self.dead = False
        self._unlisten = self.controller.add_listener(self._on_controller)
        from ._live_control import LiveControlQueue

        self.commands = LiveControlQueue(self)

    # -- viewers -------------------------------------------------------------------

    async def add(self, viewer: Viewer) -> None:
        self.viewers[viewer.id] = viewer
        self.controller.viewer_attached(viewer.id)
        await self._refresh()

    async def remove(self, viewer: Viewer) -> None:
        self.commands.release(viewer, note="the owner closed the live view")
        self.viewers.pop(viewer.id, None)
        self.controller.viewer_detached(viewer.id)
        if not self.viewers:
            await self.commands.idle_without_viewers()
            pending = [task for task in (self._resize_task, self._retry_task) if task is not None and task is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        await self._refresh()

    def visible_viewers(self) -> List[Viewer]:
        return [v for v in self.viewers.values() if v.visible and not v.closed]

    def broadcast(self, message: Dict[str, Any], *, per_viewer: bool = False) -> None:
        for viewer in list(self.viewers.values()):
            payload = dict(message)
            if per_viewer and "controller" in payload:
                payload["controller"] = self._controller_for(viewer)
            viewer.send_json(payload)

    def _controller_for(self, viewer: Viewer) -> Optional[str]:
        holder = self.controller.controller_viewer
        if holder is None:
            return None
        return "you" if holder == viewer.id else "other"

    def state_message(self, viewer: Viewer) -> Dict[str, Any]:
        snap = self.controller.snapshot(viewer.id)
        return {"type": "state", "state": snap["state"], "controller": snap["controller"], "request": snap["request"], "profile": snap["profile"]}

    def tabs_message(self) -> Dict[str, Any]:
        active = self.controller.active_target_id
        return {"type": "tabs", "tabs": [dict(t, active=t.get("target_id") == active) for t in self.controller.tabs.values()]}

    async def _on_controller(self, kind: str, payload: Dict[str, Any]) -> None:
        if kind in ("tabs", "page"):
            self.commands.target_changed()
        if kind in ("state", "request", "control_moved"):
            for viewer in list(self.viewers.values()):
                viewer.send_json(self.state_message(viewer))
            if kind == "control_moved":
                for viewer in list(self.viewers.values()):
                    if viewer.id == payload.get("from"):
                        viewer.send_json({"type": "control", "granted": False, "reason": "taken_over_elsewhere"})
        elif kind == "tabs":
            self.broadcast(self.tabs_message())
            await self._follow_target()
        elif kind == "page":
            self.broadcast({"type": "page", "target_id": payload.get("target_id"), "url": payload.get("url"), "title": payload.get("title")})
            await self._follow_target()
        elif kind == "closed":
            self.dead = True
            await self.commands.close()
            self._unlisten()
            self.broadcast({"type": "idle", "reason": payload.get("reason")})
            await self._stop_screencast()

    # -- screencast ------------------------------------------------------------------

    def _wanted_size(self) -> tuple[int, int]:
        viewers = self.visible_viewers()
        if not viewers:
            return (0, 0)
        width = max(min(int(v.width * v.dpr), _MAX_FRAME_EDGE) for v in viewers)
        height = max(min(int(v.height * v.dpr), _MAX_FRAME_EDGE) for v in viewers)
        return max(width, 160), max(height, 100)

    async def _refresh(self) -> None:
        async with self._lock:
            size = self._wanted_size()
            if self.dead or size == (0, 0):
                self._cancel_retry()
                await self._stop_screencast_locked()
                return
            if not self._active or size != self._size or self.target_id != self.controller.active_target_id:
                await self._start_screencast_locked(size)
                if not self._active and self.runtime.running:
                    self._schedule_retry()

    def _cancel_retry(self) -> None:
        if self._retry_task is not None and self._retry_task is not asyncio.current_task():
            self._retry_task.cancel()
            self._retry_task = None

    def _schedule_retry(self) -> None:
        if self._retry_task is not None and not self._retry_task.done():
            return

        async def retry() -> None:
            for delay in _SCREENCAST_RETRY_DELAYS:
                await asyncio.sleep(delay)
                if self.dead or not self.visible_viewers() or not self.runtime.running or self._active:
                    return
                await self._refresh()
                if self._active:
                    return
            self.broadcast({"type": "error", "code": "screencast", "message": "The browser live view could not start. Reconnect the view to try again.", "retrying": False})

        self._retry_task = asyncio.create_task(retry(), name="browser-screencast-retry")

    def _stream_error(self, exc: Exception) -> None:
        self.broadcast({"type": "error", "code": "screencast", "message": f"The browser live view could not start: {str(exc)[:200]}", "retrying": True})

    def schedule_resize(self) -> None:
        if self._resize_task is not None and not self._resize_task.done():
            self._resize_task.cancel()

        async def _later() -> None:
            await asyncio.sleep(0.3)
            await self._refresh()

        self._resize_task = asyncio.create_task(_later())

    async def _follow_target(self) -> None:
        if self._active and self.target_id != self.controller.active_target_id:
            await self._refresh()

    async def _start_screencast_locked(self, size: tuple[int, int]) -> None:
        await self._stop_screencast_locked()
        if not self.runtime.running:
            return
        try:
            session = await self.runtime.page_session(self.controller.active_target_id)
        except (CDPError, CDPDisconnected, TimeoutError) as exc:
            logger.debug("[browser] live view could not attach: %s", exc)
            self._stream_error(exc)
            return
        self.session, self.target_id = session, session.target_id
        generation = self._capture_generation
        self._unsubscribers = [
            session.on("Page.screencastFrame", lambda params: self._on_frame(params, session, generation)),
            session.on("Page.javascriptDialogOpening", self._on_dialog),
        ]
        try:
            await session.send("Page.enable", timeout=10)
            await session.send("Emulation.setFocusEmulationEnabled", {"enabled": True}, timeout=10)
            await session.send(
                "Page.startScreencast",
                {"format": "jpeg", "quality": 60, "maxWidth": size[0], "maxHeight": size[1], "everyNthFrame": 1, "maxFramesInFlight": _WINDOW, "sendLastFrame": True},
                timeout=10,
            )
        except (CDPError, CDPDisconnected, TimeoutError) as exc:
            logger.debug("[browser] startScreencast failed: %s", exc)
            await self._stop_screencast_locked()
            self._stream_error(exc)
            return
        self._active, self._size = True, size
        self.broadcast({"type": "page", "target_id": self.target_id, **self._tab_facts(self.target_id)})

    def _tab_facts(self, target_id: Optional[str]) -> Dict[str, Any]:
        tab = self.controller.tabs.get(target_id or "") or {}
        return {"url": tab.get("url"), "title": tab.get("title")}

    async def _stop_screencast(self) -> None:
        async with self._lock:
            self._cancel_retry()
            await self._stop_screencast_locked()

    async def _stop_screencast_locked(self) -> None:
        self._capture_generation += 1
        self._pending_acks.clear()
        ack_tasks = list(self._ack_tasks)
        for task in ack_tasks:
            task.cancel()
        if ack_tasks:
            await asyncio.gather(*ack_tasks, return_exceptions=True)
        for viewer in self.viewers.values():
            viewer.pending = None
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers = []
        session, self.session = self.session, None
        self._active = False
        if session is not None:
            try:
                await session.send("Page.stopScreencast", timeout=5)
            except (CDPError, CDPDisconnected, TimeoutError):
                pass
            await session.detach()

    def _on_frame(self, params: Dict[str, Any], source: Optional[CDPSession] = None, generation: Optional[int] = None) -> None:
        if generation is not None and generation != self._capture_generation:
            return
        source = source or self.session
        ack_id = params.get("sessionId")
        if source is not None and isinstance(ack_id, int):
            self._pending_acks.append((source, ack_id))
        started = time.monotonic()
        self.metrics.count("captured")
        if not self.visible_viewers():
            self._ack_now()
            return
        self.seq = next(_FRAME_SEQUENCES)
        meta = params.get("metadata") or {}
        try:
            jpeg = base64.b64decode(params.get("data") or "", validate=True)
        except (ValueError, TypeError):
            self._ack_now()
            return
        header = {
            "seq": self.seq,
            "target_id": self.target_id,
            "device_width": meta.get("deviceWidth") or VIEWPORT_WIDTH,
            "device_height": meta.get("deviceHeight") or VIEWPORT_HEIGHT,
            "page_scale_factor": meta.get("pageScaleFactor") or 1,
            "offset_top": meta.get("offsetTop") or 0,
            "scroll_x": meta.get("scrollOffsetX") or 0,
            "scroll_y": meta.get("scrollOffsetY") or 0,
            "ts": meta.get("timestamp"),
        }
        frame = encode_frame(header, jpeg)
        self.metrics.observe("frame_pack", time.monotonic() - started)
        for viewer in self.visible_viewers():
            viewer.offer_frame(frame)
        # Viewer backpressure is already bounded independently. Holding Chrome's
        # capture credit for a slow decoder causes it to skip the last update.
        self._ack_now()

    def frame_sent(self, viewer: Viewer) -> None:
        self.metrics.count("sent")

    def _ack_now(self) -> None:
        debt, self._pending_acks = self._pending_acks, []
        if not debt:
            return
        generation = self._capture_generation

        async def flush() -> None:
            try:
                for session, session_id in debt:
                    if generation != self._capture_generation:
                        return
                    await session.send("Page.screencastFrameAck", {"sessionId": session_id}, timeout=5)
            except (CDPError, CDPDisconnected, TimeoutError):
                # Delivery may have succeeded. Never retry an ambiguous ACK and
                # over-credit Chrome; recover with a fresh capture generation.
                if generation == self._capture_generation and not self.dead:
                    self._active = False
                    self._schedule_retry()

        task = asyncio.create_task(flush(), name="browser-frame-ack")
        self._ack_tasks.add(task)
        task.add_done_callback(self._ack_tasks.discard)

    def _on_dialog(self, params: Dict[str, Any]) -> None:
        self.broadcast({"type": "dialog", "kind": params.get("type"), "message": str(params.get("message") or "")[:2000]})

    # -- input ---------------------------------------------------------------------

    def _clamp(self, x: Any, y: Any) -> tuple[float, float]:
        try:
            fx, fy = float(x), float(y)
        except (TypeError, ValueError):
            return 0.0, 0.0
        return max(0.0, min(fx, float(VIEWPORT_WIDTH * 4))), max(0.0, min(fy, float(VIEWPORT_HEIGHT * 20)))

    async def input(self, viewer: Viewer, message: Dict[str, Any]) -> None:
        session = self.commands.input_session(viewer)
        if not self.controller.can_inject_input(viewer.id) or session is None:
            return
        self.controller.touch_user_input()
        kind = message.get("type")
        modifiers = int(message.get("modifiers") or 0) & 15
        try:
            if kind == "mouse":
                x, y = self._clamp(message.get("x"), message.get("y"))
                event = {"move": "mouseMoved", "down": "mousePressed", "up": "mouseReleased"}.get(str(message.get("action")))
                if event is None:
                    return
                params: Dict[str, Any] = {"type": event, "x": x, "y": y, "modifiers": modifiers}
                if event != "mouseMoved":
                    params.update(button=str(message.get("button") or "left"), clickCount=int(message.get("click_count") or 1))
                params["buttons"] = int(message.get("buttons") or 0)
                await session.send("Input.dispatchMouseEvent", params, timeout=5)
            elif kind == "wheel":
                x, y = self._clamp(message.get("x"), message.get("y"))
                await session.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseWheel",
                        "x": x,
                        "y": y,
                        "deltaX": float(message.get("delta_x") or 0),
                        "deltaY": float(message.get("delta_y") or 0),
                        "modifiers": modifiers,
                    },
                    timeout=5,
                )
            elif kind == "key":
                key = str(message.get("key") or "")[:32]
                down = message.get("action") == "down"
                printable = len(key) == 1 and not (modifiers & (2 | 4))
                params = {
                    "type": ("keyDown" if printable else "rawKeyDown") if down else "keyUp",
                    "key": key,
                    "code": str(message.get("code") or "")[:32],
                    "windowsVirtualKeyCode": int(message.get("key_code") or 0),
                    "nativeVirtualKeyCode": int(message.get("key_code") or 0),
                    "modifiers": modifiers,
                    "location": int(message.get("location") or 0),
                    "autoRepeat": bool(message.get("repeat")),
                }
                if down and printable:
                    params["text"] = params["unmodifiedText"] = key
                elif down and key == "Enter":
                    params["text"] = "\r"
                await session.send("Input.dispatchKeyEvent", params, timeout=5)
            elif kind == "insert_text":
                text = str(message.get("text") or "")[:_TEXT_LIMIT]
                if text:
                    await session.send("Input.insertText", {"text": text}, timeout=10)
        except (CDPError, CDPDisconnected, TimeoutError):
            pass

    async def navigate(self, viewer: Viewer, message: Dict[str, Any], policy: Any) -> None:
        from ._netpolicy import url_block_reason

        session = self.commands.input_session(viewer)
        if not self.controller.can_inject_input(viewer.id) or session is None:
            return
        action = str(message.get("action") or "")
        try:
            if action == "goto":
                url = str(message.get("url") or "").strip()
                if "://" not in url and url:
                    url = "https://" + url
                reason = url_block_reason(url, policy)
                if reason:
                    viewer.send_json({"type": "error", "code": "blocked", "message": f"Cannot open {url}: {reason}."})
                    return
                await session.send("Page.navigate", {"url": url}, timeout=30)
            elif action == "reload":
                await session.send("Page.reload", timeout=30)
            elif action in ("back", "forward"):
                history = await session.send("Page.getNavigationHistory", timeout=10)
                index = history["currentIndex"] + (-1 if action == "back" else 1)
                entries = history.get("entries") or []
                if 0 <= index < len(entries):
                    await session.send("Page.navigateToHistoryEntry", {"entryId": entries[index]["id"]}, timeout=30)
        except (CDPError, CDPDisconnected, TimeoutError) as exc:
            viewer.send_json({"type": "error", "code": "navigation", "message": str(exc)[:300]})

    async def tab(self, viewer: Viewer, message: Dict[str, Any], policy: Any) -> None:
        if not self.controller.can_inject_input(viewer.id):
            return
        action, target = str(message.get("action") or ""), str(message.get("target_id") or "")
        cdp = self.commands.guarded_session(viewer, self.runtime.cdp)
        try:
            if action == "activate" and target in self.controller.tabs:
                await cdp.send("Target.activateTarget", {"targetId": target}, timeout=10)
                self.controller.active_target_id = target
                self.commands.target_changed()
                await self._refresh()
                self.broadcast(self.tabs_message())
            elif action == "close" and target in self.controller.tabs and len(self.controller.tabs) > 1:
                await cdp.send("Target.closeTarget", {"targetId": target}, timeout=10)
            elif action == "new":
                created = await cdp.send("Target.createTarget", {"url": "about:blank"}, timeout=10)
                self.controller.active_target_id = created.get("targetId")
                self.commands.target_changed()
                await self._refresh()
        except (CDPError, CDPDisconnected, TimeoutError) as exc:
            viewer.send_json({"type": "error", "code": "tab", "message": str(exc)[:300]})

    async def dialog_reply(self, viewer: Viewer, message: Dict[str, Any]) -> None:
        session = self.commands.input_session(viewer)
        if not self.controller.can_inject_input(viewer.id) or session is None:
            return
        params: Dict[str, Any] = {"accept": bool(message.get("accept"))}
        if message.get("prompt_text") is not None:
            params["promptText"] = str(message["prompt_text"])[:_TEXT_LIMIT]
        try:
            await session.send("Page.handleJavaScriptDialog", params, timeout=10)
        except (CDPError, CDPDisconnected, TimeoutError):
            pass

    async def copy(self, viewer: Viewer) -> None:
        session = self.commands.input_session(viewer)
        if not self.controller.can_inject_input(viewer.id) or session is None:
            return
        try:
            result = await session.send(
                "Runtime.evaluate", {"expression": "String(window.getSelection ? window.getSelection() : '')", "returnByValue": True}, timeout=5
            )
        except (CDPError, CDPDisconnected, TimeoutError):
            return
        text = str(((result.get("result") or {}).get("value")) or "")[:_CLIPBOARD_LIMIT]
        viewer.send_json({"type": "clipboard", "text": text})


def get_hub(runtime: Any) -> ScreencastHub:
    hub = getattr(runtime, "hub", None)
    if hub is None:
        hub = ScreencastHub(runtime)
        runtime.hub = hub
    return hub


async def _resolve_attach(owner: str, target: Dict[str, Any]) -> Any:
    """The browser session a viewer may watch, after the ownership checks."""
    from services.plugin.base import NodeUserError

    from ._handlers import _session_for_node
    from ._runtime import get_browser_runtime
    from ._session import SessionKey

    kind = str(target.get("kind") or "")
    if kind == "node":
        _, session = await _session_for_node(owner, str(target.get("workflow_id") or ""), str(target.get("node_id") or ""), create=True)
        return session
    if kind == "profile_login":
        session = get_browser_runtime().session(SessionKey(owner, "profile_login", str(target.get("login_id") or "")).session_id)
        if session is None:
            raise NodeUserError("That login window has closed.")
        return session
    raise NodeUserError("Unknown live-view target")


async def browser_live_view(websocket: WebSocket) -> None:
    from core.container import container
    from services.authz import authenticate_ws
    from services.plugin.base import NodeUserError

    from ._runtime import get_browser_runtime

    owner = await authenticate_ws(websocket, settings=container.settings(), user_auth_service=container.user_auth_service)
    if owner is None:
        return
    await websocket.accept()

    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=_ATTACH_TIMEOUT)
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError):
        await websocket.close(code=CLOSE_ATTACH, reason="attach expected")
        return
    if not isinstance(first, dict) or first.get("type") != "attach":
        await websocket.close(code=CLOSE_ATTACH, reason="attach expected")
        return
    try:
        session = await _resolve_attach(owner, first.get("target") or {})
    except NodeUserError as exc:
        await websocket.close(code=CLOSE_FORBIDDEN, reason=str(exc)[:120])
        return

    viewer = Viewer(websocket, owner)
    _apply_viewport(viewer, first.get("viewport") or {})
    viewer.visible = bool(first.get("visible", True))
    if first.get("max_fps"):
        viewer.max_fps = max(1.0, min(float(first["max_fps"]), 60.0))
    runtime = get_browser_runtime()
    hub: Optional[ScreencastHub] = None

    async def attach_hub() -> Optional[ScreencastHub]:
        running = runtime.running(session.profile_id)
        if running is None:
            return None
        found = get_hub(running)
        await found.add(viewer)
        return found

    hub = await attach_hub()
    controller = runtime.controller(session.profile_id)
    viewer.send_json(
        {
            "type": "attached",
            "v": FRAME_VERSION,
            "viewer_id": viewer.id,
            "session_id": session.session_id,
            "running": hub is not None,
            "viewport": {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        }
    )
    if hub is not None:
        viewer.send_json(hub.state_message(viewer))
        viewer.send_json(hub.tabs_message())
    else:
        viewer.send_json({"type": "idle", "reason": "not running"})

    writer = asyncio.create_task(viewer.writer(lambda v: hub.frame_sent(v) if hub is not None else None))

    async def reader() -> None:
        nonlocal hub
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=_IDLE_POLL)
            except asyncio.TimeoutError:
                if hub is not None and hub.dead:
                    # Chrome stopped under this viewer; a restart gets a new hub.
                    hub = None
                if hub is None:
                    hub = await attach_hub()
                    if hub is not None:
                        viewer.send_json(hub.state_message(viewer))
                        viewer.send_json(hub.tabs_message())
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind in ("mouse", "wheel", "key", "insert_text", "navigate", "tab", "copy", "dialog_reply"):
                if not viewer.bucket.take():
                    await websocket.close(code=CLOSE_RATE, reason="too many input events")
                    return
            if kind == "ping":
                viewer.send_json({"type": "pong"})
            elif kind == "ack":
                viewer.acknowledge(message.get("seq"))
            elif kind == "viewport":
                _apply_viewport(viewer, message)
                if hub is not None:
                    hub.schedule_resize()
            elif kind == "visibility":
                viewer.visible = bool(message.get("visible"))
                if hub is not None:
                    if not viewer.visible:
                        hub.commands.release(viewer, note="the live view was hidden")
                    hub.schedule_resize()
            elif hub is None:
                viewer.send_json({"type": "idle", "reason": "not running"})
            elif kind == "control_request":
                hub.commands.enqueue(viewer, message, session)
            elif kind == "control_release":
                outcome = "declined" if message.get("outcome") == "declined" else "handed_back"
                hub.commands.release(viewer, outcome=outcome, note=str(message.get("note") or "")[:1000])
            elif kind in ("mouse", "wheel", "key", "insert_text", "navigate", "tab", "dialog_reply", "copy"):
                hub.commands.enqueue(viewer, message, session)

    try:
        await reader()
    except (WebSocketDisconnect, RuntimeError):
        pass
    except NodeUserError as exc:
        viewer.send_json({"type": "error", "code": "control", "message": str(exc)[:300]})
    finally:
        viewer.closed = True
        viewer.wakeup.set()
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        if hub is not None:
            await hub.remove(viewer)
        elif controller is not None:
            controller.viewer_detached(viewer.id)


def _apply_viewport(viewer: Viewer, data: Dict[str, Any]) -> None:
    try:
        viewer.width = max(80, min(int(float(data.get("width") or viewer.width)), 4000))
        viewer.height = max(60, min(int(float(data.get("height") or viewer.height)), 4000))
        viewer.dpr = max(0.5, min(float(data.get("dpr") or viewer.dpr), 3.0))
    except (TypeError, ValueError):
        pass


__all__ = ["CLOSE_FORBIDDEN", "ScreencastHub", "Viewer", "browser_live_view", "encode_frame", "get_hub"]
