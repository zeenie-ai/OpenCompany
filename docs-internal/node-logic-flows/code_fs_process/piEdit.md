# Pi Edit File (`piEdit`)

| Field | Value |
|---|---|
| Category | filesystem / tool |
| Node | [`server/nodes/filesystem/pi_edit`](../../../server/nodes/filesystem/pi_edit/__init__.py) |
| Agent tool | `pi_edit` |
| SDK tool | Pi 1.1.0 `edit` |
| Approval class | write; unsafe to repeat after interruption |

Applies Pi's text edits to a workflow file and retains the SDK's diff details.
New [Coding Agents](../specialized_agents/coding_agent.md) include this node connected to their Tools input.
Connect its tool output to an agent's Tools input, or execute it as a graph node.

| Parameter | Meaning |
|---|---|
| `path` | Required workspace file path |
| `edits` | Required array of `{oldText, newText}` replacements |

Pi evaluates disjoint replacements against the original content and preserves
the file's BOM and line-ending style. The canonical Pi 1.1.0 input is the
`edits` array, rather than top-level `oldText` and `newText` fields. Missing,
ambiguous or overlapping matches return the SDK error for correction.

The node and leaf host enforce the trusted workflow workspace. A shared Python
path lock serializes mutations of the same file within the worker, including
existing filesystem nodes. Execution uses the ordinary approval and Temporal
activity path, followed by
[`execute_pi_tool`](../../../server/services/pi_builtin_tools.py). The SDK's
maintenance driver remains unable to execute effects directly.

An interrupted edit is not automatically repeated: a replacement may match
again and change the file a second time. The existing unknown-outcome guard
applies. Dependencies are shipped Bun and the bundled Pi tool host; no runtime
package downloads are performed. Existing workspace defaults are unchanged.
