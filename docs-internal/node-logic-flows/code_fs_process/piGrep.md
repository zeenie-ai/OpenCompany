# Pi Search Text (`piGrep`)

| Field | Value |
|---|---|
| Category | filesystem / tool |
| Node | [`server/nodes/filesystem/pi_grep`](../../../server/nodes/filesystem/pi_grep/__init__.py) |
| Agent tool | `pi_grep` |
| SDK tool | Pi 1.1.0 `grep` |
| Approval class | read; safe to repeat |

Searches file contents in the workflow workspace using Pi's built-in grep
tool and ripgrep. Connect its tool output to an agent's Tools input, or run it
as a graph node.
New [Coding Agents](../specialized_agents/coding_agent.md) include this connection automatically.

| Parameter | Meaning |
|---|---|
| `pattern` | Required regular expression, or literal text when `literal` is true |
| `path` | Optional file or directory within the workspace |
| `glob` | Optional filename glob |
| `ignoreCase` | Optional case-insensitive matching |
| `literal` | Optional fixed-string matching |
| `context` | Optional surrounding line count |
| `limit` | Optional match limit; SDK default 100 |

The adapter and host enforce the trusted workspace. Pi formats matching paths,
line numbers, context and truncation details. Its filesystem reads remain
read-only and can be repeated after an interrupted activity.

Execution goes through the regular node activity and approval handling, then
[`execute_pi_tool`](../../../server/services/pi_builtin_tools.py) starts the
bundled SDK tool host. The Pi maintenance driver does not run this tool directly.
The `ignoreCase` spelling follows the SDK's canonical schema.

Dependencies: shipped Bun, bundled Pi tool host and `rg` available to the
worker. The host runs offline and does not download missing executables;
missing search dependencies produce a tool error. Existing `fsSearch` and
automatic workspace tools are unchanged.
