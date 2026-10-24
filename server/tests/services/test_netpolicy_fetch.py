"""A node's own download of a URL it was given (services/netpolicy.py)."""

from __future__ import annotations

import ipaddress

import pytest

from services import netpolicy
from services.netpolicy import NetPolicy, fetch_policy, resolved_url_block_reason

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
