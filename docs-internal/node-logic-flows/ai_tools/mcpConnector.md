# Custom Connector (`mcpConnector`)

| Field | Value |
|---|---|
| **Implementation** | [`McpConnectorNode`](../../../server/nodes/mcp/mcp_connector.py) |
| **Operation** | `call` |
| **Tests** | [`test_mcp_connector.py`](../../../server/tests/nodes/test_mcp_connector.py) |

## Purpose and configuration

Connect one owner-configured MCP server to an agent's Tools input. Persisted
`mcp_connector` is selected by the `mcpConnectors` loader. The node expands to
one binding for each remote tool that is usable and enabled by the owner;
callable names follow `<connector>__<tool>` through `call_name`.

`Params` holds the connector and binding settings. The model supplies only the
remote tool's arguments through `McpToolInput`; `mcp_connector`, `mcp_tool`,
`mcp_ask`, `mcp_label`, and `mcp_title` are server-controlled fields.
Bindings use the discovered input schema with local references inlined.
The node has the tool connection supplied by the shared ToolNode contract,
and its Run button is hidden.

## Execution paths

- **Direct canvas Run / ordinary graph call:** rejected with an instruction
  to connect the node to an agent's Tools. A hidden Run button is not a
  substitute for this runtime check.
- **Agent tool:** `tool_bindings` loads the saved connector, emits enabled
  bindings, and attaches locked settings. `call` rechecks that the tool still
  exists, is usable and is enabled, validates arguments against its remote
  schema, resolves authorized access, and sends the call.
- **Temporal:** the tool dispatch uses the normal recorded tool/activity and
  approval contracts; remote tool idempotency is not implied by MCP.
- **Independent Workspace task:** no specialized task controller is declared.

## Outputs, effects and failures

The flat ToolNode result has `text` and optional `structured` content. Discovery,
credential/access lookup and invocation can use network/OAuth connections.
Connector errors and remote `is_error` results become `NodeUserError` failures;
an invalid connector produces no bindings. Tools disabled after discovery are
refused at invocation, rather than trusted from an old model-visible list.

The approval spec asks while the employee's Ask first policy applies and the
binding's `mcp_ask` is true. The model cannot disable that flag. Controlled
execution admits/retries/cancels the call through the shared tool runtime;
uncertain remote side effects require the same reconciliation discipline as
other external effects. Reset does not promise to undo a remote call.

## Dynamic surface

The supported repository node is this adapter. Each server's discovered tools
have their own schemas and effects; arbitrary user-installed servers are not
independently verified built-in nodes. Saved connectors are also exposed as
apps through `connector_apps`.

See [MCP connectors](../../mcp_connectors.md) and
[tool construction](../../tool_building_pipeline.md).
