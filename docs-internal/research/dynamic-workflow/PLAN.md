# Dynamic workflow: plan

> **Status: Planned.** Written 2026-11-12, updated 2026-11-19. Nothing here is built, and no build has been asked for. The research behind it is in this folder; [CROSS-REFERENCE.md](CROSS-REFERENCE.md) is the result.

## Where it stands

- **Research: done.** Six source digests, six code maps, the cross-reference, the findings list and the probe are written and pass the docs lint.
- **Added 2026-11-19:** Claude Code's Code Mode ([sources/06](sources/06-claude-code-code-mode.md)) and section 10 of the cross-reference. It changes no recommendation and no step below.
- **Decided: nothing new.** The owner's decision of 2026-11-11 stands: OpenCompany "takes no pydantic-ai dependency and builds no Code Mode feature" ([temporal_production_practices.md](../../temporal_production_practices.md)).
- **Built: nothing.** No code was written. The research itself was committed on 2026-11-19.
- **Recommendation on record** (the lead's; the owner has not ruled): do not run a model-written script in Temporal workflow code. Run it in short activities that re-run it against recorded results, and let workflow code only start the sub-agent runs. [CROSS-REFERENCE.md](CROSS-REFERENCE.md), section 4.

## Next: the owner's answers

The options and what each assumes are in [CROSS-REFERENCE.md](CROSS-REFERENCE.md), section 8.

| # | Question | Recommended |
|---|---|---|
| 1 | Does the 2026-11-11 decision cover a dynamic workflow? | Dynamic workflow only; Code Mode stays out |
| 2 | First step | The prompt-level plan first, the script tool later |
| 3 | Who gets the script tool first | Dev-mode agents that have agents wired as tools |
| 4 | Sub-agent runs started by a script | Isolated: no stored conversation loaded or saved |
| 5 | Sandbox version | Move to Monty 1.x before building on it |
| 6 | Waiting or queued | The tool call waits for the script |

Questions 1 and 2 gate everything. Questions 3 to 6 matter only if the script tool is built.

On question 1, the other answer is to reopen Code Mode as well. That would also need four rulings: where the code runs, results passed by reference, a call budget, and what a held call does inside a script ([CROSS-REFERENCE.md](CROSS-REFERENCE.md), sections 10.5 and 10.6).

## Then, depending on the answers

### Path A: the prompt-level plan (no workflow code, no release needed)

1. Write the delegation directive and the plan format (roles, what each teammate must return, the call order) for one Dev-mode agent that has agents wired as tools. Source: [sources/04](sources/04-sakana-mass.md), sections 3.4 and 3.7.
2. Settle the conflicting prompt block. A non-lead agent with a delegate tool is told never to call `delegate_to_*` directly ([FINDINGS.md](FINDINGS.md), item 8). The fix is a prompt change in the prepare step; it changes no workflow command.
3. With the owner's go-ahead, try it on one real workflow in the dev app. Record whether the agent delegates, how many turns it takes and what it costs.
4. Decide from that whether the script tool is worth building.

### Path B: the script tool (workflow code, needs a release)

In this order, one commit per step:

1. Answers to questions 3 to 6, then a one-page build plan from [CROSS-REFERENCE.md](CROSS-REFERENCE.md), section 5.
2. Monty, per question 5. If it moves to 1.x, the `montyExecutor` node moves first, on its own.
3. The step activity in its plugin folder, with tests. No workflow code yet.
4. The new workflow type, with real-SDK tests, taking part in Stop and Resume.
5. The data-driven branch in the agent workflow, behind a generation flag. The flag is read in one release and written in the next. A golden entry and one recorded replay scenario go with it.
6. The two central table rows, the node's documentation card and the tool-name snapshot.

The rules each step must keep are in [analysis/04](analysis/04-deployment-control-and-durability-rules.md), section 5.

### Not planned

Code Mode. MASS's search loop and fine-tuning. Wiring for hired teams. Nesting, saved scripts, a background mode, typed sub-agent outputs.

## Separate from this plan

[FINDINGS.md](FINDINGS.md) lists sixteen possible defects, read from code and not reproduced. Two that a user could hit: item 1 (a team task with an unmet dependency never unblocks) and item 9 (the pinned Monty runs model-written code inside the backend process). Neither is scheduled.

## To resume after a break

- Read [README.md](README.md), then [CROSS-REFERENCE.md](CROSS-REFERENCE.md) sections 1, 4, 5 and 8.
- The code maps cite commit 908a8ce6. By commit 03327c5b (2026-11-19), 54 files under `server/services`, `server/nodes` and `server/core` had changed. The line numbers in [CROSS-REFERENCE.md](CROSS-REFERENCE.md) were checked again that day. The code maps and [FINDINGS.md](FINDINGS.md) were not: find a symbol by name before relying on a line number there.
- The probe and how to run it are in [PROBE.md](PROBE.md).
- The docs lint for this folder: `server/.venv/Scripts/python.exe -m pytest cli/tests/test_docs_paths_resolve.py -k dynamic-workflow -q -p no:cacheprovider`, run from the repository root.
