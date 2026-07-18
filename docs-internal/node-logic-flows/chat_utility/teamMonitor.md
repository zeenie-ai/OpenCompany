# Team Monitor (`teamMonitor`)

Team Monitor is the read-only view of one team lead execution. Connect an
`orchestrator_agent` or `ai_employee` output to its `input-main` handle.

## Panel behavior

The backend declares `isMonitorPanel`, `hideInputSection`,
`hideOutputSection`, and `hideRunButton`. The frontend consumes those hints and
renders only `TeamMonitorPanel` in the full-height middle section, matching the
Master Skill panel layout. Generic parameters and input/output columns are not
shown.

The panel:

- derives workflow and lead scope from the connection;
- shows agents on the lead's `input-teammates` handle before a run exists;
- merges persisted member status (`working`, `idle`) during execution;
- displays blocked, queued, running, submitted, accepted, failed, and cancelled
  counts;
- resolves the newest lead execution instead of trusting stale `active` flags;
- lists all tasks from that execution, including submitted and accepted work;
- exposes an execution selector for historical runs;
- refreshes on team lifecycle broadcasts and supports manual refresh.

Team Monitor never mutates tasks. Use the Task Manager panel bound to the lead
for accept, retry, modify, reassign, cancel, and finish actions.

## Completion semantics

Worker completion changes a task to `submitted`. It is visible as awaiting
review and is not counted as Done. Only `accepted` tasks (and historical legacy
`completed` rows) contribute to the completed count. This distinction prevents
the monitor from reporting unreviewed work as finished.

## Backend operation

`server/nodes/utility/team_monitor/__init__.py` provides a read-only snapshot
for workflow execution and templating. It resolves connected teammates from the
canvas even without a `team_id`, then follows the connected lead output's
`team_id`, `execution_id`, and `root_execution_id`. It merges durable members,
all tasks, active tasks, counts, and recent lifecycle events.

The operation performs no writes, external calls, or subprocess work.

## Related

- [Durable Agent Teams](../../agent_teams.md)
- [Task Manager](../ai_tools/taskManager.md)
