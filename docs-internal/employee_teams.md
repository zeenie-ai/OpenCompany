# Managed AI employee teams

Normal hiring remains describe the job, review the routine/apps/hours/approval
rule, then Hire. Optional “Their team” lists one to three responsibilities;
models, nodes and topology stay in Dev mode. Talk is the conversational
contact. The lead delegates substantive tasks through the intrinsic durable
Task Manager and reviews submitted results before delivery.

Recipe v2 follows the shipped `AI_Employee.json` template's canonical teammate,
Context and skill connections. That template shows an explicit Task Manager
tool; managed employees use the equivalent intrinsic binding without a duplicate
tool node. Talk uses its `submit_job` operation, while the lead uses `assign_task`
and the existing review operations. Each managed assignment supplies mission,
context, acceptance criteria and resolved dependency IDs.

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
Reset continues to clear execution state as before.

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
