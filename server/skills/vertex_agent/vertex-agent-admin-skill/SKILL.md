---
name: vertex-agent-admin-skill
description: Create, list, inspect, and delete custom managed agents on the Gemini Enterprise Agent Platform. Custom agents build on the Antigravity base agent with their own system instruction and built-in cloud tools; run them with the Vertex Agent node.
allowed-tools: "vertex_agent_admin"
metadata:
  author: machina
  version: "1.0"
  category: agents

---

# Vertex Agent Admin Skill

Lifecycle management for **custom managed agents** on the Gemini
Enterprise Agent Platform (Agents API). A custom agent is the prebuilt
Antigravity agent plus your own system instruction and a chosen set of
built-in cloud tools.

## Node: Vertex Agent Admin

### Operations

| Operation | Purpose | Key fields |
|---|---|---|
| `create` | Create a custom agent (long-running: ~2-3 min the first time, seconds after) | `agent_id`, `description`, `system_instruction`, `base_agent`, `tools` |
| `list` | List custom agents in the project | — |
| `get` | Inspect one agent's config | `agent_id` |
| `delete` | Delete a custom agent (destructive, irreversible) | `agent_id` |

### Fields

- `agent_id` — 1-63 chars, lowercase letters / numbers / hyphens, must
  start with a letter. Example: `research-assistant-v1`.
- `tools` — built-in cloud tools the agent may use: `code_execution`,
  `filesystem`, `google_search`, `url_context`.
- `project_id` — GCP project (auth via gcloud Application Default
  Credentials). Leave empty to use a stored `AIza` Gemini API key.

### Response

`create` / `get` return the agent config under `agent`; `list` returns
`agents` + `count`; `delete` returns `deleted: true`.

## Using a custom agent

After `create`, put the `agent_id` into a **Vertex Agent** node's
`agent` parameter — the interaction node runs it with the same memory
continuity and tool bridging as the prebuilt agent. Deleting an agent
does not delete past interactions or sandboxes (those expire on their
own 7-day TTL).
