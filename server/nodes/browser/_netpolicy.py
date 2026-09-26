"""Where the agent's browser may go. Pure functions, no I/O.

A page the agent opens is untrusted, and the agent itself follows untrusted
instructions (prompt injection). From the agent's Chrome, three kinds of
address must stay out of reach:

- **OpenCompany itself**: its ports on this machine (the app, the code
  sidecar, the WhatsApp bridge, Temporal) and the managed Chromes' debugging
  ports. These are refused on every address of this host, always.
- **Cloud metadata** (``169.254.169.254`` and friends), which hands out the
  VM's credentials. Link-local is refused always.
- **Loopback and the private network**, refused unless the operator ticked
  "Allow local network" on the Browser node (e.g. to test a local app).

The egress proxy (``_egress.py``) applies :func:`address_block_reason` to the
addresses it resolved itself, so DNS rebinding cannot swap in a private
address after the check. :func:`url_block_reason` is the earlier, friendlier
check before a navigation.
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
    ``www.example.com`` but not ``badexample.com``."""
    if not allowed:
        return True
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in allowed)


def _literal_ip(host: str) -> Optional[IPAddress]:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def address_block_reason(address: IPAddress, port: int, policy: NetPolicy) -> Optional[str]:
    """Why ``address:port`` is refused, or ``None`` when it is allowed."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address in _METADATA_ADDRESSES or address.is_link_local:
        return "cloud metadata and link-local addresses are never reachable"
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        return "this address is not reachable from the browser"
    is_local = address.is_loopback or address in policy.local_addresses
    if is_local and port in policy.blocked_local_ports:
        return "OpenCompany's own services are never reachable from the browser"
    if is_local:
        return None if policy.allow_private_network else "localhost is blocked; allow local network on the Browser node to use it"
    if address.is_private or address in _CGNAT:
        return None if policy.allow_private_network else "the private network is blocked; allow local network on the Browser node to use it"
    return None


def host_block_reason(host: str, policy: NetPolicy) -> Optional[str]:
    """Checks that need only the hostname (before any DNS lookup)."""
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return "no host"
    if host in _METADATA_HOSTS:
        return "cloud metadata addresses are never reachable"
    if _literal_ip(host) is None and not domain_allowed(host, policy.allowed_domains):
        return f"{host} is not in this Browser node's allowed domains"
    if host == "localhost" or host.endswith(".localhost"):
        if not policy.allow_private_network:
            return "localhost is blocked; allow local network on the Browser node to use it"
    return None


def url_block_reason(url: str, policy: NetPolicy) -> Optional[str]:
    """Why the browser may not navigate to ``url``, or ``None``.

    ``about:blank`` is always fine. Literal IP hosts are checked here too;
    hostnames are checked again, by resolved address, in the egress proxy.
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
    literal = _literal_ip(host)
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
    "own_ports_from_env",
    "parse_allowed_domains",
    "url_block_reason",
]
