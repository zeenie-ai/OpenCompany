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
goes out after the first viewer's send completes (or 250 ms). Input reaches
the page only from the viewer that holds control, in the USER state.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

from core.logging import get_logger

from ._cdp import CDPDisconnected, CDPError, CDPSession
from ._chrome import VIEWPORT_HEIGHT, VIEWPORT_WIDTH

logger = get_logger(__name__)

FRAME_VERSION = 1
FRAME_KIND_JPEG = 1
_WINDOW = 2
_ACK_AFTER = 0.25
_MAX_FRAME_EDGE = 1920
_CONTROL_QUEUE = 256
_INPUT_RATE = 200.0  # messages per second
_INPUT_BURST = 400.0
_TEXT_LIMIT = 10_000
_CLIPBOARD_LIMIT = 100_000
_ATTACH_TIMEOUT = 5.0
_IDLE_POLL = 2.0

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

    def send_json(self, message: Dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            self.control.put_nowait(message)
        except asyncio.QueueFull:
            self.closed = True
        self.wakeup.set()

    def offer_frame(self, frame: bytes) -> bool:
        """Queue a frame; ``False`` when it was dropped (throttled)."""
        if self.closed or not self.visible:
            return False
        if self.max_fps and time.monotonic() - self.last_frame_at < 1.0 / self.max_fps:
            return False
        self.pending = frame
        self.wakeup.set()
        return True

    async def writer(self, on_sent: Any) -> None:
        while not self.closed:
            await self.wakeup.wait()
            self.wakeup.clear()
            while not self.control.empty():
                await self.websocket.send_text(json.dumps(self.control.get_nowait(), default=str))
            if self.pending is not None and self.inflight < _WINDOW:
                frame, self.pending = self.pending, None
                self.inflight += 1
                self.last_frame_at = time.monotonic()
                await self.websocket.send_bytes(frame)
                on_sent(self)


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
        self._pending_ack: Optional[int] = None
        self._ack_timer: Optional[asyncio.TimerHandle] = None
        self._unsubscribers: List[Any] = []
        self._lock = asyncio.Lock()
        self.dead = False
        self._unlisten = self.controller.add_listener(self._on_controller)

    # -- viewers -------------------------------------------------------------------

    async def add(self, viewer: Viewer) -> None:
        self.viewers[viewer.id] = viewer
        self.controller.viewer_attached(viewer.id)
        await self._refresh()

    async def remove(self, viewer: Viewer) -> None:
        self.viewers.pop(viewer.id, None)
        self.controller.viewer_detached(viewer.id)
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
            if size == (0, 0):
                await self._stop_screencast_locked()
                return
            if not self._active or size != self._size or self.target_id != self.controller.active_target_id:
                await self._start_screencast_locked(size)

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
            return
        self.session, self.target_id = session, session.target_id
        self._unsubscribers = [
            session.on("Page.screencastFrame", self._on_frame),
            session.on("Page.javascriptDialogOpening", self._on_dialog),
        ]
        try:
            await session.send("Page.enable", timeout=10)
            await session.send("Emulation.setFocusEmulationEnabled", {"enabled": True}, timeout=10)
            await session.send(
                "Page.startScreencast",
                {"format": "jpeg", "quality": 60, "maxWidth": size[0], "maxHeight": size[1], "everyNthFrame": 1, "maxFramesInFlight": _WINDOW},
                timeout=10,
            )
        except (CDPError, CDPDisconnected, TimeoutError) as exc:
            logger.debug("[browser] startScreencast failed: %s", exc)
            await self._stop_screencast_locked()
            return
        self._active, self._size = True, size
        self.broadcast({"type": "page", "target_id": self.target_id, **self._tab_facts(self.target_id)})

    def _tab_facts(self, target_id: Optional[str]) -> Dict[str, Any]:
        tab = self.controller.tabs.get(target_id or "") or {}
        return {"url": tab.get("url"), "title": tab.get("title")}

    async def _stop_screencast(self) -> None:
        async with self._lock:
            await self._stop_screencast_locked()

    async def _stop_screencast_locked(self) -> None:
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

    def _on_frame(self, params: Dict[str, Any]) -> None:
        self.seq += 1
        meta = params.get("metadata") or {}
        try:
            jpeg = base64.b64decode(params.get("data") or "")
        except ValueError:
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
        offered = False
        for viewer in self.visible_viewers():
            offered = viewer.offer_frame(frame) or offered
        self._pending_ack = params.get("sessionId")
        if not offered:
            self._ack_now()
        elif self._ack_timer is None:
            self._ack_timer = asyncio.get_running_loop().call_later(_ACK_AFTER, self._ack_now)

    def frame_sent(self, viewer: Viewer) -> None:
        self._ack_now()

    def _ack_now(self) -> None:
        if self._ack_timer is not None:
            self._ack_timer.cancel()
            self._ack_timer = None
        session_id, self._pending_ack = self._pending_ack, None
        session = self.session
        if session_id is None or session is None:
            return
        task = asyncio.ensure_future(session.send("Page.screencastFrameAck", {"sessionId": session_id}, timeout=5))
        task.add_done_callback(lambda t: t.cancelled() or t.exception())

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
        if not self.controller.can_inject_input(viewer.id) or self.session is None:
            return
        self.controller.touch_user_input()
        session, kind = self.session, message.get("type")
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

        if not self.controller.can_inject_input(viewer.id) or self.session is None:
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
                await self.session.send("Page.navigate", {"url": url}, timeout=30)
            elif action == "reload":
                await self.session.send("Page.reload", timeout=30)
            elif action in ("back", "forward"):
                history = await self.session.send("Page.getNavigationHistory", timeout=10)
                index = history["currentIndex"] + (-1 if action == "back" else 1)
                entries = history.get("entries") or []
                if 0 <= index < len(entries):
                    await self.session.send("Page.navigateToHistoryEntry", {"entryId": entries[index]["id"]}, timeout=30)
        except (CDPError, CDPDisconnected, TimeoutError) as exc:
            viewer.send_json({"type": "error", "code": "navigation", "message": str(exc)[:300]})

    async def tab(self, viewer: Viewer, message: Dict[str, Any], policy: Any) -> None:
        if not self.controller.can_inject_input(viewer.id):
            return
        action, target = str(message.get("action") or ""), str(message.get("target_id") or "")
        cdp = self.runtime.cdp
        try:
            if action == "activate" and target in self.controller.tabs:
                await cdp.send("Target.activateTarget", {"targetId": target}, timeout=10)
                self.controller.active_target_id = target
                await self._refresh()
                self.broadcast(self.tabs_message())
            elif action == "close" and target in self.controller.tabs and len(self.controller.tabs) > 1:
                await cdp.send("Target.closeTarget", {"targetId": target}, timeout=10)
            elif action == "new":
                created = await cdp.send("Target.createTarget", {"url": "about:blank"}, timeout=10)
                self.controller.active_target_id = created.get("targetId")
                await self._refresh()
        except (CDPError, CDPDisconnected, TimeoutError) as exc:
            viewer.send_json({"type": "error", "code": "tab", "message": str(exc)[:300]})

    async def dialog_reply(self, viewer: Viewer, message: Dict[str, Any]) -> None:
        if not self.controller.can_inject_input(viewer.id) or self.session is None:
            return
        params: Dict[str, Any] = {"accept": bool(message.get("accept"))}
        if message.get("prompt_text") is not None:
            params["promptText"] = str(message["prompt_text"])[:_TEXT_LIMIT]
        try:
            await self.session.send("Page.handleJavaScriptDialog", params, timeout=10)
        except (CDPError, CDPDisconnected, TimeoutError):
            pass

    async def copy(self, viewer: Viewer) -> None:
        if not self.controller.can_inject_input(viewer.id) or self.session is None:
            return
        try:
            result = await self.session.send(
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
                viewer.inflight = max(0, viewer.inflight - 1)
                viewer.wakeup.set()
            elif kind == "viewport":
                _apply_viewport(viewer, message)
                if hub is not None:
                    hub.schedule_resize()
            elif kind == "visibility":
                viewer.visible = bool(message.get("visible"))
                if hub is not None:
                    await hub._refresh()
            elif hub is None:
                viewer.send_json({"type": "idle", "reason": "not running"})
            elif kind == "control_request":
                try:
                    await hub.controller.acquire_lease(session, wait=2.0)
                except NodeUserError as exc:
                    viewer.send_json({"type": "control", "granted": False, "reason": "held_by_other", "message": str(exc)[:300]})
                    continue
                granted, reason = await hub.controller.take_over(viewer.id, force=bool(message.get("force")))
                viewer.send_json({"type": "control", "granted": granted, "reason": reason})
            elif kind == "control_release":
                outcome = "declined" if message.get("outcome") == "declined" else "handed_back"
                await hub.controller.hand_back(viewer.id, outcome=outcome, note=str(message.get("note") or "")[:1000])
            elif kind in ("mouse", "wheel", "key", "insert_text"):
                await hub.input(viewer, message)
            elif kind == "navigate":
                await hub.navigate(viewer, message, session.policy)
            elif kind == "tab":
                await hub.tab(viewer, message, session.policy)
            elif kind == "dialog_reply":
                await hub.dialog_reply(viewer, message)
            elif kind == "copy":
                await hub.copy(viewer)

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
