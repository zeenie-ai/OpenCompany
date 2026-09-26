"""Bounded, ordered user commands independent of the live socket reader.

One queue belongs to one profile hub. Release invalidates queued work at
admission time, then waits for the dispatched command before releasing any
pressed inputs. Chrome commands already sent are never assumed cancelled.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from services.plugin.base import NodeUserError
from ._cdp import CDPDisconnected

MAX_COMMANDS = 64
MAX_BYTES = 64 * 1024
INPUT_KINDS = frozenset({"mouse", "wheel", "key", "insert_text", "navigate", "tab", "copy", "dialog_reply"})


@dataclass
class Command:
    viewer: Any
    message: dict
    session: Any
    epoch: int
    target: Any
    size: int
    queued_at: float


class _GuardedSession:
    def __init__(self, queue: "LiveControlQueue", command: Command, session: Any):
        self.queue, self.command, self.session = queue, command, session

    async def send(self, method, params=None, **kwargs):
        if not self.queue.valid(self.command):
            raise NodeUserError("Browser control or page changed; the queued command was discarded.")
        params = params or {}
        identity = None
        if method == "Input.dispatchKeyEvent":
            identity = (self.command.viewer.id, "key", params.get("code") or params.get("key"))
        elif method == "Input.dispatchMouseEvent" and params.get("type") in {"mousePressed", "mouseReleased"}:
            identity = (self.command.viewer.id, "mouse", params.get("button"))
        # Record before dispatch: even a response timeout can leave a key held.
        if identity and params.get("type") not in {"keyUp", "mouseReleased"}:
            if identity not in self.queue._pressed and len(self.queue._pressed) >= MAX_COMMANDS:
                raise NodeUserError("Too many held browser inputs. Hand control back before continuing.")
            self.queue._pressed[identity] = (self.session, dict(params))
        started = time.monotonic()
        try:
            result = await self.session.send(method, params, **kwargs)
        except (TimeoutError, CDPDisconnected):
            self.queue._unsafe = True
            self.queue.invalidate(self.command.viewer.id)
            self.queue._error(self.command.viewer, "control_uncertain", "Chrome did not confirm the action. Hand back control to safely restart the browser.")
            raise
        finally:
            self.queue._observe("command_cdp", time.monotonic() - started)
        if identity and params.get("type") in {"keyUp", "mouseReleased"}:
            self.queue._pressed.pop(identity, None)
        return result


class LiveControlQueue:
    def __init__(self, hub: Any):
        self.hub = hub
        self.controller = hub.controller
        self._queue: deque[Command] = deque()
        self._bytes = 0
        self._epochs: dict[str, int] = {}
        self._blocked: set[str] = set()
        self._worker: asyncio.Task | None = None
        self._takeover: asyncio.Task | None = None
        self._takeover_viewer: str | None = None
        self._takeover_pending: set[str] = set()
        self._lock = asyncio.Lock()
        self._current: Command | None = None
        self._pressed: dict[tuple, tuple[Any, dict]] = {}
        self._closed = False
        self._unsafe = False
        self._retiring = False
        self._input_session = None
        self._input_target = None
        self._input_epoch = None
        self._target_epoch = 0
        self._last_target = None
        self.controller.live_control_barrier = self.barrier

    def _observe(self, name: str, value: float) -> None:
        metrics = getattr(self.hub, "metrics", None)
        if metrics is not None:
            metrics.observe(name, value)

    def _error(self, viewer, code, message):
        viewer.send_json({"type": "error", "code": code, "message": message})

    def _target(self):
        target = self.controller.active_target_id
        current = target, (self.controller.tabs.get(target or "") or {}).get("url")
        if current != self._last_target:
            self._last_target = current
            self._target_epoch += 1
        return self._target_epoch, current

    def target_changed(self):
        self._target()

    def valid(self, command: Command, *, owner: bool = True) -> bool:
        viewer = command.viewer
        return (
            not self._closed and not viewer.closed and viewer.visible
            and (not owner or viewer.id not in self._blocked)
            and command.epoch == self._epochs.get(viewer.id, 0)
            and command.target == self._target()
            and (not owner or self.controller.can_inject_input(viewer.id))
        )

    def guarded_session(self, viewer, session):
        command = self._current
        if command is not None and command.viewer.id == viewer.id and session is not None:
            return _GuardedSession(self, command, session)
        return session

    def input_session(self, viewer):
        # Dedicated to input: the screencast session may be detached whenever
        # a viewer resizes or becomes hidden.
        return self.guarded_session(viewer, self._input_session)

    async def _ensure_input_session(self):
        target = self.controller.active_target_id
        epoch = self._target()
        if self._input_session is not None and self._input_target == target and self._input_epoch == epoch:
            return
        if self._input_session is not None:
            for viewer_id in {identity[0] for identity in self._pressed}:
                await self._release_pressed(viewer_id)
            if self._input_target == target:
                self._input_epoch = epoch
                return
            await self._input_session.detach()
        self._input_session = await self.hub.runtime.page_session(target)
        self._input_target = target
        self._input_epoch = epoch

    def enqueue(self, viewer, message: dict, session) -> bool:
        if self._closed or viewer.closed or not viewer.visible:
            return False
        kind = message.get("type")
        if kind == "control_request" and viewer.id in self._takeover_pending:
            return False
        if kind in INPUT_KINDS and (viewer.id in self._blocked or self._unsafe or not self.controller.can_inject_input(viewer.id)):
            return False
        size = len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
        command = Command(viewer, dict(message), session, self._epochs.get(viewer.id, 0), self._target(), size, time.monotonic())
        previous = self._queue[-1] if self._queue else None
        if previous and self._coalescible(previous, command):
            if kind == "wheel":
                command.message["delta_x"] = float(previous.message.get("delta_x") or 0) + float(message.get("delta_x") or 0)
                command.message["delta_y"] = float(previous.message.get("delta_y") or 0) + float(message.get("delta_y") or 0)
                command.size = len(json.dumps(command.message).encode("utf-8"))
            if self._bytes - previous.size + command.size <= MAX_BYTES:
                self._queue[-1] = command
                self._bytes += command.size - previous.size
                return True
        if len(self._queue) >= MAX_COMMANDS or self._bytes + command.size > MAX_BYTES:
            self._error(viewer, "control_overloaded", "Browser input is busy. Wait for the current action, then try again.")
            # Never silently lose a key-up on overflow: stop admission and
            # release everything after the current command settles.
            self.release(viewer, note="input queue overflow")
            return False
        if kind == "control_request":
            self._takeover_pending.add(viewer.id)
        self._queue.append(command)
        self._bytes += command.size
        self._ensure_worker()
        return True

    @staticmethod
    def _coalescible(a: Command, b: Command) -> bool:
        if (a.viewer.id, a.epoch, a.target) != (b.viewer.id, b.epoch, b.target):
            return False
        x, y = a.message, b.message
        if x.get("modifiers", 0) != y.get("modifiers", 0) or x.get("type") != y.get("type"):
            return False
        wheel = y.get("type") == "wheel" and all(x.get(k, 0) == y.get(k, 0) for k in ("x", "y", "buttons"))
        return (wheel or (
            y.get("type") == "mouse" and x.get("action") == y.get("action") == "move"
            and x.get("buttons", 0) == y.get("buttons", 0)
        ))

    def invalidate(self, viewer_id: str) -> None:
        self._blocked.add(viewer_id)
        self._epochs[viewer_id] = self._epochs.get(viewer_id, 0) + 1
        for command in self._queue:
            if command.viewer.id == viewer_id and command.message.get("type") == "control_request":
                command.viewer.send_json({"type": "control", "granted": False, "reason": "viewer_unavailable"})
        self._queue = deque(c for c in self._queue if c.viewer.id != viewer_id)
        self._bytes = sum(c.size for c in self._queue)
        self._takeover_pending.discard(viewer_id)
        if self._takeover_viewer == viewer_id and self._takeover is not None:
            self._takeover.cancel()

    def release(self, viewer, *, outcome="handed_back", note="") -> None:
        self.invalidate(viewer.id)
        if getattr(self.controller, "controller_viewer", None) != viewer.id and getattr(self.controller, "state", None) != "awaiting_user":
            return
        # A zero-byte priority barrier is always admissible. At most one per
        # viewer remains because invalidate removes its previous barrier.
        if len(self._queue) >= MAX_COMMANDS:
            removed = self._queue.pop()
            self._bytes -= removed.size
            self._takeover_pending.discard(removed.viewer.id)
            self._error(removed.viewer, "control_overloaded", "Browser control changed. Try your request again.")
        self._queue.appendleft(Command(viewer, {"type": "control_release", "outcome": outcome, "note": note}, None, 0, None, 0, time.monotonic()))
        self._ensure_worker()

    async def barrier(self, viewer_id: str | None) -> None:
        if viewer_id is None:
            return
        self.invalidate(viewer_id)
        if asyncio.current_task() in (self._worker, self._takeover):
            await self._release_pressed(viewer_id)
        else:
            async with self._lock:
                await self._release_pressed(viewer_id)

    async def _release_pressed(self, viewer_id):
        if self._unsafe:
            await self._retire_uncertain_browser()
            return
        for identity, (session, params) in list(self._pressed.items()):
            if identity[0] != viewer_id:
                continue
            release = dict(params)
            if identity[1] == "key":
                release.update(type="keyUp", modifiers=0)
                release.pop("text", None)
                release.pop("unmodifiedText", None)
                method = "Input.dispatchKeyEvent"
            else:
                release.update(type="mouseReleased", buttons=0, modifiers=0)
                method = "Input.dispatchMouseEvent"
            try:
                await session.send(method, release, timeout=5)
            except Exception as exc:
                self._unsafe = True
                try:
                    await self._retire_uncertain_browser()
                    return
                except Exception:
                    raise NodeUserError("Could not release browser input safely. Restart the browser before handing control back.") from exc
            self._pressed.pop(identity, None)

    async def _retire_uncertain_browser(self):
        # A local timeout only abandons the reply; Chrome may still execute
        # the command. Closing this managed process is the safety boundary.
        try:
            self._retiring = True
            await asyncio.wait_for(self.hub.runtime.chrome.shutdown(), timeout=10)
        except Exception as exc:
            raise NodeUserError("Chrome has an unconfirmed action and could not be stopped. Control remains with you.") from exc
        finally:
            self._retiring = False
        self._unsafe = False
        self._pressed.clear()
        self._input_session = None
        self._input_target = None
        self._input_epoch = None

    def _ensure_worker(self):
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="browser-live-control")

    async def _run(self):
        while self._queue:
            command = self._queue.popleft()
            self._bytes -= command.size
            viewer, message = command.viewer, command.message
            kind = message.get("type")
            async with self._lock:
                self._current = command
                try:
                    self._observe("command_queue_wait", time.monotonic() - command.queued_at)
                    if kind == "control_release":
                        await self.controller.hand_back(viewer.id, outcome=message.get("outcome", "handed_back"), note=message.get("note", ""))
                    elif kind == "control_request":
                        if not self.valid(command, owner=False):
                            continue
                        self._takeover_viewer = viewer.id
                        self._takeover = asyncio.create_task(self._claim(command))
                        try:
                            await self._takeover
                        except asyncio.CancelledError:
                            viewer.send_json({"type": "control", "granted": False, "reason": "viewer_unavailable"})
                            if self._closed:
                                return
                        finally:
                            self._takeover = None
                            self._takeover_viewer = None
                    elif self.valid(command):
                        if hasattr(self.hub, "runtime") and hasattr(self.hub.runtime, "page_session"):
                            await self._ensure_input_session()
                        self.controller.touch_user_input()
                        if kind in {"mouse", "wheel", "key", "insert_text"}:
                            await self.hub.input(viewer, message)
                        elif kind in {"navigate", "tab"}:
                            await self._release_pressed(viewer.id)
                            await getattr(self.hub, kind)(viewer, message, command.session.policy)
                        elif kind == "copy":
                            await self.hub.copy(viewer)
                        elif kind == "dialog_reply":
                            await self.hub.dialog_reply(viewer, message)
                except Exception as exc:
                    self._error(viewer, "control", str(exc)[:300])
                    if kind == "control_request":
                        viewer.send_json({"type": "control", "granted": False, "reason": "control_failed", "message": str(exc)[:300]})
                finally:
                    if kind == "control_request":
                        self._takeover_pending.discard(viewer.id)
                    self._current = None

    async def _claim(self, command):
        viewer = command.viewer
        await self.controller.acquire_lease(command.session, wait=2.0)
        if not self.valid(command, owner=False):
            return
        granted, reason = await self.controller.take_over(
            viewer.id, force=bool(command.message.get("force")),
            valid=lambda: self.valid(command, owner=False),
        )
        viewer.send_json({"type": "control", "granted": granted, "reason": reason})
        if granted:
            self._blocked.discard(viewer.id)

    async def close(self):
        self._closed = True
        if self._takeover is not None:
            self._takeover.cancel()
        self._queue.clear()
        self._bytes = 0
        if self._retiring:
            # Chrome's close notification can run inside shutdown(). Waiting
            # for the worker here would wait on our own shutdown barrier.
            return
        # Let already-dispatched CDP operations finish; their normal protocol
        # timeout still bounds this wait. Do not tell agents input has settled
        # merely because a Python task was cancelled.
        if self._worker is not None and self._worker is not asyncio.current_task():
            await asyncio.gather(self._worker, return_exceptions=True)
        if self._input_session is not None:
            await self._input_session.detach()
            self._input_session = None

    async def idle_without_viewers(self):
        """Retain the reusable hub, but no command task/session after disposal."""
        if self._worker is not None and self._worker is not asyncio.current_task():
            await asyncio.gather(self._worker, return_exceptions=True)
        async with self._lock:
            if getattr(self.hub, "viewers", None):
                return
            for viewer_id in {identity[0] for identity in self._pressed}:
                await self._release_pressed(viewer_id)
            if self._input_session is not None:
                await self._input_session.detach()
                self._input_session = None
            self._input_target = self._input_epoch = None
            self._epochs.clear()
            self._blocked.clear()
