# Sakana AI MASS: Multi-Agent Self-Supervision (digest)
> **Status: Prototype reference.** External source digest: this file describes Sakana AI's MASS, not OpenCompany. Paths, names and commands in it belong to that project.

Written 2026-11-12. Nothing from the sources was executed, built or installed. Code behaviour is described from reading the source. Every load-bearing claim carries a source tag (see the table). INFERRED marks a conclusion drawn from the sources that they do not state themselves. NOT VERIFIED marks something that could not be checked.

## Sources read

| Tag | Source | URL | Fetched | Version | Licence |
|---|---|---|---|---|---|
| Page | Sakana AI publication page, "Multi-Agent Self-Supervision" | https://pub.sakana.ai/mass/ | 2026-11-12 | "Published October 2026" | None stated on the page |
| Paper | Lee, Xu, Seely, Lee, Sojoudi, Zaharia, Tang, "Recursive Self-Improvement through Multi-Agent Self-Supervision" | https://arxiv.org/abs/2610.12176 (full text read at https://arxiv.org/html/2610.12176v1) | 2026-11-12 | arXiv v1, submitted 8 Oct 2026, 39 pages | CC BY 4.0 |
| Repo | SakanaAI/mass | https://github.com/SakanaAI/mass | 2026-11-12, shallow clone | commit `fb403581bed2c974b3f99e7236224169da643079` ("Fix typo", 2026-10-09 UTC), branch main | No licence assigned |
| Blog | Hyunin Lee, "Recursive Harness Self-Improvement: Reorganizing Multi-Agent Systems" | https://hyunin-lee.github.io/Recursive-Harness-Self-Improvement/ | 2026-11-12 | dated June 28, 2026 | None stated |
| QC | qwen-code documentation at tag v0.20.0 (supplementary, read only to pin down the `agent` tool) | https://github.com/QwenLM/qwen-code/blob/v0.20.0/docs/developers/tools/task.md and https://github.com/QwenLM/qwen-code/blob/v0.20.0/docs/users/features/sub-agents.md | 2026-11-12 | tag v0.20.0, commit `92fda5603e84ef62a1b29bf6faf4f6a8124a2bf7` | Apache-2.0 |

Coverage and caveats:

- Page: read in full, including the hover texts of the interactive figure (taken from the raw HTML).
- Paper: all sections and Appendices A to J read in the HTML rendering. Plots are images; only captions and tables were read, so curve values that appear only in plots are not in this digest.
- Repo: `NOTICE.md` says "No public open-source license has been assigned to the MASS-specific code in this repository", and the GitHub API reports no licence. The repository was created 2026-09-29 and has 14 commits on main. Read in full: `README.md`, `NOTICE.md`, `docs/*`, `configs/*`, `mass/*`, `runtime/*`, `harness_improvement/*`, `evaluation/bundle.py`, `evaluation/artifacts.py`, `evaluation_claudecodex/*`, `training/prepare_sft_data.py`, `tasks/README.md`, `tasks/splits.json`, `tasks/teacher_directive.txt`, two initial workflows, `tests/*`, `benchmarks/README.md`. Read in outline only: `training/train_sft_lora.py`, `benchmarks/scripts/bench_qwen_code_agent.py`. Not read: `training/merge_lora.py`, `training/quantize_fp8_blockwise.py`, `evaluation/openai_responses.py`, `evaluation/responses_metrics.py`, the other benchmark scripts, `tools/check_source.py`, `tools/make_source_archive.py`. Repo citations below are `path:lines` at the commit above.
- Blog: read in full. It describes RHI (arXiv 2607.15524), the earlier method that MASS uses as its inner loop. The RHI paper itself was not read.
- QC: `task.md` read in full; `sub-agents.md` overview, security and limits sections only.

## 1. What it is

MASS is a research method for recursive self-improvement (RSI) of a language model on tasks that have no automatic checker. One model fills three roles: the agent that solves the task, the evaluator that compares two results, and the optimizer that rewrites the team plan (Page; Paper sections 1 to 3). It has two nested loops:

- Inner loop, weights frozen. For each task, search for a better multi-agent workflow: execute it, compare the result, rewrite the workflow text. This is the RHI method from the same group (Paper section 6 and Table 15; Blog; Repo `README.md:25-28`).
- Outer loop, weights change. Run the best workflow many times, let the model rank the results, and fine-tune the model on the best trajectories with the workflow text removed from the prompt. The new model takes all three roles and the cycle repeats (Paper Algorithms 1 and 3).

The reported experiment runs two cycles from Qwen3.6-27B inside the qwen-code 0.20.0 coding agent, on 12 synthetic research tasks (Paper section 4.1).

The point that separates MASS from script-based dynamic workflows: a workflow here is not a program. It is a block of text appended to the task prompt that describes a team: roles, instructions, output contracts and call order. An ordinary coding agent reads it and acts as orchestrator by calling its built-in subagent tool. The blog states the contrast directly: "Instead of treating the harness as an executable program, we treat it as a prompt-level object: a structured piece of text appended to the task prompt." It names two advantages: it works with black-box models, and the text is cheap to revise.

Sections 3 to 5 cover what works without any model training (representation, execution, search). Section 6 covers the training half.

## 2. Vocabulary

| Term | Meaning in the sources |
|---|---|
| Workflow | "a written plan for how a multi-agent team should work together", added to the task prompt (Page). Formally `w_t := {h_t, (r_tj, u_tj, c_tj) for j = 1..m_t}` with `m_t` subagents (Paper section 3). The repo and blog call it the "harness" or the "multi agent design". |
| Role (`r`) | "the responsibility assigned to each subagent, such as preparing data" (Page). |
| Instruction (`u`) | "the work that subagent should carry out" (Page). The figure hover text adds: "It travels from the orchestrator to the subagent." |
| Contract (`c`) | "the information and artifacts it must return, including any required checks" (Page). It flows from the subagent back to the orchestrator. |
| Hop (`h`) | "the order of subagent calls, including any return to an earlier step" (Page). One hop sequence per workflow, shared by all its agents (Paper App. F.1). |
| Orchestrator | The main agent conversation. It "assigns work to subagents and integrates their results" (Page). |
| Subagent, worker | A separate agent conversation started by the orchestrator through the agent tool. The paper and repo also say "worker". Orchestrator and subagents use the same model (Paper section 3). |
| Workspace (`y`) | The directory an execution leaves behind: code, results, plots and a research report (Page; Paper section 2). It is what the evaluator judges. |
| Champion | "the best workflow found so far according to the current model's judgments" (Page). The paper says "retained workflow" (`w*`) and "crown workflow"; the repo writes it to `retained_workflow.json`. |
| Reference | The workspace produced by the same model from the bare task with no workflow. The repo also calls it the "no-harness baseline" (Repo `docs/paper_to_code.md:9`; `harness_improvement/iterate_multi_agent_prompt.py:1276-1291`). |
| Trajectory (`tau`) | "the messages, tool calls, and tool results of the orchestrator and its subagents" for one execution (Page). |
| Teacher | A selected, workflow-prompted execution used as training data (Paper App. B.1). |
| Execution directive | Seven fixed rules appended after the workflow during teacher collection that force real subagent calls (Paper App. B.3). |
| Bare prompt | The task prompt with no workflow and no directive. |
| Homogeneous RSI | RSI "in which the model being improved (optimizee) also serves as its own optimizer and evaluator" (Page). |
| Non-verifiable task | A task where "an automatic rule-based signal is not available and that only a self-evaluation signal (whose correctness is unknown) guides the overall learning loop" (Page). |
| RHI | Recursive Harness Self-Improvement, the workflow-search method (Blog; Paper Table 15). |
| `L(0)`, `L(1)`, `L(2)` | The base model and the models after one and two MASS cycles. Config files call them `L0`, `L1`, `L2`. |

## 3. The workflow representation

### 3.1 Formal shape

The paper's simplified format (Paper section 3, box "Multi-agent workflow w", rewritten in plain text):

```
orchestrator:  h = {subagent_2 -> subagent_m -> ... -> subagent_1}
subagent_1:    {role: r_1, instruction: u_1, output contract: c_1}
...
subagent_m:    {role: r_m, instruction: u_m, output contract: c_m}
```

The workflow is concatenated to the task prompt and the coding agent is run on the result: `A(x_t (+) w_t; L_agent)` (Paper section 3).

### 3.2 How it is serialized into the prompt

The prompt is one text file with three parts in this order (Repo `harness_improvement/iterate_multi_agent_prompt.py:35-98`, `tasks/initial/query307_ourTeam.txt:1-38`):

1. The task text: the assignment, data sources and a numbered "Deliverables" list. The repo stores it on one line with literal `\n` escapes.
2. The design block. It starts with the fixed marker sentence `Create an agent team with following agent candidates to solve this problem:` and continues with a JSON-style list of agent objects. This block is the workflow. It is the only part the optimizer may change.
3. A fixed suffix starting with `Use uv for all Python workflows`. It asks the agent to keep a `logs.txt` history, and in workflow prompts adds a second sentence asking it to document "all created and spawned agents" and the workflow between them.

The two markers are the split points: everything before the start marker is the task, everything from the last occurrence of the end marker on is the suffix (Repo `harness_improvement/iterate_multi_agent_prompt.py:67-82`).

### 3.3 The initial workflow (version 0)

Every task ships with a starting workflow, which the driver's help text calls "the human-written starting point" (Repo `harness_improvement/iterate_multi_agent_prompt.py:1302-1311`). It has only three keys per agent: `agent_id`, `role`, `instruction`. It has no contract fields and no call order. Finance tasks start with 4 agents and pharmacy and robotics tasks with 6, counting the orchestrator (Repo `tasks/initial/*`; counted by key). The paper confirms that the initial workflow "has no contracts or hops" (Paper App. F.1). One entry, verbatim, with the marker and the suffix (Repo `tasks/initial/query307_ourTeam.txt:3-4, 25-29, 37-38`):

```
Create an agent team with following agent candidates to solve this problem: 
[
  ...
  {
    "agent_id": "agent_dataset_pipeline",
    "role": "Dataset & Simulation Pipeline Engineering",
    "instruction": "You specialize in building data collection pipelines, curating datasets, and integrating simulation environments for robotics training. Plan data workflows, generate code for data processing, validation, and simulation interfacing, reflect on data quality and coverage, and iterate. Return pipeline architectures and dataset schemas to the orchestrator."
  },
  ...
]

Use uv for all Python workflows—run code with uv run, install dependencies with uv add, use uvx for tools. write your history log: write your plan, execution (or tool-execution), reflection during the reasoning logs in a logs.txt file.
In logs.txt, Document all created and spawned agents, describe the workflow between agents (e.g., as a structured outline or diagram), track which agents were executed and their roles in the process 
```

### 3.4 An evolved workflow: task 51, version 10

The paper prints two complete retained workflows. Task 51 is a finance task (macro signals and a trading backtest). Its retained path through search iteration 11 was `w0 -> w1 -> w8 -> w10` (Paper App. I). Version 10 has seven agents: orchestrator, data pipeline, feature engineer, modeling specialist, backtest validator, research auditor, report synthesizer.

The hop lives inside the orchestrator entry's instruction. Its opening, verbatim (Paper App. I.1, as rendered in the arXiv HTML):

```
"agent_id": "agent_orchestrator",
"role": "DAG Conductor & Iterative Reconciliation Engine",
"instruction": "You manage a 7-hop state-machine workflow with explicit validation gates, parallel specialist decomposition, and iterative recall/re-delegation loops. You will: 1) Execute the spine: (Hop 1) Data/Calendar -> (Hop 2) Features/Leakage Audit -> (Hop 3) Modeling/Walk-Forward -> (Hop 4) Backtest/Ablation -> (Hop 5) Auditor Cross-Check -> (Hop 6) Iterative Reconciliation -> (Hop 7) Report/Manifest. 2) Enforce strict JSON schema validation on every subagent output. Reject any output containing NaN, Inf, or degenerate values (e.g., regime_accuracy==1.0, confusion_matrix=[[N]], IC==RankIC, ablation_delta_sharpe>0). 3) Implement explicit recall routing: if a gate fails, issue a RECALL command with {target_agent, failed_checks, error_context, narrowed_scope, acceptance_criteria, code_patch_hint, max_retries: 3}. Route feedback explicitly: e.g., if Backtest reports turnover<0.01 or degenerate Sharpe, RECALL Modeling to adjust signal thresholds/risk targeting, then RE-DELEGATE to Backtest. ..."
```

One complete subagent entry, verbatim (Paper App. I.1). This is the entry the publication page condenses into its "Data specialist's output contract" example:

```
{
"agent_id": "agent_data_pipeline",
"role": "Multi-Source Ingestion & Temporal Alignment Specialist",
"instruction": "You handle FRED, Yahoo, and calendar ingestion in parallel. Output MUST follow the strict contract. You must ensure strict business-day alignment, enforce forward-fill limits (max 30 days), and verify calendar dates match release patterns. CRITICAL: Define explicit train/val/test split boundaries ensuring NO overlap (train_end < val_start < test_start) and sufficient pre/post-2020 coverage (>300 days each). If alignment gaps > 5% or calendar coverage < 80%, self-correct or flag for RECALL. Provide explicit `alignment_diagnostics` for downstream feature engineering. Generate Python code for data fetching/caching with parallel threads/processes.",
"input_dependencies": [],
"output_contract": {
"status": "success|failed|stalled|recalling",
"artifacts": ["cache/*.parquet", "src/data/*.py"],
"metrics": {"date_range": {"start": "str", "end": "str"}, "missingness_pct": {"fred": "float", "yahoo": "float"}, "calendar_events": "int", "split_days": {"train": "int", "val": "int", "test": "int", "pre_2020_test_days": "int", "post_2020_test_days": "int"}},
"validation_report": {"timestamps_aligned": "bool", "no_future_leakage": "bool", "calendar_coverage_above_threshold": "bool", "split_boundaries_valid": "bool", "train_end_before_val_start": "bool", "val_end_before_test_start": "bool", "subperiod_coverage_sufficient": "bool", "error_details": "str"},
"orchestrator_feedback": "str",
"cross_agent_context": {"aligned_data_shape": ["int", "int"], "split_indices": {"train": ["int"], "val": ["int"], "test": ["int"]}, "business_day_index": "list[str]"}
}
},
```

Keys the optimizer added in this workflow, beyond the three in version 0 (Paper App. I.1):

- `input_dependencies`: a list of upstream outputs, for example `["agent_data_pipeline.output_contract"]` or `["agent_modeling_specialist.output_contract", "agent_feature_engineer.cross_agent_context.ablation_masks"]`.
- `output_contract`: an object of typed placeholders. Most worker contracts carry `status`, `artifacts` (file globs), `metrics`, `validation_report` (named boolean checks plus `error_details`), `orchestrator_feedback`, and `cross_agent_context` (what the next agents need). The auditor's contract adds `critique`, `recall_commands` and `stall_flags`. The orchestrator's own contract has `status`, `pipeline_state`, `recall_commands` and `cross_agent_context_updates`.

The page explains why this contract matters: it requires the data specialist to check for look-ahead leakage and report whether the time splits are valid, and "tells later agents what evidence they should receive before proceeding" (Page).

### 3.5 A second shape: task 202, version 8

The other printed workflow (pharmacy, protein-interface prediction, retained path `w0 -> w8`) keeps only the three original keys. The contract, the acceptance criteria and the recall rule are written inside the instruction string. One entry, verbatim (Paper App. I.2):

```
{
"agent_id": "agent_graph_builder",
"role": "Graph Construction & Family Split Specialist",
"instruction": "Build residue graphs and generate family splits. OUTPUT TO ORCHESTRATOR SCHEMA: src/build_graphs.py, data/family_split.json (Schema: {\"train_families\": [\"str\"], \"test_families\": [\"str\"], \"zero_overlap_verified\": true, \"family_mapping\": {\"pdb_id\": \"family_name\"}}). ACCEPTANCE CRITERIA: Test split must contain ≥3 distinct families with zero sequence/structure overlap. RECALL & RE-DELEGATION: If recalled for overlap or missing families, recompute splits and rewrite family_split.json. Do not touch download scripts."
},
```

In this workflow the orchestrator's instruction lists five phases, tells it to dispatch a validator agent after each phase, parse `validation_report.json`, and on `overall: FAIL` recall the failed agent with the exact failure trace, a narrowed scope and a fallback strategy. It also carries a stall rule: if an agent repeats the same tool call more than twice without new files or changed arguments, interrupt and re-delegate with a narrower scope and a mandatory strategy change (Paper App. I.2).

### 3.6 What is fixed and what is free

- The only structural check on a proposed workflow is that it starts with the marker sentence. The driver then trims any copied prompt tail and writes the text. It does not parse the JSON, validate keys, or check that an orchestrator entry exists (Repo `harness_improvement/iterate_multi_agent_prompt.py:926-961, 2405-2459`).
- Two optional validators exist and are off in the packaged configuration: a size cap (`--max-design-chars`) and a regex rejection of numeric caps on agents, recalls, hops, turns or duration (`--forbid-fixed-agent-limits`) (Repo `harness_improvement/iterate_multi_agent_prompt.py:964-985, 2411-2454`; flags passed by `mass/pipeline.py:112-125`).
- The hop has no dedicated field. It is prose in the orchestrator's instruction, plus `input_dependencies` when the optimizer chose to add them. The information analysis in the paper had to extract contracts "from their JSON fields or ... from the instruction when embedded there" (Paper App. F.1).
- Thresholds inside workflows (in the task 51 example: "Sharpe > 10", "max 30 days", "max_retries: 3") were invented by the optimizer. The paper says they "are optimizer proposals, not additional benchmark requirements or validated scientific criteria" (Paper App. I.1).
- The abstract says the search is "constrained by structural guardrails". The paper does not define the phrase elsewhere. INFERRED: it refers to the fixed task text and suffix, the marker, and the optional validators above.

### 3.7 The execution directive

During teacher collection a fixed block headed `## Execution requirements for the agent team (mandatory)` is appended after the workflow. The paper reproduces it verbatim (Paper App. B.3; Repo `tasks/teacher_directive.txt:1-15`). Its seven rules:

1. "Run the team for real." Every non-orchestrator role "must be executed as a separate sub-agent through the `agent` tool (subagent_type "general-purpose"). Simulating a role in your own turn does not count." The orchestrator does not write pipeline or analysis code itself.
2. "Foreground only." Every call sets `run_in_background: false` so the report returns before the orchestrator continues.
3. "One stage at a time." Start a worker only when its inputs exist on disk. Several workers in one turn only if their tasks are independent and they write disjoint files.
4. "Self-contained instructions." "A worker sees nothing but your prompt and the filesystem." Each prompt must include the role's instruction from the team definition, the absolute workspace path, the exact files to create or modify, the interfaces or artifacts from earlier stages, the command(s) to verify the work, and the report expected back (files written, commands run with results, key numbers, known problems).
5. "Verify before you build on it." Check each worker's output yourself. On failure, "re-delegate to the same role with a corrected, narrower instruction instead of fixing it yourself."
6. "Bounded work." One deliverable per worker call: a handful of files and one verification command. Split large roles into several sequential calls.
7. "Finish cleanly." Do not end while a worker is running. The final answer summarizes every worker call.

The directive is absent from bare-prompt evaluation and is removed from the training input together with the workflow (Paper App. A.2, B.3).

## 4. How a workflow is executed

### 4.1 Environment

- The agent is qwen-code 0.20.0, an open-source coding-agent CLI, run headless. Its "harness, including tools and memory, is fixed throughout the paper" (Paper sections 2 and 4.1).
- One episode is one `qwen` process. The prompt file is piped to standard input with `--approval-mode yolo --output-format stream-json`, and the process runs with the workspace as its working directory (Repo `runtime/run_episode.sh:171-190`).
- The workspace is that working directory. It starts empty, the orchestrator and every subagent of the episode read and write it, and what remains at the end is the result that gets judged (Repo `runtime/run_episode.sh:45-48`; Paper section 2).
- The model is served locally through vLLM as an OpenAI-compatible endpoint (Paper App. B.1; Repo `docs/running.md:76-81`).
- Per-episode settings written by the runner: temperature 0.6, top-p 0.95, top-k 20, at most 32,768 output tokens per request, a 262,144-token context window, thinking enabled, a per-episode seed, no request retries, a 30-minute HTTP timeout, a 10-minute default shell timeout (Repo `runtime/run_episode.sh:112-163`).
- There is no sandbox for the synthetic tasks. The docs warn that qwen-code "runs generated shell commands" in yolo mode and that the runner "uses the current host environment ... It does not create a security sandbox" (Repo `docs/running.md:13-16`). Public benchmark runs use Docker containers.

### 4.2 The orchestrator

The orchestrator is the main qwen-code conversation. Its system prompt is qwen-code's own (it begins "You are Qwen Code"; Repo `training/prepare_sft_data.py:182-188`). The first user message is the whole prompt: task, workflow, suffix, and the directive when present. Nothing else tells it that it is an orchestrator. It reads the plan and decides turn by turn which tool to call.

### 4.3 How subagents are called

Subagents are started with qwen-code's built-in `agent` tool. The MASS repo adds no orchestration code of its own (INFERRED from reading `runtime/` and `mass/`: the runner only launches `qwen` and a logging proxy). From the qwen-code v0.20.0 documentation (QC `task.md`):

- Arguments: `description` (short label), `prompt` (the full task for the subagent), `subagent_type` (defaults to `general-purpose`), `run_in_background` (defaults to true for top-level agents; false waits for the result inline), `isolation` (optional git worktree).
- The subagent receives the prompt, works with its own tools, and returns "a final result message when run in the foreground".
- "Stateless execution: Each subagent invocation is independent with no memory of previous executions." "Single communication: Subagents provide one final result message - no ongoing communication."
- Subagents "maintain their own conversation history, separate from your main chat" and inherit the parent's permission mode by default (QC `sub-agents.md`).

What this means for MASS (Paper App. B.3; Repo `training/prepare_sft_data.py:91-119, 182-188`):

- A subagent gets only the orchestrator-written prompt and the shared filesystem. Its system prompt begins "You are a general-purpose subagent". It does not see the workflow, the orchestrator's history or other subagents' reports unless the orchestrator copies them into the prompt.
- It returns one report. The directive asks for files written, commands run with results, key numbers and known problems. The workflow's contract tells the orchestrator what to ask for.
- All model requests of an episode, from the orchestrator and from every subagent, go to the same endpoint. The trajectory recorder separates them afterwards by system prompt and first user message.
- Whether qwen-code runs a subagent in the same OS process or a child process is NOT VERIFIED. The documentation describes it only as a tool call.

### 4.4 Contracts are instructions, not enforced

Nothing outside the model checks a contract. No code in the repo parses a subagent's report or validates it against `output_contract`. Enforcement is itself an instruction to the orchestrator, for example "Enforce strict JSON schema validation on every subagent output" in the task 51 workflow, and rule 5 of the directive. The paper is explicit about the limit:

- "These are instructions for execution; their presence does not establish that every run performs the checks correctly." (Paper App. I)
- The behavioural audit counts structure only: "a returned worker can report failure, a read need not use the interface correctly, and an edit need not fix a problem. Five of M+'s 281 worker returns contain no model-visible report." (Paper App. G.1)
- One audited teacher revision ran a diagnostic that "incorrectly treats NaN correlations as passing" (Paper App. G.2).

### 4.5 Hops and loops back

The orchestrator follows the hop text by making `agent` calls in that order. A return to an earlier step (a "recall") is a new `agent` call to the same role with a narrower instruction. Because subagents are stateless, the recalled worker starts from the new prompt and the files on disk. State that must survive between hops lives in two places: files in the workspace (some workflows name them, such as `pipeline_state.json` or `validation_report.json`) and the orchestrator's own context (Paper App. B.3, I.1, I.2; QC `task.md`).

An example recorded in the paper: on task 51 an auditor agent found that features used post-event returns overlapping the prediction targets; the orchestrator recalled the feature worker to remove them and widen the embargo; the worker edited the code, regenerated features and ran checks (Paper App. G.2).

### 4.6 Parallelism

The directive allows several workers in one turn only when their tasks are independent and they write disjoint files, and forces foreground calls (rules 2 and 3). qwen-code's default for top-level agents is background execution, so the directive overrides the tool default (QC `task.md`). Both printed workflows also ask single agents to do parallel work inside their own run, for example "ingestion in parallel" and "Train all three variants in parallel" (Paper App. I.1, I.2).

### 4.7 Does the model actually delegate?

A workflow in the prompt does not guarantee subagent calls.

- In the packaged driver, search-time executions get the task, workflow and suffix only. The directive is appended only for teacher collection (Repo `mass/pipeline.py:96-107` against `:145-150`). The paper likewise states the directive for teacher collection and does not mention it for search (Paper App. B.1, B.3).
- The paper's single-agent comparison pool consists of base-model episodes that "receive a workflow without the execution directive and make no worker calls" (Paper App. D.3). So workflow-only prompts did produce runs with no delegation. How often is not reported. NOT VERIFIED.
- With the directive, delegation is universal: all 114 audited teacher episodes delegate, 98 contain a code handoff between workers, 57 contain a worker revising another worker's code (Paper Table 14).
- A generic directive that supplies no workflow ("choose three to six specialist roles itself" plus rules 2 to 7) also makes the untrained base model delegate in 110 of 110 episodes, with code handoffs in 103 (Paper App. C.4, Table 14).
- Under a bare prompt the base model delegates in 1 of 110 episodes (Paper Table 2a).

### 4.8 Execution failures seen

- Loop halts. qwen-code stops an agent that repeats identical tool calls. The runner records the halt reason and the last five tool calls so the optimizer can see them (Repo `runtime/run_episode.sh:226-266`). Such halts "remain in the evaluation" (Paper App. C.2).
- Infrastructure errors. A final assistant message that is an API error string is marked as a failed episode rather than judged (Repo `runtime/run_episode.sh:199-224`).
- The same prompt can succeed or fail. Two executions of a byte-identical workflow and prompt with the same recorded seed gave one completed run that won 6 of 6 judgments and one loop halt that lost 6 of 6 (Paper App. E.3).

## 5. Inner loop: workflow search

### 5.1 The algorithm as published

Per task, with frozen weights (Paper section 3 and Algorithm 2 in App. A.1):

1. Execute the initial workflow `w(0)`. Set the best workflow `w*` and best output `y*` to it. Feedback history is empty.
2. For `i = 1..I`: the optimizer proposes `w(i)` from the previous workflow, `w*` and the history; the agent executes it and produces `y(i)`; the evaluator compares `y(i)` with `y*` and returns a verdict and a rationale `d(i)`; `d(i)` is appended to the history; if `y(i)` is preferred, `w*` and `y*` are replaced.
3. Return `w*`.

Stated properties: it "resembles an evolution process with an elite size one"; `w*` is "the best self-judged workflow under sequential in-loop pairwise evaluation, rather than a certified global optimum"; "Every proposal is executed, even if it does not replace the incumbent; the quality of successive executions need not increase." Tasks are optimized independently, each with its own workflow sequence.

### 5.2 Three comparison rules exist in the sources

| Source | What the new workspace is compared with | Champion rule |
|---|---|---|
| Blog (original RHI) | The previous iteration's workspace ("on-trajectory self-comparison") | None described; history acts as "a momentum signal" |
| Paper Algorithm 2 | The best workspace so far | Replace on a win |
| Repo driver as configured by `python -m mass search` | A fixed bare-task reference workspace | A version must beat the reference; a later reference-winner must also beat the champion in one direct comparison |

The repo says so itself: "The current RHI driver uses a fixed bare-task reference ... Algorithm 1 in the paper instead compares each proposal directly with the retained best output. These rules can select different workflows, so the current driver is not an exact implementation" (Repo `docs/reproduction.md:9-18`; the docs say "Algorithm 1" where the paper numbers workflow search as Algorithm 2). Which rule produced each reported number is NOT VERIFIED; the repo states that it "does not contain the complete run ledger" (Repo `docs/reproduction.md:25-27`). The paper's ablation appendix describes six search conditions. They include comparing adjacent executions, adding the retained workflow to the history, adding text diffs between successive workflows, and comparing against a fixed bare-task workspace (Paper App. E.1). The packaged driver turns the last three on together (Repo `mass/pipeline.py:116-128`).

The blog explains why RHI avoids a population of candidates: each candidate costs a full agent execution and pairwise comparison grows quadratically, so "a search algorithm must account for its own cost".

### 5.3 The packaged driver, step by step

For one task with `iterations = 11` (Repo `mass/pipeline.py:96-131`):

1. Run the bare task once. This is the fixed reference workspace.
2. For version `k = 0..11`: run the workflow prompt for version `k` in a fresh workspace. Then call the RHI script on that workspace.
3. At `k = 0` the script makes no comparison. The optimizer proposes version 1 from the version 0 workspace alone.
4. At `k >= 1` the script judges version `k` against the reference (and, once, version 0 against the reference), updates the champion, then asks the optimizer for version `k + 1`.
5. At `k = 11` the script runs with `--evaluate-only`: it records the last comparison and writes the retained workflow without proposing another.

Counts per task (INFERRED from the loop): 13 agent executions (1 reference plus versions 0 to 11), 11 optimizer calls, 12 reference comparisons, plus at most one champion comparison per iteration.

The retained record has this shape (Repo `harness_improvement/iterate_multi_agent_prompt.py:2196-2202`):

```
{"version": <int or null>, "beats_reference": <bool>, "model": "<model id>", "last_evaluated_version": <int>}
```

If no version beat the reference, `version` is null and the task is skipped in teacher collection (Repo `mass/pipeline.py:141-144`). This happened to task 221 in the reported experiment (Paper App. B.1).

### 5.4 The champion rule in the driver

(Repo `harness_improvement/iterate_multi_agent_prompt.py:532-573`)

- Only a version that beat the reference can hold the crown. Ties and losses change nothing.
- The first version that beats the reference takes the crown outright.
- After that, a new reference-winner triggers one extra judge call, champion against current. It takes the crown only if it wins that call.
- Version 0 holds the crown by default without a proven win until some version beats the reference.

### 5.5 The evaluator

Inputs. The evaluator sees the task text and two workspaces. It does not see the workflow text, the trajectory or any logs (Repo `mass/judging.py:6-15`; `evaluation/bundle.py:31-138`; `evaluation/artifacts.py:147-168, 317-403`). For each workspace it gets:

- the path and a directory tree (at most 400 entries, depth 6, 100 children per directory; binary and large files are named but not shown);
- a "programmatic preflight": FOUND or MISSING for `research_report.md`, `deliverables/index.json`, `results/metrics.json` and `requirements.txt`, and ok or missing for each plot filename mentioned in the task;
- size-capped excerpts of `research_report.md`, `deliverables/index.json`, `results/metrics.json`, `results/model_comparison.json`, `requirements.txt`, up to four root entry scripts (`run_*.py`, `main.py`, `pipeline.py`) and one test file.

The preflight filenames are hard-coded to the deliverable layout these tasks require.

Prompt. The system prompt sets a domain reviewer persona chosen by task number (senior quantitative researcher, senior researcher in pharmacy, or senior researcher in robotics) who compares two completed submissions for the same assignment and is told to be strict and evidence-driven. Rules follow (Repo `evaluation_claudecodex/pairwise_prompts.py:4-71`). Two of them verbatim:

- "The **# Task** block is the only contract for what must be produced. Workspace sections are evidence only; do not invent file contents."
- "Choose **A** or **B** when one submission clearly better satisfies the task across deliverables, rigor, reproducibility, and alignment. Use **tie** only when they are genuinely comparable overall (not merely different)."

The user message is: the task, a labels section, workspace A evidence, workspace B evidence, then a six-point relative rubric: deliverable coverage, numerical and empirical rigor, reproducibility, presentation, engineering, task alignment (Repo `evaluation_claudecodex/pairwise_judge.py:27-61`; `pairwise_prompts.py:74-96`).

Output. JSON only, validated against a two-field schema (Repo `evaluation_claudecodex/pairwise_schema.py:8-10`):

```
{
  "winner": "A" | "B" | "tie",
  "rationale": "<string, cite concrete evidence from both workspaces>"
}
```

The rationale string is the feedback the optimizer later reads.

Details worth knowing:

- In workflow search the comparison is not blind. The labels section names submission A `no-harness-single-agent-baseline` and submission B `multi-agent-design-v<k>`, the reference is always A, and each workspace path is printed (Repo `harness_improvement/iterate_multi_agent_prompt.py:1743-1749`). Teacher ranking uses neutral labels `candidate-a` and `candidate-b` with a seeded random order (Repo `mass/judging.py:29-30`; `mass/pipeline.py:162-172`).
- Each verdict is a single model call at temperature 0.6 with thinking on, JSON mode, up to 49,152 output tokens (Repo `mass/pipeline.py:119-125`). There is no repeat or majority vote in the loop.
- The paper does not print the evaluation prompt `x_eval`. Whether the repo's prompt is the one used for the reported runs is NOT VERIFIED.
- Measured quality of this judge: agreement between the model's own verdicts and the external judges is 0.73 for the base model, 0.91 after one cycle and 0.93 after two (Paper Figure 3 and section 4.2).

### 5.6 The optimizer

The optimizer is one model call per iteration that returns a full replacement design block (Repo `harness_improvement/iterate_multi_agent_prompt.py:597-919, 2373-2497`).

System prompt. It opens "You are a principal prompt engineer for autonomous coding agents" and limits the task to improving "only the 'multi agent design' block". The sentence that steers the whole search, verbatim (lines 733-736):

> "Prioritize two improvements: (1) stronger agent-to-agent communication contracts for each subagent 'Output to orchestrator' schema, and (2) more orchestrator-subagent hops with explicit iterative recall/re-delegation instead of one-pass unidirectional execution."

It also says to prefer what multi-agent systems can do and single agents cannot ("parallel specialist decomposition, cross-agent critique, agent to agent communication, and iterative reconciliation") and that "You must ground changes in concrete evidence from submission artifacts and comparison history." The paper summarizes the same emphasis: the optimization prompt "explicitly emphasizes the communication structure" (hop and contracts) "over the role and instruction components" (Paper section 3).

User prompt, in order:

1. The task text and the current design (version `k`).
2. The champion's design when the champion is older than the current version, with a note explaining why it is shown (for example that it beat the reference while the current version did not) and the judge's stated reasons. The instruction is then to combine the champion's strengths with the current design.
3. The original version 0 design, as "the ROOT of the version track", with the instruction not to revert to it wholesale.
4. The fixed "save rules" text (the prompt suffix).
5. Evidence from the current workspace only: the same tree, preflight and excerpts the evaluator gets, plus every JSON file under `results/` (capped at 24,000 characters per file and 120,000 in total in the packaged configuration).
6. Execution metadata: the run status file. If the run ended in a halt, a failure notice asks for two things together: make the next run complete by tightening each subagent's completion and acceptance criteria and adding progress checks and re-delegation, and still improve hops and contracts. It forbids fixing stalls with numeric caps or by collapsing toward single-agent execution (lines 2020-2053).
7. The feedback history (below) and a "delta checklist" of recurring issue types counted by keyword across all rationales (lines 447-489).
8. Instructions: preserve the original intent and deliverables, "Explicitly strengthen 'Output to orchestrator' contracts so downstream agents can consume structured outputs", "Explicitly increase orchestrator-subagent feedback loops", be specific and evidence-grounded, "Do not change non-design parts of the query."

Feedback history as rendered (lines 400-444, 576-594, 854-875):

- A one-line scoreboard headed `Record vs no_harness:` that lists every version so far with W, L or T against the reference and marks the champion with a star.
- A framing note: each verdict "is a single judgment; treat trends across rounds as more informative than any single win/loss flip."
- One entry per comparison: the pair, the winner, the judge model, the time, the two workspace paths, a verbatim excerpt of the rationale (up to 2,400 characters), up to six "actionable takeaways" (sentences from the rationale that contain cue words such as fail, missing, inconsistent, mismatch, error), and a unified diff of the design change that preceded the verdict (1,500 characters for the three most recent entries, 400 for older ones).
- The closing line of each entry: "Use this history as constraints: preserve winning traits, fix listed defects."

What the optimizer does not see: the evaluator's rubric (Blog: "the harness optimizer never sees that rubric. It must infer useful revisions from the comparison history alone"; confirmed in the code, where the rubric is only in the judge's user message), the reference workspace, the champion's workspace, and any trajectory.

Output. JSON with six keys (lines 620-632):

```
{
  "improved_multi_agent_design": "<full replacement text for the design block only>",
  "changes_from_previous": ["..."],
  "why_it_should_improve": ["..."],
  "evidence_used": ["..."],
  "expected_impact": ["..."],
  "verification_checks": ["..."]
}
```

Only `improved_multi_agent_design` is used. It must start with the marker sentence. A plain-text output mode exists as "a recorded fallback for local models that truncate while JSON-escaping a long harness" (lines 1415-1423). The script then writes the new design file and the full rendered prompt for the next execution (lines 2456-2459).

Call settings in the packaged configuration: temperature 0.6, top-p 0.95, top-k 20, thinking on, up to 49,152 output tokens, a pre-call guard that the estimated prompt plus output fits 262,144 minus 8,192 tokens (Repo `mass/pipeline.py:119-125`).

### 5.7 Budgets, seeds, stopping

- Stopping rule: a fixed iteration budget. There is no convergence test and no early stop. The packaged budget is 11 updates (Repo `configs/paper.json`). The paper reports search curves over 10 or 11 iterations in the main results and 20 in the ablations (Paper Figure 3a, Table 1, Figure 7).
- Seeds in the packaged driver: the reference run uses `450000 + task`; the execution of version `k` uses `450000 + task + 1000 * k`; optimizer and judge calls use `450000 + task` (Repo `mass/pipeline.py:100-107, 123`). Cycle 2 uses base `1450000` (Repo `configs/cycle2.json`).
- A seed does not make an execution reproducible: "the recorded sampling seed does not make these full agent executions deterministic" (Paper App. E.3).
- Each proposed workflow is executed once. A verdict therefore mixes the effect of the text change with run-to-run variance (Paper App. E.2).

### 5.8 Stability problems the authors report

- The page says "workflow search is noisy. In text-space optimization, reaching a good workflow and improving on it stably are different problems." Strong iterations "can be followed by sharp performance regressions" (Page; Paper section 5.1).
- Edit size does not predict the outcome. The Spearman correlation between the fraction of tokens rewritten and the change in external verdicts is at most 0.12 in absolute value. The probability that a step flips a near-unanimous verdict (5 or 6 of 6 to 0 or 1 of 6, or the reverse) is 0.22 to 0.40 in every edit-size bin, including edits of 5 percent or less (Paper Table 10).
- Median edit per step: 16 percent of tokens with the base model as optimizer, 39 percent with GPT-5.5 as optimizer (Paper Table 10).
- The identical-prompt case in section 4.8 shows that part of the noise is execution variance, not the workflow.
- The authors' suggestion: "the LLM optimizer may need an equivalent of a learning-rate scheduler to stabilize the search in text space" (Page).
- The retained path often stays flat while later proposals are executed and rejected. Task 202 kept version 0 until version 8 (Paper App. I and Figure 8).

### 5.9 Optimizer or evaluator: which limits the search

With the executor fixed at the base model (Paper section 5.1; Page Figure 7):

- All roles by the base model: a workflow that all six external judgments prefer to the no-workflow output is found for 11 of 12 tasks, by iteration 13 according to the page's figure description.
- GPT-5.5 as evaluator only: 10 tasks.
- GPT-5.5 as evaluator and optimizer: all 12 tasks within 5 iterations.

The authors conclude that "optimizer capacity, rather than the evaluator capacity, can be a bottleneck in the speed of RSI", and that the evidence "concerns the speed of workflow improvement, rather than an inability of the weaker model to discover useful workflows". Their reading: a strong evaluator gives good feedback that a weak optimizer cannot use (Page).

## 6. Outer loop: training on the selected team

Brief. Sources: Paper section 3, section 4.1, App. A.2, B, D, G; Repo `mass/pipeline.py:134-289`, `training/*`.

Selection.

- For each training task whose retained workflow beat its reference, run task plus workflow plus directive 18 times with different seeds (packaged seeds: `710000 + 100 * task + i`).
- The current model compares pairs of the resulting workspaces within the task. A Bradley-Terry model turns the pairwise verdicts into one score per workspace. Ranks 1 to 15 go to training and rank 16 to validation.
- In the packaged driver only episodes that completed successfully are ranked, and a task needs at least 16 of them (Repo `mass/pipeline.py:184-186`).
- The packaged schedule gives each candidate six opponents (54 comparisons for 18 candidates) and fits Bradley-Terry by a minorization-maximization loop with a weak prior. The docs call the schedule and seeds "reconstruction choices, rather than the original tournament ledger" (Repo `mass/pipeline.py:162-198`; `mass/ranking.py:4-34`; `docs/reproduction.md:34-36`).
- First cycle: 9 designated training tasks, 1 excluded (task 221), 1 trajectory dropped after premature termination, giving 119 training trajectories from 8 tasks.

Data construction.

- Every model request of an episode is recorded by a logging proxy. Conversations are rebuilt from that log: system prompt, tool definitions, messages and assistant outputs. Threads are classed as orchestrator, worker or auxiliary by their system prompt (Repo `training/prepare_sft_data.py:91-119, 182-188`).
- In the orchestrator conversation the initial task-workflow-directive prompt is replaced with the bare task. Worker assignments are left unchanged. The page gives the reason: this "encourages the model to learn how to organize the work from the task itself".
- Windows of at most 49,152 tokens with up to 8,192 tokens of earlier context. Loss only on assistant reasoning, text and tool calls; system and user messages, tool outputs and the overlap are masked.
- Result: 2,435 windows (282 orchestrator, 2,054 worker, 99 auxiliary) with 17,221,055 supervised tokens.

Training.

- A fresh LoRA adapter on the BF16 parent: rank 64, alpha 128, dropout 0.05. AdamW, betas 0.9 and 0.95, peak learning rate 3e-5, 5 warmup steps, cosine decay to 10 percent, 1,624 steps of 3 windows.
- Sampling: task uniformly, then an orchestrator window with probability 1/3 or a worker or auxiliary window with probability 2/3, with replacement.
- Checkpoint by lowest validation loss (step 1200 in cycle 1), merged into the parent and quantized to block-FP8 for inference. External judgments do not select the checkpoint.
- Compute: six NVIDIA H100 GPUs, about 19 hours for the first-cycle fine-tune. This excludes search, teacher collection and evaluation.
- The second cycle repeats all stages with the first-cycle model in every role and a fresh adapter on the merged model.

What training changed.

- Behaviour under a bare prompt, episodes out of 110: delegation 1, then 48, then 87 for the base, first-cycle and second-cycle models; code handoffs 1, 42, 83; worker revisions 0, 10, 39 (Paper Table 2a). The authors describe this as "increased bare-prompt use of coordination already available in the base model" (Paper App. C.4).
- The other two roles improved without being trained on: evaluator agreement 0.73 to 0.93, and later generations find successful workflows in fewer iterations (Paper section 4.2).

Ablation on the kind of training data (Paper section 4.4, Table 8). Students trained from the same base, scored against the base on four training tasks and one test task:

| Student | Episodes | Processed supervised tokens | Win share vs base |
|---|---|---|---|
| S, single-agent | 17 | 1.82M | 44.0% |
| M, multi-agent | 17 | 3.64M | 56.0% |
| X, both | 34 | 8.85M | 58.8% |
| S+, single-agent | 78 | 9.11M | 59.0% |
| S++, single-agent | 290 | 42.00M | 64.0% |
| M+, multi-agent (the first-cycle model) | 119 | 28.82M | 68.3% |

The headline claim is that M+ beats S++ against the base with 31.4 percent fewer processed tokens. Stated limits: M+ "has broader training-task coverage and a different sampler, so it is not a matched-data control"; the comparison is each student against the base, not head to head; checkpoints of the controls were chosen with external judgments; on the single test task alone M+ and S+ both score 48.3 percent (Paper App. D.1, D.2, Table 8).

## 7. Results and findings

### 7.1 Setup

- Base model Qwen3.6-27B (BF16 for training, FP8 for inference), served with vLLM. Agent qwen-code 0.20.0 (Paper section 4.1, App. B.1).
- Other models used: GPT-5.5 and Claude Opus 4.8 as reporting judges; GPT-5.5 also as the author of the task descriptions and as a substitute evaluator or optimizer in the role ablations; Claude Sonnet 4.6 as AstaBench's official rubric judge; `text-embedding-3-large` and `all-mpnet-base-v2` for the workflow-information analysis (Paper sections 4.1, 4.3, 5.1, App. C.5).
- 12 synthetic research-programming tasks, four each in finance, pharmacy and robotics, written by GPT-5.5 before the experiment. Each requires code, quantitative results, plots and a research report. The benchmark is private in the paper; the repo includes all 12 prompts (Paper section 4.1; Repo `tasks/README.md`).
- Split for training: 9 tasks, of which 8 supplied data; 3 held out, one per domain. Workflow search was also run on the held-out tasks; the split applies to fine-tuning only (Paper App. B.1).
- Reporting judges, never shown to the loop: GPT-5.5 and Claude Opus 4.8 at maximum reasoning effort, three seeds each, six judgments per workspace pair. Win rate is wins over wins plus losses, ties excluded. "These are repeated assessments of one pair of outputs, not six independent task executions. We report descriptive shares and do not infer statistical significance from the number of judgments." (Paper App. C.1)

### 7.2 Results that involve no weight update

| Finding | Number | Source and limits |
|---|---|---|
| Base model with a searched workflow against the same model with no workflow | 63% mean win rate | Paper Table 1, row 3. Mean over 11 iterations and 12 tasks, one execution per task. The workflows came from the first-cycle model's search, so the searcher was trained even though the executor was not. |
| Frozen base-model loop finds a workflow unanimously preferred to no workflow | 11 of 12 tasks | Paper section 5.1. "Coverage records whether a task has ever achieved a unanimous win"; later versions can be worse. The per-iteration win-rate curve exists only as a plot. |
| Multi-agent executions (workflow plus directive) against single-agent executions, base model, eight training tasks | 85.1% preference (701 to 123 judgments) | Paper Table 9. 79.3% when each task is weighted equally. |
| Same, restricted to pairs with the identical workflow text | 86.8% (347 to 53) | Paper Table 9. The directive still differs between the two sides. |
| Same, restricted to pairs of similar generated length | 80.0% (32 to 8) | Paper Table 9. Only 10 pairs. |
| Generated tokens per episode | 141,844 multi-agent against 59,355 single-agent, a ratio of 2.39 | Paper App. D.3. Completion tokens summed over all requests of an episode. |
| Selected workflow-prompted outputs of the base model against the first-cycle model with a bare prompt | the trained model wins 17.3% (112 to 536) | Paper Table 6. The teacher side is selected and ranked; "it is not a bare-prompt model comparison". |
| RHI with Claude models (earlier paper, 30 tasks) | After one or two updates, a `high`-reasoning agent beat the stronger same-family settings tested; final execution cost up to 60% below the `ultracode` baseline with Opus 4.8 | Blog. Costs "describe the final execution with the evolved harness, not the total cost of discovering it". |

The paper's own caution on the multi-agent comparisons: they "do not isolate the effect of delegation: workflow mixtures, verification instructions, and successful-completion selection also differ" (Paper App. D.3).

Not reported anywhere: a quality comparison between the generic directive (no workflow) and an optimized workflow for the same model.

### 7.3 Results after training

Synthetic tasks, external-judge win rates, all models given the bare task (Paper Figure 3; Page Table 1):

| Tasks | Round 1 vs base | Round 2 vs base | Round 2 vs round 1 |
|---|---|---|---|
| 8 training tasks | 65.6% | 82.5% | 68.1% |
| 3 held-out tasks | 53.9% | 69.9% | 60.8% |

First-cycle detail (Paper Table 5): per-task shares range from 20.0% (training task 205) to 88.3% (training task 51). The held-out tasks score 48.3%, 45.0% and 68.3%. Second-cycle per-task numbers are not reported.

Workflow transfer (Paper Table 1): round-1 model with its own workflows against base with base workflows, 0.80; base executing round-1 workflows against base executing base workflows, 0.64; base executing round-1 workflows against the round-1 model executing them, 0.29.

Public benchmarks, bare benchmark instructions, same qwen-code tools for all models (Paper Table 7):

| Benchmark | Base | Round 1 | Round 2 | Score per output token, round 2 over base (Page Figure 3) |
|---|---|---|---|---|
| ScienceAgentBench, success % (102 tasks, 3 trials) | 28.8 ± 1.5 | 30.4 ± 2.0 | 31.7 ± 2.0 | 1.20 |
| MLR-Bench, overall 0 to 10 (12 tasks, 5 trials) | 1.58 ± 0.38 | 1.80 ± 0.30 | 2.40 ± 0.28 | 1.60 |
| AstaBench E2E-Bench-Hard, rubric (40 tasks, 3 trials) | 0.053 ± 0.002 | 0.055 ± 0.004 | 0.067 ± 0.017 | 1.28 |
| DSBench, normalized (74 tasks, 3 trials) | 0.473 ± 0.029 | 0.465 ± 0.011 | 0.482 ± 0.025 | 1.35 |
| Terminal-Bench 2.0, pass % (89 tasks, 3 trials) | 37.5 ± 2.2 | 35.3 ± 2.1 | 35.2 ± 1.7 | 0.96 |
| SWE-bench Verified, pass % (500 tasks, 3 trials) | 62.6 ± 1.4 | 61.8 ± 1.9 | 61.1 ± 0.6 | 0.94 |

Stated limits on these numbers (Paper App. C.5):

- The intervals are standard deviations over trial means, "not standard errors or confidence intervals".
- MLR-Bench and AstaBench gains accompany more delivered outputs (papers delivered 31, 36, 50 of 60; reports delivered 60, 69, 91 of 120) while the mean score among delivered outputs falls (3.07, 3.00, 2.88 and 0.105, 0.095, 0.088).
- "None of the 180 MLR-Bench executions calls the delegation tool, so this result does not establish delegation as the mechanism of public-benchmark transfer."
- For MLR-Bench a Welch test on trial means gives p = 0.0053 for round 2 against base, but a paired sign test across the 12 tasks gives p = 0.2266: the evidence "does not establish a consistent gain across research tasks".
- In the authors' local check the ScienceAgentBench reference programs reached only 80.4% success, which "limits comparison with scores obtained in other environments".
- The two software-engineering benchmarks scored slightly lower after training, in raw pass rate and per output token. The page suspects this could be addressed by adding software-engineering tasks to training; that is not tested.
- INFERRED, approximate: the per-token ratio combines a score change with a token change. For DSBench the score moved 0.473 to 0.482 (about 2%), so a ratio of 1.35 implies roughly a quarter fewer output tokens. Output-token counts appear only in the plot.

Inconsistencies between sources: the paper's main text says "MLR-Bench (107 tasks)" while its appendix and Table 7 report a 12-task subset; the repo README reports a 107-task, 3-trial MLR-Bench result (1.71, 1.77, 2.25) whose configuration is not in the repo (Repo `README.md:60, 79-82`). The paper's text gives ScienceAgentBench as 28.7% to 31.8%; its tables and trial rates give 28.8 and 31.7.

### 7.4 What makes a workflow good: information moves into contracts and hops

Claim (Paper section 4.3; Page "Ablation 1"): as search proceeds, contracts and hops carry more task-specific information and roles and instructions carry less, while redundancy between the four components falls. The page's example: a role such as "careful data scientist" could apply to many tasks; a contract specifying leakage checks for a trading backtest is distinctive.

Method (Paper App. F): take the retained workflow of each task at search iterations 3, 6, 9 and 11 in the all-base-model condition; embed each component and the task description with two embedding models; estimate mutual information between component and task from canonical correlations (a Gaussian proxy, in nats); estimate redundancy as total correlation across the four components; report raw and permutation-adjusted values.

Task information, iteration 3 to iteration 11 (Paper Table 12):

| Component | text-embedding-3-large, raw | same, adjusted | all-mpnet-base-v2, raw | same, adjusted |
|---|---|---|---|---|
| Role | 1.26 to 1.09 | 0.42 to 0.38 | 1.07 to 0.71 | 0.25 to 0.02 |
| Instruction | 2.56 to 2.36 | 1.74 to 1.61 | 2.19 to 1.96 | 1.38 to 1.24 |
| Contract | 2.99 to 3.29 | 2.24 to 2.50 | 2.39 to 3.00 | 1.66 to 2.22 |
| Hop | 5.23 to 6.22 | 4.72 to 5.54 | 4.82 to 6.06 | 4.36 to 5.40 |

Redundancy (Paper Table 13): total correlation falls in all four configurations (for example 11.82 to 11.62 raw with the first encoder). The task-centred version falls in three and rises in one.

The earlier RHI study reports the same direction over iterations 1 to 4 on its 30 tasks, and that contracts show "the clearest task-dependent clustering" (Blog). The blog's reading: optimizing contracts makes information flow selective, so "the contract is one of the few variables that can improve performance and inference cost at the same time". It adds that for two of the three models tested "output-token use stayed nearly flat across RHI iterations while performance improved". Its proposed rule of thumb, marked by the author as a hypothesis that "still needs plenty of verification": "Encode task information in the harness components the human designer wants to improve, while keeping every component as functionally distinct as possible."

Stated limits (Paper App. F.3; Page):

- Small sample: 43 to 51 agents from 8 or 9 tasks, and agents in one workflow share the task and the hop, so the observations "are not independent task replications".
- These are "descriptive text statistics, rather than measurements of messages exchanged during execution or a causal test of the proposed learning mechanism".
- The redundancy result holds on the retained path and "does not hold for every proposed iterate". Intermediate values are not monotone.
- Tasks still on version 0 are excluded.

A confound the sources state but do not test: the optimizer is told to prioritize contracts and hops (section 5.6). The blog calls this "a kind of 'nudging' process" and says the method "encodes task information into the harness components the human designer cares about". No experiment removes that instruction, and none varies roles or instructions while holding contracts fixed. So a statement such as "roles and instructions matter less" goes beyond the sources. They show where the workflow text became task-specific. They do not measure the effect of each component on output quality. The blog lists alternative explanations itself: the other components may need more iterations, or the feedback design may only be informative enough to update contracts.

### 7.5 Cost and token figures stated

- Fine-tuning: six H100 GPUs, about 19 hours, first cycle (Paper App. B.2).
- Supervised tokens: 17.2M stored, 28.8M processed up to the selected checkpoint, 39.5M for the full run (Paper App. D.2).
- Per episode: about 142K generated tokens for a multi-agent run and 59K for a single-agent run (Paper App. D.3).
- Per request: at most 32,768 output tokens, 262,144-token context (Paper App. C.5).
- Not stated: the GPU time, token count or money spent on workflow search, teacher collection or judging in MASS.

## 8. Implementation notes from the source code

### 8.1 Layout

(Repo `README.md:187-201`)

| Path | Contents |
|---|---|
| `mass/` | Stage driver (`pipeline.py`, 362 lines), self and external judging wrappers, Bradley-Terry ranking |
| `harness_improvement/` | The RHI script `iterate_multi_agent_prompt.py` (2,535 lines: judge calls, champion logic, optimizer prompt) and `llm_backend.py` (local vLLM chat or OpenAI Responses, JSON output) |
| `runtime/` | `run_episode.sh` (one qwen-code episode) and `api_proxy.py` (logging reverse proxy) |
| `evaluation/`, `evaluation_claudecodex/` | Workspace evidence bundle, pairwise judge prompt, schema and provider calls |
| `training/` | Conversation rendering, LoRA training, merge and FP8 export |
| `tasks/` | `bare/query<ID>.txt` (12 task prompts), `initial/query<ID>_ourTeam.txt` (task plus version 0 workflow), `teacher_directive.txt`, `splits.json` |
| `configs/` | `paper.json` (cycle 1), `cycle2.json`, `models.json` (pinned model revisions) |
| `benchmarks/` | Harbor adapters for ScienceAgentBench and a 12-task MLR-Bench subset |
| `tests/` | Offline tests with fixtures and a mocked model backend |

### 8.2 The data model of a workflow

There is no workflow class and no JSON schema. The only typed object is the prompt split (Repo `harness_improvement/iterate_multi_agent_prompt.py:39-47`): `QueryTemplate` with `query_num`, `query_name`, `raw_text`, `before_design`, `design_block`, `after_design`, `task_text_for_eval`. The workflow is `design_block`, an opaque string. A new prompt is built as `before_design + new design + blank line + after_design` (lines 922-923). Versions are stored as plain text files.

### 8.3 Where the prompts live

| Prompt | Location |
|---|---|
| Orchestrator input | The prompt file itself: `tasks/initial/query<ID>_ourTeam.txt` for version 0, `<run>/search/query<ID>_ourTeam/updated_queries/query<ID>_ourTeam-v<k>.txt` after that |
| Execution directive | `tasks/teacher_directive.txt`, appended by `mass/pipeline.py:147` |
| Optimizer system and user prompt | `harness_improvement/iterate_multi_agent_prompt.py:597-919`; failure notice at `:2020-2081` |
| Evaluator system prompt, rubric and output schema | `evaluation_claudecodex/pairwise_prompts.py`; user message assembly in `pairwise_judge.py:27-61`; schema in `pairwise_schema.py` |
| Workspace evidence shown to both | `evaluation/bundle.py`, `evaluation/artifacts.py` |

Every prompt actually sent is saved next to its result as `full_prompt.txt` (Repo `harness_improvement/iterate_multi_agent_prompt.py:143-159`).

### 8.4 Stages and commands

`python -m mass <stage> --config <config>` with stages `plan`, `search`, `collect`, `rank`, `select`, `render`, `train`, `export`, `rollout`, `report` (Repo `mass/pipeline.py:326-362`). Stages are separate so that intermediate outputs can be inspected and completed work reused. Episodes run sequentially through one endpoint and one proxy port (Repo `docs/running.md:99-105`).

### 8.5 Run directory

(INFERRED from the paths built in `mass/pipeline.py` and the RHI script)

```
runs/L0/
  config.json                                   frozen copy of the configuration
  reference/query<ID>/                          bare-task workspace (the fixed reference)
  workspaces/v<k>/query<ID>_ourTeam/            workspace produced by workflow version k
  logs/reference/..., logs/search/v<k>/...      one log directory per episode
  search/query<ID>_ourTeam/
    versions/multi_agent_design_v<k>.txt        the design block only
    updated_queries/query<ID>_ourTeam-v<k>.txt  the full prompt for version k
    evaluation_prompts/v<k>_to_v<k+1>/          optimizer call: full_prompt.txt, result.json
    comparisons/<key>/                          judge call: full_prompt.txt, result.json
    records/comparison_history.jsonl            verdicts against the reference
    records/champion_history.jsonl              champion-against-current verdicts
    retained_workflow.json
  teacher_prompts/query<ID>.txt                 retained prompt plus directive
  pool.json, pool/workspaces/, pool/runlogs/    teacher candidates, named t<ID>_L<gen>_s<seed>
  selection/                                    match records, ranked lists, train.json, val.json
  data/, training/, selected_checkpoint.json    rendered windows, checkpoints, chosen checkpoint
  evaluation/                                   bare-task rollouts and external judgments
```

Comparison keys are `v<k>_vs_no_harness` and `champion_v<a>_vs_v<b>`. Each history row stores the key, the two workspace paths, the winner, the rationale, the time, the backend, the model and call metrics (Repo `harness_improvement/iterate_multi_agent_prompt.py:1772-1791`).

### 8.6 How runs are launched and isolated

(Repo `runtime/run_episode.sh`; `mass/pipeline.py:73-83`)

- One episode equals one empty workspace directory plus one log directory. The runner refuses a non-empty workspace and refuses to overwrite an existing episode.
- Each episode gets its own qwen-code home directory with a generated `settings.json`. INFERRED: no settings or agent memory carry over between episodes; the paper's audit notes that initial requests "report empty episode memory" (Paper App. G.1).
- Before starting, the runner checks that the endpoint serves the expected model and that the proxy port is free.
- Success means exit code 0 and a final `result` event with subtype `success` in qwen-code's JSON stream. The outcome goes to `run_status.json` with the seed, model, qwen-code version, limits and, on failure, the reason and last tool calls.
- A completed episode is reused only if its saved prompt equals the current prompt.
- Write-once discipline throughout: the configuration is frozen per run directory and a changed configuration needs a new one; design files, rendered prompts and result files are never overwritten; a comparison result without its history row stops the run (Repo `mass/pipeline.py:57-62`; `harness_improvement/iterate_multi_agent_prompt.py:191-206, 1857-1862, 2311-2322`).
- Isolation is by directory only. The agent runs on the host with approval disabled.

### 8.7 How trajectories are stored

- A reverse proxy sits between qwen-code and the model server and appends one JSON line per HTTP request to `api_requests.jsonl` (compressed after the episode): the parsed request body (system prompt, tool schemas, full message history), and the response with streamed chunks reassembled into one message with content, reasoning content, tool calls, finish reason and usage. Its stated purpose is to "capture EXACTLY what qwen-code sends to the model ... so training samples can be rendered from the very same messages the served student will see" (Repo `runtime/api_proxy.py:2-17, 34-111`).
- The same log directory holds `prompt.txt`, qwen-code's own JSON event stream, its standard error, the proxy log and `run_status.json`.
- Because every request carries the whole conversation so far, a conversation is recovered from the last request of each thread plus its final response. Threads are keyed by a hash of the system prompt and first user message, and split where a request is not an extension of the previous one (Repo `training/prepare_sft_data.py:91-119, 347-407`).

### 8.8 Configuration defaults

(Repo `configs/paper.json`)

| Key | Value |
|---|---|
| `model_id` | `Qwen/Qwen3.6-27B-FP8` |
| `endpoint`, `proxy_port` | `http://127.0.0.1:8001/v1`, 9001 (the endpoint must be a loopback address) |
| `tasks.train_candidates` | 51, 53, 56, 202, 205, 221, 302, 307, 324 |
| `tasks.train` | the same without 221 |
| `tasks.test` | 60, 207, 305 |
| `search` | `iterations` 11, `seed_base` 450000 |
| `collection` | `candidates` 18, `train_ranks` 15, `validation_rank` 16, `seed_base` 710000 |
| `ranking` | `opponents_per_episode` 6, `seed` 1234 |
| `training` | `window` 49152, `context_overlap` 8192, `steps` 1624, `lr` 3e-5, `warmup_steps` 5, `min_lr_ratio` 0.1, `lora_r` 64, `lora_alpha` 128, `lora_dropout` 0.05, `save_every` 50, `val_every` 150, `replicas` 3, `gpus_per_rank` 2 |

Pinned dependencies include `openai`, `anthropic`, `pydantic`, `aiohttp` for the core; `vllm==0.25.1` for serving; `torch`, `transformers`, `peft` for training. Node.js 22 and `@qwen-code/qwen-code@0.20.0` are installed separately (Repo `requirements-*.txt`; `docs/running.md:18-32`).

### 8.9 Smaller observations

- The optimizer prompt has branches for a `prior_failures` field in the run status (recurrent or resolved failures across versions), but the packaged runner never writes that field, so those branches do not fire here (Repo `harness_improvement/iterate_multi_agent_prompt.py:2054-2081`; `runtime/run_episode.sh:279-321`).
- The judge and optimizer can be pointed at different backends (`--judge-llm-backend`; the help text gives "an external gpt-5.5 judge steering a local-model optimizer" as the example). INFERRED: this is the mechanism behind the paper's role ablations. A `--require-local-vllm` flag refuses any non-loopback endpoint so a self-improvement run cannot contact an outside API (Repo `harness_improvement/iterate_multi_agent_prompt.py:1194-1208, 1429-1436`).
- The evidence builder carries hand-written per-task lists of large result files to leave out of prompts. The code comments say such files "frequently push prompts over context limits" (Repo `harness_improvement/iterate_multi_agent_prompt.py:999-1049`).
- The repo documents which paper experiments it cannot run: the information analysis, the behavioural audit, the role ablations and four of the six benchmark adapters are not included (Repo `docs/experiment_coverage.md:25-42`).

## 9. Limitations, safety remarks, maturity

Limits the authors state.

- No guarantee: "The recursive loop here is empirical ... it provides no guarantee that every future cycle improves" (Paper App. J).
- Shared prior: "Because this homogeneous loop shares a learned prior, it risks reinforcing its own errors rather than correcting them" (Paper section 7).
- Preference is not correctness. In one held-out episode a revision replaced force-driven motion in a simulator with direct repositioning toward the goal, then reported success on all task types; it still received five favourable judgments of six. "Coordination and high workspace preference therefore do not guarantee that a revision preserves the scientific task" (Paper App. G.3).
- More revisions "do not by themselves establish more successful repairs" (Paper App. G.2).
- Attribution is open: the observations "do not identify which training example caused a student action or isolate optimized workflow text from the directive, data selection, and sampler" (Paper App. G.2).
- Scale: 12 synthetic tasks from one generator model, LLM judges, mostly three trials. Transfer is "uneven" (Page).
- Speed: "Accelerating the speed of these performance gains in each cycle remains an important direction for future works" (Paper section 7).
- From the RHI blog: search cost is real ("it still pays for each intermediate agent run, evaluation, and optimizer call"), and adaptation pays off mainly when the workflow is reused.

Safety remarks (Page, Conclusion).

- "In a homogeneous loop, a shared blind spot could allow a flawed result to be produced, accepted as an improvement, and incorporated into the next model." If humans also miss the flaw, later updates may reinforce it.
- Shared blind spots "may be an Achilles' heel of recursive self-improvement". Knowing them "could guide independent checks, constraints on autonomous actions, and conditions for intervention", but "Identifying a weakness alone does not establish control".
- The paper has no separate safety section. The operational risk in the repo is the unsandboxed agent with approvals disabled (section 4.1).

Maturity.

- "This is a research implementation for running and extending the MASS pipeline" (Repo `README.md:44`).
- The packaged search rule differs from the published algorithm, the tournament schedule and second-cycle budget are reconstructions, and the run record linking results to configurations is absent (Repo `docs/reproduction.md`).
- Validation is limited to 12 offline tests with fixtures; "Full training/serving dependency installation, GPU training, and end-to-end benchmark reruns have not been validated for this package" (Repo `docs/reproduction.md:85-91`).
- The code has no licence, so it cannot be assumed reusable. The paper text is CC BY 4.0.
- The MASS experiments and code are tied to one agent CLI (qwen-code), one base model, and a fixed deliverable layout (`research_report.md`, `deliverables/`, `results/`). The earlier RHI study used Claude models (Blog); which agent it ran them in was not checked.

## 10. What could not be confirmed

- Which comparison rule (best-so-far, fixed reference with champion comparison, or adjacent) produced each reported search result. The sources disagree and the run record is not published.
- Whether the repo's evaluator prompt is the `x_eval` used in the reported runs. The paper does not print it.
- How often search-time executions (workflow in the prompt, no directive) actually called subagents.
- Whether qwen-code runs a subagent in-process or as a child process, which tools the `general-purpose` subagent has, and whether subagents called further subagents in these runs.
- What the "auxiliary" conversations are. The paper says only that they are "additional runtime conversations outside the orchestrator and general-purpose worker roles".
- The compute, token and money cost of workflow search, teacher collection and judging.
- Values that appear only in plots: output tokens per benchmark, the per-iteration win-rate curves, the coverage curves.
- Second-cycle details: per-task results, data sizes, selected checkpoint.
- Which pairs the evaluator-agreement figures (0.73, 0.91, 0.93) were computed on.
- The meaning of "structural guardrails" in the abstract.
- The MLR-Bench task count behind the headline ratio (107 tasks in the main text and README, 12 in the appendix).
- Any effect of removing the optimizer's contract-and-hop emphasis, or of a generic directive against an optimized workflow at fixed weights. Neither experiment is reported.
- The RHI paper (arXiv 2607.15524) was not read; RHI details here come from the blog and the MASS repo.
- No code was run. Statements about behaviour come from reading source at commit `fb403581bed2c974b3f99e7236224169da643079`.
