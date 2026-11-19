# Dynamic workflows on OpenCompany: cross-reference

> **Status: Planned.** Research result written 2026-11-12. Nothing in this file is built or decided. It processes the eleven gathered files in this folder; each claim names the file and section that holds the evidence. Code is cited as of commit 908a8ce6. What happens next is in [PLAN.md](PLAN.md).

Terms used here:

- **Dynamic workflow**: an agent gets one tool. Its model writes a short script in which each available sub-agent is a function. The script fans out, chains and loops inside that one tool call, and only its final value returns to the model.
- **Lead**: the agent that would carry the tool. **Teammate**: an agent wired to the lead.
- **Sub-agent run**: one teammate working on one task, as its own Temporal child workflow.
- **Replay**: Temporal re-running workflow code against recorded history, after a restart or a cache eviction.

## 1. Bottom line

1. The four sources describe one pattern in two forms. Pydantic AI Harness, DeepSeek Harness and Claude Code let the model write a script that calls sub-agents. Sakana's MASS has no script: the plan is text in the lead's prompt, and the lead calls its ordinary sub-agent tool. Section 2.
2. OpenCompany already has most of the parts: a named teammate catalog held in the lead's workflow state, sub-agent runs as child workflows that a non-lead agent awaits in the same turn (three at a time), a Monty sandbox node, a check-and-refuse loop for model-written specs, and per-generation flags for shipping new workflow behaviour. The missing part is a tool whose single call starts and joins several sub-agent runs. Section 3.
3. The decision that matters is where the script runs. Pydantic runs the script, and the sub-agents' own loops, inside Temporal workflow code and lets Temporal replay them. That does not fit this repository. What fits: run the script in short activities that re-run it against recorded results, and let workflow code only start the sub-agent children. Claude Code resumes its workflows by the same rule, and OpenCompany's Pi coding agent already works this way. A probe on the pinned Monty 0.0.18 ran a fan-out-then-chain script this way. Section 4 and Appendix A.
4. The smallest proper build is one tool plugin with a sandbox step activity, one new workflow type, and one data-driven branch in the agent workflow behind a generation flag. It needs no pydantic-ai dependency. Section 5.
5. A cheaper first step needs no engine work: a MASS-style team plan (what each teammate must return, and in what order they are called) plus a delegation directive in the lead's prompt. The code path for it exists today for an agent that has other agents wired as tools. For hired team leads it runs through Task Manager, where results come back in later review runs and `depends_on` looks broken. Section 4.5.
6. A standing decision applies. On 2026-11-11 the owner decided that OpenCompany "takes no pydantic-ai dependency and builds no Code Mode feature" ([temporal_production_practices.md](../../temporal_production_practices.md), "Decisions in this pass"). The build in section 5 honours the first half. Whether the second half covers a dynamic workflow is the owner's call. Section 8, question 1.
7. Six decisions are open. Section 8.

## 2. One pattern, four implementations

Evidence: [sources/01](sources/01-pydantic-dynamic-workflow.md) (Pydantic, and section 8 for Claude Code and the Bun post), [sources/03](sources/03-pydantic-monty-and-temporal-durability.md), [sources/04](sources/04-sakana-mass.md), [sources/05](sources/05-deepseek-harness-plugin-architecture.md) section 7.6.

| | Claude Code | Pydantic AI Harness | DeepSeek Harness | Sakana MASS |
|---|---|---|---|---|
| What the model writes | A JavaScript script | A Python script (Monty subset) | A JavaScript script body plus a JSON `meta` | Nothing at run time. A plan in text is appended to the task prompt |
| How a sub-agent is called | `agent(prompt, options)`, any prompt | A named function from a host catalog: `name(task="...")` | `agent(prompt, options)` | The lead calls the harness's sub-agent tool, one call per step |
| Fan-out | `parallel()`, `pipeline()` | `asyncio.gather` | `parallel()`, `pipeline()` | Several tool calls in one turn, restricted by a directive |
| Where the script runs | An isolated runtime, in the background | The Monty sandbox; under Temporal, inside workflow code | A sandboxed child Node process | Not applicable |
| What the caller gets | The run is launched; the result arrives later | The tool call waits and returns the final value | Waits, or returns a job id in background mode | The lead reads each report as a tool result |
| Limits | 16 concurrent, 1,000 agents per run, 4,096 items per fan-out | 50 calls per parent run, exact; no concurrency limit found in its code | min(16, cores - 2) concurrent, 1,000 agents, 4,096 items, 50,000 result characters | None in the harness |
| Typed results | A JSON Schema per call | From the sub-agent's output type, shown as a `TypedDict` | A JSON Schema subset per call | An "output contract" in the plan text, not enforced |
| A sub-agent fails | `null` inside a fan-out; an error when awaited directly | `RuntimeError` in the script | `null`; a misused hook ends the script | The lead re-delegates |
| Check before running | Syntax, schema | Static type check against the catalog | `meta` validation and a parse | A marker sentence only |
| Durability | Saved per-agent results, replayed in the order agents start; clock and randomness throw | None alone. Under Temporal the script is re-executed on replay | None: "No journaling or resume" | None |
| Maturity | Shipped; used for the Bun rewrite (about 50 workflows, 64 agents at peak) | About four months old, 0.x, one Temporal test | Developer preview, 0.2.2-rc.2 | Research code, no licence |

What all of them agree on:

- One tool and one script or plan. Intermediate results stay out of the lead's context.
- A sub-agent run is isolated. It gets the task text and nothing of the lead's conversation, and it remembers nothing from an earlier call.
- A hard cap on the number of sub-agent runs.
- A check before anything runs. A mistake in the script fails loudly; a failed sub-agent is data the script handles.
- No nesting: a sub-agent cannot start its own workflow.
- Where resume exists, the script may not read the clock or random numbers.

They differ on durability, and that is the part OpenCompany cannot leave out: an employee runs for months, survives restarts, and can be stopped and resumed.

## 3. What OpenCompany has today

| Part of the pattern | What exists here | Gap |
|---|---|---|
| Named catalog of sub-agents | The lead's `tools` list holds one hidden `delegate_to_*` entry per teammate with node id, type, label, tools and skills. The lead's prompt already lists them. ([analysis/03](analysis/03-delegation-and-teams.md) sections 4.2 and 8) | Entries are addressed by node id, not as typed functions |
| A sub-agent run | A child `AgentWorkflow` with its own history, model loop, compaction, approvals and Stop handling. ([analysis/02](analysis/02-agent-loop-on-temporal.md) section 3) | None. This is stronger than Pydantic's inline run |
| One delegation per tool call | Two paths. A team lead calls Task Manager `assign_task`, is told "queued", and reads the answer in a later review run. Any other agent calls `delegate_to_*`: an attached child, awaited in the same turn, three at a time. ([analysis/03](analysis/03-delegation-and-teams.md) sections 3.1 and 3.2) | Nothing starts and joins several runs inside one tool call ([analysis/02](analysis/02-agent-loop-on-temporal.md) section 8) |
| Sandbox | Monty is a pinned dependency (0.0.18, [server/pyproject.toml:239](../../../server/pyproject.toml#L239)) and a node, `montyExecutor`, with a time and a memory limit. ([analysis/05](analysis/05-code-execution-and-sandboxes.md) section 3.1) | A script cannot call tools or agents; the suspend-and-resume API is unused. 0.0.18 runs in-process; every release since 0.0.19 runs in worker subprocesses ([sources/03](sources/03-pydantic-monty-and-temporal-durability.md) section 8.2) |
| Check, then refuse with reasons | `show_ui`: the server checks a model-written spec against a catalogue and returns every problem in one tool error. ([analysis/06](analysis/06-tools-plugins-and-graph-mutation.md) section 5.8) | A pattern to reuse |
| Limits | Three concurrent sub-agents (permits per root execution when the lead has a team, otherwise a start window of three per turn), delegation depth 2, the lead's own iteration limit. ([analysis/03](analysis/03-delegation-and-teams.md) section 7) | No cap on the number of delegations. A child's tokens are not added to the lead's. A delegated answer has no size cap |
| Isolation | Each run is its own workflow. A teammate with a Context node continues one stored conversation across tasks, and two concurrent runs of one teammate overwrite it. Hire gives every team member a Context node. ([analysis/03](analysis/03-delegation-and-teams.md) section 5.2) | A script that calls one teammate twice in parallel needs isolated runs |
| Typed results | The child's result is reduced to its response text. Task Manager keeps `acceptance_criteria` on the task row. ([analysis/03](analysis/03-delegation-and-teams.md) section 5) | No structured output from a teammate |
| Load on demand | The Skill tool keeps a short catalogue and loads a skill's text when asked. ([analysis/06](analysis/06-tools-plugins-and-graph-mutation.md) section 4.9) | A pattern to reuse |
| A new sub-agent mid-run | A tool result with `operations` adds tools and teammates for the next step. ([analysis/06](analysis/06-tools-plugins-and-graph-mutation.md) section 4.9) | None |
| Durable execution | Cooperative Stop and Resume, Reset, per-generation flags, a replay corpus and a golden identity file. ([analysis/04](analysis/04-deployment-control-and-durability-rules.md)) | Its section 5 lists 46 rules for new workflow code |
| A program outside the model loop that asks for work | The Pi coding agent. An outside program asks for model and tool work; the workflow performs it in activities and children and keeps receipts; a restarted driver re-runs against the receipts. ([analysis/05](analysis/05-code-execution-and-sandboxes.md) section 4.3) | The precedent for section 4 |
| Approvals inside a sub-agent run | Handing work to an agent asks nothing. Each of the child's own tool calls is checked. ([analysis/02](analysis/02-agent-loop-on-temporal.md) section 8) | None. A held call in a script-started child is a draft, as today |
| What the owner sees | Agent progress events, team events, the Task Manager panel. A delegated child's text does not stream and its tool calls are not chat steps. ([analysis/02](analysis/02-agent-loop-on-temporal.md) section 7.7) | A script's progress has no display |

## 4. Where the script runs

### 4.1 Four options

| Option | How it would work here | What fits | What breaks or costs |
|---|---|---|---|
| A. Script in workflow code (Pydantic's way) | The agent workflow runs Monty itself. Each call starts a child. Temporal replays the script after a restart | No new activity | Model-written code runs on the workflow thread. Section 4.2 |
| B. Script in one long tool activity | The tool's activity runs the whole script and starts workflows through the Temporal client | No change to workflow code | Stop waits for an admitted activity, so an employee could not be stopped while a script runs. A retry re-runs the script from the top. The started runs are not children |
| C. Script in short activities, re-run against recorded results | An activity runs the script until it blocks on sub-agent calls and returns those calls. Workflow code starts one child per call, waits, and calls the activity again with the answers | Workflow code only coordinates. Stop, Resume and Reset work as they do for today's delegations. A runaway script fails one activity | New workflow code: one branch and one workflow type. Answers are passed again at each step |
| D. No script: a plan in the prompt (MASS's way) | The lead's prompt carries the plan and a delegation directive. The lead uses the delegation tools it has | No engine work | One delegation per model turn. Every intermediate result passes through the lead's context |

Recommendation: C for a script tool, and D as a first step that needs no release of workflow code. B is listed because it is the tempting shortcut.

### 4.2 Why Pydantic's placement does not fit here

1. **Workflow code here is coordination only, and nothing enforces it.** Every workflow class is declared `sandboxed=False` (for example [agent_workflow.py:808](../../../server/services/temporal/agent_workflow.py#L808), [workflow.py:216](../../../server/services/temporal/workflow.py#L216)) and no lint exists. Determinism rests on convention, the replay corpus and the golden identity file ([analysis/01](analysis/01-graph-execution-on-temporal.md) section 5.1, [analysis/04](analysis/04-deployment-control-and-durability-rules.md) rule 24). Running a model-written script there is the opposite of the rule, with no check behind it.
2. **A workflow task must return within 2 seconds.** The installed SDK (temporalio 1.34.0, `temporalio/worker/_workflow.py`) runs each workflow activation in a thread and fails the task after `_deadlock_timeout_seconds = 2`. Temporal then retries the task while the run stays open. A script that computes for two seconds would wedge the lead's run. Pydantic's docs give the same warning, and its only guard against a runaway loop inside a workflow, `max_duration_seconds`, is itself a replay hazard ([sources/01](sources/01-pydantic-dynamic-workflow.md) section 6.5).
3. **All workflow code shares ten slots.** Workflow tasks of every workflow type run on the main worker, at most 10 at a time ([worker.py:252](../../../server/services/temporal/worker.py#L252)). Script execution would compete with every employee's coordination.
4. **Replay would re-run every script.** After a restart, each recorded script would run again in a fresh sandbox ([sources/03](sources/03-pydantic-monty-and-temporal-durability.md) section 3.6). Replaying an open run would then depend on the installed Monty behaving exactly as before. Monty went from 1.0 to 1.6 in seven weeks, and its API was rewritten between the pinned 0.0.18 and 1.x.
5. **Pydantic also runs the sub-agents' loops inline.** Under Temporal a sub-agent's whole run executes in the parent's workflow code; only model requests and tool calls are activities, all in one history ([sources/01](sources/01-pydantic-dynamic-workflow.md) section 6.3). OpenCompany's agents are child workflows with their own history, compaction, approvals and Stop handling. That part should not be copied at all.
6. **Maturity.** The capability is about four months old and has one Temporal test. Its own migration note says it is not "a durable background task service". The upstream test for a remote sandbox inside a workflow is skipped as flaky ([sources/01](sources/01-pydantic-dynamic-workflow.md) section 9, [sources/03](sources/03-pydantic-monty-and-temporal-durability.md) section 8.3).

Three earlier rulings in [RFC-0006](../../../RFC-0006-TEMPORAL-DESIGN-PATTERNS.md) point the same way:

- **Section 16.14** judged plugins that run a framework's agent loop inside the workflow "Not applicable": such a plugin "would put a foreign, mostly preview loop into workflow code that runs unsandboxed and force a migration of every recorded AgentWorkflow history".
- **Section 18.17** judged `MachinaWorkflow`, which interprets the saved graph, "Adopted correctly (the interpreter)". The lead's reading: an interpreter in workflow code is accepted there because what it interprets is frozen at Start and every step is an activity called by name. A script that a model writes in the middle of a run is neither.
- **Section 12.5** rejected a tree of child workflows for fan-out ("do not adopt (never)") and named an admission-bounded window as the form to use. The window of three in section 4.4 is that form.

### 4.3 Why re-running in activities fits

- **It is the rule the repository already follows.** "Workflow code only coordinates; model calls, tool calls and I/O run in activities" is the first line of what already matches Pydantic's own guide ([temporal_production_practices.md](../../temporal_production_practices.md)).
- **History records everything, and Temporal never runs the script.** Each step's outcome (the pending calls, or the final value) is an activity result. Each sub-agent's answer is a child workflow result. After a restart, workflow code replays those results; nothing model-written runs again.
- **It is Claude Code's resume rule.** "Claude Code replays the run in the order agents start, and each agent either returns its saved result or runs again", with the clock and randomness banned ([sources/01](sources/01-pydantic-dynamic-workflow.md) section 8.3).
- **It is the Pi coding agent's shape.** Receipts are held by the workflow, and a re-run answers a recorded operation at once ([analysis/05](analysis/05-code-execution-and-sandboxes.md) section 4.3). Pi needs Updates, a polled Query, epochs and digests because its program is a long-lived outside process. A Monty script needs none of that: the probe's script re-ran in under a millisecond, so every step can start from the top.
- **Stop, Resume and Reset need nothing new.** Steps are short admitted activities and the sub-agents are ordinary children, so the control walk reaches all of them ([analysis/04](analysis/04-deployment-control-and-durability-rules.md) section 3.3).
- **A runaway script is one failed activity.** Monty's time and memory limits stop it; the model reads a tool error.
- **A Monty upgrade cannot break the replay of an open run**, because replay never executes Monty.

The probe (Appendix A) ran this on the pinned `pydantic-monty` 0.0.18, importing only that package:

- A script that gathers two `reviewer` calls and then calls `summarizer` ran in three steps: 18.6 ms for the first (it includes the type check), 0.3 ms and 0.1 ms for the next two.
- Step 1 returned both gathered calls as pending. Step 2, given their answers, returned the `summarizer` call. Step 3 returned the final value.
- A re-run with the same recorded answers gave the same value.
- The static check refused a misspelled function, a positional `task` and `task=123` before running anything.
- `while True: pass` was stopped at the configured 2 s limit.
- `import time` and `import random` fail on 0.0.18. `datetime.datetime.now()` reaches the host as a call, so the host must refuse it.

### 4.4 What it costs, and what has to be settled inside it

- **Answers travel again at each step.** The step activity needs the recorded answers. A delegated answer has no size cap today, and an oversized activity input fails the workflow task, not the activity ([analysis/04](analysis/04-deployment-control-and-durability-rules.md) rule 28). Each answer needs a bound for script use, or the answers go by reference.
- **A call budget.** No cap on the number of delegations exists today. Pydantic's default is 50 per parent run.
- **Concurrency.** Today's limit is three. A script run needs the same bound, so a script that gathers twenty calls runs three at a time.
- **A script in flight across an upgrade.** Its next step re-runs it on the new Monty. If it then asks for different calls than the recorded ones, the step must fail the tool call with an error the model can read. It must not guess.
- **Clock and operating-system calls.** The step must refuse them. On 0.0.18 a clock call reaches the host as an ordinary call (Appendix A).
- **The lead cannot roll over while a child is live.** The agent workflow skips continue-as-new while a delegation handle exists ([analysis/02](analysis/02-agent-loop-on-temporal.md) section 6.5). A long script should therefore live in its own workflow, with its own history.
- **Isolation of the sub-agent runs.** Section 8, question 4.

### 4.5 The no-script option

MASS shows that a workflow need not be code ([sources/04](sources/04-sakana-mass.md)):

- Its workflow is a list of agents with a role, an instruction and, after optimization, an output contract and input dependencies. The call order is prose in the orchestrator's entry.
- A plan alone does not make a model delegate. Under a bare prompt the base model delegated in 1 of 110 episodes. With a generic directive and no plan at all, it delegated in 110 of 110. The directive has seven rules: run the team for real, wait for each report, one stage at a time, self-contained instructions, verify before building on a result, bounded work, finish cleanly.
- Multi-agent runs were preferred to single-agent runs in 85.1% of judgments and generated 2.39 times the tokens.
- Nothing enforces a contract. The authors say so.

What exists here for it:

- **An agent with other agents wired as tools** gets `delegate_to_*` tools that wait and return the child's answer in the same turn. That is MASS's execution model. A plan and a directive in that agent's system prompt need no code change, in Dev mode. This was not run. One obstacle: the prepare step also appends the team-lead prompt block to such an agent, telling it never to call `delegate_to_*` directly ([FINDINGS.md](FINDINGS.md) item 8).
- **A hired team lead** delegates through Task Manager, whose arguments already carry the MASS fields: `mission`, `context`, `acceptance_criteria`, `depends_on`. But its prompt says "do not poll or wait in this run", each answer arrives in a separate review run, and a task created with an unmet dependency stays `blocked`: a search found no code that unblocks it ([FINDINGS.md](FINDINGS.md) item 1). So a plan through Task Manager gives roles and contracts, not steps that feed each other within one run.

MASS's search loop (run a workflow, let the model judge it against the best so far, rewrite it) costs about 13 full executions per task in the packaged driver, and one step flips a near-unanimous verdict with a probability of 0.22 to 0.40. It is not a product feature to build now.

## 5. The smallest proper build

This is a shape, not a plan. Each piece names what it mirrors.

1. **A tool node in its own plugin folder.** Model-facing name `run_workflow`, one argument, `code`. The description is built from the lead's teammate entries: fixed rules, a budget sentence, and one line per teammate in the form `async def <name>(*, task: str) -> str`. Mirrors Pydantic's tool ([sources/01](sources/01-pydantic-dynamic-workflow.md) section 3.2) and the roster the lead's prompt already gets ([agent_activities.py:1670](../../../server/services/temporal/agent_activities.py#L1670)).
2. **A step activity owned by the plugin.** It type-checks the script against the same signatures, runs it until it blocks, and returns the pending calls, the final value or the error text. An error goes back to the model as one tool error that lists what to fix, as `show_ui` does. It runs on the `code-exec` queue, where `montyExecutor` runs.
3. **A new workflow type that owns one script run.** A loop: step activity, start the children, collect the answers, with the concurrency window and the call budget. It takes part in Stop and Resume the way [EmployeeJobWorkflow](../../../server/services/temporal/employee_job_workflow.py#L71) does. Children start the way the direct delegation path starts them ([agent_workflow.py:1886](../../../server/services/temporal/agent_workflow.py#L1886)). A lead with a team could instead start them through the team's task runner, `DelegatedTaskWorkflow` ([agent_workflow.py:552](../../../server/services/temporal/agent_workflow.py#L552)), which another workflow type already starts and awaits. Each call would then be a task on the board with times and tokens, but that runner also publishes the task event that starts a review run. That choice belongs to question 3. A new workflow type needs no patch marker ([analysis/04](analysis/04-deployment-control-and-durability-rules.md) rule 1).
4. **One branch in the agent workflow's tool dispatch** that starts that workflow as a child and awaits its result. Make it data-driven: the tool entry built at prepare time names the workflow type, as an entry can already name its own activity ([agent_workflow.py:2391](../../../server/services/temporal/agent_workflow.py#L2391)). That is one general rule. Task Manager's branch on its node type is the form not to repeat.
5. **A generation flag for piece 4**, written at Start, so only new generations take the branch. A flag touches seven places ([analysis/04](analysis/04-deployment-control-and-durability-rules.md) section 7). Tests: a golden entry with `flags` and `new_types`, one recorded replay scenario, one real-SDK test per branch ([analysis/04](analysis/04-deployment-control-and-durability-rules.md) section 5.11).
6. **Two rows in central tables**: the approval class and the repeat safety of the new tool type. An unlisted type counts as a send ([classes.py:32](../../../server/services/approvals/classes.py#L32)) and is never repeated after a broken-off attempt.

Pieces 1 and 2 need no core edit: the plugin system covers them. Pieces 3 to 6 are core today, because a plugin cannot change how the agent workflow dispatches its tool call ([analysis/06](analysis/06-tools-plugins-and-graph-mutation.md) section 3.4).

Left out of the smallest build: nesting, saved scripts, a background mode, typed sub-agent outputs, token budgets across children, Code Mode, wiring for hired teams (the hire allowlist and the recipe), and a display for a script's progress.

The rules that bind it most, from [analysis/04](analysis/04-deployment-control-and-durability-rules.md) section 5: gate the new branch (rules 1 and 7), no lifetime timeout on children (12), no rollover with a live child (16), take part in Stop (18), expect Reset to terminate and keep durable facts in the database (20), workflow code only coordinates (24), measure what can grow (27 and 28), bound the fan-out (38), replay the previous release's histories (46).

## 6. What to take from each source, and what to leave

**Pydantic DynamicWorkflow.** Take the model-facing contract: teammates as typed async functions, `task` by keyword, the last expression is the result, a static type check that costs one retry and no budget, an exact call budget, bounded previews of finished results when a script fails, no nesting. Leave its placement under Temporal (section 4.2) and the package itself (the 2026-11-11 decision).

**Claude Code.** Take the durability rule: saved results, replayed in start order, clock and randomness banned. Its limits (16 concurrent, 1,000 per run) and its advisory "large workflow" warning are reference points. Its background launch is the alternative in question 6.

**DeepSeek Harness.** Take three small things. A misused hook ends the script, while a failed sub-agent is a value the script handles. The prompt rule: use the tool "ONLY when the user explicitly asks for a workflow or for large multi-agent orchestration"; for one or two delegations use plain delegation. Four durable records per run: run start, agent start, agent end, run end. Note what it decided against: it first ran scripts in-process and moved them to a separate process, because a synchronous spin could not be killed and blocked the host. It has no journaling and no resume.

**Sakana MASS.** Take the finding that the task-specific value sits in what each agent must return and check, and in the call order, and that a directive is what makes a model delegate. Leave the search loop and the fine-tuning.

**Pydantic Code Mode.** The mechanics are the same sandbox and the same step model, with the agent's own tools as the functions. It is excluded by the 2026-11-11 decision. One fact for later: inside its scripts a tool that needs approval cannot pause the script; it is resolved while the script waits, or it fails ([sources/02](sources/02-pydantic-code-mode.md) section 3.6).

## 7. Plugin architecture: DeepSeek Harness next to OpenCompany

Evidence: [sources/05](sources/05-deepseek-harness-plugin-architecture.md) sections 3 to 6, [analysis/06](analysis/06-tools-plugins-and-graph-mutation.md) section 3.

| Aspect | DeepSeek Harness (on Cordis) | OpenCompany |
|---|---|---|
| What a plugin is | A module that exports `name`, `inject`, `Config` and `apply(ctx, config)` | A `BaseNode` subclass in a folder under `server/nodes`, with optional handlers, routers and services |
| How it is found | A configuration row names the module. Rows come from bundles, profiles and patch layers | A package walk at import. Every plugin loads; `node_allowlist.json` decides what the UI shows and what Hire may build |
| Dependencies | Declared with `inject`. A plugin runs only while the services it names exist | Not declared. Plugins import services directly |
| Registration | Through the context (`ctx.tools.register()` and others). Each registration returns an undo function recorded on the plugin | About forty `register_*` functions on module-level registries. Registrations are never removed |
| Unload and reload | At run time. Undo functions run in reverse; dependants unload first; hot reload | None. A restart applies a change |
| What is core | The boot code and the vendored framework. The agent loop, the tool registry and the session log are plugins | The agent loop, tool dispatch, approval classes and the workflow lists are core |
| A swappable capability | A "seam": a definition package, a provider package and a consumer (the model-facing tool) | The closest are the multi-vendor nodes with provider registries (speech, translate) and the LLM provider registry |
| Middleware | Waterfall events such as `tools/pre-execute` | None in use. Approvals are one central check in the activity wrapper |
| Isolation of plugin code | None: plugins run in the host process | None: plugins run in the backend process |
| Durability of a workflow run | None | Temporal |
| An agent changing its own harness | A `plugin_manager` tool plus hot reload | The Agent Builder adds nodes to its own graph; hot rebind and Safe Apply put them to use |

For this feature, two things carry over:

- **The seam split.** Its workflow capability is three packages: a definition, one provider that runs scripts, and the model-facing tool. Here that maps to piece 3 (the loop), piece 2 (the Monty step) and piece 1 (the tool node).
- **A general rule instead of a per-node branch.** Its tool calls reach engines through services, not through tests on tool names. The equivalent here is piece 4: a tool entry names the workflow type that handles its call.

What does not carry over: unloading, hot reload and `inject`. They serve a single-user harness that changes while it runs. OpenCompany's durability comes from frozen generations, which is the opposite trade.

## 8. Decisions for the owner

Each option states what it assumes. The first option is the recommendation.

1. **Scope against the 2026-11-11 decision.**
   - (a) Dynamic workflow only; Code Mode stays out. Assumes the decision's "no Code Mode feature" meant tools as functions, and that sub-agent orchestration is a separate question.
   - (b) Reopen Code Mode as well. Assumes a ruling on approvals inside scripts: in the sources a held call is answered while the script waits, or it fails. None can pause a script and resume it later.
   - (c) Neither. This folder stays research.
2. **First step.**
   - (a) The prompt-level plan first (section 4.5), the script tool later. Assumes no workflow-code release is wanted now, and that Dev-mode agents with agents as tools are a fair first audience.
   - (b) The script tool directly (section 5). Assumes a release that carries a new generation flag and a new workflow type.
3. **Who gets the script tool first.**
   - (a) Dev-mode agents that have agents wired as tools. Assumes hired employees do not change.
   - (b) Hired team leads. Assumes changes to the hire allowlist and the recipe, and a ruling on the lead prompt's "All delegation MUST use the task_manager tool" and on the rule that a managed lead that assigns nothing fails.
4. **Sub-agent runs started by a script.**
   - (a) Isolated: no stored conversation is loaded or saved. All three script implementations do this, and calling one teammate twice in parallel needs it.
   - (b) Continue each teammate's conversation, as team tasks do today. Assumes a script never calls one teammate concurrently.
5. **Sandbox version.**
   - (a) Move to Monty 1.x before building on it. Assumes a rewrite of the `montyExecutor` node (about 240 lines) for the new API. Gains worker subprocesses, so a sandbox crash is not a backend crash, and a release its authors call ready for production.
   - (b) Stay on 0.0.18. The probe shows the mechanism works. Assumes in-process execution of model-written code is acceptable; that release was labelled experimental.
6. **Waiting or queued.**
   - (a) The tool call waits for the script, as a direct delegation waits today. Assumes scripts finish within a turn's patience.
   - (b) The call returns "queued" and the result arrives later, as Task Manager and Claude Code do. Assumes the review-run machinery, or a new completion path.

## 9. What was checked, and what was not

Checked by the lead against the code or the installed packages:

- RFC-0006 sections 12.5, 16.14 and 18.17, as quoted in section 4.2.
- Every production workflow class is `sandboxed=False`; `GENERATION_FLAGS` ([workflow.py:187](../../../server/services/temporal/workflow.py#L187)); `DORMANT_PATCHES` ([patches.py:14](../../../server/services/temporal/patches.py#L14)); the body of `DelegatedTaskWorkflow`; the id helpers and the sliding window of three; `NON_PARTICIPANTS` ([execution_control.py:29](../../../server/services/deployment/execution_control.py#L29)); the `needs_canvas` allowlist ([test_plugin_contract.py:362](../../../server/tests/test_plugin_contract.py#L362)); the approval default; the golden file's `flags` and `new_types` keys; the 2026-11-11 decision text.
- The SDK's 2-second check and the worker's 10 workflow-task slots.
- The `pydantic-monty` 0.0.18 API from its installed stub, and the probe in Appendix A.
- Every file in this folder passes the docs lint (`cli/tests/test_docs_paths_resolve.py`).

Not checked:

- Nothing ran on Temporal. No workflow was started and no history inspected.
- Statements the gatherers marked INFERRED or NOT VERIFIED stay so; each file lists its own.
- The external projects were read, not run.
- The probe shows the mechanism, not an implementation: no child workflows, no limits on answers, no error paths beyond those listed.

## Appendix A. The probe

The full script, how to run it and its complete output are in [PROBE.md](PROBE.md). It was run with the server virtualenv's Python in isolated mode (`-I`), importing only `pydantic_monty`. The step function, trimmed to the branches that ran:

```python
def step(code, recorded):
    m = pm.Monty(code, type_check=True, type_check_stubs=STUBS)
    p = m.start(limits={"max_duration_secs": 2.0, "max_memory": 64 * 1024 * 1024})
    seen = {}
    while True:
        if isinstance(p, pm.MontyComplete):
            return {"status": "done", "output": p.output}
        if isinstance(p, pm.FunctionSnapshot):          # the script called a sub-agent
            seen[p.call_id] = {"name": str(p.function_name), "kwargs": dict(p.kwargs)}
            p = p.resume({"future": ...})               # let the script go on without waiting
        elif isinstance(p, pm.FutureSnapshot):          # the script is blocked on answers
            ids = list(p.pending_call_ids)
            if all(i in recorded for i in ids):
                p = p.resume({i: {"return_value": recorded[i]} for i in ids})
            else:
                return {"status": "pending",
                        "pending": {i: c for i, c in seen.items() if i not in recorded}}
```

The script and the signatures it was checked against:

```python
import asyncio

reports = await asyncio.gather(
    reviewer(task="Review auth.py"),
    reviewer(task="Review parser.py"),
)
summary = await summarizer(task="Summarize: " + " | ".join(reports))
{"summary": summary, "reviews": len(reports)}
```

```python
async def reviewer(*, task: str) -> str:
    raise NotImplementedError()

async def summarizer(*, task: str) -> str:
    raise NotImplementedError()
```

Output, shortened:

```text
pydantic_monty 0.0.18
step 1 (18.6 ms): pending 0: reviewer(task="Review auth.py"), 1: reviewer(task="Review parser.py")
step 2 (0.3 ms):  pending 2: summarizer(task="Summarize: <answer 0> | <answer 1>")
step 3 (0.1 ms):  done {"summary": "<answer 2>", "reviews": 2}
re-run equal: True
type check [misspelled]: refused -> error[unresolved-reference]: Name `reviwer` used when not defined
type check [positional]: refused -> error[missing-argument]: No argument provided for required parameter `task` of function `reviewer`
type check [wrong type]: refused -> error[invalid-argument-type]: Argument to function `reviewer` is incorrect
runaway: stopped after 2000 ms -> MontyRuntimeError: TimeoutError: time limit exceeded
clock: refused -> ModuleNotFoundError: No module named 'time'
random: refused -> ModuleNotFoundError: No module named 'random'
datetime.now: reached the host as a call (the host must refuse it)
```
