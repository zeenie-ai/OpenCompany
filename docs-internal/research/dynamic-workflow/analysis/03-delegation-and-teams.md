# Delegation, Task Manager and teams: code map

Scope: how one agent starts, tracks, finishes and cancels a run of another agent: the Task Manager tool, the `delegate_to_*` tools, teams and teammates, on Temporal and on the older in-process paths. The agent turn loop in general is mapped by another file in this folder.
Written 2026-11-12, as of commit 908a8ce6. Read from a clean working tree at 48c34266; a read-only `git diff --stat` between the two showed no server code file cited here changed. Nothing was run: every statement comes from reading code.
Markers: INFERRED means derived by reading several places together, not observed. NOT VERIFIED means not checked.

## 1. Summary

- On Temporal there are two ways one agent runs another. A team lead (`orchestrator_agent`, `ai_employee`) calls `task_manager(operation="assign_task")`: an activity writes a task row, the lead's workflow starts a detached `DelegatedTaskWorkflow` and the model is told "queued" at once; the runner starts the teammate's `AgentWorkflow`, stores its answer and emits an event, and the lead reads the answer in a separate review run started by a `taskTrigger`.
- Any other agent with an agent wired to its tool handle calls `delegate_to_*`: an attached child `AgentWorkflow` that the caller awaits inside the same tool turn, with the child's answer as the tool result.
- So "start several sub-agents, await them, read all answers in one turn" already exists as workflow code (the attached path): `workflow.start_child_workflow`, then `await handle`, with a start window of `max_concurrent_subagents` (default 3).
- The teammate catalog is already in workflow state: the lead's `tools` list holds one `delegate_to_*` entry per teammate (hidden from the lead's model), with node id, type, label, parameters and a capability sentence.
- Bounds today: a per-root concurrency counter in the database (3), delegation depth 2, and the caller's own `max_iterations` (200). There is no count budget on delegations and no size cap on a delegated answer.
- A sub-agent run is its own workflow and model loop, but not a clean slate: with a Context node it loads and saves one conversation row keyed by its node id, so tasks of one teammate share a transcript and two concurrent runs overwrite each other (last write wins).
- On the Task Manager path each finished task publishes its own event for the lead's `taskTrigger`, and the lead reads results in review runs; no code joins several results into one lead turn.

## 2. Files and responsibilities

| File | Lines (approx.) | What it owns |
|---|---|---|
| [server/services/temporal/agent_workflow.py](../../../../server/services/temporal/agent_workflow.py#L552) | 3,549 total; delegation at 285-408, 552-805, 1586-2185, 2252-2578, 2758-2873, 3172-3259, 3345-3371, 3447-3525 | `DelegatedTaskWorkflow` (the detached runner); inside `AgentWorkflow._run_impl` the two closures that start sub-agents, the Task Manager preflight, the per-call await, permit yield and reacquire, the rollover guard |
| [server/services/temporal/agent_activities.py](../../../../server/services/temporal/agent_activities.py#L1133) | 2,905 total; 1133-1891 (prepare), 2086-2656 (delegation activities), 2659-2704 (registration) | `agent.prepare_payload` (team creation, hidden delegates, the lead's team prompt, limits); `agent.queue_delegation`, `begin_delegation`, `finish_delegation`, `cancel_delegation`, `acquire_subagent_permit`, `release_subagent_permit`, `register_task_execution`, `finalize_team` |
| [server/nodes/tool/task_manager/__init__.py](../../../../server/nodes/tool/task_manager/__init__.py#L126) | 373 | The Task Manager tool: name, description, argument schema, the operation matrix, the `delegation_request` envelope |
| [server/services/agent_team.py](../../../../server/services/agent_team.py#L21) | 484 | `AgentTeamService`: team creation, authorized assignment, guarded task transitions, permits, team messages |
| [server/core/database.py](../../../../server/core/database.py#L3295) | team section 3295-4374 | Team, member, task, attempt, message and permit persistence; the compare-and-swap task transitions |
| [server/models/database.py](../../../../server/models/database.py#L502) | 304-365 (settings), 502-521, 619-762 | Tables `agent_teams`, `team_members`, `team_tasks`, `team_task_attempts`, `agent_messages`, `subagent_concurrency_counters`, `subagent_concurrency_permits` |
| [server/services/plugin/edge_walker.py](../../../../server/services/plugin/edge_walker.py#L67) | 790 | Teammate discovery over `input-teammates`, delegate names, capability text, the roster line, the intrinsic Task Manager binding, the review prompt |
| [server/services/handlers/tools.py](../../../../server/services/handlers/tools.py#L372) | 1,128 | In-process tool dispatch; `_execute_delegated_agent` (fire-and-forget asyncio delegation), `check_delegated_tasks`, process-local registries |
| [server/services/temporal/pi_inline_delegation.py](../../../../server/services/temporal/pi_inline_delegation.py#L123) | 412 | Detached Coding Agent delegation from callers that run in-process: `PiInlineDelegationWorkflow` wrapping `DelegatedTaskWorkflow` |
| [server/nodes/agent/_events.py](../../../../server/nodes/agent/_events.py#L85) | 155 | The task-completed event: workflow-scoped Signal fan-out plus an identity-only WebSocket frame |
| [server/nodes/trigger/task_trigger/__init__.py](../../../../server/nodes/trigger/task_trigger/__init__.py#L40) | 83 | `taskTrigger`: filter by task id, agent name, status, parent node |
| [server/nodes/agent/_inline.py](../../../../server/nodes/agent/_inline.py#L57) | 163 | In-process pre-dispatch for every agent; teammates become tool entries for leads |
| [server/nodes/agent/_specialized.py](../../../../server/nodes/agent/_specialized.py#L101), [server/nodes/agent/_handles.py](../../../../server/nodes/agent/_handles.py#L36) | 143, 48 | Shared agent class; the lead handle set with `input-teammates` |
| [server/services/employees/team_recipe.py](../../../../server/services/employees/team_recipe.py#L76) | 310 | Builds a hired employee's team graph: lead, specialists, per-member Context, the review `taskTrigger`, the team plan |
| [server/services/employees/team_runtime.py](../../../../server/services/employees/team_runtime.py#L206) | 293 | Managed-assignment validation, job admission for a lead run, the admitted plan |
| [server/services/employees/jobs.py](../../../../server/services/employees/jobs.py#L279) | about 480 | `EmployeeJob` lifecycle; `deliver_job` (delivers only when every task is accepted) |
| [server/services/temporal/employee_job_workflow.py](../../../../server/services/temporal/employee_job_workflow.py#L71) | 167 | `EmployeeJobWorkflow`: runs the lead for a submitted job, then delivery |
| [server/services/deployment/execution_control.py](../../../../server/services/deployment/execution_control.py#L56) | 212 | Stop and Resume fan-out over controller roots and their pending children |
| [server/services/deployment/handlers.py](../../../../server/services/deployment/handlers.py#L1000) | 870-1043, 2640-2700 | Reset: terminate every workflow of the generation, then close open team work |
| [server/services/team_task_trace.py](../../../../server/services/team_task_trace.py#L133) | 255 | Sanitized Temporal history of one task attempt, for the model and the panel |
| [server/services/agent_teams/handlers.py](../../../../server/services/agent_teams/handlers.py#L256) | 288 | WebSocket commands for the Task Manager and Team Monitor panels |
| [server/services/workflow_validator.py](../../../../server/services/workflow_validator.py#L197) | 197-291 | Team topology rules at save and Start |
| [server/services/ai.py](../../../../server/services/ai.py#L2414) | 2414-2724 | Tool name, description and schema for a delegate tool (`DelegateToAgentSchema`) |
| [client/src/components/parameterPanel/TaskManagerPanel.tsx](../../../../client/src/components/parameterPanel/TaskManagerPanel.tsx#L60) | panel | The editor's task board |

## 3. How a delegation executes

### 3.1 Task Manager on Temporal (the team-lead path)

1. Prepare, once per lead run. The first command of the lead's `AgentWorkflow` is the activity `agent.prepare_payload` (id `prepare-payload`, 60 s start-to-close, 3 attempts) ([agent_workflow.py:1091](../../../../server/services/temporal/agent_workflow.py#L1091)). For a lead type it walks `input-teammates`, appends each teammate to the tool list ([agent_activities.py:1450](../../../../server/services/temporal/agent_activities.py#L1450)), and creates or finds the execution team unless the run is a task review ([agent_activities.py:1462](../../../../server/services/temporal/agent_activities.py#L1462)). Each teammate becomes a tool entry named `delegate_to_*` with `llm_hidden` set ([agent_activities.py:1652](../../../../server/services/temporal/agent_activities.py#L1652)); the workflow drops hidden entries from what it sends the model ([agent_workflow.py:1322](../../../../server/services/temporal/agent_workflow.py#L1322)). The Task Manager tool is bound to every lead by the edge walker, connected or not ([edge_walker.py:323](../../../../server/services/plugin/edge_walker.py#L323)).
2. Model turn. `agent.execute_llm_step` returns the turn's tool calls. A lead may issue several `assign_task` calls in one response.
3. Preflight, concurrent. For every call whose tool is `taskManager` with `operation == "assign_task"`, the workflow starts the tool's own activity with `workflow.start_activity("node.taskManager.v<version>", ...)`, id `task-manager-preflight-<iteration>-<call>`, and then awaits all of them with `asyncio.gather(..., return_exceptions=True)` ([agent_workflow.py:2063](../../../../server/services/temporal/agent_workflow.py#L2063), [agent_workflow.py:2127](../../../../server/services/temporal/agent_workflow.py#L2127)). Options are the plugin's: 10 min start-to-close, 2 min heartbeat, 3 attempts.
4. Inside that activity ([task_manager/__init__.py:261](../../../../server/nodes/tool/task_manager/__init__.py#L261)): validate a managed assignment, resolve exactly one teammate by node id or delegate name ([task_manager/__init__.py:184](../../../../server/nodes/tool/task_manager/__init__.py#L184)), then `assign_durable_task`: the assignee must be a `teammate` member of this lead's team and not offline, every dependency must be a task of this team, and the row is created `queued`, or `blocked` when a dependency is not yet accepted ([agent_team.py:192](../../../../server/services/agent_team.py#L192)). The task id is `task_<12 hex>` minted in the activity. The result carries `delegation_request` ([task_manager/__init__.py:293](../../../../server/nodes/tool/task_manager/__init__.py#L293)). On this path `delegation` is null: the in-process bridge runs only when `ai_service` and `database` are in the tool config ([task_manager/__init__.py:274](../../../../server/nodes/tool/task_manager/__init__.py#L274)), and only in-process callers inject them (section 3.3).
5. In an execution-control generation (not on the Pi runtime) the preflight result is appended as that call's tool message here, before any child starts ([agent_workflow.py:2134](../../../../server/services/temporal/agent_workflow.py#L2134)).
6. If this lead is itself a delegated child it releases its own permit once ([agent_workflow.py:2146](../../../../server/services/temporal/agent_workflow.py#L2146)).
7. Bridge. For each result holding a `delegation_request` the workflow creates a coroutine task ([agent_workflow.py:2165](../../../../server/services/temporal/agent_workflow.py#L2165)) running `_run_task_manager_delegation` ([agent_workflow.py:1915](../../../../server/services/temporal/agent_workflow.py#L1915)). It checks that the delegate name resolves in `tool_index` to the same node id and to a type in `AGENT_WORKFLOW_TYPES`, checks depth, and requires a team id ([agent_workflow.py:1936](../../../../server/services/temporal/agent_workflow.py#L1936)). It schedules `agent.queue_delegation` (id `queue-task-manager-<iteration>-<call>`), builds `child_context`, and starts the runner:

    ```python
                async with self._execution_control.child_start():
                    runner_handle = await workflow.start_child_workflow(
                        "DelegatedTaskWorkflow",
                        args=[{"lifecycle": lifecycle, "child_context": child_context,
                               "child_workflow_id": child_id, "limit": max_concurrent_subagents}],
                        **delegated_task_options,
                    )
                    self._runtime_child_started(runner_handle)
                return {"status": "queued", "result": None, "runner_workflow_id": runner_id}
    ```

    The options are `id=<child id>-runner`, `parent_close_policy=ParentClosePolicy.ABANDON`, and the `EventWorkflowId` search attribute ([agent_workflow.py:2030](../../../../server/services/temporal/agent_workflow.py#L2030)). No task queue, timeout, retry policy or cancellation type is passed, so the runner uses the parent's queue, has no timeout, and takes the SDK defaults. The runner handle is never awaited. This is what "detached runner" means: a child the lead starts with ABANDON and does not wait for, which owns the permit, the claim, the teammate's workflow and the terminal bookkeeping.
8. Back in the per-call loop (model call order) the preflight result is the tool result ([agent_workflow.py:2519](../../../../server/services/temporal/agent_workflow.py#L2519)); the workflow awaits the bridge task, which finishes as soon as the runner has started, and adds `delegation_status`, `delegation_result`, `delegation_usage` ([agent_workflow.py:2557](../../../../server/services/temporal/agent_workflow.py#L2557)). `retry_task` and `reassign_task` are not preflighted: they run as ordinary tool activities and are bridged here inline ([agent_workflow.py:2570](../../../../server/services/temporal/agent_workflow.py#L2570)).
9. The lead goes on. Its prompt tells it not to wait (section 4.2). When it answers, the owning run schedules `agent.finalize_team`, which reports `active` while any task is unresolved and does not block ([agent_workflow.py:1538](../../../../server/services/temporal/agent_workflow.py#L1538), [agent_activities.py:2647](../../../../server/services/temporal/agent_activities.py#L2647)).
10. The runner, `DelegatedTaskWorkflow.run` ([agent_workflow.py:606](../../../../server/services/temporal/agent_workflow.py#L606)), in order: enrol with the generation controller (`register_root`); `agent.register_task_execution`; `agent.acquire_subagent_permit` (1 h start-to-close, 10 s heartbeat, unlimited attempts; the activity polls the database once a second) ([agent_workflow.py:641](../../../../server/services/temporal/agent_workflow.py#L641)); `agent.begin_delegation`, which claims the row `queued` to `running`, stamps `started_at`, marks the member `working` and writes an assignment message ([agent_activities.py:2133](../../../../server/services/temporal/agent_activities.py#L2133)); then

    ```python
                child_handle = await workflow.start_child_workflow(
                    agent_workflow_name(request["child_context"].get("node_type"), request["child_context"]), args=[request["child_context"]],
                    id=request["child_workflow_id"],
                )
    ```

    ([agent_workflow.py:667](../../../../server/services/temporal/agent_workflow.py#L667)), a second `agent.register_task_execution` with the child's run id, and `result = await child_handle` ([agent_workflow.py:682](../../../../server/services/temporal/agent_workflow.py#L682)). The child is `AgentWorkflow`, or `PiCodingAgentWorkflow` for a Coding Agent on the Pi harness. Only `id` is passed, so parent close policy is TERMINATE and cancellation type is WAIT_CANCELLATION_COMPLETED (defaults read from the installed temporalio 1.34.0 package, module `temporalio.workflow._workflow_ops`).
11. The teammate runs the normal agent loop with its own prepare, model steps and tools (section 5.2) and returns `{"success": True, "result": {...}}` ([agent_workflow.py:3341](../../../../server/services/temporal/agent_workflow.py#L3341)).
12. `agent.finish_delegation` ([agent_activities.py:2491](../../../../server/services/temporal/agent_activities.py#L2491)): on success the row goes `running` to `submitted` with the result summary and token usage ([database.py:3774](../../../../server/core/database.py#L3774)); on failure `fail_task` runs (section 7). It writes a team message and then publishes the task event ([agent_activities.py:2551](../../../../server/services/temporal/agent_activities.py#L2551)). The runner's `finally` releases the permit and leaves the controller ([agent_workflow.py:778](../../../../server/services/temporal/agent_workflow.py#L778)).
13. The event. One envelope type, `com.opencompany.agent.task.completed`, for success and failure ([envelope.py:463](../../../../server/services/events/envelope.py#L463)), delivered by Signal only to consumers of the same workflow id, with `broadcast=False`; a second identity-only frame goes to WebSocket clients on the key `task_completed` ([_events.py:123](../../../../server/nodes/agent/_events.py#L123)). The event id is stable across activity retries ([agent_activities.py:2565](../../../../server/services/temporal/agent_activities.py#L2565)).
14. Review run. A `taskTrigger` wired to the lead's `input-task` fires a new graph run, in which the lead runs as a new `AgentWorkflow` (the hiring recipe adds that trigger, filtered to the lead: [team_recipe.py:253](../../../../server/services/employees/team_recipe.py#L253)). Prepare detects the task event, takes the team id and execution id from it, does not create or own a team ([agent_activities.py:1442](../../../../server/services/temporal/agent_activities.py#L1442)), and prepends the review text with the full result ([agent_activities.py:1423](../../../../server/services/temporal/agent_activities.py#L1423), [edge_walker.py:753](../../../../server/services/plugin/edge_walker.py#L753)).
15. The lead reviews with `list_tasks` and `get_task`, then `accept_task` (`submitted` to `accepted`), `retry_task` or `reassign_task` (to `queued` as a new attempt, bridged to a new runner), `cancel_task`, or `finish_team` ([agent_team.py:246](../../../../server/services/agent_team.py#L246)). `accept_task` without a task id works only when exactly one task is submitted ([task_manager/__init__.py:315](../../../../server/nodes/tool/task_manager/__init__.py#L315)). `finish_team` succeeds only when every task is accepted or cancelled, and for a managed job only when at least one task exists ([agent_team.py:281](../../../../server/services/agent_team.py#L281)).
16. For a hired employee the lead run ends with `employee.job.deliver`, which sends nothing until every task of the job's team is `accepted` and the lead's response is non-empty ([agent_workflow.py:3323](../../../../server/services/temporal/agent_workflow.py#L3323), [jobs.py:343](../../../../server/services/employees/jobs.py#L343)).

### 3.2 Direct `delegate_to_*` on Temporal (attached child, awaited inline)

Reached by an agent whose delegate tools are visible: any non-lead agent with an agent on `input-tools` ([edge_walker.py:494](../../../../server/services/plugin/edge_walker.py#L494)). A recorded replay fixture covers it ([record_fixtures.py:598](../../../../server/tests/temporal/record_fixtures.py#L598)).

1. The workflow collects the turn's delegation calls: name starts with `delegate_to_`, target type in `AGENT_WORKFLOW_TYPES`, a non-empty `task` or `context`, depth below the limit ([agent_workflow.py:1598](../../../../server/services/temporal/agent_workflow.py#L1598)).
2. It starts the first `max_concurrent_subagents` of them before awaiting any ([agent_workflow.py:2175](../../../../server/services/temporal/agent_workflow.py#L2175)) through the closure `_start_delegation` ([agent_workflow.py:1736](../../../../server/services/temporal/agent_workflow.py#L1736)).
3. When the caller has a team id (it is a lead, or a delegated child that inherited one), each start first runs `agent.queue_delegation`, `agent.acquire_subagent_permit` and `agent.begin_delegation` with the task id `task-<root execution id>-<caller node id>-<iteration>-<call>` ([agent_workflow.py:1769](../../../../server/services/temporal/agent_workflow.py#L1769), [agent_workflow.py:1808](../../../../server/services/temporal/agent_workflow.py#L1808)). With no team id there is no row, no permit and no event.
4. The child starts with `workflow.start_child_workflow(agent_workflow_name(...), args=[child_context], id=<caller workflow id>-delegate-<node id>-<iteration>-<call>)` ([agent_workflow.py:1877](../../../../server/services/temporal/agent_workflow.py#L1877)). Defaults again: TERMINATE on parent close, no timeout.
5. In model call order the workflow runs `tool_result = await handle` ([agent_workflow.py:2423](../../../../server/services/temporal/agent_workflow.py#L2423)), releases the permit, writes `agent.finish_delegation` when there is a team, and builds the tool result:

    ```python
                            tool_result = {
                                "success": child_succeeded,
                                "status": "submitted" if child_succeeded else "failed",
                                "result": child_response,
                                "usage": child_summary.get("usage"),
                                **({"error": child_error} if child_error else {}),
                                **child_recovery,
                            }
    ```

6. As each awaited call returns, the next unstarted delegation is started ([agent_workflow.py:2507](../../../../server/services/temporal/agent_workflow.py#L2507), [agent_workflow.py:2853](../../../../server/services/temporal/agent_workflow.py#L2853)). The window therefore slides in call order, not completion order: a slow first call holds back a fourth even if the second and third are done.
7. A target type outside `AGENT_WORKFLOW_TYPES` (`rlm_agent`, `claude_code_agent`) runs as one blocking tool activity with the task mapped into its node data ([agent_workflow.py:2284](../../../../server/services/temporal/agent_workflow.py#L2284), [agent_workflow.py:2513](../../../../server/services/temporal/agent_workflow.py#L2513)).

### 3.3 In-process paths (no workflow for the child)

- `_execute_delegated_agent` spawns the child with `asyncio.create_task` and returns `{"status": "delegated", "task_id": ...}` at once ([tools.py:900](../../../../server/services/handlers/tools.py#L900), [tools.py:923](../../../../server/services/handlers/tools.py#L923)). Fan-out is bounded by an `asyncio.Semaphore` per root execution ([tools.py:485](../../../../server/services/handlers/tools.py#L485)); results live in process dictionaries and a `node_outputs` row ([tools.py:43](../../../../server/services/handlers/tools.py#L43), [tools.py:794](../../../../server/services/handlers/tools.py#L794)); the parent polls with the `check_delegated_tasks` tool, which the in-process agent methods inject when a delegate tool is bound ([ai.py:1133](../../../../server/services/ai.py#L1133)). `agent.prepare_payload` never binds that tool.
- It is reached only from callers that put `ai_service` and `database` in the tool config: the two in-process agent methods ([ai.py:1215](../../../../server/services/ai.py#L1215)), and the CLI-agent and RLM bridges only when the Pi harness scope is present; the CLI bridge does it only for a Coding Agent, the Task Manager and the check tool ([workflow_tools.py:355](../../../../server/services/cli_agent/workflow_tools.py#L355), [adapters.py:269](../../../../server/services/rlm/adapters.py#L269)). Without them it returns an error ([tools.py:411](../../../../server/services/handlers/tools.py#L411)).
- In-process Task Manager `assign_task` calls the same function with the pre-created task id ([task_manager/__init__.py:274](../../../../server/nodes/tool/task_manager/__init__.py#L274)); `retry_task` and `reassign_task` return a `delegation_request` that nothing in-process consumes ([agent_teams.md](../../../agent_teams.md), section "Known limitations").
- An opt-in blocking wait exists (`delegation_wait_seconds`, [tools.py:423](../../../../server/services/handlers/tools.py#L423)); a search of the server tree found no non-test code that sets it.
- When these run: Temporal off, the agent-workflow flag off, or an agent executed through its plugin method rather than `AgentWorkflow` ([_specialized.py:115](../../../../server/nodes/agent/_specialized.py#L115)). A managed employee team's plan requires Temporal and the agent workflow, and a job is not dispatched without them ([team_recipe.py:308](../../../../server/services/employees/team_recipe.py#L308), [team_runtime.py:155](../../../../server/services/employees/team_runtime.py#L155), [jobs.py:226](../../../../server/services/employees/jobs.py#L226)).

### 3.4 Pi inline delegation

For a `coding_agent` on the Pi harness called from an in-process caller, `_execute_delegated_agent` hands off to `start_inline_pi_delegation` ([tools.py:388](../../../../server/services/handlers/tools.py#L388)). It checks the edge, depth and Temporal, records an immutable admission row and a polling receipt, and starts a top-level workflow with the client: `PiInlineDelegationWorkflow`, id `coding-inline-<digest>`, the main task queue, `REJECT_DUPLICATE` ([pi_inline_delegation.py:222](../../../../server/services/temporal/pi_inline_delegation.py#L222)). That workflow starts `DelegatedTaskWorkflow` as a child with `ParentClosePolicy.REQUEST_CANCEL` and `WAIT_CANCELLATION_COMPLETED`, awaits it, and projects the final task state into the polling receipt ([pi_inline_delegation.py:368](../../../../server/services/temporal/pi_inline_delegation.py#L368)). The caller gets `{"status": "delegated"}` and polls.

### 3.5 Hired employees: the job wrapper

The Talk agent calls `task_manager(operation="submit_job", mission=...)`, which creates an `EmployeeJob` and starts `EmployeeJobWorkflow` with the client (id is the job id, main task queue, `REJECT_DUPLICATE`, tagged `EventWorkflowId`) ([task_manager/__init__.py:210](../../../../server/nodes/tool/task_manager/__init__.py#L210), [jobs.py:264](../../../../server/services/employees/jobs.py#L264)). That workflow runs the lead as a child with id `<job id>:lead`, then calls `employee.job.deliver` ([employee_job_workflow.py:118](../../../../server/services/temporal/employee_job_workflow.py#L118)). From there the lead follows 3.1. A lead reached directly by an app trigger gets its job admitted in prepare instead ([team_runtime.py:280](../../../../server/services/employees/team_runtime.py#L280)).

### 3.6 Which paths are live

| Path | State |
|---|---|
| Task Manager with `DelegatedTaskWorkflow` (3.1) | Live. The only delegation surface a lead's model has on Temporal, and the one managed employee teams use. |
| Direct `delegate_to_*` as an attached child (3.2) | Live for non-lead agents on Temporal. For leads the entries exist as hidden dispatch identities. |
| In-process fire-and-forget with `check_delegated_tasks` (3.3) | Live only where an agent runs through its plugin method, or from a bridge that injects the services. INFERRED: not reachable from `AgentWorkflow`, whose tool calls are activities that never carry those services. |
| `delegation_wait_seconds` | Present in code; no non-test setter found. |
| Pi inline delegation (3.4) | Live for a Coding Agent on the Pi harness called from an in-process caller. |
| WebSocket commands `create_team`, `add_team_task`, `claim_team_task`, `complete_team_task`, `send_team_message`, `dissolve_team` | Registered ([agent_teams/handlers.py:256](../../../../server/services/agent_teams/handlers.py#L256)); the panel code read here does not call them. |

## 4. What the model sees

### 4.1 The Task Manager tool

Name `task_manager`. The schema is locked to the plugin's `ToolInput`, so a stored custom schema cannot replace it ([task_manager/__init__.py:133](../../../../server/nodes/tool/task_manager/__init__.py#L133), [ai.py:2503](../../../../server/services/ai.py#L2503)). Description, verbatim:

```text
Manage the current lead execution's durable team tasks. Assign only connected teammates, inspect submitted work, then accept, retry, reassign, or cancel it. assign_task requires title, mission, and assignee_node_id or delegate_name. Example: {"operation":"assign_task","title":"Research","mission":"Find cited evidence","assignee_node_id":"<connected teammate node ID>"}. Use list_tasks with include_history=true to consult prior executions. An employee's conversational contact uses submit_job with mission to admit substantive work for its configured team and receive a prompt acknowledgement; reviewed delivery follows separately.
```

Operations ([task_manager/__init__.py:11](../../../../server/nodes/tool/task_manager/__init__.py#L11)): `submit_job`, `assign_task`, `list_tasks`, `get_task`, `modify_task`, `cancel_task`, `retry_task`, `reassign_task`, `accept_task`, `finish_team`, `mark_done` (alias of accept), `inspect_task_trace`.

Arguments ([task_manager/__init__.py:19](../../../../server/nodes/tool/task_manager/__init__.py#L19), [task_manager/__init__.py:46](../../../../server/nodes/tool/task_manager/__init__.py#L46)); unknown fields are rejected (`extra="forbid"`):

| Field | Type and bounds | Text the model gets |
|---|---|---|
| `operation` | one of the 12 names, required | "Task operation; always supply it explicitly. Use get_task to review status, revision and allowed_actions before mutations. modify_task: blocked/queued only; cancel_task: blocked/queued/running; retry_task/reassign_task: failed/submitted/cancelled; accept_task/mark_done: submitted only. A running task cannot be modified." |
| `task_id` | string | "Required for get, inspect, modify, cancel, retry and reassign. Accept may infer one submitted task." |
| `title` | string, at most 500 | "assign_task: required nonblank task title. modify_task: replacement title." |
| `mission` | string, at most 10,000 | "submit_job/assign_task: required nonblank mission and expected result. modify_task: replacement mission." |
| `context` | object | none |
| `acceptance_criteria` | object | none |
| `depends_on` | list of task ids | none |
| `assignee_node_id` | string | "assign_task/reassign_task: connected teammate node ID; supply this or delegate_name." |
| `delegate_name` | string | "assign_task/reassign_task: exact connected delegate name, alternative to assignee_node_id." |
| `expected_revision` | integer, 0 or more | "Task revision for optimistic concurrency; the runtime reads the current revision when omitted." |
| `revision` | integer | deprecated spelling of `expected_revision` |
| `reason` | string, at most 2,000 | none |
| `status_filter`, `include_history` | string, boolean | none |
| `attempt`, `cursor`, `limit` (1-100, default 50), `detail` (`summary`, `failures`, `timeline`, `search`), `query` (at most 200), `search_mode`, `case_sensitive`, `context_lines` (0-5), `scan_limit` (1-500), `categories` | trace inspection | only `query` has text |

A validator requires, per operation: `mission` for `submit_job`; `title` and `mission` plus an assignee for `assign_task`; `task_id` for get, inspect, modify, cancel, retry, reassign; at least one editable field for `modify_task` ([task_manager/__init__.py:89](../../../../server/nodes/tool/task_manager/__init__.py#L89)). For a managed employee job the server also rejects an assignment whose `context` is not an object or whose `acceptance_criteria` is empty, although the schema marks both optional ([team_runtime.py:206](../../../../server/services/employees/team_runtime.py#L206)).

### 4.2 How the lead learns its team

Prepare appends this to the system message whenever the tool list contains a delegate ([agent_activities.py:1670](../../../../server/services/temporal/agent_activities.py#L1670)):

```text
You lead a team of independent agents. All delegation MUST use the task_manager tool with operation='assign_task'. Provide title, a bounded mission, relevant context, acceptance criteria, and exactly one connected assignee_node_id. Never call delegate_to_* directly. You may issue multiple assign_task tool calls in one response; they are durably queued and run in parallel subject to the team limit. Review submitted tasks with list_tasks and get_task, then accept, retry, modify, reassign, or cancel before finishing.
When assign_task returns status='queued', do not poll or wait in this run. Tell the user the task was delegated and return immediately. Completion starts a separate taskTrigger review with the owning task context.
For mutations, copy task.id into task_id and task.revision into expected_revision. After a single child submits work, accept_task may omit them only when that submitted task is unambiguous.
Connected teammates (assignee_node_id: label/type):
- <node id>: <label> (<node type>) - tools: <tool label> (<blurb>), ...; skills: <skill> (<description>), ...
```

The roster line and its capability sentence come from the edge walker; each tool or skill blurb is cut to 160 characters, and personality and runtime skills are left out ([edge_walker.py:584](../../../../server/services/plugin/edge_walker.py#L584), [edge_walker.py:666](../../../../server/services/plugin/edge_walker.py#L666), [edge_walker.py:694](../../../../server/services/plugin/edge_walker.py#L694)). Delegate names are `delegate_to_<node type>` for specialized agents and `delegate_to_<label slug>` for a custom `aiAgent`, with a hash suffix when the label is empty or repeated ([edge_walker.py:67](../../../../server/services/plugin/edge_walker.py#L67)). After a canvas change adds a teammate mid-run the workflow adds a system message "Updated connected teammates (assignee_node_id: label/type)" ([agent_workflow.py:2742](../../../../server/services/temporal/agent_workflow.py#L2742)).

The block is not limited to lead types. A non-lead agent with a visible delegate tool receives the same text, including "Never call delegate_to_* directly", while it has no Task Manager and its delegate tools stay callable.

A managed lead that answers without assigning anything receives one corrective user message ([agent_workflow.py:360](../../../../server/services/temporal/agent_workflow.py#L360)): "The employee job has no durable task assignments. Before finishing, use Task Manager to assign work to a connected teammate with a clear mission, context, and acceptance criteria. A durable queued assignment is sufficient; do not wait for it to finish or bypass teammate review and owner approval. This is the one corrective attempt and it must use the remaining iteration budget."

Hired teams add recipe text. Lead ([team_recipe.py:127](../../../../server/services/employees/team_recipe.py#L127)): "You coordinate and review this employee's specialist team. Use the intrinsic Task Manager to assign durable tasks to your connected teammates. Each assignment must state its mission, context, dependencies and acceptance criteria. Review submitted results; accept only results meeting the criteria, otherwise request revision or reassign. Reuse the existing task identity for retries. Finish the team only after all required work is accepted. Then provide one reviewed result. Do not present an assignment acknowledgement as completed work. Do not independently publish through apps." Talk agent ([team_recipe.py:121](../../../../server/services/employees/team_recipe.py#L121)): use `submit_job` for substantive work and say the reviewed result arrives later. Specialist ([team_recipe.py:148](../../../../server/services/employees/team_recipe.py#L148)): work only on the assigned mission, return evidence and limits to the lead, never reply to the owner. The recipe picks one to three specialists from five responsibilities by keyword ([team_recipe.py:32](../../../../server/services/employees/team_recipe.py#L32)); [prompt.py](../../../../server/services/employees/prompt.py#L251) writes the base instructions and holds no team text.

### 4.3 The review turn

The prompt of a review run starts with ([edge_walker.py:763](../../../../server/services/plugin/edge_walker.py#L763)):

```text
A delegated task has completed:
- Agent: <agent name>
- Task ID: <task id>
- Status: Completed Successfully
- Result: <the child's whole answer>

This taskTrigger run is the lead's completion review. Use Task Manager list_tasks and get_task to read the durable task, verify the submitted result against its mission and acceptance criteria, then produce the requested user-facing output. Do not create a duplicate assignment.
```

### 4.4 The direct delegate tool

Arguments are `task` (required; "The mission directive for '<label>' ...") and `context` (optional) ([ai.py:2656](../../../../server/services/ai.py#L2656)). The description is the class default "ONE-SHOT delegation to <name>. Call ONCE per task, returns task_id immediately. Agent works in background - do NOT re-call." ([base.py:486](../../../../server/services/plugin/base.py#L486)), or, when the child has tools, "Delegate tasks to '<label>' agent. This agent has the following capabilities: ... Call ONCE per task, returns task_id. Agent works in background." ([ai.py:2546](../../../../server/services/ai.py#L2546)). That wording describes the in-process path; on Temporal the call blocks and returns the answer (3.2).

### 4.5 What the model reads back

- `assign_task`: the Task Manager result, including `task` (id, status, revision, `allowed_actions`, attempts) and `delegation_request`. Generations without execution control also get `delegation_status: "queued"`; with it, the message was recorded before those keys were added (3.1 step 5).
- `get_task` and `list_tasks`: the task dict of section 5.4, with `result`, `usage`, timestamps and attempts.
- `submit_job` (the Talk agent): `job_id`, `state` and the acknowledgement "I've given this to the team. I'll share their checked result here." ([task_manager/__init__.py:225](../../../../server/nodes/tool/task_manager/__init__.py#L225)).
- Direct delegation: the object in 3.2 step 5.
- Failures: a rejected mutation is a `NodeUserError` with a hint to call `get_task` ([task_manager/__init__.py:351](../../../../server/nodes/tool/task_manager/__init__.py#L351)); a bridge refusal or a failed child reaches the model as `{"error": "<Type>: <message>"}` ([agent_workflow.py:2815](../../../../server/services/temporal/agent_workflow.py#L2815)).

## 5. Inputs, outputs and state

### 5.1 `child_context` (the sub-agent's workflow input)

Built in two places with slightly different keys ([agent_workflow.py:1748](../../../../server/services/temporal/agent_workflow.py#L1748), [agent_workflow.py:2010](../../../../server/services/temporal/agent_workflow.py#L2010)).

| Key | Value |
|---|---|
| inherited scope | every key of `_INHERITED_SCOPE_KEYS` present on the parent, minus `_browser_task_id`: `graphVersion`, `generation`, `data_scope_id`, `context_execution_id`, `context_session_id`, `user_id`, `workspace_dir`, `temporal_worker_pool_enabled`, `run_scope`, `employee_job_id`, `team_task_id`, `team_execution_id`, `parameter_snapshot`, the execution-control keys, `native_workspace_version`, the Pi keys, `workflow_slug`, the browser keys, `approval_wait_version` ([agent_workflow.py:310](../../../../server/services/temporal/agent_workflow.py#L310)) |
| control scope | the parent's current control state, without its chain identity ([agent_workflow.py:435](../../../../server/services/temporal/agent_workflow.py#L435)) |
| `node_id`, `node_type` | the teammate node |
| `node_data` | the teammate's saved parameters with `system_message` set to the mission and `prompt` set to the context text, or the mission when there is none |
| `invocation` | `{"task": mission, "context": context text}`: the per-call input that wins over saved configuration |
| `inputs` | `{}` |
| `nodes`, `edges` | the parent's whole run graph, so the child's edge walk finds its own tools, skills and Context |
| `workflow_id`, `session_id` | the parent's |
| `execution_id`, `root_execution_id` | the parent's execution id and the root execution id |
| `parent_node_id` | the delegating agent's node id |
| `delegation_depth` | parent depth plus 1 |
| `team_id`, `team_task_id`, `trace_id` | team, task row id, the model's tool call id |
| `team_permit_id` | added by the runner after it acquires a permit ([agent_workflow.py:665](../../../../server/services/temporal/agent_workflow.py#L665)) |
| direct path only | `provider` (the parent's; not used to pick the child's model) and the call metadata `invoking_agent_node_id`, `agent_iteration`, `tool_call_index`, `tool_call_id` |

For a managed job the context text is the JSON of the model's `context` object with `acceptance_criteria` and `depends_on` merged in; for any other team only `context` is sent and the acceptance criteria stay on the task row ([agent_workflow.py:2007](../../../../server/services/temporal/agent_workflow.py#L2007)). The task `title` is never sent to the child.

### 5.2 What the sub-agent ends up with

- System message: the mission (`invocation.task`) replaces the teammate's saved system message; the skill prompt is appended after it. A Browser Agent keeps its saved role and gets mission and context as the prompt ([agent_activities.py:1301](../../../../server/services/temporal/agent_activities.py#L1301)).
- User prompt: the context text, or the mission.
- Model: the teammate's own saved `provider` and `model`, or that provider's default; no per-call override ([agent_activities.py:1319](../../../../server/services/temporal/agent_activities.py#L1319)).
- Tools and skills: whatever is wired to the teammate's own node. A teammate that is itself a lead type also gets the Task Manager and its own team. In an employee job it also gets the read-only job source tool ([edge_walker.py:354](../../../../server/services/plugin/edge_walker.py#L354)).
- Limits: the same `max_iterations` rule as any agent; `max_concurrent_subagents` and `max_delegation_depth` from settings; transcript-pressure settings of its own.
- Conversation: with a Context node on the teammate and a generation above 0, prepare loads the row keyed `(workflow_id, generation, agent_node_id)` and the run continues it; the key holds no task id ([agent_activities.py:1547](../../../../server/services/temporal/agent_activities.py#L1547)). The new system message is used, stored non-system messages follow, then the new prompt ([agent_workflow.py:1211](../../../../server/services/temporal/agent_workflow.py#L1211)). Saves are whole-list, last write wins ([agent_activities.py:713](../../../../server/services/temporal/agent_activities.py#L713)). The hiring recipe gives every member its own Context ([team_recipe.py:160](../../../../server/services/employees/team_recipe.py#L160)), so a hired specialist carries its transcript from task to task until Reset. Without a Context node, or on a manual run, each run starts from system message and prompt only.
- Output slot: the child's final result is stored under its node id and session id, one slot per node ([agent_activities.py:1077](../../../../server/services/temporal/agent_activities.py#L1077)).

### 5.3 Dict shapes

- `delegation_request` (Task Manager to workflow): `team_task_id`, `assignee_node_id`, `attempt_number`, `delegate_name`, `task`, `context`, `acceptance_criteria`, `depends_on` ([task_manager/__init__.py:296](../../../../server/nodes/tool/task_manager/__init__.py#L296)).
- `lifecycle` (workflow to the lifecycle activities): `team_id`, `team_task_id`, `parent_agent_node_id`, `child_agent_node_id`, `child_agent_name`, `workflow_id`, `parent_agent_workflow_id`, `parent_workflow_id`, `parent_run_id`, `task`, `employee_job_id`, `context`, `acceptance_criteria`, `depends_on`, `root_execution_id`, `delegation_depth`, `trace_id`, `assignment_event_id`, optional `attempt_number` ([agent_workflow.py:1967](../../../../server/services/temporal/agent_workflow.py#L1967)).
- Runner input: `{"lifecycle", "child_context", "child_workflow_id", "limit"}`.
- Agent result: `{"success": True, "result": {"response", "thinking", "model", "provider", "usage", "team_id", "execution_id", "root_execution_id", "artifacts"?}}` ([agent_workflow.py:3283](../../../../server/services/temporal/agent_workflow.py#L3283)); failures return `success: False` with `error` and `error_type`.
- Stored summary: `_normalise_delegated_result` keeps `response`, `usage`, `model`, `provider` ([agent_workflow.py:3507](../../../../server/services/temporal/agent_workflow.py#L3507)); this is `TeamTask.result`.
- Task event data: `task_id`, `status` (`completed` or `error`), `agent_name`, `agent_node_id`, `parent_node_id`, `workflow_id`, `result` or `error`, plus `team_id`, `attempt_number`, `execution_id`, `root_execution_id`, `trace_id`, `parent_agent_workflow_id` ([agent_activities.py:2557](../../../../server/services/temporal/agent_activities.py#L2557)).

### 5.4 Tables

| Table | Key fields | Written by |
|---|---|---|
| `agent_teams` ([models/database.py:502](../../../../server/models/database.py#L502)) | `id` (`team_` + 16 hex of sha256 of execution id and lead node id), `workflow_id`, `team_lead_node_id`, `execution_id`, `root_execution_id`, `status` (active, completed, failed, dissolved), `config` | prepare; `finalize_team`; `finish_team`; Reset |
| `team_members` ([models/database.py:619](../../../../server/models/database.py#L619)) | `team_id`, `agent_node_id`, `agent_type`, `agent_label`, `role` (team_lead, teammate), `status` (idle, working, offline), `capabilities` | team creation; claim and completion |
| `team_tasks` ([models/database.py:640](../../../../server/models/database.py#L640)) | `id`, `team_id`, `title`, `mission`, `context`, `acceptance_criteria`, `depends_on`, `status`, `assigned_to`, `created_by`, `queue_sequence`, `revision`, `current_attempt`, `retry_count`, `max_retries` (3), runner, child and parent workflow and run ids, `trace_id`, `cancellation_requested`, `cancellation_reason`, `usage`, `result`, `error`, `progress`, `created_at`, `started_at`, `completed_at` | Task Manager; lifecycle activities |
| `team_task_attempts` ([models/database.py:690](../../../../server/models/database.py#L690)) | `id` (`<task id>:attempt:<n>`), `attempt_number`, `assignee_node_id`, `status`, workflow and run ids, `result`, `error`, `usage`, timestamps | `register_task_execution`; retry and reassign; cancel |
| `agent_messages` ([models/database.py:715](../../../../server/models/database.py#L715)) | `event_id` (unique; the idempotency key), `team_id`, `from_agent`, `to_agent`, `message_type`, `content`, `extra_data` | queue, begin, finish, cancel, trace inspection |
| `subagent_concurrency_counters`, `subagent_concurrency_permits` ([models/database.py:737](../../../../server/models/database.py#L737)) | counter per `root_execution_id`; permit rows `permit_id`, `status` (active, released) | permit activities |
| `employee_jobs` ([employees.py:135](../../../../server/models/employees.py#L135)) | `id`, `workflow_id`, `origin_execution_id`, `lead_node_id`, `team_id`, `state`, `mission`, `source` (the admitted graph and parameters), `delivery`, `result` | `submit_job`, prepare, delivery |

Task states: `blocked`, `queued`, `running`, `submitted`, `accepted`, `failed`, `cancelled`. Allowed actions per state are computed in one place ([database.py:3533](../../../../server/core/database.py#L3533)). A team is scoped to (workflow, lead node, execution id). In a deployed generation every trigger-spawned run carries the generation's `execution_id` and `root_execution_id` ([trigger_listener_workflow.py:460](../../../../server/services/temporal/trigger_listener_workflow.py#L460), [manager.py:1122](../../../../server/services/deployment/manager.py#L1122)), so a lead without a job has one team per generation, and the permit counter is shared by all runs of the generation (INFERRED from those two lines and the team key). An employee job has its own team, and a dispatched job its own root ([agent_activities.py:1473](../../../../server/services/temporal/agent_activities.py#L1473), [jobs.py:256](../../../../server/services/employees/jobs.py#L256)).

### 5.5 Accounting and what people see

- Timestamps: `created_at` at assignment, `started_at` at claim, `completed_at` at submit, terminal failure, cancel, and again at accept (accept overwrites it) ([database.py:3737](../../../../server/core/database.py#L3737), [agent_team.py:249](../../../../server/services/agent_team.py#L249)). Elapsed time is not stored; the panel computes `completed_at` minus `started_at` ([model.ts:89](../../../../client/src/components/parameterPanel/taskManager/model.ts#L89)).
- Tokens: the child's cumulative usage rides its result and is written to the task and attempt on success only ([database.py:3777](../../../../server/core/database.py#L3777)); the failure write stores no usage. The lead's own `usage` total does not include its children ([agent_workflow.py:1487](../../../../server/services/temporal/agent_workflow.py#L1487)).
- Broadcasts: every team change goes to all sockets as `team_event` with an `event_type` such as `team.task.queued`, `task_claimed`, `team.task.submitted`, `task_failed`, `team.task.cancelled`, `team_message` ([status_broadcaster.py:1269](../../../../server/services/status_broadcaster.py#L1269)). `team.task.queued` carries the whole task and `team.task.submitted` carries the result ([agent_team.py:222](../../../../server/services/agent_team.py#L222), [agent_team.py:365](../../../../server/services/agent_team.py#L365)).
- Canvas: a child emits its own progress and, for every phase, a second `agent.broadcast_progress` activity that marks the parent node `delegating` ([agent_workflow.py:3447](../../../../server/services/temporal/agent_workflow.py#L3447)). That is two progress activities per phase in the child's history.
- Editor: the Task Manager panel shows Task, Assignee, Status, Started, Completed, Elapsed, Attempt, Tokens, refreshes on team events and every 15 s ([TaskManagerPanel.tsx:60](../../../../client/src/components/parameterPanel/TaskManagerPanel.tsx#L60), [TaskManagerPanel.tsx:133](../../../../client/src/components/parameterPanel/TaskManagerPanel.tsx#L133)), and its buttons call `manage_team_task` and `finish_team` ([agent_teams/handlers.py:200](../../../../server/services/agent_teams/handlers.py#L200)). `inspect_task_trace` and the panel's trace tab read the attempt's Temporal history, sanitized ([team_task_trace.py:138](../../../../server/services/team_task_trace.py#L138)).
- Home: the employee summary lists up to 20 steps, one per task of the active job's team (or of the newest active team when there is no job), with title, member label, status and times, never missions or results ([work_progress.py:164](../../../../server/services/employees/work_progress.py#L164)).

## 6. Determinism, versioning and durability

- Both workflows are `sandboxed=False` ([agent_workflow.py:552](../../../../server/services/temporal/agent_workflow.py#L552), [agent_workflow.py:808](../../../../server/services/temporal/agent_workflow.py#L808)). Database work, id minting for Task Manager tasks, and broadcasts all happen in activities.
- Ids are derived from `workflow.info().workflow_id`, the iteration and the call index: tool activities `tool-<node>-<iteration>-<call>`, direct child `<caller workflow id>-delegate-<node id>-<iteration>-<call>`, Task Manager child the same plus `-<task id>`, runner that plus `-runner` ([agent_workflow.py:285](../../../../server/services/temporal/agent_workflow.py#L285), [agent_workflow.py:2025](../../../../server/services/temporal/agent_workflow.py#L2025)). Lifecycle activity ids follow the same pattern (`queue-delegation-`, `acquire-permit-`, `begin-delegation-`, `register-delegation-`, `finish-delegation-`, `release-permit-`, `cancel-delegation-`, `yield-own-permit-`, `reacquire-own-permit-`). A golden file records these id expressions and the patch ids ([workflow_identities.json](../../../../server/tests/temporal/fixtures/workflow_identities.json)) and [test_workflow_identity_golden.py](../../../../server/tests/temporal/test_workflow_identity_golden.py) checks it.
- Recorded histories for `agent_direct_delegation`, `agent_task_manager`, `employee_job`, `pi_inline_delegation`, `task_runner_cancel` and `task_runner_paused_cancel` are replayed by [test_replay_corpus.py](../../../../server/tests/temporal/test_replay_corpus.py); the scenarios are defined in [record_fixtures.py:598](../../../../server/tests/temporal/record_fixtures.py#L598). A change to which commands a lead or runner schedules has to replay against them.
- Patch markers in this area ([agent_workflow.py:961](../../../../server/services/temporal/agent_workflow.py#L961) and the lines named):

| Patch id | Line | What it gates |
|---|---|---|
| `employee-task-manager-runtime-v2` | 961, 3323 | For a recipe-v2 employee lead: a job failure record on any failed exit, and the delivery activity at the end of the lead run |
| `employee-no-delegation-repair-v1` | 1544, 3264 | A managed lead that answers without assigning anything gets one corrective user message, then fails with `EmployeeLeadNoAssignments` |
| `employee-task-manager-tool-scope-v2` | 2099, 2378 | Merges the trusted scope keys and `outputs` into the Task Manager preflight payload and every tool payload |
| `agent-node-binding-refresh-v2` | 1152 | Dedupes node bindings, refreshes the run graph after a canvas change, and adds the updated-teammates message |
| `agent-tool-binding-key-v1` | 1155 | The key used for that dedupe |
| `agent-user-action-error-v1` | 2583 | A tool or child error marked `requires_user_action` ends the run after the turn |

- There is no patch for the delegation window. The word "window" appears once in the Temporal package's delegation code, in the comment above the start window ([agent_workflow.py:1586](../../../../server/services/temporal/agent_workflow.py#L1586)). Its size is `max_concurrent_subagents` from the prepared payload, and the point at which the next child starts depends on whether the generation has execution control, a flag frozen at Start, not a patch: inside the result handling without it ([agent_workflow.py:2508](../../../../server/services/temporal/agent_workflow.py#L2508)), after the call's action has ended with it ([agent_workflow.py:2853](../../../../server/services/temporal/agent_workflow.py#L2853)).
- Idempotency of the lifecycle: each task attempt has a number; activities refuse a superseded attempt ([agent_activities.py:2126](../../../../server/services/temporal/agent_activities.py#L2126)); messages dedupe on `event_id`; a version 2 permit lease is the hash of workflow id, run id, permit id, activity id and attempt, and a retry first releases this run's earlier leases ([agent_activities.py:2337](../../../../server/services/temporal/agent_activities.py#L2337), [agent_activities.py:2392](../../../../server/services/temporal/agent_activities.py#L2392)).

### Lifecycle of running sub-agents

| Event | What happens | Evidence |
|---|---|---|
| Lead finishes or fails | Detached runners keep running (ABANDON). Attached children were all awaited inside the turn that started them. | [agent_workflow.py:2032](../../../../server/services/temporal/agent_workflow.py#L2032), [agent_workflow.py:2423](../../../../server/services/temporal/agent_workflow.py#L2423) |
| Lead continues as new | Only at a turn boundary and only when no child handle and no bridge task is live. Both maps are emptied as results are consumed, so runners already started do not block it. The resume marker carries iteration, the correction count, execution id, transcript, usage, and for durable runtimes the prepared payload and `tools` (the teammate catalog). | [agent_workflow.py:3182](../../../../server/services/temporal/agent_workflow.py#L3182), [agent_workflow.py:3194](../../../../server/services/temporal/agent_workflow.py#L3194) |
| Stop (deployment pause) | The control plane walks the controller, its registered roots and their pending children, sends `set_control_state`, then waits for each checkpoint. A runner registers as a root, so it is reached after its lead is gone. Admitted activities drain; new actions and child starts wait; a permit wait and a wait on a child are not counted, so nothing deadlocks. No task is cancelled. | [deployment/execution_control.py:99](../../../../server/services/deployment/execution_control.py#L99), [agent_workflow.py:609](../../../../server/services/temporal/agent_workflow.py#L609), [temporal/execution_control.py:114](../../../../server/services/temporal/execution_control.py#L114) |
| Chat Stop | A child inherits `run_scope`, so its payload carries the chat run id and its model step returns what it has as a final answer once that run is stopping. It never streams to the chat, because it has a `parent_node_id`. | [agent_workflow.py:321](../../../../server/services/temporal/agent_workflow.py#L321), [stream.py:73](../../../../server/services/chat/stream.py#L73), [agent_activities.py:448](../../../../server/services/temporal/agent_activities.py#L448) |
| Apply with "stop work" | Sends a Temporal cancel to every running workflow tagged with the workflow id except controllers and listeners, then cancels open task rows and dissolves active teams. A cancelled runner records `agent.cancel_delegation`, releases its permit and re-raises. | [safe_apply.py:189](../../../../server/services/employees/safe_apply.py#L189), [agent_workflow.py:697](../../../../server/services/temporal/agent_workflow.py#L697) |
| Reset | Terminates every running workflow whose root is the controller or that carries the workflow's `EventWorkflowId`, and their descendants; then cancels open task rows, dissolves active teams, cancels open jobs. The runner's search attribute exists for this sweep. | [deployment/handlers.py:888](../../../../server/services/deployment/handlers.py#L888), [deployment/handlers.py:2682](../../../../server/services/deployment/handlers.py#L2682), [safe_apply.py:31](../../../../server/services/employees/safe_apply.py#L31) |
| `cancel_task` by the lead or the panel | A database transition to `cancelled` only. No code reads `cancellation_requested` to cancel the runner or the child. A task cancelled while still queued is refused at claim, so its child never starts; one cancelled while running keeps running. | [agent_team.py:248](../../../../server/services/agent_team.py#L248), [agent_activities.py:2188](../../../../server/services/temporal/agent_activities.py#L2188) |
| Lead cancelled with attached children | The cleanup sweep records a cancellation for each direct delegation it owns and releases their permits. | [agent_workflow.py:1669](../../../../server/services/temporal/agent_workflow.py#L1669) |
| Child fails | Direct: the error becomes the tool result and the lead continues. Runner: `agent.finish_delegation` with `success: False`; when the child workflow itself failed rather than returning a failure, the runner then fails too. | [agent_workflow.py:2761](../../../../server/services/temporal/agent_workflow.py#L2761), [agent_workflow.py:764](../../../../server/services/temporal/agent_workflow.py#L764) |
| Worker restart | Workflows resume from history. The in-process path loses its asyncio tasks and in-memory results. | [tools.py:43](../../../../server/services/handlers/tools.py#L43) |

Nesting: a root is depth 0 and a child gets the parent's depth plus 1; a call is refused when the caller's depth is at the limit, which the workflow clamps to 2 ([agent_workflow.py:1591](../../../../server/services/temporal/agent_workflow.py#L1591), [agent_workflow.py:2273](../../../../server/services/temporal/agent_workflow.py#L2273)). So a teammate may delegate once more and its children may not. The validator rejects a team graph deeper than two layers, cycles, duplicate teammate types other than `aiAgent`, and duplicate delegate names ([workflow_validator.py:238](../../../../server/services/workflow_validator.py#L238), [workflow_validator.py:264](../../../../server/services/workflow_validator.py#L264)). A child that delegates gives up its own permit first and asks for it back after the turn ([agent_workflow.py:1723](../../../../server/services/temporal/agent_workflow.py#L1723), [agent_workflow.py:2859](../../../../server/services/temporal/agent_workflow.py#L2859)).

## 7. Limits, budgets, timeouts and retries

| Item | Value | Where |
|---|---|---|
| Concurrent sub-agents | 3 by default; env `MAX_CONCURRENT_SUBAGENTS` (1-50); a positive `UserSettings.max_concurrent_subagents` wins; read in prepare; the workflow falls back to 3 | [config.py:525](../../../../server/core/config.py#L525), [agent_activities.py:1750](../../../../server/services/temporal/agent_activities.py#L1750), [agent_workflow.py:1595](../../../../server/services/temporal/agent_workflow.py#L1595) |
| What the 3 bounds | Active permits per `root_execution_id`, the root agent excluded; also the size of the direct start window per turn. A direct delegation with no team takes no permit. | [database.py:4260](../../../../server/core/database.py#L4260), [agent_workflow.py:2175](../../../../server/services/temporal/agent_workflow.py#L2175) |
| Delegation depth | 2; env `MAX_DELEGATION_DEPTH` (1-2); user setting clamped to 2; workflow clamps to 2 | [config.py:526](../../../../server/core/config.py#L526), [agent_activities.py:1768](../../../../server/services/temporal/agent_activities.py#L1768) |
| Count of delegations | No limit found. A lead is bounded only by its own model steps: `max_iterations`, node parameter, then user setting, then env `AGENT_RECURSION_LIMIT` (200). No cap on tool calls per turn was found in the code read for this file. | [agent_activities.py:1774](../../../../server/services/temporal/agent_activities.py#L1774) |
| Permit wait | 1 h start-to-close per attempt, 10 s heartbeat, unlimited attempts, 1 s poll | [agent_workflow.py:644](../../../../server/services/temporal/agent_workflow.py#L644), [_retry_policies.py:143](../../../../server/services/temporal/_retry_policies.py#L143), [agent_activities.py:2438](../../../../server/services/temporal/agent_activities.py#L2438) |
| Stale permit reclaim | An active permit older than 3 h is released by the next acquire on the same root | [database.py:4203](../../../../server/core/database.py#L4203) |
| Child and runner workflows | No execution or run timeout, no retry policy | [agent_workflow.py:201](../../../../server/services/temporal/agent_workflow.py#L201) |
| Lifecycle activities | 30 s start-to-close; 3 attempts, 1 s to 30 s | [agent_workflow.py:97](../../../../server/services/temporal/agent_workflow.py#L97), [_retry_policies.py:66](../../../../server/services/temporal/_retry_policies.py#L66) |
| Cleanup activities (cancel, release on unwind) | Unlimited attempts, 1 s to 30 s | [_retry_policies.py:117](../../../../server/services/temporal/_retry_policies.py#L117) |
| Task Manager activity | 10 min start-to-close, 2 min heartbeat, 3 attempts; `NodeUserError` not retried | [base.py:369](../../../../server/services/plugin/base.py#L369) |
| Task retries | `max_retries` 3. A failure while `retry_count + 1 < 3` sets the row back to `queued` and clears the assignee; otherwise `failed`. | [database.py:3832](../../../../server/core/database.py#L3832) |
| Mission, title, reason | 10,000, 500 and 2,000 characters | [task_manager/__init__.py:22](../../../../server/nodes/tool/task_manager/__init__.py#L22) |
| Roster blurbs | 160 characters each | [edge_walker.py:587](../../../../server/services/plugin/edge_walker.py#L587) |
| Delegated answer size | No cap. Delegate and Task Manager results are exempt from the per-result character limit. The answer is copied into the task row, the event and the review prompt whole. | [tool_output.py:54](../../../../server/services/tool_output.py#L54), [agent_workflow.py:2602](../../../../server/services/temporal/agent_workflow.py#L2602) |
| Legacy `check_delegated_tasks` | Results cut to 4,000 characters | [tools.py:1097](../../../../server/services/handlers/tools.py#L1097) |
| Home progress | 20 steps | [work_progress.py:22](../../../../server/services/employees/work_progress.py#L22) |
| Reviewed delivery | 31 days start-to-close, 2 min heartbeat, unlimited attempts | [agent_workflow.py:3327](../../../../server/services/temporal/agent_workflow.py#L3327) |

The failure rule, verbatim:

```python
                next_retry = TeamTask.retry_count + 1
                retryable = next_retry < TeamTask.max_retries
```

Concurrency in short: several teammates run at once, up to the permit limit, and a lead may assign any number of tasks in one response; the extra runners wait for a permit. Nothing found limits one teammate to one task at a time (section 10). The only queue is that permit wait; `queue_sequence` and `queue_position` are for display.

## 8. Extension points relevant to a dynamic workflow

Facts only.

- The call that starts a teammate run is `workflow.start_child_workflow(agent_workflow_name(node_type, child_context), args=[child_context], id=...)`. It appears twice: in the closure `_start_delegation` ([agent_workflow.py:1886](../../../../server/services/temporal/agent_workflow.py#L1886)) and in the runner ([agent_workflow.py:667](../../../../server/services/temporal/agent_workflow.py#L667)). The handle it returns is awaitable and yields the child's result envelope; both sites await it.
- Neither starter is a module-level function. `_start_delegation` and `_run_task_manager_delegation` are closures inside `_run_impl` that read per-turn locals (`calls`, `tool_index`, `iteration`, `delegation_depth`, `payload`, `context`). The reusable pieces are module-level: `_inherited_scope`, `_child_control_scope`, `_delegation_child_id`, `_normalise_delegated_result`, `agent_workflow_name`.
- The detached path cannot be awaited for an answer as written: the lead never awaits the runner handle, the runner returns only `{team_id, team_task_id, status}`, and the answer lives in the task row and the event.
- Several teammate runs are already awaited concurrently: the direct path holds up to `max_concurrent_subagents` child handles at once and consumes them in call order; the Task Manager path gathers activity handles and creates coroutine tasks that each start a child ([agent_workflow.py:2127](../../../../server/services/temporal/agent_workflow.py#L2127), [agent_workflow.py:2165](../../../../server/services/temporal/agent_workflow.py#L2165)).
- The authorized teammate list inside workflow code is `tools` and its name index `tool_index` ([agent_workflow.py:1156](../../../../server/services/temporal/agent_workflow.py#L1156), [agent_workflow.py:1243](../../../../server/services/temporal/agent_workflow.py#L1243)). A teammate entry has `name` (`delegate_to_*`), `tool_node_id`, `node_type`, `version`, `task_queue`, `parameters`, `llm_hidden`, and `tool_info` with `label`, `delegate_tool_name`, `child_tools`, `child_skills`, `capabilities` ([agent_activities.py:1624](../../../../server/services/temporal/agent_activities.py#L1624), [edge_walker.py:742](../../../../server/services/plugin/edge_walker.py#L742)). It is the result of an activity, so it is in history; it changes mid-run through `agent.refresh_tools` ([agent_workflow.py:2739](../../../../server/services/temporal/agent_workflow.py#L2739)).
- Authorization for a team assignment is checked twice: in the Task Manager activity (membership, [agent_team.py:183](../../../../server/services/agent_team.py#L183)) and in the workflow bridge (the delegate must be in `tool_index` with a matching node id, [agent_workflow.py:1936](../../../../server/services/temporal/agent_workflow.py#L1936)).
- A tool call becomes an activity named `node.<type>.v<version>` ([agent_workflow.py:2391](../../../../server/services/temporal/agent_workflow.py#L2391)). The Task Manager is the one tool whose result drives workflow commands: any `taskManager` result containing a dict `delegation_request` is bridged ([agent_workflow.py:2557](../../../../server/services/temporal/agent_workflow.py#L2557)). A tool that declares `needs_canvas` receives the run's nodes and edges ([agent_workflow.py:2304](../../../../server/services/temporal/agent_workflow.py#L2304)).
- The lifecycle activities are separate and registered on the main queue: `agent.queue_delegation`, `agent.begin_delegation`, `agent.finish_delegation`, `agent.cancel_delegation`, `agent.acquire_subagent_permit`, `agent.release_subagent_permit`, `agent.register_task_execution`, `agent.finalize_team` ([agent_activities.py:2659](../../../../server/services/temporal/agent_activities.py#L2659)). Both workflows are registered with every worker that runs workflows ([workflow_registry.py:9](../../../../server/services/temporal/workflow_registry.py#L9)).
- Subclass hooks exist around tool batches and child tracking: `_before_runtime_tool_batch`, `_runtime_tool_call_allowed`, `_before_runtime_tool_call`, `_complete_runtime_turn`, `_runtime_child_started`, `_runtime_child_finished` ([agent_workflow.py:3380](../../../../server/services/temporal/agent_workflow.py#L3380)); the Pi subclass uses them and treats a turn containing a delegate or Task Manager call specially ([pi_workflow.py:292](../../../../server/services/temporal/pi_workflow.py#L292)).
- The three numeric limits a run uses (`max_iterations`, `max_concurrent_subagents`, `max_delegation_depth`) are fields of the prepared payload, recorded once per run.
- A child's token usage is in its result (`result.usage`) and in the direct tool result (`usage`); the parent does not add it to its own total.
- The RLM agent already runs model-written Python in which connected tools are callable functions ([adapters.py:269](../../../../server/services/rlm/adapters.py#L269)). It is a single activity outside `AgentWorkflow`, and its delegate calls only start an agent when the Pi harness scope is present (INFERRED from the two gates cited in 3.3). This file did not trace it further.

## 9. Where the docs disagree with the code

- [agent_delegation.md](../../../agent_delegation.md), section "Event-Driven Task Completion (taskTrigger)": the diagram and its Key Files table say completion goes through `broadcaster.send_custom_event('task_completed')` and the in-memory event waiter. The code publishes through `dispatch.emit`, scoped to the workflow, plus an identity-only frame ([_events.py:135](../../../../server/nodes/agent/_events.py#L135)); the tools module contains no `send_custom_event` call. The same stale snippet is in [CLAUDE.md](../../../../CLAUDE.md), section "Task Trigger Node". The trigger module's own docstring also still says it listens on the event waiter ([task_trigger/__init__.py:3](../../../../server/nodes/trigger/task_trigger/__init__.py#L3)).
- [agent_delegation.md](../../../agent_delegation.md), section "Task Context Format": the quoted text ends "Please report this result to the user in a conversational way." The code tells the lead to review with Task Manager and not to create a duplicate assignment ([edge_walker.py:763](../../../../server/services/plugin/edge_walker.py#L763)).
- [agent_delegation.md](../../../agent_delegation.md), "Key Findings" 6 ("Delegation is non-blocking ... NOT returned to the parent's LLM") and the tool texts in code ("returns task_id immediately", "Use 'check_delegated_tasks'") describe the in-process path. Under `AgentWorkflow` a direct delegate call blocks and returns the child's answer, and no `check_delegated_tasks` tool is bound ([agent_workflow.py:2499](../../../../server/services/temporal/agent_workflow.py#L2499)). The same doc's section "Generation Stop/Resume ownership" states the attached behaviour correctly.
- [agent_delegation.md](../../../agent_delegation.md), steps 3, 4c and 4f: the dispatcher test is `is_registered_agent(node_type)`, not membership of `AI_AGENT_TYPES` ([tools.py:315](../../../../server/services/handlers/tools.py#L315)); the key is read with `resolve_api_key(..., principal=...)` ([tools.py:559](../../../../server/services/handlers/tools.py#L559)); the child context has about twenty keys, not six ([tools.py:610](../../../../server/services/handlers/tools.py#L610)).
- [agent_teams.md](../../../agent_teams.md), section "Human interfaces": "Selecting a team lead opens its full-height operational middle panel." The lead classes deliberately do not carry the panel hint ([orchestrator_agent/__init__.py:12](../../../../server/nodes/agent/orchestrator_agent/__init__.py#L12)); the hint is on the `taskManager` node ([task_manager/__init__.py:148](../../../../server/nodes/tool/task_manager/__init__.py#L148)) and the panel finds the lead through that node's tool edge ([TaskManagerPanel.tsx:86](../../../../client/src/components/parameterPanel/TaskManagerPanel.tsx#L86)).
- [agent_teams.md](../../../agent_teams.md), section "Parallel scheduling": "Eligible tasks enter a deterministic FIFO queue, dependencies remain blocked." `queue_sequence` orders listings ([database.py:3710](../../../../server/core/database.py#L3710)), but admission is whichever runner's one-second poll wins the counter update; no code orders admission by sequence. No code moves a `blocked` task to `queued` (section 10).
- [agent_teams.md](../../../agent_teams.md), same section: "Each child receives an isolated AgentWorkflow context containing only its bounded mission, relevant context, and its own connected tools, skills, and memory. The parent receives a compact result." The child context also carries the whole run graph, the parameter snapshot and the inherited scope including the chat run; with a Context node the child continues its stored conversation; and on the Task Manager path the parent's review prompt receives the child's whole answer, uncapped.
- In code, the lead prompt block is appended for any agent with a delegate tool, so a non-lead is told to use a tool it does not have (section 4.2).

## 10. Not verified / open questions

Nothing here was executed. The items below are read from code and matter to anyone building on this area.

- INFERRED: a delegated child that itself delegates may not get its permit back. It releases its permit under its lease id ([agent_workflow.py:1723](../../../../server/services/temporal/agent_workflow.py#L1723)) and later asks for the same id without `lease_version` ([agent_workflow.py:2861](../../../../server/services/temporal/agent_workflow.py#L2861)); the database answers "not acquired" for a permit row that exists and is `released`:

  ```python
                permit = existing.scalar_one_or_none()
                if permit:
                    return {
                        "acquired": (
                            permit.root_execution_id == root_execution_id
                            and permit.status == "active"
                        )
                    }
  ```

  ([database.py:4244](../../../../server/core/database.py#L4244)). The activity would then poll for its 1 h start-to-close under a 3-attempt policy. The permit tests state that a released lease is not reusable ([test_subagent_permit_ownership.py:1](../../../../server/tests/temporal/test_subagent_permit_ownership.py#L1)); no test names the reacquire step.
- INFERRED: `blocked` is a dead end. A task with an unaccepted dependency is created `blocked` ([agent_team.py:216](../../../../server/services/agent_team.py#L216)), the runner is still started because `delegation_request` is returned unconditionally, and the claim activity raises for a status that is neither queued, terminal nor running ([agent_activities.py:2190](../../../../server/services/temporal/agent_activities.py#L2190)). A search found no code that changes `blocked` to `queued`.
- INFERRED: an automatic requeue has no runner. After a failure with retries left the row is `queued` and unassigned, `agent.finish_delegation` reports `requeued` and publishes no event ([agent_activities.py:2529](../../../../server/services/temporal/agent_activities.py#L2529)), and `get_claimable_tasks` has no caller outside the service ([database.py:4017](../../../../server/core/database.py#L4017)). The lead can only `modify_task` or `cancel_task` a queued row.
- INFERRED: a retry or reassign from the editor panel changes the row through `manage_team_task` and starts nothing, because only the workflow bridges a `delegation_request` ([agent_teams/handlers.py:200](../../../../server/services/agent_teams/handlers.py#L200)).
- INFERRED: the 3-hour stale reclaim can release the permit of a child that is still running, since workflows have no lifetime cap ([database.py:4203](../../../../server/core/database.py#L4203)).
- INFERRED: after Reset or a terminate, a runner's `finally` does not run, so its permit stays active until that reclaim. A later Start has a new root, so the old counter no longer matters.
- INFERRED: direct team delegations derive the task id from root execution id, delegating node id, iteration and call index ([agent_workflow.py:1769](../../../../server/services/temporal/agent_workflow.py#L1769)). Runs of one deployed generation share the root execution id, so two runs of the same delegating node can derive the same id. Task Manager ids are random and do not collide.
- INFERRED: hidden delegate tools are hidden only from the tool list sent to the model; dispatch looks names up in `tool_index` without checking `llm_hidden` ([agent_workflow.py:1598](../../../../server/services/temporal/agent_workflow.py#L1598)).
- INFERRED: nothing stops one teammate from running two tasks at once. Assignment checks membership only; the permit is per root, not per teammate. Both runs would share the conversation row, the output slot and the member status.
- NOT VERIFIED: whether Temporal applies the parent close policy to attached children when the parent continues as new. The code avoids the question by never rolling over while a handle is live.
- NOT VERIFIED: how review runs are ordered when several tasks finish together (the trigger and controller side), and whether a trigger-spawned run graph always contains the teammates.
- NOT VERIFIED: the exact JSON the model receives for a Task Manager result, and whether a retried Task Manager activity can create a second task row (the id is random, not derived from the tool call id).
- NOT VERIFIED: a standalone worker process. `init_agent_team_service` is called only in the backend's startup ([main.py:337](../../../../server/main.py#L337)); the standalone worker entry point does not call it ([worker.py:851](../../../../server/services/temporal/worker.py#L851)).
- NOT VERIFIED: whether any client code still calls the older team commands (`create_team`, `add_team_task`, `claim_team_task`, `complete_team_task`, `send_team_message`); the panel uses `get_team_status`, `get_team_tasks`, `manage_team_task`, `finish_team` and `get_team_task_trace`.
- NOT VERIFIED: the frontend beyond the Task Manager panel's data calls, and the Team Monitor panel.
