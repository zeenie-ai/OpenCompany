# Pi Find Files (`piFind`)

| Field | Value |
|---|---|
| Category | filesystem / tool |
| Node | [`server/nodes/filesystem/pi_find`](../../../server/nodes/filesystem/pi_find/__init__.py) |
| Agent tool | `pi_find` |
| SDK tool | Pi 1.1.0 `find` |
| Approval class | read; safe to repeat |

Finds workspace files by glob pattern through Pi's built-in find tool.
New [Coding Agents](../specialized_agents/coding_agent.md) include this node connected to their Tools input.
Connect its tool output to an agent's Tools input, or execute it as a graph node.

| Parameter | Meaning |
|---|---|
| `pattern` | Required glob, such as `**/*.ts` |
| `path` | Optional search directory within the workflow workspace |
| `limit` | Optional result limit; SDK default 1000 |

The workflow chooses the root and the adapter rejects paths outside it. The
leaf host also checks filesystem boundaries. Results retain Pi's relative path
format and truncation details. File discovery is read-only and may be retried
after interruption.

Execution uses ordinary node approval and Temporal activity handling, followed
by [`execute_pi_tool`](../../../server/services/pi_builtin_tools.py). The
packaged tool host runs offline, with no implicit executable or package
downloads. The Pi maintenance driver keeps its built-in effects disabled.

Dependencies: shipped Bun, bundled Pi tool host and `rg` available to the
worker. The adapter supplies Pi's file discovery operation using `rg --files`
and then matches the requested glob against that file set, preserving ignore
rules instead of overriding them with a positive ripgrep glob. It excludes
`.git` and `node_modules`; no separate `fd` executable is required. Missing ripgrep produces a tool
error. Existing `fsSearch` nodes and automatic workspace-tool defaults retain
their current behavior.
