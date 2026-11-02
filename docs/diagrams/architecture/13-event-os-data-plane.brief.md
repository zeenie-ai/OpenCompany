# 13. Event OS data plane and source migration

Status: Planned — accepted-roadmap.

**Status:** accepted-roadmap. PROPOSED — ACCEPTED ROADMAP.

Baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`.

## Evidence

- RFC-0005 §§6, 8–10, 17 and 24 define ingress commit before acknowledgement, DB routing, sequenced streams, bounded known-ID relay, conversation entities, atomic consuming save and the send outbox.
- RFC-0006 §§7, 11–16 and 22–24 amend admission, no owner-chat debounce, routing, parking, version flags and safety defaults; RFC-0007 amendments to RFC-0005 §17.5 distinguish approval activation from future gate migration.
- Current services/events/dispatch.py emits through Temporal Visibility and signals; it does not implement the proposed durable inbox/router. Current begin_generation in services/deployment/control.py records execution-control and Pi flags, not Event OS/scheduler/placement flags.
- plugin_system.md phase rollout describes remaining PollingEventSource and DaemonEventSource migrations. GmailReceiveNode and EmailReceiveNode still subclass PollingTriggerNode; StripeListenSource already subclasses DaemonEventSource.

## Source files

- [RFC-0005-EVENT-OS-ON-TEMPORAL.md](../../../RFC-0005-EVENT-OS-ON-TEMPORAL.md)
- [RFC-0006-TEMPORAL-DESIGN-PATTERNS.md](../../../RFC-0006-TEMPORAL-DESIGN-PATTERNS.md)
- [RFC-0007-APPROVALS-ON-TEMPORAL.md](../../../RFC-0007-APPROVALS-ON-TEMPORAL.md)
- [docs-internal/plugin_system.md](../../../docs-internal/plugin_system.md)
- [server/services/events/dispatch.py](../../../server/services/events/dispatch.py)
- [server/services/deployment/control.py](../../../server/services/deployment/control.py)
- [server/nodes/google/gmail_receive/__init__.py](../../../server/nodes/google/gmail_receive/__init__.py)
- [server/nodes/email/email_receive/__init__.py](../../../server/nodes/email/email_receive/__init__.py)
- [server/nodes/stripe/_source.py](../../../server/nodes/stripe/_source.py)

## Exact components

- **drivers**: Drivers / chat / webhooks — external.
- **verify**: Verify, cap, normalize, dedupe — proposed.
- **ingress**: Durable ingress / app DB commit — state.
- **ack**: Acknowledge producer — interface.
- **router**: DB router / subscriptions + sequence — proposed.
- **stream**: Conversation stream / deliveries + cursors — state.
- **relay**: Bounded relay / known workflow ID — proposed.
- **entity**: ConversationWorkflow / one turn at a time — proposed.
- **turn**: Turn child / Machina + agent execution — temporal.
- **history**: History + consumed cursor / atomic save — state.
- **outbox**: Durable outbox — state.
- **gate**: Approval / token / claim gate — proposed.
- **send**: Sender activity / sent, failed, unknown — proposed.
- **provider**: External provider — external.

## Directed relationships

- **drivers → verify**: input.
- **verify → ingress**: append.
- **ingress → ack**: after commit.
- **ingress → router**: accepted rows.
- **router → stream**: route + sequence.
- **stream → relay**: pending batch.
- **relay → entity**: Signal-With-Start.
- **entity → turn**: admit one turn.
- **turn → history**: consume + persist.
- **history → stream**: advance consumed cursor.
- **turn → outbox**: enqueue effect.
- **outbox → gate**: eligible row.
- **gate → send**: permitted claim.
- **send → provider**: idempotent or at-most-once.
- **send → outbox**: record result.

## Boundaries and caveats

- The accepted roadmap is phased and unshipped as a complete data plane. Existing WorkflowEvent, graph and agent execution are reusable components, not proof of the proposed ingress guarantees.
- Signals are hints; app-database rows and cursors are authoritative in this target. A producer is acknowledged only after the durable ingress transaction commits.
- History save and consumed-cursor advancement are one proposed transaction. Unknown external outcomes require explicit resolution and are not silently resent.
- The migration strip describes current delivery → shadow comparison → durable sources → legacy retirement. The migration does not itself make unavailable twitterReceive functional.
- Cron remains Temporal Schedules, outside the EventSource class migration. The obsolete APScheduler source docstring is not a target architecture.

## Visual review

Passed at native 1672 × 941 and documentation scale. Verified all 14 components and 15 directed relationships, durable-commit acknowledgement, atomic consuming save, one-way result recording, proposed datastore outlines, migration strip, and explicit unknown-effect caveat. One ImageGen edit corrected proposed datastore status and the result-recording arrowhead. Built-in ImageGen generated the PNG using the accepted local-runtime style reference; the selected PNG is copied into this canonical directory and original generated artifacts are retained.
