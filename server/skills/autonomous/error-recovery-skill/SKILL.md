---
name: error-recovery-skill
description: Diagnose tool failures, distinguish safe retries from uncertain side effects, and report partial results accurately.
metadata:
  author: opencompany
  version: "2.0"
  category: autonomous
  icon: "🔧"
  color: "#F59E0B"
---

# Error Recovery

Inspect the actual tool result and runtime status before retrying. Some
workflow Activities retry under their configured Temporal policy; a tool
failure may instead become an error message the agent can correct. This skill
does not replace either policy with a fixed retry count or backoff schedule.

## Decide what happened

- Invalid input: compare arguments with the exposed schema and correct the actual field or value.
- Missing/expired authorization: request the supported connection or owner action; do not invent credentials.
- Transient read failure: retry only when the runtime/tool permits it, respecting retry-after guidance and avoiding duplicate retries already in progress.
- Rejected mutation: correct the cause before making another authorized attempt.
- Uncertain send/write: verify the remote outcome or use the owner's reconciliation flow. A timeout is not proof that nothing happened; never blindly repeat it.
- Cancelled/reset work: stop acting under its old task identity. Cooperative Stop may still be draining admitted work.

## Alternatives and partial work

Choose another connected tool only if it preserves the requested meaning and
access rules. Explain stale data or unavailable sections, and distinguish a
partial deliverable from completed work. Preserve useful evidence and error
identities without exposing credentials or private payloads.

For managed teams, use Task Manager's revision-aware review/retry/reassignment
operations instead of spawning duplicate direct delegations. Read the current
task before changing it. Direct delegation, where exposed by a legacy graph,
is depth-bounded and must follow its returned task identity.

Do not implement network retry loops inside Python Executor: it provides no
network client, disallows imports, and its synchronous code blocks the worker.
Use connected network tools and their runtime policies. Process Manager can
run a fuller program when explicitly needed and permitted, but that program's
side effects and cleanup remain the caller's responsibility.
