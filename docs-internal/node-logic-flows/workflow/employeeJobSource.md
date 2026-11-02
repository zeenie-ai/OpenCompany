# Original email (`employeeJobSource`)

| Field | Value |
|---|---|
| **Implementation** | [`EmployeeJobSourceNode`](../../../server/nodes/workflow/employee_job_source/__init__.py), [`job_source.py`](../../../server/services/employees/job_source.py) |
| **Operation** | `read` |
| **Tests** | [`test_email_input.py`](../../../server/tests/services/employees/test_email_input.py) (pagination, captured authority, cross-job and stale assignments) |
| **Tool** | `employee_job_source` |
| **Category** | workflow; read-only job-source capability |

## Purpose and schema

Read bounded pages of the original email captured by an admitted employee job.
The node's `output-top` tool-role handle connects this capability to an agent.
It is usable as a tool; a node placed on a canvas does not grant access to an
arbitrary employee job.

| Parameter | Default | Constraint |
|---|---|---|
| `content` | `body_text` | `body_text`, `original_body`, or `original_mission` |
| `offset` | `0` | Strict nonnegative integer character offset |
| `limit` | `10000` | Strict integer from 1 to 10000 |

Unknown arguments are forbidden. Owner, workflow, job and task identities come
from trusted runtime context, not model-supplied parameters.

## Execution and authorization

`read` delegates to `read_employee_job_source`. Direct canvas, workflow and
agent-tool calls all require an admitted `employee_job_id` and a caller in
that workflow. The captured lead is authorized by the captured team plan.
A teammate additionally requires active team membership and a matching,
noncancelled assignment to that same job; cross-job, missing and stale task
scope is refused.

The source must identify one captured Gmail output, with an unambiguous
legacy fallback. Readable text is taken from captured email metadata or
rebuilt from the saved body. It does not fetch the mailbox again or permit
an arbitrary path/message ID. No specialized Workspace task is declared.

## Result and effects

The result contains `content`, page `text`, `offset`, `next_offset`,
`total_chars`, `complete`, and captured message/thread/from/to/subject metadata.
Use `next_offset` for the next page. Requests beyond EOF return an empty page
at the stable EOF cursor. The operation reads database/captured data; it does
not send mail, write files, or mutate the job.

Invalid arguments, missing authority and unavailable/ambiguous captured source
fail through the existing node/tool error boundary. Temporal invocation uses
the shared Activity control contract; Stop/Resume does not bypass authority,
and Reset does not create new access to an old job.

See [employee teams](../../employee_teams.md) and
[Team work](./employeeJob.md).
