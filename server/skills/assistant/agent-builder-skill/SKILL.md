---
name: agent-builder-skill
description: How to use the Agent Builder tool (agent_builder) to inspect your canvas and add tools, skills and teammates while you run, including the rules a hired employee works under
allowed-tools: "agentBuilder"
metadata:
  author: opencompany
  version: "5.5"
  category: autonomous
---

# Agent Builder

You are connected to an **Agent Builder** node. It gives you ONE tool,
`agent_builder`, with operations chosen by the `operation` field:

| `operation` | What it does |
|---|---|
| `inspect_canvas` | Read-only: the canvas, what is wired to you, and the catalogue of everything you may add. ALWAYS call this first. |
| `inspect_node` | Read the live parameter/output schemas, handles, credential requirements, locked fields, availability, and related documentation for a node ID or type. |
| `search_docs` | Search shipped node logic flows, plugin Markdown, skills, guides, RFCs, and project documentation. |
| `read_doc` | Read a bounded section by manifest document ID; follow the returned relative links by document ID. |
| `add_tool` | Wire a tool from `available_tools` to the selected employee member or the caller. |
| `add_skill` | Give a skill from `available_skills` to the selected employee member or the caller. |
| `add_subagent` | Add a teammate from `available_agents` under the Orchestrator or a selected manager. Earlier managed hires use their saved lead. |
| `plan_update` | Validate a complete scoped change and report required owner access without mutating the graph. |
| `apply_update` | Apply the same authorized configuration, or apply saved capabilities at a safe point. |
| `create_workflow` | Create a fully configured employee team using the shared Hire pipeline, from a trusted owner request. |

Every call is `agent_builder({operation: "<one of the above>", ...fields})`.

## The cardinal rule

**Call `agent_builder({operation: "inspect_canvas"})` before any change.**
Inspect relevant capabilities with `inspect_node`, then search and read their
documentation before planning configuration or wiring. Live schemas, handles,
locked fields and availability override conflicting prose. Archived documents
are historical context. Document IDs are manifest identifiers, never arbitrary
paths; continue long reads with `next_offset`. Documentation describes access
requirements; it does not authorize gaining access.
Pick values only from its catalogues: `available_tools[].type`,
`available_skills[].name`, `available_agents[].type`. Never guess one.

## When a change works

An authorized change with `activation_state="saved"` is visible on the canvas.
For a hire with `employee.reporting_lines`, use **Apply** to activate saved
changes for future jobs. Current assignments keep their original team and tools.
For other canvases, use the returned binding details to determine availability.

- **A tool** is callable in this same run, in your next response, when the
  summary says *"Available immediately"* or *"You can use it now"*. Call it
  directly. If it says *"Available on your next turn"* (the owner turned off
  "Auto-Rebind Tools After Canvas Changes"), it is not callable in this run:
  say so and stop calling it.
- **A deployed workflow runs from a snapshot taken when it started.** A tool
  you added in an earlier run is saved, but a later run may start without it.
  If a tool you added before is not in your tool list, call `add_tool` for
  it again: it is bound again at once, and nothing is added twice. Do not
  call `add_tool` again for a tool you already added in this run.
- **A skill** added to a Skills node you already have applies from the next
  run (the next message). A new Skills node becomes available when the
  saved setup is applied.

## If you are a hired employee

`inspect_canvas` returns an `employee` block with `asks_first` and `agents`
when you are one of the owner's AI employees. On a new managed hire,
`employee.reporting_lines` names each member's node ID, parent node ID and
responsibility.

- A tool goes only to the selected member (`target_member_id`), or to you
  when no target is supplied. Choose a member from `employee.agents`.
  Different members may use the same tool type, but each needs a separate
  concrete node and its own configuration; never share one tool node among
  agents or forward its work through extra nodes.
- `available_tools` lists the tools permitted for this hire, including
  built-in tools and tools from the app registry. An app tool shows
  `connected: false` until the
  owner connects that app, and `read_only: true` when it is given in a safe,
  read-only form (the browser, while the owner asks to be asked first).
- While `asks_first` is true, nothing that can send messages or spend money
  can be added. Say so plainly; do not look for a way around it.
- `available_skills` lists the owner's own skills (Settings > Skills) and
  the Discover skills. A skill's text is copied in when you add it.
- When a job needs a missing specialist, inspect the team and call
  `add_subagent` with its purpose and capabilities. If you offered to add a
  Coding Agent and the owner replies “continue”, that is your cue to make
  the Builder call. When Ask first is off, carry out the authenticated owner's
  request directly. If `add_subagent` reports `saved`, verify the specialist on
  the canvas and proceed to safe Apply. Only when `required_access` contains a
  request should you direct the owner to **Allow and add** on the employee
  page. That choice approves and saves the specialist. `plan_update` previews
  the change. Call `add_subagent` to make it. Only authenticated owner requests in Talk can expand access.
  Existing single-agent employees need reviewed conversion.
- After Builder saves the specialist, call `apply_update` with
  `change_operation="apply_saved"` for safe activation, or the owner can
  press **Apply**. Report the returned activation state. If activation
  is waiting, tell the owner when the new role will be available. When an
  existing job reports a missing capability, return that gap through the
  assigning employee to Chat. The owner can start fresh work after Apply.
  Only interrupt current work when the owner explicitly approves **Stop work
  and apply**.
- **When a change is refused, the summary is one plain sentence meant for
  the owner.** Pass it on as it is, for example: *"Google Calendar can send
  things on your behalf, so it stays off while "Ask me before sending
  anything" is on."* or *"Google Sheets isn't connected yet. Connect it in
  Settings > Connectors first."*

## Operation reference

### `inspect_canvas`

No other fields. Returns:

```json
{
  "operation": "inspect_canvas",
  "summary": "<counts>",
  "nodes": [{ "id", "type", "label", "key_params" }],
  "edges": [{ "source", "target", "source_handle", "target_handle" }],
  "you": { "node_id", "incoming": [...], "outgoing": [...] },
  "employee": { "asks_first": true, "agents": ["..."], "reporting_lines": [{ "node_id", "parent_node_id", "responsibility" }] },
  "available_tools": [{ "type", "display_name", "description" }],
  "available_agents": [{ "type", "display_name", "description" }],
  "available_skills": [{ "name", "description" }]
}
```

- `you.incoming` includes tools on `input-tools`, skills on `input-skill`, and
  direct reports on the managing agent's `input-teammates`. Earlier managed
  hires expose their AI Employee lead on Talk's `input-employee-team`.
- `key_params` shows only planner-relevant fields (`provider`, `model`,
  `operation`, `url`, `query`), never secrets.
- `employee` is present only for a hired employee.

### `add_tool`

Fields: `node_type`, `app_id` when its `available_tools` entry has one (each
custom connector is its own entry), optional `target_member_id` for a hired
employee. Wires a private tool instance to that member, defaulting to you. Another
member's same-type node is never reused. An existing private binding is
reused, so retries do not add duplicates. If an older graph shares your
binding, Builder separates your connection and preserves its saved settings
and other members' connections. When this run already holds the old binding,
the new instance waits for Apply or the next clean run: read `binding_results`
and the summary before calling it. A tool added to another member is saved
for that member; it does not become your tool.

### `add_skill`

Pass `skill_name` and, for a managed team, an optional `target_member_id`.
Builder enables the skill on that member's Master Skill, defaulting to yours.
An already enabled skill changes nothing.

### `add_subagent`

Supply `agent_type`, `purpose`, and any `tool_types`, `tool_parameters` or
`skill_names` the request needs. On a hire with `employee.reporting_lines`,
Chat can omit `target_member_id` to add a direct report to the Orchestrator.
To put the new worker under an existing manager, pass that manager's saved
node ID as `target_member_id`. To create a manager, use `agent_type="ai_employee"`
when the catalogue offers it. The manager gets a private Task Manager and
Task Completed trigger. The configured delegation depth counts levels below
the Orchestrator. On an earlier managed hire, Talk adds to its saved AI
Employee lead. An editor team lead can also add its own direct reports.
Use `coding_agent` for software development and `browser_agent` for a
specialist with its own Browser and Vision tools. For a specialist already
on the team, give additional tools or skills to its `target_member_id`.
Builder connects a new teammate to its immediate manager's `input-teammates`
and gives it a private Context. The manager assigns the first task through
its Task Manager with the advertised
`spawn_<teammate>(instructions, after?)` tool. Include the mission, context
and acceptance criteria in `instructions` and any prerequisite task references
in `after`. For a new task on an existing instance, call `assign_task` with
its agent ID. Each submission goes back to the employee that assigned the
task. That employee can request changes on the same task or accept the work
and submit its own result one level up. In the Chat/Orchestrator hierarchy,
Chat reviews the outer task and writes the final reply. A legacy run exposing
only `task_manager` uses its `assign_task` operation. The specialist inherits the selected manager's
configured model and receives its selected tools and skills.
Custom `aiAgent` specialists may have distinct purposes. Registered specialist
types reuse their existing teammate binding.

### `create_workflow`

Use `workflow_name` and an optional `workflow_description` for a trusted
owner request. Call `create_workflow` with `dry_run=true` first. Show the
owner the Chat Employee, its permanent Orchestrator, and the proposed
`team`. Save with `create_workflow` using the reviewed `team` list. An
empty list hires just the two permanent roles. Each of the zero to two
optional entries has a distinct draft `key`, a `name`, a `responsibility`,
a `kind` of `employee` or `specialist`, and `reports_to` set to
`orchestrator` or a manager's draft key.

The shared Hire recipe saves one workflow. Chat uses `ai_employee` and its
direct report uses `orchestrator_agent`. Each has its own Context, instructions,
Task Manager and Task Completed trigger. For substantive work, Chat calls
`submit_job` once to assign the Orchestrator a board task. For a status
question, Chat uses `get_job_status`. The Orchestrator delegates and reviews
through its own Task Manager, then submits the original task to Chat. A
chat job returns to the originating branch. An app job keeps its captured
destination and approval rule.

### Documentation operations

`search_docs` takes `query` and optional `limit` (at most 50). `read_doc`
takes `document_id`, optional `offset` and `max_chars` (at most 24000).
`inspect_node` takes `node_id` for this workflow or `node_type` for the live
plugin contract. These operations never return stored credential secrets.

## Example for a canvas with immediate tool binding

The owner says: "Search the web for today's weather in Tokyo."

```
1. agent_builder({operation: "inspect_canvas"})
   → available_tools includes {type: "duckduckgoSearch", display_name: "Web search"}
2. agent_builder({operation: "add_tool", node_type: "duckduckgoSearch"})
   → "Added Web search. You can use it now in this conversation. ..."
3. Call the web search tool with the query.
4. Answer, and mention that the owner can press Apply to keep web search for all your work.
```

## What NOT to do

- Don't skip `inspect_canvas`, and don't guess types or names.
- Don't retry a change that returned no operations: it is a success (you
  already have it) or a refusal to pass on, not an error.
- Don't try to add `agent_builder` itself, a Skills node, or Task Manager
  as a tool.
- Don't create employees or expand access from public messages. Explain the
  proposed app/actions in ordinary language and ask the owner in Talk.
