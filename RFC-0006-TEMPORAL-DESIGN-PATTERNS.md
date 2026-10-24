# RFC-0006 — Temporal Design Patterns for OpenCompany

- **Status:** Draft, 2026-10-24. Owner decisions T1-T20 are in §3.1. The amendments to RFC-0005 listed in §20 and the corrections to RFC-0004 listed in §21 were made in the same commit.
- **Created:** 2026-10-24
- **Scope:**
  - every Temporal workflow and activity in `server/services/temporal/` and `server/nodes/scheduler/cron_scheduler/`;
  - the code that starts, signals, updates and cancels them: `server/services/deployment/`, `server/services/events/`, `server/services/chat/`, `server/services/approvals/`, `server/services/employees/`;
  - the plugin framework's activity behaviour: `server/services/plugin/`, and the nodes whose behaviour depends on Temporal semantics (Timer, approvalGate, every sending node);
  - worker topology and configuration (`worker.py`, the standalone worker), and the replay and identity tests in `server/tests/temporal/`.
- **Relationship:**
  - Complements RFC-0004 (using the SDK and platform fully). It covers what RFC-0004 skipped: a pattern-by-pattern review of Temporal's design-pattern catalogue, AI cookbook, best practices, Python samples and Nexus pages. §21 corrects the RFC-0004 statements that verification found wrong or that a decision superseded.
  - Amends RFC-0005 (the event OS) wherever a pattern or an owner decision changes a phase's design (§20). RFC-0005 stays the plan of record; this RFC adds detail and fixes gaps.
  - Adds Phase 1b to RFC-0005's rollout: fixes that need no workflow versioning and should land before Phase 2 (§22).
- **Baseline:** HEAD 4f73cb10 (2026-10-24), temporalio 1.30.0, Temporal CLI pinned to v1.9.1 (server 1.32.0) for new installs. Line numbers are as of that commit and drift.
- **Sources:** docs.temporal.io as of 2026-10-24 and temporalio/samples-python at 33f7dc29 (2026-10-22). See §2.

## How to read this

- **Owner:** §1 (summary), §3 (decisions), §24 (defaults chosen and what is still open).
- **Reviewer:** §4 (the catalogue at a glance), §5 (rules every workflow follows), and §20 (what changes in RFC-0005).
- **Implementer:** §6 to §19, one section per pattern category or source. Each pattern has the same parts: what the docs prescribe, SDK support, what OpenCompany does today with file and line, a verdict, recommendations with their versioning and phase, remarks on pitfalls, and a Python sketch where something is missing or misused. Then §22 (rollout) and §23 (acceptance gates).
- **Quotations of RFC-0005:** where an entry in §6 to §19 describes "the RFC-0005 plan", it quotes RFC-0005 as reviewed, before the amendments in §20.

Verdicts use six words:
- **Adopted correctly:** the code follows the pattern.
- **Adopted with deviations:** the code follows it with gaps, listed.
- **Misused:** the code uses the pattern's mechanism in a way the docs warn against, or that breaks a rule OpenCompany has set.
- **Missing and beneficial:** OpenCompany should adopt it.
- **Planned in RFC-0005:** RFC-0005 already designs it, and this RFC says whether that design follows the docs.
- **Not applicable:** it solves a problem OpenCompany does not have, with the reason.

Priorities:
- **P1:** fix before Phase 2: in Phase 1b, or in Phase 1 for T17's fairness and partition. These fixes need no workflow versioning.
- **P2:** fix with the RFC-0005 phase named.
- **P3:** later or never.

## 1. Summary

Temporal publishes a catalogue of design patterns for durable workflows: orchestration, messaging, entities, external waits, transactions, retries, batches, quality of service, latency and worker configuration. It also publishes an AI cookbook, best practices, Python samples and the Nexus pages. This RFC checks OpenCompany against every one of them, page by page, and says what to change.

**What we found.** OpenCompany's Temporal code follows the documented patterns in shape. Of the 45 design-pattern entries:
- 26 are adopted with deviations;
- 4 are misused;
- 5 are missing and would help;
- 3 are already planned in RFC-0005;
- 7 do not apply.

None is adopted with no deviation at all. The deviations sit exactly where the pages put their warnings: what happens on a cancel, a retry, a restart or a long wait. Verified examples:
- **Side effects can happen twice.** Every message a graph node sends retries up to three times with no guard, including the reply after the approval gate in every employee that Hire builds. Tool calls such as an HTTP POST, a GitHub pull request or a Vercel deploy run again after a broken-off attempt. Both contradict E12. (§11.2)
- **Agent children are terminated, not cancelled.** Four agent-child starts take the default parent close policy, so a parent that closes early kills its agents without their cleanup. Browser claims, permits and job rows are left behind. (§6.2)
- **A bug in workflow code makes its own fix fail replay.** Eight workflow types run cleanup activities on any exception, bugs included, and three swallow bugs per event. (§18.2)
- **Waits do not survive restarts.** The Timer node sleeps inside an activity, and a graceful shutdown fails it. Reviewed team-job delivery strands its draft when a retry lands within its lease. The approval gate polls inside a 24-hour activity. (§9.5, §9.6)
- **Some calls have no deadline:** every client-side Update call, a streamed LLM call (the 600 s client timeout is per read), and every Gemini call. (§7.3, §16.3)
- **Some failures are fatal or silent.**
  - The controller fails within seconds to about 20 seconds of database trouble while it holds a backlog.
  - Compensations give up after 3 or 10 attempts.
  - The Start path carries saved credentials into Temporal input.
  - No Temporal metric is exported, so no alert can fire. (§12.3, §10.2, §17.8, §17.10)

**What changes.** The owner settled twenty questions on 2026-10-24 (§3.1). In short:
- **Fresh start confirmed.** Old Temporal databases are deleted at the pinned release (T5).
- **Polls become retried activities** (T7).
- **Owner chat starts at once,** with no debounce (T9).
- **Owner actions write an intent** and are delivered in the background (T10).
- **Stuck turns park** instead of failing (T11).
- **Stop does not wait for drafts** (T12).
- **Side effects are redone only when idempotent** (T13).
- **Agent transcripts stay in history** (T6).
- **Hosted instances run workers in a second process** (T8).
- **Fairness ships with the fresh start** (T17).
- **Lost supervisors are rebuilt in place** (T20).

**How it ships.**
- **Phase 1b** (§22.2) is a new step before RFC-0005's Phase 2: 46 fixes that need no workflow versioning (each leaves the replayed command sequence unchanged), one commit each. Side effects come first, then waits, lifecycle, compensation, retries, platform, and lints and tests.
- **Design-level changes** land in the RFC-0005 phases they belong to (§22.3). RFC-0005 was amended in the same commit (§20), and 34 RFC-0004 statements that verification found wrong or that a decision superseded were corrected (§21).
- **What does not apply:** Nexus, eager workflow start, delayed start, asynchronous completion and MapReduce trees, each for a stated reason (§19, §14.4, §9.4, §18.15, §12.5).

## 2. Method and sources

- **Docs** (all fetched as raw markdown on 2026-10-24 by appending `.md` to each page URL):
  - the design-pattern catalogue (https://docs.temporal.io/design-patterns): the index, 10 category overviews and 35 patterns, 46 pages. The same pages existed on 2026-10-16; only the Saga page changed since.
  - the AI section and its cookbook, 19 pages;
  - the best-practices section (13 pages, four of them new since 2026-10-16) and the Python SDK best practices (6 pages);
  - the Nexus section and the standalone activity and operation pages, 15 pages.
- **Code samples:** temporalio/samples-python at commit 33f7dc29 (2026-10-22), 57 sample folders. It targets temporalio 1.34.0 or later; every call it makes was checked against the installed 1.30.0 source.
- **Research:** twelve parallel passes, one per pattern category or source. Each read its pages in full and audited the code at HEAD.
- **Verification:** four independent passes re-checked every P1 claim, every Misused verdict, every defect statement and every figure this RFC quotes. They tried to refute each one against the code, the 1.30.0 and 1.34.0 SDK sources, the CLI and dev-server source, and the recorded 0.3.1 replay histories. Of 493 claims checked:
  - 388 were confirmed;
  - 88 were corrected;
  - 4 were refuted;
  - 13 could not be verified without a live server.

  This RFC states only confirmed or corrected findings.
- **Owner questions:** four rounds on 2026-10-24 (§3.1).
- **Drafting:** six passes wrote §6 to §19 from the verified notes. Six more passes applied the owner's last decisions, shared numbering and cross-references.
- **Not done:** nothing was run against a live server. Timings and event counts come from the recorded replay histories, which were made with stub activities, so they are upper bounds on orchestration cost, not measurements of a live instance.

## 3. Decisions

### 3.1 Owner decisions (2026-10-24, four rounds)

| # | Decision | Changes |
|---|---|---|
| T1 | **Sources.** The review covers the design-pattern catalogue plus the AI cookbook, the best practices, the Python samples and Nexus. | §2 |
| T2 | **Role.** This RFC amends RFC-0005 in the same commit wherever a pattern changes a phase's design. | §20 |
| T3 | **Depth.** A full entry per pattern, with Python sketches. | §6-§19 |
| T4 | **Priority.** When two patterns pull in different directions, reliability wins: no lost events and no stuck or duplicated runs, even at some cost in latency or money. | throughout |
| T5 | **Fresh start confirmed.** RFC-0005 E16 stands: the first pinned release deletes old Temporal databases, and every install runs server 1.32. The code that keeps an install's old database and server (commit 49177427) is reverted in Phase 1. Server 1.31 and 1.32 features need no per-install gating once that release ships. | RFC-0005 §3.1 E16, §24.4 |
| T6 | **Agent transcripts stay in history.** They keep travelling in LLM-step inputs, bounded by the byte budget, compaction and continue-as-new. RFC-0005's rule that nothing over 64 KB enters Temporal (I7) gets an exception for agent loops. | RFC-0005 §6.2 I7 |
| T7 | **Polls are retried activities.** Each poll trigger gets one small workflow that runs a single activity. The activity fetches, stores the new items and the cursor in the database in one transaction, then ends with a BENIGN error so Temporal retries it after the interval. History stays near zero. This replaces one Schedule per poll. | RFC-0005 §3.1 E5, §15 |
| T8 | **Hosted tier S splits workers out.** Hosted instances run Temporal workers in a second process; the desktop stays one process. First, activities bound to the backend process (the WhatsApp bridge, the phone emulator, the Claude session pool, the socket broadcaster, the encryption key) are routed to a queue only that process polls. | RFC-0005 §20 |
| T9 | **No debounce for owner chat.** The turn starts at once; messages sent while it runs merge at the agent's next step (merge point B1). The docs say timers are accurate only from one second up. | RFC-0005 §11 |
| T10 | **Owner actions write an intent first.** Stop, Resume, approval decisions and Interrupt write a database row and answer at once (§7.3 applies the same to Start and Apply). A background deliverer sends the Update with a stable id until it is applied, and the UI shows "applying". | RFC-0005 §10.2, §13.4, §17.5 |
| T11 | **Stuck turns park.** A turn that cannot continue for a reason that may clear (a provider outage past the step's time budget, an exhausted key or allowance, an account problem) suspends where it stopped. It holds no worker slot, raises an attention item, and continues on Resume, top-up, or when the outage ends. | RFC-0005 §10.3, §14 |
| T12 | **Stop does not wait for drafts.** Stop completes while an approval draft waits. A decision taken while stopped is sent on Resume. | RFC-0005 §10.2, §17.5 |
| T13 | **Side effects are redone only when idempotent.** A side-effecting tool call that broke off is run again only if its plugin declares itself idempotent or uses a provider idempotency key. Otherwise it is recorded as `unknown` and the employee is told. This extends E12 from sends to every side-effecting tool. | RFC-0005 §3.1 E12, §17.3 |
| T14 | **A release that cannot replay an open run starts anyway.** The affected generations are marked "needs Reset". This applies to replay failures only; a release that fails its readiness check still rolls back (RFC-0005 §21.2). | RFC-0005 §21.2, §24.1 |
| T15 | **Outgoing content rules.** Every outgoing message passes built-in rules (a saved credential, a secret-shaped token, a recipient outside the conversation) and the owner's own rules, even with Ask first off. A match holds the message for the owner. | RFC-0005 §17 |
| T16 | **Phase 1 is finished after this RFC,** by the same session. | RFC-0005 §24.4 |
| T17 | **The fresh-start release adds fairness and one partition.** The dev server gets `matching.enableFairness=true` and one task-queue partition (read and write) in the release that creates every Temporal database empty, because partitions can be lowered safely only on a new database. Retention and search attributes at namespace creation stay deferred, as decided on 2026-10-23 (RFC-0004 D4). The Start-snapshot secret fix ships in Phase 1b. | RFC-0005 §13.2, §20, §24.2 |
| T18 | **The step budget and parking ship with Phase 5, for new generations only** (the generation flag `scheduler_version=1`). Existing generations keep today's unlimited LLM-step retries and fail-and-pause. A budget without parking would fail turns and lose their input. | RFC-0005 §14, §24.2 |
| T19 | **A parked turn waits at most 7 days.** Then its input is dead-lettered with an attention item. RFC-0005's per-class turn deadlines become "slow turn" alerts that do not count parked time. (A 24-hour backstop for turns that are stuck without being parked is a default, §24.1.) | RFC-0005 §13.1, §18.1, §23 |
| T20 | **A lost supervisor is rebuilt in place.** A terminated or failed supervisor is started again from the database (`ALLOW_DUPLICATE_FAILED_ONLY`), keeping chat and Context. After 3 rebuilds within an hour it stops and asks the owner to Reset. | RFC-0005 §10.1 |

Decisions already in force: RFC-0005 §3.1 (E1-E16) and RFC-0004 §17, including Q6: a total time budget per LLM step. §11 of this RFC proposes the values, and T18 places them in Phase 5.

## 4. The catalogue at a glance

One row per entry in §6 to §19. Priority P1 means Phase 1b (the ids are listed in §22), P2 means the RFC-0005 phase named, and P3 means later or never.

### 4.1 Design patterns

| § | Pattern | Verdict | P | Action |
|---|---|---|---|---|
| 6.1 | Task orchestration (overview) | Adopted with deviations | P1 | Explicit child options, named bounds, named loser cleanup; lint (1b.40) |
| 6.2 | Child Workflows | Adopted with deviations | P1 | Agent children request cancellation (1b.12); cancel-and-drain and durable Claude Code and RLM delegations (cancellation lane) |
| 6.3 | Parallel Execution | Adopted with deviations | P2 | One failure policy (running nodes finish, nothing new starts); graph by reference; delegation-window fix |
| 6.4 | Pick First (race) | Adopted with deviations; RFC-0005 turn-deadline race planned (deviates) | P1 | A chat run past 24 h cancels its workflow (1b.14); for Phase 4, slow-turn alerts (T19) and a 24-hour no-progress backstop (a default, §24.1) |
| 7.1 | Workflow messaging (overview) | Adopted with deviations | P1 | The handler checklist (§5 rules 12-15) |
| 7.2 | Signal with Start | Missing and beneficial (as start-with-state); RFC-0005 relay planned (deviates) | P2 | Triggers in the controller's start input; the stream cursor as the entity's start input |
| 7.3 | Request-Response via Updates | Adopted with deviations; RFC-0005 owner Updates planned (follows) | P1 | A deadline on every Update call (1b.15); stable ids, slim results, the T10 deliverer |
| 7.4 | Event Accumulator | Adopted with deviations; RFC-0005 debounce planned (follows) | P2 | Dead-letter poison events; no owner-chat debounce (T9); absolute debounce deadline; carry cursors |
| 8.1 | Entity and lifecycle (overview) | Not applicable (selection guide) | P3 | The database-as-truth rules (§5.1) |
| 8.2 | Entity Workflow | Adopted with deviations; RFC-0005 plan deviates | P2 | Decommission the Workspace controller; turn numbers from admission; a seeded `applied_seq`; supervisor rebuilt in place (T20) |
| 8.3 | Continue-As-New | Adopted with deviations | P2 | Slim controller carry, cancellation fix, rollover at the first boot of each release; slim agent carry (1b.19) |
| 8.4 | Updatable Timer | Planned in RFC-0005 (follows, misses details) | P2 | One timer over absolute deadlines; no zero timeouts; no sub-second windows; one instance-wide upkeep sweep |
| 9.1 | External interaction (overview) | Not applicable (selection guide) | P3 | "Where each wait goes" (§5 rule 2) |
| 9.2 | Polling External Services | Adopted with deviations | P1 | Heartbeats and bounded attempts (1b.17); one retried poll activity per trigger in Phase 3 (T7) |
| 9.3 | Long-Running Activity | Adopted with deviations | P1 | Activities re-raise cancellation (1b.2); long waits fit one attempt (1b.4); checkpoint external job ids |
| 9.4 | Delayed Start | Not applicable | P3 | No adoption; never `start_delay` on ids addressed by Signal-With-Start |
| 9.5 | Delayed Callback (webhooks) | Misused (Timer node; webhooks until Phase 2) | P1 | Timer node fixes (1b.8); a durable Timer behind a flag; the Phase 2 inbox for webhooks |
| 9.6 | Approval | Misused (the wait) | P1 | Reviewed delivery re-attaches (1b.9); approved sends wait while stopped (1b.5); stranded drafts expire (1b.10); ApprovalWorkflow for all four waits (Phase 7) |
| 10.1 | Distributed transactions (overview) | Adopted with deviations | P1 | The transaction rule in DESIGN.md (1b.45) |
| 10.2 | Saga | Adopted with deviations | P1 | Compensations retry until they land (1b.20); Reset closes everything it started (1b.21); Start's compensation in reverse (1b.22); interrupted Resets finish (1b.23) |
| 10.3 | Early Return | Adopted with deviations | P1 | A bounded Workspace Update-with-Start (1b.15); later behind the T10 deliverer |
| 11.1 | Error handling (overview) | Adopted with deviations | P1 | Retry-policy lint and shared constants (1b.41) |
| 11.2 | Fixed Count of Retries | Adopted with deviations | P1 | Effect classes and the redo guard (1b.1); the legacy tool path honours declared policies (1b.3) |
| 11.3 | Fixed Wall-Time Retries | Missing and beneficial | P2 | A per-step budget of 15 min (cloud) and 45 min (local); expiry parks the turn (Phase 5, new generations, T18) |
| 11.4 | Non-Retryable Errors | Misused | P1 | LLM-step bugs and `PayloadSizeError` non-retryable (1b.26); compaction on the step's policy (1b.27) |
| 11.5 | Delayed Retry | Adopted with deviations | P2 | A provider wait longer than the remaining budget parks the turn (Phase 5) |
| 11.6 | Fast/Slow Retries | Adopted with deviations | P2 | Parking is the slow phase (Phase 5); controller spawn backoff and dead letter behind a patch |
| 11.7 | Retry Alerting via Metrics | Missing and beneficial | P1 | The attention item and a quieter log now (1b.30); the metric counter in Phase 8 |
| 11.8 | Resumable Activity | Adopted with deviations | P2 | One park mechanism (Phase 5; T11, T18, T19) |
| 12.1 | Batch processing (overview) | Adopted with deviations | P1 | A real-server test of the 2,000-Update limit (1b.43) |
| 12.2 | Fan-Out with Child Workflows | Adopted with deviations | P2 | Admission caps for new generations; a cap on open team tasks |
| 12.3 | Batch Iterator | Adopted with deviations | P1 | Controller paging retries without limit (1b.16); bounded poll pages (1b.18) |
| 12.4 | Sliding Window | Missing and beneficial | P2 | Permit waits inside the workflow with a wake Signal; Reset releases permits |
| 12.5 | MapReduce Tree | Not applicable | P3 | Do not adopt |
| 13.1 | QoS and throughput (overview) | Overview | P2 | One enforcement point per resource; "reserved share" means admitted turns |
| 13.2 | Downstream Rate Limiting | Misused | P2 | Frozen class-queue routes for LLM steps; per-credential buckets; never cap `machina-tasks` |
| 13.3 | Priority | Planned in RFC-0005 (follows, misses details) | P2 | An explicit key on every top-level start; entities keyed by lane |
| 13.4 | Fairness | Planned in RFC-0005 (follows, misses details) | P1 | Fairness and one partition in the fresh-start release (T17); the employee as fairness key |
| 14.1 | Performance and latency (overview) | Overview | P2 | A timing mode with production settings; a pinned metrics port |
| 14.2 | Local Activities | Missing and beneficial | P2 | AgentWorkflow progress broadcasts as best-effort local activities (`progress_local_version=1`, after SDK 1.34.0); new types schedule no UI-only activity |
| 14.3 | Early Return + Local Activities | Adopted with deviations (Workspace tasks); Not applicable (chat) | P3 | Only if measured |
| 14.4 | Eager Workflow Start | Not applicable | P3 | Do not adopt |
| 15.1 | Worker configuration (overview) | Adopted with deviations | P1 | A placement axis and one worker builder (1b.36) |
| 15.2 | Worker-Specific Task Queues | Adopted with deviations; process affinity missing | P1 | The pool always polls (1b.34); the standalone guard (1b.35); an instance queue before the T8 split |
| 15.3 | Activity Dependency Injection | Adopted with deviations | P1 | One worker builder (1b.36) |

### 4.2 AI agents, best practices, samples and Nexus

| § | Entry | Verdict | P | Action |
|---|---|---|---|---|
| 16.1 | Durable AI and the cookbook | Adopted with deviations | P2 | Priority and fairness keys (Phase 5); summaries without user text (Phase 8) |
| 16.2 | Agentic loop with tool calls | Adopted with deviations | P1 | Non-retryable failures (1b.26); compaction's policy (1b.27) |
| 16.3 | LLM call timeouts | Adopted with deviations | P1 | A deadline inside each attempt and a Gemini timeout (1b.25); the step budget in Phase 5 (T18) |
| 16.4 | Hello world (OpenAI Responses) | Adopted with deviations | P3 | Settle node-level SDK retries with RFC-0004 Q9 |
| 16.5 | Retry policy from HTTP responses | Adopted with deviations | P2 | Honour `x-should-retry`, retry 409, cap Retry-After (Phase 5) |
| 16.6 | Deep research | Adopted with deviations | P3 | Summaries (Phase 8) |
| 16.7 | Structured outputs | Adopted with deviations | P3 | Stop offering a tool after 3 refusals in a turn |
| 16.8 | Claim check | Adopted with deviations | P1 | Slim the carry (1b.19); relief before every step (Phase 4); the I7 exception and its bounds (T6) |
| 16.9 | Guardrails | Adopted with deviations | P1 | Built-in content rules (1b.7); owner and recipient rules (Phase 7) (T15) |
| 16.10 | Human in the loop | Misused | P1 | See §9.6 |
| 16.11 | Agent memory and state | Adopted with deviations | P1 | The redo guard (1b.1) |
| 16.12 | Durable MCP server | Not applicable | P3 | A durable route only if Claude Code employees should send |
| 16.13 | Multi-agent with ADK | Adopted with deviations | P3 | Tool errors built with `json.dumps` (Phase 4) |
| 16.14 | Framework cookbook pages | Not applicable | P3 | Keep the native loop |
| 16.15 | Framework plugin samples | Not applicable | P3 | Ideas only |
| 16.16 | OpenRouter samples | Planned in RFC-0005 (deviates) | P2 | Provider exhaustion and long outages park the turn (Phase 5) |
| 16.17 | Bedrock and AgentCore samples | Planned in RFC-0005 (follows) | P2 | No change |
| 16.18 | Google GenAI sample | Not applicable | P3 | None |
| 16.19 | Tracing and metrics samples | Not applicable | P2 | gen_ai metric names, no content (Phase 8) |
| 16.20 | Workflow Streams sample | Adopted with deviations | P3 | Keep the chat hub |
| 17.1 | Best practices (index) | Not applicable | P3 | None |
| 17.2 | Namespace best practices | Adopted with deviations | P2 | Retention and search attributes at creation, deferred per RFC-0004 D4 (T17) |
| 17.3 | Multi-tenant patterns | Planned in RFC-0005 (follows) | P2 | See §13.4; no ids in metric labels |
| 17.4 | Managing APS limits | Missing and beneficial | P1 | A history-cost counting script (1b.44); jittered budget timers; no UI-only activities |
| 17.5 | Workflow cost optimization | Adopted with deviations | P2 | No UI-only activities in new types; graphs by reference |
| 17.6 | Cost governance | Not applicable | P3 | Release tags in fleet telemetry |
| 17.7 | Cloud access control | Not applicable | P3 | Telemetry key hygiene |
| 17.8 | Security controls | Misused (Start path) | P1 | Sanitized Start snapshot (1b.31, 1b.32); named ports and netpolicy (1b.33) |
| 17.9 | Worker deployment and performance | Adopted with deviations | P1 | Graceful shutdown (1b.37); the T8 worker split |
| 17.10 | Alerting on worker metrics | Missing and beneficial | P1 | SDK metrics and five starter alerts (1b.38) |
| 17.11 | Error handling | Adopted with deviations | P1 | Explicit chain-unique activity ids; cancellation (1b.2); the send guard (1b.1) |
| 17.12 | Pre-production testing | Missing and beneficial | P2 | Replay open runs before polling and mark "needs Reset" (T14, Phase 9a) |
| 17.13 | Knowledge hub | Adopted with deviations | P1 | Stale comments (1b.45) |
| 17.14 | Python best practices (index) | Not applicable | P3 | None |
| 17.15 | Python debugging | Adopted with deviations | P2 | Core logs at WARN; opt-in span exporter (Phase 8) |
| 17.16 | Python error handling | Adopted with deviations | P1 | The bug guard (1b.13); bugs stay stuck and visible |
| 17.17 | Python testing suite | Adopted correctly | P1 | Node-cancel and per-type bug tests |
| 17.18 | Python SDK sandbox | Misused | P1 | The determinism lint (1b.40); new types sandboxed (Phase 4) |
| 17.19 | Python sync vs async | Adopted with deviations | P1 | Loop-lag watchdog (1b.39); the T8 second process (Phase 9) |
| 18.1 | Pattern-to-sample map | - | - | 12 of 35 patterns have no Python sample |
| 18.2 | Exit cleanup and handler waits | Misused | P1 | The bug guard (1b.13) |
| 18.3 | Message handlers and Update-with-Start | Adopted correctly | P3 | None |
| 18.4 | Workflow basics (`hello`) | Adopted correctly | P3 | None |
| 18.5 | Observability samples | Missing and beneficial | P1 | Task failures at ERROR (1b.29); BENIGN retries (1b.28) |
| 18.6 | Polling | Adopted with deviations | P2 | T7's poll workflow (Phase 3) |
| 18.7 | Timers | Planned in RFC-0005 (follows) | P2 | Versioned `alarms_changed` (Phase 6) |
| 18.8 | Schedules | Adopted correctly | P3 | Cron jitter (planned) |
| 18.9 | Patching | Adopted with deviations | P2 | Four-stage retirement; dormant patches after SDK 1.34.0 |
| 18.10 | Replay | Adopted with deviations | P2 | Replay open runs at upgrade (Phase 9a) |
| 18.11 | Worker-specific task queues | Adopted with deviations | P2 | The instance queue (T8) |
| 18.12 | Worker processes | Planned in RFC-0005 (follows) | P2 | Second process on hosted tier S (T8) |
| 18.13 | Worker versioning | Not applicable | P3 | Declined for topology |
| 18.14 | Long-running activities | Adopted with deviations | P1 | Cancellation (1b.2); heartbeat interval from the timeout |
| 18.15 | Asynchronous activity completion | Not applicable | P2 | Human waits move to an Update plus a timer (Phase 7) |
| 18.16 | Resource pool | Adopted with deviations | P3 | Reset releases permits (1b.21) |
| 18.17 | Interpreters and batches | Adopted correctly | P3 | None |
| 18.18 | Codecs, external storage, converters | Not applicable | P3 | References first; a codec only with an owner decision (RFC-0004 Q13) |
| 18.19 | Eager start and local activities | Not applicable | P3 | Drop RFC-0004's eager-start experiment |
| 18.20 | Test techniques | Adopted correctly | P3 | None |
| 18.21 | Samples that do not apply | Not applicable | P3 | None |
| 19.1-19.10 | Nexus, its operations, patterns, security and standalone activities | Not applicable | P3 | Keep out; correct RFC-0004 §4.2, §6.6, §9.6 and §15 |

### 4.3 Counts

Across the 45 design-pattern entries (10 category overviews and 35 patterns; the catalogue's index page has no entry), counting each entry's main verdict:
- Adopted correctly: none.
- Adopted with deviations: 26.
- Misused: 4 (Delayed Callback, Approval, Non-Retryable Errors, Downstream Rate Limiting).
- Missing and beneficial: 5.
- Planned in RFC-0005: 3.
- Not applicable, or an overview with no verdict of its own: 7.

Most of OpenCompany's Temporal code follows the documented patterns in shape. The deviations are in the details the pages call out: cancellation, retries, waits, and what happens on a restart.

## 5. Rules every workflow and activity follows

These rules distil the patterns in §6 to §19 into checks a reviewer can apply to any change in `server/services/temporal/`, the plugin framework or a new workflow type. Each names the section that explains it. Where a lint or test enforces a rule, the rule says so (§22 schedules them).

### 5.1 The database holds the truth

1. **Workflows hold handles, cursors and timers, not facts** (RFC-0005 I10).
   - Anything the owner sees, or that a restart, a Reset or a fresh Temporal database must recover, lives in the app database first. That covers control state, approvals, sends, budgets, alarms and conversation cursors.
   - Turn numbers and any id that feeds an idempotency or spend key live there too, and every entity start (the first, a restart after passivation, a rebuild) seeds its state from the database.
   - A Signal is a hint to look (I1). (§8.1, §8.2)
2. **A wait goes where it can survive what it waits through.** (§9.1)
   - Waiting seconds for an answer: an activity, within one attempt.
   - Waiting for a person or a date: an Update plus a durable workflow timer.
   - Waiting for a provider to recover: activity retries, then a parked turn (T11).
   - Never sleep inside an activity for longer than one attempt can last.
   - Never put `start_delay` on an id that the relay addresses with Signal-With-Start: any delivery would bypass the delay (§9.4).

### 5.2 Workflow code

3. **Determinism is checked, not assumed.**
   - The 14 existing types run unsandboxed (`sandboxed=False`). A determinism lint covers their workflow code: the methods of `@workflow.defn` classes and the helpers they call, not activities that share the module. It checks for no I/O, no wall clock, no unseeded randomness, no `asyncio.wait` or `asyncio.as_completed` (their ordering is not deterministic; `workflow.wait` and `workflow.as_completed` are), and no command-issuing code in a signal handler.
   - New RFC-0005 types run in the sandbox, passing through only side-effect-free leaf modules. (§17.18)
4. **Cleanup runs only when the workflow is really exiting.**
   - Code that issues commands in `finally`, `except Exception` or `except BaseException` first re-raises a workflow bug (`is_workflow_bug`). Otherwise a bug records cleanup commands, marks rows failed, and makes the fixed release fail replay.
   - `asyncio.TimeoutError` counts as a failure, not a bug. (§18.2)
5. **Every child start states its policies.**
   - Each child start passes `parent_close_policy`, `cancellation_type` and `id_reuse_policy` explicitly.
   - Agent children use `REQUEST_CANCEL` with `WAIT_CANCELLATION_COMPLETED`; per-event runs use `ABANDON`.
   - A lint rejects a child start without them. (§6.2)
6. **Know what replay compares.**
   - On replay Temporal compares only a command's kind, its activity or child type, and its id.
   - Changing timeouts, retry policies, task queues or a parent close policy needs no patch.
   - Changing a cancellation type does need a patch whenever a replayed history can contain a cancel of that activity or child, because the cancellation type decides whether a cancel emits a command. (§6.2; corrects RFC-0004 §8.5)
7. **Every fan-out names its bound, and every race names its loser's fate.** That means who cancels the loser, whether the workflow waits for its cleanup, and how long that wait may last. (§6.3, §6.4)
8. **Timers run over absolute deadlines.**
   - One live timer per workflow over named absolute deadlines, through a shared helper.
   - Never `wait_condition` with a timeout of zero or less.
   - No sub-second timers: the docs say timers are accurate only from one second up (https://docs.temporal.io/workflow-execution/timers-delays). (§8.4)
9. **Close cleanly.**
   - Continue-as-new and completion happen only in the main method, after `await workflow.wait_condition(workflow.all_handlers_finished)`, with no `await` between the final buffer check and the close.
   - A long-lived type rolls over when the server suggests it, or at 1,800 Updates (90% of the 2,000-per-run limit), whichever comes first. (§7.4, §8.3, §12.1)
10. **Activity ids are idempotency keys.** Wherever an id feeds an idempotency or spend key, it is explicit and unique across the continue-as-new chain. The SDK's default activity id is a per-run counter that restarts after continue-as-new. (§17.11)
11. **No UI-only activities in new types.** Progress broadcasts become best-effort local activities or ride an adjacent activity. Today they are 35-52% of an AgentWorkflow history. (§14.2, §17.4)

### 5.3 Messages

12. **Handlers stay small.** Signal handlers only buffer or set flags. Rejections that need no I/O live in Update validators, which reject without writing history. (§7.1)
13. **Every client-side Update call runs under `asyncio.timeout`.** `rpc_timeout` bounds one request only: after the Update is accepted, `execute_update` waits for the outcome without it. (§7.3; corrects RFC-0004 §8.2)
14. **Owner actions go through intents** (T10).
    - Stop, Resume, approval decisions, Interrupt, Start and Apply write an intent row and answer at once.
    - A background deliverer sends the Update by workflow id, with a stable id and a per-attempt deadline, until it is applied.
    - No request path waits on Temporal (RFC-0005 I2). (§7.3, §10.2)
15. **Updates carry absolute values and a revision.**
    - Handlers are idempotent by revision across continue-as-new, because Update-id dedupe works only within one run.
    - A stale revision is rejected in the validator. (§7.3, §8.2)

### 5.4 Activities, retries and side effects

16. **Named retry policies only.**
    - Every activity call site uses a shared policy constant; a lint rejects inline policies.
    - A paid or side-effecting call never retries without a time budget.
    - One exception: the LLM step and compaction on generations without `scheduler_version=1` (RFC-0005 Phase 5) keep unlimited retries, under the retry alert (1b.30). A budget without parking would fail their turns (T18).
    - An unlimited policy is otherwise allowed only for idempotent bookkeeping, compensations, queue paging, permit waits, T7's poll activities (each retry is the next poll), and sends and deliveries made at-most-once by their claim (the egress outbox; reviewed delivery's `inflight` mark, 1b.9). Each is declared, and the retry alert covers all of them except the planned retries of polls and permit waits. (§11.1; rewords RFC-0004 AG9)
17. **Classify every failure.**
    - User and configuration errors fail fast as non-retryable: `NodeUserError`, permission errors, `PayloadSizeError` and bugs inside the LLM step.
    - Rate limits retry as BENIGN with the provider's `next_retry_delay`.
    - Planned retries (T7's poll attempts, permit waits) are BENIGN and excluded from retry alerts. (§11.4, §11.5, §11.7)
18. **Side effects declare their class** (T13, E12).
    - Every operation that changes something outside OpenCompany declares `read_only`, `idempotent` (a provider key, or a natural key) or `unsafe`.
    - An `unsafe` call runs under a claim guard: if an attempt broke off after the provider might have acted, the next attempt records `unknown` and tells the employee instead of calling again.
    - This applies to graph nodes and tool calls alike, sends included. (§11.2, §16)
19. **Cancellation reaches the code.**
    - Activities re-raise cancellation instead of returning a failure envelope (RFC-0004 H2).
    - A worker shutdown becomes a retryable error, so the next worker resumes the work. (§9.3)
20. **Long activities heartbeat.**
    - The heartbeat interval is a third of `heartbeat_timeout`, at most 30 s.
    - They record external job ids in a database row before waiting on the job, and also in heartbeat details. A retry then re-attaches to the job instead of starting it again. Heartbeat details alone are lost when the activity is scheduled again after a rollover. (§9.3)

### 5.5 Capacity and order

21. **One enforcement point per resource.**
    - Turns are limited by admission (RFC-0005 §13.3), providers by per-credential buckets.
    - Queue-wide rate caps go only on a queue with one downstream and one fixed limit, and never on `machina-tasks`, where a cap turns off eager activity dispatch. (§13.1, §13.2)
22. **Every top-level workflow start sets an explicit priority key.** An unkeyed start runs at 3, which is RFC-0005's internal class; unkeyed children and activities inherit their parent's key. An AST test enforces this. (§13.3)

### 5.6 Payloads and secrets

23. **References, not bytes.**
    - Media travels as `FileRef`, and events over 64 KB travel by reference (RFC-0005 I7).
    - Agent transcripts are the one exception (T6). They stay within its bounds: at most 393,216 bytes of messages before every step, each step input under 1 MiB, the continue-as-new carry under 1.9 MB and the stored conversation under 1 MB. (§16.8)
24. **No secrets in Temporal inputs.** Snapshots of node parameters are sanitized on every path, Start included, and activities read credentials from the saved row. (§17.8)

### 5.7 Workers and tests

25. **One worker builder.**
    - Every Worker, embedded, standalone, test or recorder, comes from one function.
    - That function makes dependencies ready before polling, registers from one list (`workflow_registry.py`), and checks the registration against what workflows schedule.
    - The pool always polls the queues frozen runs can target.
    - Activities bound to the backend process run on the instance queue (T8). (§15)
26. **Every limit we rely on has a real-server test.** That includes Updates per run, history size and continue-as-new suggestions. Every change to workflow code passes the N-1 replay gate and the golden identity test. (§12.1, §17.17)

## 6. Task orchestration

Three pages cover how one workflow splits, parallelizes and races work. OpenCompany uses all three mechanisms where the docs put them: agents run as child workflows, graph nodes run as parallel activities, and races happen inside activities. The gaps are in what happens to work that is still running when its parent stops or fails.

Terms used below:
- A **Child Workflow** is a workflow started by another workflow, its parent, with its own id and history.
- A **command** is what workflow code asks the server to do: schedule an activity, start a child, start a timer, record a patch marker. On replay the SDK checks the code's commands against the recorded history, so a change to which commands a workflow issues needs versioning: `workflow.patched(...)` inside a type that old and new runs share, or a per-generation `resource_manifest` flag frozen into the workflow's input (RFC-0005 §24.1 rule 2).
- The **parent close policy** decides what the server does to a child that is still running when its parent closes: TERMINATE (kill it; none of its code runs), REQUEST_CANCEL (send it a cancellation request, so its cleanup runs) or ABANDON (leave it running).
- The **cancellation type** decides what the parent's handle does when the parent itself cancels the child or activity: TRY_CANCEL (request the cancel and resolve at once; the default for activities), WAIT_CANCELLATION_COMPLETED (resolve only when the child or activity has finished cancelling; the default for children) or ABANDON (resolve at once and send no cancel).
- The **id reuse policy** decides whether a new execution may take the id of a closed one.

### 6.1 Task orchestration patterns (overview)

**Docs:** https://docs.temporal.io/design-patterns/task-orchestration-patterns. A chooser for the three patterns of this section:
- "A process is large or reused across Workflows": Child Workflows "with independent histories".
- "Independent work can run at the same time": Parallel Execution "with a concurrency bound".
- "Several approaches compete and you want the first to finish": Pick First "and cancel the losers".
- Scale-out goes to Batch Processing, latency to Performance & Latency.

**SDK:** none of its own (§6.2 to §6.4).

**OpenCompany today:**
- Agents are child workflows. MachinaWorkflow schedules graph nodes as parallel activities. AgentWorkflow runs a turn's tools one at a time, delegations as a window of children, and Task Manager work as ABANDON runners. The controller starts one child run per event. Races exist only inside activities. (§6.2 to §6.4.)
- No rule says which close policy, cancellation type and id reuse policy a child start uses. Four agent-child starts, at six call lines, leave the parent close policy at the SDK default, TERMINATE (table in §6.2).
- No fan-out names its bound. MachinaWorkflow schedules every ready node (`server/services/temporal/workflow.py:640-874`); the controller starts one child per event without a cap (`server/services/temporal/workflow_control_workflow.py:431-483`).
- Work that should stop is either killed without cleanup or left running: an uncontrolled run's failure abandons its running nodes (`workflow.py:907-908`), a cancelled root's agent children are terminated (§6.2), and a chat run past 24 hours leaves its workflow running (§6.4).
- Nothing enforces determinism. All 14 workflow definitions pass `sandboxed=False`, so the SDK's sandbox warning for `asyncio.wait` can never fire, and no test forbids it.

**Verdict:** Adopted with deviations. The three mechanisms sit where the docs put them; bounds and the cleanup of losers are stated nowhere.

**Recommendation:**
1. **State three orchestration rules and lint them.** §5 adopts them as rules: (a) every child start passes `parent_close_policy`, `cancellation_type` and `id_reuse_policy` explicitly; (b) every fan-out names its bound; (c) every race names who cancels the loser and whether the workflow waits for its cleanup. The checks ship in the determinism lint, 1b.40, which §17.18 specifies. The orchestration part this entry adds to it: a child start without the three explicit options; `asyncio.wait` and `asyncio.as_completed` in workflow modules; a direct `execute_activity` or `start_activity` call inside a `@workflow.signal` handler. (Pi `pause` and `resume` reach an activity indirectly, through `_forward`; §7.3 R6 removes them.) The same commit writes out the current defaults at the child starts that rely on them; passing a default explicitly changes nothing on replay. The retry-policy lint over the same workflow modules is 1b.41 (§11.1).
   - Versioning: none (a test, and explicit defaults).
   - When: Phase 1b (1b.40), after 1b.12 and 1b.13. Effort S. Risk: none at run time.

**Remarks:** The overview points to Batch Processing. OpenCompany has no record-batch workload, so those pages map only to the controller's one child per event, its queue paging and the delegation window (§12.2 to §12.4).

### 6.2 Child Workflows

**Docs:** https://docs.temporal.io/design-patterns/child-workflows. A child "executes as an independent Workflow with its own Workflow ID, event history (51,200 event limit), and lifecycle". Use a child for a separate id, work that "may outlive the parent", reuse, another task queue or an independent history; use an activity for external calls ("Consider Activities first").
- Python: `workflow.execute_child_workflow(...)` waits for the child to finish; `workflow.start_child_workflow(...)` "returns a handle once the child has started".
- Parent close policy: TERMINATE for "Tightly coupled processes", ABANDON for "Fire-and-forget, long-running tasks", REQUEST_CANCEL for "Graceful cleanup". "The default policy is TERMINATE, which kills children when the parent closes." The cancellation concept page adds: "Anything a terminated Workflow would have cleaned up is left as it was: reserved inventory stays reserved, held locks" stay held (https://docs.temporal.io/encyclopedia/workflow/cancellation-and-termination).
- "You must wait for the child to start before the parent completes." The Python guide adds: from a non-main coroutine, "make sure the Parent Workflow doesn't complete before that call resolves".
- Best practices: unique ids, "Generate deterministic IDs based on business keys" (the page's own samples use `workflow.uuid4()`); choose the policy; handle `ChildWorkflowError`; "Limit parallelism" (a hard limit of 2,000 pending children per parent, and at most 1,000 children per parent in total is recommended); "Set timeouts"; typed stubs; "Monitor child executions".
- Pitfalls: "Treating Child Workflows like Activities", unbounded children in a loop, "Ignoring the Parent Close Policy", omitting ids, not handling child failures. The comparison table rates an "Async Lambda" as "Independent history: No; Can outlive parent: No".

**SDK:**
- 1.30.0: `workflow.start_child_workflow(..., cancellation_type=WAIT_CANCELLATION_COMPLETED, parent_close_policy=TERMINATE, id_reuse_policy=ALLOW_DUPLICATE, execution_timeout, run_timeout, task_timeout, retry_policy, memo, search_attributes, static_summary, static_details, priority)` (`temporalio/workflow/_workflow_ops.py:291-316`). Children have no `id_conflict_policy`.
- 1.34.0 adds only the experimental `event_groups` parameter; the enums and defaults are unchanged.
- In both versions, cancelling a workflow cancels only its main task (`temporalio/worker/_workflow_instance.py:598-606` in 1.30.0, `:660-668` in 1.34.0). A child that the code is not awaiting at that moment gets no cancel; the server applies its parent close policy when the parent closes.
- Cancellation fixes after 1.30.0, per the release notes: 1.31 "cancellations no longer swallowed in some circumstances"; 1.32 no nondeterminism error when a try-cancel child finishes after its cancel; 1.33 duplicate cancel commands de-duplicated (`_request_cancel`, present in the 1.34.0 source and absent in 1.30.0).

**OpenCompany today:**

Every child start in production code. The 21 recorded scenarios in `server/tests/temporal/fixtures/0.3.1/` record the same values. "Default" is the SDK default. No child start sets an execution or run timeout.

| Parent -> child | Start | Parent close | Cancellation type | Id reuse |
|---|---|---|---|---|
| MachinaWorkflow -> AgentWorkflow or PiCodingAgentWorkflow | `server/services/temporal/workflow.py:826` (execution control on), `:828` (off); id `<root id>-agent-<node id>` (`:170-172`) | TERMINATE (default) | default | ALLOW_DUPLICATE (default) |
| DelegatedTaskWorkflow -> agent | `server/services/temporal/agent_workflow.py:595-598` | TERMINATE (default) | default | default |
| AgentWorkflow -> agent (`delegate_to_*`) | `agent_workflow.py:1740-1744` | TERMINATE (default) | default | default |
| AgentWorkflow -> DelegatedTaskWorkflow (Task Manager runner) | `agent_workflow.py:1894-1899` (options `:1878-1891`, with `EventWorkflowId`) | ABANDON | default | default |
| EmployeeJobWorkflow -> lead agent | `server/services/temporal/employee_job_workflow.py:117` (on), `:120` (off) | TERMINATE (default) | default | default |
| PiInlineDelegationWorkflow -> DelegatedTaskWorkflow | `server/services/temporal/pi_inline_delegation.py:365-369` | REQUEST_CANCEL | WAIT_CANCELLATION_COMPLETED (explicit) | default |
| WorkspaceTaskControllerWorkflow -> NodeInvocationWorkflow | `server/services/temporal/workspace_tasks_workflow.py:114-121` | REQUEST_CANCEL | explicit | REJECT_DUPLICATE |
| NodeInvocationWorkflow -> Workspace agent | `server/services/temporal/node_invocation.py:34-39` | REQUEST_CANCEL | explicit | default |
| Controller (push and poll) -> MachinaWorkflow per event | `server/services/temporal/trigger_listener_workflow.py:495`, `:497`; `polling_trigger_workflow.py:398-402`; entered from `workflow_control_workflow.py:755-765`, `:888-895` | ABANDON | default | ALLOW_DUPLICATE_FAILED_ONLY |
| CronTriggerWorkflow -> MachinaWorkflow | `server/nodes/scheduler/cron_scheduler/_workflow.py:203` (options `:176-189`) | ABANDON | default | ALLOW_DUPLICATE_FAILED_ONLY |

- **What follows the docs.**
  - Agent loops are children, with their own history, continue-as-new (`agent_workflow.py:2982-3069`) and control Updates; tools are activities. NodeInvocationWorkflow runs a plain node as an activity and only an agent as a child (`node_invocation.py:31-49`). No child is used as a function call.
  - Ids are deterministic and built from business keys (table).
  - Starts resolve before a parent can finish. Cron and listeners return after the start resolves (`cron_scheduler/_workflow.py:186-214`; `trigger_listener_workflow.py:493-497`). Task Manager runner starts run in asyncio tasks that are awaited in call order before the turn ends (`agent_workflow.py:2015-2021`, `:2374-2381`), and the rollover guard refuses continue-as-new while any is live (`:2993-2996`). An exception that escapes the tool loop leaves runner-start tasks unawaited.
  - Child failures: MachinaWorkflow turns a finished child's exception into a node failure (`workflow.py:1082-1133`); AgentWorkflow returns a failed delegation to the model (`agent_workflow.py:2574-2628`); DelegatedTaskWorkflow and EmployeeJobWorkflow record a terminal state and re-raise (`agent_workflow.py:692-702`; `employee_job_workflow.py:121-126`).
  - Timeouts: none on children, on purpose (`workflow.py:148-155`; `agent_workflow.py:186-191`): runs may stay paused for months and timeout timers keep running through a pause. This departs from "Set timeouts" deliberately (RFC-0005 §27).
  - Parallelism: delegations per turn are capped by `max_concurrent_subagents` (default 3, `agent_workflow.py:1451-1453`) and per root by a database permit (`server/services/temporal/agent_activities.py:2247-2339`).
  - Monitoring: runner and child ids are recorded by `agent.register_task_execution` (`agent_workflow.py:553-561`, `:601-609`, `:1748-1757`). Workflow types travel as strings on purpose, guarded by `server/tests/temporal/test_workflow_identity_golden.py` and `server/services/temporal/workflow_registry.py`.
- **Where a parent closes early, so TERMINATE kills an agent without cleanup:**
  1. *An uncontrolled run fails.* The first failed node ends the loop (`workflow.py:907-908`); the run completes with `success: False` (`:913-929`) and the server terminates its agent children. Manual canvas Runs are uncontrolled: execution-control fields are copied only for an admitted generation (`server/services/temporal/executor.py:152-173`; `server/routers/websocket.py:968-975`).
  2. *Chat Stop outlasts its grace period.* Stop moves the run to `stopping` (`server/services/chat/ledger.py:598-639`). The watchdog cancels the run's MachinaWorkflow 45 to about 75 seconds later (`ledger.py:714-728`; `stop_grace_s` 45 and `watchdog_interval_s` 30 in `server/config/chat_defaults.json`). MachinaWorkflow waits in `workflow.wait_condition` (`workflow.py:1120`) with no `CancelledError` handler, closes Cancelled, and the server terminates its AgentWorkflow. A run that stops cooperatively inside the grace closes normally.
  3. *Apply with "stop work".* `server/services/employees/safe_apply.py:112-116` cancels running executions found by `EventWorkflowId`. Agent children carry no search attributes (`workflow.py:820-822`), so only the parent's TERMINATE reaches them. Task Manager agents are the exception: their runner carries `EventWorkflowId` (`agent_workflow.py:1882-1891`), is cancelled directly and awaits its agent (`:610`), so that agent gets a real cancel.
  4. *A child-start collision.* `WorkflowAlreadyStartedError` at `workflow.py:826` or `:828` is a `FailureError` (`temporalio/exceptions.py:53`) and fails the whole run instead of one node. Any other start failure becomes a `RuntimeError`, which fails the Workflow Task instead (`temporalio/worker/_workflow_instance.py:918-920`).
  5. *An AgentWorkflow cancelled while its delegations run.* asyncio passes the cancel only to the handle being awaited. The other started children in `delegation_handles` (`agent_workflow.py:1467`) are terminated when the agent closes. `_cleanup_cancelled_delegations` (`:1518-1566`) settles the task rows and permits but cancels no handle.
- **What termination skips.** AgentWorkflow's `fail_job(cancelled=True)` (employee runtime-v2 leads only, `agent_workflow.py:826-836`), its browser-claim release (`:842-857`), its delegation cleanup, and the cleanup of its own children. Consequences:
  - *Browser claims.* A claim left by a terminated agent keeps the profile `BrowserBusy` for every other task (`server/services/browser_owners.py:245-258`). Reset releases it only while the terminated execution is still retained (24 hours on the dev server) and only if it belongs to the generation being reset (`server/services/browser_reset.py:23-45`, `:60-75`). A manual canvas Run's claim, or one older than retention, is never released, and it also defers the profile cleanup of a deleted employee (`server/nodes/browser/__init__.py:94-98`).
  - *Subagent permits.* A leaked permit holds one of the root's delegation slots (default limit 3) for up to 3 hours; the next acquire for that root then releases it (`server/core/database.py:4143-4187`).
  - *Employee job rows.* A runtime-v2 lead started by MachinaWorkflow records its own failure only from workflow code (`agent_workflow.py:826-829`; `employee_job_workflow.py:28-66`), so its job stays non-terminal; no automatic repair exists. EmployeeJobWorkflow leads are settled by the parent's shielded `employee.job.failed` (`employee_job_workflow.py:121-126`) unless the parent itself is terminated.
  - Reset is not on this list: it terminates the whole tree on purpose (`server/services/deployment/handlers.py:974-1017`) after expanding by `RootWorkflowId` (`:862-900`); RFC-0004 H12 and D9 cover its cancel-first variant.
- **Async Lambda: delegations from Claude Code and RLM.** `_execute_delegated_agent` runs a delegated agent as `asyncio.create_task(run_child_agent())` (`server/services/handlers/tools.py:889-890`) under a process-local semaphore (`:78-81`, `:475`).
  - From Claude Code, this path is reached only through Task Manager `assign_task` to a non-Coding teammate: the MCP bridge injects `ai_service` and `database` only for `coding_agent`, `taskManager` and `_builtin_check_delegated_tasks` with `pi_harness_version == 1` (`server/services/cli_agent/workflow_tools.py:337-355`). A Claude Code `delegate_to_*` call to any other agent already fails with "Agent delegation requires ai_service and database in config" (`tools.py:401-405`), a separate defect.
  - From RLM, both direct delegate tools and Task Manager reach it (`server/services/rlm/adapters.py:269-272`; Task Manager's in-process branch, `server/nodes/tool/task_manager/__init__.py:198-209`).
  - A Coding Agent target becomes durable through PiInlineDelegationWorkflow, but only when `pi_harness_version == 1` (`tools.py:378-386`).
  - The task runs in the process that handled the call: the API process for the `/mcp/ide` bridge (`server/main.py:406`, `:668`), the worker process of the RLM activity for RLM. It is lost on restart (the status lookup then answers `not_found`, `tools.py:1061-1062`), invisible to Temporal, untouched by Stop and Reset (`server/services/workflow.py:833-858` cancels CLI sessions only), its TeamTask row stays `running` (`server/core/database.py:3698`), and its permit self-heals only after 3 hours.

**Verdict:** Adopted with deviations. Ids, awaited starts, ABANDON for per-event runs and the child-versus-activity split follow the docs. Two deviations: four agent-child starts keep TERMINATE although agents own cleanup (browser claims, permits, job rows), and delegations from Claude Code and RLM run as Async Lambdas.

**Recommendation:**
1. **R1. Agent children get REQUEST_CANCEL.** Pass `parent_close_policy=workflow.ParentClosePolicy.REQUEST_CANCEL` and an explicit `cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED` at the six call lines: `workflow.py:826` and `:828` (through `child_start_kwargs`, `:820-822`), `agent_workflow.py:595-598` and `:1740-1744`, `employee_job_workflow.py:117` and `:120`. The Workspace agent child already does this (`node_invocation.py:34-39`).
   - Versioning: none. On replay, core compares only a child's id and type (and an activity's id and type); the parent close policy is never compared. The checks are in the core sources shipped in the 1.30.0 wheel, under `temporalio/bridge/sdk-core/crates/sdk-core/src/worker/workflow/machines/`: `child_workflow_state_machine.rs:233-258` and `activity_state_machine.rs:371-394`. The docs agree: changing "The Child Workflow Options in a call to spawn a Child Workflow Execution" is listed among changes "that would not result in non-determinism errors" (https://docs.temporal.io/workflow-definition). The golden identity test is unchanged, because it captures only the `id` and `activity_id` keywords (`server/tests/temporal/workflow_identities.py:36-40`). `test_replay_corpus.py` must stay green.
   - When: Phase 1b (1b.12). RFC-0004 decided it (D9, RFC-0004 §8.4); RFC-0005 says it implements D9 but schedules no step for it. Effort S.
   - Risk: a closed parent now leaves its child running its cleanup for a while (see Remarks on id reuse).
2. **R2. Cancel and drain.** On cancellation, MachinaWorkflow cancels every handle in `running` and waits for them, with a bound (sketch); AgentWorkflow's `_cleanup_cancelled_delegations` does the same for every started handle in `delegation_handles` before its database cleanup. With the same patch, node activities get `cancellation_type=WAIT_CANCELLATION_COMPLETED`, so the drain waits for the activity's own cleanup. §6.3 rec 1 adds the failure half of the same patch.
   - Versioning: `workflow.patched("machina-close-and-drain-v1")` in MachinaWorkflow, read once before the scheduling loop, and `workflow.patched("agent-delegation-drain-v1")` in AgentWorkflow. The cancellation-type change must ship inside the same patch: a cancellation type is not compared on replay, but it decides whether a cancel emits a RequestCancel command and when the workflow sees the outcome, and core raises a nondeterminism error when a non-ABANDON activity or child records a start or completion after its cancel ("Non-Abandon cancel mode activities cannot be completed after being cancelled", `activity_state_machine.rs:779-781`; the child equivalent at `child_workflow_state_machine.rs:146-149`). Here core is stricter than the workflow-definition page, which lists activity and child option changes as safe. The repo already gated the same change for Workspace nodes behind `workspace-node-cancellation-v1` (`workflow.py:856-861`).
   - When: P2, in a cancellation lane after Phase 1 (which ends with the 1.34.0 upgrade and its 1.31 and 1.33 cancellation fixes) and Phase 1b, and before RFC-0005 Phase 4, whose Interrupt and 24-hour no-progress backstop (a default, §24.1; §6.4 rec 2) cancel a turn and expect its agents to clean up. Record two fixtures first (1b.11): a root cancelled while its agent child runs (the chat Stop grace path), and an uncontrolled branch failure (a manual Run with two branches, one failing). None of the 21 scenarios in `server/tests/temporal/fixtures/0.3.1/manifest.json` contains a cancelled root or a failed node activity. Effort M.
   - Risk: a node that never sees the cancel holds the run until the bound. The drain assumes node activities report cancellation; today `NodeExecutor` turns `CancelledError` into a `{success: False, error: "Cancelled"}` result (RFC-0004 H2), which resolves the handle but records the activity as Completed. 1b.2 (§9.3) fixes H2 in Phase 1b, before this lane.
3. **R3. Durable delegations from Claude Code and RLM** (default chosen; §24 lists the alternative). When Temporal is on, every delegation reached through `_execute_delegated_agent` starts a durable runner instead of an asyncio task: a new workflow type `InlineDelegationWorkflow` that generalizes PiInlineDelegationWorkflow's admission (an admission row with a request digest, a deterministic runner id from root, parent, target and call, `REJECT_DUPLICATE`, `memo` with the digest; `pi_inline_delegation.py:120-230`) to every agent type in `AGENT_WORKFLOW_TYPES`, starts `DelegatedTaskWorkflow` as its child with REQUEST_CANCEL (as `pi_inline_delegation.py:365-369` does), and is started with `EventWorkflowId` so Reset and Apply find it (RFC-0004 H12 notes the Pi inline runner lacks it). It must cover both entry paths: Task Manager's in-process branch and the direct `delegate_to_*` tools; the Claude Code bridge then passes `database` and the trusted scope for those calls too.
   - Versioning: none (a new workflow type; old runs never meet it).
   - When: P2, an independent lane after Phase 1, before hosted tier S splits workers into a second process (T8): process-local tasks and semaphores do not survive that split. Effort L.
   - Risk: admission and permission checks (`assert_runtime_access`) must run on the trusted scope, not on model-supplied arguments.
4. **R4. The Task Manager runner rejects duplicates.** Start it with `id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE` (`agent_workflow.py:1878-1881`), like every other do-once start (`server/services/employees/jobs.py:240-243`, `pi_inline_delegation.py:219-226`, `workspace_tasks_workflow.py:118`), and treat `WorkflowAlreadyStartedError` as "already queued". Versioning: the policy needs none; the new exception handling changes commands only after a failed start, so ship it under the next AgentWorkflow patch. When: P3. Effort S. Risk: none known; duplicates are near impossible because runner ids include the iteration and the task id.
5. **R5. Agent children carry `EventWorkflowId`.** Pass `search_attributes=event_workflow_search_attributes(workflow_id)` on agent child starts. Only safe-apply's stop-work sweep then reaches them directly (`safe_apply.py:112-116`); the manager's sweep filters to the two listener types (`server/services/deployment/manager.py:1176-1185`). Versioning: none (an option). When: P3. Effort S. Risk: an agent child may get two cancel requests (direct and from its parent), which is harmless.
6. **R6. A failed child start fails only its node.** Catch `WorkflowAlreadyStartedError` around `workflow.py:824-829` and record it as that node's failure. Versioning: it changes commands only after a failed start, which fails the run today, so put it under `machina-close-and-drain-v1`. When: P2 with R2. Effort S. Risk: only `WorkflowAlreadyStartedError` is caught; other start failures still fail the Workflow Task and stay visible.

**Remarks:**
- R1 and R2 are both needed. REQUEST_CANCEL sends the cancel when the parent closes and does not wait, so the parent's result cannot reflect the child's cleanup. The drain alone does not cover parents that close without running code: termination, or a failure path that skips the handler.
- Replay never re-sends a recorded command. Children started before R1 ships keep TERMINATE; only children started afterwards get REQUEST_CANCEL; an in-flight run can hold both.
- AgentWorkflow already runs its cleanup after catching `CancelledError`, shielding the activities (`agent_workflow.py:826-859`). That is Python's form of "cleanup outside the canceled scope", and it runs only when a cancel reaches the workflow, which R1 provides.
- Id reuse after R1. Per-event roots use ALLOW_DUPLICATE_FAILED_ONLY, so a Cancelled or Terminated root id can run again. If its old agent child is still cleaning up, the new start of `<root id>-agent-<node id>` raises `WorkflowAlreadyStartedError` and, until R6, fails the new run. The controller treats a duplicate start as accepted when the accumulator is on (`workflow_control_workflow.py:468-472`), so repeats are rare; R2's drain closes the window.
- The parent close policy also applies when a parent continues as new. AgentWorkflow's rollover guard refuses to continue while delegations or runner starts are live (`agent_workflow.py:2982-2996`); keep it after R1, or a rollover would cancel live delegations.
- Pending-child limit: only the controller's count follows the event rate. Whether ABANDON children count as pending while the parent runs was not verified; the docs' "incomplete Child Workflows" suggests they do. At 2,000 the start command fails and the workflow task retries until a child closes, stalling the employee's triggers. Not reachable at today's volumes (unmeasured); RFC-0005's one turn per conversation bounds it by design.
- Tests that pin today's policies: `server/tests/temporal/test_agent_workflow.py:394` (runner ABANDON) and `server/tests/temporal/test_browser_task_dispatch.py:28` (REQUEST_CANCEL). Unit tests that assert agent-child start options may need updating.
- If a release ever ships without a needed patch, T14 starts it anyway and marks the affected generations "needs Reset". The patches above keep open runs resumable instead.

**Sketch** (R2 in MachinaWorkflow, SDK 1.30.0; AgentWorkflow's delegation drain has the same shape):

```python
# server/services/temporal/workflow.py, MachinaWorkflow._run_graph
DRAIN_PATCH = "machina-close-and-drain-v1"
_DRAIN_BOUND = timedelta(minutes=2)

drain_v1 = workflow.patched(DRAIN_PATCH)   # read once, before the scheduling loop
...
if drain_v1:
    # Same patch: a replayed history can only hold a cancel of this activity if it has the marker.
    start_kwargs["cancellation_type"] = workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
...
try:
    while True:
        ...  # the existing scheduling loop (workflow.py:647-911)
except asyncio.CancelledError:
    if drain_v1 and running:
        for handle in running.values():
            handle.cancel()   # activity: RequestCancelActivityTask; agent child: cancel request, then wait
        try:
            await workflow.wait_condition(
                lambda: all(h.done() for h in running.values()),
                timeout=_DRAIN_BOUND,   # a node that never sees the cancel must not hold the run
            )
        except asyncio.TimeoutError:
            workflow.logger.warning("Cancel drain bound reached; closing with work still cancelling")
    raise   # controlled runs then unregister in run()'s finally (workflow.py:503-513)
```

### 6.3 Parallel Execution

**Docs:** https://docs.temporal.io/design-patterns/parallel-execution. Start every independent operation, then wait for the results.
- Python: `asyncio.gather(*[workflow.execute_activity(...) for item in items])`; "Avoid premature blocking. Schedule all Activities before waiting for any results."
- Controlled parallelism: chunks of `max_parallel`, each awaited before the next ("Limit concurrency").
- Errors: `asyncio.gather(..., return_exceptions=True)`; waiting for all "fails on the first error by default"; "Support partial results."
- Limits and pitfalls: at most 2,000 pending activities ("Scheduling more causes Workflow Task failures"), counted separately from children, Signals and cancellation requests; "Blowing the 4 MB gRPC message limit ... if their combined inputs are large. Batch scheduling across multiple Workflow Tasks"; history growth toward 51,200 events; Workflow memory for many futures; "Parallel execution makes tracing more difficult"; "Rate limit".
- Not a fit for dependent operations or strict ordering.
- The in-flight figure "aim for 500" is on the Batch Processing page, not this one (https://docs.temporal.io/design-patterns/batch-processing-patterns).

**SDK:** activity and child handles are asyncio tasks (`temporalio/workflow/_activities.py:61`, `_workflow_ops.py:53`), so `asyncio.gather` works. `workflow.wait` and `workflow.as_completed` are the deterministic replacements for the asyncio versions (`temporalio/workflow/_asyncio.py`, the same in 1.34.0 apart from line endings). Paginated workflow-task completions (SDK 1.33 with server 1.32, behind `history.enableWorkflowTaskCompletionPagination`, default false) are the only server-side relief for the 4 MB limit; the recombined size must still fit, and an over-limit completion then fails the Workflow Task and retries forever instead of terminating the execution. T5 removes the server-version gate, not the size rule. 1.34.0's experimental Event Groups could group a turn's commands in the UI; RFC-0004 Q12 keeps preview features off.

**OpenCompany today:**
- **MachinaWorkflow schedules continuously.** Ready nodes come from `_find_ready_nodes` in graph order (`server/services/temporal/workflow.py:1244-1264`), all start at once (`:674-874`), and completions are taken one at a time by `wait_condition` and an ordered scan (`_wait_any_complete`, `:1101-1133`). This beats the docs' chunks (no wait for a chunk's slowest member) and is deterministic: insertion order, never `asyncio.wait`.
- **No in-flight bound.** `running` grows to every ready node (`:640-874`); only the graph's width limits it.
- **Inputs carry the graph.** Every node input embeds `nodes`, `edges`, the whole `parameter_snapshot` and upstream outputs (`workflow.py:708-724`); agent tool inputs embed them too (`server/services/temporal/agent_workflow.py:2170-2195`). K ready nodes in one task send K copies in one completion. Recorded toy graphs are small: AgentWorkflow start 1,738 bytes and the `node.chatReply.v1` input 1,612 bytes (`server/tests/temporal/fixtures/0.3.1/machina_chat_agent.json`); real sizes are unmeasured. Already RFC-0004 M6 and D7; RFC-0005's turn loads the graph by reference (RFC-0005 §10.2).
- **Three failure policies.** Uncontrolled Temporal runs (manual canvas Runs and generations without execution control) stop at the first failure and abandon running siblings (`workflow.py:907-908`). Controlled runs close admission and let in-flight branches finish, so independent nodes not yet started never start (`:656-657`). The in-process executor cancels the remaining tasks and waits for them (`server/services/execution/executor.py:441-458`).
- **A turn's tool calls run one at a time** (`agent_workflow.py:2035-2369`; the `await _execute_plugin_tool_activity(...)` at `:2363-2369`). Concurrency exists only for Task Manager `assign_task` preflights (`start_activity` plus `asyncio.gather(..., return_exceptions=True)`, `:1903-1992`, the page's error-handling shape) and `delegate_to_*` children. The in-process loop is sequential too (`server/services/agent_runtime.py:359-388`). 39 lines under `server/nodes` declare `"readonly": True` in plugin `annotations` (for example `server/nodes/filesystem/file_read/__init__.py:45`), but `agent.prepare_payload` records no annotations.
- **The delegation window is ordered, not sliding,** although its comment calls it "a bounded sliding window" (`agent_workflow.py:1442-1446`). Handles are awaited in model order. On the uncontrolled path the next delegation starts only after the earliest call finishes without raising (`:2259-2270`, `:2343-2348`); the controlled path starts it after any completion (`:2666-2668`). Most child failures come back as `success: False` results and do start the next one; a child that raises (terminated, cancelled, or failed by an uncaught error) blocks it. The never-started delegation's call then reports `KeyError` (`:2259`, caught at `:2574`). For a team lead, `agent.finish_delegation` is then called for a task that was never queued; it raises "Delegated team task does not exist" three times (`server/services/temporal/agent_activities.py:2383-2384`), and the lead AgentWorkflow fails. Uncontrolled path only.
- **Controller poll loops.** One asyncio task per polling trigger (`server/services/temporal/workflow_control_workflow.py:332`, `:526-528`) catches and logs its own errors (`:849-866`); rollover cancels and gathers them (`:612-615`). This is not the docs' isolation: a cancel that lands while a poll awaits its activity arrives as `ActivityError`, which `except Exception` swallows, so the gather can wait forever and stall the rollover (RFC-0004 H8). Legacy controllers are fully exposed and v1 controllers in a narrower window (§8.3). Under T7, polls leave the controller for new generations.
- Results are ordered deterministically: tool messages in model order, preflight results zipped in order (`:1980-1989`), delegations awaited in order.

**Verdict:** Adopted with deviations. The scheduling loop is better than the docs' sample; the deviations are three failure policies, no bound, inputs that copy the graph, a delegation window that can fail a team lead, and poll loops that swallow a cancel.

**Recommendation:**
1. **One failure policy for graph runs** (default chosen; §24 lists the alternative): when a node fails, running siblings finish and no new node starts. This is what controlled runs already do. Uncontrolled MachinaWorkflow runs adopt it under the same marker as §6.2 R2 (`machina-close-and-drain-v1`): with the marker, a failure sets `ready = []` instead of `break` (sketch). The in-process executor stops cancelling (`executor.py:441-458`) and waits for running tasks without starting new ones.
   - Versioning: the MachinaWorkflow change is inside §6.2 R2's patch; the in-process change needs none. Ship both in the same release.
   - When: P2, the cancellation lane with §6.2 R2. Effort M.
   - Why this default: T4 puts reliability first, and T13 records a side-effecting call that broke off as `unknown` (§11.2, 1b.1). Cancelling running siblings when an unrelated node fails turns known outcomes into unknown ones; starting nothing new stops further side effects.
   - Risk: a failed run keeps spending on branches whose result is no longer used.
2. **Graph by reference before any added parallelism.** RFC-0004 M6 and RFC-0005 §10.2 already plan it for turns. Every concurrent command multiplies the embedded graph and parameter snapshot, so items 4 and 5 wait for it. Versioning: the turn's frozen input flag (`event_os_version=1`, RFC-0005 §10.4). When: P2, RFC-0005 Phase 4. Effort M (planned). Risk: activities read the graph from the database, so a generation's snapshot must stay immutable while its runs are open.
3. **Fix the delegation window on the uncontrolled path.** Start the next delegation after any outcome of the one being awaited, failure included, as the controlled path does (`agent_workflow.py:2666-2668`); never call `agent.finish_delegation` for a delegation that was never queued; correct the "sliding window" comment. The same patch refills on any completion (§12.4 rec 3): wait with `workflow.wait(..., return_when=asyncio.FIRST_COMPLETED)` on the started delegations and start the next as soon as any of them finishes, while still appending results in call order. Today both paths start the next delegation only after the one awaited in call order completes (`agent_workflow.py:2343-2348`, `:2666-2668`).
   - Versioning: `workflow.patched("agent-delegation-window-v2")` (command order changes after a failed delegation, and whenever a later delegation finishes first).
   - When: P2, the cancellation lane, not later: for any team lead it fails the whole lead AgentWorkflow. Effort S. Risk: low. §12.4 rec 3 refers here.
4. **Cap in-flight nodes per run.** Take at most N ready nodes (for example 100, under the batch page's "aim for 500"). It changes commands only for graphs wider than N; gate it with a frozen input flag. When: P3. Effort S. Risk: a very wide graph runs slower; no graph today comes near the bound (unmeasured).
5. **Read-only batches run concurrently** (default chosen; §24 lists the alternative). When every call in one model response is a tool whose plugin declares `annotations["readonly"] is True`, has no approval spec and parsed cleanly, start them together (at most 8) and append results in call order; any other batch runs one at a time as today. `agent.prepare_payload` records each tool's annotations in the prepared tool index, and a per-generation flag, `tool_concurrency_version=1` in the generation's `resource_manifest`, frozen into the run's input, turns the batching on (RFC-0005 §24.1 rule 2).
   - Versioning: `tool_concurrency_version=1` (command order changes for such batches). No run-level version is recorded in `agent.prepare_payload`'s result, since T18 leaves rule 2 unamended; only new generations batch.
   - When: P3, after item 2. Effort M.
   - Risk: the batch is only as safe as the plugins' `readonly` annotations; each concurrent call must also be admitted by execution control (`begin_action`) so Stop's checkpoint counts it.

**Remarks:**
- Keep the FIRST_COMPLETED loop: the docs' chunked "controlled parallelism" makes every chunk wait for its slowest member.
- Several completions in one activation are handled in insertion order, one per loop pass, with new starts in between; replay produces the same order.
- RFC-0004 §8.1 item 3 (replace `_wait_any_complete` with `workflow.wait(..., return_when=FIRST_COMPLETED)`) changes no commands; do not bundle it with a command change.
- Pending activities stay far below 2,000 (graph width; sequential tools). Preflights are one per `assign_task` call in a turn, which the model controls; a per-turn cap (for example 20) is cheap insurance (model behaviour unverified).
- Per-call error isolation already holds in AgentWorkflow: a failed tool becomes an error tool message (`agent_workflow.py:2574-2628`).
- Tracing: activity ids are node ids (`workflow.py:847`) and stable tool ids (`agent_workflow.py:2350-2354`).
- An execution whose task completion exceeds 4 MB is terminated ("the Temporal Service terminates the Workflow Execution", https://docs.temporal.io/troubleshooting/blob-size-limit-error). T6 keeps agent transcripts in LLM-step inputs, but only one LLM step per agent runs at a time; item 5 is where copies multiply.

**Sketch** (items 1 and 5, SDK 1.30.0; helper names are illustrative):

```python
# (1) MachinaWorkflow: one failure policy, under §6.2 R2's marker (drain_v1)
if errors and (self._execution_control.enabled or drain_v1):
    ready = []                       # start nothing new; running nodes finish
...
if not result.get("success"):
    errors.append(error_info)
    if not self._execution_control.enabled and not drain_v1:
        break                        # pre-patch histories: abandon running siblings

# (5) AgentWorkflow: a model batch that is entirely read-only runs at once
readonly_batch = (
    run_input.get("tool_concurrency_version") == 1         # the generation's flag, frozen at admission
    and 1 < len(calls) <= _MAX_READONLY_BATCH              # 8
    and all(_is_readonly_call(tool_index, call) for call in calls)
)
if readonly_batch:
    handles = []
    for index, call in enumerate(calls):
        info = tool_index[call["name"]]
        await self._execution_control.begin_action()      # Stop's checkpoint counts each call
        handles.append(workflow.start_activity(
            _tool_activity_name(info), args=[_tool_payload(index, call)],
            activity_id=_tool_activity_id(info["tool_node_id"], iteration, index),
            **_tool_options(info)))
    outcomes = await asyncio.gather(*handles, return_exceptions=True)
    for index, (call, outcome) in enumerate(zip(calls, outcomes)):
        self._execution_control.end_action()
        _append_tool_outcome(messages, index, call, outcome)   # model order; today's error text
else:
    ...                              # today's sequential loop
```

### 6.4 Pick First (Race)

**Docs:** https://docs.temporal.io/design-patterns/pick-first. Run several activities that reach the same goal, take the first result and cancel the rest.
- Python: `asyncio.create_task(workflow.execute_activity(...))` per candidate; `done, pending = await workflow.wait([task1, task2], return_when=asyncio.FIRST_COMPLETED)`; cancel each task in `pending`; return `done.pop().result()`.
- Activities heartbeat and re-raise `asyncio.CancelledError`: "Activities do not cancel instantly (they wait for the next heartbeat)".
- Cleanup variant: `cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED`, then await each pending task, swallowing `CancelledError`.
- Pitfalls: missing heartbeats; "Not waiting for cancellation cleanup"; "Ignoring errors from the winning Activity"; "Forgetting to cancel remaining Activities", which "continue running indefinitely".
- Not a fit when "Activities have side effects that should not be cancelled".

**SDK:** `workflow.wait` returns ordered lists and rejects coroutines ("Passing coroutines is forbidden, use tasks explicitly", `temporalio/workflow/_asyncio.py:89-118`). Handles have `.cancel()`. `ActivityCancellationType` is TRY_CANCEL (default), WAIT_CANCELLATION_COMPLETED or ABANDON (`temporalio/workflow/_activities.py:71-82`), unchanged in 1.34.0, which also de-duplicates a repeated activity cancel command. The worker throttles heartbeats to min(0.8 x heartbeat timeout, 60 s) by default (`temporalio/worker/_worker.py:125-126`); OpenCompany does not override it.

**OpenCompany today:**
- **No race in workflow code:** no `workflow.wait` or `asyncio.wait` in any workflow. The one hit in a workflow module (`server/services/temporal/employee_job_workflow.py:20`) is inside the activity `deliver_employee_job`.
- **In-activity races follow the docs.** `_until_chat_run_stops` races the model call against a 1-second database stop watcher, cancels the loser in `finally` and gathers both, so the winner's error surfaces through `task.result()` (`server/services/temporal/agent_activities.py:419-444`). Its docstring states the docs' trade-off: "a heartbeat-borne cancel arrives too late to stop a stream mid-sentence". `_await_with_llm_heartbeats` (`:378-398`) and `deliver_employee_job` (`employee_job_workflow.py:11-27`) clean up the same way.
- **Chat Stop** is a cooperative race against a grace timer: the run moves to `stopping`, the model step returns its partial answer and unstarted tools are skipped; 45 to about 75 seconds later the watchdog cancels the run's workflow (`server/services/chat/ledger.py:598-639`, `:714-728`). The shape is right; the cancel reaches only the root, whose agent child is then terminated (§6.2).
- **The chat run timeout cancels nothing.** A running run past `max_running_s` (86,400 s) is failed as `timed_out` (`ledger.py:766-767`), and `_sweep_one` is not even given `temporal_cancel` (`:701-703`; compare `:722-726`). `is_stopping` matches only `stopping` and `stopped` (`:642-645`), so the LLM stop watcher and the tool skip never see the `error` run, and `post_reply` still writes for a terminal run (`:334-370`). The workflow keeps running, may keep billing model calls, and the freed lane admits a second run on the same conversation: the page's "Forgetting to cancel remaining Activities", at run level. Detection takes 24 hours plus up to one 30-second sweep; the clock restarts at Resume and paused controlled runs are skipped (`:742-765`).
- **Uncontrolled failure:** the first failure "wins" and siblings are not cancelled (§6.3).
- **Pi driver, the docs' cleanup variant:** `_await_request` waits for "operation recorded or driver done"; `_stop_driver(cancel=True)` cancels the driver activity, which runs with WAIT_CANCELLATION_COMPLETED, and awaits it (`server/services/temporal/pi_workflow.py:180-216`).
- **Not races, correctly:** the Undo window is settled by a database claim at the approval revision, and a started send is never cancelled (`server/services/temporal/approved_tool_call_workflow.py:41-75`), as the page advises for side effects. Workspace Take over is a process-local lease (`server/nodes/browser/_live_control.py:300-352`, `server/nodes/mobile/_control.py:30-50`). `wait_for_delegation` lets only the waiter time out and never cancels the child (`server/services/handlers/tools.py:946-976`).
- **Planned in RFC-0005:** turn deadlines per class (RFC-0005 §13.1: 15, 15, 30 and 60 minutes), the turn as a child with REQUEST_CANCEL and WAIT_CANCELLATION_COMPLETED (RFC-0005 §10.1), Interrupt that cancels a turn "at its next merge point" with its inputs returned to pending (RFC-0005 §13.4), and "Turn hangs" (RFC-0005 §23). Missing:
  1. Turn n+1 must not start until turn n's handle is done, or two turns share one conversation key (I4).
  2. A bound on that wait, and a last resort when cleanup hangs. A workflow can only request cancellation of another workflow; termination needs an activity that calls the client.
  3. The winner may be a failure: a failing turn and a firing deadline in one activation need a deterministic rule.
  4. A turn's clocks must stop while the generation is paused (the chat watchdog restarts its window at Resume; Temporal timers keep running through a pause, `workflow.py:148-155`) and while the turn is parked (T11). That makes each an updatable timer. T19 also turns the per-class deadlines into "slow turn" alerts; cancelling is left to a 24-hour no-progress backstop, a default (§24.1; recommendation 2).
  5. Cancelling a turn helps only if the turn passes the cancel to its agents (§6.2 R1 and R2).
  6. A cancelled turn's agent can still finish a model step and save after turn n+1 started; the consumption transaction must check that the saving turn still holds the conversation.

**Verdict:** Adopted with deviations. In-activity races follow the docs; the run-level timeout leaves its loser running. The RFC-0005 turn as a child follows the docs; its turn-cancel race is Planned in RFC-0005 (deviates) on the six points above.

**Recommendation:**
1. **A chat run that hits its 24-hour limit cancels its workflow** (default chosen; §24 lists the alternative). Pass `temporal_cancel` to `_sweep_one` and, in its `timed_out` branch, cancel `run.temporal_workflow_id` and `run.temporal_run_id` as `_sweep_stopping` does (`ledger.py:714-728`). This 24-hour limit covers today's chat runs. RFC-0005 turns are bounded by the 24-hour no-progress backstop (a default, §24.1; recommendation 2), which never counts time parked, paused or waiting for the owner; from RFC-0005 Phase 5 (`scheduler_version=1`) a parked turn waits at most 7 days (§11.8; T18, T19).
   - Versioning: none (client code).
   - When: Phase 1b (1b.14), after 1b.12, so the agent child receives a cancel rather than termination. Effort S.
   - Why this default: T4. A run that keeps going after its run row ended is a second writer on the conversation and an unbounded spend.
   - Risk: an answer that would have arrived after 24 hours is lost; the owner sees `timed_out`, as today.
2. **Specify the turn's clocks and its cancel race in RFC-0005** (RFC-0005 §10.2, §13.1, §13.4, §18.1, §23; sketch):
   - T19: the per-class turn deadlines (15, 15, 30 and 60 minutes for C1 to C4) become "slow turn" alerts. The entity raises an attention item and a metric, and cancels nothing.
   - A backstop cancels a turn after 24 hours with no completed step (default chosen, §24.1). It never counts time the turn is parked, paused or waiting for the owner (an approval draft, the owner's browser). Each completed step restarts it.
   - Both clocks are named deadlines on §8.4's one live timer, and both stop while the generation is paused or the turn is parked, so parked time never counts toward either. Parking exists only for generations with `scheduler_version=1` (RFC-0005 Phase 5, T18); a parked turn waits at most 7 days, then its input is dead-lettered with an attention item (§11.8, T19).
   - A turn that finished, even by failing, in the same activation as the backstop wins.
   - Otherwise the entity cancels the turn, waits up to `_TURN_CANCEL_GRACE` (2 minutes) for it to close, then terminates the turn and its agent children by id through an activity; only then may turn n+1 start. Interrupt uses the same wait.
   - Versioning: none (ConversationWorkflow is a new type).
   - When: P2, RFC-0005 Phase 4, after §6.2 R2; the parked-time rules arrive with parking in Phase 5 (T18). Effort M.
3. **Fence consumption by the turn's lease.** The transaction that saves a transcript and advances `consumed_seq` (RFC-0005 §9.5 step 5) checks that the saving turn still holds its `sched_leases` row (RFC-0005 §13.3); a cancelled or terminated turn's late save is refused. Versioning: none (a database transaction). When: P2, RFC-0005 Phase 4 design text. Effort S. Risk: the refused step's model spend is already settled; only its transcript is dropped.

**Remarks:**
- Heartbeat delivery bounds every cancel. Node activities heartbeat every 30 seconds from a background task when start-to-close exceeds the heartbeat timeout (`server/services/plugin/base.py:1248-1263`), and the 2-minute heartbeat timeout gives a 60-second throttle. A cancel rides the next heartbeat response, so it reaches a node activity about 0-60 seconds after it is sent (about 30 seconds on average), and an LLM step (1-minute heartbeat timeout, `server/services/temporal/agent_workflow.py:99`) within about 48 seconds. That is why the chat path polls a 1-second stop flag.
- The 24-hour start-to-close (`workflow.py:167`) also ends a wedged node attempt, after which the retry policy may run it again (3 attempts by default). A drain or a turn-cancel wait still needs its own bound.
- Side effects: on Stop, Interrupt or the no-progress backstop, an in-flight send is cancelled like any other activity. T13 then records it as `unknown` (1b.2, §9.3; the redo guard is 1b.1, §11.2), unless its plugin is idempotent or uses a provider idempotency key.
- Record a replay fixture of the chat Stop grace path before changing any cancel path (1b.11).

**Sketch** (the turn's clocks inside ConversationWorkflow: RFC-0005 Phase 4, parking from Phase 5; SDK 1.30.0):

```python
# self._deadlines is §8.4's helper: one live timer for all of the entity's named deadlines.
turn = await workflow.start_child_workflow(
    "MachinaWorkflow", turn_input, id=f"turn-{self._conv_id}-{self._turn_no}",   # number from admission (§8.2)
    parent_close_policy=workflow.ParentClosePolicy.REQUEST_CANCEL,
    cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
)
now = workflow.now()
self._deadlines.set("slow_turn", now + _SLOW_TURN[self._turn_class])  # 15/15/30/60 min: alert only
self._deadlines.set("no_progress", now + _NO_PROGRESS)                # 24 h, no completed step (§24.1)
while True:
    due = await self._deadlines.wait(turn.done)
    if turn.done():                # checked first: a turn that ended, even by failing, wins
        outcome = self._turn_outcome(turn)
        break
    if "slow_turn" in due:         # a latency target (T19): attention item and metric, no cancel
        self._deadlines.set("slow_turn", None)
        await self._report_slow_turn()
    if "no_progress" in due:
        turn.cancel()              # its agents clean up only with §6.2 R1 and R2
        try:
            await workflow.wait_condition(turn.done, timeout=_TURN_CANCEL_GRACE)
        except asyncio.TimeoutError:   # a workflow cannot terminate another one: an activity does
            await workflow.execute_activity(
                "conversation.terminate_turn", {"turn_id": turn.id},  # the turn and its agent children
                start_to_close_timeout=timedelta(seconds=60), retry_policy=DEFAULT_ACTIVITY_RETRY)
        outcome = "no_progress"    # RFC-0005 §23, "Turn hangs": the turn is cancelled, then the retry policy
        break
self._deadlines.set("slow_turn", None)
self._deadlines.set("no_progress", None)
self._release_turn(outcome)        # only now may turn n+1 start (I4)

# Handlers only move these deadlines; the helper re-arms on the new minimum (§8.4):
# - a completed step (the progress RFC-0005 §18.1's stuck-turn alarm watches): no_progress = now + 24 h;
# - a pause, or a park (T11, from Phase 5): both are removed and their remaining time is kept;
# - a wait for the owner (an approval draft, the owner's browser): no_progress alone, the same way;
# - when the pause, park or wait ends: each is set again at now + its remaining time.
```

## 7. Workflow messaging

Three pages cover how callers talk to running workflows. Terms used below:
- A **Signal** is a one-way message to a workflow. The server records it and returns; the caller gets no answer and the workflow may handle it later.
- An **Update** is a message that the workflow validates, records and answers. A **validator** is a read-only check that runs before the Update is written to history; when it raises, the Update is rejected and leaves no history.
- **Signal-With-Start** (SWS) starts a workflow if none is running under the id and delivers a Signal to it, in one call. **Update-With-Start** does the same with an Update.
- **Continue-as-new** closes the current run and starts a fresh one under the same id with new input, to keep history bounded.

### 7.1 Workflow messaging patterns (overview)

**Docs:** https://docs.temporal.io/design-patterns/workflow-messaging-patterns. A chooser: "You want to send a message without checking whether the Workflow is running": Signal with Start. "You need a result back from the Workflow, with validation": Request-Response via Updates. "You receive many events and want to process them in batches": Event Accumulator. The rules come from the three pattern pages and the concept pages:
- **Order.** In each activation, handlers run before the main method, including the first activation after Signal-With-Start or continue-as-new (https://docs.temporal.io/handling-messages).
- **Exceptions.** A Signal handler that raises `ApplicationError`, or lets an activity exhaust a finite retry policy, fails the whole workflow; any other exception fails the Workflow Task, and "the Workflow will get stuck". In an Update handler, a validator exception rejects the Update; an `ApplicationError` or an exhausted activity or child fails only that Update; in the Python SDK any other exception fails the Workflow Task too (only a `FailureError` or `asyncio.TimeoutError` fails just the Update).
- **Dedupe.** Updates are deduplicated by Update id "per Workflow run"; Signals need an application key. Neither request ids nor Update ids survive continue-as-new.
- **Updates need a worker.** With none polling, "Your request will be retried by the SDK Client indefinitely. You can use asyncio.timeout to impose a timeout" (https://docs.temporal.io/develop/python/workflows/message-passing).
- **Limits.** 10 in-flight and 2,000 total Updates per run; the server suggests continue-as-new at 90% of the 2,000. `history.maximumSignalsPerExecution` defaults to 10,000; the dynamic-config reference says further Signals fail with InvalidArgument, while the event page says the execution is terminated. The docs disagree; either way, continue as new first. Throughput: "below 5/sec per workflow instance" (accumulator page).
- **Closing.** "Complete all handlers before using Continue-as-New"; "Use Continue-as-New from your main Workflow Definition method".

**SDK:** everything this section needs exists in 1.30.0: `Client.start_workflow(..., start_signal=, start_signal_args=, id_reuse_policy=, id_conflict_policy=, priority=, rpc_timeout=)` (`temporalio/client/_client.py:527-565`); `execute_update_with_start_workflow` and `WithStartWorkflowOperation`; `WorkflowUpdateStage`; `workflow.all_handlers_finished`; `HandlerUnfinishedPolicy`; `workflow.current_update_info`; `Info.is_continue_as_new_suggested`, `get_current_history_length` and `get_current_history_size`; `Priority(priority_key, fairness_key, fairness_weight)`. 1.33.0 made continue-as-new from an Update handler fail the Workflow Task (OpenCompany never does it) and moved Update-With-Start to Core's long-poll timeout. 1.34.0 removes the private `start_workflow` keywords `callbacks`, `links` and `request_id` (unused here) and adds `WorkflowAlreadyStartedError.first_run_id` (server 1.32 or later) and `Info.original_execution_run_id`.

**OpenCompany today:**
- **Events are Signals routed through Visibility.** `dispatch.emit` runs one Visibility query per event and sends a plain `on_event` Signal by workflow id (`server/services/events/dispatch.py:155-165`, `:251-258`); it drops the event when the query fails or finds nothing (`:163-165`, `:186-191`). The consumer is the per-generation controller (`server/services/temporal/workflow_control_workflow.py:336-359`).
- **Control Updates with validators** on eight workflow types: the controller, MachinaWorkflow (`server/services/temporal/workflow.py:249-263`), DelegatedTaskWorkflow (`server/services/temporal/agent_workflow.py:500-514`), AgentWorkflow (`:759-773`), EmployeeJobWorkflow (`server/services/temporal/employee_job_workflow.py:78-92`), PiInlineDelegationWorkflow (`server/services/temporal/pi_inline_delegation.py:329-343`), CronTriggerWorkflow (`server/nodes/scheduler/cron_scheduler/_workflow.py:88-101`) and PiCodingAgentWorkflow (`server/services/temporal/pi_workflow.py:49-77`).
- **Updates without validators:** Workspace `submit` and `reset` (`server/services/temporal/workspace_tasks_workflow.py:82-193`); Pi `register_driver`, `submit_operation` and `assistant_ready` (`pi_workflow.py:79-107`); the controller's `pause_admissions` (`workflow_control_workflow.py:255-264`).
- **Update-With-Start:** Workspace tasks only (`server/services/node_invocations.py:44-74`).
- **Other Signals:** the controller's `register_trigger`, `reset` and legacy `pause` and `resume`; Pi `pause` and `resume`, which run an activity (`pi_workflow.py:64-73`); MachinaWorkflow's `on_event`, which buffers into `_matched_events` that nothing reads (`workflow.py:427-447`).
- **Signal-With-Start:** not found in `server/`.
- **`@workflow.init`:** 9 decorators in 8 production files.
- **Request-response outside Temporal:** chat admission (`server/services/chat/ledger.py:252-331`); approval decisions by compare-and-swap on a revision (`server/services/approvals/decisions.py`); Stop and Resume intent rows (`server/services/deployment/handlers.py:2295-2310`); Apply claims, re-driven every 10 seconds by `recover_applies` (`server/services/employees/safe_apply.py:285-289`, `server/services/employees/activation.py:55-72`).

**Verdict:** Adopted with deviations. Control needs an answer, so it uses Updates; events need none and must queue while paused, so they use Signals. That matches the chooser. Delivery is at-most-once through Visibility (RFC-0004 H1, replaced in RFC-0005 Phases 2 and 4), and the client-side rules are missing (§7.3).

**Recommendation:**
1. **Adopt a handler checklist** as rules in §5, for every new or changed handler: (a) Signal handlers only buffer or set flags, and drop a malformed payload with a log line instead of raising; (b) every rejection that needs no I/O lives in a validator; (c) every client Update call runs under a deadline (§7.3 R1, 1b.15) and passes a stable id where it is retried and the handler cannot fail transiently after acceptance; (d) continue-as-new or completion happens only in the main method, after `wait_condition(all_handlers_finished)`, with no `await` between the final buffer check and the close; (e) every Signal payload carries an idempotency key, carried across continue-as-new; (f) Signals and Updates are counted per run, not only events; (g) owner actions (Start, Stop, Resume, Apply, approval decisions, Interrupt) go through intent rows and the deliverer (T10, §7.3 R7), and any other request path that sends an Update (Workspace submit) does so under a deadline and answers "pending" when it expires.
   - Versioning: none. The lintable parts ship in 1b.40 (§6.1 rec 1, §17.18). When: Phase 1b. Effort S. Risk: none at run time.

**Remarks:**
- The accumulator page says "Signal handlers must not call activities or yield to the scheduler"; the Python page allows `async def` handlers that run activities. Both hold: Python permits it, but such a Signal handler fails the whole workflow if its activity exhausts a finite retry policy. Pi `pause` and `resume` are that case (`DEFAULT_ACTIVITY_RETRY`, 3 attempts, `server/services/temporal/_retry_policies.py:62-67`).
- RFC-0004 §8.2 already lists deadlines, stable ids, validators for Pi and Workspace, and counting Signals and Updates; none of it has landed.

### 7.2 Signal with Start

**Docs:** https://docs.temporal.io/design-patterns/signal-with-start. One atomic call starts the workflow if it is not running and delivers the Signal; if it runs, only the Signal is delivered. Python: `client.start_workflow(Wf.run, id=..., task_queue=..., start_signal="add_item", start_signal_args=[...])`. The call returns when the server has accepted the Signal, not when it is processed.
- Best practices: derive the workflow id from the entity; "Implement Signal idempotency. Track processed operation IDs"; "Use WorkflowInit. Initialize state before Signals are delivered (... Python's `__init__`)"; "Handle unbounded execution. Use Continue-As-New"; "Use ALLOW_DUPLICATE_FAILED_ONLY for entity Workflows" (while the same page says both ALLOW_DUPLICATE and ALLOW_DUPLICATE_FAILED_ONLY "work well"); include operation ids; return early on duplicates.
- Pitfalls: no idempotency ("Signals can be delivered more than once (for example, client retries)"); "Unbounded history growth ... will hit the 51,200 event or 10K Signal limit"; "Losing pending Signals on Continue-As-New. Drain all pending Signals before calling Continue-As-New, and pass unprocessed ones as input to the new execution"; expecting a return value. On a Signal that races continue-as-new, "Temporal prevents this race".
- Not a fit for "one-time operations (use REJECT_DUPLICATE policy instead)" or for request-response that needs synchronous confirmation ("use Update with Start").

**SDK:** present in 1.30.0 as listed in §7.1. Each call gets a fresh `request_id` (`temporalio/client/_impl.py:327`) that the caller cannot set, in 1.30.0 or 1.34.0, so the server deduplicates only the SDK's own RPC retries. `id_conflict_policy` is passed through as UNSPECIFIED (`_impl.py:332-335`); the server's default for Signal-With-Start is to use the running execution. `priority` travels with the start request (`_impl.py:355-356`), so it most likely applies only when the call starts a run (the server side was not verified). `rpc_timeout` bounds the single RPC; a deadline error is not retried. Signal-With-Start cannot use eager start. The workflow-side `workflow.signal_with_start_workflow` is experimental; RFC-0004 Q12 keeps preview features off.

**OpenCompany today:**
1. **Controllers correctly avoid Signal-With-Start.** A controller's existence is lifecycle state, so an event must never create one (RFC-0004 §10.1 item 2); `dispatch.emit` signals by id only (`server/services/events/dispatch.py:251-258`).
2. **The controller is started empty, then signalled: the "start, then signal" shape the accumulator page warns about.**
   - `_start_controller` starts `WorkflowControlWorkflow` with state, revision and scope but no triggers (`server/services/deployment/handlers.py:614-657`). The deployment manager then sends one `register_trigger` Signal per trigger node (`server/services/deployment/manager.py:952-982`).
   - Until a trigger is registered, `on_event` finds no matching spec and drops the event without queueing it (`server/services/temporal/workflow_control_workflow.py:336-359`). Before the first registration the `ControlEventTypes` search attribute is absent, so dispatch treats the controller as match-all and does signal it (`dispatch.py:175-184`, `:217-248`).
   - Nothing guards the window. The generation stays `starting` until after `_await_deployment_setup` (`handlers.py:2106-2156`), and producers deliver to `starting` generations: chat answers "now" for `starting` (`server/services/chat_thread.py:72`, `:84-92`), and webhook routes read every row in `WORKFLOW_CONTROL_ACTIVE_STATES`, which includes `starting` (`server/nodes/trigger/webhook_trigger/_routes.py:36-46`). Events in that window are lost.
   - A crash between the start and the Signals leaves a live but empty controller. `_converge_interrupted_start` fails the generation when no trigger registered but treats a partial registration as complete (`handlers.py:1708-1745`, the test `if triggers_registered or not graph_has_triggers`); the boot re-arm then registers the rest.
   - The controller already supports start-with-state: `run` seeds `triggers` from its input (`workflow_control_workflow.py:488-533`), and for execution-control v1 `@workflow.init` seeds the same carried state before any handler runs (`:62-92`). Every continued run already takes this path.
3. **Legacy controllers seed nothing in `__init__`** (`workflow_control_workflow.py:90-92`), so an `on_event` in the first activation after continue-as-new is dropped. Every generation started on current code is v1 (`server/services/deployment/control.py:160`), and T5's fresh start fails every older generation, which owners Reset and Start as v1. No live path remains after the first pinned release.
4. **RFC-0005 relay (planned, RFC-0005 §9.4).** `client.start_workflow("ConversationWorkflow", id=<entity id>, start_signal="deliver", start_signal_args=[batch], id_reuse_policy=ALLOW_DUPLICATE, priority=Priority(priority_key=<class>, fairness_key=<employee id>))`; at most one Signal-With-Start in flight per stream and 4 per entity per second; `delivered_seq` is committed after acceptance; the entity drops `seq <= applied_seq`, carried across continue-as-new (RFC-0005 §9.5). Ids `conv-<workflow_id>-g<gen>-<lane>-<thread>`; the supervisor `emp-<workflow_id>-g<gen>` is started at Start (RFC-0005 §10.1) and rebuilt in place when lost (rec 5, T20). Passivation: "owner 24 h, customer 30 min, others 10 min", and "the server rejects completion while new Signals are unhandled" (RFC-0005 §11). Subscriptions are compiled "in the same transaction that moves the generation to `running`" (RFC-0005 §9.3).

**Verdict:**
- Today: Missing and beneficial, as start-with-state rather than Signal-With-Start. It gives the atomicity Signal-With-Start provides without letting events create controllers.
- RFC-0005: Planned in RFC-0005 (deviates). It follows the docs on deterministic ids, a stable idempotency key (`seq`, a gap-free high-water mark, which is better than a seen-set), batching, a rate below 5 per second, buffer-only handlers, `@workflow.init`, a continue-as-new trigger well below 10,000 Signals (4,000) and commit after acceptance. It misses the start input after passivation, the Reset race, the instant EG12 measures from, and a deadline on the call.

**Recommendation:**
1. **Start the controller with its triggers in the start input.** The deployment manager builds every trigger spec first and `_start_controller` passes them as `triggers` (sketch). `register_trigger` stays only for the boot re-arm. For a listener id it already knows, it changes nothing today (`workflow_control_workflow.py:327-329`); after 1b.32 (§17.8) it refreshes only that listener's sanitized `parameter_snapshot`.
   - Versioning: none in workflow code. Old histories replay with their recorded input; the input shape is the one every continued run already uses. New first runs move their search-attribute upsert and poll starts into the first task, which the golden identity test does not lock.
   - When: P2, the controller-hardening lane, with H10's slim carry. It is not needed for EG12: RFC-0005 delivers events only from `running` (RFC-0005 §9.3; rec 3), which comes after the Signals were accepted, and Signals are applied in arrival order. It closes the window where today's Visibility path loses chat messages, webhooks and unscoped events sent to a `starting` controller, and the crash window. Effort M.
   - Risk: specs embed the graph and parameter snapshot (RFC-0004 H10). N specs in one start input can exceed the 2 MB payload limit, so Start fails at once instead of at the first rollover (`ControllerContinuationTooLarge`, `:705-711`). Land the slim carry with it. Each spec's snapshot must be the one 1b.31 (§17.8) sanitizes on the Start path, so no secret enters the start input; rec 1 lands after it.
2. **Pass the stream cursor as the entity's start input.** The Signal-With-Start call in RFC-0005 §9.4 passes no workflow argument. Pass only the stream cursor, `consumed_seq` (as `{"applied_seq": consumed_seq}`, §8.2 rec 2), read in the same transaction as the batch, and seed `applied_seq` from it in `@workflow.init` on every start, a restart after passivation included, because the `deliver` handler of a new run runs before `run`. Workflow arguments take effect only when the call starts a run, which is exactly the passivated case. Without it, a restarted run cannot tell consumed inputs from new ones. The turn number is not in the start input: §8.2 rec 2 allocates it in the admission transaction (RFC-0005 §13.3), which no restart can reuse, so turn ids, agent child ids and spend keys stay unique after passivation. Versioning: none (ConversationWorkflow is a new type). When: P2, RFC-0005 Phase 4. Effort S. Risk: the relay must read the cursor in the batch's transaction, or a stale `consumed_seq` re-applies inputs.
3. **Define EG12 by the Start commit.** Compile subscriptions in the transaction that creates the `starting` generation, and hold its streams with reason `starting` until the move to `running` releases them. An event accepted after Start committed is then delivered once the generation runs; today's RFC text would mark it `no_consumer`. Versioning: none (database and router). When: P2, RFC-0005 Phase 2 (design text and router). Effort S. Risk: a Start that fails must release or dead-letter its held streams.
4. **Close the Reset race.** The relay's generation check (RFC-0005 §9.4 step 1) and its Signal-With-Start are not atomic; a Reset committed between them lets ALLOW_DUPLICATE start an entity for a dead generation. `admission.acquire` (RFC-0005 §13.3) also checks the generation, and a dead generation's entity completes without a turn. The alternative is for Reset to take the stream leases. Versioning: none (new types). When: P2, RFC-0005 Phase 4. Effort S. Risk: none known.
5. **State the reuse policies.** ALLOW_DUPLICATE is right for the entity: passivation completes the run, and the page's ALLOW_DUPLICATE_FAILED_ONLY would refuse to restart a completed entity. The supervisor follows T20 (RFC-0005 §10.1):
   - A lost supervisor (terminated or failed) is rebuilt in place from the database, keeping chat and Context.
   - Start starts it with `REJECT_DUPLICATE` under the new generation's id (RFC-0005 §10.1). Each rebuild passes `id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY`, so the id can be taken again only after a terminated, cancelled, timed-out or failed run, and `id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING`, so a rebuild that meets a live run attaches to it.
   - After 3 rebuilds within an hour it stops rebuilding and raises an attention item that asks the owner to Reset.
   - Today's v1 controller has no such rebuild: a vanished one fails its generation and only Reset recovers it (`handlers.py:780-835`, `:1912-1914`).
   - Versioning: none (new types). When: P2, RFC-0005 Phase 4. Effort M.
   - Risk: the policy also accepts a start after a cancelled run, so the rebuild first checks in the database that the generation is still live.
6. **Priority applies at start.** An entity started by a C4 input would keep priority 4 for its own workflow tasks after a C1 input arrives. §13.3 rec 3 settles this: entities are keyed by lane, not by their first input, and turn children carry their inputs' class (RFC-0005 §11 step 5). The fairness key in the same `Priority` is the employee id, which never changes; fairness itself is on from the fresh-start release (T17, §13.4). Versioning: none (a start option). When: P2, RFC-0005 Phase 4, with §13.3. Effort S.
7. **Put a deadline on the relay's calls.** `rpc_timeout=timedelta(seconds=10)` on every Signal-With-Start (Phase 4) and every plain Signal to today's controllers (Phase 2). A timed-out call commits nothing; the relay re-sends and the entity deduplicates by `seq`. Versioning: none (client code). Effort S. Risk: none known.

**Remarks:**
- A client-retried Signal-With-Start that straddles the entity's continue-as-new reaches both runs; only the carried `applied_seq` makes that harmless. The controller's carried `seen_event_ids` play the same role (`workflow_control_workflow.py:695`).
- Passivation: "Temporal prevents this race" is stated for continue-as-new. For completion it is documented elsewhere: when new Signals arrive while a Workflow Task runs and the workflow tries to close, "The Temporal Service fails the Workflow Task and schedules a new one" (https://docs.temporal.io/references/workflow-task-errors, Unhandled Command). It protects the entity only if it checks its buffer after `wait_condition(all_handlers_finished)` and closes with no `await` in between (§7.4 A3). A `deliver` accepted after the close starts a new run, which is correct with ALLOW_DUPLICATE and recommendation 2.
- Signal budget: the 4,000-Signal trigger must count every Signal type (`deliver`, `inputs_consumed`, `turn_suspended`, `wake`) in the handlers; the SDK has no Signal-count API. The relay window (32 inputs or 512 KB, RFC-0005 §9.4 step 3) keeps a stuck entity from absorbing Signals toward the 10,000 server limit.
- EG7 ("10,000 messages ... cost fewer than 1,000 Signals per entity run") holds only for bursts: at one message per second every message is its own Signal. Define it as a burst test: the 10,000 messages are appended before the relay runs, so batches are full (16 inputs, about 625 Signals).
- Testing: run Signal-With-Start against `WorkflowEnvironment.start_local()` with the pinned CLI. Test the passivation race by sending `deliver` while the entity's completing task is in flight; time skipping cannot interleave it. Add an entity history to the replay corpus as soon as Phase 4 records one.

**Sketch** (SDK 1.30.0):

```python
# (1) Today's controller: triggers travel in the start input (caller-only change)
specs = {spec["listener_id"]: spec for spec in await manager.build_trigger_specs(control)}
await client.start_workflow(
    "WorkflowControlWorkflow",
    args=[{**control_args, "triggers": specs}],  # v1 @workflow.init seeds them before any handler
    id=control.controller_workflow_id, task_queue=queue, search_attributes=attrs,
)

# (2) RFC-0005 relay: the workflow argument is used only when this call starts a run
seed = {"applied_seq": consumed_seq}   # the stream cursor, read in the same transaction as the batch

await client.start_workflow(
    "ConversationWorkflow", seed, id=entity_id, task_queue="machina-tasks",
    start_signal="deliver", start_signal_args=[batch],
    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,  # passivation completes the run
    priority=Priority(priority_key=lane_key, fairness_key=employee_id),  # keyed by lane (§13.3 rec 3)
    rpc_timeout=timedelta(seconds=10),
)

@workflow.defn
class ConversationWorkflow:
    @workflow.init
    def __init__(self, seed: dict) -> None:
        self._applied_seq = int(seed.get("applied_seq") or 0)   # deliver runs before run(): seed here
        self._pending: list[dict] = []   # turn numbers come from admission (§8.2 rec 2), not from here

    @workflow.signal
    def deliver(self, batch: dict) -> None:       # buffer only
        for row in batch["rows"]:
            if row["seq"] > self._applied_seq:
                self._pending.append(row)
                self._applied_seq = row["seq"]
```

### 7.3 Request-Response via Updates

**Docs:** https://docs.temporal.io/design-patterns/request-response-via-updates. `@workflow.update` plus `@<update>.validator`: the validator runs first and, if it raises, the Update is rejected with nothing written to history; otherwise the handler changes state and returns a typed result, and "The Update is recorded in Workflow history before the response is returned".
- Best practices: "Validate early"; typed exceptions; "Return quickly"; "Track Update IDs only across Continue-As-New ... Update ID deduplication is scoped to a single Workflow Run"; "Set timeouts"; atomic state changes.
- Pitfalls: long operations in the handler ("Offload long-running work to Activities"); exceeding 2,000 total Updates; "Not setting Update timeouts. Without a client-side timeout, the caller blocks indefinitely if the Worker is unavailable"; set "a stable, business-meaningful `updateId` when you want a retried Update-with-Start to attach to an existing in-flight Update"; "Updates require a Worker to be online and responsive. For fire-and-forget operations, use Signals instead".
- Limits: about 2 MB per argument or result; each Update adds events toward 51,200; "a maximum of 10 in-flight Updates per Workflow execution and a maximum of 2,000 total Updates".
- The page's Python sample calls `uuid.uuid4()` inside the handler; workflow code must use `workflow.uuid4()`.

**SDK:** everything is in 1.30.0, including `start_update(..., wait_for_stage=ACCEPTED)` for long handlers and `WorkflowUpdateRPCTimeoutOrCancelledError`. A `WithStartWorkflowOperation` is single-use ("WithStartWorkflowOperation cannot be reused", `temporalio/client/_client.py:1185-1187`), so a retried Update-With-Start builds a new one. One trap, read in the source: `rpc_timeout` does not bound `execute_update`. It bounds each request while the Update is sent, but after acceptance the SDK polls for the outcome without it (1.30.0 `temporalio/client/_workflow.py:840` and `temporalio/client/_impl.py:802`; 1.34.0 `:846` and `:889`), and both loops repeat while the server answers without an outcome. Only `asyncio.timeout` (or `asyncio.wait_for`) around the whole call bounds it. The SDK turns the resulting cancellation into `WorkflowUpdateRPCTimeoutOrCancelledError` whose cause is `CancelledError`, which looks the same as the caller being cancelled; check the timeout scope's `expired()` to tell them apart.

**OpenCompany today:**
- **What follows the docs.**
  - Revision-idempotent control Updates whose validators raise non-retryable `ApplicationError` (`server/services/temporal/workflow_control_workflow.py:161-174`, `:196-198`, `:234-253`, `:311-323`; `server/services/temporal/execution_control.py:58-106`), and chain identity checked by `first_execution_run_id` (`server/services/temporal/execution_control.py:72-74`).
  - Stable ids for retried control calls: `control:{revision}:admission`, `:checkpoint` and `:release` (`server/services/deployment/execution_control.py:80-81`, `:120-121`, `:150-152`, `:181-183`); reconcile and pause or resume ids (`server/services/deployment/handlers.py:1263`, `:2313-2315`, `:2405`); safe-apply ids derived from a database claim (`server/services/employees/safe_apply.py:107`, `:199`, `:231`, `:254-255`, `:264`).
  - Cross-workflow Updates only through activities, as the Python guide requires (`server/services/temporal/execution_control_activities.py:17-27`; `server/services/temporal/pi_activities.py:106-112`).
  - Continue-as-new only from main loops after `all_handlers_finished` (`workflow_control_workflow.py:595-716`; `server/services/temporal/workspace_tasks_workflow.py:195-215`; `server/services/temporal/agent_workflow.py:2992-3031`; `server/services/temporal/pi_workflow.py:382-387`); the controller requests rollover from Update handlers instead of continuing there (`workflow_control_workflow.py:588-593`).
  - Update-With-Start for Workspace tasks uses `USE_EXISTING` and `@workflow.init`, because "Update-With-Start can invoke a handler before run()" (`server/services/node_invocations.py:52-59`; `workspace_tasks_workflow.py:39-53`).
  - Update-id dedupe across continue-as-new is done with business keys, as the page advises: revisions, bounded `submissions` and `resets` carried across the Workspace rollover (`workspace_tasks_workflow.py:43-46`, `:210-215`), and Pi digests in Update ids (`pi_activities.py:263`, `:297-298`).
- **Deviations.**
  1. *No deadline on any app-process Update call:* Workspace Update-With-Start (`node_invocations.py:52`); `_update_controller_state` (`handlers.py:708-712`); every call in `transition_generation` (`server/services/deployment/execution_control.py:80`, `:108`, `:120`, `:150`, `:181`, `:187`); safe-apply (`safe_apply.py:107`, `:199`, `:231`, `:254`, `:255`, `:264`). The socket waiter awaits `asyncio.shield(task)` with no timeout (`handlers.py:134-139`). Only `_finish_versioned_transition` handles `WorkflowUpdateRPCTimeoutOrCancelledError` (`handlers.py:1423-1430`); its "bounded RPC poll" comment is wrong without a deadline. Worker-side calls are bounded by their activity's 60-second start-to-close (`server/services/temporal/execution_control.py:210-229`).
  2. *Random Update ids on retried calls.* `execution_control.register` and `unregister` pass no `id=` (`execution_control_activities.py:17-27`), so each of the 3 attempts of `DEFAULT_ACTIVITY_RETRY` is a new Update; `transition_generation`'s `unregister_execution` and its final controller `set_control_state` pass none either (`server/services/deployment/execution_control.py:108-111`, `:187-190`). The handlers are idempotent (`workflow_control_workflow.py:219-247`), so this costs history and in-flight slots, not correctness.
  3. *Rejections inside handlers.* Workspace `submit` checks admission before and after its lock and `reset` checks its request id (`workspace_tasks_workflow.py:83-97`, `:147-148`), so every refusal ("queue full", "resetting", "admission changed") is an accepted-then-failed Update in history. The caller compensates with a fresh random Update id per submit (`node_invocations.py:246-252`). Pi `register_driver`, `submit_operation` and `assistant_ready` have no validators (`pi_workflow.py:79-107`). RFC-0004 L3.
  4. *Update results copy the whole controller status.* Every controller Update returns `self.status()`, including the full `live_roots` map (`workflow_control_workflow.py:369-388`), into the controller's history and again into each child's activity result. A recorded entry costs about 135 bytes with a 25-character id (`server/tests/temporal/fixtures/0.3.1/controller_push.json`); real `<slug>-<label>-<event id>` ids make it about 200. With L live runs, each fired run writes about 2 x L entries. One caller depends on it: after a Resume, the UI's in-flight count walks `live_roots` from the final `set_control_state` result (`server/services/deployment/execution_control.py:25-27`, `:187-202`; `handlers.py:1183-1190`).
  5. *Large arguments.* `replace_graph` sends the whole graph snapshot as one Update argument (`safe_apply.py:199`) and the handler copies it into every trigger spec (`workflow_control_workflow.py:303-306`), bounded by the 2 MB event limit (RFC-0004 H10).
  6. *Signal handlers that do request-response work.* Pi `pause` and `resume` run `coding.pi_child_control` (30 minutes, 3 attempts) when the Pi run has live Task Manager children (`pi_workflow.py:64-73`; `server/services/temporal/pi_control.py:36-55`); an exhausted activity there fails the whole Pi workflow. Only the legacy run-pinned fan-out for unversioned generations sends them (`handlers.py:915-950`), while Pi runs only in v1 generations, so this is latent. Run pinning also loses the Signal when the target continues as new between listing and signalling (NOT_FOUND counts as "already gone", `:903-912`).
  7. *`register_trigger` indexes its payload* (`spec["listener_id"]`, `spec["workflow_type"]`, `workflow_control_workflow.py:325-334`). A malformed spec raises a non-`ApplicationError` and wedges the controller in a Workflow Task failure loop (RFC-0004 M5). The producer is internal (`server/services/deployment/manager.py:971-981`): low likelihood, total impact.
- **Budgets in practice.** A fired push run costs the controller 1 Signal, 2 Updates and about 40 history events; a cron firing costs 4 Updates and about 22 events (fixtures `controller_push.json`, `cron_schedule.json`). The server suggests continue-as-new at 4,096 events or 4 MiB by default, so a controller rolls over after about 100 push runs (200 Updates) or about 190 cron firings (about 750 Updates), far below 2,000. The tightest budget is 10 in flight; whether an eleventh concurrent register fails its run was not verified (the server's error code for that limit is not in the downloaded sources).
- **Owner actions today.** Stop and Resume already write an intent row first: the generation row moves to `pausing` or `resuming` with a new revision, a background task runs the transition (`handlers.py:98-105`), and `_reconcile_control` re-drives a stale one when the next status read or the boot pass calls it (`:1223-1238`). The socket handler then waits for that task (`:134-158`). An approval decision commits by compare-and-swap, then starts the send workflow from the request path and reverts the decision if Temporal is unavailable (`server/services/approvals/handlers.py:139-159`); `recover_sends` restarts sends that never started (`server/services/approvals/reconcile.py:76-89`). A pending approval draft keeps Stop in `pausing`: every node activity, an approval gate included, is admitted as an action (`server/services/temporal/workflow.py:865-868`), reviewed delivery is one too (`server/services/temporal/employee_job_workflow.py:132-136`), and `wait_for_checkpoint` waits until no action is admitted (`server/services/temporal/execution_control.py:114-119`); hence "A checkpoint can remain open on admitted work for months" (`handlers.py:1601-1602`).

**Verdict:** Adopted with deviations. The control plane is the docs' pattern almost verbatim; the gaps are the client-side rules (deadlines, stable ids) and validator placement in two newer workflows. RFC-0005's supervisor, `interrupt_turn` and ApprovalWorkflow `decide` are Planned in RFC-0005 (follows the docs): validators and Updates as the page describes, but RFC-0005 never says who sends them, and sending them from a request path would break its own I2. T10 settles the sender.

**Recommendation:**
1. **R1. Bound every Update call.** A helper `execute_update_bounded` (new `server/services/temporal/update_calls.py`, sketch) wraps each app-process call in `asyncio.timeout`: about 15 seconds for Workspace Update-With-Start, 30 seconds for controller and safe-apply calls. On expiry it raises `UpdatePending`: the durable intent row or the deterministic id makes a retry safe. `_finish_versioned_transition` treats `UpdatePending` like today's pending branch (keep the intent and the closed admission). Safe-apply treats it as "leave the claim for `recover_applies`" and must not run its rollback Updates. The socket waiter in `_await_versioned_transition` waits at most 10 seconds, then answers with the pending snapshot while the shielded task continues. A Workspace submission (`_controller_update`, `server/services/node_invocations.py:44-74`) answers "submitted, still pending: check the task list" when its deadline passes, and also when the server refuses the Update because 10 are already in flight (that error's code was not verified). Submitting again is safe: the run id derives from the caller's `submission_id` (`node_invocations.py:182`), and the controller deduplicates by run id and fingerprint (`workspace_tasks_workflow.py:89-94`).
   - Versioning: none (client code).
   - When: Phase 1b (1b.15). Effort S.
   - Risk: a pending answer instead of a final one; the UI already handles pending control states, and the task list shows a Workspace submission that later succeeds.
2. **R2. Stable Update ids for execution control.** `execution_control.register` and `unregister` pass `id=f"exec:{workflow_id}:{first_execution_run_id}:register"` (or `:unregister`), built from the payload they already carry (`server/services/temporal/execution_control.py:194-205`). In `transition_generation`, `unregister_execution` uses `control:{revision}:unregister:{workflow_id}:{chain}` and the final controller release uses `control:{revision}:release-controller:{epoch}`; the epoch is needed because the loop can send that call more than once for one revision. Retries then attach to the admitted Update instead of queueing a new one.
   - Versioning: none (activity body and client code).
   - When: P2, the controller-hardening lane. It is not urgent: the handlers are idempotent and the server suggests continue-as-new near 4,096 events, so the 2,000-Update cap is never approached; the cost is extra events and in-flight slots on rare retries. Effort S.
   - Risk: a stable id makes the server return the first outcome to every retry in the run. That is safe here: the handlers repeat their validators' checks (`workflow_control_workflow.py:206-253`), and a failure after acceptance (a closing controller, a conflicting chain) is final anyway.
3. **R3. Validators for request-independent checks.** Move Workspace's first `_check_admission` pass and the reset request-id check into validators, and Pi's argument checks into validators. Keep the in-lock re-checks in the handler, and keep random submit Update ids: those checks can still fail after acceptance, and a stable id would make the server return that cached transient failure to every retry. With validators, most refused submits leave no history.
   - Versioning: none; validators do not run on replay, and handlers keep their checks for histories that recorded the accepted-then-failed form.
   - When: P3. Effort S. Risk: none at run time.
4. **R4. Small acknowledgements from controller Updates.** Return `state`, `revision`, `participant_state`, `participant_revision`, `membership_epoch` and `producers_held`, and leave `live_roots` to the `status` Query. The Resume path must then query `status` before `count_generation_actions` walks the roots, or the in-flight count drops every descendant.
   - Versioning: `workflow.patched("controller-slim-update-result-v1")` inside the handlers; whether replay compares Update results was not verified, so assume it might.
   - When: P2, the controller-hardening lane, with a fixture recorded under the marker. Effort S.
   - Risk: any other caller that reads `live_roots` from an Update result breaks; the Resume count is the only one found.
5. **R5. Harden `register_trigger`.** Validate the spec shape in the handler and drop a malformed spec with an error log line. 1b.32 (§17.8) first adds a snapshot refresh for known listeners to the same handler; the shape check covers that path too.
   - Versioning: none: valid specs take the same path, and only histories already stuck in a Workflow Task failure change, which is the docs' remedy ("until the exception is fixed, for example by a code change").
   - When: P2, the controller-hardening lane. Effort S. Risk: a dropped spec leaves its trigger unregistered until the next re-arm; the error line is the only signal until RFC-0005's attention items exist.
6. **R6. Remove the dead Signal paths.** Delete Pi `pause` and `resume` together with the legacy pause and resume fan-out (`handlers.py:915-950`), and MachinaWorkflow's unused `on_event` (`workflow.py:427-447`).
   - Versioning: removing `on_event` changes no commands (an unhandled Signal is buffered). Removing the Pi handlers does change commands (no more `coding.pi_child_control` after those Signals); it is replay-safe only because no recorded history has such a Signal reaching a Pi run, and after T5's fresh start no unversioned generation can send one.
   - When: P3, or Phase 10 with the legacy path. Effort S. Risk: none; no live generation reaches these paths.
7. **R7. Owner actions through intent rows and a deliverer** (T10). Start, Stop, Resume, Apply, approval decisions and Interrupt write a database intent and answer at once; a background deliverer sends the Update by workflow id, with a stable id from the intent (`intent:{id}:{revision}`) and a per-attempt deadline, until the Update is applied, refused or its target is gone; the UI shows "applying" until a broadcast settles it (sketch). For Start and Apply the deliverer drives their steps from the intent row (§10.2).
   - Handlers stay idempotent by revision, because Update-id dedupe is per run and the supervisor continues as new daily and is rebuilt in a new run after a loss (T20).
   - Long handlers, such as the supervisor's Stop barrier that fans out to every live turn, use `start_update(wait_for_stage=ACCEPTED)` and fetch `result()` later by id.
   - T12: Stop completes while an approval draft waits. A decision taken while the employee is stopped is delivered at once, so the owner sees it recorded, and its send waits for Resume. Today that means the send is not started while the generation is paused (1b.5), and the Stop checkpoint stops counting approval waits; §9.6 carries both changes.
   - Today's code moves toward it without versioning: Stop and Resume answer after the bounded wait of R1, and a periodic sweep re-drives stale `pausing` and `resuming` rows instead of waiting for the next status read; an approval decision no longer reverts when Temporal is down, because `recover_sends` restarts sends that never started, and 1b.6 (§10.2) runs it from the 10-second recovery loop instead of once per process.
   - Versioning: none (client code, and the new supervisor and ApprovalWorkflow types).
   - When: P2, RFC-0005 Phases 4 (supervisor, Interrupt) and 7 (ApprovalWorkflow), plus the no-versioning steps above with §9.6. Effort M.
   - Risk: the owner sees "applying" for as long as Temporal or the target is down; the deliverer must surface a stuck intent as an attention item.

**Remarks:**
- Long handlers are allowed but hold in-flight slots: `wait_for_checkpoint` waits for admitted actions to drain, and "A checkpoint can remain open on admitted work for months" (`handlers.py:1601-1602`). Workspace `reset` waits for child cancellation and a 120-second activity while holding the admission lock (`workspace_tasks_workflow.py:166-191`); a submit that arrives during a reset fails its first admission check at once, and only submits already waiting on the lock when the reset began stay in flight, then fail the re-check.
- If a handler is stuck in a Workflow Task failure, Updates not yet accepted get `FAILED_PRECONDITION` and accepted ones stay durable (Python message-passing guide); the deliverer of R7 keeps retrying both.
- Replay coverage: `controller_stop_resume`, `controller_push`, `controller_poll`, `controller_queue_read`, `controller_spill_rollover`, `workspace_task`, the `pi_*` and `paused_*` fixtures exist. R4 needs a new fixture with its marker; R1 to R3 and R5 change no commands.

**Sketch** (SDK 1.30.0):

```python
# server/services/temporal/update_calls.py (new)
class UpdatePending(Exception):
    """Our deadline passed. The Update may still apply: keep the intent and retry."""

async def execute_update_bounded(handle, update: str, arg: Any, *, update_id: str | None, seconds: float) -> Any:
    try:
        async with asyncio.timeout(seconds) as scope:   # bounds the send loop and the outcome poll
            return await handle.execute_update(update, arg, id=update_id)
    except WorkflowUpdateRPCTimeoutOrCancelledError as exc:
        if scope.expired():                              # our deadline, not the caller's cancellation
            raise UpdatePending(update) from exc
        raise
    except TimeoutError as exc:
        raise UpdatePending(update) from exc

# T10: the only sender of owner Updates (Stop, Resume, decide, interrupt_turn)
async def deliver_due_intents(database, client) -> None:
    for intent in await database.claim_due_intents(limit=50):           # leased, oldest first
        handle = client.get_workflow_handle(intent.target_workflow_id)   # by id, never a run id
        try:
            result = await execute_update_bounded(
                handle, intent.update_name, intent.payload,
                update_id=f"intent:{intent.id}:{intent.revision}", seconds=30)
        except UpdatePending:
            continue                                    # still "applying"; the next pass retries
        except WorkflowUpdateFailedError as exc:        # the validator or the handler refused it
            await database.settle_intent(intent, "refused", error=str(exc.cause))
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
            await database.settle_intent(intent, "target_gone")
        else:
            await database.settle_intent(intent, "applied", result=result)
        await broadcast_intent_settled(intent)           # the UI leaves "applying"
```

### 7.4 Event Accumulator

**Docs:** https://docs.temporal.io/design-patterns/event-accumulator. One workflow per group key, with a deterministic id ("optionally" with the accumulation period, for example `accumulator-order-123-2026-05-14`); producers always use Signal-With-Start; Signal handlers only append to a buffer.
- Main loop: wait with a sliding inactivity timeout (`workflow.wait_condition(..., timeout=timedelta(seconds=10))`), drain and dedupe by a stable item key, run one batch activity on timeout or `flush`, then complete. When `is_continue_as_new_suggested()` and the buffer is empty, continue as new with the items and the sorted seen keys.
- Best practices: a dedupe key in every payload; "Size the inactivity timeout to your domain's quiet period"; carry items and seen keys into continue-as-new ("If you omit the seen-keys list, any signal delivered to both the old and new run during the Continue-As-New handoff will be processed again"); "Keep signal handlers fast and side-effect free"; a flush Signal "for testing and operational runbooks"; "Keep producer signal rate below 5/sec per workflow instance"; the 10,000-Signal limit per run (stated "on Temporal Cloud"; self-hosted servers have the same default).
- Pitfalls: random ids; "Calling start followed by a separate signal"; no dedupe key; a timeout too short; forgetting state at continue-as-new; activities in handlers; "Assuming workflow completion means all events were captured"; "Signal rate too high to allow Continue-As-New to complete", which needs "a brief window (~100 ms) with no unhandled signals"; "Not draining the signal queue before calling Continue-As-New".
- The page sets no maximum on the window: with arrivals faster than the timeout, a pure sliding window never flushes. Its only bounds are the explicit flush and a period encoded in the id.
- **The Python sample is wrong for the Python SDK.** It reads `wait_condition` as returning a bool (`timed_out = not await workflow.wait_condition(...)`). In 1.30.0 and 1.34.0 it returns `None` and raises `asyncio.TimeoutError` on timeout (`temporalio/workflow/_context.py:920-943`; 1.34.0 `:1016-1049`), and an `asyncio.TimeoutError` that escapes `run` fails the workflow (`temporalio/worker/_workflow_instance.py:1824-1839`). As written, the sample flushes on the first wake-up and fails on the first quiet window. Catch `asyncio.TimeoutError` instead.

**SDK:** all in 1.30.0: `wait_condition` with a timeout, `is_continue_as_new_suggested`, `continue_as_new(args=...)`, `all_handlers_finished`. The 1.34.0 docstring adds that switching a timeout between `None` and a positive value is a nondeterministic change and that `0` "should not be used"; under CPython 3.12 a timeout of 0 or less raises at once without a timer.

**OpenCompany today:**
- **WorkflowControlWorkflow is the accumulator, keyed by deployment generation.**
  - *Follows the docs.* `on_event` only buffers and dedupes on `listener_id:event_id` (`server/services/temporal/workflow_control_workflow.py:336-359`). The last 4,096 seen keys are kept (`:32`, `:535-539`), and queued keys stay deduplicated after they age out (`:72-76`, `:353-354`, `:556-566`). Continue-as-new carries the queue head (256 events or 512 KB), the seen keys and the triggers' poll baselines (`:684-704`). Larger queues spill to the database, deduplicated by `sha256(controller:listener:event_id)` (`server/services/temporal/controller_queue.py:9-40`), and are read back idempotently by request id (`:43-71`). Before continuing as new, the v1 path re-spills new arrivals, waits for `all_handlers_finished` and re-checks with no await before the close (`workflow_control_workflow.py:647-665`): the page's drain-and-recheck guard. A paused controller keeps accepting and rolls over with its queue (`:355-359`). Duplicate child starts count as accepted (`:468-472`).
  - *Not a batch accumulator.* The main loop takes one event at a time and starts one child run per event (`:431-483`). For each event, the controller's own history records the trigger filter as an activity when the trigger has filter parameters (`server/services/temporal/trigger_listener_workflow.py:337-343`, `:176-178`), two broadcast activities (`:425-434`, `:503-512`) and the child start (`:494-497`): about 40 events per fired event, and about 10 per event the filter rejects.
  - *Coarse group key.* Telegram and WhatsApp events are unscoped (`server/nodes/telegram/_events.py:160`; `server/nodes/whatsapp/_events.py:193`) and reach every controller subscribed to the type (`server/services/events/dispatch.py:175-184`); webhook events are scoped to the workflows that declare the path (`server/nodes/trigger/webhook_trigger/_routes.py:95-96`). Every message on a busy account becomes a Signal, plus a filter activity, in every subscribed employee's controller, against the 5-per-second guidance.
  - *A poison event blocks the queue.* Any spawn failure other than `WorkflowAlreadyStartedError` puts the event back at the head and sleeps 5 seconds, forever (`workflow_control_workflow.py:464-480`; RFC-0005 §4.2). A filter activity that fails deterministically qualifies.
  - *Carry budget.* Seen keys are `<slug>-<label>:<event id>` strings of about 60-85 bytes with JSON quoting, so 4,096 keys take 0.25-0.35 MB of the 1.9 MB carry limit (`:38`, `:695`). Above the limit, `ControllerContinuationTooLarge` fails the controller (`:705-711`).
  - *Legacy gaps.* Runs without execution control or without the accumulator marker never fence handlers before continue-as-new (`:605-609`, `:682-683`), and Signals that arrive during the final spill can be marked seen but neither spilled nor carried (`:666-669`). The legacy listener resets its seen ids at continue-as-new and carries only the newest 256 events (`trigger_listener_workflow.py:123-127`, `:291-303`). No generation started on current code takes these paths, and T5's fresh start fails every older one.
- **RFC-0005 (planned).** Debounce with a sliding window plus an absolute cap: owner chat 0.4 s / 1.5 s, customer chats 2 s / 8 s, groups 3 s / 10 s, others none, with "`workflow.wait_condition` with a timeout" driving it (RFC-0005 §11). Relay window of 32 inputs or 512 KB, batches of 16 inputs or 256 KB, 4 Signal-With-Start calls per entity per second (RFC-0005 §9.4). Entity continue-as-new "only between turns, when suggested or past 4,000 events or 4,000 Signals", carrying `applied_seq`, `turn_no` and at most 256 KB of pending inputs, "any excess goes back to the database as `hold(window)`" (RFC-0005 §10.2). Partitioning by conversation stream (RFC-0005 §12), matching and filters in the database before any Temporal call (RFC-0005 §9.3), dedupe in `bus_dedupe` for 7 days (RFC-0005 §9.2).

**Verdict:**
- Today: Adopted with deviations. Buffering, dedupe, carry and drain match the docs, and the spill goes beyond them. It does not batch, it filters inside history, it has no bound on a poison event, and its group key, a whole employee, is coarser than the docs' "partition by a finer-grained key".
- RFC-0005: Planned in RFC-0005 (follows the docs). It improves on the page twice: the absolute cap fixes the starvation of a pure sliding window, and a gap-free `seq` high-water mark is a bounded dedupe key. The gaps are A3 to A6 below.

**Recommendation:**
1. **A1. Dead-letter poison events in today's controller.** §11.6 rec 2 owns this change: the dead letter ships in one patch with its spawn backoff (5 s, 30 s, 2 min, 10 min). The dead-letter details: count spawn attempts per event, carried with the event. After 5 failures that are not duplicate starts, write the event to dead-letter rows and move on; the dead-letter write can reuse `controller.queue.spill` with `controller_id=f"{controller_id}:dead"`, which `controller.queue.read` never reads back, so no schema change is needed (sketch). Reset and workflow deletion also clear those rows; RFC-0005 Phase 8 adds the attention item.
   - Versioning: `workflow.patched("controller-spawn-backoff-v1")`, the one marker for the backoff and the dead letter, specified in §11.6 rec 2.
   - When: P2, the controller-hardening lane, next to C1, H8 and H10. T5 does not remove the need: generations started after the first pinned release and before Phase 4 run on this controller until Phase 10. Effort M.
   - Risk: an event that would have succeeded on a sixth try is dead-lettered; the dead row keeps it for replay.
2. **A2. Carry seen keys as digests.** Use the first 16 hex characters of `sha256(listener_id:event_id)` as the dedupe key everywhere, and convert carried raw keys when `_seed_carried_state` loads them: about 75% less of the carry budget. Keep carrying keys for spilled events too: the spill table forgets an event once `controller.queue.read` deletes its rows, after which only the carried key stops a producer retry.
   - Versioning: none. Dedupe decisions and commands stay the same, and continue-as-new inputs are not compared on replay; prove it by replaying `controller_spill_rollover`.
   - When: P2, the controller-hardening lane, folded into H10's slim carry. Effort S.
3. **A3. Specify the entity's close sequence** (RFC-0005 §10.2, §11 Passivation): `wait_condition(all_handlers_finished)`, re-check the pending buffer, no `await`, then complete or continue as new. Returning inputs to the database needs an activity, which is an `await`, so loop as the controller does (`workflow_control_workflow.py:651-665`). Versioning: none (a new type). When: P2, RFC-0005 Phase 4 (design text). Effort S. Risk: under a sustained flood the loop may not converge; the relay's rate cap is what lets it.
4. **A4. Carry cursors, not payloads.** When inputs above the 256 KB carry go back to the database, the carried `applied_seq` must be the highest seq actually retained; otherwise the re-delivered excess has `seq <= applied_seq` and the new run drops it as a duplicate. Simpler and closer to I1 ("The inbox and its cursors are the truth"): carry `consumed_seq`, `turn_no` and the debounce anchors only, set `applied_seq` to the consumed cursor, and have the relay rewind `delivered_seq` to it with one activity before the close. Versioning: none (a new type). When: P2, RFC-0005 Phase 4 (design text). Effort S. Risk: inputs delivered but not consumed are sent twice; the entity's `seq` check keeps that harmless.
5. **A5. Drive debounce by an absolute deadline.** `deadline = min(last_arrival + window, first_arrival + cap)`, then `wait_condition(new_input, timeout=deadline - workflow.now())` in a loop, returning when the remaining time is zero or less (sketch). `wait_condition` fixes its duration when called, and the Updatable Timer page says to work from absolute timestamps. In the entity, this deadline is one of the named deadlines that §8.4's shared helper keeps on one live timer. Carry `first_arrival` (RFC-0005's "debounce anchors"). Versioning: none (a new type). When: P2, RFC-0005 Phase 4. Effort S. Risk: none known.
6. **A6. No debounce for owner chat** (T9). An owner message starts its turn at once; messages sent while it runs merge at the agent's next step (B1), or start the next turn at once when the turn has no next step (RFC-0005 §11 step 6). The customer windows stay. The timers page says a Timer may be "as short as one second" and that workflows "should not rely on sub-second accuracy for Timers" (https://docs.temporal.io/workflow-execution/timers-delays); the SDK accepts shorter delays, so this is guidance, not an enforced floor. Of RFC-0005's owner values only the 0.4 s window was below one second; T9 removes both. Versioning: none. When: P2, RFC-0005 Phase 4 (RFC-0005 §11 table, §22 (c) and EG5). Effort S. Risk: two owner messages a second apart can make two turns; the owner accepted that for lower latency.
7. **A7. Add a flush message.** `wake(flush=True)` ends a debounce early, for tests and runbooks, as the docs recommend, so EG5 is testable without waiting real time. Versioning: none. When: P3. Effort S.
8. **A8. Keep each controller below 5 Signals per second in Phase 2.** The relay that delivers to today's controllers sends several events per Signal through a new `on_events` handler that takes a list, or limits each controller's rate. Versioning: none (adding a Signal handler changes no commands). When: P2, RFC-0005 Phase 2. Effort S. Risk: none known.

**Remarks:**
- Rate: the docs' 5 per second is per workflow. The relay's 4 calls per second per entity, with up to 16 inputs each, satisfies it; today's controllers do not, because one controller absorbs a whole employee's traffic.
- The continue-as-new quiet window (about 100 ms with no unhandled Signals) is protected in the entity by between-turn continue-as-new and the relay's rate cap. In today's controller the spill-and-recheck loop converges only if arrivals pause briefly; under a sustained flood each pass adds spill activities to history.
- "Assuming workflow completion means all events were captured": with passivation and ALLOW_DUPLICATE, a late `deliver` starts the next run, as intended. Without the cursor input (§7.2 recommendation 2) the new run cannot tell consumed inputs from new ones.
- Testing: windows of seconds need the time-skipping environment (`WorkflowEnvironment.start_time_skipping()`, RFC-0004 §14). Sub-second windows are not meaningfully testable with time skipping, one more reason for A6.

**Sketch** (SDK 1.30.0):

```python
# (A1) WorkflowControlWorkflow.run, the spawn-failure branch (workflow_control_workflow.py:464-480).
# The dead-letter arm of §11.6 rec 2's patch; §11.6's sketch shows the backoff arm.
SPAWN_BACKOFF_PATCH = "controller-spawn-backoff-v1"
_MAX_SPAWN_ATTEMPTS = 5

except Exception as exc:  # noqa: BLE001 - per-event isolation
    if accumulator_v1 and isinstance(exc, WorkflowAlreadyStartedError):
        accepted = True
    elif queue_v2 and workflow.patched(SPAWN_BACKOFF_PATCH):
        attempts = int(event.get("_spawn_attempts", 0)) + 1
        if attempts >= _MAX_SPAWN_ATTEMPTS:
            await workflow.execute_activity(
                "controller.queue.spill",
                {"controller_id": f"{workflow.info().workflow_id}:dead",   # never read back
                 "events": [[listener_id, {**event, "_dead_reason": type(exc).__name__}]]},
                start_to_close_timeout=timedelta(seconds=60), retry_policy=QUEUE_PAGING_RETRY)
            accepted = True              # the dead row is the record; the queue moves on
        else:
            event = {**event, "_spawn_attempts": attempts}
            ...                          # §11.6 rec 2: behind the rest of the queue, after its backoff
    elif queue_v2 and not accepted:      # recorded histories: today's head-of-line loop
        self._events.insert(0, (listener_id, event))
        self._durable_prefix += 1
        await workflow.sleep(timedelta(seconds=5))

# (A5) ConversationWorkflow: sliding window with an absolute cap; deliver() bumps self._arrivals
async def _debounce(self, window: timedelta, cap: timedelta) -> None:
    first = self._first_arrival_at          # carried across continue-as-new
    while True:
        seen = self._arrivals
        remaining = min(self._last_arrival_at + window, first + cap) - workflow.now()
        if remaining <= timedelta(0):        # a timeout of 0 raises at once and arms no timer
            return
        try:
            await workflow.wait_condition(lambda: self._arrivals != seen, timeout=remaining)
        except asyncio.TimeoutError:         # wait_condition returns None; a timeout raises
            return
```

## 8. Entity and lifecycle

Three patterns keep long-lived workflows healthy: one workflow per business entity, continue-as-new to bound its history, and timers whose deadline can move. OpenCompany uses the first two today (the deployment controller, the Workspace controller, `AgentWorkflow`), and RFC-0005 adds more entities (supervisor, conversation, approval). The third exists only in RFC-0005's plan.

### 8.1 Entity and lifecycle patterns (category overview)

**Docs:** https://docs.temporal.io/design-patterns/entity-lifecycle-patterns. A selection guide with no API content of its own. It maps three needs to three patterns:
- "You are modeling something with an ongoing lifecycle": use an Entity Workflow "as the single source of truth for that entity".
- "Your Workflow runs long enough to grow a large history": apply Continue-As-New "to reset history while preserving state".
- "You need a wait that responds to new information": use an Updatable Timer "instead of a fixed sleep".

It points to the messaging patterns for the Signals and Updates that drive state changes, and to task orchestration to "decompose a large entity into child Workflows".

**OpenCompany today:**

| Need | Today | RFC-0005 |
|---|---|---|
| An entity with a lifecycle | `WorkflowControlWorkflow`, one per deployment generation; `WorkspaceTaskControllerWorkflow`, one per workflow; legacy `TriggerListenerWorkflow` and `PollingTriggerWorkflow`, one per (deployment, trigger) | `EmployeeSupervisorWorkflow` per employee generation, `ConversationWorkflow` per conversation stream, `ApprovalWorkflow` per approval (RFC-0005 §10.1) |
| A history that grows | the controller, `AgentWorkflow` and Pi, the Workspace controller, legacy listeners | supervisor, conversation entity, `AgentWorkflow` (RFC-0005 §10.2) |
| A wait whose deadline moves | none in workflow code | alarms, debounce, idle passivation, slow-turn alerts (T19), the no-progress backstop (a default, §24.1), restart backoff, budget period end (RFC-0005 §10.2, §11, §15) |
| Splitting an entity into children | controller to `MachinaWorkflow` (ABANDON) to `AgentWorkflow`; Workspace controller to `NodeInvocationWorkflow` (REQUEST_CANCEL) | entity to turn child (REQUEST_CANCEL) to `AgentWorkflow` |

**Verdict:** Not applicable (selection guide). One structural deviation is deliberate and should be stated once: the app database, not workflow state, is the source of truth (RFC-0005 I10). Chat runs, approvals, employee jobs and budgets are database rows changed by compare-and-swap, which is the page's "Database + Locks" row. That is justified. A Temporal database can be replaced by an empty one (RFC-0005 E16, confirmed by T5). Closed histories are kept 24 hours on today's dev server and 7 days in RFC-0005's plan (RFC-0005 §25.1), so history cannot be the audit trail.

**Recommendation:**
1. Add two rules to §5 (rules every workflow follows):
   - Any counter whose value names something outside the run (turn numbers, sequence cursors, any id that feeds an idempotency or spend key) lives in the app database, or is derived from it. It is never kept only in workflow state carried by continue-as-new.
   - Every entity start (first start, restart after passivation, rebuild after a failure) seeds its state from the database.

   Versioning: none (text). When: with this RFC. Effort S.

**Remarks:** The docs' "single source of truth" also means "audit trail in history". OpenCompany keeps its audit trail in the database instead: `approval_decisions` today, and `employee_timeline` from RFC-0005 Phase 8 (RFC-0005 §18.4). §8.2 gap a shows what breaks when the first rule is missed, and item 2 fixes it.

### 8.2 Entity Workflow

**Docs:** https://docs.temporal.io/design-patterns/entity-workflow. One workflow per long-lived business entity (an account, a device, an order), "using the entity ID as the Workflow ID", holding the entity's state in workflow variables for its whole life. Rules that matter:
- Use Updates (synchronous messages the workflow validates and answers) for operations "that require validation and a return value", Signals (asynchronous messages) "for asynchronous notifications that do not need responses", and Queries to "Expose state for monitoring and debugging".
- Handlers only change state. "All SDK docs explicitly warn: do not call Continue-As-New from Update or Signal handlers." The main method decides, after `workflow.all_handlers_finished()`.
- "Losing Update ID deduplication across Continue-As-New ... Carry processed IDs in the Continue-As-New input": the server deduplicates Update ids within one run only.
- "Keep state minimal. Store large data externally and reference it in the Workflow."
- "Handle deletion. Implement an explicit deletion or decommission Signal."
- "Set timeouts. Use Workflow execution timeout as a safety net." "Version carefully. Use Worker versioning."
- Fit and cost: not for "high-frequency updates (more than 100 per second per entity)"; "One Workflow per entity means you should consider costs at scale"; "The first operation after an idle period may have latency".
- Do not copy the page's Python sample: it calls `datetime.utcnow()` in workflow code (use `workflow.now()`), and after the handler fence it continues as new without re-checking `deleted`, so a delete that lands during the fence is lost.

**SDK:** Everything is in 1.30.0:
- `@workflow.init`, `@workflow.update` with `.validator`, `@workflow.signal`, `@workflow.query`, `workflow.wait_condition`, `workflow.all_handlers_finished`, `workflow.info().is_continue_as_new_suggested()`, `workflow.continue_as_new`, `workflow.current_update_info()`.
- Signal-With-Start (start the workflow if needed, then deliver a Signal, atomically) through `Client.start_workflow(start_signal=...)`; Update-With-Start through `execute_update_with_start_workflow`.
- `QueryRejectCondition.NOT_OPEN` (`temporalio/common.py:338-346`) and `WorkflowQueryRejectedError`, for a Query that must not answer from a closed run.
- Order inside one activation: the worker applies patches, then Signals and Updates, then everything else, and builds the workflow object on the first pass (`temporalio/worker/_workflow_instance.py:441-472`, `:2532-2540`). So handlers can run before `run()`, and state they need belongs in `@workflow.init`.
- 1.34.0 adds one rule: continue-as-new from an Update handler fails the workflow task (1.34.0 `worker/_workflow_instance.py:777-783`). OpenCompany never does this: all six `workflow.continue_as_new` calls sit in main loops.

**OpenCompany today:**
- **Deployment generation, `WorkflowControlWorkflow`.**
  - Id `workflow-control-<workflow_id>-g<generation>` (`server/services/deployment/control.py:147`). Started at Start with the default id reuse policy and an `EventWorkflowId` search attribute (`server/services/deployment/handlers.py:614-661`), and addressed by workflow id only (`:664-680`).
  - Messages (`server/services/temporal/workflow_control_workflow.py:94-388`): Signals `pause` and `resume` (legacy), `reset`, `register_trigger`, `on_event`; Updates `set_control_state`, `wait_for_checkpoint`, `register_execution`, `unregister_execution` and `replace_graph` with validators, and `pause_admissions` without one; Queries `status` and `execution_control_status`. The `reset` Signal closes it (`:274-280`, `:398`, `:438-439`).
  - A vanished controller. Only unversioned (legacy) generations are paused and later rebuilt with `WorkflowIDConflictPolicy.USE_EXISTING` (`handlers.py:788-825`). Every generation started on current code has `execution_control_version=1` (`control.py:160`). A vanished v1 controller fails its generation with `controller_execution_missing` (`handlers.py:826-840`, `:1913-1914`), and only Reset, which starts a new id, recovers it. That is what E16 prescribes. For RFC-0005's supervisor, decision T20 rebuilds a lost one in place and asks for Reset only after 3 rebuilds within an hour (item 5). CLAUDE.md's "Resume rebuilds the controller" is stale for v1 generations.
- **A workflow's Workspace, `WorkspaceTaskControllerWorkflow`.** Id `workspace-tasks-<sha256(workflow_id)>` (`server/services/node_invocations.py:27-28`), started by Update-With-Start with `USE_EXISTING` (`:44-74`). Updates `submit` and `reset`, Query `describe` (`server/services/temporal/workspace_tasks_workflow.py:55-193`). Its `run()` only ever continues as new (`:207-215`); there is no exit.
- **What follows the docs:**
  - The entity id is the workflow id.
  - Handlers set state or request a rollover; the main loop alone continues as new: "Wake the main run loop, which alone owns CAN and the handler completion fence, rather than continuing as new from an Update handler" (`workflow_control_workflow.py:588-593`). Handler fences come before continue-as-new (`:608-609`, `:661`, `:683`; the Workspace controller at `workspace_tasks_workflow.py:195-205`).
  - Carried state is seeded in `@workflow.init`: `workflow_control_workflow.py:62-92` (v1 controllers), `workspace_tasks_workflow.py:39-53` ("Update-With-Start can invoke a handler before run()"), `server/services/temporal/agent_workflow.py:746-757`.
  - Update-id deduplication across rollover is done in workflow code, as the docs require. Controller Updates carry revisions (`server/services/temporal/execution_control.py:84-106`) and deterministic ids of the form `control:{revision}:admission`, `:checkpoint` and `:release` (`server/services/deployment/execution_control.py:80-81`, `:120-121`, `:150-152`, `:181-183`). The Workspace controller carries its last 128 submissions and resets (`workspace_tasks_workflow.py:45-46`, `:210-215`).
  - Run-chain identity is checked by `first_execution_run_id` (`execution_control.py:71-73`), never by a cached run id.
  - The controller's queue overflow lives in the database and is read back in bounded pages (`server/services/temporal/controller_queue.py`).
- **Exception to id-only addressing.** The legacy pause and resume fan-out for unversioned generations signals run-pinned handles (`handlers.py:915-940`), so a target that continued as new between listing and signalling misses the Signal. v1 generations do not use this path.
- **Deviation 1: no decommission for the Workspace controller.**
  - Deleting a workflow runs Reset (`server/services/workflow_storage/deletion.py:50-60`). Reset calls `_reset_workspace_tasks` (`handlers.py:1145-1159`), which sends the controller's `reset` Update through Update-With-Start whenever the graph or its frozen snapshot has a Browser agent, Mobile Agent or Android tool node (`node_invocations.py:77-106`), or a controller already answers.
  - Nothing closes the controller afterwards. Reset's execution sweep (`handlers.py:843-900`) does not match it, and no workflow-deleted hook touches it.
  - So every deleted workflow whose deletion ran Reset and whose graph has such a node leaks one Running `workspace-tasks-*` execution, even if no Workspace task ever ran: Reset itself starts it.
  - The leaked run is idle: no timer, no workflow tasks, no history growth. The cost is Visibility and persistence clutter, one execution per deleted employee, not load.
- **Deviation 2: one hot entity per deployment.** Every push event, poll cycle and run registration of a generation passes through its controller: about 40 controller history events per fired push run, and four Updates (about 20 events) per cron firing (fixtures `controller_push.json` and `cron_schedule.json` in `server/tests/temporal/fixtures/0.3.1/`). The event-accumulator page asks for Signals "below 5/sec per workflow instance". RFC-0005 removes this for new generations: one entity per conversation, at most 4 Signal-With-Starts per entity per second (RFC-0005 §9.4).
- **Deviation 3: duplicated state.** Every trigger spec carries the whole graph and parameter snapshot (`server/services/deployment/manager.py:896-913`), rewritten by `replace_graph` (`workflow_control_workflow.py:303-306`), against "Keep state minimal" (RFC-0004 H10; §8.3 item 1).
- **Deviation 4: legacy entities restore control state in `run()`.** `TriggerListenerWorkflow` and `PollingTriggerWorkflow` have no `@workflow.init` and set their pause flag at the top of `run()` (`server/services/temporal/trigger_listener_workflow.py:131-135`, `:254`; `server/services/temporal/polling_trigger_workflow.py:95-98`, `:139`). Unversioned controllers reset their state there too (`workflow_control_workflow.py:90-92`, `:393-394`). A `pause` or `resume` Signal applied in the first activation of a continued run is overwritten. After T5's fresh start no unversioned generation survives, so only generation-less deployments can still run these types.
- **Justified deviations.** No workflow execution timeout: it spans the whole continue-as-new chain and would end months-long, possibly paused entities (RFC-0005 §27). No Worker Versioning: a one-process desktop cannot keep old builds polling, so patches and per-generation flags are used instead (RFC-0005 §10.4).

**RFC-0005 plan, before the amendments in §20 (RFC-0005 §10.1, §10.2, §11):** Ids, message types, "signal handlers that only buffer" and "continues as new only from the main loop, after `workflow.all_handlers_finished()`" follow the docs. Gaps:
- **a. Nothing seeds the entity's counters after passivation.** The entity completes after an idle period (RFC-0005 §11) and the relay restarts it with Signal-With-Start and `ALLOW_DUPLICATE` (RFC-0005 §9.4), passing no workflow argument. `applied_seq` and `turn_no` are only "carried across continue-as-new" (RFC-0005 §9.5 step 4, §10.2), so a restarted run starts both at zero.
  - Turn ids `turn-<conv id>-<n>` (RFC-0005 §10.1) then repeat, and so do the agent child ids built from them (`<root workflow id>-agent-<node id>`, `server/services/temporal/workflow.py:170-172`) and the agent's activity ids (`llm-step-{iteration + 1}`, `agent_workflow.py:3187`).
  - Spend reservations are keyed `{workflow_id}:{activity_id}` under a UNIQUE constraint (RFC-0005 §14), and send keys come from workflow and activity ids (RFC-0005 §21.3; RFC-0004 §9.4). A repeated turn id makes a new turn's reservations look like old ones, and can make a provider drop a new send as a duplicate.
- **b. Supervisor Updates must survive its rollover.** The server deduplicates Update ids per run, the supervisor rolls over daily (RFC-0005 §10.2), and T10's deliverer resends an Update with the same id until it is applied. RFC-0005 §10.2 gives `set_limits` and `resume_after_budget` no payload shape; a relative change ("add $10") applied by two runs is applied twice.
- **c. Cost per employee.** A supervisor that never passivates and wakes every 5 minutes for upkeep (RFC-0005 §10.2) wakes 288 times a day. A desktop's sticky cache (the worker's cache of recently run workflows) holds 50 workflows (`server/services/temporal/worker.py:76-88`; `company deploy` VMs hold 500 and Docker 100). With 100 employees on a desktop, most wakes replay the run's whole history. The docs: "consider costs at scale"; RFC-0005 principle 1: "Sleep by default; wake on an edge". (§8.4 item 5.)
- **d. A lost supervisor can only be Reset.** The supervisor starts with `REJECT_DUPLICATE` (RFC-0005 §10.1), which suits Start, because each generation has a new id, but leaves a lost supervisor no way back under its id; the signal-with-start page recommends `ALLOW_DUPLICATE_FAILED_ONLY` for entities. That matches E16 and today's v1 behaviour (a failed controller fails its generation with `controller_execution_missing` and needs Reset, which starts a new id), but Reset clears the generation's chat and Context. Decision T20 keeps `REJECT_DUPLICATE` for Start and rebuilds a lost supervisor in place with `ALLOW_DUPLICATE_FAILED_ONLY`; after 3 rebuilds within an hour an attention item asks for Reset (item 5).

**Verdict:** Adopted with deviations (today). Planned in RFC-0005 (deviates in a, c and d, misses b).

**Recommendation:**
1. **Decommission the Workspace controller.**
   - In `controller_status` (`server/services/node_invocations.py:31-41`), query with `reject_condition=QueryRejectCondition.NOT_OPEN` and treat `WorkflowQueryRejectedError` as "no controller". A Query also answers from a closed run, so without this a retried deletion would see the cancelled controller as present.
   - In `_reset_workspace_tasks` (`server/services/deployment/handlers.py:1145-1159`), when no controller is running, run the plugin runtime cleanup in process (the body of `workspace_tasks.reset_runtime`, `server/services/temporal/workspace_task_activities.py:7-47`) instead of starting a controller through Update-With-Start.
   - In `stop_workflow_for_deletion` (`server/services/workflow_storage/deletion.py:55-60`), after a successful Reset, cancel `workspace-tasks-<hash>` by id. Reset has already cancelled the children and the run waits in `wait_condition`, so the cancel ends it.

   Versioning: none (client side only). When: P2, with RFC-0005 Phase 4, which adds entity cancellation to Reset (RFC-0005 §23); it needs no versioning and may land earlier. Effort S. Risk: a Workspace submit racing the deletion starts a child; the child is cancelled through REQUEST_CANCEL when the controller closes.
2. **The entity-start rule (RFC-0005 Phase 4 design).**
   - The relay's Signal-With-Start passes the stream cursor (`consumed_seq`) as the workflow argument. The entity seeds `applied_seq` from it in `@workflow.init` on every start, a restart after passivation included. The server ignores the argument when a run is open.
   - The turn number is allocated in the admission transaction (`admission.acquire`, RFC-0005 §13.3, which already writes one `sched_leases` row per turn) and returned with the grant. No restart, rebuild or rollover can reuse it, so turn ids, agent child ids and spend keys stay unique after passivation.
   - The continue-as-new carry holds cursors, not payloads (§7.4 A4).
   - Not chosen: keeping `turn_no` on the `bus_streams` row and seeding it the same way.

   Versioning: new type, none. When: P2, Phase 4 (design text now). Effort S. Risk: none beyond the design.
3. **Absolute, revisioned supervisor Updates.** Every supervisor Update (`set_control_state`, `set_limits`, `resume_after_budget`) carries absolute values and the database row's revision. The validator rejects a revision at or below the applied one, which writes nothing to history. Required by T10, whose deliverer resends with a stable id. Versioning: new type. When: P2, Phases 4-5. Effort S.
4. **Legacy entities.** Stop starting generation-less deployments instead of fixing deviation 4 (default, §24): refuse the `deploy_workflow` WebSocket command without a generation (`server/services/deployment/handlers.py:163`, registered at `:2817`), remove the client's unused `deployWorkflowAsync` (`client/src/contexts/WebSocketContext.tsx:2766-2773`, exposed as `deployWorkflow`; it has no caller), and keep the two listener types for replay only. Versioning: none. When: P1, Phase 1b (1b.46). Effort S. Risk: a script or an old client that still calls `deploy_workflow`.
5. **Rebuild a lost supervisor in place** (decision T20; amends RFC-0005 §10.1).
   - Start keeps `id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE`, because each generation has a new id. Every rebuild uses `ALLOW_DUPLICATE_FAILED_ONLY`, which admits a new run under `emp-<workflow_id>-g<gen>` only when the last one ended terminated, cancelled, timed out or failed, never after a completed one. Both pass `id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING`, which returns the open run instead of failing, so a duplicate Start or rebuild attaches to it.
   - Whatever reaches the supervisor by known id (RFC-0005 I6) and finds it closed rebuilds it: boot re-arm, the T10 deliverer, or the upkeep sweep (§8.4 item 5). It does so only while the database shows the generation live, because the rebuild's reuse policy would also admit a run after a Reset that cancelled or terminated the old one.
   - The generation stays, so it keeps its chat and Context, which Reset would clear. The new run seeds its control state, alarms and budget from the database (§8.1 rule 2).
   - At most 3 rebuilds within an hour, counted in the database. The next loss stops rebuilding and raises an attention item that asks the owner to Reset.

   Versioning: new type (start options only). When: P2, RFC-0005 Phase 4 (attention items from Phase 8). Effort S. Risk: a cause the rebuild does not clear, such as an activity that fails for good, fails each new run; the limit turns that loop into one attention item.

**Remarks:**
- Limits per run: 10 Updates in flight and 2,000 in total (the server suggests continue-as-new at 90%), 10,000 Signals, 51,200 history events (https://docs.temporal.io/workflow-execution/event; https://docs.temporal.io/design-patterns/request-response-via-updates). The Workspace controller rolls over every 256 operations (`workspace_tasks_workflow.py:23`, `:202`), far inside the Update cap.
- The controller stays the hot path for every generation started before RFC-0005 Phase 4, until Phase 10 upgrades them.
- Item 1 changes no workflow code. Item 2 needs a test that passivates an entity and checks that the next turn's child and activity ids differ from the previous turn's. Item 5 needs a test that terminates a supervisor and checks that the rebuilt run keeps the generation, its chat and Context, and that a fourth loss within an hour raises the attention item instead of a rebuild.
- A rebuilt supervisor (item 5) is a new run, so the server's Update-id deduplication does not carry over from the lost run; item 3's revisions keep a resent Update from applying twice. The limit of 3 rebuilds an hour has the restart-intensity shape of RFC-0005 §18.2 (OTP MaxR/MaxT).
- Replay: the corpus (`server/tests/temporal/fixtures/0.3.1/`) covers controller push, poll, queue read, stop and resume, a spill rollover and one Workspace task. It has no Workspace controller rollover or decommission.

**Sketch:** (items 1 and 2)

```python
# Item 1: server/services/node_invocations.py
from temporalio.client import WorkflowQueryRejectedError
from temporalio.common import QueryRejectCondition
from temporalio.service import RPCError, RPCStatusCode

async def controller_status(workflow_id: str, *, client=None) -> dict | None:
    handle = (client or temporal_client()).get_workflow_handle(controller_id(workflow_id))
    try:
        # A Query also answers from a closed run: a closed controller is absent.
        return await handle.query(WorkspaceTaskControllerWorkflow.describe,
                                  reject_condition=QueryRejectCondition.NOT_OPEN)
    except WorkflowQueryRejectedError:
        return None
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return None
        raise

async def close_controller(client, workflow_id: str) -> None:
    """stop_workflow_for_deletion calls this after Reset succeeded."""
    try:
        await client.get_workflow_handle(controller_id(workflow_id)).cancel()
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise

# Item 2: ConversationWorkflow (RFC-0005 Phase 4), sandboxed (§17.18)
@workflow.defn(name="ConversationWorkflow")
class ConversationWorkflow:
    @workflow.init
    def __init__(self, cursor: dict) -> None:
        # The relay passes the stream cursor; "deliver" can run before run().
        self._applied_seq = int(cursor.get("consumed_seq") or 0)

    async def _start_turn(self, admission_request: dict) -> str:
        grant = await workflow.execute_activity(
            "admission.acquire", admission_request, start_to_close_timeout=timedelta(seconds=30))
        # The database allocates the number with the lease: never reused.
        return f"turn-{workflow.info().workflow_id}-{grant['turn_no']}"
```

### 8.3 Continue-As-New

**Docs:** https://docs.temporal.io/design-patterns/continue-as-new. Continue-as-new "completes the current Workflow execution and atomically starts a new one with the same Workflow ID"; state travels as arguments, and the new run (one execution in the chain of a workflow id) starts with an empty history. It keeps a workflow below the hard limits: terminated above 51,200 events, 2,000 Updates or 10,000 Signals (https://docs.temporal.io/workflow-execution/event). Rules that matter:
- Decide with `workflow.info().is_continue_as_new_suggested()`: "more reliable than a fixed iteration count". The page also says "Continue as new every 100-1000 iterations", which contradicts its own pitfall; a count is a backstop, not the trigger.
- "Pass minimal state": arguments "are serialized into the first event of the new execution", and the 2 MB payload limit applies.
- Call it from the main method only, after handlers finish: "don't call Continue-as-New from the handlers. Instead, wait for your handlers to finish" (https://docs.temporal.io/develop/python/workflows/continue-as-new).
- Pitfalls: "Forgetting to drain Signals before continuing ... carry pending Signals forward as arguments"; "Not accounting for Child Workflows ... By default, children are terminated. If children must survive, set ParentClosePolicy to ABANDON and pass their Workflow IDs"; "Caching Run IDs ... Always use Workflow ID without a Run ID"; "Catching the Continue-As-New exception"; "Not versioning state arguments".
- Best practices: log transitions, test state transfer, and "For fixed Schedules, use Temporal Schedules". The concept page adds: "you may also want to Continue-as-New periodically, depending on how often you deploy. This makes sure you're running only a couple of versions" (https://docs.temporal.io/workflow-execution/continue-as-new).

**SDK:**
- 1.30.0 has `workflow.continue_as_new(...)` (memo and search attributes default to the current run's), `info.get_current_history_length()`, `info.get_current_history_size()` and `info.is_continue_as_new_suggested()` (`temporalio/workflow/_context.py:166-205`).
- `continue_as_new` raises `ContinueAsNewError`, a `BaseException`, so `except Exception` cannot swallow it. The four `except BaseException` blocks in `AgentWorkflow` all re-raise and none wraps a rollover (`agent_workflow.py:1728`, `:1762`, `:2030`, `:2268`).
- `workflow.patched()` (the patch marker that versions workflow code) is memoized per run (`temporalio/worker/_workflow_instance.py:1358-1372`). A run that first evaluates a marker while replaying a history without it keeps the old branch for the rest of that run; only a later run of the chain takes the new branch. A marker first reached outside replay returns True.
- The server suggests continue-as-new at 4,096 events or 4 MiB of history by default (server 1.32 dynamic config `limit.historyCount.suggestContinueAsNew` and `limit.historySize.suggestContinueAsNew`). Neither the app nor the dev server overrides them.
- 1.34.0 adds only the Update-handler rule of §8.2.

**OpenCompany today:**
- **`WorkflowControlWorkflow`.**
  - Trigger: `_history_pressure` is "suggested, or more than 10,000 events" (`workflow_control_workflow.py:27`, `:568-576`; helper `trigger_listener_workflow.py:89-102`), checked after events, spawns, poll cycles and Updates (`:359`, `:483`, `:588-593`, `:853`, `:865`, `:908`), and acted on only by the main loop (`:440-443`). The suggestion is read first, so controllers normally roll over near 4,096 events.
  - Fences: it waits for the execution-control checkpoint and for all handlers (`:608-609`), spills any tail that arrived meanwhile and fences again ("There is no await between this final fence and CAN", `:647-665`).
  - Carry (`:684-704`): state, revision, triggers with their provider `seen_ids`, the first 256 events or 512 KB of the queue (the rest goes to the database), the last 4,096 dedupe keys, execution-control state, live roots and the membership epoch. It measures the argument and raises the non-retryable `ControllerContinuationTooLarge` above 1,900,000 bytes, for v1 controllers only (`:697-711`).
  - Children: graph runs start with ABANDON and live roots are carried by id. Poll tasks are cancelled and awaited (`:612-615`). The transition is logged (`:712-715`). Fixture `controller_spill_rollover`.
- **`AgentWorkflow`.** Rolls over at an iteration boundary when pressure says so and no delegation is live (`agent_workflow.py:2982-2996`). The durable path waits until resumed, fences handlers and carries `_agent_resume` (iteration, transcript, usage, prepared payload, tools, thinking text, browser artifacts), with a 1.9 MB check that raises `AgentContinuationTooLarge` (`:2997-3031`). The legacy path has no fence and drops the transcript to the opening prompt above 1 MB (`:3032-3069`). Iterations continue across runs (`:1151-1159`), so activity ids stay unique in the chain. Fixture `agent_rollover`.
- **`WorkspaceTaskControllerWorkflow`.** Rolls over only when idle, after 256 operations, a suggestion or 10,000 events (`workspace_tasks_workflow.py:195-205`). It carries its epoch and the bounded dedupe maps (`:207-215`). It logs nothing and has no rollover fixture.
- **Others.** `PiCodingAgentWorkflow` defers its rollover while Task Manager children are live (`server/services/temporal/pi_workflow.py:382-387`). The legacy listeners roll over on pressure (fixtures `legacy_trigger_listener`, `legacy_polling_trigger`). `CronTriggerWorkflow` handles one firing, because cron is a Temporal Schedule (`server/services/temporal/schedules.py:139-250`), as the page advises.
- **Tests.** Fixtures were recorded on a server with `limit.historyCount.suggestContinueAsNew=40`, so four scenarios record a rollover (`server/tests/temporal/record_fixtures.py:57`, `:971-976`): the page's "Test state transfer", done well.
- **Deviation 1: carry weight.** Each controller trigger embeds the graph (RFC-0004 H10). The agent's durable carry holds the stored seed conversation inside `prepared_payload` (up to 1 MB), the tools twice and unbounded thinking text (`server/services/temporal/agent_activities.py:1825-1830`; `agent_workflow.py:3014-3016`). Over the limit the entity fails, explicitly; a failed v1 controller then fails its generation (§8.2).
- **Deviation 2: idle controllers never roll over.** A controller with no events, poll cycles, firings or Updates never reaches a suggestion. When a later release adds a patch marker, such a run replays without it and keeps the old branch for its whole life (markers read at run start, like `controller-durable-queue-v2` at `:396` and `controller-event-accumulator-v1` at `:549`, are memoized per run). The old branch can then never be deleted, and RFC-0005 §24.3 cannot deprecate the marker. Busy controllers are fine: a v1 cron firing sends the controller four Updates and Update handlers request a rollover (`:588-593`), so a cron-only controller rolls over after roughly 200 firings.
- **Deviation 3: a swallowed cancellation can stall the rollover (RFC-0004 H8).**
  - `_continue_as_new` cancels the poll tasks and awaits them (`:612-615`). A cancelled poll activity reaches the loop as `ActivityError`, which `except Exception` catches (`:851`, `:863`); the loop continues and the `gather` never returns.
  - Unversioned controllers are fully exposed, and v1 controllers are exposed too. The checkpoint at `:608` means no poll is in flight when it resolves, but `begin_action` does not refuse new actions during a rollover. A poll task that becomes ready before the cancel at `:612` (the first cycle of a trigger registered during the `:609` fence, or a loop released from its pause wait in the same pass) can be cancelled mid-activity.
  - A `register_trigger` Signal, or a `replace_graph` that revives a retired poll, during the spill phase creates a poll task after the cancel (`:301-302`, `:331-332`). It is not cancelled before the rollover.
- **Deviation 4: legacy listeners (P3).** A paused `TriggerListenerWorkflow` checks pressure only after processing an event (`trigger_listener_workflow.py:208-225`, `:261-264`). Queued events beyond 256 are dropped at rollover (`:291-303`), against "carry pending Signals forward". `PollingTriggerWorkflow` skips the check on failed cycles (`polling_trigger_workflow.py:186-198`), so a permanently failing poll reaches termination in about 3.4 days at the 60 s default (11 events per cycle, fixture `legacy_polling_trigger.json`). The legacy `AgentWorkflow` path loses a pause at rollover.
- **Minor.** The durable `AgentWorkflow` rollover and the Workspace controller do not log the transition.

**Verdict:** Adopted with deviations. Main-loop-only rollover, handler fences, pending events carried or spilled, dedupe ids carried, ABANDON children (or waiting for none), id-only addressing and chain identity all follow the docs. The deviations are carry weight, idle controllers that never pick up new code, and a cancellation path that can stall a rollover.

**Recommendation:**
1. **Slim the controller carry** (RFC-0004 D7 and H10; already in RFC-0005 §24.2's controller lane).
   - Step 1: carry each distinct graph once, in a `graphs` map keyed by content hash; give each trigger a `graph_ref`, and rebuild `listener_args` in `_seed_carried_state`. Deduplicate by hash, not "one graph": a trigger retired by `replace_graph` keeps its old snapshot (`workflow_control_workflow.py:293-300`).
   - Step 2: carry a generation reference and load the graph by activity.

   Versioning: write the new shape behind `workflow.patched("controller-slim-carry-v1")`, evaluated in `_continue_as_new`; read both shapes in `_seed_carried_state`. When: P2, controller lane after Phase 1. Effort S, then M.
2. **Fix cancellation in the poll loops, for v1 controllers too** (RFC-0004 H8). In `_poll_trigger` (`:849-866`), re-raise when `temporalio.exceptions.is_cancelled_exception(exc)`. In `_continue_as_new`, cancel and await any poll task created after the first cancel, immediately before `workflow.continue_as_new`. Versioning: `workflow.patched("controller-poll-cancel-v1")`, because a history that recorded a swallowed cancel must keep its old branch (RFC-0004 H8). When: P2, controller lane. Effort S.
3. **Move idle controllers onto new code.**
   - Add a `request_rollover` Signal that sets `_can_requested`; the main loop already wakes on that flag (`:431-443`).
   - At the first boot of each release, boot reconcile sends it to every live generation's controller, by id from the app database (RFC-0005 I6). Default (§24).
   - A new handler adds no command until a history contains its Signal, so it needs no patch. The old run leaves through its own memoized code, and the next run takes the newest branches. This is the "periodic rollover" that RFC-0005 §24.3 needs to retire markers.

   Versioning: none (new handler). When: P2, controller lane, after items 1 and 2. Effort S. Risk: forcing a rollover of a controller whose carry is too large fails it; hence item 1 first.
4. **Trim the agent carry and carry unmerged inputs.** The trim is 1b.19 (§16.8 rec 1): drop `conversation`, `memory_content` and `parameter_snapshot` from the carried `prepared_payload`, and keep `tools`, which the resumed run reads. Also cap the carried thinking text. Under RFC-0005 merging (T9's merge point B1), carry offered-but-unmerged input references in `_agent_resume`, or merge them at B1 before rolling over ("Drain your Signal channel and carry pending Signals forward"). Versioning: none for the trim and the cap, because the continue-as-new input is not compared on replay; the new carry under the frozen `merge_version=1` flag. When: the trim in Phase 1b (1b.19); the rest P2, RFC-0005 Phase 4. Effort S, then M. T6 keeps transcripts in history, which makes this trim the main lever on carry size.
5. **Byte backstop.** Add `info.get_current_history_size()` to `_history_pressure` only if a namespace ever raises the server's 4 MiB suggestion. Versioning: patch. When: P3. Effort S.
6. **Legacy listeners.** Request a rollover from `on_event` while paused, spill instead of dropping, and check pressure on failed poll cycles; or stop starting generation-less deployments (the default: §8.2 item 4, 1b.46). When: P3. Effort S.
7. **Log every rollover** (`workflow.logger` is replay-aware) and record a Workspace controller rollover fixture. Versioning: none. When: P3. Effort S.

**Remarks:**
- Order matters: items 1 and 2 before item 3, or forced rollovers fail large controllers or stall.
- Signal rate: above about 5 Signals per second, the controller's multi-step rollover (checkpoint wait, spill activities, fences) can be rewound again and again ("Continue-As-New requires a brief window (~100 ms) with no unhandled signals", event-accumulator page). RFC-0005's cap of 4 per entity per second keeps new generations out of this.
- The carry lands in the new run's first event, so a large carry also starts every run large and brings the next size-based suggestion closer.
- RFC-0005's plan before the amendments in §20 (the entity rolls over "only between turns, when suggested or past 4,000 events or 4,000 Signals"; the supervisor "when suggested, or daily", RFC-0005 §10.2) follows the docs, periodic rollover included. It needs §8.2 item 2, and the excess inputs returned to the database at rollover must not be dropped by `applied_seq` when they are delivered again (§7.4).

**Sketch:** (items 1 and 3)

```python
# server/services/temporal/workflow_control_workflow.py (controller lane)
class WorkflowControlWorkflow:
    @workflow.signal
    async def request_rollover(self) -> None:
        # Recorded in history, so replay is deterministic. The main loop owns
        # continue-as-new and already wakes on _can_requested.
        if not self._closed:
            self._can_requested = True

    def _slim_triggers(self) -> tuple[dict, dict]:
        """Carry each distinct graph once; retired triggers keep their own."""
        graphs, triggers = {}, {}
        for listener_id, spec in self._triggers.items():
            args = dict(spec["listener_args"])
            graph = {key: args.pop(key, None) for key in ("nodes", "edges", "parameter_snapshot")}
            ref = hashlib.sha256(json.dumps(graph, sort_keys=True, default=str).encode()).hexdigest()
            graphs[ref] = graph
            triggers[listener_id] = {**spec, "listener_args": {**args, "graph_ref": ref}}
        return graphs, triggers
    # _continue_as_new writes {"graphs", "triggers"} only when
    # workflow.patched("controller-slim-carry-v1"); _seed_carried_state reads both shapes.

# Boot reconcile, once per release (the app database records the last booted version):
if await release_changed_since_last_boot(database):
    for control in await database.list_live_workflow_controls():   # known ids (I6)
        await client.get_workflow_handle(control.controller_workflow_id).signal("request_rollover")
```

### 8.4 Updatable Timer

**Docs:** https://docs.temporal.io/design-patterns/updatable-timer. A wait "with both a time limit and an update condition": when a message moves the deadline, the workflow recomputes the remaining time and waits again. In Python, `workflow.wait_condition(fn, timeout=duration)` "returns normally when the condition is met, or raises `asyncio.TimeoutError` on timeout"; the page's helper keeps an absolute wake time and loops. Rules that matter:
- "Use absolute timestamps"; "Validate updates. Ensure new deadlines are in the future"; "Consider max extensions"; "Add Queries"; "Log changes"; "Reuse the timer helper"; "Combine with conditions".
- Pitfalls: "A wait without a timeout does not create a timer"; "The timer duration is set once when the wait is called ... This is why the timer helper loops and recalculates"; "Accepting a deadline in the past causes the timer to expire immediately".
- Fit: approval deadlines, SLAs, escalations, grace periods. Not for "fixed timeouts that never change (use a fixed sleep)" or "complex scheduling (use Temporal Schedules)".
- Timers are accurate only from one second up. The timers page: "your Workflow might specify a value as short as one second or as long as several years ... your Workflows should not rely on sub-second accuracy for Timers" (https://docs.temporal.io/workflow-execution/timers-delays). This is a documented range and an accuracy caveat, not an enforced minimum.

**SDK:**
- 1.30.0: `wait_condition(fn, *, timeout, timeout_summary)` (`temporalio/workflow/_context.py:920-943`) is `asyncio.wait_for` over a condition future. A positive timeout starts a timer; when the condition wins first, the SDK cancels it, so each move of a deadline costs a TimerStarted and a TimerCanceled event (`temporalio/worker/_workflow_instance.py:1790-1815`, `:2768-2799`).
- Under CPython 3.12, a timeout of 0 or less raises `TimeoutError` at once without creating a timer, even if the condition is already true.
- `workflow.sleep(duration, *, summary=)` and `timeout_summary` name the timer in the Web UI.
- 1.34.0 documents two traps: switching a wait between `timeout=None` and a positive timeout "constitutes a nondeterministic change", and a timeout of `0` "should not be used".

**OpenCompany today:**
- No workflow deadline moves. Fixed timers: `ApprovedToolCallWorkflow` sleeps until an absolute `grace_until` and skips the sleep when it has passed (`server/services/temporal/approved_tool_call_workflow.py:41-46`), then claims the row at its revision, so an Undo, a newer Send or a cancel ends it with nothing sent (`:48-55`); the controller's 5-second head-of-queue retry (`workflow_control_workflow.py:480`); poll intervals (`:823`).
- Human waits use no workflow timer: the approval gate re-reads its row inside an activity (`server/nodes/workflow/approval_gate/__init__.py:235-277`); approval expiry (168 hours by default) is enforced by that loop or by the next decision (`server/services/approvals/decisions.py:65-81`); reviewed delivery waits inside 31-day activities (§9.6).

**RFC-0005 plan, before the amendments in §20:** the supervisor's "updatable alarm timer", "budget period end (midnight in the owner's time zone)" and "5-minute upkeep" (RFC-0005 §10.2, §15); the entity's debounce, 60-second admission retry, turn deadline per class (a "slow turn" alert under T19), idle passivation and restart backoff (RFC-0005 §10.2, §11, §18.2); `ApprovalWorkflow`'s expiry, then the Undo window (RFC-0005 §10.2, §17.5); recurring alarms as Schedules (RFC-0005 §15).

**Verdict:** Planned in RFC-0005 (follows the docs, misses details). Today's code has no moving deadline, and its fixed Undo wait on an absolute instant is what the page prescribes for a fixed wait. RFC-0005 uses absolute instants, caps extensions (debounce caps, 500 alarms per employee), fires late alarms with a lateness note (better than rejecting a past deadline, for alarms), treats Signals as hints reconciled from the database, and uses Schedules for recurring alarms. It misses the details below.

**Recommendation:**
1. **One live timer per workflow.**
   - The supervisor, the entity and `ApprovalWorkflow` keep named absolute deadlines and wait on one `wait_condition(lambda: changed or work_ready, timeout=earliest - now)`, recomputed after every wake.
   - One shared helper in `server/services/temporal/` (sketch below), with time-skipping tests (RFC-0004 §14).
   - The deadlines cross continue-as-new as instants; timers never do.
   - Deadlines that move: the per-class "slow turn" alert (T19) does not count time parked or paused, and it is recomputed when priority inheritance changes the turn's class (RFC-0005 §11 step 5). The no-progress backstop (a default, §24.1; specified in §6.4) cancels a turn after 24 hours without a completed step; each completed step restarts it, and it never counts time parked, paused, or waiting for the owner (an approval draft, the owner's browser). Parking ships with RFC-0005 Phase 5, for generations with `scheduler_version=1` (T18, §11.8); today's chat runs keep the 24-hour cancel (1b.14, §6.4).

   Versioning: new types. When: P2, Phases 4-7. Effort M.
2. **Never wait with a remaining time of 0 or less.** Check `remaining <= 0` and act first (fire, expire, flush), as the page's Go sample does. Late alarms after a laptop sleep (RFC-0005 §22 scenario d) take the same path. Add the check to the determinism lint (1b.40, §17.18; RFC-0004 §8.1). Versioning: new types. When: P2, Phase 4. Effort S.
3. **Compute "midnight in the owner's time zone" outside workflow code**, with the same function that keys the ledger's day, and carry the UTC instant to the supervisor. Otherwise the timer and the ledger can disagree on daylight-saving days, and a tzdata update changes what replayed workflow code computes. Versioning: `scheduler_version=1`, because the supervisor already runs from Phase 4 without a period timer. When: P2, Phase 5. Effort S.
4. **No sub-second windows.** T9 removes the owner-chat debounce (§7.4); its window (0.4 s, RFC-0005 §11) was the only one below one second. Keep every remaining window at one second or more, and document that timers are accurate only from one second up. When: P2, Phase 4. Effort S (text).
5. **No per-employee upkeep timer** (default, §24). Remove the supervisor's 5-minute upkeep. One instance-wide sweep in the backend process, every 5 minutes beside the chat watchdog (`server/services/chat/watchdog.py`), reads the database for overdue work and signals, by known id, only the supervisors and entities concerned:
   - leases whose turn has closed (confirmed by describing the turn by id);
   - turns suspended or parked past the 7-day limit (RFC-0005 §10.2; T19 then dead-letters a parked turn's input with an attention item);
   - alarms that did not fire;
   - parked turns whose cause has cleared, such as an allowance topped up (T11): they get `released{reason}` (§11.8). Outage parks probe on their own 15-minute timer. Parking exists from RFC-0005 Phase 5, with `scheduler_version=1` (T18).

   Versioning: new types (the supervisor is built without an upkeep timer in Phase 4; the sweep itself is backend code). When: P2, Phase 4, and the sweep gains parked turns in Phase 5. Effort S. Saves 288 wakes per employee per day (28,800 a day at 100 employees).
6. **Name timers and log moves.** Pass `summary` or `timeout_summary` (`alarm`, `period`, `debounce`, `passivate`, `slow-turn`, `no-progress`, `expiry`, `undo`), and log every deadline change ("Log changes"). When: P3. Effort S.

**Remarks:**
- Adding a timeout to an existing wait (for example a passivation timeout in an existing type) is a command change: it needs a patch or a frozen flag. New types are free.
- Each debounce extension costs about 6 events (a Signal, a workflow task of 3 events, TimerCanceled, TimerStarted). The 8-10 second caps and the relay's limit of 4 Signals per second bound it.
- Timers keep running through a cooperative pause. A timer that fires while the employee is stopped must check control state before it acts; alarms become inputs that the relay holds (RFC-0005 §14).
- `alarms_changed` as a Signal matches the docs and RFC-0005 I1: the timer re-reads the earliest due time from the database, and `alarms.fire_due` by compare-and-swap makes a stale timer harmless ("Check if the timer already expired before updating").

**Sketch:** (items 1 and 2)

```python
# server/services/temporal/deadlines.py: one live timer over named absolute deadlines
import asyncio
from datetime import datetime
from typing import Callable
from temporalio import workflow

class Deadlines:
    def __init__(self, carried: dict[str, str] | None = None) -> None:
        self._due = {name: datetime.fromisoformat(at) for name, at in (carried or {}).items()}
        self._changed = False

    def set(self, name: str, at: datetime | None) -> None:   # handlers call this
        if at is None:
            self._due.pop(name, None)
        else:
            self._due[name] = at
        self._changed = True                 # wakes wait(); it re-arms on the new minimum

    def carry(self) -> dict[str, str]:       # continue-as-new carries instants, never timers
        return {name: at.isoformat() for name, at in self._due.items()}

    async def wait(self, ready: Callable[[], bool]) -> list[str]:
        """Return the names now due, or [] once ready() is true."""
        while True:
            now = workflow.now()
            due = sorted(name for name, at in self._due.items() if at <= now)
            if due or ready():
                return due                   # act first: never wait with a timeout <= 0
            self._changed = False
            earliest = min(self._due.values(), default=None)
            try:
                await workflow.wait_condition(
                    lambda: self._changed or ready(),
                    timeout=None if earliest is None else earliest - now,
                    timeout_summary="next deadline")
            except asyncio.TimeoutError:
                pass                         # the next pass returns what is now due
```

## 9. External interaction

Five patterns cover waiting on the world outside a workflow: polling, long activities, delayed starts, HTTP callbacks and human decisions. OpenCompany has all five needs. Two rules of the category are broken today: a delay (the Timer node) and multi-day human waits (approvals) run inside activities.

### 9.1 External interaction patterns (category overview)

**Docs:** https://docs.temporal.io/design-patterns/external-interaction-patterns. A selection guide for waiting on the world:
- "The external system offers no notification": poll, and "tune the interval to the expected latency".
- "One Activity runs for a long time": heartbeats "so failures resume instead of restarting".
- "A person or external system must decide": Approval, "block on a Signal".
- "Execution should begin later": Delayed Start.
- "You send or receive HTTP callbacks": Delayed Callback.

**OpenCompany today:**

| Wait | Today | Pattern |
|---|---|---|
| Gmail, Outlook, IMAP triggers | a sleep-plus-activity loop inside the controller | Polling (§9.2) |
| LLM steps, coding and browser agents, Pi driver, Apify, permit waits | heartbeating activities | Long-running activity (§9.3) |
| Drafts (approvalGate), reviewed team-job delivery | a database poll inside an activity | Approval (§9.6) |
| Held tool calls (Ask first) | a database row, then a send workflow | Approval plus Delayed Callback pattern 2 |
| Browser "Needs you" | an in-memory wait inside the browser activity | Approval (bounded, deliberate) |
| The Undo window before a send | `workflow.sleep`, then one activity | Delayed Callback pattern 2 |
| Timer node; cron node on a canvas Run | `asyncio.sleep` inside an activity | Delayed Callback pattern 2, misused (§9.5) |
| Generic and signed webhooks | `emit`: a Visibility query, then a plain Signal | Delayed Callback pattern 1 (§9.5) |
| An external job that completes later | none | Delayed Callback pattern 3 |

**Verdict:** Not applicable (selection guide).

**Recommendation:**
1. Add a "where each wait goes" rule to §5 and to `docs-internal/TEMPORAL_ARCHITECTURE.md`:
   - human waits never sit inside an activity; they are an Update plus a durable timer (RFC-0004 D13);
   - delays are workflow timers; an activity may sleep only within one attempt;
   - a poll is one retried activity per trigger (T7, §9.2);
   - inbound events reach workflows by known id from the app database (RFC-0005 I6), and no request path calls Temporal (I2);
   - timers are accurate only from one second up (T9).

   Versioning: none (text). When: with this RFC; the TEMPORAL_ARCHITECTURE.md text with Phase 3. Effort S.

**Remarks:** The index summarizes Approval as "block on a Signal", but the Approval page suggests "an Update with a validator" when the approver needs immediate confirmation. OpenCompany's card needs that (`too_late`, `already_decided`, `approval_conflict`). Under I2 and T10 the database compare-and-swap gives the immediate answer, and the Update carries the decided state into the workflow (§9.6).

### 9.2 Polling External Services

**Docs:** https://docs.temporal.io/design-patterns/polling. Three strategies, chosen by interval:
1. **Frequent** (1 s or faster): a loop inside one activity that heartbeats on every iteration, with `heartbeat_timeout` shorter than `start_to_close_timeout` (the sample uses 2 s and 60 s); for operations of "minutes, not hours".
2. **Infrequent** (1 minute or slower): the activity polls once and raises when the result is not ready, and `RetryPolicy(backoff_coefficient=1, initial_interval=<interval>)` sets the cadence. "Retries do not add events to the Workflow history, keeping it small."
3. **Periodic sequence**: a child workflow loops an activity and `workflow.sleep` up to N times, then continues as new.

Also on the page:
- The comparison table rates history impact "Medium" (frequent), "Minimal" (infrequent), "Low (with CAN)" (periodic), and "Workflow Timer Loop | Any | High | Medium | Avoid this approach."
- Best practices: "Add exponential backoff. Use backoff for error cases (not normal polling)"; "Implement circuit breakers"; "Add jitter. Prevent thundering herd when many Workflows poll simultaneously"; "Distinguish transient from permanent failures"; "Monitor polling metrics"; "Respect rate limits"; "Consider webhooks".
- Pitfalls: missing heartbeats, unbounded history, tight loops, "No timeout", "Ignoring errors", "Workflow timer loops ... bloats history".
- The page never mentions Schedules.
- The delayed-retry page adds: "`nextRetryDelay` overrides the interval for a single retry; the RetryPolicy still governs maximum attempts" (https://docs.temporal.io/design-patterns/delayed-retry).

**SDK:** All in 1.30.0 and unchanged in 1.34.0: `activity.heartbeat`; `RetryPolicy`; `ApplicationError(..., non_retryable=, next_retry_delay=, category=ApplicationErrorCategory.BENIGN)` (`temporalio/exceptions.py:111-175`); a BENIGN failure logs at DEBUG and records no failure metric (`temporalio/worker/_activity.py:457-472`); `workflow.execute_activity(..., summary=)`; `WorkflowIDConflictPolicy.USE_EXISTING`.

**OpenCompany today:**
- **Controlled generations.** Polls run inside `WorkflowControlWorkflow._poll_trigger` (`server/services/temporal/workflow_control_workflow.py:792-908`):
  - `workflow.sleep(poll_interval)` (`:823`), then one `poll.<type>.v<n>` activity per cycle, with `start_to_close = max(30, 4 x interval)` seconds and no heartbeat timeout (`:808-809`, `:834-838`, `:856-860`);
  - `DEFAULT_ACTIVITY_RETRY`: 3 attempts, 1-30 s apart, only `NodeUserError` and `InvalidEvent` non-retryable (`server/services/temporal/_retry_policies.py:52-55`, `:62-67`);
  - each cycle is an admitted action under execution control (`:833`), so Stop and rollover wait for it;
  - the trigger arrives by a `register_trigger` Signal that carries the full graph (`server/services/deployment/manager.py:965-975`).
- **Generation-less deployments** run the same loop in the standalone `PollingTriggerWorkflow` (`server/services/temporal/polling_trigger_workflow.py:157-245`).
- **The cycle activity**, `PollingTriggerNode.as_poll_activity` (`server/services/plugin/polling.py:204-272`):
  - fetches the visible ids, then each new id's details, skipping (and so losing) any that fail (`:236-246`);
  - runs `post_emit`, for example mark-as-read, before returning (`:256-259`);
  - returns full payloads and the whole visible-id set (`:270`), with no byte bound (1b.18 adds one, §12.3); the count is capped by the provider's page (Gmail 20, Outlook 25, IMAP 50);
  - never heartbeats.
- **Canvas Runs** loop `asyncio.sleep(poll_interval)` inside the trigger's node activity until the first new item (for example `server/nodes/google/gmail_receive/__init__.py:149-193`), kept alive by the plugin beat loop. That is the frequent shape, acceptable for an interactive run.
- **Measured against the page:**
  - Interval: a 30-second floor in both workflow paths (`workflow_control_workflow.py:37`, `:808`; `polling_trigger_workflow.py:80`) and a 60-second default. Gmail's parameters allow 10 s (`gmail_receive/__init__.py:39`), which is silently raised.
  - No jitter: every loop sleeps exactly the interval, and after a boot re-arm or a rollover all of an instance's polls restart together.
  - No failure classification and no backoff: a cycle that exhausts its attempts is logged and the loop continues at the same interval (`workflow_control_workflow.py:851-866`; `polling_trigger_workflow.py:193-198`). A disconnected Google account raises a retryable `ValueError` (`server/nodes/google/_auth_helper.py:68`): three attempts and an ERROR line every cycle, forever. Nothing escalates.
  - Cancellation is swallowed (RFC-0004 H8; §8.3 deviation 3).
  - No heartbeat: a crashed worker or a hung IMAP call is noticed only at start-to-close (2 minutes at 30 s, 4 minutes at 60 s, 4 hours at the 3,600 s maximum), and a cancel cannot reach the activity. Because Stop waits for an admitted poll, a hung poll can hold Stop for up to three start-to-close timeouts (12 minutes at the 60-second default).
  - History: about 8-11 events per quiet cycle (fixture `legacy_polling_trigger.json`: 11). With the server's 4,096-event suggestion, a controller with one 30-second poll rolls over about every 3 hours (est.), carrying every trigger spec with a full graph copy.
  - RFC-0004 M3: mark-as-read runs before the event is recorded, and an id whose detail fetch failed counts as seen.

**RFC-0005 plan, before the amendments in §20:** one Schedule per poll trigger (`poll-<workflow_id>-g<gen>-<node_id>`, overlap SKIP, RFC-0005 §15), whose short workflow fetches, appends to the inbox and saves the cursor in one transaction, then runs `post_emit` (fixing M3). That is not one of the page's strategies, and it costs one workflow execution per tick: 1,440 a day per 60-second trigger, about 10,080 retained per trigger at 7-day retention, about 1M at 100 employees with one poll each (est.). Owner decision T7 replaces it.

**Verdict:** Adopted with deviations. Today's loop is the page's periodic sequence, hosted inside the one workflow meant to live for months, without jitter, backoff, failure classification, a heartbeat timeout or working cancellation. T7 adopts the page's infrequent strategy for RFC-0005, which then follows the docs.

**Recommendation:**
1. **Polls as retried activities** (decision T7; amends RFC-0005 §3.1 E5 and §15).
   - A new `PollWorkflow`, id `poll-<workflow_id>-g<gen>-<node_id>`, is started at Start and by boot re-arm with `WorkflowIDConflictPolicy.USE_EXISTING` and `EventWorkflowId`.
   - It runs one activity that never completes: `RetryPolicy(initial_interval=<interval>, backoff_coefficient=1, maximum_attempts=0)`, no `schedule_to_close_timeout`, `start_to_close` 3 minutes, `heartbeat_timeout` 60 s.
   - Each attempt reads the subscription and the cursor from the app database (I10). When the generation has ended it raises a non-retryable `PollRetired`, which ends the workflow. While the employee is stopped or held by its budget, it skips the fetch, so Stop needs no Temporal call.
   - Otherwise it fetches, appends at most one page of new items (50 items or 512 KB, §12.3) and saves the cursor, advanced only over appended items, in one transaction, heartbeating after the list call and after each detail, and runs `post_emit` after the commit (fixes M3).
   - It ends with `ApplicationError("polled", category=BENIGN, next_retry_delay=<interval plus jitter, or the error backoff>)`.
   - Effects: the history holds about five events per generation and trigger (est.); bodies never enter Temporal (I7); Reset also terminates the workflow through `EventWorkflowId`.

   Versioning: new type. When: P2, RFC-0005 Phase 3. Effort M. Risk: operators see an activity at attempt 50,000; give it a `summary=` such as "poll Gmail every 60 s" and document it.
2. **Errors and pacing, for the new path and today's loops** (defaults, §24):
   - jitter of up to 10% of the interval, at most 30 s;
   - credential and configuration failures: in today's loops they raise `NodeUserError` (after 1b.17), which `DEFAULT_ACTIVITY_RETRY` does not retry within a cycle; in the new path they are counted and backed off, and never end the poll (the owner may reconnect later);
   - consecutive failures back the interval off, doubling up to 1 hour;
   - 5 consecutive failed cycles raise the "driver down" attention item of RFC-0005 §18.3;
   - poll metrics: attempts, durations, items found.

   In the new path this lives in the activity and the database. In today's controller, jitter and backoff only change timer durations, which replay never compares except from or to zero; derive the jitter from a hash of the listener id and cycle number, so `workflow.random()` stays untouched. Versioning: none. When: P2, Phase 3 and the controller lane. Effort S.
3. **Today's loops, options and activity body only.** Set `heartbeat_timeout=60 s` and a fixed `start_to_close` of 3 minutes instead of 4 x the interval (`workflow_control_workflow.py:808-809`, `:834-838`, `:856-860`, and the same options in `polling_trigger_workflow.py`); heartbeat after the list call and after each detail in `as_poll_activity`; raise `NodeUserError` for credential failures. Activity options are not compared on replay (RFC-0004 §8.5). Versioning: none. When: P1, Phase 1b (1b.17). Effort S. The replay corpus must still pass `controller_poll.json` and `legacy_polling_trigger.json`.
4. **Retried-by-design activities must not log as failures.** `ActivityObservabilityInterceptor` logs a WARN `activity_retry` on every attempt after the first and a WARN `activity_end` on every failure (`server/services/temporal/_interceptors.py:58-81`): under item 1 that is two WARN lines per poll cycle. Log BENIGN failures, and attempts of poll-cycle activities, at DEBUG; they count toward no retry alert (1b.30 excludes T7's poll attempts, §11.7). This refines 1b.30's quieter retry log. Versioning: none. When: P2, Phase 3. Effort S.

**Remarks:**
- Why not Schedules: every tick becomes an execution with its own events and Visibility row, kept for the namespace retention; the infrequent strategy records nothing per quiet cycle. T7 also keeps Stop and Resume free of Temporal calls.
- A never-completing poll activity must not count as a business action, or a Stop checkpoint never comes. `PollWorkflow` lives outside the controller and the supervisor, so nothing counts it.
- `next_retry_delay` overrides the retry interval for one attempt, and `maximum_attempts=0` keeps attempts unlimited. Never add `schedule_to_close_timeout`: it would end the poll.
- Under T8 (workers in a second process on hosted tier S), poll activities read credentials. By default the worker builder initializes the encryption key in the worker process too (§15.2 rec 3), so reading credentials does not bind polls to the API process.
- Generations that keep today's controller until RFC-0005 Phase 10 keep its loop; items 2 and 3 cover them.

**Sketch:** (item 1)

```python
# server/services/temporal/poll_workflow.py: new type (RFC-0005 Phase 3, decision T7)
@workflow.defn(name="PollWorkflow")             # sandboxed (§17.18)
class PollWorkflow:
    @workflow.run
    async def run(self, ref: dict) -> dict:
        # ref: workflow_id, generation, node_id, activity ("poll.<type>.v<n>"), interval_s
        try:
            await workflow.execute_activity(
                ref["activity"], ref, activity_id="poll",
                start_to_close_timeout=timedelta(minutes=3),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=ref["interval_s"]),
                                         backoff_coefficient=1.0, maximum_attempts=0),
                summary=f"poll every {ref['interval_s']} s")
        except ActivityError as exc:
            if getattr(exc.cause, "type", None) != "PollRetired":
                raise
        return {"retired": True}

# The activity body, in an activities module outside the sandbox: one cycle per attempt.
# It never returns normally.
async def poll_cycle(ref: dict) -> None:
    sub = await poll_subscription(ref)          # generation state and cursor, from the database
    if sub is None:                             # generation reset, or the trigger was removed
        raise ApplicationError("retired", type="PollRetired", non_retryable=True)
    delay = sub.interval + timedelta(seconds=random.uniform(0, sub.jitter_s))
    if sub.running:                             # stopped or budget-held: skip the fetch
        try:
            await poll_once(sub, heartbeat=activity.heartbeat)  # items and cursor in one tx; post_emit after
            await poll_succeeded(sub)
        except Exception as exc:                # counted in the database; 5 in a row: driver down
            failures = await poll_failed(sub, exc)
            delay = min(sub.interval * 2 ** failures, timedelta(hours=1))
    raise ApplicationError("polled", type="PollCycleDone", next_retry_delay=delay,
                           category=ApplicationErrorCategory.BENIGN)
```

### 9.3 Long-Running Activity

**Docs:** https://docs.temporal.io/design-patterns/long-running-activity. An activity that runs for minutes to hours reports progress with heartbeats (periodic calls that prove liveness and can carry checkpoint details), resumes from its last checkpoint after a failure, and handles cancellation. Rules that matter:
- Heartbeat details "are persisted and available to retry attempts": read `activity.info().heartbeat_details` on a retry and resume from them.
- "Heartbeat timeouts detect stuck Activities faster than execution timeouts." Set the heartbeat timeout to "2-3x the expected heartbeat interval"; heartbeat "every 10-30 seconds"; keep details small ("IDs, offsets, counts"); make reprocessing the last checkpoint idempotent; "Test resumption"; avoid heartbeat spam.
- Cancellation is delivered on a heartbeat, and in Python it arrives as `asyncio.CancelledError`: clean up, then re-raise.
- Pitfalls: "Missing HeartbeatTimeout"; "Heartbeating too infrequently. Cancellation is only delivered on the next heartbeat"; not resuming from details; catching the wrong exception.
- Throttling (https://docs.temporal.io/encyclopedia/detecting-activity-failures#activity-heartbeat): heartbeats reach the server at most every `min(0.8 x heartbeat timeout, 60 s)`, or every 30 s without a heartbeat timeout. "Activities that don't Heartbeat can't receive a Cancellation."

**SDK:** 1.30.0 has:
- `activity.heartbeat(*details)` and `Info.heartbeat_details`;
- `Info.scheduled_time` (the proto's "When was this task first scheduled", the first attempt's schedule time, timezone-aware UTC), `Info.started_time` and `Info.start_to_close_timeout`;
- `activity.cancellation_details()` with `worker_shutdown`, `cancel_requested`, `paused`, `reset` and `timed_out`; `activity.is_worker_shutdown()`;
- worker throttles of 60 s maximum and 30 s default.

An activity is recorded Cancelled only when it raises the cancellation. Nothing these recommendations use changes in 1.34.0.

**OpenCompany today:**
- **Liveness heartbeats are well covered:**

  | Activity | Beat every | Heartbeat timeout | Evidence |
  |---|---|---|---|
  | Per-type node wrapper (when start-to-close exceeds the heartbeat timeout) | 30 s | 2 min (24 h start-to-close in graphs) | `server/services/plugin/base.py:1248-1264`; `server/services/temporal/workflow.py:167`, `:845-861` |
  | LLM step | 20 s while the provider call runs | 1 min | `server/services/temporal/agent_activities.py:378-399`; `agent_workflow.py:99` |
  | Permit waits (small dict details) | 1 s | 10 s | `agent_activities.py:2288-2316`; `agent_workflow.py:569-576` |
  | Pi driver | 30 s | 90 s | `server/services/temporal/pi_activities.py:125-128`; `pi_workflow.py:184` |
  | Workspace cleanup | 5 s | 30 s | `server/services/temporal/workspace_task_activities.py:15-18` |
  | Reviewed delivery | 30 s | 2 min | `server/services/temporal/employee_job_workflow.py:19-22`, `:135` |

  After throttling the server sees a heartbeat every 60 s (2-minute timeout), 48 s (1-minute timeout) or 8 s (10-second timeout), all inside the docs' guidance.
- **Resumption uses durable rows keyed by the activity's identity, not heartbeat details** (`heartbeat_details` is not used anywhere in server code): the approval gate's key `t:{workflow_id}:{run_id}:{activity_id}` (`server/nodes/workflow/approval_gate/__init__.py:110-123`), reviewed delivery's claim, lease and `inflight` mark (`server/services/employees/jobs.py:315-386`), and permit lease ids per attempt. Equivalent, and it survives a Temporal rebuild (I10).
- **Done well:** permit acquisition compensates, then re-raises on cancel (`agent_activities.py:2317-2339`); the LLM step re-raises (`:575-578`); `NodeWaitInterrupted` turns a worker-shutdown interruption into a retryable failure that re-attaches (`server/services/plugin/base.py:125-143`, `:1355-1366`).
- **Gap 1: node activities swallow cancellation (RFC-0004 H2).** `NodeExecutor` turns `CancelledError` into `{success: False, error: "Cancelled"}` for every node type except Workspace-task nodes (`server/services/node_executor.py:231-239`), so the activity completes normally after a cancel request. An approved send cancelled mid-call is recorded as `not_sent` (`server/services/temporal/approved_tool_call_workflow.py:66-69`), so the owner's Retry asks no confirmation (`server/services/approvals/decisions.py:167-168`), although the message may have gone out. No RFC-0005 phase schedules H2.
- **Gap 2:** the Timer node turns cancellation into `RuntimeError("Timer cancelled")` (`server/nodes/scheduler/timer/__init__.py:81-84`); see §9.5.
- **Gap 3:** poll activities never heartbeat (§9.2).
- **Gap 4: long external jobs start over.**
  - The Apify node calls `actor(...).call(...)` and waits up to the actor timeout of 1-3,600 s (`server/nodes/scraper/apify_actor/__init__.py:221`, `:287-291`) without recording the Apify run id.
  - As an AI tool, its attempt runs under the plugin's 10-minute start-to-close (`server/services/plugin/scaling.py:93`).
  - On new generations (3 attempts), a long actor run times out and up to three paid runs start.
  - On the legacy tool path, which manual canvas runs use and which sets no retry policy (`agent_workflow.py:114-143`), a new paid Apify run starts every 10 minutes with no limit.
  - In a graph, the 24-hour attempt covers the 1-hour maximum.
- **Gap 5: cancellation latency** is up to about 60 s of throttling plus the 30-second beat. The chat path works around it deliberately by polling a database stop flag (`agent_activities.py:419-444`: "a heartbeat-borne cancel arrives too late to stop a stream mid-sentence").
- **Gap 6: the beat loop runs whether or not the body progresses** (`base.py:1248-1264`), so a hung body is caught only at start-to-close: 24 hours in graphs, 30 minutes for AI nodes, 10 minutes for actions.

**Verdict:** Adopted with deviations. Heartbeat cadence and timeouts are good and resumption is durable, but node activities swallow cancellation, polls never heartbeat, and long external jobs restart from scratch.

**Recommendation:**
1. **Node activities re-raise cancellation** (RFC-0004 D9 and H2; 1b.2). In `NodeExecutor` (`server/services/node_executor.py:231-239`), on the Temporal path, re-raise `CancelledError` after cleanup. When `activity.cancellation_details()` reports `worker_shutdown`, raise `ApplicationError(type="NodeWaitInterrupted")` instead, which the wrapper passes on as a retryable failure (`base.py:1398-1400`). A cancel during a send then becomes `unknown`: `ApprovedToolCallWorkflow` sees an `ActivityError` and records `broke_off`, whose outcome is `unknown` (`server/services/approvals/execution.py:148-155`), so the owner's Retry asks for confirmation; a retried graph or tool send meets the redo guard (1b.1, §11.2). Versioning: none (activity body; old histories keep their recorded results). When: P1, Phase 1b (1b.2). Effort S-M. Test: RFC-0004 gate AG10, and a time-skipping `ApprovedToolCallWorkflow` run whose send is cancelled records `unknown`. Risk: agent tool calls use `WAIT_CANCELLATION_COMPLETED` (`agent_workflow.py:145-156`) and will see a Cancelled `ActivityError` where they now see a failed result; check the tool-result path (`agent_workflow.py:2574-2628`) and the pause-on-failure streak.
2. **Long waits fit one attempt.** A shared helper `attempt_deadline()` in `server/services/plugin/` returns `info.started_time + info.start_to_close_timeout` minus a 60-second margin, or None outside an activity. The Timer node (§9.5 item 1) and the Apify node refuse, with `NodeUserError`, a wait or actor timeout that would end after it. It applies on every path, because the helper reads the attempt's own timeout; no branch on the path. Together with 1b.3 (§11.2: the legacy tool path honours each plugin's declared retry policy and timeouts), a long actor run as a tool costs at most one paid run. Versioning: none. When: P1, Phase 1b (1b.4; the Timer part is 1b.8). Effort S.
3. **Poll heartbeats**: §9.2 item 3 (P1, Phase 1b, 1b.17).
4. **Checkpoint external job ids** (§5 rule 20). Start the Apify run with `actor(...).start()`, save its run id in a database row keyed by the activity's identity and in the heartbeat details before waiting, and on a retry wait on that run (`run(run_id).wait_for_finish()`) instead of starting another. The row is the durable record: heartbeat details alone are lost when the activity is scheduled again after a rollover, and rows survive a Temporal rebuild (I10). The plugin beat loop, which today overwrites the details every 30 s with a status string, must repeat the run id instead. This makes the retry idempotent, as T13 requires for redoing a side-effecting call (1b.1, §11.2). Versioning: none. When: P2, RFC-0005 Phase 7. Effort M.
5. **Progress heartbeats and resumption tests.** Plugins that can report progress heartbeat on progress instead of the fixed 30-second beat, so a hung body is caught by the heartbeat timeout; add `ActivityEnvironment` tests for resume after a crash. When: P3. Effort M.

**Remarks:**
- The embedded worker shares uvicorn's event loop (RFC-0004 M1). A blocked loop stops heartbeats and causes false heartbeat timeouts and duplicate attempts. Sends refuse a second attempt (`server/services/approvals/tool_calls.py:367-379`) only for plugins with an approval spec; T13 extends the rule to every side-effecting call, graph nodes included (1b.1, §11.2).
- Keep the LLM step's beat at 20 s or less: its 1-minute timeout throttles heartbeats to every 48 s, a 12-second margin.
- Tests exist for the shutdown re-attach (`server/tests/services/test_node_wait_interrupted.py`); none for resuming an external job after a crash.

**Sketch:** (items 1 and 2)

```python
# server/services/plugin/base.py (helper shared by Timer and Apify)
def attempt_deadline(margin_s: float = 60.0) -> datetime | None:
    """When this attempt's start-to-close timeout fires, minus a margin."""
    from temporalio import activity
    if not activity.in_activity():
        return None
    info = activity.info()
    if info.start_to_close_timeout is None:
        return None
    return info.started_time + info.start_to_close_timeout - timedelta(seconds=margin_s)

# server/services/node_executor.py:231-239 (RFC-0004 H2)
except asyncio.CancelledError:
    from temporalio import activity
    if activity.in_activity():
        details = activity.cancellation_details()
        if details is not None and details.worker_shutdown:
            # Not a failure: the next attempt re-attaches (NodeWaitInterrupted contract).
            raise ApplicationError("Worker shutting down", type=NODE_WAIT_INTERRUPTED) from None
        raise                                   # recorded Cancelled, as Temporal expects
    return ExecutionResult(False, node_id, node_type, error="Cancelled",
                           execution_id=execution_id,
                           execution_time=time.time() - start_time).to_dict()
```

### 9.4 Delayed Start

**Docs:** https://docs.temporal.io/design-patterns/delayed-start. `start_delay` creates the execution and reserves its id at once, but no workflow code runs and no worker is used until the delay passes. Rules that matter:
- "If the client sends a Signal-With-Start or Update-With-Start during the delay, the remaining delay is bypassed ... Regular Signals do not interrupt the delay." Handlers do not run during the delay, and Queries "have no state to return".
- "You cannot dynamically adjust the delay after creation (use the Updatable Timer pattern for that)."
- One-time delays only (recurring work belongs on Schedules); not for sub-second delays ("Treat the configured duration as a minimum, not an exact value").
- To cancel before it runs, cancel the execution or use Signal-With-Start.

**SDK:** `Client.start_workflow(..., start_delay=timedelta)` in 1.30.0 (`temporalio/client/_client.py:414`) and 1.34.0. Child workflows have no equivalent.

**OpenCompany today:**
- Not used: `start_delay` appears nowhere in server code.
- The only workflow whose first operation is a sleep is `ApprovedToolCallWorkflow`. It sleeps to `grace_until`, 5 s after Send (`server/services/temporal/approved_tool_call_workflow.py:41-46`), and has no handlers.
- Every other delay is an in-workflow timer or a misused `asyncio.sleep` (§9.5).

**Verdict:** Not applicable.

**Recommendation:**
1. **No adoption.** `start_delay` would save `ApprovedToolCallWorkflow` one timer per send, but RFC-0005 Phase 7 replaces it with `ApprovalWorkflow`, whose Undo window must accept an Update. A delayed start runs no handler, and Update-With-Start would bypass the delay. This corrects RFC-0004 §4.2, which lists `start_delay` for the Undo window. When: never. Effort none.
2. **A rule for §5:** never use `start_delay` on an id that the relay addresses with Signal-With-Start (`conv-...`, RFC-0005 §9.4), because any delivery would bypass it. Debounce, quiet hours and restart backoff stay in-workflow timers, as RFC-0005 §11 and §18.2 already have them. Versioning: none (text). When: with this RFC. Effort S.

**Remarks:**
- The page's "Cancellable delayed execution" sample relies on a cancel Signal that cannot run during the delay; the page admits this afterwards.
- `ApprovedToolCallWorkflow` decides whether to sleep from `workflow.now()`, which replays deterministically, so skipping a past deadline never turns into a timer change from or to zero on replay.

### 9.5 Delayed Callback (Webhooks)

**Docs:** https://docs.temporal.io/design-patterns/delayed-callback. Three patterns that make HTTP callbacks durable:
- **Pattern 1, inbound:** the HTTP handler calls Signal-With-Start; "The handler can return an HTTP 200 immediately after this call; Temporal takes responsibility for delivery." Workflow ids are business ids, "deterministic and stable across webhook deliveries". "Sending a plain Signal to a Workflow that does not exist causes an error."
- **Pattern 2, outbound after a delay:** `workflow.sleep(delay)`, then an activity whose retries cover HTTP failures. "Using `time.sleep()` (non-durable) ... A process sleep disappears on restart; only Temporal's timer is durable." Not for delays under one second.
- **Pattern 3, async completion:** the activity stores `activity.info().task_token` (an opaque handle for completing the activity from outside) "in a transactional write alongside the job submission", calls `activity.raise_complete_async()`, and the callback handler completes it with `client.get_async_activity_handle(task_token=...)`. For a human review that can take a week, the activity-execution page prefers a short activity plus a Signal (https://docs.temporal.io/activity-execution#asynchronous-activity-completion).

**SDK:** All in 1.30.0 and 1.34.0: Signal-With-Start through `start_signal` (`temporalio/client/_client.py:415`), `activity.raise_complete_async()`, `Client.get_async_activity_handle`, and `AsyncActivityHandle.heartbeat`, `complete`, `fail` and `report_cancellation`.

**OpenCompany today:**
- **Pattern 1.**
  - `server/routers/webhook.py:59-117` hands the request to `deliver_webhook`, which builds a route table from the generation snapshots and answers 404, 405 or 401 (`server/nodes/trigger/webhook_trigger/_routes.py:59-101`), then calls `emit` with `broadcast=False` (`server/nodes/trigger/webhook_trigger/_events.py:43-48`).
  - `emit` runs a Visibility query, then sends a plain `on_event` Signal (`server/services/events/dispatch.py:155-165`, `:251-258`).
  - The route answers 200 whether or not any controller received the event: `emit` logs and returns when the client is missing, the query fails, nothing matches or a Signal fails (`dispatch.py:118-130`, `:155-165`, `:186-191`, `:196-199`). Signed providers (Stripe, Dodo Payments, WhatsApp Business) feed the same `emit`. Delivery is at most once (RFC-0004 H1).
  - RFC-0005 Phase 2 replaces this. Ingress appends to a database inbox and commits before the 2xx; a relay sends Signal-With-Start to the business-stable entity id `conv-<workflow_id>-g<gen>-<lane>-<thread>` (RFC-0005 §8, §9.4).
- **Pattern 2, adopted correctly in `ApprovedToolCallWorkflow`** (`server/services/temporal/approved_tool_call_workflow.py:33-80`): it sleeps to `grace_until`, claims the row at its revision, sends once with `SEND_ONCE = RetryPolicy(maximum_attempts=1)` (`:30`, `:57-72`), and records `sent`, `not_sent` or `unknown`. The reconciler restarts sends that never started and ends stale `sending` rows as `unknown` (`server/services/approvals/reconcile.py:76-110`).
- **Pattern 2 misused, the Timer node** (`server/nodes/scheduler/timer/__init__.py`): `asyncio.sleep(wait_seconds)` inside an activity (`:81-84`); durations of 1-86,400 in seconds, minutes or hours (`:18`), while the tool text says 1-3,600 (`:38`); on `machina-default` (20 slots per worker, 10 in local mode, `server/services/temporal/worker.py:413-423`, `:477`); usable as an AI tool (`:45`). Consequences:
  - As a tool on new generations: the plugin's 10-minute start-to-close and 3 attempts (`server/services/plugin/scaling.py:58`, `:93`; `agent_workflow.py:145-156`). A 20-minute wait fails after about 30 minutes, and the model receives an `ActivityError`.
  - As a tool on the legacy path (manual canvas runs): no retry policy (`agent_workflow.py:114-143`), so the wait times out every 10 minutes and is retried without limit. The call never ends.
  - In a graph: 24 hours per attempt (`server/services/temporal/workflow.py:167`, `:845-861`). A crash restarts the full delay on the next attempt, and a delay over 24 hours fails after three attempts (about 72 hours).
  - A graceful shutdown that outlasts the grace period cancels the activity. The node turns the cancel into `RuntimeError`, the plugin returns it as a failed result (`server/services/plugin/base.py:766-768`), and MachinaWorkflow records the node as failed (`workflow.py:893-908`): the node and the run fail, with no retry. Only a crash restarts the delay.
  - A user's "wait 2 hours, then call this URL" graph is exactly pattern 2, and it is not durable. The cron node's canvas Run does the same for up to 24 hours (`server/nodes/scheduler/cron_scheduler/__init__.py:274`, `:312-315`); deployed cron uses Schedules.
- **Pattern 3:** not used. No node submits a job that calls back; Apify waits through its SDK's own polling.

**Verdict:** Misused. The Timer node implements pattern 2 with a process sleep, and inbound webhooks answer 200 without any delivery until RFC-0005 Phase 2. `ApprovedToolCallWorkflow` adopts pattern 2 correctly, with no retries by decision E12, and pattern 3 is not applicable. RFC-0005's inbound design deviates from pattern 1 on purpose: the inbox commit comes before Temporal (I2, I10), which survives Temporal outages and gives deduplication and 429/503 backpressure.

**Recommendation:**
1. **Timer node, interim fix** (activity body only):
   - Anchor the deadline to `activity.info().scheduled_time` (the first attempt's schedule time), so a retry after a crash or a restart waits only the remainder.
   - Refuse, with `NodeUserError`, a wait that does not fit one attempt (§9.3 item 2), on every path including the legacy tool path. For a tool call this caps waits near 9 minutes; in a graph, near 24 hours.
   - On a worker-shutdown cancellation raise `NodeWaitInterrupted` (retryable), and otherwise re-raise the cancellation.

   It still holds a worker slot for the whole wait. Versioning: none. When: P1, Phase 1b (1b.8). Effort S.
2. **Durable Timer.** MachinaWorkflow schedules no activity for a `timer` node and awaits `workflow.sleep` instead, for runs whose frozen input has `durable_timer_version=1`: set in `resource_manifest` by `begin_generation` (`server/services/deployment/control.py`) and in the executor's run input for manual runs (`server/services/temporal/executor.py:140-152`). The parameters come from `parameter_snapshot`, or from one small activity when a manual run has none. The node's output shape stays the same, and status broadcasts use the existing maintenance activities. Sleeps are not business work for a Stop checkpoint (`docs-internal/temporal-workflow-control.md:117-119`): Stop completes during the wait, the delay keeps counting, and the next node waits for Resume. Versioning: per-generation (and per-run) input flag, so no patch. When: P2, RFC-0005 Phase 6 (§22.3); it may land earlier, in the controller lane after Phase 1. Effort M.
3. **As an AI tool, keep the one-attempt cap.** Agents that need to wait longer use RFC-0005's alarms (`set_alarm`, Phase 6) (default, §24). Versioning: none. When: Phase 6 documents it in the tool text. Effort S.
4. **Pattern 1: RFC-0005 Phase 2 as designed.** Keep entity ids business-stable and tie the 2xx to the commit (EG9). When: P2, Phase 2.
5. **Pattern 3, if ever needed.** Where a provider can call back (Apify run webhooks, batch APIs), the callback becomes an inbox row delivered to the entity, and the submitting activity completes with the job id. A callback handler that completes a task token would call Temporal from a request path, against I2. Use task tokens only for an external process that must heartbeat or receive cancellation. When: P3.

**Remarks:**
- `webhookResponse`'s "responseNode" mode is not wired: `_pending_responses` is never filled (`server/routers/webhook.py:41`, `:51`). If it is built, it cannot wait on Temporal from the request path (I2).
- The page's pattern 3 sample opens a new `Client` per callback; reuse the app's client.
- The 5-second Undo window satisfies "not under one second".

**Sketch:** (items 1 and 2)

```python
# Item 1 (Phase 1b): server/nodes/scheduler/timer/__init__.py, TimerNode.wait
wait_seconds = _seconds(params)                        # unchanged unit arithmetic
deadline = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
if activity.in_activity():
    # Anchor to the first attempt's schedule time: a retry waits only the remainder.
    deadline = activity.info().scheduled_time + timedelta(seconds=wait_seconds)
    limit = attempt_deadline()                          # §9.3 item 2
    if limit is not None and deadline > limit:
        raise NodeUserError(f"A wait of {params.duration} {params.unit} does not fit in "
                            "one attempt. Use a shorter wait, or an alarm.")
try:
    await asyncio.sleep(max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds()))
except asyncio.CancelledError:
    if activity.in_activity() and activity.is_worker_shutdown():
        raise NodeWaitInterrupted("worker shutting down during a Timer wait") from None
    raise

# Item 2 (P2): server/services/temporal/workflow.py, MachinaWorkflow dispatch
if node_type == "timer" and workflow_data.get("durable_timer_version") == 1:
    running[node_id] = asyncio.create_task(self._durable_timer(node_id, timer_params))
    continue                                            # no activity, no admitted action

async def _durable_timer(self, node_id: str, params: dict) -> dict:
    started = workflow.now()
    await workflow.sleep(timedelta(seconds=timer_seconds(params)), summary=f"Timer {node_id}")
    return {"success": True, "result": {
        "timestamp": workflow.now().isoformat(), "duration": params.get("duration"),
        "unit": params.get("unit"),
        "elapsed_ms": int((workflow.now() - started).total_seconds() * 1000)}}
```

### 9.6 Approval

**Docs:** https://docs.temporal.io/design-patterns/approval. A workflow blocks until a person or an external system decides, with a timeout. Rules that matter:
- Block with a timeout: `await workflow.wait_condition(lambda: self.approval_data is not None, timeout=...)`, which raises `asyncio.TimeoutError`.
- The decision is a rich object (approver, decision `APPROVED` / `REJECTED` / `ESCALATED`, comments, timestamp). It arrives by Signal, or, "If the approver's interface needs immediate confirmation that the approval was accepted and valid, consider using an Update with a validator instead of a Signal."
- A Query for status; multi-level chains; escalation (on timeout, notify, then wait longer).
- Best practices: reasonable timeouts; "Validate Signal data. Verify approver permissions"; log every decision with timestamps; "Support cancellation"; "Ensure idempotency ... Signals may be duplicated"; expose approval history.
- Pitfalls: no timeout; missing validation; lost context; no audit trail; tight timeouts; boolean-only Signals; no status Query; no duplicate handling; "Design your timeout path to account for late-arriving Signals"; "No escalation path".
- The page's comparison table accepts a polling activity for external approval systems, at high complexity: "Polling Activity | Yes | Yes | Yes | High | External approval systems". The activity-execution page prefers a short activity plus a Signal for a review that "could take up to a week" (https://docs.temporal.io/activity-execution#asynchronous-activity-completion).

**SDK:** 1.30.0 has `@workflow.update` with `@<update>.validator`, `workflow.wait_condition(timeout=)`, `@workflow.query` and `workflow.all_handlers_finished`. Update ids are deduplicated per run. 1.34.0 changes nothing used here (the Update-handler rule of §8.2 does not apply: an approval never continues as new).

**OpenCompany today:**
1. **Decision semantics** (`server/services/approvals/`):
   - Rows in the app database. Each decision is a compare-and-swap on `revision`, with a `decision_key` and the actor recorded in `approval_decisions` (`server/services/approvals/decisions.py:116-196`).
   - Owner-only (`server/services/approvals/handlers.py:172-175`). Expiry is applied before any decision, so a late Send gets `expired` (`decisions.py:65-81`, `:135`).
   - Default expiry 168 hours (`server/services/approvals/contract.py:25`); the gate allows 1-720 hours (`server/nodes/workflow/approval_gate/__init__.py:69`); Undo and Restore 5 s (`contract.py:35-39`).
   - Status comes from the list handlers and the identity-only `approval_lifecycle` broadcast; the employee reads the outcome as an `[update]` note.
   - Not found: a comment or reason field, and any reminder or escalation before expiry.
2. **Held tool calls** (Ask first) wait on nothing: the tool answers that it is waiting for the owner and the agent goes on (`server/services/approvals/tool_calls.py:178-224`). Send starts `ApprovedToolCallWorkflow` from the request handler, and reverts the decision with `send_unavailable` when Temporal is down (`handlers.py:139-159`, `:199-209`). Neither `start_send` nor `claim_send` checks whether the employee is stopped, and `will_send_on_resume` is reported for gates only (`server/services/approvals/execution.py:58-70`; `handlers.py:215-216`): a held call approved while the employee is stopped goes out after the Undo window anyway (by code reading).
3. **The approvalGate node in graphs** waits inside its activity:
   - It re-reads its row at least every 10 s, woken earlier by a process-local notifier (`approval_gate/__init__.py:47-48`, `:235-277`; `server/services/approvals/waiter.py:1-6`).
   - Each attempt runs up to 24 hours with unlimited retries (`:181-185`), re-attaching to the same row by the activity's identity (`:110-123`). On worker shutdown it raises `NodeWaitInterrupted` (`:274-275`).
   - Each waiting draft holds one `triggers-event` slot: 100 per worker, 50 in local mode, shared with every other event-trigger activity (`server/services/temporal/worker.py:413-423`, `:477`).
4. **Reviewed team-job delivery** waits in three 31-day activities, each with 3 attempts:
   - `employee.job.deliver` in `EmployeeJobWorkflow` (`server/services/temporal/employee_job_workflow.py:132-139`);
   - `employee-reviewed-delivery` in `AgentWorkflow` (`agent_workflow.py:3133-3138`, `AGENT_ACTIVITY_RETRY = DEFAULT_ACTIVITY_RETRY` at `:184`);
   - `employee.job.resolve_delivery` in `EmployeeJobDeliveryWorkflow` (`employee_job_workflow.py:155-157`).

   The gate runs inline (`server/services/employees/jobs.py:376-381`), on `machina-tasks`, whose 100 slots it shares with LLM steps. Defects (by code reading):
   - A shutdown during a pending draft makes the gate raise `NodeWaitInterrupted`, which `deliver_job` turns into `RuntimeError` (`jobs.py:385-386`): a failed attempt. If the next attempt starts after the 2-minute delivery lease has expired (set and renewed every 30 s, `jobs.py:319-320`, `:358-366`), it re-attaches to the draft and uses one attempt; the third interruption fails the delivery activity and the workflow. The job row stays `delivering`, because the delivery call sits outside the code that runs `employee.job.failed` (`employee_job_workflow.py:114-126`).
   - If the next attempt starts within the lease (a quick restart, or a crash whose heartbeat timeout fires before the lease ends), it returns `{"delivered": False, "state": "delivering"}` (`jobs.py:273-275`) and completes. The workflow ends successfully with the draft still pending and nothing waiting on it.
   - Nothing expires such a stranded gate row while the generation lives: `expire_due` ends only `tool_call` rows (`server/services/approvals/reconcile.py:113-125`), and live generations keep their gate rows (`:53-63`). `reconcile_jobs.py` repairs only jobs whose lead finished without assignments.
5. **Stop and pending drafts.** Every node activity and the reviewed-delivery activities are admitted business actions (`workflow.py:865-868`; `employee_job_workflow.py:132-136`; `agent_workflow.py:3134`). Stop's checkpoint waits until admitted actions reach zero, with no deadline (`server/services/temporal/execution_control.py:114-119`; `server/services/deployment/execution_control.py:144-169`). So an employee with a pending draft stays "pausing" until the owner decides or the draft expires. Crash recovery's boot pause (`server/services/deployment/handlers.py:1511-1541`) goes through the same path and stays "pausing" too. `handlers.py:215-216` already models "will send on resume". No test covers it: `server/tests/services/approvals/` has no pause or checkpoint test.
6. **Browser "Needs you"** waits in memory in the browser activity, 600 s by default (60-1,800 s); each tool call waits up to 480 s and then answers `still_waiting` (`server/nodes/browser/browser/__init__.py:104-105`, `:210`, `:556-564`). Deliberate: Chrome dies with the process.

**RFC-0005 plan, before the amendments in §20 (RFC-0005 §10.1, §10.2, §17.5; RFC-0004 D13):** `ApprovalWorkflow` with id `approval-<id>`; an Update `decide{decision, revision, key}` and a `status` Query; timers for expiry, then the Undo window; one send activity, then `approval.decided` appended to the origin thread; it replaces `ApprovedToolCallWorkflow`, with `expire_due` as a backstop. It names held sends only: not the gate node, and none of the three delivery waits.

**Verdict:** Misused (the wait). The verdict rests on the owner's decision (RFC-0004 Q10, D13: "Human waits become an Update plus a durable timer") and on the measured costs: a worker slot per waiting draft, Stop held in "pausing" for up to the draft's expiry (168 hours by default, 720 at most), and a reviewed delivery that a restart can strand. The page's comparison accepts a polling activity for external approval systems, at high complexity, so the verdict does not rest on the page alone. The decision semantics are adopted with deviations (no comment field, no reminder or escalation). Keeping the record in the database rather than in history is correct here: history is kept 24 hours today and 7 days in RFC-0005, and T5 makes Temporal disposable. RFC-0005's `ApprovalWorkflow` follows the docs (an Update with a validator plus a durable timer) but misses the gate node, the three delivery waits, and the details in item 4.

**Recommendation:**
1. **Reviewed delivery survives restarts** (1b.9; sketch part a):
   - Give the three 31-day activities one dedicated policy, `REVIEWED_DELIVERY_RETRY = RetryPolicy(maximum_attempts=0, initial_interval=2 s, maximum_interval=1 min)` in `server/services/temporal/_retry_policies.py`, used at `employee_job_workflow.py:132-139`, `:155-157` and `agent_workflow.py:3133-3138`.
   - Let a retry re-attach to its own lease. `deliver_job` records `lease_holder` = `<workflow_id>:<activity_id>` (from `activity.info()`, stable across the activity's retries) with the lease, and treats an unexpired lease held by the same holder as its own instead of answering `delivering`.
   - When the gate's result is a wait interruption, `deliver_job` raises `NodeWaitInterrupted` instead of `RuntimeError`, so the attempt fails retryably and nothing records a failure.
   - A clean failure clears `inflight`. When a sink answers `success: False`, `deliver_job` clears the `inflight` mark and the lease, records the job `failed`, and returns that state instead of raising. Today it raises first (`jobs.py:385-386`, before `:393` clears the mark), so a clean rejection is shown as "Check whether it arrived" (`jobs.py:412-418`). Returning also keeps the unlimited policy from repeating a send the sink refused.

   A send that broke off is not retried forever: the `inflight` write-ahead mark turns a retry into `delivery_needs_review` (`jobs.py:276-281`). Versioning: none; retry policies are not compared on replay. When: P1, Phase 1b (1b.9). Effort S.
2. **Expire stranded drafts.** `expire_due` (`reconcile.py:113-125`) also expires gate rows past `expires_at`. A live gate would expire its own row at the same moment, and compare-and-swap keeps one result, so this is only a backstop for rows that nothing waits on. Versioning: none. When: P1, Phase 1b (1b.10). Effort S.
3. **T12 before Phase 7.** Two interim fixes. Reviewed delivery keeps holding Stop until item 4, because its gate and its send share one activity (default, §24).
   - **Approved sends wait while the employee is stopped** (1b.5). `claim_send` (`server/services/approvals/execution.py:101-128`) answers `{"claimed": false, "reason": "stopped"}` and leaves the row `approved` while the row's generation is not running; `recover_sends` (`server/services/approvals/reconcile.py:76`) skips such rows; Resume starts the workflow's pending sends through the same `start_send`. `will_send_on_resume` is then reported for these rows too, so the card says the message goes when the employee resumes. Rows without a deployment keep today's behaviour. Versioning: none (activity bodies and the Resume handler). When: P1, Phase 1b (1b.5). Effort S. Test: a send approved while paused is not sent and its row stays `approved`; the recovery sweep does not start it; Resume sends it once.
   - **Stop completes while a graph draft waits** (graph gates). For runs whose frozen input has `approval_wait_version=1` (set in `resource_manifest` by `begin_generation`), MachinaWorkflow still schedules an `approvalGate` activity only while the employee is running, as today, but no longer counts it in the checkpoint. Stop and the boot pause then complete while the draft waits. A decision taken while stopped is consumed by the run, and the next node, the send, waits for Resume through its own admission (`begin_action`). Versioning: per-generation input flag. When: P2, controller lane after Phase 1. Effort S. The test comes first: it should show today's "pausing" and then the fix.
4. **`ApprovalWorkflow` for all four waits** (RFC-0004 D13, T10, T12; sketch part b):
   - **Coverage.** The approvalGate node (MachinaWorkflow runs a short `approvals.open` activity, then starts a child `ApprovalWorkflow` and awaits it, gated by the generation flag `approval_wait_version=2`; the `child_workflow` dispatch kind already exists, `workflow.py:801-830`); the gate step of reviewed delivery in `EmployeeJobWorkflow` and `AgentWorkflow` (new generations); the resolution path in `EmployeeJobDeliveryWorkflow`; held tool calls, replacing `ApprovedToolCallWorkflow`. Child-result waits are excluded from Stop checkpoints, which removes the Stop block and the slot cost.
   - **Database first (T10).** `decisions.apply` stays the record and the immediate answer; the T10 deliverer then sends `decide` with the decided row and `id=decision_key` until it is applied, and the UI shows "applying". Deciding still works while the worker or Temporal is down.
   - **Validator.** The state allows the decision and the revision is newer; a stale or final request is rejected without writing history.
   - **Absolute deadlines** (`expires_at`, the reminder, `grace_until`, `restore_until`) in one timer loop (§8.4 item 1), so Undo returns to the original expiry instead of starting a fresh 168-hour wait.
   - **Late decisions.** When a deadline fires, an activity re-reads the row, and a decision committed just before the timer wins (the page's late-arriving Signal). A decision delivered after the workflow closed is answered with the row's final state (`expired`).
   - **T12 for held tool calls.** The send step keeps item 3's rule (1b.5): it claims the row only while the generation runs. The supervisor's Stop and Resume fan-out also reaches open `ApprovalWorkflow`s, whose ids come from the approval table (RFC-0005 I6), so a send decided while stopped waits without holding a slot and goes out once on Resume.
   - **Reminder** (default, §24): one attention item 24 hours before expiry (kind `approval_expiring`), from the reminder deadline; drafts whose whole lifetime is 24 hours or less get none.

   Versioning: new type; the gate child behind `approval_wait_version=2`, set by `begin_generation` from Phase 7. When: P2, RFC-0005 Phase 7 (attention items from Phase 8). Effort L. Risk: the Undo window spans Update and timer races; test them with time skipping (RFC-0004 §14).
5. **Comments and escalation.** A reason field on decisions and escalation beyond the reminder are left to the owner (open item, §24). When: P3.

**Remarks:**
- Signal or Update: the card needs immediate answers (`too_late`, `already_decided`, `approval_conflict`). Under I2 they come from the database compare-and-swap; the Update only carries the decided state into the workflow, which is why a decision works while the engine is down.
- Duplicate decisions are deduplicated twice: by `decision_key` in the database and by the Update id within the run. Update ids do not survive continue-as-new, but an approval never continues as new.
- Audit: do not rely on "All approval data is recorded in the Workflow history". Retention and T5 delete history, so `approval_decisions` stays the audit trail.
- Payload size: drafts are at most 20,000 characters (`approval_gate/__init__.py:49`), fine in an Update; pass a reference where possible (I7).
- Under T8 (workers in a second process), a decision made in the API process does not wake a gate waiting in a worker process, because the notifier is process-local (`waiter.py:1-6`). The gate then sees the decision at its next 10-second re-read. Item 4 removes the re-read.
- Browser help: a workflow-level wait would save repeated model steps, but the in-memory wait is bounded and deliberate. P3.
- Turn deadlines. Under item 4, a turn that waits on an approval child (the gate node, the delivery gate) completes no step while it waits. The 24-hour no-progress backstop (a default, §24.1; §6.4) never counts time waiting for the owner, as it never counts time parked or paused, so a draft left for more than a day does not cancel its turn.

**Sketch:** (part a: item 1, Phase 1b; part b: item 4, Phase 7)

```python
# (a) server/services/employees/jobs.py, deliver_job: a retry keeps its own lease
info = activity.info() if activity.in_activity() else None
holder = f"{info.workflow_id}:{info.activity_id}" if info else None   # same on every retry
lease = job.delivery.get("lease_until")
if lease and datetime.fromisoformat(lease) > _now() and not (holder and job.delivery.get("lease_holder") == holder):
    return {"delivered": False, "state": "delivering", "job_id": job.id}
# The claim then writes "lease_holder": holder beside "lease_until".

# After the sink call, in place of the RuntimeError at jobs.py:385-386:
if not result.get("success"):
    if result.get("error_type") == NODE_WAIT_INTERRUPTED:
        raise NodeWaitInterrupted("reviewed delivery wait interrupted")   # retryable; same lease
    await record_refusal(database, job.id, claim)   # clears inflight and the lease; state "failed"
    return {"delivered": False, "state": "failed", "job_id": job.id}

# (b) server/services/temporal/approval_workflow.py: database first, one timer; sandboxed (§17.18)
with workflow.unsafe.imports_passed_through():       # app modules
    from services.temporal._retry_policies import BOOKKEEPING_RETRY
    from services.temporal.deadlines import Deadlines   # §8.4 helper

@workflow.defn(name="ApprovalWorkflow")
class ApprovalWorkflow:
    @workflow.init
    def __init__(self, ref: dict) -> None:
        self._deadlines = Deadlines()                # §8.4 helper
        self._arm(dict(ref["row"]))                  # status, revision, deadlines in UTC

    def _arm(self, row: dict) -> None:               # Undo returns to the original expiry
        self._row = row
        for name, at in deadlines_of(row).items():
            self._deadlines.set(name, at)

    @workflow.update
    async def decide(self, row: dict) -> dict:       # the row decisions.apply committed (T10)
        self._arm(row)
        return {"status": row["status"], "revision": row["revision"]}

    @decide.validator
    def _check(self, row: dict) -> None:             # rejected requests write no history
        if self._row["status"] in FINAL or row["revision"] <= self._row["revision"]:
            raise ApplicationError("stale_or_final")

    @workflow.run
    async def run(self, ref: dict) -> dict:
        while self._row["status"] not in FINAL:
            due = await self._deadlines.wait(lambda: self._row["status"] in FINAL)
            if due:   # the database says what a deadline means now; a late decision wins
                self._arm(await workflow.execute_activity(
                    "approvals.on_deadline", {"id": ref["id"], "due": due},
                    start_to_close_timeout=timedelta(seconds=30), retry_policy=BOOKKEEPING_RETRY))
        return {"status": self._row["status"]}       # a held tool call's SEND_ONCE step is omitted
```

## 10. Distributed transactions

### 10.1 Distributed Transaction Patterns (category overview)

**Docs:** https://docs.temporal.io/design-patterns/distributed-transaction-patterns. A selection guide for work that spans services "that each own their own data, with no shared database transaction to roll back".
- "You need to undo completed steps when a later step fails": use the Saga Pattern and "define a compensation for every step that has an external effect".
- "You need to respond to the caller before the transaction finishes": use Early Return.
- Retry patterns "control how each step retries before a compensation triggers"; Update-with-Start (one call that starts a workflow and sends it an Update, a synchronous message that returns a result) is the mechanism behind Early Return.

**SDK:** no dedicated API. A saga is a list of callables in Python (there is no Java-style `Saga` helper). Early Return uses `Client.execute_update_with_start_workflow` or `start_update_with_start_workflow` with `WithStartWorkflowOperation`. All of it exists in 1.30.0 and in 1.34.0.

**OpenCompany today:** multi-step flows use four techniques, roughly in order of preference.
- **One local transaction plus an outbox** (a row that a background loop turns into the next step):
  - Hire writes the graph, parameters, employee row, grants and an activation intent `hire:<id>` in one reserved transaction (`server/services/employees/store.py:140-200`, intent at `:192`). The start runs from the intent, in the background and from a 10-second recovery loop (`server/services/employees/hire.py:292-297`; `server/services/employees/activation.py:13-75`).
  - Chat admission writes the owner's message and its run in one reserved transaction (`server/services/chat/ledger.py:252-331`).
  - A team job writes its `EmployeeJob` row first, then starts with `REJECT_DUPLICATE` and the row id; the same loop re-drives it (`server/services/employees/jobs.py:128-187`, `:208-252`).
  - Context archive receipts are written with the graph save and drained idempotently later (`server/services/workflow_storage/handlers.py:90-135`).
- **Irreversible step last:** deleting a workflow runs Reset first and deletes only after Reset succeeds (`server/services/workflow_storage/deletion.py:26-84`). An approved send waits out its Undo window, claims its row at a revision, sends once and records `sent`, `not_sent` or `unknown` (`server/services/temporal/approved_tool_call_workflow.py:41-79`).
- **Forward recovery with a write-ahead mark:** reviewed job delivery marks a sink `inflight` before calling it; an expired mark becomes `delivery_needs_review` (`server/services/employees/jobs.py:276-281`, `:351-357`).
- **Compensating sagas:** Start (`server/services/deployment/handlers.py:2157-2186`), Apply's rollback (`server/services/employees/safe_apply.py:246-279`), adding an MCP connector (`server/nodes/mcp/_store.py:175-183`; `server/nodes/mcp/_handlers.py:91-106`), an approval decision (`server/services/approvals/handlers.py:139-159`) and `DelegatedTaskWorkflow` (`server/services/temporal/agent_workflow.py:535-728`).

**Verdict:** Adopted with deviations. The technique chosen for each flow is sound; the deviations are in how the compensating sagas are written (§10.2).

**Recommendation:**
1. Write OpenCompany's rule into §5 and `docs-internal/DESIGN.md`:
   - prefer one local transaction plus an outbox;
   - put irreversible effects last;
   - use a saga only where several systems change;
   - guard an effect that has no compensation (a sent message, a refund) before the pivot with an Undo window and a claim at a revision, and after it report `unknown` (E12, extended to every side-effecting tool by T13), never a compensating "unsend";
   - owner actions write an intent, answer, and let a deliverer apply them (T10).
   - Versioning: none (docs). When: §5 now; `DESIGN.md` in Phase 1b (1b.45). Effort S. Risk: none.

**Remarks:**
- Most OpenCompany effects cannot be compensated. The Saga page lists "processes where compensations cannot be defined" as not a good fit; Undo windows, claims at a revision and `unknown` are the right answer.
- Idempotency keys: the Fixed Count page asks for "a stable identifier derived from the Workflow and Activity IDs". RFC-0004 §9.4 forbids the run id, which changes across continue-as-new (ending a run and starting a fresh one with the same workflow id), and RFC-0005 §7.2 and §21.3 follow that. Every key this RFC adds uses the workflow id and the activity id.

### 10.2 Saga

**Docs:** https://docs.temporal.io/design-patterns/saga-pattern. A saga runs a distributed transaction as a sequence of local steps. Each step with an external effect has a compensation, and on failure the compensations run "in reverse order". Rules:
- Register each compensation before its step runs ("recommended for safety"), so it also covers a step that failed after a partial effect. "All compensations must be idempotent and able to handle cases where the forward Activity never executed."
- Run compensations last in, first out, then "Re-throw the original error after compensating".
- "Log compensation failures but continue"; "In production, alert for manual intervention on persistent compensation failures."
- "Set a `StartToCloseTimeout` but avoid `ScheduleToCloseTimeout` on compensations. Do not set Workflow-level timeouts — let compensations retry until they succeed." Pitfall: "Compensations that can fail permanently ... Design compensations with generous retry policies."
- "Use idempotency keys for forward Activities"; "Keep compensation payloads small" (the 2 MB payload limit).
- "Temporal's durable execution guarantees that compensations will execute even after Worker failures." Not after termination: terminating a workflow runs none of its code.
- Not a good fit for "single-service transactions that can use a local database transaction".
- Python: a list plus `reversed()`. The page catches `Exception`, which does not include `asyncio.CancelledError`; a Python workflow that must clean up after cancellation catches it separately and shields the cleanup.

**SDK:** plain workflow code: `workflow.execute_activity`, and `asyncio.shield` for cleanup after cancellation. Nothing to add in 1.30.0. Release notes for SDK 1.28 and 1.31 list fixes to shielded cancellation (unverified here), so the 1.34.0 upgrade's replay corpus should include a cancelled delegation and a cancelled team job.

**OpenCompany today:**
- **`DelegatedTaskWorkflow` is a textbook saga** (`server/services/temporal/agent_workflow.py:535-728`). Forward steps register the task, acquire a permit, begin, start the agent and finish (`:553-623`). On cancellation it cancels the task whether or not `begin` ran, because "Queue/acquire/begin may have completed even when their result lost the cancellation race" (`:647-651`), and it releases the permit in `finally` (`:703-721`). Deviations:
  - cleanup uses `DELEGATION_CLEANUP_RETRY`, 10 attempts at 1-30 s (`server/services/temporal/_retry_policies.py:112-116`). It gives up after about 2.5 to 7.5 minutes and logs; the task row stays non-terminal and the permit stays held until the 3-hour age reclaim (`server/core/database.py:4153-4183`);
  - the `except Exception` arm calls `finish_delegation` unguarded (`:692-701`), so its failure replaces the original error.
- `AgentWorkflow`'s `_cleanup_cancelled_delegations` cancels every owned task, then releases every permit, logging and continuing per step (`agent_workflow.py:1525-1566`): the right shape, with the same capped policy.
- `EmployeeJobWorkflow` runs a shielded `employee.job.failed` on any exception with `RetryPolicy(maximum_attempts=3)` (`server/services/temporal/employee_job_workflow.py:121-130`). A short database outage leaves the job `running`; Apply then treats it as live work (`server/services/employees/safe_apply.py:14-31`) until the owner picks Stop work.
- `workflow_runs.record_completion` in `NodeInvocationWorkflow` is capped at 3 attempts (`server/services/temporal/node_invocation.py:85-99`).
- **Reset terminates, so none of these compensations runs.** Reset's sweep calls `terminate` (`server/services/deployment/handlers.py:974-1017`; strict at `:2591`). Reset runs database hooks instead (`reset_execution_state`, `server/services/deployment/runtime_state.py:39-56`), implemented for the browser, browser agent, chat reply, chat trigger, context, memory, mobile and approval gate nodes, and for no team, team task, employee job or permit. Those rows stay non-terminal, and the next generation's Apply answers "waiting", because `safe_apply._active` reads them by workflow id (`safe_apply.py:14-31`). RFC-0004 H12 found this; D9 plans cancel, a bounded grace period, then terminate.
- **Start compensates in the API process** (`handlers.py:2122-2186`). On failure it fails the control row, signals the controller to reset, terminates the generation's workflows, cancels the local deployment, broadcasts and re-raises. It is not in reverse order. If `service.fail` loses its compare-and-swap, the rest is skipped. What to undo is found through Visibility (Temporal's search index of executions, `handlers.py:844-900`), which is eventually consistent, although every resource has a known id (the controller is `workflow-control-<workflow_id>-g<generation>`).
- **Reset is idempotent but not resumed.** A latched `resetting` state and strict steps let a repeated Reset continue where it stopped, but boot reconcile leaves `resetting` rows as they are (`_reconcile_control`, `handlers.py:1223-1260`); the owner must press Reset again.
- **Apply is the largest saga** (`safe_apply.py:34-289`): a durable intent with the previous snapshot, a renewed lease, forward steps, a rollback (`:246-279`) and the 10-second sweeper. Undo data is captured before each step. But the rollback is not the reverse of the forward order, and every rollback step re-checks the lease (`:162-167`), so a lost lease aborts the rollback half-way; the sweeper then re-runs the forward path from the top (`:285-289`).
- Adding an MCP connector writes two rows to the same credentials database and removes the first if the second fails (`server/nodes/mcp/_store.py:175-183`). One local transaction would do. An orphaned URL row is harmless, because `exists` reads the sign-in row (`:157-158`).
- An approval decision moves the row, then starts the send; a failed start reverts the row (`server/services/approvals/handlers.py:139-159`). An ambiguous start is harmless: the send claims only at the revision it was started for (`server/services/approvals/execution.py:101-128`).
- `ApprovedToolCallWorkflow` has no `finally` around the send. A cancelled or terminated send leaves the row `sending` until `recover_sends` marks it `unknown` after 30 minutes, and that sweep runs once per process, on the first draft listing (`server/services/approvals/reconcile.py:76-110`, `:131-141`; `server/services/approvals/handlers.py:84`). Reset never reaches this workflow, because it carries no `EventWorkflowId` (`server/services/approvals/execution.py:58-70`).
- Dead code with a recovery hole: `restart_with_latest_graph` (`server/services/deployment/restart.py:72-101`) runs Reset and then Start with no intent row. Apply and Turn on Talk call `apply_saved_changes` instead (`server/services/employees/handlers.py:222-223`, `:232-233`, `:276-277`). Only its test, `resources/builder_documentation.json`, CLAUDE.md's Normal Mode row and `docs-internal/normal_mode.md:839` still mention it.

**Verdict:** Adopted with deviations. `DelegatedTaskWorkflow` follows the page. The service-side sagas are not last in, first out and stop half-way, compensations give up after 3-10 attempts, and Reset terminates, so workflow compensations never run.

**Recommendation:**
1. **Compensations retry until they land.** Make `DELEGATION_CLEANUP_RETRY` unlimited (1 s to 30 s). Give both `employee.job.failed` calls and `workflow_runs.record_completion` an unlimited bookkeeping policy, the one `ApprovedToolCallWorkflow` already uses (`BOOKKEEPING_RETRY`, `approved_tool_call_workflow.py:26`), moved into `_retry_policies.py`. Guard `finish_delegation` in the `except Exception` arm (`agent_workflow.py:692-701`) so its failure is logged and the original error is re-raised.
   - Versioning: none. Retry policies are activity options, which replay does not compare (RFC-0004 §8.5); the replay corpus must stay green.
   - When: Phase 1b (1b.20). Effort S.
   - Risk: a broken compensation retries until it is fixed, so it needs the retry alert (§11.7) and the rewording of RFC-0004's gate AG9 (§21), which today forbids an unlimited policy without `schedule_to_close_timeout` (a total time budget across all attempts). The Saga page says the opposite for compensations, and T4 says reliability wins.
2. **Reset closes what terminated workflows cannot.** Add `reset_execution_state` to `TaskManagerNode` (`server/nodes/tool/task_manager/__init__.py`): it cancels the workflow's open team tasks (`cancel_team_task` is already idempotent and guards terminal states, `server/core/database.py:3845-3860`), dissolves its active teams and releases their subagent permits. Reset also fails the workflow's non-terminal `EmployeeJob` rows with reason `reset`, from `services/employees`, which creates them. With the rows closed, `safe_apply._active` stops reporting live work.
   - Versioning: none (database only). When: Phase 1b (1b.21). Effort S-M.
   - Risk: a hook that misses a row type; the test lists every non-terminal state.
   - T14 makes this essential: a generation whose histories cannot replay is marked "needs Reset", its workflow compensations can never run, and these hooks are its only cleanup.
3. **Start's compensation runs in reverse order, by known ids, each step logged and continued:** cancel the local deployment, terminate the generation's workflows (the controller by its known id first, then the Visibility sweep for descendants), signal reset, then fail the row. A lost compare-and-swap on the row no longer skips the rest.
   - Versioning: none (API process). When: Phase 1b (1b.22). Effort S.
   - Risk: mistakes appear only on failure paths; the test injects a failure at each step.
   - New generations get the rest with RFC-0005 §9.3 (subscriptions compiled in the transaction that creates the `starting` generation, §7.2) and RFC-0005 §15 (Schedules addressed by id).
4. **Resume an interrupted Reset:** boot reconcile drives `resetting` rows through `handle_reset_workflow` at the row's revision instead of leaving them. Versioning: none. When: Phase 1b (1b.23). Effort S. Risk: a Reset that fails on every boot stays visible, as it is today.
5. **Run `recover_sends` from the 10-second recovery loop** (`server/services/employees/activation.py:55-75`) instead of once per process, so a cancelled send always becomes `unknown` within about 30 minutes. Versioning: none. When: Phase 1b (1b.6). Effort S. RFC-0005 §17.5's ApprovalWorkflow replaces it later.
6. **Specify Apply for `event_os_version=1`.** RFC-0005 does not specify Apply: it names it only as an owner action (I2) and a Phase 4 item, and never mentions `replace_graph` or `pending_changes`. With subscriptions and the snapshot in the database, Apply becomes one local transaction (snapshot, subscriptions, revision), the supervisor's hold and release Updates, and Schedule updates by id: a much smaller saga. Following T10, the owner's Apply writes an intent, answers "applying", and the deliverer drives the steps until they are applied.
   - Versioning: new workflow types. When: RFC-0005 Phase 4 (amendment, §20). Effort M.
   - Risk: two Apply paths until old generations are retired (R_k+1).
7. **Cleanups:** add the MCP connector in one `AuthService` transaction; delete `restart_with_latest_graph` and its test; fix CLAUDE.md's Normal Mode row and `docs-internal/normal_mode.md:839`. Versioning: none. When: later. Effort S.

**Remarks:**
- Do not add `schedule_to_close_timeout` or a workflow timeout to compensations (the page; RFC-0005 §27 keeps workflow timeouts unset). Unlimited compensation retries are bounded in practice by Reset, which under RFC-0004 D9 cancels, waits a grace period and then terminates.
- The shielded finalizers (`employee_job_workflow.py:124`; `node_invocation.py:84`, `:102`) fit the cancellation guide's "execute any cleanup logic" after a cancel.
- Under T10 and T12 the approval decision stops being a saga: the decision is an intent the deliverer sends, and a decision taken while the employee is stopped is recorded at once while its send waits for Resume (T12, 1b.5). The revert-on-failed-start branch then goes away (§9.6; RFC-0005 §17.5).
- Under T10, Start and Apply run from intent rows the way a hire's activation already does; the compensation runs inside the deliverer, which retries until the row reaches a terminal state.
- Compensation payloads are small: ids and short lifecycle dicts. Apply keeps its snapshots in the database.

**Sketch** (recommendation 2; helper names other than `cancel_team_task` and `update_team_status` are illustrative):

```python
# server/nodes/tool/task_manager/__init__.py
class TaskManagerNode(ToolNode):
    type = "taskManager"

    @classmethod
    async def reset_execution_state(cls, *, node_id, workflow_id, execution_id,
                                    generation, graph, database) -> dict:
        """Reset terminates the generation's workflows, so DelegatedTaskWorkflow
        and AgentWorkflow never run their own cancel-and-release. Close what
        they would have closed. Idempotent: a second Reset finds nothing open."""
        cancelled = released = 0
        for team in await database.list_active_teams(workflow_id):       # status == "active"
            for task in await database.get_team_tasks(team["id"]):
                if await database.cancel_team_task(team["id"], task["id"], reason="reset"):
                    cancelled += 1                                         # terminal rows are kept
            released += await database.release_subagent_permits(
                root_execution_id=team["root_execution_id"])
            await database.update_team_status(team["id"], "dissolved")
        return {"reset": bool(cancelled or released),
                "cancelled_tasks": cancelled, "released_permits": released}
```

### 10.3 Early Return

**Docs:** https://docs.temporal.io/design-patterns/early-return. Answer the caller as soon as a fast initialization succeeds while the slow phase continues in the background, using Update-with-Start. Rules:
- The Update handler only waits (`await workflow.wait_condition(lambda: self.init_done)`); `run()` does the initialization, ideally as a local activity "under 5 seconds", and then the slow phase.
- "A `WorkflowIdConflictPolicy` must be specified." Use `FAIL` for one workflow per request; "Use `USE_EXISTING` only for lazy initialization patterns."
- "Update-with-Start is **not atomic**": the workflow may start without the Update. "Set a timeout on the Update result"; "Provide a unique Update ID ... so retried requests attach to the same Update"; "The default `maxInFlightUpdates` is 10 per Workflow"; "Avoid Workflow timeouts".
- Pitfalls: "Blocking too long in the Update handler"; using it for fire-and-forget work ("use Signals").

**SDK:** `Client.execute_update_with_start_workflow` and `start_update_with_start_workflow`, `WithStartWorkflowOperation(..., id_conflict_policy=...)` and `rpc_timeout=` all exist in 1.30.0 (`temporalio/client/_client.py:930-1043`) and keep the same parameters in 1.34.0. `rpc_timeout` bounds each request, not the call: the client repeats its request until the Update is accepted, then polls for the outcome without a deadline (1.30.0 `temporalio/client/_impl.py:973-1003`). Only `asyncio.timeout` around the whole call bounds the wait (§7.3).

**OpenCompany today:**
- **Workspace tasks use the page's lazy-initialization form:** Update-with-Start on a long-lived controller per workflow, with `USE_EXISTING` (`server/services/node_invocations.py:44-74`).
  - Its state is set in `@workflow.init` because "Update-With-Start can invoke a handler before run()" (`server/services/temporal/workspace_tasks_workflow.py:39-53`).
  - Admission is a projection activity plus the child's start acknowledgement (`:97-125`); a duplicate is caught by run id and fingerprint (`:89-94`); at most 32 tasks run at once (`:20`, `:95-96`).
  - The Update id has a random suffix on purpose, so a refused submission is not cached (`node_invocations.py:249-254`). The controller's own dedup gives the idempotency the page wants from a stable Update id.
- Deviations:
  - **no deadline:** neither `_controller_update` nor the HTTP routes set one (`server/routers/browser_tasks.py:40-50`; `server/nodes/mobile/_router.py:251-255`), so with no worker polling, a submission hangs;
  - the handler runs an activity and a child start under a lock instead of only waiting, so concurrent submissions queue as in-flight Updates (limit 10), and only `WorkflowUpdateFailedError` is mapped to a user message (`node_invocations.py:60-74`);
  - refusals are raised inside the handler, not in a validator, so a refused submission still costs history (RFC-0004 L3);
  - the controller rolls over only when idle (`workspace_tasks_workflow.py:195-206`).
- **Chat, hire and team jobs answer after a database commit** and start their Temporal work in the background (`server/services/chat/handlers.py:233-341`; `server/services/employees/hire.py:292-297`; `server/services/employees/jobs.py:208-252`). This is a database-first early return. RFC-0005 keeps it and rejects ingress straight to Temporal (RFC-0005 §9.7), and T10 extends it to owner actions. It is the right choice for E4 and I10: an Update-with-Start would refuse or hang whenever Temporal or a worker is down.
- **Start is synchronous:** the controller starts empty and triggers arrive as Signals (one-way messages to a running workflow) afterwards (`server/services/deployment/manager.py:971`), so a crash in between leaves an empty controller that boot converges (`handlers.py:1708-1764`). RFC-0005 §9.3 removes the gap.

**Verdict:** Adopted with deviations. The one Update-with-Start caller follows the page's lazy-initialization form but never bounds its wait; the database-first answers elsewhere are the better fit for OpenCompany (E4, I10, T10).

**Recommendation:**
1. **Bound the Workspace submission.** §7.3 owns the fix (1b.15): every client Update call runs under `asyncio.timeout`, the Workspace Update-with-Start included (about 15 s). On this route, an expired deadline, or the server's refusal when 10 Updates are already in flight, answers "submitted, still pending: check the task list". The stable `submission_id` makes a resubmission safe, because the controller dedups by run id and fingerprint.
   - Versioning: none (client). When: Phase 1b (1b.15). Effort S.
   - Risk: a "pending" answer for a submission that later succeeds; the task list shows it.
2. **With T10's deliverer, put submissions behind it:** write the `workspace_tasks` record as the intent, answer "applying", and let the deliverer send the Update-with-Start. It keeps a random Update id per attempt, because the submit checks can fail after acceptance (§7.3 R3); the controller's dedup by run id and fingerprint makes a resend safe. A refusal (queue full, resetting) then ends the intent visibly.
   - Versioning: none (client). When: with T10. Effort M.

**Remarks:**
- A Workspace controller that never idles cannot roll over: its children use `REQUEST_CANCEL` (`:114-121`) so that Reset can cancel them and wait, and rollover waits until no task is active. It grows by about 10 events per submission (est.), and `_OPERATIONS_PER_RUN = 256` and the 10,000-event soft cap apply only when it is idle. Add a fixture with one long task and many submissions (P3). Abandoning children, as Sliding Window does (§12.4), would give up Reset's barrier.
- Moving the controller's refusals into an Update validator (RFC-0004 L3) belongs to §7.3.
- Running `workspace_tasks.admit_record` as a local activity, as the page advises for the init phase, is not worth it: RFC-0004 D17 lists local activities as not adopted.
- RFC-0005's relay uses Signal-With-Start (RFC-0005 §9.4), the page's fire-and-forget case; the ApprovalWorkflow `decide` Update (RFC-0005 §17.5) is request-response on a running workflow, not Early Return.

## 11. Error handling and retries

### 11.1 Error Handling & Retry Patterns (category overview)

**Docs:** https://docs.temporal.io/design-patterns/error-handling-patterns. A decision tree for a failing activity:
- each attempt costs money or quota: Fixed Count of Retries;
- the error will never succeed by itself: Resumable Activity if a human can correct it, otherwise Non-Retryable Errors;
- the downstream has a predictable unavailability window: Delayed Retry;
- it "Must resolve within a time budget": Fixed Wall-Time Retries (`ScheduleToCloseTimeout`);
- recover fast, then wait patiently: Fast/Slow Retries;
- "For any long-running retry scenario, add **Retry Alerting via Metrics**."
- Defaults: "Temporal's default `RetryPolicy` retries Activities indefinitely with exponential backoff": `MaximumAttempts` 0 (unlimited), `InitialInterval` 1 s, `BackoffCoefficient` 2.0, `MaximumInterval` 100 times the initial interval, no non-retryable types. "`ScheduleToCloseTimeout` is set on the Activity call options, not in `RetryPolicy`."
- The tree calls Delayed Retry "Fixed interval backoff"; the Delayed Retry page itself is about the per-error `next_retry_delay`. Follow the page.

**SDK:** `RetryPolicy` is identical in 1.30.0 and 1.34.0 (AST comparison of `temporalio/common.py`). `workflow.execute_activity` takes the same keyword arguments, except that 1.34.0 adds the experimental `event_groups`.

**OpenCompany today:**
- **Where Temporal sees an error.**
  - Plugin code returns every exception as a result envelope: `PermissionError`, `NodeUserError`, `NodeWaitInterrupted` and any other `Exception` (`server/services/plugin/base.py:692-768`). `NodeExecutor` turns anything else into a failure dict, including `asyncio.CancelledError` for every class except `workspace_task` ones (`server/services/node_executor.py:231-244`).
  - So for graph nodes and agent tools, Temporal retries only a lost worker (after the 2-minute heartbeat timeout, the longest a running activity may go without reporting that it is alive), an expired start-to-close timeout, `NodeWaitInterrupted` (re-raised as a retryable error, `base.py:1355-1366`) and a crash in the activity wrapper (`:1398-1410`). A plugin's `retry_policy` and its `non_retryable_error_types` apply to nothing else.
  - Framework activities raise typed `ApplicationError`s: the LLM step (`server/services/temporal/agent_activities.py:246-303`), Pi (`server/services/temporal/pi_activities.py:27-47`), the controller's Update validators and compaction.
  - `MachinaWorkflow` and `AgentWorkflow` turn an activity or child failure into `{"success": False, ...}`. From the exception they read only `requires_user_action` (behind a patch in `MachinaWorkflow`), never `retry_state` or the timeout type (`server/services/temporal/workflow.py:1080-1133`; `agent_workflow.py:1258-1340`). Runs end Completed with `success: False` (`workflow.py:913-930`), although the owner chose Temporal Failed (RFC-0004 Q4).
- **Retry and timeout inventory:**

  | Activity | Attempt timeout | Retry policy | Total budget |
  |---|---|---|---|
  | graph node `node.{type}.v{n}` (`workflow.py:837-869`) | 24 h, heartbeat 2 min | the plugin's, default 3 attempts at 1-60 s (`server/services/plugin/scaling.py:58`) | none |
  | agent tool, frozen path (`agent_workflow.py:117-122`, `:143-155`) | plugin value | frozen plugin policy | none |
  | agent tool, legacy path: manual canvas runs and generations without execution control (`agent_workflow.py:113-140`) | 10 min | none: the server default, unlimited | none |
  | `agent.execute_llm_step` (`agent_workflow.py:3183-3188`) | 10 min, heartbeat 1 min | `LLM_STEP_RETRY`: unlimited, 5 s to 5 min (`_retry_policies.py:94-100`) | none |
  | `agent.compact_context`, a paid LLM call (`agent_workflow.py:2829-2835`) | 5 min, no heartbeat | 3 attempts | none |
  | `agent.acquire_subagent_permit` (`agent_workflow.py:569-578`) | 1 h, heartbeat 10 s | `PERMIT_WAIT_RETRY`: unlimited | none |
  | approved send (`approved_tool_call_workflow.py:59-65`) | 10 min | `SEND_ONCE`: 1 attempt | none |
  | approval bookkeeping; `workspace_tasks.update_record` | 30 s | unlimited | none |
  | `employee.job.deliver`, `employee.job.resolve_delivery` (`employee_job_workflow.py:132-139`, `:155-157`) | 31 days | 3 attempts | none |
  | controller queue read and spill (`workflow_control_workflow.py:402-406`, `:629`) | 60 s | `DEFAULT_ACTIVITY_RETRY`: 3 attempts | none |
  | `store_node_output_activity`; graph load (`workflow.py:618-626`; `trigger_listener_workflow.py:360-364`) | 10 s | none: unlimited | none |

  Outside any policy, the controller puts a failed spawn back at the head of its queue and sleeps 5 s, without limit (`workflow_control_workflow.py:454-480`).
- **No metric leaves the process:** both Temporal runtimes configure logging only (`server/services/temporal/worker.py:91-102`; `server/services/temporal/client.py:76-83`).
- The lint against inline policies covers five files (`server/tests/test_retry_policies.py:89-95`); five other workflow modules build policies inline: `approved_tool_call_workflow.py`, `employee_job_workflow.py`, `node_invocation.py`, `pi_workflow.py` and `workspace_tasks_workflow.py`.
- The in-process `WorkflowExecutor`, used only when `REDIS_ENABLED=true` (`server/services/workflow.py:561-562`), decides retries by matching substrings of the error message (`server/services/execution/models.py:80-101`): the Non-Retryable page's "message instead of type name" pitfall.
- **The tree applied to each failure class:**

  | Failure | The tree says | Today | Changes in |
  |---|---|---|---|
  | provider 429, 5xx or connection error on an LLM step | Fast/Slow plus alerting; Delayed Retry for Retry-After | unlimited, 5 s to 5 min; Retry-After honoured; no alert | §11.3, §11.7, §11.8 |
  | ambiguous loss mid-call (crash, timeout) | Fixed Count or Wall-Time plus an idempotency key | unlimited; gateway calls replay at $0 | §11.3 |
  | provider account problem or missing key | Resumable | non-retryable: the run fails and the deployment pauses at once | §11.8 (T11) |
  | transient error inside a plugin | Delayed Retry, Fast/Slow | an envelope: the node fails at once | §11.4 (after RFC-0004 Q9) |
  | side effect with an unknown outcome | Fixed Count 1 plus an owner decision | refused only for some agent tool calls | §11.2 (T13) |
  | bug inside an activity | Non-Retryable | retries forever under unlimited policies | §11.4 |
  | bookkeeping write fails | unlimited plus alert | unlimited, or 3-10 attempts | §10.2, §11.7 |

**Verdict:** Adopted with deviations. The LLM, Pi and controller boundaries classify errors well. Graph nodes and tools hide every error from Temporal, unlimited policies have no outer bound, and nothing alerts.

**Recommendation:**
1. **Make the tree a test.** `server/tests/test_retry_policies.py` iterates every module behind `server/services/temporal/workflow_registry.py` and fails on:
   - an activity call with no explicit `retry_policy`;
   - an inline `RetryPolicy(...)` (shared constants from `_retry_policies.py` only; add `BOOKKEEPING_RETRY` and `QUEUE_PAGING_RETRY` there);
   - an unlimited policy that is neither bounded by `schedule_to_close_timeout` nor on the declared allowlist of §5 rule 16: idempotent bookkeeping, compensations, queue paging, permit waits, T7's poll activities (each retry is the next poll), and calls a claim makes at-most-once (egress sends; reviewed delivery's `REVIEWED_DELIVERY_RETRY` through its `inflight` mark, 1b.9), with the retry alert (§11.7) covering all but the planned retries of polls and permit waits; plus the LLM step and compaction on generations without `scheduler_version=1`, which keep unlimited retries under the retry alert (T18, §11.3).
   - Replace the five modules' inline policies with the constants, and give `store_node_output_activity` and the graph-load activity an explicit `BOOKKEEPING_RETRY` (both are idempotent database calls).
   - Versioning: none (options; the replay corpus stays green). When: Phase 1b (1b.41). Effort S.
   - Risk: the constants carry the shared non-retryable list, so `NodeUserError` and `InvalidEvent` now fail fast where three attempts were spent. That is the intent.
2. **Retire the substring classifier** with the in-process path (RFC-0005 Phase 10). Effort S.

**Remarks:**
- **AG9 is stricter than the docs.** The retry-metrics page's own sample is unlimited and alerted, and the Saga page wants compensations retried until they succeed. Proposed AG9 (§21): no policy on a paid or side-effecting call is unlimited without a time budget, except the LLM step and compaction on generations without `scheduler_version=1`, which keep unlimited retries under the retry alert (T18, §11.3). Every other unlimited policy (bookkeeping, compensations, queue paging, permit waits, T7's poll activities, and calls a claim makes at-most-once: egress sends and reviewed delivery) is declared, and the retry alert covers all of them except the planned retries of polls and permit waits (§5 rule 16, §11.7).
- **What needs versioning in this section.** Retry policies, timeouts (adding `schedule_to_close_timeout` included), task queues and cancellation types are options of an existing command. When Core matches a recorded activity-scheduled event it compares only the activity id and type (1.34.0 Core `activity_state_machine.rs:382-406`; RFC-0004 §8.5 for 1.30.0), and runs in flight pick up new values for activities scheduled after the upgrade. A changed cancellation type still needs a patch wherever a replayed history can contain a cancel of that activity (§5 rule 6, §6.2). Activity-side changes (raising instead of returning, `next_retry_delay`, the BENIGN category, which logs a failure at DEBUG and records no failure metric, metrics, the redo guard) need no versioning. New workflow commands (a second activity call, a park timer, a dead-letter activity) need a gate: `workflow.patched` inside a shared type, or a generation flag in `resource_manifest`, frozen into the workflow input at Start (RFC-0005 §24.1 rule 2; `scheduler_version=1` for the park).

### 11.2 Fixed Count of Retries

**Docs:** https://docs.temporal.io/design-patterns/fixed-count-retries. Cap attempts "when each attempt consumes a paid API call, a rate-limited token, or any scarce resource where unbounded retries translate directly to unbounded cost". Rules:
- `RetryPolicy(maximum_attempts=N)` counts the first attempt ("`maximum_attempts=3` means one attempt plus two retries"). When the attempts are used up, the workflow receives `ActivityError` with `retry_state == RetryState.MAXIMUM_ATTEMPTS_REACHED` and should handle it: "alert, compensate, or escalate".
- `maximum_attempts=1` disables retries for a call where a second attempt "would cause a duplicate side effect such as a double charge or a duplicate email". But "If a Worker crashes after the API call succeeds but before the result is recorded, Temporal will not retry — the call is lost." And: "An idempotency key (a stable identifier derived from the Workflow and Activity IDs) lets the downstream system detect and discard duplicates ... If you have idempotency keys, there's little need to cap retries at 1."
- Best practices: match the cap to the cost model; always set `StartToCloseTimeout`; "Use idempotency keys"; prefer non-retryable errors for structural failures.
- Pitfalls: confusing attempts with retries; a low cap without a per-attempt timeout; ignoring the `ActivityError`; "Disabling retries on operations without safeguards ... results in a permanent gap."

**SDK:** `RetryPolicy`, `ActivityError.retry_state` and `RetryState.MAXIMUM_ATTEMPTS_REACHED` exist in 1.30.0 (`temporalio/exceptions.py:266-288`, `:337`) and are unchanged in 1.34.0.

**OpenCompany today:**
- **Owner-approved sends: correct.** `SEND_ONCE` (`maximum_attempts=1`) plus a claim token made from the workflow and run ids (`approved_tool_call_workflow.py:29-39`). An `ActivityError` is recorded as `broke_off`, which becomes outcome `unknown` (`:58-72`; `server/services/approvals/execution.py:148-155`), and the owner's Retry asks for confirmation when the outcome is `unknown` (`server/services/approvals/decisions.py:164-179`). This is the page's "Disabling retries entirely" with the "call is lost" case made explicit.
- **Some agent tool calls: correct.** A later attempt answers `SendOutcomeUnknown` instead of sending again (`server/services/approvals/tool_calls.py:367-379`, called at `server/services/plugin/base.py:1220-1238` and `server/services/coding_workspace_tools.py:111`). This covers plugins with an `ApprovalSpec` whose `sends()` counts the call and that are not `restrict_while_asking` (so not the Browser): Discord, WhatsApp, WhatsApp Business, Telegram, email, Gmail, Outlook mail, Google and Microsoft Calendar, Google Drive, Stripe, Dodo Payments' money operations, and MCP tools whose per-tool Ask is on.
- **Not covered.** Each of these runs again on attempt 2, which after a crash comes once the 2-minute heartbeat timeout fires:
  - **every send made as a graph node**, including the reply node after `approvalGate` in every app employee that Hire builds. Graph nodes keep the plugin default of 3 attempts (`scaling.py:58`; `base.py:270`; honoured at `workflow.py:840-844`), and `resend_refusal` returns early for anything that is not an agent tool call or an approved send (`tool_calls.py:375-376`). **This contradicts E12.** A graceful shutdown does not re-send; it records the node as failed ("Cancelled");
  - agent tool calls to side-effecting plugins without a spec: HTTP Request, GitHub, Vercel, Cloudflare, AWS, gcloud, shell, Dodo's unheld writes such as `create_customer`, and MCP tools whose per-tool Ask is off;
  - no plugin passes a provider idempotency key (grep over `server/nodes`); Dodo's API has none (`server/nodes/dodo_payments/_sdk.py:13`).
- **A cancel during an approved send is recorded as `not_sent`.** `NodeExecutor` returns `{"success": False, "error": "Cancelled"}` (`node_executor.py:231-239`), which `ApprovedToolCallWorkflow` records as `not_sent` (`:66-69`), so the owner's Retry asks no confirmation (`decisions.py:167-168`) although the message may have gone out.
- **Declared caps are ignored on the legacy tool path.** CLI agents declare `maximum_attempts=1` (`server/nodes/agent/claude_code_agent/__init__.py:257`, and `codex_agent`, `rlm_agent`, `mobile_use_agent`), and `MachinaWorkflow` honours it, but the legacy tool path passes no policy (`agent_workflow.py:113-140`). On manual canvas runs:
  - a Claude Code session longer than 10 minutes is cut off and started again, without limit;
  - an Apify actor run longer than 10 minutes starts a new paid run every 10 minutes, without limit; the run id is never checkpointed (`server/nodes/scraper/apify_actor/__init__.py:287-291`). On new generations the frozen policy gives Apify 3 attempts, so up to 3 paid runs;
  - a long Timer never ends.
- **The LLM step** is paid per attempt with the owner's own keys and retries without limit. Gateway calls carry a replay namespace per workflow, run and activity, so a retry of a call that already completed upstream replays at $0 for 48 hours (`server/services/gateway/attribution.py:12-14`, `:45`, `:72-86`): the page's idempotency key. Own-key calls have none; the code accepts re-billing an ambiguous loss (`_retry_policies.py:82-93`).
- **Compaction** is a paid call with 3 attempts and no heartbeat (`agent_workflow.py:2829-2835`); a burst of 429s ends the run with `CompactionError` (`:2866-2882`).
- **TikHub's SDK retries inside each Temporal attempt:** up to 3 requests per attempt (`server/nodes/scraper/tikhub_action/_sdk.py:36-39`).
- No workflow reads `retry_state`: used-up attempts, a non-retryable error and a timeout all look the same.

**Verdict:** Adopted with deviations. Approved sends and CLI agents run once and gateway LLM calls replay at $0, but graph-node sends and side-effecting tools without a spec run again after a crash, contradicting E12 and T13.

**Recommendation:**
1. **The redo guard (T13).** Generalize `resend_refusal` into `redo_refusal(context, node_cls, node_data)`, called where `resend_refusal` is called today (`base.py:1218-1238`; `server/services/coding_workspace_tools.py:111`), so it covers every path that runs a plugin as an activity: agent tool calls, the Coding Agent's automatic workspace tools (the `coding.workspace_tool` activity), owner-approved sends and graph nodes. On attempt 2 or later, a call whose effect class is `unsafe` answers instead of running: `SendOutcomeUnknown` for sends, `OutcomeUnknown` for other effects. The outcome is recorded as `unknown` on the approval row (the gate's row for a gated reply; otherwise an `approved_by: auto` row, as `_auto_recorder` writes today).
   - **The effect class** comes from the plugin: a new classmethod `BaseNode.effect(node_data) -> "read_only" | "idempotent" | "unsafe"`. It defaults from `annotations` (`readonly: True` is `read_only`, `idempotent: True` is `idempotent`, anything else is `unsafe`, the conservative default that I9 asks for) and can be overridden per operation (`httpRequest` with GET is `read_only`; `githubAction` `pr_list` is `read_only`; Stripe with its key is `idempotent`). A plugin that sends a provider idempotency key (item 2), or whose effect has a natural key, is `idempotent`. MCP tools take the server's `readOnlyHint` and `idempotentHint` (`mcp.types.ToolAnnotations`), kept in the connector snapshot and passed as locked binding fields.
   - **Who is told:** an agent tool call returns the result to the model, which tells the owner (today's behaviour for sends; no `requires_user_action`, which would end the run at `agent_workflow.py:2392-2404`). A graph node returns it with `requires_user_action`, so the run fails visibly and, for a run started by a trigger, the breaker pauses the deployment at once (threshold 1, `server/services/deployment/handlers.py:2757`; the flag reaches it through `server/services/temporal/workflow.py:895-899`, `:296-301`). For an old generation, that pause is what shows the owner "check this". Where the run answers a chat, the employee also gets an `[update]` note through `execution.tell_employee`, as approval outcomes already do.
   - Versioning: none (activity body). When: Phase 1b (1b.1). Effort M: a one-time audit classifies about 150 plugins (est.) and declares the harmless ones `idempotent` (memory and checklist writes, canvas notes, file overwrites, Task Manager with its durable row), and a contract test lists every node type's effect class so none relies on the default by accident.
   - Risk: a read-only plugin left on the default reports `unknown` after a crash instead of retrying; the contract test catches it.
2. **Provider idempotency keys where providers have them:** Stripe's `Idempotency-Key`, Discord's `nonce` with `enforce_nonce`, each derived from `sha256(workflow_id, activity_id)` and never the run id (RFC-0004 §9.4; RFC-0005 §7.2). Those plugins then declare `idempotent` and keep their retries.
   - Versioning: none. When: RFC-0005 Phase 3 ("send idempotency keys"); it needs no versioning, so it can land earlier. Effort S each.
   - Risk: a key reused across distinct calls; the activity id is unique per call.
3. **A cancel during a send is `unknown`** (RFC-0004 H2). §9.3 owns the fix (1b.2): activities re-raise cancellation, and a worker shutdown becomes a retryable `NodeWaitInterrupted`. `ApprovedToolCallWorkflow` then sees an `ActivityError` and records `broke_off`, which becomes `unknown`, and a retried graph or tool send meets the redo guard (item 1). When: Phase 1b (1b.2).
4. **The legacy tool path honours declared policies:** pass `cls.retry_policy.to_temporal()`, `cls.start_to_close_timeout` and `cls.heartbeat_timeout`, as the `workspace_task` branch already does under its patch (`agent_workflow.py:124-133`).
   - Versioning: none (options). When: Phase 1b (1b.3), first among the option fixes: on manual runs it is what re-runs paid Apify actors and Claude Code sessions without limit. Effort S.
   - Risk: tools that silently depended on unlimited retries fail after their declared attempts on manual runs. That is the intent.
5. **Compaction** moves to the LLM step's retry policy: §11.4 item 2 (1b.27).
6. **Read `retry_state` and the cause type** wherever an `ActivityError` reaches workflow code, and word the run's error accordingly: attempts used up, refused for good, or out of time. Versioning: none (result text only). When: with RFC-0004 D12 (Failed runs). Effort S.
7. **TikHub:** turn the SDK's own retries off, since Temporal owns retries, as on the LLM path. Versioning: none. When: later. Effort S.

**Remarks:**
- **The attempt number is the guard's only evidence.** Today attempt 2 means the first attempt broke off, because a clean plugin failure never reaches Temporal. Once RFC-0004 Q9 lets transient plugin errors reach Temporal (§11.4), that is no longer true, and the guard must move to a claim written just before the provider call: RFC-0005's outbox claim (RFC-0005 §17.2-17.3), extended to non-send effects.
- **RFC-0005 follows the page on counts** (inputs retried once, relay rejections dead after 5 attempts, pollers dead after 5 failures) but misses two things (§20):
  - a retried turn re-runs every node, and only outbox sends are deduplicated (RFC-0005 §17.1). Payments, deploys and writes repeat unless keyed. The retried turn should resume from the saved conversation, and a graph-only turn at its failed node (§11.8);
  - spend: the ledger keys reservations by activity id (RFC-0005 §14), so an own-key retry that really billed twice is counted once and escapes the daily budget (E11).
- `maximum_attempts=1` on send plugins, the alternative, also drops the retry for a crash before anything was sent, and records nothing as `unknown`. The guard is better.

**Sketch** (recommendation 1):

```python
# server/services/plugin/base.py
class BaseNode:
    @classmethod
    def effect(cls, node_data: Mapping[str, Any]) -> str:
        """read_only | idempotent | unsafe. Plugins override per operation."""
        if cls.annotations.get("readonly") is True:
            return "read_only"
        if cls.annotations.get("idempotent") is True:
            return "idempotent"
        return "unsafe"                               # never a silent duplicate (I9)

# server/services/approvals/tool_calls.py (replaces resend_refusal)
def redo_refusal(context, node_cls, node_data) -> dict | None:
    """A later attempt of an effect that may already have happened answers
    `unknown` instead of running again (T13)."""
    if _attempt() <= 1 or node_cls.effect(node_data) != "unsafe":
        return None                                    # first attempt, reads, keyed calls
    spec = approval_spec(node_cls)
    if spec is not None and spec.restrict_while_asking:
        return None                                    # the Browser resumes its own wait
    sends = spec is not None and spec.sends(node_data)
    result = {"success": False, "error": REDO_MESSAGE,  # "It may already have happened ..."
              "error_type": "SendOutcomeUnknown" if sends else "OutcomeUnknown"}
    if not is_agent_tool_call(context):
        result["requires_user_action"] = True          # a graph run fails visibly
    return result
```

### 11.3 Fixed Wall-Time Retries

**Docs:** https://docs.temporal.io/design-patterns/fixed-wall-time-retries. Bound the total time across all attempts with `schedule_to_close_timeout` (the total budget from the first scheduling to completion); `start_to_close_timeout` caps one attempt. Rules:
- The budget "starts when the Activity is first scheduled and expires when the clock runs out, regardless of how many attempts have occurred". On expiry the workflow receives `ActivityError` whose cause is a `TimeoutError` of type `TimeoutType.SCHEDULE_TO_CLOSE`.
- "A timeout does not forcibly stop Activity code that is already running, so an Activity that runs past the budget must heartbeat and handle cancellation to stop cooperatively."
- Set both timeouts; "Cap `MaximumInterval` well below the SLA"; handle the `ActivityError` explicitly; "Distinguish SLA breaches from transient errors."
- Pitfalls: "Not accounting for `ScheduleToStart` delay in the budget" (queue wait counts); `StartToCloseTimeout` alone is no budget; "Setting `ScheduleToCloseTimeout` shorter than `StartToCloseTimeout`"; ignoring the breach; "Not accounting for backoff delays in the budget".

**SDK:** `workflow.execute_activity(..., schedule_to_close_timeout=...)`, `TimeoutError.type` and `TimeoutType.SCHEDULE_TO_CLOSE` exist in 1.30.0 (`temporalio/exceptions.py:211-226`) and 1.34.0. Inside the activity, `activity.info()` carries `scheduled_time` (the first schedule), `current_attempt_scheduled_time`, `schedule_to_close_timeout` and `attempt` (1.30.0 `temporalio/activity.py:106-131`), so an attempt can compute its remaining budget.

**OpenCompany today:**
- No production activity sets `schedule_to_close_timeout`; the only occurrence is a test that asserts its absence (`server/tests/temporal/test_browser_task_dispatch.py:40`). No unlimited policy has an outer bound.
- The owner decided "A total budget per step" for LLM steps on 2026-10-16 (RFC-0004 Q6). No value was chosen and nothing is built.
- **Stop waits for unlimited retries.** The LLM step is an admitted action, work that execution control's Stop checkpoint waits for (`agent_workflow.py:1251-1259`), and Stop's checkpoint waits until no admitted action runs (`server/services/temporal/execution_control.py:114-118`). The workflow-control doc says it: "Unlimited LLM retries can keep it there during an outage" (`docs-internal/temporal-workflow-control.md`; `server/nodes/README.md:145`).
- **Attempts hang up to the attempt timeout.** A Gemini call has no request timeout and keeps heartbeating every 20 s, so each attempt ends only at the 10-minute timeout. OpenAI and Anthropic steps stream, and their SDKs' 600 s timeout bounds only the silence between chunks, so a slow call that keeps streaming is also ended only by the attempt timeout (§16.3).
- **The chat watchdog has a budget of its own** (`max_running_s` 86,400, `server/config/chat_defaults.json:4`). The running clock restarts after a Resume. On expiry it fails the chat run as `timed_out` but does not cancel the workflow (`server/services/chat/ledger.py:739-768`; only a stopping run is cancelled, `:714-730`), so the agent keeps retrying after the owner was told it timed out.
- Temporal's clocks keep running while a run is paused or the app is closed (`workflow.py:148-155`). That is why workflow and run timeouts were removed, and RFC-0005 §27 keeps them unset. A `schedule_to_close_timeout` is the same kind of clock at activity level.

**Verdict:** Missing and beneficial. The owner chose a per-step budget (RFC-0004 Q6), and T11 now says what happens when it runs out: the turn parks; it does not fail. T18 ships the budget together with parking, in RFC-0005 Phase 5, for new generations only.

**Recommendation:**
1. **Now: bound each attempt.** §16.3 owns the fix (1b.25): a deadline inside each attempt, the Gemini client timeout, and 30-minute attempts for local and OpenAI-compatible endpoints. It adds no total budget.
2. **The step budget, for new generations.** With RFC-0005 Phase 5, under `scheduler_version=1` (a generation flag in `resource_manifest`, frozen at Start), `_execute_model_step` (`agent_workflow.py:3183-3188`) and the compaction call pass `start_to_close_timeout=<attempt timeout>` and `schedule_to_close_timeout=<step budget>`. `LLM_STEP_RETRY` stays as it is inside the budget. The values come from `server/config/llm_defaults.json`: `agent.attempt_timeout_seconds` and `agent.step_budget_seconds`, with overrides in the `ollama`, `lmstudio` and `openai_compatible` provider blocks. `prepare_agent_payload` puts the values for the run's provider into the payload, so workflow code never reads `Settings`; only `scheduler_version=1` decides whether a run uses them.
3. **Expiry parks the turn.** On `TimeoutType.SCHEDULE_TO_CLOSE`, from the LLM step or from compaction, the turn parks with reason `provider_outage` (§11.8) and says "The model provider did not answer within the step budget; retrying every 15 minutes." Without parking, that case would fall into "failed unexpectedly" (`agent_workflow.py:1296-1300`).
4. **Do not wait past the budget.** In the activity, a retryable error whose provider hint (the raw Retry-After, before §16.5's 5-minute cap) exceeds the remaining budget raises a non-retryable error carrying that hint, and the park uses it as its first probe (§11.5).
5. **Chat runs.** Today's chat runs keep their 24-hour limit; with §6.4's fix (1b.14) a run past it also cancels its workflow. Under Phase 5 a parked turn's time counts toward no deadline, the chat run's limit included, and a park lasts at most 7 days (T19, §11.8).
- **Existing generations get no step budget;** items 1 and 5 apply to them. They keep today's unlimited LLM-step retries (and compaction's, from 1b.27), covered by the retry alert (1b.30, §11.7), and today's fail-and-pause (§11.8). A budget without parking would fail turns whose provider recovers later and lose their input, which T4 rules out (T18).
- Versioning: items 2-4 add commands (the park), so they take `scheduler_version=1`, frozen into the workflow input at Start (RFC-0005 §10.4, §24.1 rule 2). Generations without it replay today's options and branches. Items 1 and 5: none.
- When: item 1 in Phase 1b (1b.25); items 2-4 with the park (§11.8) in RFC-0005 Phase 5; item 5 in Phase 1b (1b.14), and for parked time with Phase 5.
- Effort M, with the park. Risk: the budget also counts time while the app is closed or the queue is backed up, so it can park a turn that would have succeeded; parking is cheap, and the boot wake probes at once.

**Defaults** (RFC-0004 Q6 values; change at review):

| Provider | Attempt timeout | Step budget |
|---|---|---|
| cloud providers | 10 min (today's `LLM_STEP_TIMEOUT`) | 15 min |
| local: `ollama`, `lmstudio`, `openai_compatible:<slug>` | 30 min | 45 min |

The attempt timeouts ship in Phase 1b (1b.25); the step budgets ship with RFC-0005 Phase 5, for new generations (T18).

Why these values:
- **One full attempt plus a retry window.** The page warns that a budget shorter than one attempt cannot succeed and that backoff counts. After a 10-minute attempt that hangs, 5 minutes remain for five quick retries (waits of 5, 10, 20, 40 and 80 s).
- **The fast phase fits.** With instant failures (429, 503, a refused connection), `LLM_STEP_RETRY` makes 8 attempts in 15 minutes, starting at 0, 5, 15, 35, 75, 155, 315 and 615 s. That is inside the Fast/Slow page's 5-20 fast-phase attempts. The backoff reaches its 5-minute ceiling at 615 s, so everything after that is already a slow phase, which parking now takes over without holding Stop or an admitted action.
- **Stop waits at most one budget** (15 minutes) for an LLM step during an outage, instead of indefinitely (new generations).
- **Bounded cost.** Within 15 minutes at most one full 10-minute attempt and one cut-short attempt can end ambiguously, so an own-key step is billed at most about twice (est.), against no limit today; clean rejections bill nothing, and gateway calls replay at $0 anyway.
- **Local models** get 30-minute attempts, because CPU inference of a long answer can exceed 10 minutes (RFC-0004 H3), and keep the same shape: one attempt plus 15 minutes.
- **Rejected:** 30 minutes (RFC-0004's example) and 24 hours only delay the park and hold Stop longer, since expiry no longer fails the turn; 10 minutes leaves no retry after an attempt that hangs.

**Remarks:**
- **The clock runs while the desktop app is closed or the laptop sleeps.** On restart, every step that was retrying times out at once and parks; the boot wake (§11.8) probes each parked turn immediately. An overnight closure then costs one park and one probe, not a failed turn.
- **`next_retry_delay` and the budget:** "If the override delay exceeds the remaining `ScheduleToCloseTimeout` budget, the retry will never execute." Today's uncapped Retry-After (`server/services/llm/protocol.py:807-819` accepts up to 10,000 years) would end the step as an unexplained timeout; item 4 handles it.
- **RFC-0005's turn deadlines (T19).** As first written they were 15 minutes for C1 and C2, 30 for C3 and 60 for C4 (RFC-0005 §13.1), and they ticked through provider backoff and downtime. A 15-minute budget equals the C1 and C2 deadline, and the 45-minute local budget exceeds C1 to C3, so the deadline would cancel turns T11 wants parked, then spend their single retry and dead-letter them (RFC-0005 §10.3). T19 settles it (§20): each per-class deadline becomes a "slow turn" alert (an attention item and a metric) that does not count parked time. Cancelling is left to the 24-hour no-progress backstop, a default (§24.1; §6.4), which never counts time parked, paused or waiting for the owner.
- **Testing:** `WorkflowEnvironment.start_time_skipping()` with a fake LLM activity that keeps raising a retryable error; assert the park, its wording, and that Stop completes while the turn is parked, and that a generation without `scheduler_version=1` keeps today's options. The replay corpus must pass, since options are not compared.

**Sketch** (items 2-4; RFC-0005 Phase 5, new generations only; written against 1.30.0):

```python
# server/services/temporal/agent_workflow.py
async def _execute_model_step(self, llm_payload, context, payload, iteration, probe=0):
    options = dict(start_to_close_timeout=_attempt_timeout(payload["provider"]),  # 10 or 30 min (1b.25)
                   heartbeat_timeout=LLM_STEP_HEARTBEAT_TIMEOUT, retry_policy=LLM_STEP_RETRY)
    if self._parking_enabled:            # scheduler_version=1, frozen at Start (T18)
        options.update(
            start_to_close_timeout=timedelta(seconds=payload["llm_attempt_timeout_seconds"]),
            schedule_to_close_timeout=timedelta(seconds=payload["llm_step_budget_seconds"]))
    suffix = f"-r{probe}" if probe else ""             # a probe after a park
    return await workflow.execute_activity(
        "agent.execute_llm_step", args=[llm_payload],
        activity_id=f"llm-step-{iteration + 1}{suffix}", **options)

# server/services/temporal/agent_activities.py, before raising a retryable LLMError
info = activity.info()
hint = failure.details[0].get("retry_after")             # the raw hint, before §16.5's 5-minute cap
if info.schedule_to_close_timeout and hint is not None:
    remaining = info.scheduled_time + info.schedule_to_close_timeout - datetime.now(timezone.utc)
    if timedelta(seconds=hint) >= remaining:            # the wait does not fit the budget
        raise ApplicationError(
            f"{error.user_message} The provider asks to wait {hint:.0f} s.",
            {**failure.details[0], "park_after_seconds": hint},
            type="LLMError.wait_exceeds_budget", non_retryable=True) from None
```

### 11.4 Non-Retryable Errors

**Docs:** https://docs.temporal.io/design-patterns/non-retryable-errors. Mark permanent failures so "the Temporal Activity fails fast instead of retrying indefinitely". Rules:
- Two mechanisms, best used together: `ApplicationError(msg, type="X", non_retryable=True)` at the throw site, and `RetryPolicy(non_retryable_error_types=["X"])` at the caller, which "matches on type names, not message strings".
- "Validate input before scheduling the Activity", for example in an Update validator; use specific type names; "Reserve non-retryable for truly permanent failures. A rate-limit error (HTTP 429) is transient."
- Pitfalls: "Marking transient errors as non-retryable"; "Using the error message instead of a type name"; "Swallowing the `ActivityError` without logging"; "Confusing non-retryable errors with Workflow failures"; "Losing the flag by wrapping the error": the server looks only at the outermost error, so a plain exception wrapped around a non-retryable one becomes retryable.

**SDK:** `ApplicationError(..., non_retryable=, next_retry_delay=, category=)` exists in 1.30.0 (`temporalio/exceptions.py:111-180`) and 1.34.0. Any other exception becomes a retryable `ApplicationError` named after its class (1.30.0 `converter/_failure_converter.py:108-114`). That includes an oversized result, typed `PayloadSizeError` when the namespace reports its limits to the worker (`worker/_worker.py:771-779`). In 1.34.0 payload limits moved into Core and `_PayloadSizeError` is gone from Python; how an oversized result is typed after the upgrade is unverified, so the 1.34.0 replay gate re-tests it.

**OpenCompany today:**
- **Correct at three boundaries:**
  - the LLM step classifies `LLMError` by category, raises a safe `ApplicationError` as the outermost error with `non_retryable=not retryable`, and keeps the diagnostics in history (`agent_activities.py:246-303`, `:558-574`; categories at `server/services/llm/protocol.py:329-346`, the retryable set at `:550-556`). A missing credential is a non-retryable `MissingAgentProviderCredential` (`agent_activities.py:168-174`, `:236-242`);
  - Pi raises typed transient and permanent errors and also lists the permanent ones at the call site (`pi_activities.py:27-47`; `server/services/temporal/pi_workflow.py:182-186`): both mechanisms, as the page advises;
  - the controller's Update validators raise typed non-retryable errors (`workflow_control_workflow.py:141-323`).
- **Misused in the plugin layer.** Plugin exceptions never leave the activity as exceptions (§11.1), so the shared non-retryable lists (`scaling.py:59-72`; `_retry_policies.py:52-55`) never match a plugin error, and classification written for Temporal is thrown away:
  - WhatsApp Business raises `RuntimeError` for throttles: "Not a NodeUserError: that type is non-retryable in the shared retry policy, so raising one here would defeat the backoff Meta asks for" (`server/nodes/whatsapp_business/_base.py:36-44`, `:147-150`);
  - Discord's `RateLimitExceeded` is documented as "Retryable: Temporal re-dispatches" (`server/nodes/discord/_ratelimit.py:41-46`);
  - both become result envelopes, so the node fails at once. Discord first waits inline up to 30 s; past that, `retry_after` survives only in the message text.
- **Speech** turns every provider failure, 429 and 5xx included, into `NodeUserError` (`server/nodes/speech/_unifier.py:135-150`): the page's first pitfall. A structured mode exists (`translate_errors=False`), and no caller uses it.
- **Unclassified LLM-step exceptions retry forever.** Only `LLMError` and `CancelledError` are caught (`agent_activities.py:527-578`). Wire decoding (`:491-495`), `ThinkingConfig(**...)` (`:511-516`) and anything unexpected after the client exists escape as plain exceptions, and `LLM_STEP_RETRY` retries them without limit. An oversized result would do the same, but an LLM step's result is bounded by the model's output tokens, so the real risk is deterministic bugs. A bug after the provider has answered is worse: with the owner's own key, every attempt pays for the call again.
- **Compaction** raises an empty summary as a retryable `CompactionFailed` (`agent_activities.py:2757-2766`), under a 3-attempt policy (§11.2).
- **Inline policies lose the shared list.** Five workflow modules build `RetryPolicy(maximum_attempts=3)` inline, so `NodeUserError` is retried three times (`employee_job_workflow.py:125-139`, `:157`; `pi_workflow.py:158`, `:224`; `node_invocation.py:98`; `workspace_tasks_workflow.py:106`, `:183`).

**Verdict:** Misused. The plugin layer declares and writes a classification that never reaches Temporal, and LLM-step bugs retry forever; the LLM, Pi and controller boundaries are correct.

**Recommendation:**
1. **Classify LLM-step bugs (1b.26).**
   - **Before the provider call:** wrap the deterministic phase of `_execute_native_llm_step` (wire decoding, tool definitions and `ThinkingConfig`, `agent_activities.py:491-516`) so that programming errors (`KeyError`, `TypeError`, `ValueError`, `AttributeError`, decode errors) raise `ApplicationError(type="LLMStepFailed", non_retryable=True)`, with the original class name in the details. Input/output errors before the call (database, file system) stay retryable; they cost nothing.
   - **After the provider has answered:** everything after the provider call returns (wire conversion, the save, the result) turns any exception that is neither an `ApplicationError` nor a cancellation into `ApplicationError(type="LLMStepInternalError", non_retryable=True)`. The provider has already been paid, and today this tail does no input/output that can fail transiently: the conversation save swallows its own errors (`agent_activities.py:689-697`). Under `event_os_version=1` the consuming save raises a retryable `ApplicationError` instead (§16.8), which this wrapper lets through.
   - **`PayloadSizeError` in both lists:** add it to `NON_RETRYABLE_ERROR_TYPES` (`_retry_policies.py:52-55`) and to the plugin `RetryPolicy`'s own default non-retryable list (`scaling.py:59-72`), which tool and graph-node activities use and which lacks it today.
   - Versioning: none (activity body and options). When: Phase 1b (1b.26). Effort S.
   - Risk: a transient error misread as a bug fails the turn; keep the set wrapped before the call to programming-error classes. The `PayloadSizeError` test is redone after the 1.34.0 upgrade, which moves payload checks into Core (SDK note above).
   - T11 does not park these turns: a bug clears only with a fix.
2. **Compaction runs on the LLM step's policy (1b.27).** Run `agent.compact_context` on `LLM_STEP_RETRY` instead of `AGENT_ACTIVITY_RETRY` (`agent_workflow.py:2829-2835`), with a heartbeat, and pass `attempt=` to `_as_temporal_llm_error` (`agent_activities.py:2747`). Provider errors keep their classification and pacing, so a 429 burst the step itself would wait out no longer ends the run with `CompactionError` (`agent_workflow.py:2866-2882`). An empty summary (`CompactionFailed`) becomes non-retryable from the third attempt (`activity.info().attempt >= 3`), so a summarizer that keeps returning nothing does not retry without limit.
   - Versioning: none (retry policy, heartbeat timeout and activity body). When: Phase 1b (1b.27). Effort S.
   - Risk: in a provider outage compaction now waits as the step does: without limit in existing generations, with the retry alert (§11.7, T18), and within the step budget and parking in new ones (§11.3, §11.8).
3. **Let transient plugin errors reach Temporal** (needs RFC-0004 Q9, still open; RFC-0005 §25.2). Add a typed `TransientNodeError(message, retry_after=None)` to `services.plugin`. `_execute_body` and `NodeExecutor` let it through, and the per-type wrapper raises `ApplicationError(type="NodeTransient", non_retryable=False, next_retry_delay=retry_after, category=BENIGN)` after recording the attempt (approval record, chat step). Convert Discord's `RateLimitExceeded`, WhatsApp Business throttles, TikHub's 429, and speech and translate in structured mode. `NodeUserError` stays an envelope.
   - Only plugins whose effect class is `read_only` or `idempotent` (§11.2) may raise it before the claim-based guard exists.
   - `MachinaWorkflow._node_failure` (`server/services/temporal/workflow.py:1081-1100`) must take the innermost `ApplicationError`'s message, or the run's error degrades to "Activity task failed". That changes only the text of a result, not a command, so it needs no new `workflow.patched` call.
   - Versioning: none. When: after the owner answers Q9. Effort M.
4. **Fix the misleading comments** in the Discord and WhatsApp Business plugins now: they describe a Temporal backoff that never happens, and the next author will copy the assumption. Versioning: none. When: Phase 1b (1b.45). Effort S.
5. **Replace the five inline policies** with shared constants (§11.1 recommendation 1). When: Phase 1b (1b.41).

**Remarks:**
- The approval and send paths depend on envelopes: `checked.record(result)` and the chat steps run on the result (`base.py:1345-1354`). A wrapper that raises must record first, or only the last attempt's outcome is recorded.
- Keep the flag on the outermost error; the LLM boundary already does `raise failure from None` (`agent_activities.py:574`).
- RFC-0005 adds cases that follow the page: `BudgetExceeded` is non-retryable and BENIGN (RFC-0005 §14), and a filter that raises becomes `dead(filter_error)` (RFC-0005 §9.3). One small deviation: "A deterministic rejection marks the delivery dead after 5 attempts" (RFC-0005 §9.4); a rejection known to be deterministic (`INVALID_ARGUMENT`, payload too large) can be dead at once.
- Agent tools: returning errors to the model is the right contract, since the agent loop is the correction loop. But on a transient error the model calls again at once and ignores Retry-After; for Discord that feeds the process-wide invalid-request guard toward a Cloudflare ban of the host's IP (`_ratelimit.py:28-33`). Recommendation 3 fixes it for tools too.

**Sketch** (recommendation 1):

```python
# server/services/temporal/agent_activities.py, _execute_native_llm_step
_STEP_BUGS = (KeyError, TypeError, ValueError, AttributeError, UnicodeDecodeError)

def _step_bug(exc: BaseException) -> ApplicationError:
    """A deterministic failure in our own code: every retry replays the same bug."""
    return ApplicationError(
        "The language model step failed unexpectedly. Retry the run or check server logs.",
        {"exception": type(exc).__name__},
        type="LLMStepFailed", non_retryable=True)

try:                                                    # before the call: nothing paid yet
    messages = filter_empty_messages(messages_from_wire(payload.get("messages") or []))
    _ensure_llm_contents(messages)
    tool_defs = _tool_definitions(payload.get("tools") or [])
    thinking_data = payload.get("thinking_config")
    thinking = ThinkingConfig(**thinking_data) if isinstance(thinking_data, dict) else None
except _STEP_BUGS as exc:
    raise _step_bug(exc) from None
# ... the provider call and its `except LLMError` arm stay as they are ...
try:
    return await _finish_step(payload, messages, response, emitter)   # wire, save, result
except (asyncio.CancelledError, ApplicationError):
    raise
except Exception as exc:                               # the provider answered, and was paid
    raise ApplicationError(
        f"LLM step failed after the provider answered: {type(exc).__name__}",
        type="LLMStepInternalError", non_retryable=True) from None
```

### 11.5 Delayed Retry

**Docs:** https://docs.temporal.io/design-patterns/delayed-retry. When an error says how long to wait (a 429 `Retry-After`, a maintenance window), raise `ApplicationError(..., next_retry_delay=timedelta(...))`. Temporal "replaces the RetryPolicy-calculated interval for that single retry". Rules:
- "The RetryPolicy's `MaximumAttempts` and `ScheduleToCloseTimeout` still apply"; later attempts return to the policy unless the delay is set again.
- "Use the error's own delay information"; "Fall back to the RetryPolicy for unknown errors"; "Still set a meaningful RetryPolicy" and make `scheduleToCloseTimeout` "long enough to accommodate the maximum possible `nextRetryDelay`"; "Surface the delay in the failure message".
- Pitfalls: "Assuming `nextRetryDelay` persists across all retries"; "Setting `nextRetryDelay` longer than `ScheduleToCloseTimeout` ... the retry will never execute".
- The AI cookbook's HTTP retry recipe (https://docs.temporal.io/ai/cookbook/http-retry-enhancement-python) honours Retry-After only when it is between 0 and 60 s, lets `x-should-retry` override the status code, and retries 409.

**SDK:** `next_retry_delay` exists since 1.7.0; in 1.30.0 at `temporalio/exceptions.py:133`, `:169-175`; unchanged in 1.34.0.

**OpenCompany today:**
- **Correct for LLM steps.** `Retry-After`, `Retry-After-ms`, an HTTP date and Gemini's `RetryInfo` are parsed (`protocol.py:521-544`) and become `next_retry_delay` (`agent_activities.py:265-272`, `:288`). Without a hint the activity computes the policy's own next interval (`:289-296`), so the UI can show the wait (`_publish_llm_attempt_status`, `:313-345`) and the WARN line names it (`:567-571`). Provider SDK retries are off (`:542-545`).
- **No upper bound.** `valid_retry_delay` accepts any delay up to 10,000 years (`protocol.py:807-819`); the in-process loop caps only its own fallback, "never the provider's hint" (`server/services/agent_runtime.py:163-167`).
- **Compaction** maps errors without the attempt number (`agent_activities.py:2747`), under a 3-attempt policy.
- **Plugins:** Discord and TikHub know the provider's delay but lose it in the envelope (§11.4).
- `x-should-retry` is not read (not found), and 409 is not retried (`protocol.py:942` classifies it with 400 and 422 as `INVALID_REQUEST`).

**Verdict:** Adopted with deviations. Textbook for LLM calls; the honoured delay has no bound, compaction drops the attempt number, and plugin delays are lost in envelopes.

**Recommendation:**
1. **Bound the honoured delay by the remaining step budget** (§11.3 item 4). §16.5 caps each honoured delay at 5 minutes per retry and keeps the raw hint for the UI. When the raw hint exceeds the remaining budget, the activity raises the non-retryable `LLMError.wait_exceeds_budget` carrying it, and the turn parks with the hint as its first probe (T11). Do not adopt the cookbook's fixed 60-second cap: a daily-quota reset hint is real information, and a parked turn holds no worker.
   - Versioning: `scheduler_version=1`; only runs with a budget take this path. When: RFC-0005 Phase 5, with the park (§11.8). Effort S.
   - Existing generations have no budget to compare against: they keep honouring the hint, capped at 5 minutes (§16.5), with the retry alert (§11.7, T18).
2. **Compaction passes `attempt`** (§11.4 item 2). When: Phase 1b (1b.27).
3. **Plugins:** `TransientNodeError` with `next_retry_delay` (§11.4 item 3). When: after Q9.
4. **Read `x-should-retry`** in `LLMError.from_exception` (`protocol.py:497-562`) and retry 409 for the OpenAI and Anthropic providers, whose own SDKs retry it. §16.5 owns it. Versioning: none. When: RFC-0005 Phase 5 (§16.5). Effort S.

**Remarks:**
- **RFC-0005 follows the page**: "Retry-After responses become BENIGN retries with `next_retry_delay`" (RFC-0005 §13.4), and an egress bucket wait raises "a retryable error with `next_retry_delay`, so no worker slot is held while waiting" (RFC-0005 §17.2). It misses three details (§20):
  - **(a) The claim comes before the token.** RFC-0005 §17.2 claims the outbox row first and takes a bucket token second. If the bucket wait outlasts the claim's lease, the next attempt finds an expired `sending` claim, and RFC-0005 §17.3 marks the row `unknown` although the provider was never called. Take the token first, or write a `provider_called_at` mark just before the call and treat only rows with that mark as `unknown`.
  - **(b) Bucket waits use up attempts.** `next_retry_delay` still counts toward `maximum_attempts` and the budget, so egress needs an unlimited policy, with the claim giving at-most-once (never `maximum_attempts=1`), and no budget shorter than the longest bucket wait.
  - **(c) BENIGN hides the failure metric.** BENIGN errors "emit DEBUG level logs and do not record metrics" (`temporalio/exceptions.py:121`), so a long 429 storm marked BENIGN is invisible to `temporal_activity_execution_failed`. The attempt threshold of §11.7 (its attention item, then its counter) becomes required.
- **T7's poll workflow uses this mechanism as its clock:** the poll activity ends each cycle with a BENIGN error and Temporal retries it after the interval. The interval rides `next_retry_delay` (§9.2), so it is set on every cycle (it applies to one retry only), and the activity has no `schedule_to_close_timeout`.

### 11.6 Fast/Slow Retries

**Docs:** https://docs.temporal.io/design-patterns/fast-slow-retries. Two phases run by the workflow: a fast phase (short interval, bounded attempts) and, once it is exhausted, the same activity again with a long fixed interval and unlimited attempts, so the workflow "outlasts an extended downstream outage". Rules:
- Tuning: phase 1 interval 1-5 s, coefficient 1.5-2.0, 5-20 attempts; phase 2 interval 1-15 minutes with `BackoffCoefficient` 1.0.
- "Phase 2 runs indefinitely by default"; add `ScheduleToCloseTimeout` only "If the business process has a maximum wait time".
- "Log the phase transition"; "Combine with Retry Alerting via Metrics".
- Pitfalls: "Catching too broadly in Phase 1 ... (such as `CancelledError` in Python)"; a finite `MaximumAttempts` in phase 2; exponential backoff in phase 2.

**SDK:** `RetryPolicy`, `workflow.execute_activity` and `ActivityError`: 1.30.0 and 1.34.0.

**OpenCompany today:**
- **`LLM_STEP_RETRY` approximates both phases in one policy:** 5 s doubling to a 5-minute ceiling, unlimited (`_retry_policies.py:94-100`). The waits are 5, 10, 20, 40, 80, 160 and then 300 s, so from attempt 8 (at 615 s) it is a fixed 5-minute slow phase. The workflow never sees the change, because retries happen inside the server and add no history events.
- **The poll loop is fast/slow by structure:** three quick attempts (`DEFAULT_ACTIVITY_RETRY`), then the next poll interval (`workflow_control_workflow.py:834-866`; `polling_trigger_workflow.py:186-196`). An exhausted cycle is only logged.
- **The controller's spawn loop is the opposite.** A failed spawn goes back to the head of the queue and the controller sleeps 5 s, with no limit, no slow phase and no signal (`workflow_control_workflow.py:454-480`), blocking every event behind it (RFC-0005 §4.2). The trigger filter call that feeds it is not guarded: when its 2 attempts are used up, the exception escapes `_spawn_child_run` (`trigger_listener_workflow.py:186-206`, `:337-343`). The comment there says "the caller logs it and moves on", but under the durable queue the event is retried at the head every 5 s.
- **Catching too broadly:** `AgentWorkflow`'s LLM-step except branch re-raises cancellation only under some runtime flags (`agent_workflow.py:1260-1264`); elsewhere a cancelled step becomes "LLM step failed", the page's first pitfall (RFC-0004 H8, D9).
- Bookkeeping retries without limit at 1-30 s: right for idempotent writes that cost nothing.

**Verdict:** Adopted with deviations. The LLM step and polls have a slow phase, but its start is never signalled; the controller retries a failed spawn every 5 s forever and blocks its queue.

**Recommendation:**
1. **LLM step: parking is the slow phase.** Do not add a second `execute_activity` with an unlimited slow policy. The step budget (§11.3) is the fast phase, and a park with a 15-minute probe is the slow phase (§11.8, T11). The park is the logged and visible phase transition (an attention item and a log line), and between probes the turn holds no admitted action, so Stop completes.
   - Versioning: `scheduler_version=1`. When: RFC-0005 Phase 5, new generations only (T18). Effort: counted in §11.8.
   - Existing generations keep `LLM_STEP_RETRY`'s unlimited 5-minute slow phase, with the retry alert (§11.7).
2. **Controller spawn backoff** (old generations). Retry a failed spawn after 5 s, 30 s, 2 min and 10 min (fast, then slow), with the event moved behind the rest of the queue while it waits so other events keep running. Attempts are counted per event and carried with the event (a `_spawn_attempts` field), so the count survives continue-as-new and spills. After 5 failures that are not duplicate starts, dead-letter it as §7.4 A1 details: write it to dead-letter rows through the existing `controller.queue.spill` activity with `controller_id=f"{controller_id}:dead"`, which `controller.queue.read` never reads back, so no schema change is needed; paging and spill calls use `QUEUE_PAGING_RETRY` (1b.16). Reset and workflow deletion also clear those rows; RFC-0005 Phase 8 adds the attention item. This is RFC-0005 §9.4's rule for the new path ("dead after 5 attempts. The stream keeps moving"). Treat an exhausted filter call as a failed spawn of that event, not as a head-of-line retry.
   - Versioning: one patch, `workflow.patched("controller-spawn-backoff-v1")` inside `WorkflowControlWorkflow`, for both the backoff and the dead letter.
   - When: the controller-hardening lane after Phase 1 (RFC-0005 §24.2), serialized with any other controller edit. Effort M.
   - Risk: a mistake in the gate breaks replay of open controllers; T14 then starts the release and marks those generations "needs Reset". Add a fixture whose spawn always fails.
3. **RFC-0005: split "provider unavailable" from "bad input" at turn level.** Classified provider outages park (the slow phase, while suspended); only unclassified failures spend an input's single retry (RFC-0005 §10.3, amended in §20).

**Remarks:**
- The docs' slow phase keeps an activity pending. That is fine for Temporal, but wrong for Stop here (an admitted action keeps Stop waiting) and for RFC-0005's admission (a turn waiting on a provider would hold its lease). Parking puts the slow phase in workflow code, the shape the Resumable page uses ("Parked in Temporal — zero cost, no polling").
- RFC-0005 already has slow phases at two levels: per-thread restart backoff "1, 5, then 30 minutes" (RFC-0005 §18.2) and the relay breaker "with backoff and a 5 s probe" (RFC-0005 §9.4).
- Today's head-of-line loop costs about 10 history events per pass (est.: an activity, a timer, workflow tasks), so a stuck controller rolls over many times a day while every queued event waits.
- §7.4 A1 gives the dead letter's details; the two ship as one change under `controller-spawn-backoff-v1`, specified here.
- The sketch below is not drop-in: `_next_event()` must skip events whose retry time has not come, the main loop's wait needs a timeout at the earliest retry time, and the durable-prefix bookkeeping must account for an event moved to the back. The retry time rides in the event with the attempt count, so continue-as-new and spills keep both.

**Sketch** (recommendation 2):

```python
# server/services/temporal/workflow_control_workflow.py, run(): the spawn failure arm (:464-480)
SPAWN_PATCH = "controller-spawn-backoff-v1"          # one marker: the backoff and §7.4 A1's dead letter
_SPAWN_BACKOFF = (timedelta(seconds=5), timedelta(seconds=30),
                  timedelta(minutes=2), timedelta(minutes=10))
_MAX_SPAWN_ATTEMPTS = 5

except Exception as exc:  # noqa: BLE001 - per-event isolation
    if is_cancelled_exception(exc):
        raise
    if accumulator_v1 and isinstance(exc, WorkflowAlreadyStartedError):
        accepted = True                                            # a duplicate start, as today
    elif queue_v2 and workflow.patched(SPAWN_PATCH):
        attempts = int(event.get("_spawn_attempts", 0)) + 1       # carried with the event (§7.4 A1)
        if attempts >= _MAX_SPAWN_ATTEMPTS:
            await workflow.execute_activity(
                "controller.queue.spill",                          # a dead row, never read back
                {"controller_id": f"{workflow.info().workflow_id}:dead",
                 "events": [[listener_id, {**event, "_dead_reason": type(exc).__name__}]]},
                start_to_close_timeout=timedelta(seconds=60), retry_policy=QUEUE_PAGING_RETRY)
            accepted = True                                        # the queue moves on
        else:
            retry_at = workflow.now() + _SPAWN_BACKOFF[attempts - 1]
            self._events.append((listener_id, {**event, "_spawn_attempts": attempts,
                                               "_retry_at": retry_at.isoformat()}))
            accepted = None                                        # waits behind the rest of the queue
    if queue_v2 and accepted is False:                             # recorded histories: today's loop
        self._events.insert(0, (listener_id, event))
        self._durable_prefix += 1
        await workflow.sleep(timedelta(seconds=5))
```

### 11.7 Retry Alerting via Metrics

**Docs:** https://docs.temporal.io/design-patterns/retry-metrics. Make "silent, persistent failures" visible "before they breach an SLA", without changing retries. Rules:
- In the activity: `if activity.info().attempt > THRESHOLD: activity.metric_meter().create_counter("high_activity_error_count", ...).add(1, {...})`.
- "Use the SDK metrics scope, not a third-party library" (it adds the namespace and task-queue tags); "Emit on every attempt above the threshold, not only once"; "Set up rate-based alerts, not count-based."
- Pitfalls: emitting in the workflow; alerting on the cumulative count; "Not resetting alerting context on success"; "A threshold of 1 means the metric fires on the very first retry — which is normal behavior."

**SDK:** `activity.metric_meter()` exists in 1.30.0 (`temporalio/activity.py:247-268`, `:461`) and 1.34.0; it adds `namespace`, `task_queue` and `activity_type` attributes itself. Exporting needs `Runtime(telemetry=TelemetryConfig(metrics=PrometheusConfig(...) or OpenTelemetryConfig(...)))`, or a `MetricBuffer` the app drains. None is configured (§11.1).

**OpenCompany today:**
- `ActivityObservabilityInterceptor` logs `activity_retry` at WARN on every attempt after the first (`server/services/temporal/_interceptors.py:56-60`): the page's threshold-of-1 pitfall, as a log line rather than a metric.
- `ExpectedLLMRetryFilter` rewrites the SDK's failure log for planned LLM retries (`server/core/logging.py:256-279`).
- The LLM step publishes `retry_wait` with the attempt number to the node status (`agent_activities.py:313-345`); it is visible only while someone watches the canvas or the chat.
- RFC-0004 D6 plans a "Retry storm (ours)" alert counted with `activity.metric_meter()` in this interceptor (RFC-0004 §7.1). RFC-0005 §18.3 and §18.4 listed attention items and metrics, but no retry attempts, until this RFC added them (§20).

**Verdict:** Missing and beneficial. RFC-0004 plans it the documented way, RFC-0005 left it out until this RFC's amendment (§20), and nothing alerts today.

**Recommendation:**
1. **The counter.** In `ActivityObservabilityInterceptor.execute_activity`, when `info.attempt` exceeds the activity type's threshold, add 1 to `opencompany_activity_high_attempts`, tagged with the provider for LLM steps (read from the activity input). Never tag workflow ids (RFC-0004 §7.1). Default thresholds, shared with the attention item (recommendation 2):
   - `agent.execute_llm_step` and `agent.compact_context`: 8. Attempt 8 is the first to have waited the full 5-minute ceiling (§11.6), so the counter fires from attempt 9;
   - bookkeeping, compensation and paging policies: 10;
   - excluded: `agent.acquire_subagent_permit` (waiting is its purpose) and T7's poll activity, whose attempts are planned BENIGN failures; it counts its own consecutive real failures in its cursor row.
   - Versioning: none (interceptor). When: RFC-0005 Phase 8; the fleet's rate alert with Phase 9a (recommendation 3). Effort S.
2. **The attention item.** Raise a `stuck_retry` attention item on the instance, alongside 1b.38's starter alerts, once per activity id when the threshold is crossed, and clear it when the activity completes: the page's "resetting alerting context on success". On the desktop, which exports nothing (E14), it is the only alert. Versioning: none (interceptor). When: Phase 1b (1b.30), before the counter. Effort S.
3. **Hosted.** Export through OTLP (E14) and alert on the rate over a window of at least 15 minutes. When: Phase 9a.
4. **Now:** log `activity_retry` at WARN only at attempt 2, at the threshold and every 10th attempt after it, and at DEBUG otherwise. Versioning: none. When: Phase 1b (1b.30). Effort S.

**Remarks:**
- **BENIGN makes the threshold check required.** Once retryable LLM errors are BENIGN (1b.28; RFC-0005 §13.4; RFC-0004 §7.4), the SDK records no failure metric for them, and the attempt threshold (the attention item from Phase 1b, the counter from Phase 8) is the only signal of a 429 storm.
- The interceptor runs when an attempt starts, so an outage with 5-minute backoff adds one increment per 5 minutes: rate alerts need a window of 15 minutes or more.
- With parking (§11.8) an LLM step in a new generation stops at about 8 attempts before it parks; the alert then mostly fires for bookkeeping and compensations that cannot reach the database, and for the LLM steps of existing generations, which keep unlimited retries (T18).
- Testing: `ActivityEnvironment` with a fake meter, or a `MetricBuffer` in a test runtime.

**Sketch** (recommendations 1, 2 and 4; `attention.raise_once` stands for the instance's attention items, as 1b.38 raises them; the counter lands in Phase 8):

```python
# server/services/temporal/_interceptors.py
_ALERT_AFTER = {"agent.execute_llm_step": 8, "agent.compact_context": 8}
_DEFAULT_ALERT_AFTER = 10
_PLANNED_RETRIES = frozenset({"agent.acquire_subagent_permit"})   # plus T7's poll activities

class ActivityObservabilityInterceptor(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        info = activity.info()
        limit = _ALERT_AFTER.get(info.activity_type, _DEFAULT_ALERT_AFTER)
        planned = info.activity_type in _PLANNED_RETRIES or _is_poll_activity(info.activity_type)
        if not planned and info.attempt > limit:
            tags = {}
            provider = _llm_provider(input.args)        # None outside LLM steps
            if provider:
                tags["provider"] = provider             # the meter adds activity_type itself
            activity.metric_meter().create_counter(      # RFC-0005 Phase 8
                "opencompany_activity_high_attempts",
                "Activity attempts above the alert threshold").add(1, tags)
            if info.attempt == limit + 1:               # once per activity (1b.30)
                await attention.raise_once(kind="stuck_retry",
                                           ref=f"{info.workflow_id}:{info.activity_id}")
        if info.attempt == 2 or info.attempt == limit or (info.attempt > limit and info.attempt % 10 == 0):
            logger.warning("activity_retry", activity_name=info.activity_type, attempt=info.attempt)
        result = await self.next.execute_activity(input)
        if info.attempt > limit and not planned:
            await attention.clear(kind="stuck_retry", ref=f"{info.workflow_id}:{info.activity_id}")
        return result
```

### 11.8 Resumable Activity

**Docs:** https://docs.temporal.io/design-patterns/resumable-activity. After a few retries, "park the Workflow in a waiting state and block on a Signal" that delivers a correction or a go-ahead, then run the activity again, so the workflow "resumes exactly where it left off instead of being restarted from scratch". Rules:
- A bounded `RetryPolicy` (`maximum_attempts=3`), or a non-retryable error for input that will never work; catch the `ActivityError`, set a status (`AWAITING_CORRECTION`), update a Keyword search attribute, and park on `workflow.wait_condition`: "Parked in Temporal — zero cost, no polling".
- Continue-as-new for long correction loops; a status Query; validate corrections in the handler; log and record state at every transition; "Notify the operator proactively"; keep it distinct from the Approval pattern.
- Pitfalls: "Waiting without a timeout"; "Not clearing the correction state before re-entering the loop"; corrections that arrive while the activity runs; "Conflating the correction loop with a general retry loop ... For retrying the same call against a temporarily unavailable system, use Fast/Slow Retries instead."
- The linked guide (https://docs.temporal.io/guides/recover-without-restart): "completed steps do not re-execute"; Signals are at-least-once, so deduplicate with a caller-minted id, or use an Update, which "would give you this deduplication for free"; parked runs wait "without consuming Worker capacity".

**SDK:** `workflow.wait_condition(..., timeout=)` (raises `asyncio.TimeoutError`), `@workflow.signal`, `@workflow.update` with validators, `workflow.upsert_search_attributes` and `@workflow.query`: all in 1.30.0 and 1.34.0. Activity Pause, the server-side alternative, is reachable in 1.30.0 only through the raw service stub (`client.workflow_service.pause_activity`), and 1.34.0's `ActivityHandle.pause` covers only activities not started by a workflow. It is a preview feature (owner decision RFC-0004 Q12: behind a flag), and today's wrapper would record a paused node activity as a completed failure (RFC-0004 H2). Not used here.

**OpenCompany today** (app-level versions of the pattern):
- **The deployment circuit breaker.** A failed run started by a trigger schedules `workflow_control.pause_on_failure` (`server/services/temporal/workflow.py:276-315`; `server/services/temporal/activities.py:599-631`). The deployment pauses after 3 failures in 600 s, or at once for `requires_user_action` (`server/services/deployment/handlers.py:2648-2674`, threshold 1 at `:2757`), with an attention text the owner sees (`server/services/employees/summaries.py:156-170`). It parks the deployment (future events), not the run: the failed run ends, and the event that triggered it does not run again on Resume. The failure streak is not keyed by run, so a retried activity can count twice (`handlers.py:2683-2709`).
- **Reviewed employee delivery is a faithful app-level version.** A delivery claims its sink with a 2-minute lease and an `inflight` mark (`server/services/employees/jobs.py:319-321`, `:351-357`). An expired mark becomes `delivery_needs_review`; the owner decides "arrived" or "retry" with an idempotent request key (`server/services/employees/delivery_resolution.py:32-78`), and a continuation workflow with a deterministic id resumes from the saved state (`delivery_resolution.py:80-110`; `employee_job_workflow.py:149-157`). Deviations:
  - the original workflow ends instead of parking;
  - a clean `success: False` from the sink leaves the `inflight` mark set (`jobs.py:385-386` raises before `:393` clears it), so a clean rejection is shown as "Check whether it arrived";
  - the 3-attempt budget of `employee.job.deliver` interacts with the lease. If the next attempt starts after the 2-minute lease has expired, it re-attaches to the same draft and uses one attempt, and the third interruption fails the workflow while the job row stays `delivering`. If it starts before the lease expires, it returns `state: delivering` (`jobs.py:273-275`) and completes, so the workflow ends with the draft still pending and nothing waiting on it. The gate row never expires while the generation lives.
- **Approvals:** the owner can retry a failed send, with confirmation when the outcome is `unknown` (`decisions.py:164-179`).
- **Owner chat:** Regenerate and edit re-run from a message (`docs-internal/chat_protocol.md`, Branches).
- **Agent tools:** the model reads an error as data and corrects itself, an automatic correction loop. A tool result with `requires_user_action` ends the run instead (`agent_workflow.py:2392-2404`, patch `agent-user-action-error-v1`).
- **Account problems end the run.** An LLM step that fails for billing, quota, authentication, permission, model not found or protocol (`protocol.py:485-491`; a spent gateway allowance is classified as billing, `:899-900`), or for a missing key, ends the run, and the breaker pauses the deployment at once. The input that met the problem is lost.
- **RFC-0005's budget suspension follows the page closely:** a turn over budget suspends without a timer, holds no slot and is woken by `released{reason=budget}`, and the owner is told; a turn parked for 7 days fails and its input is dead-lettered with an attention item (RFC-0005 §14 and §10.2, E11). Dead letters plus `Bus.replay` re-drive retained inputs (RFC-0005 §9.1 and §10.3).

**Verdict:** Adopted with deviations. Owner-review loops exist for sends and deliveries, and RFC-0005's budget suspension follows the page, but a turn that fails for a reason the owner or time can fix is not parked and its input is lost: the case T11 now decides.

**Recommendation:**
1. **Park, do not fail (T11), in new generations.** Generalize RFC-0005's budget suspension into one park mechanism in `AgentWorkflow` with four reasons:
   - `provider_outage`: the step budget expired, or the provider's wait exceeds the remaining budget (§11.3, §11.5);
   - `provider_billing`: a non-retryable `LLMError` for billing or quota, a spent gateway allowance (`budget_exceeded`) included;
   - `provider_account`: authentication or permission, or `MissingAgentProviderCredential` (a missing key);
   - `budget`: `BudgetExceeded` (E11; RFC-0005 §14).

   Every other failure still fails the turn: NOT_FOUND, PROTOCOL, INVALID_REQUEST, CONTEXT_LENGTH and bugs (§11.4), since the frozen request would fail the same way after a release.

   A parked turn:
   - leaves its admitted action, so Stop completes while it waits, as T12 does for approval drafts;
   - records a `parked_turns` row (agent workflow id, reason, provider, hint, since) and sets its admission lease `suspended` (RFC-0005 §13.3) through a bookkeeping activity, so it holds no worker slot and counts toward no capacity; it raises an attention item, which the employee summary shows;
   - for `provider_billing` and `provider_account`, also requests the automatic pause of the employee through the existing path (`handlers.py:2757`), written as an intent (T10) so the workflow never waits on its own pause; new events then wait in the inbox instead of each parking. Outage parks do not pause;
   - waits on `workflow.wait_condition` for the `released{reason}` Signal, sent by known workflow id: an outage park also has its probe timer, the others no probe timer, only the 7-day limit;
   - wakes on: the probe timer (every 15 minutes for `provider_outage`, fixed, or the provider's hinted wait when longer); the owner's Resume or "Try now"; a saved credential for that provider (`provider_account`); a top-up (`released{reason=budget}`, RFC-0005 §14); and backend boot, which signals every parked turn once;
   - on waking, waits for `running` (`_wait_until_resumed`), clears the row, and runs the same step again under a new activity id (`llm-step-<n>-r<k>`);
   - counts toward no deadline while parked: RFC-0005's per-class turn deadlines become "slow turn" alerts that skip parked time (T19), and the 24-hour no-progress backstop never counts time parked, paused or waiting for the owner (a default, §24.1; §6.4);
   - ends after 7 days parked, and its input is dead-lettered with an attention item (T19; the page's "Waiting without a timeout").
   - Senders find parked turns by known workflow id in `parked_turns`, never through Visibility (I6).
   - Versioning: `scheduler_version=1`, a generation flag in `resource_manifest`, frozen into the workflow input at Start (RFC-0005 §10.4, §24.1 rule 2); runs without it keep today's failure path. The new activity names and ids go into the golden identity test.
   - When: RFC-0005 Phase 5, for new generations only, together with the step budget (T18); budget suspension (RFC-0005 §14) is this mechanism's `budget` reason. Effort M.
   - Existing generations keep today's behaviour (T18): a provider outage retries without limit, with the retry alert (1b.30, §11.7), and an account problem fails the run and pauses the deployment at once. A budget without parking would fail turns and lose their input, which T4 rules out.
   - Risk: new commands in a shared type; fixtures for park, probe and wake; T14 covers a mistaken gate.
   - Scope: agent turns only. A graph run without an agent keeps the breaker plus dead letters (recommendation 4).
2. **Reviewed delivery:** §9.6 owns the fix (1b.9). A retry of `employee.job.deliver` re-attaches to its own lease after any restart, the three 31-day waits get an unlimited policy, and a clean `success: False` clears `inflight` and records `failed`. When: Phase 1b (1b.9).
3. **Key the breaker's failure streak by run,** so a retried activity counts once. Versioning: none. When: Phase 1b (1b.24). Effort S.
4. **Graph runs without an agent:** keep the breaker plus dead letters, and make RFC-0005's `Bus.replay` resume at the failed node from stored node outputs instead of re-running the turn ("completed steps do not re-execute"; T13). When: RFC-0005 Phase 4 (amendment, §20). Effort M.

**Remarks:**
- **Do not park instead of retrying.** The page warns against "Conflating the correction loop with a general retry loop". The fast phase stays a retry policy; only past the step budget does the turn park, and its probe is Fast/Slow's slow phase, not a correction.
- **History.** Each park-and-probe cycle adds about 10 events (est.: a timer, a failed probe activity, workflow tasks). A turn parked for 7 days with 15-minute probes adds about 6,700 events (est.): below the 10,240-event warning together with a normal turn's own events, and far below the 51,200 limit. `AgentWorkflow` continues as new only at a clean turn end (RFC-0004 M11), so a longer park limit would need continue-as-new from the park loop.
- **Downtime is covered too.** A budget that expired while the app was closed parks, and the boot wake retries at once.
- **Signals are at-least-once.** `released` only bumps a counter, and the woken turn clears its row, so a duplicate is harmless. Use an Update only if the Resume button needs a synchronous answer; T10's deliverer sends Updates with stable ids.

**Sketch** (the park loop; RFC-0005 Phase 5, new generations only):

```python
# server/services/temporal/agent_workflow.py (AgentWorkflow)
PARK_PROBE = timedelta(minutes=15)                # outage parks: fixed, or the provider's longer wait
PARK_LIMIT = timedelta(days=7)                    # T19

def _park_expired() -> ApplicationError:          # the entity dead-letters the input, with an attention item
    return ApplicationError("The turn stayed parked for 7 days.", type="TurnParkExpired", non_retryable=True)

@workflow.signal
def released(self, reason: str) -> None:          # Resume, "Try now", a saved key, a top-up, boot
    self._release_version += 1                    # buffered if it arrives before the wait

async def _run_model_step(self, llm_payload, context, payload, iteration):
    probe, parked_since = 0, None
    while True:
        seen = self._release_version              # before the call (§16.16): a release during the step counts
        try:
            async with self._execution_control.action():
                return await self._execute_model_step(llm_payload, context, payload, iteration, probe)
        except ActivityError as e:
            reason, wait = _park_reason(e)        # wait: max(PARK_PROBE, hint) for outages, else None
            if not self._parking_enabled or reason is None:   # no scheduler_version=1, or a fail-only error
                raise                             # today's failure path
        probe += 1
        parked_since = parked_since or workflow.now()
        left = parked_since + PARK_LIMIT - workflow.now()
        if left <= timedelta(0):
            raise _park_expired()
        await workflow.execute_activity("agent.park", {**_park_scope(payload), "reason": reason},
            activity_id=f"park-{iteration + 1}-{probe}",   # row, lease `suspended`, attention item;
            # a pause intent for billing and account parks
            start_to_close_timeout=PERSIST_TURN_TIMEOUT, retry_policy=BOOKKEEPING_RETRY)
        try:                                      # outage: the probe timer; others: no probe timer
            await workflow.wait_condition(lambda: self._release_version > seen,
                                          timeout=min(wait, left) if wait else left)
        except asyncio.TimeoutError:              # checked before waiting for `running`: an account
            if workflow.now() >= parked_since + PARK_LIMIT:   # park has paused the employee itself
                raise _park_expired()
        await self._wait_until_resumed()          # never probe while the employee is stopped
```

## 12. Batch processing

### 12.1 Batch Processing Patterns (category overview)

**Docs:** https://docs.temporal.io/design-patterns/batch-processing-patterns. Choose by record count and rate control: Basic Workflow ("up to a few hundred records"), Fan-Out with Child Workflows ("Up to ~500K records"), Batch Iterator (unlimited, "fixed page rate"), Sliding Window (unlimited, "configurable window") and MapReduce Tree (unlimited, "maximum speed"). Rules and limits:
- Basic Workflow: "Limit: 2,000 in-flight Activities per Workflow run (aim for 500)"; when the event count may pass 2,000, use the Batch Iterator.
- Schedules with Overlap Policies for recurring work; batch signalling from the CLI ("1 running batch job per namespace", "50 Workflows per second per batch").
- Limits: 51,200 events or 50 MB per history (a warning at 10,240 events or 10 MB); 10,000 Signals; "Updates per Workflow: 10 in-flight, 2,000 total". A run is terminated when its history "contains more than 2000 Updates" or "more than 10000 Signals" (https://docs.temporal.io/workflow-execution/event); "The server sets `SuggestContinueAsNew` at 90% of the limit" (https://docs.temporal.io/design-patterns/request-response-via-updates).
- The page's table says "2,000 max (aim for 500). Includes Activities, Signals, Child Workflows, cancellation requests", which reads as one shared limit; the limits page gives 2,000 for each kind (RFC-0004 §12.1). Cite the limits page.

**SDK:** `workflow.info().is_continue_as_new_suggested()`, `get_current_history_length()` and `get_current_history_size()` (1.30.0 `workflow/_context.py:166-195`); unchanged in 1.34.0.

**OpenCompany today:**
- **Basic Workflow:** `MachinaWorkflow` schedules every ready node with no in-flight cap (`server/services/temporal/workflow.py:646-910`). Hand-built graphs keep it in the page's first row; the cap belongs to §6.3.
- **History limits:** a 10,000-event soft cap plus the server's suggestion everywhere (`trigger_listener_workflow.py:89-102`; `workflow_control_workflow.py:27`, `:568-576`; `workspace_tasks_workflow.py:23-24`; `agent_workflow.py:332`), and bounded carries (256 events, 4,096 seen ids, a 1.9 MB check: `workflow_control_workflow.py:31-38`, `:705-711`).
- **The controller's Update budget.** Every versioned root registers and unregisters through two controller Updates (`server/services/temporal/execution_control.py:203-229`, through `execution_control_activities.py:17-27`, to `workflow_control_workflow.py:219-249`). A cron tick registers twice, the tick and its run (`server/nodes/scheduler/cron_scheduler/_workflow.py:81-85`; `workflow.py:505-512`): four Updates for about 20 events (est.). The controller rolls over on the server's suggestion first, so the 90% rule should cover the 2,000-Update limit, but nothing tests the server's behaviour: the Update-driven rollover test stubs the pressure hook (`server/tests/temporal/test_controller_messaging_integration.py:24-29`).
- **Schedules:** cron Schedules use `SKIP` and a 24-hour catch-up (`server/services/temporal/schedules.py:150`, `:226-229`), but the tick starts an abandoned run and returns, so `SKIP` covers only the tick (RFC-0004 H6; fixed by `on_busy` in RFC-0005 Phase 6).
- **Batch signalling:** not used. Stop and Resume fan out from a client loop over a Visibility list (`server/services/deployment/handlers.py:915-971`); RFC-0005 replaces discovery with the `sched_leases` registry (RFC-0005 §13.3). A CLI batch job would not fit: it is asynchronous, and Reset needs a barrier.

**Verdict:** Adopted with deviations. Caps and carries are in place; the controller's paging can fail the controller (§12.3), and the 2,000-Update limit rests on server behaviour nothing tests.

**Recommendation:**
1. **Test the Update limit on the pinned server.** A real-server test (`TEMPORAL_TEST_CLI`, CLI v1.9.1, server 1.32) sends 1,900 register and unregister Updates to a controller and asserts that it continues as new rather than being terminated. If the server does not suggest continue-as-new at 90% of the Update limit, count Updates and Signals in `_maybe_request_rollover` and roll over at 1,800 Updates or 9,000 Signals per run, behind `workflow.patched("controller-message-budget-v1")`.
   - Versioning: none for the test; a patch for the counter. When: Phase 1b (test, 1b.43); the controller-hardening lane (counter, if needed). Effort S each.
2. **RFC-0005's rollover rules count Updates.** As first written, RFC-0005 §10.2 rolled the entity over "past 4,000 events or 4,000 Signals" and the supervisor "when suggested, or daily". Both also take Updates (the supervisor `set_control_state`, `wait_for_checkpoint`, `set_limits` and `resume_after_budget`; the entity `interrupt_turn`), so both should also roll over at 1,800 Updates.
   - Versioning: new types (none). When: RFC-0005 Phase 4 (docs; §20). Effort S.

**Remarks:**
- `PollingTriggerWorkflow` checks its history only after a whole poll batch (`polling_trigger_workflow.py:203-245`); the 2 MB activity result bounds a batch in practice. T7 replaces the poll loops with one workflow per trigger whose history stays near zero.
- The MapReduce page's "signal storms" pitfall has a cousin here: every versioned root sends two Updates to its controller. RFC-0005's supervisor and `sched_leases` remove that traffic per run.

### 12.2 Fan-Out with Child Workflows

**Docs:** https://docs.temporal.io/design-patterns/fanout-child-workflows. Split a record set into fixed-size chunks and start one child per chunk, passing only `offset` and `length`. Rules:
- "Give each child a deterministic Workflow ID (`parentId/batch-<offset>`). This makes it safe to re-run the parent: Temporal deduplicates child starts by Workflow ID, so already-completed children are not re-executed." That holds only with a reuse policy that rejects closed ids; the SDK default (`ALLOW_DUPLICATE`) lets a completed child run again.
- "Cap concurrent children in the parent"; "Set `PARENT_CLOSE_POLICY_ABANDON` for fire-and-forget fan-outs" (with the default `TERMINATE`, cancelling the parent terminates the children).
- Pitfalls: "Starting too many children at once" (2,000 pending children per parent; "a single parent should not spawn more than 1,000 Child Workflow Executions"); passing large lists of ids; "A failed child does not automatically fail the parent unless you await all results."

**SDK:** `workflow.start_child_workflow` defaults to `parent_close_policy=TERMINATE`, `id_reuse_policy=ALLOW_DUPLICATE` and `cancellation_type=WAIT_CANCELLATION_COMPLETED` (1.30.0 `workflow/_workflow_ops.py:185-190`). 1.34.0 adds `WorkflowAlreadyStartedError.first_run_id`, set when the server provides it.

**OpenCompany today:**
- **One run per event.** The controller, the legacy listener, the poll loop and cron start one `MachinaWorkflow` per event with `ABANDON`, `ALLOW_DUPLICATE_FAILED_ONLY` and the id `<slug>-<label>-<event id or firing time>` (`trigger_listener_workflow.py:414-445`; `polling_trigger_workflow.py:330-358`; `cron_scheduler/_workflow.py:166-185`). The page's ids and policies are followed; a failed firing may run again under its id, so runs are at-least-once for failures.
- **No concurrency cap.** Nothing limits how many runs execute at once (RFC-0005 §4.1): a burst of 100 polled emails or webhooks starts 100 agent runs, and there is no daily budget yet (E11 lands in Phase 5).
- **Workspace tasks:** at most 32 children, `REJECT_DUPLICATE` (`workspace_tasks_workflow.py:20`, `:95-96`, `:114-121`).
- **Task Manager:** one abandoned `DelegatedTaskWorkflow` per assignment (`agent_workflow.py:1873-1903`), with no cap per turn or per team (`server/nodes/tool/task_manager/__init__.py:186-226`). RFC-0005 §19 capped open messaging tasks at 200 per employee, but not team tasks, until recommendation 3 (§20). Concurrency is then held by permits (§12.4).
- **Team job:** one lead child (`employee_job_workflow.py:113-120`).

**Verdict:** Adopted with deviations. Deterministic ids and policies follow the page; nothing caps concurrent runs per generation or open team tasks per lead. RFC-0005's fix follows the docs: one turn at a time per conversation (RFC-0005 §11) and admission with a per-employee cap of 4 plus the owner's slot (RFC-0005 §13.3).

**Recommendation:**
1. **New generations:** RFC-0005 Phases 4-5 as designed, and count delegated turns in admission (open in §24: how team jobs and delegations share an employee's capacity). Effort: in those phases.
2. **Old generations until R_k+1** (open in §24: whether they get an interim concurrency cap). If the owner wants an interim cap: the controller already carries `live_roots` across continue-as-new (`workflow_control_workflow.py:501-502`, `:702`), which is the page's carried `active` count. Wait before `_spawn_push_run` while `len(live_roots)` plus spawned-but-unregistered runs reaches the cap.
   - Versioning: waiting changes the command order, so freeze `run_cap_version=1` and the cap (default 4, RFC-0005's per-employee cap) into the generation's input at Start (RFC-0005 §24.1 rule 2).
   - When: the controller-hardening lane, if chosen. Effort M.
   - Risk: a burst waits longer; the controller's queue already holds it durably.
3. **Cap open team tasks:** `assign_task` refuses above 50 open tasks per team and 200 per employee (RFC-0005 §19's figure for messaging tasks), with a tool error that names the limit.
   - Versioning: none (tool body). When: RFC-0005 Phase 6. Effort S.
   - Risk: a legitimately large job is refused; the error tells the lead to wait.

**Remarks:**
- `ABANDON` without awaiting is the page's fire-and-forget form; failures are handled in the child and by the pause-on-failure breaker.
- Pending children per controller run stay well below 2,000, because the controller rolls over every few hundred spawns (est., from the "~15-25 events per firing" at `trigger_listener_workflow.py:64-73`).

### 12.3 Batch Iterator

**Docs:** https://docs.temporal.io/design-patterns/batch-iterator. Process one page per run, then continue as new with the next offset, so history never grows without bound. Rules:
- Choose a page size that keeps a run under about 2,000 events ("roughly `3 × pageSize`"); keep a counter in the arguments; "Fetch inside an Activity, not the Workflow".
- "Make `processRecord` idempotent. Activities have at-least-once execution semantics."
- "`continueAsNew` does not carry over in-memory state"; pass minimal state; stop on the last page.
- Per-record work is sequential unless fanned out within a page.

**SDK:** `workflow.continue_as_new(args=[...])` in 1.30.0 and 1.34.0.

**OpenCompany today:**
- **The controller's durable queue pages exactly once, and is built correctly:**
  - pages hold at most 100 events or 512 KB (`server/services/temporal/controller_queue.py:47-64`, `:74-85`);
  - a read deletes its rows under a mutation-ledger id `<run id>:queue:<n>` (`workflow_control_workflow.py:402-406`; `controller_queue.py:70`), so a retried read returns the same page (`run_runtime_mutation`, `server/core/database.py:749-819`);
  - spills are keyed by `sha256(controller:listener:event id)` and are idempotent (`controller_queue.py:17`, `:35-38`);
  - continue-as-new carries at most 256 events and spills the rest (`workflow_control_workflow.py:617-669`).
- **Its paging can fail the controller.** Read and spill use `DEFAULT_ACTIVITY_RETRY`, three attempts 1 s and then 2 s apart (`_retry_policies.py:63-68`), and are awaited in `run()` outside any `try` (`workflow_control_workflow.py:400-406`, `:626-629`, `:676-679`). An exhausted attempt fails the controller (SDK `worker/_workflow_instance.py:1824-1839`; no `failure_exception_types` is set), and a versioned generation cannot rebuild its controller (`handlers.py:1912-1913`: "Reset is required").
  - When the database refuses outright, the controller fails within seconds; with SQLite's 5-second lock waits, within about 20 s; at most about 3 minutes if every attempt hangs to its 60-second timeout.
  - The risk exists only while the controller holds a backlog (spilled pages, or more than 256 queued events at a rollover), which is exactly when an outage or a pause is most likely.
  - Whether a failed controller still answers its `status` Query, so that status reads keep showing it running, is unverified.
- The review read scans all of a controller's rows to filter by listener (`controller_queue.py:48-52`): quadratic on a large backlog.
- **The poll cycle** (`server/services/plugin/polling.py:206-270`, run by the controller at `workflow_control_workflow.py:792-908` and by the legacy loop at `polling_trigger_workflow.py:157-245`). Besides M3 (RFC-0004; RFC-0005 Phase 3):
  - **no byte bound.** Every new item's detail comes back in one activity result. The count is capped by the provider's page (Gmail 20, `server/nodes/google/_gmail.py:18-31`; Outlook 25, `server/nodes/microsoft/mail_receive/__init__.py:110-113`; IMAP 50, `server/config/email_providers.json:26`), so passing the 2 MB limit takes about 100 KB per Gmail message (HTML newsletters reach that) or one very large message. Then every cycle fails the same way, because the seen-id set updates only on success. With `mark_as_read` on (off by default, `server/nodes/google/gmail_receive/__init__.py:38`), the first attempt marks the mail read, the retry no longer sees it under the default `label:INBOX is:unread` query (`:88-91`), and the mail is lost;
  - **the cursor is the provider's whole visible window** (`seen_ids = current`, `polling.py:270`), not "the items appended so far".
- RFC-0005 Phase 0.3: "Poll events are not capped yet; that moves to Phase 3" (RFC-0005 §24.4); RFC-0005 §19 gives polls "1,000 unrouted | cycle skipped; cursor stays". T7 replaces the Schedule per poll with one small workflow per trigger that stores new items and the cursor in the database in one transaction.

**Verdict:** Adopted with deviations. The controller's queue is a correct exactly-once page iterator, but its paging activities can fail the controller, and the poll cycle has no page bound.

**Recommendation:**
1. **Paging never fails the controller.** Give `controller.queue.read` and `controller.queue.spill` an unlimited policy (new `QUEUE_PAGING_RETRY`: 1 s to 30 s, unlimited, the shared non-retryable types); both are idempotent.
   - Versioning: none (options, not compared on replay). When: Phase 1b (1b.16). Effort S.
   - Risk: a broken spill retries until fixed; the retry alert (§11.7) covers it.
2. **Bound the poll page** (old generations). In `_poll_cycle_activity`, emit at most 50 items or about 512 KB per cycle (defaults), and return `seen_ids = current - (new_ids - attempted_ids)`, where `attempted_ids` are the ids fetched this cycle (emitted, or failed as today). Ids not reached stay new for the next cycle; `post_emit` runs only for emitted items, as today; the controller's dedup (`workflow_control_workflow.py:842-848`) prevents a second emission. The result's shape does not change.
   - Versioning: none (activity body). When: Phase 1b (1b.18). Effort S.
   - Risk: a backlog drains over several cycles, at 50 items per interval. M3's fixes stay in Phase 3.
3. **RFC-0005 §15, under T7:** each poll cycle appends at most one page (50 items or 512 KB) and advances the cursor only over appended items (§20).
   - Versioning: new type (none). When: RFC-0005 Phase 3 (docs). Effort S.
4. **Index the review read** by listener. When: later. Effort S.

**Remarks:**
- Exactly once per page relies on the mutation ledger and deterministic child ids. Failed runs may run again (`ALLOW_DUPLICATE_FAILED_ONLY`), which the page accepts ("Make `processRecord` idempotent"). An event redelivered after it leaves the 4,096-key dedup window and Temporal's retention runs again; RFC-0005's 7-day `bus_dedupe` closes that.
- One item larger than the 2 MB limit still fails a cycle; RFC-0005's bodies by reference (`event_blobs`, RFC-0005 §8.1) fix that.
- T7's poll workflow is a Batch Iterator whose checkpoint lives in the database rather than in continue-as-new arguments: it records no history per quiet cycle, and its cursor survives a rebuilt Temporal (I10).
- Long loops that carry state: `AgentWorkflow` carries the transcript in its continue-as-new input with a byte guard (`agent_workflow.py:2996-3031`); the controller carries each trigger's `listener_args` with the whole graph (RFC-0004 H10, the "slim carry" of the controller lane).

**Sketch** (recommendation 2):

```python
# server/services/plugin/polling.py, _poll_cycle_activity
MAX_ITEMS_PER_CYCLE = 50                 # one page per cycle (default)
MAX_BYTES_PER_CYCLE = 512 * 1024

new_ids = sorted(current - prior_seen)   # the same subset is chosen on a retried cycle
events, attempted, size = [], set(), 0
for msg_id in new_ids:
    if len(events) >= MAX_ITEMS_PER_CYCLE or size >= MAX_BYTES_PER_CYCLE:
        break                            # the rest stay "new" for the next cycle
    attempted.add(msg_id)
    try:
        detail = await instance.fetch_detail(service, msg_id, params)
    except Exception as exc:  # noqa: BLE001 - today's M3 behaviour; fixed in Phase 3
        logger.warning("Polling fetch_detail failed; skipping id", msg_id=msg_id, error=str(exc))
        continue
    detail.setdefault("id", msg_id)
    size += len(json.dumps(detail, default=str))
    events.append(detail)
    try:
        await instance.post_emit(service, msg_id, params)
    except Exception:  # noqa: BLE001 - mark-as-read never blocks the cycle
        pass
return {"events": events, "seen_ids": list(current - (set(new_ids) - attempted))}
```

### 12.4 Sliding Window

**Docs:** https://docs.temporal.io/design-patterns/sliding-window. Keep a fixed number of children running; "each completion signal triggers the next record to start immediately". Rules:
- The parent keeps a live `active` count and blocks each start on `await workflow.wait_condition(lambda: self._active < window_size)`. Each child signals the parent by workflow id, without a run id, reading it from `workflow.info().parent`. The parent continues as new after `window_size` starts and carries `active`, the start index and the total, adding them with `+=` so signals that arrive early still count.
- "Use `PARENT_CLOSE_POLICY_ABANDON` on child Workflows"; "A window of 50–200 is a reasonable starting point"; register signal handlers before any await.
- Pitfalls: losing signals across continue-as-new (they are buffered for an existing id); the race between continue-as-new and draining; "Thundering herd on startup"; a child ignores NOT_FOUND when the parent has already finished (the page's sample matches the text "not found").

**SDK:** `workflow.get_external_workflow_handle(...).signal(...)`, `workflow.info().parent`, `wait_condition(..., timeout=)`, `continue_as_new` and `workflow.wait(..., return_when=asyncio.FIRST_COMPLETED)` exist in 1.30.0 and 1.34.0. Which exception class a Signal to a closed workflow raises was not confirmed (unverified).

**OpenCompany today:**
- **The turn's delegation window** for direct `delegate_to_*` children: "Keep a bounded sliding window so at most three descendants from this turn are active" (`agent_workflow.py:1442-1453`). The first three start (`:2023-2030`); the next starts only after the result awaited in call order completes (`:2343-2348`), so one slow first child holds the window (head of line).
- **Root-wide permits for Task Manager runners.** `agent.acquire_subagent_permit` polls the database every second inside a heartbeating activity (`agent_activities.py:2247-2316`), with unlimited attempts (`PERMIT_WAIT_RETRY`, `_retry_policies.py:127-132`). Each waiting delegation:
  - holds an activity slot on `machina-tasks`, whose 100 slots it shares with LLM steps (`worker.py:116`, `:289`; RFC-0004 M9), so enough queued delegations can starve running agents of LLM slots;
  - runs one write transaction per second (`BEGIN IMMEDIATE` on SQLite, the global advisory lock on PostgreSQL: `server/core/database.py:777-781`, through `run_runtime_mutation` at `:4143-4237`), adding to RFC-0005's N8.
  - Permits are reclaimed by age: active leases older than 3 hours are released on the next acquire (`database.py:4153-4183`). A delegated agent may legitimately run, or stay paused, longer, and the limit is then exceeded.
- **RFC-0005 admission** (RFC-0005 §13.3) applies the page's principle across workflows: the count lives in the database, the waiter waits in its workflow, a `wake` Signal by id, a 60-second re-check, and leases reaped when their turn is confirmed closed. As first written it did not mention team delegations or `acquire_subagent_permit`; recommendation 1 adds them (§20).
- **RFC-0005's relay window** (RFC-0005 §9.4: at most 32 inputs or 512 KB unconsumed per stream, batches of 16 or 256 KB, one Signal-With-Start in flight per stream) is a credit window that follows the page and respects the 10,000-Signal limit (fewer than 1,000 Signals per 10,000 messages, RFC-0005 §9.8).

**Verdict:** Missing and beneficial for delegations, whose waits poll in an activity, hold a slot and a write lock every second, and are reclaimed by age. Planned in RFC-0005, following the docs, for turns and the relay.

**Recommendation:**
1. **Delegations wait in the workflow, not in an activity.** Fold permits into RFC-0005 admission: a non-blocking claim activity (grant now, or enqueue this waiter; idempotent per `permit_id`), a `permit_granted` Signal by workflow id, a 60-second re-check, and leases reaped when their holder is confirmed closed, not by age. A release grants the oldest waiter of the same root and wakes only that one, avoiding a thundering herd.
   - Versioning: a named generation flag for RFC-0005 Phase 6, `permit_wait_version=1`, in `resource_manifest` and frozen at Start (RFC-0005 §24.1 rule 2). `AgentWorkflow` and `DelegatedTaskWorkflow` are shared types and branch on it; generations without it keep the polling activity.
   - When: RFC-0005 Phase 6. Effort M.
   - Risk: a lost wake costs at most the 60-second re-check; a claim retried after its grant committed must return the existing lease.
2. **Interim:** Reset releases its generation's permits (§10.2 recommendation 2, 1b.21), which removes the main reason for the age reclaim; then raise the 3-hour threshold or drop it.
   - Versioning: none. When: Phase 1b (1b.21, the release); the threshold after it. Effort S.
3. **Refill on any completion.** Wait with `workflow.wait(..., return_when=asyncio.FIRST_COMPLETED)` on the started delegations and start the next as soon as any finishes, while still appending results in call order.
   - Versioning and timing: §6.3 rec 3 owns this change, one patch, `workflow.patched("agent-delegation-window-v2")` (a new command order). When: with it, P2, the cancellation lane. Effort S-M.

**Remarks:**
- A Signal sent before the waiter's handler runs is buffered (page), and a waiting `DelegatedTaskWorkflow` does not continue as new, so nothing needs carrying.
- RFC-0005 admission counts turns, not delegations; whether delegated turns count toward an employee's cap is open in §24.

**Sketch** (recommendation 1; `DelegatedTaskWorkflow` with `permit_wait_version=1`):

```python
@workflow.signal
def permit_granted(self, lease_id: str) -> None:
    self._granted = lease_id                      # buffered if it arrives before the wait

async def _await_permit(self, root_id: str, permit_id: str, limit: int) -> str:
    while True:
        claim = await workflow.execute_activity(
            "agent.try_acquire_subagent_permit",    # one transaction: grant now, or enqueue this waiter
            {"root_execution_id": root_id, "permit_id": permit_id, "limit": limit,
             "waiter_workflow_id": workflow.info().workflow_id},   # the wake target, by id only
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=BOOKKEEPING_RETRY,         # idempotent per permit_id; no slot held while waiting
        )
        if claim["granted"]:
            return claim["lease_id"]
        try:                                        # a lost wake costs at most 60 s
            await workflow.wait_condition(lambda: self._granted is not None,
                                          timeout=timedelta(seconds=60))
            return self._granted
        except asyncio.TimeoutError:
            continue

# agent.release_subagent_permit: in one transaction, release the lease and grant the oldest
# waiter of the same root; after the commit, signal that waiter by workflow id. A waiter that
# has already closed is skipped; its own re-check would have found the grant anyway.
```

### 12.5 MapReduce Tree

**Docs:** https://docs.temporal.io/design-patterns/mapreduce-tree. Recursively split a record set into child "Node" workflows until a `leafThreshold`; leaves do the work and signal results up the tree. It is for "maximum throughput" when "downstream systems can absorb an unbounded burst of concurrent requests". Rules: a `MAX_DEPTH` guard; "Avoid external writes in Node Workflows"; "Use signals for result aggregation, not return values. A parent cannot directly await a child started in a previous Workflow run"; at most 1,000 direct children per node; pitfalls "Thundering herd", "Signal storms" and "History bloat in the Root Workflow"; prefer activities over leaf workflows unless each record needs its own history.

**SDK:** the same APIs as Sliding Window (§12.4); nothing version-specific.

**OpenCompany today:** not found. Delegation trees are at most two levels deep (`max_delegation_depth = min(2, ...)`, `agent_workflow.py:1448-1450`) and teams are small. The page's principle does show up: delegation results come back through completion events rather than awaited returns, so they survive the lead's continue-as-new, which waits only for this turn's live delegations (`agent_workflow.py:2988-2996`).

**Verdict:** Not applicable. Every unit of work here is an LLM turn under a daily budget (E11), class floors (E10) and provider limits; a tree built for maximum speed works against all three.

**Recommendation:** do not adopt (never). A future bulk job ("process 5,000 rows") should use the Batch Iterator or an admission-bounded window (§12.3, §12.4).

**Remarks:** the "signal storms" pitfall has a cousin today: every versioned root sends two Updates to its controller (§12.1).

## 13. QoS and throughput

### 13.1 QoS & Throughput Patterns (category overview)

**Docs:** https://docs.temporal.io/design-patterns/qos-throughput-patterns. These patterns "control how fast
work executes, protect downstream services from overload, and make sure no single caller or tenant dominates
dispatch." All three act in the Matching service: the part of the Temporal server that keeps each task queue's
backlog and hands tasks to polling workers. (A task queue is a named queue that workers poll for workflow tasks
and activity tasks.) The page's selection rules:
- "A downstream dependency has a fixed rate limit": Downstream Rate Limiting (§13.2).
- "Urgent work should move ahead of bulk work": Priority (§13.3).
- "Multiple tenants share the same Workers": Fairness (§13.4).

**SDK:** every API the three patterns need exists in 1.30.0 (details in §13.2 to §13.4).

**OpenCompany today:**
- The owner decisions create four QoS needs:
  - the owner first when capacity is short (RFC-0005 E10): Priority;
  - employees must not starve each other: Fairness, with employees as the tenants, since each instance has
    one owner (E1);
  - provider and channel limits, which providers set per API key or account: rate limiting by credential,
    not by queue;
  - the daily budget per employee (E11), which is not a Temporal pattern.
- Only rate limiting exists today, as per-process worker caps on queues that mix many downstreams (§13.2).
  No code under `server/` sets a Priority or a fairness key (grep for `Priority(` and `fairness_key`).
- Worker slots also absorb long waits. Each waiting approvalGate draft holds a `triggers-event` slot for up to
  24 h per attempt, and local mode has 50 such slots, shared with the other event-trigger activities
  (`server/nodes/workflow/approval_gate/__init__.py:48`, `:181-185`; `server/services/temporal/worker.py:413-423`,
  `:477`). RFC-0005 Phase 7's ApprovalWorkflow removes this.

**Verdict:** category overview; the pattern verdicts are in §13.2 to §13.4.

**Recommendation:**
1. **One enforcement point per resource.** RFC-0005 §13 names, for each scarce resource, the single mechanism
   that limits it:

   | Resource | Limited by | Where |
   |---|---|---|
   | Process CPU and memory | worker slot suppliers | `worker.py`; §15 |
   | Concurrent turns, class shares (E10), the per-employee cap | admission leases | RFC-0005 §13.3 |
   | Provider requests and tokens per minute; channel send rates | per-credential and per-account buckets in the app database | RFC-0005 §13.4, §17.2 |
   | Dispatch order on shared queues | Priority and fairness keys | RFC-0005 §13.2 |
   | Spend | the daily budget ledger | RFC-0005 §14 |

   *Versioning:* none (a design rule). *When:* the text now (§20 amendments); code in RFC-0005 Phases 4, 5 and
   7. *Effort:* S. *Risk:* none; it removes RFC-0005's double enforcement of class floors (§13.3 rec. 5).

**Remarks:**
- Work that skips Matching also skips priority, fairness and queue caps. Two paths skip it: local activities
  (§14.2) and eager activity dispatch, which the pinned server 1.32.0 turns on by default
  (`system.enableActivityEagerExecution`). The pages say this for rate limits ("bypass the task queue entirely
  and cannot be throttled by the server"); for priority and fairness it is an inference. It does no harm for
  millisecond bookkeeping, so keep only such work on those paths.
- E10's "Every class keeps a reserved share" is read here as a share of admitted turns (default; §13.3 rec. 5).

### 13.2 Downstream Rate Limiting

**Docs:** https://docs.temporal.io/design-patterns/downstream-rate-limiting. The pattern caps how many activities
per second reach a downstream service. Throttled activities go to a dedicated task queue whose workers set
`max_task_queue_activities_per_second`. "The Temporal matching service enforces this limit before dispatching
tasks", so it "holds across all Worker replicas without coordination." The rules that matter:
- The workflow routes the activity with an explicit `task_queue`. The first pitfall: "Forgetting to override the
  task queue in Activity options ... bypasses the rate-limited Worker entirely."
- "Use a separate Task Queue for each rate limit": the cap applies to every activity on the queue.
- Every worker on the queue sets the same value: "If Workers report different values, the server applies the
  value from the last Worker that polled." `max_activities_per_second` is a different cap, held per process:
  "the effective queue throughput is the per-worker cap multiplied by Worker count."
- "Confusing throughput limits with concurrency limits ... Long-running Activities that hold slots for minutes may
  exhaust concurrency before the RPS cap applies."
- A task queue is split into partitions, "default four": slices of the backlog that Matching serves separately.
  The rate holds "as an average over time" with short bursts above it; leave headroom, or use fewer partitions.
- Eager activity dispatch (the server hands a new activity straight back to the worker that completed the
  scheduling workflow task) skips the queue and its cap, and "every SDK disables it on a Worker that sets
  MaxTaskQueueActivitiesPerSecond".
- Monitor `temporal_activity_schedule_to_start_latency` and the queue's `ApproximateBacklogCount`
  (DescribeTaskQueue).
- The page's comparison table says a queue cap cannot change at runtime. The develop page disagrees: a limit set
  with `temporal task-queue config set --queue-rps-limit` "takes precedence over the limit set through Worker
  Options", and `--fairness-key-rps-limit-default` adds a per-fairness-key limit scaled by the key's weight
  (https://docs.temporal.io/develop/task-queue-priority-fairness). The table also lists "Client-side token bucket
  in Activity" as an alternative, and the linked guide answers a 429 with `next_retry_delay` taken from
  `Retry-After`.

**SDK:** `Worker(max_task_queue_activities_per_second=..., max_activities_per_second=...)` in 1.30.0
(`temporalio/worker/_worker.py:127-128`; the docstring at `:256-264` warns that differing values "will thrash
with the last poller winning"); unchanged in 1.34.0, which adds `max_eager_activity_reservations_per_workflow_task`
(default 3). Neither version wraps runtime queue configuration, but the raw RPCs exist in 1.30.0:
`client.workflow_service.update_task_queue_config` and `describe_task_queue` with `report_stats`
(`temporalio/bridge/services_generated.py:2046`, `:426`). `ApplicationError(next_retry_delay=...,
category=ApplicationErrorCategory.BENIGN)` exists in 1.30.0 (`temporalio/exceptions.py:111-121`, `:133-134`). A
BENIGN error is logged at DEBUG and kept out of failure metrics; retries are unchanged.

**OpenCompany today:**
- Rate limits are worker-side and per process only: `max_activities_per_second` on each pool worker
  (`server/services/temporal/worker.py:584`): rest-api 100/s, ai-heavy 60/s, browser 10/s, messaging 20/s, none on
  the other queues (`:430-440`), overridable with `TEMPORAL_<QUEUE>_RATE_LIMIT` (`:481-496`). No queue-level cap,
  `update_task_queue_config` or `describe_task_queue` call exists.
- Those queues mix many downstreams. `rest-api` carries http_request, Gmail, GitHub, AWS, Stripe, Dodo Payments,
  search, Apify, TikHub, speech and translation plugins; `messaging` carries WhatsApp, WhatsApp Business,
  Telegram, Discord, email, Twitter and social sends (grep of `task_queue = TaskQueue.*` under `server/nodes`).
- AgentWorkflow's LLM and compaction steps never reach ai-heavy. `agent.execute_llm_step`
  (`server/services/temporal/agent_workflow.py:3183-3188`, and the Pi override at
  `server/services/temporal/pi_workflow.py:257-261`) and `agent.compact_context` (`agent_workflow.py:2829-2835`)
  pass no `task_queue`, and agent children start without one (`server/services/temporal/workflow.py:813-828`).
  Both therefore run on `machina-tasks`, which has 100 activity slots and no cap (`worker.py:289-290`;
  `server/services/temporal/lifecycle.py:207-210`). ai-heavy still carries other provider calls: canvas
  chat-model nodes, embeddings, vision, the specialized CLI agents, and aiAgent and chatAgent when a frozen
  snapshot dispatches them as activities.
- On a desktop ai-heavy has at most two resource-tuned slots (`worker.py:413-423`, `:458-479`, `:504-537`) and
  runs agent activities that take minutes, so its 60/s cap can never bind: the page's "throughput versus
  concurrency" pitfall.
- 429 handling follows the linked guide for LLM steps: a provider's Retry-After becomes `next_retry_delay`
  (`server/services/temporal/agent_activities.py:246-303`) under unlimited attempts (`LLM_STEP_RETRY`,
  `server/services/temporal/_retry_policies.py:94-100`). No code uses the BENIGN category; Phase 1b adds it to
  retryable LLM errors (1b.28, §18.5).
- Comments disagree with the code: `agent_activities.py:12-13` ("Lives on TaskQueue.AI_HEAVY") is wrong;
  `worker.py:425-429` ("Anthropic/OpenAI tier limits on ai-heavy") is misleading; `server/services/plugin/scaling.py:25`
  calls ai-heavy "long-running LLM + agent loops".
- Plans: RFC-0004 §11.3 put LLM steps on "their own queue (or one per provider)" with a queue-wide cap (§21). RFC-0005
  routes them to class queues instead (RFC-0005 §13.2), caps providers with "Per-credential token buckets ... for
  each class" and BENIGN Retry-After retries (RFC-0005 §13.4), and makes sends take a token from per-account and
  per-recipient buckets, raising a retryable error with `next_retry_delay` "so no worker slot is held while
  waiting" (RFC-0005 §17.2).

**Verdict:** Misused. Per-process caps sit on queues that mix many downstreams, and the main downstream bypasses
the capped queue, which is the page's first pitfall. RFC-0005's per-credential buckets deviate from the page on
purpose and correctly: providers limit requests and tokens per minute per API key, which one dispatch cap per queue
cannot express.

**Recommendation:**
1. **Route LLM and compaction steps explicitly.** For generations with `scheduler_version=1`,
   `agent.prepare_payload` (`prepare_agent_payload` in `agent_activities.py`) records
   `llm_route = {"task_queue": "llm-c<k>", "priority_key": k}` in its result, where k is the class the entity
   admitted the turn with. `AgentWorkflow._execute_model_step`, the Pi override and the compaction call pass that
   `task_queue`; when a merge raises the class (RFC-0005 §11 step 5), the workflow computes the higher class from
   the merged input recorded in history and also passes `priority=Priority(priority_key=k)`. Class queues get
   fixed-size slot suppliers, no shared slot pool (§13.3 rec. 5) and no queue-wide cap. Their workers start in
   every process that runs activities, whatever `TEMPORAL_WORKER_POOL_ENABLED` says. *Versioning:* per-generation
   input flag (`scheduler_version=1`); old histories replay because their recorded `prepare_payload` result has no
   route. *When:* RFC-0005 Phase 5. *Effort:* M. *Risk:* a frozen queue that nothing polls hangs runs; workers
   start unconditionally and queue names follow the retirement rule (§15.2 rec. 6).
2. **Provider limits in per-credential buckets in the app database** (requests and tokens per minute), with class
   shares inside each bucket so C1 keeps headroom on a saturated key, and C4 sends using only the top half
   (RFC-0005 §17.4). Bucket values (default): a shipped per-provider table only seeds them; a 429 with Retry-After
   pauses that credential's bucket for that long, for every class; calls routed through the OpenCompany Gateway
   rely on the Gateway's own limits and answers. A throttled step raises
   `ApplicationError(next_retry_delay=wait, category=ApplicationErrorCategory.BENIGN)`, so it holds no slot while
   it waits. An honoured Retry-After is capped at 5 minutes per retry, with the raw hint kept for the UI; there is
   no 60 s cap like the cookbook's (§16.5). Throttle waits never spend a bounded attempt budget. A Gateway
   `budget_exceeded` answer, and a wait longer than the step's remaining time budget (§11.3, §11.5; the hinted wait
   becomes the first probe), park the turn (T11) under RFC-0005 Phase 5 (T18), for new generations only; existing
   generations keep today's behaviour. *Versioning:* none for the buckets (activity body); parking follows
   `scheduler_version=1` (T18). *When:* Phase 5 for LLM steps, Phase 7 for sends. *Effort:* M. *Risk:* seed values
   that are too tight slow every turn; the 429 path corrects them, never the other way round.
3. **Retire the mixed-queue worker caps when their replacement exists** (`worker.py:430-440`): ai-heavy's 60/s once
   rec. 2's buckets cover the provider calls that run there (canvas chat-model, embedding and vision nodes, the
   specialized agents; Phase 5), messaging's 20/s with the egress buckets (Phase 7), rest-api's 100/s later, with a
   bucket for any plugin whose provider publishes a limit. Use `max_task_queue_activities_per_second` only on a
   queue that serves exactly one downstream with one fixed limit, and set it once with `update_task_queue_config`
   from the deploy logic (the API value wins over worker options and reaches every process). *Versioning:* none
   (worker configuration). *When:* Phases 5 and 7; rest-api P3. *Effort:* S. *Risk:* removing messaging's cap before
   Phase 7 would leave send bursts unguarded; hence the order.
4. **Never set `max_task_queue_activities_per_second` on `machina-tasks`.** A worker that sets it loses eager
   activity dispatch. Scope: eager dispatch applies only to activities a workflow schedules on its own queue on the
   same worker, and the pinned server 1.32 turns it on by default; the 0.3.1 corpus, recorded on server 1.31.2,
   shows no eager dispatch (9-37 ms). *Versioning:* none. *When:* a rule from now on. *Effort:* none.
5. **Fix the comments** at `agent_activities.py:12-13` (the step runs on the workflow's queue, `machina-tasks`),
   `worker.py:425-429` (ai-heavy carries canvas chat-model, embedding, vision and activity-dispatched agent calls,
   not AgentWorkflow's LLM steps) and `scaling.py:25`. *Versioning:* none. *When:* Phase 1b (1b.45). *Effort:* S.

**Remarks:**
- On the dev server a queue cap is enforced across four partitions, with bursts above it; one partition (§13.4
  rec. 1) makes it exact.
- Per-fairness-key limits could enforce per-account send rates in the server if every activity on the send queue
  carries `fairness_key=<account>` (§13.4 rec. 3 gives sends their own queue). Waiting tasks then sit in the
  server's backlog and hold no slot. Limits: dispatch rate only (no tokens, no per-recipient limit), one default
  per queue scaled by weight, and the weight also sets the fairness share. An option, not a replacement for the
  buckets.
- "Run at least two Worker processes per queue for availability" does not fit a desktop, where one process is the
  deployment unit. Hosted tier S's second process (T8) still has one poller process per queue.
- Per-worker caps multiply with the number of processes that poll a queue (tier L); another reason to keep
  limits in the database.
- A test that replays a 0.3.1 fixture must show no `task_queue` on the LLM step; a fixture recorded with
  `scheduler_version=1` must show `llm-c<k>` (§23.4).

**Sketch:**

```python
# agent_activities.prepare_agent_payload (an activity): the route lands in its recorded result,
# so replay reads it from history. Only generations with scheduler_version=1 get one.
if context.get("scheduler_version") == 1:
    k = int(context["turn_class"])                       # 1..4, fixed when the entity admitted the turn
    payload["llm_route"] = {"task_queue": f"llm-c{k}", "priority_key": k}

# AgentWorkflow._execute_model_step (and PiCodingAgentWorkflow's override)
async def _execute_model_step(self, llm_payload, context, payload, iteration):
    options = {}
    route = payload.get("llm_route")
    if route is not None:                                # absent in every pre-flag history
        k = min(route["priority_key"], self._merged_class_key or route["priority_key"])
        options["task_queue"] = f"llm-c{k}"
        if k != route["priority_key"]:                   # a merge raised the class (RFC-0005 §11 step 5)
            options["priority"] = Priority(priority_key=k)   # fairness key stays inherited
    return await workflow.execute_activity(
        "agent.execute_llm_step", args=[llm_payload], activity_id=f"llm-step-{iteration + 1}",
        start_to_close_timeout=LLM_STEP_TIMEOUT, heartbeat_timeout=LLM_STEP_HEARTBEAT_TIMEOUT,
        retry_policy=LLM_STEP_RETRY, **options)

# agent.execute_llm_step: wait for the credential's bucket without holding the slot.
wait = await credential_buckets.take(database, credential_key, turn_class, estimated_tokens)
if wait is not None:
    raise ApplicationError("Provider rate limit reached; waiting", {"retry_after": wait.total_seconds()},
                           type="LLMError.rate_limit", next_retry_delay=wait,
                           category=ApplicationErrorCategory.BENIGN)

# worker.py: fixed slots, no queue-wide cap, started whatever TEMPORAL_WORKER_POOL_ENABLED says.
for queue in ("llm-c1", "llm-c2", "llm-c3", "llm-c4"):
    Worker(client, task_queue=queue, activities=[execute_llm_step, compact_context],
           max_concurrent_activities=settings.llm_queue_slots[queue], identity=_worker_identity(queue))
```

### 13.3 Priority

**Docs:** https://docs.temporal.io/design-patterns/priority-task-queues. A Priority key is an integer from 1
(highest) to 5 set on a workflow, activity or child workflow; Matching "exhausts all backlogged Tasks at a given
level before dispatching to the next", so urgent work overtakes a backlog of bulk work on a shared queue. The rules
that matter:
- "Tasks default to Priority `3` when no key is set." Activities and child workflows inherit their workflow's key
  unless they set their own.
- "Priority applies to dispatch. It does not preempt running Tasks or reserve Worker capacity." A key-5 task may
  still hold a slot when a key-1 task arrives; for hard isolation, "Use separate Task Queues with dedicated Worker
  pools."
- Best practices: at most five coarse levels; "Reserve Priority `1` for genuinely urgent work"; "Set the initial
  Priority key at Workflow start"; override activity priority deliberately; monitor backlog per level.
- Pitfalls: everything at 1; low-priority starvation ("Use a Schedule-To-Start Timeout on low-priority Activities to
  surface starvation as a visible failure"); "The Priority key is evaluated when a Task enters the queue and cannot
  be changed while it waits"; assuming hard isolation.
- The develop page adds: priority "is enforced within a single Task Queue partition", so on imbalanced partitions
  "lower-priority Tasks on a lighter partition can dispatch ahead"; "Continue-As-New inherits from the current
  execution unless explicit values are passed"; self-hosted servers turn it off with `matching.useNewMatcher=false`.

**SDK:** `temporalio.common.Priority` in 1.30.0 (`temporalio/common.py:1149-1231`): `priority_key` is a positive
integer and defaults to (min+max)/2 = 3; unset fields inherit from the calling workflow (`:1154-1158`). It is
accepted by `start_workflow` and `execute_workflow`, `WithStartWorkflowOperation` (Update-with-Start),
`start_child_workflow`, `execute_activity` and Schedule actions (`temporalio/client/_schedule.py:553`), and
Signal-With-Start sends it with the start request, where it applies only when the call starts a run.
`workflow.continue_as_new` takes no priority in 1.30.0 or 1.34.0, so a run chain keeps its first key. Local
activities take none. Unchanged in 1.34.0. The pinned server 1.32.0 has priority on (`matching.useNewMatcher`
true) with five levels (`matching.priorityLevels`).

**OpenCompany today:**
- Nothing is keyed: every task is priority 3 and FIFO within its partition. Owner chat competes on `machina-tasks`,
  whose 100 activity slots also carry every LLM step (`worker.py:289`).
- Start sites without a key: cron Schedule actions (`server/services/temporal/schedules.py:213-219`),
  ApprovedToolCallWorkflow (`server/services/approvals/execution.py:65-69`), EmployeeJobWorkflow
  (`server/services/employees/jobs.py:240-243`), the Workspace task controller's Update-with-Start
  (`server/services/node_invocations.py:52-58`), manual canvas Runs and Start-node runs
  (`server/services/temporal/executor.py:181-185`), PiCodingAgentWorkflow (`server/services/workflow.py:367`),
  the generation controller (`server/services/deployment/handlers.py:640-660`) and legacy listeners
  (`server/services/deployment/manager.py:984-992`).
- Inside one generation's controller, events spawn strictly FIFO (`_next_event` returns 0,
  `server/services/temporal/workflow_control_workflow.py:361-363`), and the spawn routine awaits a "waiting"
  broadcast after the child start (`server/services/temporal/trigger_listener_workflow.py:504-510`). Each queued
  event therefore holds the controller's loop about 0.52 s in the recorded corpus (filter, "idle" broadcast,
  child start, "waiting" broadcast), and an owner message behind N events waits about N x 0.5 s. That is
  head-of-line blocking inside one workflow, which Temporal Priority cannot fix (§14.1 rec. 3 shortens it).
- Plans: RFC-0005 §13.1-§13.2 maps classes C1-C5 to keys 1-5 "on entity starts, turn children and every activity
  (inherited), and on sends", gives LLM steps class queues `llm-c1`..`llm-c4`, sends background queues
  `schedule_to_start` timeouts, and raises a turn's key when a merged input has a higher class (RFC-0005 §11
  step 5).

**Verdict:** Planned in RFC-0005 (follows the docs, misses details the docs call out): partitions; an unkeyed start
is 3, which is class C3; an entity's key is fixed at its first start; schedule-to-start timeouts fail work instead
of revealing starvation; control actions need key 1; class floors are enforced twice.

**Recommendation:**
1. **Correct RFC-0005 §13 now** with items 2 to 6 (§20). *Versioning:* none. *When:* this RFC. *Effort:* S.
2. **An explicit key on every top-level start.** One helper, `services/temporal/priority.py`, holds the table, and
   an AST test fails when a top-level start (client `start_workflow` or `execute_workflow`,
   `WithStartWorkflowOperation`, `ScheduleActionStartWorkflow`) omits `priority=`. Keys (default):
   - 1: owner chat (the talk lane), approval decisions and the ApprovedToolCallWorkflow or ApprovalWorkflow that
     sends them; control actions and the supervisor that applies them; manual canvas Runs (generation 0, with no
     admission lease by default: O8, §24.2) and Workspace tasks;
   - 2: the customer lane;
   - 3: the worker lane, EmployeeJobWorkflow, delegations; PiCodingAgentWorkflow takes its invoking turn's key;
   - 4: cron Schedule actions, poll workflows (T7), Start-node runs (generation above 0), alarms;
   - 5: upkeep.

   Old generations' controllers stay unkeyed until Reset: keying the controller would make every child it spawns
   inherit the controller's key. *Versioning:* none for client-side start options and new workflow types; a key on a
   child start inside a shared type is a command attribute and goes behind `event_os_version=1` (RFC-0005 §24.1
   rule 2). *When:* RFC-0005 Phase 4, together with the entities. Keys on supervisor and entity starts cannot wait
   for Phase 5: a supervisor started without a key keeps 3 for its whole generation, because continue-as-new cannot
   change it. *Effort:* M. *Risk:* a misclassified start fills level 1; the table lives in one module and the test
   names every start site.
3. **Key entities by lane, not by their first input.** Talk lane 1, customer lane 2, worker lane 3; turn children
   carry their inputs' class key explicitly; activities inherit. A workflow's key is set when it starts,
   Signal-With-Start applies its start options only when it starts a run, and continue-as-new inherits, so an
   entity first started by a C4 tick would otherwise process owner messages at key 4 (RFC-0005 scenario (c) puts C4
   and C1 inputs on one thread). The supervisor and the ApprovalWorkflow get key 1 because Stop, Resume and decide
   arrive as Updates on their workflow tasks, which T10's deliverer resends until applied. The
   supervisor's own activities would inherit that 1, so they set their keys explicitly: upkeep 5, alarm firing 4.
   *Versioning:* none (new types). *When:* Phase 4. *Effort:* S. *Risk:* a supervisor activity that forgets its
   key runs at 1; the supervisor schedules few activity types, and a unit test can list them.
4. **No schedule-to-start timeouts on any queue.** A schedule-to-start timeout (the longest an activity may wait
   in the queue before a worker takes it) fails the activity without retry, and RFC-0005 §10.3 retries an entity's
   inputs once and then dead-letters them, so a busy instance would dead-letter background work. Measure instead:
   schedule-to-start latency and backlog from the SDK metrics (1b.38, §17.10), and per key from the dev server's
   metrics on its pinned loopback port (1b.33, §14.1 rec. 2) and `describe_task_queue(report_stats=True)`, whose
   response carries `stats_by_priority_key` in 1.30.0; an upkeep check raises an attention item when C4 backlog
   age passes a threshold (RFC-0005 §18.3). If a timeout is ever used, classify `SCHEDULE_TO_START` as BENIGN and
   return the inputs to pending without counting a failed turn. *Versioning:* none. *When:* Phase 5 (rule), Phase
   8 (attention item). *Effort:* S. *Risk:* none.
5. **Enforce class floors once, in admission (default for E10).** E10's "reserved share" means a share of admitted
   turns, enforced only by `admission.acquire` (RFC-0005 §13.3). Each class queue gets a fixed-size slot supplier
   (the part of a worker that decides how many tasks of a kind it may run at once) sized at least to admission's
   maximum grant for that class, so an admitted step never waits for a slot. Drop the `ClassSlotPool` and use the
   fixed sizes RFC-0005 already names as its fallback. Reasons: `reserve_slot` "is called before polling for new
   tasks" and `ActivitySlotInfo` carries only `activity_type` (`temporalio/worker/_tuning.py:76-121`, unchanged in
   1.34.0), so each idle class poller pins a slot of a small desktop pool for its whole long poll; the pool is per
   process, so its floors split once workers run in several processes (T8, tier L); and two enforcement points can
   disagree. LLM steps are asynchronous I/O, so generous slot counts cost little; the binding limits are provider
   quota and the budget.
   *Versioning:* none. *When:* Phase 5. *Effort:* S (it removes work). *Risk:* team fan-out can run more LLM steps
   than turns; within a class queue, fairness keys share the slots by employee.
6. **Tool queues stay priority-only (default).** Priority does not preempt, so a C4 browser task can hold one of the
   two desktop browser slots for minutes while a C1 tool call waits. Measure schedule-to-start per key on
   `browser` and `ai-heavy` before reserving slots for C1. *When:* Phase 8 measurement; decide after. *Effort:* S.

**Remarks:**
- With four partitions per queue a lower key on a lighter partition can dispatch first; one partition (§13.4 rec.
  1) makes the order exact.
- Five levels, all used. The page wants coarse levels with 1 reserved for urgent work; C1 qualifies, and
  control-plane workflows share it because their workflow tasks are short.
- A parked or budget-suspended turn (T11, RFC-0005 §14) holds no slot. Its admission lease moves to `suspended` and
  counts toward no floor and no shared capacity; it re-acquires under the same turn id (default).
- Raising a turn's class on merge changes the key of later steps only, which matches "cannot be changed while it
  waits".
- The raw RPC `update_workflow_execution_options` accepts a priority in 1.30.0; whether server 1.32 re-prioritizes a
  running workflow that way is unverified, so nothing should rely on it.

**Sketch:**

```python
# services/temporal/priority.py (new): the one table of keys, so no start site guesses.
from temporalio.common import Priority

OWNER, CUSTOMER, INTERNAL, BACKGROUND, UPKEEP = 1, 2, 3, 4, 5      # RFC-0005 §13.1, C1-C5
LANE_KEYS = {"talk": OWNER, "customer": CUSTOMER, "worker": INTERNAL}

def start_priority(key: int, employee_workflow_id: str | None) -> Priority:
    """For every top-level start. Unkeyed means 3 (C3), and continue-as-new keeps
    the first run's key for the whole chain."""
    return Priority(priority_key=key, fairness_key=employee_workflow_id or None)

# services/temporal/executor.py: a manual canvas Run is the owner's; a Start-node run is a C4 tick.
priority = start_priority(OWNER if int(generation or 0) == 0 else BACKGROUND, workflow_id)

# tests/temporal/test_priority_keys.py
TOP_LEVEL_STARTS = {"start_workflow", "execute_workflow", "WithStartWorkflowOperation",
                    "ScheduleActionStartWorkflow"}

def test_every_top_level_start_sets_priority():
    missing = []
    for path in server_python_files("tests", "scripts"):           # tests/_source_tree.py
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and _called_name(node) in TOP_LEVEL_STARTS
                    and not any(k.arg == "priority" for k in node.keywords)):
                missing.append(f"{path.relative_to(SERVER_ROOT)}:{node.lineno}")
    assert not missing, "start without priority= runs as class C3: " + ", ".join(missing)
```

### 13.4 Fairness

**Docs:** https://docs.temporal.io/design-patterns/fairness. A fairness key (and optional weight) on workflows,
activities and child workflows shares dispatch among tenants on one queue, so "a burst from one caller does not
starve others." The rules that matter:
- The model is "one virtual queue per Fairness key, with weighted round-robin dispatch", approximated "with stride
  scheduling and a count-min sketch"; FIFO within a key; unkeyed tasks share an implicit empty-string key with
  weight 1.0 and "compete as one group".
- It must be enabled; self-hosted servers set `matching.enableFairness`.
- "Fairness is best effort": ratios vary across partitions and Worker Versioning boundaries; accuracy "can degrade
  with a large number of Fairness keys"; weights apply when a task is scheduled; "It does not account for Task
  duration or resource use."
- "Use stable, consistent Fairness keys": "account identifiers or tenant slugs instead of display names."
  "Combine Priority and Fairness": Priority separates classes, fairness shares within each.
- Pitfalls: enabling it with a backlog drains that backlog "in its original order" first; "Task Queue partitioning
  reducing accuracy" (single-partition queues on request); fairness after a restart. The develop page: "During
  server restarts, Temporal preserves fairness state for the top 100 keys"; per-key rate limits and up to 1,000
  weight overrides through task-queue configuration.

**SDK:** `Priority.fairness_key` (at most 64 bytes) and `fairness_weight` (clamped by the server to 0.001-1000) in
1.30.0 (`temporalio/common.py:1177-1197`), unchanged in 1.34.0; configuration through the raw RPCs in §13.2.

**OpenCompany today:**
- Nothing is keyed; every task sits in the empty-string key.
- The dev server's argv passes no dynamic configuration (`server/services/temporal/_runtime.py:143-165`). CLI v1.9.1
  builds a static dynamic-config client on every start, so a value must be passed on every boot. The pinned server
  1.32.0 defaults: `matching.enableFairness` false ("Implies matching.useNewMatcher"), four write and read
  partitions per queue, `matching.fairnessPassDither` false.
- Plans: RFC-0005 §13.2: "Fairness key = employee id, with weight 1"; `egress.send` uses the channel account
  (RFC-0005 §17.2); "The dev server gets `--dynamic-config-value matching.enableFairness=true`" (RFC-0005 §13.2
  and §20).

**Verdict:** Planned in RFC-0005 (follows the docs, misses details the docs call out): partitions, duration
blindness, mixed key families on one queue, and the flags are not in the dev server's argv yet.

**Recommendation:**
1. **Dev-server flags, every boot.** Add `--dynamic-config-value matching.enableFairness=true`,
   `--dynamic-config-value matching.numTaskqueueReadPartitions=1` and
   `--dynamic-config-value matching.numTaskqueueWritePartitions=1` to `_runtime.py`'s argv, and the same keys to tier
   L's dynamic-config file. Lower the read count only on a database that is created with it: lowering read
   partitions on a database with backlog strands the tasks in the removed partitions (general Temporal partition
   guidance, unverified here). The first pinned release starts every install on an empty Temporal database (T5), so
   all three ship in that release (T17). Namespace retention and search attributes at namespace creation are not
   part of it: they stay deferred to RFC-0005 Phases 5 and 9a, as decided on 2026-10-23 (RFC-0004 D4; §17.2).
   *Versioning:* none (server configuration). *When:* RFC-0005 Phase 1, the fresh-start release (§22.1). *Effort:* S.
   *Risk:* one partition caps a queue's throughput; the ceiling is unverified, against RFC-0005 §20's estimate of
   8-10 actions/s at a 20-employee peak and about 50 at tier S; EG14's load test confirms it.
2. **Keys.** `fairness_key` = the employee's immutable workflow id, never the slug (a short decimal id, or 32 hex
   characters on older rows: both under 64 bytes); weight 1; no overrides. Set at supervisor and entity starts with
   the Priority key (§13.3 rec. 2), inherited by turns, children and activities. *Versioning:* none (new types).
   *When:* Phase 4. *Effort:* S.
3. **Sends on their own queue (default).** `egress.send` runs on a new `egress` queue instead of `messaging`, so
   each queue has one key family: accounts on `egress`, employees everywhere else. Round-robin between an account
   key and an employee key means nothing, and a per-key rate limit would hit both. *Versioning:* none (new activity).
   *When:* Phase 7. *Effort:* S.
4. **State the limits in RFC-0005 §13.2:** fairness is duration-blind, and LLM step attempts last from seconds to
   10 minutes (30 for local and OpenAI-compatible endpoints, 1b.25), so admission's per-employee cap (4, RFC-0005
   §13.3) is what bounds an employee's share of long work. After the fresh-start release every install runs server
   1.32 (T5), so the sentence that gates priority and fairness on the server version goes. *When:* this RFC.
   *Effort:* S.

**Remarks:**
- Fairness adds value where admission does not govern: workflow tasks on `machina-tasks`, tool queues, and LLM steps
  from a team's fan-out. Admission already rotates employees within a class (RFC-0005 §13.3).
- Tier S (up to about 100 employees) keeps fairness state for every key across restarts; tier L (up to about 1,000)
  loses it for most keys at each weekly upgrade (E13). Acceptable. Leave `matching.fairnessPassDither` off: it
  trades FIFO order among equal-weight keys for faster recovery.
- Turning fairness on with a backlog drains the old backlog first in its original order; harmless.
- An owner with one busy employee and several quiet ones barely notices fairness: it acts only when several keys
  have backlog at the same time.
- A start site missed by §13.3's test also misses its fairness key, and all such tasks compete as one tenant.

## 14. Performance and latency

### 14.1 Performance & Latency Patterns (category overview)

**Docs:** https://docs.temporal.io/design-patterns/performance-latency-patterns. Three latency sources and their
patterns: the first workflow task's trip through Matching ("~30-50 ms", Eager Workflow Start, §14.4), each
activity's scheduling round trip ("~50 ms per Activity", Local Activities, §14.2), and a client that waits for the
whole workflow (Early Return; with local activities, §14.3). A workflow task is one batch of workflow-code execution
that a worker takes from the queue; its result is the workflow's next commands. The benchmarks are Temporal Cloud
figures. "You want to start simple: begin with Local Activities."

**SDK:** see §14.2 to §14.4.

**OpenCompany today:** the 0.3.1 replay corpus (`server/tests/temporal/fixtures/0.3.1/`) records real event
timestamps. The recorder used a SQLite database without the production WAL pragmas, workers with
`max_cached_workflows=0`, stub activities and server 1.31.2 (`server/tests/temporal/record_fixtures.py:1026-1046`;
`manifest.json`), so these are upper bounds on orchestration cost, not measurements of a live instance.

| Step on the owner-chat path today | Recorded |
|---|---|
| Controller: `on_event` Signal to the child MachinaWorkflow start (`controller_push.json`) | 232 ms |
| MachinaWorkflow start to AgentWorkflow start (`machina_chat_agent.json`) | 147 ms |
| Controlled generations add `execution_control.register` | 156 ms |
| AgentWorkflow: `agent.prepare_payload` | 62 ms |
| AgentWorkflow: progress broadcasts "starting" and "llm_step" | 205 ms |
| Total to the first `agent.execute_llm_step` scheduled (started) | 747 (778) ms; controlled generation 903 (934) ms |
| One regular activity, scheduled to the next workflow task started | median 73 ms (p10 30, p90 112, max 353) |
| First workflow task, scheduled to started | 11.5-32 ms |

- So 0.75-0.93 s passes in orchestration before the provider request. Subtract about 85 ms when the trigger has no
  saved filter parameters (no filter activity). The table excludes the chat handler, which also awaits a
  Visibility query and a Signal before it answers (`server/services/chat/handlers.py:333-342`;
  `server/services/events/dispatch.py:63-206`).
- About 0.32 s of it is cosmetic broadcasts: the controller's "idle" broadcast (111 ms) and the two progress
  broadcasts (205 ms).
- Each tool iteration adds three serial progress broadcasts ("executing_tool", "tool_completed", "llm_step";
  `server/services/temporal/agent_workflow.py:2236-2250`, `:2425-2430`, `:1167-1173`), and a delegated child mirrors
  each phase onto its parent with one more (`:3250-3273`).
- Progress broadcasts are 35-52% of the events in the corpus's ten AgentWorkflow histories (19-39% in
  PiCodingAgentWorkflow's); one tool iteration costs 18 events.
- Each event queued behind another in an old generation's controller holds its loop about 0.52 s (§13.3).
- On the SQLite dev server a regular activity costs as much as the docs' Cloud figure; loopback does not make round
  trips free.
- RFC-0005 §11's 0.4 s owner-chat debounce, longer than any single step above, is gone by T9 (§7.4). The docs advise
  against such short timers anyway: "your Workflows should not rely on sub-second accuracy for Timers"
  (https://docs.temporal.io/workflow-execution/timers-delays).
- SDK 1.33's fix for a small-cache stall does not apply: it needs a sticky cache smaller than the worker's workflow
  task slots (10), and OpenCompany's caches hold 50, 100 or 500 workflows.

**Verdict:** category overview; §14.2 to §14.4 hold the verdicts.

**Recommendation:**
1. **Measure with production settings before converting anything beyond §14.2 rec. 1.** Add a timing mode to
   `tests/temporal/record_fixtures.py` that uses the production sticky cache and the `--sqlite-pragma` values
   (`_runtime.py:159-164`) on the pinned server 1.32 (eager activity dispatch on), records `controller_push` and
   `machina_chat_agent`, prints per-hop latency, and never writes into `fixtures/`. *Versioning:* none (test tool).
   *When:* P2, independent of the phases. *Effort:* S. *Risk:* none.
2. **Read the dev server's metrics on its named port.** `--metrics-port 0` makes CLI v1.9.1 pick a random free
   port and serve Prometheus `/metrics` on it (on the frontend address, loopback by default), and nothing reads it;
   the comment at `_runtime.py:124` says it disables the endpoint. Phase 1b names the port in `.env.template` and
   corrects that comment (§17.8, 1b.33). The timing run (rec. 1), the upkeep checks (§13.3 rec. 4) and the health
   view then read task-queue backlog and schedule-to-start latency from it. *Versioning:* none. *When:* the
   port in Phase 1b (1b.33); its readers with rec. 1 and §13.3 rec. 4. *Effort:* S. *Risk:* a port clash at boot;
   the CLI checks the port is free and fails clearly.
3. **Old controller: stop paying round trips for cosmetic work.** Behind
   `workflow.patched("controller-spawn-local-v1")`, run the trigger filter and both status broadcasts of the spawn
   routine (`trigger_listener_workflow.py:176-206`, `:424-434`, `:503-510`) as local activities (§14.2), non-fatal
   as today. Of the 0.52 s each queued event holds the loop, the two broadcasts account for about 0.3 s (est. from
   `controller_push.json`). *Versioning:* a patch (the controller is a shared type that old generations keep until
   Phase 10). *When:* RFC-0005's controller-hardening lane, serialized with the other controller edits. *Effort:* S.
   *Risk:* replay of old histories; record a fixture with the patch on.

**Remarks:** Early Return is not the lever for chat: the WebSocket answer already returns after the database write
and the Signal, and the first token needs the provider. Eager Workflow Start does not fit any hot path (§14.4).

### 14.2 Local Activities

**Docs:** https://docs.temporal.io/design-patterns/local-activities. A local activity runs inside the worker that is
running the workflow task; its result is recorded with that task's completion as a marker event, with "no additional
server calls". The rules that matter:
- Set `schedule_to_close_timeout`. Local activities are bounded by the workflow task timeout ("default 10 seconds");
  aim for "well under 5 seconds".
- At-least-once: a failed workflow task re-runs every local activity of that task ("if a Worker crashes after the
  third of five sequential Local Activities, all five re-execute").
- No heartbeats. Long retry backoff "still cause[s] the SDK to schedule server-side timer events".
- They "bypass the task queue entirely and cannot be throttled by the server."
- Signals and Updates wait while a local activity runs. Past about 80% of the workflow task timeout the SDK
  heartbeats the workflow task, which adds events.
- "If you haven't measured a latency problem, start with regular Activities."

**SDK:** `workflow.execute_local_activity` and `start_local_activity` in 1.30.0
(`temporalio/workflow/_activities.py:1148-1344`) accept an activity name string, `schedule_to_close_timeout`,
`start_to_close_timeout`, `retry_policy`, `local_retry_threshold`, `cancellation_type`, `activity_id` and `summary`;
no `task_queue` or `priority`. The framework worker's local-activity slots default to 100
(`temporalio/worker/_tuning.py:352-363`). No workflow sets `task_timeout`, so the 10 s default applies. Same API in
1.34.0, whose bundled Core changelog lists three local-activity fixes: results keep their activation grouping on
replay, "preventing workflows that wait for the first completion from receiving a result on the wrong activity
handle"; results are delivered as each completes; and workers with `max_cached_workflows=0` no longer stall when one
resolves while an earlier one is still being delivered (`temporalio-1.34.0 .../bridge/sdk-core/CHANGELOG.md:43-45`,
`:354-357`, `:362-363`). The 1.30.0 wheel ships no Core source to compare. Adopt on 1.34.0 (Phase 1's last step)
and await local activities one at a time.

**OpenCompany today:**
- No local activity anywhere. RFC-0004 §15 rejected them ("Small gains; whole-task re-runs; signals wait") until
  "Measured latency on bookkeeping steps"; §14.1 now measures them.
- Candidates (short, idempotent, no heartbeat):
  - a. `agent.broadcast_progress` through `AgentWorkflow._emit_phase` (`agent_workflow.py:3216-3273`; activity at
    `server/services/temporal/agent_activities.py:865-990`): it updates the in-memory node status and emits
    CloudEvents, and its tool-capability events carry deterministic ids, so a re-run duplicates nothing durable.
    It costs 205 ms before the first LLM step and three round trips per tool iteration.
  - b. `broadcast_trigger_status_activity` and c. `evaluate_trigger_filter_activity` in the old controller (§14.1
    rec. 3); the filter is pure and fails open.
  - d. `store_node_output_activity` (`workflow.py:616-626`), an idempotent overwrite, and e. `chat_run.start`
    (`workflow.py:377-399`), a claim keyed by run id.
  - f. `workspace_tasks.admit_record` (§14.3).
  - g. RFC-0005's `admission.acquire` and `release`, on the C1 path before every turn.
- Today a failing broadcast fails the turn: `_emit_phase` awaits it with no handler, under
  `AGENT_ACTIVITY_RETRY` = `DEFAULT_ACTIVITY_RETRY` (3 attempts; `agent_workflow.py:184`; `_retry_policies.py:63-68`).
- Poor fits: `agent.prepare_payload` (its result carries the transcript, up to about 1 MB, T6), `execute_llm_step`,
  tools and sends (E12, T13), and anything that needs a queue cap, a priority or heartbeats.

**Verdict:** Missing and beneficial: progress broadcasts now (measured), the rest only after measurement.

**Recommendation:**
1. **AgentWorkflow progress broadcasts as best-effort local activities.** `_emit_phase` runs
   `agent.broadcast_progress` as a local activity with `schedule_to_close_timeout` 5 s, a short retry policy and
   `local_retry_threshold` 1 s, and logs and continues on any failure except cancellation: a cosmetic failure must
   never fail a turn (T4). Alternative: fold "starting" into `prepare_payload` and "llm_step" into
   `execute_llm_step`; local activities are chosen because one mechanism covers every phase. *Versioning:*
   per-generation input flag (`progress_local_version=1`, frozen into the resource manifest at Start; manual Runs
   capture it with the routing input); update `tests/temporal/fixtures/workflow_identities.json` deliberately and
   record a fixture scenario with the flag on. *When:* P2, after the SDK 1.34.0 upgrade (Phase 1's last step), so
   it can land before Phase 4 (§22.3). *Effort:* S. *Risk:* re-runs repeat a broadcast (harmless: node status is
   latest-state, capability events deduplicate by id); on hosted tier S the local activity runs in the worker
   process, so its frames reach sockets only through the relay (§15.2 rec. 3).
2. **The old controller's spawn loop:** filter and broadcasts as local activities behind a patch (§14.1 rec. 3).
3. **RFC-0005's new types:** they never schedule a UI-only activity (a design rule, Phase 4; RFC-0005 §10.1).
   Admission `acquire` and `release` start as regular activities; move them to local activities, keyed by turn id
   with `schedule_to_close_timeout` at most 5 s, only if the timing run shows the C1 path needs it and after N8's
   advisory-lock fix (Phase 9b), because a local activity waiting on a contended lock holds the workflow task.
   *Versioning:* none (new types). *When:* P3. *Effort:* M.
4. **MachinaWorkflow bookkeeping (d, e):** only if the timing run shows they matter; they cost once per run. P3.
5. **Never as local activities:** LLM steps, `prepare_payload`, tools and sends. A rule.

**Remarks:**
- Local activities skip queue caps, priority and fairness; fine for millisecond work.
- Each converted broadcast replaces six history events (three activity events and three workflow-task events) with
  one marker (est.). Fewer events mean fewer AgentWorkflow continue-as-new rollovers, which matters because
  transcripts stay in history (T6), and less replay after a cache miss.
- In tier S's worker process (T8) the workflow worker runs every local activity, so it needs the database and the
  container (§15.3 rec. 1).
- Record local-activity fixtures on 1.34.0: the recorder runs with `max_cached_workflows=0`, the case 1.34.0's Core
  fixes.

**Sketch:**

```python
# _retry_policies.py (every workflow retry policy lives in this module)
LOCAL_BOOKKEEPING_RETRY = RetryPolicy(
    initial_interval=timedelta(milliseconds=200), maximum_interval=timedelta(seconds=1),
    maximum_attempts=3, non_retryable_error_types=list(NON_RETRYABLE_ERROR_TYPES))

# AgentWorkflow: _emit_phase builds `progress` as today, then calls this.
async def _broadcast_progress(self, progress: Dict[str, Any]) -> None:
    if self._progress_local:      # context.get("progress_local_version") == 1, frozen at Start
        try:
            await workflow.execute_local_activity(
                "agent.broadcast_progress", args=[progress],
                schedule_to_close_timeout=timedelta(seconds=5),
                retry_policy=LOCAL_BOOKKEEPING_RETRY,
                local_retry_threshold=timedelta(seconds=1),
                summary=f"progress {progress.get('phase')}")
        except Exception as exc:  # noqa: BLE001 -- cosmetic; never fails a turn
            if is_cancelled_exception(exc):
                raise             # cancellation must propagate (RFC-0004 H8)
            workflow.logger.warning(f"progress broadcast skipped: {exc}")
        return
    await workflow.execute_activity(      # the recorded command shape of older generations
        "agent.broadcast_progress", args=[progress],
        start_to_close_timeout=PERSIST_TURN_TIMEOUT, retry_policy=AGENT_ACTIVITY_RETRY)
```

### 14.3 Early Return + Local Activities

**Docs:** https://docs.temporal.io/design-patterns/early-return-local-activities. The client sends one
Update-with-Start (a single call that starts the workflow and delivers an Update to it); the Update handler waits on
a Phase 1 flag; Phase 1 runs as local activities inside the first workflow task; Phase 2 runs as regular activities
after the client has its answer ("~160 ms" first response against "~265 ms" with regular Phase 1 activities). The
rules: Phase 1 is short and idempotent, because "a retried Workflow Task re-executes all Local Activities in that
task"; "Always check Phase 1 error state before proceeding to Phase 2" and compensate.

**SDK:** `Client.execute_update_with_start_workflow` with `WithStartWorkflowOperation` in 1.30.0 (in use at
`server/services/node_invocations.py:52-58`). `UpdateWithStartStartWorkflowInput` has no `request_eager_start` in
1.30.0 or 1.34.0 (`temporalio/client/_interceptor.py:340-344`; `_impl.py:213-216`), so in Python early return
cannot be combined with eager start. From 1.33, an Update handler may not continue-as-new.

**OpenCompany today:**
- Workspace tasks follow Early Return. `_controller_update` sends Update-with-Start to the
  WorkspaceTaskControllerWorkflow (`node_invocations.py:44-60`). Its `submit` handler checks admission, writes
  `workspace_tasks.admit_record` as a regular activity (new submissions, `history_version == 1`), starts the
  NodeInvocationWorkflow child and returns "accepted" while the child runs as Phase 2
  (`server/services/temporal/workspace_tasks_workflow.py:82-131`). A rejected submission fails before any Phase 2
  work starts, which is the page's error rule. Recorded: the Update completed 85 ms after the start, without the
  admit activity (`workspace_task.json`); the activity adds one regular round trip (median 73 ms).
- Chat: the WebSocket answer already returns after the database write and the Signal, and the first token needs the
  provider, so no Phase 1 could answer sooner. RFC-0005 answers after the database commit (RFC-0005 §8), earlier
  still.
- Approvals: under T10 the owner's decision is a database intent answered at once; a background deliverer sends the
  Update and the UI shows "applying".

**Verdict:** Adopted with deviations for Workspace tasks (Phase 1 uses a regular activity); Not applicable to chat
and approvals.

**Recommendation:**
1. **Workspace submit (P3).** If the timing run (§14.1 rec. 1) shows that submission latency matters, run
   `admit_record` as a local activity under a new `history_version == 2` (the payload already carries a version).
   *Versioning:* the payload version (recorded per submission). *When:* later. *Effort:* S. *Risk:* the Update
   handler holds the workflow task while it runs (milliseconds).
2. **No local activity in the ApprovalWorkflow's `decide` for click latency.** T10 already answers the click at
   once; a faster `decide` only shortens "applying". *When:* never for that reason.

**Remarks:** Signals and Updates wait while a local activity runs inside an Update handler. A Phase 1 failure must
reject the Update before any Phase 2 command is issued.

### 14.4 Eager Workflow Start

**Docs:** https://docs.temporal.io/design-patterns/eager-workflow-start. With `request_eager_start=True`, a starter
that shares its `Client` with a worker in the same process gets the first workflow task back inside the start
response, skipping Matching ("~30-50 ms"). The rules: start the worker before the call; "Do not rely on eager
dispatch always firing" (it falls back silently when no slot is free); combine with local activities; mind shared
CPU and failure domains. On by default from server 1.29 (`system.enableEagerWorkflowStart`). The worker-performance
page labels it "Public Preview", warns that "Eager start does not respect Worker versioning", and retries a failed
eager task "non-eagerly after WorkflowTaskTimeout" (https://docs.temporal.io/develop/worker-performance).

**SDK:** `request_eager_start` on `start_workflow` and `execute_workflow` in 1.30.0
(`temporalio/client/_client.py:419` onward; sent as `request_eager_execution`, `client/_impl.py:216`). Not available
for Signal-With-Start or Update-with-Start in 1.30.0 or 1.34.0.

**OpenCompany today:**
- Hot paths start workflows as children (controller to MachinaWorkflow to AgentWorkflow), by Signal (chat today), by
  Update-with-Start (Workspace tasks) or, in RFC-0005, by Signal-With-Start from the relay (RFC-0005 §9.4). None
  can be eager.
- Plain client starts that share the worker's `Client` in one process: manual canvas Runs (`executor.py:181-194`),
  the controller (`deployment/handlers.py:640-660`), EmployeeJobWorkflow (`employees/jobs.py:240`),
  PiCodingAgentWorkflow (`services/workflow.py:367`), ApprovedToolCallWorkflow (`approvals/execution.py:65`). None
  is latency-critical; the recorded first-workflow-task dispatch is 11.5-32 ms.
- Hosted tier S's second process (T8) separates the API from the workflow worker, so eager starts would never fire
  there.
- RFC-0005 §10.4 keeps eager start unused (RFC-0004 Q12); RFC-0004 §16 Phase 3 listed "an eager-start
  experiment for chat runs" until §21 dropped it.

**Verdict:** Not applicable: no hot path uses a plain client start inside the worker's process.

**Recommendation:**
1. **Do not adopt, and drop RFC-0004's chat-run experiment** (§21). Reopen only if a measured hot path uses a plain
   client start in the worker's process. *When:* never, unless that changes.
2. **Keep eager activity dispatch,** a different feature that already matters: the pinned server 1.32 enables it, so
   bookkeeping activities on `machina-tasks` can skip Matching, up to three reservations per workflow task (the 1.34.0
   default of `max_eager_activity_reservations_per_workflow_task`). Do not set a queue cap on `machina-tasks` (§13.2
   rec. 4). *Versioning:* none. *Effort:* none.

**Remarks:** in tier S's split, the worker process runs both the workflows and their `machina-tasks` activities, so
eager activity dispatch still applies there.

## 15. Worker configuration

### 15.1 Worker Configuration Patterns (category overview)

**Docs:** https://docs.temporal.io/design-patterns/worker-configuration-patterns. The category covers "how you set
up Workers, route work to them, and give Activities the external dependencies they need — while keeping Workflow
code deterministic and testable." Two rules: "A sequence of Activities must run on the same Worker host: use
Worker-Specific Task Queues" (§15.2); "Activities depend on external resources: use Activity Dependency Injection to
supply them at startup rather than constructing them inside each Activity" (§15.3).

**SDK:** see §15.2 and §15.3.

**OpenCompany today:**
- Three kinds of routing:
  - capability queues: nine plugin queues (`server/services/plugin/scaling.py:17-45`), one activity-only Worker per
    queue (`server/services/temporal/worker.py:393-673`), and the Pi driver queue `coding-pi-driver`
    (`server/services/temporal/pi_protocol.py:15`; `worker.py:708-749`);
  - affinity queues: browser owner queues in distributed mode (`server/services/browser_owners.py:46-47`;
    `worker.py:686-705`);
  - implicit affinity: everything else relies on the backend being the only process that polls the queues where
    process-bound activities land (§15.2).
- Seven places build a Worker, each with its own list: the embedded manager (`worker.py:183-316`), the pool
  (`:543-611`), the owner pool (`:693-705`), the Pi driver pool (`:737-749`), the standalone worker (`:758-928`),
  `create_worker` for tests (`:931-1015`), and the fixture recorder (`server/tests/temporal/record_fixtures.py:1040-1061`).
- Framework worker options: 100 activity slots, 10 workflow-task slots, autoscaling pollers (1-10 activity, 1-20
  workflow), sticky cache 50 (local), 100 (Docker, `self_hosted`) or 500 (VMs, `cloud`) (`worker.py:76-88`,
  `:289-307`), graceful shutdown 10 s on desktop and 30 s elsewhere. The sticky cache keeps recently run workflows in
  memory so their next workflow task skips a history replay. The standalone worker's main Worker sets no identity
  (SDK default pid@host), no cache size (SDK default 1,000) and default pollers, and hard-codes namespace `default`
  and queue `machina-tasks` (`worker.py:758-763`, `:884-915`).
- Worker identity is `opencompany-<queue>-<mode>` (`worker.py:63-73`): unique within one process, but repeated
  across backend replicas and by the Pi driver pool in every process.
- `"machina-tasks"` is hard-coded where cron Schedules and legacy listeners start
  (`server/services/temporal/schedules.py:149`, not overridden at `server/services/deployment/manager.py:1128-1138`;
  `manager.py:988`), while the manager polls `settings.temporal_task_queue` (`lifecycle.py:203-210`). A non-default
  `TEMPORAL_TASK_QUEUE` strands both.

**Verdict:** Adopted with deviations. The capability half is in place; placement (which process may run an
activity) and "dependencies ready before polling" are implicit in the one embedded process, and every Worker
construction keeps its own registration.

**Recommendation:**
1. **A placement axis and one worker builder** (§15.2 rec. 3, Phase 4; §15.3 rec. 1, Phase 1b, 1b.36), both before
   any second process (T8).
2. **Size the sticky cache from the resident entity count.** RFC-0005 adds a supervisor per employee (§8.4 replaces
   its 5-minute upkeep timer with one instance-wide sweep) and owner conversations that passivate only after 24 h,
   so 50 to 100 employees exceed the desktop (50) and Docker (100) caches, and a wake after eviction replays
   history. Measure cache misses (the SDK metrics of 1b.38, §17.10) and either raise `max_cached_workflows` from
   the resident count or accept the replay; RAM is the trade-off. *Versioning:* none. *When:* measured in Phase 4,
   confirmed by EG14; the tier defaults are written in RFC-0005 Phase 9a (§17.9 item 3). *Effort:* S.
3. **One queue-name source:** Schedules and legacy listeners take `settings.temporal_task_queue`. Unique worker
   identities (append host and process id) come with the worker builder (§15.3 rec. 1, 1b.36). *Versioning:* none
   (start options). *When:* P3. *Effort:* S.
4. **Stop workers with `Worker.shutdown()`** (RFC-0004 §11.5), which also drains affinity queues gracefully (§15.2):
   Phase 1b (1b.37, §17.9).

**Remarks:** moving off one process changes several patterns at once: where process-local dependencies exist
(§15.2); per-worker caps multiply (§13.2); eager starts and eager activities need the starter and the worker in one
process (§14.4); local activities run in the workflow worker, which then needs the database and the container
(§14.2); a workflow-only process should set `no_remote_activities=True`; and RFC-0005's `ClassSlotPool` would hold
its floors per process (moot under §13.3 rec. 5). RFC-0005 said both "Each worker process runs one Worker per class
queue" (RFC-0005 §13.2) and tier L has "activity workers per class" (RFC-0005 §20); §20 of this RFC drops the first
with the slot pool.

### 15.2 Worker-Specific Task Queues

**Docs:** https://docs.temporal.io/design-patterns/worker-specific-taskqueue. When later activities depend on
local state (files, sessions, devices) created by an earlier one, route them to a queue that only one worker polls.
"The first Activity returns its host-specific Task Queue name, and subsequent Activities use that queue." The rules
that matter:
- Each worker polls the shared queue and its own unique queue (`f"FileProcessing-{socket.gethostname()}-{uuid.uuid4()}"`),
  with the queue name injected into its activities at startup.
- The schedule-to-start timeout "is critical"; it "is non-retryable: when it expires, the Activity fails rather than
  retrying, because a retry would only place the Task back on the same queue. The Workflow catches that failure
  and retries the entire sequence so the work can restart on a different host."
- "Task Queue routing is recorded in Workflow history, ensuring deterministic behavior." Trade-offs: a crashed
  worker blocks until the timeout; uneven load; cleanup on failure. Poor fit: "Activities that use shared storage"
  and "High-availability requirements".
- Best practices: no Worker Versioning on these queues ("A per-worker Task Queue's lifetime is tied to the Worker
  itself"); set the timeout; "Implement cleanup"; unique names; "Monitor queue depth"; "Drain gracefully"; retry the
  whole sequence; limit concurrent workflows per worker; "Add health checks".
- Pitfalls: a missing timeout; "Not registering the Worker on both queues"; assuming the pinned worker is always up;
  leaking temporary files; host queues "when shared storage suffices".

**SDK:** `workflow.execute_activity(task_queue=..., schedule_to_start_timeout=...)` in 1.30.0
(`temporalio/workflow/_activities.py:105-110`), unchanged in 1.34.0. Several Workers per process are supported.
`Worker(no_remote_activities=..., sticky_queue_schedule_to_start_timeout=timedelta(seconds=10))` and
`Worker.shutdown()` exist in 1.30.0 (`temporalio/worker/_worker.py:123-124`, `:886`). DescribeTaskQueue can report
pollers and backlog through the raw RPC (§13.2). 1.34.0 adds nothing for this pattern.

**OpenCompany today:**

*A. Browser owner queues (distributed mode).*
- The queue is `browser-owner-<sha256(owner_id)[:24]>` (`browser_owners.py:46-47`): stable per configured replica,
  not per process. Uniqueness is enforced harder than the docs ask: a database row per replica with machine id and
  runtime epoch, refusal of a second live process (`:94-107`), a process lock file (`:85-93`), a heartbeat every
  10 s (`:110-120`).
- Routing is frozen into run input and so recorded in history: browser bindings in the MachinaWorkflow context
  (`workflow.py:767-771`, `:788-792`), tool policies with `owner_routing` (`agent_activities.py:67-114`;
  `agent_workflow.py:145-156`), and direct Workspace runs bind before submit (`node_invocations.py:226-247`).
- Claim, tool steps and release all run on the owner queue; the release runs in `finally` under `asyncio.shield`
  with `BROWSER_TASK_CLEANUP_RETRY = RetryPolicy(maximum_attempts=0)` (`agent_workflow.py:794-858`;
  `_retry_policies.py:137-138`): the docs' "Implement cleanup".
- The backend polls both queues: the manager on `machina-tasks` and the owner pool on its owner queue, started even
  with the pool flag off (`lifecycle.py:211-220`; `worker.py:686-705`); generic workers drop browser in distributed
  mode (`worker.py:566-567`, `:676-683`). The "register on both queues" pitfall is avoided.
- Health check before routing: a binding is `available` when its owner's heartbeat is younger than 35 s
  (`browser_owners.py:60-68`), and the UI reports "Browser unavailable — waiting for its owner"
  (`docs-internal/browser_agent_deployment.md:181-184`).
- No schedule-to-start timeout anywhere, on purpose: an offline owner's queued activity "remains on that owner's
  queue without consuming retries or starting another model turn", and "there is no scheduler, automatic profile
  transfer or retry identity reset" (`browser_agent_deployment.md:181-197`).
- No queue-depth or poller monitoring. Workers stop by cancelling their task (`worker.py:373-381`, `:663-673`); the
  SDK encourages explicit shutdown (`temporalio/worker/_worker.py:754-757`).

*B. Implicit affinity to the backend process (all modes).*
- Per-type activities assume one process: "The Temporal worker shares the FastAPI process, so direct DI works"
  (`server/services/plugin/base.py:1103-1104`); the legacy activity calls back to `ws://HOST:PORT/ws/internal`
  (`server/services/temporal/activities.py:28-41`).
- Process-local resources those activities use: the Android relay client (`server/nodes/android/_relay/manager.py:16`),
  the Claude session pool (`server/nodes/agent/claude_code_agent/_pool.py:1270-1273`), the process manager
  (`server/services/process_service.py:597-600`), the phone runtime with its "process-wide lease for one writable
  AVD" (`server/nodes/mobile/_runtime.py:975-978`; `_emulator.py:1`, `:107`), the browser runtime
  (`server/nodes/browser/_runtime.py:606-609`), the WhatsApp RPC client, which starts edgymeow in the calling
  process (`server/nodes/whatsapp/_service.py:297`, `:314-334`), the socket broadcaster (`plugin/base.py:1118-1123`),
  the browser `RUNTIME_EPOCH` (`browser_owners.py:23`), and the Fernet key, initialized only by `server/main.py:227`.
- They run in the backend only because no other process polls their queues: with the pool on, plugin queues are
  polled by the backend's pool alone (`lifecycle.py:224-227`).
- What a second process does today. A non-distributed standalone worker registers no `node.*` activity, so WhatsApp
  and phone activities that reach it fail with a retryable `NotFoundError` instead of running, and the legacy
  `execute_node_activity` runs nodes back in the backend over `/ws/internal`. Any standalone worker runs local-mode
  browser claim and release in the wrong process (registered there, `worker.py:872-874`, `:898`; the binding falls
  back to `workflow.info().task_queue`, `agent_workflow.py:808`, `:849`), and node status and agent progress from its
  activities reach only its own process (`docs-internal/TEMPORAL_ARCHITECTURE.md:77-81`; the relay carries only
  `chat.updated` and `approval_lifecycle`, `server/services/chat/relay.py:53-56`). A second WhatsApp bridge or phone
  driver needs a distributed-mode standalone (which imports `nodes`) plus pool-off or legacy routing. It becomes the
  normal case as soon as a second process imports `nodes` and polls `messaging`: exactly T8's tier S split.
- RFC-0004 H7 still holds: only the flag starts the pool (`lifecycle.py:224-227`), so runs frozen with the pool on
  strand when the flag is turned off, while `.env.template:101-107` calls the flag a clean rollback.

*C. The Pi driver queue.* `coding-pi-driver` is shared (`pi_protocol.py:15`). Each attempt registers a new epoch
through an Update (`server/services/temporal/pi_activities.py:182-189`), uses its own temporary directory and keeps
durable content in the database (`:207-212`), with unlimited retries and a 90 s heartbeat
(`server/services/temporal/pi_workflow.py:182-187`). That is the docs' "shared storage suffices" case: a capability
queue, not an affinity queue. Gaps: the standalone worker always polls it (`worker.py:881-883`), and a worker without
bun fails the turn with the non-retryable `PiRuntimeMissing` (`pi_activities.py:203-206`) instead of leaving it to a
capable worker. Parent drivers park while their children run (`worker.py:708-714`), so a backlog there is normal.

**Verdict:** Adopted with deviations for browser owner queues (deliberate: no timeout and no failover, because a
profile never moves); Missing and beneficial for process affinity, which T8's split requires; Not applicable to the
Pi driver queue (affinity correctly avoided; a capability check is missing).

**Recommendation:**
1. **Refuse a local standalone worker.** `run_standalone_worker` refuses to start unless `DISTRIBUTED_MODE=true`
   until the worker builder (§15.3 rec. 1) and placement routing (rec. 3 below) exist; after that, it refuses unless
   the builder's registration check passes and every live generation carries `placement_version=1`. The reasons are
   not a second WhatsApp bridge (a non-distributed standalone registers no plugin activities). They are: its empty
   plugin registry makes workflow code schedule `execute_node_activity` where the embedded worker schedules
   `node.<type>.v<n>`, and a lazy `nodes.*` import inside an activity it runs (`browser_owners.py:260`,
   `agent_activities.py:2425`) flips the same process mid-run, so a workflow task that moves between workers fails
   replay; stored Fernet keys are unreadable (`core/encryption.py:146-147`); `CronTriggerWorkflow` and the `poll.*`
   activities are not registered; the container's Temporal client is never connected, so `emit` skips its fan-out;
   and local-mode browser claims run in the wrong process. Correct `docs-internal/TEMPORAL_ARCHITECTURE.md:70-81`
   ("for horizontal scaling"), `docs-internal/SCRIPTS.md:114` and `package.json:112` (`temporal:worker`).
   *Versioning:* none. *When:* Phase 1b (1b.35). *Effort:* S. *Risk:* anyone running `temporal:worker` beside a
   local backend gets a clear refusal instead of silent nondeterminism.
2. **Always poll the pool's queues.** `lifecycle._start_execution_engine` starts `TemporalWorkerPool` whatever
   `TEMPORAL_WORKER_POOL_ENABLED` says; the flag only selects the routing frozen into new runs
   (`workflow.py:1037-1040` already says so). Correct `.env.template:101-107`. *Versioning:* none. *When:* Phase 1b
   (1b.34). *Effort:* S. *Risk:* idle pollers on nine queues when the flag is off; the flag is on by default.
3. **Process-affinity routing first (T8).** A `placement: ClassVar[Literal["portable", "instance"]]` on plugin
   classes and framework activities, `instance` for WhatsApp, Android, mobile, `claude_code_agent`, the process
   manager and the local-mode browser runtime. An instance queue `instance-<instance_id>`, where `instance_id` is
   created once and kept in the app database so the name survives restarts; only the API process polls it,
   built like `BrowserOwnerWorkerPool`. New generations freeze placement in their input
   (`resource_manifest.placement_version=1`): tool policies record the instance queue in `_tool_activity_policy`
   (`agent_activities.py:67-90`), and graph nodes take it from the frozen dispatch map (§15.3 rec. 2). Old
   generations keep today's routing, so an instance turns the split on only when no live generation lacks the flag,
   the rule RFC-0005 §20 already uses for the S-to-L move. **Default (T8 read narrowly):** only resources that
   cannot be shared go to the instance queue, and T8's other two items are met by sharing rather than routing: the
   worker process relays its status frames to the API process over `/ws/internal` (extend `RELAYED_BROADCASTS` in
   `services/chat/relay.py`, keeping RFC-0005 §17.6's audience rules), and the worker builder initializes the
   encryption key in the worker process (§15.3 rec. 1). Routing every activity that broadcasts or reads a key to the
   API process would bring every plugin activity and every LLM step back into it and undo the split. *Versioning:*
   per-generation input flag (`placement_version=1`). *When:* P2, RFC-0005 Phase 4, so new-path generations carry
   it from their first release. The split itself ships per instance in RFC-0005 Phase 9, after durable delegations
   (§6.2, §22.4); the desktop stays one process (T8). *Effort:* M. *Risk:* an unpolled instance queue strands
   runs; the API process always polls it and its name never changes.
4. **Watch owner queues instead of timing them out (P3).** An upkeep check (DescribeTaskQueue pollers and backlog,
   plus the database heartbeat) raises an attention item, "browser owner offline, N tasks waiting" (RFC-0005 §18.3).
   Default for how long browser work waits for an offline owner: it keeps waiting on the owner queue with no
   schedule-to-start timeout, and the owner is told. Today a chat run still ends at 24 h (1b.14, §6.4). Under
   RFC-0005 Phase 5 (`scheduler_version=1`) the wait is a park, with the 7-day limit (T11, T19), and it never counts
   toward the 24-hour no-progress backstop (a default, §24.1; §6.4). Keep `browser-owner-*` and `instance-*` queues
   out of Worker Versioning when it is adopted, and stop workers with `Worker.shutdown()` (1b.37, §17.9).
   *Versioning:* none. *Effort:* S.
5. **Pi driver capability check (P3).** Start `PiDriverWorkerPool` only after `require_bun` and `resolve_pi_host`
   succeed; keep schedule-to-start off this queue and watch its latency instead (§21 corrects RFC-0004 M8).
   *Versioning:* none. *Effort:* S.
6. **Retire frozen queue names like patch markers.** Pool routing (`workflow.py:759-761`), owner bindings, direct
   runs (`node_invocations.py:207`), RFC-0005's class queues and the instance queue all freeze queue names in run
   input. A queue stays polled while any open run chain can schedule onto it, and is retired one release after none
   can (RFC-0005 §24.3). The golden identity snapshot records only `id` and `activity_id`
   (`server/tests/temporal/workflow_identities.py:39`); add `task_queue` and `priority` so a routing change shows in
   review. *Versioning:* none (test). *When:* Phase 1b (1b.42) for the snapshot; the rule from now on. *Effort:* S.

**Remarks:**
- Following the docs literally on owner queues (a timeout, then retry elsewhere) breaks the product rule that a
  profile never moves. A timeout loop that reschedules on the same queue would add about three events per loop
  (scheduled, timed out, timer) to the AgentWorkflow history and wake it without a model turn; it buys visibility
  only, which rec. 4 gives without touching history.
- If a schedule-to-start timeout is ever set, the failure is a `TimeoutError` of type `SCHEDULE_TO_START` that the
  retry policy never retries; the workflow must catch it.
- Sticky execution is Temporal's own worker-specific queue for workflow tasks: when a worker dies, a cached
  workflow waits `sticky_queue_schedule_to_start_timeout` (10 s) before another worker takes it. This is the delay a
  restart of tier S's worker process adds.
- Activities inherit Priority and fairness from their workflow (`temporalio/common.py:1154-1158`), so class order
  reaches owner and instance queues without extra keys. Their slots carry no class floor; the owner pool has four
  fixed slots (`worker.py:701`).
- Browser release retries forever on the owner queue, and Reset waits for it, as documented. RFC-0005's supervisor
  Stop barrier should complete while such a cleanup waits for an offline owner, as T12 lets Stop complete while an
  approval draft waits (default); the cleanup continues and is reported.
- If RFC-0005 Phase 3's gRPC bridge protocol (RFC-0005 §7.3) lets any process on the host send through the bridge,
  WhatsApp needs host affinity only and can leave the instance queue.
- The Python sample (samples-python `worker_specific_task_queues` at 33f7dc29) sets no schedule-to-start timeout,
  unlike the page.
- The fixture recorder registers fakes on every activity queue and the Pi driver queue
  (`record_fixtures.py:1040-1056`); no fixture covers an owner queue.

**Sketch:**

```python
# lifecycle._start_execution_engine (rec. 2): frozen runs keep their queues polled.
pool = TemporalWorkerPool(client=client)   # always; TEMPORAL_WORKER_POOL_ENABLED only picks
await pool.start()                         # the routing frozen into new runs

# services/plugin/base.py (rec. 3)
class BaseNode:
    placement: ClassVar[Literal["portable", "instance"]] = "portable"
    # "instance": whatsapp, android, mobile, claude_code_agent, process_manager, local browser

# agent_activities._tool_activity_policy: recorded in history, for new generations only.
if context.get("placement_version") == 1 and getattr(cls, "placement", "portable") == "instance":
    policy.update(task_queue=context["instance_queue"], instance_routing=True)
# agent_workflow._frozen_tool_activity_options then routes on
#   policy.get("owner_routing") is True or policy.get("instance_routing") is True

# worker.py: only the API process builds this pool, whatever the other flags say.
class InstanceWorkerPool(TemporalWorkerPool):
    def __init__(self, client: Client, instance_id: str):
        super().__init__(client, queues=[f"instance-{instance_id}"], default_pool_size=8)

    def _build_queue_worker(self, queue: str) -> Worker:
        return Worker(self.client, task_queue=queue,
                      activities=build_registration(self.client).activities_for(placement="instance"),
                      max_concurrent_activities=self._concurrency_for(queue),
                      graceful_shutdown_timeout=_graceful_shutdown_timeout(),
                      identity=_worker_identity(queue), interceptors=[ObservabilityWorkerInterceptor()])
```

### 15.3 Activity Dependency Injection

**Docs:** https://docs.temporal.io/design-patterns/activity-dependency-injection. Create external dependencies
(connection pools, API clients, configuration) once at worker startup and inject them into activities, instead of
building them inside each activity, so resources are shared, workflows stay deterministic and tests can substitute
doubles. The rules that matter:
- Python: a `@dataclass` with `@activity.defn` methods; register bound methods (`payment_activities.charge_customer`);
  workflows call `workflow.execute_activity_method(PaymentActivities.charge_customer, ...)`.
- A circuit breaker is a stateful dependency (Python `pybreaker`, `call_async`) constructed once, so every execution
  feeds one failure window; it "pairs well with a retry policy that backs off".
- Best practices: thread-safe dependencies; protocols for swapping; "Do not inject dependencies into Workflows ... If
  a Workflow needs configuration, retrieve it through a Local Activity so the value gets recorded in the Event
  History"; "Initialize dependencies before Worker startup ... the Worker does not start accepting tasks until all
  dependencies are ready"; group activities that share dependencies.
- Pitfalls: constructing dependencies inside activity methods; injecting them into workflows; non-thread-safe shared
  objects; registering an unbound method in Python (`BotService.send_message` instead of `bot_service.send_message`).

**SDK:** bound-method registration and `execute_activity_method` in 1.30.0 and 1.34.0. `activity.client()` returns
the worker's client inside async activities (1.30.0 `temporalio/activity.py:270`); `ActivityEnvironment(client=...)`
supplies one in tests. No change in 1.34.0.

**OpenCompany today:**
- Constructor injection, as the docs show: `NodeExecutionActivities(session)` (`activities.py:44-66`),
  `ExecutionControlActivities(client)` (`server/services/temporal/execution_control_activities.py:13-31`),
  `PiControlActivities(client)` (`pi_activities.py:60-67`) and `PiDriverActivities(client)` (`pi_activities.py:160-165`),
  all registered as bound methods (`worker.py:265-308`). The unbound-method pitfall is avoided.
- A service locator for the rest: a `dependency-injector` container of singletons (`server/core/container.py:121-190`),
  read at call time through `services/plugin/deps.py` (deliberately "NOT memoised" so tests can override) or
  `container.*()` directly. In `agent_activities.py`: `database` 8 call sites, `ai_service` 2, `auth_service` 2,
  `workflow_service` 2, `chat_unifier` 1, plus the module-level `get_status_broadcaster()` 3. `activity.client()` is
  used nowhere.
- Readiness before polling. Embedded: the lifespan starts the database, cache, credentials and encryption before it
  schedules the Temporal lifecycle (`server/main.py:204-229`, `:393-396`); the CLI MCP server is entered afterwards
  (`main.py:404-412`), a race the lifecycle's startup time usually hides. Standalone: model registry, database and
  compaction only (`worker.py:791-837`). Only `main.py:227` initializes encryption, so in a non-distributed
  standalone worker a stored-key read raises "Encryption service not initialized" at decrypt
  (`server/core/encryption.py:146-147`), `CredentialsDatabase.get_api_key` catches only `ValueError`
  (`server/core/credentials_database.py:292-296`), and `AuthService.get_api_key` logs and returns `None`
  (`server/services/auth.py:307-309`): the key looks missing. Distributed mode uses 1Password and skips Fernet
  (`auth.py:115-141`).
- The client. The standalone worker connects its own client (`worker.py:806-821`) and never attaches it to the
  container's `TemporalClientWrapper` (`core/container.py:147`), whose `.client` has no lazy connect
  (`server/services/temporal/client.py:57-59`). `dispatch.emit` then skips its fan-out with a DEBUG line
  (`server/services/events/dispatch.py:124-128`). That path runs inside an activity for delegated-task completion
  (`agent_activities.py:2424-2445` to `server/nodes/agent/_events.py:85-105`), so taskTrigger automations would not
  fire when that activity runs there, and employee-job dispatch from a tool refuses with `team_runtime_not_ready`
  (`server/services/employees/jobs.py:208-213`; `server/services/employees/team_runtime.py:155-172`).
- Registration drift. Three hand-written activity lists (`worker.py:265-308`, `:884-913`, `:986-1015`) and the
  recorder's. Only the manager passes `plugins=temporal_plugins()`, the sole registration of `CronTriggerWorkflow`
  (`server/nodes/scheduler/cron_scheduler/__init__.py:43`), and the `poll.*` activities (`worker.py:233`, `:286`),
  which the controller and the polling listener schedule on their own queue
  (`workflow_control_workflow.py:834-838`, `:856-860`; `server/services/temporal/polling_trigger_workflow.py:186-192`).
  An unregistered activity fails with a retryable `NotFoundError` and burns an attempt
  (`temporalio/worker/_activity.py:540-546`); an unregistered workflow type fails the workflow task
  (`temporalio/worker/_workflow.py:727-733`). The past incident: `agent.cancel_delegation` was scheduled but not
  registered and burned 10 attempts per cancelled delegation (`agent_activities.py:2558-2563`). Existing guards keep
  the workflow lists single-sourced (`server/tests/temporal/test_agent_workflow.py:1142-1155`;
  `test_workflow_identity_golden.py:25-33`) and check the `agent.*` names (`test_agent_workflow.py:1157-1173`).
  RFC-0004's D15, §11.2 and AG14 plan the fix; RFC-0005 Phase 0 did not include it.
- Workflow code reads process state, which the docs forbid. MachinaWorkflow reads the plugin class for activity name,
  version, queue, retry policy and the workspace flag (`workflow.py:788-792`, `:838-844`, `:856`, `:1053-1079`);
  AgentWorkflow's legacy tool path does too (`agent_workflow.py:114-142`), and `_default_max_iterations()` falls back
  to `Settings()` (`:163-175`). New generations already record these through `prepare_agent_payload`
  (`agent_activities.py:1591`, `:1966`; `agent_workflow.py:145-156`): the docs' approach, with a regular activity.
- Circuit breakers: none for model providers (grep of `services/llm`, `services/gateway`). The only breaker pauses a
  deployment after failed runs, in the database (`server/services/deployment/handlers.py:2722-2780`).
- Dead code: the WebSocket pool in `server/services/temporal/ws_client.py` is unused; only `internal_ws_url` is
  imported (`server/services/chat/relay.py:392`).

**Verdict:** Adopted with deviations. The container gives "once per process" and "substitutable in tests"; it does
not give "ready before polling", nor a statement of which process provides what, and each worker builds its own
registration.

**Recommendation:**
1. **One worker builder.** `build_worker_runtime(client, role)` in a new `services/temporal/worker_runtime.py`, used
   by the embedded manager, the standalone worker, `create_worker` and the fixture recorder:
   - imports `nodes` in every role, so the plugin registry is never empty;
   - outside the embedded role, starts and checks the database, cache, credentials and encryption, model registry,
     compaction and proxy before any Worker polls;
   - attaches the connected client to the container's wrapper (activity code may also use `activity.client()`);
   - reads namespace and queue from settings, and gives every worker a unique identity;
   - returns one registration: workflows, plugins and activities per queue;
   - checks it: every literal activity type in `identity_snapshot()` is registered on the framework queue, every
     plugin's activity on its declared queue and on the framework queue, and `poll.*` and `CronTriggerWorkflow`
     wherever the framework workflows are.

   The check runs in CI first. At runtime the embedded (desktop) worker logs an ERROR and keeps running, and the
   standalone and tier S worker processes refuse to start (RFC-0004 AG14). *Versioning:* none (worker setup).
   *When:* Phase 1b (1b.36). *Effort:* M. *Risk:* a check that refused at desktop boot would strand the owner, hence
   the split by role.
2. **Freeze MachinaWorkflow's dispatch.** New generations carry a per-node dispatch map (activity name, version,
   queue, retry policy, placement) computed at Start or by an activity, never by workflow code, so workflow code
   stops reading the plugin registry. Old generations keep the registry read until Reset. *Versioning:*
   per-generation input flag (`placement_version=1`). *When:* RFC-0005 Phase 4 (turn mode), which is RFC-0004
   Phase 2.1's freeze. *Effort:* M. *Risk:* a map that disagrees with the registry for new runs; a test compares
   them at Start.
3. **Clean up (P3).** Delete the unused `ws_client.py` pool (RFC-0004 L1); move module singletons (the broadcaster,
   the Claude pool, the process service) behind container providers so tests use `override` instead of
   monkeypatching. Add no per-process provider breaker: the database-backed buckets (§13.2 rec. 2) and T11's
   parking, under RFC-0005 Phase 5 (T18), cover provider outages across processes. *Versioning:* none. *Effort:* S.

**Remarks:**
- "Keep dependencies thread-safe" holds because every activity is async on one event loop and no worker sets an
  `activity_executor` (RFC-0004 M1); the builder must keep it so.
- Rec. 1 is the prerequisite for lifting §15.2 rec. 1's refusal, for T8's split and for RFC-0005 tier L.
- Do not move dependencies into workflows while doing rec. 2: the docs' rule is to read configuration through an
  activity so it is recorded in history.
- A breaker in the worker process, as the page shows, guards one process; with two processes (T8) or more (tier L)
  only the database can hold one failure window.

**Sketch:**

```python
# services/temporal/worker_runtime.py (new): every Worker in every process is built from here.
@dataclass(frozen=True)
class WorkerRuntime:
    client: Client
    registration: Registration            # workflows, plugins and activities per queue

async def build_worker_runtime(client: Client, *, role: str) -> WorkerRuntime:
    """role: "embedded" | "standalone" | "test" | "recorder". Nothing polls before this returns."""
    import nodes  # noqa: F401 -- the plugin registry in every role: no execute_node_activity fallback
    from core.container import container
    from services.model_registry import get_model_registry

    if role != "embedded":                # main.py's lifespan already did these for the backend
        settings = container.settings()
        await container.database().startup()
        await container.cache().startup()
        salt = await container.credentials_database().initialize()
        encryption = container.encryption_service()
        if not encryption.is_initialized():
            encryption.initialize(settings.api_key_encryption_key, salt)
        get_model_registry().startup()
        container.compaction_service().set_ai_service(container.ai_service())
    container.temporal_client().attach(client)       # emit, jobs and team runtime use this client
    registration = build_registration(client)        # the one list
    problems = schedulable_problems(registration)    # identity_snapshot() names, plugin queues, poll.*, cron
    if problems and role != "embedded":              # standalone and tier S refuse; tests and the recorder fail
        raise RuntimeError("worker registration incomplete: " + "; ".join(problems))
    for problem in problems:                         # the desktop still boots and says why
        logger.error("Activity not registered on its queue", problem=problem)
    return WorkerRuntime(client=client, registration=registration)
```

## 16. AI agents

This section reviews Temporal's AI pages (the landing page, the cookbook and the featured article on agent memory and state) and the agent-related groups in temporalio/samples-python, against AgentWorkflow and the code around it. Owner decisions T4 (reliability wins), T6 (transcripts stay in history), T11 (stuck turns park), T13 (side effects are redone only when idempotent), T15 (outgoing content rules), T18 (the step budget and parking ship with RFC-0005 Phase 5, for new generations only) and T19 (a turn stays parked at most 7 days) shape the recommendations.

Two terms recur. An *attempt timeout* (`start_to_close_timeout`) ends one try of an activity. A *step budget* (`schedule_to_close_timeout`) ends all tries together; the docs call this Fixed Wall-Time Retries (§11.3).

**How an employee's agent runs today** (the ground truth the entries refer to):
- AgentWorkflow (`server/services/temporal/agent_workflow.py:729-731`) runs one firing of one agent: first the `agent.prepare_payload` activity (`:947-954`), then a loop of LLM steps (`:1159`).
- Each step is the activity `agent.execute_llm_step`: a 10-minute attempt, a 1-minute heartbeat timeout and `LLM_STEP_RETRY`, which retries without limit from 5 s to 5 min apart (`:91`, `:99`, `:3183-3188`; `server/services/temporal/_retry_policies.py:94-100`). Its input carries the whole transcript, every visible tool definition and a second copy of the system prompt (`agent_workflow.py:1179-1197`).
- Tool calls run in the order the model gave them, one at a time, each as the plugin's own activity `node.<type>.v<n>` (`:2363-2369`, `:102-142`). Only Task Manager preflights and delegations run concurrently, delegations in a window of 3 (`:1451-1452`, `:1977-1979`).
- After a tool turn the loop relieves transcript pressure (clears old tool results, summarizes, clips; `:2741-2980`) and continues as new when the server suggests it or past 10,000 events (`:2992-3069`).
- The step activity (`server/services/temporal/agent_activities.py:476-645`) turns SDK retries off (`:544-545`), heartbeats every 20 s while it waits (`:378-399`), polls the chat ledger so Stop ends a stream mid-sentence (`:419-444`), converts provider failures into `ApplicationError(type="LLMError.<category>")` with the provider's pacing as `next_retry_delay` (`:246-303`, `:558-574`), and saves the conversation as a best-effort side write (`:648-697`).
- Before any plugin activity runs, `BaseNode.as_activity` calls `approvals.tool_calls.check` (Ask first) and then `resend_refusal` (`server/services/plugin/base.py:1194`, `:1220`).

### 16.1 Durable AI and the AI Cookbook index

**Docs:** https://docs.temporal.io/ai and https://docs.temporal.io/ai/cookbook. The landing page names four kinds of AI system that run on Temporal and sends each to patterns. Its agent case is "Long-running, stateful agent loops that call LLMs and tools, wait on humans, and pick up exactly where they left off". The cookbook index lists 16 recipes, reviewed one by one below.
- Agents: the Approval and Entity Workflow patterns; long-running work: the Long-Running Activity pattern.
- Internal agent platforms (many agents on one runtime) "reuse Temporal's Worker and Task Queue primitives" instead of their own scheduler.
- Pipelines: Task Queue Priority and Fairness, to run urgent work first and "keep one tenant from starving the others".
- Rules the recipes repeat: every LLM call is an activity; client-library retries stay off because they "can interfere with correct and durable error handling and recovery"; the workflow keeps the conversation and passes it to each call.

**SDK:** `priority=Priority(priority_key=..., fairness_key=...)` and `summary=` on `workflow.execute_activity` are in 1.30.0 (`temporalio/workflow/_activities.py:100-101`, `:117-118`), unchanged in 1.34.0. Fairness also needs server 1.31 or later and `matching.enableFairness`; the fresh-start release (Phase 1) provides both, with one task-queue partition (T5, T17, §13.4).

**OpenCompany today:**
- The agent shape matches: AgentWorkflow is the loop, every LLM call and tool call is an activity, and owner approvals survive restarts (§16.10).
- No activity or child start sets `priority=` or `summary=` (searched `server/services` and `server/nodes`).
- LLM steps run on `machina-tasks`, the queue the workflow tasks use (`agent_workflow.py:3185-3188` passes no task queue).
- Provider keys resolve inside leaf activities and never enter history (`agent_activities.py:176-243`). Secrets saved in node parameters do enter it, through the `parameter_snapshot` every tool input of an admitted generation carries (§17.8); 1b.31 and 1b.32 keep them out.
- OpenCompany is also an internal agent platform: many employees share one instance. RFC-0005 §13 plans class queues, Priority keys and a fairness key per employee, plus a database admission layer for per-employee floors and budgets, which Temporal does not provide.

**Verdict:** Adopted with deviations. The agent shape is right; the platform parts the page asks for (priority, fairness, labels in the Web UI) are planned, not built.

**Recommendation:**
1. Priority and fairness keys and class queues as RFC-0005 §13 designs them, with the refinements in §13.3 and §13.4; that design follows this page. Versioning and timing as there. No change here.
2. Label every agent activity: `summary=f"LLM step {n}: {model}"` on `agent.execute_llm_step`, `summary=f"tool {name}"` on tool activities, `static_summary` on agent children. Model and tool names only, never user text, at most 200 bytes (RFC-0004 §7.3). Versioning: none; on replay the SDK's core compares only an activity's id and type (§5.2, rule 6), and the replay corpus confirms it. When: RFC-0005 Phase 8 (observability), or earlier with RFC-0004 D14. Effort S. Risk: none beyond a few dozen bytes per activity in history.

**Remarks:** The five patterns the page links have their own entries: Approval (§9.6), Long-Running Activity (§9.3), Entity Workflow (§8.2), Saga (§10.2) and Local Activities (§14.2). This section covers only their AI-specific use. RFC-0005's admission tables are an addition the docs do not ask for, justified by budgets and reserved owner slots.

### 16.2 Agentic loop with tool calling (Claude, OpenAI, and the tool-calling agent)

**Docs:** https://docs.temporal.io/ai/cookbook/agentic-loop-tool-call-claude-python, https://docs.temporal.io/ai/cookbook/agentic-loop-tool-call-openai-python and https://docs.temporal.io/ai/cookbook/tool-call-openai-python. The loop is a workflow. Each iteration calls one generic LLM activity with the messages so far, the system prompt and the tool definitions; tool calls run as activities; the loop ends when the model answers without calling a tool. The third recipe is the non-looping version: one LLM call, at most one tool, one call to format the answer.
- A response can hold text and several tool calls; the whole assistant message is appended before the tool results.
- Tools are one dynamic activity (`@activity.defn(dynamic=True)`), so "changing the tools requires NO changes to the agent implementation".
- Client retries off (`AsyncAnthropic(max_retries=0)`). 400, 401, 403, 404 and 422 become non-retryable `ApplicationError`s, because "Retrying them under the default policy would loop forever instead of surfacing the problem"; rate limits and 5xx propagate so Temporal retries them.
- The OpenAI recipe reads only `result.output[0]` and builds tool schemas with a private OpenAI helper; both are simplifications. The 30 s timeouts are sample values.

**SDK:** dynamic activities and `ApplicationError(non_retryable=...)`: 1.30.0, unchanged in 1.34.0.

**OpenCompany today:**
- The same loop. Tools are one registered activity per plugin type, scheduled by name (`agent_workflow.py:137-142`): the same loose coupling, plus per-plugin queues, timeouts and retry policies.
- Every output item is handled, and text beside tool calls streams as narration (`agent_activities.py:587-634`). Tool schemas come from each plugin's Pydantic models, compiled per provider (`server/services/llm/schema.py`).
- Provider failures are classified (`server/services/llm/protocol.py:888-954`); the retryable categories are RATE_LIMIT, TIMEOUT, CONNECTION and SERVER (`:550-556`), and the rest fail at once.
- Gap 1: an exception that is not an `LLMError` (a bug after the provider answered, for example) falls through to `LLM_STEP_RETRY`, whose only non-retryable types are `NodeUserError` and `InvalidEvent` (`_retry_policies.py:52-55`). It retries forever and, with the owner's own key, pays for the call on every attempt. SDK 1.30.0 reports an activity result too large to record as a retryable `PayloadSizeError` (`temporalio/converter/_failure_converter.py:108-113`), which takes the same path.
- Gap 2: compaction (`agent.compact_context`) is another paid LLM call, but runs on the 3-attempt default policy (`agent_workflow.py:184`, `:2829-2835`). A rate-limit burst the step itself would wait out fails compaction, and a failed compaction fails the run (`:2836-2846`).
- A step that fails for good ends the run with a failure result (`:1258-1340`). The deployment pauses at once when the error requires the owner's action (`server/services/deployment/handlers.py:2757`), otherwise when the failure circuit breaker trips.

**Verdict:** Adopted with deviations. The loop, the classification and the client-retry rule match; two error paths retry without bound, the failure the recipe's classification exists to prevent.

**Recommendation:**
1. LLM-step bugs become non-retryable (input/output errors before the provider call stay retryable; they cost nothing), and `PayloadSizeError` joins both non-retryable lists: `NON_RETRYABLE_ERROR_TYPES` (`_retry_policies.py:52-55`) and the plugin `RetryPolicy`'s default (`server/services/plugin/scaling.py:59-71`), which tool and graph-node activities use. This is 1b.26, specified in §11.4. Legacy-path agent tools run with no retry policy, so they keep retrying until 1b.3 (§11.2) gives them the plugin's.
2. Compaction runs on the LLM step's retry policy, with a heartbeat: 1b.27, specified in §11.4. On that unlimited policy an empty summary, which `agent.compact_context` raises as a retryable `CompactionFailed` (`agent_activities.py:2757-2766`), must become non-retryable from the third attempt (`activity.info().attempt >= 3`), or it retries without end.

**Remarks:** Running tool calls one at a time matches the recipes; §16.6 covers running read-only calls together.

### 16.3 LLM call timeouts: Hello world with LiteLLM and the `litellm_activity` sample

**Docs:** https://docs.temporal.io/ai/cookbook/hello-world-litellm-python and the `litellm_activity` sample group. A reusable activity wraps `litellm.acompletion` with `num_retries=0`; authentication, bad-request, unsupported-parameter, schema-validation, content-policy and not-found errors are non-retryable. The sample adds a timeout ladder: provider client 30 s, activity attempt 45 s, `schedule_to_close_timeout` 2 min, at most 3 attempts (`samples/litellm_activity/activities.py:18-20`, `workflow.py:19-24`).
- Each rung ends before the one above it: client deadline < attempt timeout < step budget. A call the client ends becomes a classified, retryable error; a call the attempt timeout ends is an unclassified timeout.
- "Provider credentials stay in the Worker environment; they are not passed through the Workflow or stored in Event History." (`samples/litellm_activity/README.md:28`)

**SDK:** `schedule_to_close_timeout`, `ApplicationError(next_retry_delay=...)` and `activity.info().start_to_close_timeout` are in 1.30.0, unchanged in 1.34.0. `asyncio.timeout` needs Python 3.11; the server requires 3.11 or later (`server/pyproject.toml:5`).

**OpenCompany today:**
- The attempt timeout is 10 minutes for every provider, local models included (`LLM_STEP_TIMEOUT`, `agent_workflow.py:91`). No production activity sets a `schedule_to_close_timeout` (§11.3), although the owner chose a total budget per step (RFC-0004 §17 Q6).
- No provider client gets a request timeout (`server/services/llm/providers/openai.py:87-112`, `anthropic.py:31-43`, `gemini.py:37-66`). The OpenAI and Anthropic SDK default, `httpx.Timeout(600, connect=5)`, limits each read, not the whole call. Agent steps on OpenAI and Anthropic stream (only they set `streaming: true` in `server/config/llm_defaults.json`), so the 600 s bounds only silence between chunks, and a slow stream ends only at the 10-minute attempt timeout. OpenAI-compatible providers do not stream; their read timeout races the attempt timeout, which started first and wins. Either way no classified `TIMEOUT` reaches the UI.
- google-genai has no default timeout (`google/genai/_api_client.py:1068-1069`). A hung Gemini call keeps heartbeating every 20 s, so the heartbeat timeout never fires; each attempt ends at 10 minutes and is retried without limit. The same provider code serves graph-node calls (the chat-model and translate nodes), which run under MachinaWorkflow's 24-hour node attempt (`server/services/temporal/workflow.py:167`) with a periodic heartbeat, so a hung Gemini call there can hold its slot for a day.
- A local model that needs more than 10 minutes for one answer is cut and retried from the start without end: the "retry loop that never completes" the deep-research page warns about (§16.6).

**Verdict:** Adopted with deviations. Client retries are off and errors are classified, as the recipe asks; the ladder is missing at every rung.

**Recommendation:**
1. A deadline inside each attempt. In `_execute_native_llm_step`, wrap the provider call in `asyncio.timeout(...)` set to the attempt timeout minus 30 s (read from `activity.info().start_to_close_timeout`), and turn its `TimeoutError` into `LLMError(category=TIMEOUT, retryable=True)`. The attempt then ends as a classified, retryable timeout, which the UI shows as a retry wait. This bounds streamed and buffered calls alike, which no httpx setting does. Versioning: none (activity body). When: Phase 1b, as 1b.25. Effort S. Risk: a stream still flowing at the deadline is cut, as the attempt timeout cuts it today.
2. Give the Gemini client an explicit request timeout: `http_options["timeout"] = 600_000` (milliseconds; `HttpOptions.timeout`, `google/genai/types.py:2490`) in `GeminiProvider.__init__`, matching the other SDKs. This also bounds graph-node Gemini calls. Versioning: none. When: Phase 1b, with item 1 (1b.25). Effort S.
3. Longer attempts for local endpoints: 30 minutes for `ollama`, `lmstudio` and `openai_compatible:*`, 10 minutes for cloud providers (§11.3's defaults), chosen from the provider in `AgentWorkflow._execute_model_step` (`agent_workflow.py:3183-3188`). Versioning: none (timeouts are not compared on replay). When: Phase 1b, with item 1 (1b.25). Effort S. Risk: a hung local server holds its slot longer; item 1's deadline still ends each attempt.
4. The step budget (RFC-0004 Q6) ships with parking in RFC-0005 Phase 5, for new generations only (T18). Set `schedule_to_close_timeout` (15 minutes for cloud providers, 45 minutes for local and OpenAI-compatible endpoints; §11.3 owns the values) only for AgentWorkflow runs of generations with `scheduler_version=1`, where its expiry parks the turn (T11, §11.8, §16.16). Adding the budget alone would turn a stall that recovers by itself into a failed turn whose input is never offered again (`agent_workflow.py:1331-1340`), which T4 rules out. Existing generations keep today's unlimited LLM-step retries, covered by the retry alert (1b.30, §11.7), and today's fail-and-pause. Versioning: `scheduler_version=1`, frozen at Start. When: RFC-0005 Phase 5. Effort: part of §16.16.

**Remarks:**
- The budget counts wall time, including hours a desktop is closed or asleep; that is why its expiry must park, not fail.
- The per-class turn deadlines of RFC-0005 §13.1 become "slow turn" alerts that do not count parked time (T19), and a turn is cancelled only after 24 hours with no completed step, not counting time parked, paused or waiting for the owner (a default, §24.1, §6.4). No turn deadline therefore cuts a step that is still retrying inside its budget.
- Credentials follow the sample's rule: keys resolve inside the activity (`agent_activities.py:176-243`).

**Sketch** (item 1; activity body only):

```python
# server/services/temporal/agent_activities.py  (Phase 1b, 1b.25)
_DEADLINE_MARGIN = timedelta(seconds=30)

def _call_deadline_seconds() -> float:
    """End the provider call before the attempt times out, so it fails classified."""
    limit = activity.info().start_to_close_timeout or timedelta(minutes=10)
    return max(1.0, (limit - _DEADLINE_MARGIN).total_seconds())

async def _execute_native_llm_step(payload):
    ...
    try:
        async with asyncio.timeout(_call_deadline_seconds()):
            response = await _await_with_llm_heartbeats(_until_chat_run_stops(call, run_id), detail=detail)
    except TimeoutError:
        if emitter is not None:
            emitter.discard()                       # the client erases this attempt's text
        error = LLMError(message="provider call passed its deadline", provider=payload["provider"],
                         category=LLMErrorCategory.TIMEOUT, retryable=True)
        raise _as_temporal_llm_error(error, attempt=_llm_activity_attempt()) from None
    except LLMError as error:
        ...                                         # unchanged
    ...                                             # failures after the answer: 1b.26 (§11.4)
```

### 16.4 Hello world with the OpenAI Responses API

**Docs:** https://docs.temporal.io/ai/cookbook/hello-world-openai-responses-python. One generic LLM activity; the client is built with `max_retries=0`; the activity runs on the default retry policy ("unlimited attempts, no non-retryable errors"); `pydantic_data_converter` lets SDK response objects cross the activity boundary. The reason given for client retries off: they "can interfere with correct and durable error handling and recovery".

**SDK:** `temporalio.contrib.pydantic.pydantic_data_converter`: 1.30.0 and 1.34.0. OpenCompany does not need it.

**OpenCompany today:**
- Agent steps follow the recipe and return plain JSON message wires (`message_to_wire`), so no Pydantic converter is needed.
- Outside the agent loop, LLM calls made inside node activities keep the unifier's default of two SDK retries (`server/services/llm/unifier.py:133`): the chat-model and text path (`server/services/ai.py:820-828`) and the translate node's LLM provider (`server/nodes/translate/_providers/llm.py:168-180`). Their failures come back as result envelopes, and graph nodes are never retried on a reported failure, so the SDK's hidden retries are the only ones.

**Verdict:** Adopted with deviations. Agent steps follow the recipe; node-level calls keep client retries.

**Recommendation:**
1. Settle this with RFC-0004 Q9 (transient errors in graph nodes, open): either keep SDK retries for node-level calls and document why, or pass `sdk_max_retries=0` and raise a retryable error for transient categories so Temporal owns the retry. Do not apply RFC-0004 §13.3's "Turn off SDK retries at the three call sites" on its own: without Q9's retryable errors, one 429 would fail the node. Versioning: none. When: later, with Q9. Effort S.

**Remarks:** Nested retries multiply cost and hide waits from the UI, the recipe's reason. For one-shot node calls the cost is small, so this stays P3.

### 16.5 Retry policy from HTTP responses

**Docs:** https://docs.temporal.io/ai/cookbook/http-retry-enhancement-python. Translate the HTTP response into `ApplicationError(non_retryable=..., next_retry_delay=...)`.
- Retry 408, 409, 429 and 5xx; do not retry other 4xx.
- An `x-should-retry: true|false` header overrides the status.
- Read `retry-after-ms`, then `retry-after` in seconds, then as an HTTP date; use the delay only `if retry_after is not None and 0 < retry_after <= 60`, otherwise fall back to the policy's backoff.

**SDK:** `ApplicationError.next_retry_delay` and `ApplicationErrorCategory.BENIGN` are in 1.30.0 (`temporalio/exceptions.py:111-121`); the worker logs BENIGN failures at DEBUG instead of WARNING. Unchanged in 1.34.0.

**OpenCompany today:**
- Parsing is richer than the recipe's: seconds, HTTP dates (measured from the response's own `Date` header), `retry-after-ms` and Google's `RetryInfo`; the largest hint wins (`server/services/llm/protocol.py:521-544`) and becomes `next_retry_delay` (`agent_activities.py:265-302`).
- `x-should-retry` is not read, although the OpenAI and Anthropic SDKs both honour it (`openai/_base_client.py:832-839`, `anthropic/_base_client.py:805`).
- 409 is INVALID_REQUEST, non-retryable (`protocol.py:942-943`), while both SDKs retry it as a lock timeout (`openai/_base_client.py:847-850`, `anthropic/_base_client.py:821`).
- The honoured delay has no cap (`valid_retry_delay`, `protocol.py:807-819`): a 3,600 s hint holds the step's next attempt for an hour.
- Retryable failures are not BENIGN, so every rate-limit retry logs a WARNING with a traceback.

**Verdict:** Adopted with deviations: richer parsing than the recipe, three gaps.

**Recommendation:**
1. In `LLMError.from_exception` (`protocol.py:502`) and `_classify_error` (`:888`): honour `x-should-retry`; treat 409 as retryable for the OpenAI and Anthropic providers, whose own SDKs retry it, and keep it non-retryable elsewhere. In `_as_temporal_llm_error` (`agent_activities.py:246-303`): cap the honoured delay at 5 minutes per retry (`LLM_STEP_RETRY.maximum_interval`) while keeping the raw hint in the details for the UI; there is no 60 s cap like the recipe's. A hint longer than the step's remaining budget parks the turn instead, with the hinted time as its first probe, in generations with `scheduler_version=1` (§11.3 item 4, §11.5, §16.16). Versioning: none here; the park takes `scheduler_version=1`. When: RFC-0005 Phase 5, with RFC-0005 §13.4's Retry-After handling. Effort S. Risk: an attempt made before the provider's window ends meets another 429, which costs nothing.
2. Mark retryable failures `category=ApplicationErrorCategory.BENIGN` (RFC-0004 §9.2): 1b.28, specified in §18.5.

**Remarks:** QUOTA and BILLING are non-retryable, so daily-quota hints of hours never reach the cap. The docs' Delayed Retry pattern (§11.5) is the general form of this recipe.

### 16.6 Deep research (`basic-openai-python`)

**Docs:** https://docs.temporal.io/ai/cookbook/basic-openai-python. Four agents, each a structured-output LLM activity. Planning and query generation run in sequence; searches run in parallel with `asyncio.gather`, each failure caught so the workflow continues "with partial results", and the workflow fails only if every search failed.
- "It is critical to set the `start_to_close_timeout` ... If it is too short, the Activity will fail with a timeout error, causing a retry loop that never completes." The recipe uses 300 s.
- Every activity passes `summary=` ("Planning research", "Searching web for information").
- The current date comes from `workflow.now()` at run time.

**SDK:** `summary=` on `execute_activity`: 1.30.0. `asyncio.gather` over activities is deterministic.

**OpenCompany today:**
- Delegations fan out with partial failure: direct `delegate_to_*` children in a window of `max_concurrent_subagents` (default 3, `agent_workflow.py:1451-1452`), detached `DelegatedTaskWorkflow` runners for Task Manager assignments (`:1873-1899`, `ParentClosePolicy.ABANDON`), and concurrent Task Manager preflights that tolerate a failed call (`:1977-1979`).
- The ordinary tool calls of one model turn always run one after another (`:2363-2369`).
- No activity or child passes a summary (§16.1).

**Verdict:** Adopted with deviations. Fan-out with partial failure exists for delegations; parallel independent tool calls and labels do not.

**Recommendation:**
1. Activity summaries: §16.1 item 2.
2. Optional: run the read-only tool calls of one turn together, as §6.3 specifies, once tool inputs stop carrying the graph (§16.8 item 3).

**Remarks:** The page's "retry loop that never completes" is what the 10-minute attempt does to a slow local model today (§16.3 item 3).

### 16.7 Structured outputs

**Docs:** https://docs.temporal.io/ai/cookbook/structured-output-openai-responses-python. `responses.parse` with a Pydantic `text_format`; an invalid answer fails the activity and Temporal retries it. "If the LLM produces invalid responses consistently, Activity retries will fail consistently", so the recipe caps attempts at 3 "to mitigate the cost of such futile retries".

**SDK:** plain activities and `RetryPolicy`.

**OpenCompany today:**
- Agent turns repair instead of retrying blind. `show_ui` refuses an invalid spec "with every reason; write it again" (`server/services/genui/spec.py:7-24`, `:557`); invalid JSON tool arguments are answered "Tool arguments were invalid JSON" (`agent_workflow.py:2050`); tool arguments are validated against the plugin's `ToolInput`, and the errors go back to the model.
- Nothing stops a model that keeps failing a schema: it can spend up to `max_iterations` paid steps (default 200).

**Verdict:** Adopted with deviations, and better than the recipe for agents: the model sees why its output failed, while a retried activity would only resample the same prompt.

**Recommendation:**
1. Count consecutive refusals of the same structured tool within one firing; after 3, stop offering that tool for the turn and tell the model why. Versioning: a per-run flag (fewer steps change the commands); it can ride `context_pressure_version=2` (§16.8) if it ships with it in RFC-0005 Phase 4, and needs a version of its own after that. When: later. Effort S.

**Remarks:** Provider-native strict structured output would reduce refusals, but it behaves differently across the 14 providers; not worth the per-provider surface.

### 16.8 Claim check

**Docs:** https://docs.temporal.io/ai/cookbook/claim-check-pattern-python. A claim check stores a large payload outside Temporal and puts only a key in history. The recipe's `PayloadCodec` stores payloads above an inline threshold (default 20 KB, for "Most AI/RAG Workflows") in S3; a codec server lets the Web UI show them. Since Python SDK 1.25 the built-in External Storage does the same without a custom codec (Public Preview).
- Limits: one payload warns at 256 KB on this page and fails at 2 MB; history warns at 10 MB and is terminated at 50 MB, or at 51,200 events; one gRPC request is capped at 4 MB, so "scheduling several Activities with moderate-sized inputs in the same Workflow Task can exceed 4 MB even when each payload is under 2 MB".
- "Stay well under 256 KB."

**SDK:** 1.30.0 has `ExternalStorage` (experimental, default threshold 256 KiB, `temporalio/converter/_extstore.py:242`) and an S3 driver; 1.34.0 changes `driver_selector`'s context type, a breaking change. `PayloadCodec` is GA in both. The SDK warns at 512 KiB (`converter/_payload_limits.py:17`), the server's default, so the page's 256 KB is conservative; 1.34.0 moves the checks into sdk-core and removes the Python `PayloadSizeError`. `workflow.info().is_continue_as_new_suggested()` and `get_current_history_size()` are in 1.30.0.

**OpenCompany today:**
- Already by reference: media (`FileRef`, `AudioRef`, `server/services/media/refs.py`), Pi tool receipts over 256 KiB (`server/services/temporal/pi_protocol.py:16`), and, planned, webhook bodies over 64 KB (RFC-0005 I7). No data converter, codec or External Storage is configured (`server/services/temporal/client.py:92-97`).
- Still in history: every step input carries the transcript, every visible tool definition and a second copy of the system prompt (`agent_workflow.py:1179-1197`). The transcript budget is 393,216 bytes, three quarters of the 512 KiB warning (`transcript_budget_bytes()`, `server/services/temporal/agent_context_pressure.py:59-70`). It holds only after a tool turn: relief runs there (`agent_workflow.py:2741-2980`), not before a firing's first step and not after an answer without tools (`:1391`). A turn with many large results can stay over it, because clipping keeps at least 2,000 characters per result (`agent_context_pressure.py:56`).
- So the first step of every firing sends the stored conversation as it was saved, up to the 1,000,000-byte load limit (`agent_activities.py:64`), and `agent.prepare_payload` records that conversation once more as its result. A conversation that grows through answers without tool calls is never cleared or compacted: it fails with `ConversationTooLarge` once it passes 1 MB (`:1542-1556`), or earlier if the model's context window is smaller. Compaction can also be switched off globally (no threshold, `agent_activities.py:1673-1685`), which leaves only clearing and clipping.
- Rollover: the check reads `is_continue_as_new_suggested()` first and its own 10,000-event cap second (`_history_pressure`, `server/services/temporal/trigger_listener_workflow.py:89-102`; `agent_workflow.py:2992`). Server 1.32.0 suggests continue-as-new at 4 MiB of history or 4,096 events, so history size is already covered. The check runs after each tool turn and waits while a delegation is live (`:2993-2996`).
- The carry: on the durable path, continue-as-new carries the transcript, the whole prepared payload again (with its seed `conversation` of up to 1 MB, `memory_content` and `parameter_snapshot`), the tools and all thinking text so far, and refuses above 1,900,000 bytes with the non-retryable `AgentContinuationTooLarge` (`agent_workflow.py:366`, `:3004-3031`). The resumed run reads `conversation` and `memory_content` only when no transcript was carried (`:1058-1090`), which never happens on that path. The carried `parameter_snapshot` is also stale: it predates any `agent.refresh_tools` merge into the run's own snapshot (`:2499`) and overwrites that merge on resume (`:1002-1003`).
- Every tool input of an admitted generation carries the graph's `parameter_snapshot` (`agent_workflow.py:124`, `:300-325`); delegation children carry the canvas.
- The 4 MB request trap: Task Manager preflights are scheduled together, each with the graph and the scope (`:1936-1971`). It needs about 0.8-1 MB per input, and the recorded fixtures' inputs are a few KB, so the risk is unmeasured, not live.

**Verdict:** Adopted with deviations. The application-level claim check is right and the transcript stays in history by decision (T6), but the bound T6 relies on fails at a firing's first step and for conversations without tool calls.

**The bound T6 relies on.** With transcripts in history, every agent run must keep:
- **Bound 1.** Before every LLM step, the transcript is at most `transcript_budget_bytes` (393,216 bytes), a firing's first step included. The one exception is a turn whose unread tool results alone exceed it after clipping; it is logged and still limited by bound 2.
- **Bound 2.** Every `agent.execute_llm_step` input stays under 1 MiB, half the 2 MiB payload error, tools and system prompt included.
- **Bound 3.** A run continues as new at the server's suggestion (4 MiB or 4,096 events) at the next iteration boundary, so its history stays near 4 MiB plus one iteration, far below the 10 MB warning and the 50 MB limit. At about 400 KB per step that is about ten steps per run (est.).
- **Bound 4.** The continue-as-new input stays under 1,900,000 bytes (the existing guard), with the transcript as its only large part.
- **Bound 5.** The stored conversation stays under the 1,000,000-byte load limit. Bound 1 makes this true: a save is at most the budget plus one answer.

**Recommendation:**
1. Slim the continue-as-new carry. In the durable carry (`agent_workflow.py:3004-3019`), drop `conversation`, `memory_content` and `parameter_snapshot` from the carried `prepared_payload`. Keep `tools`: the resumed run's browser claims and, without execution control, its tool list read `prepared_payload["tools"]` (`:959`, `:1012`). The resumed run keeps the context's own, refreshed snapshot. Versioning: none; the continue-as-new input is not compared on replay, and the resumed run already reads these keys with `.get`, so old carries still parse. When: Phase 1b, as 1b.19. Effort S. Risk: none known; the 1.9 MB guard now measures what matters.
2. Relief before every step, `context_pressure_version=2`. Move the relief block (clear old tool results, summarize, clip) from after the tool turn to the top of each iteration, keeping the newest user message and any unread tool results verbatim. When compaction is off or cannot get under the budget, drop the oldest whole turns and add a one-line note that earlier conversation was trimmed. Before scheduling a step, check bound 2; if relief cannot meet it, fail the run with a non-retryable `AgentStepInputTooLarge` that names the tools or the prompt. This makes bound 1 hold for the first step, for conversations without tool calls and for the inputs that merge point B1 will append (RFC-0005 §11). Versioning: a new value of the recorded `context_pressure_version`, frozen per run by `agent.prepare_payload` (`agent_activities.py:1839-1840`), the existing replay contract, and chosen only for generations with `event_os_version=1`; version-1 runs replay unchanged. When: RFC-0005 Phase 4, before merge point B1. Effort M. Risk: a first step that must summarize adds one paid call to that firing.
3. Tool and delegation inputs carry `(generation, node id)` instead of the graph's `parameter_snapshot`; the activity loads the snapshot of that generation (RFC-0004 D7; RFC-0005 §10.2 "Loads the graph by reference"). Canvas Runs, which have no stored generation, keep passing it. Versioning: none (activity inputs are not compared; the activity accepts both shapes). When: RFC-0005 Phase 4. Effort M. Risk: a generation's stored snapshot must be readable from every worker. §17.8 covers the security side of this snapshot (1b.31, 1b.32).
4. Amend RFC-0005 I7 with the T6 exception and these bounds (§20).
5. The consuming save raises. Under `event_os_version=1` the save that consumes inputs must raise on failure. RFC-0005 §9.5 makes it the consumption point ("writes the transcript and advances the stream's `consumed_seq` in one transaction"), but `_save_conversation` swallows every error today (`agent_activities.py:689-697`). The step then retries instead of continuing with inputs that would be offered again; the retry replays free through the gateway and is paid again with an own key. The save raises a retryable `ApplicationError`, which 1b.26's wrapper for failures after the provider answered lets through (§11.4). Versioning: activity body, behind `event_os_version=1`. When: RFC-0005 Phase 4. Effort S.

**Remarks:**
- External Storage and a claim-check codec are rightly not adopted: they are preview, and "Keep payload objects for as long as execution, reset, or replay may need them" (`samples/deepagents_plugin/external_storage/README.md:62`) has no end for runs that never time out (RFC-0004 §12.3).
- The page's quadratic-growth warning applies: each step records the whole transcript again. Bound 3 limits it; the price is a rollover every ten or so steps at the budget's ceiling, with a carry of about 0.5 MB after item 1 (est.).
- Moving the transcript by reference (RFC-0004 §13.4) is superseded by T6. It would have needed an authoritative save in the middle of a step and pressure decisions moved into the activity.
- A tool's own result is recorded whole: `tool_result_max_chars` cuts only the model's copy (`agent_workflow.py:2414-2424`). A node that returns megabytes inline fails at the 2 MiB payload limit; media nodes return `FileRef`s, and text-heavy tools rely on their own read limits. After 1b.26 (§11.4) that failure is no longer retried, except on the legacy tool path until 1b.3 (§11.2) gives it the plugin's retry policy.
- History holds prompts and customer text in plain text. Encryption is RFC-0004 Q13 (open); a codec would compose with this design.

**Sketch** (item 2; needs the new version):

```python
# AgentWorkflow: top of each iteration when pressure_version >= 2  (RFC-0005 Phase 4)
async def _relieve_before_step(self, messages, payload, iteration) -> None:
    budget = int(payload.get("transcript_budget_bytes") or 0)
    keep_from = _newest_user_index(messages)        # the request and unread results stay verbatim
    clear_old_tool_results(messages, turn_start=keep_from, budget_bytes=budget,
                           is_capped=_result_is_capped)
    if transcript_bytes(messages) <= budget:
        return
    if payload.get("compaction_threshold") and has_summarizable_history(messages, keep_from):
        summary = await workflow.execute_activity(
            "agent.compact_context", args=[self._compact_payload(messages[:keep_from], payload)],
            activity_id=f"compact-before-step-{iteration + 1}",
            start_to_close_timeout=COMPACT_MEMORY_TIMEOUT, retry_policy=LLM_STEP_RETRY)
        messages[:] = swap_in_summary(messages, keep_from, summary["summary"])  # system prompt kept
    if transcript_bytes(messages) > budget:
        drop_oldest_turns(messages, keep_from=keep_from, budget_bytes=budget,
                          note="Earlier conversation was trimmed to fit.")

def _check_step_input(llm_payload) -> None:          # bound 2, before scheduling the step
    size = sum(p.ByteSize() for p in workflow.payload_converter().to_payloads([llm_payload]))
    if size > STEP_INPUT_MAX_BYTES:                  # 1 MiB
        raise ApplicationError(f"LLM step input is {size} bytes", type="AgentStepInputTooLarge",
                               non_retryable=True)
```

### 16.9 Post-LLM guardrail with hard rules

**Docs:** https://docs.temporal.io/ai/cookbook/guardrails-hard-rules-python. An LLM classifies content as safe, review or block; deterministic hard rules run after it and may only escalate: "a rule can only escalate a verdict to `block`". When a rule fires, the LLM's reasoning stays in the result "so every decision remains auditable". The page's extensions: more rules ("regex, allow/deny lists, PII detectors"), an audit sink, and routing `review` verdicts to a human approval step. The openai_agents samples add LLM-based input and output guardrails that end a run through tripwire exceptions.

**SDK:** plain activities.

**OpenCompany today:**
- Deterministic rules already decide whether a call may run, over the model's choice. Ask first holds a call that sends, judged on what the node will run with, so a model cannot get past it by setting a locked field (`as_it_runs`, `server/services/approvals/tool_calls.py:118-129`, `:198`). A rule that cannot be read counts as on (`:168-175`). MCP and Browser requests pass the network policy (`server/services/netpolicy.py`), and the Hire rule decides which tools an employee gets.
- Audit: a held row keeps the model's raw arguments (`original_args`, `tool_calls.py:283`), and a send made with Ask first off while answering the owner is still recorded (`approved_by: auto`, `_auto_recorder`, `:329-354`).
- Missing: nothing checks what an outgoing message says. With Ask first off, an agent's sending call runs (`check`, `:205-208`), and the approvalGate in a Hire-built graph approves the draft at once (`server/nodes/workflow/approval_gate/__init__.py:217-226`). An employee could send a saved credential, a key-shaped string or one customer's number to someone new, and only the provider would see it.

**Verdict:** Adopted with deviations. The escalate-only, auditable structure exists for actions; rules on content are missing, and T15 adds them.

**Recommendation:**
1. Built-in content rules. A new `server/services/approvals/content_rules.py` with two rules that cannot misfire on ordinary mail: (a) the text contains the value of a saved credential (API keys and OAuth tokens read through `AuthService`, compared in process, values of 12 characters or more); (b) it contains a secret-shaped token (well-known key prefixes, PEM private-key headers). Call it where sends are decided today: in `tool_calls.check` with Ask first off, a hit holds the call as a draft through the existing `hold` path instead of running it; in the approvalGate's `request` operation, a hit keeps the row `pending` instead of approving it; in `check_in_process` (CLI agents, which cannot hold a draft), a hit refuses the call. The owner's card says which rule fired, as a line in the row's existing `details`, so no column is added before Phase 7's `held_reason`; the row keeps the rule id and a redacted excerpt, never the secret. Versioning: none (activity bodies). When: Phase 1b, as 1b.7. Effort M. Risk: a message that legitimately carries a key the owner asked for is held, which is the intended outcome.
2. Owner-editable rules and the recipient rule. Owner rules (phrases, patterns, recipient lists) per employee or for all, edited in Settings and run by the same engine. The built-in rule "a recipient outside the conversation" applies to turns that answer a customer thread, once Phase 4b gives each customer conversation its own thread. In Phase 7 every send goes through the outbox, whose row gains `held_reason`, so graph sends without a gate (Dev-mode graphs) are covered too. Versioning: none for the rules. When: RFC-0005 Phase 7. Effort M.

**Remarks:**
- Judge rules on what the node will run with (`as_it_runs`), as Ask first does, or a locked-field trick bypasses them.
- Secret values never enter history: the check runs inside the activity, and only the rule id and a redacted excerpt are stored.
- Do not tell the model which rule fired. A prompt-injected model told "contains a key" could split the key across messages; the model reads the ordinary "Not sent yet" text.
- The page's LLM classifier is optional. The deterministic layer is the valuable part; a model call per send would cost money on every message and add a gate the owner cannot predict.
- The recipient rule does not apply to owner chat: the owner may ask an employee to write to anyone.
- With Ask first on, a hit still adds its line to the card, so the owner sees why a draft deserves a second look.

**Sketch** (item 1; activity bodies only):

```python
# server/services/approvals/content_rules.py  (new; built-in rules 1b.7, owner rules RFC-0005 Phase 7)
_SECRET_SHAPES = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|xox[abp]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY-----")

@dataclass(frozen=True)
class Hit:
    rule: str      # "saved_credential", "secret_token"; later "owner:<id>", "unknown_recipient"
    label: str     # shown on the owner's card; never the matched text

async def hits(*, owner_id: str, workflow_id: str, text: str) -> list[Hit]:
    found: list[Hit] = []
    if await _contains_saved_credential(owner_id, text):     # values via AuthService, in process
        found.append(Hit("saved_credential", "Contains a saved credential"))
    if _SECRET_SHAPES.search(text):
        found.append(Hit("secret_token", "Contains something that looks like a key"))
    return found

# tool_calls.check, the Ask-first-off branch (today it runs the call):
found = await content_rules.hits(owner_id=owner, workflow_id=workflow_id,
                                 text=outgoing_text(spec, node_data))
if found:                               # rules only escalate: held even with Ask first off (T15)
    row = await hold(database, context, node_cls, spec, node_data, held_by=found)
    return Checked(result=_held(row, node_cls, context))
# approvalGate.request: a hit keeps the row "pending" instead of approving it.
# check_in_process: a hit refuses the call; an in-process call cannot wait.
```

### 16.10 Human-in-the-loop AI agent

**Docs:** https://docs.temporal.io/ai/cookbook/human-in-the-loop-python. The LLM proposes an action; if it is risky, the workflow notifies the human (an activity) and waits. A Signal handler sets state only when its `request_id` matches the pending request, and `workflow.wait_condition(lambda: self.current_decision is not None, timeout=...)` waits with a durable timer: "while waiting, the agent consumes no compute resources". Extensions: Queries for status, escalation. The framework samples use a Signal or an Update for the answer and a Query for the pending draft; the Strands "activity interrupt" lets an activity decide to pause on data only it can see.

**SDK:** Signals, Updates with validators, `wait_condition(timeout=...)`, Queries, `workflow.sleep` and `@workflow.init`: 1.30.0.

**OpenCompany today:** four human waits.
1. Held tool calls (Ask first). The tool activity keeps a draft row and tells the model it waits (`tool_calls.py:223-224`); the agent does not block. The owner's Send starts `ApprovedToolCallWorkflow` (`server/services/temporal/approved_tool_call_workflow.py:33-80`): it sleeps out the 5-second Undo window, claims the row at its revision, runs the node's activity once (`SEND_ONCE`), records `sent`, `not_sent` or `unknown`, and leaves an `[update]` note for the employee (`server/services/approvals/execution.py:209-220`). Drafts expire after 168 hours through a watchdog sweep, not a timer (`server/services/approvals/reconcile.py`).
2. The `approvalGate` node, which sits before the reply in every Hire-built app employee (`server/services/employees/builder.py:818-831`). Its activity re-reads its row at least every 10 s (`POLL_SECONDS`, `server/nodes/workflow/approval_gate/__init__.py:48`), runs up to 24 hours per attempt with unlimited attempts (`:184-185`), and re-attaches by a key built from the activity's identity (`:110-117`). A pause checkpoint waits for it as for any node activity, so Stop does not complete while a gate waits.
3. Reviewed delivery: three activities of up to 31 days each, waiting for the owner's review through gates run in process (`server/services/employees/jobs.py:333-381`): `employee.job.deliver` in EmployeeJobWorkflow (`server/services/temporal/employee_job_workflow.py:133-139`), `employee-reviewed-delivery` in AgentWorkflow under `execution_control.action()` (`agent_workflow.py:3133-3138`), and `employee.job.resolve_delivery` in EmployeeJobDeliveryWorkflow (`employee_job_workflow.py:149-157`).
4. The Browser's `request_user` wait, held in memory inside the tool activity (`server/nodes/browser/_session.py:25`, `:408`). Acceptable: the browser session itself does not survive a restart.

Two further findings, both addressed in §9.6:
- An approved held send goes out even while its employee is stopped. Nothing on that path reads the generation's control state (`server/services/approvals/decisions.py:116`, `execution.py:58-128`, `ApprovedToolCallWorkflow`). T12 asks for the send on Resume (1b.5).
- RFC-0005 §17.5 plans `ApprovalWorkflow` ("an Update `decide` plus a durable timer ... It replaces `ApprovedToolCallWorkflow`") but names only held sends; neither the gate nor reviewed delivery appears in RFC-0005. RFC-0004 D13 covers all of them, and §9.6 extends ApprovalWorkflow to them.

**Verdict:** Misused: the gate and reviewed delivery hold a worker slot and a heartbeat for days, depend on polling and keep Stop from completing, where the docs use a message and a durable timer (held tool calls follow the docs' non-blocking variant on purpose).

**Recommendation:**
1. Approved sends wait while the employee is stopped (T12): 1b.5, specified in §9.6.
2. `ApprovalWorkflow` for every human wait: §9.6 specifies it for RFC-0005 Phase 7, with T10's intent deliverer and T12. Two points are specific to agents:
   - The reply gate fails closed: the turn maps the ApprovalWorkflow's outcome to the gate's output, and no reply text goes out unless the owner approved it.
   - The three reviewed-delivery waits become the same child workflow, followed by a single send activity under the redo guard (1b.1, §11.2).
3. Keep held tool calls non-blocking. Blocking the turn as the recipe does would hold the turn and its admission lease (RFC-0005 §13.3) for up to 168 hours.

**Remarks:**
- The agent learns a held call's outcome on its next turn (an `[update]` note), so it cannot chain a dependent step in the same turn. That is the price of not blocking.
- §9.6 holds ApprovalWorkflow's design, T10 and T12 included; this entry adds only the points specific to agents.

**Sketch:** §9.6 (ApprovalWorkflow).

### 16.11 Agent memory and state (featured: memory-state-stripe)

**Docs:** https://docs.temporal.io/ai/featured/memory-state-stripe. The article separates context (what the model sees this turn), long-term memory (a store you own) and state that systems of record own: execution state (Temporal), effect state (the provider, Stripe in the demo), authorization state and domain state.
- "Temporal is not a memory store": it rebuilds one run's working memory by replay.
- Authorization: "read it live from the authorization system, never from memory".
- "A step can run more than once ... Give every side effect an idempotency key that the receiving system checks." "The idempotency key follows the Run ID. It stays the same across retries in one run. It changes on a reset, on a new run that reuses the Workflow ID, and on Continue-As-New."
- "Event History grows every turn ... the history grows with the square of the turn count."
- Model and Stripe SDK retries are off; history holds customer data, so "plan its retention and add an encryption codec".

**SDK:** none specific; the article is about patterns.

**OpenCompany today:**
- Context: `messages` in the workflow, saved each step to `agent_conversations` keyed by workflow, generation and agent node (`server/services/agent_context/conversation.py`), loaded loudly at run start (`agent_activities.py:1542-1556`).
- Long-term memory: the Memory tool's own store (`server/nodes/tool/simple_memory/`).
- Authorization read live: Ask first is read on every call and fails closed (`tool_calls.py:168-175`).
- Effect state: approval rows with claim tokens and outcomes (`execution.py:101-128`). Through the gateway, a retried LLM call "replays at $0" from a cache keyed by workflow, run and activity ids, kept 48 hours (`server/services/gateway/attribution.py:12-14`, `:46`, `:73-87`). Calls with the owner's own key have no replay; the step policy accepts paying twice for an ambiguous loss (`_retry_policies.py:82-93`).
- Idempotent effects today: a later attempt of an agent's call that sends answers `SendOutcomeUnknown` instead of sending (`resend_refusal`, `tool_calls.py:367-379`); approved sends run once; a held row is created once per `call_key` (workflow, execution and tool call id, `:103-104`), and `execution_id` survives continue-as-new (`agent_workflow.py:966-973`), so the key survives a rollover, which is stronger than the demo's run-id key.
- The gap. `resend_refusal` covers a later attempt of an agent's tool call or an owner-approved send only when the plugin has an `ApprovalSpec` whose `sends()` is true and that is not `restrict_while_asking`: Discord, WhatsApp, WhatsApp Business and Telegram sends, email, Gmail, Outlook mail, Google and Microsoft Calendar, Google Drive, Stripe, Dodo Payments' money operations, and MCP tools whose per-tool Ask is on. Not covered:
  - plugins without a spec that change the world: shell, file_modify, aws_action, cloudflare_action, gcloud_action and process_manager (annotated `destructive: True`), and github_action (creates and merges PRs), vercel_action (deploys) and http_request (POST), annotated `destructive: False`. So `destructive` is no reliable marker. 58 plugin files declare `readonly: False`, 39 declare `readonly: True`, the rest neither;
  - MCP tools whose per-tool Ask is off (`server/nodes/mcp/mcp_connector.py:83-90`); the connector snapshot keeps only `read_only` from the server's hints (`server/nodes/mcp/_client.py:457-465`);
  - every send made as a graph node, because `resend_refusal` returns early for anything that is not an agent's call or an approved send (`tool_calls.py:375-376`). That includes the reply after the approvalGate in every app employee Hire builds (`builder.py:818-831`). Graph nodes run with the plugin's policy, 3 attempts by default (`server/services/plugin/scaling.py:58`, `workflow.py:838-845`), so that reply can go out twice, which contradicts E12; RFC-0004 §9.4 already says graph sends have no guard. Team-job deliveries have their own guard (`delivery_needs_review`, `server/services/employees/jobs.py:276-281`).
  - Legacy-path agent tools run with no retry policy, so the server's unlimited default applies (`agent_workflow.py:115-118`, `:137-142`). A laptop that sleeps through the 2-minute heartbeat timeout during a tool call is enough to start a second attempt.
- History grows with the square of the turn count inside a run (§16.8).

**Verdict:** Adopted with deviations. The split between context, memory and state matches the article closely; the idempotency rule is applied to agent sends only.

**Recommendation:**
1. A redo guard for every send and every side effect that is not idempotent (T13, E12): 1b.1, specified in §11.2 with the effect classes, for agent tool calls, owner-approved sends and graph nodes alike. Points specific to agents:
   - The Coding Agent's virtual workspace tools (`coding.workspace_tool`) call today's guard at `server/services/coding_workspace_tools.py:111`, besides `BaseNode.as_activity` (`base.py:1220`), so 1b.1 changes both call sites.
   - MCP tools take their class from the server's `readOnlyHint` and `idempotentHint` (`mcp.types.ToolAnnotations`), which the connector snapshot must then keep (today it keeps only `read_only`) and pass as locked binding fields.
   - Agent tools that are safe to run again: memory and checklist writes, canvas notes, file overwrites, Task Manager with its durable row, and the Browser, whose later attempt resumes a cut wait.
2. Provider idempotency keys, where a provider accepts them (Stripe's API does; Dodo Payments' does not, which is why that plugin's writes already run once): §11.2 specifies them, from RFC-0005 Phase 3 for message sends ("send idempotency keys"), then per plugin.
3. Under `event_os_version=1` the save that consumes inputs raises on failure, so an input counts as consumed only when its text is saved: §16.8 item 5, RFC-0005 Phase 4.

**Remarks:**
- Workflow id plus activity id is the right key here: tool activity ids are `tool-<node>-<iteration>-<call>` (`agent_workflow.py:270-272`), and iterations continue across continue-as-new. They repeat only after an operator-level Temporal reset of a run; product Resets start a new generation with new ids. The runbook should say so.
- The demo continues as new only before its refund, because its key follows the run id. OpenCompany's call keys survive continue-as-new, so it needs no such rule.

### 16.12 Durable MCP server

**Docs:** https://docs.temporal.io/ai/cookbook/hello-world-durable-mcp-server. MCP tools "act as thin wrappers that start Temporal Workflows". Each tool's steps are a workflow whose external calls are activities with a retry policy (unlimited attempts, at most a minute apart, `schedule_to_close_timeout=40 s` per activity); the FastMCP server waits on `handle.result()`.

**SDK:** plain client and workflow APIs.

**OpenCompany today:**
- OpenCompany is an MCP client and serves MCP to no outside client. Its one MCP server is `/mcp/ide`, for its own CLI agents (`server/services/cli_agent/mcp_server.py`). Each connected node becomes a tool that runs in process (`execute_tool`, `server/services/cli_agent/workflow_tools.py:321-338`) inside the single Claude Code activity, on the AI_HEAVY queue with one attempt (`server/nodes/agent/claude_code_agent/__init__.py:253-257`). Pi Coding Agent and Task Manager delegations are the exception: they get a durable `PiInlineDelegationWorkflow` (`workflow_tools.py:337-353`).
- Those calls cannot wait as drafts, so a call that sends is refused while Ask first is on (`check_in_process`, `tool_calls.py:227-251`).
- As a client: a connector's tool list is read when it is saved, and changes need the owner's Accept; each call runs as the `mcpConnector` node's activity, with Ask first per tool (`server/nodes/mcp/mcp_connector.py:83-90`).

**Verdict:** Not applicable as written, since OpenCompany serves no outside MCP client; the CLI agents' bridge uses the durable-tool idea for delegations only.

**Recommendation:**
1. Only if Claude Code employees are meant to send messages: route `/mcp/ide` calls that send through a short durable workflow started by the handler (the recipe's shape), so they get held drafts and at-most-once sends instead of a refusal. A new workflow type; no change to existing types. When: later. Effort M.

**Remarks:** The client side is stricter than the framework plugins', which can cache a server's tool list (`TemporalMCPClient(cache_tools=True)` in the Strands recipe): OpenCompany snapshots the list in its database and requires Accept on change, which also blocks tool-list poisoning. The single attempt means these in-process calls are never retried, so the redo guard (1b.1, §11.2) is not needed on that path; T15's built-in rules are (1b.7, §16.9 item 1).

### 16.13 Multi-agent orchestration with Google ADK

**Docs:** https://docs.temporal.io/ai/cookbook/multi-agent-adk-python. Orchestration runs in the workflow: two agents reason in parallel (`ParallelAgent`), a third synthesizes (`SequentialAgent`). Each LLM call is an activity with a per-agent `summary` ("Fleet Agent — LLM reasoning"), each I/O tool an activity. A local wrapper returns a failed tool "to the LLM as a string so the agent can adapt instead of crashing the pipeline", while programming bugs propagate.

**SDK:** `temporalio.contrib.google_adk_agents` is in 1.30.0; 1.34.0 adds human-in-the-loop helpers (`_hitl.py`). The samples use the standalone `temporalio.google_adk` package, which needs 1.34.0.

**OpenCompany today:** parallel children for delegations (§16.6); tool failures go back to the model as text, built by string formatting: `f'{{"error": "{type(e).__name__}: {e}"}}'` (`agent_workflow.py:2628`), which is invalid JSON when the message holds a quote. Teammates' structured results pass Task Manager's acceptance. No activity or child summaries.

**Verdict:** Adopted with deviations: no labels, and a tool error that can be malformed JSON.

**Recommendation:**
1. Build the tool error with `json.dumps({"error": f"{type(e).__name__}: {e}"})` at `agent_workflow.py:2628`. The text lands in `messages`, whose byte size drives transcript-pressure decisions, so a different encoding could flip a clearing or compaction decision on replay. Versioning: ship it under `context_pressure_version=2` (§16.8 item 2). When: RFC-0005 Phase 4. Effort S.
2. Summaries per agent and per tool: §16.1 item 2.

**Remarks:** ADK's 1.34.0 helpers leave out credential requests because "the exchanged secret would be recorded in workflow history" (`temporalio/contrib/google_adk_agents/_hitl.py:13-15`): the same rule OpenCompany follows by resolving credentials only in leaf activities.

### 16.14 Framework cookbook pages: OpenAI Agents SDK, Strands Agents, AI SDK by Vercel

**Docs:** https://docs.temporal.io/ai/cookbook/openai-agents-sdk-python, https://docs.temporal.io/ai/cookbook/strands-agents-python and https://docs.temporal.io/ai/cookbook/ai-sdk-by-vercel-typescript. Each runs a framework's agent loop inside the workflow, with a plugin that makes every model call an activity and helpers that turn activities into tools (`activity_as_tool`).
- OpenAI Agents: `OpenAIAgentsPlugin` on the client; `Runner.run` in the workflow.
- Strands: `TemporalAgent` replaces `Agent`; `TemporalMCPClient` references an MCP server registered on the worker by name; `cache_tools` avoids re-listing its tools every turn.
- AI SDK by Vercel (TypeScript): `temporalProvider` wraps the model; tools are activities with `maximumAttempts: 3`; `stepCountIs(5)` caps the loop.

**SDK:** `temporalio.contrib.openai_agents` and `temporalio.contrib.strands` are in 1.30.0 and 1.34.0; the samples import the standalone packages (`temporalio.openai_agents`, `temporalio.strands_agents`), which need 1.34.0. The Vercel recipe is TypeScript only.

**OpenCompany today:** AgentWorkflow is its own provider-neutral loop (14 providers and named OpenAI-compatible endpoints), with what none of the plugins provide: a conversation store across firings, transcript pressure and compaction, cooperative Stop and Resume, Ask-first drafts, team delegation and per-attempt chat streaming. Its step cap is `max_iterations` (default 200); frozen plugin policies allow 3 attempts by default (`scaling.py:58`), like the Vercel recipe.

**Verdict:** Not applicable: a plugin would put a foreign, mostly preview loop into workflow code that runs unsandboxed and force a migration of every recorded AgentWorkflow history, and RFC-0004 §13.1 and §15 already decided "Never as the core loop".

**Recommendation:** none.

**Remarks:** The plugins made the same choices independently (the model call as one activity carrying the transcript, tools as activities, client retries off), which supports the native loop.

### 16.15 Sample group: framework plugins

**Docs:** samples `openai_agents`, `langgraph_plugin`, `google_adk_agents`, `deepagents_plugin` and `strands_plugin` (temporalio/samples-python at 33f7dc29; all need temporalio 1.34.0 or later, `pyproject.toml:9`). Beyond the framework loop in the workflow, they show:
- input and output guardrails with tripwires, handoffs, and agents as tools (OpenAI Agents);
- MCP servers registered on the worker and called by name;
- streaming through Workflow Streams (§16.20);
- human-in-the-loop with a Signal or an Update for the answer and a Query for the pending draft (LangGraph, Strands, Deep Agents), and the Strands "activity interrupt";
- continue-as-new after all handlers finish, with LangGraph caching completed node results across it and Deep Agents using the server's suggestion, "which accounts for both history length and size" (`deepagents_plugin/continue_as_new/README.md:7`);
- External Storage for a large tool result (Deep Agents);
- replay-safe gen_ai metrics (ADK, §16.19).

**SDK:** in 1.30.0, `contrib/openai_agents`, `contrib/strands`, `contrib/google_adk_agents` and `contrib/langgraph` (experimental); `contrib/deepagents` exists only in 1.34.0. The samples import the standalone packages.

**OpenCompany today:** none of the frameworks is used. Their ideas are covered where they apply: guardrails (§16.9), MCP tool lists (§16.12), streaming retry markers (§16.20), human waits (§16.10, §9.6), continue-as-new after all handlers finish (already required by RFC-0005 §10.1), External Storage (§16.8, not adopted).

**Verdict:** Not applicable.

**Recommendation:** none.

**Remarks:** The samples keep the conversation in workflow state; OpenCompany and RFC-0005 keep it in the app database (I10), which is what lets Temporal be rebuilt. LangGraph's result cache across continue-as-new is not needed: AgentWorkflow continues as new only between iterations and never while a delegation is in flight (`agent_workflow.py:2982-2996`).

### 16.16 Sample group: OpenRouter (budget gate and cache replay)

**Docs:** samples `openrouter/prompt_batch` and `openrouter/budget_gate`.
- prompt_batch (`samples/openrouter/activities.py`): client `max_retries=0`. 408, 429, 5xx and a transient in-flight-budget 402 are retryable; other 4xx are not. Running out of money is its own non-retryable type, `OpenRouterOutOfCredits` (a 402, or a 403 "Key limit exceeded"). Retry-After is honoured but capped at 5 minutes: "cap it rather than park a prompt for hours" (`:62-82`). Each request asks OpenRouter to cache a successful answer, so a byte-identical retry within the TTL "is served from cache and billed at $0" (`:193-197`). Heartbeats let a killed worker be noticed.
- budget_gate (`samples/openrouter/budget_gate/workflow.py`): a soft budget held in the workflow. Each call in flight reserves an estimate. When money runs out (the soft budget, or the provider's out-of-credits error) the prompt parks on `workflow.wait_condition(..., timeout=deadline)` instead of failing. `raise_budget` is an Update whose validator refuses to lower the budget; `spend_report` is a Query. "Snapshot the budget version before the call, not after it fails: a raise_budget that lands while this call is in flight must count as the top-up this prompt is waiting for" (`:157-161`).

**SDK:** Updates with validators, Queries and `wait_condition(timeout=...)`: 1.30.0.

**OpenCompany today:**
- Cache replay: adopted for gateway calls (§16.11); calls with the owner's own key have none, an accepted trade-off.
- A 2xx whose body is an error is a non-retryable PROTOCOL error, "answered without a completion" (`server/services/llm/providers/openai.py:144-170`). The sample classifies by the inner error code instead, so an outage behind OpenRouter is retried; RFC-0004 §13.3 lists the same fix.
- Exhaustion: the gateway's spent allowance (`budget_exceeded`) and billing signatures are non-retryable BILLING, Google quota is QUOTA (`server/services/llm/protocol.py:486-491`, `:888-911`). The run then fails (`agent_workflow.py:1331-1340`) and the deployment pauses at once because the error requires the owner's action (`handlers.py:2757`). The turn's input is not retried after Resume.
- RFC-0005 §14 plans the owner's daily budget in this sample's shape: an upper-estimate reservation keyed by activity id; `BudgetExceeded`, non-retryable and BENIGN, which suspends the turn; `resume_after_budget` and `set_limits` Updates with validators; a `status` Query.

**Verdict:** Planned in RFC-0005 (deviates). RFC-0005 §14 follows the budget gate for the owner's own budget, more strictly (upper estimates); provider-side exhaustion and outages still fail the turn, which T11 now rules out.

**Recommendation:**
1. Park instead of failing (T11). In AgentWorkflow runs of generations with `scheduler_version=1` (RFC-0005 Phase 5, T18), whose supervisor can send the release, an LLM step that fails for a reason that may clear parks the turn where it stopped:
   - `BudgetExceeded`, the employee's own daily budget (RFC-0005 §14): wait for `released{reason=budget}`, sent on top-up or when the budget period ends;
   - `LLMError.billing` and `LLMError.quota`, the gateway's `budget_exceeded` included: wait for `released`, sent on Resume or top-up;
   - `LLMError.authentication`, `LLMError.permission` and a missing key (`MissingAgentProviderCredential`): wait for `released`, sent on Resume after the owner fixes the key;
   - the step budget (`schedule_to_close_timeout`, §16.3 item 4) running out while the provider keeps failing with retryable errors, or a provider wait longer than the remaining budget (§11.3 item 4, §16.5): an outage park, which also tries again on a durable timer every 15 minutes, fixed (or after the provider's hinted wait when that is longer, §11.8), and on Resume;
   - other non-retryable categories (INVALID_REQUEST, NOT_FOUND, PROTOCOL, CONTEXT_LENGTH) and bugs (§11.4) still fail the turn: the frozen request would fail the same way after Resume.

   A parked turn runs no activity and holds no worker slot; its admission lease becomes `suspended` (RFC-0005 §13.3), and an attention item says why. Account and billing parks also pause the employee through the existing automatic-pause path, written as a T10 intent (§11.8), so new inputs wait in the inbox instead of parking one by one; outage parks do not. On release the turn runs the same step again under a new activity id, keeping its transcript and consumed inputs. A parked turn's time counts toward no deadline, the 24-hour chat-run cancel (1b.14) included; instead a turn stays parked at most 7 days, after which it fails and its input is dead-lettered with an attention item (T19, §11.8; RFC-0005 §10.2). Older generations keep today's unlimited LLM-step retries with the retry alert (1b.30, §11.7), today's fail-and-pause (T18) and the 24-hour cancel. Versioning: `scheduler_version=1` (never a patch on old generations). When: RFC-0005 Phase 5. Effort M. Risk: a parked customer turn may wait up to 7 days unanswered.
2. Snapshot the release version before each step, as the sample does, so a top-up that lands while the step is in flight releases the turn that is about to park. Part of item 1.
3. Classify a 2xx error body by its inner code (RFC-0004 §13.3). Versioning: none. When: RFC-0005 Phase 5, with §16.5. Effort S.

**Remarks:**
- Parking is the docs' Resumable Activity pattern (§11.8) applied to the LLM step; §11.8 holds the general mechanics, §11.3 the budget values and §11.4 the error classification.
- Under T19, the per-class turn deadlines of RFC-0005 §13.1 become "slow turn" alerts that do not count parked time. A turn is cancelled only after 24 hours with no completed step, not counting time parked, paused or waiting for the owner (a default, §24.1, §6.4). The 10-minute stuck-turn alarm of RFC-0005 §18.1 must not count parked time either.
- The gateway's replay namespace includes the activity id, so the re-run step after a park is paid again; the failed attempts delivered nothing, so nothing is paid twice.
- Test with time skipping: park on a 402, release, complete; park on a long 503, recover by the probe timer without Resume; a turn parked for 7 days is dead-lettered with an attention item; a generation without `scheduler_version=1` keeps today's fail-and-pause.

**Sketch** (item 1; RFC-0005 Phase 5, generations with `scheduler_version=1`):

```python
# AgentWorkflow  (RFC-0005 Phase 5; only for generations with scheduler_version=1, T18)
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutType
from temporalio.exceptions import TimeoutError as ActivityTimeout

PARK_PROBE = timedelta(minutes=15)                   # outage parks probe every 15 minutes, fixed (§11.8)
_RELEASE_PARKS = {"BudgetExceeded": "budget",        # these wait for `released`, with no probe timer
                  "LLMError.billing": "provider_account", "LLMError.quota": "provider_account",
                  "LLMError.authentication": "provider_account",
                  "LLMError.permission": "provider_account",
                  "MissingAgentProviderCredential": "provider_account"}

def _park_reason(err: ActivityError) -> tuple[Optional[str], Optional[timedelta]]:
    cause = err.cause
    if isinstance(cause, ApplicationError):
        if cause.type == "LLMError.wait_exceeds_budget":       # §11.3 item 4
            hint = timedelta(seconds=cause.details[0]["park_after_seconds"])
            return "provider_outage", max(PARK_PROBE, hint)   # a longer hint is the first probe
        return _RELEASE_PARKS.get(cause.type or ""), None
    if isinstance(cause, ActivityTimeout) and cause.type == TimeoutType.SCHEDULE_TO_CLOSE:
        return "provider_outage", PARK_PROBE         # the step budget ran out while retrying
    return None, None                                # everything else fails (§11.4)

@workflow.signal
def released(self, reason: str) -> None:             # Resume, top-up, key fixed
    self._release_version += 1

async def _step_or_park(self, llm_payload: dict, iteration: int) -> dict:
    rerun = 0
    while True:
        seen = self._release_version                 # before the call, as the budget gate does
        try:
            return await workflow.execute_activity(
                "agent.execute_llm_step", args=[llm_payload],
                activity_id=f"llm-step-{iteration + 1}" + (f"-r{rerun}" if rerun else ""),
                start_to_close_timeout=_attempt_timeout(llm_payload["provider"]),
                schedule_to_close_timeout=_step_budget(llm_payload),   # 15 or 45 min (§11.3)
                heartbeat_timeout=LLM_STEP_HEARTBEAT_TIMEOUT, retry_policy=LLM_STEP_RETRY)
        except ActivityError as err:
            reason, probe = _park_reason(err)
            if reason is None:
                raise
        rerun += 1
        await self._park(reason, iteration)          # attention item, lease suspended, turn_suspended
        try:                                         # §11.8 ends any park after 7 days (T19)
            await workflow.wait_condition(lambda: self._release_version > seen, timeout=probe)
        except asyncio.TimeoutError:
            pass                                     # outage probe: try the step again
        await self._end_park(iteration)              # waits for `running`, clears the row (§11.8)
```

### 16.17 Sample groups: Bedrock and AgentCore

**Docs:** samples `bedrock` and `bedrock_agentcore`. The entity sample (`samples/bedrock/entity/workflows.py`) keeps one workflow per chat: prompts arrive as Signals into a queue, `wait_condition` waits for the next one, the history lives in workflow state, and once the history reaches a fixed count of entries (`continue_as_new_per_turns = 6`) the workflow summarizes the chat and continues as new, carrying the summary and the queued prompts; Queries return the history and the summary. The AgentCore sample runs a Strands agent whose worker lives in AWS Bedrock AgentCore Runtime, launched by Temporal Serverless Workers, and needs "A Temporal Cloud namespace (or a self-hosted" cluster that AgentCore can reach) (`samples/bedrock_agentcore/strands_agent/README.md:19-24`).

**SDK:** Signals, `wait_condition`, `continue_as_new` and Queries: 1.30.0. The AgentCore sample needs the 1.34.0 standalone package.

**OpenCompany today:** RFC-0005's ConversationWorkflow (RFC-0005 §10.1-§10.2 and §11) is the same entity shape: Signal-With-Start by a known id from the relay, continue-as-new between turns after `workflow.all_handlers_finished()`, carrying cursors, not pending inputs (§7.4). Unlike the sample, it keeps the transcript in `agent_conversations` (I10), routes through the inbox (I1), and leaves compaction to AgentWorkflow. AgentCore needs Temporal Cloud or serverless workers, which E1 and E2 exclude (one free Temporal per owner); RFC-0004 §15 lists serverless workers as not adopted.

**Verdict:** Planned in RFC-0005 (follows the docs): the entity shape matches, and RFC-0005's invariants add what the sample cannot do, surviving a lost Temporal database and holding work for budgets.

**Recommendation:** none beyond RFC-0005 Phase 4.

**Remarks:** AgentCore is not applicable. The sample rolls over at a fixed count; RFC-0005 rolls over when the server suggests it, which also accounts for history size.

### 16.18 Sample group: Google GenAI

**Docs:** sample `google_genai`. Every Gemini SDK call runs as an activity through `TemporalAsyncClient`, including the Files API (`client.files.upload` runs as an activity and the workflow passes the returned handle) and Gemini's server-side stateful interactions; streaming goes through Workflow Streams.

**SDK:** `contrib/google_genai` exists only in 1.34.0 (experimental); the sample imports the standalone `temporalio.google_genai`.

**OpenCompany today:** the Gemini provider is a native SDK client called from the LLM step activity; images travel as workspace references and are read into each provider call (`server/services/llm/media.py`).

**Verdict:** Not applicable.

**Recommendation:** none. A provider Files API is a provider-side claim check, but each upload costs a round trip and expires on the provider; workspace references already keep the bytes out of history.

**Remarks:** none.

### 16.19 Sample groups: tracing and metrics (Langfuse, LangSmith, ADK metrics)

**Docs:** samples `langfuse_tracing`, `langsmith_tracing` and `google_adk_agents/metrics`.
- Langfuse: `OpenTelemetryPlugin`, an OTLP exporter and OpenAI instrumentation that records each completion as a GENERATION (model, tokens, cost), "duplicate-free across replay and worker restarts". The default OpenInference instrumentation records prompt and completion content on spans; the OpenTelemetry gen_ai flavour records content only when opted in (`samples/langfuse_tracing/telemetry.py:86-108`, `README.md:125`).
- LangSmith: `LangSmithPlugin`, `wrap_openai` and `@traceable`, with optional runs per workflow and activity.
- ADK metrics: `ReplaySafeMeterProvider` drops observations made during replay; the metrics are named `gen_ai.client.token.usage` and `gen_ai.client.operation.duration`; "Recordings are first-execution-only rather than exactly-once" (`samples/google_adk_agents/metrics/README.md:29-31`).

**SDK:** `contrib/opentelemetry` and `contrib/langsmith`: 1.30.0. `ReplaySafeMeterProvider`: only 1.34.0. `workflow.metric_meter()`, which skips replay: 1.30.0 (`temporalio/workflow/_context.py:736`).

**OpenCompany today:** OpenTelemetry tracing is registered once on the shared client (`server/services/temporal/client.py:96`); LLM attempts record content-free stage diagnostics (`server/services/llm/observation.py`); SDK metrics are off (`client.py:76-83`, `server/services/temporal/worker.py:91-102`) until 1b.38 adds the SDK metrics runtime (§17.10). RFC-0005 §18.5 plans OTLP metrics without content (E14).

**Verdict:** Not applicable for fleet export: the Langfuse and LangSmith setups export prompts and completions, which E14 forbids.

**Recommendation:**
1. Name OpenCompany's LLM metrics after the OpenTelemetry gen_ai conventions (`gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, with `gen_ai.request.model` and the provider; never prompt or content attributes), recorded in activities, where replay does not happen. Any OTLP backend then reads them. Versioning: none. When: RFC-0005 Phase 8. Effort S.
2. Optional: a local, opt-in Langfuse for developers on the desktop does not break E14; its instrumentation must drop message content, or RFC-0005 §18.5's content-filter test fails. Later.

**Remarks:** Workflow-side metrics, if any are added, use `workflow.metric_meter()`, which already skips replay in 1.30.0.

### 16.20 Sample group: workflow_streams

**Docs:** sample `workflow_streams`. A workflow hosts a `WorkflowStream`. Publishers (activities through `WorkflowStreamClient.from_within_activity()`, or outside code) send batched Signals: every 200 ms in the LLM sample (`activities/llm_activity.py:39`), 100 ms by default in the framework plugins. Subscribers long-poll with Updates from an offset and can reconnect; `truncate()` bounds the log. On a retried attempt the LLM activity first publishes a `RetryEvent`, so subscribers erase the failed attempt's text (`:28-30`). The OpenAI Agents plugin's streaming activity "publishes no retry marker" (`samples/openai_agents/streaming/README.md:124`).

**SDK:** `temporalio.contrib.workflow_streams` exists in 1.30.0 and is identical in 1.34.0 apart from line endings; experimental in both.

**OpenCompany today:** `ChatStreamEmitter` in the LLM step publishes token deltas to the in-process `ChatRunHub` (`server/services/chat/hub.py:1-25`), or through `/ws/internal` from a standalone worker. The hub has a stream's properties without history: a per-run `seq` and a process `epoch` (offsets); an `event_key` dedupe so "a retried activity publishes once"; a snapshot of each live run for late subscribers; bounded per-socket queues with an `opencompany.resync` frame instead of a hole. Segment ids are `<run>.<iteration>.<attempt>`, with `opencompany.segment_discarded` for earlier attempts (the retry marker), and text that could still be `NO_REPLY` is held back (`server/services/chat/stream.py:9-22`, `:50-51`, `:100-114`). Nothing enters history.

**Verdict:** Adopted with deviations: an equivalent design that keeps tokens out of history, with Workflow Streams unused on purpose (RFC-0004 §13.2 and §15; RFC-0005 §10.4).

**Recommendation:** none now. If a durable, coarse progress log is ever wanted (turn started, tool step done, reply ready), a Workflow Stream on the conversation entity could carry it, never tokens. Reconsider when Workflow Streams leaves experimental status. Later.

**Remarks:** A token stream in the agent's own history would cost about 5 Signals per second of output (est., at a 200 ms batch) plus one Update per subscriber poll, against the per-run caps of 10,000 Signals and 2,000 Updates and RFC-0005 EG7 (fewer than 1,000 Signals per entity run). The hub loses its live snapshot on a backend restart; the client then takes a fresh snapshot from the saved reply, and the step's next attempt streams a new segment, the outcome the sample's `RetryEvent` produces.

## 17. Best practices

**Sources.** Two parts of docs.temporal.io, read as raw markdown on 2026-10-24:
- **The best-practices section** (https://docs.temporal.io/best-practices): the index and 12 pages.
  - **New since 2026-10-16 (four pages):** Managing Actions per Second (APS) limits (17.4), Cost governance (17.6), Managing Temporal Cloud access control (17.7) and Knowledge hub (17.13).
  - **Changed:** Multi-tenant application patterns, whose recommendation flipped from a Task Queue per tenant to "Single Task Queue with Fairness (Recommended)"; Security controls, which adds Temporal Proxy and Workflow History Export; Worker deployment, wording only.
  - **Unchanged:** the index, cost optimization, error handling, namespace, pre-production testing and worker alerting.
- **The Python SDK best practices** (https://docs.temporal.io/develop/python/best-practices): the index and five pages (debugging, error handling, the sandbox, sync vs async, the testing suite). They differ from the 2026-10-16 copies only in their titles. The index also links "Data handling", which is outside this section.

**File references** in §17 and §18. Paths are relative to the repository root. A bare file name is the file of that name already cited in the same entry; otherwise it is under `server/services/temporal/`, except `handlers.py`, `manager.py` and `control.py` (`server/services/deployment/`), `base.py` and `scaling.py` (`server/services/plugin/`), `node_executor.py` (`server/services/`), `main.py` (`server/`) and test files (`server/tests/temporal/`). SDK files are cited as `temporalio/...` in the installed 1.30.0 tree (`server/.venv/Lib/site-packages/temporalio/`) unless marked 1.34.0; samples by folder at commit 33f7dc29.

**Which pages bind OpenCompany.** The index's scope note: "Namespace best practices, Worker deployment, and error handling apply to self-hosted Temporal too; security controls, access control, cost optimization, cost governance, and APS limits are Cloud-specific." The worker-alerting page says it applies to self-hosted servers too. This RFC treats the namespace, worker deployment, worker alerting, error handling, pre-production testing and knowledge-hub pages, and all Python pages, as rules. It applies the methods of the Cloud pages through this translation:

| Temporal Cloud concept | OpenCompany equivalent |
|---|---|
| Namespace per tenant; the 10,000-namespace ceiling | One Temporal server per owner instance (RFC-0005 E1), stronger isolation than a namespace. About 10,000 instances, no shared cluster. |
| APS (actions per second) limit and throttling | The dev server's single-writer SQLite on one history shard (tier S), or 64 shards on PostgreSQL (tier L). SQLite contention shows up as `context deadline exceeded` (the reason for the WAL pragmas, `server/services/temporal/_runtime.py:127-142`), not as graceful throttling. Moving from tier S to tier L is the analog of buying capacity. |
| Billable actions and storage | History events and SQLite writes per turn; the size of `temporal.db` and its backups. |
| Cost governance | No Temporal bill. The money is LLM spend, governed by the daily budgets of RFC-0005 §14. |
| SAML, RBAC, API keys, mTLS | None. The dev server has no authorizer and listens on loopback. |
| Tenant | Inside an instance: an employee (the fairness key) and a conversation (an entity). Across the fleet: an owner (an instance). |
| On-call paging | Desktop: attention items for the owner. Hosted: fleet telemetry, metrics and errors only (E14), for the operator. |
| Deploys and rolling restarts | The weekly window for hosted instances (E13), desktop upgrades the owner installs, and laptop sleep and wake. |

### 17.1 Best practices (index)

**Docs:** https://docs.temporal.io/best-practices. A map in four groups: namespace, tenancy and capacity; security and access control; worker and workflow reliability; organizational enablement. Its scope note limits most pages to Temporal Cloud.

**SDK:** none.

**OpenCompany today:** not applicable; 17.2 to 17.13 review the pages one by one.

**Verdict:** Not applicable. It is an index.

**Recommendation:**
1. Record the split above (rules vs translated methods) in this RFC. Versioning: none. When: with this RFC. Effort S.

**Remarks:** The index says the APS page covers "when to use Provisioned Capacity". OpenCompany's analog is the move from tier S to tier L (RFC-0005 §20); 17.4 says what should trigger it.

### 17.2 Namespace best practices

**Docs:** https://docs.temporal.io/best-practices/managing-namespace. A namespace is the unit of isolation inside one Temporal server: workflow ids, histories, search attributes and the retention period (how long a closed run's history is kept) live per namespace. The page:
- "Start with the smallest number of Namespaces that gives you clear ownership and safe isolation"; "As a default, start with one Namespace per use case and environment." Split later for APS pressure, credentials, ownership or blast radius.
- Names: lowercase with hyphens, `<use-case>-<domain>-<environment>`; open-source names are case-sensitive.
- "You must register a Namespace before setting it in your Client."
- On open source, "If an Authorizer is not set, Temporal uses the `nopAuthority` authorizer that unconditionally allows all API calls." Deletion protection, high availability and Terraform are Cloud features.
- Descriptions, tags and search attributes are not encrypted: no secrets or personal data in them.
- **Docs inconsistency:** the page still says "For most SaaS use cases, a shared Namespace with per-tenant Task Queues is simpler and more scalable", which the updated multi-tenant page (17.3) now contradicts.

**SDK:** `Client.connect(namespace=...)` (1.30.0). Retention and search attributes are server settings, reached through raw RPCs present in 1.30.0 and 1.34.0: `client.workflow_service.update_namespace` and `client.operator_service.add_search_attributes`. The dev server also takes `--search-attribute Name=Type`, applied only when it creates the namespace.

**OpenCompany today:**
- One namespace per instance: `TEMPORAL_NAMESPACE=default` (`.env.template:89`). The dev server creates it with `--namespace` (`server/services/temporal/_runtime.py:143-165`) only if it does not exist (`createNamespaceIfNotExists` in the server's SQLite setup, which the CLI embeds).
- Retention is never set. No server code calls `update_namespace` or sets retention (grep). The dev server creates the namespace with 24 hours (CLI setup source for server 1.27 to 1.32). `.env.template:147-150` still says history is "always preserved across restarts (Temporal UI keeps showing every workflow that ever ran)".
- Seven custom search attributes are registered after connect through `add_search_attributes` (`server/services/temporal/search_attributes.py:128-183`). A failure is logged as "non-fatal" (`server/services/temporal/client.py:125-137`), but every start that sets them then fails (RFC-0004 M7). The argv passes no `--search-attribute`.
- The standalone worker hard-codes `namespace="default"` and `task_queue="machina-tasks"` instead of reading the settings (`server/services/temporal/worker.py:758-763`, `:1021`).
- Search attribute values carry no personal data: `EventWorkflowId` is a UUID.

**Verdict:** Adopted with deviations. One namespace per instance is exactly the docs' default (one use case, one environment, one owner). Retention and search-attribute registration at creation are missing; both stay deferred to RFC-0005 Phases 5 and 9a, as decided on 2026-10-23 (RFC-0004 D4; T17).

**Recommendation:**
1. Set retention to 7 days (RFC-0004 D4, RFC-0005 §25.1). In `TemporalClientWrapper.connect` (`client.py`), after the existing `describe_namespace` call, compare the namespace's `workflow_execution_retention_ttl` with a new `TEMPORAL_RETENTION_DAYS` setting (default 7 in `.env.template`) and call `workflow_service.update_namespace` when they differ. Versioning: none. When: deferred to RFC-0005 Phases 5 and 9a, as decided on 2026-10-23 (RFC-0004 D4; T17); the wrong comment at `.env.template:147-150` is corrected now (17.13 item 1). Effort S. Risk: the first boot after the change lets the server delete closed runs older than 7 days; retention applies only to runs that close afterwards.
2. Register the seven search attributes when the namespace is created: add `--search-attribute <Name>=Keyword` for each to the argv in `_runtime.py`, built from the same list as `search_attributes.py`. The flag applies only when the namespace is created, so the operator call stays for databases created before the change, including those the fresh-start release creates (RFC-0004 §6.6), and its failure should stop Temporal startup with the reason, because every start depends on the attributes. Versioning: none. When: deferred with item 1 to RFC-0005 Phases 5 and 9a (RFC-0004 D4; T17). Effort S. Risk: a registration failure then blocks the engine instead of failing every start later.
3. The standalone worker reads `TEMPORAL_NAMESPACE` and `TEMPORAL_TASK_QUEUE` like the embedded one: the one worker builder reads both from settings for every Worker (§15.3, 1b.36). Versioning: none. When: Phase 1b (1b.36). Effort: in 1b.36.
4. Tier L: the deploy logic creates the namespace with `temporal operator namespace create --retention 7d` and registers the search attributes before workers start. Versioning: none. When: RFC-0005 Phase 9c. Effort S.
5. Keep the name `default`. Renaming buys nothing inside a dedicated server and moves every id. When: never.

**Remarks:**
- With 24-hour retention, a run that closed yesterday is gone from the Web UI. The app's own run records are the durable view (RFC-0005 I10).
- Retention does not affect the replay corpus (files) or the upgrade-time replay (17.12), which replays open runs only.
- The page's reason to split a namespace, a workload that "regularly threatens others in the same Namespace", never arises inside one instance; the class queues of RFC-0005 §13 do that job.
- Under T5 every install runs server 1.32 after the first pinned release, so no per-install check of server features is needed.

### 17.3 Multi-tenant application patterns

**Docs:** https://docs.temporal.io/best-practices/multi-tenant-patterns. How several tenants share one Temporal deployment. Since 2026-10-16 the page says: "Start with a single Task Queue with Fairness, and choose a pattern with stronger isolation only when your tenants need it." Fairness means the server dispatches backlogged tasks across fairness keys (here: tenants) in proportion to their weights, so one tenant's burst cannot starve the others.
- Pattern 1, "Single Task Queue with Fairness (Recommended)": fairness keys, per-key weights and rate limits; onboarding needs no new Workers. Cons: "Fairness is probabilistic", and "The fairness weight applies at schedule time, not at dispatch time".
- Pattern 2: a Task Queue per tenant, for compute isolation or dedicated capacity. Pattern 3: shared workflow queue, per-tenant activity queues for heavy activities.
- Pattern 4: a Namespace per tenant: "Most teams find this manageable for fewer than 50 tenants"; "not a good fit if you expect a very large number of tenants (10,000+)".
- Principles: "Test at scale - Performance testing must drive your capacity decisions"; watch "metrics capacity and cardinality"; "Use Workflow/Activity timeouts aggressively" against noisy neighbors.
- A tenant search attribute for operations queries.

**SDK:** `temporalio.common.Priority(priority_key=, fairness_key=, fairness_weight=)` in 1.30.0 (`temporalio/common.py:1165-1188`; a fairness key is at most 64 bytes, a weight is clamped to 0.001-1000), accepted on workflow starts, Schedule actions, Signal-With-Start, activities and children. Unkeyed children and activities inherit the parent's key; `workflow.continue_as_new` cannot set it in 1.30.0 (the new run inherits). Per-key rate limits and weight overrides are server-side (`temporal task-queue config set --fairness-key-weight ...`, at most 1,000 overrides). Both need server 1.31 or later (RFC-0005 §13.2); fairness also needs `matching.enableFairness=true`.

**OpenCompany today:**
- **Across owners:** one instance per owner (E1) goes beyond pattern 4: a separate server, not a namespace. The page's warnings (overhead, a Worker pool per tenant, cost at 10,000+ tenants) are accepted by the owner and countered by automation: the deploy logic and the weekly window.
- **Inside an instance:** one workflow queue `machina-tasks` and plugin activity queues (`server/services/plugin/scaling.py:17-31`). No `Priority` and no fairness key anywhere (grep). LLM steps run on `machina-tasks` (`server/services/temporal/agent_workflow.py:3185-3188` sets no `task_queue`). Nothing stops one employee's burst from taking the shared slots; only per-run caps exist (max iterations, subagent permits).
- **RFC-0005 §13:** class queues `llm-c1` to `llm-c4` for hard isolation between scheduling classes, Priority keys 1-5, fairness key = employee id with weight 1, a per-employee admission cap of 4 plus a reserved owner slot, and a shared custom slot supplier with floors, which §13.3 (rec 5) replaces with fixed-size suppliers and admission as the only floor (RFC-0005 §13.2 as amended, §20).
- **Tenant search attribute:** `EventWorkflowId` plays `TenantId` (`search_attributes.py`), set on roots but not on agent children (`server/services/temporal/workflow.py:820-828` passes only args and id).

**Verdict:** Planned in RFC-0005 (follows the docs). Its design is the page's new pattern 1 (fairness by employee inside a queue) plus pattern 2 only where hard capacity isolation is needed (classes). One conversation entity runs one turn at a time (RFC-0005 I4), so one noisy customer holds at most one slot.

**Recommendation:**
1. Fairness and one task-queue partition on the dev server: `matching.enableFairness=true` and one read and one write partition ship in the fresh-start release (Phase 1, T17). Priority and fairness hold only within a partition: the priority docs say "lower-priority Tasks on a lighter partition can dispatch ahead of higher-priority Tasks waiting on a heavier one", and the fairness pattern page suggests "a single-partition Task Queue" where accuracy matters (https://docs.temporal.io/design-patterns/fairness). §13.4 (rec 1) owns the change, its reasons and its risk.
2. Drop the version hedge in RFC-0005 §13.2 ("Without them behaviour stays correct, only unfair"): §13.4 (rec 4).
3. Never put employee, conversation or thread ids into metric labels (the page's cardinality warning). Per-employee views come from the app database (`employee_timeline`, RFC-0005 §18.4). Versioning: none. When: from the first metrics (1b.38, 17.10), and in RFC-0005 Phases 5 and 8. Effort S.
4. An owner setting "this employee matters more" can become a fairness weight override per key, without a redeploy. Versioning: none (server configuration). When: later. Effort S.
5. Set `EventWorkflowId` on agent children too. Replay compares only a child's id and type, so no patch is needed; the replay corpus must pass. When: later. Effort S.
6. **Default** (the owner did not answer whether the fleet needs a control plane): no fleet-level Temporal. The page's advice to run tenant onboarding as a Temporal workflow is for tenants that share a namespace, not for separate servers. Onboarding and the weekly upgrade stay in the deploy logic: each hosted instance takes its pinned release in the window, snapshots, migrates, replays its open runs (17.12), restarts and rolls back on its own (RFC-0005 §21.2); the central side only receives telemetry. Reasons: E1 and the owner's rule against shared clusters, and no single point of failure across about 10,000 instances. Versioning: none. When: RFC-0005 Phase 9a. Effort: part of 9a. Risk: per-instance upgrades are harder to watch centrally; the window marker in telemetry (17.6) covers it.

**Remarks:**
- The server keeps fairness state for the top 100 keys across a restart (task-queue priority and fairness page). A tier L instance with 1,000 employees loses ordering state for the rest at each weekly restart; acceptable.
- Fairness only orders the backlog and ignores how long a task runs. Long LLM activities hold slots, so admission (RFC-0005 §13.3: the per-employee cap and the reserved owner slot), not fairness, protects the owner's chat; §13.3 (rec 5) makes admission the only floor.
- §13.3 (rec 5) drops RFC-0005's custom slot supplier. If one is ever built, its `reserve_slot` "is called before polling for new tasks", "must block until a slot is available", and may throw only `asyncio.CancelledError` (`temporalio/worker/_tuning.py:165-169`). It runs on the worker's event loop: keep its state in memory and never read the database from it.
- `worker_task_slots_available` is not meaningful with resource-based suppliers (17.10); assume the same for a custom supplier (unverified) and alert on schedule-to-start latency instead.

### 17.4 Managing Actions per Second (APS) limits (new page)

**Docs:** https://docs.temporal.io/best-practices/managing-aps-limits. Every state change is an action, and "a single Workflow Execution generates multiple actions" ("The action multiplier effect"). The page lists causes of spikes and how to design them out:
- Bursty traffic, the recovery "thundering herd", retry storms, and "Timer Storms: a large number of Workflows all set a Timer for the exact same time".
- Fan-out through children; human-in-the-loop at scale: "Avoid polling patterns where UIs constantly query Workflow state. Instead, push state changes to a database that UIs can read."
- Timers: "consolidate Timers", "have an external system signal your Workflow rather than using short-lived Timers to poll", "Use longer monitoring intervals".
- Mitigations: application-level queuing, staggered starts, jitter, accepting rate limits, and "Set alerts at 70-80% utilization". The planning question: "What happens to APS consumption when the number of Actions * number of active Workflows scales to 100x current volume?"

**SDK:** nothing specific. `ScheduleSpec(jitter=...)` (`temporalio/client/_schedule.py:310`), `start_delay` on starts (`temporalio/client/_client.py:414`) and `Priority` are in 1.30.0.

**OpenCompany today (capacity of the dev server, not a bill):**
- **Measured cost per turn**, counted from the committed N-1 replay corpus (`server/tests/temporal/fixtures/0.3.1/`). The recorder used stub activities, small prompts and no sticky cache, so these are orchestration costs, not live measurements:

  | Scenario | Events | Activities | Children | Workflow tasks |
  |---|---|---|---|---|
  | `machina_chat_agent`: owner chat, no controller (MachinaWorkflow + AgentWorkflow; 2 LLM steps, 1 tool) | 50 + 83 = 133 | 5 + 12 = 17 | 1 | 8 + 13 = 21 |
  | `controller_push`: one push event, console node | 96 | 8 | 1 | — |
  | `agent_task_manager`: a lead delegates once (3 histories) | 210 | 29 | 2 | 34 |
  | A chat turn in a deployed generation (est.): the chat turn plus the controller's share (about 42 events, 3 activities, 1 Signal, 2 Updates) and `execution_control.register`/`unregister` (2 activities, about 12 events) | about 185 | about 22 | 2 | — |

- In the chat turn, 4 activities do the user's work (2 LLM steps, the tool, `chatReply`). The other 13 are bookkeeping: `agent.broadcast_progress` 6 times, `prepare_payload`, `store_output`, `skill.clear`, `chat_run.start` and `finish`, `store_node_output_activity` and `workflow_runs.record_completion`. A broadcast costs 6 history events (3 for the activity, 3 for the workflow task that follows); progress broadcasts are 36 of the AgentWorkflow's 83 events (43%) here and 35-52% across the corpus's 10 AgentWorkflow histories. The count is 1 at start, 1 per LLM step, 2 per tool call and 1 at the end (`agent_workflow.py:3216-3248`).
- **Against RFC-0005 §20:** its model is 2,600 turns and about 35k actions a day for 20 employees, about 13.5 actions per turn. The fixture turn is 19 actions (17 activities, a child start, a workflow start), 1.4x that. A deployed turn is about 27 (22 activities, 2 child starts, 1 Signal, 2 Updates), about 2x, before upserts, markers and heartbeats. Tier S's "about 50 Temporal actions/s" peak is likely about 100 (est.).
- **Timer storms and polling timers in the RFC-0005 design as first written** (§20 records the amendments):
  - The daily budget resets at midnight in the owner's time zone (E11, RFC-0005 §14). Every instance has one owner, so 100 to 1,000 period timers fire in the same second, each releasing held inputs and signalling suspended turns: the page's timer storm.
  - Each supervisor has a 5-minute upkeep timer (RFC-0005 §10.2): an idle employee wakes 288 times a day, and 100 employees make about 0.33 wakes a second of constant load. That contradicts RFC-0005's own principle "Sleep by default", grows history until a daily continue-as-new, and on the desktop's 50-entry sticky cache (`worker.py:76-88`) means a replay of the supervisor's history on most wakes (est.).
  - The entity's "admission retry every 60 s" (RFC-0005 §10.2) is a polling timer behind the `wake` Signal.
  - Today's Schedules have no jitter (`server/services/temporal/schedules.py:212-221`); RFC-0005 §15 adds 60 s for cron.
  - Polls: T7 replaces "one Schedule per poll". With Schedules, 100 employees with 2 polls each at the plugins' default 60 s interval would start about 288,000 executions a day (est.), each with its own history and Visibility row.
- **UI reads by Query:** every `get_workflow_control_status` on an active generation runs a Query on the controller (`server/services/deployment/handlers.py:1980-1989` to `_reconcile_control` `:1221-1242` to `handle.query("status")` `:733`). A Query (a read-only call answered by workflow code) runs a workflow task on the worker, with a full replay on a cache miss. The chat and approval UIs already read the database.
- **Fan-out depth:** under RFC-0005 a turn is entity, then MachinaWorkflow, AgentWorkflow, DelegatedTaskWorkflow and the teammate's AgentWorkflow; each child costs a start, its own workflow tasks and a history.

**Verdict:** Missing and beneficial. The page's method (count actions per execution, multiply by concurrency, design out multipliers, alert at 70-80% of capacity) is the capacity model OpenCompany lacks; RFC-0005's tier figures are estimates, and the corpus puts them about 2x low.

**Recommendation:**
1. A script under `server/tests/temporal/` counts events, activities, children, Signals, Updates and workflow tasks per fixture scenario, and the release check prints the table for each new `fixtures/<version>/`. RFC-0005 §20 then states capacity in events and workflow tasks per second, from these counts and EG14, instead of the 35k-actions estimate. Versioning: none. When: Phase 1b (1b.44). Effort S. Risk: none; it reads files. Test: on `fixtures/0.3.1/machina_chat_agent.json` it reports 133 events, 17 activities, 21 workflow tasks and 1 child.
2. Progress broadcasts stop costing six events each: §14.2 (rec 1) runs them as best-effort local activities, and new RFC-0005 types schedule no UI-only activity at all (§14.2 rec 3). `workflow.set_current_details` (served by a Query, no history events) can also carry coarse state. Versioning, timing and risk: §14.2.
3. Spread the budget-period timer: each supervisor waits until midnight in the owner's time zone, computed outside workflow code (§8.4), plus a stable per-employee offset of 0 to 10 minutes (sketch), and held inputs are released in (class, seq) order as RFC-0005 §14 says. Versioning: `scheduler_version=1` (Phase 5 behaviour in the supervisor that Phase 4 introduces). When: RFC-0005 Phase 5. Effort S. Risk: an employee resumes up to 10 minutes after midnight; the notice says so.
4. No upkeep timer per supervisor: §8.4 replaces it with one instance-wide sweep that signals only the supervisors and entities with overdue work, which removes the 288 wakes a day per idle employee counted above. Versioning and timing: §8.4.
5. Read control status from the control row and its projection; Query the controller only for an explicit reconcile, with a deadline. This matches T10, where owner actions write an intent row first and the UI shows "applying". Versioning: none. When: RFC-0005 Phase 4. Effort S. Risk: the row lags the controller by one delivery; T10's deliverer closes the gap.
6. The tier S to tier L move triggers on measured load (workflow-task schedule-to-start p99, events per second against the EG14 ceiling, at 70-80%), not only on an employee count. Versioning: none. When: RFC-0005 Phase 9c. Effort S. Risk: needs the metrics of 17.10.

**Remarks:**
- The dev server does not throttle gracefully the way the page describes Cloud ("increased latency, not lost work"): SQLite contention surfaces as `context deadline exceeded`, which clients must retry.
- Heartbeats are actions on Cloud; here they are throttled SQLite writes. They stay (17.5).
- Fixture counts understate real turns: prompts are small and each extra tool call adds 3 broadcasts (18 events). History bytes per run (30-52 KB in the fixtures) grow with transcripts, which stay in history under T6.
- T7 keeps poll history near zero: a poll's retries are not recorded in history.

**Sketch** (the supervisor, RFC-0005 Phase 5 under `scheduler_version=1`; 1.30.0):

```python
# services/temporal/supervisor_workflow.py (sketch): the budget period ends at
# local midnight plus a stable per-employee offset, so the 100-1,000 supervisors
# of one owner do not all wake in the same second.
import asyncio
import hashlib
from datetime import datetime, timedelta

from temporalio import workflow

_PERIOD_SPREAD_S = 600

def period_offset(employee_id: str) -> timedelta:
    # sha256 is stable across processes and replays; Python's hash() is not.
    digest = hashlib.sha256(employee_id.encode("utf-8")).digest()
    return timedelta(seconds=int.from_bytes(digest[:4], "big") % _PERIOD_SPREAD_S)

def start_period(self, midnight_utc: datetime) -> None:
    # midnight_utc: the period's end, local midnight as a UTC instant, computed
    # outside workflow code by the function that keys the ledger's day (§8.4)
    # and carried across continue-as-new, so a later release's time-zone data never
    # changes a timer that is already running. A supervisor rebuilt in place (T20)
    # gets it again the same way.
    self._period_due = midnight_utc + period_offset(self.employee_id)

async def wait_for_period_end(self) -> None:
    remaining = self._period_due - workflow.now()
    if remaining <= timedelta(seconds=1):      # timers are accurate only from 1 s up (T9)
        return
    try:
        await workflow.wait_condition(lambda: self._budget_changed, timeout=remaining)
    except asyncio.TimeoutError:               # the period ended: reset the day
        pass
```

### 17.5 Workflow cost optimization

**Docs:** https://docs.temporal.io/best-practices/cost-optimization. "Build Workflows following best practices first, then optimize based on observed costs." The page names anti-patterns and levers:
- Anti-patterns: "Premature Activity consolidation" (split by failure boundary and retry needs), local activities everywhere, missing continue-as-new, high retry volume, large payloads in history, over-optimizing at the expense of observability, and excessive heartbeats: "Only use Heartbeats for long-running Activities (10+ minutes)".
- Actions: a child workflow costs 2 actions against 1 for an activity; search attributes set at start are free, upserts are not; "Use `SignalWithStart` instead of separate `StartWorkflow` and `SignalWorkflow` calls".
- Storage: continue-as-new before long sleeps, compression with a custom data converter, the claim check, retention.

**SDK:** `PayloadCodec` and `DataConverter` (1.30.0); `start_delay`; `ApplicationError(next_retry_delay=...)`. Standalone Activities are GA in SDK 1.33 and server 1.32.

**OpenCompany today:**
- **Follows the page:** media and files move by reference (`server/services/media/`); every long-lived type continues as new; LLM retries start at 5 s and honour Retry-After (`server/services/temporal/_retry_policies.py:94-100`, `agent_activities.py:285-303`); the Workspace controller uses Update-With-Start.
- **Deviates:**
  - UI-only bookkeeping activities: the progress broadcasts of 17.4, each with its own retry policy (3 attempts, `agent_workflow.py:184`). A broadcast that fails three times raises into the agent run, because `_emit_phase` awaits it without a try (`agent_workflow.py:3236-3273`): a cosmetic failure can end real work.
  - Every LLM step input carries the whole transcript, the tool definitions and the system prompt (RFC-0004 M11). T6 keeps that by decision, bounded by the byte budget, compaction and continue-as-new.
  - Every node activity input carries the graph and the full parameter snapshot (RFC-0004 M6, `workflow.py:711-720`).
  - The controller upserts `ControlEventTypes` as triggers register (`server/services/temporal/workflow_control_workflow.py:718-742`); it exists only for Visibility routing, which RFC-0005 removes (I6).
- **Heartbeats:** node activities beat every 30 s whenever their start-to-close exceeds the heartbeat timeout (`server/services/plugin/base.py:1248-1264`). This is deliberate: with no workflow timeouts, heartbeats are the only liveness signal after a laptop sleeps or the process dies.
- Retention unset (24 h); no codec.

**Verdict:** Adopted with deviations.

**Recommendation:**
1. Keep the heartbeats. The page's 10-minute rule is about billing; here heartbeats detect dead workers. Do not add them to short bookkeeping activities. When: never change.
2. Progress broadcasts stop being remote activities (17.4 item 2, §14.2). Never merge activities that have different retry semantics, the page's first anti-pattern; a UI broadcast has none, so folding it into the activity next to it is also allowed (the alternative §14.2 names). Versioning, timing and effort: §14.2.
3. Pass graphs and parameter snapshots by reference: RFC-0005's turn loads the graph by generation and trigger node (RFC-0005 §10.2, fixing RFC-0004 H10 and M6). Versioning: per-generation flag (`event_os_version=1`). When: RFC-0005 Phase 4. Effort M. Transcripts stay in history (T6); relief before every LLM step, a firing's first step and conversations without tool calls included, keeps a chat-only conversation from growing until `ConversationTooLarge` (§16.8).
4. Drop the `ControlEventTypes` upsert together with Visibility routing. Versioning: removes an upsert command, so only for generations on the new path. When: RFC-0005 Phase 10. Effort S.
5. No compression codec. Every history written with one then needs it forever, and the replay gate must use the same converter. References are the lever. When: never, unless EG14 shows storage pressure.
6. Retention: 17.2 item 1 (deferred to RFC-0005 Phases 5 and 9a, T17).

**Remarks:**
- A codec changes payload metadata, not commands, so it needs no patch; old histories must still decode (codecs pass through payloads without their encoding marker).
- The cosmetic broadcasts also cost latency on the chat path (about 0.32 s before the first model call in the corpus; §14.1).

### 17.6 Cost governance on Temporal Cloud (new page)

**Docs:** https://docs.temporal.io/best-practices/cost-governance. Attribute spend with namespace tags set at creation; forecast month-end from month-to-date; alert at 90, 100 and 110% of budget; use the minute-level billable-action metric (by `action_type` and `temporal_workflow_type`) as an early, directional signal and reconcile with the bill; detect anomalies against a seasonal baseline ("Alert if the number of Actions for an hour exceeds the expected maximum for all hours in a day"); correlate with deploy markers; plan metric cardinality from day one.

**SDK:** none (Cloud Billing API, OpenMetrics).

**OpenCompany today:** no Temporal bill. The same method already shapes RFC-0005 §14 for LLM spend: a reservation at an upper estimate, settled with the actual (the page's "directional estimate", reconciled once the numbers are final), a soft threshold at 80% and a hard stop at 100%.

**Verdict:** Not applicable to Temporal billing. Its anomaly method fits fleet telemetry.

**Recommendation:**
1. Fleet telemetry (E14) carries the release version and a window marker as low-cardinality tags, so a jump in activity or LLM-call rate per workflow type after an upgrade shows as a regression; an hourly event or activity count far above an instance's own normal maximum flags a runaway loop or retry storm. Keep per-instance series few (an opaque instance id, tier, release); never per employee. Versioning: none. When: RFC-0005 Phase 9a. Effort S.

**Remarks:** the page's advice to use an idempotency key for billing reports has no counterpart here.

### 17.7 Managing Temporal Cloud access control (new page)

**Docs:** https://docs.temporal.io/best-practices/cloud-access-control. Standardize on API keys with Service Accounts, or mTLS for clients plus API keys for automation; one Service Account per service; namespace-scoped access; rotate with dual credentials (create, migrate, validate, remove); "self-signed certificates are a valid and commonly used approach, even in production"; Let's Encrypt is not recommended for mTLS.

**SDK:** `TLSConfig`, API keys and `temporalio.envconfig` exist in 1.30.0; OpenCompany's `Client.connect` passes none (`client.py:92-97`).

**OpenCompany today:** no Cloud. Workers and clients reach a loopback server without credentials. The credentials that exist are the fleet telemetry exporter's (E14) and the app's own provider keys, which are outside Temporal.

**Verdict:** Not applicable.

**Recommendation:**
1. If tier L adopts mTLS, the deploy logic generates a per-instance self-signed CA and rotates certificates in the weekly window with the page's dual-credential sequence. Versioning: none. When: RFC-0005 Phase 9c, only if adopted. Effort M. Risk: an expired certificate stops the instance's workers; rotation must run before expiry.
2. Apply the page's key hygiene to the telemetry collector: one key per instance, rotation, revocation on anomalies. Versioning: none. When: RFC-0005 Phase 9a. Effort S.

**Remarks:** none beyond 17.8.

### 17.8 Security controls

**Docs:** https://docs.temporal.io/best-practices/security-controls (Cloud), pointing self-hosted users to https://docs.temporal.io/self-hosted-guide/security. The rules that carry over:
- Least privilege and machine identities; mTLS or API keys; private connectivity.
- Client-side encryption with a payload codec (an SDK hook that encrypts every payload before it leaves the process; you own the keys, their rotation and a codec server for the Web UI). New on 2026-10-24: Temporal Proxy, which "encrypts payloads in your environment before forwarding them to Temporal Cloud".
- "Temporal's default behavior copies error messages and call stacks as plain text"; encrypt them with a failure converter.
- Retention as a control: "Shorter retention means completed workflow data (history, payloads) is purged sooner". Workflow History Export (new) for retention past 90 days on Cloud.
- Self-hosted: without an explicit authorizer, the server allows every API request from anyone who can reach it.

**SDK:** `PayloadCodec` (`temporalio/converter/_payload_codec.py:12`), `DataConverter`, and `DefaultFailureConverterWithEncodedAttributes` (`temporalio/converter/_failure_converter.py:463`), all in 1.30.0 and 1.34.0. A codec server is a separate HTTP service (dev server flag `--ui-codec-endpoint`).

**OpenCompany today:**
- **Follows the page:**
  - Provider keys are resolved inside the LLM activity and never enter workflow state (`server/services/temporal/agent_activities.py:176-243`).
  - The LLM error boundary sends a category message and structured details instead of raw provider text (`agent_activities.py:246-303`).
  - The generation snapshot is sanitized when a generation begins (`server/services/deployment/control.py:117-127`, using `sanitize_runtime_payload` in `server/services/workflow_sanitizer.py`), and listener `filter_params` are sanitized (`server/services/deployment/manager.py:903-905`).
  - The webhook trigger's expected header value is read at the edge from the saved parameters (`server/nodes/trigger/webhook_trigger/_routes.py:49-57`).
- **The Start path breaks the rule (code read, not run):**
  - At Start, `deploy_data["parameters_by_id"]` is the raw saved parameters (`server/services/deployment/handlers.py:2141`, loaded at `:2063-2075`), passed on as `parameter_snapshot` (`handlers.py:338`) and deep-copied into `DeploymentState` (`manager.py:260`).
  - From there it enters Temporal input: every listener's input next to the sanitized `filter_params` (`manager.py:908`), which the controller receives in a `register_trigger` Signal; every cron Schedule's action, where `filter_params` is raw too (`manager.py:1095-1102`, `schedules.py:212-221`); every MachinaWorkflow start (`manager.py:1373`, `server/services/temporal/executor.py:151-152`); every node activity input (`workflow.py:711-720`); and every agent tool input and Task Manager preflight of the generation (`agent_workflow.py:125`, `:411-412`, `:1946-1947`).
  - The raw copy outlives restarts. The boot re-arm sends the sanitized snapshot (`handlers.py:1805`), but the controller's `register_trigger` ignores a listener id it already knows (`workflow_control_workflow.py:326-330`), and its continue-as-new carries the raw specs into every new run for the life of the generation (`:688`). Only cron Schedules, which re-arm updates in place, and legacy listeners without a controller pick up the sanitized copy.
  - The Phase 0.1 test checks only `filter_params` (`server/tests/test_deployment_canary_listener.py:363-365`).
- **Which saved fields hold secrets:** webhookTrigger `header_value`, the one shipped node whose saved parameters are the only home of a real secret; vectorStore `pinecone_api_key`; an optional `api_key` on embeddingGenerator, the chat-model nodes, chatSend and chatHistory; proxyConfig `password`; and any `Authorization` value typed into a `headers` map (httpRequest, proxyRequest, webhookResponse, httpScraper). Agent nodes declare no `api_key`, and nothing copies a stored credential into these fields. The sanitizer has no rule for `Authorization` (`workflow_sanitizer.py:62-104`), so such a header passes even the sanitized paths.
- **Exposure: low to moderate.** The same values already sit unencrypted in `workflow.db` in the same data directory. Temporal adds two things: a read path without authentication (the loopback gRPC frontend and the Web UI, which employees' HTTP tools can reach, and shell and code tools in Dev-mode graphs), and copies that outlive the run (the Schedule definition until it is deleted, the controller's state for the whole generation, closed histories for 24 h, `temporal.db` backups). Normal-mode hires carry almost none of these values: Hire builds none of the secret-bearing nodes except chat models and httpRequest, and fills neither field. The realistic case is a Dev-mode graph with a header-authenticated webhook.
- **No authorizer, loopback only.** The argv passes no `--ip`, and the CLI maps `localhost` to 127.0.0.1. `server/services/netpolicy.py` refuses OpenCompany's own ports (every `*_PORT` variable, `own_ports_from_env`) and cloud metadata, but only the Browser and MCP nodes use it; the HTTP request node has no guard (`server/nodes/utility/http_request/__init__.py:90-131`), and it is on the Hire allowlist (`server/config/node_allowlist.json`). The dev server also opens its HTTP API and its metrics endpoint on random free ports that no `*_PORT` variable names (CLI v1.9.1 `internal/devserver/server.go:121-122`, `internal/temporalcli/commands.server.go:147-149`). Public firewalls open 22, 80, 443 and the app port, never a Temporal port (`cli/terraform/gcp/main.tf:62-72`, `cli/terraform/aws/main.tf:21-30`).
- **No codec, no failure converter.** Prompts, transcripts and customer messages sit in `temporal.db` in plain text, and T6 keeps transcripts there. Hosted backups copy the data directory (RFC-0005 §21.3).

**Verdict:** Misused (the Start path only). It breaks the rule Phase 0.1 set, that secrets stay out of snapshots and Temporal input (RFC-0005 §24.4, item 0.1; the comment at `manager.py:903-904`). The rest follows the page, with the gaps listed.

**Recommendation:**
1. Keep secrets out of the Start path. One commit:
   - `handlers.py:2141` passes `sanitize_runtime_payload(normalization.node_parameters)`.
   - `saved_settings` (`server/services/plugin/base.py:118-122`) puts back from the saved row every value the sanitizer drops, at any depth, when it returns a frozen copy. Per-type activities already read the row and lay the run's `node_data` over it, because they forward no snapshot (`base.py:1270-1332`). The frozen copy is used alone on the legacy `execute_node_activity` path (`activities.py:236`), for plugins that list `parameter_snapshot` in `context_keys` (the Browser node, `server/nodes/browser/browser/__init__.py:400`) and in process.
   - The sanitizer also drops `authorization`, `proxyauthorization` and `cookie`, so header maps lose those values.
   - Versioning: none (payload content only). When: Phase 1b (1b.31), in the same release as item 2 (T17). Effort S. Risk: a run now reads a secret the owner changed after Start; that is already true for per-type activities.
2. Clean the raw copies that live controllers hold. Phase 1b ships after the fresh-start release (T5), so every generation started in between holds raw copies, and its controller carries them across continue-as-new for the life of the generation. `register_trigger` refreshes a known listener's `listener_args["parameter_snapshot"]` from the incoming spec; the boot re-arm already sends the sanitized spec, so the next boot's re-arm cleans the live controllers, and re-arm already updates cron Schedules in place. Refresh nothing else: `filter_params` already arrive sanitized and decide whether a filter activity runs (`trigger_listener_workflow.py:177-178`), so changing them would change commands. `replace_graph` cannot do this on a running controller: its validator requires a paused one (`workflow_control_workflow.py:311-314`). Versioning: none (state and child inputs only; inputs are not compared on replay, RFC-0004 §8.5); the replay corpus must pass. When: Phase 1b (1b.32), in the same release as item 1 (T17). Effort S. Risk: backups of `temporal.db` taken before the fix keep raw copies until they expire; the continuation size check (`workflow_control_workflow.py:705-711`) sees a smaller carry, which can change a branch only for a controller already at the 1,900,000-byte limit.
3. Tests for items 1 and 2 (1b.31, 1b.32): Start a generation with a header-authenticated webhook trigger and an httpRequest node whose headers carry `Authorization`. Assert that neither value appears in the `register_trigger` Signal, the listener input, the Schedule action, the MachinaWorkflow input or any node activity input; that the webhook still authenticates; and that the httpRequest node still sends the header. For item 2, a re-arm Signal replaces a known listener's snapshot and nothing else, and the next child input carries the sanitized copy. The replay corpus stays green.
4. Retention of 7 days (17.2 item 1; deferred to RFC-0005 Phases 5 and 9a, T17).
5. **Default** (the owner did not answer whether agent tools may reach the local Temporal): treat hired employees' HTTP tool as untrusted.
   - The HTTP request node runs each URL through `netpolicy.url_block_reason` with the private network allowed, as MCP connectors do, so it refuses OpenCompany's own ports, including Temporal's gRPC and Web UI ports, and cloud metadata.
   - Every dev-server listener gets a named port: `TEMPORAL_HTTP_PORT` and `TEMPORAL_METRICS_PORT` in `.env.template`, passed as `--http-port` and `--metrics-port`, so `own_ports_from_env` covers them. The wrong comment at `_runtime.py:124` ("disables the Prometheus endpoint") is corrected in the same commit.
   - Shell and code executors stay off the Hire allowlist, as today. A Dev-mode graph that gives them to an employee facing customers is the owner's choice, and the docs say so.
   - Versioning: none. When: Phase 1b (1b.33). Effort S. Risk: an owner whose HTTP node calls a local service on one of those ports gets a refusal that names the reason.
   - Test: the argv carries `--http-port` and `--metrics-port` from `.env.template`; `own_ports_from_env` includes them; the node refuses `http://127.0.0.1:<TEMPORAL_UI_PORT>/` with a `NodeUserError` naming the reason and still reaches another loopback port.
6. **Default** for history encryption at rest (RFC-0004 Q13 stays open): no payload codec now; 7-day retention (17.2 item 1); hosted backups encrypted at rest in their bucket. The same content sits unencrypted in `workflow.db` and the conversation store beside `temporal.db`, so a codec alone does not protect data at rest, while it adds a key that must outlive every open run (months), a codec server for the Web UI, and the same converter in the replay gate. Under T4 a lost key would strand runs. Revisit with tier L. When: later.
7. T15's built-in outgoing rules (§16.9) hold any message that contains a saved credential or a secret-shaped token, which also covers an employee who reads a webhook secret and tries to send it. When: Phase 1b at today's hold points (1b.7); the owner's own rules and the recipient rule follow in RFC-0005 Phase 7 (§16.9).
8. Keep ids, search attributes and Schedule ids free of personal data (true today; codecs never encode them). When: always.

**Remarks:**
- Replay: runs already in history keep their recorded inputs; nothing to migrate.
- Temporal Proxy is described for Temporal Cloud; whether it can front a self-hosted server was not checked, and it would be another process on 10,000 instances. Not evaluated further.

**Sketch** (items 1 and 2; 1.30.0):

```python
# services/deployment/handlers.py, Start (:2141): nothing secret enters Temporal input.
deploy_data = {**data, ..., "parameters_by_id": sanitize_runtime_payload(normalization.node_parameters)}

# services/workflow_sanitizer.py: header maps lose credential values too.
_SENSITIVE_EXACT = frozenset({..., "authorization", "proxyauthorization", "cookie"})

def is_secret_key(name: object) -> bool:          # public form of _blocked_key(_key(name))
    return _blocked_key(_key(name))

# services/plugin/base.py: a frozen copy never hides a credential the saved row holds.
async def saved_settings(database, node_id: str, parameter_snapshot=None) -> dict:
    row = dict(await database.get_node_parameters(node_id) or {})
    if isinstance(parameter_snapshot, dict) and node_id in parameter_snapshot:
        return restore_secrets(dict(parameter_snapshot[node_id] or {}), row)
    return row

def restore_secrets(frozen: dict, saved: Mapping) -> dict:
    for key, value in saved.items():
        if is_secret_key(key):
            frozen[key] = value
        elif isinstance(value, Mapping) and isinstance(frozen.get(key), Mapping):
            frozen[key] = restore_secrets(dict(frozen[key]), value)
    return frozen

# services/temporal/workflow_control_workflow.py (:326-330), item 2 (1b.32):
# a re-armed listener refreshes its snapshot copy.
@workflow.signal
async def register_trigger(self, spec: Dict[str, Any]) -> None:
    listener_id = str(spec["listener_id"])
    if listener_id in self._triggers:
        incoming = (spec.get("listener_args") or {}).get("parameter_snapshot")
        if incoming is not None:      # child inputs only; never filter_params
            self._triggers[listener_id].setdefault("listener_args", {})["parameter_snapshot"] = incoming
        return
    ...
```

### 17.9 Worker deployment and performance

**Docs:** https://docs.temporal.io/best-practices/worker. A Worker is the process-side object that polls a task queue and runs workflow and activity code. The page's checklist and rules:
- Tune each Worker; deploy enough Workers; package them as CI/CD artifacts with runtime configuration.
- "Use separate Task Queues for distinct workloads ... For each Task Queue, ensure you configure at least two Workers to poll the Task Queue." Define queue names as shared constants: a mismatch "creates two different Task Queues, and the Worker never receives Tasks".
- "For most teams, Worker Versioning should be the default approach"; patching is "a fallback for environments that cannot adopt versioned worker deployments yet, not as the default recommendation."
- "We recommend not exceeding a few thousand Events in a single Workflow Execution"; use continue-as-new (start a fresh run of the same workflow id with a new, empty history) when the SDK suggests it. Hard limits: 2 MB per payload, 51,200 events and 50 MB per history.
- Tune task slots, the sticky cache (workflows kept in memory so their next task needs no replay) and pollers (prefer autoscaling); read metrics together; scale down with graceful shutdown.

**SDK:** all in 1.30.0: `Worker(max_concurrent_*, tuner=, max_cached_workflows=, *_poller_behavior=PollerBehaviorAutoscaling(...), graceful_shutdown_timeout=)`, `Worker.shutdown()` (`temporalio/worker/_worker.py:886`), `workflow.info().is_continue_as_new_suggested()`, `get_current_history_length()` and `get_current_history_size()`. Worker Versioning types (`WorkerDeploymentConfig`, `temporalio/worker/_worker.py:1027`) exist in both versions. 1.33.0 drains in-flight activity completions at shutdown.

**OpenCompany today:**
- **Embedded framework worker** (`server/services/temporal/worker.py:265-308`): 100 activity slots, 10 workflow-task slots, a sticky cache of 50, 500 or 100 by `DEPLOYMENT_MODE` (`:76-88`), autoscaling pollers (activity 1-10, workflow 1-20), identity `opencompany-<queue>-<mode>`, graceful timeout 30 s (10 s on the desktop, `desktop/src/main/env.ts:104`), and a crash-restart that rebuilds the single-use `Worker` (`:318-361`).
- **Pool:** one activity-only Worker per plugin queue, per-queue slots halved in `local` mode, per-worker rate caps, a resource-based tuner on `ai-heavy` and `browser` (`worker.py:393-673`).
- **`DEPLOYMENT_MODE`** is a 3-value hint (`local` on the desktop, `self_hosted` in Docker, `cloud` on VMs), not the instance's tier or machine size.
- **Standalone worker** (`worker.py:758-928`): no `max_cached_workflows` (SDK default 1,000), no poller autoscaling, no identity, no `plugins=` (so no CronTriggerWorkflow), no polling activities, hard-coded namespace and queue (RFC-0004 H7).
- **Queue names are not one constant:** cron Schedules default to the literal `"machina-tasks"` (`schedules.py:149`), legacy listeners start on a literal (`manager.py:988`), and a non-default `TEMPORAL_TASK_QUEUE` would strand both.
- **Shutdown:** workers are stopped by cancelling their run tasks (`worker.py:373-378`, `:663-670`); `Worker.shutdown()` is never called. The SDK says a cancel "could also cancel the shutdown process. Therefore users are encouraged to use explicit shutdown instead" (`temporalio/worker/_worker.py:742-757`).
- **History caps:** 10,000 events plus the server's suggestion (`trigger_listener_workflow.py:74`, `workflow_control_workflow.py:27`, `agent_workflow.py:332`). RFC-0005's entity uses 4,000 (RFC-0005 §10.2).
- **Versioning:** patching plus per-generation input flags (RFC-0005 §24.1 rule 2); Worker Versioning declined (RFC-0004 D17).

**Verdict:** Adopted with deviations.

**Recommendation:**
1. Stop workers with `await worker.shutdown()`, bounded by the graceful timeout, then cancel only what is left: the manager, the pool, the browser owner pool and the Pi driver pool (RFC-0004 §11.5). Versioning: none. When: Phase 1b (1b.37). Effort S. Risk: the desktop shell's quit deadline must still cover the slowest pool. Test: a fake Worker records `shutdown()` before any cancel, and an in-flight activity gets the graceful window.
2. Bring the worker process to parity before T8 puts workers in a second process on hosted tier S. The one worker builder (§15.3, 1b.36) gives every Worker one registration, plugins and polling activities included, reads namespace and queue from settings, gives each process a unique identity, and makes a standalone or tier S worker process that registers different code refuse to start (RFC-0004 AG14). A standalone worker outside distributed mode refuses to run (§15.2, 1b.35). When: Phase 1b (1b.35, 1b.36), before the T8 split (RFC-0005 Phase 9). Two related changes keep their own timing: the Replayer built from the same function (17.17 item 2) and one queue-name source for Schedules and legacy listeners (§15.1 rec 3).
3. Size workers from the tier and the host (CPU count, RAM) in the deploy logic instead of the 3-value `DEPLOYMENT_MODE`. §15.1 (rec 2) owns the sticky-cache size: it is measured against the resident entity count in RFC-0005 Phase 4, and its tier defaults are written here, in Phase 9a. Versioning: none. When: RFC-0005 Phase 9a. Effort S.
4. Tier L runs at least two worker processes for the workflow queue and for `llm-c1`, so one restart or out-of-memory kill does not stall the owner's chat (the page's "at least two Workers"). Tier S and the desktop keep one, restarted by the lifecycle (`worker.py:318-361`) and the service manager. Versioning: none. When: RFC-0005 Phase 9c. Effort S.
5. Keep patching and per-generation flags, and cite the page's own fallback clause as the reason: a single-process desktop and an in-place weekly upgrade cannot keep old builds polling, and long-lived entities would need patches anyway. RFC-0005 §10.4 listed Worker Versioning among "preview features" until this RFC amended it (§20); the worker page now calls it the default, so the reason to decline is topology, not maturity (whether it is GA was not checked). When: with this RFC.
6. New types continue as new at "a few thousand" events; RFC-0005's 4,000 follows. Legacy types keep 10,000: lowering it changes when open runs roll over, which needs a patch or a flag. Versioning: new types only. When: RFC-0005 Phase 4. Effort S.

**Remarks:**
- The SDK 1.33 fix for small sticky caches is not a reason to upgrade: it needs a cache smaller than the workflow-task slot count (10 here), and OpenCompany's caches are 50, 100 and 500 (RFC-0004 §6.7 and §11.4 said otherwise; §21 corrects them).
- The 10 workflow-task threads hold the GIL while they replay, competing with uvicorn on the same process (17.19).

### 17.10 Alerting on Worker metrics

**Docs:** https://docs.temporal.io/best-practices/worker-alerting. Workers emit metrics the server cannot see, so SDK metrics come first.
- Names: a `temporal_` prefix; "Counters pick up a `_total` suffix when scraped through Prometheus"; gauges take no suffix; "Confirm the exact names and tags in your own metrics endpoint before writing queries."
- "Start with these five": all pollers disconnected; non-determinism error (workflow code no longer matches its recorded history); gRPC message too large; workflow-task schedule-to-start latency elevated; `RESOURCE_EXHAUSTED` on starts, Signals and Updates.
- Thresholds come from your own healthy p99. A rolling restart drops pollers, produces `NOT_FOUND` and short-lived non-determinism errors, "so the fix is a `for` duration longer than a deploy takes". Idle queues need alerts paired with demand.
- "`worker_task_slots_available` reports meaningful values only with fixed-size slot suppliers."

**SDK:** `Runtime(telemetry=TelemetryConfig(metrics=PrometheusConfig | OpenTelemetryConfig | MetricBuffer, global_tags=...))` in 1.30.0 (`temporalio/runtime.py:318`, `:367`, `:417`, `:468-477`). `PrometheusConfig(counters_total_suffix=False)` is the Python default (`runtime.py:392`), so counter names differ from the page unless set. 1.30.0 already logs `[TMPRL1104]` at WARNING when a workflow task exceeds 10 s (`temporalio/worker/_workflow.py:580`). 1.34.0 adds `failure_reason` tags (`GrpcMessageTooLarge`, `PayloadsTooLarge`, `RequestTooLarge`) on `workflow_task_execution_failed`.

**OpenCompany today:**
- No SDK metrics: both runtimes are `Runtime(telemetry=TelemetryConfig(logging=LoggingConfig(filter="ERROR")), worker_heartbeat_interval=None)` (`client.py:76-83`, `worker.py:91-102`); `PrometheusConfig`, `OpenTelemetryConfig`, `MetricBuffer` and `metric_meter` appear nowhere (grep).
- The dev server serves Prometheus `/metrics` on a random loopback port that nothing reads; the comment that `--metrics-port 0` "disables the Prometheus endpoint" is wrong (`_runtime.py:124`; CLI v1.9.1 picks a free port when it is 0).
- Signals that exist: the `activity_retry` WARN line (`server/services/temporal/_interceptors.py:62`), `[TMPRL1104]`, the dev-server watchdog in `lifecycle.py`, worker crash-restart logs.
- Deploy-like events: the weekly window restarts every hosted instance at once (RFC-0005 §21.2); a desktop that sleeps drops its pollers and stops completions with no fault.
- Resource-based suppliers on `ai-heavy` and `browser`. RFC-0005 planned a custom slot pool for its class queues, which §13.3 (rec 5) replaces with fixed-size suppliers.

**Verdict:** Missing and beneficial. Without metrics, nothing detects a non-determinism error (a run that stays "Running" and never moves), an oversized task or a starved event loop.

**Recommendation** (RFC-0004 D6). No item touches workflow code, so none needs versioning.
1. Create one runtime with metrics before any client or worker: a `MetricBuffer` on the desktop, drained every few seconds into the health view and attention items and never exported unless the owner opts in (E14); an OTLP exporter on hosted instances. `global_tags`: an opaque instance id, the tier and the release; never employee ids. Versioning: none. When: Phase 1b (1b.38). Effort M. Risk: an undrained buffer drops updates and logs an error; drain on a timer.
2. The five starter alerts become attention items on the instance and counters in fleet telemetry: pollers at zero on a queue with demand; any non-determinism error; `GrpcMessageTooLarge` or `PayloadsTooLarge`; workflow-task schedule-to-start p99 above 5 s (on the embedded worker this is also the event-loop starvation signal, 17.19); `RESOURCE_EXHAUSTED` on start, Signal and Update. Add the stuck-run count from `TemporalReportedProblems` (RFC-0004 §7.2; the server sets it after five consecutive workflow-task failures once `system.numConsecutiveWorkflowTaskProblemsToTriggerSearchAttribute=5` is passed, which 1b.38 adds to the dev-server command line (a dynamic-config value, not a search-attribute registration, so T17's deferral does not cover it); server 1.30.1+, which every install runs after T5). When: Phase 1b (1b.38). Effort M.
3. Route by scope: a non-determinism error on several instances after a window is a release regression for the operator; one stuck generation is an owner attention item ("needs attention"). When: RFC-0005 Phase 9a. Effort S.
4. Hold the deploy-transient alerts during the weekly window and for a grace period after a desktop wakes; pair "completions dropped to zero" and "sticky cache empty" with a demand signal, because most employees are idle most of the time. When: Phase 1b (1b.38), with item 2. Effort S. Risk: a real fault inside the grace period is reported late.
5. Use schedule-to-start latency, not `worker_task_slots_available`, for the resource-tuned queues and for any custom slot supplier; §13.3 keeps the class queues on fixed-size suppliers, where the gauge is meaningful. When: Phase 1b (1b.38). Effort S.
6. Write metric names once, in the exporter configuration, and test them against the endpoint (Python counters lack `_total` by default). When: Phase 1b (1b.38). Effort S.
7. Give the dev server a named metrics port (`TEMPORAL_METRICS_PORT`, 17.8 item 5, which also corrects the comment at `_runtime.py:124`) and read its task-queue and backlog metrics into the same health view; that works before SDK metrics land. When: the port in Phase 1b (1b.33); the health view reads it with 1b.38. Effort S.
8. Mark expected retries BENIGN (18.5 item 3) so they do not count as failures. When: Phase 1b (1b.28). Effort S.

Items 1, 2 and 4-6 are 1b.38. Its test (RFC-0004 AG4): an injected non-determinism error yields the metric and one attention item; exported tags hold no employee or workflow id; the desktop buffer is drained. The loop-lag watchdog is 1b.39 (17.19), and the retry alert belongs to §11.7: its attention item is 1b.30, its metric counter RFC-0005 Phase 8.

**Remarks:** alerts cluster (slots fill, pollers drop, latency rises, completions stop); an attention item should name the root cause, not four symptoms. Exported metrics must pass RFC-0005 EG15: tags and values only, no content.

**Sketch** (item 1; 1.30.0):

```python
# services/temporal/client.py: one runtime, created before any client or worker
# (create_runtime() in worker.py uses the same function).
import asyncio

from temporalio.runtime import (LoggingConfig, MetricBuffer, MetricBufferDurationFormat,
                                OpenTelemetryConfig, Runtime, TelemetryConfig)

def build_runtime(settings) -> tuple[Runtime, MetricBuffer | None]:
    tags = {"instance": settings.instance_id, "tier": settings.tier, "release": settings.release}
    buffer = None if settings.otlp_endpoint else MetricBuffer(
        buffer_size=50_000, duration_format=MetricBufferDurationFormat.SECONDS)
    metrics = OpenTelemetryConfig(url=settings.otlp_endpoint) if settings.otlp_endpoint else buffer
    runtime = Runtime(
        telemetry=TelemetryConfig(
            logging=LoggingConfig(filter="ERROR"),   # as today; Core logs at WARN are 17.15 item 2
            metrics=metrics,
            global_tags=tags,
        ),
        worker_heartbeat_interval=None,
    )
    return runtime, buffer

async def drain_metrics(buffer: MetricBuffer, health) -> None:
    while True:                                  # a full buffer drops updates
        for update in buffer.retrieve_updates():
            health.record(update.metric.name, update.value, dict(update.attributes))
        await asyncio.sleep(5)
```

### 17.11 Error handling

**Docs:** https://docs.temporal.io/best-practices/error-handling. Classify failures as transient (default retries), intermittent (longer backoff and maximum interval) or permanent (non-retryable). Mark permanent errors non-retryable in the activity or in the caller's policy. "When an Activity returns an error, the SDK checks the **outermost** error type to determine retryability." "Use non-retryable errors sparingly." Activities run at least once: "Combine the Workflow Run ID and Activity ID" for idempotency keys. Saga compensations run in reverse order.

**SDK:** `ApplicationError(type=, non_retryable=, next_retry_delay=, category=)`, `RetryPolicy(non_retryable_error_types=)`, `activity.info().workflow_run_id` and `.activity_id`, all in 1.30.0. The default activity id is a per-run sequence number (`temporalio/worker/_workflow_instance.py:3195`), so it starts again at 1 after continue-as-new.

**OpenCompany today:**
- The LLM boundary maps provider error categories to `non_retryable`, Retry-After to `next_retry_delay`, and turns provider retries off (`agent_activities.py:246-303`): textbook.
- Policies: a short shared non-retryable list (`_retry_policies.py:52`); `LLM_STEP_RETRY` unlimited with 5 s to 5 min backoff (`:94-100`); no `schedule_to_close_timeout` anywhere (RFC-0004 H3). §11.3 proposes a step budget, which T18 ships with parking in RFC-0005 Phase 5, for new generations only.
- The plugin layer turns every exception into a result envelope, so Temporal never sees a transient node failure, and graph nodes are never retried on a reported failure (`base.py:1336-1395`; RFC-0004 Q9 open).
- Node activities turn cancellation into a failure result (`server/services/node_executor.py:230-238`, RFC-0004 H2).
- Idempotency: agent sends are refused on a later attempt (`server/services/approvals/tool_calls.py:367-379`) with a business key `c:{workflow}:{execution}:{tool_call_id}` (`:103-104`); approved sends use a claim token from the Temporal workflow and run id and `maximum_attempts=1` (`server/services/temporal/approved_tool_call_workflow.py:30`, `:39`). Sends made as graph nodes have no key and run with the plugin policy, up to 3 attempts; that includes the reply node after approvalGate in every app employee Hire builds (§16.11).
- Saga: cleanup compensations exist (delegation cancel, permit release, browser claim release), but Reset terminates instead of cancelling, so they never run (RFC-0004 H12).

**Verdict:** Adopted with deviations.

**Recommendation:**
1. One idempotency-key rule for every effect (RFC-0005 §14 spend keys, RFC-0005 §17 send keys, and T13's side-effecting tools): use a business key where one exists (tool call id, inbox sequence, provider event id); otherwise the Temporal workflow id plus an explicit `activity_id` that is unique across the whole continue-as-new chain, as the LLM step's `llm-step-<n>` is (`agent_workflow.py:3187`); never the default activity id, which repeats after continue-as-new and would silently deduplicate a different operation away; use the run id only for claims scoped to one run, as ApprovedToolCallWorkflow does. The page's run-id key would duplicate an effect after a Temporal workflow reset; RFC-0004 §9.4's workflow-id key loses one after continue-as-new unless ids are chain-unique. Versioning: adding an explicit `activity_id` to an existing call changes an attribute replay compares, so it needs `workflow.patched` in shared types; new types set it from the start. When: RFC-0005 Phase 4 (new types) and Phase 7 (sends). Effort S.
2. Activities re-raise cancellation (RFC-0004 H2): §9.3 owns it (1b.2; activity side only, no versioning). Workflow loops re-raise cancellation (RFC-0004 H8): that changes commands on the cancellation path, so it needs `workflow.patched`, in RFC-0005's controller-hardening lane. Reset cancels, waits, then terminates (RFC-0004 AG18), so compensations run (P2, RFC-0005 Phase 4). Reset also closes in the database what terminated runs leave open (§10.2, 1b.21).
3. Graph-node sends, and every other side-effecting call, get the effect classes and the redo guard of §11.2 (1b.1; E12, T13). When: Phase 1b.

**Remarks:** E12's at-most-once rule with an `unknown` state is a deliberate alternative to the page's idempotency keys where a provider has none.

### 17.12 Pre-production testing

**Docs:** https://docs.temporal.io/best-practices/pre-production-testing. Principles: "Partial failure is often harder to deal with than total failure"; "Recovery paths deserve as much testing as steady state". Drills: kill all Workers, then restart them; restart a share (for example 20-30%) every few minutes; load tests with success criteria; break dependencies; "Deploy a version that causes NDEs, then recover"; remove network connectivity; a game-day runbook. Anti-patterns the drills reveal: "Non-idempotent Activities", "Infinite retries without circuit breaking", "Using Workflow logic to "wait out" broken dependencies".

**SDK:** `Replayer`, `client.list_workflows(...)`, `WorkflowHandle.fetch_history()` (`temporalio/client/_workflow.py:395`) and `WorkflowEnvironment.start_local(dev_server_existing_path=...)` in 1.30.0.

**OpenCompany today:**
- Exists: worker restart and recovery tests (`server/tests/temporal/test_worker_restart.py`, `test_pi_worker_recovery.py`, `test_durability_hardening.py`); the CI replay gate on three operating systems (`.github/workflows/predeploy.yml`, `backend-tests`); RFC-0005 gates EG1 (kill -9), EG6 (a paused generation survives each phase), EG14 (tier load) and EG16 (a forced readiness failure rolls back).
- Not found: load-test tooling, failure-injection scripts, a game-day runbook.
- The weekly window rolls back on `/health/ready` (RFC-0005 §21.2 step 5), which means "database up and workers started" (`server/main.py:755-789`). A release that cannot replay an open run passes it: those runs stay Running with failing workflow tasks.
- The page's anti-patterns exist: unlimited LLM retries with no outer budget (Q6 decided; §11.3 proposes the values, and T18 ships the budget with parking, §11.8, in RFC-0005 Phase 5 for new generations; existing generations keep unlimited retries under the retry alert, 1b.30), approval and permit waits inside activities (RFC-0004 M4; four waits to convert, §9.6), graph sends without keys (17.11).

**Verdict:** Missing and beneficial.

**Recommendation:**
1. Replay before polling at every upgrade, on hosted instances and desktops: after the client connects and before any worker starts, replay every open run of this instance with the new code (sketch). Under T14, a run that cannot replay does not stop the upgrade: workers start, and each affected generation is marked "needs Reset" with an attention item. A failed readiness check still rolls back (RFC-0005 §21.2). Histories never leave the instance (E14). Versioning: none. When: RFC-0005 Phase 9a (hosted) and the same release for desktops. Effort M. Risk: replay time grows with open histories and their length; EG14 measures it.
2. State the rollback rule: rolling back is replay-safe only before the new release completes a workflow task, because patches are forward-only, unless every patch the new release activated shipped dormant in the release before it (18.9). After that, problems are fixed forward; when a failed readiness check still rolls back (T14), generations whose runs the old release cannot replay are marked "needs Reset". Versioning: this is policy. When: with this RFC. Effort S.
3. EG14 uses a repeatable driver: start N employees, push synthetic events and chats at a target rate, and measure schedule-to-start, SQLite write latency, CPU and RAM. Tier limits in RFC-0005 §20 come from it. Versioning: none. When: RFC-0005 Phase 9c. Effort M. Risk: synthetic load misses real prompt sizes; replay recorded production-shaped turns too.
4. A game-day page in docs-internal: kill the backend mid-turn; sleep a laptop through a cron tick and an approval expiry; stop the dev server for 10 minutes; a provider outage of 30 minutes; a full disk under `temporal.db`; a release with a deliberate non-determinism error on staging. Versioning: none. When: RFC-0005 Phase 8. Effort S.

**Remarks:** listing open runs uses Visibility, which is fine for an operational sweep (RFC-0005 I6). The SDK parameter is `raise_on_replay_failure`; the docs' cross-SDK text says `fail_fast`.

**Sketch** (item 1; 1.30.0):

```python
# services/temporal/startup_replay.py (new). The lifecycle calls it after the
# client connects and before TemporalWorkerManager.start().
from temporalio.common import SearchAttributeKey
from temporalio.worker import Replayer

from services.temporal.workflow_registry import replay_workflows

_EVENT_WORKFLOW_ID = SearchAttributeKey.for_keyword("EventWorkflowId")

async def runs_that_cannot_replay(client) -> dict[str, list[str]]:
    """App workflow id -> Temporal ids of its open runs this release cannot replay."""
    replayer = Replayer(workflows=replay_workflows())   # same configuration as the Worker (17.17)
    failed: dict[str, list[str]] = {}
    async for run in client.list_workflows('ExecutionStatus="Running"'):
        history = await client.get_workflow_handle(run.id, run_id=run.run_id).fetch_history()
        result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
        if result.replay_failure is not None:
            app_id = run.typed_search_attributes.get(_EVENT_WORKFLOW_ID) or run.id
            failed.setdefault(app_id, []).append(run.id)
    return failed

# T14: start the workers anyway, then mark the latest generation of each app id
# "needs Reset" (an attention item; never an automatic Reset).
```

### 17.13 Knowledge hub (new page)

**Docs:** https://docs.temporal.io/best-practices/knowledge-hub. An internal hub organized by journey (Evaluate, Build, Ship, Operate); link to the official docs instead of copying them; "Review each page at least quarterly"; "Prune aggressively: ... Outdated documentation is worse than no documentation because developers follow it and get unexpected results."

**SDK:** none.

**OpenCompany today:** `docs-internal/TEMPORAL_ARCHITECTURE.md`, `docs-internal/temporal-workflow-control.md`, CLAUDE.md and the RFCs. No Temporal runbook (RFC-0004 §7.6 planned one). Operational comments that contradict the code at HEAD:
1. `.env.template:101-107`: setting `TEMPORAL_WORKER_POOL_ENABLED=false` is a "Rollback channel" and activities "route back to the manager worker unchanged". Routing is frozen into run input, so open runs strand (RFC-0004 H7). This is the dangerous one: an operator who follows it strands every open run.
2. `.env.template:147-150`: history is "always preserved"; the dev server deletes it after 24 h.
3. `.env.template:82` and `_runtime.py:61-63`, `:122-123` say a database from another server minor "is deleted"; since commit 49177427, `prepare_database` deletes nothing and refuses to start (`_install.py:229-241`), and `server/config/temporal_cli.json` says installations "retain their installed server/database pair". T5 reverts 49177427, which makes the first comments true again; the `temporal_cli.json` comment and the `prepare_database` docstring must go with the revert.
4. `_runtime.py:124`: `--metrics-port 0` "disables the Prometheus endpoint"; CLI v1.9.1 picks a random port.
5. `agent_activities.py:12-13`: the LLM step "Lives on TaskQueue.AI_HEAVY"; it runs on `machina-tasks` (`agent_workflow.py:3185-3188`).
6. `client.py:125-129`: a failed search-attribute registration "falls back to broadcast-only routing"; starts that set the attributes fail.
7. `worker.py:425-429` names "Anthropic/OpenAI tier limits on ai-heavy": misleading rather than wrong, since `ai-heavy` carries canvas chat-model, embedding, vision and activity-dispatched agent calls, but no AgentWorkflow LLM step.

**Verdict:** Adopted with deviations.

**Recommendation:**
1. Fix comments 2 and 5 to 7 now (1b.45). Comment 1 changes with the pool fix (§15.2, 1b.34), comment 4 with the named metrics port (17.8 item 5, 1b.33), and comment 3 with the T5 revert in the fresh-start release (Phase 1). Versioning: none. When: Phase 1b (comment 3: Phase 1). Effort S. Test: none at runtime; reviewed in the diff.
2. One `docs-internal/temporal_handbook.md` in the page's four parts: why Temporal and the decisions; how to add a workflow type (sandbox, registry, golden identity, fixtures, patch or flag, the bug guard of 18.2); tiers, versions, retention, payload rules and capacity budgets; the runbook (including "needs Reset") and the alerts. Link to docs.temporal.io instead of copying, and give each part a review date. Versioning: none. When: later. Effort M. Risk: another document to keep current; the review dates are the guard.

**Remarks:** none.

### 17.14 Python SDK best practices (index)

**Docs:** https://docs.temporal.io/develop/python/best-practices. Links error handling, testing, the sandbox, debugging, data handling and sync vs async.

**SDK:** none.

**OpenCompany today:** 17.15 to 17.19 review the five pages. Data handling (payload size, converters) is covered by §16.8 and 18.18.

**Verdict:** Not applicable. It is an index.

**Recommendation:** none.

**Remarks:** none.

### 17.15 Python debugging

**Docs:** https://docs.temporal.io/develop/python/best-practices/debugging. In development: logging, a debugger, the Web UI and the CLI. In production: the Web UI, the CLI, replay, tracing and logging; Worker metrics with the worker performance guide; server metrics from the self-hosted production checklist.

**SDK:** `workflow.logger` (replay-aware) and `activity.logger`; `TracingInterceptor`; `Replayer`; the metrics runtime; Core log forwarding through `LoggingConfig(forwarding=LogForwardingConfig(...))` (`temporalio/runtime.py:194-227`). `debug_mode` turns off the deadlock detector, which fails a workflow activation that runs longer than 2 s without yielding (`temporalio/worker/_workflow.py:158-160`). All in 1.30.0.

**OpenCompany today:**
- Web UI: the dev server's UI on `TEMPORAL_UI_PORT`, loopback only. On hosted instances it needs an SSH tunnel, and it shows payloads in plain text.
- CLI: the pinned binary under `packages/temporal/<version>/`; no runbook (RFC-0004 §7.6).
- Replay: the corpus and `WorkflowHistory.from_json` (17.17).
- Tracing: `TracingInterceptor` once on the client (`client.py:92-97`), but spans have no exporter unless console spans are on (`server/core/tracing.py:70-96`).
- Logging: Core logs are filtered at ERROR and not forwarded (`client.py:76-83`); `ExpectedLLMRetryFilter` hides expected LLM retries (`server/core/logging.py:256-283`); the interceptor's structured `activity_retry` lines.
- Metrics: none (17.10).

**Verdict:** Adopted with deviations.

**Recommendation:**
1. Metrics and the starter alerts: 17.10 (1b.38).
2. Forward Core logs at WARN through `LogForwardingConfig` into the app's logger (RFC-0004 §7.4). Versioning: none. When: RFC-0005 Phase 8. Effort S. Risk: noise during the weekly window; the same suppression as 17.10 item 4.
3. A span exporter only when an operator turns it on, with attributes filtered to identities and types: E14 forbids content in telemetry, and spans can carry attributes. Versioning: none. When: RFC-0005 Phase 9a. Effort S. Risk: an unfiltered attribute leaks content; EG15's content filter checks spans too.
4. The runbook (RFC-0004 §7.6) in the handbook of 17.13, including what "needs Reset" means (T14) and that debugging one owner's workflows on a hosted instance needs the owner's consent and an SSH tunnel. Versioning: none. When: RFC-0005 Phase 8. Effort S.

**Remarks:** owners debug through the app (the timeline and attention items), never through the Temporal Web UI.

### 17.16 Python error handling

**Docs:** https://docs.temporal.io/develop/python/best-practices/error-handling. Idempotency keys from `info.workflow_run_id` and `info.activity_id`; raise `ApplicationError` with a `type`. In workflows, an `ApplicationError` (more generally any `FailureError`) fails the execution, while any other exception fails the workflow task, which "retries indefinitely, letting you fix the bug and redeploy without losing Workflow state". Catch `ActivityError`, `ChildWorkflowError`, `CancelledError` and `TimeoutError`. "Don't use Workflow Retry Policies." "Do not extend `TemporalError` or its children." "The default Failure Converter copies exception messages and stack traces as plain text visible in the Web UI."

**SDK:** all in 1.30.0. `workflow_failure_exception_types` on the Worker and `failure_exception_types` on `@workflow.defn` turn listed exception types into workflow failures. The SDK's own rule: an exception fails the workflow if it is a `FailureError`, an `asyncio.TimeoutError` or a configured type; anything else fails the task (`temporalio/worker/_workflow_instance.py:1824-1839`, `:2614-2616`). From 1.33.0, continue-as-new from an Update handler fails the workflow task (the repo never does that).

**OpenCompany today:**
- No workflow retry policy on any start (grep of `start_workflow`, `execute_workflow`, child starts and Schedule actions): correct.
- Update validators reject with `ApplicationError` (`workflow_control_workflow.py:161-323`): correct.
- Failed runs complete with `success: False` (RFC-0004 M5). The owner chose Temporal Failed (Q4); RFC-0005 §10.3 applies it to new turns, not yet to MachinaWorkflow or AgentWorkflow.
- The page's promise ("fix the bug and redeploy") does not hold for 11 workflow types: they run cleanup commands for any exception, so a fixed release cannot replay a run that hit a bug (18.2).
- Workflow loops catch `Exception` without checking `is_cancelled_exception` (`workflow_control_workflow.py:849-852`, `:861-864`; `polling_trigger_workflow.py:193`; `trigger_listener_workflow.py:278`; RFC-0004 H8). The controller re-raises `asyncio.CancelledError`, but a cancelled activity raises `ActivityError` with a `CancelledError` cause, which `except Exception` swallows.
- Plain exceptions for bad data are rare in workflow code. One remains at `workflow.py:186` (`ValueError("Unsupported recorded Coding Agent harness version")`, checked at the start of every run, `:556`); it guards inputs that a newer release may write, so a run stuck there resumes when that release comes back, which is what a rollback needs.

**Verdict:** Adopted with deviations.

**Recommendation:**
1. Guard all run-exit and per-event cleanup with the bug test of 18.2 (`is_workflow_bug`), so a bug records nothing and the page's "fix the bug and redeploy" holds. Versioning: none (18.2). When: Phase 1b (1b.13, owned by 18.2). Effort M.
2. Failed graph and agent runs end as Failed (RFC-0004 Q4), through RFC-0005's turn for new generations. Versioning: per-generation flag (`event_os_version=1`). When: RFC-0005 Phase 4. Effort S. Risk: callers that read `success: False` from a completed run must handle a failed run instead.
3. Loops re-raise cancellation, including an `ActivityError` whose cause is a cancellation (RFC-0004 H8). This changes commands on the cancellation path, so it needs `workflow.patched` (RFC-0005 §24.2 controller-hardening lane). When: P2, after Phase 1. Effort S.
4. Classify each new `raise` in workflow code: an input a newer release may write stays a plain exception (stuck until that release returns); a value no release can accept becomes `ApplicationError(type=..., non_retryable=True)`. The determinism lint (17.18, 1b.40) flags plain raises for review. Versioning: none for the lint; changing an existing raise changes only runs that hit it, which are stuck already. When: with the lint. Effort S.
5. No failure converter while history stays unencrypted (17.8 item 6): the LLM boundary already sends category messages instead of provider text.
6. **Default** for bugs in workflow code (RFC-0004 Q5 stays open): stuck and visible. No `workflow_failure_exception_types`; item 1 so a bug records nothing; the ERROR log of 18.5, the metrics and `TemporalReportedProblems` (17.10) so it is seen. Reasons: T4 (no lost runs) and T14 (a release that cannot replay a run marks its generation "needs Reset" instead of failing it). Fail-fast with `[Exception]` would also fail every affected open run on an incompatible upgrade, because `NondeterminismError` derives from `Exception` (`temporalio/workflow/_exceptions.py:17`).

**Remarks:** the docs' Saga compensations run when a workflow is cancelled, not when it is terminated; Reset terminates today (RFC-0004 H12).

### 17.17 Python testing suite

**Docs:** https://docs.temporal.io/develop/python/best-practices/testing-suite. Mostly integration tests; `ActivityEnvironment` (with heartbeat capture and cancellation); `WorkflowEnvironment.start_local`, `start_time_skipping` and `from_client`; mock activities by registering a function under the same name; time is global per environment, so tests with different time behavior use separate environments. Replay in CI: pick workflow types, download recent histories, replay them, and "Fail CI if any error is encountered during replay".

**SDK:** all in 1.30.0: `Replayer(..., interceptors=, workflow_runner=, data_converter=)` and `replay_workflows(..., raise_on_replay_failure=)` (`temporalio/worker/_replayer.py:110-142`).

**OpenCompany today:**
- Replay gate: `server/tests/temporal/test_replay_corpus.py` replays every N-1 fixture (21 scenarios in `fixtures/0.3.1/`) in a fresh process with the production registry, checks fixture provenance against the recorded commit, refuses histories with a failed workflow task (`:65`), requires every production workflow type to be covered (`:69`), and forbids skipping in CI (`:19-21`). CI runs it on ubuntu, windows and macos with the pinned CLI (`.github/workflows/predeploy.yml`, `backend-tests`).
- Golden identity test: activity names, child ids, patch markers and node activity versions (`server/tests/temporal/test_workflow_identity_golden.py:12-45`); worker and replayer share one registry (`server/services/temporal/workflow_registry.py`).
- Time skipping in 15 test files, each with its own environment; real-server tests through `start_local` with the pinned `TEMPORAL_TEST_CLI`; `ActivityEnvironment` in 7 files; 128 activity mocks registered by name.
- The Replayer is built from workflows only (`test_replay_corpus.py:66`), while workers add `ObservabilityWorkerInterceptor`; harmless today, because the interceptor only logs and checks `is_replaying`.
- The node wrapper's cancellation path has no test (RFC-0004 AG10).

**Verdict:** Adopted correctly.

**Recommendation** (none of these items changes workflow code, so none needs versioning):
1. Replay real open histories at every upgrade (17.12). The corpus covers recorded scenarios; 10,000 instances take paths no fixture has. When: RFC-0005 Phase 9a. Effort: in 17.12.
2. Build the Replayer from the same function as the Worker: interceptors, workflow runner, data converter and `workflow_failure_exception_types`. It becomes required the day a sandboxed type (17.18) or a codec arrives; until then the gate replays the way production runs. When: with the first sandboxed type, RFC-0005 Phase 3 (PollWorkflow) or Phase 4, whichever lands first. Effort S. Risk: none.
3. An `ActivityEnvironment` test that cancels a node activity and expects `CancelledError` ships with RFC-0004 H2's fix (§9.3, 1b.2).
4. One test per workflow type for the bug guard: workflow code raises a plain `RuntimeError` (a monkeypatched helper); the run stays open with a failed workflow task and no cleanup activity was scheduled. It ships with the guard (18.2 item 2, 1b.13).
5. Tests that exercise Updates use `start_local`, not the time-skipping server: the samples skip Updates there ("Java test server", sdk-java#1903). When: always.

**Remarks:** the fixture recorder (`server/tests/temporal/record_fixtures.py`) must run on the release commit with the pinned CLI; the provenance check enforces the first part.

### 17.18 Python SDK sandbox

**Docs:** https://docs.temporal.io/develop/python/best-practices/python-sdk-sandbox. The sandbox runs each workflow run with its own fresh import of its modules, except passed-through ones, and replaces known non-deterministic calls (clock, randomness, I/O) with ones that fail. "Skipping Workflow Sandboxing results in a lack of determinism checks", and the ways to skip it "aren't recommended". Pass through known deterministic, side-effect-free modules with `workflow.unsafe.imports_passed_through()` or `SandboxRestrictions.default.with_passthrough_modules(...)`. Import notification policies: `WARN_ON_DYNAMIC_IMPORT` (default), `WARN_ON_UNINTENTIONAL_PASSTHROUGH`, `RAISE_ON_UNINTENTIONAL_PASSTHROUGH`. The sandbox "is not completely isolated", so it lowers the risk without removing it.

**SDK:** all in 1.30.0: `SandboxedWorkflowRunner` (`temporalio/worker/workflow_sandbox/_runner.py:63`), `with_passthrough_modules` and `with_import_notification_policy` (`_restrictions.py:175`, `:189`), `SandboxImportNotificationPolicy` (`temporalio/workflow/_sandbox.py:26`), `imports_passed_through` and `sandbox_unrestricted` (`:135`, `:108`). `@workflow.defn(sandboxed=False)` sends one type to the worker's unsandboxed runner, so sandboxed and unsandboxed types share one worker.

**OpenCompany today:**
- All 14 production workflow definitions pass `sandboxed=False`: `workflow.py:201`, `agent_workflow.py:481` and `:731`, `pi_workflow.py:22`, `pi_inline_delegation.py:317`, `trigger_listener_workflow.py:121`, `polling_trigger_workflow.py:83`, `workflow_control_workflow.py:45`, `node_invocation.py:14`, `workspace_tasks_workflow.py:37`, `employee_job_workflow.py:70` and `:149`, `approved_tool_call_workflow.py:33`, `server/nodes/scheduler/cron_scheduler/_workflow.py:40`.
- What runs unchecked in workflow code: `Settings()` (file and environment reads) in `_default_max_iterations` (`agent_workflow.py:163-175`, a fallback when a payload lacks `max_iterations`) and in routing fallbacks (`workflow.py:974`, `:1061`); the live plugin registry deciding activity names (RFC-0004 C2, now guarded by the golden test); a user-supplied regular expression on the workflow thread (RFC-0004 M10); logging from workflow code (guarded by `is_replaying` in the interceptor). Module-level state in workflow modules is shared by every run in the process.
- Why it is off is not documented; `server/services/temporal/__init__.py` registers WebSocket handlers on import, and the workflow modules import app packages.
- The replay gate replays unsandboxed too, so determinism is checked only on recorded paths. No determinism lint exists (RFC-0004 §8.1, AG8).

**Verdict:** Misused. The documented default is the sandbox with explicit passthrough. Skipping it everywhere removes the only runtime check in a codebase whose rule is that every command change needs a patch or a flag.

**Recommendation:**
1. The lints, one commit (1b.40):
   - The determinism lint of RFC-0004 §8.1: an AST walk over workflow code, meaning the methods of `@workflow.defn` classes and the helpers they call, not activities that share the module (§5 rule 3). It fails on the clock, randomness, `uuid4`, `os.environ`, `Settings()`, I/O, `asyncio.wait` and `asyncio.as_completed` (`workflow.wait` and `workflow.as_completed` are the deterministic forms), stdlib or structlog loggers, `except Exception` around awaited work without a cancellation re-raise, and a `wait_condition` whose timeout is not first checked for zero or less (§8.4 rec 2). It lists plain `raise` statements for review (17.16 item 4).
   - The exit-cleanup rule (new, 18.2): cleanup that awaits a workflow operation inside `finally`, `except Exception` or `except BaseException` must first re-raise a bug (`is_workflow_bug`).
   - The orchestration checks of §6.1: a child start without explicit `parent_close_policy`, `cancellation_type` and `id_reuse_policy`, and an `execute_activity` or `start_activity` call inside a `@workflow.signal` handler. The same commit writes out the current defaults at the child starts that rely on them; passing a default explicitly changes nothing on replay.
   - The blocking-call lint of RFC-0004 §9.1 over plugin operations (17.19).

   Versioning: none. When: Phase 1b (1b.40), after the bug guard (1b.13), without which the exit-cleanup rule fails on today's tree, and after the agent-child policies (1b.12, §6.1). Effort S. Risk: false positives; an explicit allow comment with a reason. Test: `server/tests/temporal/test_workflow_lint.py` passes on the tree (RFC-0004 AG8) and fails on seeded violations: an `asyncio.wait` in a workflow module, a child start without `parent_close_policy`, an `execute_activity` in a `@workflow.signal` handler, and command-issuing cleanup in `finally`.
2. Every new RFC-0005 type (ConversationWorkflow, EmployeeSupervisorWorkflow, ApprovalWorkflow, ScheduleFireWorkflow and PollWorkflow, T7's poll workflow) is sandboxed: no `sandboxed=False`, app modules imported through `imports_passed_through()`, tests run with `RAISE_ON_UNINTENTIONAL_PASSTHROUGH`, production keeps the default warning policy, and a golden test asserts new types are sandboxed. Everything a sandboxed type imports must sit in packages without import side effects; `services.temporal`'s `__init__` registers handlers, so shared contracts live in a leaf package such as RFC-0005's `services/bus/`. Versioning: new types. When: with each new type, from RFC-0005 Phase 3 (PollWorkflow) and Phase 4. Effort S. Risk: a restricted call on a rare path fails that run's workflow task; tests with the RAISE policy find most of them first.
3. Sandbox the small existing types (ApprovedToolCallWorkflow, NodeInvocationWorkflow, EmployeeJobDeliveryWorkflow, CronTriggerWorkflow) once the corpus replays green under the sandboxed runner; the large types stay unsandboxed until their imports are free of side effects. Turning the sandbox on changes no commands, but replay every fixture under it first. Until then all 14 existing types stay unsandboxed under the determinism lint (1b.40). When: later. Effort M.

**Remarks:**
- The sandbox re-imports non-passed-through modules for every run; passthrough avoids that cost. Measure workflow-task latency (`[TMPRL1104]`, metrics) before and after.
- The lint stays even after the sandbox is on.

**Sketch** (item 2; 1.30.0):

```python
# services/temporal/conversation_workflow.py (new type, RFC-0005 Phase 4): sandboxed.
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from services.bus.contracts import DeliverBatch    # pure dataclasses, no import side effects

@workflow.defn(name="ConversationWorkflow")             # no sandboxed=False
class ConversationWorkflow:
    ...

# services/temporal/worker.py: one runner for the Worker and the Replayer (17.17).
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

def workflow_runner(*, testing: bool) -> SandboxedWorkflowRunner:
    policy = (workflow.SandboxImportNotificationPolicy.RAISE_ON_UNINTENTIONAL_PASSTHROUGH if testing
              else workflow.SandboxImportNotificationPolicy.WARN_ON_DYNAMIC_IMPORT)
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default
        .with_passthrough_modules("services.bus.contracts")
        .with_import_notification_policy(policy))

# Worker(client, ..., workflow_runner=workflow_runner(testing=False)): new types run
# sandboxed; legacy types keep sandboxed=False and use the unsandboxed runner.
```

### 17.19 Python sync vs async activities

**Docs:** https://docs.temporal.io/develop/python/best-practices/python-sdk-sync-vs-async. One event loop runs async activities and the rest of the Worker, including its traffic with the server; a blocking call in an async activity can freeze the worker and block workflow progress. "By default, Activities should be synchronous rather than asynchronous"; use async only when nothing blocks; offload blocking calls with `asyncio.to_thread` or `run_in_executor`. Workflow tasks run in a thread pool. "The only ways to use more than one core in a python Worker ... are: Run more than one Worker Process" or a process pool for sync activities; some users "deploy separate Workers for Workflow Tasks and Activity Tasks". "If you experience bugs that you think may be a result of an unsafe call being made in an asynchronous Activity, convert it to a synchronous Activity."

**SDK:** `Worker(activity_executor=, workflow_task_executor=)` in 1.30.0; the SDK refuses sync activities without an `activity_executor`. The SDK decodes each activity's input on the worker's event loop (`temporalio/worker/_activity.py:606`).

**OpenCompany today:**
- All 48 `@activity.defn` functions under `server/services` and `server/nodes` are `async def`, and so is the per-type activity every node plugin gets from `BaseNode.as_activity` (`server/services/plugin/base.py:1093-1115`). No `activity_executor` anywhere.
- The embedded workers run on uvicorn's event loop: `main.py:393-395` schedules `run_temporal_lifecycle`, which starts the manager and the pool on that loop (`server/services/temporal/lifecycle.py:190-239`). The same loop serves HTTP, every WebSocket, the status broadcaster, heartbeats and, in RFC-0005, the relay.
- Good practice exists in places: the filesystem backend runs subprocesses through `asyncio.to_thread` (`server/nodes/filesystem/_backend.py:67`); Pi resolves its host off the loop (`pi_activities.py:197`). CPU work stays on the loop elsewhere: each LLM step measures the whole conversation with `json.dumps` (`agent_activities.py:671`), and the SDK decodes each step's transcript input there.
- No event-loop lag measurement (grep); RFC-0005 §18.4 planned "event-loop lag" for Phase 8, and this RFC moves it to Phase 1b (1b.39).
- Topology: T8 decides that hosted tier S runs the Temporal workers in a second process and the desktop stays one process; activities bound to the backend process are routed first.

**Verdict:** Adopted with deviations. Async activities are allowed when nothing blocks, but OpenCompany depends on every plugin never blocking, on the one loop that also serves the API and every heartbeat. T8 brings hosted tier S in line with the docs' separate-process option; the desktop keeps the risk.

**Recommendation:**
1. An event-loop lag watchdog (sketch): a task that sleeps 100 ms and records how late it wakes, with a WARN line and a metric above 250 ms and an attention item when lag stays above 250 ms for 60 s; `loop.slow_callback_duration` in development. Versioning: none. When: Phase 1b (1b.39; RFC-0005 §18.4 had it in Phase 8). Effort S. Risk: none. Test: blocking the loop for 500 ms records lag above 250 ms and logs a WARN line. The blocking-call lint of RFC-0004 §9.1 over plugin operations ships with the lints of 17.18 (1b.40).
2. T8: on hosted tier S, a second process runs the Temporal workers, started by the deploy logic as its own service. Before the split, the process-affinity routing (`placement_version=1`) and status relay of §15.2 (rec 3), the one worker builder (§15.3, 1b.36) and durable Claude Code and RLM delegations (§6.2, §22.4) land, and the worker process reaches parity (17.9 item 2); an instance turns the split on only when every live generation carries `placement_version=1`. Workers hold no state, so the split is a deploy-logic change in the window, not a Temporal change. When: RFC-0005 Phase 9. Effort M. Risk: the second Python process's memory on small VMs is unmeasured; EG14 measures it.
3. Do not convert plugins to sync activities wholesale. Offload known blocking or CPU-heavy calls with `asyncio.to_thread` (the docs' alternative), starting with the conversation size check at `agent_activities.py:671`. Versioning: none (activity bodies). When: as the blocking-call lint (1b.40) finds them. Effort S per call. Risk: code moved to a thread must not touch loop-bound objects.

**Remarks:**
- A blocked loop delays heartbeats; a heartbeat timeout retries an activity that may have sent something, so loop health is also a duplicate-send risk (T13's resend guard covers declared sends).
- The 10 workflow-task threads hold the GIL while they replay, so heavy replays slow HTTP even when the loop is healthy, and the reverse is also likely: a long CPU-bound stretch on the loop can push a workflow activation past the 2 s deadlock limit (inference, not observed). One process uses one core for Python work.

**Sketch** (item 1; plain asyncio):

```python
# core/loop_watchdog.py (new): started in main.py's lifespan before Temporal.
import asyncio
import time

async def watch_event_loop(record, *, interval_s: float = 0.1, warn_s: float = 0.25,
                           attention_after_s: float = 60.0) -> None:
    """Measure how late the loop wakes a 100 ms sleep. Lag here delays every
    heartbeat, WebSocket frame and workflow-task completion in the process."""
    lagging_since = None
    while True:
        started = time.monotonic()
        await asyncio.sleep(interval_s)
        lag = time.monotonic() - started - interval_s
        record("event_loop_lag_seconds", lag)                 # metric (17.10)
        if lag < warn_s:
            lagging_since = None
            continue
        lagging_since = lagging_since or started
        if time.monotonic() - lagging_since > attention_after_s:
            record("event_loop_lag_sustained", 1)             # becomes an attention item
```

## 18. Python samples

**Source.** temporalio/samples-python at commit 33f7dc29 (2026-10-22), 57 sample folders, pinned to `temporalio>=1.34.0,<2` (`pyproject.toml:9`). Every file of the folders below was read, with their tests:
- **Core SDK features (reviewed here, 33 folders):** `hello` (22 programs), `message_passing`, `polling`, `updatable_timer`, `sleep_for_days`, `schedules`, `patching`, `replay`, `worker_specific_task_queues`, `worker_multiprocessing`, `worker_versioning`, `custom_decorator`, `resource_pool`, `batch_sliding_window`, `dsl`, `eager_wf_start`, `encryption`, `external_storage`, `external_storage_redis`, `pydantic_converter`, `pydantic_converter_v1`, `custom_converter`, `env_config`, `context_propagation`, `custom_metric`, `prometheus`, `open_telemetry`, `sentry`, `gevent_async`, `trio_async`, `lambda_worker`, `activity_worker` and `tests`.
- **AI samples (13, in §16):** `bedrock`, `bedrock_agentcore`, `deepagents_plugin`, `google_adk_agents`, `google_genai`, `langfuse_tracing`, `langgraph_plugin`, `langsmith_tracing`, `litellm_activity`, `openai_agents`, `openrouter`, `strands_plugin`, `workflow_streams`.
- **Nexus and standalone activities (8, in §19):** `hello_nexus`, `hello_standalone_activity` and the six `nexus_*` folders.
- **Not about the SDK (3):** `cloud_export_to_parquet` and `gcp` (Temporal Cloud and Cloud Run examples) and `scripts` (repository tooling).

**SDK status.** Every SDK call these samples make exists in the installed 1.30.0, without exception. That includes `ExternalStorage` (marked experimental), `MetricBuffer`, `PollerBehaviorSimpleMaximum`, `Runtime.prevent_default`, `WithStartWorkflowOperation`, `workflow.wait`, `@workflow.init`, `ApplicationErrorCategory.BENIGN`, `WorkerDeploymentConfig` and `max_workflow_task_external_storage_concurrency` (`temporalio/worker/_worker.py:148`). A scan of every `temporalio` name the reviewed folders import or access (177 pairs) found nothing missing. The 1.34.0 additions `patch_activation_callback`, `event_groups` and `StorageDriverSelectContext` are used by no sample.

### 18.1 The 35 design patterns and their Python samples

"Page link" means the pattern page itself links the sample. Two page links point to folders that do not exist at this commit (`samples-python/start_delay`, `samples-python/early_return`); the Pick First and Saga pages link only the repository root.

| # | Pattern | Python sample at 33f7dc29 | Note |
|---|---|---|---|
| 1 | Child Workflows | `hello/hello_child_workflow.py` (page link); also `batch_sliding_window`, `polling/periodic_sequence` | fixed child id; no `ParentClosePolicy` shown |
| 2 | Parallel Execution | `hello/hello_parallel_activity.py` (page link); `custom_metric/workflow.py`; `dsl/workflow.py` | `asyncio.gather`: the first failure raises and the others keep running (`dsl` says so) |
| 3 | Pick First | no Python sample | closest: `sleep_for_days` and `waiting_for_handlers_and_compensation` race tasks with `workflow.wait`; `nexus_cancel` is §19 |
| 4 | Signal with Start | no core sample: the page's two links (`hello_signal.py`, `message_passing/introduction`) start, then signal | `bedrock/entity` and `bedrock/signals_and_queries` use `start_signal` (§16.17) |
| 5 | Request-Response via Updates | `message_passing/safe_message_handlers` (page link), `introduction`, `hello/hello_update.py`, `update_with_start/lazy_initialization` | validators; `ApplicationError` fails an Update |
| 6 | Event Accumulator | no Python sample | closest: `hello_signal.py` drains a queue; `batch_sliding_window` collects completion Signals |
| 7 | Entity Workflow | `message_passing/safe_message_handlers` (page link); `resource_pool`; `lazy_initialization` | 18.3 |
| 8 | Continue-As-New | `safe_message_handlers` (page link); `hello_continue_as_new.py`; `batch_sliding_window`; `polling/periodic_sequence`; `resource_pool` | handler wait before continuing (18.3) |
| 9 | Updatable Timer | `updatable_timer` (page link) | moved by Signal, no deadline check (18.7) |
| 10 | Polling | `polling/frequent`, `infrequent`, `periodic_sequence` (page links); `polling/test_service.py` | BENIGN; T7 takes `infrequent` (18.6) |
| 11 | Long-Running Activity | `hello/hello_cancellation.py`, `custom_decorator/activity_utils.py` (page links); `hello_activity_heartbeat.py`; `polling/frequent` | no heartbeat-details resume example (18.14) |
| 12 | Delayed Start | no Python sample (dead link) | SDK: `start_delay` (`temporalio/client/_client.py:414`) |
| 13 | Delayed Callback | none dedicated | task token: `hello_async_activity_completion.py` (18.15); durable sleep: `sleep_for_days` |
| 14 | Approval | `hello/hello_signal.py`, `message_passing/introduction` (page links) | Signal plus `wait_condition(approved and all_handlers_finished())` |
| 15 | Saga | no Python sample | compensation shape: `waiting_for_handlers_and_compensation` (18.2); cleanup in `finally`: `hello_cancellation.py` |
| 16 | Early Return | no Python sample (dead link) | closest: `lazy_initialization` (Update-With-Start, `USE_EXISTING`) |
| 17 | Fixed Count of Retries | `hello_exception.py` (`maximum_attempts=2`); `safe_message_handlers`, `polling/periodic_sequence`, `worker_specific_task_queues` (1 each) | none inspects the retry state |
| 18 | Fixed Wall-Time Retries | no sample with a budget plus retries | `patching/workflow_*.py` set `schedule_to_close_timeout` alone |
| 19 | Non-Retryable Errors | `external_storage_redis/_driver.py:203` (`non_retryable=True`); `openrouter` (§16.16) | |
| 20 | Delayed Retry | no core sample; `openrouter/activities.py` uses `next_retry_delay` (§16.16) | OpenCompany already does it (`agent_activities.py:285-303`) |
| 21 | Fast/Slow Retries | no Python sample | |
| 22 | Retry Alerting via Metrics | closest: `custom_metric` (a histogram in an activity interceptor) | 18.5 |
| 23 | Resumable Activity | `openrouter/budget_gate` (§16.16): parks on missing credits until a `raise_budget` Update | the shape T11 decides (§11.8) |
| 24 | Fan-Out with Child Workflows | `batch_sliding_window/batch_workflow.py` | |
| 25 | Batch Iterator | `batch_sliding_window/sliding_window_workflow.py` | pages records, continues as new |
| 26 | Sliding Window | `batch_sliding_window` (the page links Java) | children signal the parent by id without a run id |
| 27 | MapReduce Tree | no Python sample | |
| 28 | Downstream Rate Limiting | no Python sample | SDK: `max_task_queue_activities_per_second` (`temporalio/worker/_worker.py:128`) |
| 29 | Priority | no Python sample | SDK: `Priority` (`temporalio/common.py:1165`) |
| 30 | Fairness | no Python sample | SDK: `fairness_key`, `fairness_weight` (`temporalio/common.py:1177-1188`) |
| 31 | Local Activities | `hello/hello_local_activity.py`, `eager_wf_start/workflows.py` | 18.19 |
| 32 | Early Return + Local Activities | no Python sample | |
| 33 | Eager Workflow Start | `eager_wf_start` | 18.19 |
| 34 | Worker-Specific Task Queues | `worker_specific_task_queues` (page link) | omits the `schedule_to_start_timeout` the page requires (18.11) |
| 35 | Activity Dependency Injection | `hello/hello_activity_method.py` (page link); `hello_async_activity_completion.py` (client injected); `batch_sliding_window` (`RecordLoader`); `dsl` (`DSLActivities`) | §15.3 |

Twelve patterns have no Python sample anywhere at this commit: Pick First, Event Accumulator, Delayed Start, Saga, Early Return, Fixed Wall-Time Retries (as described), Fast/Slow Retries, MapReduce Tree, Downstream Rate Limiting, Priority, Fairness, and Early Return + Local Activities. For them the pattern page's own code is the only Python reference.

### 18.2 Exit cleanup and handler waits

**Samples:** `message_passing/waiting_for_handlers` and `message_passing/waiting_for_handlers_and_compensation` (https://github.com/temporalio/samples-python/tree/33f7dc29b2c68f01b65155b3b9a570dac67fabe0/message_passing). Related pages: https://docs.temporal.io/design-patterns/entity-workflow and https://docs.temporal.io/design-patterns/saga-pattern.
- Wait for handlers, and compensate, only when the run is really exiting: `try: result = await logic(); await workflow.wait_condition(workflow.all_handlers_finished); return result` and `except BaseException as e: if is_workflow_exit_exception(e): await workflow.wait_condition(...); raise`, with `is_workflow_exit_exception = isinstance(e, (asyncio.CancelledError, exceptions.FailureError))` (`waiting_for_handlers/workflows.py:16-61`).
- Why, in the sample's words: "Use this `try...except` style, instead of waiting for message handlers to finish in a `finally` block. The reason is that some exception types cause a workflow task failure as opposed to workflow exit, in which case we do *not* want to wait for message handlers to finish" (`:48-52`), and "If you have set additional failure_exception_types you should also check for these here" (`:23-24`).
- The compensation variant races each Update against a `workflow_exit` future with `workflow.wait([...], return_when=asyncio.FIRST_EXCEPTION)` ("Always use `workflow.wait` instead of `asyncio.wait` in Workflow code"), fails the Update with an `ApplicationError` caused by the workflow's exception, and compensates only for exit exceptions (`waiting_for_handlers_and_compensation/workflows.py:48-127`).

**SDK:** all in 1.30.0: `workflow.wait` (`temporalio/workflow/_asyncio.py:72`), `workflow.all_handlers_finished` (`workflow/_workflow_ops.py:1013`), `exceptions.FailureError` (`exceptions.py:25`). Why the test matters: an exception leaving workflow code fails the workflow if it is a `FailureError`, an `asyncio.TimeoutError` or a configured failure type, and fails only the workflow task otherwise (`temporalio/worker/_workflow_instance.py:1824-1839`, `:2614-2616`). `ContinueAsNewError` derives from `BaseException` (`workflow/_workflow_ops.py:662`), so it reaches `finally` and `except BaseException` but not `except Exception`. At eviction from the cache the SDK cancels every task (`_workflow_instance.py:798-807`) and every workflow operation raises `_WorkflowBeingEvictedError` (`:2142-2148`), so nothing is recorded then. The sample's test omits `asyncio.TimeoutError`.

**OpenCompany today:** eight workflow types run exit cleanup (activities, cancellations, handler waits) for every exception, in `finally`, `except Exception` or `except BaseException`. No `workflow_failure_exception_types` is set anywhere (grep), so a plain Python exception from workflow code is a bug that fails the workflow task. The cleanup awaits an activity, so it completes that workflow task with the cleanup's commands recorded; the bug is raised again in a later task. A fixed release then no longer reaches the cleanup at that point, replay fails with a non-determinism error, and the run cannot resume. Meanwhile the app database says "failed" or "unregistered" for a run that is still open.

| Workflow | Exit code | What a bug records today |
|---|---|---|
| MachinaWorkflow | `finally`: releases action contexts, unregisters the root, waits for handlers (`server/services/temporal/workflow.py:499-513`); only with execution control (`:503-504`) | the `execution_control.unregister` activity: the controller hears the root ended while the run keeps retrying |
| CronTriggerWorkflow | `finally`: unregister and handler wait (`server/nodes/scheduler/cron_scheduler/_workflow.py:82-86`); only with execution control | the same |
| EmployeeJobWorkflow | `run` `finally` (`server/services/temporal/employee_job_workflow.py:105-109`, with execution control); `_run_job` `except BaseException` runs `employee.job.failed` under `asyncio.shield` (`:121-126`) | the job marked failed or cancelled; unregister |
| AgentWorkflow | `except Exception: await fail_job(...)` (`agent_workflow.py:838-841`, records only with employee runtime v2); `finally`: browser claim release and handler wait (`:842-859`) | `employee.job.failed`, `browser_tasks.release` |
| DelegatedTaskWorkflow | `except Exception`: `agent.finish_delegation` with `success: False` (`agent_workflow.py:692-702`); `finally`: permit release, unregister, handler wait (`:703-728`) | the team task failed, the permit released, unregister |
| Delegation helpers in AgentWorkflow | `except BaseException`: release the permit or sweep cancelled delegations, then re-raise (`agent_workflow.py:1728-1730`, `:1762-1765`, `:2030-2032`, `:2268-2272`) | permit release and cancellation activities |
| PiInlineDelegationWorkflow | `except Exception: error = ...; raise`, then `finally`: `agent.cancel_delegation`, status records, unregister, handler wait (`server/services/temporal/pi_inline_delegation.py:374-400`) | the delegation cancelled with the bug's text |
| NodeInvocationWorkflow | `except Exception` maps errors, then `finally`: `workspace_tasks.update_record` and `workflow_runs.record_completion` (`server/services/temporal/node_invocation.py:55-103`) | the task and run recorded as failed |
| PiCodingAgentWorkflow | `except Exception` returns `{"success": False, ...}` (`server/services/temporal/pi_workflow.py:137-146`); `finally` stops the driver (`:147-150`) | the bug is hidden: the run completes as a normal failure |

Three more types swallow bugs per event, log, and keep recording commands, so a fixed release fails replay there too and the event is lost or retried forever instead of stuck: WorkflowControlWorkflow per event (`workflow_control_workflow.py:464-479`) and per poll (`:849-852`, `:861-864`, with bookkeeping inside the `try`), TriggerListenerWorkflow (`trigger_listener_workflow.py:278-284`), and PollingTriggerWorkflow per cycle and per event (`polling_trigger_workflow.py:193-198`, `:222-226`).

Conditions: an uncontrolled MachinaWorkflow, CronTriggerWorkflow or EmployeeJobWorkflow, and an AgentWorkflow without browser claims or employee runtime v2, record nothing on a bug and stay replayable today. A bug inside a child never reaches the parent as a Python exception; the child's own task fails.

**Verdict:** Misused. It breaks the policy RFC-0004 §8.7 recommends and RFC-0005 §10.3 assumes: "A bug in workflow code leaves that entity or supervisor stuck and visible", resumed by a fixed release. RFC-0004 §8.1's lint as first planned would not catch it, because every one of these blocks re-raises; §21 adds the rule (item 3 below).

**Recommendation:**
1. Add `server/services/temporal/_exits.py` (sketch) with tests that mirror the SDK's rule: `is_failure_exception` (`FailureError`, `asyncio.TimeoutError`, plus any configured failure type), `is_workflow_bug` (an `Exception` that is not a failure: the SDK fails the task, not the run) and the sample's `is_workflow_exit_exception` (cancellation or failure).
   - Legacy types put `if is_workflow_bug(exc): raise` in front of every exit cleanup above. Cleanup is skipped only for a bug; success, failure, timeout, cancellation and continue-as-new keep exactly today's commands. Continue-as-new matters here: PiCodingAgentWorkflow's `finally` cancels its driver activity before the run continues (`pi_workflow.py:147-150`), so the samples' test, which skips cleanup on continue-as-new, would change commands in a shared type.
   - Every per-event and per-poll handler re-raises a bug the same way, so the task fails and the event stays queued (replay rebuilds the state from the last completed task) while activity and child failures keep today's isolation.
   - PiCodingAgentWorkflow re-raises a bug instead of returning `success: False`. A stuck Pi run keeps its driver until a fix or Reset (Reset terminates the run, and the driver activity ends at its next heartbeat).
   - Versioning: none. Commands are identical on every path that completes a workflow task; only paths that end in a failed workflow task change, and the replay corpus refuses histories that contain one (`test_replay_corpus.py:65`). The corpus must pass.
   - When: Phase 1b (1b.13), before RFC-0005 Phases 3 and 4, so new types copy the right shape. Effort M (about 13 sites in 7 files, plus the per-event handlers in 3 files). Risk: low. A run that already hit a bug and recorded cleanup is stuck today and stays stuck; T14 marks its generation "needs Reset". A controller with a bug in its spawn code now stops instead of dropping events; its queued events stay in its state, and Reset remains the way out (T4).
2. Tests: one per workflow type, in a time-skipping or local environment, with workflow code made to raise a plain `RuntimeError` (a monkeypatched helper). Assert that the run stays open with a failed workflow task and that no cleanup activity was scheduled. For PiCodingAgentWorkflow, also assert that continue-as-new still cancels the driver. The replay corpus stays green. When: Phase 1b, in the same commit (1b.13).
3. The determinism lint gains the matching rule: cleanup that awaits a workflow operation inside `finally`, `except Exception` or `except BaseException` must first re-raise a bug (`is_workflow_bug`). 17.18 owns the lint (1b.40), which lands after this fix.
4. Every new RFC-0005 type uses the samples' shape from the start: `try` / `except BaseException` with `is_workflow_exit_exception`, an explicit handler wait right before `workflow.continue_as_new(...)`, never cleanup in `finally`, and `workflow.wait`, never `asyncio.wait`. New types have no history, so they can skip cleanup on continue-as-new. When: with each new type, from RFC-0005 Phases 3 and 4.

**Remarks:**
- This is RFC-0004 Q5 in code; the default chosen (17.16 item 6) is stuck and visible. With fail-fast (`workflow_failure_exception_types=[Exception]`) the `finally` form would be nearly right, but every open run would fail on an incompatible upgrade, because `NondeterminismError` derives from `Exception` (`temporalio/workflow/_exceptions.py:17`; the worker passes the setting to Core, `worker/_workflow.py:809-813`).
- If a workflow type ever sets `failure_exception_types`, the tests must list the same types, as the sample's comment says; a unit test compares `_FAILURE_TYPES` with the Worker's configuration.
- At eviction from the cache the SDK cancels every task, so cleanup code sees a cancellation; nothing is recorded because workflow operations then raise.
- A bug leaves a supervisor running with a failed workflow task. T20's rebuild in place covers a supervisor that was terminated or failed, so it does not fire on a bug: the fixed release resumes the supervisor, and Reset remains the way out.

**Sketch** (1.30.0):

```python
# server/services/temporal/_exits.py (new)
"""When workflow code may clean up (samples-python message_passing/waiting_for_handlers).
A bug fails the workflow task and must record nothing, so a fixed release replays
and resumes the run (RFC-0004 §8.7, RFC-0005 §10.3)."""
import asyncio

from temporalio import exceptions

# Mirrors workflow_is_failure_exception (worker/_workflow_instance.py:1824-1839).
# Add every type a Worker or @workflow.defn lists in its failure exception types.
_FAILURE_TYPES: tuple[type[BaseException], ...] = (exceptions.FailureError, asyncio.TimeoutError)

def is_failure_exception(exc: BaseException) -> bool:
    """The exception fails the run (or one event), not the workflow task."""
    return isinstance(exc, _FAILURE_TYPES)

def is_workflow_bug(exc: BaseException) -> bool:
    """A plain exception from workflow code. Cancellation, continue-as-new and
    eviction are BaseExceptions, so they are never bugs."""
    return isinstance(exc, Exception) and not is_failure_exception(exc)

def is_workflow_exit_exception(exc: BaseException) -> bool:
    """The sample's test, for new types: failure or cancellation."""
    return isinstance(exc, asyncio.CancelledError) or is_failure_exception(exc)

# server/services/temporal/workflow.py, MachinaWorkflow.run (replaces :505-513).
# Every path but a bug keeps today's commands.
        await self._execution_control.register_root()
        try:
            result = await self._run_graph(workflow_data)
        except BaseException as exc:
            if not is_workflow_bug(exc):
                await self._close_root()   # action contexts, unregister_root, handler wait
            raise
        await self._close_root()
        return result

# workflow_control_workflow.py:464 (and :851, :863, trigger_listener_workflow.py:278,
# polling_trigger_workflow.py:193 and :222): per-event isolation for failures only.
            except Exception as exc:  # noqa: BLE001
                if is_workflow_bug(exc):
                    raise                  # stuck and visible; the event stays queued
                ...                        # unchanged handling of an activity or child failure
```

### 18.3 Message handlers and Update-With-Start

**Samples:** `message_passing/introduction`, `message_passing/safe_message_handlers`, `message_passing/update_with_start/lazy_initialization`, and `hello/hello_signal.py`, `hello_update.py`, `hello_query.py`. Related pages: https://docs.temporal.io/design-patterns/request-response-via-updates, `/entity-workflow`, `/continue-as-new`, `/approval`, `/early-return`.
- `introduction`: a Query, a Signal, a sync Update with a validator, and an async Update that runs an activity under an `asyncio.Lock`; the run waits for `approved_for_release and workflow.all_handlers_finished()`. A validator rejects before anything is written to history; an `ApplicationError` in an async Update fails only that Update. The starter uses `start_update(..., wait_for_stage=WorkflowUpdateStage.ACCEPTED)`.
- `safe_message_handlers`: an entity with `@workflow.init` (reads the input before any handler runs), an `asyncio.Lock` around each handler that awaits, idempotent Updates, and `ApplicationError` rather than `ValueError` in Update bodies, because "other exceptions in the main handler will cause the workflow to keep retrying and get it stuck". It continues as new when `is_continue_as_new_suggested()` (plus a test-only history cap), after `wait_condition(all_handlers_finished)`, and runs its health check with `maximum_attempts=1` because "our lock would block the whole workflow if we let it retry forever".
- `lazy_initialization`: `client.execute_update_with_start_workflow(update, arg, start_workflow_operation=WithStartWorkflowOperation(..., id_conflict_policy=USE_EXISTING))`; a `WorkflowUpdateFailedError` handled by its cause's type; `await start_op.workflow_handle()` for the handle even when the Update failed.

**SDK:** all in 1.30.0 and unchanged in 1.34.0: `all_handlers_finished` (`temporalio/workflow/_workflow_ops.py:1013`), `@workflow.init` (`workflow/_definition.py:104`), `Info.is_continue_as_new_suggested` and `get_current_history_length` (`workflow/_context.py:186`, `:166`), `WorkflowHandle.start_update` (`client/_workflow.py:844`), `WorkflowUpdateStage` (`:1979`), `execute_update_with_start_workflow` (`client/_client.py:930`), `WithStartWorkflowOperation` (`client/_workflow.py:1038`) with `workflow_handle()` (`:1227`), `WorkflowUpdateFailedError` (`client/_exceptions.py:73`).

**OpenCompany today:**
- Validators on every control Update (`workflow_control_workflow.py:161-323`, `employee_job_workflow.py:78-92`); Update handler bodies raise `ApplicationError`, never bare exceptions (`server/services/temporal/execution_control.py:66-84`).
- WorkspaceTaskControllerWorkflow follows `safe_message_handlers` almost line for line: `@workflow.init` with the comment "Update-With-Start can invoke a handler before run()" (`server/services/temporal/workspace_tasks_workflow.py:39-52`), an admission lock (`:86`), idempotency by run id and fingerprint, a bounded dedup set carried across continue-as-new, and rollover only when `workflow.all_handlers_finished()` (`:198-210`).
- WorkflowControlWorkflow (`workflow_control_workflow.py:609`, `:661`, `:683`) and AgentWorkflow's durable path (`agent_workflow.py:3001`) wait for handlers before continuing as new.
- Update-With-Start: `server/services/node_invocations.py:44-74` makes the same call with `USE_EXISTING` and a stable Update id, and walks the cause chain to map `WorkspaceResetInProgress` and its siblings to `NodeUserError`.

**Verdict:** Adopted correctly.

**Recommendation:** none for the handler shape. The client side of these calls has no deadline, the Workspace Update-With-Start included; §7.3 bounds every Update call with `asyncio.timeout` (1b.15). RFC-0005's new entity types follow the same shape (its §10.1 already requires `@workflow.init`, buffering handlers and continue-as-new from the main loop after `all_handlers_finished()`).

**Remarks:**
- The `safe_message_handlers` sample holds a lock across an activity, which blocks every other handler on that lock while the activity retries. OpenCompany's lock covers only admission (a child start): the better scope.
- The early-return page's `id_conflict_policy=FAIL` suits one workflow per request. OpenCompany's Workspace controller is an entity, so `USE_EXISTING` is right.
- The samples skip Update tests on the time-skipping server (17.17 item 5).

### 18.4 Workflow basics (the `hello` programs)

**Samples:** 14 of the 22 programs in `hello/`; the other eight belong to 18.5 (`hello_change_log_level`), 18.9 (`hello_patch`), 18.14 (`hello_activity_heartbeat`, `hello_cancellation`), 18.15 (`hello_async_activity_completion`), 18.19 (`hello_local_activity`) and 18.21 (`hello_mtls`, `hello_activity_multiprocess`). Every program connects through `temporalio.envconfig.ClientConfig.load_client_connect_config()`.

**SDK and OpenCompany today:** every call is in 1.30.0.

| Program | Calls | OpenCompany today |
|---|---|---|
| `hello_activity`, `_async`, `_choice` | a sync activity with `activity_executor=ThreadPoolExecutor`; an async activity; an activity chosen by input | all activities are async; the SDK refuses a sync one without an executor (`temporalio/worker/_activity.py:100-104`) |
| `hello_activity_method` | an instance-method activity, `workflow.execute_activity_method` (`workflow/_activities.py:974`) | `ExecutionControlActivities(self.client)` and `PiControlActivities(self.client)` (`worker.py:232`, `:272`); most activities reach `core.container` inside the body instead (§15.3) |
| `hello_activity_retry`, `hello_exception` | `RetryPolicy(maximum_interval=...)`, `maximum_attempts=2`, the stack trace from `FailureError.failure` | explicit policies in `_retry_policies.py:52-138` |
| `hello_child_workflow` | `workflow.execute_child_workflow` with a fixed id | agent, delegation and node-invocation children |
| `hello_continue_as_new` | `workflow.continue_as_new(iteration + 1)` (`workflow/_workflow_ops.py:679`) | the controller, both listener types, the agent types and the Workspace controller continue as new |
| `hello_parallel_activity` | `asyncio.gather` of five activities | MachinaWorkflow schedules ready nodes and waits for the first to finish with `workflow.wait_condition` (`workflow.py:1101-1133`): deterministic, equivalent to `workflow.wait(FIRST_COMPLETED)` |
| `hello_query`, `hello_signal`, `hello_update` | Queries after completion; an async Signal handler plus a `wait_condition` drain; an Update without a validator | handlers with validators throughout (18.3) |
| `hello_search_attributes` | the dict form of `upsert_search_attributes` and `search_attributes=`, deprecated in 1.30.0 (`workflow/_context.py:871-889` warns) | typed keys everywhere (`schedules.py:195-218`, `workflow_control_workflow.py:742-745`, `manager.py:936-939`) |
| `hello_cron` | `cron_schedule="* * * * *"` on a start | Schedules instead (`schedules.py:139-318`), the better choice |

**Verdict:** Adopted correctly.

**Recommendation:** none.

**Remarks:** the hello README still says Updates need `frontend.enableUpdateWorkflowExecution=true`; OpenCompany's controller Updates run on the dev server without it (`_runtime.py:143-165` passes no such flag). The root README lists `hello_activity_threaded`, which does not exist.

### 18.5 Observability: metrics, workflow-task failures and error reports

**Samples:** `custom_metric`, `prometheus`, `open_telemetry`, `hello/hello_change_log_level.py`, `sentry` and `context_propagation`. Related pages: https://docs.temporal.io/design-patterns/retry-metrics and https://docs.temporal.io/best-practices/worker-alerting.
- `custom_metric`, `prometheus`, `open_telemetry`: `Runtime(telemetry=TelemetryConfig(metrics=PrometheusConfig(bind_address=...)))` passed to `Client.connect(runtime=...)`; an activity interceptor records `activity.info().started_time - activity.info().current_attempt_scheduled_time` with `activity.metric_meter().create_histogram_timedelta(...)`, tagged by workflow type; `OpenTelemetryConfig(url=...)` for metrics plus `TracingInterceptor()` on the client only.
- `hello_change_log_level`: a `logging.Filter` raises records whose `__temporal_error_identifier` is `"WorkflowTaskFailure"` from WARNING to ERROR.
- `sentry`: a worker interceptor tags errors with workflow and activity identity inside `sentry_sdk.isolation_scope()`, reports workflow errors only when `not workflow.unsafe.is_replaying()`, inside `workflow.unsafe.sandbox_unrestricted()`, and attaches the activity or workflow input to every report (`sentry/interceptor.py:41-45`, `:68-71`).
- `context_propagation`: one class that is both a client and a worker interceptor, registered once on `Client.connect`, copies a `ContextVar` into headers on start, Signal, Query and Update and back on every inbound call.

**SDK:** all in 1.30.0: `PrometheusConfig` (`temporalio/runtime.py:367`), `OpenTelemetryConfig` (`:318`), `MetricBuffer` (`:417`, `retrieve_updates` `:450`: "If the buffer is full, metric updates will be dropped"), `activity.metric_meter()` (`temporalio/activity.py:461`), `current_attempt_scheduled_time` (`activity.py:107`), `workflow.unsafe.is_replaying` and `sandbox_unrestricted` (`workflow/_sandbox.py:61`, `:108`). The SDK logs a failed workflow task at WARNING with `__temporal_error_identifier = "WorkflowTaskFailure"` from the logger `temporalio.worker._workflow_instance` (`temporalio/worker/_workflow_instance.py:83`, `:511-518`). `ApplicationErrorCategory.BENIGN` (`exceptions.py:118`) downgrades an activity's failure log to DEBUG and records no failure metric (`worker/_activity.py:457-472`).

**OpenCompany today:**
- No SDK metrics (17.10). The interceptor logs `activity_retry` and failures (`_interceptors.py:42-83`) but records no meter.
- Only `ExpectedLLMRetryFilter` on `temporalio.activity` (`server/core/logging.py:256-283`, `:333-335`). A failed workflow task, which leaves a run stuck until a fix or a Reset, is logged at WARNING, and under "metrics and errors only" (E14) nothing reports it.
- No error-reporting service. The observability interceptor already guards workflow logs with `is_replaying()` (`_interceptors.py:86-99`).
- No BENIGN anywhere; retryable LLM errors are plain `ApplicationError`s (`agent_activities.py:297-303`).
- `TracingInterceptor` once on the client (`client.py:92-97`), the same rule as `context_propagation`. Application scope (owner, employee, run) travels in payload dicts.

**Verdict:** Missing and beneficial. RFC-0004 D6 planned the metrics part; nothing is built.

**Recommendation:**
1. Report a failed workflow task as ERROR: a filter on the `temporalio.worker._workflow_instance` logger, installed in `configure_logging` next to `ExpectedLLMRetryFilter` (sketch). Versioning: none (logging only). When: Phase 1b (1b.29). Effort S. Risk: none; it pairs with `TemporalReportedProblems` (17.10 item 2). Test: a workflow that raises `RuntimeError` under a test worker produces a log record at ERROR.
2. SDK metrics as in 17.10 item 1, plus the schedule-to-start histogram of `custom_metric` in the existing activity interceptor, tagged with activity type and task queue only. The retry counter belongs to §11.7 (RFC-0005 Phase 8), which counts attempts above a threshold, not every retry. Create the runtime before the client (both runtimes change). Any Prometheus endpoint binds `127.0.0.1`, never the sample's `0.0.0.0` (`custom_metric/worker.py:49`). Versioning: none. When: Phase 1b, with 1b.38. Effort M.
3. Mark expected retryable failures BENIGN: retryable LLM errors (`agent_activities.py:297-303`) and expected poll failures (18.6). Retries are unchanged; failure metrics then count only real failures, which `ExpectedLLMRetryFilter` cannot do. The interceptor's `activity_retry` line keeps retries visible (quieter under 1b.30, §11.7), and §11.7's retry alert (the attention item in 1b.30, the metric counter in RFC-0005 Phase 8) is then the signal of a retry storm. Versioning: none (activity only). When: Phase 1b (1b.28) for the LLM errors, in `_as_temporal_llm_error`; poll failures with 18.6. Effort S. Test: retryable errors carry `ApplicationErrorCategory.BENIGN` and others `UNSPECIFIED`; with 1b.38, a BENIGN retry adds no failure metric.
4. Error reports for hosted instances (E14) through a worker interceptor shaped like `sentry`, without its inputs: exception type, workflow type, activity type, task queue, attempt, release; never inputs, workflow ids (they contain workflow slugs) or messages. Versioning: none (interceptors issue no commands). When: RFC-0005 Phase 9a. Effort S. Risk: a traceback frame can quote content; send frames without local values, as RFC-0005 §18.5 says.
5. Optional: a header interceptor shaped like `context_propagation` for owner, employee and scheduling class, so every activity, plugins included, can attribute spend and gateway calls without threading fields. Headers are not compared on replay, so no versioning. Keep values small and never secret. When: later. Effort S.

**Remarks:** in-workflow spans have no duration by design (the `open_telemetry` README); spans are a separate decision (17.15 item 3).

**Sketch** (items 1 and 3; 1.30.0):

```python
# server/core/logging.py, next to ExpectedLLMRetryFilter
class WorkflowTaskFailureFilter(logging.Filter):
    """A failed workflow task leaves the run stuck until a fixed release or a Reset.
    The SDK logs it at WARNING (worker/_workflow_instance.py:511-518); report ERROR
    (samples-python hello/hello_change_log_level.py)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "__temporal_error_identifier", None) == "WorkflowTaskFailure":
            record.levelno, record.levelname = logging.ERROR, "ERROR"
        return True

# configure_logging(), after the activity filter
task_logger = logging.getLogger("temporalio.worker._workflow_instance")
if not any(isinstance(item, WorkflowTaskFailureFilter) for item in task_logger.filters):
    task_logger.addFilter(WorkflowTaskFailureFilter())

# server/services/temporal/agent_activities.py (:297-303): expected retries are BENIGN
from temporalio.exceptions import ApplicationErrorCategory

return ApplicationError(
    safe_message, details, type=f"LLMError.{category}",
    non_retryable=not details["retryable"], next_retry_delay=next_retry_delay,
    category=ApplicationErrorCategory.BENIGN if details["retryable"] else ApplicationErrorCategory.UNSPECIFIED,
)
```

### 18.6 Polling

**Samples:** `polling/frequent`, `polling/infrequent`, `polling/periodic_sequence` and `polling/test_service.py` (https://github.com/temporalio/samples-python/tree/33f7dc29b2c68f01b65155b3b9a570dac67fabe0/polling). Page: https://docs.temporal.io/design-patterns/polling.
- `frequent`: one long activity loops (poll, `activity.heartbeat(...)`, `asyncio.sleep(1)`) under `heartbeat_timeout=2s` and `start_to_close_timeout=60s`, swallowing "not ready" errors.
- `infrequent`: the activity raises while the result is not ready, and the retry policy is the poll loop: `RetryPolicy(backoff_coefficient=1.0, initial_interval=timedelta(seconds=60))`, unlimited attempts by default. Retries of one activity are not recorded in history.
- `periodic_sequence`: a child workflow tries 10 times with `maximum_attempts=1` and a 1 s durable sleep, then continues as new.
- `test_service.py:24-30` raises `ApplicationError(..., category=ApplicationErrorCategory.BENIGN)`: "BENIGN errors have activity failure logs downgraded to DEBUG level and do not emit activity failure metrics."

**SDK:** all in 1.30.0: `RetryPolicy`, `activity.heartbeat`, `workflow.continue_as_new`, `ApplicationErrorCategory.BENIGN` (`temporalio/exceptions.py:118`; the DEBUG downgrade at `worker/_activity.py:457-472`), and `ApplicationError(next_retry_delay=...)`, which overrides the interval for one retry.

**OpenCompany today:**
- The legacy PollingTriggerWorkflow is the periodic-sequence shape inside the listener: `workflow.sleep(poll_interval)`, one activity per cycle with `DEFAULT_ACTIVITY_RETRY`, an ERROR line for each failed cycle, and continue-as-new under history pressure carrying `seen_ids` (`server/services/temporal/polling_trigger_workflow.py:157-245`, `:193-198`). The controller polls the same way for new generations (`workflow_control_workflow.py:832-868`).
- No BENIGN anywhere (grep of `services`, `nodes`, `core`).
- T7 decides the new design: one small workflow per poll trigger runs a single activity that fetches, stores the new items and the cursor in the database in one transaction, then ends with a BENIGN error so Temporal retries it after the interval. That is the `infrequent` sample's shape with `test_service.py`'s BENIGN, with one twist: the activity ends BENIGN even after it found items, because items go to the database, not to the workflow. It replaces "one Schedule per poll" in RFC-0005 §15; with Schedules, 100 employees with 2 polls each at the plugins' default 60 s interval would start about 288,000 executions a day (est.).

**Verdict:** Adopted with deviations (the legacy listener: no BENIGN, and an ERROR line per failed cycle during a provider outage).

**Recommendation:**
1. T7's poll workflow (PollWorkflow) is the `polling/infrequent` sample's shape with `test_service.py`'s BENIGN, and §9.2 (with its sketch) owns the design: one activity retried without limit, no `schedule_to_close_timeout`, each attempt ending with a BENIGN `ApplicationError` whose `next_retry_delay` carries the interval. Like every new type, it runs in the sandbox (17.18 item 2). BENIGN hides every cycle from failure logs and metrics, so the poll's health signal must come from the database: §9.2 counts consecutive failures there and raises "driver down". Versioning: new workflow type. When: RFC-0005 Phase 3. Risk: an attempt that dies after its commit runs again at the next interval; the cursor makes it idempotent.
2. Legacy listeners: classify expected cycle failures (provider unreachable) as BENIGN in the poll activity. Versioning: none (activity only). When: later, or never if Phase 10 retires them first. Effort S.

**Remarks:**
- An activity waiting for its next attempt holds no worker slot, and "individual Activity retries are not recorded in Workflow History" (`polling/infrequent/README.md:25`).
- Neither sample handles a stream of new items: `infrequent` completes on the first ready result, and `periodic_sequence` is what the legacy listener already does.

### 18.7 Timers

**Samples:** `updatable_timer` and `sleep_for_days`. Page: https://docs.temporal.io/design-patterns/updatable-timer.
- `updatable_timer`: `UpdatableTimer.sleep()` loops: compute `wake_up_time - workflow.now()`, stop when it is not positive, clear the flag, `wait_condition(lambda: updated, timeout=interval)`, and loop on `asyncio.TimeoutError` (`updatable_timer/updatable_timer_lib.py:12-31`). `@workflow.init` builds the timer from the input; a Signal moves the deadline; a Query reads it. The deadline is an absolute UTC `datetime`.
- `sleep_for_days`: send an email, then `workflow.wait([create_task(workflow.sleep(timedelta(days=30))), create_task(workflow.wait_condition(lambda: self.is_complete))], return_when=FIRST_COMPLETED)`, in a loop until a `complete` Signal (`sleep_for_days/workflows.py:18-33`). Its test skips 90 days with `env.sleep`.

**SDK:** all in 1.30.0: `workflow.now` (`temporalio/workflow/_context.py:748`), `wait_condition(timeout=)` (`:920`), `@workflow.init` (`workflow/_definition.py:104`), `workflow.wait` (`workflow/_asyncio.py:72`).

**OpenCompany today:** no alarms yet. The approval Undo window is a `workflow.sleep` (`approved_tool_call_workflow.py:41-46`). RFC-0005 §15 plans one updatable timer per supervisor for the earliest due alarm, moved by an `alarms_changed` Signal that carries `next_due` and a version (RFC-0005 §10.2), with `alarms.fire_due` moving due alarms to `fired` by compare-and-swap.

**Verdict:** Planned in RFC-0005 (follows the docs).

**Recommendation:**
1. Use the sample's loop for the alarm deadline, one of the named deadlines of §8.4's one timer per workflow, with RFC-0005's version on each `alarms_changed`, so a late or out-of-order Signal is ignored. The page warns that "Not validating new deadlines" makes a timer expire at once; for alarms that is harmless, because the timer only runs `alarms.fire_due`, which reads the alarms table and fires only what is due. After a re-arm from the app database (I10), or when a lost supervisor is rebuilt in place (T20), read the earliest due alarm from the table, not from a carried value. Versioning: new type. When: RFC-0005 Phase 6. Effort S.
2. When racing a sleep against a condition, cancel the task that lost. `sleep_for_days` never does: its loop ends when the condition wins, so at most one timer is left until the run completes, but each round the sleep wins leaves a pending `wait_condition` task. The Pick First page cancels whatever lost the race. Versioning: new types (cancelling a timer is a command). When: RFC-0005 Phase 4 onward. Effort S.

**Remarks:**
- `wait_condition` with a timeout creates one timer per call, and Python cancels it when the condition wins, so no timers pile up (unlike the Java and Go pitfalls on the page).
- Timers are accurate only from 1 s up (T9). A turn suspended for budget waits without a timer (RFC-0005 §14). A parked turn (§11.8; RFC-0005 Phase 5, new generations only, T18) probes an outage on a 15-minute timer and is dead-lettered after 7 days (T19).
- The budget-period timer is jittered per employee (17.4 item 3).

### 18.8 Schedules

**Samples:** `schedules`. Page: https://docs.temporal.io/design-patterns/batch-processing-patterns. `create_schedule` with `ScheduleIntervalSpec` and `ScheduleState(note=...)`; `get_schedule_handle(...)` then `.backfill(ScheduleBackfill(..., overlap=ALLOW_ALL))`, `.trigger()`, `.pause(note=...)`, `.describe()`, `.delete()`; `list_schedules()`; `.update(callback)` returning `ScheduleUpdate`.

**SDK:** all in 1.30.0: `create_schedule` (`temporalio/client/_client.py:2675`), `get_schedule_handle` (`:2730`), `list_schedules` (`:2734`); `ScheduleSpec.jitter` (`client/_schedule.py:310`) and `pause_on_failure` (`:963`).

**OpenCompany today:** `server/services/temporal/schedules.py:139-318` creates Schedules, updates them in place through the callback while keeping the server-owned paused state, checks ownership, puts typed search attributes on the Schedule and its action, sets `SchedulePolicy(overlap=SKIP, catchup_window=timedelta(hours=24))` (`:226-229`), and pauses and unpauses with notes (`:407`, `:409`). No backfill, no trigger, no jitter, no `pause_on_failure` (OpenCompany has its own breaker).

**Verdict:** Adopted correctly.

**Recommendation:**
1. RFC-0005 §15's cron Schedules set `ScheduleSpec(jitter=timedelta(seconds=60))`, as planned. Versioning: none (Schedule specification). When: RFC-0005 Phase 6. Effort S.
2. `handle.trigger()` could serve a "Run now" button on a cron node without starting a separate path. Versioning: none. When: later. Effort S.

**Remarks:** under T7 polls no longer use Schedules, so jitter matters for cron only.

### 18.9 Patching

**Samples:** `patching` and `hello/hello_patch.py`. Four stages: the initial code; `if workflow.patched("my-patch")` keeps both branches; `workflow.deprecate_patch("my-patch")` once no run from before the patch remains; remove the call once no stage-2 run remains. A patch marker is the history event that `patched()` records on a run's first pass, so replay can tell which branch the run took.

**SDK:** `workflow.patched` (`temporalio/workflow/_context.py:761`) and `deprecate_patch` (`:552`) in 1.30.0. 1.34.0 adds `event_groups=` to both and a Worker option `patch_activation_callback(PatchActivationInput) -> bool` (1.34.0 `worker/_worker.py:143`; `PatchActivationInput` exported from `temporalio.worker`). The callback is consulted only for a new, non-replay `patched()` call; replay and deprecation use the markers as before (1.34.0 `worker/_workflow_instance.py:1462-1497`). When it returns False, `patched()` returns False and records no marker.

**OpenCompany today:** 19 patch ids at 24 `workflow.patched(...)` call sites (`agent_workflow.py` 11, `workflow.py` 5, `workflow_control_workflow.py` 6, `trigger_listener_workflow.py` 1, `pi_workflow.py` 1) and no `deprecate_patch` anywhere. The golden identity test locks the markers. RFC-0004 §8.6 and RFC-0005 §24.3 define retirement.

**Verdict:** Adopted with deviations (no retirement yet; planned).

**Recommendation:**
1. Retire markers in the samples' four stages, as RFC-0005 §24.3 says: `deprecate_patch` one release after no open history can lack the marker, removal one release later, never in one step. When: P2, with RFC-0005 Phase 10 or periodic rollover. Effort S each.
2. **Default** (the owner did not answer): after the upgrade to 1.34.0, every new patch id ships dormant for one release (sketch). Release N+1 adds `if workflow.patched("x")` and lists `"x"` in `DORMANT_PATCHES`, so its runs take the old branch and record no marker; N+2 removes it from the set and runs start taking the new branch. A rollback from N+2 to N+1 then replays, because N+1 already contains the branch. The same rule covers per-generation input flags: a release reads a flag one release before any release writes it. Exception: a fix for sends, money or approvals may activate at once, accepting that generations whose runs a rollback cannot replay are marked "needs Reset" (T14). Reason: RFC-0005 §21.2 keeps automatic rollback on a failed readiness check (T14), and a rollback replays only branches the older release already has. Cost: a behavior change takes effect one weekly window later. Versioning: this is the versioning policy. When: P2, from the first release after the 1.34.0 upgrade. Effort S. Risk: the dormant set becomes release state; the golden identity test reads it.
3. Never copy `hello_patch.py:70`: `name="patch-worklow"` (a typo) makes the deprecated stage a different workflow type, which would never replay stage-2 histories. Copy `patching/` instead.

**Sketch** (item 2; needs 1.34.0):

```python
# server/services/temporal/patches.py (new)
from temporalio.worker import PatchActivationInput   # 1.34.0

#: Patch ids this release ships inactive. The next release removes them from the set.
DORMANT_PATCHES: frozenset[str] = frozenset()

def activate_patch(inp: PatchActivationInput) -> bool:
    """Consulted only for a run's first, non-replay patched() call."""
    return inp.patch_id not in DORMANT_PATCHES

# Every Worker(...) in services/temporal/worker.py:
#     Worker(client, ..., patch_activation_callback=activate_patch)
# test_workflow_identity_golden.py reads DORMANT_PATCHES with the marker list.
```

### 18.10 Replay

**Samples:** `replay`. `client.list_workflows('WorkflowId="..."').map_histories()` fed to `Replayer(workflows=[...]).replay_workflows(histories, raise_on_replay_failure=False)`, whose `replay_failures` maps run id to error; the README shows the non-determinism error that a timer inserted before an activity produces.

**SDK:** all in 1.30.0: `map_histories` (`temporalio/client/_workflow.py:1649`), `Replayer.replay_workflow` and `replay_workflows` (`worker/_replayer.py:110`, `:138`, `raise_on_replay_failure` `:142`), `WorkflowHandle.fetch_history` (`client/_workflow.py:395`), `WorkflowHistory.from_json`.

**OpenCompany today:** `server/tests/temporal/test_replay_corpus.py` replays committed N-1 histories (21 scenarios in `fixtures/0.3.1/`) one at a time in a fresh process, checks source provenance by git archive hash, refuses histories with a failed workflow task (`:65`) and requires every production workflow type to be covered (`:69`). For CI this is stronger than the sample. No replay of the instance's own open runs at upgrade.

**Verdict:** Adopted with deviations (no upgrade-time replay of live histories, which is what the sample shows).

**Recommendation:**
1. The sample's path is the upgrade check of 17.12 item 1: replay every open run with the new code before workers poll; under T14, start anyway and mark the affected generations "needs Reset"; a failed readiness check still rolls back. Versioning: none. When: RFC-0005 Phase 9a and the same release for desktops. Effort M.
2. Build that Replayer with the production data converter, interceptors, workflow runner and `workflow_failure_exception_types` (17.17 item 2), or it proves less than it claims. Versioning: none. When: with item 1. Effort S.

**Remarks:** replay time grows with the number and length of open histories; EG14 times it. Paused runs are Running in Temporal, so they are included.

### 18.11 Worker-specific task queues

**Samples:** `worker_specific_task_queues`. Page: https://docs.temporal.io/design-patterns/worker-specific-taskqueue. Each process runs one Worker on a shared queue and one on a queue unique to it; an activity named `get_available_task_queue` (a closure in `main()`) returns the unique queue; the file activities run there with `task_queue=unique_worker_task_queue`; cleanup in `finally`. The sample sets no `schedule_to_start_timeout` (how long a task may wait for a worker to pick it up), which the page requires; that timeout "is non-retryable".

**SDK:** `task_queue=` and `schedule_to_start_timeout=` on `workflow.execute_activity`, in 1.30.0.

**OpenCompany today:**
- Browser activities route to the owning process's queue `browser-owner-<sha256[:24]>` (`server/services/browser_owners.py:46-47`), with claim and release at `agent_workflow.py:805-859` and options built from the frozen tool policy. `schedule_to_start_timeout` appears nowhere in `services` or `nodes`. Cleanup deliberately waits for the owner ("Owner absence leaves cleanup queued", `agent_workflow.py:855-857`); the owner pool has a fixed 4 slots (`worker.py:701`).
- T8 adds a second use: before hosted tier S moves workers to another process, every activity bound to the backend process (the WhatsApp bridge, the phone emulator, the Claude session pool, the socket broadcaster, the encryption key) runs on a queue that only the backend process polls. §15.2 (rec 3) designs it as an instance queue and, by default, reads T8 narrowly: the broadcaster and the encryption key are shared with the worker process rather than routed.

**Verdict:** Adopted with deviations.

**Recommendation:**
1. How long owner-routed work waits for an absent owner is §15.2's default (rec 4): it keeps waiting on the owner queue with no `schedule_to_start_timeout`, an upkeep check tells the owner, and the run's own limits still end the wait: today a chat run's 24 h (1b.14); under RFC-0005 Phase 5 (`scheduler_version=1`) the wait is a park with the 7-day limit, which never counts toward the 24-hour backstop (a default, §24.1). That departs from the page, which requires the timeout, and from RFC-0004 §9.2. Cleanup never takes such a timeout (`agent_workflow.py:855-857`).
2. The T8 instance queue applies the same pattern to activities bound to the backend process. §15.2 (rec 3) owns it: a placement declared on plugin classes and framework activities, an `instance-<instance_id>` queue that only the API process polls, and placement frozen into new generations' input. Versioning: per-generation flag (`placement_version=1`). When: alongside RFC-0005 Phase 4, before T8's tier S split (§15.2).

**Remarks:** the page warns against Worker Versioning on per-worker queues; irrelevant while Worker Versioning is unused (18.13).

### 18.12 Worker processes

**Samples:** `worker_multiprocessing`. `Runtime.prevent_default()` at import; each process `Runtime.set_default(Runtime(telemetry=TelemetryConfig()))`; workflow-only Workers with `max_concurrent_workflow_tasks=2` and `workflow_task_poller_behavior=PollerBehaviorSimpleMaximum(2)` in N processes; activity Workers in separate processes; `fork` where available, else `spawn`; a custom sandbox (`SandboxRestrictions.invalid_module_members_default.with_child_unrestricted("os")`).

**SDK:** all in 1.30.0: `Runtime.prevent_default` and `set_default` (`temporalio/runtime.py:89`, `:101`), `PollerBehaviorSimpleMaximum` (`worker/_worker.py:50`), `with_child_unrestricted` (`worker/workflow_sandbox/_restrictions.py:421`).

**OpenCompany today:** the workers share the FastAPI process and its event loop (`lifecycle.py:190-239`), with autoscaling pollers and a resource-based tuner on heavy queues. A standalone worker exists but registers different code (17.9). T8 decides that hosted tier S runs the workers in a second process and the desktop stays one process; RFC-0005 §20 already puts tier L's workers in separate processes.

**Verdict:** Planned in RFC-0005 (follows the docs); RFC-0005 §20 now carries T8 (§20).

**Recommendation:**
1. Run the second process as its own service started by the deploy logic (systemd or compose), not with `multiprocessing` from the backend; keep the sample's rules that matter: one `Runtime` per process created before its client (with the metrics of 17.10), the one worker builder shared with the backend (§15.3, 1b.36), and the instance queue of §15.2 (rec 3). Hosted instances run Linux, so a process pool could `fork`; desktops stay one process (T8), so Windows' `spawn`-only limit does not arise. Versioning: none (workers hold no state). When: RFC-0005 Phase 9. Effort M. Risk: memory of a second Python process on small VMs, unmeasured.
2. Decide workflow-only and activity-only splits (the sample's shape) from metrics: workflow-task schedule-to-start, execution latency and sticky-cache hits. Versioning: none. When: RFC-0005 Phase 9c. Effort S.

**Remarks:** none.

### 18.13 Worker versioning

**Samples:** `worker_versioning`. `Worker(deployment_config=WorkerDeploymentConfig(version=WorkerDeploymentVersion(deployment_name, build_id), use_worker_versioning=True))`; `@workflow.defn(versioning_behavior=VersioningBehavior.AUTO_UPGRADE | PINNED)`; compatible changes on AUTO_UPGRADE behind `workflow.patched`; promotion through the raw gRPC calls `describe_worker_deployment` and `set_worker_deployment_current_version` (`worker_versioning/app.py:96-138`). Server 1.28+.

**SDK:** `WorkerDeploymentConfig` (`temporalio/worker/_worker.py:1027`), `WorkerDeploymentVersion` (`temporalio/common.py:1252`), `VersioningBehavior` (`common.py:1234`), all in 1.30.0. Neither 1.30.0 nor 1.34.0 has a high-level client API for deployments.

**OpenCompany today:** unversioned workers. The default build id is computed off the event loop through the private `load_default_build_id` (`worker.py:243-263`), which 1.34.0 still has.

**Verdict:** Not applicable. Old builds must keep polling until their runs drain, which a single-process desktop and an instance upgraded in place in the weekly window cannot do (RFC-0004 §15; 17.9 item 5).

**Recommendation:** none now. The reason to decline is topology, not maturity (17.9 item 5). If distributed installs ever adopt it: PINNED for short runs, AUTO_UPGRADE plus continue-as-new for entities. When: never, unless the topology changes.

**Remarks:** `worker_versioning/workerv1.py:20-22` loads a client configuration and then connects to a hard-coded `localhost:7233`.

### 18.14 Long-running activities, heartbeats and cancellation

**Samples:** `custom_decorator`, `hello/hello_activity_heartbeat.py`, `hello/hello_cancellation.py` and `polling/frequent`. Page: https://docs.temporal.io/design-patterns/long-running-activity. A heartbeat is a call from a running activity that tells the server it is alive; without one inside `heartbeat_timeout`, the server times the attempt out and retries it.
- `custom_decorator`: `@auto_heartbeater` starts a task that heartbeats every `activity.info().heartbeat_timeout / 2` and cancels it when the activity ends (`custom_decorator/activity_utils.py:11-31`); the workflow uses `workflow.start_activity(..., cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)` and cancels the handle from a Signal.
- `hello_activity_heartbeat`: a sync activity heartbeats in a loop.
- `hello_cancellation`: a sync activity catches `temporalio.exceptions.CancelledError` from `heartbeat()`; the workflow runs a cleanup activity in `finally`.

**SDK:** all in 1.30.0: `activity.info().heartbeat_timeout` (`temporalio/activity.py:109`), `activity.heartbeat`, `ActivityCancellationType.WAIT_CANCELLATION_COMPLETED` (`workflow/_activities.py:77`), `workflow.start_activity`.

**OpenCompany today:**
- Every per-type node activity beats every fixed 30 s when its start-to-close exceeds its heartbeat timeout (`server/services/plugin/base.py:1248-1264`); the default heartbeat timeout is 2 minutes (`server/services/plugin/scaling.py:88`, `base.py:271`), and no plugin overrides it (grep of `heartbeat_timeout =` under `nodes`).
- Async activities catch `asyncio.CancelledError` and compensate where they own state (`agent_activities.py:2317-2339`), but the node wrapper turns cancellation into a failure result (`node_executor.py:230-238`, RFC-0004 H2).
- The LLM step heartbeats every 20 s while it waits for the provider (`agent_activities.py:378-399`).
- No activity resumes from heartbeat details after a retry (RFC-0004 §9.3 wants it for the browser and coding agents).

**Verdict:** Adopted with deviations.

**Recommendation:**
1. Derive the beat interval from `activity.info().heartbeat_timeout`, as `custom_decorator` does (here a third of it, at most 30 s, where the sample uses half), so a plugin that declares a shorter timeout cannot time out between beats. Versioning: none (activity only). When: later. Effort S. Risk: none while every plugin keeps the 2-minute default.
2. Re-raise cancellation from the node wrapper (RFC-0004 H2), with the `ActivityEnvironment` test of 17.17 item 3: §9.3 owns it (1b.2). When: Phase 1b.
3. `hello_cancellation`'s workflow-side cleanup in `finally` is the shape 18.2 forbids for cleanup that issues commands: cleanup on cancellation is right, cleanup on a bug is not. When: with 18.2.

**Remarks:** a heartbeat that never arrives because the event loop is blocked looks like a dead worker; the retry may repeat a side effect (17.19, T13).

### 18.15 Asynchronous activity completion

**Samples:** `hello/hello_async_activity_completion.py`. Page: https://docs.temporal.io/design-patterns/delayed-callback (the task-token part). The activity reads `activity.info().task_token`, hands it to another component and calls `activity.raise_complete_async()`; that component later calls `client.get_async_activity_handle(task_token=...)` and `handle.heartbeat()` or `handle.complete(...)`. The activity holds no worker slot while it waits.

**SDK:** all in 1.30.0: `task_token` (`temporalio/activity.py:117`), `raise_complete_async` (`activity.py:441`), `get_async_activity_handle` (`temporalio/client/_client.py:2594-2609`).

**OpenCompany today:** not used. The approvalGate node waits inside an activity for up to 24 h per attempt, with unlimited attempts, woken by in-process change notifications and re-reading its row at least every 10 s (`server/nodes/workflow/approval_gate/__init__.py:48`, `:181-185`, `:255-277`; RFC-0004 M4). Reviewed deliveries wait up to 31 days in activities at three sites (`employee_job_workflow.py:133-139` and `:156`; `agent_workflow.py:3133-3138`).

**Verdict:** Not applicable. RFC-0004 Q10/D13 decided that human waits become an Update plus a durable timer in a workflow, which answers "accepted, too late or already decided" at once and needs no stored task token.

**Recommendation:** none for this sample. The four waits above move to D13's shape in RFC-0005 Phase 7 (§9.6; RFC-0005 §17.5 covers them since this RFC amended it, §20).

**Remarks:** a task token is a capability: anyone holding it can complete the activity. If a later design does use one, it is stored encrypted like a credential.

### 18.16 Resource pool

**Samples:** `resource_pool`. A `ResourcePoolWorkflow` entity hands out locks by Signal: waiters signal `acquire_resource`, and the pool answers on a Signal name registered at runtime (`workflow.get_signal_handler`, `set_signal_handler`); holders release in an `asynccontextmanager`'s `finally`; a lock is detached and reattached across continue-as-new; `@workflow.defn(failure_exception_types=[FailWorkflowException])` (`resource_pool/resource_user_workflow.py:54`). README caveats: "true locking (not leasing!)"; a lock leaks when "An operator terminates a ResourceUserWorkflows" ("Temporal recommends canceling workflows instead of terminating them whenever possible"); "A single ResourcePoolWorkflow scales to tens, but not hundreds, of request/release events per second".

**SDK:** all in 1.30.0: `get_signal_handler` and `set_signal_handler` (`temporalio/workflow/_workflow_ops.py:846`, `:861`), `get_external_workflow_handle_for` (`:639`), `failure_exception_types` (`workflow/_definition.py`).

**OpenCompany today:** a database lease per delegation, acquired by an activity that polls every second while heartbeating (10 s heartbeat timeout, 1 h attempts, unlimited retries), with lease ids derived from the attempt so a retry reconciles the previous attempt's lease, and compensation on cancellation (`agent_activities.py:2247-2339`; `agent_workflow.py:570-576`, `:1690-1696`; `PERMIT_WAIT_RETRY`, `_retry_policies.py:127-131`). Reset terminates every run of the generation (`handlers.py:974-995`) and cleans browser tasks out of band (`:2591-2597`) but releases no subagent permits.

**Verdict:** Adopted with deviations (a polled lease instead of a signalled pool, which is the better fit; Reset leaks leases).

**Recommendation:**
1. Keep database leases: they survive a lost Temporal database and fit RFC-0005 I10. §12.4 moves the wait for a permit into the workflow, woken by a Signal, still on database leases.
2. Release a generation's subagent permits in Reset, because termination skips every `finally` (the sample's caveat): §10.2 owns it (1b.21). When: Phase 1b.

**Remarks:** when RFC-0005's entities signal one another and the target has already closed, match the error type `ExternalWorkflowExecutionNotFound`, as `resource_pool/pool_client/resource_pool_workflow.py:98-104` does, not the substring "not found" that the sliding-window page's Python matches.

### 18.17 Interpreters and batches

**Samples:** `dsl` and `batch_sliding_window`. Pages: https://docs.temporal.io/design-patterns/sliding-window, `/fanout-child-workflows`, `/batch-iterator`.
- `dsl`: a workflow interprets a YAML-defined tree (sequence, parallel, an activity called by its string name with `workflow.execute_activity(name, args=[...])`); variables live in workflow state; `asyncio.gather` runs parallel branches and "will raise an exception when the first activity fails and will not cancel the others" (`dsl/workflow.py:80`).
- `batch_sliding_window`: `ProcessBatchWorkflow` splits records into N partitions with one `SlidingWindowWorkflow` child each (`id_reuse_policy=ALLOW_DUPLICATE`); each window starts `RecordProcessorWorkflow` children with `ParentClosePolicy.ABANDON` while fewer than the window are running, continues as new after one page and carries the in-flight set; children signal the parent by workflow id without a run id (`record_processor_workflow.py:18-25`); handlers are registered in `run()` with `workflow.set_signal_handler` and `set_query_handler` (`sliding_window_workflow.py:74-77`).

**SDK:** all in 1.30.0: `set_signal_handler`, `set_query_handler`, `get_external_workflow_handle`, `ParentClosePolicy`, `WorkflowIDReusePolicy`.

**OpenCompany today:**
- MachinaWorkflow interprets the saved graph frozen at Start, dispatching `node.<type>.v<version>` activities by name, with continuous scheduling and per-node failure handling (`workflow.py:515` onward, `:1101-1133`).
- No record batches. Concurrency windows come from database permits (18.16) and the Workspace controller's active-task cap (`workspace_tasks_workflow.py:95-96`).

**Verdict:** Adopted correctly (the interpreter); the batch shape has no counterpart.

**Recommendation:** none beyond RFC-0004 §8.6: freeze dispatch (activity names, queues, timeouts, policies) into run input, which the `dsl` sample ignores because its activity set never changes between releases. When: P2, RFC-0005 Phase 4 (§15.3 rec 2), which is RFC-0004 Phase 2's freeze.

**Remarks:** a child that signals a parent which has already finished gets `ExternalWorkflowExecutionNotFound`; the sample leaves it unhandled (18.22).

### 18.18 Payload codecs, external storage and converters

**Samples:** `encryption`, `external_storage`, `external_storage_redis`, `pydantic_converter`, `pydantic_converter_v1`, `custom_converter`.
- `encryption`: an AES-GCM `PayloadCodec` with a key id in payload metadata; `decode` passes through payloads without its encoding, so turning it on reads old history (`encryption/codec.py:38-41`); `dataclasses.replace(temporalio.converter.default(), payload_codec=...)` on client and worker; an aiohttp codec server with CORS for the Web UI. Failures are not encoded.
- `external_storage`: `ExternalStorage(drivers=[S3StorageDriver(...)])` next to a compression codec on the data converter; payloads at or above `payload_size_threshold` (default 256 KiB) are stored and replaced by a claim; client, worker and codec server must share the configuration. The Redis driver is about 200 lines: `StorageDriver` `name`, `type`, `store` and `retrieve`, content-addressed keys scoped by namespace and workflow or activity identity, an optional TTL on first insert, a SHA-256 check, and a missing key raised as non-retryable (`external_storage_redis/_driver.py:200-204`).
- `pydantic_converter` and `custom_converter`: `temporalio.contrib.pydantic.pydantic_data_converter`; a `CompositePayloadConverter` with a custom converter first.

**SDK:** all in 1.30.0: `PayloadCodec` (`temporalio/converter/_payload_codec.py:12`), `ExternalStorage` (`converter/_extstore.py:242`, threshold `:270`, marked experimental), `StorageDriver` (`:170`), `Worker(max_workflow_task_external_storage_concurrency=...)` (`worker/_worker.py:148`), `contrib.pydantic`. 1.34.0 changes `ExternalStorage.driver_selector` to take a new `StorageDriverSelectContext`, a breaking change for selector users.

**OpenCompany today:**
- No codec and no external storage (grep). Payloads are plain dicts on purpose ("plain dicts keep the payload-serialisation cost flat", `agent_activities.py:116-119`).
- Large data moves by reference: `FileRef` and `AudioRef` for media (`server/services/media/refs.py`), the conversation store for transcripts. Agent transcripts stay in history by decision (T6), bounded by the byte budget, compaction and continue-as-new.
- Hand-rolled claim checks: the controller spills overflow events through `controller.queue.spill` in 200,000-character chunks when one event exceeds 512,000 bytes (`workflow_control_workflow.py:626-643`); continue-as-new inputs are capped at 1,900,000 bytes (`:38`, `:705-711`; `agent_workflow.py:366`, `:3020-3029`). On the legacy agent rollover path a transcript above 1,000,000 bytes is dropped and the agent restarts from its opening prompt (`agent_workflow.py:365`, `:3039-3050`); the version-1 path fails with a non-retryable `AgentContinuationTooLarge` above 1,900,000 bytes instead.

**Verdict:** Not applicable. Codecs: the same content sits unencrypted in `workflow.db` beside `temporal.db`, so a codec alone does not protect it (17.8 item 6; RFC-0004 Q13 stays open). External storage: preview features stay off (RFC-0004 Q12), deletion is unsolved, and references already carry large data. Converters: plain dicts by design.

**Recommendation:**
1. Keep references as the main mechanism. Revisit `ExternalStorage` at GA as a safety net for whatever still exceeds 256 KiB. The samples show the driver is small; the open problems are deletion (a claim must outlive every history that references it, and OpenCompany sets no run timeouts) and the replay gate, whose Replayer would need the driver too. When: later.
2. If a codec is ever adopted (Q13), copy `encryption`'s pass-through decode, which makes turning it on safe for open runs; turning it off is not, unless every reader keeps the key. Pair it with `DefaultFailureConverterWithEncodedAttributes`, because the sample leaves failure messages readable. When: only with an owner decision.

**Remarks:** `FileRef` is visible and deliberate (a node chooses to return a reference); `ExternalStorage` is transparent and applies to every payload, including workflow inputs and continue-as-new arguments. They complement each other.

### 18.19 Eager start and local activities

**Samples:** `eager_wf_start` and `hello/hello_local_activity.py`. Pages: https://docs.temporal.io/design-patterns/eager-workflow-start and https://docs.temporal.io/design-patterns/local-activities. The worker and client share one `Client` in one process; `start_workflow(..., request_eager_start=True)` hands the first workflow task straight to the local worker; the first task runs a local activity (`workflow.execute_local_activity(..., start_to_close_timeout=...)`), an activity run inside the worker without a server round trip.

**SDK:** 1.30.0: `request_eager_start` (`temporalio/client/_client.py:419`) is sent only on a plain start (`client/_impl.py:216`); Update-With-Start inputs lack the field (`_impl.py:213-215`), Signal-With-Start never sets it, and children cannot be eager. `execute_local_activity` takes no task queue or priority.

**OpenCompany today:** not used. Executor and workers share one client (`lifecycle.py:190-226`), so eager start would work for plain starts: approvals (`server/services/approvals/execution.py:65-69`), team jobs (`server/services/employees/jobs.py:240-243`), manual runs (`server/services/temporal/executor.py:181-185`). The corpus shows 12-32 ms from a first workflow task's scheduling to its start. RFC-0005 §10.4 keeps eager start unused; RFC-0004 §15 deferred local activities until bookkeeping latency was measured, and §21 reopens them for progress broadcasts (§14.2).

**Verdict:** Not applicable (as RFC-0005 decides).

**Recommendation:**
1. Drop RFC-0004 Phase 3's "eager-start experiment for chat runs", as §14.4 (rec 1) also says: under RFC-0005 a chat message reaches its ConversationWorkflow by Signal-With-Start and turns are children, and neither can be eager. Versioning: none (text). When: with this RFC (§21). Effort S.
2. Local activities for the bookkeeping steps are §14.2's decision.

**Remarks:** none.

### 18.20 Test techniques

**Samples:** `tests/` (with `tests/conftest.py`). A session-wide `WorkflowEnvironment.start_local(dev_server_extra_args=[...], dev_server_download_version=...)` or `start_time_skipping()` (`tests/conftest.py:44-58`); activities and children mocked by registering a different function under the same name; `env.sleep(timedelta(days=90))` to skip time (`tests/sleep_for_days/workflow_test.py:45`); Update tests skipped on the time-skipping server; `client.list_workflows().map_histories()` to assert what histories contain.

**SDK:** all in 1.30.0 (`temporalio.testing`).

**OpenCompany today:** the same techniques: time skipping in 15 test files, 128 activity mocks registered by name in `server/tests`, `ActivityEnvironment` in 7 files, and `start_local` with the pinned `TEMPORAL_TEST_CLI` (`server/tests/temporal/test_workspace_tasks_integration.py`, `test_generation_execution_control_integration.py`, `record_fixtures.py`); the corpus test refuses to skip under `TEMPORAL_REQUIRE_NATIVE_REPLAY=1` (`test_replay_corpus.py:19-21`).

**Verdict:** Adopted correctly.

**Recommendation:** the gaps are listed in 17.17 (Replayer parity, the node-cancellation test, the 18.2 tests).

**Remarks:** none.

### 18.21 Samples that do not apply

| Folder | What it shows | Why it does not apply |
|---|---|---|
| `gevent_async`, `trio_async` | Temporal under gevent or Trio | OpenCompany runs FastAPI on asyncio |
| `lambda_worker`, `gcp` | a Worker inside AWS Lambda or Cloud Run, invoked by Temporal Cloud | no Temporal Cloud (E1) |
| `cloud_export_to_parquet` | Cloud history export | Cloud only |
| `activity_worker` | a Go workflow calling a Python activity | no polyglot workers; the bun sidecar is called over HTTP from Python activities |
| `env_config`, `hello/hello_mtls.py` | TOML client profiles; `TLSConfig` | loopback dev server with no TLS (`client.py:92-97`). `ClientConfig.load_client_connect_config` reads `TEMPORAL_*` environment variables by default (`temporalio/envconfig.py`, `disable_env=False`), so adopting it would change which variables configure the client |
| `hello/hello_activity_multiprocess.py` | sync activities in a `ProcessPoolExecutor` with a `SharedStateManager` | all activities are async (17.19) |
| `scripts` | repository tooling | not a sample |

**Verdict:** Not applicable.

### 18.22 Defects in the samples (do not copy)

1. `hello/hello_patch.py:70`: `name="patch-worklow"` makes the deprecated stage a different workflow type.
2. `worker_versioning/workerv1.py:20-22`: loads a client configuration, then connects to `localhost:7233` anyway.
3. `message_passing/waiting_for_handlers/workflows.py:16-25`: `is_workflow_exit_exception` omits `asyncio.TimeoutError`, which the SDK treats as a workflow failure (`temporalio/worker/_workflow_instance.py:1831-1834`); a run that fails on a timeout would skip its cleanup.
4. `message_passing/waiting_for_handlers_and_compensation/workflows.py:80`: `workflow_compensation()` sets `_update_compensation_done`; `run()` sets the right flag, so the test still passes.
5. `sleep_for_days/workflows.py:24-33`: never cancels the task that lost the race; each round the sleep wins leaves a pending `wait_condition` task.
6. `worker_specific_task_queues`: no `schedule_to_start_timeout`, which the pattern page requires.
7. `batch_sliding_window/record_processor_workflow.py:22-25`: signalling a parent that has finished raises `ExternalWorkflowExecutionNotFound`, unhandled.
8. `custom_metric/worker.py:49`: Prometheus bound to `0.0.0.0`.
9. `sentry/interceptor.py:41-45`, `:68-71`: workflow and activity inputs attached to error reports.
10. `updatable_timer/workflow.py:21-27`: the deadline moves by Signal with no check that it is in the future.
11. README drift: the root README lists `hello_activity_threaded`, which does not exist; `hello/README.md:49-51` still asks for `frontend.enableUpdateWorkflowExecution=true`.
12. Pattern-page links that do not resolve at this commit: `samples-python/start_delay` and `samples-python/early_return`; Pick First and Saga link only the repository root; Signal-with-Start's two Python links do not use Signal-With-Start.

## 19. Nexus and standalone activities

Temporal Nexus connects workflows across namespaces through named services, endpoints and operations. A standalone activity is one durable activity started by a client, with no workflow around it. This section reviews the 13 Nexus pages and the two standalone pages against OpenCompany's deployment model: one free Temporal per owner instance (RFC-0005 E1), the CLI dev server up to about 100 employees and the open-source server on the instance's PostgreSQL above that (E2), and separate hosted instances that report only metrics and errors centrally (E13, E14). The verdict is the same for every page: none of it applies today. Standalone activities have one later use.

**What the pinned server and the SDK support.** T5 (fresh start) makes every install run the pinned CLI v1.9.1, server 1.32.0, once the first pinned release ships: the revert of commit 49177427 in Phase 1 deletes old Temporal databases again. Until then, installs keep the server that created their database (`server/services/temporal/_install.py:195-227`; the 0.3.1 replay corpus was recorded on CLI 1.8.0 and server 1.31.2, `server/tests/temporal/fixtures/0.3.1/manifest.json`). After it, none of the features below needs a per-install gate. The revert also brings back the behaviour the comments at `server/services/temporal/_runtime.py:62-63` and `:121-123` describe (a database from another server minor is deleted); today those comments are stale.

| Feature | Server and configuration | Pinned dev server (CLI v1.9.1, server 1.32.0) | Full server 1.32.0 (tier L) | SDK 1.30.0 | SDK 1.34.0 |
|---|---|---|---|---|---|
| Nexus Operations called from a workflow (synchronous, or backed by a workflow); Endpoints | `system.enableNexus` was removed in 1.31.0 ("Nexus is always on") | On, no flags; the CLI also sets `system.forceNexusEndpointRefreshOnRead=true` | On | Experimental: `workflow.create_nexus_client`, `nexus.workflow_run_operation`, `nexusrpc.handler.sync_operation`, `Worker(nexus_service_handlers=...)`; Operator API `create_nexus_endpoint`, `delete_nexus_endpoint`, `update_nexus_endpoint` (`temporalio/bridge/services_generated.py:2255`, `:2291`, `:2417`) | Same |
| Operation Schedule-to-Start and Start-to-Close timeouts | Server 1.31.0 or later | Yes | Yes | All three timeouts (`workflow/_nexus.py:109-112`) | Yes |
| Temporal Operation Handler (pre-release) | Update backing needs `history.enableUpdateCallbacks`; Signal backlinks and Signal-With-Start from a handler need `history.enableCHASMSignalBacklinks` and `history.enableSignalWithStartFromWorkflow`; all default false. Activity backing needs `activity.enableCallbacks=true` | Flags needed; the dev server sets none of them | Flags needed | `@nexus.temporal_operation`; `TemporalNexusClient` offers only `start_workflow` | Adds `start_workflow_update` and `start_activity` (`nexus/_temporal_client.py:292`, `:377`) |
| Standalone Nexus Operations (pre-release) | `nexusoperation.enableStandalone`, default off per the server 1.32.0 release notes | Most likely off: v1.9.1 sets no override; a later dev-server build adds one | Off | Experimental: `Client.create_nexus_client`, `list_nexus_operations`, `count_nexus_operations`, `get_nexus_operation_handle` (`client/_client.py:2878-2997`) | Still experimental |
| Standalone Activities (GA) | Server 1.32.0 and CLI 1.9.1 or later | On: the dev server forces CHASM, the standalone-activity switch, its operator commands and batch operations on in code (`devserver/server.go:246-251`, "These will be on by default in server v1.32") | On, per the server 1.32.0 release notes (the switch's default was not read in source); batch operations default off (`frontend.enableBatchOperationsForStandaloneActivities`) | Present, marked "This API is experimental." (`client/_client.py:1468-1553`, `:1497`); the Python guide requires 1.33.0 | GA; the handle adds `pause`, `unpause`, `update_options` |

### 19.1 Temporal Nexus (overview)

**Docs:** https://docs.temporal.io/nexus. "Nexus connects Temporal Applications across (and within) isolated Namespaces", "peer-to-peer, not hierarchical". A Service groups Operations; an Endpoint routes by name to a target Namespace and Task Queue; handler Workers poll that queue; caller workflows call through a Nexus client.
- A synchronous Operation must finish inside "the 10-second handler deadline"; an asynchronous one starts a workflow (or, pre-release, an Update or an Activity) and completes by callback.
- Delivery is at-least-once, with retries, rate and concurrency limits and a circuit breaker that "trips after 5 consecutive retryable errors".
- "If a Nexus Service is down, caller Workflows continue to schedule Operations."

**SDK:** caller and handler APIs are in 1.30.0, experimental (see the table).

**OpenCompany today:** not found; no Nexus API is used in `server/` or `cli/`. There is one namespace (`server/core/config.py:145`). The boundaries where Nexus could apply:
- **Namespaces inside one instance.** On the dev server a second namespace shares the single SQLite store and the single history shard (`devserver/server.go:283-285`), so it isolates neither storage nor throughput; namespaces scope retention, search attributes and many dynamic-config settings. RFC-0005 isolates classes with task queues, Priority keys, fairness keys and floors inside one namespace (E10, RFC-0005 §13). Splitting would turn the child workflows, Signals and Updates that controllers and entities use into Nexus Operations, limited by default to 30 pending Operations per caller workflow and 32 callbacks per handler workflow (`system.maxCallbacksPerWorkflow`).
- **An owner's instance and a hosted control plane.** The control plane is the deploy logic, the installer service and the central OTLP collector, none of it Temporal; E1 forbids a shared hosted backend. On self-hosted Temporal "the Registry is scoped to a Cluster" (nexus/registry), so a call between clusters needs the external-URL endpoint target (`temporal operator nexus endpoint create --target-url`, experimental), a reachable frontend HTTP port, callback allowlists on both sides and authentication the dev server lacks. Tier S binds to 127.0.0.1 (RFC-0005 §20), and OpenCompany passes no `--http-port`, so the dev server's HTTP port is a random free port (`server/services/temporal/_runtime.py:143-165`) until 1b.33 names it (§17.8).
- **Owner to owner.** Ruled out by E8: owners live in separate instances.
- **Plugins and drivers.** Plugins are in-process Python. The Go WhatsApp bridge streams into the inbox over RFC-0005's gRPC protocol (E4, RFC-0005 §7.3); a Go Nexus handler would bypass the inbox, the only durable store for events.
- **Employee to employee.** Routed through the inbox with storm guards and budgets (RFC-0005 §16); delegation runs in detached `DelegatedTaskWorkflow` runners with `ABANDON` (`server/services/temporal/agent_workflow.py:1873-1899`) and durable task rows. Nexus completion callbacks would bypass the inbox's dedupe and its rebuild after a lost Temporal database (I10).
- **DISTRIBUTED_MODE browser replicas.** They share one external Temporal and namespace (`server/core/config.py:566-588`); browser effects go to per-owner queues `browser-owner-<hash>` (`server/services/browser_owners.py:46-47`). Plain task-queue routing meets the goals Nexus's router-queue pattern names.

**Verdict:** Not applicable. With one Temporal per owner, Nexus could only join namespaces inside one instance, which queues and priority already separate, and crossing instances would need experimental external targets and an exposed, authenticated Temporal.

**Recommendation:**
1. Keep Nexus out of the adoption list, and replace RFC-0004 §15's reason and reopen condition (§21): reopen only if an instance must call another cluster and external-URL endpoints leave pre-release. Doc-only, in this RFC's commit. Effort S.

**Remarks:** If Nexus were ever used here, a desktop worker that sleeps or restarts would trip the breaker for every Operation to its Endpoint: "Consecutive timeouts count as retryable errors". A caller must also await its cancelled Operations: "If the workflow completes before cancellation requests are delivered, the server drops them" (`samples/nexus_cancel/caller/workflows.py:58-61`).

### 19.2 Nexus Services, Operations, Endpoints and Registry

**Docs:** https://docs.temporal.io/nexus/services, https://docs.temporal.io/nexus/operations, https://docs.temporal.io/nexus/endpoints, https://docs.temporal.io/nexus/registry. A Service is a named collection of Operations registered on a Worker. An Endpoint is "a reverse proxy for a single Nexus Service", and "Adding an Endpoint to the Nexus Registry deploys it immediately". The Registry holds endpoint names, unique per Registry.
- Operations record `ScheduleNexusOperation`, `NexusOperationScheduled`, `Started` (asynchronous only) and a terminal event. The caller's machinery retries retryable errors until Schedule-to-Start or Schedule-to-Close; asynchronous Operations can run up to 60 days in Temporal Cloud.
- The circuit breaker is per caller Namespace and Endpoint: open for 60 s after 5 consecutive retryable errors, then one probe; worker-less timeouts count.
- Delivery is at-least-once, so handlers must be idempotent; for exactly-once, back the Operation with a workflow under RejectDuplicates. "Terminating a caller Workflow abandons all pending Nexus Operations." Versioning is by task routing (`prod.payments.v2`). Continue-as-new copies callbacks; the old run's stay Standby.
- "In self-hosted deployments, the Registry is scoped to a Cluster." In Temporal Cloud "No callers are allowed by default"; self-hosted relies on custom Authorizers.

**SDK:** `@nexusrpc.service` and `@nexusrpc.handler.service_handler` (nexus-rpc 1.4.0, installed); `workflow.create_nexus_client(...).start_operation` and `execute_operation` with three timeouts and a cancellation type, in 1.30.0 and 1.34.0; the Operator API for endpoints (see the table) and `temporal operator nexus endpoint create`.

**OpenCompany today:** not found. The closest contracts are the plugin `BaseNode` API and the WebSocket message types, both in-process or client-facing.

**Verdict:** Not applicable: child workflows, Signals and Updates do the same inside one namespace, with fewer limits.

**Recommendation:** none. If Nexus is ever adopted, the startup and deploy logic creates endpoints (RFC-0005 §21.1), never a person by hand.

**Remarks:** none.

### 19.3 Nexus Patterns

**Docs:** https://docs.temporal.io/nexus/patterns. The collocated pattern is the default: handlers run on the same Worker and Task Queue as the workflows they front, which allows Eager Workflow Start and a "Clean facade". The router-queue pattern puts a dedicated Nexus Worker on a router queue that starts workflows on other queues of the same Namespace, for "separate scaling", "different IAM permissions", or adding Nexus without touching existing Workers.

**SDK:** `ctx.start_workflow(..., task_queue=...)` inside a `workflow_run_operation`: 1.30.0.

**OpenCompany today:** the router-queue goals are met without Nexus: one activity-only Worker per plugin queue (`TemporalWorkerPool`, `server/services/temporal/worker.py`) and per-owner browser queues in DISTRIBUTED_MODE (`server/services/browser_owners.py:46-47`). Eager Workflow Start is a preview feature RFC-0005 keeps unused (RFC-0005 §10.4).

**Verdict:** Not applicable.

**Recommendation:** none.

**Remarks:** none.

### 19.4 Security in Temporal Nexus

**Docs:** https://docs.temporal.io/nexus/security. Temporal Cloud uses an Endpoint allowlist of caller Namespaces and mTLS throughout. Self-hosted uses custom Authorizers: "Self-hosted deployments rely on the Temporal Cluster being secure." Nexus payloads use the same Data Converter, so a Codec encrypts them, and caller and handler need compatible converters.

**SDK:** the standard `DataConverter`; `NexusSerializationContext` is absent in 1.30.0 and experimental in 1.34.0 (`converter/_serialization_context.py:102`).

**OpenCompany today:** no codec (RFC-0004 Q13, open); tier S binds to loopback; the dev server's start options have no TLS or authorization flag.

**Verdict:** Not applicable, and the second reason cross-instance Nexus is off the table: exposing the dev server would contradict tier S's loopback binding (RFC-0005 §20).

**Recommendation:** none.

**Remarks:** none.

### 19.5 Nexus error handling, execution debugging and metrics

**Docs:** https://docs.temporal.io/nexus/error-handling, https://docs.temporal.io/nexus/execution-debugging, https://docs.temporal.io/nexus/metrics.
- Errors: a handler error is retryable unless it is a non-retryable ApplicationFailure, an Operation error or a non-retryable Nexus error; retryable ones show only among Pending Operations; "Return a specific Nexus error type to avoid infinite retries"; the caller receives a Nexus Operation Failure with the cause.
- Debugging: links between caller and handler events, added automatically; Pending Operations and Pending Callbacks in `temporal workflow describe`; OpenTelemetry through interceptors.
- Metrics: SDK `nexus_*` metrics from Nexus Workers, Cloud OpenMetrics (preview), and the self-hosted service metrics.

**SDK:** `nexusrpc.HandlerError` and `temporalio.exceptions.NexusOperationError` in both versions; `TracingInterceptor` covers Nexus handler starts (`temporalio/contrib/opentelemetry/_interceptor.py:400-407`); sdk-core emits `nexus_*` metrics once a meter is configured.

**OpenCompany today:** no Nexus handlers. `RootWorkflowId`, the `EventWorkflowId` search attribute and RFC-0005's `chainid` timeline (RFC-0005 §18.4) fill the debugging role. SDK metrics are off (`server/services/temporal/client.py:78-83`, `worker.py:97-102`) until 1b.38 adds the SDK metrics runtime (§17.10); RFC-0005 §18.5 plans OTLP metrics.

**Verdict:** Not applicable.

**Recommendation:** none.

**Remarks:** none.

### 19.6 NexGen

**Docs:** https://docs.temporal.io/nexus/nexgen. Pre-release. One JSON Schema 2020-12 file generates typed models, runtime validators (failures map to `BAD_REQUEST`) and Nexus Service definitions for Go, Java, Python and TypeScript; known divergences include nanosecond precision in Python.

**SDK:** an external generator; no SDK dependency.

**OpenCompany today:** the only planned cross-language contract, Python to the Go WhatsApp bridge, is RFC-0005's gRPC driver protocol, whose protobuf already gives one schema and generated code on both sides (RFC-0005 §7.3).

**Verdict:** Not applicable.

**Recommendation:** none.

**Remarks:** none.

### 19.7 Temporal Operation Handler

**Docs:** https://docs.temporal.io/nexus/temporal-operation-handler. Pre-release. One handler type backs an Operation with a workflow, an Update or an activity. Its Nexus-aware client carries links and request ids; "Constructing your own Client inside a handler works, but the Executions it starts are not linked back to the caller." Asynchronous backings: "at most one per Operation invocation"; synchronous messaging (Signals, Signal-With-Start): "as many as you need". Derive the backing id from the Nexus request id, so retries reuse it.

**SDK:** 1.30.0 has `@nexus.temporal_operation`, `TemporalStartOperationContext`, `TemporalNexusClient` and `TemporalOperationResult`, but only `start_workflow` as a backing; `start_workflow_update` and `start_activity` arrive by 1.34.0. The server flags default off (see the table).

**OpenCompany today:** not found.

**Verdict:** Not applicable: pre-release, and excluded by RFC-0004 Q12 (preview features only behind a flag, off by default).

**Recommendation:** none.

**Remarks:** The `nexus_messaging_temporal_operation` sample asks for a pre-release dev-server build and three dynamic-config values; the Python feature guide recommends CLI v1.9.2 and SDK 1.33.0 or later. It is still moving.

### 19.8 Nexus Standalone Activity (an activity-backed Operation)

**Docs:** https://docs.temporal.io/nexus/standalone-activity. Pre-release, and a handler-side choice, "not the same as a Standalone Nexus Operation": an Operation backed by one standalone activity, for "one durable step". It needs an Activity ID unique in the Namespace ("Deriving it from the Nexus request ID makes the start idempotent") and a start-to-close or schedule-to-close timeout; the Task Queue is optional. Cancellation reaches the Worker only on its next heartbeat. Use a workflow for more steps, waits, messages or durable state.

**SDK:** `TemporalNexusClient.start_activity` is absent in 1.30.0 and present in 1.34.0 (`nexus/_temporal_client.py:377`). The server needs `activity.enableCallbacks=true` (`samples/nexus_standalone_activity/README.md:26-27`), which the pinned dev server does not set.

**OpenCompany today:** not found.

**Verdict:** Not applicable.

**Recommendation:** none.

**Remarks:** The docs disagree with themselves: the GA standalone-activity page lists "Starting from Temporal Nexus" as not yet supported, while this pre-release page and its sample do exactly that. Read it as "Nexus-started standalone activities are pre-release".

### 19.9 Standalone Nexus Operation

**Docs:** https://docs.temporal.io/standalone-nexus-operation. Pre-release. A client starts one Nexus Operation with no caller workflow, with the same contract, handlers and Workers, synchronous or asynchronous, at-least-once, listable and countable. The use case is "Make Nexus calls from anywhere" (a UI backend, an HTTP handler, a script). It replaces a caller workflow whose only job was one Operation; pick a stable Operation ID if you relied on workflow-id dedupe. Not yet supported: delete and reset, batch operations, UI list actions, global namespaces. CLI v1.9.0 or later; the UI needs `EnableStandaloneNexusOperations`.

**SDK:** `Client.create_nexus_client`, `list_nexus_operations`, `count_nexus_operations` and `get_nexus_operation_handle` in 1.30.0 (`client/_client.py:2878-2997`), marked experimental and still marked in 1.34.0.

**OpenCompany today:** not found. The API process is a Temporal client of its own namespace and starts workflows directly (`server/services/node_invocations.py:52`, `server/services/temporal/executor.py:181`).

**Verdict:** Not applicable.

**Recommendation:** none.

**Remarks:** The docs say the dev server enables this by default, but server 1.32.0 defaults it off and the v1.9.1 dev server sets no override; a later dev-server build adds one. Treat it as off on the pinned CLI.

### 19.10 Standalone Activity

**Docs:** https://docs.temporal.io/standalone-activity. GA. "Temporal's job queue": a client starts one durable activity, which is persisted before any Worker sees it.
- It has a retry policy and timeouts ("Set Maximum Attempts to 1 for at-most-once") and heartbeats with checkpoint details.
- Its Activity ID has a conflict policy (default Fail; Use Existing) and a reuse policy (default Allow Duplicate; Allow Duplicate Failed Only; Reject Duplicate). "The Activity ID stops you submitting the same job twice. Idempotent code stops one job's side effects happening twice if retried": both are needed.
- Priority and fairness; Start Delay (first task only); `temporal activity list/count/describe`; Request Cancel, Terminate and Delete (GA); Pause, Unpause, Reset and Update Options (preview).
- The result is kept for the Namespace's retention, which is also the dedupe window. For long jobs, "start the Activity and hold the handle rather than blocking".
- Compared with a workflow's activity: "no Workflow Event History and no deterministic replay".
- Limitations: no TerminateExisting; no start from a recurring Schedule ("schedule a Workflow that invokes the Activity"); no start from Nexus; no batch Reset, Pause or Update; no export. Needs CLI v1.9.1 and server v1.32.0 or later.

**SDK:** `Client.start_activity`, `execute_activity`, `get_activity_handle`, `list_activities` and `count_activities` in 1.30.0, marked experimental (`client/_client.py:1468-1553`, `:2471-2561`), with the same signature in both versions (`id`, `task_queue`, the four timeouts, `id_reuse_policy`, `id_conflict_policy`, `retry_policy`, `search_attributes`, `summary`, `priority`, `start_delay`). GA in 1.34.0, whose handle adds `pause`, `unpause` and `update_options`.

**OpenCompany today:** no client starts a standalone activity. The rule the pages give, applied here: **if anything must run after the activity fails, times out or is cancelled (a status record, an `unknown` mark, a cleanup), keep a workflow**; a standalone activity has no owner to run that step. The one-off wrappers, checked against it:

| # | Candidate | Shape today | Fit |
|---|---|---|---|
| 1 | NodeInvocationWorkflow, device branch (Mobile Agent, Android tool) | One activity on the plugin queue with one attempt, a 2-minute heartbeat and `WAIT_CANCELLATION_COMPLETED` (`server/services/temporal/node_invocation.py:41-49`); then two shielded writes in `finally`: `workspace_tasks.update_record`, retried without limit, and `workflow_runs.record_completion` (`:78-117`); a `describe` Query (`:20-22`). The mobile nodes allow 3 hours and one attempt (`server/nodes/mobile/_node.py:147-149`) | No: if the worker dies, nothing would write the terminal record |
| 2 | NodeInvocationWorkflow, agent branch (Browser agent, Coding Agent) | A child AgentWorkflow or PiCodingAgentWorkflow (`node_invocation.py:32-39`) | No: an agent loop is a workflow |
| 3 | WorkspaceTaskControllerWorkflow | An entity with Update-With-Start (`server/services/node_invocations.py:44-59`), 32 active tasks, children with `REJECT_DUPLICATE`, `WAIT_CANCELLATION_COMPLETED` and `REQUEST_CANCEL`; Reset cancels each child and waits (`server/services/temporal/workspace_tasks_workflow.py:20`, `:114-121`, `:168-175`) | No: a workflow cannot start a standalone activity, so the controller could not own its tasks, and its design rules out the Visibility search Reset would then need ("No process-local registry or visibility query is the source of truth", `:3-5`) |
| 4 | Canvas Run of a graph | `client.execute_workflow(MachinaWorkflow.run, id="<slug>-<uuid8>")` (`server/services/temporal/executor.py:126-127`, `:181-194`) | No: multi-node orchestration |
| 5 | Single-node Run from the parameter panel | In process, with no Temporal (`server/routers/websocket.py:446`, `server/services/workflow.py:202-298`); its context carries a callback (`get_output_fn`, `:237`); only the Coding Agent starts a workflow (`:367`) | No: a test run that must work with Temporal off |
| 6 | EmployeeJobDeliveryWorkflow | One activity: `employee.job.resolve_delivery`, 31 days, 2-minute heartbeat, 3 attempts (`server/services/temporal/employee_job_workflow.py:149-157`); started with `id=resolution_id` and `REJECT_DUPLICATE` (`server/services/employees/delivery_resolution.py:94-98`); started again by a 10-second recovery loop that begins at boot (`server/services/employees/activation.py:55-72`) | In shape today; not after RFC-0005 Phase 7, when §9.6 makes its 31-day wait an ApprovalWorkflow child |
| 7 | ApprovedToolCallWorkflow | Sleep to the Undo deadline, claim, send once, record the outcome with unlimited retry (`approved_tool_call_workflow.py:36-80`) | No: the `unknown` record after a broken send needs an owner; RFC-0005 §17.5 replaces it with ApprovalWorkflow |
| 8 | RFC-0005's ScheduleFireWorkflow | One activity started by a Schedule (RFC-0005 §15) | Must stay a workflow: Schedules cannot start standalone activities, and the docs say to "schedule a Workflow that invokes the Activity" |
| 9 | RFC-0005's `egress.send` | An activity inside the turn, FIFO per thread (RFC-0005 §17.2, §17.4) | No: the order per thread comes from running inside the turn |

For the delivery workflow (row 6), adoption would also cost:
- **Sweeps miss activities.** Apply and Stop decide "is there live work" by listing running *workflows* with `EventWorkflowId` (`server/services/employees/safe_apply.py:28-30`, `:114-116`; `server/services/deployment/handlers.py:844-855`). A delivery started as a standalone activity would be invisible to them: Apply could proceed during a send, and Stop would leave it running. Adoption needs one helper that also calls `client.list_activities(query=...)` and cancels through the activity handle.
- **Activity code assumes a parent workflow.** Outside a workflow `activity.info().workflow_id` is `None` (`temporalio/activity.py:118-119`). The delivery runs gates in process first (`server/services/employees/jobs.py:333`, `:376-381`), and the gate keys its wait as `t:{workflow_id}:{workflow_run_id}:{activity_id}` (`server/nodes/workflow/approval_gate/__init__.py:110-117`). Standalone, that key becomes `t:None:None:<activity id>`, so a resolution that changed shape mid-flight could ask for the same approval twice. Other activities make the same assumption (`server/services/temporal/pi_activities.py:182-183`, `server/services/employees/team_runtime.py:119-132`, `agent_activities.py:822-825`).
- **Retention is the dedupe window:** 24 hours on today's dev-server namespace, and 7 days once the value RFC-0005 §25.1 plans is set, in RFC-0005 Phases 5 and 9a (T17, RFC-0004 D4). The resolution record in the database stays the long-term dedupe, as today.
- **Replay.** The type is already in the 0.3.1 corpus (`employee_job.json`, history `employee-delivery:fixture`, 11 events), and `server/tests/temporal/test_replay_corpus.py:61-69` fails if any production type lacks a history. It must stay registered until its last run closes (31 days plus retention).
- **Gain.** The docs' benefits are billing (two Billable Actions against one, Temporal Cloud only) and latency for short jobs. Neither applies to free Temporal or a 31-day, owner-started job; pause, reset and update-options work on workflow activities too (RFC-0004 §9.5).

**Verdict:** Not applicable now. No wrapper gains enough on free self-hosted Temporal to pay for a second path; the one fit in shape loses its shape in Phase 7.

**Recommendation:**
1. Correct RFC-0004 §4.2 and §9.6, which map Standalone Activities to direct node runs (§21), and record the rule above. Doc-only, in this RFC's commit. Effort S.
2. Reopen for the E12 owner Retry (RFC-0005 §17.3), a single send the owner triggers, behind two gates: SDK 1.33.0 or later (after the planned 1.34.0 upgrade), and one live-work helper that lists and cancels both workflows and activities, used by safe-apply and Stop. (A third gate, server 1.32 on every install, is met once T5's fresh-start release ships.) Use Activity ID = the outbox key, `REJECT_DUPLICATE`, `start_to_close_timeout` set, and the egress retry policy: unlimited, with the outbox claim keeping the send at-most-once (never `maximum_attempts=1`, which would end the send at its first bucket wait; §11.5). Versioning: a call-site change in the API process; no workflow is touched. When: later. Effort M, mostly the helper. Risk: activities that read `activity.info().workflow_id` change their keys.

**Remarks:** Schedules cannot start standalone activities, so RFC-0005's ScheduleFireWorkflow stays a workflow, as the docs prescribe; T7's poll trigger is a workflow anyway. Cancellation is cooperative: `employee.job.resolve_delivery` heartbeats every 30 s through its call chain (`employee_job_workflow.py:19-22`) inside a 2-minute heartbeat timeout, so a cancel would land. On tier L the batch operator commands default off.

## 20. Amendments to RFC-0005

RFC-0005 was edited in the same commit as this RFC (T2). This table records every change, in RFC-0005's section order. RFC-0005's new §30 (Amendment log) points back here. Where §6 to §19 describe the RFC-0005 plan, they quote it as reviewed, before these amendments.

| RFC-0005 § | Change | Basis |
|---|---|---|
| §1, §3.1 E5, §5.2, §6.1 | Polls are no longer Schedules. Each polling trigger is one small workflow that runs a single retried activity; cron and reminders stay on Schedules and timers. The supervisor in §6.1 loses "upkeep" | T7; §9.2, §8.4 |
| §3.1 E12 | Extended from sends to every side-effecting call: a call that broke off is redone only when its plugin is idempotent or keyed; otherwise it is recorded `unknown` | T13; §11.2 |
| §3.1 E16 | Marked confirmed on 2026-10-24 | T5 |
| §4.2, §4.3 | The worker.py line reference updated (`:584`). RFC-0004 H2 (node activities swallow cancellation) added to the defects, fixed in Phase 1b | §13.2, §9.3 |
| §6.2 I2 | Owner actions that need Temporal write an intent row and answer at once. A background deliverer sends the Update by workflow id with a stable id until it is applied; the UI shows "applying" | T10; §7.3 |
| §6.2 I7 | An exception for agent loops: transcripts travel in LLM-step inputs and the continue-as-new carry, within bounds: at most 393,216 bytes of messages before every step, each step input under 1 MiB, the carry under 1.9 MB, the stored conversation under 1 MB | T6; §16.8 |
| §9.3 | Subscriptions are compiled in the transaction that creates the `starting` generation. Its streams are held with reason `starting` until `running` releases them | §7.2 |
| §9.4, §13.3 | Admission also checks the generation. An entity started for a generation that Reset retired completes without a turn | §7.2 |
| §9.4, §9.5, §10.2 | The relay passes the stream cursor (`consumed_seq`) as the Signal-With-Start argument, and the entity seeds `applied_seq` from it on every start. Turn numbers are allocated in the admission transaction, so turn ids, agent child ids and spend keys stay unique after passivation | §7.2, §8.2 |
| §9.5 | Under `event_os_version=1`, the consuming save raises on failure and the step retries; today's save is best effort | §16.8 |
| §10.1 | New rules for every new type: it runs in the sandbox; it cleans up only on exit exceptions; it keeps one timer over named absolute deadlines and never waits with a timeout of zero or less; it schedules no UI-only activities; it rolls over when suggested or at 1,800 Updates; its idempotency-relevant activity ids are explicit and unique across the continue-as-new chain | §5; §8.4, §17.18, §18.2 |
| §10.1 | The supervisor is rebuilt in place when lost: `ALLOW_DUPLICATE_FAILED_ONLY`, at most 3 rebuilds an hour, then an attention item asking for Reset | T20; §8.2 |
| §10.1, §10.2 | Turn row: the turn's agent children use `REQUEST_CANCEL` and `WAIT_CANCELLATION_COMPLETED`, and the turn cancels and drains its running work when cancelled | §6.2 |
| §10.1, §10.2 | The poll row splits: ScheduleFireWorkflow serves cron and alarm Schedules; a PollWorkflow `poll-<workflow_id>-g<gen>-<node_id>` runs one activity that never completes and is retried every interval | T7; §9.2 |
| §10.2 | Supervisor row: no 5-minute upkeep (an instance-wide sweep instead). Every Update carries absolute values and a revision and is sent by the T10 deliverer. The budget period end is computed outside workflow code, with a per-employee offset of 0-10 minutes. It rolls over at 1,800 Updates | §8.2, §8.4, §17.4, §12.1 |
| §10.2 | Entity row: carry cursors, not payloads. The close sequence is `wait_condition(all_handlers_finished)`, a final check, then the close with no `await` between. Every Signal type is counted, and it rolls over at 1,800 Updates | §7.4, §12.1 |
| §10.2 | AgentWorkflow row (Phase 5, new generations): budget suspension generalizes to parks. The reasons are budget, billing, quota, provider account and outage past the step budget. The Signal is `released{reason}`; an outage park probes every 15 minutes; each park sets the lease `suspended` and raises an attention item | T11, T18; §11.8 |
| §10.3 | A parked turn has not failed and spends no retry. A retried turn resumes from the saved conversation, a graph-only turn from its failed node. "Stuck and visible" holds only with the bug guard (1b.13) | T11; §11.8, §18.2 |
| §10.4 | Worker Versioning is declined because of topology (a single-process desktop, and every instance upgraded in place in the weekly window), not maturity. Phase 5's behaviour (class queues, the step budget, parking) gets its own generation flag, `resource_manifest.scheduler_version=1`, frozen at Start, so Phase 4 runs recorded without it replay unchanged. `context_pressure_version=2` is recorded per run, as version 1 is today, and chosen only for generations with the Phase 4 flag | §11.3, §16.8; T18 |
| §11 | Owner chat has no debounce: the turn starts at once and later messages merge at B1. Timers are accurate only from one second up. Customer debounce runs on an absolute deadline, `min(last + window, first + cap)`. The passivation text cites the docs' Unhandled Command rule and requires the close sequence | T9; §7.4 |
| §12 | Relief runs before every LLM step (`context_pressure_version=2`), a firing's first step and turns without tool calls included | T6; §16.8 |
| §13.1, §18.1, §23 | Turn deadlines become "slow turn" alerts that do not count parked time. A turn with no completed step for 24 hours is cancelled, not counting time parked, paused or waiting for the owner (a default). Class floors are shares of admitted turns | T19; §6.4, §13.1, §24.1 |
| §13.2 | Every top-level start sets an explicit priority key, checked by an AST test. Entities are keyed by lane; the supervisor and ApprovalWorkflow, manual Runs and Workspace tasks run at 1; cron, polls and Start-node runs at 4 | §13.3 |
| §13.2 | The dev server gets fairness and one task-queue partition in Phase 1 (T17). Every install runs server 1.32, so no per-install gating applies (T5) | T5, T17; §13.4 |
| §13.2 | LLM and compaction steps run on frozen class-queue routes, the Pi override included. `machina-tasks` is never capped, because a cap turns off eager activity dispatch. There is no shared slot pool: fixed-size suppliers per class queue | §13.2, §13.3 |
| §13.2 | No schedule-to-start timeouts: schedule-to-start and backlog are measured, from the SDK metrics (1b.38) and the dev server's metrics on a pinned port (1b.33). Sends run on their own `egress` queue. Metric labels carry no employee or thread ids | §13.3, §13.4, §17.3 |
| §13.3 | A suspended or parked lease counts toward no capacity. Delegation permits move into admission, with a wake Signal | §13.3, §12.4 |
| §13.4 | Honoured Retry-After values are capped at 5 minutes, with the raw hint kept; `x-should-retry` is read; 409 is retried for OpenAI and Anthropic. Bucket values come from 429 responses and the Gateway, seeded by a table. A wait past the step budget parks the turn (Phase 5) | §16.5, §13.2, §11.5 |
| §14 | Own-key providers reserve spend per attempt; Gateway calls keep per-activity keys. Provider billing, quota, authentication and permission failures take the suspend path, and account and billing parks pause the employee. Midnight gets the per-employee offset | §11.2, §16.16, §17.4 |
| §15 | Polls: the PollWorkflow design. Each attempt appends at most one page and its cursor in one transaction, then raises a BENIGN error with `next_retry_delay`, with jitter up to 10% of the interval and failures backing off to 1 hour. 5 consecutive failures raise "driver down" | T7; §9.2, §12.3 |
| §17.2 | Egress takes the bucket token before claiming the row, or marks `provider_called_at` just before the call, so only rows with that mark can become `unknown`. Its policy is unlimited | §11.5 |
| §17.3 | `unknown` covers every side-effecting plugin, not only sends (T13). Until the outbox exists, the redo guard (1b.1) covers graph-node sends too | T13; §11.2 |
| §17.5, §10.1, §10.2 | ApprovalWorkflow covers four waits: the approvalGate node, the reviewed-delivery gate in EmployeeJobWorkflow and AgentWorkflow, the resolution in EmployeeJobDeliveryWorkflow, and held tool calls. Decisions arrive from the T10 deliverer; Stop completes while drafts wait (T12); one reminder goes out 24 h before expiry | T10, T12; §9.6 |
| §17.7 (new) | Content rules (T15). Built-in rules (a saved credential, a secret-shaped token, a recipient outside the conversation) and owner-editable rules hold a matching message even with Ask first off. The outbox row records `held_reason`; the model is not told which rule fired | T15; §16.9 |
| §18.3, §18.4 | New attention kinds: parked turn, stuck retry, slow turn, approval expiring. New metric: activity attempts above threshold. LLM metrics use the gen_ai names with no content. The loop-lag watchdog, SDK metrics, the five starter alerts and the stuck-retry attention item move to Phase 1b (1b.30, 1b.38, 1b.39) | §11.7, §16.19, §17.10 |
| §18.5 | Telemetry tags are an opaque instance id, the tier and the release, never employee or workflow ids. The desktop keeps a local metric buffer | §17.10 |
| §19 | Overflow: at most 50 open team tasks per team and 200 per employee | §12.2 |
| §20 | Per-turn cost from the replay corpus: a deployed chat turn is about 185 events, 22 activities and 27 actions, about twice the estimate behind "35k actions a day". Peaks are marked about 2x low (est.) until EG14 measures them. Tier S runs workers in a second process (T8), and its hardening names every dev-server port | T8; §17.4, §17.8 |
| §21.2 | The weekly window replays open runs before workers poll and marks those that fail "needs Reset" (T14); a failed readiness check still rolls back. A rollback is replay-safe only before the new release completed a workflow task, unless its patches shipped dormant | T14; §17.12, §18.9 |
| §21.3 | Send keys come from workflow ids and explicit activity ids unique across the continue-as-new chain | §17.11 |
| §22 (c) | "After the 0.4 s debounce, admission grants" becomes "At once, admission grants" | T9 |
| §24.2 | Phase 1b is inserted (RFC-0006 §22.2). Phase 1 gains fairness and one partition (T17). Phases 3-9 gain the items in RFC-0006 §22.3. The controller-hardening lane gains the items in RFC-0006 §22.4, and a cancellation lane and a durable-delegations lane are added | §22 |
| §24.3 | Markers retire in four stages, helped by a rollover request at the first boot of each release; new patches ship dormant for one release after SDK 1.34.0 | §8.3, §18.9 |
| §24.4 | Phase 0.1's "blocked from snapshots and Temporal input" gets an exception: the Start-path snapshot, fixed by 1b.31 and 1b.32. Phase 1 records T5 and T17 | §17.8 |
| §25.1, §25.2 | Defaults gain RFC-0006 §24.1's list. "Rebuild a lost supervisor" leaves the open list (T20); RFC-0004 Q9 is noted as depending on 1b.1 | §24 |
| §26 | EG5 applies to customer chats only (T9). EG7 is a burst: 10,000 messages appended before the relay runs. EG12 counts from the Start commit. EG14 is measured with a repeatable driver reporting events and workflow tasks per second. New gates refer to RFC-0006 §23 | §7.2, §7.4, §17.4 |

## 21. Corrections to RFC-0004

RFC-0004 was corrected in the same commit. Each row replaces a statement that the verification passes showed to be wrong, or that a decision has since superseded. RFC-0004's new §22 (Corrections log) points back here.

| RFC-0004 § | Correction | Basis |
|---|---|---|
| §4.2 (Standalone Activities) | They do not fit direct node runs. A possible use is E12's owner Retry. SDK 1.30.0 already ships the client API, marked experimental; it is GA in 1.34.0 | §19.10 |
| §4.2 (`get_current_history_size()`) | Low value: the server suggests continue-as-new at 4,096 events or 4 MiB, and every rollover check reads the suggestion first. Keep a size check only as a backstop | §8.3 |
| §4.2 (`start_delay`) | Not for the Undo window: an approval must accept Updates during it, and a delayed start runs no handler. Never use it on ids addressed by Signal-With-Start | §9.4 |
| §4.2 (queue rate limits, eager start) | Per-credential buckets replace per-provider LLM queues with queue-wide caps. Eager start is not applicable: chat runs start as children or by Signal-With-Start, and neither can be eager | §13.2, §14.4 |
| §5 H7 (fix) | Always start the worker pool. The flag selects only the routing frozen into new runs | §15.2 |
| §5 H10 | For versioned generations a failed controller fails the generation (`controller_execution_missing`), and only Reset recovers; the `USE_EXISTING` rebuild applies to unversioned generations only. RFC-0005's supervisor is rebuilt in place (T20) | §8.2 |
| §5 H12 | Reset releases a browser claim only while the terminated execution is still retained (24 h on the dev server) and only for its own generation. A manual Run's claim is never released | §6.2 |
| §5 M4, §9.2 (employee delivery) | There are three 31-day activity sites, and a retry within the 2-minute delivery lease strands the draft. The interim fix is 1b.9; Phase 7's ApprovalWorkflow replaces them | §9.6 |
| §5 M8, §9.2, §16 Phase 0 item 4 | No schedule-to-start timeouts on owner-routed queues (a profile never moves) or the Pi driver queue (parked parents make a backlog normal). Monitor them and raise an attention item | §15.2 |
| §5 M9, §20 (line references) | Updated to `worker.py:430-440`, `:584`; `agent_activities.py:12-13`; `_runtime.py:124` | §13.2 |
| §6.6 | Name both `--metrics-port` and `--http-port` with `*_PORT` variables: the dev server opens both on random ports, and netpolicy protects only named ports. Pin a loopback metrics port, which telemetry will scrape. Drop `history.enableStandaloneActivityOperatorCommands` for tier S: the dev server forces it on | §17.8, §14.1, §19.10 |
| §6.7, §11.4 | SDK 1.33's fix for sticky-poller stalls needs a cache smaller than the workflow-task slot count (10); OpenCompany's caches of 50, 100 and 500 are unaffected, so it is not a reason to upgrade | §14.1, §17.9 |
| §7.1 (retry storm alert) | Exclude permit waits and T7's planned poll retries; include compaction, controller paging and compensation policies | §11.7 |
| §7.3 | Adding `summary=` to an activity needs no patch: replay compares only an activity's id and type | §5 rule 6 |
| §8.1 item 1 | Also forbid cleanup that awaits a workflow operation in `finally`, `except Exception` or `except BaseException` without first re-raising a workflow bug | §18.2 |
| §8.2 | `rpc_timeout` bounds each request only. After acceptance, `execute_update` waits for the outcome without it, so bound the whole call with `asyncio.timeout`. Keep random ids for Workspace submissions, whose checks can fail after acceptance | §7.3 |
| §8.3 | An idle controller never rolls over, but busy versioned controllers do: each cron firing sends four Updates, and handlers request a rollover under pressure | §8.3 |
| §8.4 | On cancellation, cancel and drain with a bound. On failure, start nothing new and let running nodes finish. No sweep exists for team tasks, employee jobs or permits; Reset must close them (1b.21) | §6.2, §6.3, §10.2 |
| §8.5 | Cancellation types are not compared on replay, but they decide whether a cancel emits a command. Changing one therefore needs a patch whenever a replayed history can contain that cancel; passing today's default explicitly changes nothing. The zero-duration timer rule is on https://docs.temporal.io/workflow-definition | §5 rule 6, §6.2 |
| §8.7 | "Keep bugs stuck but visible" holds only with the bug guard (1b.13) | §18.2 |
| §9.2 (LLM step) | Defaults: attempts of 10 min (cloud) and 30 min (local), budgets of 15 and 45 min. Expiry parks the turn instead of failing it, so the budget ships with parking in RFC-0005 Phase 5 (T18). The `PayloadSizeError` line holds for SDK 1.30.0; 1.34.0 removes that Python type, so recheck after the upgrade | §11.3, §16.3 |
| §9.2 (bookkeeping) | Storing node output and loading the graph use a declared unlimited bookkeeping policy | §11.1 |
| §9.4 | Agent tool sends refuse a second attempt only for plugins with an approval spec whose `sends()` counts the call and that are not `restrict_while_asking`; MCP tools only while their per-tool Ask is on. T13 extends the guard to every side effect, graph nodes included. Idempotency keys come from the workflow id and an explicit activity id unique across the continue-as-new chain | §11.2, §17.11 |
| §9.6 | Standalone activities do not fit single-node direct runs: the Workspace task is a child of its controller, the agent branch is a child workflow, and the status writes after a failure need a workflow. Keep a workflow whenever something must run after the activity fails | §19.10 |
| §10.5 | The docs' infrequent-polling shape is adopted by T7 for RFC-0005 Phase 3; the measurement becomes Phase 3's acceptance test | §9.2 |
| §11.1, §17 (Q14 answer) | The desktop stays one process; hosted tier S runs workers in a second process (T8); tier L follows RFC-0005 §20 | §15.2 |
| §4.2, §6.6, §11.3 (fairness) | `matching.enableFairness` and one task-queue partition ship with the fresh-start release, because partitions can be lowered safely only on a new database. Per-employee keys follow in RFC-0005 Phases 4-5 | T17; §13.4 |
| §11.3, §13.3 | Providers limit per credential: class queues with fixed slots and no queue-wide cap. Retry-After is honoured for at most 5 minutes per retry, with the raw hint kept; a wait longer than the remaining step budget parks the turn (Phase 5); there is no 60 s cap like the cookbook's. Both the OpenAI and Anthropic SDKs honour `x-should-retry` and retry 409; turning off node-level SDK retries waits for Q9. Priority keys follow RFC-0005 §13 and RFC-0006 §13.3 | §13.2, §16.5 |
| §13.4, §16 Phase 2.7 | Passing the transcript by reference is superseded by T6. Trimming the continue-as-new input keeps `tools`; it needs no patch and moves to Phase 1b (1b.19). The byte-based rollover check is unnecessary | §16.8 |
| §15 (evaluated, not adopted) | Local activities are reopened: progress broadcasts cost 205 ms before the first LLM step and are 35-52% of an agent's history. For Nexus, the reason is that on self-hosted Temporal the registry is per cluster. Reopen only if an instance must call another cluster and external-URL endpoints leave pre-release | §14.2, §19.1 |
| §16 Phase 3 | Drop the eager-start experiment for chat runs | §14.4 |
| §17 Q15 | Answered: start anyway and mark the affected generations "needs Reset"; a failed readiness check still rolls back | T14 |
| §18 AG9 | No policy on a paid or side-effecting call is unlimited without a time budget, except the LLM step and compaction on generations without RFC-0005 Phase 5's `scheduler_version=1`, which keep unlimited retries under the retry alert (T18). Every other unlimited policy (bookkeeping, compensations, queue paging, permit waits, T7's poll activities, sends and deliveries made at-most-once by their claim) is declared and idempotent, and the retry alert covers all but the planned retries of polls and permit waits | §11.1 |
| §18 AG12 | "Started" means the Start commit | §7.2 |

## 22. Rollout

### 22.1 Phase 1 (the fresh-start release)

RFC-0005 Phase 1 pins the Temporal CLI and starts every install on an empty database (E16, T5). By T17, that release also passes the dev server three dynamic-config values:
- `matching.enableFairness=true`;
- `matching.numTaskqueueReadPartitions=1`;
- `matching.numTaskqueueWritePartitions=1`.

Read partitions can be lowered safely only on a database created with the lower value, so this is the one release where every database gets it. §13.4 owns the details.

Phase 1 ends with the SDK upgrade to 1.34.0 (RFC-0005 §24.4). From the next release on, new patch ids ship dormant for one release, so a rollback still replays (§18.9; RFC-0005 §24.3).

### 22.2 Phase 1b: pattern fixes before Phase 2

Phase 1b is new to RFC-0005's rollout (§20).
- **What it holds:** fixes that need no workflow versioning. They change activity bodies, activity options, client calls, worker setup, tests or docs, and the replay corpus stays green after each.
- **Commits:** each is one commit, and the section named owns the fix's full description and its test.
- **Order:** the tracks are in priority order under T4: side effects first, then waits, then lifecycle.

**Track 1: side effects never happen twice**

| Id | Fix | § |
|---|---|---|
| 1b.1 | Effect classes (`read_only`, `idempotent`, `unsafe`) for every side-effecting operation, and a redo guard on every path, graph nodes and tool calls alike. A later attempt of an `unsafe` call answers `SendOutcomeUnknown` (sends) or `OutcomeUnknown` (other effects), records `unknown` and tells the employee. A registry contract test requires every non-read-only plugin to declare its class | 11.2 |
| 1b.2 | Activities re-raise cancellation instead of returning a failure envelope; a worker shutdown becomes the retryable `NodeWaitInterrupted`; a cancel during a send is recorded `unknown` (RFC-0004 H2) | 9.3 |
| 1b.3 | The legacy tool path passes each plugin's declared retry policy and timeouts | 11.2 |
| 1b.4 | Apify actor waits must fit one activity attempt on tool paths; today a manual run starts a new paid actor run every 10 minutes | 9.3 |
| 1b.5 | An approved send waits while the employee is stopped: `claim_send` refuses, `recover_sends` skips the row, Resume starts it (T12) | 9.6 |
| 1b.6 | `recover_sends` runs from the 10-second recovery loop | 10.2 |
| 1b.7 | Built-in outbound content rules (a saved credential, a secret-shaped token) hold the message at today's hold points, even with Ask first off (T15, first part) | 16.9 |

**Track 2: waits survive restarts**

| Id | Fix | § |
|---|---|---|
| 1b.8 | Timer node: the deadline is anchored to the first schedule, a wait longer than one attempt is refused, and a shutdown is retryable | 9.5 |
| 1b.9 | Reviewed delivery re-attaches to its own lease after any restart. The three 31-day sites retry without limit, and a clean failure clears `inflight` | 9.6 |
| 1b.10 | `expire_due` expires gate rows past their expiry that no waiter holds | 9.6 |

**Track 3: lifecycle and cancellation**

| Id | Fix | § |
|---|---|---|
| 1b.11 | Record two replay fixtures before any cancel-path change: cancelling a root while an agent child runs, and an uncontrolled branch failure | 6.2 |
| 1b.12 | The four agent-child starts (six call lines) use `REQUEST_CANCEL` and `WAIT_CANCELLATION_COMPLETED` | 6.2 |
| 1b.13 | The bug guard: exit cleanup in 8 run methods and 4 delegation helpers, and the per-event handlers of 3 types, re-raise a workflow bug first (`is_workflow_bug`) | 18.2 |
| 1b.14 | A chat run past 24 h cancels its workflow | 6.4 |
| 1b.15 | Every client Update call is bounded by `asyncio.timeout`, the Workspace Update-with-Start included; a timeout answers "pending" | 7.3 |
| 1b.16 | Controller queue paging retries without limit (`QUEUE_PAGING_RETRY`) | 12.3 |
| 1b.17 | Poll activities heartbeat, run under a fixed start-to-close of 3 minutes, and fail credential errors fast | 9.2 |
| 1b.18 | Each poll cycle appends at most one page (50 items or 512 KB) and advances its cursor only over appended items | 12.3 |
| 1b.19 | The AgentWorkflow continue-as-new carry drops the seed conversation, memory content and parameter snapshot, and keeps `tools` | 16.8 |

**Track 4: compensation and Reset**

| Id | Fix | § |
|---|---|---|
| 1b.20 | Compensations retry until they land: delegation cleanup, job failure, the run record | 10.2 |
| 1b.21 | Reset closes teams, team tasks, employee jobs and permits | 10.2 |
| 1b.22 | Start's compensation runs in reverse order, by known ids, each step logged | 10.2 |
| 1b.23 | Boot reconcile finishes a Reset that a crash interrupted | 10.2 |
| 1b.24 | The pause-on-failure breaker counts each run once | 11.8 |

**Track 5: errors and retries**

| Id | Fix | § |
|---|---|---|
| 1b.25 | LLM step: an `asyncio.timeout` deadline inside each attempt (the attempt timeout minus 30 s), a Gemini client timeout, and 30-minute attempts for local and OpenAI-compatible endpoints. The step's total budget waits for Phase 5 (T18) | 16.3 |
| 1b.26 | LLM-step bugs (`LLMStepInternalError`) and `PayloadSizeError` are non-retryable, in both non-retryable lists | 11.4 |
| 1b.27 | Compaction runs on the LLM step's retry policy, with a heartbeat; an empty summary is non-retryable from attempt 3 | 11.4 |
| 1b.28 | Retryable LLM errors carry the BENIGN category | 18.5 |
| 1b.29 | Workflow task failures are logged at ERROR (`WorkflowTaskFailureFilter`) | 18.5 |
| 1b.30 | The retry alert. An attention item is raised once an activity passes its attempt threshold (8 for LLM steps and compaction, 10 elsewhere; planned retries excluded) and cleared on success. A quieter log: attempt 2, the threshold, then every 10th. The metric counter follows in Phase 8 | 11.7 |

**Track 6: platform and workers**

| Id | Fix | § |
|---|---|---|
| 1b.31 | The Start-path parameter snapshot is sanitized; activities restore secrets from the saved row; the sanitizer drops `Authorization`, `Proxy-Authorization` and `Cookie` values | 17.8 |
| 1b.32 | Live controllers refresh a known listener's snapshot on re-registration, which clears raw copies held since Start | 17.8 |
| 1b.33 | Named dev-server ports (`--http-port`, `--metrics-port`) and `services/netpolicy.py` on the HTTP request node; the wrong `--metrics-port` comment at `_runtime.py:124` is fixed here | 17.8 |
| 1b.34 | The worker pool always polls; the flag only sets the routing of new runs | 15.2 |
| 1b.35 | The standalone worker refuses to run outside distributed mode | 15.2 |
| 1b.36 | One worker builder with one registration and its check, used by every Worker construction | 15.3 |
| 1b.37 | Workers shut down through `Worker.shutdown()`, so in-flight activities get the graceful window | 17.9 |
| 1b.38 | The SDK metrics runtime and five starter alerts as attention items, quiet during the weekly window and after a laptop wakes; the dev server gets `system.numConsecutiveWorkflowTaskProblemsToTriggerSearchAttribute=5`, so `TemporalReportedProblems` marks stuck runs | 17.10 |
| 1b.39 | An event-loop lag watchdog | 17.19 |

**Track 7: lints, tests and docs**

| Id | Fix | § |
|---|---|---|
| 1b.40 | Determinism, orchestration and blocking-call lints (§5 rules 3-5) | 17.18 |
| 1b.41 | The retry-policy lint and shared policy constants | 11.1 |
| 1b.42 | The golden identity snapshot records `task_queue` and `priority` | 15.2 |
| 1b.43 | A real-server test of the 2,000-Update limit | 12.1 |
| 1b.44 | A history-cost counting script for the replay corpus | 17.4 |
| 1b.45 | Comment and doc corrections, and the transaction rule in DESIGN.md | 10.1, 17.13 |
| 1b.46 | Refuse `deploy_workflow` without a generation (no client calls it), and remove the unused client `deployWorkflowAsync` | 8.2 |

**Order and dependencies**
- 1b.11 comes before 1b.2, 1b.12 and 1b.13, so the cancel paths they change are already in the corpus.
- 1b.13 comes before 1b.40, whose exit-cleanup rule passes only after the bug guard.
- 1b.12 comes before 1b.40, whose child-start rule passes only once the agent-child starts state their policies.
- 1b.12 comes before 1b.14.
- 1b.31 and 1b.32 ship in the same release.
- 1b.1 comes before any answer to RFC-0004 Q9 (graph nodes retrying transient errors).
- 1b.20 and 1b.16 introduce the constants 1b.41 enforces.
- 1b.36 comes before any work on T8's worker split.

### 22.3 What each RFC-0005 phase gains

| Phase | Adds (owning section) |
|---|---|
| 2 (inbox and router) | Webhooks commit before they answer, which fixes the Delayed Callback misuse (§9.5). Subscriptions are compiled when the generation is created, and EG12 counts from the Start commit (§7.2) |
| 3 (drivers) | T7's poll workflow: one activity retried every interval with a BENIGN error, items and cursor in one transaction, jitter and backoff defaults, BENIGN retries logged at DEBUG (§9.2); PollWorkflow runs in the sandbox (§17.18). Provider idempotency keys where providers offer them (§11.2) |
| 4 (entities) | The stream cursor as the entity's start input; turn numbers from admission; a seeded `applied_seq`; the close sequence (§7.2, §7.4, §8.2). No owner-chat debounce (T9, §7.4). The T10 deliverer for every owner action (§7.3). Priority keys at every start site with the AST test (§13.3). `placement_version=1` routing and the frozen dispatch map (§15.2, §15.3). Relief before every LLM step (`context_pressure_version=2`), tool inputs by reference, and a consuming save that raises (§16.8). New types sandboxed with no UI-only activities (§17.18). AgentWorkflow's progress broadcasts become local activities behind `progress_local_version=1`; this can land earlier, after the SDK 1.34.0 upgrade (§14.2). Apply for new generations (§10.2). Slow-turn alerts (T19) and the 24-hour no-progress backstop, a default (§6.4). One timer over absolute deadlines in each new type, and one instance-wide upkeep sweep instead of a timer per supervisor (§8.4) |
| 5 (scheduler and budgets) | Everything here except the HTTP retry details (§16.5), which are activity code for every generation, is gated by the new generation flag `scheduler_version=1`. The LLM step budget and parking (T11, T18, T19): budgets of 15 and 45 min, a probe every 15 min, a 7-day park limit (§11.3, §11.8). Retry-After beyond the budget parks (§11.5); HTTP retry details (§16.5). Class queues with fixed slots and explicit routes for LLM and compaction steps; admission as the only floor; per-credential buckets; no schedule-to-start timeouts; suspended leases count toward no capacity (§13). Own-key spend reserved per attempt (§11.2). Budget-period timers spread over 10 minutes (§17.4). Namespace retention and search attributes at namespace creation (RFC-0004 D4, kept by T17; the fleet's deploy logic in Phase 9a; §17.2) |
| 6 (timers and messaging) | Alarms join the supervisor's one timer (§8.4). The durable Timer node behind a flag (§9.5). Delegation permits move into admission, with a cap on open team tasks (§12.2, §12.4). Versioned `alarms_changed` (§18.7) |
| 7 (egress and approvals) | ApprovalWorkflow covers the approvalGate node, the three reviewed-delivery waits and held tool calls. Decisions arrive through the T10 deliverer; Stop completes while drafts wait (T12); one reminder 24 h before expiry (§9.6). Owner-editable and recipient content rules (T15, §16.9). Sends on their own `egress` queue, claiming after the token (§13.4, §11.5). Checkpointed external job ids (§9.3) |
| 8 (supervision) | Attention items in the employee summary: parked turns, slow turns, expiring approvals, and stuck retries, which 1b.30 already raises on the instance. The attempt-threshold metric; gen_ai metric names; core logs at WARN; activity summaries without user text (§11.7, §16.19, §17.15) |
| 9 (tiers and fleet) | T8's worker split on hosted tier S, enabled per instance only when every live generation carries `placement_version=1` (§15.2). The weekly window replays open runs before workers poll and marks failures "needs Reset" (T14); a readiness failure still rolls back (§17.12). The EG14 capacity driver (§17.4). The rollback check that relies on dormant patches (§18.9; the rule starts after Phase 1, §22.1). Opt-in spans, with the telemetry content filter (§17.15) |
| 10 (retirement) | Legacy listener types kept for replay only. Markers retired in four stages, helped by a rollover request to every live controller at the first boot of each release (§8.3, §18.9) |

### 22.4 Parallel lanes

- **Controller hardening** (RFC-0005 §24.2, for generations still on WorkflowControlWorkflow) adds:
  - dead-letter poison events after 5 spawn attempts, with spawn backoff: one patch, `controller-spawn-backoff-v1` (§11.6, §7.4);
  - stable execution-control Update ids, and slim Update results with the Resume count fixed (§7.3);
  - `register_trigger` hardening; triggers in the start input (§7.2);
  - digest seen keys (§7.4), and the slim carry with the cancellation fix (§8.3);
  - the trigger filter and status broadcasts as local activities behind a patch (§14.1);
  - once the slim carry and the cancellation fix have shipped, every live controller is asked to roll over at the first boot of each release (§8.3);
  - graph approval gates stop holding Stop, behind the generation flag `approval_wait_version=1` (§9.6).
- **Cancellation** (new; after Phase 1b, before Phase 4):
  - MachinaWorkflow and AgentWorkflow cancel and drain their running work, with a 2-minute bound, behind a patch;
  - one failure policy: running nodes finish and nothing new starts (§6.3);
  - the delegation-window fix (§6.3), and child-start failures as node failures (§6.2).
- **Durable delegations** (before T8's split): Claude Code and RLM delegations run as durable child workflows instead of asyncio tasks (§6.2).

### 22.5 Version flags and patch markers

Every change to the commands of a shared workflow type is gated, so that open histories keep replaying. A generation flag is written into `resource_manifest` by `begin_generation` and frozen into the workflow input at Start (RFC-0005 §24.1 rule 2); a patch marker is a `workflow.patched` call. New workflow types need neither, because only generations with their flag start them. This RFC names the following; the golden identity test records every marker (1b.42).

**Flags**

| Flag | Recorded in | Gates | When | § |
|---|---|---|---|---|
| `event_os_version=1` | generation | the new event path: entities, turns, the supervisor, the consuming save | RFC-0005 Phase 4 | RFC-0005 §10.4 |
| `merge_version=1` | AgentWorkflow input | merge points B1 and B2, and the carry of unmerged inputs | Phase 4 | §8.3 |
| `context_pressure_version=2` | each run, in `agent.prepare_payload`'s result | relief before every LLM step | Phase 4 generations | §16.8 |
| `placement_version=1` | generation | process-affinity routing and the frozen dispatch map (T8) | Phase 4 | §15.2 |
| `scheduler_version=1` | generation | class queues, the LLM step budget and parking (T11, T18) | Phase 5 | §11.3, §11.8, §13.2 |
| `permit_wait_version=1` | generation | delegation permits wait in admission | Phase 6 | §12.4 |
| `durable_timer_version=1` | generation, and the run input of manual Runs | the Timer node as a workflow timer | Phase 6 | §9.5 |
| `approval_wait_version=1` | generation | graph approval gates stop holding Stop | controller lane | §9.6 |
| `approval_wait_version=2` | generation | the gate as an `ApprovalWorkflow` child | Phase 7 | §9.6 |
| `progress_local_version=1` | generation | AgentWorkflow progress broadcasts as local activities | after SDK 1.34.0 | §14.2 |
| `tool_concurrency_version=1` | generation | read-only tool batches run concurrently | P3 | §6.3 |
| `run_cap_version=1` | generation | old generations wait for a run slot, only if the owner chooses an interim cap (O4) | controller lane | §12.2 |

**New patch markers**

| Marker | Workflow | Change | When | § |
|---|---|---|---|---|
| `machina-close-and-drain-v1` | MachinaWorkflow | cancel and drain running work; one failure policy | cancellation lane | §6.2, §6.3 |
| `agent-delegation-drain-v1` | AgentWorkflow | cancel and drain its running work | cancellation lane | §6.2 |
| `agent-delegation-window-v2` | AgentWorkflow | start the next delegation after any completion, failures included | cancellation lane | §6.3 |
| `controller-spawn-backoff-v1` | WorkflowControlWorkflow | spawn backoff and the poison-event dead letter | controller lane | §11.6 |
| `controller-slim-update-result-v1` | WorkflowControlWorkflow | slim execution-control Update results | controller lane | §7.3 |
| `controller-slim-carry-v1` | WorkflowControlWorkflow | the slim continue-as-new carry | controller lane | §8.3 |
| `controller-poll-cancel-v1` | WorkflowControlWorkflow | poll loops re-raise cancellation | controller lane | §8.3 |
| `controller-spawn-local-v1` | WorkflowControlWorkflow | the trigger filter and status broadcasts as local activities | controller lane | §14.1 |
| `controller-message-budget-v1` | WorkflowControlWorkflow | rolling over at 1,800 Updates or 9,000 Signals, only if 1b.43 shows the server does not suggest it | controller lane | §12.1 |

From the first release after the SDK 1.34.0 upgrade, each new marker ships dormant for one release, except fixes to sends, money and approvals (§18.9). Markers already in the code that this RFC cites (`controller-durable-queue-v2`, `controller-event-accumulator-v1`, `workspace-node-cancellation-v1`, `agent-user-action-error-v1`) are unchanged.

## 23. Acceptance gates

Every gate runs with the pinned CLI (`TEMPORAL_TEST_CLI`) where it says "real server", and with time skipping where it says so. Every change to workflow code also passes the N-1 replay gate and the golden identity test.

### 23.1 Phase 1 and Phase 1b

- **Phase 1b.**
  - Every Phase 1b item ships with the test its owning section names (§22.2 lists the section).
  - After each item, `test_replay_corpus.py` and `test_workflow_identity_golden.py` stay green, apart from deliberate changes to the identity snapshot (1b.42).
- **Phase 1 (T17).**
  - The dev-server command line carries `matching.enableFairness=true` and both partition values at 1.
  - A database created by an older release would keep four read partitions. After E16 no such database exists, and a test proves that the release never lowers read partitions on an existing database.

### 23.2 Phases 2 and 3

- **Start window (§7.2).** An event accepted 10 ms after Start committed is delivered once the generation runs (replaces EG12's wording). A chat message sent while the generation is `starting` is delivered.
- **Polls as retried activities (T7, §9.2).**
  - 20 simulated 60-second polls run for 24 hours on the real server. Each poll workflow's history stays under 10 events, and the growth of `temporal.db` is measured against today's loop.
  - A kill -9 between fetch and commit loses no item and marks none seen.
  - Stop makes no provider call, and Reset ends the workflow.
  - 5 consecutive credential failures raise one "driver down" item and back the interval off.
  - A quiet attempt logs nothing above DEBUG, and a changed interval applies at the next attempt.
  - PollWorkflow runs in the sandbox with `RAISE_ON_UNINTENTIONAL_PASSTHROUGH`, and the golden test asserts it (§17.18).
- **Provider idempotency keys (§11.2).** A Stripe write retried after a broken-off attempt sends the same key, and Stripe records one effect.

### 23.3 Phase 4

- **Entity identity (§7.2, §8.2).** An entity that passivates and restarts gives its next turn a new turn number. That turn's agent child id, `llm-step-*` activity ids and spend reservations differ from the previous turn's, and it re-runs no consumed input. An entity started for a generation that Reset retired completes without a turn.
- **Close sequence (§7.4).**
  - A `deliver` that arrives during the entity's completing task is handled by that run, or starts a new run that sees it.
  - Excess inputs returned at continue-as-new are delivered again, not dropped.
- **Owner chat (T9).**
  - An owner message's entity history shows no timer before the turn child starts.
  - A second owner message sent during the turn appears in the next step's model input.
  - Customer inputs at 0, 1.5, 3 and 4.5 s, with a 2 s window and an 8 s cap, produce one turn that starts by 8 s.
- **T10 and T12 (§7.3, §9.6).**
  - With Temporal down, Stop answers at once with "applying" and is applied when Temporal returns, and a re-delivered intent is applied once.
  - Stop completes while an approval draft waits.
  - A decision taken while stopped is recorded at once and sent only after Resume.
- **Turn deadlines (T19) and the 24-hour backstop (§6.4, a default).**
  - A turn past its deadline raises a "slow turn" item and is not cancelled.
  - A turn with no completed step for 24 hours is cancelled, and turn n+1 starts only after it closed. A parked turn, or one waiting on an approval draft or the owner's browser, is not.
  - A turn that ignores the cancel is terminated with its agent children after 2 minutes.
  - A cancelled turn's late save does not advance `consumed_seq`.
- **Priority keys (§13.3).**
  - The AST test fails when any top-level start omits `priority=`.
  - On one partition, a key-1 task is dispatched ahead of 50 backlogged key-4 tasks on a one-slot queue.
- **Placement (§15.2, §15.3).**
  - In a two-process test with `placement_version=1`, a WhatsApp send, a phone action and a Claude Code step run in the API process, asserted by process id. Node-status frames from the worker process reach a socket through the relay.
  - The worker process refuses to start while any live generation lacks `placement_version=1`.
  - Bumping a plugin's version changes no command of an open run.
- **Transcript relief (§16.8).**
  - Under `context_pressure_version=2`, a firing whose stored conversation is 900 KB of answers without tool calls sends a first step of at most 393,216 bytes of messages.
  - A step input over 1 MiB after relief fails with `AgentStepInputTooLarge`.
  - Version-1 histories replay unchanged.
- **Consuming save (§16.8).** Under the new event path, a failing save fails the step attempt, and the input is answered once.
- **Apply (§10.2).** An Apply interrupted at each step converges to either the new or the old snapshot once the deliverer retries.
- **Local activities (§14.2).** A new-generation AgentWorkflow history has no `agent.broadcast_progress` activity events, a failing broadcast does not fail the turn, and the first LLM step is scheduled earlier than on the 0.3.1 path.
- **Timers (§8.4).**
  - Time-skipping tests of the shared deadline helper: a deadline moved earlier and later; a past deadline acts at once; no deadlines means no timer.
  - A lint forbids `wait_condition` with a timeout of 0 or less.
  - With 100 idle employees, no supervisor workflow task runs for an hour, while a stale lease is still reaped within 5 minutes by the sweep.
- **Sandbox (§17.18).** A golden test asserts every RFC-0005 type is sandboxed, and the suite runs them with `RAISE_ON_UNINTENTIONAL_PASSTHROUGH`.

### 23.4 Phase 5

- **Parking, provider outage (T11, T18, §11.3, §11.8).** A fake provider answers 503 for 40 minutes:
  - an owner-chat turn retries for 15 minutes, then parks with an attention text;
  - a Stop sent while it is parked completes;
  - after recovery the turn answers its original input without a new message.
- **Parking, account problem.** An authentication error parks the turn and pauses the employee with the provider's hint; saving a key and pressing Resume finishes the same turn. A billing error parks and pauses the same way, and Resume or "Try now" finishes it.
- **Parking with a paused employee.** A turn whose account park paused the employee is still dead-lettered after 7 days if nobody resumes it.
- **Parking across downtime.** With time skipping, a step whose budget expires while no worker runs parks, and the boot wake probes it at once.
- **Retry-After beyond the budget.** A 429 with `Retry-After: 3600` parks the turn, with its first probe after 3,600 s and no activity attempt pending meanwhile.
- **Park limit (T19).** A turn parked for 7 days fails, and its input is dead-lettered with an attention item.
- **Old generations (T18).** Generations without `scheduler_version=1` keep unlimited LLM-step retries and fail-and-pause, and their histories replay green.
- **Class queues and buckets (§13.2).**
  - A new-generation AgentWorkflow schedules `agent.execute_llm_step` and `agent.compact_context` on `llm-c<k>`, while every 0.3.1 fixture still replays.
  - A 429 with Retry-After 7 s delays the next attempt by at least 7 s, logs no ERROR, holds no slot, and makes that credential refuse every class for 7 s.
- **Fairness (§13.4).** With fairness on and one partition, two employees with 20 backlogged tasks each on a one-slot queue alternate within one task.
- **Own-key spend (§11.2).** A step whose first attempt bills and times out, then succeeds, records both attempts in `spend_events`.
- **HTTP retry details (§16.5).**
  - `x-should-retry: false` on a 503 is non-retryable.
  - 409 from OpenAI and Anthropic retries.
  - On a call with no step budget (a generation without `scheduler_version=1`, or a node's own call), Retry-After 3,600 s gives `next_retry_delay` 300 s, with the raw hint kept.
- **Budget timers (§17.4).** With 100 supervisors under time skipping, period-end timers spread across the 10 minutes after midnight in the owner's time zone. On a daylight-saving day the supervisor and the ledger roll over at the same instant.
- **Retention and search attributes (§17.2).** After boot, `describe_namespace` reports 7 days. A failed search-attribute registration stops Temporal startup with the reason.

### 23.5 Phases 6 and 7

- **Durable Timer node (§9.5).** A 2-hour Timer survives two backend restarts and fires within seconds of its due time. Stop completes during the wait, and the next node runs on Resume.
- **Delegation permits (§12.4).** 20 queued delegations hold no activity slot and write no rows while they wait. One release wakes exactly one waiter, and a dropped wake is recovered within 60 s.
- **Team task cap (§12.2).** The 51st open task in one team is refused with a tool error that names the limit.
- **Alarms (§18.7).** An `alarms_changed` with a lower version is ignored, an earlier alarm shortens the wait, and a past due alarm fires `alarms.fire_due` once.
- **ApprovalWorkflow (§9.6, §16.10).**
  - A decision taken while the worker is down is recorded and delivered on recovery.
  - Undo returns the draft to its original expiry, and a decision committed just before the expiry timer wins.
  - Stop completes while a draft waits, and a held tool call approved while stopped goes out within 30 s of Resume.
  - One reminder arrives 24 h before expiry, and no worker slot is held while waiting.
  - A stale revision is rejected by the validator, and the same Update id returns the first outcome.
  - The gate node and the three delivery waits all use it.
- **Content rules (T15, §16.9).**
  - An owner rule holds a matching message on every send path, even with Ask first off.
  - The recipient rule holds a reply to a party outside the customer thread and never fires in owner chat.
- **Egress claim order (§11.5).** A bucket wait longer than the claim lease never marks an uncalled send `unknown`.

### 23.6 Phases 8 and 9, and the lanes

- **Retry alert (§11.7).**
  - Phase 1b (1b.30): an activity failing past its threshold raises one attention item and clears it on success. Permit waits and T7 poll attempts never count.
  - Phase 8: each such attempt also counts toward `opencompany_activity_high_attempts`.
- **Telemetry (§16.19, §17.10).** Exported LLM metrics use the gen_ai names and carry no content attribute (RFC-0005 EG15). Exported tags carry no employee or workflow id.
- **Upgrade (T14, §17.12).** An upgrade to a build with a deliberate non-deterministic change starts its workers, marks exactly the affected generation "needs Reset", and runs the others. A forced readiness failure still rolls back.
- **Worker split (T8).** On a two-process tier S, no process-bound activity runs in the worker process, and node status and progress reach the browser through the relay.
- **Dormant patches (§18.9; after SDK 1.34.0).** Release N+1 with patch "x" dormant replays histories that N+2 recorded with the marker, and N+1's own runs record none.
- **Controller lane.**
  - An event whose spawn always fails is dead-lettered after 5 attempts while the 10 events behind it run, and the patch-closed replay reproduces today's loop.
  - A register activity whose first attempt times out after acceptance leaves one accepted Update.
  - Carried seen keys are 16-character digests.
  - After a release change, every live controller rolls over once at boot.
- **Cancellation lane (§6.2, §6.3).**
  - Cancelling a root with a running agent child and node activity delivers a cancel to both, and the root closes Cancelled after both finish or after the 2-minute bound.
  - In a two-branch run where one branch fails, the other completes, nothing downstream starts, and the run ends `success: False`.
- **Graph approval gates (§9.6).** With `approval_wait_version=1`, Stop completes while a graph draft waits, and the send after it runs once, after Resume.
- **Durable delegations (§6.2).** A Claude Code or RLM delegation survives a backend restart, Reset cancels it, and a repeated call with the same identity attaches instead of starting a second runner.
- **Supervisor rebuild (T20).**
  - A supervisor terminated by hand is rebuilt from the database, keeping chat and Context.
  - A fourth rebuild within an hour stops and raises an attention item asking for Reset.

## 24. Defaults chosen and open items

### 24.1 Defaults (change at review)

Each was chosen under T4 (reliability first). The alternative is named where one was seriously considered.

**Orchestration and messaging**
- **Graph-run failure:** running nodes finish and no new node starts. Alternative: cancel and drain on failure too. A cancel mid-send would turn a known outcome into `unknown`. (§6.3)
- **Concurrent tool calls:** only when every call in one model response is a declared read-only tool without an approval spec, at most 8. Alternative: keep every call sequential. (§6.3)
- **Claude Code and RLM delegations:** durable on Temporal, through one runner workflow per delegation. Alternative: refuse them on Temporal. (§6.2)
- **Chat runs past 24 hours:** the run's workflow is cancelled (1b.14). Alternative: keep it running and post a late answer. (§6.4)
- **Bounds:**
  - MachinaWorkflow's cancel drain: 2 min.
  - Turn cancel grace: 2 min, then termination.
  - Update deadlines: 15 s for Workspace, 30 s for the controller, safe-apply and the deliverer.
  - The socket waiter: 10 s.
  - (§6.4, §7.3)
- **Poison events:** 5 spawn attempts, then a dead row; spawn backoff 5 s, 30 s, 2 min, 10 min. Carried seen keys are 16-character digests. (§7.4, §11.6)
- **T10 covers Start and Apply** as well as Stop, Resume, approval decisions and Interrupt. A decision taken while stopped is delivered and recorded at once, and its send waits for Resume. (§7.3)

**Lifecycle and timers**
- **Rollover at upgrade:** at the first boot of each release, every live controller is asked to roll over, once the slim carry and the cancellation fix have shipped. (§8.3)
- **Upkeep:** one instance-wide sweep every 5 minutes signals only the employees with overdue work. It replaces a 5-minute timer per supervisor. (§8.4)
- **Budget periods:** each ends at midnight in the owner's time zone plus a stable per-employee offset of 0-10 minutes, computed outside workflow code. (§17.4)
- **Long agent waits:** the Timer tool keeps a one-attempt cap; longer waits use `set_alarm` (RFC-0005 Phase 6). (§9.5)
- **Generation-less deployments:** the `deploy_workflow` command without a generation is refused and its unused client call removed (1b.46), instead of fixing the old controller's deviation for them. (§8.2)

**External interaction**
- **Poll pacing:**
  - jitter up to 10% of the interval, at most 30 s;
  - failures double the interval, up to 1 hour;
  - 5 consecutive failures raise "driver down";
  - start-to-close 3 min, heartbeat timeout 60 s.
  - (§9.2)
- **Poll page:** 50 items or 512 KB per cycle. (§12.3)
- **Reviewed delivery and Stop:** a reviewed delivery keeps holding Stop until ApprovalWorkflow (RFC-0005 Phase 7), because its gate and its send share one activity. (§9.6)
- **Approval reminders:** one, 24 h before expiry, as an attention item. Drafts whose whole life is 24 h or less get none. (§9.6)
- **Browser work for an offline owner:** keeps waiting and the owner is told. Stop completes meanwhile; Reset still waits for the browser release, as it does today.
  - Today a chat run still ends at 24 h (1b.14).
  - Under RFC-0005 Phase 5 (`scheduler_version=1`) the wait is a park, with the 7-day limit (T11, T19), and it never counts toward the 24-hour backstop. (§15.2)

**Errors and retries**
- **LLM step** (ships in Phase 5 for new generations, under `scheduler_version=1`, T18):
  - attempts of 10 min for cloud providers and 30 min for local and OpenAI-compatible endpoints;
  - a budget of 15 and 45 min;
  - a park probe every 15 min, fixed;
  - a park limit of 7 days (T19).
  - (§11.3)
- **Park reasons:** the employee's daily budget, provider billing, quota (the Gateway's `budget_exceeded` included), authentication, permission, and the step budget's expiry. Invalid requests, missing models, protocol errors and context-length errors still fail. Account and billing parks also pause the employee; outage parks do not. (§11.8, §16.16)
- **Stuck-turn backstop:** a turn that has completed no step for 24 hours is cancelled. Time spent parked, paused, or waiting for the owner (an approval draft, the owner's browser) does not count, and each completed step restarts the clock. This is a default, not part of T19. (§6.4, §9.6)
- **T11's scope:** agent turns. A graph run without an agent still fails; its input is dead-lettered and replayed from the failed node. (§11.8)
- **Retry alert threshold:** 8 attempts for LLM steps and compaction, 10 elsewhere. (§11.7)
- **Unlimited policies:** compensations, queue paging and bookkeeping retry without limit, 1 s to 30 s apart. Each is declared and covered by the retry alert. (§11.1)
- **Idempotent:** means a second attempt after a broken-off one is safe. Every non-read-only plugin declares its class, and the default is `unsafe`. (§11.2)
- **Graph-node sends:** on a later attempt they answer `SendOutcomeUnknown`, rather than running with one attempt, so the outcome is recorded explicitly. (§11.2)

**Capacity**
- **Reserved share:** E10's "reserved share" is a share of admitted turns, enforced only by admission. Class queues use fixed-size slot suppliers, and there is no shared slot pool. (§13.1)
- **Provider limits:** they come from 429 and Retry-After responses plus the Gateway's own limits; a shipped table only seeds the buckets. (§13.2)
- **Priority keys:**
  - owner-started work runs at key 1: chat, approvals, control actions, manual canvas Runs and Workspace tasks;
  - Start-node runs, cron and polls run at key 4;
  - entities are keyed by lane: talk 1, customer 2, worker 3;
  - the supervisor and ApprovalWorkflow run at key 1.
  - (§13.3)
- **No schedule-to-start timeouts:** measure schedule-to-start and raise an attention item instead. (§13.3)
- **Sends:** they run on their own `egress` queue. (§13.4)
- **Tool queues:** priority only, no class floors, until measured. (§13.3)
- **Parked leases:** a suspended or parked lease counts toward no capacity. (§13.3)
- **Team tasks:** at most 50 open per team and 200 per employee. (§12.2)
- **Rollover:** long-lived types roll over at 1,800 Updates. (§12.1)

**Workers and platform**
- **T8, read narrowly:**
  - only resources that cannot be shared go to the instance queue: the WhatsApp bridge client, the Android phone, the Claude session pool, the process manager and the local browser runtime;
  - the worker process relays its status frames to the API process;
  - the worker builder initializes the encryption key in the worker process.
  - (§15.2)
- **The pool always polls,** and the flag only sets routing. (§15.2)
- **Progress broadcasts:** they become best-effort local activities. In AgentWorkflow this sits behind a per-generation flag (`progress_local_version=1`) after the SDK 1.34.0 upgrade, so it can land before Phase 4; new types never schedule UI-only activities. (§14.2)
- **Agent tools and the local Temporal:** the HTTP request node goes through `services/netpolicy.py`, and every dev-server listener gets a named port. Shell and code tools stay off the Hire allowlist. Dev-mode graphs that give them to customer-facing employees are documented as the owner's risk. (§17.8)
- **History at rest:** no payload codec for now; retention of 7 days (Phase 5); hosted backups encrypted in their bucket. RFC-0004 Q13 stays open. (§17.8)
- **Fleet control plane:** no fleet-level Temporal. Each instance upgrades itself in the window from the deploy logic. (§17.3)
- **Patches:** after SDK 1.34.0, new patch ids ship dormant for one release (`patch_activation_callback`). Sends, money and approvals may activate at once. (§18.9)
- **Bugs in workflow code** stay stuck and visible: the bug guard, ERROR logs, metrics and `TemporalReportedProblems`. RFC-0004 Q5 stays open. (§17.16)
- **Watchdogs:**
  - the loop-lag probe runs every 100 ms, warns at 250 ms, and raises an attention item after 60 s sustained;
  - the workflow-task schedule-to-start alert fires at p99 above 5 s.
  - (§17.10, §17.19)
- **Heartbeats:** the interval is a third of the heartbeat timeout, at most 30 s. (§9.3)

### 24.2 Still open for the owner

| # | Question | Default above | Where |
|---|---|---|---|
| O1 | RFC-0004 Q5: should bugs in workflow code fail runs fast? | Stuck and visible | §17.16 |
| O2 | RFC-0004 Q9: should graph nodes retry transient errors through Temporal? | After 1b.1: yes for `read_only` and `idempotent` plugins, and for `unsafe` ones only when the failure came before the provider was contacted | §11.4 |
| O3 | RFC-0004 Q13: should hosted instances encrypt history with a payload codec? | No codec; encrypted backups | §17.8 |
| O4 | Should old generations get an interim cap on parallel runs before Phases 4-5? | No; wait for admission | §12.2 |
| O5 | How do team jobs and delegations share an employee's capacity? | They count toward the cap of 4 at class C3 and never take the owner slot, with the open-task cap | §12.2, §12.4 |
| O6 | Should Claude Code employees send messages through the MCP bridge? | No durable route yet. Sends are refused while Ask first is on and run once otherwise | §16.12 |
| O7 | Should approval decisions carry a reason, and should anything escalate beyond the reminder? | An optional reason field; no escalation | §9.6 |
| O8 | Should manual canvas Runs take an admission lease? | No; they run at key 1 on the run's queue | §13.3 |

## 25. What this RFC does not change

- **RFC-0005's architecture:** the inbox, router and relay; conversation entities and the supervisor; scheduling classes and budgets; the egress outbox; instance tiers and the weekly window. E1-E16 stand. This RFC refines them where a pattern or an owner decision requires it (§20).
- **RFC-0002's conversation rules:**
  - requests are built from messages;
  - the store records what was sent;
  - loads are loud and saves are best effort, except the consuming save under RFC-0005's new path (§16.8);
  - a carried rollover transcript wins.
- **RFC-0004's decisions,** apart from the statements corrected in §21. Its open questions Q5, Q9, Q11 and Q13 stay open: §24 proposes defaults for Q5, Q9 and Q13, and RFC-0005 already defaults Q11 (cron overlap) to skip. Q15 is answered by T14.
- **The deployment model:** free Temporal only, one instance per owner, no Temporal Cloud and no Nexus.
- **The plugin authoring model,** except that every side-effecting operation now declares its effect class (1b.1).
- **The chat protocol,** except the "applying" state of owner actions (T10) and the new attention kinds (§20).

## 26. References

**Temporal documentation** (fetched 2026-10-24; append `.md` to any page for raw markdown):
- Design patterns: https://docs.temporal.io/design-patterns and its 45 sub-pages, each cited by URL in §6 to §15.
- AI and the cookbook: https://docs.temporal.io/ai and https://docs.temporal.io/ai/cookbook, cited in §16.
- Best practices: https://docs.temporal.io/best-practices and https://docs.temporal.io/develop/python/best-practices, cited in §17.
- Nexus and standalone activities: https://docs.temporal.io/nexus, https://docs.temporal.io/standalone-activity, https://docs.temporal.io/standalone-nexus-operation, cited in §19.
- Timers: https://docs.temporal.io/workflow-execution/timers-delays ("should not rely on sub-second accuracy for Timers").
- Workflow task errors: https://docs.temporal.io/references/workflow-task-errors.
- The site index: https://docs.temporal.io/llms.txt.

**Code and releases:**
- temporalio/samples-python at commit 33f7dc29b2c68f01b65155b3b9a570dac67fabe0 (2026-10-22): https://github.com/temporalio/samples-python
- The temporalio Python SDK 1.30.0 (installed) and 1.34.0 (source distribution).
- Temporal CLI v1.9.1 (server 1.32.0).

**OpenCompany:**
- [RFC-0002](./RFC-0002-AGENT-CONTEXT-AND-MEMORY.md), [RFC-0004](./RFC-0004-TEMPORAL-SDK-FEATURE-ADOPTION.md), [RFC-0005](./RFC-0005-EVENT-OS-ON-TEMPORAL.md).
- [TEMPORAL_ARCHITECTURE.md](./docs-internal/TEMPORAL_ARCHITECTURE.md), [temporal-workflow-control.md](./docs-internal/temporal-workflow-control.md), [event_framework.md](./docs-internal/event_framework.md), [chat_protocol.md](./docs-internal/chat_protocol.md), [DESIGN.md](./docs-internal/DESIGN.md).
- The replay corpus: `server/tests/temporal/fixtures/0.3.1/` and `server/tests/temporal/test_replay_corpus.py`.
