"""Custom MCP connectors: the owner adds a remote MCP server from the
Connectors page, by its URL and how it signs in, and its tools become
something employees can use.

- ``_client.py``: talking to a server with the official ``mcp`` SDK, and
  where a connector may connect (``services/netpolicy.py``).
- ``_store.py``: where a connector is kept (two credential rows).
- ``_credentials.py``: each connector's own card in the credentials
  catalogue.
- ``_handlers.py``: add, test, refresh, accept or discard what a refresh
  found, and remove.
"""

from __future__ import annotations

from services.ws_handler_registry import register_ws_handlers

from ._credentials import McpConnectorCredential
from ._handlers import WS_HANDLERS

register_ws_handlers(WS_HANDLERS)

__all__ = ["McpConnectorCredential", "WS_HANDLERS"]
