"""TCP probes — port-up detection without shell pipes."""

from __future__ import annotations

import asyncio
import socket
import time


async def probe_tcp_port(port: int, host: str = "127.0.0.1", *, timeout: float = 0.5) -> bool:
    """Return True iff a TCP connection to ``host:port`` succeeds within ``timeout``."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, OSError):
            pass
        return True
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return False


async def wait_for_tcp_port(
    port: int,
    *,
    host: str = "127.0.0.1",
    interval: float = 0.25,
    timeout: float = 60.0,
) -> bool:
    """Poll until ``port`` accepts a TCP connection or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await probe_tcp_port(port, host, timeout=interval):
            return True
        await asyncio.sleep(interval)
    return False


def probe_tcp_port_sync(port: int, host: str = "127.0.0.1", *, timeout: float = 0.5) -> bool:
    """Synchronous variant for places that aren't already in an event loop."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()
