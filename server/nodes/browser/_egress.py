"""The egress proxy every managed Chrome sends its traffic through.

Chrome is launched with ``--proxy-server=http://127.0.0.1:<this port>`` and
``--proxy-bypass-list=<-loopback>`` (which removes Chrome's built-in
"localhost goes direct" rule), so every request, WebSocket and all, arrives
here. For each one the proxy resolves the hostname itself, drops every
address the network policy refuses, and connects only to an address it has
checked, so a DNS answer cannot switch to a private address between the
check and the connection. A refused request gets a plain ``403`` page that
names the reason.

It runs on its own thread and event loop: page traffic (images, video) is
bulk byte copying that has no business competing with the backend's loop.
The policy comes from a callable, read on each new connection, because the
Browser node holding a profile decides whether its local network is allowed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import threading
from typing import Callable, Optional, Tuple
from urllib.parse import urlsplit

from core.logging import get_logger

from ._netpolicy import NetPolicy, address_block_reason, host_block_reason

logger = get_logger(__name__)

_HEADER_LIMIT = 64 * 1024
_CONNECT_TIMEOUT = 15.0
_RELAY_CHUNK = 64 * 1024

PolicyProvider = Callable[[], NetPolicy]


class _Blocked(Exception):
    pass


async def _read_head(reader: asyncio.StreamReader) -> bytes:
    try:
        return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
    except asyncio.LimitOverrunError:
        raise _Blocked("request headers too large") from None


async def _relay(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await src.read(_RELAY_CHUNK)
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        try:
            if dst.can_write_eof():
                dst.write_eof()
        except (OSError, RuntimeError):
            pass


def _split_host_port(authority: str, default_port: int) -> Tuple[str, int]:
    parts = urlsplit(f"//{authority}")
    host = parts.hostname or ""
    try:
        port = parts.port or default_port
    except ValueError:
        raise _Blocked("invalid port") from None
    return host, port


class EgressProxy:
    """One filtering HTTP/CONNECT proxy on ``127.0.0.1:<ephemeral>``."""

    def __init__(self, policy: PolicyProvider, *, name: str = "browser-egress") -> None:
        self._policy = policy
        self._name = name
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[asyncio.base_events.Server] = None
        self.port: Optional[int] = None

    # -- lifecycle (called from the main loop) ------------------------------

    def start(self) -> int:
        if self.port is not None:
            return self.port
        ready = threading.Event()
        error: list[BaseException] = []

        def _run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            try:
                self._server = loop.run_until_complete(asyncio.start_server(self._handle, host="127.0.0.1", port=0, limit=_HEADER_LIMIT))
                self.port = self._server.sockets[0].getsockname()[1]
            except BaseException as exc:  # noqa: BLE001 - surfaced to start()
                error.append(exc)
                ready.set()
                return
            ready.set()
            try:
                loop.run_forever()
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

        self._thread = threading.Thread(target=_run, name=self._name, daemon=True)
        self._thread.start()
        ready.wait(timeout=10)
        if error:
            raise error[0]
        if self.port is None:
            raise RuntimeError("the browser egress proxy did not start")
        logger.debug("[browser] egress proxy listening on 127.0.0.1:%s", self.port)
        return self.port

    def stop(self) -> None:
        loop, server = self._loop, self._server
        if loop is None:
            return

        def _shutdown() -> None:
            if server is not None:
                server.close()
            for task in asyncio.all_tasks(loop):
                task.cancel()
            loop.call_soon(loop.stop)

        try:
            loop.call_soon_threadsafe(_shutdown)
        except RuntimeError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = self._server = self._thread = None
        self.port = None

    # -- connection handling (proxy thread) ---------------------------------

    async def _resolve_allowed(self, host: str, port: int, policy: NetPolicy) -> str:
        reason = host_block_reason(host, policy)
        if reason:
            raise _Blocked(reason)
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            literal = None
        if literal is not None:
            candidates = [literal]
        else:
            loop = asyncio.get_running_loop()
            try:
                infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            except socket.gaierror:
                raise _Blocked(f"{host} could not be resolved") from None
            candidates = list(dict.fromkeys(ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos))
        last_reason = "no address"
        for address in candidates:
            reason = address_block_reason(address, port, policy)
            if reason is None:
                return str(address)
            last_reason = reason
        raise _Blocked(last_reason)

    async def _open(self, host: str, port: int, policy: NetPolicy) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        address = await self._resolve_allowed(host, port, policy)
        try:
            return await asyncio.wait_for(asyncio.open_connection(address, port), timeout=_CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as exc:
            raise ConnectionError(f"could not connect to {host}:{port}: {exc}") from exc

    @staticmethod
    async def _refuse(writer: asyncio.StreamWriter, status: str, reason: str) -> None:
        body = f"Blocked by OpenCompany: {reason}\n".encode("utf-8", errors="replace")
        head = (
            f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        try:
            writer.write(head + body)
            await writer.drain()
        except (ConnectionError, OSError):
            pass

    async def _handle(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        upstream_writer: Optional[asyncio.StreamWriter] = None
        try:
            head = await _read_head(client_reader)
            request_line, _, rest = head.partition(b"\r\n")
            parts = request_line.decode("latin-1").split(" ")
            if len(parts) != 3:
                await self._refuse(client_writer, "400 Bad Request", "malformed request")
                return
            method, target, version = parts
            policy = self._policy()

            if method.upper() == "CONNECT":
                host, port = _split_host_port(target, 443)
                upstream_reader, upstream_writer = await self._open(host, port, policy)
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
            else:
                url = urlsplit(target)
                if url.scheme.lower() != "http" or not url.hostname:
                    await self._refuse(client_writer, "400 Bad Request", "only absolute http:// requests are proxied")
                    return
                host, port = _split_host_port(url.netloc, 80)
                upstream_reader, upstream_writer = await self._open(host, port, policy)
                path = url.path or "/"
                if url.query:
                    path += "?" + url.query
                headers = []
                upgrade = False
                for line in rest.split(b"\r\n"):
                    if not line:
                        continue
                    name = line.split(b":", 1)[0].strip().lower()
                    if name in (b"proxy-connection", b"proxy-authorization"):
                        continue
                    if name == b"upgrade":
                        upgrade = True
                    if name in (b"connection", b"keep-alive"):
                        continue
                    headers.append(line)
                # One request per upstream connection: a kept-alive client
                # connection could otherwise send the next request, for any
                # host, down this already-checked socket.
                headers.append(b"Connection: " + (b"Upgrade" if upgrade else b"close"))
                upstream_writer.write(f"{method} {path} {version}\r\n".encode("latin-1") + b"\r\n".join(headers) + b"\r\n\r\n")
                await upstream_writer.drain()

            await asyncio.gather(
                _relay(client_reader, upstream_writer),
                _relay(upstream_reader, client_writer),
            )
        except _Blocked as exc:
            await self._refuse(client_writer, "403 Forbidden", str(exc))
        except ConnectionError as exc:
            await self._refuse(client_writer, "502 Bad Gateway", str(exc))
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad request must not stop the proxy
            logger.debug("[browser] egress proxy request failed", exc_info=True)
        finally:
            for writer in (upstream_writer, client_writer):
                if writer is not None:
                    try:
                        writer.close()
                    except (OSError, RuntimeError):
                        pass


__all__ = ["EgressProxy", "PolicyProvider"]
