# Found on the way

Possible defects and document drift that the gatherers met while mapping the code on 2026-11-12 (commit 908a8ce6). None of this was the task, and none of it was run: every item is read from code. "Lead check" says what the lead re-read. Items the gatherers marked INFERRED stay inferred until someone reproduces them.

Nothing here is fixed or planned. It is a list to pick from.

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
| 15 | No code was found that stores the token usage or cost of an agent run on the Temporal path; the usage is only returned. | [analysis/02](analysis/02-agent-loop-on-temporal.md), section 7.1 | Not re-read |
| 16 | Tool messages on the Temporal path carry no image blocks; only the in-process loop attaches them. | [analysis/02](analysis/02-agent-loop-on-temporal.md), section 4.6 | Not re-read |

Already known and queued elsewhere: the Python code node runs on the event loop and never reads its timeout ([temporal_production_practices.md](../../temporal_production_practices.md), item 2); an oversized activity input fails the workflow task forever (the same note, item 3).

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

The ones that touch [CLAUDE.md](../../../CLAUDE.md):

- It says Turn on Talk and Apply are "each a restart on the saved graph" through `restart.py`. The code calls Safe Apply, and `restart_with_latest_graph` has no production caller ([analysis/04](analysis/04-deployment-control-and-durability-rules.md), section 8).
- Its Task Trigger section shows completion going through `broadcaster.send_custom_event('task_completed')`. The code publishes through `dispatch.emit`, scoped to the workflow ([analysis/03](analysis/03-delegation-and-teams.md), section 9).
- It describes the workflow facade as about 840 lines; it is 1,155 ([analysis/01](analysis/01-graph-execution-on-temporal.md), section 8).
- It says importing `evaluate_condition` from the submodule avoids the package `__init__`; the comment in the code says the `__init__` still runs and that this is tolerated (the same section).

One that touches [TEMPORAL_ARCHITECTURE.md](../../TEMPORAL_ARCHITECTURE.md): its list of live patch markers names nine ids; the code has twenty ([analysis/04](analysis/04-deployment-control-and-durability-rules.md), section 8).
