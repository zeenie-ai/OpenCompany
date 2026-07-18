---
name: subagent-orchestration
description: Coordinate connected subagents through durable Task Manager assignments, bounded parallel execution, review, retry, reassignment, and acceptance.
allowed-tools: task_manager
metadata:
  author: opencompany
  version: "3.0"
  category: assistant
  icon: "🤖"
  color: "#8B5CF6"
---

# Durable Subagent Orchestration

Connected teammates extend the team lead's capabilities. Before declining a
request, inspect the connected teammate list and match the work to an agent's
label, type, and capability description.

## Mandatory delegation path

All teammate work must be created with `task_manager` using
`operation="assign_task"`. Never call `delegate_to_*` directly. The runtime
keeps those delegate identities private and uses them only after Task Manager
has authorized and persisted the assignment.

For every assignment provide:

- `title`: short human-readable task name;
- `mission`: one bounded outcome;
- `assignee_node_id`: an exact connected teammate ID;
- `context`: only relevant inputs and constraints;
- `acceptance_criteria`: observable conditions for approval;
- `depends_on`: task IDs when sequencing is required.

Independent `assign_task` calls may be emitted together. All tasks persist
before execution and enter a deterministic queue. At most three descendants,
including grandchildren, run concurrently; excess work remains `queued`.

## Capability matching

- Android agents: connected Android device services.
- Coding agents: implementation, code analysis, tests, and computation.
- Web agents: browsing, HTTP, extraction, and web interaction.
- Social agents: messaging and social-platform operations.
- Travel agents: location and itinerary work.
- Task agents: scheduling and task-domain operations.
- Custom `aiAgent` teammates: use their visible label and description.

Only assign agents connected to this lead's `input-teammates` handle. Never
invent an agent ID, use an ordinary tool-edge agent, or target another team's
member.

## Review lifecycle

Task states are `blocked`, `queued`, `running`, `submitted`, `accepted`,
`failed`, and `cancelled`.

A worker completion produces `submitted`, not Done. When signalled:

1. Inspect the result and acceptance criteria with `get_task` or `list_tasks`.
2. Copy `task.id` into `task_id` and `task.revision` into
   `expected_revision`.
3. Choose one outcome:
   - `accept_task` when the work satisfies the criteria;
   - `retry_task` for another attempt with the same assignee;
   - `reassign_task` to a different connected teammate;
   - `modify_task` only while blocked or queued;
   - `cancel_task` when work should stop or be waived.

When exactly one submitted task exists, `accept_task` may omit identifiers and
the runtime resolves it safely. With multiple submissions, always identify the
task explicitly. Revision conflicts mean the task changed: refresh it and make
a new decision instead of overwriting newer state.

## Completion gate

Call `finish_team` only when every required task is `accepted` or intentionally
`cancelled`. Synthesize accepted results in the final answer. Team Monitor is a
read-only view: it shows connected agents and lifecycle state, while all
mutations belong to Task Manager.
