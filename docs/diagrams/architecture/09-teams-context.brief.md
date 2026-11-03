# 09. Teams and context

Status: current implementation.

Baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`. Source review is static; no provider or device sessions were run.

## Boundary and layout

Main left-to-right loop: lead turn → Task Manager → durable task record → delegated workflow → connected teammate → persisted result → taskTrigger → lead review. Beneath this loop, put opt-in Context, explicit Memory and materialized Skills/Workspace as three separate resources with only their listed edges. Make the queued return a short feedback arrow from Task Manager to lead.

## Exact components

- "Lead agent turn" (service)
- "Task Manager" (service)
- "Durable task records" (state)
- "DelegatedTaskWorkflow" (temporal)
- "Connected teammate" (service)
- "Persisted task outcome" (state)
- "taskTrigger" (temporal)
- "Lead review and acceptance" (service)
- "Opt-in Context conversation" (state)
- "Explicit Memory tool" (service)
- "Skills and workspace files" (state)
- "Generation control" (temporal)

## Exact directed relationships

- Lead agent turn → Task Manager [assign_task]
- Task Manager → Durable task records [persist and queue]
- Task Manager → Lead agent turn [returns queued]
- Durable task records → DelegatedTaskWorkflow [Temporal execution]
- DelegatedTaskWorkflow → Connected teammate [authorized teammate]
- Connected teammate → Persisted task outcome
- Persisted task outcome → taskTrigger [terminal event after persistence]
- taskTrigger → Lead review and acceptance [new lead firing]
- Lead review and acceptance → Task Manager [accept / retry / finish]
- Opt-in Context conversation → Lead agent turn [load conversation]
- Lead agent turn → Opt-in Context conversation [save each turn]
- Opt-in Context conversation → Lead review and acceptance [continue same conversation]
- Lead agent turn → Explicit Memory tool [explicit remember / recall]
- Skills and workspace files → Lead agent turn [instructions and files]
- Skills and workspace files → Connected teammate [scoped workspace]
- Generation control → Durable task records [lifecycle admission]
- Generation control → Opt-in Context conversation [Reset uses new generation]

## Conditions and caveats

- Only agents connected to input-teammates can be assigned.
- Context is a plain conversation keyed by workflow, generation and agent node.
- Memory is an explicit tool; it does not automatically preserve conversation continuity.

## Code evidence

- `server/nodes/tool/task_manager/__init__.py` — TaskManagerNode and assign_task; operations, graph-scoped teammate checks.
- `server/services/handlers/tools.py` — _execute_delegated_agent; input-teammates scope and persist-before-taskTrigger ordering; lines 372, 493, 809.
- `server/services/temporal/agent_workflow.py` — DelegatedTaskWorkflow; line 553 onward.
- `server/services/agent_context/conversation.py` — load_conversation and save_conversation; plain JSON conversation store.
- `docs-internal/agent_context_flow.md` — normative Context versus Memory continuity contract.
- `docs-internal/agent_teams.md` — lead assignment, review and task lifecycle contract.

## Artifacts

- Editable source: [09-teams-context.mmd](09-teams-context.mmd).
- Full generation prompt: [09-teams-context.prompt.txt](09-teams-context.prompt.txt).
- Image: `09-teams-context-v1.png` (reviewed built-in ImageGen output).

## Visual review

- Reviewed delegation queue, connected-teammate authorization, persist-before-taskTrigger and lead-review loop against the brief.
- Context load/save/review continuity, explicit Memory, scoped skills/files and Reset/new-generation boundaries remain distinct and legible.
