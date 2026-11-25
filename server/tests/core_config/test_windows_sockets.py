"""core.windows_sockets: a connection Windows has already dropped still
closes, and its server stops counting it (errors.md #37).

The transports here are asyncio's own Windows transport class, built over a
socket stand-in whose shutdown fails the way Windows refuses one, so every
test runs on every OS."""

from __future__ import annotations

import asyncio
import errno
from asyncio import proactor_events

import pytest

from core import windows_sockets


class _Protocol(asyncio.Protocol):
    def __init__(self) -> None:
        self.lost: list[BaseException | None] = []

    def connection_lost(self, exc: BaseException | None) -> None:
        self.lost.append(exc)


class _FailingProtocol(_Protocol):
    def connection_lost(self, exc: BaseException | None) -> None:
        super().connection_lost(exc)
        raise RuntimeError("the protocol failed")


class _Socket:
    """An accepted connection's socket. ``dropped`` is a connection Windows
    already gave up on: its shutdown raises WinError 10022 (EINVAL)."""

    def __init__(self, *, dropped: bool) -> None:
        self.dropped = dropped
        self.shutdowns = 0
        self.closed = False

    def fileno(self) -> int:
        return -1 if self.closed else 7

    def shutdown(self, how: int) -> None:
        self.shutdowns += 1
        if self.dropped:
            raise OSError(errno.EINVAL, "An invalid argument was supplied")

    def close(self) -> None:
        self.closed = True


class _Pipe:
    """A subprocess pipe: no shutdown at all."""

    def __init__(self) -> None:
        self.closed = False

    def fileno(self) -> int:
        return -1 if self.closed else 8

    def close(self) -> None:
        self.closed = True


def _stdlib_call_connection_lost() -> windows_sockets.CallConnectionLost:
    """The stdlib's own method, even where the backend already guarded it."""
    method = proactor_events._ProactorBasePipeTransport._call_connection_lost
    return getattr(method, "__wrapped__", method)


async def _serve() -> asyncio.base_events.Server:
    return await asyncio.get_running_loop().create_server(asyncio.Protocol, "127.0.0.1", 0)


def _connection(server, sock, protocol=None):
    protocol = protocol or _Protocol()
    transport = proactor_events._ProactorBasePipeTransport(asyncio.get_running_loop(), sock, protocol, server=server)
    return transport, protocol


async def _server_lets_go(server) -> bool:
    """Close the server; whether it stops waiting for its connections."""
    server.close()
    try:
        await asyncio.wait_for(server.wait_closed(), 0.3)
    except asyncio.TimeoutError:
        return False
    return True


async def test_the_stdlib_still_skips_its_cleanup_when_shutdown_fails():
    """Why the guard exists. Once this fails, Python closes such a
    connection by itself and core/windows_sockets.py can go."""
    server = await _serve()
    sock = _Socket(dropped=True)
    transport, protocol = _connection(server, sock)

    with pytest.raises(OSError):
        _stdlib_call_connection_lost()(transport, None)

    assert protocol.lost == [None]
    assert not sock.closed
    assert not await _server_lets_go(server)
    transport._sock = None  # no unclosed-transport warning at collection


async def test_a_dropped_connection_closes_and_the_server_lets_go():
    server = await _serve()
    sock = _Socket(dropped=True)
    transport, protocol = _connection(server, sock)

    windows_sockets.tolerate_failed_shutdown(_stdlib_call_connection_lost())(transport, None)

    assert protocol.lost == [None]
    assert sock.shutdowns == 1
    assert sock.closed
    assert transport._sock is None
    assert transport._called_connection_lost
    assert await _server_lets_go(server)


async def test_a_live_connection_still_shuts_down_first():
    server = await _serve()
    sock = _Socket(dropped=False)
    transport, protocol = _connection(server, sock)

    windows_sockets.tolerate_failed_shutdown(_stdlib_call_connection_lost())(transport, None)

    assert sock.shutdowns == 1
    assert sock.closed
    assert await _server_lets_go(server)


async def test_a_failing_protocol_still_raises_and_the_socket_still_closes():
    server = await _serve()
    sock = _Socket(dropped=True)
    transport, protocol = _connection(server, sock, _FailingProtocol())

    with pytest.raises(RuntimeError, match="the protocol failed"):
        windows_sockets.tolerate_failed_shutdown(_stdlib_call_connection_lost())(transport, None)

    assert protocol.lost == [None]
    assert sock.closed
    assert await _server_lets_go(server)


async def test_a_pipe_closes_without_a_shutdown():
    pipe = _Pipe()
    transport, protocol = _connection(None, pipe)

    windows_sockets.tolerate_failed_shutdown(_stdlib_call_connection_lost())(transport, None)

    assert protocol.lost == [None]
    assert pipe.closed
    assert transport._sock is None


def test_installed_once_and_only_on_windows(monkeypatch):
    base = proactor_events._ProactorBasePipeTransport
    stdlib = _stdlib_call_connection_lost()
    monkeypatch.setattr(base, "_call_connection_lost", stdlib)

    monkeypatch.setattr(windows_sockets.sys, "platform", "linux")
    assert windows_sockets.install_shutdown_guard() is False
    assert base._call_connection_lost is stdlib

    monkeypatch.setattr(windows_sockets.sys, "platform", "win32")
    assert windows_sockets.install_shutdown_guard() is True
    guarded = base._call_connection_lost
    assert guarded.__wrapped__ is stdlib
    assert windows_sockets.install_shutdown_guard() is True
    assert base._call_connection_lost is guarded
    # Every Windows connection is a subclass that keeps the base's method.
    assert proactor_events._ProactorSocketTransport._call_connection_lost is guarded
