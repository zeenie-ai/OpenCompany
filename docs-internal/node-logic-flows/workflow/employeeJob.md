# Team work (`employeeJob`)

`EmployeeJobNode` in `server/nodes/workflow/employee_job` is the managed
employee team's durable request and reviewed delivery boundary. Its live
Pydantic contract takes precedence over this description.

Operations:

- `submit`: Talk submits a substantive mission to the approved lead. The
  stable tool-call identity creates one durable job; admission starts an
  `EmployeeJobWorkflow` on Temporal and returns a progress acknowledgement.
- `intake`: app/schedule triggers capture their original outputs, graph,
  parameters and server-controlled destinations before the lead starts.
- `deliver`: resolve the originating job by its execution or team identity.
  Assignment receipts and unaccepted task submissions cannot publish.
  Accepted work uses the original delivery configuration and approval gates.
  On the team runtime (`EmployeeJobWorkflow`, the lead's own delivery and the
  owner's resolution of an uncertain send) the delivery activity retries
  without limit (`REVIEWED_DELIVERY_RETRY`): a worker restart during the
  approval wait re-attaches to the same delivery under its lease, a sink
  that refuses cleanly ends the job failed, and a failed job is never
  delivered again. In a v1 graph `deliver` runs as the node's own activity
  under the plugin's default policy, 3 attempts of up to 24 h each, so it
  re-attaches only within those. A job that a Reset or "Stop work and apply"
  cancels sends nothing more, even when its approval wait ends afterwards.

A Reset cancels the workflow's open jobs, queued ones included, and a queued
job of a generation that has ended (reset, failed or gone) is cancelled
instead of started. A manual canvas run's job has no generation and is never
refused this way.

`operation`, `lead_node_id` and `delivery_node_ids` are server-controlled
configuration. Models can supply only the mission when this node is a tool.
Specialists never connect directly to delivery sinks. Task Manager is
intrinsic to the lead; do not add a duplicate binding.

Chat results have a stable message UID. An interrupted external send retains
its durable intent for reconciliation and is not blindly repeated. New teams
require Temporal and AgentWorkflow readiness; queued owner requests remain
saved while admission is blocked. Safe Apply retains their identities.

When an external send's outcome is uncertain, the owner sees **It arrived**
and **It did not arrive; retry** in the conversation. Only an authenticated
owner can resolve it; public messages and worker sockets cannot. Arrival
confirmation marks that sink complete without sending it again. Explicit
retry preserves the original approved recipients, approval outcomes and
reviewed response. A stable owner request is saved with the job before the
`EmployeeJobDeliveryWorkflow` continues remaining sinks; restart recovery
reattaches to that identity. Unreviewed work and assignment acknowledgements
remain ineligible for either action.

See [Agent Builder](../ai_tools/agentBuilder.md) and
[the employee team runtime guide](../../employee_teams.md).
