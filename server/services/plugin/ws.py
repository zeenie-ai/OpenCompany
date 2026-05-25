"""WebSocket handler helpers (Wave 11.I, milestone T-residual).

Single helper today: :func:`ws_response`, an opt-in decorator that
collapses the duplicated ``try / except / return {success: False,
error: str(e)}`` block at the bottom of every plugin WS handler.

Audited 79 instances of this exact pattern across ``nodes/<plugin>/``
in the pre-T-residual codebase. The decorator is **opt-in** because
not every handler returns the ``{success: bool}`` envelope -- OAuth
lifecycle handlers built by
:mod:`services.events.oauth_lifecycle` return ``{connected: bool}``,
auto-wrapping would clobber that. Plugin authors decorate per-handler.

Honors the :class:`services.plugin.base.NodeUserError` contract: when
a handler raises ``NodeUserError`` (user-correctable failure -- bad
input, unknown enum value, missing required field), the wrapper logs
at WARN without a traceback. Genuinely unexpected exceptions log at
ERROR with full traceback via ``logger.exception``.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Awaitable, Callable, Dict

from fastapi import WebSocket

from core.logging import get_logger
from services.plugin.base import NodeUserError

logger = get_logger(__name__)

WSHandlerFn = Callable[[Dict[str, Any], WebSocket], Awaitable[Dict[str, Any]]]


def ws_response(handler: WSHandlerFn) -> WSHandlerFn:
    """Wrap a WS handler to convert exceptions into a ``{success: False,
    error: ...}`` envelope.

    Successful return values pass through untouched -- the wrapper
    only intervenes on exceptions. Extra fields the handler adds to
    the success envelope (``connected``, ``message``, ``result``, ...)
    are preserved.

    Two log levels:

    * :class:`NodeUserError` -> WARN, no traceback. Reason: these are
      expected and user-correctable; a stack trace just clutters the
      operator log without adding signal.
    * Anything else -> ERROR via ``logger.exception`` (full traceback).
      Reason: it's a server bug, the trace is the diagnostic.

    Usage::

        from services.plugin.ws import ws_response

        @ws_response
        async def handle_thing(data, websocket):
            ...
            return {"success": True, "result": ...}
    """

    handler_name = getattr(handler, "__name__", repr(handler))

    @wraps(handler)
    async def wrapper(
        data: Dict[str, Any],
        websocket: WebSocket,
    ) -> Dict[str, Any]:
        try:
            return await handler(data, websocket)
        except NodeUserError as exc:
            logger.warning(f"[{handler_name}] {type(exc).__name__}: {exc}")
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 -- surface as envelope
            logger.exception(f"[{handler_name}] unexpected error")
            return {"success": False, "error": str(exc)}

    return wrapper


__all__ = ["ws_response"]
