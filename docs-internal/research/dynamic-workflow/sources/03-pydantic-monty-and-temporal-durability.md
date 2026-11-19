# Monty sandbox and Pydantic AI Temporal durability (digest)
> **Status: Prototype reference.** External source digest: this file describes Pydantic AI Harness and Monty, not OpenCompany. Paths, names and commands in it belong to those projects.

Digest written 2026-11-12. Nothing from the sources was executed, built or installed; every statement comes from reading documentation, source code, tests and release notes. INFERRED marks a conclusion drawn from the code that no document states outright. NOT VERIFIED marks something that could not be checked.

Citation shorthand:

- `M/` is the root of the Monty clone (source S6). `M/docs/...` files are the source of the Monty documentation site.
- `C/` is `pydantic_ai_slim/pydantic_ai/` in the pydantic-ai clone (source S4), `H/` is `src/pydantic_ai_harness/pydantic_ai_harness/`, `T/` is `tests/harness/`. A number after a colon is a line number at the commit named in the table.
- `guide: "Streaming"` names a section of the Temporal guide (S1). `cm: "Temporal durability"` names a section of the Code Mode page (S3).

The companion file `02-pydantic-code-mode.md` covers Code Mode itself.

## Sources read

| # | Source | URL | Fetched | Version or commit | Licence |
|---|--------|-----|---------|-------------------|---------|
| S1 | Pydantic AI guide "Durable Execution with Temporal" | https://pydantic.dev/docs/ai/capabilities/durable_execution/temporal/ | 2026-11-12 | live page; same text as `docs/durable_execution/temporal.md` in S4 | page footer "© Pydantic Services Inc. 2025 to present"; the source file is in the MIT repository |
| S2 | Pydantic AI "Durable Execution" overview | https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/ | 2026-11-12 | live page | as S1 |
| S3 | Code Mode page and Dynamic Workflow page (Temporal sections) | https://pydantic.dev/docs/ai/harness/code-mode/ and https://pydantic.dev/docs/ai/harness/dynamic-workflow/ | 2026-11-12 | live pages | as S1 |
| S4 | pydantic-ai repository, shallow clone | https://github.com/pydantic/pydantic-ai | 2026-11-12 | commit `d94586aad1acd640715f2bf1152e8d730c8f9938`, committed 2026-11-12T05:48:03Z | MIT |
| S5 | PyPI metadata for pydantic-monty, pydantic-ai-harness, pydantic-ai-slim | https://pypi.org/pypi/pydantic-monty/json | 2026-11-12 | pydantic-monty 1.6.0 (2026-11-11); pydantic-ai-harness 0.68.0 and pydantic-ai-slim 2.68.0 (2026-11-12) | MIT |
| S6 | Monty repository, full-history clone without blobs | https://github.com/pydantic/monty | 2026-11-12 | commit `3f89dcbf82071896efb5e3ded507db4191e8d829`, committed 2026-11-11T13:24:39Z, "Bump version to v1.6.0" | MIT (the server product described in `M/docs/server.md` is closed-source) |
| S7 | Monty GitHub release notes, v0.0.1 to v1.6.0 | https://github.com/pydantic/monty/releases | 2026-11-12 | 39 releases, read through the public GitHub API | MIT repository |

Files read in S4: every file listed in the companion digest, plus `C/durable_exec/temporal/__init__.py`, `_durability.py`, `_operation_backend.py`, `_operation_names.py`, `_activity_execution.py`, `_toolset.py`, `_run_context.py`, `_payload_converter.py`, `_spec.py`, `_workflow.py`; `C/durable_exec/_spec.py`, `_toolset.py`, `_utils.py`, `_operation_names.py`; the relevant parts of `C/durable_exec/_base.py`; `H/dynamic_workflow/_toolset.py` and `_capability.py`; `H/_sub_agent_output.py`; `T/code_mode/test_temporal.py`, `T/dynamic_workflow/test_temporal.py`, `T/_temporal.py`. Not read: `C/durable_exec/temporal/_agent.py` and `_model.py` (the deprecated `TemporalAgent` path), `_event_stream.py`, `_logfire.py`, `_model_errors.py`, `_transports.py` beyond its class outline.

Files read in S6: `README.md`; `M/docs/index.md`, `quickstart/python.md`, `host-functions.md`, `snapshots.md`, `resource-limits.md`, `security.md`, `type-checking.md`, `server.md` (first 140 lines), `alternatives.md` (first 60 lines), `limitations/index.md`, `asyncio.md`, `random.md`, `time.md`, `exceptions.md`, `language.md` (first 120 lines), `builtins.md` (hash section); `M/crates/monty-python/python/pydantic_monty/__init__.py` and `_monty.pyi` (pool, session and snapshot classes); the same two files and `README.md` as they were at tag `v0.0.18`.

## 1. What it is

### 1.1 Monty

Monty is "A secure Python sandbox, written in Rust, for code written by AI" (`M/README.md:5`). It is its own interpreter with its own bytecode VM, not CPython. The Python package is `pydantic-monty`; there are also JavaScript and Rust packages.

What it is:

- A language-level sandbox. "There is no container, no seccomp filter and no virtual machine. The isolation comes from the interpreter itself: sandboxed code cannot express an operation that touches the host, because the interpreter implements no such operation" (`M/docs/security.md:12-15`).
- A subset of Python that aims to match CPython 3.14 exactly where it implements something: "Everything it does implement is meant to behave exactly like CPython 3.14; everywhere it does not, the divergence is written down" (`M/docs/limitations/index.md:5-6`).
- A suspend-and-resume interpreter. When sandboxed code reads a name it never defined, "execution suspends. The host resolves the name ... and execution resumes with the result. The interpreter never calls out; it stops and waits to be told what happened" (`M/docs/host-functions.md:7-9`).
- Fast to start: 0.80 ms for a new sandbox from a running pool and 0.40 ms for ten REPL commands, against about 195 ms for local Docker and 1500 ms for a sandboxing service; creating the pool itself takes about 5 ms, once per application (`M/docs/index.md:34-45`, `M/docs/alternatives.md:20-29`). The Python download is about 4.5 MB.
- Sold in two forms: "OSS Monty", the MIT package, and "Commercial Support", a closed-source gRPC server image that hosts the same workers remotely (`M/docs/server.md:1-13`).

What it is not:

- Not a complete Python. No third-party packages, no class inheritance, no generators (`yield`), no `del`, no `async with` or `async for`, no decorators on methods, no user-defined exception classes, no `globals`, `compile`, `__import__` or `super` (`M/docs/limitations/index.md:45-68`).
- Not an OS-level sandbox, and not crash-proof in-process, which is why the Python API always runs the interpreter in worker subprocesses: "a Monty process can never be made fully crash-proof against memory errors triggered by adversarial code" (`M/docs/quickstart/python.md:37-40`).
- Not a durable-execution engine. Its own comparison page says: "Durable-execution frameworks such as Temporal are not snapshotting: they replay a workflow written for them, which a script a model just wrote is not" (`M/docs/alternatives.md:34-35`).

### 1.2 Pydantic AI's Temporal integration

`TemporalDurability` is a capability in `pydantic_ai.durable_exec.temporal`. Attached to an `Agent`, it makes `agent.run()` durable when the call happens inside a Temporal workflow: "the capability routes model requests, tool calls, and MCP server communication through Temporal activities", while "the logic that coordinates them (i.e. the agent run) lives in the workflow" (guide: "Durable Agent", "Durable Execution"). Outside a workflow the capability is transparent and the agent behaves normally. The guide warns: "Attaching `TemporalDurability` does not by itself make runs durable — durability comes from executing the run inside a Temporal workflow that your application starts."

Temporal is one of nine supported engines (Temporal, DBOS, Prefect, Restate, AWS Lambda, Absurd, pgtask, Azure Durable Task, Apache Airflow). An agent can have only one; attaching a second raises `UserError` (S2).

## 2. Vocabulary

| Term | Meaning |
|------|---------|
| Pool | A set of Monty worker processes owned by a `Monty`, `AsyncMonty` or `AsyncMontyRemote` object. |
| Checkout, session | One worker dedicated to one REPL session, obtained with `pool.checkout()`. State persists across feeds of the session. |
| Feed | One snippet executed in a session, with `feed_run` (driven to completion) or `feed_start` (returns at each suspension). |
| Suspension | The interpreter stopping to ask the host for something: a host function call, an OS call, a name lookup, or pending futures. |
| Snapshot (object) | What `feed_start` and `resume` return at a suspension: `FunctionSnapshot`, `NameLookupSnapshot`, `FutureSnapshot`, or `MontyComplete` when finished. |
| Dump | Opaque bytes from `session.dump()` or `snapshot.dump()` holding the whole interpreter state, idle or mid-feed. |
| Host function | A host callable the sandbox can call, supplied through `external_lookup` or an `ExternalModule`. |
| OS policy | Per-session settings for clock, time zone, sleep, process clock and random seeding. |
| Workflow code | In Temporal, deterministic code that is re-executed on replay. The agent run loop runs here. |
| Activity | In Temporal, a recorded unit of I/O. A completed activity's result is read from history on replay instead of re-running. |
| Replay | Temporal re-running workflow code against recorded history, to rebuild state after a crash, a worker change or a cache eviction. |
| Workflow task | One activation of workflow code. An exception here that is not a registered failure type fails the task, not the workflow, and Temporal retries it. |
| Durable unit | Pydantic AI's engine-neutral name for an activity, step or task. |
| Blocking portal | An `anyio` helper thread with its own event loop. Inside a workflow, every call into Monty goes through it while the workflow thread blocks. |
| Passthrough module | A module the Temporal workflow sandbox uses from the host instead of re-importing it in isolation. |

## 3. How it works

### 3.1 Monty: pool, session, feed

Source: `M/docs/quickstart/python.md`.

1. Create a pool. `Monty()` (sync) or `AsyncMonty()` (asyncio) spawns `monty` worker subprocesses when entered with `with` or `async with`. The worker binary comes from the `pydantic-monty-runtime` distribution; `pydantic-monty` is a metapackage over that and `pydantic-monty-client`.
2. Check out a session: `pool.checkout(limits=..., type_check=..., type_check_stubs=..., os_policy=..., external_modules=...)`. Limits, type checking and OS policy are per session.
3. Feed code. `session.feed_run(code, inputs=..., external_lookup=..., print_callback=..., mount=..., os=...)` runs a snippet and returns the value of its trailing expression. Globals, functions and classes persist to the next feed. When the session's `with` block exits the worker returns to the pool and the state is gone.
4. Values cross the boundary by conversion. `None`, `bool`, `int`, `float`, `complex`, `str`, `bytes`, `list`, `tuple`, `dict`, `set`, `frozenset`, dates and times, `Decimal`, named tuples and exception instances convert both ways. Other host objects must be wrapped in `ClassInstance`; anything else raises `MontyConversionError`.

Minimal example from the README:

```python
from pydantic_monty import Monty

with Monty() as pool:
    with pool.checkout() as session:
        session.feed_run(
            code,
            inputs={'bulb_watts': 10},
            external_lookup={'nutrition': lambda food: {'kcal': 230}},
        )
```

### 3.2 Monty: host functions, sync and async

- `inputs` binds values eagerly as globals before the snippet runs. `external_lookup` resolves names lazily when the code reads them: a callable becomes a host function, any other value is converted and returned, an absent name raises `NameError` in the sandbox.
- Positional and keyword arguments pass through as written. A host exception crosses into the sandbox and can be caught there; only exception types Monty implements carry their type.
- With `AsyncMonty`, host functions may be coroutine functions; they are awaited on the caller's event loop. `asyncio.gather` inside the sandbox lets several run concurrently. The sync `Monty` cannot drive coroutine host functions.
- "There is no event loop inside the sandbox — the host is the loop." The sandbox's `asyncio` module has exactly three functions: `run`, `gather` and `sleep`. `gather` takes no keyword arguments and always behaves as `return_exceptions=False`. `create_task`, `wait`, `wait_for`, `Queue`, `Lock`, `TaskGroup` and the rest do not exist (`M/docs/limitations/asyncio.md:8-34`).
- Concurrency inside the sandbox is cooperative and host-driven: "`gather` suspends Monty whenever every branch is blocked on an external call, hands the pending calls to the host, and resumes when the host returns results. There is no preemption, no threads and no exposed event loop."
- `external_modules` on `checkout()` lets the session `import` host-provided modules (`ExternalModule({'add': add}, stubs='...')`).
- New in 1.6.0: `stream_async_calls`. A call to an `async def` host function that the sandbox does not await at once is sent to the host without suspending the worker. On by default for remote pools, off for local subprocess pools, and never used by `feed_start`, which "suspend[s] at every call so that each reaches a snapshot" (`M/docs/host-functions.md:305-385`).

### 3.3 Monty: suspension, resume and serialisation to bytes

Source: `M/docs/snapshots.md`.

`feed_start` is the suspendable counterpart of `feed_run`. It returns a snapshot at every suspension:

| Kind | Why execution stopped | How to resume |
|------|-----------------------|---------------|
| `FunctionSnapshot` | a host function call or OS call | `resume(result)`, `resume_not_handled()`, `resume_auto()` |
| `NameLookupSnapshot` | an undefined name was read | `resume(value=...)`, or `resume()` to raise `NameError` |
| `FutureSnapshot` | every sandbox task is blocked on host futures | `resume({call_id: result})` |
| `MontyComplete` | the snippet finished | read `.output` |

`FunctionSnapshot.resume` takes one of four shapes: `{'return_value': value}`, `{'exception': SomeError('...')}`, `{'exc_type': 'ValueError', 'message': '...'}`, or `{'future': ...}` to hand the sandbox a pending future that is settled later at a `FutureSnapshot`. That last shape is how a host runs several calls concurrently by hand. A snapshot exposes `function_name`, `args`, `kwargs`, `call_id`, `is_os_function`, `position` (source range) and `trace_context()`.

Serialisation:

- `snapshot.dump()` serialises a worker suspended mid-feed. `session.dump()` serialises an idle session between feeds. Both return opaque bytes.
- A fresh session restores them with `load_snapshot(blob)` or `load_session(blob)`, "in another process or on another machine" (`M/docs/quickstart/python.md:289-291`). Both loaders are valid only on a fresh session.
- The stated reason it works: "the sandbox holds no operating system resources. There are no live file descriptors, no sockets and no threads to reconstruct."
- The format is `MONTY\0` magic, a format version, then CBOR. A release that changes what stored data means bumps the version; older builds refuse newer dumps.
- A dump over the 256 MiB message cap raises `RuntimeError` without changing the session.

What restoring carries and does not carry (`M/docs/snapshots.md:345-403`):

- Carried: globals, functions, classes, the paused call stack, `script_name`, type-check state, the accumulated time budget, the `max_suspensions` limit (the count resets to zero), the state of an already started `random` generator.
- Not carried: mounts (pass the same `mount=` again), host objects in the instance store, `'overlay'` writes, the OS policy (the restoring checkout's policy applies).
- "A restored `FutureSnapshot` cannot be driven with `resume_auto()`. Its pending coroutines lived in the previous process." Resolve it by hand.
- A dump suspended on a call re-announces that call when restored, so "Make callbacks idempotent if you plan to restore snapshots across a restart" (`M/docs/host-functions.md:562-563`).
- Trust: "Only restore unmodified snapshots from a trusted, compatible Monty producer ... Monty does not authenticate snapshots or fully validate their contents."

The uses the docs list are long-running agents, approval gates, forking and surviving restarts. Pydantic AI's Temporal integration does not use any of this: in the harness, `session.dump` and `load_session` appear only in the Code Mode REPL store, which is switched off under durable execution, and `load_snapshot` is not called anywhere (search of `H/` at the S4 commit; `H/code_mode/_repl_store.py:117`, `164`).

### 3.4 A Pydantic AI agent run on Temporal

Sources: guide; `C/durable_exec/temporal/`.

Setup, all outside any workflow:

1. Build the agent at module level with a `name`, with every executing toolset given an explicit `id`, and with `TemporalDurability()` in `capabilities`.
2. When the capability is bound to the agent it creates one Temporal activity definition per operation of the agent's model and of each toolset (section 4.3 lists the names).
3. Define a workflow class whose `@workflow.run` method awaits `agent.run(...)`. List the agent in `__pydantic_ai_agents__` on the class, or give the worker an `AgentPlugin(agent)`.
4. Connect the client with `plugins=[PydanticAIPlugin()]` and start a `Worker` from that client.

At run time inside the workflow:

| Runs in workflow code (re-executed on replay) | Runs as an activity (recorded) |
|-----------------------------------------------|--------------------------------|
| the agent run loop | each model request, streamed or not |
| capability hooks, including `before_tool_execute` and friends | each function-tool call, unless the tool opts out |
| a function tool's `prepare` function | a tool's `args_validator`, in a `validate_args` activity |
| listing a `FunctionToolset`'s tools | MCP `get_tools`, `get_instructions` and every MCP tool call |
| a `ProcessEventStream` handler and `@on_event` listeners | `DynamicToolset` tool discovery and tool calls |
| async tools marked `metadata={'temporal': False}` | the durability `event_stream_handler`, one activity per tool event |
| Code Mode's `run_code` and DynamicWorkflow's `run_workflow` | capability methods decorated as durable operations; workspace calls made from workflow code |
| the body of a tool configured as a child workflow (in the child's workflow code) | provider teardown of an abandoned suspended model response |

Inside the workflow the capability also disables thread use (sync callbacks run inline), swaps the agent graph's sleep for `workflow.sleep`, uses `workflow.now` as the run clock, derives default run and conversation ids deterministically from the workflow run id and `workflow.uuid4()`, and suppresses telemetry for work that is being replayed (`C/durable_exec/temporal/_durability.py:547-582`).

Tool errors that are control flow are returned from the activity as data, not raised. `ModelRetry`, `ApprovalRequired`, `CallDeferred`, `ToolFailed`, validation errors and run cancellation are wrapped into a tagged result inside the activity and re-raised in workflow code, so they do not count as activity failures and are not retried by Temporal (`C/durable_exec/_toolset.py:437-518`).

### 3.5 `run_code` inside a Temporal workflow: first execution

The model request that produces the `run_code` call is an activity. The agent loop, now in workflow code, calls the `run_code` tool. Code Mode's toolset is not wrapped as an activity: `CodeMode` orders itself outermost and the durability capability innermost, so `CodeModeToolset` wraps toolsets whose leaves are already the durable wrappers (`H/code_mode/_capability.py:289-291`, `C/durable_exec/_base.py:1937-1941`). The test file states the result: "CodeMode runs workflow-side, nested tools use activities, and the history replays" (`T/code_mode/test_temporal.py:298`).

Step by step (`H/code_mode/_toolset.py:1272-1309`, `H/_monty_exec.py`):

1. `in_temporal_workflow()` is true. It checks `sys.modules['temporalio.workflow'].in_workflow()` without importing Temporal.
2. The resolved limits get `max_feed_duration_secs: None`. The comment: "`run_code` executes in workflow code and Temporal replays it. An elapsed timer may make the original run and replay take different branches, which Temporal cannot record." Memory and suspension limits stay.
3. `MontyRunState.get_session` starts a blocking portal (`start_blocking_portal()`), then creates the pool (`AsyncMonty()` or `AsyncMontyRemote`) and checks out the session through that portal.
4. Every later call into Monty (`feed_start`, each `resume`, checkout, reset, close) is `portal.call(...)`. The module comment explains why: Monty's bindings complete an awaited call "from Monty's own I/O thread by waking the event loop the `await` started on (`loop.call_soon_threadsafe`). A Temporal workflow's event loop cannot be woken that way, so the call would never complete." So "The workflow thread blocks until the sandbox suspends or completes ... and control is back in the workflow between calls, where nested tools run as activities."
5. The snippet runs in the Monty worker process. At a nested tool call the sandbox suspends; the executor (running in workflow code) builds the dispatch coroutine and, for an async tool, wraps it with `asyncio.ensure_future` on the workflow's event loop, then resumes the sandbox with a pending future. The portal call blocks again until the next suspension, so the new task has not started running yet.
6. When every sandbox task is blocked, Monty returns a future snapshot and the executor awaits `asyncio.gather` over the pending tasks. Only now does the workflow event loop run them. Each task goes through the nested `ToolManager` to the durable toolset wrapper, which calls `workflow.start_activity` for that tool. All calls gathered together are therefore scheduled in the same workflow task, in dispatch order, and their activities run concurrently. That ordering is INFERRED from the executor code; the docs say only that "`asyncio.gather` can schedule nested tool activities concurrently."
7. While the activities run, the workflow is idle and the Monty worker stays suspended, held in the memory of the Temporal worker process that is running the workflow (INFERRED: the session object lives in the toolset's run state and nothing persists it).
8. When the results arrive, the executor resumes the sandbox with a return value or an exception per call id. The loop continues until Monty reports completion, and `run_code` returns its value to the agent loop, which makes the next model request as an activity.

Other host interactions in workflow code:

- Sleeps. The OS policy routes `time.sleep` and `asyncio.sleep` to the host, and the executor waits with `anyio.sleep` on the workflow's own event loop: "inside a Temporal workflow a sleep is a durable timer" (cm: "Sandbox restrictions"). The sleep allowance still applies. The test that asserts a `TimerStarted` event for this is currently skipped (section 8).
- Clock, environment and randomness. With an `os_access` handler, the calls `os.getenv`, `os.environ`, `date.today`, `datetime.now`, `os.urandom` and `time.time` are answered by calling the handler on the workflow's own thread, "so it can use `temporalio.workflow` APIs" such as `workflow.now()` (`H/_monty_exec.py:347-350`, `421-440`). Without a handler they are unavailable and raise in the sandbox.
- File calls. They go to Monty, which answers them "from the mounts first" and otherwise calls the handler "on another thread" (cm: "Temporal durability").
- Remote checkout backoff. A capacity refusal is retried once after a 0.1 s sleep that runs inside the portal thread, not as a workflow timer, so it leaves nothing in history (`H/_monty_exec.py:195-204`; `T/code_mode/test_temporal.py:476-522` asserts no timer event). The test docstring gives the reason: the refusal depends on the network and may not recur on replay, "so recording the original backoff as a workflow timer would make its command history nondeterministic."

What the event history holds for a Code Mode run: `agent__<agent>__model_request` activities for the model turns and one `agent__<agent>__toolset__<toolset id>__call_tool` activity per nested tool call. The names come from the namer in section 4.3; the Code Mode test asserts the message history and a clean replay, not the activity names. The snippet text travels inside the recorded model response; its return value travels inside the input of the next model-request activity. Nothing records the sandbox's own state.

### 3.6 The same run on replay

Replay happens whenever a worker must rebuild the workflow's state from history: after a crash or deploy, on another worker, or after the workflow is evicted from the worker's cache.

1. Workflow code runs again from the start. Each model-request activity returns its recorded response, so the agent loop sees the same `run_code` calls with the same code.
2. `run_code` runs again in workflow code. A new portal, a new pool and a new session are created; with a local pool that means a new Monty worker subprocess. "The REPL is process-local state for one agent run, not durable storage. Replay reconstructs it by running the recorded snippets again against recorded activity results" (cm: "Temporal durability").
3. The snippet executes for real a second time inside the sandbox. Each nested tool call schedules the same activity command as before, and Temporal answers it from history without running the tool.
4. Anything the snippet did that was not an activity happens again: pure computation, `mount` reads and writes, `os_access` callbacks, clock and randomness answers.
5. With `monty_sandbox`, "replay connects to the worker again, so it must be reachable whenever the workflow replays."

Why this stays deterministic, and where it does not, is section 6.

### 3.7 `run_workflow` (DynamicWorkflow) inside a Temporal workflow

Sources: Dynamic Workflow page, "Running inside a Temporal workflow"; `H/dynamic_workflow/_toolset.py:795-891`; `T/dynamic_workflow/test_temporal.py`.

Setup: "Attach `TemporalDurability` to the orchestrating agent alongside `DynamicWorkflow`, and to each sub-agent whose model requests should run as activities, then register every agent with its own `AgentPlugin`."

Execution uses the same `MontyExecutor` and the same blocking portal as Code Mode. The differences:

- Each `run_workflow` call creates a fresh `MontyRunState` and closes it in a `finally` block. There is no REPL state across calls, so the script is type-checked every time.
- The dispatch callback runs a sub-agent: `await self._by_name[agent_name].agent.run(task, deps=ctx.deps, model=..., usage=..., usage_limits=..., capabilities=[serving_model])` (`H/dynamic_workflow/_toolset.py:744-751`). This is a direct call in workflow code.
- The execution-time limit is not removed. `limits = _resolve_resource_limits(self.resource_limits)` is passed unchanged (`H/dynamic_workflow/_toolset.py:813-822`). DynamicWorkflow has no default time cap, so nothing changes unless the user set one. If they did, the docs warn: "Unlike Code Mode, a `max_duration_seconds` you set still applies inside the workflow, so a script that runs close to it can stop at a different point on replay and cause a `NondeterminismError`."
- Sub-agents always run concurrently when gathered. A source comment notes "durable ordering (global_sequential) lands with durability" (`H/dynamic_workflow/_toolset.py:825-826`).
- The per-run capability passed to the sub-agent, `ServingModelRecorder`, is marked `_safe_at_runtime = True` because Temporal otherwise rejects capabilities added per run inside a workflow (`H/_sub_agent_output.py:65-66`).

Is a sub-agent run a child workflow, an activity, or inline workflow code? It is inline workflow code. The sub-agent's run loop executes in the orchestrator's workflow, on the same event loop, as an asyncio task created by the executor. If the sub-agent has its own `TemporalDurability`, each of its model requests and tool calls is an activity of that sub-agent, recorded in the same workflow history. The test docstring: "The script runs workflow-side, sub-agents run through activities, and the history replays." No child workflow is started and the sub-agent run as a whole is not an activity.

The docs do not say what happens to a sub-agent without `TemporalDurability`. INFERRED from the code: its run loop would still execute inline, and its model request would then be made directly from workflow code, unrecorded. NOT VERIFIED.

For contrast, the other delegation shapes the Temporal guide describes:

- A function tool configured with `metadata={'temporal': {'child_workflow': ChildWorkflowConfig(...)}}` runs its body as a Temporal child workflow. A nested agent with its own `TemporalDurability` called from that body records its activities "in the child's separate workflow history instead of growing the parent's" (guide: "Child workflows").
- The `SubAgents` and `Coder` capabilities: a delegation to `self` "runs in the workflow, so the child's model requests and tools become activities of the same agent"; "With other delegates, a delegation runs in an activity" (guide: "Harness Workspace Capabilities").
- An ordinary tool that calls `await other_agent.run(...)` runs that whole nested run inside the tool's one activity (guide: "Agent Run Context and Dependencies", the paragraph on delegate usage).

### 3.8 Event history for a script that fans out to three sub-agents

The repository's test uses a two-way fan-out, `await asyncio.gather(summarize(task='alpha'), summarize(task='beta'))`, and asserts exactly two scheduled activities named `agent__summarize__model_request`, then replays the history without error (`T/dynamic_workflow/test_temporal.py:51`, `133-146`).

For three sub-agents the history is therefore, INFERRED from that test and the code:

1. `agent__<orchestrator>__model_request`: scheduled, started, completed. Its result contains the `run_workflow` call with the script.
2. One workflow task in which the script runs up to the gather. In that single task three `ActivityTaskScheduled` events appear, one `agent__<sub-agent>__model_request` per sub-agent run, in the order the script called them.
3. The three activities start and complete independently. After each completion a workflow task advances that sub-agent's run loop. If a sub-agent calls tools, each call adds an `agent__<sub-agent>__toolset__<toolset id>__call_tool` activity, followed by another `agent__<sub-agent>__model_request`.
4. When all three runs have finished, the gather resolves, the sandbox resumes, the script finishes and `run_workflow` returns.
5. `agent__<orchestrator>__model_request` again, with the script's result in its input.

There are no `StartChildWorkflowExecutionInitiated` events. A timer event appears only if the script sleeps or a run `timeout=` is set. One `MarkerRecorded` event for the patch id `pydantic_ai:stable_default_run_id` is expected from the default run-id logic (`C/durable_exec/temporal/_durability.py:86`, `547-562`); that is INFERRED and not asserted by a test.

## 4. API and configuration surface

### 4.1 `pydantic_monty` 1.6.0

In the code blocks of this section, argument names and defaults are exact. Type annotations are complete for the pool constructors, `checkout`, `ResourceLimits` and `OSPolicy`, and omitted for the session methods to keep them readable.

Pools (`M/crates/monty-python/python/pydantic_monty/_monty.pyi:694-1283`):

```python
Monty(
    *,
    binary_path: str | Path | None = None,
    min_processes: int = 1,
    max_processes: int | None = None,          # defaults to the CPU count
    checkout_timeout: float | None = None,
    request_timeout: float | None = None,      # hard per-turn deadline; kills the worker
    execution_clock: Literal['wall', 'cpu'] = 'wall',   # 'cpu' is Linux only
    max_checkouts_per_worker: int | None = 1000,
    feed_duration_limit_grace: float | None = 1.0,
    turn_duration_limit_grace: float | None = 1.0,
)
AsyncMonty(...same arguments..., stream_async_calls: bool = False)
AsyncMontyRemote(
    url: str,
    *,
    max_processes: int | None = None,
    checkout_timeout: float | None = None,
    request_timeout: float | None = 10.0,
    metadata: Callable[[], Mapping[str, str]] | None = None,
    feed_duration_limit_grace: float | None = 1.0,
    turn_duration_limit_grace: float | None = 1.0,
    auto_resume: bool = True,
    max_decode_bytes: int | None = None,
    connect_headers: Callable[[], Mapping[str, str]] | None = None,   # deprecated name of metadata
    stream_async_calls: bool = True,
)
```

There is no sync remote pool. `AsyncMontyWebsocket` is the deprecated former name of `AsyncMontyRemote`.

Session creation and use:

```python
pool.checkout(
    *,
    script_name: str = 'main.py',
    limits: ResourceLimits | None = None,
    type_check: bool = False,
    type_check_stubs: str | None = None,
    type_check_format: TypeCheckFormat | None = None,
    type_check_color: bool = False,
    assert_message_annotations: bool | int = ...,
    print_flush_interval: float | None = None,      # None means 0.005 s
    os_policy: OSPolicy | None = None,
    external_modules: Mapping[str, ExternalModule | Callable[[], ExternalModule]] | None = None,
)   # AsyncMontyRemote.checkout adds ephemeral: bool | None = None, profile: str | None = None

session.feed_run(code, *, inputs=None, external_lookup=None, print_callback=None,
                 mount=None, cwd=None, os=None, skip_type_check=False) -> Any
session.feed_start(code, *, inputs=None, external_lookup=None, print_callback=None,
                   mount=None, cwd=None, os=None, skip_type_check=False) -> snapshot
session.dump() -> bytes
session.load_session(state: bytes) -> None
session.load_snapshot(state: bytes, *, mount=None, print_callback=None,
                      external_lookup=None, os=None) -> snapshot
session.install_dependencies(requirements: list[str]) -> None   # only on an embedded-CPython worker
```

`AsyncMontySession` has the same methods as coroutines, plus `get_session_id()` for servers that store sessions. Snapshot classes: `FunctionSnapshot`, `NameLookupSnapshot`, `FutureSnapshot`, `MontyComplete`, and the `Async...` variants whose `resume` and `resume_auto` are awaitable.

Limits and policy (`M/crates/monty-python/python/pydantic_monty/__init__.py:142-244`):

```python
class ResourceLimits(TypedDict, total=False):
    max_feed_duration_secs: float | None   # execution time per feed
    max_turn_duration_secs: float | None   # execution time between host round trips
    max_memory: int | None                 # heap bytes
    gc_interval: int | None
    max_recursion_depth: int | None        # default 1000, cannot be disabled
    max_suspensions: int | None            # default 1000 per checkout, cannot be disabled
    max_total_sleep_secs: float | None     # cumulative 'system' sleep

class OSPolicy(TypedDict, total=False):
    datetime: Literal['system', 'call_host'] | datetime.datetime   # a datetime freezes the clock
    timezone: str | TimeZone                                        # default 'utc'
    sleep: Literal['system', 'call_host', 'zero']                   # default 'system'
    sleep_system_max: float                                         # default 10 s per sleep
    process_time: Literal['zero', 'elapsed']                        # default 'zero'
    random_start: Literal['system', 'call_host'] | RandomSeed       # RandomSeed is {'seed': ...}
```

Host-call answers:

```python
ExternalReturnValue = {'return_value': Any}
ExternalException = {'exception': BaseException}
ExternalExceptionData = {'exc_type': ExcType, 'message': str}   # message optional
ExternalFuture = {'future': ...}
```

An OS handler is called as `handler(*, name, args, kwargs, is_async, **_future_kwargs)` and returns the result or `NOT_HANDLED`.

Print capture: `print_callback` takes a callable `(stream, text)`, a `CollectString()` or a `CollectStreams()`. Both collectors cap output at 10 MiB by default (`max_bytes=None` disables). The cap is enforced in the host process; exceeding it fails the feed with a `MontyRuntimeError` wrapping `MemoryError` that sandboxed code cannot catch. Output arrives in chunks and is always flushed before a host call and before a feed ends.

Static type checking: off by default; `checkout(type_check=True, type_check_stubs=...)` runs Astral's `ty` checker, bundled in the worker binary, against `monty-typeshed`, a typeshed trimmed to Monty's own runtime surface. Each successfully executed snippet joins the context for later snippets. `skip_type_check=True` bypasses it for one feed. Diagnostics come back pre-rendered in `MontyTypingError.display()`. Caveats: static only; `eval()` and `exec()` source is not checked; "Passing the type check does not mean the code runs" (`M/docs/type-checking.md:267-279`).

Errors (`M/docs/quickstart/python.md:342-350`): `MontySyntaxError`, `MontyTypingError`, `MontyRuntimeError` (session survives, but discard it after a resource limit), `MontyConversionError`, `MontyCrashedError` (session lost; the pool replaces the worker). Remote pools add `MontyDisconnectError`, `MontyConnectError`, `MontyRefusedError`, `MontyConfigurationError` and `MontyShutdown`.

Standard-library modules present (`M/docs/limitations/index.md:72-103`): `annotationlib`, `asyncio`, `base64`, `binascii`, `bisect`, `collections`, `copy`, `csv`, `dataclasses`, `datetime`, `decimal`, `fnmatch`, `functools`, `hashlib`, `itertools`, `json`, `math`, `os`, `os.path`, `pathlib`, `posixpath`, `random`, `re`, `sys`, `time`, `tomllib`, `typing`, `unicodedata`. Each covers only part of its CPython surface. Notably absent: `enum`, `contextlib`, `io`, `string`, `struct`, `operator`, `inspect`, `logging`, `traceback`, `uuid`, `urllib`, and by design `socket`, `subprocess`, `multiprocessing`, `threading`, `ctypes`.

Remote server mode: `AsyncMontyRemote(url)` talks gRPC to a server, one HTTP/2 connection per pool and one stream per session. The intended peer is the closed-source `monty-server` image; the repository also has a development relay script. The docs warn that "A remote peer need not be a Monty sandbox at all" and that none of the sandbox guarantees transfer across that boundary (`M/docs/security.md:513-521`).

### 4.2 `TemporalDurability` and the plugins

Constructor (`C/durable_exec/temporal/_durability.py:185-200`). Names and defaults are exact; the generic parameter `[AgentDepsT]` is dropped from the type annotations:

```python
TemporalDurability(
    *,
    models: Mapping[str, Model] | None = None,
    event_stream_handler: EventStreamHandler | None = None,
    event_stream_topic: str | WorkflowStreamTopic | None = None,
    name: str | None = None,                     # defaults to the agent's name
    deps_type: type | None = None,               # defaults to the agent's deps_type
    activity_config: ActivityConfig | None = None,              # default: start_to_close_timeout of 60 seconds
    model_activity_config: ActivityConfig | None = None,        # merged on the base; heartbeat_timeout defaults to 30 seconds
    event_stream_handler_activity_config: ActivityConfig | None = None,
    toolset_activity_config: dict[str, ActivityConfig] | None = None,   # keyed by toolset id
    run_context_type: type[TemporalRunContext] = TemporalRunContext,
    continue_as_new: ContinueAsNewHandler | None = None,
    non_retryable_model_errors: Sequence[type[ModelAPIError]] = (
        ModelRequestRejectedError, ModelQuotaExceededError, ModelRequestTimeout),
)
```

Per-tool configuration lives in tool metadata under the key `'temporal'`, set on the tool or through the `SetToolMetadata` capability:

| Metadata value | Effect |
|----------------|--------|
| an `ActivityConfig` | merged over the toolset and base configs for that tool's activity |
| `False` | the tool runs inline in workflow code. Only valid for `async` function tools; a sync tool, an MCP tool or a dynamic-toolset tool raises `UserError` |
| `{'child_workflow': ChildWorkflowConfig(...)}` | the tool body runs as a Temporal child workflow. Only for async function tools. Default child id: the parent workflow id, two hyphens, and a `workflow.uuid4()` value |

Config keys are validated when the capability is constructed, because "a misspelled or misplaced key ... would only fail once the config is handed to Temporal inside the workflow, where the failure is retried indefinitely" (guide: "Activity Configuration").

Plugins (`C/durable_exec/temporal/__init__.py:195-313`):

- `PydanticAIPlugin()` is a client and worker plugin. It replaces the payload converter with `PydanticAIPayloadConverter` (Pydantic-based; a custom codec, failure converter or external storage is preserved). It adds passthrough modules to a `SandboxedWorkflowRunner`. It registers `workflow_failure_exception_types`: `UserError`, `PydanticUserError`, `AgentRunError`, `FallbackExceptionGroup`, `UnsupportedEventLoopError`, `WorkspaceError`. When a worker is configured it walks each workflow class's `__pydantic_ai_agents__` and registers the activities and child workflow classes of every listed agent.
- `AgentPlugin(agent)` is a worker plugin that registers one agent's activities and child workflow classes. It does not set the converter or the sandbox passthrough (INFERRED from its constructor); the tests pair it with a client that carries `PydanticAIPlugin`.
- `PydanticAIWorkflow` is an optional base class that only types the `__pydantic_ai_agents__` attribute.
- `TemporalDurability.temporal_activities` returns the activity list for manual registration and raises `UserError` when any tool uses a child workflow.
- `TemporalAgent` is the older wrapper path, deprecated and "will be removed in v3". Histories recorded under it replay under the capability if names and ids are unchanged.

Passthrough modules added by the plugin (`C/durable_exec/temporal/__init__.py:125-192`): `pydantic_ai`, `pydantic_graph`, `pydantic`, `pydantic_core`, `annotated_types`, `pydantic_monty`, `logfire`, `rich`, `httpx`, `anyio`, `sniffio`, `httpcore`, `certifi`, `fastmcp`, `mcp`, `anthropic`, `openai`, `google.auth`, `beartype`, `attrs`, `numpy`, `pandas`, `genai_prices`, `httpx2`, `httpcore2`, `truststore`, and, when the harness is installed, `pydantic_ai_harness`, `opentelemetry` and `stackone_defender`.

### 4.3 Activity and child workflow names

`TemporalOperationNamer` (`C/durable_exec/temporal/_operation_names.py`). The prefix is `agent__<agent name>`. The class docstring: "These names must essentially never change. Changing them can strand in-flight workflows and recorded runs."

| Operation | Activity name |
|-----------|---------------|
| model request | `agent__<name>__model_request` |
| streamed model request | `agent__<name>__model_request_stream` |
| cancel a suspended model response | `agent__<name>__model_cancel_suspended_response` |
| model-side message compaction | `agent__<name>__model_compact_messages` |
| durability event stream handler | `agent__<name>__event_stream_handler` |
| function tool call | `agent__<name>__toolset__<toolset id>__call_tool` |
| function tool argument validation | `agent__<name>__toolset__<toolset id>__validate_args` |
| MCP list tools, instructions, tool call | `agent__<name>__mcp_server__<toolset id>__get_tools`, `...__get_instructions`, `...__call_tool` |
| dynamic toolset list tools, tool call, validation | `agent__<name>__dynamic_toolset__<toolset id>__get_tools`, `...__call_tool`, `...__validate_args` |
| capability durable operation | `agent__<name>__capability__<capability id>__<operation>` |
| child workflow for a function toolset | `<call_tool activity name>__child_workflow` |

There is one `call_tool` activity type per toolset, not per tool. The tool name travels in the activity input and in the activity summary (`call tool: <toolset id>:<tool name>`). Tools defined directly on the agent belong to a toolset whose id is `<agent>`; the tests assert names such as `agent__complex_agent__toolset__<agent>__call_tool`.

### 4.4 What crosses into an activity

Each activity receives two arguments: a parameter object and `deps`. A model-request activity's parameters include the full message list, the model settings, the request parameters, the model id and a serialised run context. A tool-call activity's parameters include the tool name, the arguments, the tool definition and the serialised run context (`C/durable_exec/temporal/_transports.py:205-218`, `C/durable_exec/temporal/_toolset.py:51-59`).

The run context inside an activity is a restricted copy. Available: `deps`, `run_id`, `conversation_id`, `metadata`, `retries`, `tool_call_id`, `tool_name`, `tool_call_approved`, `tool_call_metadata`, `retry`, `max_retries`, `run_step`, `partial_output`, `usage`, `usage_limits`, `deadline`, `trace_include_content`, `instrumentation_version`, `loaded_capability_ids`, `discovered_tool_names`, `capability_active`. Reading `model`, `prompt`, `messages`, `model_settings`, `tracer` or `capabilities` raises `UserError`. `ctx.enqueue()`, `ctx.dequeue()`, `ctx.load_capability()`, `ctx.cancel()` and `ctx.emit()` raise inside an activity. A custom `TemporalRunContext` subclass passed as `run_context_type` can carry more (guide: "Agent Run Context and Dependencies").

## 5. Limits, budgets and failure handling

### 5.1 Monty

| Limit | Default | Notes |
|-------|---------|-------|
| `max_memory` | none | Bytes requested from the worker's allocator, "not a ceiling on process RSS". Operations with a predictable result size are refused before allocating, above a 100 KB threshold. |
| `max_feed_duration_secs` | none | Execution time for one feed. The clock "runs only while the interpreter executes bytecode or its native builtins"; it is paused while suspended on the host and while sleeping. |
| `max_turn_duration_secs` | none | Same clock, restarted at each host answer. |
| `max_recursion_depth` | 1000 | `RecursionError` is catchable. Cannot be disabled. |
| `max_suspensions` | 1000 per checkout | Counts external calls, host-object calls, OS callbacks, name lookups and future resolutions. The first suspension over budget raises an uncatchable `RuntimeError: suspension limit N exceeded`; the session stays consistent. Cannot be disabled. |
| `max_total_sleep_secs` | none | Cumulative `'system'` sleep. |
| `sleep_system_max` | 10 s per sleep | A longer `'system'` sleep returns early without error. |
| `request_timeout` | none for local pools, 10 s for remote pools | Host-side per-turn wall deadline; the worker is killed and the call raises `MontyCrashedError` with `timed_out=True`. Each resume starts a new deadline. |
| Print collectors | 10 MiB | Enforced in the host process. |
| Dump and message size | 256 MiB | A value and its envelope must fit; exported values deeper than the remaining recursion depth become the string `<deeply nested>`. |
| Worker recycling | after 1000 checkouts | Bounds how long a leaking or compromised worker serves. |
| Compilation | AST nesting capped at 200 levels | Compilation time is not charged to the duration budgets. |

After a memory or time limit fires the session must be discarded: "no guarantees are made about heap state or reference counts". The pool does not do this for the caller; the checkout stays open and "a later feed may quietly succeed against a heap you can no longer trust" (`M/docs/resource-limits.md:315-329`). There is no cumulative per-session time budget: "every limit restarts, so a long-lived session is bounded per request rather than over its lifetime."

### 5.2 Pydantic AI on Temporal

| Item | Value | Source |
|------|-------|--------|
| Base activity timeout | `start_to_close_timeout` of 60 seconds when no `activity_config` is given | guide: "Activity Configuration" |
| Model activity heartbeat timeout | 30 seconds by default; other activities have none | same |
| Heartbeats | every registered activity heartbeats in the background, at half the configured heartbeat timeout or every 5 s | `C/durable_exec/temporal/_toolset.py:62-102` |
| Shell command activities | 300 seconds for `run_command` and `shell` | guide: "Command timeouts" |
| Retry attempts | Temporal's default; for model requests the guide says "`RetryPolicy.maximum_attempts` of `0` means unbounded re-execution". The base config sets no attempt cap for tool activities either (INFERRED from `_durability.py:311-315`) | guide; source |
| Never retried | `UserError`, `PydanticUserError`, `UnexpectedModelBehavior`, `FallbackExceptionGroup`, four workspace errors, their subclasses, and Temporal's over-limit payload failures | `C/durable_exec/temporal/_toolset.py:150-196` |
| Model errors not retried by default | `ModelRequestRejectedError`, `ModelQuotaExceededError`, `ModelRequestTimeout` | guide: "Activity Retries" |
| `Retry-After` | honoured as the next retry delay, capped at the policy's `maximum_interval` | same |
| Payload size | 2 MB per payload by default; about 1.5 MB of raw bytes for base64 content | guide: "Large Payloads" |
| History size | "capped at around 50,000 events or 50MB" | guide: "Continuing as New for Long-Running Agents" |
| Workflow task deadline | "Temporal fails a workflow task that does not yield within 2 seconds" | cm: "Temporal durability" |

Failure routing:

- A tool exception reaches workflow code as Temporal's `ActivityError`, "with the original exception's class name in `cause.type`". A `ModelAPIError` from a model request is rebuilt as itself in workflow code, so `on_model_request_error` hooks and `ModelFallback` work the same as without Temporal.
- An exception in workflow code that is one of the registered failure types fails the workflow. Any other exception fails the workflow task, and Temporal retries the task while the workflow stays running.
- An oversized value travelling into an activity is encoded in workflow code and so fails the workflow task: "the run retries indefinitely rather than raising." Only what an activity returns gets a friendly `UserError`. The guide recommends Temporal external storage or a claim-check payload codec.
- `continue_as_new` is opt-in. Just before each model request the capability checks `workflow.info().is_continue_as_new_suggested()` and, if true, calls the user's function, which calls `workflow.continue_as_new(...)` with the message history and usage. "At that point, every tool result from the previous step is already in the history and nothing is in flight."

## 6. Durable execution / Temporal specifics

### 6.1 What keeps a replayed script deterministic

1. Every source of outside data is an activity. Model output, including the script text, and every nested tool result come from history on replay.
2. The sandbox is passed through, not rebuilt differently. `PydanticAIPlugin` lists `pydantic_monty` as a passthrough module so the workflow sandbox uses the host's copy. The page is explicit that this is only an enabler: "This makes Monty runnable there, but `run_code` still executes in workflow code and is re-executed during replay." Monty's own test suite guards the import behaviour this needs. Its comment: "hosts that run `pydantic_monty` under an import-restricted environment (e.g. Temporal's workflow sandbox) fail any module first imported after the initial load — pool construction must not trigger a `pydantic_monty.*` import" (`M/crates/monty-python/tests/test_pool.py:24-43`).
3. The interpreter has no ambient clock or entropy in this configuration. Code Mode and DynamicWorkflow check out every session with `{'datetime': 'call_host', 'sleep': 'call_host', 'random_start': 'call_host'}`. The comment: "without one they are unavailable, which keeps sandbox code deterministic when a Temporal workflow replays it" (`H/_monty_exec.py:124-127`). So `datetime.now()`, `time.time()` and unseeded `random` either fail or are answered by the application's handler.
4. Monty's own semantics are stable. Its hashes "are stable within a run and across runs of the same build (there is no hash randomisation)", its sets keep insertion order, and a seeded `random` reproduces CPython 3.14's sequence (`M/docs/limitations/builtins.md:49-58`, `M/docs/limitations/random.md:3-7`).
5. Elapsed time cannot change the control flow in Code Mode, because the execution timer is removed inside a workflow. A unit test pins it: with a 0.001 s limit configured, a 100,000-iteration loop still completes when `in_temporal_workflow()` is true, while the memory limit still fires (`T/code_mode/test_code_mode.py:1192-1225`).
6. Scheduling order is fixed by construction. Nested calls are dispatched in the order the sandbox makes them, each becomes a task on Temporal's deterministic event loop, and their activity commands are issued when the executor awaits the gather.
7. Identifiers are derived, not random. Nested tool call ids are the parent id plus a counter; default run ids come from the workflow run id and `workflow.uuid4()`; the run deadline uses `workflow.now()`.
8. Waiting is durable. Sandbox sleeps become workflow timers; agent graph sleeps use `workflow.sleep`.
9. Features that overlap non-deterministic work with the model stream are off: eager execution, speculation and the REPL store (cm; `H/code_mode/_capability.py:361-378`).
10. Replay-only infrastructure effects stay out of history. The remote checkout backoff sleeps in the portal thread, so an original run that backed off and a replay that did not produce the same commands.

### 6.2 Hazards and limitations named by the docs or the code

Each item is something the sources themselves flag.

Script execution in workflow code:

- Unrecorded host access. "`mount` reads and writes, `os_access` callbacks, and host-clock calls happen again during replay; changing their results can change which activities the workflow schedules and cause a `NondeterminismError`. Put external reads, writes, clock access, and other side effects in wrapped tools so Temporal records them as activities" (cm).
- Replay does not catch everything. "Replay may not flag changed arguments when the same activity remains at the same history position, so replay validation is not a substitute for this boundary" (cm).
- Compute blocks the workflow. "Temporal activity timeouts apply to nested tools, not pure computation inside `run_code`. The workflow waits while the sandbox computes, and Temporal fails a workflow task that does not yield within 2 seconds, so move heavier computation into a tool" (cm). INFERRED consequence for Code Mode: with the execution timer removed and no `request_timeout` on a local pool, Monty itself puts no time bound on a CPU-bound snippet inside a workflow; only the memory and suspension limits and Temporal's task deadline remain.
- DynamicWorkflow keeps a user-set timer. A script "that runs close to it can stop at a different point on replay and cause a `NondeterminismError`" (Dynamic Workflow page). Its README also notes that a pure-CPU loop "burns a core in the sandbox worker (and blocks the workflow thread under Temporal)" (`H/dynamic_workflow/README.md:347-348`).
- Every replay re-runs every snippet. The REPL "is process-local state for one agent run, not durable storage" (cm). INFERRED: each replay pays for a new sandbox process and for re-executing all sandbox computation of the run so far.
- Remote sandboxes must stay reachable: "replay connects to the worker again, so it must be reachable whenever the workflow replays" (cm). The `monty_sandbox` field docstring says "Inside Temporal, only the transport deadline applies" (`H/code_mode/_capability.py:173-174`). INFERRED from the code: that holds for the factory form, whose pool keeps its own `request_timeout` (10 s by default). For the URL form the harness sets the deadline to `max_feed_duration_secs` plus 10 s and passes `None` when that limit is `None` (`H/_monty_exec.py:130-133`, `179`), which it always is inside a workflow (`H/code_mode/_toolset.py:1276`). Monty documents `request_timeout=None` as "wait indefinitely with wall-mode servers" (`M/crates/monty-python/python/pydantic_monty/_monty.pyi:1192-1198`). So a URL-form remote sandbox inside a workflow has no per-turn deadline at all. The repository's own test for a remote sandbox in a workflow is skipped as intermittently hanging (section 8).
- Setup errors can stall instead of failing. "`PydanticAIPlugin` treats `UserError` as a workflow failure. Other setup errors, such as `RuntimeError` and `MontyRefusedError`, follow Temporal's default: an uncaught error fails the workflow task, not the workflow. Temporal retries the task while the workflow remains running, so a client awaiting its result keeps waiting if setup keeps failing." The remedy given: catch the expected setup exceptions in the workflow and raise `ApplicationError(..., non_retryable=True)` (cm). A test asserts the task failure type `MontyRefusedError` with the workflow still `RUNNING` (`T/code_mode/test_temporal.py:440-473`).
- Step ordering under concurrency. For engines that number nested calls as steps: "When a tool or a capability hook such as `before_tool_execute` awaits before its step starts, concurrent calls can start their steps in a different order on recovery" (cm: "Concurrent tool calls"). The remedy named, `parallel_execution_mode='sequential'`, is a `DBOSDurability` argument; `TemporalDurability` has none, and the Temporal engine spec sets `sequential_tools_in_durable_context=False` (`C/durable_exec/temporal/_spec.py:22`).
- Sub-agents need their own durability: attach `TemporalDurability` "to each sub-agent whose model requests should run as activities" (Dynamic Workflow page).

Agent runs on Temporal in general:

- Names are contracts. Agent `name` and toolset `id`s "should not be changed once the durable agent has been deployed to production as this would break active workflows" (guide: "Agent Names and Toolset IDs").
- Upgrades can change the activity sequence. Example given: tools with an `args_validator` gained a `validate_args` activity, so in-flight workflows "need Temporal worker versioning or a patch". Adding a workspace to an agent also "changes its workflows' history".
- Payload schemas are contracts. "Temporal deserializes persisted workflow and activity payloads using the models and type annotations available in the currently deployed worker, so treat these models as durable contracts across deployments."
- Everything crossing into an activity must be Pydantic-serialisable: `deps`, `model_settings`, run-context metadata, tool metadata. Untyped dictionaries arrive as their JSON shapes.
- Construction must happen outside the workflow. Binding inside a workflow raises `UserError`: the agent "must be constructed outside of a Temporal workflow, so its activities can be registered with the worker before the workflow runs" (`C/durable_exec/temporal/_durability.py:342-348`). For the same reason `agent.run(capabilities=[...])` and executing toolsets passed per run raise `UserError` inside a workflow.
- Model instances cannot be passed at run time unless registered in `models=`; model-name strings are rebuilt on the worker. A `ResolveModelId` resolver "must be deterministic for a given `(model_id, deps)` and must not perform external I/O".
- A `DynamicToolset` factory "is re-resolved inside activities ... so it must be deterministic given the run dependencies".
- Code that runs in workflow code must be deterministic: a `ProcessEventStream` handler "must be deterministic because it re-runs on workflow replay"; `@on_event` listeners "re-run on every workflow replay"; approval listeners run again on replay, "so make their external effects idempotent".
- Activities can run more than once. An event stream handler "may run more than once if an activity retries, so keep its side effects idempotent". Model events published to a Workflow Stream from a retried activity "are published again at new offsets".
- Mutations inside an activity are lost. The activity's `RunContext` is a copy; a delegate agent's usage charged to `ctx.usage` inside a tool is "missing from the parent run's `result.usage`".
- Cancellation must be replayable. A `CancellationToken` may only be fired from a signal handler or other workflow code; fired elsewhere before a run takes it, "the workflow won't replay".
- Streaming is buffered inside a workflow. Events from `run_stream()`, `run_stream_events()` and `iter()` "are buffered rather than delivered in real time": the model stream runs inside the activity and the workflow replays the final response as one event per part. Live delivery needs the durability `event_stream_handler=` or a Workflow Stream topic. `ctx.emit()` from a tool raises `UserError`.
- Unsupported outright: `Agent.run_sync()` and `run_stream_sync()` in workflow code; an `output_type` that includes `BinaryImage`.
- Retries can multiply. The guide recommends turning off provider SDK retries (`max_retries=0`) so Temporal is the one retry owner.
- Python 3.14: the repository skips its Temporal sandbox tests there, with the reason "temporalio sandbox is incompatible with Python 3.14" (`T/_temporal.py:20-25`).

## 7. Implementation notes from the source code

- The portal is the whole Temporal adaptation of the sandbox loop. Outside a workflow `call_monty` is a plain `await`; inside it is `portal.call(...)`. The same helper wraps context-manager entry (`portal.wrap_async_context_manager`). Release of sessions and pools is shielded from cancellation so a cancelled run does not leak a worker (`H/_monty_exec.py:89-117`).
- `in_temporal_workflow()` (`H/_monty_exec.py:68-74`) is true for any code running in a workflow, whether or not the agent has `TemporalDurability`. The engine-neutral checks are separate functions that read the durability capability's `in_durable_context` flag: `in_durable_execution(ctx)` in `H/code_mode/_toolset.py:199-204`, which Code Mode uses to switch off eager execution, speculation and the REPL store, and `in_durable_context(ctx)` in `H/_durable.py:59-66`, which other harness capabilities use. The comment in `H/_durable.py:57-58` gives the distinction: the first asks Temporal directly "because only Temporal replays `run_code`".
- Activities are started through a wrapper, not `workflow.execute_activity`. `execute_activity` shields the Temporal handle and forwards exactly one cancellation, because awaiting an activity directly from a task inside an anyio cancel scope "can livelock the workflow when that scope is cancelled", ending with Temporal's deadlock detector failing the task repeatedly (`C/durable_exec/temporal/_activity_execution.py:1-64`).
- Each function toolset gets a child workflow class generated alongside its `call_tool` activity, but a worker registers it only if some tool selects a child workflow when the worker is configured. The set of registered child workflow names lives at module level in a passthrough module "because the sandbox re-imports the user's workflow module and so runs calls on re-created agents whose backends never saw the worker being configured" (`C/durable_exec/temporal/_operation_backend.py:52-58`, `284-321`).
- A child workflow tool body that raises is converted to an `ApplicationError` so the child fails "instead of failing the workflow task, which Temporal would retry forever".
- The payload converter caches one Pydantic `TypeAdapter` per type hint and event-registry version; the comment argues the cache is replay-safe because an adapter "is a pure function of its type hint" (`C/durable_exec/temporal/_payload_converter.py:19-45`).
- The plugin imports the anyio backends eagerly at module import, "or it will happens inside workflow code and temporal has issues" (`C/durable_exec/temporal/__init__.py:66-75`).
- Toolset lifecycles differ under Temporal: function and MCP toolsets are entered only outside the workflow; inside, each activity resolves and enters what it needs, because "an activity may run in another process". An MCP toolset is therefore connected inside every activity that lists or calls its tools; with `cache_tools` the workflow keeps the tool definitions so discovery costs one activity per run (`C/durable_exec/temporal/_spec.py:15-19`; guide).
- DynamicWorkflow's budget counter relies on asyncio atomicity: the `max_agent_calls` check and increment have no `await` between them, which "is what makes `max_agent_calls` an exact ceiling under fan-out" (`H/dynamic_workflow/_toolset.py:712-730`).
- The DynamicWorkflow toolset is not a function, MCP or dynamic toolset, so the durability capability leaves it unwrapped and `run_workflow` runs in workflow code (INFERRED from `C/durable_exec/_base.py:1270-1298` and `C/durable_exec/temporal/_durability.py:420-436`; consistent with the test).

## 8. Maturity, versions, caveats

### 8.1 Versions on 2026-11-12

| Package | Current | Notes |
|---------|---------|-------|
| pydantic-monty | 1.6.0, published 2026-11-11 | Requires Python 3.10+. 1.0.0 was published 2026-09-25; the docs index says of it "we now consider it ready for production use", provided as is without warranty under the licence. |
| pydantic-ai-harness | 0.68.0, published 2026-11-12 | Requires Python 3.11+. Extras `codemode` and `dynamic-workflow` require `pydantic-monty>=1.5.0,<2`. Classifier "Development Status :: 3 - Alpha". |
| pydantic-ai-slim | 2.68.0, published 2026-11-12 | Extra `temporal` requires `temporalio>=1.27.0`; the repository lock file uses temporalio 1.31.0 and pydantic-monty 1.5.0. |

The Monty release cadence in the last seven weeks was 1.0.0, 1.1.0, 1.2.0, 1.3.0, 1.4.0, 1.5.0 and 1.6.0.

### 8.2 What changed in the Python API between 0.0.18 and 1.6.0

Version 0.0.18 was released 2026-05-29; its README still carried "Experimental - This project is still in development, and not ready for the prime time." The API at that tag, from `_monty.pyi` and `README.md` at `v0.0.18`:

```python
m = pydantic_monty.Monty(code, inputs=['x'], script_name='main.py',
                         type_check=False, type_check_stubs=None, dataclass_registry=None)
m.run(inputs={...}, limits=..., external_functions={...}, print_callback=..., mount=..., os=...)
await m.run_async(inputs={...}, limits=..., external_functions={...}, print_callback=..., os=...)
snap = m.start(inputs={...}, limits=..., print_callback=..., mount=..., os=...)
snap = snap.resume({'return_value': ...})        # FunctionSnapshot | NameLookupSnapshot | FutureSnapshot | MontyComplete
blob = snap.dump(); snap2 = pydantic_monty.load_snapshot(blob)
blob = m.dump(); m2 = pydantic_monty.Monty.load(blob)
repl = pydantic_monty.MontyRepl(limits=..., type_check=...); repl.feed_run(code, external_functions={...})
```

Changes that matter for running model-written orchestration scripts:

| Area | 0.0.18 | 1.6.0 | Release that changed it |
|------|--------|-------|-------------------------|
| Where code runs | in the caller's process | always in a `monty` worker subprocess from a pool; no in-process mode in Python | 0.0.19, "Move execution to a subprocess pool" |
| Entry point | `Monty(code, ...)` holds one parsed script; `MontyRepl` is a separate class | `Monty()` or `AsyncMonty()` is a pool; `pool.checkout()` gives a REPL session; code is passed to `feed_run` or `feed_start` | 0.0.19 |
| Declaring inputs | names declared up front, `inputs=['x']`, values at run time | `inputs={...}` per feed | 0.0.19 |
| Host functions | `external_functions={'name': fn}` on `run`, `run_async`, `feed_run` | `external_lookup={'name': fn_or_value}`; non-callable values allowed; `ExternalModule` for importable host modules | 0.0.19 "rename and change behaviour of `external_lookup`"; 1.2.0 "Import host call" |
| Async host functions | `Monty.run_async()`; module-level `run_monty_async()` already deprecated | `AsyncMonty` sessions; `await session.feed_run(...)`; `stream_async_calls` option | 0.0.19, 0.0.20 "Make monty-pool async end-to-end", 1.6.0 |
| Manual suspension | `m.start()` and `snap.resume(...)`; `mount=` and `os=` accepted on each `resume` | `session.feed_start()`; `resume(...)`, `resume_auto()`, `resume_not_handled()`; mounts and `os` fixed for the feed | 0.0.19 "reintroduce `feed_start`", "Add `resume_auto()`" |
| Snapshots | `snap.dump()` with module functions `load_snapshot()` and `load_repl_snapshot()`; `Monty.dump()` and `Monty.load()` cache parsed code | `snapshot.dump()` or `session.dump()`; `session.load_snapshot()` and `session.load_session()` on a fresh session; one dump format, CBOR, versioned | 0.0.20 "Collapse the two dump versions into one session dump format"; 1.0.0 "Switch dumps to use CBOR instead of postcard" |
| Limits passed | per `run()` or `start()` call, or on `MontyRepl()` | per `checkout()` | 0.0.19 |
| Limit keys | `max_allocations`, `max_duration_secs`, `max_memory`, `gc_interval`, `max_recursion_depth` | `max_feed_duration_secs`, `max_turn_duration_secs`, `max_memory`, `gc_interval`, `max_recursion_depth`, `max_suspensions`, `max_total_sleep_secs`; unknown keys are rejected | 0.0.19 "Remove `max_allocations`"; 0.0.22 "Reject unknown keys in the limits dict", "Add `max_suspensions`"; 1.0.0 "Replace session `max_duration` with feed and turn limits" |
| Type checking | `type_check=` on the constructor; `MontyTypingError.display(format, color)` | `type_check=`, `type_check_stubs=`, `type_check_format=`, `type_check_color=` on `checkout()`; `display()` takes no arguments | 0.0.19 and later |
| OS callback | positional `os(function_name, args, kwargs)` | keyword `os(*, name, args, kwargs, is_async, **_)` | 1.0.0 (still positional at tag `v0.0.23`, keyword at tag `v1.0.0`) |
| Clock and randomness | no `time` or `random` module | `time`, `random`, `asyncio.sleep`; behaviour set by `os_policy` | 1.0.0 "Add the `random` module", "Add `time.time()`, `time.sleep()` and `asyncio.sleep()`", "Auto OS calls", renamed to `OsPolicy` |
| Print collectors | uncapped | 10 MiB cap by default | 0.0.19 "Cap CollectString/CollectStreams" |
| Dataclass handling | `dataclass_registry=`, `register_dataclass()` | removed; host objects go through `ClassInstance` and `ClassType` | 0.0.22 "Replace wire `Dataclass` with `ClassInstance`" |
| Language | no classes, no `match` | simple classes without inheritance, class and function decorators, `match`, native `@dataclass`, `eval` and `exec` | 0.0.19 "User-defined classes"; 0.0.20; 1.0.0; 1.2.0 "Match statements" |
| Standard library | README lists `sys`, `os`, `typing`, `asyncio`, `re`, `datetime`, `json` | the 28 modules in section 4.1 | 0.0.19 through 1.5.0 |
| Remote execution | none | `AsyncMontyRemote`; WebSocket transport from 0.0.19, replaced by gRPC in 1.5.0 | 0.0.19 "websocket protocol"; 1.5.0 "gRPC transport" |
| Packaging | one wheel with the interpreter | metapackage over `pydantic-monty-client` and `pydantic-monty-runtime` (the worker binary) | 0.0.20 "Split `pydantic-monty` into a metapackage" |
| Tracing | none | `snapshot.trace_context()`, `instrument_telemetry()` | 1.0.0 "expose otel trace context from snapshots" |
| Restore rules | not documented in the README | restored limits are capped at the restoring checkout's; the restoring checkout's OS policy applies | 1.2.0 "Cap restored session budgets at destination limits"; 1.5.0 "Apply the checkout's os_policy to restored sessions" |

The 0.0.18 README also claimed start-up "<1μs to go from code to execution result" and run-time speed "generally between 5x faster and 5x slower" than CPython. Those numbers describe the in-process interpreter; the current docs quote pool checkout latency instead and do not repeat the run-time comparison.

### 8.3 Caveats

- `TemporalDurability` is the current path; `TemporalAgent` is deprecated. A test docstring dates this: "pydantic-ai 2.14 deprecated the `TemporalAgent` wrapper in its favor" (`T/code_mode/test_temporal.py:10-12`).
- Temporal's replay-safe tracer provider, used by `LogfirePlugin`, is described as experimental; Temporal external storage is described as public preview (guide).
- The test that runs Code Mode against a remote sandbox inside a workflow, including the durable-timer and `workflow.now()` assertions, carries `@pytest.mark.skip(reason='Hangs intermittently in CI: ...')` pointing at pydantic-ai issue 8824 (`T/code_mode/test_temporal.py:525`).
- A stale comment in the tests says the plugin does not pass `pydantic_graph` through; at this commit it does (`C/durable_exec/temporal/__init__.py:135`).
- Monty's security page runs a bounty programme and states the limits of its guarantees: host functions run "with your process's full authority", workers are reused until recycled, snapshots are not authenticated, and a remote peer may not be a Monty sandbox at all.

## 9. What could not be confirmed

- Nothing was executed. No workflow was run and no history was inspected; the event-history descriptions in sections 3.5 and 3.8 come from test assertions and code reading. The three-sub-agent history and the patch marker are INFERRED.
- How the Monty worker process, the portal thread and the suspended session are cleaned up when Temporal evicts a workflow from a worker's cache mid-run. The release code is shielded, but the eviction path was not traced. NOT VERIFIED.
- The exact behaviour of a sub-agent without `TemporalDurability` inside a DynamicWorkflow script in a workflow. NOT VERIFIED.
- Whether the Temporal SDK's 2-second figure is configurable and how the SDK behaves after it fires. The sources state only the sentence quoted in section 5.2.
- How `continue_as_new` interacts with a script in flight when a sub-agent, rather than the orchestrator, carries the handler. Not discussed in the sources.
- Which exception type the sandbox sees for a host exception class Monty does not implement, such as Temporal's `ActivityError` raised by a failed nested tool activity. NOT VERIFIED.
- Monty's run-time performance relative to CPython at 1.6.0. The current docs give latency figures only.
- The deprecated `TemporalAgent` implementation, the Workflow Streams implementation and the model-error rebuilding code were not read; statements about them come from the guide.
- The licence of the documentation site text beyond its copyright footer.
