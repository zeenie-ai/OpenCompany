"""Who may open the internal socket, and which handlers it may reach.

``/ws/internal`` exists so a Temporal activity worker can call back into
the running backend. It is in ``PUBLIC_PATHS`` because a worker has no
session cookie, so it carries no user principal at all — yet it dispatched
through the same registry as the authenticated socket, which meant every
handler was reachable: ``save_workflow``, ``delete_workflow``, and all six
Memory handlers among them.

It also accepted any peer. ``execute_node`` runs whatever node type and
parameters the message names, so anyone who could reach the app port — or
any web page open in a browser on the same machine, including the agent's
own browser — could run a ``shell`` node. A worker must therefore present
the token from :func:`internal_socket_token` in the handshake. A
loopback-address check would not do: a reverse proxy on the same host
makes public traffic arrive from 127.0.0.1, and a web page's socket to
``localhost`` is itself a loopback connection.

This is a deny-by-default allowlist rather than a per-handler opt-out
because per-handler opt-out has already failed here once: the Context
handler checked the socket path and its Memory sibling did not. An
allowlist inverts the default, so a newly added handler is closed until
someone edits this named constant.

Splitting the handler registry in two was considered and rejected: 40+
plugin ``__init__`` modules self-register into one registry, so a split
forces every plugin author to classify their handler, and whichever
registry is the default is wrong half the time — with the wrong default
being a silent privilege grant.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Callable, Mapping, Optional

#: Handshake header carrying :func:`internal_socket_token`.
INTERNAL_SOCKET_TOKEN_HEADER = "X-OpenCompany-Internal-Token"


def internal_socket_token(secret_key: str) -> str:
    """The token a worker presents to open ``/ws/internal``.

    Derived from ``SECRET_KEY`` so every process that shares the deployment's
    env (the embedded worker, a standalone worker) computes the same value
    with no new setting, and the secret itself never crosses the wire.
    """
    return hmac.new(secret_key.encode(), b"opencompany-ws-internal", hashlib.sha256).hexdigest()


def internal_socket_headers(secret_key: str) -> dict[str, str]:
    """Handshake headers for a worker connecting to ``/ws/internal``."""
    return {INTERNAL_SOCKET_TOKEN_HEADER: internal_socket_token(secret_key)}


def is_internal_caller(headers: Mapping[str, str], secret_key: str) -> bool:
    """Whether a ``/ws/internal`` handshake presented the worker token."""
    presented = headers.get(INTERNAL_SOCKET_TOKEN_HEADER) or ""
    return hmac.compare_digest(presented.encode(), internal_socket_token(secret_key).encode())


#: Everything the activity worker legitimately needs. Both execute
#: handlers already carry their own ``/ws/internal`` identity branch.
INTERNAL_SOCKET_HANDLERS: frozenset[str] = frozenset(
    {
        "execute_node",
        "execute_ai_node",
        "ping",
    }
)


def resolve_internal_handler(
    msg_type: str,
    resolver: Callable[[str], Optional[Any]],
) -> Optional[Any]:
    """Resolve ``msg_type`` for the internal socket, or return ``None``.

    A refused type returns ``None`` so the caller emits its ordinary
    unknown-message-type response. Refusal and "no such handler" are
    deliberately indistinguishable — a distinct error would tell a prober
    that ``save_workflow`` exists here but is forbidden.
    """
    if msg_type not in INTERNAL_SOCKET_HANDLERS:
        return None
    return resolver(msg_type)


def execution_principal(data: Mapping[str, Any], websocket: Any) -> str:
    """Resolve the identity an execution runs as.

    One implementation for every execute handler. They previously had three
    slightly different copies: two honoured a payload-supplied ``user_id``
    on the internal worker socket and one did not, so the same request
    executed as a different principal depending on which handler received
    it.

    A payload-supplied ``user_id`` is trusted ONLY on ``/ws/internal``,
    where the sender is a Temporal activity relaying identity that was
    minted server-side at deploy time. On any authenticated socket the
    handshake identity wins and the payload is ignored.
    """
    from constants import OWNER_PRINCIPAL_ID

    scope = getattr(websocket, "scope", {}) or {}
    if str(scope.get("path") or "") == "/ws/internal":
        candidate = data.get("user_id")
    else:
        candidate = getattr(getattr(websocket, "state", None), "user_id", None)
    return str(candidate or OWNER_PRINCIPAL_ID)


__all__ = [
    "INTERNAL_SOCKET_HANDLERS",
    "INTERNAL_SOCKET_TOKEN_HEADER",
    "execution_principal",
    "internal_socket_headers",
    "internal_socket_token",
    "is_internal_caller",
    "resolve_internal_handler",
]
