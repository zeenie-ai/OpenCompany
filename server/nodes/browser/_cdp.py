"""A small asyncio Chrome DevTools Protocol client.

The backend keeps its own CDP connection to each profile's Chrome, next to
the one the browser-use CLI daemon holds. It uses it for what needs a
persistent connection or events: the live-view screencast, the user's mouse
and keyboard input, WebMCP, cookie import and export, and tab tracking.
Chrome allows several CDP clients on one browser.

Built on ``websockets`` (already a server dependency) instead of a CDP
library: the backend needs request/response correlation, flattened target
sessions and event routing, nothing more. No ``Origin`` header is sent, so
Chrome needs no ``--remote-allow-origins`` flag, which would let web pages
reach the debugging port.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from core.logging import get_logger

logger = get_logger(__name__)

EventCallback = Callable[[Dict[str, Any]], Union[None, Awaitable[None]]]

#: A full-page screenshot or a large DOM snapshot can be tens of MB of base64.
_MAX_MESSAGE_BYTES = 512 * 1024 * 1024
_DEFAULT_TIMEOUT = 30.0


class CDPError(Exception):
    """Chrome answered a command with an error."""

    def __init__(self, method: str, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"{method}: {message} ({code})")
        self.method = method
        self.code = code
        self.message = message
        self.data = data

    @property
    def method_not_found(self) -> bool:
        return self.code == -32601


class CDPDisconnected(ConnectionError):
    """The connection to Chrome closed."""


def read_devtools_active_port(user_data_dir: Path) -> Optional[Tuple[int, str]]:
    """``(port, browser_path)`` from Chrome's ``DevToolsActivePort`` file.

    Chrome writes it once the debugging server listens: the port on line 1,
    ``/devtools/browser/<id>`` on line 2. ``None`` while it is absent or only
    partly written.
    """
    try:
        text = (user_data_dir / "DevToolsActivePort").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].isdigit() or not lines[1].startswith("/devtools/"):
        return None
    port = int(lines[0])
    if not 0 < port < 65536:
        return None
    return port, lines[1]


class CDPConnection:
    """One WebSocket to a browser endpoint (``ws://127.0.0.1:<port>/devtools/browser/<id>``)."""

    def __init__(self, websocket: Any) -> None:
        self._ws = websocket
        self._next_id = 0
        self._pending: Dict[int, Tuple[str, asyncio.Future]] = {}
        self._listeners: Dict[Tuple[Optional[str], str], List[EventCallback]] = {}
        self._wildcard: List[EventCallback] = []
        self._closed = asyncio.Event()
        self._close_reason: Optional[str] = None
        self._reader = asyncio.create_task(self._read_loop(), name="cdp-reader")

    @classmethod
    async def connect(cls, url: str, *, open_timeout: float = 10.0) -> "CDPConnection":
        from websockets.asyncio.client import connect

        websocket = await connect(
            url,
            max_size=_MAX_MESSAGE_BYTES,
            open_timeout=open_timeout,
            ping_interval=None,
            compression=None,
            origin=None,
        )
        return cls(websocket)

    # -- lifecycle ----------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        if not self._closed.is_set():
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass
        self._reader.cancel()
        try:
            await self._reader
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._mark_closed("closed by client")

    def _mark_closed(self, reason: str) -> None:
        if self._closed.is_set():
            return
        self._close_reason = reason
        self._closed.set()
        pending, self._pending = self._pending, {}
        for method, future in pending.values():
            if not future.done():
                future.set_exception(CDPDisconnected(f"{method}: connection to Chrome closed ({reason})"))

    # -- commands -----------------------------------------------------------

    async def send(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        session_id: Optional[str] = None,
        timeout: Optional[float] = _DEFAULT_TIMEOUT,
    ) -> Dict[str, Any]:
        if self._closed.is_set():
            raise CDPDisconnected(f"{method}: connection to Chrome is closed ({self._close_reason})")
        self._next_id += 1
        message_id = self._next_id
        message: Dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = (method, future)
        try:
            await self._ws.send(json.dumps(message))
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"{method}: Chrome did not answer within {timeout} s") from None
        except Exception as exc:
            if isinstance(exc, (CDPError, CDPDisconnected)):
                raise
            if self._closed.is_set():
                raise CDPDisconnected(f"{method}: connection to Chrome closed") from exc
            raise
        finally:
            self._pending.pop(message_id, None)

    # -- events -------------------------------------------------------------

    def on(self, method: str, callback: EventCallback, *, session_id: Optional[str] = None) -> Callable[[], None]:
        """Call ``callback(params)`` for each ``method`` event. ``method="*"``
        receives every event as ``{"method", "params", "sessionId"}``."""
        if method == "*":
            self._wildcard.append(callback)
            return lambda: self._wildcard.remove(callback) if callback in self._wildcard else None
        key = (session_id, method)
        self._listeners.setdefault(key, []).append(callback)

        def unsubscribe() -> None:
            listeners = self._listeners.get(key)
            if listeners and callback in listeners:
                listeners.remove(callback)

        return unsubscribe

    async def wait_for_event(
        self,
        method: str,
        *,
        session_id: Optional[str] = None,
        predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> Dict[str, Any]:
        future: asyncio.Future = asyncio.get_running_loop().create_future()

        def _match(params: Dict[str, Any]) -> None:
            if not future.done() and (predicate is None or predicate(params)):
                future.set_result(params)

        unsubscribe = self.on(method, _match, session_id=session_id)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            unsubscribe()

    def _dispatch(self, method: str, params: Dict[str, Any], session_id: Optional[str]) -> None:
        # A listener hears only its own session's events (``None`` = the
        # browser target); the wildcard hears everything.
        callbacks = list(self._listeners.get((session_id, method), ()))
        for callback in callbacks:
            self._invoke(callback, params)
        if self._wildcard:
            envelope = {"method": method, "params": params, "sessionId": session_id}
            for callback in list(self._wildcard):
                self._invoke(callback, envelope)

    @staticmethod
    def _invoke(callback: EventCallback, payload: Dict[str, Any]) -> None:
        try:
            result = callback(payload)
            if inspect.isawaitable(result):
                task = asyncio.ensure_future(result)
                task.add_done_callback(lambda t: t.cancelled() or t.exception())
        except Exception:  # noqa: BLE001 - one bad listener must not stop the reader
            logger.debug("[cdp] event listener failed", exc_info=True)

    async def _read_loop(self) -> None:
        reason = "closed by Chrome"
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if "id" in message:
                    entry = self._pending.get(message["id"])
                    if entry is None:
                        continue
                    method, future = entry
                    if future.done():
                        continue
                    if "error" in message:
                        error = message["error"] or {}
                        future.set_exception(
                            CDPError(method, int(error.get("code", 0)), str(error.get("message", "error")), error.get("data"))
                        )
                    else:
                        future.set_result(message.get("result") or {})
                elif "method" in message:
                    self._dispatch(message["method"], message.get("params") or {}, message.get("sessionId"))
        except asyncio.CancelledError:
            reason = "closed by client"
            raise
        except Exception as exc:  # noqa: BLE001 - any transport failure ends the connection
            reason = f"{type(exc).__name__}: {exc}"
        finally:
            self._mark_closed(reason)

    # -- targets ------------------------------------------------------------

    async def attach(self, target_id: str) -> "CDPSession":
        result = await self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        return CDPSession(self, result["sessionId"], target_id)

    async def page_targets(self) -> List[Dict[str, Any]]:
        result = await self.send("Target.getTargets")
        return [t for t in result.get("targetInfos", []) if t.get("type") == "page"]


class CDPSession:
    """A flattened session on one target, sharing the browser connection."""

    def __init__(self, connection: CDPConnection, session_id: str, target_id: str) -> None:
        self.connection = connection
        self.session_id = session_id
        self.target_id = target_id

    async def send(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: Optional[float] = _DEFAULT_TIMEOUT) -> Dict[str, Any]:
        return await self.connection.send(method, params, session_id=self.session_id, timeout=timeout)

    def on(self, method: str, callback: EventCallback) -> Callable[[], None]:
        return self.connection.on(method, callback, session_id=self.session_id)

    async def wait_for_event(
        self, method: str, *, predicate: Optional[Callable[[Dict[str, Any]], bool]] = None, timeout: float = _DEFAULT_TIMEOUT
    ) -> Dict[str, Any]:
        return await self.connection.wait_for_event(method, session_id=self.session_id, predicate=predicate, timeout=timeout)

    async def detach(self) -> None:
        if self.connection.is_closed:
            return
        try:
            await self.connection.send("Target.detachFromTarget", {"sessionId": self.session_id}, timeout=5.0)
        except (CDPError, CDPDisconnected, TimeoutError):
            pass


__all__ = ["CDPConnection", "CDPDisconnected", "CDPError", "CDPSession", "read_devtools_active_port"]
