# Managed AI employee teams

## New hires (recipe v3)

Hire shows editable reporting lines before saving the workflow. The same
recipe serves Hire, Agent Builder and the editor's **File → Hire an employee**.
The Chat Employee owns the conversation. Its direct report is the Orchestrator.
The initial team can have up to two additional roles, including a manager with a worker.
For example, a research team can look like this in the Hire preview.

```text
Chat Employee (ai_employee)                 talks with the user
└── Orchestrator (orchestrator_agent)       reviews the research
    └── Research manager (ai_employee)     reviews the sources
        └── Research specialist           gathers the sources
```

In the saved graph, each report's `output-top` connects to its immediate
manager's `input-teammates`. These edges describe who can assign work.
Chat and the Orchestrator each own their instructions, Context, Task Manager
and Task Completed trigger. The chat input and ordinary reply connect to Chat.

A greeting, clarification or status check stays with Chat. For substantive
work, Chat's Task Manager opens a job and assigns its outer board task to
the Orchestrator. Each manager assigns work to its direct reports through
its own Task Manager. Submitting a task returns it to the employee instance
that assigned that attempt. A manager can request changes on the same task,
or accept the submission and use it to finish its own assignment. Replaying
the initial chat submission reuses the job and outer task. Each delegated
task records the assigning node and agent run, which identify the immediate
manager and its upstream attempt even when jobs use the same canvas nodes.
A manager waiting for child work saves its turn and releases its execution
slot. Child submission or failure wakes that manager through its durable inbox.

Chat reviews the Orchestrator's submission and writes the final answer on
the originating chat branch. A busy chat receives the answer when its current
turn finishes. After the conversation is cleared, open the original assignment
on Chat's Task Manager board to read the final answer. App jobs keep the
destination and approval gate captured at intake.

Each manager's Task Completed node connects to its `input-task`. The event matches the assigning
employee before the node's status, worker and task filters are evaluated.
Standalone Task Completed nodes can observe matching events across employees.
Separate success and error filters can share an employee. Validation flags
overlapping filters on that employee. Redelivery uses the trigger node and
committed event identity to recognize the same firing.

Builder can add a specialist under a selected manager. Apply activates the
reviewed reporting lines for future work. If an assignment needs a missing
role, the owner can approve the change, apply it and start fresh work.
The configured delegation depth counts levels below the Orchestrator.

For regression coverage, the [single-slot Temporal test](../server/tests/temporal/test_employee_single_slot_integration.py)
runs the nested handoff with a scripted model and replays the recorded
histories. Set `TEMPORAL_TEST_CLI` to the local Temporal executable to run
it. The [hierarchy runtime tests](../server/tests/temporal/test_employee_hierarchy_runtime.py)
cover job and chat delivery, and the [app intake tests](../server/tests/temporal/test_employee_app_hierarchy.py)
exercise the captured app destination.

## Earlier hires

Recipe v2 saves a Task Manager for the AI Employee lead and connects its
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
In the editor, the canvas accepts a connection to the Team input only from
`output-top` of a node whose spec declares the `leadsEmployeeTeam` uiHint (the
AI Employee) to a node whose spec declares an `input-employee-team` handle
(`useReactFlowNodes`); `employee_team_connection_issues` checks the same at
save.

## Shared runtime and editing

Each concrete tool node has exactly one owning agent. Business capabilities
belong to the specialist responsible for the work. Its manager requests
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

When the owner asks Chat to add a specialist, Agent Builder connects it to
the selected manager. With **Ask first** off, the owner's
request saves the specialist and its scoped tools directly. The same rule
applies when the owner equips an existing specialist. With **Ask first**
on, **Allow and add** in Access saves the reviewed change. Apply activates
the saved team for future work, after which that manager can assign a task.

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
recipes v2 and v3 admit jobs off canvas. Existing bound teams and original recipients
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
