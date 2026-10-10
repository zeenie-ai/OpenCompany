"""Custom MCP connectors: the owner adds a remote MCP server from the
Connectors page, by its URL and how it signs in, and its tools become
something employees can use.

- ``_client.py``: talking to a server with the official ``mcp`` SDK, and
  where a connector may connect (``services/netpolicy.py``).
- ``_store.py``: where a connector is kept (two credential rows).
- ``_credentials.py``: each connector's own card in the credentials
  catalogue.
- ``_handlers.py``: add, test, refresh, accept or discard what a refresh
  found, set a tool, and remove.
- ``mcp_connector.py``: the node that gives an agent a connector's tools,
  one per tool (``ToolNode.tool_bindings``), and each connector as an app a
  hire can use (``connector_apps``).
"""

from __future__ import annotations

from services.employees.apps import register_app_source
from services.ws_handler_registry import register_option_loader, register_ws_handlers

from ._credentials import McpConnectorCredential
from ._handlers import WS_HANDLERS
from .mcp_connector import McpConnectorNode, connector_apps, load_connectors

register_ws_handlers(WS_HANDLERS)
register_option_loader("mcpConnectors", load_connectors)
register_app_source("mcp", connector_apps)

__all__ = ["McpConnectorCredential", "McpConnectorNode", "WS_HANDLERS"]
