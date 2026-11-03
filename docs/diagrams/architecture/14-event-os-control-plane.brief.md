# 14. Event OS control plane and resource lifecycle

Status: Planned — accepted-roadmap.

**Status:** accepted-roadmap. PROPOSED — ACCEPTED ROADMAP.

Baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`.

## Evidence

- RFC-0005 §§10, 13–18 and 20–21 define employee supervisors, scheduling classes, budget ledger/holds, durable alarms, approvals, supervision and fleet lifecycle.
- RFC-0006 §§11–16 and 22–24 amend class routes, admission/floors, per-step budgets, parking, process affinity, versioning, replay and rollback; §24.2 lists decisions that remain open.
- RFC-0007 implementation status and §§4.6–4.10 document implemented approval classes/rules/wait workflows plus activation AP.7 and future gate/outbox migration.
- Current begin_generation in services/deployment/control.py does not enable approval_wait_version, event_os_version, scheduler_version or placement_version. Current workflow_registry.py lacks EmployeeSupervisorWorkflow and ConversationWorkflow.

## Source files

- [RFC-0005-EVENT-OS-ON-TEMPORAL.md](../../../RFC-0005-EVENT-OS-ON-TEMPORAL.md)
- [RFC-0006-TEMPORAL-DESIGN-PATTERNS.md](../../../RFC-0006-TEMPORAL-DESIGN-PATTERNS.md)
- [RFC-0007-APPROVALS-ON-TEMPORAL.md](../../../RFC-0007-APPROVALS-ON-TEMPORAL.md)
- [server/services/deployment/control.py](../../../server/services/deployment/control.py)
- [server/services/temporal/workflow_registry.py](../../../server/services/temporal/workflow_registry.py)

## Exact components

- **owner**: Owner intents — interface.
- **intents**: Durable intent rows — state.
- **deliverer**: Background intent deliverer — proposed.
- **supervisor**: EmployeeSupervisorWorkflow / control + alarms + budget — proposed.
- **classes**: Scheduling classes / owner, customer, internal, background — proposed.
- **budget**: Daily budget ledger / holds + reset — state.
- **admission**: Admission / fairness + resource gates — proposed.
- **turn**: Turn execution — temporal.
- **park**: Parked turns / explicit reason + release — proposed.
- **alarm**: Durable alarms + schedules — proposed.
- **approval**: ApprovalWorkflow / implemented, rollout pending — dormant.
- **outbox**: Outbox + egress gates — proposed.
- **truth**: App DB / authoritative recovery state — state.
- **reconcile**: Reconcile entities + resources — proposed.
- **fleet**: Per-owner instance upgrades — proposed.
- **replay**: Checkpoint + replay + rollback gate — proposed.
- **workers**: Version-compatible workers — temporal.

## Directed relationships

- **owner → intents**: commit intent.
- **intents → deliverer**: pending.
- **deliverer → supervisor**: stable-ID update.
- **supervisor → admission**: control state.
- **classes → admission**: reserved shares.
- **budget → admission**: holds.
- **admission → turn**: permit.
- **turn → park**: resource or provider wait.
- **park → admission**: release.
- **supervisor → alarm**: own deadlines.
- **alarm → admission**: wake.
- **turn → approval**: conditional wait.
- **approval → outbox**: decision consumed.
- **truth → reconcile**: recovery truth.
- **reconcile → supervisor**: re-arm.
- **reconcile → alarm**: restore.
- **fleet → replay**: upgrade check.
- **replay → workers**: poll after gate.
- **workers → turn**: execute.

## Boundaries and caveats

- Employee supervisors, class admission, daily budgets, durable alarms, parking and hosted worker separation are proposed target components, not current deployment requirements.
- ApprovalWorkflow is implemented but rollout is pending AP.7 for ordinary new generations. Future gate/reviewed-delivery migration is a separate phase.
- App DB truth drives reconciliation of entities, schedules, alarms and budgets in the proposed recovery model. The complete Temporal-rebuildability guarantee is future.
- Fleet tiers and weekly upgrades remain per-owner instance architecture. Do not imply one shared multi-tenant Temporal cluster or invent throughput/capacity guarantees.
- RFC-0004 open choices stay open. No image selects a new retry default, history payload codec or old-generation upgrade policy beyond recorded RFC amendments.

## Visual review

Passed at native 1672 × 941 and documentation scale. Verified all 17 components and 19 directed relationships, budget/admission holds, parked-turn release, conditional approval, workers-to-turn execution, recovery re-arming, upgrade gating, proposed versus dormant status, and readable native/documentation scale. Three ImageGen edits corrected wrong arrow endpoints, one detached permit connector, proposal badge color, and a clear gap between the execute connector and Outbox. Built-in ImageGen generated the PNG using the accepted local-runtime style reference; the selected PNG is copied into this canonical directory and original generated artifacts are retained.
