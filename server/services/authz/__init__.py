"""Authorization: who may act, and on what.

Public surface is re-exported here so callers never import submodules
directly and the boundary stays greppable.
"""

from services.authz.ws_session import (
    admit_internal_ws,
    authenticate_ws,
    is_allowed_ws_origin,
)
from services.authz.ws_surface import (
    INTERNAL_SOCKET_HANDLERS,
    INTERNAL_SOCKET_TOKEN_HEADER,
    execution_principal,
    internal_socket_headers,
    internal_socket_token,
    is_internal_caller,
    resolve_internal_handler,
)

__all__ = [
    "INTERNAL_SOCKET_HANDLERS",
    "INTERNAL_SOCKET_TOKEN_HEADER",
    "admit_internal_ws",
    "authenticate_ws",
    "execution_principal",
    "internal_socket_headers",
    "internal_socket_token",
    "is_allowed_ws_origin",
    "is_internal_caller",
    "resolve_internal_handler",
]
