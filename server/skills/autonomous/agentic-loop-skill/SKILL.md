---
name: agentic-loop-skill
description: Solve multi-step tasks through observation, focused tool actions, review, and bounded delegation using the connected runtime.
allowed-tools: task_manager python_executor
metadata:
  author: opencompany
  version: "2.0"
  category: autonomous
---

# Iterative Work

Use the native agent loop: observe the available evidence, choose one useful
action, inspect its result, and continue until the requested outcome is met.
The runtime already alternates model turns and tools; iteration does not
require creating another copy of yourself.

## Working loop

1. State the outcome and its acceptance criteria.
2. Inspect current state and available tools. Do not claim an unconnected tool exists.
3. Perform a focused action, or issue independent authorized calls together when supported.
4. Inspect the result and record a concise next step; do not repeat completed work.
5. Verify the deliverable before declaring completion. Report missing access or unresolved uncertainty directly.

Use `write_todos` when connected for a multi-step plan. Use `python_code` for
bounded calculations with its preinjected modules and a fresh namespace each
call; no imports, `eval`, or persistent interpreter state are promised.

## Delegation

For managed employee teams, assign work through `task_manager` using
`assign_task` and an exact connected teammate ID, mission, context, and
acceptance criteria. Review the submission before accepting it. Talk submits
a substantial job through `submit_job`; an assignment receipt is not a result.

A legacy graph may expose direct `delegate_to_*` and `check_delegated_tasks`
tools. Use only the tools actually exposed in that graph, call a delegation
once per task, and follow the returned task identity. Delegation depth is
bounded by runtime configuration (default two child layers, constrained to
one or two); recursive self-delegation cannot create an unlimited loop.

## Continuity and stopping

Context is the authoritative transcript store when connected to an admitted
generation; durable facts are separate explicit Memory items. Keep task
summaries concise rather than moving the whole transcript into arguments.
There is no universal five-iteration runtime default imposed by this skill.

Cooperative Stop closes new action admission and drains admitted work; Resume
continues the pending execution. Explicit cancellation and Reset differ.
When progress stalls, inspect the actual error or seek the missing input.
Never keep spawning tasks, repeat an uncertain external effect, or claim
completion merely because an iteration count was reached.
