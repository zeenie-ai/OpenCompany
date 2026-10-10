# MCP Connectors

The owner connects OpenCompany to an outside MCP server from Connectors →
**Add**: its URL, a name, and how it signs in. Each saved connector is its own
card on the Connectors page, and its tools are what that server offers.

Here OpenCompany is the MCP **client**. The opposite direction, where CLI
agents call into OpenCompany's own MCP server (`/mcp/ide`), is a separate
thing: see [CLI Agent Framework](./cli_agent_framework.md).

## Where it lives

| Part | Path |
|---|---|
| Talking to a server (official `mcp` SDK), where a connector may connect | `server/nodes/mcp/_client.py` |
| What is kept (two credential rows) | `server/nodes/mcp/_store.py` |
| One catalogue card per connector | `server/nodes/mcp/_credentials.py` |
| The Connectors page's commands | `server/nodes/mcp/_handlers.py` |
| The node that gives an agent the tools, and each connector as an app a hire can use | `server/nodes/mcp/mcp_connector.py` |
| The outbound address rules, shared with the Browser node | `server/services/netpolicy.py` |
| The cards' shared fields, and the `custom` categories | `server/config/credential_providers.json` (`_mcp_connector`) |
| The Add form | `client/src/components/credentials/AddConnectorForm.tsx` |
| A connector's page | `client/src/components/credentials/panels/McpConnectorPanel.tsx` |
| The card's wire type | `ServerMcpConnector` in `client/src/hooks/useCatalogueQuery.ts` |

## Adding a connector

`mcp_connector_add {name?, url, sign_in: {kind, token?, header?, value?}}`
reads the server first and saves the connector only when it answered, so a
wrong URL or a refused sign-in comes back in words for the owner and nothing
is kept.

- **Sign-in**: none, a bearer token, or one header of the owner's choosing.
  It is sent as a header. A header the transport sets itself (`Host`,
  `Content-Type`, `Mcp-Session-Id`, `Mcp-Protocol-Version` and the like) is
  refused, as is a line break in a value.
- **Where it may connect** (`check_server`):
  - The URL must be http or https and carry no user or password.
  - The host and every address it resolves to are checked against
    `services/netpolicy.py`. Cloud metadata, link-local addresses and
    OpenCompany's own ports are never reached. The owner's own network is
    allowed, so a local MCP server works.
  - Plain http is allowed only to this machine or the owner's network: over
    the internet it would carry the sign-in in clear.
  - The addresses are checked when connecting, not pinned for the connection.
- **The connection**: our own httpx client with redirects off, so a server
  cannot bounce the sign-in to another host. The transport is streamable
  HTTP, then the older SSE transport for a server that refuses it (the MCP
  spec's fallback); the one that answered is kept.
- **Time**: reading a server, every transport included, takes at most
  `DISCOVER_S` (45 s). The app waits 60 s for these commands
  (`CREDENTIAL_PROBE_REQUEST_TIMEOUT`), so a slow read never finishes after
  the app gave up on it.
- **Its name**: the slug comes from the name, or else from the host
  (python-slugify, at most 20 characters). It starts with a letter
  (`mcp-` goes in front otherwise), because its tools' names start with it
  and some models (Gemini) take only a name that starts with a letter. A
  second connector needs its own name.

## What is kept

Two rows through `AuthService`, the way named OpenAI-compatible endpoints are
kept:

- `mcp:<slug>`: the sign-in, encrypted. Its `model_params["_mcp"]` holds the
  rest in plain text, so no secret ever goes there:
  - the name and host;
  - the address, without credentials;
  - the transport;
  - the sign-in's kind, and its header name;
  - the server's name, title and version;
  - its instructions;
  - the tools as last accepted, with the owner's settings for them;
  - a refresh waiting to be accepted;
  - when the tools were read.
- `mcp:<slug>_proxy`: the URL, encrypted, since it may carry a key.

**Tools.** Up to 500. Each is kept as the server described it: name, title,
description (up to 4,000 characters), input schema, and the read-only hint.
A tool no model could call is kept with the reason and never renamed:
- its name repeats another;
- `<slug>__<tool>` breaks the strictest provider's rule,
  `^[A-Za-z0-9_-]{1,64}$`;
- its input schema is not an object, or is not valid JSON Schema.

**Settings.** Each usable tool has `{enabled, ask}`. It starts on, and asks
first unless the server marks it read-only. The owner changes both per tool.

## The card

`Credential.catalogue_entries` (`services/plugin/credential.py`) is a
generic hook: `handle_get_credential_catalogue` appends what every credential
class returns from it. `McpConnectorCredential.catalogue_entries` builds one
card per saved connector from the `_mcp_connector` template
(`CredentialRegistry.get_template`). Each card has:
- `id` `mcp:<slug>` and `kind` `mcp`;
- `consumer_category` `custom`, publisher "you", not verified;
- the description "Custom connection · {host}";
- `stored` and `connected` true;
- an `mcp` block: slug, address, transport, sign-in kind, server, tools with
  their settings, the waiting change, and when the tools were read.

No secret is ever on the card.

## Commands

All are `@ws_response`. They are refused from `/ws/internal`. Each change is
announced as `credential.api_key.saved` or `credential.api_key.deleted` for
the connector, and `AuthService` bumps the catalogue version, so every open
page refetches.

| Command | Sends | Returns |
|---|---|---|
| `mcp_connector_add` | `{name?, url, sign_in}` | `{ref, tools}` |
| `mcp_connector_test` | `{ref}` | `{ok, message}`; saves nothing |
| `mcp_connector_refresh` | `{ref}` | `{changes}`, null when nothing changed |
| `mcp_connector_review` | `{ref, accept}` | `{accepted}` |
| `mcp_connector_set_tool` | `{ref, tool, enabled?, ask?}` | `{tool, enabled, ask}` |
| `mcp_connector_remove` | `{ref}` | `{ref}` |

**Refresh holds changes.** A server can change what a tool says it does, so
Refresh never adopts what it read. Tools added, removed or changed in any
way, or new instructions, wait as `pending` until the owner takes or drops
them with `mcp_connector_review`. Until then, the tools the owner accepted
stay as they were. Accepting keeps the owner's settings for tools already
set; new usable tools get the defaults.

## The node

`mcpConnector` (Custom Connector) goes on an agent's Tools and names one
connector (`mcp_connector`, a dropdown of the saved ones). It gives the agent
one tool per tool of that connector that can be used and is on, named
`<connector>__<tool>` with the server's description and input schema
(`ToolNode.tool_bindings`; see [Agent Architecture](./agent_architecture.md)
for how every agent path builds them). Each tool runs with its binding's
settings, which are locked so the model never sets them: `mcp_tool`, `mcp_ask`
(its Ask first), and the labels its card shows (`mcp_label`, `mcp_title`).

- **A call**: the connector is read again, so a tool the server no longer
  offers or the owner turned off since the run began is refused. The
  arguments are checked against the tool's input schema (jsonschema), then
  the tool is called over the connector's transport within `CALL_TIMEOUT_S`
  (300 s). The model gets the answer's text (each part that is not text is
  named) and its structured result; an answer marked as an error comes back
  as the tool's error.
- **Ask first**: the node's `approval` spec holds a call while the employee
  asks first, unless its tool is set not to (`mcp_ask`). A call whose binding
  says nothing asks first. The card names the connector and the tool, and
  reads "Done" or "Not done" once it ran.
- **When settings reach the agent**: the tools are read when an agent's run
  starts, so a tool turned on, or a change to its Ask first, applies from the
  next run. A tool turned off is refused at once.
- CLI agents (Claude Code, Codex) leave the node out; an RLM agent refuses it.

## In hires

Each saved connector is also an app a hire can use. `connector_apps`
(registered with `register_app_source`, see
[apps.py](../server/services/employees/apps.py)) gives one app per connector:
its id and provider id are the connector's reference (`mcp:<slug>`), its name
the connector's, and its one tool the node set to that connector.
`Connections.apps` reads them with the registry's apps, so:

- the setup model is told about them with the connected apps, and a hire that
  names one gets the node on its agent (`mcpConnector` is on the hire
  allowlist, `enabled_nodes`);
- a saved connector counts as connected;
- under Ask first the node stays, since each call waits for the owner per its
  tool's setting;
- from Talk, the Agent Builder offers each connector as its own entry with
  its `app_id`, and `add_tool` adds the one named. An agent already has a
  connector only on a node naming it, so a second connector is a second
  node.

The employee's card lists the connector its node names. The node declares
`app_field = "mcp_connector"`, so the summaries read that parameter
(`GraphIndex.app_params`, one query for the whole team list). A removed
connector is no longer listed.

## The page

- **Add** on the Connectors page (`CatalogLayout.primaryAction`) opens
  `AddConnectorForm`. After a save, the catalogue is refetched, then the new
  connector's page opens. The Welcome guide's AI model step has no Add
  (`customConnectors={false}`).
- **`McpConnectorPanel`** shows:
  - the server, its address, how it signs in (and, in Dev, the transport),
    and when its tools were read;
  - Test, Refresh tools and Remove;
  - a waiting change, with Accept and Discard;
  - each tool with its Use and Ask first switches. A tool no model could call
    shows why, and has no switches.

  Each command waits for the refetched catalogue, so the page shows only what
  the server holds. Remove asks first, then goes back to the list.
- **Disconnect** on a connector's card removes it too.

## Known gaps

- No OAuth sign-in.
- A host's addresses are checked when connecting, not pinned for the
  connection.
