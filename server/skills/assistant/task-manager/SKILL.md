---
name: task-manager
description: Coordinate durable team tasks by assigning connected teammates, reviewing results, and accepting, retrying, reassigning, or cancelling work.
allowed-tools: task_manager
metadata:
  author: opencompany
  version: "2.3"
  category: automation
---

# Task Manager

Use `task_manager` as the team lead's durable control plane. It is scoped to
the current workflow execution and only permits assignment to agents connected
to this lead's `input-teammates` handle.

## Required workflow

1. Call `list_tasks` with `include_history=true` before assigning work so you
   do not duplicate a mission from an earlier workflow execution.
2. Split independent work into bounded tasks with explicit acceptance criteria.
3. Call `assign_task` once per mission. Use the connected teammate node ID or
   the exact delegate name reported by the lead's available teammate list.
   Do not call `delegate_to_*` directly; delegate descriptors are internal
   dispatch identities, not the lead's task-creation interface.
4. Independent tasks may be assigned together. The durable queue starts at most
   three descendants across the whole agent tree; excess tasks remain queued.
   After `assign_task` returns `queued`, report that delegation started and
   return immediately. Do not poll or wait in the assigning invocation.
5. When a teammate submits work or an attempt fails, call `get_task`, then use
   `inspect_task_trace` before retrying, reassigning, or reporting a terminal
   failure. Start with `detail="summary"`; use `failures` or a paginated
   `timeline` only when the summary does not explain the outcome. For large
   histories use `detail="search"` with a narrow `query`, inspect the returned
   context lines, and follow `next_cursor` until the match is found or the
   cursor is empty. Prefer `categories` and `all_terms` over broad searches.
   Copy `task.id` to `task_id` and
   `task.revision` to `expected_revision` for the review mutation.
6. Choose exactly one review outcome:
   - `accept_task` when the result satisfies the task;
   - `modify_task` for blocked or queued work;
   - `retry_task` when the mission is still valid;
   - `reassign_task` to another connected teammate with revision context;
   - `cancel_task` when the work is no longer needed.
7. Call `finish_team` only after every required task is accepted or intentionally
   cancelled. Synthesize accepted results in the final report and disclose any
   unresolved failure.

## Operations

| Operation | Purpose | Important arguments |
|---|---|---|
| `assign_task` | Persist and queue a bounded mission | `title`, `mission`, `assignee_node_id` or `delegate_name`, optional `context`, `acceptance_criteria`, `depends_on` |
| `list_tasks` | Inspect current or historical tasks | optional `status_filter`, `include_history` |
| `get_task` | Review one task and all attempts | `task_id` |
| `inspect_task_trace` | Inspect or grep the task attempt's sanitized Temporal events | `task_id`; optional `attempt`, `detail`, `cursor`, `limit`; search supports `query`, `search_mode`, `categories`, `context_lines`, `scan_limit` |
| `modify_task` | Change queued/blocked work | `task_id`, `expected_revision`, changed task fields |
| `cancel_task` | Cancel queued or running work | `task_id`, `expected_revision`, `reason` |
| `retry_task` | Queue a new attempt | `task_id`, `expected_revision`, optional revision context |
| `reassign_task` | Queue a new attempt for another teammate | `task_id`, `expected_revision`, new connected assignee |
| `accept_task` | Record lead approval of submitted work | `task_id`, `expected_revision` |
| `finish_team` | Finalize after the review barrier clears | optional summary |

If exactly one task is `submitted` in the current execution, `accept_task` may
omit `task_id` and `expected_revision`; the runtime safely resolves that single
task and its current revision. If zero or multiple submissions await review,
you must call `list_tasks` or `get_task` and pass both fields. Never guess.

`mark_done` is a deprecated compatibility alias for `accept_task`. Never use it
to discard history.

## State model

- `blocked`: waiting for dependencies.
- `queued`: durable and waiting for a concurrency permit.
- `running`: owned by a teammate.
- `submitted`: teammate finished; lead review is required.
- `accepted`: lead approved the work.
- `failed`: no automatic attempt remains.
- `cancelled`: intentionally stopped; may be revised and reassigned.

Do not treat `submitted` as finished team work. Do not poll a running task;
`taskTrigger` starts a separate review invocation carrying the owning execution.
Do not invent teammate IDs, team IDs, or
execution IDs—the runtime supplies authority and rejects cross-team access.
Trace output is intentionally sanitized and scoped to the persisted task
execution. Never request or infer raw Temporal workflow IDs.

Trace search is a bounded grep-like read over normalized event metadata, never
raw payloads. A call scans at most 500 events and returns only matches plus up
to five surrounding events. Continue from `next_cursor`; do not restart from
the beginning or load the entire timeline when a targeted search is sufficient.

## Team Monitor interpretation

Team Monitor is read-only. It shows graph-connected teammates immediately and
merges their persisted `working`/`idle` status during execution. Its Done count
means `accepted`, not merely `submitted`. Use Task Manager—not Team Monitor—to
accept, retry, modify, reassign, cancel, or finish work.
