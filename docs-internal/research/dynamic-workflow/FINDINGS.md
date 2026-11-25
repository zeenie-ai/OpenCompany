# Found on the way

Possible defects and document drift that the gatherers met while mapping the code on 2026-11-12 (commit 908a8ce6). None of this was the task, and none of it was run: every item is read from code. "Lead check" says what the lead re-read. Items the gatherers marked INFERRED stay inferred until someone reproduces them.

Nothing here was fixed or planned when it was written. It is a list to pick from. Ten of the sixteen possible defects were checked again on 2026-11-19; one had been fixed since ([Checked again on 2026-11-19](#checked-again-on-2026-11-19)). All sixteen were checked on 2026-11-23, after reusable agent instances arrived, and five new ones were found ([Checked again on 2026-11-23](#checked-again-on-2026-11-23)).

## Possible defects

| # | What | Where it is described | Lead check |
|---|---|---|---|
| 1 | A team task created with an unmet dependency stays `blocked`. It is created `blocked` at [agent_team.py:216](../../../server/services/agent_team.py#L216), its runner is still started, and the claim activity raises for that status. No code was found that moves `blocked` to `queued`. | [analysis/03](analysis/03-delegation-and-teams.md), section 10 | A search of `core/database.py`, `services/agent_team.py` and the agent activities found no such transition. Not run |
| 2 | After a failed task with retries left, the row goes back to `queued` with no assignee, and nothing starts a runner for it. | [analysis/03](analysis/03-delegation-and-teams.md), section 10 | Not re-read |
| 3 | A delegated child that itself delegates gives up its permit and may not get it back: the database answers "not acquired" for a released permit row. | [analysis/03](analysis/03-delegation-and-teams.md), section 10 | Not re-read |
| 4 | A permit older than three hours is released by the next acquire on the same root, although workflows have no lifetime cap, so a child that is still running can lose its permit. | [analysis/03](analysis/03-delegation-and-teams.md), section 10 | Not re-read |
| 5 | Direct team delegations derive the task id from the root execution id, the delegating node, the iteration and the call index. Runs of one generation share the root execution id, so two runs can derive the same id. | [analysis/03](analysis/03-delegation-and-teams.md), section 10 | Not re-read |
| 6 | `cancel_task` changes the task row only ([agent_team.py:248](../../../server/services/agent_team.py#L248)). A task cancelled while running keeps running. | [analysis/03](analysis/03-delegation-and-teams.md), section 6 | The row transition was re-read; the absence of a cancel was not |
| 7 | A retry or reassign from the editor's task panel changes the row and starts nothing, because only the workflow turns a `delegation_request` into a run. | [analysis/03](analysis/03-delegation-and-teams.md), section 10 | Not re-read |
| 8 | The model-facing text of a direct delegate tool says it "returns task_id immediately" and that the agent "works in background". On Temporal the call waits and returns the answer. The lead prompt block ("Never call delegate_to_* directly") is also added for a non-lead agent that has no Task Manager. | [analysis/02](analysis/02-agent-loop-on-temporal.md), section 9; [analysis/03](analysis/03-delegation-and-teams.md), section 4.2 | The prompt site was re-read. At commit 03327c5b it is at [agent_activities.py:1705](../../../server/services/temporal/agent_activities.py#L1705) |
| 9 | `pydantic-monty` 0.0.18 runs the sandbox inside the process that runs the node, and `montyExecutor` is usable as an AI tool. Upstream moved execution to worker subprocesses in 0.0.19, saying a Monty process "can never be made fully crash-proof against memory errors triggered by adversarial code". | [sources/03](sources/03-pydantic-monty-and-temporal-durability.md), section 8.2; [analysis/05](analysis/05-code-execution-and-sandboxes.md), section 3.1 | The pin and the installed version were checked |
| 10 | A `ToolNode` in main-flow position, such as `canvas`, returns a result with no `success` key, so a graph run may record it as failed. | [analysis/01](analysis/01-graph-execution-on-temporal.md), section 9 | Not re-read |
| 11 | `store_node_output_activity` is scheduled with no retry policy, so Temporal's unlimited default applies. | [analysis/01](analysis/01-graph-execution-on-temporal.md), section 9 | Not re-read |
| 12 | The JS and TS executors accept a `timeout` up to 600 s, but the HTTP client to the sidecar has a fixed 30 s total timeout. | [analysis/05](analysis/05-code-execution-and-sandboxes.md), section 3.3 | Not re-read |
| 13 | A lone `add_node` result from the Agent Builder (a saved tool the run lacks) may be skipped by the hot rebind whenever the calling agent's id is known. | [analysis/06](analysis/06-tools-plugins-and-graph-mutation.md), section 10 | Not re-read |
| 14 | The Skill tool's "loaded" state is a dictionary in one process. It does not survive a restart or reach a second worker. | [analysis/06](analysis/06-tools-plugins-and-graph-mutation.md), section 4.9 | Not re-read |
| 15 | No code was found that stores the token usage or cost of an agent run on the Temporal path; the usage is only returned. | [analysis/02](analysis/02-agent-loop-on-temporal.md), section 7.1 | Fixed on 2026-11-18, see below |
| 16 | Tool messages on the Temporal path carry no image blocks; only the in-process loop attaches them. | [analysis/02](analysis/02-agent-loop-on-temporal.md), section 4.6 | Not re-read |

Already known and queued elsewhere: the Python code node runs on the event loop and never reads its timeout ([temporal_production_practices.md](../../temporal_production_practices.md), item 2); an oversized activity input fails the workflow task forever (the same note, item 3).

### Checked again on 2026-11-19

Checked at commit 092def0c, again from code only. Nothing was run.

| # | Result |
|---|---|
| 1 | Still as described. No code moves a task from `blocked` to `queued`: outside the tests, the status is written only where a task is created, and read where tasks are listed, edited or cancelled |
| 2 | Still as described for a failure that may be retried. `fail_task` in [agent_team.py](../../../server/services/agent_team.py) has since gained a `retryable` argument: with `retryable=False` the task fails at once and is not queued again |
| 8 | Still as described. The prompt block is at [agent_activities.py:1705](../../../server/services/temporal/agent_activities.py#L1705) |
| 9 | The pin is unchanged: `pydantic-monty==0.0.18` in [pyproject.toml](../../../server/pyproject.toml) |
| 10 | Not read again, but the two files it rests on ([plugin/tool.py](../../../server/services/plugin/tool.py) and the canvas node) have not changed since 908a8ce6 |
| 11 | Still as described: the call at [workflow.py:639](../../../server/services/temporal/workflow.py#L639) passes a 10-second timeout and no retry policy |
| 12 | Not read again, but the two files it rests on ([_base.py](../../../server/nodes/code/_base.py) and [_nodejs.py](../../../server/nodes/code/_nodejs.py)) have not changed since 908a8ce6 |
| 14 | Still as described. The only change to [skill_runtime.py](../../../server/services/skill_runtime.py) since 908a8ce6 is the wording of one error |
| 15 | **Fixed** by `3dd89528` on 2026-11-18: every `agent.execute_llm_step` books a priced row in `token_usage_metrics` through `record_llm_usage` ([agent_activities.py:662](../../../server/services/temporal/agent_activities.py#L662)) |
| 16 | Still as described: `image_blocks_from_tool_result` is called only by the in-process loop ([agent_runtime.py:440](../../../server/services/agent_runtime.py#L440)) |

Items 3, 4, 5, 6, 7 and 13 were not checked again.

### Checked again on 2026-11-23

Checked at commit cfb06d6c by read-only agents, from code only; the lead re-read items 1, 8 and new item A. Nothing was run.

Since commit e599ebe0 (2026-11-20) there are two delegation runtimes ([CROSS-REFERENCE.md](CROSS-REFERENCE.md), section 11.1). Hired employees in generations started since then run on agent instances (version 1). Older generations, and plain agents with agents wired as tools, keep Task Manager and `delegate_to_*` (version 0). Items 1 to 7 concern Task Manager and the team permits, so each has two answers.

| # | Version 0 | Version 1 |
|---|---|---|
| 1 | Still there: no code unblocks a `blocked` task ([agent_team.py:216](../../../server/services/agent_team.py#L216)) | Fixed: `accept_task` queues and wakes the dependent tasks ([service.py:829](../../../server/services/agent_instances/service.py#L829)) |
| 2 | Still there | Changed: a failed task stays `failed`; retry, request changes and reassign queue it and wake the instance |
| 3 | Still there | Fixed: a waiting manager takes a fresh lease |
| 4 | Still there | Still there. Opaque instance turns may run up to a day, so a permit past three hours can be released while its run goes on, and the limit of three can be exceeded (inferred) |
| 5 | Still there | Cannot happen: instances never see `delegate_to_*`, and ids come from team, job, instance, run and tool call |
| 6 | Still there | Fixed: cancelling fences and stops the running attempt and the work below it |
| 7 | Still there | Changed: the editor refuses these tasks; recovery is Retry review |

| # | Result |
|---|---|
| 8 | Still there. The prompt block is now at [agent_activities.py:1885](../../../server/services/temporal/agent_activities.py#L1885) and is still appended to any agent that has `delegate_to_*` tools and is not an instance. The "returns task_id immediately" text remains in [ai.py](../../../server/services/ai.py) and [plugin/base.py](../../../server/services/plugin/base.py) |
| 9 | Still there: the pin is unchanged |
| 10 | Not checked: [plugin/tool.py](../../../server/services/plugin/tool.py), which it rests on, has changed since |
| 11 | Still there, now at [workflow.py:644](../../../server/services/temporal/workflow.py#L644) |
| 12 | Still there |
| 13 | Still there |
| 14 | Still there. Commit ba81442b only moved the skill load off the event loop |
| 15 | Fixed (2026-11-18, see above) |
| 16 | Partly fixed: Temporal tool messages now carry image blocks for `piRead` results only |

New possible defects, read from code and not reproduced:

| # | What | Where |
|---|---|---|
| A | Stop can be acknowledged while a Claude Code, Codex, RLM or Gemini CLI instance turn is still running. The session counts the turn only while starting it: `child_start()` closes as soon as the activity has started, and neither `set_control_state` nor `wait_for_checkpoint` waits for the running activity. [agent_teams.md](../../agent_teams.md) says opaque managed agents "finish the whole node Activity before acknowledging Stop". The lead re-read this | [agent_instance_workflow.py:443](../../../server/services/temporal/agent_instance_workflow.py#L443), [execution_control.py:108](../../../server/services/temporal/execution_control.py#L108) |
| B | An instance's whole transcript travels in the claim activity's result and in the run snapshot, and the instance load has no 1 MB guard like the node-keyed store's. A long transcript could exceed Temporal's payload limit (inferred) | [service.py:1137](../../../server/services/agent_instances/service.py#L1137) |
| C | The session continues as new after every 100 runs, not on history pressure, and without waiting for its handlers to finish (inferred) | [agent_instance_workflow.py:515](../../../server/services/temporal/agent_instance_workflow.py#L515) |
| D | The session builds its `RetryPolicy` inline, and its module is not in the list that the retry-policy test scans | [tests/test_retry_policies.py](../../../server/tests/test_retry_policies.py) |
| E | Three registered workflow types (`ApprovalWorkflow` and the two agent instance types) have no replay recorder scenario, so the next release's replay corpus cannot cover them until scenarios exist | [record_fixtures.py](../../../server/tests/temporal/record_fixtures.py) |

Document drift on 2026-11-23: the four CLAUDE.md items below are all still there. The code now has 22 distinct patch ids; [TEMPORAL_ARCHITECTURE.md](../../TEMPORAL_ARCHITECTURE.md) names 14 of them somewhere, and its list of live markers still names nine.

## Document drift

Each code map ends with a section that lists where a document and the code disagree. The count per file:

| File | Section | Items |
|---|---|---|
| [analysis/01](analysis/01-graph-execution-on-temporal.md) | 8 | 11 |
| [analysis/02](analysis/02-agent-loop-on-temporal.md) | 9 | 6 |
| [analysis/03](analysis/03-delegation-and-teams.md) | 9 | 8 |
| [analysis/04](analysis/04-deployment-control-and-durability-rules.md) | 8 | 10 |
| [analysis/05](analysis/05-code-execution-and-sandboxes.md) | 9 | see the file |
| [analysis/06](analysis/06-tools-plugins-and-graph-mutation.md) | 9 | 12 |

The ones that touch [CLAUDE.md](../../../CLAUDE.md), all four still in it on 2026-11-19:

- It says Turn on Talk and Apply are "each a restart on the saved graph" through `restart.py`. The code calls Safe Apply, and `restart_with_latest_graph` has no production caller ([analysis/04](analysis/04-deployment-control-and-durability-rules.md), section 8).
- Its Task Trigger section shows completion going through `broadcaster.send_custom_event('task_completed')`. The code publishes through `dispatch.emit`, scoped to the workflow ([analysis/03](analysis/03-delegation-and-teams.md), section 9).
- It describes the workflow facade as about 840 lines; it is 1,155 ([analysis/01](analysis/01-graph-execution-on-temporal.md), section 8).
- It says importing `evaluate_condition` from the submodule avoids the package `__init__`; the comment in the code says the `__init__` still runs and that this is tolerated (the same section).

One that touches [TEMPORAL_ARCHITECTURE.md](../../TEMPORAL_ARCHITECTURE.md): its list of live patch markers names nine ids; the code has twenty ([analysis/04](analysis/04-deployment-control-and-durability-rules.md), section 8).
