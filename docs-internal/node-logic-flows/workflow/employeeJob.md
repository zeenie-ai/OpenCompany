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
