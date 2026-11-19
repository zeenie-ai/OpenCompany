# Pi List Directory (`piLs`)

| Field | Value |
|---|---|
| Category | filesystem / tool |
| Node | [`server/nodes/filesystem/pi_ls`](../../../server/nodes/filesystem/pi_ls/__init__.py) |
| Agent tool | `pi_ls` |
| SDK tool | Pi 1.1.0 `ls` |
| Approval class | read; safe to repeat |

Lists entries in a workflow directory using Pi's built-in list tool. Connect
its tool output to an agent's Tools input, or execute it as a graph node.
New [Coding Agents](../specialized_agents/coding_agent.md) include this connection automatically.

| Parameter | Meaning |
|---|---|
| `path` | Optional directory within the workflow workspace |
| `limit` | Optional entry limit; SDK default 500 |

Omitting the path lists the workflow root. The root comes from trusted node
context; the model cannot select a different workspace. Both the node adapter
and the leaf host enforce path boundaries. Pi retains its directory markers,
sorting and truncation messages in the result.

The regular node activity handles approvals, results and retries, then
[`execute_pi_tool`](../../../server/services/pi_builtin_tools.py) invokes the
actual SDK in a separate Bun process. Directory listing is read-only and safe
to repeat. Pi's maintenance driver keeps its built-in effects disabled.

Dependencies: shipped Bun and bundled Pi tool host. No shell, provider access,
or runtime package download is required. Existing filesystem nodes and
automatic workspace-tool defaults are unchanged.
