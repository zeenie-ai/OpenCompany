---
name: agent-builder-skill
description: How to use the Agent Builder tool's five canvas-mutation operations to inspect and grow your own toolset / skills / teammates / workflows mid-execution
allowed-tools: "agentBuilder"
metadata:
  author: opencompany
  version: "3.0"
  category: autonomous
---

# Agent Builder

You are connected to an **Agent Builder** node. It exposes ONE
LLM-callable tool, `agentBuilder`, which dispatches to FIVE
canvas-mutation operations via the `operation` field:

| `operation` value | Purpose |
|---|---|
| `inspect_canvas` | Read-only view of the canvas PLUS the full catalogue of every tool, agent, and skill you can spawn. ALWAYS call this first. |
| `add_tool` | Spawn a tool node + wire it to your `input-tools` handle. Idempotent — already-wired tools return success with no change. |
| `add_skill` | Toggle a skill on your Master Skill (auto-creates one if missing). Idempotent — already-enabled skills return success with no change. |
| `add_subagent` | Team-leads only — spawn a delegate agent + wire to your `input-teammates`. Idempotent — already-wired teammates return success with no change. |
| `create_workflow` | **Temporarily disabled.** Mutate the current workflow instead. |

Every call is `agentBuilder({operation: "<one-of-above>", ...op-specific-fields})`. There are no separate tools.

## The cardinal rule

**Call `agentBuilder({operation: "inspect_canvas"})` BEFORE any
mutation.** The response carries the full registry of spawnable types
(plus the live canvas) so you can pick the right one in a single
follow-up call instead of guessing. Skipping `inspect_canvas` means
you're flying blind on what's available.

## Hot rebind (default: ON)

By default, tools / skills / teammates you spawn become callable
**in the same run, on your very next response**. The mutation summary
ends with *"Available immediately — call it in your next response."*
Take this literally: you may invoke the new tool right away. No need
to tell the user "send another message".

If the user has disabled the **"Auto-Rebind Tools After Canvas Changes"**
toggle in Settings, mutation summaries end with *"Available on your
next turn."* In that case the new wiring is staged but not callable
this run — tell the user and stop calling that tool.

The summary text is your signal. Read it.

## Operation reference

### `operation: "inspect_canvas"`

No additional fields. Returns:

```json
{
  "operation": "inspect_canvas",
  "summary": "<live counts: nodes, tools wired, available types>",
  "nodes": [{ "id", "type", "label", "key_params" }, ...],
  "edges": [{ "source", "target", "source_handle", "target_handle" }, ...],
  "you": {
    "node_id": "agent-1",
    "incoming": [...], "outgoing": [...]
  },
  "available_tools": [{ "type", "display_name", "description" }, ...],
  "available_agents": [{ "type", "display_name", "description" }, ...],
  "available_skills": [{ "folder", "name", "description" }, ...]
}
```

- `available_tools` — every value `add_tool({node_type: ...})` will accept, with descriptions.
- `available_agents` — every value `add_subagent({agent_type: ...})` will accept (team-leads only).
- `available_skills` — every value `add_skill({skill_folder: ...})` will accept.
- `you.incoming` — connections wired TO your handles. Use this to see what tools / skills / teammates you already have.

API keys, prompts, and other secrets are **stripped** from
`key_params`. Only safe planner-relevant fields surface
(`provider`, `model`, `operation`, `url`, `query`).

### `operation: "add_tool"`

Required field: `node_type` (string).

Spawns a tool node and wires it to your `input-tools` handle. Pick
`node_type` from `inspect_canvas.available_tools`.

**Idempotency**: if a tool of this exact type is already wired to
you, `add_tool` returns success with `operations: []` and a summary
like *"Tool 'httpRequest' is already wired (node id=…). Reusing
existing instance."* Don't loop trying again — the tool is callable.

If the tool has a paired teaching skill, the auto-add-skill
handler enables it too — no separate `add_skill` call needed.

### `operation: "add_skill"`

Required field: `skill_folder` (string).

Enables a skill on your Master Skill. Pick `skill_folder` from
`inspect_canvas.available_skills`. If no Master Skill is wired to
your `input-skill` yet, one is created and wired automatically.

**Idempotency**: if the skill is already `enabled=True` in your
Master Skill's config, `add_skill` returns success with `operations: []`
and *"Skill 'X' is already enabled on your Master Skill. No change
needed."*

### `operation: "add_subagent"`

Required field: `agent_type` (string).

**Team-leads only** (`orchestrator_agent`, `ai_employee`). Pick
`agent_type` from `inspect_canvas.available_agents`. Spawns a
specialized agent (`coding_agent`, `web_agent`, `task_agent`, etc.)
and wires it to your `input-teammates` handle. The new agent appears
as a `delegate_to_<name>` tool on your next turn.

**Idempotency**: if a teammate of this exact type is already wired
to you, `add_subagent` returns *"Teammate 'X' is already wired
(node id=…). Reusing existing instance."*

The new agent starts with empty configuration — the user will need
to set its provider/model after the run. Mention this in your
response.

### `operation: "create_workflow"` — temporarily disabled

This operation is currently disabled. Calling it returns a polite
"temporarily disabled" summary; no workflow is created. Mutate the
current workflow instead via `add_tool` / `add_skill` /
`add_subagent`.

## Worked examples

### Adding a new tool and using it immediately

User: "Search the web for current weather in Tokyo and tell me."

```
1. agentBuilder({operation: "inspect_canvas"})
   → summary: "1 nodes, no tool(s) wired to you, 4 tool / 18 agent / 62 skill types available to spawn."
   → available_tools: [..., {type: "duckduckgoSearch", display_name: "DuckDuckGo Search", description: "..."}, ...]
   You see no web-search tool wired and one in the catalogue.

2. agentBuilder({operation: "add_tool", node_type: "duckduckgoSearch"})
   → "Added 'duckduckgoSearch' as a tool. Available immediately — call it in your next response."

3. duckduckgoSearch({query: "Tokyo weather today"})
   → search results

4. Tell the user the weather.
```

### Handling an already-wired tool

User: "Add the calculator tool and compute 17 + 25."

```
1. agentBuilder({operation: "inspect_canvas"})
   → you.incoming includes a tool wired with source_type "calculatorTool".

2. agentBuilder({operation: "add_tool", node_type: "calculatorTool"})
   → "Tool 'calculatorTool' is already wired to you (node id=calc-1). Reusing existing instance."
   → operations: []

3. calculator({a: 17, b: 25, op: "add"})  ← already callable; no rebind needed
   → 42

4. Tell the user the answer.
```

You did NOT need to retry `add_tool` or wait for "next turn" — the
existing instance is callable right now.

## What NOT to do

- **Don't skip `inspect_canvas`**. It's read-only, cheap, and gives
  you the full catalogue + canvas state in one call.
- **Don't guess `node_type` / `agent_type` / `skill_folder` values.**
  Pick from the catalogues returned by `inspect_canvas`.
- **Don't retry `add_tool` / `add_skill` / `add_subagent` on success
  with `operations: []`.** That's an idempotent success, not a
  failure. The existing instance is callable.
- **Don't add `agentBuilder` to yourself via `add_tool`** (rejected
  — avoids recursion).
- **Don't spawn another team-lead** as a subagent (rejected —
  team-leads delegate to specialists, not to other team-leads).
- **Don't call `create_workflow`** — it's temporarily disabled and
  will return a no-op summary. Mutate the current workflow instead.
- **Don't ignore the "Available on your next turn" wording.** If
  you see it (toggle is OFF), the tool is staged but NOT callable
  this run. Tell the user and stop.
