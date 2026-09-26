"""Where the agent's browser may go (nodes/browser/_netpolicy.py, _egress.py)."""

from __future__ import annotations

import asyncio
import ipaddress

import pytest

from nodes.browser._netpolicy import (
    NetPolicy,
    address_block_reason,
    domain_allowed,
    own_ports_from_env,
    parse_allowed_domains,
    url_block_reason,
)

APP_PORT = 5678
LOCAL = frozenset({ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("10.1.2.3")})


def _policy(**kw):
    base = dict(blocked_local_ports=frozenset({APP_PORT}), local_addresses=LOCAL)
    base.update(kw)
    return NetPolicy(**base)


@pytest.mark.parametrize(
    ("address", "port", "allow_private", "blocked"),
    [
        ("93.184.216.34", 443, False, False),
        ("169.254.169.254", 80, True, True),  # metadata, even with the opt-in
        ("100.100.100.200", 80, True, True),
        ("fd00:ec2::254", 80, True, True),
        ("fe80::1", 80, True, True),
        ("127.0.0.1", 3000, False, True),
        ("127.0.0.1", 3000, True, False),
        ("127.0.0.1", APP_PORT, True, True),  # OpenCompany itself, always
        ("::1", APP_PORT, True, True),
        ("::ffff:127.0.0.1", APP_PORT, True, True),  # IPv4-mapped loopback
        ("10.1.2.3", APP_PORT, True, True),  # this host's LAN address, app port
        ("192.168.1.10", 80, False, True),
        ("192.168.1.10", 80, True, False),
        ("100.64.0.1", 80, False, True),
        ("0.0.0.0", 80, True, True),
        ("224.0.0.1", 80, True, True),
    ],
)
def test_address_rules(address, port, allow_private, blocked):
    reason = address_block_reason(ipaddress.ip_address(address), port, _policy(allow_private_network=allow_private))
    assert (reason is not None) is blocked, reason


@pytest.mark.parametrize(
    ("url", "blocked"),
    [
        ("https://example.com/path", False),
        ("about:blank", False),
        ("file:///etc/passwd", True),
        ("chrome://settings", True),
        ("javascript:alert(1)", True),
        ("view-source:https://example.com", True),
        ("data:text/html,<script>1</script>", True),
        ("http://localhost:3000", True),
        ("http://metadata.google.internal/computeMetadata/v1/", True),
        ("http://169.254.169.254/latest/meta-data/", True),
        ("http://127.0.0.1:5678/ws/internal", True),
        ("http://[::1]:5678/", True),
    ],
)
def test_navigation_rules(url, blocked):
    assert (url_block_reason(url, _policy()) is not None) is blocked


def test_allowed_domains_match_on_label_boundaries():
    allowed = parse_allowed_domains(" Example.com, *.docs.io\n")
    assert allowed == ("example.com", "docs.io")
    assert domain_allowed("www.example.com", allowed)
    assert domain_allowed("example.com", allowed)
    assert not domain_allowed("badexample.com", allowed)
    assert url_block_reason("https://evil.test/", _policy(allowed_domains=allowed)) is not None
    assert url_block_reason("https://a.docs.io/", _policy(allowed_domains=allowed)) is None


def test_own_ports_come_from_every_port_variable():
    env = {"PORT": "5678", "PYTHON_BACKEND_PORT": "5679", "TEMPORAL_UI_PORT": "8233", "NOT_A_PORT_VALUE": "x", "WHATSAPP_RPC_PORT": "junk"}
    assert own_ports_from_env(env) == frozenset({5678, 5679, 8233})


async def _serve_http(body: bytes):
    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _through_proxy(proxy_port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(request)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), timeout=10)
    writer.close()
    return data


@pytest.mark.asyncio
async def test_egress_proxy_enforces_the_policy_by_resolved_address():
    from nodes.browser._egress import EgressProxy

    server, target_port = await _serve_http(b"hello")
    state = {"policy": NetPolicy(allow_private_network=False, blocked_local_ports=frozenset({APP_PORT}))}
    proxy = EgressProxy(lambda: state["policy"])
    port = proxy.start()
    try:
        # Loopback refused without the opt-in, by the resolved address of a name too.
        refused = await _through_proxy(port, f"GET http://localhost:{target_port}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert refused.startswith(b"HTTP/1.1 403")
        assert b"Blocked by OpenCompany" in refused

        state["policy"] = NetPolicy(allow_private_network=True, blocked_local_ports=frozenset({APP_PORT}))
        allowed = await _through_proxy(port, f"GET http://127.0.0.1:{target_port}/x HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert allowed.startswith(b"HTTP/1.1 200") and allowed.endswith(b"hello")

        tunnel = await _through_proxy(port, f"CONNECT 127.0.0.1:{APP_PORT} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert tunnel.startswith(b"HTTP/1.1 403")

        metadata = await _through_proxy(port, b"CONNECT 169.254.169.254:80 HTTP/1.1\r\nHost: x\r\n\r\n")
        assert metadata.startswith(b"HTTP/1.1 403")
    finally:
        proxy.stop()
        server.close()
        await server.wait_closed()
