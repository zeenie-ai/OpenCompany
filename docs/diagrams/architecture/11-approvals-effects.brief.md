# 11. Approvals and external effects

Status: current implementation.

Baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`. Source review is static; no provider or device sessions were run.

## Boundary and layout

Main path left to right: agent tool call→approval rules→allow/restrict or hold draft. The allowed branch has its own Allowed tool execution box which calls the external system and returns its result, bypassing the held-draft machinery. Hold leads through owner decision, Undo grace window, revision-fenced claim and one-attempt send to a three-outcome record. Add a dashed lower inset for implemented approval waiting, labeled rollout pending, which rejoins the same claim/send/outcome machinery. The ApprovalWorkflow wait and Implemented; rollout pending boxes and their edges must be dashed amber. Do not route every tool call through waiting.

## Exact components

- "Agent tool call" (service)
- "Approval rules and tool capability" (service)
- "Run or restrict" (service)
- "Allowed / restricted tool execution" (service)
- "Held approval draft" (state)
- "Owner decision" (client)
- "Undo grace window" (temporal)
- "Revision-fenced send claim" (state)
- "One-attempt tool activity" (temporal)
- "External side effect" (external)
- "sent / not_sent / unknown" (state)
- "Agent result or continuation" (service)
- "ApprovalWorkflow wait" (dormant)
- "Implemented; rollout pending" (dormant)

## Exact directed relationships

- Agent tool call → Approval rules and tool capability
- Approval rules and tool capability → Run or restrict [allowed / restricted]
- Run or restrict → Allowed / restricted tool execution [permitted call]
- Allowed / restricted tool execution → External side effect
- Allowed / restricted tool execution → Agent result or continuation [tool result]
- Approval rules and tool capability → Agent result or continuation [refused call]
- Approval rules and tool capability → Held approval draft [asked send]
- Held approval draft → Owner decision
- Owner decision → Undo grace window [Send]
- Undo grace window → Revision-fenced send claim [Undo window closes]
- Revision-fenced send claim → One-attempt tool activity [claim still valid]
- One-attempt tool activity → External side effect
- One-attempt tool activity → sent / not_sent / unknown [record outcome]
- sent / not_sent / unknown → Agent result or continuation
- Implemented; rollout pending → ApprovalWorkflow wait [versioned waiting path]
- Held approval draft → ApprovalWorkflow wait [when waiting is activated]
- Owner decision → ApprovalWorkflow wait [decision update]
- ApprovalWorkflow wait → Revision-fenced send claim [approved turn execution]

## Conditions and caveats

- Restricted tools still execute with enforced settings (`tool_calls._restricted` supplies restricted node data and arguments); only an explicitly refused call bypasses execution with a result.
- Ordinary new generations do not activate approval_wait_version at this baseline.
- Approval handling is capability/rule dependent; legacy in-process agents may refuse rather than hold.
- Approved sends are not automatically retried; interrupted attempts can have outcome unknown.

## Code evidence

- `server/services/approvals/tool_calls.py` — check, _check_by_class and check_in_process; capability/rule/version-dependent allow, restrict, hold or refusal.
- `server/services/approvals/decisions.py` — decide; owner decision and durable revision transition.
- `server/services/temporal/approved_tool_call_workflow.py` — ApprovedToolCallWorkflow, run_approved_call, SEND_ONCE; Undo sleep, claim, one-attempt execution and outcome.
- `server/services/temporal/approval_workflow.py` — ApprovalWorkflow; implemented durable waiting.
- `server/services/deployment/control.py` — begin_generation; resource_manifest has execution_control_version and pi_harness_version only, lines 154–168.
- `RFC-0007-APPROVALS-ON-TEMPORAL.md` — implementation status and AP.7 activation boundary.

## Artifacts

- Editable source: [11-approvals-effects.mmd](11-approvals-effects.mmd).
- Full generation prompt: [11-approvals-effects.prompt.txt](11-approvals-effects.prompt.txt).
- Image: `11-approvals-effects-v1.png` (reviewed built-in ImageGen output).

## Visual review

- Native review: allowed/restricted execution and explicit refusal return are separate from draft approval, owner Send, Undo grace, revision claim and one-attempt execution.
- Cross-checked _restricted: constrained settings still execute, so corrected first image's restricted-result bypass to an explicit refusal path.
- Implemented ApprovalWorkflow waiting remains dashed amber and marked rollout pending; new-generation activation caveat is explicit.
- Legend avoids equating service modules with OS processes; line bridges separate side-effect calls from result/refusal paths.
