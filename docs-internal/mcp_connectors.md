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
  (python-slugify, at most 20 characters). A second connector needs its own
  name.

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

- No employee uses a connector's tools yet.
- No OAuth sign-in.
- A host's addresses are checked when connecting, not pinned for the
  connection.
