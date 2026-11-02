---
name: memory-skill
description: Remember, recall, correct, and forget durable facts and preferences using the connected Memory tool. Conversation history belongs to Context.
allowed-tools: memory
metadata:
  author: opencompany
  version: "3.0"
  category: memory
  icon: "🧠"
  color: "#8B5CF6"
---

# Durable Memory

The `simpleMemory` node exposes one callable tool, `memory`, with six
operations. It stores facts explicitly requested by the agent. Conversation
continuity and transcript compaction belong to Context and the agent runtime;
Memory does not automatically record, archive, or replay conversations.

## Operations

| Operation | Required arguments | Purpose |
|---|---|---|
| `remember` | `content` | Store a stable fact; optionally set `title`, `category`, `tags`, or `expires_at`. |
| `recall` | `query` | Search remembered facts; optionally filter `categories` and `tags`. |
| `list` | none | Browse remembered facts, optionally with the same filters. |
| `get` | `memory_id` | Read one item and its current version. |
| `update` | `memory_id`, `expected_version`, `patch` | Correct an item using a nonempty patch. |
| `forget` | `memory_id`, `expected_version` | Remove an item using its current version. |

`recall` and `list` accept `limit` (1–100, default 20) and a returned `cursor`
for pagination. Use the item identifiers and versions returned by the tool;
do not invent them. On a version conflict, read the item again before deciding
whether to retry the change.

```json
{"operation":"remember","content":"The user prefers concise weekly reports.","category":"preferences","tags":["reports"]}
```

```json
{"operation":"recall","query":"weekly report preferences","limit":5}
```

```json
{"operation":"list","categories":["preferences"],"limit":20}
```

## Working rules

- Check Memory before claiming ignorance about the user's preferences or past decisions.
- Save stable facts and explicit requests to remember; avoid copying entire transcripts.
- Recall only facts relevant to the task, and distinguish a remembered fact from a new inference.
- Use `get` before `update` or `forget` to obtain the current `expected_version`.
- The runtime supplies owner, workflow, and Memory-node scope. Never pass scope IDs or change `reset_policy` through tool arguments.
- Workflow Reset preserves Memory by default. An operator may configure that node's `reset_policy` to `clear`; this is separate from Context reset.

The Memory panel offers item management, rather than an editable Markdown
transcript. The legacy hyphenated memory callables are not available.
