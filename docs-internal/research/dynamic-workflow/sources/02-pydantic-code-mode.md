# Pydantic AI Harness Code Mode (digest)
> **Status: Prototype reference.** External source digest: this file describes Pydantic AI Harness and Monty, not OpenCompany. Paths, names and commands in it belong to those projects.

Digest written 2026-11-12. Nothing from the sources was executed, built or installed; every statement below comes from reading documentation, source code and tests. A statement marked INFERRED is a conclusion drawn from the code that no document states outright. A statement marked NOT VERIFIED could not be checked.

Citation shorthand used throughout:

- A citation of the form `page: "Return values"` names a section of the Code Mode documentation page (source S1 below).
- `H/` is the directory `src/pydantic_ai_harness/pydantic_ai_harness/` in the pydantic-ai clone (source S4). `C/` is `pydantic_ai_slim/pydantic_ai/`. `T/` is `tests/harness/`. A number after a colon is a line number at the commit named in S4.

The companion file `03-pydantic-monty-and-temporal-durability.md` holds the Monty API and the Temporal integration in full. Section 6 here only summarises what changes for Code Mode under Temporal.

## Sources read

| # | Source | URL | Fetched | Version or commit | Licence |
|---|--------|-----|---------|-------------------|---------|
| S1 | Code Mode documentation page (guide and API reference) | https://pydantic.dev/docs/ai/harness/code-mode/ | 2026-11-12 | live page; its text mirrors `H/code_mode/README.md` at the S4 commit | page footer says "© Pydantic Services Inc. 2025 to present"; the mirrored README sits in the MIT-licensed repository |
| S2 | IPython Code Mode documentation page | https://pydantic.dev/docs/ai/harness/ipython-code-mode/ | 2026-11-12 | live page | as S1 |
| S3 | Pydantic AI Harness overview page (version policy, capability list) | https://pydantic.dev/docs/ai/harness/ | 2026-11-12 | live page | as S1 |
| S4 | pydantic-ai repository, shallow clone | https://github.com/pydantic/pydantic-ai | 2026-11-12 | commit `d94586aad1acd640715f2bf1152e8d730c8f9938`, committed 2026-11-12T05:48:03Z | MIT (root `LICENSE` and `src/pydantic_ai_harness/LICENSE`) |
| S5 | PyPI package metadata | https://pypi.org/pypi/pydantic-ai-harness/json | 2026-11-12 | pydantic-ai-harness 0.68.0 (published 2026-11-12), pydantic-ai-slim 2.68.0 (published 2026-11-12) | MIT |

Files read in full in S4: `H/code_mode/__init__.py`, `_capability.py`, `_toolset.py`, `_parts.py`, `_repl_store.py`, `_eager.py`, `_streaming.py`; `H/_monty_exec.py`, `H/_code_tools.py`, `H/_durable.py`; `T/code_mode/test_temporal.py`. Read in part: `H/code_mode/_speculation.py` (lines 1 to 250 plus an outline of the rest), `H/code_mode/_events.py` (class and field outline), `H/code_mode/README.md` (Temporal and API sections), `T/code_mode/test_code_mode.py` (the Temporal, signature and limit tests), `C/tool_manager.py` (`handle_call` and `_execute_tool_call_impl`).

## 1. What it is

Code Mode is a capability (`CodeMode`) in the `pydantic-ai-harness` package. Attached to a Pydantic AI `Agent`, it removes the selected tools from the model-facing tool list and gives the model one tool instead, `run_code`. The model writes a Python snippet; the snippet runs in the Monty sandbox; inside it every selected tool is an ordinary Python function. Calls from the snippet leave the sandbox, run through Pydantic AI's normal tool pipeline on the host, and their results come back into the sandbox as plain values. Only the value of the snippet's last expression (plus anything printed) goes back to the model (page: "The solution").

The stated purpose is to cut model turns. Of plain tool calling the page says: "An agent that needs to fetch 10 items and then process their results can require many model turns, increasing latency, cost, and context use." Every intermediate result also enters the conversation history. With Code Mode the model "fans calls out with `asyncio.gather`, filters and transforms results, and returns only what matters" (page: "The problem", "The solution").

Code Mode has a sibling, IPython Code Mode (`IPythonCodeMode`), which exposes the same `run_code` tool but runs the code in a persistent IPython kernel. The differences, from S2:

| | Code Mode | IPython Code Mode |
|---|---|---|
| Built for | production and web-facing agents | local agents the operator trusts |
| Safe in a web server | yes | no, unless the whole agent runs in a sandbox |
| Python | the Monty subset, with time and memory limits | full CPython, any installed package |
| Filesystem and network | only through `mount`, `os_access` and tools | unrestricted, with the agent process's permissions |
| State | per run, plus an optional `repl_store` | a kernel per run, or one `IPythonKernel` kept across runs |
| Tool functions | `async`, concurrent with `asyncio.gather` | synchronous, one call at a time, over the Jupyter stdin channel |
| Limits | `resource_limits`, `max_tool_calls` | `timeout` per call (default 60 s), `max_tool_calls` |
| Durable execution | Temporal, DBOS, Prefect | not supported: "a run under Temporal, DBOS, or Prefect durability fails with `UserError` before any code runs" |

IPython Code Mode does not support eager execution, speculation, `dynamic_catalog`, `mount`, `os_access` or `resource_limits` (S2: "What carries over from Code Mode").

## 2. Vocabulary

| Term | Meaning in these sources |
|------|--------------------------|
| Capability | A Pydantic AI extension object attached with `Agent(capabilities=[...])`. It can wrap the agent's toolset and hook into the run. `CodeMode` is one. |
| Toolset | A source of tools. `CodeModeToolset` is the wrapper toolset the capability installs around the agent's assembled toolset. |
| `run_code` | The single tool Code Mode exposes. Arguments: `code` (string) and optional `restart` (boolean). |
| Snippet, feed | One piece of code submitted in one `run_code` call. Monty calls executing it a "feed". |
| REPL session | The sandbox interpreter state (variables, imports, definitions) that persists between `run_code` calls of one agent run. |
| Sandboxed tool | A tool the selector placed inside `run_code`; it is no longer offered to the model directly. |
| Native tool | A tool that stays a normal model-facing tool call. |
| Hidden tool | With `dynamic_catalog=True`, a deferred tool that `run_code` will take over once it is discovered; until then it is neither declared natively nor callable from code. |
| Nested call | One call from the snippet to a sandboxed tool. It has its own tool call id and is recorded in the `run_code` return's metadata. |
| Catalog | The rendered list of TypedDict definitions and function signatures that tells the model what it can call from code. |
| Suspension | A point where the sandbox stops and waits for the host: an external function call, an OS call, a name lookup or a future resolution. |
| Eager execution | Running complete statements while the model is still streaming the rest of the `run_code` call. |
| Speculation | Starting side-effect-free tool calls found in the partially streamed code before the snippet runs. |
| Worker, pool | Monty executes in separate worker processes drawn from a pool; local subprocesses by default, remote workers with `monty_sandbox`. |
| `mount`, `os_access` | The two ways to give sandboxed code filesystem, environment or clock access. |
| Model retry | A `ModelRetry` raised by `run_code`: the model receives the error text as the tool result and may try again, counted against `max_retries`. |
| Tool kind | A typed label on a tool definition. `run_code` declares `'code_mode.run_code'`, so its call parts are `RunCodeCallPart` instances. |

## 3. How it works

### 3.1 Wiring

1. `CodeMode()` goes in `Agent(capabilities=[...])`. Its ordering is `CapabilityOrdering(position='outermost', wraps=[ToolSearch])`, so it wraps around Tool Search and `search_tools` stays native (`H/code_mode/_capability.py:289-291`).
2. `get_wrapper_toolset` wraps the agent's assembled toolset in a `CodeModeToolset` (or `EagerCodeModeToolset` when `eager=True`) (`H/code_mode/_capability.py:320-350`).
3. `for_run` returns a fresh capability copy per run when `dynamic_catalog` or `speculate` is in use, so concurrent runs do not share announcement or speculation state. The toolset's `for_run` likewise returns a fresh toolset "with isolated REPL state for this agent run" (`H/code_mode/_capability.py:293-309`, `H/code_mode/_toolset.py:939-942`).

### 3.2 Which tools become functions

On every model step `CodeModeToolset.get_tools` places each wrapped tool as `'native'`, `'sandboxed'` or `'hidden'` (`H/code_mode/_toolset.py:1069-1158`).

A tool can never be sandboxed, whatever the selector says, when any of these holds (`H/_code_tools.py:37-51`):

- its `tool_kind` is `'tool-search'` or `'capability-load'` (framework control tools);
- it is an `unless_native` local fallback, which must stay native so the model request can drop it when the provider has the native tool;
- it is itself a code-execution tool, recognised by `'code_arg_name'` in its metadata. That covers `run_code`, DynamicWorkflow's `run_workflow`, `Shell`'s `run_command` and `start_command`, and `CapabilityCreation`'s `author_capability`. The reason given: "nesting one code surface inside another would make the model write a script that passes a second script as a string literal." A host can override this per tool by setting metadata `fold_into_code_mode` (constant `FOLD_INTO_CODE_MODE`) to `True`.

Then:

- A tool the run does not currently consider available (`ctx.is_tool_available(tool_def)` is false, for example a `defer_loading=True` tool not yet found by Tool Search) is `'hidden'` if `dynamic_catalog` is on and the selector matches it, otherwise `'native'`.
- An available tool that matches the `tools` selector is `'sandboxed'`.
- Everything else is `'native'`.

The `tools` selector is a Pydantic AI `ToolSelector`. Its forms (page: "Selective tool sandboxing", "Metadata-based selection"):

```python
CodeMode(tools='all')                                        # default: every eligible tool
CodeMode(tools=['search', 'fetch'])                          # by name
CodeMode(tools=lambda ctx, td: td.name != 'dangerous_tool')  # predicate, may be async
CodeMode(tools={'code_mode': True})                          # by tool metadata, deep inclusion match
```

Names are turned into Python identifiers: non-identifier characters become underscores, a leading digit gets a `_` prefix, a keyword gets a `_` suffix. When two tool names sanitise to the same identifier the first keeps it and the other is hidden from the sandbox with a `UserWarning`. A tool that sanitises to `run_code` raises `UserError`. A tool whose name is a Python builtin (`list`, `open`, `print`, anything in `dir(builtins)`) raises `UserError`, because "Monty resolves builtins before host names, so a tool with one of these names would never run" (`H/_code_tools.py:70-115`, `H/code_mode/_toolset.py:830-833`, `1476-1497`).

A sandboxed tool with no return schema still works but its signature shows `-> Any`. Code Mode names all such tools in one `CodeModeReturnSchemaWarning` per run (page: "Sandbox restrictions").

### 3.3 How the model learns the functions

Each sandboxed tool is rendered by core's `FunctionSignature` as a Python signature with keyword-only parameters. Structured parameter and return types are rendered first as `TypedDict` classes; when two tools define a type with the same name, each gets its tool name as a prefix. A tool is `async def` unless it is marked `sequential=True`, in which case it is a plain `def` (`H/code_mode/_toolset.py:1509-1533`). Examples asserted by tests (`T/code_mode/test_code_mode.py:1862-1867`, `1907-1910`):

```python
class Address(TypedDict):
    street: str
    ...

async def lookup_person(*, person: Person, count: int = 1) -> str
```

and, for a name conflict, `class get_user_Address(TypedDict):` and `class get_company_Address(TypedDict):`.

Where the catalog is placed depends on `dynamic_catalog`:

- `True` (the capability's default). The `run_code` description holds only static prose (sandbox restrictions, return-value contract). The catalog goes into the agent instructions as a dynamic `InstructionPart` with id `toolset:code_mode:catalog` and `on_change='append'`, so a tool discovered mid-run appends an updated catalog to the message history instead of rewriting the cached prompt prefix. Functions gained through Tool Search are also announced in a short `SystemPromptPart` (page: "Tool Search interaction"; `H/code_mode/_toolset.py:1039-1067`, `H/code_mode/_capability.py:417-446`).
- `False`. All signatures are rendered into the `run_code` description. A discovery then changes the description and invalidates the prompt cache from that point on.

The header prose tells the model that the functions are already defined, that all parameters are keyword-only, that async functions need `await`, and that to run calls concurrently it must `import asyncio` and use `await asyncio.gather(...)` with positional awaitables (`H/code_mode/_toolset.py:580-613`).

### 3.4 One `run_code` call, step by step

Source: `H/code_mode/_toolset.py:1160-1323` unless noted.

1. `call_tool` receives `code` and `restart`. With `restart: true` the REPL session is dropped first.
2. A `RunCodeExecution` object is created for this call. It holds the print collector, the nested-call counter, the recorded nested calls and returns, and any model-facing content returned by nested tools.
3. If a `repl_store` is configured and the run is not under durable execution, the saved state is loaded on the run's first `run_code` call.
4. A nested `ToolManager` is built over the wrapped toolset and the wrapped tools, inheriting the agent's capability chain, "so that sandboxed tool calls go through the standard validation/execution path".
5. Type checking is enabled only for the first executed snippet of a fresh REPL (`type_check = fresh_repl and bool(callable_defs)`). The stubs are the same TypedDicts and signatures with `raise NotImplementedError()` bodies, preceded by `import asyncio` and `from typing import Any, TypedDict, NotRequired, Literal`. The comment gives the reason for checking only the first snippet: "Monty's checker can reject valid later snippets that reuse imports or pass a runtime-validated dict to a TypedDict parameter."
6. Resource limits are resolved (section 5). Inside a Temporal workflow the execution-time limit is replaced by `None`.
7. The Monty session is obtained. The worker pool is created lazily on the first `run_code` call of the run: `AsyncMonty()` for local subprocess workers, or `AsyncMontyRemote(...)` for `monty_sandbox`. The session is checked out with the limits, the type-check settings and a fixed OS policy `{'datetime': 'call_host', 'sleep': 'call_host', 'random_start': 'call_host'}` (`H/_monty_exec.py:127`, `156-193`).
8. `MontyExecutor.run` starts the snippet with `session.feed_start(code, print_callback=..., os=..., mount=..., skip_type_check=...)` and drives it until Monty reports completion (section 3.5).
9. The final value is validated back into Pydantic AI's tool-return content type, so multimodal values such as binary content are reconstructed, and anything without a JSON form is replaced by its `repr`.
10. `make_tool_return` builds the tool return (section 3.7).

When the run ends, the toolset's `__aexit__` saves the REPL to the `repl_store` if one is in use, then closes the session and the pool (`H/code_mode/_toolset.py:980-995`).

### 3.5 How a nested call leaves the sandbox and reaches the host tool

Monty never calls out. When sandboxed code calls a name it does not define, the interpreter suspends and hands the host a snapshot. `MontyExecutor` (`H/_monty_exec.py:265-479`) handles each snapshot kind:

- `AsyncNameLookupSnapshot`: resumed with no value, so the sandbox raises `NameError`.
- `AsyncFunctionSnapshot` for an OS function: sleeps are handled by the executor; clock, environment and randomness calls are answered on the caller's thread when inside a Temporal workflow with an `os_access` handler; everything else goes to `snapshot.resume_auto()`, which lets Monty answer from the mounts and then the `os=` handler.
- `AsyncFunctionSnapshot` for a tool:
  - an unknown name is answered with `NameError('Unknown function: ...')` raised in the sandbox;
  - positional arguments are refused with `TypeError('<name>() does not accept positional arguments; use keyword arguments')`;
  - a `sequential` tool is resolved inline: every pending parallel call is awaited first (a barrier), then this call runs and the sandbox is resumed with its result;
  - any other tool is deferred: the dispatch coroutine is created, wrapped in an `asyncio.Task` straight away (unless the run is in global sequential mode, where it stays an unscheduled coroutine), and the sandbox is resumed with `{'future': ...}`. The snippet receives an awaitable and keeps running.
- `AsyncFutureSnapshot`: every sandbox task is blocked on host futures. The executor awaits the listed pending calls with `asyncio.gather(..., return_exceptions=True)` and resumes the sandbox with a result or an exception per call id. In global sequential mode they are awaited one at a time in dispatch order.

The dispatch callback is `CodeModeToolset._dispatch_tool_call` (`H/code_mode/_toolset.py:1420-1474`):

1. Reserve budget synchronously. `RunCodeExecution.next_tool_call_id` raises `RuntimeError` when `max_tool_calls` is already used; otherwise it increments the counter and returns the id `"<run_code tool_call_id>__<n>"`, with `n` counting from 1. The reservation is synchronous because the executor turns each deferred call into a task as soon as dispatch returns: "Counting inside the coroutine would let one `asyncio.gather` over many calls allocate a host task per call before the first check ran, which is the cost the budget bounds."
2. Build a `ToolCallPart` with the original tool name, the arguments keyed by the tool's schema property names, and that id.
3. If speculation holds an in-flight result for the same call, adopt it. Otherwise run `run_nested_call`.

`run_nested_call` (`H/_code_tools.py:148-208`):

1. Wait for the verdict of any parallel input guardrail; a blocked prompt makes the call fail with a `RuntimeError` in the sandbox.
2. If the run has a `tool_calls_limit`, check it counting this call and the enclosing `run_code` call, then hold a temporary reservation on `ctx.usage.tool_calls` until the call settles, so gathered calls cannot all pass the check before any is counted.
3. Call `tool_manager.handle_call(call_part, wrap_validation_errors=False)`. This is the same entry point core uses: it validates arguments, runs capability hooks, applies approval and deferral rules and calls the tool through the wrapped toolset. `wrap_validation_errors=False` means a validation error or `ModelRetry` from the nested tool propagates raw and "retry-budget state is left untouched" for that tool (`C/tool_manager.py:1396-1452`).
4. Classify the outcome: a `ToolReturn` is unwrapped into its `return_value`, `metadata` and `content`; a `ToolDenied` becomes `RuntimeError("Tool '<name>' call denied: <message>")`; an unresolved approval or deferral becomes a `UserError` (section 3.6); any other exception is kept as the error.

`RunCodeExecution.finish` then records the call and its outcome and either raises the error (the executor passes it into the sandbox as `{'exception': exc}`, so it is raised at the `await` site and the snippet can catch it) or returns the value converted to the JSON shape the stub declares (`H/code_mode/_toolset.py:725-735`).

What the snippet receives (page: "Sandbox restrictions"; `H/code_mode/_toolset.py:459-500`): the value in the JSON shape of the tool's schema. `Decimal`, `UUID` and `datetime` arrive as strings; mapping keys are stringified, so `{1: 'a'}` arrives as `{'1': 'a'}`; two keys that render to the same JSON key raise `UserError`. `bytes` and `bytearray` cross unchanged. When a nested tool returns a `ToolReturn`, the snippet gets only `return_value`; its `content` (a screenshot, for example) "never enters the sandbox: it is sent to the model alongside the `run_code` result, in call order" (page: "Return values").

### 3.6 Approval-requiring and deferred tools inside `run_code`

Such tools are sandboxed like any other tool (page: "Sandbox restrictions"). `handle_call` reads the tool's deferral kind before executing it, so a `requires_approval=True` tool does not simply run (`C/tool_manager.py:1470-1485`). What happens next depends on the agent:

- With a `HandleDeferredToolCalls` capability (or equivalent) on the agent, the handler resolves the call inline. An approval lets the tool run. A denial returns `ToolDenied`, which the snippet sees as a `RuntimeError`, and the history records the nested return with `outcome='denied'`.
- Without one, `ApprovalRequired` or `CallDeferred` propagates out of `handle_call`. `run_nested_call` converts it to `UserError("Tool '<name>' raised <ExceptionName> inside code mode, but no HandleDeferredToolCalls capability resolved it. ...")`. The comment states why: "The sandbox can't round-trip to the caller, so the error propagates through Monty -> MontyRuntimeError -> ModelRetry." The model gets a retry; the run does not end with deferred tool requests.

So inside `run_code` there is no way to end the run and resume the same snippet later with an approval result. The call is either resolved while the snippet waits, or it fails.

### 3.7 Return values

The last expression of the snippet is the return value; the model does not need to `print()`. An assignment on the last line returns nothing (page: "Return values"; `H/code_mode/_toolset.py:737-757`).

| Scenario | `run_code` returns |
|----------|--------------------|
| Non-`None` final expression, nothing printed | the value |
| Final assignment or `None`, nothing printed | `{}` |
| Printed output, no final expression or `None` | `{'output': '<printed text>'}` |
| Printed output and a plain non-`None` final expression | `{'output': '<printed text>', 'result': <value>}` |
| Multimodal final expression, nothing printed | the content, natively |
| Printed output and a multimodal final expression | a list: the printed text, then the content |

The tool return also carries history-only metadata that the model never sees: `{'code_mode': True, 'tool_calls': {...}, 'tool_returns': {...}}`, keyed by nested call id, plus a `speculation` entry when speculation is on.

### 3.8 Concurrency

Sandboxed tools are `async def`, so the snippet can start several and await them together with `asyncio.gather`. Whether they overlap follows the run's parallel execution mode (page: "Concurrent tool calls"):

- `'parallel'` (default) and `'parallel_ordered_events'` run gathered calls concurrently. The latter is DBOS's default.
- `'sequential'` runs gathered calls one at a time in the order the code started them.
- A tool marked `sequential=True` is rendered as a plain `def` and runs alone in every mode.

In the sandbox, `asyncio.gather` accepts positional awaitables only; keyword arguments (including `return_exceptions`) are rejected, and no other task-creation or wait API exists. `run_code` itself is declared `sequential=True`, so two `run_code` calls from one model response do not run at the same time (`H/code_mode/_toolset.py:1133`).

### 3.9 REPL state and `repl_store`

State persists between `run_code` calls within one agent run. Each agent run starts its own REPL. A `repl_store` (any object with async `load(ctx)` and `save(ctx, state)`) carries the REPL across runs: the state is loaded before the run's first snippet and saved when the run ends. The state is Monty's opaque dump behind a header recording the resolved resource limits; a state saved under other limits, or one Monty cannot load, is dropped with a warning and the run starts fresh. Monty does not authenticate a dump, so a store must return only states the application saved. A store error never fails the run. A restored REPL skips the first-snippet type check. The store "is not used under durable execution, where its I/O would run in replayed workflow code" (page: "Keep the REPL across runs"; `H/code_mode/_repl_store.py`).

### 3.10 Eager execution

`eager=True` starts running statements before the model has finished writing the `run_code` call. As the `code` argument streams, top-level statements that can no longer change are fed to the live REPL in order. A statement counts as closed "only once a later top-level statement starts on a later line" (`H/code_mode/_streaming.py:80-116`). The call still counts as one `run_code` call with one session, one nested-call budget and one combined result. When the full call arrives, `call_tool` checks that the code already run is a prefix of the final code, waits for the fed statements, and runs the remaining tail; if the prefix no longer matches it resets the session and raises a model retry (`H/code_mode/_eager.py:338-378`).

Documented limits (page: "Eager execution"): side effects of code already run cannot be undone; hooks around `run_code` run only after the model finished the call, so they cannot approve or change lines already run; `max_duration_seconds` and the sleep allowance count per fragment; eager execution applies only when `run_code` is the first tool call in a model response; nested tools must cooperate with asyncio cancellation (the wait is 5 seconds); and "Eager mode is disabled when using durable execution such as Temporal or DBOS."

### 3.11 Speculative execution

`speculate` starts side-effect-free tool calls while the `run_code` arguments are still streaming. Code Mode scans the partial code for calls to eligible tools "whose arguments are all keyword literals" and starts them; when the completed snippet reaches the same call it adopts the in-flight result. Eligibility is an explicit promise: a list of tool names, or `'declared'` to trust tools marked `metadata={'read_only': True}` and MCP tools with the `readOnlyHint` annotation. Idempotence does not count: "an idempotent delete still deletes" (page: "Speculative execution"; `H/code_mode/_speculation.py:1-21`, `225-243`).

Rules that matter: at most `min(32, max_tool_calls)` calls start early per `run_code` call and they do not reserve from `max_tool_calls`; lookahead stops at a known tool call that is not eligible; a launch is not used if a non-eligible tool call started or finished after it; `sequential` tools never speculate; nothing speculates in sequential mode; a speculated call counts toward `tool_calls_limit` from the moment it starts; enabling `speculate` puts runs in streaming mode; "the option is disabled under durable execution such as Temporal or DBOS." `CodeMode.speculation_stats` counts `launched`, `adopted` and `evicted` across runs, and capability events in the `code_mode` namespace report the lifecycle.

### 3.12 Remote workers

`monty_sandbox` moves code execution to a remote Monty server. It takes the root URL of the server (`http://`, `https://`, or the older `ws://` and `wss://`; no path, query or fragment, otherwise `UserError` when the run starts) or a sync or async function returning a `pydantic_monty.AsyncMontyRemote`, called once per run. The function form is how credentials are supplied (`metadata=lambda: {'authorization': 'Bearer ...'}`). Each worker is a gRPC stream. "Only code execution moves to the worker. Your tools, `mount` directories, `os_access`, and `print` output stay in your process." The server must run `pydantic-monty>=1.5.0`; earlier servers spoke WebSockets (page: "Remote workers").

### 3.13 Filesystem and OS access

By default sandboxed code has no access to the host's files, environment or clock. Two fields add controlled access, both fixed when the capability is built (page: "Filesystem and OS access"):

- `mount`: one `pydantic_monty.MountDir` or a list. A mount maps a host directory to a virtual path for sandboxed `pathlib` code. The default mode is `'overlay'`: reads come from the host, writes are visible within the current `run_code` call and are discarded before the next one, and never reach the host. `'read-write'` persists writes to the host; `'read-only'` forbids writes.
- `os_access`: an `AbstractOS` such as `OSAccess(environ={...})`, or a handler called with keyword arguments (`name`, `args`, `kwargs`, plus future keywords). The handler's return value becomes the result; returning `NOT_HANDLED` makes the call unsupported, which raises in the sandbox. The handler may be `async`. The older positional form `(name, args, kwargs)` is deprecated. The built-in `OSAccess` "uses an isolated in-memory filesystem and environment but the host clock by default".

### 3.14 Sandbox restrictions the model is told about

From the `run_code` description and the page ("Sandbox restrictions"):

- No third-party imports. Importable standard-library modules: `sys`, `typing`, `asyncio`, `math`, `json`, `re`, `unicodedata`, `datetime`, `time`, `random`, `os`, `pathlib`, `collections`, `itertools`, `functools`, `dataclasses`, `copy`, `base64`, `binascii`, `bisect`, `csv`, `decimal`, `fnmatch`, `hashlib`, `os.path`, `tomllib`, `annotationlib`. Each must be imported before use.
- No clock or randomness by default: `datetime.datetime.now()`, `datetime.date.today()`, `time.time()` and unseeded `random` fail unless an `os_access` handler implements them.
- `time.sleep` and `asyncio.sleep` really wait, up to the sleep allowance.
- No `import *`.
- Filesystem I/O needs a `mount` or an `os_access` handler; `os.getenv` and `os.environ` need an `os_access` handler.

## 4. API and configuration surface

### 4.1 `CodeMode` fields

From `H/code_mode/_capability.py:102-229` and the API reference on S1. `tools` and `max_retries` are positional; the rest are keyword-only.

```python
CodeMode(
    tools='all',              # ToolSelector: 'all', Sequence[str], predicate, or metadata dict
    max_retries=3,            # retries for run_code; syntax errors count
    max_tool_calls=100,       # nested tool calls per run_code invocation
    os_access=None,           # CodeModeOS | None: AbstractOS, OsHandler, or deprecated positional callback
    mount=None,               # CodeModeMount | None: MountDir or list[MountDir]
    resource_limits=None,     # CodeModeResourceLimits | 'unlimited' | None
    eager=False,
    speculate=None,           # Sequence[str] | 'declared' | None
    monty_sandbox=None,       # str URL, or callable returning AsyncMontyRemote (sync or async)
    monty_sandbox_url=None,   # deprecated: pass monty_sandbox=url
    repl_store=None,          # CodeModeReplStore | None
    dynamic_catalog=True,
)
# inherited from AbstractCapability and accepted by from_spec:
#   id=None, description=None, defer_loading=False
```

`speculation_stats` is a read-only `SpeculationStats` (`launched`, `adopted`, `evicted`). Passing a plain string other than `'declared'` to `speculate` raises `UserError`.

`from_spec`, used for YAML or JSON agent specs, exposes only the spec-expressible fields:

```python
@classmethod
def from_spec(
    cls,
    tools: Literal['all'] | Sequence[str] | dict[str, Any] = 'all',
    max_retries: int = 3,
    *,
    id: str | None = None,
    description: str | None = None,
    defer_loading: bool = False,
    max_tool_calls: int = 100,
    resource_limits: CodeModeResourceLimits | Literal['unlimited'] | None = None,
    eager: bool = False,
    speculate: Sequence[str] | Literal['declared'] | None = None,
    monty_sandbox: str | None = None,
    dynamic_catalog: bool = True,
    **kwargs: Any,
) -> CodeMode[Any]
```

### 4.2 `CodeModeResourceLimits`

A `TypedDict` with `total=False` (`H/code_mode/_toolset.py:331-386`):

| Key | Type | Default when omitted | Notes |
|-----|------|----------------------|-------|
| `max_duration_seconds` | float | 30 | Sandbox execution time per snippet. Passed to Monty as `max_feed_duration_secs`. Also the total sleep allowance per snippet. |
| `max_duration_secs` | float | none | Deprecated name of the key above; still accepted with a warning. Passing both raises `TypeError`. |
| `max_memory` | int | 268,435,456 bytes (256 MiB) | Sandbox heap. |
| `max_suspensions` | int | 1000 | Cumulative host interactions per session. Must be an integer from 1 to below 2**64; cannot be disabled. |

`resource_limits=None` applies the defaults. A partial dict overrides only the keys it names. `'unlimited'` removes the time and memory caps but leaves Monty's suspension default. Unknown keys raise `UserError`. Values are checked when the run enters the toolset, not at the first `run_code` call.

### 4.3 The `run_code` tool

From `H/code_mode/_parts.py:18-38` and `H/code_mode/_toolset.py:1126-1142`. The field types are condensed here: in the source each is `Annotated[..., Field(description=...)]`, and the comment shows that description, which is what the model sees.

```python
class RunCodeArgs(TypedDict):
    code: str                      # "The Python code to execute in the sandbox."
    restart: NotRequired[bool]     # "Set to true to reset REPL state. When false (default), state is preserved between calls."

RUN_CODE_TOOL_NAME = 'run_code'
RUN_CODE_KIND = ToolKind('code_mode', RUN_CODE_TOOL_NAME, args=RunCodeArgs)   # tool kind 'code_mode.run_code'
```

The tool definition has `sequential=True`, metadata `{'code_arg_name': 'code', 'code_arg_language': 'python'}`, `max_retries` from the capability, and `capability_id` set to the owning `CodeMode`. A wrapped tool named `run_code` raises `UserError`.

### 4.4 `CodeModeReplStore`

```python
class CodeModeReplStore(Protocol[AgentDepsT]):
    async def load(self, ctx: RunContext[AgentDepsT]) -> bytes | None: ...
    async def save(self, ctx: RunContext[AgentDepsT], state: bytes | None) -> None: ...
```

`save(ctx, None)` means `restart: true` cleared the REPL and nothing replaced it. The save is shielded from cancellation and bounded by `SAVE_TIMEOUT_SECONDS = 10.0`. The saved state begins with the header `b'pydantic-ai-harness code-mode REPL v1\n'`, then a JSON line with the resolved limits, then the Monty dump.

### 4.5 Other exported names

`pydantic_ai_harness.code_mode` exports `CODE_MODE_EVENTS` (`'code_mode'`), `FOLD_INTO_CODE_MODE` (`'fold_into_code_mode'`), `CodeMode`, `CodeModeMount`, `CodeModeOS`, `CodeModeOSCallback`, `CodeModeReplStore`, `CodeModeResourceLimits`, `CodeModeReturnSchemaWarning`, `CodeModeToolset`, `RunCodeArgs`, `RunCodeCallPart`, `SpeculationStats`, and six speculation event classes: `SpeculativeCodeUpdateEvent`, `SpeculativeCallLaunchedEvent`, `SpeculativeCallSettledEvent`, `SpeculativeCallClaimedEvent`, `SpeculativeCallMissedEvent`, `SpeculativeCallEvictedEvent`. `CodeModeToolset` built by hand defaults `dynamic_catalog` to `False`, unlike the capability.

Install: `pip install "pydantic-ai-harness[codemode]"` (alias `code-mode`), which requires `pydantic-monty>=1.5.0,<2` (`src/pydantic_ai_harness/pyproject.toml:51-53`). For Temporal: `pydantic-ai-harness[codemode,temporal]`.

### 4.6 `IPythonCodeMode` fields, for comparison

`tools='all'`, `max_retries=3`, `max_tool_calls=100`, `timeout=60.0` (seconds per `run_code` call, `None` for no limit), `kernel=None` (an `IPythonKernel` to keep across runs). `IPythonKernel` takes `kernel_name='python3'`, `cwd=None`, `startup_timeout=60.0` (S2).

## 5. Limits, budgets and failure handling

### 5.1 Limits

| Limit | Default | Scope | What happens at the limit |
|-------|---------|-------|---------------------------|
| `max_retries` | 3 | the `run_code` tool, by Pydantic AI's tool retry rules | once exhausted the run fails with `UnexpectedModelBehavior` (core behaviour, `C/tool_manager.py:1325`) |
| `max_tool_calls` | 100 | nested calls per `run_code` call | the call "fails at its call site inside the sandbox" with `RuntimeError`; a snippet that catches it keeps the completed results; uncaught, the model gets a retry listing the calls that started |
| `max_duration_seconds` | 30 s | sandbox execution time per snippet; time awaiting tools and sleeping is excluded | the snippet is stopped, the session is reset, the model gets a retry. Disabled inside a Temporal workflow. With `eager`, counted per fragment |
| Sleep allowance | equal to `max_duration_seconds` | total sleep per snippet | a sleep that would exceed it raises `TimeoutError` in the sandbox without waiting; the session is kept |
| `max_memory` | 256 MiB | sandbox heap | session reset and a retry asking for less memory |
| `max_suspensions` | 1000 | cumulative per session, shared by consecutive snippets; counts external calls, OS callbacks, name lookups and future resolutions, "so it is not a tool-call count" | further host interactions fail; the retry carries the started-call summary and restart guidance; "There is no automatic restart or replay for exhaustion" |
| Printed output | 10 MiB | the print collector of one `run_code` call | model retry; the session is reset |
| Speculative launches | min(32, `max_tool_calls`) | per `run_code` call | later eligible calls run normally |
| Run-level `tool_calls_limit` | set by the caller's `UsageLimits` | the whole agent run; nested calls and the `run_code` call both count | the run ends with `UsageLimitExceeded`; "Catching the error inside the snippet does not prevent this" |
| Retry-summary size | 120 characters per value, 5 items per container, 2000 characters in total | the started-call list appended to a retry | truncated, with exact totals kept (`H/code_mode/_toolset.py:97-99`) |
| Eager stream scan | 262,144 characters per parse, 1,048,576 characters cumulative | one streamed `run_code` call | the call falls back to normal dispatch (`H/code_mode/_streaming.py:24-34`) |
| Cancellation wait | 5 s | eager and speculative work at run end or invalidation | work that does not release is abandoned |
| Remote checkout refusal | one retry after 0.1 s | refusals with cause `caller_cap`, `capacity` or `worker_unavailable` | a second refusal propagates to the caller (`H/_monty_exec.py:195-204`) |
| Remote per-turn transport deadline | URL form: `max_duration_seconds` + 10 s, and none when that limit is off, which includes every run inside a Temporal workflow (`H/_monty_exec.py:120-133`, `179`); factory form: the pool's `request_timeout`, 10 s by default | one worker round trip | the worker connection is killed; the model gets a time-limit retry |

How results are bounded. The model-visible result of `run_code` is whatever the snippet returns and prints. Printed text is capped at 10 MiB. The returned value has no size cap applied by Code Mode itself; it is limited only by what the sandbox can hold (`max_memory`) and by Monty's message limits (INFERRED from `H/code_mode/_toolset.py:737-757` and `1317-1323`, which contain no truncation). The design relies on the model returning "only what matters"; separate harness capabilities for tool output limits exist (`H/tool_output_limits/`, `H/overflowing_tool_output/`) but are not part of Code Mode. What Code Mode does bound explicitly is the diagnostic text it adds to a retry: arguments and results of started calls are previewed at the sizes in the table and the comment says the summary "exists to identify calls, not to redeliver their payloads".

### 5.2 Errors that go back to the model as retries

From `_feed_failure_retry` (`H/code_mode/_toolset.py:1362-1418`) and page: "Error handling".

| Failure | Session | Model retry text begins |
|---------|---------|-------------------------|
| Syntax error (`MontySyntaxError`) | reset only if the REPL was fresh | "Syntax error in code:" plus the diagnostic |
| Type error (`MontyTypingError`), first snippet only | reset | "Type error in code:" plus the diagnostic |
| Ordinary runtime error (`MontyRuntimeError`), including an uncaught error from a nested tool | kept | "Runtime error:" plus captured stdout and the traceback |
| Execution time limit | reset | "The code ran longer than its time limit and was stopped, so the session was reset." plus the started calls |
| Memory limit, or a `MemoryError` with no traceback | reset | "The sandbox ran out of memory and the session was reset." |
| Suspension limit | kept | the runtime error plus guidance to pass `restart: true` and not replay completed side effects |
| Worker crash or lost connection (`MontyCrashedError`, `MontyDisconnectError`) | reset | a crash, time-limit, session-ended or connection-lost message plus the started calls |
| Any other exception from the execution loop | reset | "Code execution failed and the session was reset." With a remote sandbox only the exception type is shown, "because they can contain pool URLs or credentials" |
| Rust-side sandbox panic (pyo3 `PanicException`) | reset | "The code aborted inside the sandbox and the session was reset." |

After a reset the model must re-run imports and recreate state. The retry lists the nested calls that already started, including those that did not finish, "since a tool can apply a change before it stops". Code Mode does not replay them.

### 5.3 Errors that propagate to the caller instead

- Pool creation, connection and session checkout errors: missing or invalid credentials, an invalid URL, exceptions from a pool factory, a checkout refused twice. "They are not sent to the model as `ModelRetry`: the model cannot fix sandbox configuration." In the code these calls sit outside the `try` block that builds retries (`H/code_mode/_toolset.py:1277-1285`).
- `UsageLimitExceeded` from the run's `tool_calls_limit`, even when the snippet caught it at the call site.
- Cancellation and other `BaseException` values that are not sandbox panics; the suspended session is dropped and the exception re-raised unchanged.
- Configuration errors (`UserError`) for unknown limit keys, bad limit values, `max_tool_calls < 1`, a `monty_sandbox` URL with a path, a tool named like a builtin or like `run_code`.

## 6. Durable execution / Temporal specifics

The comparison table on the IPython Code Mode page lists Code Mode's durable execution support as "Temporal, DBOS, Prefect" (S2). The Code Mode page has a dedicated section for Temporal only; it mentions DBOS in passing and never names Prefect. The skill reference bundled with the package names the same three engines when it says eager execution and speculation are inactive under durable execution (`H/.agents/skills/pydantic-ai-harness/references/CODE-MODE.md:224`). The repository holds Code Mode tests for all three (`T/code_mode/test_temporal.py`, `test_dbos.py`, `test_prefect.py`); only the Temporal one was read in full.

Under Temporal (page: "Temporal durability"; details and source evidence in the companion file):

- `run_code` "executes in workflow code and is re-executed during replay". Model requests and, by default, nested tool calls are Temporal activities. `asyncio.gather` in the snippet "can schedule nested tool activities concurrently".
- `PydanticAIPlugin` passes `pydantic_monty` through Temporal's workflow sandbox so Monty can run there.
- The REPL "is process-local state for one agent run, not durable storage. Replay reconstructs it by running the recorded snippets again against recorded activity results."
- `max_duration_seconds` is disabled, "including an explicit override", because "measuring elapsed time there could make replay choose a different path from the recorded workflow". The memory and suspension caps and the sleep allowance still apply.
- `eager`, `speculate` and `repl_store` are inactive.
- `mount` reads and writes, `os_access` callbacks and host-clock calls happen again on replay and are not recorded.
- "Temporal fails a workflow task that does not yield within 2 seconds, so move heavier computation into a tool."
- With a remote sandbox, "replay connects to the worker again, so it must be reachable whenever the workflow replays."
- Setup errors keep their types in workflow code. `UserError` fails the workflow. Other setup errors, such as `RuntimeError` and `MontyRefusedError`, fail the workflow task, which Temporal retries while the workflow stays running.

For engines that record nested calls as numbered steps, the page adds a warning: "When a tool or a capability hook such as `before_tool_execute` awaits before its step starts, concurrent calls can start their steps in a different order on recovery. Pass `parallel_execution_mode='sequential'` to the durability capability to run nested calls one at a time." In the source that constructor argument exists on `DBOSDurability` only; `TemporalDurability` has no such argument (`C/durable_exec/dbos/_durability.py:117`, `C/durable_exec/temporal/_durability.py:185-200`).

The two checks Code Mode uses are different.

- `in_durable_execution(ctx)`, defined in `H/code_mode/_toolset.py:199-204`, is true when any durability capability on the run reports `in_durable_context`. It switches off eager execution, speculation and the REPL store for every engine (`H/code_mode/_capability.py:374`, `H/code_mode/_toolset.py:1212`, `1257`).
- `in_temporal_workflow()`, defined in `H/_monty_exec.py:68-74`, asks `temporalio.workflow.in_workflow()` directly. It switches on the blocking portal and drops the execution timer (`H/code_mode/_toolset.py:1272-1276`).

The reason for the second, narrower check is given in a comment above a sibling helper, `in_durable_context(ctx)` in `H/_durable.py:57-59`, which other harness capabilities use as their engine-neutral check: "`_monty_exec.in_temporal_workflow` asks Temporal directly, because only Temporal replays `run_code`. This one covers every engine."

## 7. Implementation notes from the source code

- The execution loop is shared. `H/_monty_exec.py` serves both Code Mode (dispatch runs the agent's tools) and DynamicWorkflow (dispatch runs sub-agents). It uses Monty's snapshot API (`feed_start` and `resume`) rather than `feed_run` deliberately: "it exposes each suspension to this host-controlled loop, which owns sequential barriers, dispatch cancellation, and trace context."
- Nested call ids are deterministic: the parent `run_code` tool call id, two underscores, and a 1-based counter. Under eager execution one `RunCodeExecution` is shared across fragments so ids, budgets, output and metadata do not reset between statements.
- The nested `ToolManager` reuses the agent's root capability, so capability hooks on a tool (`before_tool_execute` and the rest) run for nested calls exactly as for direct calls. Hooks around `run_code` itself see only the outer call.
- Tracing context is carried across the sandbox boundary. Each snapshot exposes `trace_context()`; the executor attaches it around dispatch so nested tool spans appear under the `run_code` span. Tasks inherit the context; bare coroutines in sequential mode have it restored when awaited (`H/_monty_exec.py:446-454`, `482-491`).
- Clean-up on failure or cancellation: every pending task is cancelled and awaited inside a shielded scope, "so dispatched work (e.g. sub-agent runs mutating shared usage) has fully unwound before this returns" (`H/_monty_exec.py:316-339`).
- Sleeps are waited for by the host, not by Monty. `time.sleep` blocks the snippet; `asyncio.sleep` is deferred like a parallel call so gathered sleeps overlap. Both are charged to the sleep allowance (`H/_monty_exec.py:395-419`).
- Only the first snippet of a fresh REPL is type-checked. A syntax or type error on that snippet resets the session so the type stubs are rebuilt, because a later step may expose a different tool catalog.
- Limit detection depends on Monty's wording. `_SANDBOX_LIMIT_MARKERS` maps `'time limit exceeded'`, `'memory limit exceeded'` and `'suspension limit '` to the three limits; the comment notes "Monty offers no typed marker for either, so its phrasing is load-bearing here and nowhere else" (`H/code_mode/_toolset.py:106-116`). A time-limit stop is recognised by wording plus an empty traceback.
- A snippet result that has no JSON form (a type object, a builtin, a bare exception instance) is replaced by its `repr` so the tool return can be serialised. A non-finite float is replaced too, because it would otherwise serialise as `null` and hide the result (`H/code_mode/_toolset.py:1556-1575`).
- The history-only metadata holds every nested call and its full return value. The Temporal test reads it back from the serialised message history (`T/code_mode/test_temporal.py:349-364`), so nested results are stored in the message history even though the model is not shown them.
- `run_code` is recognisable without matching on its name: `isinstance(part, RunCodeCallPart)` works in hooks and history processors, and `part.code` gives the submitted code (page: "Recognizing `run_code` calls").

## 8. Maturity, versions, caveats

- Versions on 2026-11-12: pydantic-ai-harness 0.68.0, pydantic-ai-slim 2.68.0, both published that day. The harness version follows pydantic-ai's minor and patch numbers ("pydantic-ai 2.51.0 ships pydantic-ai-harness 0.51.0", `src/pydantic_ai_harness/pyproject.toml:8-11`). It requires Python 3.11 or newer and its classifier is "Development Status :: 3 - Alpha".
- Version policy (S3): the harness "uses 0.x versioning to signal API stability, not maturity: the capabilities are tested end-to-end and meant for production use, but their APIs may change between minor releases". Every doc page for a harness capability carries the same notice.
- Deprecated names still accepted with warnings: `monty_sandbox_url` (use `monty_sandbox`), the `max_duration_secs` limit key (use `max_duration_seconds`), the positional `os_access(name, args, kwargs)` callback, and the event fields `closed_statements` and `sandbox_function`.
- `dynamic_catalog` changed its default: "Earlier releases defaulted to `False`" (`H/code_mode/_capability.py:226`).
- Remote transport changed in Monty 1.5.0 from WebSockets to gRPC; `ws://` URLs are read as `http://`.
- The remote-sandbox Temporal test is skipped in the repository: "Hangs intermittently in CI" with a link to pydantic-ai issue 8824 (`T/code_mode/test_temporal.py:525`).
- Tool definitions, including names, descriptions and schemas supplied by an MCP server, are rendered into the instructions. The page's position: "Tool definitions are trusted operator configuration ... Connect only MCP servers you trust."
- Eager execution and speculation trade safety checks for latency: work can start before hooks, approval or guardrails on `run_code` have seen the completed call.

## 9. What could not be confirmed

- No code was run. Behaviour is taken from documentation, source and test assertions only.
- The licence of the documentation site text is not stated on the pages beyond the copyright footer.
- Which exception type the sandbox sees when a host exception class is not one Monty implements (for example the `UserError` built for an unresolved approval, or Temporal's `ActivityError`). Monty's docs say only that the type name carries over for implemented types. NOT VERIFIED.
- What exactly ends the run when `max_retries` is exhausted is taken from a core docstring (`UnexpectedModelBehavior`), not from a Code Mode test.
- How a `HandleDeferredToolCalls` handler behaves for a nested call under durable execution (whether its wait is durable) was not traced. NOT VERIFIED.
- `H/code_mode/_speculation.py` was read in full only to line 250; the claim, eviction and event code beyond that is summarised from the documentation page.
- Core's `FunctionSignature` renderer was not read; the signature format is taken from test assertions.
- The articles linked under "Further reading" on the page were not read.
