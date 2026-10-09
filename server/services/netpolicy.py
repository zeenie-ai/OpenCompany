"""Where an outbound connection made for an agent may go.

Two callers: the agent's browser (``nodes/browser``: each navigation, and the
egress proxy on the addresses it resolved itself), and the custom MCP
connectors (``nodes/mcp_connector``: the server the owner added, on every
address its host resolves to). What they reach is untrusted, and an agent
follows untrusted instructions (prompt injection), so these stay out of
reach:

- **OpenCompany itself**: its ports on this machine (the app, the code
  sidecar, the WhatsApp bridge, Temporal) and the managed Chromes' debugging
  ports. Refused on every address of this host, always.
- **Cloud metadata** (``169.254.169.254`` and friends), which hands out the
  VM's credentials. Link-local is refused always.
- **The private network**, unless the policy allows it (the Browser node's
  "Allow local network"; a connector always may, for a server on the owner's
  own network). Loopback is allowed so local apps work, except on the
  protected ports above.

Pure functions, except :func:`local_addresses`, which reads this machine's
interface addresses (no network).
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Optional, Tuple, Union
from urllib.parse import urlsplit

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

#: Hostnames that name a cloud metadata service.
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata.goog", "metadata"})
#: Metadata addresses outside link-local (Alibaba, AWS IPv6).
_METADATA_ADDRESSES = frozenset({ipaddress.ip_address("100.100.100.200"), ipaddress.ip_address("fd00:ec2::254")})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

#: Schemes a navigation may use. Everything else (file:, chrome:, javascript:,
#: view-source:, data: pages that could embed script) is refused.
NAVIGABLE_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class NetPolicy:
    allow_private_network: bool = False
    #: Ports refused on every address of this host (OpenCompany's own).
    blocked_local_ports: FrozenSet[int] = field(default_factory=frozenset)
    #: Addresses of this host (loopback plus every interface).
    local_addresses: FrozenSet[IPAddress] = field(default_factory=frozenset)
    #: When non-empty, only these domains (and their subdomains).
    allowed_domains: Tuple[str, ...] = ()
    #: How the caller's owner allows the private network, said with the refusal.
    private_network_hint: str = ""


def own_ports_from_env(environ: Optional[dict] = None) -> FrozenSet[int]:
    """Every port OpenCompany is configured to listen on.

    Read from the ``*_PORT`` variables (``.env.template`` is their single
    source), so a new service port is covered without a code change.
    """
    env = environ if environ is not None else os.environ
    ports = set()
    for key, value in env.items():
        if key == "PORT" or key.endswith("_PORT"):
            try:
                port = int(str(value).strip())
            except (TypeError, ValueError):
                continue
            if 0 < port < 65536:
                ports.add(port)
    return frozenset(ports)


def local_addresses() -> FrozenSet[IPAddress]:
    """This machine's addresses: loopback plus every interface's."""
    found = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
    try:
        import psutil

        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                try:
                    found.add(ipaddress.ip_address(addr.address.split("%", 1)[0]))
                except ValueError:
                    continue
    except Exception:  # noqa: BLE001 - loopback alone still protects the local case
        pass
    return frozenset(found)


def parse_allowed_domains(raw: Union[str, Iterable[str], None]) -> Tuple[str, ...]:
    if raw is None:
        return ()
    items = raw.replace("\n", ",").split(",") if isinstance(raw, str) else list(raw)
    out = []
    for item in items:
        domain = str(item).strip().lower().lstrip("*").lstrip(".")
        if domain:
            out.append(domain)
    return tuple(dict.fromkeys(out))


def domain_allowed(host: str, allowed: Tuple[str, ...]) -> bool:
    """Suffix match on label boundaries: ``example.com`` allows
    ``www.example.com`` but not ``badexample.com``. IP entries match only
    the exact address, never a numeric suffix or a domain beneath it."""
    if not allowed:
        return True
    host = host.lower().rstrip(".")
    literal = literal_ip(host)
    if literal is not None:
        return any(literal_ip(d) == literal for d in allowed)
    return any(literal_ip(d) is None and (host == d.rstrip(".") or host.endswith("." + d.rstrip("."))) for d in allowed)


def literal_ip(host: str) -> Optional[IPAddress]:
    """``host`` as an IP address, or None when it is a name."""
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _unmapped(address: IPAddress) -> IPAddress:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_local_network(address: IPAddress, policy: NetPolicy) -> bool:
    """Loopback, this host, or the private network: not the internet."""
    address = _unmapped(address)
    return address.is_loopback or address in policy.local_addresses or address.is_private or address in _CGNAT


def address_block_reason(address: IPAddress, port: int, policy: NetPolicy) -> Optional[str]:
    """Why ``address:port`` is refused, or ``None`` when it is allowed."""
    address = _unmapped(address)
    if address in _METADATA_ADDRESSES or address.is_link_local:
        return "cloud metadata and link-local addresses are never reachable"
    if address.is_unspecified or address.is_multicast or (address.is_reserved and not address.is_loopback):
        return "this address is not reachable"
    is_local = address.is_loopback or address in policy.local_addresses
    if is_local and port in policy.blocked_local_ports:
        return "OpenCompany's own services are never reachable"
    if address.is_loopback:
        return None
    if is_local or address.is_private or address in _CGNAT:
        if policy.allow_private_network:
            return None
        return "the private network is blocked" + (f"; {policy.private_network_hint}" if policy.private_network_hint else "")
    return None


def host_block_reason(host: str, policy: NetPolicy) -> Optional[str]:
    """Checks that need only the hostname (before any DNS lookup)."""
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return "no host"
    if host in _METADATA_HOSTS:
        return "cloud metadata addresses are never reachable"
    if not domain_allowed(host, policy.allowed_domains):
        return f"{host} is not in the allowed domains"
    return None


def url_block_reason(url: str, policy: NetPolicy) -> Optional[str]:
    """Why ``url`` may not be opened, or ``None``.

    ``about:blank`` is always fine. Literal IP hosts are checked here too;
    a hostname is checked again by its resolved addresses (the browser's
    egress proxy, the connector's connect).
    """
    if url.strip() == "about:blank":
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return "not a valid URL"
    scheme = (parts.scheme or "").lower()
    if scheme not in NAVIGABLE_SCHEMES:
        return f"only http and https pages can be opened (got {scheme or 'no scheme'})"
    host = parts.hostname or ""
    reason = host_block_reason(host, policy)
    if reason:
        return reason
    literal = literal_ip(host)
    if literal is not None:
        try:
            port = parts.port or (443 if scheme == "https" else 80)
        except ValueError:
            return "not a valid port"
        return address_block_reason(literal, port, policy)
    return None


__all__ = [
    "NAVIGABLE_SCHEMES",
    "NetPolicy",
    "address_block_reason",
    "domain_allowed",
    "host_block_reason",
    "is_local_network",
    "literal_ip",
    "local_addresses",
    "own_ports_from_env",
    "parse_allowed_domains",
    "url_block_reason",
]
