# Pi Read File (`piRead`)

| Field | Value |
|---|---|
| Category | filesystem / tool |
| Node | [`server/nodes/filesystem/pi_read`](../../../server/nodes/filesystem/pi_read/__init__.py) |
| Agent tool | `pi_read` |
| SDK tool | Pi 1.1.0 `read` |
| Approval class | read; safe to repeat |

Reads a file in the workflow workspace through Pi's built-in read tool.
New [Coding Agents](../specialized_agents/coding_agent.md) include this node connected to their Tools input.
Connect its tool output to an agent's Tools input, or execute it as a graph node.

| Parameter | Meaning |
|---|---|
| `path` | Required workspace file path |
| `offset` | Optional starting line, **1-based** |
| `limit` | Optional number of lines to read |

The node validates the path against the trusted workflow root. The leaf host
checks filesystem paths again; a `cwd` supplied by the model cannot select a
different workspace. Pi's pagination and truncation messages are retained.
PNG, JPEG, GIF and WebP image results become durable media references before
entering workflow history. Raw image bytes are not retained in tool messages.
Image resizing is disabled in the Pi host because its optional Photon assets
are not shipped; BMP conversion is not guaranteed.

Execution uses the normal node activity and approval path, then
[`execute_pi_tool`](../../../server/services/pi_builtin_tools.py) starts the
packaged Bun tool host. The Pi maintenance driver keeps its built-ins disabled.
No provider call or runtime package download is needed. This node does not
replace the existing `fileRead` node or automatic workspace-tool defaults.

Dependencies: shipped Bun runtime and the bundled Pi tool host. Missing files,
invalid paths and SDK errors become node/tool errors. Read-only calls may be
retried after interruption.
