"""Finish closing a connection that Windows has already dropped.

On Windows, asyncio ends a connection in
``_ProactorBasePipeTransport._call_connection_lost``: it tells the protocol,
calls ``sock.shutdown(SHUT_RDWR)``, then closes the socket and detaches the
connection from its server. When Windows has already dropped the connection
(the computer slept, the network went away), ``shutdown`` raises
``OSError: [WinError 10022] An invalid argument was supplied`` and the rest
never runs. asyncio logs "Exception in callback
_ProactorBasePipeTransport._call_connection_lost(None)" with a traceback,
the socket stays open until garbage collection, and the server still counts
the connection: uvicorn's shutdown then waits in ``server.wait_closed()``
until ``--timeout-graceful-shutdown`` runs out (errors.md #37).

The shutdown is only there to settle a pending overlapped read (the
stdlib's own comment says so), so when Windows refuses it there is nothing
left to shut down. The guard hands the stdlib a socket whose refused
shutdown is logged at DEBUG instead of raised, and the stdlib's own cleanup
runs to the end. CPython 3.12.15 and 3.13.1 still call ``shutdown``
unguarded; ``tests/core_config/test_windows_sockets.py`` fails once a
Python release stops skipping that cleanup, and then the guard can go.
"""

from __future__ import annotations

import functools
import sys
from typing import Any, Callable

from core.logging import get_logger

logger = get_logger(__name__)

CallConnectionLost = Callable[[Any, Any], None]


class _SocketWithTolerantShutdown:
    """A transport's socket during its last cleanup: a shutdown the OS
    refuses is logged, not raised. Everything else is the socket's own."""

    __slots__ = ("_sock",)

    def __init__(self, sock: Any) -> None:
        self._sock = sock

    def shutdown(self, how: int) -> None:
        try:
            self._sock.shutdown(how)
        except OSError as error:
            logger.debug("Connection was already dropped when it closed", error=str(error))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)


def tolerate_failed_shutdown(call_connection_lost: CallConnectionLost) -> CallConnectionLost:
    """Wrap the stdlib's ``_call_connection_lost`` so a refused shutdown
    no longer stops the socket closing and the server letting go of it."""

    @functools.wraps(call_connection_lost)
    def guarded(transport: Any, exc: Any) -> None:
        sock = transport._sock
        # A pipe has no shutdown, and the stdlib skips it for one too.
        if sock is not None and hasattr(sock, "shutdown"):
            transport._sock = _SocketWithTolerantShutdown(sock)
        call_connection_lost(transport, exc)

    guarded.tolerates_failed_shutdown = True  # type: ignore[attr-defined]
    return guarded


def install_shutdown_guard() -> bool:
    """Guard every asyncio connection on Windows, once; elsewhere change
    nothing (no other event loop calls this shutdown). Returns whether the
    guard is in place."""
    if sys.platform != "win32":
        return False
    from asyncio import proactor_events

    transport = proactor_events._ProactorBasePipeTransport
    current = transport._call_connection_lost
    if not getattr(current, "tolerates_failed_shutdown", False):
        transport._call_connection_lost = tolerate_failed_shutdown(current)
    return True


__all__ = ["install_shutdown_guard", "tolerate_failed_shutdown"]
