"""Network egress guards shared by anything that fetches a caller-supplied URL.

Lives here rather than inside one plugin because more than one consumer needs
the same check, and two copies of an SSRF guard is how one of them silently
falls behind the other.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Tuple
from urllib.parse import urlparse

#: Shared address space (RFC 6598): carrier NAT, tailnets, and Alibaba Cloud's
#: metadata service at 100.100.100.200. Not publicly routable, and none of the
#: ipaddress flags below covers it.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


def is_public_url(url: str) -> Tuple[bool, str]:
    """SSRF guard: allow only http/https to a publicly-routable host.

    Resolves every A/AAAA record and rejects loopback / private / link-local /
    reserved / multicast / unspecified addresses and the shared address space.

    Best-effort, not TOCTOU-proof: httpx re-resolves DNS when it connects, so a
    hostile record could change between this check and the request. Closing
    that needs connection-time pinning, which is a bigger change than the
    threat has so far justified. Returns ``(ok, reason)`` rather than raising
    so callers can phrase the refusal in their own error vocabulary.
    """
    try:
        parsed = urlparse(url)
    except ValueError:  # "http://[::1/x"; the message would repeat the URL
        return False, "not a valid URL"
    if parsed.scheme not in ("http", "https"):
        return False, f"only http/https allowed (got {parsed.scheme or 'no scheme'!r})"
    host = parsed.hostname
    if not host:
        return False, "missing host"
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as exc:
        return False, f"DNS resolution failed: {exc}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
            or (getattr(ip, "ipv4_mapped", None) or ip) in _SHARED_ADDRESS_SPACE
        ):
            return False, f"blocked non-public address {ip}"
    return True, ""


__all__ = ["is_public_url"]
