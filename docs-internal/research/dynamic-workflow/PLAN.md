# Dynamic workflow: plan

> **Status: Planned.** Written 2026-11-12, updated 2026-11-23. Nothing here is built, and no build has been asked for. The research behind it is in this folder; [CROSS-REFERENCE.md](CROSS-REFERENCE.md) is the result.

## Where it stands

- **Research: done.** Six source digests, six code maps, the cross-reference, the findings list and the probe are written and pass the docs lint.
- **Added 2026-11-19:** Claude Code's Code Mode ([sources/06](sources/06-claude-code-code-mode.md)) and section 10 of the cross-reference. It changed no recommendation.
- **Checked again 2026-11-23** against commit cfb06d6c, 94 commits later ([CROSS-REFERENCE.md](CROSS-REFERENCE.md), section 11). Since 2026-11-20 hired employees delegate through reusable agent instances: a teammate starts in its own workflow and its answer arrives later. Plain Dev-mode agents keep the direct delegation the original build was written for. The placement in section 4 holds, and Pydantic has since moved the same way for its remote Code Mode.
- **Decided: nothing new.** The owner's decision of 2026-11-11 stands: OpenCompany "takes no pydantic-ai dependency and builds no Code Mode feature" ([temporal_production_practices.md](../../temporal_production_practices.md)).
- **Built: nothing.** No code was written.
- **Recommendation on record** (the lead's; the owner has not ruled):
  - Do not run a model-written script in Temporal workflow code. Run it in short activities that re-run it against recorded results ([CROSS-REFERENCE.md](CROSS-REFERENCE.md), section 4).
  - Revised 2026-11-23: if a script tool is built, build it for the agent instance runtime, where hired employees work and where isolation, permits, review and a durable inbox already exist. The section 5 build stays as the shape of the sandbox and the step activity, not of the workflow that runs the teammates (section 11.5).

## Next: the owner's answers

The options and what each assumes are in [CROSS-REFERENCE.md](CROSS-REFERENCE.md), sections 8 and 11.5.

| # | Question | Recommended |
|---|---|---|
| 0 | Which runtime a script tool serves (new 2026-11-23) | Hired employees, on the agent instance runtime |
| 1 | Does the 2026-11-11 decision cover a dynamic workflow? | Dynamic workflow only; Code Mode stays out. Weigh that RLM code inside an instance already starts agents |
| 2 | First step | The prompt-level plan first, the script tool later |
| 3 | Who gets the script tool first | Merged into question 0 |
| 4 | Sub-agent runs started by a script | Isolated. On instances that is already so: each spawn is a fresh instance with its own transcript |
| 5 | Sandbox version | Move to Monty 1.x before building on it, pinned to one minor version |
| 6 | Waiting or queued | On instances, queued with the result through the inbox, as their tools already work. On direct delegation, the call waits |

Questions 0 to 2 gate everything. Questions 4 to 6 matter only if the script tool is built.

On question 1, the other answer is to reopen Code Mode as well. That would also need four rulings: where the code runs, results passed by reference, a call budget, and what a held call does inside a script ([CROSS-REFERENCE.md](CROSS-REFERENCE.md), sections 10.5 and 10.6).

## Then, depending on the answers

### Path A: the prompt-level plan (no workflow code, no release needed)

It reaches only plain Dev-mode agents with agents wired as tools. A hired team's Orchestrator already gets its delegation instructions from its saved prompt.

1. Write the delegation directive and the plan format (roles, what each teammate must return, the call order) for one such agent. Source: [sources/04](sources/04-sakana-mass.md), sections 3.4 and 3.7.
2. Settle the conflicting prompt block. An agent with `delegate_to_*` tools that is not an instance is told never to call them ([FINDINGS.md](FINDINGS.md), item 8, still there on 2026-11-23). The fix is a prompt change in the prepare step; it changes no workflow command.
3. With the owner's go-ahead, try it on one real workflow in the dev app. Record whether the agent delegates, how many turns it takes and what it costs.
4. Decide from that whether the script tool is worth building.

### Path B: the script tool (workflow code, needs a release)

Written on 2026-11-12 for direct delegation. If question 0 picks the instance runtime, steps 4 and 5 change: no new workflow type starts `AgentWorkflow` children; the script's functions become spawns and assignments, and two pieces are added instead, a wait for submitted task results and a ruling on review ([CROSS-REFERENCE.md](CROSS-REFERENCE.md), section 11.3).

In this order, one commit per step:

1. Answers to questions 0 and 4 to 6, then a one-page build plan.
2. Monty, per question 5. If it moves to 1.x, the `montyExecutor` node moves first, on its own.
3. The step activity in its plugin folder, with tests. No workflow code yet.
4. The workflow code that runs a script, with real-SDK tests, taking part in Stop, Resume and the budget pause.
5. The data-driven branch in the agent workflow, behind a generation flag. Section 5 said the flag is read in one release and written in the next; the three flags added since 2026-11-19 were read and written in the same release, so the owner should say which rule applies. A golden entry and one recorded replay scenario go with it.
6. The two central table rows, the node's documentation card and the tool-name snapshot.

The rules each step must keep are in [analysis/04](analysis/04-deployment-control-and-durability-rules.md), section 5, with the new ones in [CROSS-REFERENCE.md](CROSS-REFERENCE.md), section 11.3 item 4.

### Not planned

Code Mode. MASS's search loop and fine-tuning. Nesting, saved scripts, a background mode, typed sub-agent outputs.

## Separate from this plan

[FINDINGS.md](FINDINGS.md) lists possible defects, read from code and not reproduced. Those a user could hit, as of 2026-11-23:

- Item 9: the pinned Monty runs model-written code inside the backend process.
- Item 1: a team task with an unmet dependency never unblocks. Fixed for generations started since 2026-11-20; still there in older ones.
- New item A: Stop can be acknowledged while a Claude Code, Codex, RLM or Gemini CLI agent instance is still running its turn.

None is scheduled.

## To resume after a break

- Read [README.md](README.md), then [CROSS-REFERENCE.md](CROSS-REFERENCE.md) sections 1, 4, 5, 8 and 11.
- The code maps cite commit 908a8ce6. By commit cfb06d6c (2026-11-23), 187 files under `server/services`, `server/nodes` and `server/core` had changed, and the map of delegation and teams ([analysis/03](analysis/03-delegation-and-teams.md)) now describes the older runtime only. The line numbers in [CROSS-REFERENCE.md](CROSS-REFERENCE.md) and every item in [FINDINGS.md](FINDINGS.md) were checked at cfb06d6c. The code maps were not: find a symbol by name before relying on a line number there.
- The probe and how to run it are in [PROBE.md](PROBE.md).
- The docs lint for this folder: `server/.venv/Scripts/python.exe -m pytest cli/tests/test_docs_paths_resolve.py -k dynamic-workflow -q -p no:cacheprovider`, run from the repository root.
