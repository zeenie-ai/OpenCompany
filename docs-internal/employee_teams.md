# Managed AI employee teams

Normal hiring remains describe the job, review the routine/apps/hours/approval
rule, then Hire. Optional “Their team” lists one to three responsibilities;
models, nodes and topology stay in Dev mode. Talk is the conversational
contact. The lead delegates substantive tasks through the intrinsic durable
Task Manager and reviews submitted results before delivery.

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

Safe Apply stops new admissions while allowing existing work and its reviews
to finish. It replaces future snapshots without Reset, preserving Context,
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
