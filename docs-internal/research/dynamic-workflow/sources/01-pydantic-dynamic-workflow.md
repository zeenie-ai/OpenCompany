# Pydantic AI Harness: DynamicWorkflow (digest)
> **Status: Prototype reference.** External source digest: this file describes Pydantic AI Harness, not OpenCompany. Paths, names and commands in it belong to that project.

Written 2026-11-12. Nothing from the sources was executed, built or installed. Behaviour is described from reading the documentation, the source and the tests. Source line numbers refer to commit `d94586aad1acd640715f2bf1152e8d730c8f9938` of https://github.com/pydantic/pydantic-ai (branch main, committed 2026-11-12T05:48:03Z). A permalink for any cited line has the form `https://github.com/pydantic/pydantic-ai/blob/d94586aad1acd640715f2bf1152e8d730c8f9938/<path>#L<line>`.

Marks used: INFERRED is a conclusion drawn from the sources that they do not state in so many words. NOT VERIFIED is something that could not be checked. Everything else is stated by a source and cited.

Short names for the files cited most often (paths are relative to the repository root):

| Tag | File |
|---|---|
| TS | `src/pydantic_ai_harness/pydantic_ai_harness/dynamic_workflow/_toolset.py` (891 lines) |
| CAP | `src/pydantic_ai_harness/pydantic_ai_harness/dynamic_workflow/_capability.py` (160 lines) |
| README | `src/pydantic_ai_harness/pydantic_ai_harness/dynamic_workflow/README.md` (513 lines) |
| MX | `src/pydantic_ai_harness/pydantic_ai_harness/_monty_exec.py` (500 lines, shared with Code Mode) |
| SAO | `src/pydantic_ai_harness/pydantic_ai_harness/_sub_agent_output.py` (93 lines) |
| T1 | `tests/harness/dynamic_workflow/test_dynamic_workflow.py` (1,947 lines, 124 test functions) |
| T2 | `tests/harness/dynamic_workflow/test_temporal.py` (146 lines, 1 test function) |
| DOC | `docs/harness/dynamic-workflow.md` (source of the published docs page) |
| CM | `docs/harness/code-mode.md` |
| TEMP | `docs/durable_execution/temporal.md` |
| CC | Claude Code docs page "Orchestrate subagents at scale with dynamic workflows" |

## Sources read

| Source | What it is | Fetched | Commit / licence |
|---|---|---|---|
| https://pydantic.dev/docs/ai/harness/dynamic-workflow/ | The DynamicWorkflow documentation page. Its text matches DOC in the clone. | 2026-11-12 | Site footer: copyright Pydantic Services Inc. The page source is in the MIT-licensed repository. |
| https://pydantic.dev/docs/ai/harness/ | Harness overview: capability list, version policy. Matches `docs/harness/index.md`. | 2026-11-12 | Same |
| https://pydantic.dev/docs/ai/harness/subagents/ | SubAgents capability page. Matches `docs/harness/subagents.md`. | 2026-11-12 | Same |
| https://pydantic.dev/docs/ai/capabilities/on-demand/ | On-demand (deferred) capabilities, `defer_loading`. Matches `docs/capabilities/on-demand.md`. | 2026-11-12 | Same |
| https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/ | Deferred tools, `DeferredToolRequests`. Matches `docs/deferred-tools.md`. Read only for what DynamicWorkflow says about it. | 2026-11-12 | Same |
| https://github.com/pydantic/pydantic-ai (shallow clone) | Source code, tests and docs sources. Read in full: every file in the `dynamic_workflow` package (README, `__init__.py`, `_capability.py`, `_toolset.py`), the deprecated `experimental/dynamic_workflow/__init__.py`, MX, SAO, `_warn.py`, `_durable.py`, T1, T2, `tests/harness/_temporal.py`, DOC, `docs/harness/index.md`, `docs/version-policy.md`, `src/pydantic_ai_harness/pyproject.toml`. Read in part: CM, TEMP, `docs/harness/subagents.md`, `docs/capabilities/on-demand.md`, `docs/deferred-tools.md`, `code_mode/_toolset.py`, `_code_tools.py`, `tests/harness/test_monty_tracing.py`, and the Temporal integration files named in section 6. | 2026-11-12 | Commit `d94586aad1acd640715f2bf1152e8d730c8f9938`. MIT (root `LICENSE` and `src/pydantic_ai_harness/LICENSE`). |
| https://pydantic.dev/docs/monty/concepts/type-checking/ | Monty documentation page on type checking. Read only to identify the type checker. | 2026-11-12 | Monty is MIT per its README (https://github.com/pydantic/monty) |
| https://pydantic.dev/articles/dynamic-workflows | Pydantic article "Dynamic Workflows: feature that enabled the Bun rewrite", Aditya Vardhan, dated 2026-07-28. | 2026-11-12 | Copyright 2026 Pydantic Services Inc.; no licence stated |
| https://bun.com/blog/bun-in-rust | "Rewriting Bun in Rust", Jarred Sumner, dated 2026-07-08. Read for the dynamic-workflow parts only. | 2026-11-12 | Copyright 2026 Anthropic, PBC; no licence stated for the post |
| https://code.claude.com/docs/en/workflows | CC: Claude Code documentation for dynamic workflows (fetched as Markdown from the `.md` form of the URL). | 2026-11-12 | Anthropic documentation; no licence stated on the page |
| https://code.claude.com/docs/en/agents | Claude Code "Run agents in parallel" comparison page. | 2026-11-12 | Same |
| https://code.claude.com/docs/en/agent-sdk/typescript | Agent SDK reference; only the `Workflow` tool input and output types. | 2026-11-12 | Same |
| https://pypi.org/pypi/pydantic-ai-harness/json and https://pypi.org/pypi/pydantic-monty/json | Package versions and release dates. | 2026-11-12 | Metadata only |
| GitHub API: releases of `pydantic/pydantic-ai` and `pydantic/pydantic-ai-harness`, commit lists for the package, its Temporal test and its docs page | Release notes and dates used in section 9. | 2026-11-12 | Metadata only |

## 1. What it is

`DynamicWorkflow` is a capability in the `pydantic-ai-harness` package (the official capability library for Pydantic AI). It gives the model of an orchestrating agent exactly one tool, `run_workflow`, with one string argument, `code`. The model writes a Python script. Inside the script each sub-agent in a host-supplied catalog is an `async` function with the fixed signature `(*, task: str)`. The script runs to completion inside one tool call in the Monty sandbox (a subset of Python, from the `pydantic-monty` package), and only the script's final value, plus anything it printed, returns to the orchestrator's context (DOC:14-20; TS:1-6).

The docs position it for the case where "the coordination *between* sub-agents is the actual work" (DOC:8): fan-out, chaining, voting and retry loops that would otherwise take one model turn per step with every intermediate result flowing through the orchestrator's context.

How it relates to its neighbours in the same library:

- Code Mode uses the same sandbox and the same host-side executor. In Code Mode the script calls the agent's own tools; here it calls whole sub-agents (DOC:20; MX:6-9).
- `SubAgents` exposes one `delegate_task(agent_name, task)` tool; each delegation is its own tool call and model turn. The docs say to start with `SubAgents` if unsure, and that its sub-agents move to a workflow catalog unchanged (DOC:24-29).

Each sub-agent call is a full `Agent.run` with its own model loop, message history, tools and typed output. Calls are isolated: a sub-agent sees only the `task` string, never the parent conversation, and remembers nothing from earlier calls (DOC:79; T1:864-883). The parent run's `deps` are forwarded (T1:847-861).

It is a synchronous tool call of the parent run. A migration reference shipped inside the package says of this capability and its Deep Agents counterpart: "Neither is a durable background task service." (`src/pydantic_ai_harness/pydantic_ai_harness/.agents/skills/migrating-deep-agents-to-pydantic-ai-harness/references/ORCHESTRATION-AND-RECOVERY.md`, line 10).

## 2. Vocabulary

| Term | Meaning in this source |
|---|---|
| Orchestrator | The agent that carries the `DynamicWorkflow` capability. Its model writes the script. |
| Sub-agent | A Pydantic AI agent (`AbstractAgent`) in the catalog. A leaf: it is not offered `run_workflow` while it runs inside a script. |
| Catalog | The list of sub-agents the script may call. Normalised to a list of `WorkflowAgent` entries. |
| `WorkflowAgent` | A frozen dataclass wrapping one agent with an optional per-use `name` and `description` override (TS:434-467). |
| `run_workflow` | The single tool the model sees. Name is configurable (`tool_name`). |
| Script | The Python source passed as the `code` argument. Runs once per tool call in a fresh sandbox. |
| Monty | The sandbox (`pydantic-monty` package). The host drives it through a snapshot API: the sandbox suspends at each external call and the host resumes it. |
| Dispatch | The host-side handling of one sandbox call to a sub-agent function: validate, check budget, run the agent, return the output (TS:802-811). |
| Completed dispatch | A record `(agent_name, task, result)` of a sub-agent call that finished in the current script (TS:311-317). Source of the bounded previews. |
| Budget | `max_agent_calls`: the exact ceiling on sub-agent runs per parent run. |
| Terminal result | The dict returned as a normal tool result when the budget is exhausted, instead of a retry (TS:361-376). |
| Retry | A `ModelRetry` raised by the tool. The model gets the message and may call the tool again, up to `max_retries`. |
| Baseline | The agents present when a run started. They appear in the frozen tool description (TS:567-568). |
| Reveal | Adding a sub-agent mid-run with `DynamicWorkflow.reveal()`. |
| Announcement | The system-level message that tells the model about a revealed agent (TS:269-287). |
| Capability, toolset | Pydantic AI extension units. The capability (`DynamicWorkflow`) contributes one toolset (`DynamicWorkflowToolset`). |
| `defer_loading` | Core Pydantic AI feature: a capability is collapsed to a one-line catalog entry until the model loads it with `load_capability`. |
| `TemporalDurability`, `AgentPlugin`, `PydanticAIPlugin` | Pydantic AI's Temporal integration: a capability that routes an agent's model requests and tool calls through activities, and the worker and client plugins that register them. |

## 3. How it works

### 3.1 Construction

1. The host builds `DynamicWorkflow(agents=[...])`. Each entry is an agent or a `WorkflowAgent`; bare agents are wrapped as `WorkflowAgent(agent)` (CAP:116-129).
2. Names are validated at construction: each entry needs a name (explicit or the agent's own), it must be a Python identifier that is not a keyword, and names must be unique; the list must not be empty (TS:470-509). Settings are validated too: `max_agent_calls` must be at least 1 and `resource_limits` keys must be known (TS:488-494). Failures raise `UserError` at the constructor (T1:245-290, 688-701).
3. `get_toolset()` creates a `DynamicWorkflowToolset` that shares the capability's catalog list object by reference, so later `reveal()` calls reach toolsets that already exist (CAP:145-160).
4. The toolset indexes the agents by name and renders the tool description once (TS:584-596). For each agent run, `for_run` makes a shallow clone with the call counter reset to 0 and the description rendered again from the agents present at that moment (TS:602-614). The description is then frozen for the run.

### 3.2 What the model sees

One tool (T1:546-567 asserts the model's tool list is `['run_workflow']`). Its definition (TS:698-709):

- Parameters: a JSON schema with one required string property `code`, described as "The Python orchestration script to execute in the sandbox." (TS:142-148).
- `sequential=True`, so two `run_workflow` calls in one model response never overlap (T1:479-487; core meaning of the flag at `pydantic_ai_slim/pydantic_ai/tools.py:740-748`).
- Metadata `{'code_arg_name': 'code', 'code_arg_language': 'python'}`.
- `max_retries` from the capability setting.
- A description made of three parts (TS:259-266): a fixed instruction text, one budget sentence, and the catalog rendered as Python.

The fixed instruction text (`_WORKFLOW_BASE_DESCRIPTION`, TS:158-198) covers, in this order:

1. Purpose: write and run an orchestration script to coordinate several sub-agents in one step, instead of delegating to one at a time.
2. Sandbox restrictions: Monty is a subset of Python; no third-party libraries; importable standard-library modules are `sys`, `typing`, `asyncio`, `math`, `json`, `re`, `unicodedata`, `datetime`, `time` and `random`; no filesystem or environment access; no clock or randomness (`datetime.datetime.now()`, `datetime.date.today()`, `time.time()` and unseeded `random` fail); `time.sleep` and `asyncio.sleep` really wait.
3. Calling convention. Key rules, quoted from TS:173-179: "Each sub-agent below is an async function. Await it and pass `task` by keyword"; "all parameters are keyword-only"; "Each sub-agent call is an independent run with no memory of earlier calls; include all needed context in `task`." A sub-agent returns a string by default, or a dict for a structured `output_type`, read by subscript, not attribute.
4. Concurrency: use `asyncio.gather` rather than awaiting sequentially, with a two-call example. `asyncio.gather` takes positional awaitables and no keyword arguments, "including `return_exceptions=True`"; other task creation and wait APIs are unavailable (TS:186-187).
5. Failure: "A sub-agent failure surfaces as `RuntimeError`: catch it with `try`/`except RuntimeError`, or let it abort the whole script and retry." (TS:187-189).
6. Results: "The last expression's value is captured as the result -- you do **not** need to `print()` it" (TS:191). `print()` is for debug logging. The three return shapes are listed (section 5.4), and the text says previews of up to the 20 most recent completed results are reported when a script fails.

The budget sentence (TS:262-265), with the configured number filled in:

> This run can make at most {max_agent_calls} sub-agent calls in total -- one budget shared across every `run_workflow` call in the run, not per script; plan fan-out width accordingly.

The catalog. Each agent is rendered through core's `FunctionSignature` renderer, the same one Code Mode uses (TS:201-256; `pydantic_ai_slim/pydantic_ai/function_signature.py:296-444`):

- Parameters come from a fixed schema: an object with one required string property `task` (TS:207-211). That renders as `(*, task: str)`.
- The return type comes from the agent's output JSON schema (`agent.output_json_schema(...)`, TS:214-223). Structured outputs render as `TypedDict` classes placed before the functions. If the schema cannot be derived, the return type is `Any` (T1:443-454).
- `DeferredToolRequests` and `PendingModelResponse` are removed from the output type before rendering, because a script cannot resume such a run (SAO:28-44; T1:457-476).
- The description (override, else the agent's own `description`) becomes the function docstring. An explicit empty string hides the agent's own description (TS:461-467; T1:225-231).
- Type names that collide across agents are disambiguated (TS:243-246).

The test snapshot shows the exact listing for two agents, one with a Pydantic model output and one with a string output (T1:411-427):

```python
class Review(TypedDict):
    score: int
    """Score from 0 to 10."""
    note: str

async def reviewer(*, task: str) -> Review:
    """Reviews code for bugs."""
    ...

async def summarizer(*, task: str) -> str:
    """Summarizes findings."""
    ...
```

### 3.3 One `run_workflow` call, step by step

1. The tool call arrives at `DynamicWorkflowToolset.call_tool` with `{'code': ...}` (TS:795-798).
2. Per-call host state is created: a `budget_exhausted` flag, an empty `completed_dispatches` list, a `dispatch` closure, a print collector, the type-check stubs, and a fresh `MontyRunState`. A context variable `_in_workflow` is set to true for the duration of the call (TS:799-818).
3. A sandbox session is opened: `monty.get_session(type_check=True, type_check_stubs=..., limits=..., in_temporal_workflow=...)` (TS:820-822). With the defaults this spawns a local Monty worker pool (`AsyncMonty()`, local subprocess workers) and checks out one session with the limits, the stubs and an OS policy (MX:156-193). A checkout refused for capacity (`caller_cap`, `capacity`, `worker_unavailable`) is retried once after 0.1 s (MX:195-204).
4. The script is fed to the session with `session.feed_start(code, print_callback=...)`, driven by `MontyExecutor.run` (TS:827-832). The static type check runs here, before any script code executes (section 3.4).
5. The executor loops until the sandbox reports completion (MX:304-316). The sandbox suspends in one of three ways:
   - A name lookup for an unknown global. The host leaves it undefined so the sandbox raises `NameError` (MX:309-311).
   - A function call to a host function. For a sleep, the host does the waiting (MX:395-419). For a sub-agent name, the host checks the name is in the catalog and that no positional arguments were passed, creates the dispatch coroutine, schedules it at once as an `asyncio.Task`, stores it under the sandbox's call id, and resumes the sandbox with a future placeholder (`ExternalFuture`) so the script continues without waiting (MX:356-393, 446-454).
   - An await on pending futures. The host awaits all the pending tasks the sandbox is waiting on with `asyncio.gather(..., return_exceptions=True)`, wraps each outcome as a return value or an exception, and resumes the sandbox with all of them (MX:456-479).
6. So `await reviewer(task=...)` is a function-call suspension (the sub-agent run starts) followed by a future suspension on one id. `asyncio.gather(reviewer(task="a"), reviewer(task="b"))` is two function-call suspensions (two tasks start, one per call expression) followed by one future suspension that waits on both. That is how `asyncio.gather` in the script maps to concurrent sub-agent runs on the host.
7. Each dispatch (TS:802-811) validates the keyword arguments (TS:416-431), then calls `_run_one` (TS:712-767): budget check and increment, then `await agent.run(task, deps=ctx.deps, model=..., usage=..., usage_limits=..., capabilities=[ServingModelRecorder()])`, then `to_jsonable_python(result.output)` so the script receives JSON-compatible values (a dict for a Pydantic model). On success the dispatch appends a completed-dispatch record and returns the output to the sandbox.
8. When the sandbox completes, the value of the last expression and the collected print output are shaped into the tool result (TS:885-891, 302-308).
9. In a `finally` block the context variable is reset and the session and pool are closed (TS:879-881). Every `run_workflow` call is a fresh sandbox; no script variables survive between calls (TS:775-776).

Details worth knowing:

- A sub-agent run starts when the script evaluates the call expression, not when it awaits it, because the host schedules the task immediately (MX:381-393, 446-454). INFERRED: a call that is never awaited still runs and spends budget, and is cancelled if it is still running when the script ends, because the executor cancels every task left in its pending map in its `finally` (MX:316-338).
- In a gathered batch, one failing call does not cancel its siblings. The host waits for all of them to settle before the script sees the exception (MX:470-479, `return_exceptions=True` on the host side). The siblings' results are recorded as completed dispatches (T1:666-685).
- On cancellation of the tool call, the executor cancels in-flight sub-agent tasks and awaits them, shielded, before returning (MX:316-338; T1:1318-1341).
- The code contains no concurrency limit on sub-agent runs other than the call budget: every call in a `gather` is scheduled at once. INFERRED from the absence of any semaphore or queue in TS and MX.

### 3.4 The static type check before execution

- Tool: Monty's built-in checker. The Monty docs say Monty "bundles ty, Astral's type checker, in the same binary" and checks against `monty-typeshed`, a trimmed typeshed describing Monty's own runtime surface; a snippet that fails the check does not run (https://pydantic.dev/docs/monty/concepts/type-checking/).
- What the harness supplies: stubs for the host functions, built by `_build_type_check_stubs` (TS:769-788). The stub text starts with `import asyncio` and `from typing import Any, TypedDict, NotRequired, Literal`, followed by the same type definitions and `async def` signatures the model was shown, with the body `raise NotImplementedError()`. Baseline agents are rendered as in the frozen description and each revealed agent as in its announcement, so a type name the model read is the one the checker knows (T1:1573-1599).
- What it catches (all asserted in T1): a call to a function that does not exist (742-746), a positional `task` (757-761), a wrong-typed argument such as `task=123` (780-786), and syntax errors, because the checker parses first (1019-1024, 1053-1054).
- What it cannot catch: values typed `Any`, for example results of `json.loads` or `**` unpacking. A runtime guard covers those: a missing `task`, an extra keyword argument, or a non-string `task` raises `TypeError` in the script before the budget is touched (TS:416-431; T1:749-777). The executor also rejects unknown function names and positional arguments at run time (MX:356-363).
- What a failure costs: the tool raises `ModelRetry` with the text `Type error in workflow:` followed by the checker's diagnostics (TS:833-834). That uses one of the tool's retries. It uses no sub-agent budget and the script does not execute (DOC:219; T1:780-786 shows the full budget is still available afterwards).

### 3.5 What crosses the sandbox boundary

| Direction | What crosses |
|---|---|
| Host to sandbox, at start | The script source, the type-check stubs, the resource limits, the OS policy |
| Sandbox to host, per sub-agent call | The function name and keyword arguments (in practice one string, `task`) |
| Host to sandbox, per sub-agent call | The sub-agent's output converted to JSON-compatible Python, or an exception raised at the call site |
| Sandbox to host, during the run | Printed text (collected by a host callback), sleep requests |
| Sandbox to host, at the end | The value of the last expression |

Nothing else is reachable from the script. The OS policy routes clock, sleep and unseeded-randomness calls to the host (`_OS_POLICY`, MX:124-127). The workflow passes no OS handler, so clock and randomness are unavailable; sleeps are answered by the executor (MX:343-354, 395-419; T1:505-516).

### 3.6 State the host keeps

| Scope | State |
|---|---|
| Per capability instance | The catalog list, shared by reference with every toolset and run (CAP:60-61, 145-150) |
| Per agent run (toolset clone) | `_call_count` (the budget counter), the name index `_by_name`, the baseline name set, the frozen description, the set of reveal announcements queued in the current step (TS:560-577) |
| Per `run_workflow` call | `budget_exhausted` flag, `completed_dispatches` list, print collector, the Monty pool and session, the executor's pending-task map, pre-resolved results and total slept seconds (TS:799-818; MX:296-302) |
| Per asyncio task | The `_in_workflow` context variable (TS:63-68) |
| Inside the sandbox | The script's variables, for one call only |

There is no store. Nothing about a script or its results is persisted by this package. The only memory across `run_workflow` calls in one run is the call counter; the only memory of a failed script is the preview text placed in the retry message.

## 4. API and configuration surface

Install: `pip install "pydantic-ai-harness[dynamic-workflow]"` or `uv add "pydantic-ai-harness[dynamic-workflow]"`. The extra adds `pydantic-monty>=1.5.0,<2` (`src/pydantic_ai_harness/pyproject.toml:77`).

Imports: `from pydantic_ai_harness import DynamicWorkflow`, or from `pydantic_ai_harness.dynamic_workflow`, which exports `DynamicWorkflow`, `DynamicWorkflowToolset`, `WorkflowAgent`, `WorkflowResourceLimits` (package `__init__.py:10`). The old path `pydantic_ai_harness.experimental.dynamic_workflow` still works and emits a `HarnessDeprecationWarning`.

`DynamicWorkflow` is a keyword-only dataclass (CAP:23-109):

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `agents` | sequence of agent or `WorkflowAgent` | required | The catalog. Read at construction only; later mutation of the passed sequence is ignored (T1:343-353). |
| `tool_name` | `str` | `'run_workflow'` | Name of the tool. |
| `max_agent_calls` | `int` | `50` | Exact ceiling on sub-agent runs per parent run. Must be at least 1. |
| `max_retries` | `int` | `3` | Retries of the tool; type, syntax and runtime errors count. |
| `forward_usage` | `bool` | `True` | Share the parent run's usage counter with sub-agents. |
| `inherit_model` | `bool` | `False` | Run sub-agents on the parent run's resolved model. |
| `sub_agent_usage_limits` | `UsageLimits` or `None` | `None` | Limits applied to each sub-agent run. |
| `resource_limits` | `WorkflowResourceLimits`, `'unlimited'` or `None` | `None` | Sandbox limits for the script itself. |
| `id` | `str` or `None` | `None` | Capability id (core field). Required with `defer_loading=True`. Also becomes the toolset id. |
| `description` | `str` or `None` | `None` | One-line catalog entry shown while deferred (core field). |
| `defer_loading` | `bool` | `False` | Hide the tool until the model loads the capability (core field). |

Other members:

- `workflow.reveal(agent)`: append a sub-agent mid-run; validates first and raises `UserError` on a bad or duplicate name (CAP:131-143).
- `get_serialization_name()` returns `None`: the capability cannot be built from a spec file because it holds live agent objects (CAP:111-114).

`WorkflowAgent(agent, name=None, description=None)` is a frozen dataclass; `agent` may be passed positionally (TS:434-467). `name` falls back to `agent.name`. `description=None` falls back to `agent.description`; `''` hides it.

`WorkflowResourceLimits` is a `TypedDict` with all keys optional (TS:71-93):

| Key | Type | Meaning |
|---|---|---|
| `max_duration_seconds` | `float` | Ceiling on time spent executing sandbox code. No default. |
| `max_duration_secs` | `float` | Deprecated name of the above; still accepted with a warning. |
| `max_memory` | `int` | Sandbox memory in bytes. Backstop default 256 MiB. |

`DynamicWorkflowToolset` (TS:512-577) takes the same settings plus `toolset_id` and `owning_capability`, and requires `agents` to be a list of `WorkflowAgent`. Its `id` is `toolset_id` or the tool name (TS:598-600). The docs list it as exported "for advanced use" (DOC:264).

What the script can use: the sub-agent functions; the listed standard-library modules; `asyncio.gather` with positional awaitables; `print`; `try`/`except RuntimeError` around sub-agent calls. What a sub-agent call returns inside the script (README:266-272): the string for a `str` output, a dict for a Pydantic model, the list or scalar for those types.

## 5. Limits, budgets and failure handling

### 5.1 `max_agent_calls`: the exact ceiling

- The counter lives on the per-run toolset clone and is shared by every `run_workflow` call in that run (TS:560-561, 602-614; T1:704-716, 1444-1449).
- Enforcement is two adjacent statements at the start of `_run_one`: `if self._call_count >= self.max_agent_calls: raise _BudgetExhausted(...)`, then `self._call_count += 1` (TS:728-730). There is no `await` between the check and the increment. asyncio switches tasks only at suspension points, so the pair is atomic across concurrently gathered dispatches. The docstring states the rule and the consequence of breaking it: insert an `await` there "and the count can race past the limit; you would then need an explicit reservation instead" (TS:715-720).
- The test fans eight calls out under a budget of three and asserts exactly three sub-agent runs happened (T1:639-663).
- The docstring also says why the usage limits cannot do this job: core's limit check is split from its increment by the model-request `await`, a check-then-act race under fan-out with "measured ~20x overshoot" (TS:722-726).
- The counter is incremented before the sub-agent runs, so a sub-agent run that fails still spends budget. This follows from the statement order (TS:728-730 before TS:744) and is consistent with T1:666-685, where a failing call uses one of two budget slots. Calls rejected by the argument guard do not spend budget (TS:418-422, 804).
- Inside the script, an over-budget call raises a `RuntimeError` subclass with the message `sub-agent call budget (N) exhausted` (TS:151-155). The script can catch it, but exhaustion stays terminal for the tool call: a host-side flag, not the displayed error, decides (TS:840-849, 883-891; T1:1292-1315).
- Once the flag is set, the tool returns a normal result, not a retry (T1:570-614 checks no retry part is produced):

```python
{
    'error': 'This run exhausted its sub-agent call budget (N). Conclude using the results already gathered; '
             'further sub-agent calls in this run will be refused.',
    'last_error': '<the sandbox error text, or a fixed sentence>',
    'completed': ['<preview line>', ...],
}
```

### 5.2 `sub_agent_usage_limits` and `forward_usage`: token and request budgets

From TS:739-751 and CAP:73-99:

| `forward_usage` | `sub_agent_usage_limits` | Counter passed to the sub-agent | Limits passed to the sub-agent |
|---|---|---|---|
| `True` (default) | `None` | The parent run's `usage` object | The parent run's `usage_limits`, checked against the shared counter, so they bound the whole tree |
| `True` | set | The parent run's `usage` object | The given limits, checked against the shared counter. They replace the parent's limits; they do not intersect them (T1:992-999). |
| `False` | set | A fresh counter per run | The given limits, per run |
| `False` | `None` | A fresh counter per run | Pydantic AI's default `UsageLimits()`: `request_limit=50`, no token limit (`pydantic_ai_slim/pydantic_ai/usage.py:559`) |

- Tree-wide limits are best-effort under concurrent fan-out: several sub-agents can pass the check before any adds to the count (DOC:140; TS:722-726).
- With `forward_usage=False`, a per-run `total_tokens_limit` of `T` and `max_agent_calls` of `N` bound the tree to roughly `N * T` tokens; each run can overshoot by its final response because core checks token limits after a response arrives (CAP:93-96).
- A sub-agent that hits a usage limit fails its call. The script sees `RuntimeError` whose message includes the limit text, the one case where the underlying exception message is passed through (TS:108, 757-763; T1:886-924).

### 5.3 `resource_limits`: guarding the script itself

Resolution (TS:96-139):

- `None`: backstop of `max_memory = 256 * 1024 * 1024` bytes and no duration cap.
- `'unlimited'`: no sandbox limits at all.
- A dict: merged onto the backstop; only the named caps change. Unknown keys raise `UserError`, so a typo cannot silently disable a cap (TS:103-107, 129-133; T1:1431-1438).
- `max_duration_seconds` is passed to Monty as `max_feed_duration_secs` (TS:137-138).

`max_duration_seconds` semantics (TS:79-87; DOC:153):

- It is a ceiling on time spent executing sandbox code. Monty checks it per bytecode step.
- Time spent awaiting sub-agents does not count, for a single `await` or a `gather` batch, because the script is suspended on the host. A test runs three sub-agents that each sleep 0.2 s under a 0.1 s cap and the workflow completes (T1:1378-1392).
- Its purpose is to stop a pure-CPU loop such as `while True:` that never awaits. No sub-agent budget can stop that, because it never calls a sub-agent. Without a cap such a loop burns a core in the sandbox worker and, under Temporal, blocks the workflow thread (TS:83-85).
- Sleeping is not execution time. When a cap is set, the script may also sleep for at most that long in total; a sleep that would exceed it raises `TimeoutError` in the sandbox without waiting (MX:288-290, 395-409; T1:519-522).
- There is no default duration cap.

Printed output is collected separately under Monty's 10 MiB default cap, which stays in force even with `'unlimited'` (DOC:147, 153). Exceeding it produces a runtime-error retry (T1:1199-1202).

INFERRED: Monty's own suspension budget probably also applies. The Code Mode docs say Monty limits cumulative suspensions with `max_suspensions`, "default 1,000 per session", that external calls, name lookups and future resolutions each consume it, and that `'unlimited'` leaves it in place (CM:215-221, 236-239). `WorkflowResourceLimits` has no key for it and unknown keys are rejected, so a workflow script cannot raise it. The DynamicWorkflow docs do not mention this. With the default budget of 50 calls it is unlikely to matter.

### 5.4 Result shaping

`_workflow_result` (TS:302-308; DOC:110-114; T1:1187-1215):

| The script | The model receives |
|---|---|
| ends in a value, prints nothing | that value (or `{}` if the value is `None`; falsy values such as `0`, `''`, `False` are returned as they are) |
| prints and ends in a non-`None` value | `{'output': '<printed text>', 'result': <value>}` |
| prints and ends in `None` | `{'output': '<printed text>'}` |

If the budget was exhausted during the script, the terminal result of section 5.1 replaces whatever the script returned, even when the script caught the error and finished normally (TS:398-413; T1:1307-1315).

### 5.5 Failure handling

How each outcome of a script is turned into a tool result (TS:833-878):

| What happened | Without budget exhaustion | With budget exhaustion |
|---|---|---|
| Type error (`MontyTypingError`) | Retry: `Type error in workflow:` plus diagnostics | Same (raised before any call can run) |
| Syntax error (`MontySyntaxError`) | Retry: `Syntax error in workflow:` plus diagnostics. A backstop; the checker normally reports syntax errors first. | Same |
| Uncaught runtime error in the script (`MontyRuntimeError`), including an uncaught sub-agent failure, a memory or duration limit, the print cap | Retry: `Runtime error in workflow:` plus the sandbox's error text plus the completed-results section | Terminal result, with the sandbox error in `last_error` |
| Sandbox worker died (`MontyCrashedError`) | Retry: "The workflow script crashed the sandbox worker. Revise the script and try again." plus the completed-results section | Terminal result |
| Rust-side sandbox panic (matched by exception class name, MX:236-244) | Retry: "The workflow script aborted inside the sandbox. Revise the script and try again." plus the completed-results section | Terminal result |
| Any other `BaseException`, for example cancellation | Re-raised unchanged | Re-raised unchanged |

Other rules:

- When a script printed before failing, the printed text is placed in front of the error between `[stdout before error]` and `[/stdout before error]` markers (MX:257-262; T1:1218-1221).
- A sub-agent failure reaches the script as `RuntimeError` with the message `sub-agent '<name>' raised <ExceptionType>`. The original message is withheld so host internals (paths, object reprs) do not leak to the model; only `UsageLimitExceeded` messages are passed through (TS:757-763; T1:1344-1366).
- A sub-agent run that ends with `DeferredToolRequests` or `PendingModelResponse` fails the call with a `RuntimeError` saying the workflow script cannot resume it. A pending provider request is cancelled, best effort (TS:752-767; SAO:47-85; T1:1250-1271). A sub-agent that resolves its own deferred calls with `HandleDeferredToolCalls` returns its output normally (T1:1283-1289).
- Errors from setting up the sandbox (pool creation, session checkout after the one retry) are not caught by any of the clauses above. INFERRED: they propagate to the code calling the agent, as the Code Mode docs describe for the same executor (CM:619-623).

### 5.6 Bounded previews of completed results

When a script fails after some sub-agent calls finished, or the budget runs out, the host lists what completed so the model can reuse values instead of paying again. Constants at TS:109-112; formatting at TS:320-358:

- At most the 20 most recent completed dispatches are shown (`_MAX_COMPLETED_DISPATCHES = 20`). If more completed, a first line says `... N earlier completed result(s) omitted ...`.
- Each line has the form `<agent_name>(task=<JSON string>) -> <compact JSON of the result>`, for example `counted(task="first") -> "counted-result"` (T1:606).
- The task is cut to 120 characters and the result JSON to 300 characters; a cut value ends with ` ... [truncated]`, and the marker counts toward the limit.
- In a retry message the section is introduced by: "Completed sub-agent results from the failed script (up to 20 bounded previews; reuse untruncated values instead of re-calling them; their budget was already spent):". In a terminal result the same lines are the `completed` list.
- Tests: T1:1066-1097.

This is the only reuse mechanism. Nothing replays or caches completed calls for the retried script; the model has to paste an untruncated preview back into the new script as a literal. A result longer than 300 characters of JSON is not recoverable this way.

### 5.7 Retries

`max_retries` (default 3) is the tool's retry limit (TS:529-530, 707). Each `ModelRetry` uses one. When the limit is exceeded, the agent run fails with `UnexpectedModelBehavior`: "Tool 'run_workflow' exceeded max retries count of 2" in the test that sets `max_retries=2`, after three model calls and two retry messages (T1:1027-1054). A budget-exhaustion terminal result is not a retry.

### 5.8 No nesting

A context variable `_in_workflow` is set while a script executes. `get_tools` returns no tools when it is set, so a sub-agent that itself carries `DynamicWorkflow` is not offered `run_workflow` while it runs inside a script. asyncio copies the context into each task, so concurrently dispatched sub-agents inherit the flag; the comment adds "the capability is asyncio-only" (TS:63-68, 688-691, 817, 880). The same agent run on its own still gets the tool (T1:719-739). The variable is module-level, so the rule holds across different `DynamicWorkflow` instances. The docs' practical advice: do not give catalog sub-agents the capability (DOC:155-157).

### 5.9 `reveal()`, `defer_loading`, `inherit_model`

`reveal()` (CAP:131-143; TS:616-658; DOC:179-191):

- The catalog is fixed when a run starts so the tool description stays in the prompt-cache prefix. `reveal(agent)` appends to the shared catalog list.
- At the start of the next model step (`get_tools`), the toolset compares the live list with its name index, adds newcomers to the index so the script can call them, and enqueues an announcement. The tool description is not re-rendered (T1:1706-1716).
- The announcement is a `SystemPromptPart` whose content is "A new sub-agent is now available to call from inside the `run_workflow` script:" followed by the new function's signature block, with metadata keys `dynamic_workflow_tool` and `dynamic_workflow_revealed_agent` (TS:113-115, 269-287).
- The toolset looks for that metadata in the message history after the latest compaction (`post_compaction_window`). If compaction or a history processor removed the announcement, it is announced again on the next step. Text that merely looks like the announcement, without the metadata, does not count (TS:290-299; T1:1602-1703).
- If the run context has no pending-message queue, the reveal still makes the agent callable and a `UserWarning` says the model will not see the announcement (TS:651-658; T1:1631-1643).
- Append-only: a revealed sub-agent cannot be removed for the rest of the run. One `DynamicWorkflow` instance shared across concurrent runs reveals to all in-flight runs, and the agent joins the baseline of runs that start afterwards (CAP:136-139; T1:1468-1487).

`defer_loading` (DOC:193-205; core docs `docs/capabilities/on-demand.md`):

- A core feature. With `defer_loading=True` and a stable `id`, the capability collapses to a one-line catalog entry (its `id` plus optional `description`). The model calls the framework's `load_capability` tool to load it; the tool and its description appear on the next request. Loaded state lives in message history, which is why the `id` must be stable.
- The DynamicWorkflow-specific part: while the capability is deferred and unloaded, reveals are held back so a signature does not leak into the conversation; they are folded in and announced on the first step after loading. Visibility is resolved against the run's capability registry by object identity, through wrapper capabilities (TS:660-697; T1:1774-1872).

`inherit_model` (TS:731-737; CAP:82-88):

- `False`: each sub-agent uses the model it was constructed with.
- `True`: each sub-agent run is passed the parent run's resolved model, for hosts that switch models per run. If the parent's model is not a request-response `Model` (a realtime run), the sub-agent falls back to its own model. Tests: T1:804-844.

## 6. Running under Temporal

### 6.1 What the documentation says

The whole section is three sentences (DOC:224-231). The first says to attach `TemporalDurability` to the orchestrating agent alongside `DynamicWorkflow` and to each sub-agent whose model requests should run as activities, then to register every agent with its own `AgentPlugin`. The other two, quoted:

> "The script runs in workflow code, like Code Mode's `run_code`, and is re-executed during replay against the recorded sub-agent results. Unlike Code Mode, a `max_duration_seconds` you set still applies inside the workflow, so a script that runs close to it can stop at a different point on replay and cause a `NondeterminismError`."

### 6.2 What the package code does

- Detection. `in_temporal_workflow()` reads `sys.modules` for `temporalio.workflow` and calls `workflow.in_workflow()`; the Temporal extra is never imported by the harness itself (MX:68-74). `call_tool` passes the result to the session setup (TS:813, 820-822). This is the only Temporal-specific branch in the package.
- The blocking portal. Monty's Python bindings are async and complete each call by waking the event loop the call started on. A Temporal workflow's event loop cannot be woken that way. Inside a workflow the helpers therefore route every Monty call through an `anyio` blocking portal: a helper thread with a normal asyncio loop. "The workflow thread blocks until the sandbox suspends or completes", and control is back in the workflow between calls (MX:77-95, 168-171). Outside a workflow the portal is `None` and every call is a plain `await`.
- No change to limits. `call_tool` passes the configured limits unchanged in a workflow (TS:814, 820-822). Code Mode instead sets `max_feed_duration_secs` to `None` inside a workflow, with the comment that an elapsed timer "may make the original run and replay take different branches, which Temporal cannot record" (`src/pydantic_ai_harness/pydantic_ai_harness/code_mode/_toolset.py:1272-1276`). This difference is the caveat the docs describe.
- Concurrency mode. The call site comment says: "Sub-agents always run concurrently (the executor's defaults); durable ordering (global_sequential) lands with durability." (TS:825-826). Code Mode passes the run's parallel-execution mode to the executor (`code_mode/_toolset.py:1253, 1293`); the workflow toolset does not. A test notes that the executor's sequential branch is "unreachable through DynamicWorkflowToolset" (T1:1494-1496).

### 6.3 Where things run and which Temporal primitive each becomes

Why the script runs in workflow code: `TemporalDurability` wraps only leaf toolsets of the kinds it knows (function toolsets, dynamic toolsets, MCP toolsets) into activities. Any other leaf toolset is returned unchanged and its `call_tool` runs inside the workflow (`pydantic_ai_slim/pydantic_ai/durable_exec/_base.py:1270-1298`; `pydantic_ai_slim/pydantic_ai/durable_exec/temporal/_durability.py:420-436`; `pydantic_ai_slim/pydantic_ai/durable_exec/temporal/_toolset.py:409-473`). `DynamicWorkflowToolset` subclasses `AbstractToolset` directly (TS:513), so it is one of the unchanged ones. Core's own comment calls this pass-through for unknown leaf types a "blind spot" (`pydantic_ai_slim/pydantic_ai/durable_exec/_runtime_toolsets.py:13-17`). TEMP:201-211 states the same from the user's side: activities are created for model requests and for `FunctionToolset` and `MCPToolset`.

| Thing | Where it runs | Temporal primitive |
|---|---|---|
| The orchestrator's agent loop | Workflow code | None of its own |
| Each orchestrator model request | Activity worker | One activity, `agent__<orchestrator name>__model_request` (or `..._model_request_stream` when streaming), started with `workflow.start_activity` (`durable_exec/temporal/_operation_names.py:29-39`; `durable_exec/temporal/_activity_execution.py:38-40`) |
| The `run_workflow` tool call: type check, sandbox, script | Workflow code, with Monty on a helper thread and worker subprocess | None. Not an activity, not a child workflow. |
| A sub-agent call from the script | Workflow code: an `asyncio.Task` on the workflow's event loop running `Agent.run` | None of its own |
| Each model request of a sub-agent that carries `TemporalDurability` | Activity worker | One activity, `agent__<sub-agent name>__model_request` |
| Each function-tool or MCP call made by such a sub-agent | Activity worker | By default one activity per call, named from the sub-agent's name and the toolset id (`_operation_names.py:46-55`) |
| A sleep in the script | Workflow code | A durable timer: the executor awaits the sleep on the run's own event loop (MX:124-127, 292, 410-419) |
| `asyncio.gather` over several sub-agents | Workflow code | Several activities scheduled concurrently |

What is recorded in history: the activity events, with their inputs and results, for every model request and wrapped tool call of the orchestrator and of durable sub-agents, plus timers. The script text is not recorded as its own event; it is part of the result of the orchestrator's model-request activity that produced the tool call. INFERRED from the mapping above. All of it lands in the one workflow's history: Pydantic AI can run an async function tool as a child workflow (TEMP:942-1014), but `run_workflow` is not a function tool, so that option does not apply here. INFERRED.

INFERRED: because sub-agent runs execute in workflow code, the shared `usage` counter and the call budget are updated in the workflow itself and are recomputed the same way on replay. The Temporal docs describe the opposite case, a delegation made from inside an activity, where the delegate's usage stays behind in the activity (TEMP:237). The `SubAgents` docs state that on Temporal a delegation to an agent other than the agent itself runs in an activity by default, or as a child workflow when configured (`docs/harness/subagents.md:540-571`); DynamicWorkflow has neither behaviour.

Activity defaults come from `TemporalDurability`, not from this package: a 60-second `start_to_close_timeout` unless configured, a 30-second heartbeat timeout on model-request activities only, and Temporal's default retry policy, which the Temporal docs page describes as "unbounded re-execution of the model request" (TEMP:877-886). The other digests in this folder cover the integration in depth.

Replay: Temporal re-runs the workflow code. The orchestrator's recorded model response supplies the same `code`; the type check runs again; a new sandbox session executes the script again; each sub-agent's `Agent.run` runs again in workflow code and its model requests resolve from the recorded activity results instead of calling the model. The docs' phrase is "re-executed during replay against the recorded sub-agent results". The sandbox is process-local state, rebuilt on every replay; the Code Mode docs say the same of its REPL (CM:679-681).

### 6.4 Test evidence

T2 is the only Temporal test for this capability:

- Two module-level agents: `summarize` with `TemporalDurability`, and an orchestrator with `DynamicWorkflow(agents=[summarize_agent])` plus `TemporalDurability`, both using a 60 s `start_to_close_timeout` and `RetryPolicy(maximum_attempts=1)` (T2:47-50, 94-104).
- The workflow body is one line: `await orchestrator_agent.run(prompt)` (T2:107-112).
- The worker registers `AgentPlugin(orchestrator_agent)` and `AgentPlugin(summarize_agent)`; the client uses `PydanticAIPlugin()` (T2:73, 118-124).
- The script is `asyncio.gather(summarize(task='alpha'), summarize(task='beta'))` (T2:51). The workflow output is `"done: ['summary of alpha', 'summary of beta']"` (T2:131).
- The history contains exactly two scheduled activities of type `agent__summarize__model_request` (T2:133-139).
- The history is then replayed with `Replayer` and `replay_failure is None` (T2:141-146). The replayer runs no activities, so the script's sub-agent results came from history.

The test does not cover: a sub-agent without `TemporalDurability`, a failing script, budget exhaustion, `reveal()`, `inherit_model`, a duration cap, worker restarts, or continue-as-new.

### 6.5 Determinism hazards named in the code or docs

1. `max_duration_seconds`. It stays active in a workflow. A script that runs close to it can stop at a different point on replay and cause a `NondeterminismError` (DOC:230-231). Code Mode avoids the hazard by disabling the timer in workflows; this capability does not (section 6.2).
2. Clock and randomness. Unavailable in the script by policy. The comment gives the reason: without an OS handler "they are unavailable, which keeps sandbox code deterministic when a Temporal workflow replays it" (MX:124-126).
3. Blocking the workflow thread. The workflow thread blocks while the sandbox executes code. A pure-CPU loop "would otherwise burn a core in the sandbox worker (and block the workflow thread under Temporal)" (TS:83-85). The Code Mode docs add, for the same executor: "Temporal fails a workflow task that does not yield within 2 seconds, so move heavier computation into a tool" (CM:697-698). INFERRED that this applies to workflow scripts as well.
4. Sub-agents without `TemporalDurability`. The docs ask for it on "each sub-agent whose model requests should run as activities". INFERRED: a sub-agent without it makes its model requests inside workflow code, unrecorded. No guard against this was found and no test covers it.
5. Workflow-side errors. Per the Code Mode docs, `PydanticAIPlugin` treats `UserError` as a workflow failure, while other setup errors such as `RuntimeError` and `MontyRefusedError` fail the workflow task, which Temporal retries while the workflow keeps running (CM:683-689). INFERRED that the same holds here, since sandbox setup happens in the same place. The plugin also registers `AgentRunError` as a workflow-failure type, with a comment that it covers `UsageLimitExceeded` and `UnexpectedModelBehavior` (`pydantic_ai_slim/pydantic_ai/durable_exec/temporal/__init__.py:203-225`). INFERRED: a `run_workflow` tool that exhausts `max_retries` therefore fails the workflow rather than the workflow task.
6. Sandbox availability at replay. Every replay opens a Monty session on the replaying worker. The capability always uses local Monty workers (`MontyRunState()` with no remote URL, TS:818), so the worker needs `pydantic-monty` installed. `PydanticAIPlugin` passes `pydantic_ai_harness`, `pydantic_monty` and `anyio` through Temporal's workflow sandbox (`pydantic_ai_slim/pydantic_ai/durable_exec/temporal/__init__.py:125-145`).
7. Agents must exist before the workflow runs. An agent with `TemporalDurability` must be constructed outside a workflow so its activities can be registered with the worker (`durable_exec/temporal/_durability.py:342-348`). The per-run capability the workflow toolset adds to each sub-agent run (`ServingModelRecorder`) is marked safe for this: "It only observes the request, so it can be added to a sub-agent run under durable execution too." (SAO:65-66; the rule it satisfies is at `durable_exec/_base.py:861-880` and TEMP:316-320).

Other engines: a helper comment says the Temporal check exists "because only Temporal replays `run_code`" (`src/pydantic_ai_harness/pydantic_ai_harness/_durable.py:57-58`). The DynamicWorkflow docs have no section for DBOS, Prefect or the other engines, and its test folder has no test for them (Code Mode's has `test_dbos.py` and `test_prefect.py`).

## 7. Implementation notes from the source code

### 7.1 Files in the package

| File | What it does |
|---|---|
| `dynamic_workflow/__init__.py` (10 lines) | Re-exports `DynamicWorkflow`, `DynamicWorkflowToolset`, `WorkflowAgent`, `WorkflowResourceLimits`. |
| `dynamic_workflow/_capability.py` (160 lines) | The `DynamicWorkflow` dataclass. Holds configuration, normalises and validates the catalog, implements `reveal()`, and builds the toolset. Defines no lifecycle hooks and emits no events. |
| `dynamic_workflow/_toolset.py` (891 lines) | Everything else: resource-limit resolution, the instruction text, catalog rendering, reveal announcements, result and preview formatting, argument validation, `WorkflowAgent`, and `DynamicWorkflowToolset` with `get_tools`, `_run_one` and `call_tool`. |
| `dynamic_workflow/README.md` (513 lines) | User documentation. A superset of the published page: adds a complete runnable example (draft three answers in parallel, score them, pick the best, refine it) and a longer note on `max_duration_seconds`. |
| `experimental/dynamic_workflow/__init__.py` (22 lines) | Deprecated import location. Re-exports the same names and warns that the module moved. |

### 7.2 Shared modules the package depends on

| File | What it contributes |
|---|---|
| `_monty_exec.py` (MX) | `MontyRunState` (opens the worker pool and session lazily, closes both, owns the Temporal portal), `MontyExecutor` (the suspend-and-resume loop), `PrintCapture`, `in_temporal_workflow`, `is_sandbox_panic`. Shared with Code Mode; the only difference is the dispatch callback. |
| `_sub_agent_output.py` (SAO) | Removes resumable output types from a sub-agent's signature, explains why a run ended with one, records the serving model, cancels a pending provider request. Shared with `SubAgents`. |
| `_warn.py` | `renamed_duration_limit`: moves the deprecated `max_duration_secs` key to `max_duration_seconds` with a warning; raises `TypeError` if both are given (lines 154-175). |
| `_code_tools.py` | Keeps `run_workflow` out of Code Mode's sandbox. A tool whose metadata carries `code_arg_name` is treated as a code-execution tool and stays a native peer of `run_code` (lines 39-67; T1:1907-1947). |

### 7.3 Key data structures

- `_CompletedDispatch(agent_name, task, result)`: frozen dataclass; one per finished sub-agent call in the current script (TS:311-317).
- `_by_name: dict[str, WorkflowAgent]`: insertion-ordered name index. Order is baseline first, then reveal order; `_catalog_through(name)` uses that order to reproduce what the model knew when an agent was revealed (TS:790-793).
- `_baseline_names: frozenset[str]`: what the frozen description contains (TS:567-568).
- `MontyExecutor._pending: dict[int, PendingCall]`: Monty call id to `(task or coroutine, OpenTelemetry context)` (MX:228-233, 297-298).
- `_BudgetExhausted(RuntimeError)`: raised in the dispatch, visible to the script as a `RuntimeError` (TS:151-155).
- `_WorkflowArguments`: `TypedDict` with the single `code` field; its Pydantic schema and validator are built once at import (TS:142-148).

### 7.4 Key algorithms

- Exact budget under fan-out: an await-free check-then-increment (TS:712-730). The docstring names the alternative needed if an `await` is ever inserted: an explicit reservation.
- Reveal folding: on each `get_tools`, snapshot the shared list, add newcomers to the index and queue announcements, all without an `await`, "so a concurrently-running `dispatch` never observes a half-revealed agent" (TS:616-631). Announcements queued in the current step are tracked so the same step does not announce twice; the set is cleared when `run_step` changes (TS:631-633, 570-572).
- Frozen description: rendered in `_rebuild`, called from the constructor and from `for_run`, never after a reveal (TS:574-596).
- Type-name stability: conflicts are computed against the agents known when each agent was first shown, so a name the model already read is never renamed; a revealed agent whose type name collides gets a prefixed name such as `extra_Score` in its announcement (TS:235-256; T1:1541-1589).
- Host-side gather: `asyncio.gather(*tasks, return_exceptions=True)` over exactly the call ids the sandbox awaits; tasks stay in `_pending` until it returns so that cancellation cleanup can still reach them (MX:470-479).
- Cancellation cleanup: cancel tasks, close unscheduled coroutines, then await the cancelled tasks inside a shielded cancel scope so that sub-agent runs mutating shared usage have fully unwound (MX:316-338).
- Preview truncation: cut to `max_chars - len(marker)` and append the marker (TS:320-324).

### 7.5 Observability

- The docs recommend the Logfire trace: each sub-agent run is nested under the `run_workflow` span, and the span carries the `code` argument the model wrote. To watch sub-agent runs inside the one tool call, set `event_stream_handler` on each sub-agent agent (DOC:233-235).
- The `code_arg_name` and `code_arg_language` tool metadata are read by core instrumentation and attached to the tool-call telemetry (`pydantic_ai_slim/pydantic_ai/_instrumentation.py:784-787`); a core comment says these fields are not part of the OpenTelemetry spec and are used by Logfire (`pydantic_ai_slim/pydantic_ai/_otel_messages.py:44-45`).
- The executor attaches each sandbox call's trace context when it dispatches and when it awaits the result (MX:446-454, 482-491). A test shows that with Monty telemetry enabled, each sub-agent run span is a child of the Monty span named `call {function_name}` (`tests/harness/test_monty_tracing.py:59-65, 187-216`).
- The package defines no capability events and no lifecycle hooks of its own. `SubAgents` and Code Mode each have an `_events.py`; this package does not. A search of TS and CAP for hook methods found none.
- Under Temporal, `TemporalDurability` keeps telemetry of work that is being replayed out of traces and metrics (`durable_exec/temporal/_durability.py:147-149, 571-582`).

## 8. The pattern elsewhere

### 8.1 The Pydantic article

https://pydantic.dev/articles/dynamic-workflows, "Dynamic Workflows: feature that enabled the Bun rewrite", Aditya Vardhan, 2026-07-28, five minutes long.

- Presents the capability as a response to the Bun post. Its summary: "`DynamicWorkflow` is Code Mode moved up a level." Code Mode gives the model a script for its tools; this gives it a script for its agents.
- Repeats the Bun numbers: about half a million lines of Zig ported, a diff of just over a million lines, the whole existing test suite passing, eleven days from first commit to merge, about 50 workflows, 64 Claudes at peak, every file reviewed by two adversarial agents.
- Shows the same two-agent example and `reveal()`. Its sample imports from `pydantic_ai_harness.experimental.dynamic_workflow`, the path that is now a deprecated shim.
- Stresses tracing: the `run_workflow` span carries the script the model wrote.
- Says an orchestrator can be pointed at "a couple of agents (or a few hundred)". The article gives no measurement behind the larger number.
- Says nothing about Temporal, durability or resuming.

### 8.2 The Bun post

https://bun.com/blog/bun-in-rust, "Rewriting Bun in Rust", Jarred Sumner, 2026-07-08. The post discloses that Bun was acquired by Anthropic in December 2025 and that a pre-release Claude model was used.

What the pattern is, in the author's words: day-to-day engineering "over-simplified into loops": take a task, produce a result, have two reviewers review it, apply the feedback. "I rewrote Bun in Rust using about 50 dynamic workflows in Claude Code run continuously over the course of 11 days." Each workflow was such a loop for one goal: generate a porting guide; port every `.zig` file to a `.rs` file; fix each crate's compiler errors; get subcommands working; get every test passing; refactors and cleanup.

Scale:

- 1,448 `.zig` files ported. About 16,000 compiler errors fixed crate by crate.
- At peak, four workflows at once, each in its own git worktree, each with 16 Claudes: about 64 at a time.
- 6,502 commits excluding merges; the landed diff was +1,009,272 lines; peak of about 1,300 lines of code per minute.
- Before merge: 5.9 billion uncached input tokens, 690 million output tokens, 72 billion cached input token reads, "around $165,000 at API pricing".
- "Claude Code's dynamic workflows kept 64 Claudes running for 11 days (I would've had to write my own harness to pull this off otherwise)."

What the author says made it work:

- Three things named together: a language-independent test suite with a million assertions, adversarial code review, and "fixing the process that generates the code instead of hand-fixing the code".
- Adversarial review with split context windows: one implementer, two or more adversarial reviewers, one fixer. The reviewer is a separate Claude that "gets the diff and nothing else" and is told to find how it is wrong. The implementer does not review; the reviewer does not implement.
- De-risking first: about three hours of discussion turned into a `PORTING.md`; a first workflow produced a `LIFETIMES.tsv` for every struct field; a trial run on 3 files before all 1,448.
- Editing the workflow when it went wrong. Agents ran `git stash` and `git reset` and stepped on each other; the fix was to edit the workflow's instructions (no such git commands, no `cargo`, no slow commands) and split into four worktree shards. "Then, Claude resumed the workflows." When agents stubbed out functions to make code compile, one rule was added for the reviewers and the behaviour stopped.
- Isolation by resource limits rather than instruction for the test phase: `systemd-run` (cgroups) for memory, CPU and pid namespaces.
- Continuous human monitoring: the author read outputs and prompted Claude to edit the loops throughout the 11 days.

### 8.3 Claude Code's dynamic workflows

A public page exists: https://code.claude.com/docs/en/workflows (CC). The Agent SDK reference documents the tool's input and output types. Everything below is from those pages.

What it is: "A dynamic workflow is a JavaScript script that orchestrates many subagents at once. Claude writes the script for the task you describe, and a runtime executes it in the background while your session stays responsive." The script holds the loop, the branching and the intermediate results, so Claude's context holds only the final answer.

Script API (CC, "What the saved script looks like" and "Edit a saved script"):

- The file starts with `export const meta = { name, description }` as a plain object literal, optionally with a `phases` list. The body is plain JavaScript with top-level `await`; the page's example hands back its result with a top-level `return`.
- `agent(prompt, options)` spawns one subagent and resolves to its result. Options shown on the page: `schema` (a JSON Schema; the subagent then returns JSON matching it instead of prose), `label`, `stallMs`.
- `pipeline(items, fn)` runs one agent per item in a list. `parallel()` runs a set of agent tasks at the same time and waits for all of them.
- `phase(title)` groups the agents that follow in the progress view. `log()` shows a message. `args` is a global holding structured input passed at invocation.
- The page's example:

```javascript
export const meta = {
  name: 'audit-routes',
  description: 'Audit every route handler for missing auth checks',
}

const found = await agent('List every .ts file under src/routes/.', {
  schema: { type: 'object', required: ['files'], properties: { files: { type: 'array', items: { type: 'string' } } } },
})

const audits = await pipeline(found.files, file =>
  agent(`Audit ${file} for missing authentication checks.`, { label: file }),
)

return audits.filter(Boolean)
```

How sub-agents are called and how they fail:

- Sub-agents are addressed by prompt, not by name from a catalog. The workers are Claude Code subagents and use the session's permission rules.
- `agent()` resolves to `null` if it is stopped mid-run or hits an unrecoverable API error. Inside `pipeline()` or `parallel()` the run carries on with `null` in that slot; awaited directly, a failed agent ends the run with the error.
- With a `schema`, the schema is checked before the subagent starts; output that still fails validation after five attempts fails the call (`MAX_STRUCTURED_OUTPUT_RETRIES` changes the count).
- An agent whose output stops arriving restarts from the same prompt, at most five times; then the `agent()` call fails.

Limits (CC, "Behavior and limits" and "Cost"):

| Constraint | Value |
|---|---|
| Concurrent agents | Up to 16 by default, fewer with fewer CPUs; `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` accepts 1 to 256 |
| Items in one `parallel()` or `pipeline()` call | Up to 4,096; a longer list is rejected with an error |
| Agents per run | 1,000 total, "Prevents runaway loops" |
| Script environment | No direct filesystem or shell access; no module loading (`import()` fails before the run starts) |
| User input | None mid-run; a run pauses on its own only for agent permission prompts and a usage-limit wait |
| Advisory warning | "Large workflow" when more than 25 agents are scheduled or the projected token total passes 1.5 million; it does not pause or limit the run |
| Size guideline | Advice to the model, not a cap: `small` (fewer than 5 agents), `medium` (fewer than 10; the default, except `small` on a Pro plan), `large` (fewer than 50), `unrestricted` |

Other run-time behaviour the page documents:

- Prompt-cache sharing in a fan-out: agents with the same model, effort level, agent type, tools, output schema and working directory build the same prompt prefix. Claude Code holds all but the first matching agent until the first agent's response begins, for at most 5,000 ms by default, so the others read the cached prefix.
- Usage-limit pause: when an agent hits a subscription usage limit, the run pauses instead of failing that agent and continues after the reset, under listed conditions and at most twice per run.
- Models: each workflow agent's model is picked in the same order as for subagents; "A model the script names for a stage counts as the per-invocation model in that order."

Determinism for resume: "Claude Code makes `Date.now()`, `Math.random()`, and a no-argument `new Date()` throw inside the script, so that a relaunched run repeats the same `agent()` calls. Pass a timestamp in through `args` instead."

Resume and caching of completed sub-agent calls (CC, "How a workflow runs" and "Resume after a pause"):

- "The runtime tracks each agent's result as the run progresses, which is what makes a run resumable within the same session."
- A paused run is resumed from the `/workflows` view. A stopped run is relaunched by asking Claude to launch the workflow again with the same script. On the tool, this is the `resumeFromRunId` input: "Completed `agent()` calls with unchanged inputs usually return cached results; the rest run live... Same session only".
- The rule: "Claude Code replays the run in the order agents start, and each agent either returns its saved result or runs again."
  - Completed agents return their saved result, up to the first agent whose prompt differs from the previous run (because the script was edited or an earlier agent returned something different). That agent runs again, "and so does every agent after it, even ones that completed".
  - Agents still running when the run was stopped start over.
  - A failed agent runs again, and so does every agent the script starts after it. Stopping a single agent counts as failing. Example from the page: a script starts A, B, C, D in that order and B failed; relaunching returns A from cache and runs B, C and D again.
  - Reordering matters: editing the script so it starts A, C, B, D returns A from cache and runs C, B and D again.
  - Agents that failed at a usage limit run again while completed ones return saved results (from v2.1.318). If the script called another agent after that failure and it returned a result, every agent started after the failed one also runs again.
  - Version note from the page: before v2.1.305, completed agents could also run again on relaunch of an unedited script whose `pipeline()` items or `parallel()` tasks ran more than one agent each.
- Where results are kept: under the session's directory in `~/.claude/projects/`; in a cloud session also with the conversation history. If the saved results cannot be found the relaunch fails with a `nothing to resume` error instead of starting over.
- The run's script is written to a file under the session directory. It can be read, edited and relaunched; saved as a reusable command in `.claude/workflows/`; or shipped in a plugin.

The `Workflow` tool (Agent SDK reference): input fields `script`, `name`, `scriptPath`, `args`, `resumeFromRunId` (plus ignored `title` and `description`); at least one of the first three is required. The tool returns immediately with `status` (`async_launched` or `remote_launched`), `taskId`, `runId`, `scriptPath`, `transcriptDir` and others; "The final result arrives later as a task completion."

The page mentions a bundled `/workflow-authoring` skill that holds the script-writing reference. Its content is not on the public page and was not used here.

### 8.4 Side by side, facts only

| Aspect | Pydantic AI Harness `DynamicWorkflow` | Claude Code dynamic workflows |
|---|---|---|
| Script language | Python subset in the Monty sandbox | JavaScript in an isolated runtime |
| How a sub-agent is addressed | A named function from a host-supplied catalog, `name(task=...)` | `agent(prompt, options)`; any prompt |
| Typed results | From the sub-agent's `output_type`, shown to the model as `TypedDict` | Per call, with a JSON Schema in `schema` |
| Pre-run check | Static type check of the script against the signatures | Syntax check; schema self-consistency check per `agent()` call |
| Relation to the caller | A synchronous tool call; the result is the tool result | Launched in the background; the result arrives later as a task completion |
| Count limit | `max_agent_calls`, default 50 per parent run, exact | 1,000 agents per run; 4,096 items per fan-out call |
| Concurrency limit | None in the code besides the count (INFERRED) | 16 by default, configurable 1 to 256 |
| Clock and randomness in the script | Unavailable | Throw |
| Sub-agent failure | Raises `RuntimeError` in the script; uncaught, it aborts the script | `null` inside `parallel()` or `pipeline()`; ends the run when awaited directly |
| Reuse of completed calls after a failure | Bounded text previews (20 lines, 300 characters of JSON each) in the retry message; the model must paste values back | Saved per-agent results, replayed in start order until the first changed prompt |
| Persistence of the script | None | A file per run; can be saved as a command |
| Durable execution | Optional, by running the orchestrator inside a Temporal workflow | Resumable within the same session; a run does not continue on its own after the session is left |

## 9. Maturity, versions, caveats

Versions at the time of reading:

- `pydantic-ai-harness` 0.68.0 was published to PyPI on 2026-11-12, matching the `v2.68.0` release of `pydantic/pydantic-ai` the same day. The clone is a few hours newer than that release. The harness version tracks pydantic-ai's minor and patch: "pydantic-ai 2.51.0 ships pydantic-ai-harness 0.51.0" (`src/pydantic_ai_harness/pyproject.toml:8-11`).
- The first PyPI release of the harness was 0.1.1 on 2026-04-13; PyPI lists 58 releases in total.
- Required Monty: `pydantic-monty>=1.5.0,<2` for the `dynamic-workflow` extra. The repository lock file pins 1.5.0; PyPI's latest is 1.6.0 (2026-11-11). Monty 1.0.0 was released on 2026-09-25.
- Python 3.11 or later. Licence MIT. PyPI classifier: "Development Status :: 3 - Alpha" (`pyproject.toml:23-24, 32`).
- The lock file pins `temporalio` 1.31.0.

Version policy (docs overview page): the harness "uses **0.x versioning** to signal API stability, not maturity: the capabilities are tested end-to-end and meant for production use, but their APIs may change between minor releases". Patch releases will not intentionally break behaviour, and breaking changes come with deprecation warnings where practical and migration notes. The DynamicWorkflow page repeats the 0.x warning at its top (DOC:10).

History of this capability, from release notes and commit lists:

| Date | Event |
|---|---|
| 2026-07-09 | Added as experimental in the then-separate harness repository, release v0.6.0: "feat(experimental): Dynamic Workflows Capability" (pull request 273 of `pydantic/pydantic-ai-harness`) |
| 2026-07-14 | Release v0.7.0: "graduate capabilities out of `experimental` (ACP excepted)" (pull request 347). The capability left `experimental` five days after it was added. |
| 2026-07-18 | pydantic-ai v2.13.0: "fix(temporal): pass through `pydantic_monty` in workflow sandbox" |
| 2026-09-25 | "Add support for `pydantic-monty>=1`". This is the first commit in the history of the Temporal test file T2. The harness moved into the pydantic-ai repository around the same days (INFERRED from commit titles of 2026-09-25 and 2026-09-26). |
| 2026-10-02 | v2.53.0: "Restore `DynamicWorkflow` reveal announcements after compaction or history loss" |
| 2026-10-19 | "Stop harness sub-agents from returning `DeferredToolRequests` to a parent that cannot resolve them" |
| 2026-10-21 | v2.59.0: "Fix `DynamicWorkflow` revealed type names, nested tool exposure, and empty `WorkflowAgent` descriptions" |
| 2026-10-30 | v2.64.0: "Run `DynamicWorkflow` sub-agents under the parent run's `usage_limits` instead of the 50-request default". A behaviour change. |
| 2026-11-12 | v2.68.0: `max_duration_secs` renamed to `max_duration_seconds` |

So the capability is about four months old, and its behaviour changed in at least three releases during the six weeks before this digest.

Test and quality signals: 124 test functions in T1 and one in T2. The repository holds the harness to a 100% coverage gate (root `pyproject.toml:732`, `fail_under = 100`).

Open points visible in code comments:

- "Until the run loop reserves before requesting, tree-wide caps stay best-effort" (TS:725-726). Token and request limits can overshoot under fan-out; only `max_agent_calls` is exact.
- "durable ordering (global_sequential) lands with durability" (TS:825-826). The toolset ignores the run's parallel-execution mode; sub-agents always run concurrently.
- "the capability is asyncio-only" (TS:64-65).
- No comment marked as a to-do or a fix-me exists in the package or in MX.

Caveats stated by the sources:

- An uncaught error aborts the whole script; the model retries it from the start (DOC:221-222).
- There is no default time cap on the script. A pure-CPU loop is stopped only if the host sets `max_duration_seconds` (DOC:153).
- Under Temporal, setting `max_duration_seconds` risks a `NondeterminismError` on replay (DOC:230-231). INFERRED from the two statements together: inside a workflow, the only guard against a runaway loop is itself a replay hazard.
- Sub-agents are isolated and stateless across calls; everything they need must be in `task` (DOC:79).
- Token limits are budgets, not exact ceilings (DOC:132-143).
- The capability cannot be declared in a spec file (CAP:111-114).

Caveats that follow from the code (INFERRED):

- Completed sub-agent work is not reused automatically after a failed script. Outside Temporal, a retry re-runs every call the model does not replace with a pasted literal, and each re-run spends budget and tokens again.
- A result is only as recoverable as its 300-character JSON preview.
- A failed sub-agent run and an un-awaited call both spend budget.
- All sub-agent activity of a workflow lands in one Temporal history; there is no child-workflow option for `run_workflow`.

## 10. What could not be confirmed

Every item in this section is NOT VERIFIED.

- Nothing was run. Every statement about behaviour comes from reading code, tests and docs at one commit.
- Sub-agent without `TemporalDurability` inside a workflow: no test and no explicit statement. The reading in section 6.5 item 4 is an inference from the docs' wording and from the absence of a guard.
- The orchestrator's own activity names and count in the T2 history. The test asserts only the two `agent__summarize__model_request` activities; the orchestrator's activities are inferred from the naming rule.
- `inherit_model=True` under Temporal. The `TemporalDurability` docstring says a run-time `Model` instance must be registered with `models=` or it is rejected (`durable_exec/temporal/_durability.py:207-214`). Whether the model object the toolset forwards from the parent run satisfies that was not traced and is not tested.
- `reveal()` under Temporal and under replay: not documented, not tested.
- Behaviour on DBOS, Prefect and the other durable engines: no DynamicWorkflow docs section and no tests.
- Temporal history growth, payload sizes and continue-as-new for large fan-outs: not discussed in the DynamicWorkflow docs or code. `TemporalDurability` has a `continue_as_new` handler called before model requests; how it interacts with a sub-agent run started from a script was not examined.
- Whether a local Monty worker has a default per-request timeout that would eventually stop a runaway loop when no duration cap is set. The tests force a crash by setting `request_timeout=0.5` (T1:1100-1112); the default was not looked up.
- Whether Monty's default suspension budget of 1,000 per session applies to workflow scripts (section 5.3). The figure comes from the Code Mode docs; Monty's source was not read.
- Monty internals in general: the snapshot protocol, the checker and the memory accounting are described here only as far as the harness code and one Monty docs page show. Whether user-defined coroutines can be passed to `asyncio.gather` in a script is not stated in the sources read.
- How the reveal announcement is rendered for each model provider. The code enqueues a `SystemPromptPart`; the end-to-end tests find its text in what a `FunctionModel` receives (T1:1856, 1890). Provider-specific rendering is core behaviour that was not traced.
- The Pydantic article's "a few hundred" agents: no evidence is given, and the default budget is 50 calls per run.
- Claude Code: option names for `agent()` beyond `schema`, `label` and `stallMs` (for example how a script names a model for a stage) are not on the public page. The bundled `/workflow-authoring` skill was not used as a source.
- The Bun post does not say how its workflows were resumed beyond "Claude resumed the workflows", so it cannot be tied to the documented replay rule.
- The exact live-site build of the Pydantic docs pages versus the clone commit: the page texts matched the repository Markdown on every statement checked, but the site's build commit is not shown.
