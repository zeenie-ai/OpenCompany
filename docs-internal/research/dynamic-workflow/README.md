# Dynamic workflow research

This folder answers one question: how could a "dynamic workflow" capability be built properly on OpenCompany's Temporal execution engine. It holds the gathered sources, six maps of the existing code, and the cross-reference that processes them.

Written 2026-11-12. A sixth source, Claude Code's Code Mode, was added on 2026-11-19. Research only: nothing here is built or decided.

State on 2026-11-19: the research is complete, and the next step is the owner's answers to six questions. [PLAN.md](PLAN.md) holds that state and what follows from each answer.

A dynamic workflow, as the sources use the term: an agent gets one tool, its model writes a short script in which each available sub-agent is a function, the script fans out, chains and loops inside that one tool call, and only its final value returns to the model.

## Read this first

[CROSS-REFERENCE.md](CROSS-REFERENCE.md) is the result: the four implementations side by side, what OpenCompany already has, where the script should run under Temporal and why, the smallest proper build, and six decisions for the owner. Its section 10, added 2026-11-19, covers Code Mode: how Claude Code built it and what one would touch here.

[PLAN.md](PLAN.md) is the short plan: where things stand, the six questions with the recommended answers, the two paths that follow, and how to resume after a break.

[FINDINGS.md](FINDINGS.md) lists possible defects and document drift the gatherers met on the way. None of it is fixed.

[PROBE.md](PROBE.md) holds the feasibility probe in full: the script, how to run it, and its output on the pinned Monty 0.0.18.

## Sources

Digests of other projects, written from their documentation and source code. Each carries a status line saying it describes that project, not OpenCompany.

| File | Covers |
|---|---|
| [sources/01-pydantic-dynamic-workflow.md](sources/01-pydantic-dynamic-workflow.md) | Pydantic AI Harness `DynamicWorkflow`: the tool, the catalog, budgets, failure handling, its behaviour under Temporal. Also the Pydantic article, the Bun post and Claude Code's dynamic workflows |
| [sources/02-pydantic-code-mode.md](sources/02-pydantic-code-mode.md) | Pydantic AI Harness Code Mode: tools as functions in one `run_code` call |
| [sources/03-pydantic-monty-and-temporal-durability.md](sources/03-pydantic-monty-and-temporal-durability.md) | The Monty sandbox (API, limits, snapshots, what changed between 0.0.18 and 1.6.0) and how Pydantic AI runs on Temporal |
| [sources/04-sakana-mass.md](sources/04-sakana-mass.md) | Sakana AI's MASS: a workflow as text in the prompt, how it is executed, the workflow search loop, the training half |
| [sources/05-deepseek-harness-plugin-architecture.md](sources/05-deepseek-harness-plugin-architecture.md) | DeepSeek Harness and Cordis: the plugin contract, lifecycle, what is a plugin, and its own workflow capability |
| [sources/06-claude-code-code-mode.md](sources/06-claude-code-code-mode.md) | Claude Code's Code Mode: the model writes JavaScript that calls the session's tools. Also the Claude API's programmatic tool calling, and a side-by-side table of four code-mode implementations. Added 2026-11-19 |

## Code maps

Facts about this repository as of commit 908a8ce6. Every claim links to a file and line. INFERRED marks a conclusion drawn from several places; NOT VERIFIED marks something not checked. Each map ends with the places where a document and the code disagree.

The code has moved since. At commit 03327c5b on 2026-11-19, 54 files under `server/services`, `server/nodes` and `server/core` differed from 908a8ce6, so a line number in a code map may be stale. Find the symbol by name before relying on it. The line numbers in the cross-reference were checked again that day.

| File | Covers |
|---|---|
| [analysis/01-graph-execution-on-temporal.md](analysis/01-graph-execution-on-temporal.md) | How a workflow graph runs: `MachinaWorkflow`, node activities, the worker pool, patch markers and generation flags |
| [analysis/02-agent-loop-on-temporal.md](analysis/02-agent-loop-on-temporal.md) | `AgentWorkflow`: the turn loop and how every kind of tool call is dispatched |
| [analysis/03-delegation-and-teams.md](analysis/03-delegation-and-teams.md) | How one agent runs another: Task Manager, `delegate_to_*`, teams, permits, the task tables |
| [analysis/04-deployment-control-and-durability-rules.md](analysis/04-deployment-control-and-durability-rules.md) | Generations, Start, Stop, Resume, Reset, recovery, and 46 rules for new workflow code with their sources |
| [analysis/05-code-execution-and-sandboxes.md](analysis/05-code-execution-and-sandboxes.md) | The code executor nodes including Monty, the RLM agent, the Pi coding agent and its inline delegation |
| [analysis/06-tools-plugins-and-graph-mutation.md](analysis/06-tools-plugins-and-graph-mutation.md) | What a plugin can and cannot add, how a tool reaches a model, the Agent Builder, Hire and validation |

## How it was gathered

- Ten agents worked in parallel on 2026-11-12, four on the external sources and six on the code. Each wrote one or two of the files above.
- The lead read all eleven files, re-read the code behind the claims the cross-reference rests on, and ran one probe on the pinned sandbox package. The cross-reference, section 9, says what was checked.
- The external sources were fetched that day. Each digest records the URLs, the clone commits and the licences. Nothing downloaded was executed.
- The code maps were read-only. No test and no server code was run. HEAD moved during the work, from 908a8ce6 to 13708877 (the v0.3.2 release commits). Nothing under `server/services`, `server/nodes` or `server/core` differs between the two.
- On 2026-11-19 the lead added the sixth source alone, from Anthropic's public documentation pages, and wrote section 10 of the cross-reference. Each quoted passage in that digest was matched by a script against the downloaded pages. No Claude Code session was started in Code Mode.

## A standing decision this touches

[temporal_production_practices.md](../../temporal_production_practices.md) records an owner decision of 2026-11-11: OpenCompany "takes no pydantic-ai dependency and builds no Code Mode feature". The cross-reference keeps to the first half and asks about the second (section 8, question 1).

## Housekeeping

- The folder was committed on 2026-11-19. Until then it was untracked.
- All files pass the docs lint: `cli/tests/test_docs_paths_resolve.py`, selected with `-k dynamic-workflow`.
- [builder_documentation.py](../../../server/services/builder_documentation.py) collects every Markdown file under `docs-internal`. In a source checkout the Agent Builder's documentation search can therefore read these files, and a bundle built from this tree would include them.
