# Pi Write File (`piWrite`)

| Field | Value |
|---|---|
| Category | filesystem / tool |
| Node | [`server/nodes/filesystem/pi_write`](../../../server/nodes/filesystem/pi_write/__init__.py) |
| Agent tool | `pi_write` |
| SDK tool | Pi 1.1.0 `write` |
| Approval class | write; safe to repeat the same full-content write |

Creates or replaces a text file in the workflow workspace using Pi's built-in
write tool. Connect its tool output to an agent's Tools input, or execute it as
a graph node.
New [Coding Agents](../specialized_agents/coding_agent.md) include this connection automatically.

| Parameter | Meaning |
|---|---|
| `path` | Required workspace file path |
| `content` | Required complete replacement text; an empty string creates an empty file |

The workflow chooses the workspace. Both the node adapter and the tool host
check path containment. File mutations hold the shared Python path lock so
concurrent Pi and existing filesystem nodes cannot edit the same file within
one worker at the same time. The SDK creates missing parent directories and
reports its write result through the normal node envelope.

Execution uses the existing approval and Temporal node-activity path, then
[`execute_pi_tool`](../../../server/services/pi_builtin_tools.py) invokes the
actual Pi SDK in a separate Bun process. The maintenance driver does not
execute this effect directly. Repeating the same complete-content overwrite
is allowed after interruption; incremental edits have a separate policy.

Dependencies: shipped Bun runtime and bundled Pi tool host. No automatic
package downloads occur. Existing `fileModify` nodes and workspace defaults
retain their current behavior.
