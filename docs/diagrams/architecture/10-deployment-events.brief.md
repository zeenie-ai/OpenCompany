# 10. Deployment and events

Status: current implementation.

Baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`. Source review is static; no provider or device sessions were run.

## Boundary and layout

Three entry lanes across the top: verified push/webhook events, provider polling workflow, and Temporal Schedule/cron workflow, all leading to graph execution through their correct routes. In the lower half show durable generation/control and the lifecycle chain: Start→Running→Stopping/drain→Stopped→Resume→Running; Reset archives the generation and returns Ready, and a later Start creates a new generation. Keep event delivery and lifecycle controls visibly distinct.

## Exact components

- "Verified push / webhook" (external)
- "WorkflowEvent dispatch" (service)
- "Controller / trigger listener" (temporal)
- "Polling workflow and activity" (temporal)
- "Temporal Schedule and cron workflow" (temporal)
- "MachinaWorkflow" (temporal)
- "Generation and graph snapshot" (state)
- "WorkflowControlWorkflow" (temporal)
- "Start" (client)
- "Running" (service)
- "Stop gates new work" (client)
- "Drain admitted work" (temporal)
- "Stopped" (service)
- "Resume same continuation" (client)
- "Reset and archive" (client)
- "Ready for next Start" (service)

## Exact directed relationships

- Verified push / webhook → WorkflowEvent dispatch
- WorkflowEvent dispatch → Controller / trigger listener [Visibility and signals]
- Controller / trigger listener → MachinaWorkflow [admitted event]
- Polling workflow and activity → MachinaWorkflow [matching poll result]
- Temporal Schedule and cron workflow → MachinaWorkflow [scheduled run]
- Start → Generation and graph snapshot [allocate generation]
- Generation and graph snapshot → WorkflowControlWorkflow [snapshot and revision]
- WorkflowControlWorkflow → Controller / trigger listener [trigger lifecycle]
- WorkflowControlWorkflow → Polling workflow and activity [polling gates]
- WorkflowControlWorkflow → Temporal Schedule and cron workflow [pause / resume schedules]
- Start → Running [setup acknowledged]
- Running → Stop gates new work
- Stop gates new work → Drain admitted work
- Drain admitted work → Stopped [acknowledged drain]
- Stopped → Resume same continuation
- Resume same continuation → Running [same generation]
- Running → Reset and archive
- Stopped → Reset and archive
- Reset and archive → Ready for next Start [cancel resources and archive]
- Ready for next Start → Start [new generation]

## Conditions and caveats

- Current dispatch is fail-soft Visibility + signals, without a durable event inbox.
- Stop retains admitted work and buffered events; Resume releases the same continuation.
- Reset revision-guards cleanup and requires a later Start for the next generation.

## Code evidence

- `server/services/events/dispatch.py` — emit, _signal_running_consumers and _signal_one; current fail-soft event fanout.
- `server/services/temporal/trigger_listener_workflow.py` — TriggerListenerWorkflow; signal buffering, controller admission and Machina child.
- `server/services/deployment/manager.py` — DeploymentManager.deploy; trigger-specific setup.
- `server/services/temporal/schedules.py` — Temporal schedule creation and lifecycle controls.
- `server/services/temporal/workflow_control_workflow.py` — WorkflowControlWorkflow; control updates, admission gates and queue preservation.
- `server/services/deployment/control.py` — WorkflowControlService.begin_generation; revisioned graph/runtime snapshot.
- `docs-internal/temporal-workflow-control.md` — normative Start/Stop/drain/Resume/Reset contract.

## Artifacts

- Editable source: [10-deployment-events.mmd](10-deployment-events.mmd).
- Full generation prompt: [10-deployment-events.prompt.txt](10-deployment-events.prompt.txt).
- Image: `10-deployment-events-v1.png` (reviewed built-in ImageGen output).

## Visual review

- Reviewed push/webhook, poll and Temporal Schedule routes; generation snapshot/controller wiring and full Start/Stop/drain/Resume/Reset cycle remain distinct.
- Two targeted edits corrected polling-gate endpoint and separated its crossing from scheduled-run delivery.
- Current fail-soft event dispatch, buffered-work preservation and new-generation-after-Reset caveats are visible.
