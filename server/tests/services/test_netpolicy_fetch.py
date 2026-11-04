"""A node's own download of a URL it was given (services/netpolicy.py)."""

from __future__ import annotations

import ipaddress

import httpx
import pytest

from services import netpolicy
from services.netpolicy import BlockedURL, NetPolicy, check_request, fetch_policy, resolved_url_block_reason

APP_PORT = 5678
POLICY = NetPolicy(allow_private_network=True, blocked_local_ports=frozenset({APP_PORT}),
                   local_addresses=frozenset({ipaddress.ip_address("127.0.0.1")}))


def _resolving_to(monkeypatch, *addresses: str) -> None:
    async def resolve(host, port):
        return [ipaddress.ip_address(address) for address in addresses]

    monkeypatch.setattr(netpolicy, "resolve_addresses", resolve)


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/computeMetadata/v1/",
    f"http://127.0.0.1:{APP_PORT}/api/credentials",
    "file:///etc/passwd",
    "ftp://example.com/file",
])
async def test_refused_before_any_lookup(url, monkeypatch):
    async def resolve(host, port):
        raise AssertionError("no lookup expected")

    monkeypatch.setattr(netpolicy, "resolve_addresses", resolve)
    assert await resolved_url_block_reason(url, POLICY)


async def test_a_name_that_resolves_to_an_own_port_is_refused(monkeypatch):
    _resolving_to(monkeypatch, "93.184.216.34", "127.0.0.1")
    assert "OpenCompany" in await resolved_url_block_reason(f"http://sneaky.example:{APP_PORT}/", POLICY)


async def test_a_name_that_resolves_to_metadata_is_refused(monkeypatch):
    _resolving_to(monkeypatch, "169.254.169.254")
    assert "metadata" in await resolved_url_block_reason("https://rebind.example/latest", POLICY)


async def test_a_public_name_is_allowed(monkeypatch):
    _resolving_to(monkeypatch, "93.184.216.34")
    assert await resolved_url_block_reason("https://cdn.example.com/a.jpg", POLICY) is None


async def test_the_owners_network_is_allowed(monkeypatch):
    _resolving_to(monkeypatch, "192.168.1.20")
    assert await resolved_url_block_reason("http://nas.local/photo.jpg", POLICY) is None


async def test_a_name_that_does_not_resolve_is_refused(monkeypatch):
    _resolving_to(monkeypatch)
    assert "could not be found" in await resolved_url_block_reason("https://nowhere.invalid/", POLICY)


def test_the_fetch_policy_refuses_the_configured_ports(monkeypatch):
    monkeypatch.setenv("PYTHON_BACKEND_PORT", "5999")
    policy = fetch_policy()
    assert 5999 in policy.blocked_local_ports
    assert policy.allow_private_network


def test_a_local_model_servers_port_is_not_ours():
    # Ollama's port: agents may reach a local model server.
    assert netpolicy.own_ports_from_env({"AI_PROXY_DEFAULT_PORT": "11434", "PORT": "5678"}) == frozenset({5678})


def test_the_fetch_policy_refuses_a_plugins_live_ports(monkeypatch):
    # The Browser's Chromes and proxies, Android's ADB forwards (1b.33).
    monkeypatch.setattr(netpolicy, "_port_sources", [lambda: {9222}, lambda: [8888]])
    assert {9222, 8888} <= fetch_policy().blocked_local_ports


# check_request, the httpx request hook the downloading nodes use.


@pytest.fixture
def hooked(monkeypatch):
    """A client that follows redirects with check_request on every request,
    served by ``routes`` (host -> response); returns it and the URLs it sent."""
    monkeypatch.setattr(netpolicy, "fetch_policy", lambda: POLICY)
    sent = []

    def make(routes):
        def handler(request):
            sent.append(str(request.url))
            return routes[request.url.host]

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True,
                                 event_hooks={"request": [check_request]})

    return make, sent


@pytest.mark.parametrize("location, reason", [
    ("http://169.254.169.254/latest/meta-data/", "metadata"),
    (f"http://127.0.0.1:{APP_PORT}/api/credentials", "OpenCompany"),
])
async def test_a_redirect_to_a_blocked_address_is_refused_at_that_hop(hooked, monkeypatch, location, reason):
    _resolving_to(monkeypatch, "93.184.216.34")
    make, sent = hooked
    secret = httpx.Response(200, text="secret")
    async with make({"files.example": httpx.Response(302, headers={"location": location}),
                     "169.254.169.254": secret, "127.0.0.1": secret}) as client:
        with pytest.raises(BlockedURL) as refused:
            await client.get("https://files.example/report.pdf")

    assert sent == ["https://files.example/report.pdf"]
    assert refused.value.url == location
    assert reason in refused.value.reason
    assert str(refused.value).startswith(f"Cannot request {location}: ")


async def test_a_request_to_a_blocked_address_is_refused(hooked):
    make, sent = hooked
    async with make({}) as client:
        with pytest.raises(BlockedURL, match="metadata"):
            await client.get("http://169.254.169.254/latest/meta-data/")
    assert sent == []


async def test_an_ordinary_public_url_passes(hooked, monkeypatch):
    _resolving_to(monkeypatch, "93.184.216.34")
    make, _ = hooked
    async with make({"cdn.example.com": httpx.Response(200, content=b"jpg")}) as client:
        response = await client.get("https://cdn.example.com/a.jpg")
    assert response.content == b"jpg"


async def test_a_redirect_into_the_owners_network_is_allowed(hooked, monkeypatch):
    _resolving_to(monkeypatch, "93.184.216.34")
    make, sent = hooked
    async with make({"files.example": httpx.Response(302, headers={"location": "http://192.168.1.20/photo.jpg"}),
                     "192.168.1.20": httpx.Response(200, content=b"jpg")}) as client:
        response = await client.get("https://files.example/photo.jpg")
    assert response.content == b"jpg"
    assert sent == ["https://files.example/photo.jpg", "http://192.168.1.20/photo.jpg"]
