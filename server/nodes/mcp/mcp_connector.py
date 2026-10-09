"""The custom connector node (``mcpConnector``): on an agent's Tools, it gives
the agent each tool of one custom MCP connector that the owner has on, named
``<connector>__<tool>`` (``tool_bindings``). A call is checked against the
tool's input schema, then sent to the server (``_client.call_tool``).

What a call runs with (``mcp_tool``, ``mcp_ask`` and the labels its card
shows) comes from its binding and is locked: the model never sets it. While
the employee asks first, a call of a tool set to Ask first waits for the
owner (``approval``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, NodeUserError, Operation, TaskQueue, ToolNode
from services.plugin.approval import ApprovalSpec
from services.plugin.tool import ToolBinding, inline_schema_refs

from ._client import ConnectorError, call_name, call_tool, check_arguments
from ._credentials import McpConnectorCredential
from ._store import get_connector, list_connectors, read_access, slug_of


class McpConnectorParams(BaseModel):
    mcp_connector: str = Field(
        default="",
        description="The custom connector whose tools the agent may use",
        json_schema_extra={"loadOptionsMethod": "mcpConnectors", "placeholder": "Choose a connector"},
    )
    # Set by each tool's binding, never shown or set by hand.
    mcp_tool: str = Field(default="", json_schema_extra={"hidden": True})
    mcp_ask: bool = Field(default=True, json_schema_extra={"hidden": True})
    mcp_label: str = Field(default="", json_schema_extra={"hidden": True})
    mcp_title: str = Field(default="", json_schema_extra={"hidden": True})

    model_config = ConfigDict(extra="ignore")


class McpToolInput(BaseModel):
    """A remote tool's arguments, checked against its own input schema."""

    model_config = ConfigDict(extra="allow")


class McpToolOutput(BaseModel):
    text: str = ""
    structured: Optional[Dict[str, Any]] = None


async def load_connectors(params: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """The saved custom connectors, for the node's dropdown."""
    return [{"name": connector.name, "value": connector.ref} for connector in await list_connectors()]


class McpConnectorNode(ToolNode):
    type = "mcpConnector"
    display_name = "Custom Connector"
    subtitle = "MCP Tools"
    group = ("tool", "ai")
    description = "Gives an agent the tools of a custom connector added on the Connectors page"
    component_kind = "tool"
    task_queue = TaskQueue.REST_API
    credentials = (McpConnectorCredential,)
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    ui_hints = {"hideRunButton": True}

    Params = McpConnectorParams
    ToolInput = McpToolInput
    Output = McpToolOutput
    server_controlled_fields = frozenset({"mcp_connector", "mcp_tool", "mcp_ask", "mcp_label", "mcp_title"})

    approval = ApprovalSpec(
        channel="Custom connector",
        action="Use a connector tool",
        # A call asks first unless its tool is set not to.
        when=lambda data: data.get("mcp_ask") is not False,
        details=(("Connector", "mcp_label"), ("Tool", "mcp_title")),
        outcome_labels=("Done", "Not done"),
    )

    @classmethod
    async def tool_bindings(cls, parameters: Mapping[str, Any]) -> List[ToolBinding]:
        """One tool per tool of the connector that can be used and is on."""
        ref = str(parameters.get("mcp_connector") or "")
        if slug_of(ref) is None:
            return []
        try:
            connector = await get_connector(ref)
        except ConnectorError:
            return []
        bindings = []
        for tool in connector.tools:
            setting = connector.settings.get(tool["name"]) or {}
            if not tool.get("usable") or not setting.get("enabled"):
                continue
            title = tool.get("title") or tool["name"]
            bindings.append(
                ToolBinding(
                    key=tool["name"],
                    name=call_name(connector.slug, tool["name"]),
                    description=tool.get("description") or title,
                    schema=inline_schema_refs(tool["input_schema"]),
                    parameters={
                        "mcp_tool": tool["name"],
                        "mcp_ask": bool(setting.get("ask", True)),
                        "mcp_label": connector.name,
                        "mcp_title": title,
                    },
                )
            )
        return bindings

    @Operation("call")
    async def call(self, ctx: NodeContext, params: McpToolInput | McpConnectorParams) -> McpToolOutput:
        config = ctx.raw.get("_tool_config")
        if not isinstance(params, McpToolInput) or not isinstance(config, McpConnectorParams) or not config.mcp_tool:
            raise NodeUserError("Connect this node to an agent's Tools: its tools are the agent's to call.")
        try:
            connector = await get_connector(config.mcp_connector)
            tool = next((tool for tool in connector.tools if tool["name"] == config.mcp_tool), None)
            if tool is None or not tool.get("usable"):
                raise ConnectorError(f"{connector.name} no longer offers {config.mcp_tool}.")
            if not (connector.settings.get(tool["name"]) or {}).get("enabled"):
                raise ConnectorError(f"The owner turned {config.mcp_title or tool['name']} off.")
            arguments = params.model_dump()
            check_arguments(tool["input_schema"], arguments)
            url, sign_in = await read_access(connector)
            result = await call_tool(url, sign_in, connector.meta.get("transport") or "streamable_http", tool["name"], arguments)
        except ConnectorError as exc:
            raise NodeUserError(str(exc)) from None
        if result.is_error:
            raise NodeUserError(result.text or f"{config.mcp_title or config.mcp_tool} reported an error.")
        return McpToolOutput(text=result.text, structured=result.structured)


__all__ = ["McpConnectorNode", "McpConnectorParams", "McpToolInput", "McpToolOutput", "load_connectors"]
