# Managed AI employee teams

Normal hiring remains describe the job, review the routine/apps/hours/approval
rule, then Hire. Optional “Their team” lists one to three responsibilities;
models, nodes and topology stay in Dev mode. Talk is the conversational
contact. The lead delegates substantive tasks through its connected
Task Manager and reviews submitted results before delivery.

Hire saves a Task Manager for the AI Employee lead and connects its
`output-tool` to the lead's `input-tools`. The team plan records the manager's
node ID. Opening that node in the editor shows the lead's task board.
Talk gets its own Task Manager for submitting jobs and checking their status.
Each role also gets a dedicated Master Skill node. Talk keeps its selected
library skills. The lead's default instructions cover assignment and review.
Hire connects the AI Employee's
top output to the Talk agent's Team input (`input-employee-team`). This edge
identifies the lead that receives `submit_job`. The submission starts a durable
job and returns a receipt. Talk can call `get_job_status` to check whether the
lead has assigned work or the job failed. The lead uses `spawn_<teammate>` to
assign work to a new specialist instance, then reviews the submitted result.

Start and Apply upgrade older v2 hires using their recorded lead and contact.
The upgrade adopts each role's existing managers and skill holders and adds
missing ones. Each admitted generation retains its own graph until Apply.
The Team input is excluded from ordinary graph execution, so a chat reply or
a scheduled lead run cannot trigger a second job through this edge.

Each concrete tool node has exactly one owning agent. Business capabilities
belong to the specialist responsible for the work; the lead and Talk request
that work through Task Manager. Private utilities such as clocks, memory and
checklists may use the same node type with separate configured instances.
Builder additions target one selected member, and never broadcast the same
callable node to the whole team. Existing shared bindings are separated when
that capability is updated, without resetting active work or other members.

Job admission and reviewed delivery run off canvas. The admitted graph captures
the team plan, original recipient outputs and parameter snapshot; durable job
records and Temporal activities retain restart, approval and uncertain-send
recovery. The graph contains the lead, specialists, Context/instructions,
taskTrigger and actual approval/output nodes, with no employeeJob forwarding
nodes. Saved recipe v1 graphs keep their existing nodes and execution paths.

The employee page always reads current detail. Its bounded twenty-row work view
shows native Task Manager assignments/reviews and public tool phases, with
five-second active snapshot polling and coalesced status refresh; idle polling
runs every fifteen seconds. Pauses, approvals, disconnects and slow work show
plain-text status and last activity. Raw internal reasoning is never displayed.

For newly started Temporal generations, Stop closes admission across the
controller, enrolled job/graph/detached roots and their attached agent children.
Admitted work and bookkeeping drain before Stopped is acknowledged. Resume
releases the same pending model/tool continuation without repeating recorded
completed tools. Queued events and the chat run's lane remain occupied while
stopped. Independent Workspace tasks and separately approved sends retain their
own lifecycle. See the [control contract](temporal-workflow-control.md) and
[chat protocol](chat_protocol.md) for revision guards, timeout reconciliation,
restart behavior and legacy compatibility.

Creation uses a stable owner-scoped key and payload hash, renewable lease,
atomic graph/parameter/metadata/grant save, and a recoverable activation
intent. Failed or abandoned builds keep the same workflow identity. A saved
team never falls back to single-agent execution when Temporal is unavailable.

Builder can inspect live contracts and search/read shipped Markdown by
manifest ID. Documentation is embedded for npm, desktop and Docker; archived
prose ranks after current documentation. Plugin schemas and locked fields
remain authoritative. Capability grants are scoped and revocable in Settings
and checked again inside the graph mutation. Public app messages cannot
approve expanded access. Execution approval for sending remains separate.

Safe Apply pauses producer admission while allowing existing work and its reviews
to finish when generation execution admission is open. It cannot reopen a
stopped generation or drain reviews while Stop/held Resume closes admission.
It replaces future snapshots without Reset, preserving Context,
memory, chats, files, approvals and queued events. Paused employees stay
paused. “Stop work and apply” is the explicit interruption path. The editor's
Reset continues to clear execution state, and it also cancels the team's open
tasks and requests, queued ones included, which the terminated runs can no
longer close. It cancels an Apply still waiting too, so the next Start runs the
graph saved by then.

Conversion requires an owner review of a recognizable template graph and
keeps a full configuration snapshot. Custom graphs require Dev review.
Workflow IDs, accounts, schedules and existing conversations retain their
identity. Rollback refuses to overwrite later edits.

Independent rollout switches:

| Setting | Default | Purpose |
| --- | --- | --- |
| EMPLOYEE_TEAMS_ENABLED | true | New Hire/Builder team creation |
| EMPLOYEE_CAPABILITY_UPDATES_ENABLED | true | Reviewed capability expansion |
| EMPLOYEE_SAFE_APPLY_ENABLED | true | Apply saved changes without Reset |
| EMPLOYEE_TEAM_CONVERSION_ENABLED | false | Opt-in reviewed legacy conversion |

Existing single-agent employees continue executing. Conversion is never
automatic. Run backend, frontend, CLI, replay and distribution checks before
enabling conversion for an installation.

## Gmail intake and no-assignment rollout

Managed Gmail admission preserves the original trigger output and rendered
mission in the job's existing source JSON. Initial context uses readable text
and fits within the 20,000-character mission limit, including any shortening
notice. Literal instructions and other email fields are preserved. The
intrinsic read-only `employee_job_source` tool pages through `body_text`,
`original_body`, or `original_mission` with character offsets and pages up to
10,000 characters. Its job and assignment identities come from runtime scope;
agents cannot choose another job through tool arguments.

New admitted jobs have separate team identities even when email arrivals share
one deployment generation. Saved recipe v1 graphs retain their nodes, while
recipe v2 admits jobs off canvas. Existing bound teams and original recipients
remain attached across worker retries and resumes.

Upgrade workers normally. The polling payload correction changes child input
only: already-recorded empty child inputs remain historical, while queued and
future events use native polling dictionaries. No graph rewrite or database
migration is needed. The `employee-no-delegation-repair-v1` Temporal marker
allows one corrective delegation instruction within the existing iteration
budget. A queued durable assignment counts as progress. Exhausted or repeated
no-assignment finalization records `lead_no_assignments`, without publishing
the rejected draft or entering approval or sending.

Historical cleanup is deliberate and dry-run by default. From `server/`, using
the installation's existing database and Temporal settings:

```powershell
python -m services.employees.reconcile_jobs --workflow-id ID
python -m services.employees.reconcile_jobs --workflow-id ID --job-id JOB_ID
python -m services.employees.reconcile_jobs --workflow-id ID --job-id JOB_ID --apply
```

Review the content-free JSON decisions and exact run proof references before
applying. The service requires zero persisted assignments and an unambiguous
completed lead execution, verifies Continue-As-New chains, and rechecks
eligibility under the same transaction reservation used by task insertion.
Paused, reused, reset, ambiguous or unavailable executions and any approval or
delivery evidence are skipped. Repeated application is idempotent. The command
does not start model calls, workflows, approvals or sends, and there is no
periodic historical scanner.

Regression tests can use a disposable PostgreSQL server via
`TEST_POSTGRES_URL=postgresql+asyncpg://.../postgres`; the shared employee fixture
creates and drops a unique database per test. Real controller upgrade and
history replay checks use a native Temporal CLI selected with
`TEMPORAL_TEST_CLI`.
