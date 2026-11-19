# Code execution and sandboxes: code map

> **Status: Historical snapshot.** A map of the code at commit 908a8ce6 (2026-11-12), written for the [dynamic workflow research](../README.md) and not updated since, so a line number here may be stale. The maintained documents for this subject are [RLM Service](../../../rlm_service.md), [Pi coding agent](../../../pi_coding_agent.md) and the code executor cards under [node-logic-flows](../../../node-logic-flows/README.md).

Scope: every place OpenCompany runs model-written or user-written code, or lets code or an external harness call models, tools or agents: the four code executor nodes, the RLM agent's REPL, the autonomous ("Code Mode") agent, the Pi coding agent on Temporal with its inline delegation, the CLI agents' MCP bridge and the JS/TS sidecar.

Date: 2026-11-12, as of commit 908a8ce6. The two later commits on main at the time of reading (c6655c64, 90fc876c) touch none of the files cited here.

Method: read-only (Read, Grep, Glob); nothing was run. Facts about pydantic-monty 0.0.18 and rlms 0.1.3 that go beyond repository code come from the package files installed in the server virtualenv. They are named in prose and not linked, because they are not repository files.

## 1. Summary

1. Four executor nodes exist (`montyExecutor`, `pythonExecutor`, `javascriptExecutor`, `typescriptExecutor`). Each runs one script inside one node call (one Temporal activity) and returns `{output, console_output}`. None lets the script call an OpenCompany tool, model or agent.
2. Monty is already a pinned dependency (`pydantic-monty==0.0.18`) and a node. The node uses only the synchronous `Monty.run(...)` in a worker thread. The one host function it exposes is `http_get`, plus an optional `/workspace` mount. The pinned package also ships a suspend/resume API (`start`, `resume`, `dump`, `load_snapshot`); nothing in the repository calls it.
3. The only place where model-written code calls models and tools today is the RLM agent: CPython `exec` in the backend or worker process, tools bridged as blocking Python functions with a 60 s wait, models called by the library's own clients, all inside one node call (under Temporal, one activity with a single attempt). Nothing inside the run is durable.
4. The autonomous agent's "Code Mode" is a prompt (a skill) on the standard agent loop. It has no sandbox and no tools-as-functions mechanism.
5. The Pi coding agent is the one component where an outside program (the Pi SDK in a bun process) asks for model and tool work and Temporal performs it and feeds the result back deterministically: operation ids derived from turn and call order, receipts held in workflow state, a driver activity that can die and be re-run without repeating a recorded effect.
6. `pi_inline_delegation` covers the other direction: a runtime that is not a Temporal workflow (RLM code, a CLI agent over MCP, an in-process loop) asks for a Coding Agent run. It is start-only: an immutable admission row plus a detached workflow with a deterministic id, an immediate acknowledgement, and the result fetched later by polling `check_delegated_tasks`.
7. A script-driven tool would inherit: executor `timeout` at most 600 s, tool activity `start_to_close` 10 min (24 h as a graph node), heartbeat 2 min, result payload under 2 MiB, 100,000 characters of tool result into the transcript, delegation depth 2 and 3 concurrent sub-agents (section 7).

## 2. Files and responsibilities

| File | Lines | Owns |
|---|---|---|
| [server/nodes/code/__init__.py](../../../../server/nodes/code/__init__.py) | 18 | Registers the sidecar supervisor for lifespan shutdown |
| [server/nodes/code/_base.py](../../../../server/nodes/code/_base.py) | 52 | Shared `CodeExecutorParams`, `CodeExecutorOutput`, `CodeExecutorBase` (queue, tool flag, handles) |
| [server/nodes/code/monty_executor/__init__.py](../../../../server/nodes/code/monty_executor/__init__.py) | 241 | `montyExecutor`: Monty run, capability menu, `http_get` host function, error mapping |
| [server/nodes/code/python_executor/__init__.py](../../../../server/nodes/code/python_executor/__init__.py) | 142 | `pythonExecutor`: CPython `exec` with a replaced `__builtins__` |
| [server/nodes/code/javascript_executor/__init__.py](../../../../server/nodes/code/javascript_executor/__init__.py) | 58 | `javascriptExecutor`: posts to the sidecar |
| [server/nodes/code/typescript_executor/__init__.py](../../../../server/nodes/code/typescript_executor/__init__.py) | 53 | `typescriptExecutor`: same, `language="typescript"` |
| [server/nodes/code/_nodejs.py](../../../../server/nodes/code/_nodejs.py) | 51 | Client singleton, on-demand sidecar start |
| [server/nodes/code/_client.py](../../../../server/nodes/code/_client.py) | 101 | aiohttp client, token header, 401 handling |
| [server/nodes/code/_runtime.py](../../../../server/nodes/code/_runtime.py) | 170 | Sidecar supervisor: bun spawn, per-process token, port probe |
| [server/nodes/code/_claude_code.py](../../../../server/nodes/code/_claude_code.py) | 135 | Legacy Claude Code shim. No importer found in `server/` (grep for its two public names returns only this file) |
| [server/nodejs/src/index.ts](../../../../server/nodejs/src/index.ts) | 240 | The sidecar: `/execute`, `/health`, `/packages`, token check |
| [server/nodes/agent/rlm_agent/__init__.py](../../../../server/nodes/agent/rlm_agent/__init__.py) | 116 | `rlm_agent` node: edge collection, prompt preparation, call into `RLMService` |
| [server/services/rlm/service.py](../../../../server/services/rlm/service.py) | 335 | `RLMService.execute`: provider and key resolution, tool bridge, the `RLM(...).completion(...)` call, result |
| [server/services/rlm/adapters.py](../../../../server/services/rlm/adapters.py) | 490 | `BackendAdapter`, `ChatModelExtractor`, `ToolBridgeAdapter` (tools as blocking functions) |
| [server/services/rlm/constants.py](../../../../server/services/rlm/constants.py) | 24 | Provider map, default limits |
| [server/nodes/agent/autonomous_agent/__init__.py](../../../../server/nodes/agent/autonomous_agent/__init__.py) | 11 | `autonomous_agent`: name and description only |
| [server/nodes/agent/_specialized.py](../../../../server/nodes/agent/_specialized.py) | 144 | `SpecializedAgentBase` and its params (used by autonomous and coding agents, params also by RLM) |
| [server/nodes/agent/coding_agent/__init__.py](../../../../server/nodes/agent/coding_agent/__init__.py) | 22 | `coding_agent` node: adds `tool_mode` |
| [server/services/coding_agent_runtime.py](../../../../server/services/coding_agent_runtime.py) | 101 | Pi harness flag: `uses_pi`, `agent_workflow_name`, freezing helpers, admission switch |
| [server/services/coding_workspace_tools.py](../../../../server/services/coding_workspace_tools.py) | 144 | Automatic workspace tools (`fileRead`, `fileModify`, `fsSearch`, `shell`) for `tool_mode="workspace"` |
| [server/services/temporal/pi_workflow.py](../../../../server/services/temporal/pi_workflow.py) | 395 | `PiCodingAgentWorkflow`: coordinator over the inherited `AgentWorkflow` loop |
| [server/services/temporal/pi_protocol.py](../../../../server/services/temporal/pi_protocol.py) | 129 | Canonical JSON, digests, `OperationLedger`, limits, queue name |
| [server/services/temporal/pi_activities.py](../../../../server/services/temporal/pi_activities.py) | 353 | Driver activity (bun host subprocess), child-control activity, blob activity |
| [server/services/temporal/pi_control.py](../../../../server/services/temporal/pi_control.py) | 87 | Stop/Resume accounting for Pi workflows and their children |
| [server/services/temporal/pi_inline_delegation.py](../../../../server/services/temporal/pi_inline_delegation.py) | 413 | Start-only durable Coding delegation from inline runtimes; `PiInlineDelegationWorkflow` |
| [server/services/pi_runtime.py](../../../../server/services/pi_runtime.py) | 47 | Locates the Pi host bundle or source |
| [server/services/pi_transport.py](../../../../server/services/pi_transport.py) | 80 | Per-attempt transport files for frames above the inline limit |
| [server/services/pi_blobs.py](../../../../server/services/pi_blobs.py) | 44 | Durable content-addressed blobs (`pi_runtime_blobs`) |
| [server/models/pi_checkpoint.py](../../../../server/models/pi_checkpoint.py) | 20 | `PiRuntimeBlob` table |
| [server/pi/src/index.ts](../../../../server/pi/src/index.ts) | 136 | Pi host entry point: frame loop, pending operations, park and abort |
| [server/pi/src/driver.ts](../../../../server/pi/src/driver.ts) | 282 | `TurnDriver`: one SDK round with a stub model provider and stub tools |
| [server/pi/src/types.ts](../../../../server/pi/src/types.ts) | 157 | Wire types and limits |
| [server/pi/src/protocol.ts](../../../../server/pi/src/protocol.ts) | 71 | Canonical JSON, digest, frame decoder |
| [server/pi/src/transport.ts](../../../../server/pi/src/transport.ts) | 87 | TypeScript side of the transport files |
| [server/pi/src/codec.ts](../../../../server/pi/src/codec.ts) | not counted | `MessageWire` to Pi message conversion (first 40 lines read) |
| [server/services/cli_agent/mcp_server.py](../../../../server/services/cli_agent/mcp_server.py) | 658 | FastMCP app, per-batch bearer tokens, infrastructure tools |
| [server/services/cli_agent/workflow_tools.py](../../../../server/services/cli_agent/workflow_tools.py) | 464 | One MCP tool per connected workflow tool; handler calls `execute_tool` |
| [server/services/handlers/tools.py](../../../../server/services/handlers/tools.py) | 1100+ | `execute_tool` and `_dispatch_tool` (in-process tool calls), delegation polling (lines 1-440 and 960-1100 read) |

Other parts of `server/pi/` (layout only): [server/pi/package.json](../../../../server/pi/package.json) pins `@earendil-works/pi-coding-agent` 1.1.0; [server/pi/scripts/build.ts](../../../../server/pi/scripts/build.ts) writes the bundle to `dist/`; `tests/` holds bun tests; [server/pi/README.md](../../../../server/pi/README.md) describes the host.

## 3. The code executor nodes

### 3.1 montyExecutor

**Version and reason.** `pydantic-monty==0.0.18` ([server/pyproject.toml:239](../../../../server/pyproject.toml#L239), locked at [server/uv.lock:2546](../../../../server/uv.lock#L2546)). The comment above the pin gives the reason: a deny-by-default Python subset written in Rust with enforced time and memory limits, for running AI-generated code without a container; prebuilt wheels for cp310 to cp314 on Windows, macOS and Linux, so an ordinary dependency; "Pinned because pre-1.0 (alpha; API still moving fast)" ([server/pyproject.toml:231-238](../../../../server/pyproject.toml#L231-L238)). The plugin docstring lists the API surface it was "Verified against" for that version ([server/nodes/code/monty_executor/__init__.py:17-28](../../../../server/nodes/code/monty_executor/__init__.py#L17-L28)). Only this file imports `pydantic_monty` in production code (grep over the repository; the test file injects a fake module and probes for the real one).

**Where it runs.** In the Python process that runs the node body. The package is imported lazily inside the operation, so a missing wheel fails the call and not server start ([server/nodes/code/monty_executor/__init__.py:176-185](../../../../server/nodes/code/monty_executor/__init__.py#L176-L185)). `run()` is synchronous and is moved to a thread with `asyncio.to_thread` ([server/nodes/code/monty_executor/__init__.py:206-213](../../../../server/nodes/code/monty_executor/__init__.py#L206-L213)).

**The core of the execution function** ([server/nodes/code/monty_executor/__init__.py:193-213](../../../../server/nodes/code/monty_executor/__init__.py#L193-L213); the comments and the enclosing `try:` line are left out, the statements are verbatim):

```python
limits = monty.ResourceLimits(
    max_duration_secs=params.timeout,
    max_memory=params.max_memory_mb * 1024 * 1024,
)
m = monty.Monty(params.code, inputs=["input_data"])
result = await asyncio.to_thread(
    m.run,
    inputs={"input_data": input_data},
    limits=limits,
    external_functions=ext_funcs,
    print_callback=collector,
    mount=mount,
)
```

**Exact API use.**
- Constructor: `Monty(code, inputs=["input_data"])`. No `script_name`, `type_check`, `type_check_stubs` or `dataclass_registry`. Type checking is therefore off (the installed stub's signature default is `type_check=False`).
- Run: `run(inputs=, limits=, external_functions=, print_callback=, mount=)`. The `os` argument is not passed. The docstring says `run_async` has no `mount` parameter, which is why the sync `run` is used ([server/nodes/code/monty_executor/__init__.py:19-21](../../../../server/nodes/code/monty_executor/__init__.py#L19-L21)); the installed stub confirms it (`run_async` takes `os: AbstractOS` and no `mount`).
- Limits: only `max_duration_secs` and `max_memory` (bytes) are set. `max_allocations`, `max_recursion_depth` and `gc_interval` are not.
- Print capture: `collector = monty.CollectString()` passed as `print_callback`; its `.output` becomes `console_output` ([server/nodes/code/monty_executor/__init__.py:188](../../../../server/nodes/code/monty_executor/__init__.py#L188), [server/nodes/code/monty_executor/__init__.py:240](../../../../server/nodes/code/monty_executor/__init__.py#L240)).
- Result: the last expression. `result.output` if the value is a `MontyComplete`, else the value itself ([server/nodes/code/monty_executor/__init__.py:239](../../../../server/nodes/code/monty_executor/__init__.py#L239)).
- Not used anywhere: `run_async`, `start`, `resume`, `dump`, `load_snapshot`, `MontyRepl`, `type_check`.

**Host functions exposed today.** One, and only when the call asks for it. `_build_capabilities` turns the `capabilities` list into Monty grants ([server/nodes/code/monty_executor/__init__.py:91-121](../../../../server/nodes/code/monty_executor/__init__.py#L91-L121)):
- `http_get`: an `external_functions` entry `http_get(url) -> str`. It is a synchronous httpx client with a 15 s timeout that follows redirects; a request hook checks every hop with `services.net.is_public_url` and raises `ValueError` on a blocked address ([server/nodes/code/monty_executor/__init__.py:67-88](../../../../server/nodes/code/monty_executor/__init__.py#L67-L88)). The comment says Monty calls external functions inline on the worker thread ([server/nodes/code/monty_executor/__init__.py:70-72](../../../../server/nodes/code/monty_executor/__init__.py#L70-L72)).
- `workspace_read` / `workspace_write`: `MountDir("/workspace", ctx.workspace_dir, mode="read-only" or "read-write")`; an error if the run has no `workspace_dir` ([server/nodes/code/monty_executor/__init__.py:110-119](../../../../server/nodes/code/monty_executor/__init__.py#L110-L119)).
- The menu is the fixed tuple `CAPABILITY_MENU` ([server/nodes/code/monty_executor/__init__.py:45](../../../../server/nodes/code/monty_executor/__init__.py#L45)); an unknown name is refused ([server/nodes/code/monty_executor/__init__.py:98-102](../../../../server/nodes/code/monty_executor/__init__.py#L98-L102)). With an empty list both `external_functions` and `mount` are `None`.

**Language subset** (the plugin's own constants, surfaced to the model): supported are def, closures, lambda, if/for/while, try/except, list/dict/set comprehensions, f-strings, async def/await and `import` of math, json, re; not supported are class, yield/generators, with, match and `import` of random, collections, os ([server/nodes/code/monty_executor/__init__.py:48-52](../../../../server/nodes/code/monty_executor/__init__.py#L48-L52)).

**Inputs the script sees.** One variable, `input_data`, which is `ctx.raw["connected_outputs"]` or `{}` ([server/nodes/code/monty_executor/__init__.py:187](../../../../server/nodes/code/monty_executor/__init__.py#L187)). The executor fills that key for the listed node types ([server/services/node_executor.py:359-380](../../../../server/services/node_executor.py#L359-L380)) with the outputs of nodes whose edges target this node, keyed by the source node's type, not its id ([server/services/node_executor.py:512-515](../../../../server/services/node_executor.py#L512-L515)). There is no `workspace_dir` variable; files are reachable only through the mount.

**Parameters and limits.** `code` (required), `timeout` default 30, 1 to 600 s, `max_memory_mb` default 256, 16 to 2048, `capabilities` default empty ([server/nodes/code/monty_executor/__init__.py:124-136](../../../../server/nodes/code/monty_executor/__init__.py#L124-L136)). No output size limit of its own; the general node result and tool result limits apply (section 7).

**Error mapping.** Every Monty failure subclasses `MontyError` and becomes a `NodeUserError`, classified by message text: "does not yet support" or "notimplementederror" gets a subset hint that points to `python_code`; "time limit" or "timeouterror" becomes "Monty time limit exceeded (timeout=Ns)"; "memory limit" or "memoryerror" likewise; a `MontySyntaxError` becomes "SyntaxError: ..."; anything else passes its message through, including an exception raised by a host function ([server/nodes/code/monty_executor/__init__.py:214-237](../../../../server/nodes/code/monty_executor/__init__.py#L214-L237)). `NodeUserError` is returned as an error envelope and is non-retryable (section 3.5).

**As an AI tool.** `usable_as_tool = True` on the base ([server/nodes/code/_base.py:47](../../../../server/nodes/code/_base.py#L47)); tool name `sandboxed_python` and a description that states the subset, the `input_data` variable, "return a result as the LAST expression" and the capability names ([server/nodes/code/monty_executor/__init__.py:147-158](../../../../server/nodes/code/monty_executor/__init__.py#L147-L158)). The tool schema is the node's `Params` model, since a registered plugin without a `tool_input_model` exposes `Params` ([server/services/ai.py:2712-2716](../../../../server/services/ai.py#L2712-L2716)). Model arguments are merged over the saved node parameters ([server/services/plugin/base.py:923-933](../../../../server/services/plugin/base.py#L923-L933)); the executors declare no `server_controlled_fields`, so the model can set `timeout`, `max_memory_mb` and `capabilities` itself. Approvals classify each call by its capabilities: `workspace_read` is a read, `workspace_write` a write, `http_get` a send, and no capability is pure computation ([server/services/approvals/classes.py:370-371](../../../../server/services/approvals/classes.py#L370-L371)).

**Under Temporal.** Activity `node.montyExecutor.v1`, declared queue `code-exec` (pinned in [server/tests/temporal/fixtures/workflow_identities.json:1089](../../../../server/tests/temporal/fixtures/workflow_identities.json#L1089) and [server/tests/temporal/fixtures/workflow_identities.json:1248](../../../../server/tests/temporal/fixtures/workflow_identities.json#L1248)). Routing and timeouts are the shared ones in section 3.5. The Monty interpreter runs inside the activity's process, in the `to_thread` worker thread.

**What the tests assert** ([server/tests/nodes/test_monty_executor.py](../../../../server/tests/nodes/test_monty_executor.py)).
- Tier A uses a fake `pydantic_monty` module ([server/tests/nodes/test_monty_executor.py:37-113](../../../../server/tests/nodes/test_monty_executor.py#L37-L113)): empty code is refused; a missing package gives a `NodeUserError`; `inputs == ["input_data"]` and the run receives the connected outputs keyed by source type; default limits are `max_duration_secs == 30` and `max_memory == 256 * 1024 * 1024` ([server/tests/nodes/test_monty_executor.py:185-190](../../../../server/tests/nodes/test_monty_executor.py#L185-L190)); timeout, memory, unsupported-feature and syntax errors map to user errors; an unknown capability is refused; no capabilities means `external_functions is None` and `mount is None` ([server/tests/nodes/test_monty_executor.py:220-224](../../../../server/tests/nodes/test_monty_executor.py#L220-L224)); `http_get` plus `workspace_write` wire an `http_get` entry and a read-write mount at `/workspace` ([server/tests/nodes/test_monty_executor.py:226-239](../../../../server/tests/nodes/test_monty_executor.py#L226-L239)).
- `TestHttpGet`: a redirect from a public host to a cloud metadata address is refused before the second request ([server/tests/nodes/test_monty_executor.py:261-281](../../../../server/tests/nodes/test_monty_executor.py#L261-L281)).
- Tier B runs the real package when installed: `print('hi')` then `40 + 2` returns 42 with the print captured; `while True: pass` with `timeout=1` ends with a limit error; `class Foo` is refused with the `python_code` hint; `import math` works ([server/tests/nodes/test_monty_executor.py:289-315](../../../../server/tests/nodes/test_monty_executor.py#L289-L315)).
- No test covers an external function other than `http_get`, async code in the script, or `start`/`resume`.

### 3.2 pythonExecutor

- **Where it runs.** CPython `exec(params.code, namespace)` inside the node body ([server/nodes/code/python_executor/__init__.py:101](../../../../server/nodes/code/python_executor/__init__.py#L101)). The call is made directly in the `async def`, with no thread and no subprocess, so it runs on the event loop thread of the hosting process.
- **Isolation.** `__builtins__` is replaced by a dict ([server/nodes/code/python_executor/__init__.py:54-93](../../../../server/nodes/code/python_executor/__init__.py#L54-L93)): abs, all, any, bool, dict, enumerate, filter, float, int, len, list, map, max, min, print (captured), range, round, set, sorted, str, sum, tuple, type, zip, True, False, None, and the pre-injected names math, json, datetime (the module), timedelta, re, random, Counter, defaultdict. There is no `__import__`, no `open`, and no exception class. The Monty plugin's docstring calls this "a known-weak sandbox whose `timeout` is never actually enforced" ([server/nodes/code/monty_executor/__init__.py:3-8](../../../../server/nodes/code/monty_executor/__init__.py#L3-L8)), and the approvals table groups the code tools under "the code tools' sandboxes are weak" ([server/services/approvals/classes.py:162-167](../../../../server/services/approvals/classes.py#L162-L167)).
- **Inputs.** `input_data` (connected outputs), `workspace_dir` (a string, possibly empty) and `output` preset to `None` ([server/nodes/code/python_executor/__init__.py:94-99](../../../../server/nodes/code/python_executor/__init__.py#L94-L99)). The namespace is new on every call.
- **Result.** The `output` variable and the captured stdout ([server/nodes/code/python_executor/__init__.py:138-141](../../../../server/nodes/code/python_executor/__init__.py#L138-L141)).
- **Limits.** `timeout` is validated (default 30, 1 to 600, [server/nodes/code/_base.py:19](../../../../server/nodes/code/_base.py#L19)) and then never read by this file. No memory limit. INFERRED: a script that never returns blocks the hosting event loop, including the activity's heartbeat task.
- **Errors.** Any exception becomes a `NodeUserError`. An `import` statement fails with an `ImportError` about `__import__` and is rewritten into a message that lists the pre-injected names ([server/nodes/code/python_executor/__init__.py:113-122](../../../../server/nodes/code/python_executor/__init__.py#L113-L122)). Other errors become `<ErrorName> at line N: <message>` plus the stdout captured before the error ([server/nodes/code/python_executor/__init__.py:124-136](../../../../server/nodes/code/python_executor/__init__.py#L124-L136)).
- **As an AI tool.** Name `python_code` ([server/nodes/code/python_executor/__init__.py:23-24](../../../../server/nodes/code/python_executor/__init__.py#L23-L24)); schema is `CodeExecutorParams` (`code`, `timeout`). Approval class: send.
- **Tests.** Real `exec` path: reads `input_data` keyed by source type, sets `output`, captures print; empty code refused; an exception in user code is captured ([server/tests/nodes/test_code_fs_process.py:114-160](../../../../server/tests/nodes/test_code_fs_process.py#L114-L160)).

### 3.3 javascriptExecutor

- **Where it runs.** The node posts the script to the bun sidecar over HTTP and returns its answer ([server/nodes/code/javascript_executor/__init__.py:34-57](../../../../server/nodes/code/javascript_executor/__init__.py#L34-L57)). The sidecar evaluates it with `vm.runInContext` (section 4.6).
- **Inputs.** `input_data` is a copy of the connected outputs with an added `workspace_dir` key ([server/nodes/code/javascript_executor/__init__.py:31-32](../../../../server/nodes/code/javascript_executor/__init__.py#L31-L32)).
- **Result.** The sandbox's `output` global and the captured console text.
- **Limits.** `timeout` seconds times 1000 is sent as the script timeout in milliseconds ([server/nodes/code/javascript_executor/__init__.py:39](../../../../server/nodes/code/javascript_executor/__init__.py#L39)). The HTTP client has its own total timeout, fixed at 30 s by a default argument ([server/nodes/code/_nodejs.py:33](../../../../server/nodes/code/_nodejs.py#L33), [server/nodes/code/_client.py:25](../../../../server/nodes/code/_client.py#L25)). INFERRED: a script allowed more than about 30 s fails at the HTTP layer first, and that timeout error is not one of the two exception types the node rewrites.
- **Errors.** A connect failure or a `RuntimeError` (spawn failure, port held by another process, refused token) becomes "JavaScript executor is unavailable ... Fall back to python_executor" ([server/nodes/code/javascript_executor/__init__.py:42-50](../../../../server/nodes/code/javascript_executor/__init__.py#L42-L50)). A `success: false` answer becomes a `NodeUserError` with the sidecar's error text ([server/nodes/code/javascript_executor/__init__.py:52-53](../../../../server/nodes/code/javascript_executor/__init__.py#L52-L53)).
- **As an AI tool.** Name `javascript_code` ([server/nodes/code/javascript_executor/__init__.py:17-18](../../../../server/nodes/code/javascript_executor/__init__.py#L17-L18)). Approval class: send.
- **Tests.** With a mocked client: the timeout is forwarded in milliseconds, `workspace_dir` is inside `input_data`, a failure answer and a connection error become error envelopes ([server/tests/nodes/test_code_fs_process.py:168-240](../../../../server/tests/nodes/test_code_fs_process.py#L168-L240)). The token rules are pinned in [server/tests/nodes/test_js_executor_token.py](../../../../server/tests/nodes/test_js_executor_token.py).

### 3.4 typescriptExecutor

Identical to `javascriptExecutor` except `language="typescript"` and the tool name `typescript_code` ([server/nodes/code/typescript_executor/__init__.py:17-52](../../../../server/nodes/code/typescript_executor/__init__.py#L17-L52)). The sidecar strips types with `Bun.Transpiler` and then evaluates JavaScript ([server/nodejs/src/index.ts:105-115](../../../../server/nodejs/src/index.ts#L105-L115)). INFERRED: types are removed, not checked; see section 9.

### 3.5 Shared base, dispatch and Temporal settings

- **Base class.** `CodeExecutorBase` is an `ActionNode` with `group = ("code", "tool")`, main input and output handles, `task_queue = TaskQueue.CODE_EXEC`, `usable_as_tool = True` and annotations `destructive: True` ([server/nodes/code/_base.py:31-51](../../../../server/nodes/code/_base.py#L31-L51)). `TaskQueue.CODE_EXEC` is the string `code-exec` ([server/services/plugin/scaling.py:26](../../../../server/services/plugin/scaling.py#L26)).
- **Activity names and queues.** `node.<type>.v1` for all four, queue `code-exec` ([server/tests/temporal/fixtures/workflow_identities.json:1088-1091](../../../../server/tests/temporal/fixtures/workflow_identities.json#L1088-L1091), [server/tests/temporal/fixtures/workflow_identities.json:1247-1250](../../../../server/tests/temporal/fixtures/workflow_identities.json#L1247-L1250)). The per-type name and queue come from `_resolve_activity`: the queue is `cls.task_queue` only when the run's frozen routing has the worker pool on, otherwise the workflow's own queue; with per-type dispatch off the legacy `execute_node_activity` is used ([server/services/temporal/workflow.py:1098-1106](../../../../server/services/temporal/workflow.py#L1098-L1106)).
- **As a graph node.** `MachinaWorkflow` schedules the activity with `start_to_close_timeout` 24 h, `heartbeat_timeout` 2 min and the plugin's declared retry policy ([server/services/temporal/workflow.py:173](../../../../server/services/temporal/workflow.py#L173), [server/services/temporal/workflow.py:861-886](../../../../server/services/temporal/workflow.py#L861-L886)). The executors declare no retry policy of their own, so they get the default: 3 attempts, 1 s initial interval, coefficient 2, 60 s maximum, with `NodeUserError`, `ValidationError`, `PermissionDeniedError`, `InvalidParametersError` and `PayloadsTooLarge` non-retryable ([server/services/plugin/scaling.py:48-75](../../../../server/services/plugin/scaling.py#L48-L75)).
- **As an agent tool under `AgentWorkflow`.** The tool activity takes the plugin's declared options: `start_to_close_timeout` 10 min (the `ActionNode` value), heartbeat 2 min, the same retry policy ([server/services/temporal/agent_workflow.py:111-120](../../../../server/services/temporal/agent_workflow.py#L111-L120), [server/services/plugin/action.py:16-21](../../../../server/services/plugin/action.py#L16-L21), [server/services/plugin/scaling.py:92-96](../../../../server/services/plugin/scaling.py#L92-L96)). For newer generations the same values are read from a policy recorded when the payload was prepared, and the task queue is set from that policy when the worker pool is on ([server/services/temporal/agent_workflow.py:123-170](../../../../server/services/temporal/agent_workflow.py#L123-L170)).
- **What the activity does.** The per-type activity calls `workflow_service.execute_node(...)` in its own process ("the worker shares the FastAPI process") and runs a 30 s heartbeat task while the body runs ([server/services/plugin/base.py:1383-1463](../../../../server/services/plugin/base.py#L1383-L1463)). A node failure such as `NodeUserError` is returned as an envelope with `success: False`, not raised ([server/services/plugin/base.py:849-855](../../../../server/services/plugin/base.py#L849-L855), [server/services/plugin/base.py:1531-1542](../../../../server/services/plugin/base.py#L1531-L1542)), so Temporal does not retry it.
- **Repeat after a broken attempt.** All four executors are in `REPEATABLE`: a later attempt may simply run the call again ([server/services/approvals/repeats.py:57](../../../../server/services/approvals/repeats.py#L57), [server/services/approvals/repeats.py:63](../../../../server/services/approvals/repeats.py#L63), [server/services/approvals/repeats.py:72](../../../../server/services/approvals/repeats.py#L72), [server/services/approvals/repeats.py:86](../../../../server/services/approvals/repeats.py#L86)).
- **In-process tool calls** (RLM, MCP bridge, in-process agent loop). `execute_tool` reaches the plugin through `_dispatch_tool`, which builds a context without `connected_outputs` and calls `execute_as_tool` ([server/services/handlers/tools.py:176-272](../../../../server/services/handlers/tools.py#L176-L272)). Read from the code, no test opened: on this path `input_data` is `{}`.
- **Worker capacity.** The `code-exec` pool has 10 activity slots, 5 when `DEPLOYMENT_MODE` is local, and no rate limit ([server/services/temporal/worker.py:371-398](../../../../server/services/temporal/worker.py#L371-L398), [server/services/temporal/worker.py:431-437](../../../../server/services/temporal/worker.py#L431-L437)).
- **Hire allowlist.** None of the four executors, `rlm_agent` or `autonomous_agent` is in `enabled_nodes`; `coding_agent` is ([server/config/node_allowlist.json:3-100](../../../../server/config/node_allowlist.json#L3-L100)).

### 3.6 Side by side

| | montyExecutor | pythonExecutor | javascriptExecutor / typescriptExecutor |
|---|---|---|---|
| Runs in | Monty interpreter, worker thread of the hosting Python process | CPython `exec`, event loop thread of the hosting Python process | `node:vm` context in the bun sidecar process |
| Time limit | Enforced by Monty, `timeout` 1 to 600 s | Not enforced | `vm` timeout in ms, plus a 30 s HTTP client timeout |
| Memory limit | Enforced, 16 to 2048 MB | None | None |
| Script inputs | `input_data` | `input_data`, `workspace_dir`, `output` | `input_data` (with `workspace_dir` inside), `output` |
| Result | Last expression | `output` variable | `output` global |
| Imports | math, json, re | None (names pre-injected) | None |
| Host functions | `http_get` on request; `/workspace` mount on request | None | None |
| Tool name | `sandboxed_python` | `python_code` | `javascript_code` / `typescript_code` |
| Approval class | By capability | Send | Send |

## 4. Code and harnesses that call models, tools or agents

### 4.1 RLM agent

**What it is.** The node `rlm_agent` wraps the external `rlms` package (`rlms>=0.1.1` in [server/pyproject.toml:201-202](../../../../server/pyproject.toml#L201-L202), locked at 0.1.3 in [server/uv.lock:2954-2955](../../../../server/uv.lock#L2954-L2955)). The node collects connected context, skills, tools and input, prepares the prompt and calls `RLMService.execute` ([server/nodes/agent/rlm_agent/__init__.py:49-115](../../../../server/nodes/agent/rlm_agent/__init__.py#L49-L115)).

**How the REPL runs model-written code.** `RLMService.execute` builds `RLM(backend=..., environment="local", max_iterations=..., max_depth=..., custom_tools=..., logger=RLMLogger(), ...)` and calls `completion(augmented_prompt)` inside `asyncio.to_thread` ([server/services/rlm/service.py:237-258](../../../../server/services/rlm/service.py#L237-L258)). The loop itself is the library's. From the installed 0.1.3 source (rlm/core/rlm.py and rlm/environments/local_repl.py):
- Each iteration asks the model for a reply, extracts its code blocks and runs each with `exec(code, combined, combined)` in a namespace that persists across iterations.
- The namespace's builtins are a "safe builtins" dict that still contains `__import__` and `open`; `eval`, `exec`, `compile`, `input`, `globals` and `locals` are set to `None`.
- While a block runs, the environment replaces `sys.stdout` and `sys.stderr` under a lock and calls `os.chdir` into a temp directory. Both are process-wide.
- Completion is signalled by `answer["content"] = ...` and `answer["ready"] = True`. The word FINAL does not occur in the installed package.

**How `llm_query` and `rlm_query` call models.** Both are library functions placed in the REPL globals.
- `llm_query(prompt)` sends a request over a local TCP socket to the library's `LMHandler`, a threading socket server bound to 127.0.0.1 on a free port, which calls the provider client and returns the text. `llm_query_batched` runs up to 16 concurrently.
- `rlm_query(prompt)` starts a child RLM with its own REPL only when a sub-call callback was given, which the library does only when `max_depth > 1`. Otherwise it falls back to `llm_query`.
- Model clients are the library's own, built from `backend` and `backend_kwargs` (model name, API key, optional base URL) by `BackendAdapter` ([server/services/rlm/adapters.py:35-62](../../../../server/services/rlm/adapters.py#L35-L62)). No call goes through OpenCompany's `ChatUnifier`. Only six providers are accepted; others are refused ([server/services/rlm/constants.py:4-11](../../../../server/services/rlm/constants.py#L4-L11), [server/services/rlm/service.py:100-106](../../../../server/services/rlm/service.py#L100-L106)).
- A chat-model node connected to the agent's tools handle becomes the library's single "other backend", used for depth 1 calls, which is where `llm_query` lands ([server/services/rlm/adapters.py:65-117](../../../../server/services/rlm/adapters.py#L65-L117)).

**Recursion and other limits.** Defaults are `max_iterations` 30 and `max_depth` 1 ([server/services/rlm/constants.py:21-23](../../../../server/services/rlm/constants.py#L21-L23)). The service reads `max_iterations`, `max_depth`, `max_budget`, `max_timeout`, `max_tokens` and `verbose` from the parameters ([server/services/rlm/service.py:201-206](../../../../server/services/rlm/service.py#L201-L206)), but the node's params model has none of the first four and ignores extras ([server/nodes/agent/_specialized.py:33-87](../../../../server/nodes/agent/_specialized.py#L33-L87)). So through the node: 30 iterations, depth 1, no budget, no wall-clock limit. With depth 1 the library passes no sub-call callback, so `rlm_query` is a plain model call. The node's `max_tokens` option, when set, is passed as the library's total token limit for the whole run.

**How tools become callable from the code.** `ToolBridgeAdapter.bridge` turns each connected tool node into a synchronous Python function placed in the REPL as a `custom_tools` entry ([server/services/rlm/adapters.py:155-489](../../../../server/services/rlm/adapters.py#L155-L489)).
- Agents and chat-model nodes are skipped, except a `coding_agent` when the run carries the Pi flag ([server/services/rlm/adapters.py:177-181](../../../../server/services/rlm/adapters.py#L177-L181)).
- The function validates keyword arguments against the tool's input model, then schedules `execute_tool(name, args, config)` on the main event loop and blocks the REPL thread on the result ([server/services/rlm/adapters.py:274-287](../../../../server/services/rlm/adapters.py#L274-L287), [server/services/rlm/adapters.py:311-315](../../../../server/services/rlm/adapters.py#L311-L315)):

```python
future = asyncio.run_coroutine_threadsafe(
    execute_with_completion(),
    main_loop,
)
try:
    return future.result(
        timeout=(
            ToolBridgeAdapter.TOOL_TIMEOUT_SECONDS
        )
    )
```

- The wait limit is 60 s. On expiry the coroutine is cancelled, a 1 s grace is waited, and a `TimeoutError` saying the outcome is ambiguous is raised into the REPL ([server/services/rlm/adapters.py:128-130](../../../../server/services/rlm/adapters.py#L128-L130), [server/services/rlm/adapters.py:384-431](../../../../server/services/rlm/adapters.py#L384-L431); pinned by [server/tests/services/test_rlm_context_v2.py:121-193](../../../../server/tests/services/test_rlm_context_v2.py#L121-L193)).
- Tool calls run in-process through `execute_tool`. They are not Temporal activities. Approval checks still apply inside `_dispatch_tool` ([server/services/handlers/tools.py:251-261](../../../../server/services/handlers/tools.py#L251-L261)).
- A node that yields several tools (`node_tool_bindings` is not `None`) is refused for RLM ([server/services/rlm/service.py:163-168](../../../../server/services/rlm/service.py#L163-L168)).
- Python code has no tool-call ids. For `coding_agent` and `taskManager` under the Pi flag the wrapper derives one from the calling source position: `rlm:<sha256 of the code block>:<line>:<bytecode offset>:<occurrence>` ([server/services/rlm/adapters.py:247-268](../../../../server/services/rlm/adapters.py#L247-L268)).

**Where it runs.** `rlm_agent` is excluded from `AGENT_WORKFLOW_TYPES`; the comment says its REPL state "requires single-process continuity and breaks across activity boundaries" ([server/services/temporal/workflow.py:106-136](../../../../server/services/temporal/workflow.py#L106-L136)). As a graph node on Temporal it is one activity, `node.rlm_agent.v1` on queue `ai-heavy` ([server/tests/temporal/fixtures/workflow_identities.json:1059](../../../../server/tests/temporal/fixtures/workflow_identities.json#L1059), [server/tests/temporal/fixtures/workflow_identities.json:1218](../../../../server/tests/temporal/fixtures/workflow_identities.json#L1218)), with `RetryPolicy(maximum_attempts=1)` ([server/nodes/agent/rlm_agent/__init__.py:43-44](../../../../server/nodes/agent/rlm_agent/__init__.py#L43-L44)).

**What is persisted.**
- With a Context node: the stored conversation is loaded and rendered into the prompt, and after the run one exchange (the original prompt and the final response) is saved, best-effort ([server/services/rlm/service.py:50-60](../../../../server/services/rlm/service.py#L50-L60), [server/services/rlm/service.py:232-235](../../../../server/services/rlm/service.py#L232-L235), [server/services/rlm/service.py:260-268](../../../../server/services/rlm/service.py#L260-L268); pinned by [server/tests/services/test_rlm_context_v2.py:17-114](../../../../server/tests/services/test_rlm_context_v2.py#L17-L114)).
- The result dict: response, model, provider, total cost, token totals, execution time, iteration count ([server/services/rlm/service.py:289-304](../../../../server/services/rlm/service.py#L289-L304)).
- Not persisted: REPL variables, the iteration trajectory (the logger is created with no directory, so it keeps it in memory only), intermediate model calls.

**On a crash.** INFERRED from the above: the activity has one attempt, so a worker death fails the node once the 2 min heartbeat timeout passes; the in-memory REPL is lost; nothing of the partial run is saved, because the conversation save runs only after `completion` returns. Effects of tools already called are not rolled back.

### 4.2 Autonomous agent ("Code Mode")

- The node file is about ten lines: a `SpecializedAgentBase` subclass that sets the type, display name, description "Autonomous agent using Code Mode patterns" and a delegation description ([server/nodes/agent/autonomous_agent/__init__.py:4-10](../../../../server/nodes/agent/autonomous_agent/__init__.py#L4-L10)).
- It runs the standard agent: `execute_chat_agent` in process ([server/nodes/agent/_specialized.py:115-143](../../../../server/nodes/agent/_specialized.py#L115-L143)) or the `AgentWorkflow` child workflow, since `autonomous_agent` is in `AGENT_WORKFLOW_TYPES` ([server/services/temporal/workflow.py:131](../../../../server/services/temporal/workflow.py#L131)).
- It has no sandbox of its own. "Code Mode" is the skill [server/skills/autonomous/code-mode-skill/SKILL.md](../../../../server/skills/autonomous/code-mode-skill/SKILL.md), which tells the model to batch calculations into one `python_code` call and to use the other tools separately for network, files and devices (its section "When NOT to Use Code Mode").
- Tools do not become callable from code. The code runs in `pythonExecutor`, which exposes no host function (section 3.2).

### 4.3 Pi coding agent (PiCodingAgentWorkflow)

**What Pi is.** The Earendil Pi Coding Agent SDK, `@earendil-works/pi-coding-agent` 1.1.0, pinned in [server/pi/package.json:13-15](../../../../server/pi/package.json#L13-L15) and as `SDK_VERSION` on both sides ([server/services/temporal/pi_protocol.py:13-17](../../../../server/services/temporal/pi_protocol.py#L13-L17), [server/pi/src/types.ts:3-6](../../../../server/pi/src/types.ts#L3-L6)). OpenCompany runs it as a "maintenance driver" in a bun subprocess for one assistant response and its tool batch. The SDK performs no model request and no tool effect itself:
- The host disables `fetch` ([server/pi/src/index.ts:6-9](../../../../server/pi/src/index.ts#L6-L9)), loads no extensions, skills or prompts ([server/pi/src/driver.ts:17-32](../../../../server/pi/src/driver.ts#L17-L32)), registers a stub provider named `opencompany-temporal` and passes every tool as a custom tool whose callback only forwards the recorded call id ([server/pi/src/driver.ts:197-220](../../../../server/pi/src/driver.ts#L197-L220)). SDK compaction, retries and cache warming are off, built-in tools are off.
- The subprocess gets an allowlisted environment with no application credentials ([server/services/temporal/pi_activities.py:152-157](../../../../server/services/temporal/pi_activities.py#L152-L157)).
- The host is the bundle `dist/index.js` under `server/pi/`, or the TypeScript source in an unbuilt checkout ([server/services/pi_runtime.py:24-38](../../../../server/services/pi_runtime.py#L24-L38)).

**When it is used.** A `coding_agent` node dispatches to `PiCodingAgentWorkflow` when the run's frozen routing snapshot has `pi_harness_version == 1` ([server/services/temporal/workflow.py:1029-1032](../../../../server/services/temporal/workflow.py#L1029-L1032)). Every new Start writes that flag into the generation's resource manifest ([server/services/deployment/control.py:168](../../../../server/services/deployment/control.py#L168)), and admission copies it onto the workflow payload ([server/services/temporal/executor.py:164-179](../../../../server/services/temporal/executor.py#L164-L179), [server/services/coding_agent_runtime.py:60-66](../../../../server/services/coding_agent_runtime.py#L60-L66)). The native `AgentWorkflow` refuses a payload that carries the flag ([server/services/temporal/agent_workflow.py:955-958](../../../../server/services/temporal/agent_workflow.py#L955-L958)). `CODING_PI_ADMISSIONS_ENABLED=false` blocks new admissions ([server/services/coding_agent_runtime.py:88-100](../../../../server/services/coding_agent_runtime.py#L88-L100)).

**Shape.** `PiCodingAgentWorkflow` subclasses `AgentWorkflow` and overrides hook methods that the inherited loop calls ([server/services/temporal/pi_workflow.py:23-48](../../../../server/services/temporal/pi_workflow.py#L23-L48); hook defaults at [server/services/temporal/agent_workflow.py:3373-3404](../../../../server/services/temporal/agent_workflow.py#L3373-L3404); call sites at [server/services/temporal/agent_workflow.py:1394-1403](../../../../server/services/temporal/agent_workflow.py#L1394-L1403), [server/services/temporal/agent_workflow.py:1583](../../../../server/services/temporal/agent_workflow.py#L1583) and [server/services/temporal/agent_workflow.py:2188](../../../../server/services/temporal/agent_workflow.py#L2188)). The module docstring: "Only the harness changes. The inherited loop continues to own tool policies, approvals, Context persistence, team permits, task waits, compaction and events." ([server/services/temporal/pi_workflow.py:1-6](../../../../server/services/temporal/pi_workflow.py#L1-L6)).

**One turn, step by step.**
1. Workflow, `_before_runtime_model_step`: builds the turn request (scope, the three digests of transcript, configuration and tool bindings, cwd, messages, tools, model), attaches the stored snapshot reference when its digests still match, opens the ledger for this turn with one expected operation `turn:<n>:model`, and waits until a driver has asked for it ([server/services/temporal/pi_workflow.py:234-262](../../../../server/services/temporal/pi_workflow.py#L234-L262)). Waiting starts the driver activity if none is running ([server/services/temporal/pi_workflow.py:176-194](../../../../server/services/temporal/pi_workflow.py#L176-L194), [server/services/temporal/pi_workflow.py:212-224](../../../../server/services/temporal/pi_workflow.py#L212-L224)).
2. Driver activity `coding.pi_drive_turn` on queue `coding-pi-driver`: sends the Update `register_driver` to its own workflow and gets an epoch, spawns the bun host, sends a `run_turn` frame ([server/services/temporal/pi_activities.py:178-219](../../../../server/services/temporal/pi_activities.py#L178-L219)).
3. Host: rebuilds the SDK session from the snapshot or from the canonical messages, then calls `session.agent.prompt([])` ([server/pi/src/driver.ts:105-132](../../../../server/pi/src/driver.ts#L105-L132), [server/pi/src/driver.ts:253](../../../../server/pi/src/driver.ts#L253)). The SDK asks the stub provider for a reply; the host emits an `operation` frame of kind `model` and suspends on a promise ([server/pi/src/driver.ts:134-146](../../../../server/pi/src/driver.ts#L134-L146)).
4. Driver: forwards the frame as the Update `submit_operation`, then polls the Query `operation_status` every 0.1 s ([server/services/temporal/pi_activities.py:253-277](../../../../server/services/temporal/pi_activities.py#L253-L277)).
5. Workflow, `_execute_model_step`: runs the model call as the ordinary activity `agent.execute_llm_step`, registers one expected tool operation per returned call, and stores the step result as the model receipt (quoted below).
6. Driver sees `completed`, writes an `operation_result` frame; the SDK receives the assistant message. The host validates each recorded call and sends `assistant_ready` with a verdict per call ([server/pi/src/driver.ts:149-163](../../../../server/pi/src/driver.ts#L149-L163)); the driver forwards it as the Update `assistant_ready` ([server/services/temporal/pi_activities.py:293-295](../../../../server/services/temporal/pi_activities.py#L293-L295), [server/services/temporal/pi_workflow.py:90-108](../../../../server/services/temporal/pi_workflow.py#L90-L108)).
7. Workflow, `_before_runtime_tool_batch`: waits for that verdict. If any call is a `delegate_to_*` call or a Task Manager tool, it stops the driver first, so the driver slot is free while children run ([server/services/temporal/pi_workflow.py:277-295](../../../../server/services/temporal/pi_workflow.py#L277-L295)).
8. Per tool call, `_before_runtime_tool_call`: a call the SDK rejected gets a non-effect result and no tool runs; otherwise the workflow waits until the driver has asked for that tool operation, and the inherited loop then executes the tool as its usual activity or child workflow ([server/services/temporal/pi_workflow.py:316-324](../../../../server/services/temporal/pi_workflow.py#L316-L324)). Each tool message becomes that operation's receipt ([server/services/temporal/pi_workflow.py:297-311](../../../../server/services/temporal/pi_workflow.py#L297-L311)), which the driver hands back to the SDK's tool callback.
9. Workflow, `_complete_runtime_turn`: waits for the driver to return a `turn_checkpoint`; checks epoch, scope, turn and digests, the exact set of consumed operations, and that the SDK's projected transcript digest equals the canonical transcript digest; keeps the snapshot reference (or drops it when the transcript changed meanwhile); clears the ledger ([server/services/temporal/pi_workflow.py:326-387](../../../../server/services/temporal/pi_workflow.py#L326-L387)).

Step 5 in code ([server/services/temporal/pi_workflow.py:264-275](../../../../server/services/temporal/pi_workflow.py#L264-L275)):

```python
async def _execute_model_step(self, llm_payload, context, payload, iteration):
    model_id = f"turn:{iteration}:model"
    step = await workflow.execute_activity("agent.execute_llm_step", args=[llm_payload],
        activity_id=f"llm-step-{iteration + 1}", start_to_close_timeout=llm_attempt_timeout(payload),
        heartbeat_timeout=LLM_STEP_HEARTBEAT_TIMEOUT, retry_policy=LLM_STEP_RETRY)
    self._pi_calls = list(step.get("calls") or [])
    for index, call in enumerate(self._pi_calls):
        identity = tool_operation_id(iteration, index, call)
        self._pi.expected[identity] = {"kind": "tool", "payload": self._operation_payload(
            call_index=index, call_id=call.get("id") or "")}
    await self._receipt(model_id, step)
    return step
```

The host side of the same exchange, where the SDK program is suspended on a host call ([server/pi/src/index.ts:55-64](../../../../server/pi/src/index.ts#L55-L64)):

```ts
driver=new TurnDriver(input,async(operation,signal)=>{
  if (pending.has(operation.id)) throw new ProtocolError("duplicate_operation","Pi issued a concurrent duplicate operation");
  const response=new Promise<OperationResult>((resolve,reject)=>{
    const aborted=()=>{pending.delete(operation.id);reject(new DriverInterrupted(false));};
    signal?.addEventListener("abort",aborted,{once:true});
    pending.set(operation.id,{resolve,reject,cleanup:()=>signal?.removeEventListener("abort",aborted)});
  });
  await output(operation);
  return await response;
},output);
```

**Which parts are activities.**
- `agent.prepare_payload` (inherited), `agent.execute_llm_step`, each tool call's per-type activity or `coding.workspace_tool` for the automatic workspace tools ([server/services/coding_workspace_tools.py:9-10](../../../../server/services/coding_workspace_tools.py#L9-L10), [server/services/temporal/agent_activities.py:1894-1907](../../../../server/services/temporal/agent_activities.py#L1894-L1907)), child workflows for delegation: these are the business effects.
- `coding.pi_drive_turn`: the SDK driver. Its docstring: "all business effects stay in leaf Activities" ([server/services/temporal/pi_activities.py:1](../../../../server/services/temporal/pi_activities.py#L1)).
- `coding.pi_put_blob` for a receipt above 256 KiB ([server/services/temporal/pi_workflow.py:226-232](../../../../server/services/temporal/pi_workflow.py#L226-L232)), `coding.pi_child_control` for Stop and Resume of children, `agent.skill.clear` on failure.

**What is recorded.** In workflow history: the Updates (`register_driver`, `submit_operation`, `assistant_ready`, `wait_for_checkpoint`), every activity above with a stable id (`pi-driver-<turn>-<sequence>`, `llm-step-<n>`, `pi-blob-<operation id>`), and the child workflows. Queries are not recorded. In the database: snapshot and large-receipt blobs in `pi_runtime_blobs`. In workflow state: the `OperationLedger` ("History-resident receipts", [server/services/temporal/pi_protocol.py:61-69](../../../../server/services/temporal/pi_protocol.py#L61-L69)) and the snapshot reference `_pi_checkpoint`, which also rides the context across continue-as-new ([server/services/temporal/pi_workflow.py:43](../../../../server/services/temporal/pi_workflow.py#L43), [server/services/temporal/pi_workflow.py:373-375](../../../../server/services/temporal/pi_workflow.py#L373-L375)).

**The ledger's admission rule** ([server/services/temporal/pi_protocol.py:98-110](../../../../server/services/temporal/pi_protocol.py#L98-L110)). Before this, `submit` rejects an id the workflow did not expect and any payload field that differs from the recorded scope ([server/services/temporal/pi_protocol.py:83-97](../../../../server/services/temporal/pi_protocol.py#L83-L97)):

```python
fingerprint = semantic_fingerprint(request["kind"], payload)
# Do not trust a JavaScript-supplied digest: calculate both sides.
prior = self.operations.get(identity)
if prior and prior["fingerprint"] is not None and prior["fingerprint"] != fingerprint:
    raise conflict("Pi operation identity was reused with different content")
if payload["epoch"] != self.epoch:
    raise conflict("Pi driver epoch has been fenced")
if not prior:
    self.operations[identity] = {"fingerprint": fingerprint, "status": "pending",
        "receipt_only": bool(payload.get("receipt_only"))}
elif prior["fingerprint"] is None:
    prior["fingerprint"] = fingerprint
return {"id": identity, "status": self.operations[identity]["status"], "epoch": self.epoch}
```

**How a turn resumes after a worker restart.**
- Workflow worker restart: the workflow replays; the ledger is ordinary workflow state rebuilt from recorded Updates and activity results.
- Driver worker death: the activity's 90 s heartbeat lapses, Temporal starts another attempt (unlimited attempts), the new attempt registers and gets a higher epoch, spawns a new host and sends the same `run_turn`. The SDK replays the round; an operation that already has a receipt answers `completed` at once, so the model step and finished tools are not executed again. The old epoch is fenced: its status query answers `park` and its submissions are refused ([server/services/temporal/pi_protocol.py:122-128](../../../../server/services/temporal/pi_protocol.py#L122-L128)).
- Pinned by tests: [server/tests/temporal/test_pi_protocol.py:28-40](../../../../server/tests/temporal/test_pi_protocol.py#L28-L40) (receipts survive re-registration, the old epoch is fenced, a completed receipt cannot be overwritten) and [server/tests/temporal/test_pi_worker_recovery.py:149-182](../../../../server/tests/temporal/test_pi_worker_recovery.py#L149-L182) (a driver worker is hard-killed after tool A's receipt reached it; the orphan host exits on input EOF; a second worker finishes the run; the first model step and tool A ran once each; the history replays cleanly).
- The host also aborts when it gets no input for 120 s ([server/pi/src/index.ts:106](../../../../server/pi/src/index.ts#L106)); the driver sends `keepalive` every 30 s with its heartbeat ([server/services/temporal/pi_activities.py:340-345](../../../../server/services/temporal/pi_activities.py#L340-L345)).

**Wire protocol** (`pi_protocol` and [server/pi/src/types.ts:66-149](../../../../server/pi/src/types.ts#L66-L149)). LF-delimited UTF-8 JSON frames on the host's stdin and stdout, protocol version 1, at most 256 KiB per frame. Input frames: `run_turn`, `operation_result`, `park`, `abort`, `keepalive`. Output frames: `operation`, `assistant_ready`, `turn_checkpoint`, `parked`, `aborted`, `error`. Both sides compute SHA-256 over the same canonical JSON (sorted keys, JavaScript number spelling) ([server/services/temporal/pi_protocol.py:20-44](../../../../server/services/temporal/pi_protocol.py#L20-L44), [server/pi/src/protocol.ts:5-34](../../../../server/pi/src/protocol.ts#L5-L34)). Host errors reach the workflow only as fixed codes mapped to error types ([server/services/temporal/pi_activities.py:298-316](../../../../server/services/temporal/pi_activities.py#L298-L316)).

**Blobs.** Two separate stores.
- Transport files: a value of 256 KiB or more, up to 16 MiB, is written as `<sha256>.json` in a temp directory that exists for one activity attempt, and referenced from the frame; scope, epoch, size, hash and canonical encoding are verified on read ([server/services/pi_transport.py:19-79](../../../../server/services/pi_transport.py#L19-L79), [server/services/temporal/pi_activities.py:204-209](../../../../server/services/temporal/pi_activities.py#L204-L209)).
- Durable blobs: `put_blob(scope, kind, payload)` and `get_blob(scope, reference)` on the `pi_runtime_blobs` table, immutable and content-addressed, verified by scope and digest ([server/services/pi_blobs.py:12-43](../../../../server/services/pi_blobs.py#L12-L43), [server/models/pi_checkpoint.py:10-19](../../../../server/models/pi_checkpoint.py#L10-L19)). Kinds seen: `snapshot`, `receipt`, `inline-delegation-v1`.

**Control.** The Update `wait_for_checkpoint` parks the driver and bumps the epoch when the run is paused ([server/services/temporal/pi_workflow.py:50-63](../../../../server/services/temporal/pi_workflow.py#L50-L63)); the Query answers `park` while stopped ([server/services/temporal/pi_workflow.py:110-125](../../../../server/services/temporal/pi_workflow.py#L110-L125)). `PiDescendantExecutionControl` tracks child workflow handles and forwards control to them through the `coding.pi_child_control` activity; `PiLocalExecutionControl` is the variant for one-off runs with no deployment controller ([server/services/temporal/pi_control.py:9-86](../../../../server/services/temporal/pi_control.py#L9-L86)). Continue-as-new is allowed only with no driver, no pending operation and no live child ([server/services/temporal/pi_workflow.py:389-394](../../../../server/services/temporal/pi_workflow.py#L389-L394)).

### 4.4 Inline delegation (pi_inline_delegation)

**What it solves.** Some runtimes are not Temporal workflows: RLM code inside one activity, a CLI agent calling tools over MCP, the in-process agent loop. When one of them calls a tool whose target is a `coding_agent` in a run that carries the Pi flag, the call cannot wait inside the caller for a durable child. The module docstring: "The caller waits only for admission/start acknowledgement. This workflow owns the attached delegation runner and the legacy polling receipt across restarts." ([server/services/temporal/pi_inline_delegation.py:1-5](../../../../server/services/temporal/pi_inline_delegation.py#L1-L5)).

**Entry.** `execute_tool` routes an agent-typed tool to `_execute_delegated_agent`, whose first branch is ([server/services/handlers/tools.py:388-396](../../../../server/services/handlers/tools.py#L388-L396)): node type `coding_agent` and `pi_harness_version == 1` in the config, then `start_inline_pi_delegation(args, config, precreated_task_id=...)`; an `ApplicationError` is returned as an error dict. Callers that reach it: the RLM wrapper ([server/services/rlm/adapters.py:311-315](../../../../server/services/rlm/adapters.py#L311-L315)), the MCP tool handler ([server/services/cli_agent/workflow_tools.py:385](../../../../server/services/cli_agent/workflow_tools.py#L385)), and the in-process agent loop, which forwards the same scope ([server/services/ai.py:1222-1225](../../../../server/services/ai.py#L1222-L1225)). The `precreated_task_id` parameter serves Task Manager; that caller was not read here.

**`start_inline_pi_delegation`** is ordinary async code, not workflow code ([server/services/temporal/pi_inline_delegation.py:123-233](../../../../server/services/temporal/pi_inline_delegation.py#L123-L233)). In order:
1. Requires a database, target node id, parent node id, workflow id, root execution id, a call id (`operation_id` or `tool_call_id`) and a task text ([server/services/temporal/pi_inline_delegation.py:131-143](../../../../server/services/temporal/pi_inline_delegation.py#L131-L143)).
2. Checks that the graph has an edge from the target to the caller on `input-tools` or `input-teammates` and that the target is a `coding_agent` ([server/services/temporal/pi_inline_delegation.py:144-150](../../../../server/services/temporal/pi_inline_delegation.py#L144-L150)).
3. Requires Temporal to be enabled and connected ([server/services/temporal/pi_inline_delegation.py:151-155](../../../../server/services/temporal/pi_inline_delegation.py#L151-L155)); enforces delegation depth and reads the concurrency limit ([server/services/temporal/pi_inline_delegation.py:156-162](../../../../server/services/temporal/pi_inline_delegation.py#L156-L162)).
4. Derives every identity from recorded inputs (quoted below).
5. Builds the child context: the forwarded server scope (an allowlist of keys, [server/services/temporal/pi_inline_delegation.py:28-45](../../../../server/services/temporal/pi_inline_delegation.py#L28-L45)), the child's parameters with `system_message = task` and `prompt = context or task`, depth plus one, team and task ids ([server/services/temporal/pi_inline_delegation.py:168-206](../../../../server/services/temporal/pi_inline_delegation.py#L168-L206)).
6. Builds the request, sanitizes it, adds `request_digest`, validates it (size at most 1,900,000 bytes) ([server/services/temporal/pi_inline_delegation.py:61-76](../../../../server/services/temporal/pi_inline_delegation.py#L61-L76), [server/services/temporal/pi_inline_delegation.py:212-216](../../../../server/services/temporal/pi_inline_delegation.py#L212-L216)).
7. Records the admission: one `pi_runtime_blobs` row keyed by the runner workflow id, with the request digest, and in the same transaction the first polling receipt (`NodeOutput`, session `delegation_<task id>`, name `delegation_result`, status `running`). A retry with the same content finds the row and continues; different content under the same call fails with `PiProtocolConflict` ([server/services/temporal/pi_inline_delegation.py:79-109](../../../../server/services/temporal/pi_inline_delegation.py#L79-L109)).
8. Queues the team task unless it is already past queueing, starts the detached workflow, returns the acknowledgement `{success, status: "delegated", task_id, agent_node_id, agent_name, runner_workflow_id, message, result}` ([server/services/temporal/pi_inline_delegation.py:217-233](../../../../server/services/temporal/pi_inline_delegation.py#L217-L233)).

Identity and start ([server/services/temporal/pi_inline_delegation.py:164-167](../../../../server/services/temporal/pi_inline_delegation.py#L164-L167) and [server/services/temporal/pi_inline_delegation.py:222-229](../../../../server/services/temporal/pi_inline_delegation.py#L222-L229)):

```python
identity = canonical_digest({"version": INLINE_DELEGATION_VERSION, "root": root_id, "parent": parent_id,
                             "target": node_id, "call": call_id, "precreated_task": precreated_task_id})
task_id = precreated_task_id or f"delegated_pi_{identity[:32]}"
runner_id = f"coding-inline-{identity}"
# ... (child context, request and admission are built in between)
options = {"id": runner_id, "task_queue": settings.temporal_task_queue,
           "id_reuse_policy": WorkflowIDReusePolicy.REJECT_DUPLICATE,
           "memo": {"pi_inline_digest": request["request_digest"]}}
try:
    await executor.client.start_workflow(INLINE_DELEGATION_WORKFLOW, request, **options)
except WorkflowAlreadyStartedError:
    # Immutable admission matched above; attach to this recorded operation.
    pass
```

**Where the call id comes from.**
- MCP: `"mcp:" + digest({session: <MCP session id>, request: <JSON-RPC request id>})` ([server/services/cli_agent/workflow_tools.py:342-354](../../../../server/services/cli_agent/workflow_tools.py#L342-L354)).
- RLM: the source-position id described in section 4.1.
- Without a call id the admission is refused (step 1).

**`PiInlineDelegationWorkflow.run`** ([server/services/temporal/pi_inline_delegation.py:352-408](../../../../server/services/temporal/pi_inline_delegation.py#L352-L408)):
1. Validates the request, registers with execution control, waits until running.
2. Runs the activity `coding.inline_delegation_status` with status `running`. It verifies the request against the admission row before anything else happens ([server/services/temporal/pi_inline_delegation.py:112-120](../../../../server/services/temporal/pi_inline_delegation.py#L112-L120), [server/services/temporal/pi_inline_delegation.py:285-317](../../../../server/services/temporal/pi_inline_delegation.py#L285-L317)).
3. Starts the child workflow `DelegatedTaskWorkflow` with id `<runner id>-runner` and awaits it.
4. In `finally`: on error runs `agent.cancel_delegation`; always runs `coding.inline_delegation_status` again, which copies the durable team task's state into the polling receipt; unregisters.

**`DelegatedTaskWorkflow`** (shared with Task Manager; owned by the delegation map) registers the task, waits for a sub-agent permit, marks the delegation begun, starts the agent as a child workflow whose type comes from `agent_workflow_name` (so `PiCodingAgentWorkflow` here) with id `<runner id>-agent`, awaits it, and records the result with `agent.finish_delegation` ([server/services/temporal/agent_workflow.py:606-696](../../../../server/services/temporal/agent_workflow.py#L606-L696)).

**How the result gets back.** It is not returned into the caller's program. The caller's tool call already returned the acknowledgement. The caller later calls the built-in tool `check_delegated_tasks`, which reads live tasks, then an in-memory cache, then the `NodeOutput` receipt, and for a Pi-scoped caller checks the receipt against the admission row ([server/services/handlers/tools.py:988-1075](../../../../server/services/handlers/tools.py#L988-L1075), [server/services/temporal/pi_inline_delegation.py:236-282](../../../../server/services/temporal/pi_inline_delegation.py#L236-L282)). Each result is cut to 4000 characters ([server/services/handlers/tools.py:1097](../../../../server/services/handlers/tools.py#L1097)). RLM and Claude Code runs get the `check_delegated_tasks` tool added when a Coding Agent is connected ([server/services/rlm/service.py:153-155](../../../../server/services/rlm/service.py#L153-L155), [server/nodes/agent/claude_code_agent/__init__.py:304-310](../../../../server/nodes/agent/claude_code_agent/__init__.py#L304-L310)).

**What the tests pin** ([server/tests/temporal/test_pi_inline_delegation.py](../../../../server/tests/temporal/test_pi_inline_delegation.py)): the caller never awaits a business result (the fake client's `execute_workflow` raises if awaited) and `start_workflow` is called once ([server/tests/temporal/test_pi_inline_delegation.py:67-84](../../../../server/tests/temporal/test_pi_inline_delegation.py#L67-L84)); a lost start response reuses the same task and never overwrites a terminal receipt ([server/tests/temporal/test_pi_inline_delegation.py:87-101](../../../../server/tests/temporal/test_pi_inline_delegation.py#L87-L101)); the same call with different content fails before a second start ([server/tests/temporal/test_pi_inline_delegation.py:104-110](../../../../server/tests/temporal/test_pi_inline_delegation.py#L104-L110)); depth and connectivity fail before any team or child effect ([server/tests/temporal/test_pi_inline_delegation.py:113-121](../../../../server/tests/temporal/test_pi_inline_delegation.py#L113-L121)); scope forwarding copies only allowlisted keys ([server/tests/temporal/test_pi_inline_delegation.py:136-139](../../../../server/tests/temporal/test_pi_inline_delegation.py#L136-L139)). The MCP and RLM call ids are pinned in [server/tests/services/test_pi_inline_adapters.py:26-96](../../../../server/tests/services/test_pi_inline_adapters.py#L26-L96).

**For comparison, the durable caller.** When the caller is itself an `AgentWorkflow`, a `delegate_to_*` call becomes a child workflow started from workflow code with the deterministic id `<agent workflow id>-delegate-<tool node id>-<turn>-<call>` ([server/services/temporal/agent_workflow.py:290-297](../../../../server/services/temporal/agent_workflow.py#L290-L297), [server/services/temporal/agent_workflow.py:1886-1890](../../../../server/services/temporal/agent_workflow.py#L1886-L1890)), within a sliding window of concurrent children, and the results are appended as tool messages in the model's call order ([server/services/temporal/agent_workflow.py:1586-1610](../../../../server/services/temporal/agent_workflow.py#L1586-L1610)). The delegation map covers this path.

### 4.5 CLI agents' MCP bridge

- A FastMCP app (streamable HTTP) is mounted at `/mcp/ide` ([server/services/cli_agent/mcp_server.py:611-640](../../../../server/services/cli_agent/mcp_server.py#L611-L640), [server/main.py:686-691](../../../../server/main.py#L686-L691)).
- Each batch gets a bearer token mapped to a `BatchContext`; a middleware validates the token and binds the context for the request ([server/services/cli_agent/mcp_server.py:54-80](../../../../server/services/cli_agent/mcp_server.py#L54-L80), [server/services/cli_agent/mcp_server.py:286-354](../../../../server/services/cli_agent/mcp_server.py#L286-L354)).
- Claude Code receives the URL and token as `--mcp-config` JSON with `--strict-mcp-config` ([server/nodes/agent/claude_code_agent/_provider.py:200-220](../../../../server/nodes/agent/claude_code_agent/_provider.py#L200-L220)). Codex receives `-c mcp_servers.opencompany.url=...` and the token through an environment variable ([server/services/cli_agent/providers/openai_codex.py:114-124](../../../../server/services/cli_agent/providers/openai_codex.py#L114-L124)).
- Each tool node connected to the agent becomes one MCP tool with the same name and input model as for native agents ([server/services/cli_agent/workflow_tools.py:94-163](../../../../server/services/cli_agent/workflow_tools.py#L94-L163)). The handler checks that the tool belongs to the calling batch, validates the arguments, builds the config and calls `execute_tool` in the backend process ([server/services/cli_agent/workflow_tools.py:281-385](../../../../server/services/cli_agent/workflow_tools.py#L281-L385)). The CLI harness waits on the HTTP response.
- Infrastructure tools: `getWorkspaceFiles`, `listSkills`, `getSkill`, `readSkillResource`, `searchSkillResource`, `getCredential`, `broadcastLog`. The module docstring lists `executeCode` under "Tools deferred to v2" ([server/services/cli_agent/mcp_server.py:12-28](../../../../server/services/cli_agent/mcp_server.py#L12-L28)).
- Tool calls made this way are in-process calls, not Temporal activities, except the Coding Agent case of section 4.4.

### 4.6 The JS/TS sidecar

- **Process.** An Express server on bun, spawned on first use by `NodeJSExecutorRuntime` as `[bun, dist/index.js]` with the port and a token in its environment; it waits up to 50 probes 0.3 s apart for the port ([server/nodes/code/_runtime.py:103-160](../../../../server/nodes/code/_runtime.py#L103-L160)). `NODEJS_EXECUTOR_URL` skips the spawn and points at an external executor ([server/nodes/code/_nodejs.py:42-50](../../../../server/nodes/code/_nodejs.py#L42-L50)).
- **Token.** Made once per backend process with `secrets.token_urlsafe(32)` unless `NODEJS_EXECUTOR_TOKEN` is set ([server/nodes/code/_runtime.py:95-99](../../../../server/nodes/code/_runtime.py#L95-L99)); sent in the header `X-OpenCompany-Executor-Token` ([server/nodes/code/_client.py:10-26](../../../../server/nodes/code/_client.py#L10-L26)); compared in constant time before the body is parsed, on every route except `/health` ([server/nodejs/src/index.ts:85-103](../../../../server/nodejs/src/index.ts#L85-L103)). If another process holds the port and no shared token is set, the runtime refuses to send anything ([server/nodes/code/_runtime.py:143-152](../../../../server/nodes/code/_runtime.py#L143-L152)).
- **`/execute` contract.** Request `{code, language?, input_data?, timeout?}` with defaults `javascript`, `{}`, 30000 ms ([server/nodejs/src/index.ts:64-69](../../../../server/nodejs/src/index.ts#L64-L69), [server/nodejs/src/index.ts:119](../../../../server/nodejs/src/index.ts#L119)). Response `{success: true, output, console_output, execution_time_ms}` or `{success: false, error, console_output, execution_time_ms}` ([server/nodejs/src/index.ts:157-170](../../../../server/nodejs/src/index.ts#L157-L170)).
- **Evaluation.** `vm.runInContext(source, context, {timeout, filename})`, called synchronously in the request handler ([server/nodejs/src/index.ts:144-155](../../../../server/nodejs/src/index.ts#L144-L155)). The context object:

```ts
const sandbox = {
  console: capturedConsole,
  input_data,
  output: undefined as unknown,
  JSON, Math, Date, Array, Object, String, Number, Boolean, RegExp, Map, Set, Promise,
  setTimeout, setInterval, clearTimeout, clearInterval,
};
```

- **Isolation.** The source comment states that `vm` "is not a security boundary" and that the boundary is the localhost bind plus the token ([server/nodejs/src/index.ts:146-154](../../../../server/nodejs/src/index.ts#L146-L154)).
- **Limits.** Body size `NODEJS_EXECUTOR_BODY_LIMIT`, default `10mb` ([server/nodejs/src/index.ts:25](../../../../server/nodejs/src/index.ts#L25)). The package routes are rate-limited to 120 requests per 60 s, and `bun add` has a 60 s timeout ([server/nodejs/src/index.ts:57-62](../../../../server/nodejs/src/index.ts#L57-L62), [server/nodejs/src/index.ts:207](../../../../server/nodejs/src/index.ts#L207)). No memory limit.
- **Can user code call back into the host?** No. The contract has no callback channel, and the context holds nothing that reaches OpenCompany: a captured console, the input data, standard constructors and the timer functions. INFERRED from the handler: the response is built right after `runInContext` returns, so a value produced later by a promise or a timer cannot reach `output`.

## 5. Inputs, outputs and state

**Executor nodes.**
- `CodeExecutorParams`: `code: str` (min length 1), `timeout: int` (30, 1..600), extras allowed ([server/nodes/code/_base.py:17-21](../../../../server/nodes/code/_base.py#L17-L21)).
- `MontyExecutorParams`: `code`, `timeout`, `max_memory_mb: int` (256, 16..2048), `capabilities: list[str]`, extras allowed ([server/nodes/code/monty_executor/__init__.py:124-136](../../../../server/nodes/code/monty_executor/__init__.py#L124-L136)).
- `CodeExecutorOutput`: `output: Any`, `console_output: str`, extras allowed ([server/nodes/code/_base.py:24-28](../../../../server/nodes/code/_base.py#L24-L28)).
- State between calls: none in the Python and Monty executors. The sidecar process persists, but each `/execute` builds a new context.

**RLM.**
- Node params: `prompt`, `provider`, `model`, `system_message`, `temperature`, `max_tokens`, `runtime_behavior_version` ([server/nodes/agent/_specialized.py:33-87](../../../../server/nodes/agent/_specialized.py#L33-L87)).
- Service result: `{success, node_id, node_type, result: {response, model, provider, total_cost, total_input_tokens, total_output_tokens, execution_time, iterations}, execution_time}` ([server/services/rlm/service.py:289-304](../../../../server/services/rlm/service.py#L289-L304)).
- Bridged tool entry: `{tool_name: {"tool": <callable>, "description": str}}` ([server/services/rlm/adapters.py:474-486](../../../../server/services/rlm/adapters.py#L474-L486)).
- Ambiguous timeout record: `{outcome, reason, tool_name, tool_node_id, tool_node_type, arguments, timeout_seconds, cancel_requested, cancellation_observed}` ([server/services/rlm/adapters.py:394-408](../../../../server/services/rlm/adapters.py#L394-L408)).

**Delegation tool schema** (what a parent model fills in to call a sub-agent): `task: str`, `context: Optional[str]`; "The 'task' becomes the agent's mission directive (system message). The 'context' becomes the agent's input data (user prompt)." ([server/services/ai.py:2653-2674](../../../../server/services/ai.py#L2653-L2674)). The tool name is `delegate_to_<node type>` ([server/services/plugin/base.py:486-495](../../../../server/services/plugin/base.py#L486-L495)).

**MCP bridge.** `BatchContext`: `workflow_id`, `node_id`, `workspace_dir`, `execution_id`, `user_id`, `connected_skill_names`, `connected_skill_descriptors`, `allowed_credentials`, `connected_tools`, `broadcaster` ([server/services/cli_agent/mcp_server.py:54-80](../../../../server/services/cli_agent/mcp_server.py#L54-L80)).

**Pi protocol** ([server/pi/src/types.ts](../../../../server/pi/src/types.ts)).
- `RunTurn`: `type`, `protocol`, `turn`, `scope {workflow_id, generation_id, node_id}`, `epoch`, `context_digest`, `config_digest`, `binding_digest`, `cwd`, `blob_dir?`, `model {id, provider, contextWindow?, maxTokens?, reasoning?}`, `messages`, `tools [{name, description, parameters}]`, `snapshot?`.
- `Operation`: `type: "operation"`, `id`, `kind: "model" | "tool"`, `fingerprint`, `payload {scope, epoch, turn, context_digest, config_digest, binding_digest, purpose? | call_id?, call_index?, receipt_only?, error?}`.
- `OperationResult`: a model result `{assistant_message, calls?, usage?, finish_reason?, kind?}` or a tool result `{message, is_error?}`.
- `AssistantReady`: `turn`, `epoch`, `context_digest`, `binding_digest`, `calls [{call_index, call_id, receipt_only, error?}]`.
- `TurnCheckpoint`: `scope`, `turn`, `epoch`, `snapshot`, `consumed_operations`, `projection_digest` (the message bodies are dropped before sending).
- `Snapshot`: `version`, `sdk_version`, `scope`, `turn`, `epoch`, the three digests, `header`, `entries`, `leaf_id`.
- `OperationLedger` (Python): `epoch`, `turn`, `operations {id: {fingerprint, status, receipt_only, result?}}`, `expected {id: {kind, payload}}`, `parked` ([server/services/temporal/pi_protocol.py:61-69](../../../../server/services/temporal/pi_protocol.py#L61-L69)).
- Operation ids: `turn:<n>:model` and `turn:<n>:tool:<index>:<call id>` ([server/services/temporal/pi_protocol.py:51-52](../../../../server/services/temporal/pi_protocol.py#L51-L52)).
- `PiRuntimeBlob` row: `id`, `scope_digest`, `kind`, `digest`, `payload`, `created_at` ([server/models/pi_checkpoint.py:10-19](../../../../server/models/pi_checkpoint.py#L10-L19)).

**Inline delegation.**
- Request: `inline_delegation_version`, `runner_workflow_id`, `child_workflow_id`, `limit`, `lifecycle`, `child_context`, `request_digest` ([server/services/temporal/pi_inline_delegation.py:212-215](../../../../server/services/temporal/pi_inline_delegation.py#L212-L215)).
- `lifecycle`: `team_id`, `team_task_id`, `parent_agent_node_id`, `child_agent_node_id`, `child_agent_name`, `workflow_id`, `task`, `context`, `employee_job_id`, `root_execution_id`, `delegation_depth`, `trace_id`, `assignment_event_id`, `queued_event_id` ([server/services/temporal/pi_inline_delegation.py:207-211](../../../../server/services/temporal/pi_inline_delegation.py#L207-L211)).
- Polling receipt (`NodeOutput.data`): `task_id`, `status` (`running`, `completed`, `error`), `agent_name`, `agent_node_id`, `result`, `error`, `request_digest`, `runner_workflow_id` ([server/services/temporal/pi_inline_delegation.py:311-313](../../../../server/services/temporal/pi_inline_delegation.py#L311-L313)).

## 6. Determinism, versioning and durability

**Replay-safe by construction, and how.**
- **Executor nodes.** All code execution happens inside an activity body. Workflow code only schedules the activity and reads its recorded result, so replay never re-runs a script. Non-determinism inside the script (`random`, `datetime.now()`, `http_get`) stays inside the activity. A retry after a broken attempt does run the script again (they are listed as repeatable).
- **RLM.** One activity with one attempt. Replay-safe in the same sense, but with no durability inside: its REPL state lives in one thread's memory.
- **Autonomous agent.** The ordinary `AgentWorkflow`: model steps and tool calls are activities (agent-loop map).
- **Pi.** Workflow code is the coordinator only. It mutates the ledger in Update handlers and in the main loop, computes digests with pure functions, and schedules activities and children. The SDK process holds no authority: its transcript is checked against the canonical one by digest before a checkpoint is accepted, and a mismatch is a terminal `PiProtocolConflict`. The driver activity has no business effect, so re-running it is safe. The workflows are declared `sandboxed=False` ([server/services/temporal/pi_workflow.py:23](../../../../server/services/temporal/pi_workflow.py#L23), [server/services/temporal/pi_inline_delegation.py:320](../../../../server/services/temporal/pi_inline_delegation.py#L320)).
- **Inline delegation.** The caller is not a workflow, so determinism comes from identity instead: ids derived by digest from recorded inputs, an immutable admission row, `REJECT_DUPLICATE` on the workflow id, and a conflict error on changed content. The detached workflow itself only runs activities and one child.

**Not durable.**
- Tool calls made from RLM code, from a CLI agent over MCP, or from the in-process loop: plain in-process calls with no history (the Coding Agent case of section 4.4 excepted).
- The legacy in-process delegation path for non-Pi agents, an asyncio background task tracked in module dictionaries ([server/services/handlers/tools.py:43-81](../../../../server/services/handlers/tools.py#L43-L81)); owned by the delegation map.

**Versioning.**
- Generation flag `pi_harness_version` (value 1), frozen at Start and read from the routing snapshot ([server/services/coding_agent_runtime.py:14-28](../../../../server/services/coding_agent_runtime.py#L14-L28)). A history without it keeps the native loop.
- `PROTOCOL_VERSION = 1`, `SDK_VERSION = "1.1.0"`, `INLINE_DELEGATION_VERSION = 1`, snapshot `version: 1`. A snapshot with another format or SDK version is refused ([server/pi/src/driver.ts:108-111](../../../../server/pi/src/driver.ts#L108-L111)).
- One patch marker in the Pi workflow: `workflow.patched("pi-paused-receipt-checkpoint-v1")` ([server/services/temporal/pi_workflow.py:333](../../../../server/services/temporal/pi_workflow.py#L333)).
- Registered workflow types include `PiCodingAgentWorkflow`, `PiInlineDelegationWorkflow` and `DelegatedTaskWorkflow` ([server/services/temporal/workflow_registry.py:6-27](../../../../server/services/temporal/workflow_registry.py#L6-L27)).
- Activity names for nodes are `node.<type>.v<version>`; the executors are at version 1.

## 7. Limits, budgets, timeouts and retries

| Limit | Value | Defined at |
|---|---|---|
| Executor `timeout` parameter | Default 30 s, range 1 to 600 | [server/nodes/code/_base.py:19](../../../../server/nodes/code/_base.py#L19), [server/nodes/code/monty_executor/__init__.py:127](../../../../server/nodes/code/monty_executor/__init__.py#L127) |
| Monty memory | Default 256 MB, range 16 to 2048 | [server/nodes/code/monty_executor/__init__.py:129](../../../../server/nodes/code/monty_executor/__init__.py#L129) |
| Monty `http_get` | 15 s client timeout, public addresses only | [server/nodes/code/monty_executor/__init__.py:67-88](../../../../server/nodes/code/monty_executor/__init__.py#L67-L88) |
| pythonExecutor time and memory | Not enforced | [server/nodes/code/python_executor/__init__.py:101](../../../../server/nodes/code/python_executor/__init__.py#L101) |
| JS/TS script timeout | `timeout * 1000` ms; sidecar default 30000 ms | [server/nodes/code/javascript_executor/__init__.py:39](../../../../server/nodes/code/javascript_executor/__init__.py#L39), [server/nodejs/src/index.ts:119](../../../../server/nodejs/src/index.ts#L119) |
| JS/TS HTTP client timeout | 30 s total | [server/nodes/code/_nodejs.py:33](../../../../server/nodes/code/_nodejs.py#L33) |
| Sidecar request body | `10mb` by default | [server/nodejs/src/index.ts:25](../../../../server/nodejs/src/index.ts#L25) |
| Node result payload | Warning at 524,288 bytes; `NodeUserError` at 2,097,152 bytes | [server/services/media/limits.py:26-27](../../../../server/services/media/limits.py#L26-L27), [server/services/plugin/base.py:1144-1158](../../../../server/services/plugin/base.py#L1144-L1158) |
| Tool result into an agent transcript | 100,000 characters (`TOOL_RESULT_MAX_CHARS`, 0 disables, per-user override). Exempt: agent results, Task Manager, skill loads, `check_delegated_tasks` | [server/core/config.py:514](../../../../server/core/config.py#L514), [server/services/tool_output.py:34-61](../../../../server/services/tool_output.py#L34-L61) |
| Graph node activity | `start_to_close` 24 h, heartbeat 2 min | [server/services/temporal/workflow.py:173](../../../../server/services/temporal/workflow.py#L173), [server/services/temporal/workflow.py:868-878](../../../../server/services/temporal/workflow.py#L868-L878) |
| Agent tool activity (declared options) | `start_to_close` 10 min for an `ActionNode`, heartbeat 2 min | [server/services/temporal/agent_workflow.py:111-120](../../../../server/services/temporal/agent_workflow.py#L111-L120), [server/services/plugin/scaling.py:92-96](../../../../server/services/plugin/scaling.py#L92-L96) |
| Default plugin retry | 3 attempts, 1 s, x2, 60 s max; `NodeUserError` and four other types non-retryable | [server/services/plugin/scaling.py:48-75](../../../../server/services/plugin/scaling.py#L48-L75) |
| `code-exec` worker slots | 10, or 5 in local mode; no rate limit | [server/services/temporal/worker.py:371-398](../../../../server/services/temporal/worker.py#L371-L398) |
| RLM loop | 30 iterations, depth 1, no budget or wall-clock limit unless passed | [server/services/rlm/constants.py:21-23](../../../../server/services/rlm/constants.py#L21-L23) |
| RLM bridged tool wait | 60 s, then 1 s cancellation grace | [server/services/rlm/adapters.py:128-130](../../../../server/services/rlm/adapters.py#L128-L130) |
| RLM activity | Queue `ai-heavy`, 1 attempt | [server/nodes/agent/rlm_agent/__init__.py:43-44](../../../../server/nodes/agent/rlm_agent/__init__.py#L43-L44) |
| LLM step activity | 10 min attempt by default, heartbeat 1 min, unlimited retries 5 s to 5 min | [server/services/temporal/agent_workflow.py:96-98](../../../../server/services/temporal/agent_workflow.py#L96-L98), [server/services/temporal/_retry_policies.py:97-103](../../../../server/services/temporal/_retry_policies.py#L97-L103) |
| Pi driver activity | Queue `coding-pi-driver`, `start_to_close` 30 min, heartbeat 90 s, unlimited retries 1 s to 30 s, four non-retryable types | [server/services/temporal/pi_workflow.py:189-194](../../../../server/services/temporal/pi_workflow.py#L189-L194) |
| Pi driver slots | 1 in local mode, else 2; `TEMPORAL_CODING_PI_DRIVER_CONCURRENCY` overrides | [server/services/temporal/worker.py:718-759](../../../../server/services/temporal/worker.py#L718-L759) |
| Pi frame and transport file | 256 KiB inline, 16 MiB file | [server/services/temporal/pi_protocol.py:16-17](../../../../server/services/temporal/pi_protocol.py#L16-L17) |
| Pi host input lease | 120 s; keepalive every 30 s | [server/pi/src/index.ts:106](../../../../server/pi/src/index.ts#L106), [server/services/temporal/pi_activities.py:340-345](../../../../server/services/temporal/pi_activities.py#L340-L345) |
| Pi operation polling | 0.1 s for a result; 1 s for a park check | [server/services/temporal/pi_activities.py:224](../../../../server/services/temporal/pi_activities.py#L224), [server/services/temporal/pi_activities.py:277](../../../../server/services/temporal/pi_activities.py#L277) |
| Inline delegation request | At most 1,900,000 bytes | [server/services/temporal/pi_inline_delegation.py:75-76](../../../../server/services/temporal/pi_inline_delegation.py#L75-L76) |
| Inline delegation activities | 60 s `start_to_close`, unlimited retries | [server/services/temporal/pi_inline_delegation.py:363-366](../../../../server/services/temporal/pi_inline_delegation.py#L363-L366), [server/services/temporal/_retry_policies.py:117-121](../../../../server/services/temporal/_retry_policies.py#L117-L121) |
| Delegation depth | `MAX_DELEGATION_DEPTH` default 2, range 1 to 2; `AgentWorkflow` caps at 2 | [server/core/config.py:526](../../../../server/core/config.py#L526), [server/services/temporal/agent_workflow.py:1591-1594](../../../../server/services/temporal/agent_workflow.py#L1591-L1594) |
| Concurrent sub-agents | `MAX_CONCURRENT_SUBAGENTS` default 3, range 1 to 50 | [server/core/config.py:525](../../../../server/core/config.py#L525), [server/services/temporal/agent_workflow.py:1595-1597](../../../../server/services/temporal/agent_workflow.py#L1595-L1597) |
| Sub-agent permit wait | 1 h per attempt, heartbeat 10 s, unlimited attempts | [server/services/temporal/agent_workflow.py:641-650](../../../../server/services/temporal/agent_workflow.py#L641-L650) |
| `check_delegated_tasks` result | 4000 characters per task | [server/services/handlers/tools.py:1097](../../../../server/services/handlers/tools.py#L1097) |
| Agent loop steps | `AGENT_RECURSION_LIMIT` default 200 | [server/core/config.py:519](../../../../server/core/config.py#L519) |
| Agent workflow history | Soft cap 10,000 events; continuation input at most 1,900,000 bytes | [server/services/temporal/agent_workflow.py:350](../../../../server/services/temporal/agent_workflow.py#L350), [server/services/temporal/agent_workflow.py:384](../../../../server/services/temporal/agent_workflow.py#L384) |

## 8. Extension points relevant to a dynamic workflow

Facts only.

**Where a host function reaches a Monty script today.**
- `_build_capabilities` returns the dict that is passed as `external_functions` to `m.run` ([server/nodes/code/monty_executor/__init__.py:104-121](../../../../server/nodes/code/monty_executor/__init__.py#L104-L121), [server/nodes/code/monty_executor/__init__.py:210](../../../../server/nodes/code/monty_executor/__init__.py#L210)). `http_get` is the one entry. Names are gated by `CAPABILITY_MENU`.
- With the sync `run`, a host function is an ordinary Python callable executed on the `to_thread` worker thread. The node body has the `NodeContext` (`ctx.raw`, `ctx.workspace_dir`) in scope when it builds the dict.
- On the Temporal activity path `ctx.raw` carries the forwarded scope keys, among them `tool_call_id`, `parent_node_id`, `root_execution_id`, `generation` and `run_scope` ([server/services/plugin/base.py:1406-1447](../../../../server/services/plugin/base.py#L1406-L1447)).
- The pattern for calling async OpenCompany code from a blocking function running in another thread already exists in the RLM bridge: `asyncio.run_coroutine_threadsafe(...)` onto the main loop, then a bounded `future.result(timeout=...)` ([server/services/rlm/adapters.py:374-383](../../../../server/services/rlm/adapters.py#L374-L383)).

**What the pinned Monty package offers beyond what is used** (installed 0.0.18 type stub and package README).
- `Monty.start(inputs=, limits=, print_callback=, mount=, os=)` returns a `FunctionSnapshot` when the script calls an external function, with `function_name`, `args`, `kwargs`, `call_id`; `snapshot.resume({"return_value": ...})` or `resume({"exception": ...})` or `resume({"exc_type": ..., "message": ...})` continues it and returns the next snapshot or a `MontyComplete`.
- `resume({"future": ...})` marks a call as pending; a `FutureSnapshot` then lists `pending_call_ids` and is resumed with `{call_id: result}`.
- `FunctionSnapshot.dump()` returns bytes; `load_snapshot(bytes, print_callback=...)` restores it. The README text: "suspending execution across process boundaries".
- `run_async(...)` awaits external functions that return coroutines on the event loop; it takes no `mount`.
- `MontyRepl` keeps state across snippets (`feed_run`, `feed_start`, `dump`, `load`).
- `Monty(..., type_check=True, type_check_stubs=...)` type-checks the script against stub declarations before running.
- None of these is called anywhere in the repository.

**Components that already suspend a program at a host call and resume it with a result.**
- The Pi host: the SDK is suspended on a promise per operation ([server/pi/src/index.ts:55-64](../../../../server/pi/src/index.ts#L55-L64)); the result is produced by Temporal work and delivered as a frame. After a process loss the program is not resumed from memory: it is re-run and receives the recorded receipts.
- The RLM tool wrapper: the REPL thread blocks on a future; in memory only.
- The MCP bridge: the CLI process blocks on an HTTP response; in memory only.
- `AgentWorkflow`: workflow code awaits child workflow handles and activity results (agent-loop and delegation maps).

**Existing shapes a script-level sub-agent call maps onto.**
- A sub-agent call is already `delegate_to_<type>(task, context)` at the tool layer ([server/services/ai.py:2653-2674](../../../../server/services/ai.py#L2653-L2674)).
- From workflow code a sub-agent run is `workflow.start_child_workflow(agent_workflow_name(...), args=[child_context], id=...)` behind a permit and the depth check ([server/services/temporal/agent_workflow.py:1886-1890](../../../../server/services/temporal/agent_workflow.py#L1886-L1890)).
- From non-workflow code, `_execute_delegated_agent` takes the durable route only for `coding_agent` targets under the Pi flag (`start_inline_pi_delegation`); for other agent types it continues into its in-process background-task branch ([server/services/handlers/tools.py:372-396](../../../../server/services/handlers/tools.py#L372-L396)). Task Manager has its own dispatch, which was not read here.
- A tool call from workflow code is one activity with id `tool-<tool node id>-<turn>-<call>` ([server/services/temporal/agent_workflow.py:285-287](../../../../server/services/temporal/agent_workflow.py#L285-L287)).

**Idempotency keys for calls made by a program.**
- Pi: `turn:<n>:model`, `turn:<n>:tool:<index>:<call id>`, with a content fingerprint.
- RLM: source-position ids. MCP: session plus request id.
- `_dispatch_tool` carries `tool_call_id` and `operation_id` as trusted keys "never accepted from the model's JSON arguments" ([server/services/handlers/tools.py:185-189](../../../../server/services/handlers/tools.py#L185-L189)).

**Limits a script-driven tool would inherit.** Section 7. In short: script time at most 600 s; the tool activity's 10 min `start_to_close` and 2 min heartbeat; the 2 MiB payload ceiling on the result; 100,000 characters into the transcript unless the tool's node type is exempt; depth 2 and 3 concurrent sub-agents; 60 s if a tool is called through the RLM-style blocking bridge.

## 9. Where the docs disagree with the code

1. **RLM completion signal.** [docs-internal/rlm_service.md](../../../rlm_service.md) (sections "How RLM Differs from Standard Agents" and "RLM Concepts Quick Reference"), [docs-internal/node-logic-flows/specialized_agents/rlm_agent.md](../../../node-logic-flows/specialized_agents/rlm_agent.md) and the skill [server/skills/rlm_agent/rlm-reasoning-skill/SKILL.md](../../../../server/skills/rlm_agent/rlm-reasoning-skill/SKILL.md) say the model ends with `FINAL(answer)` or `FINAL_VAR(name)`. The installed rlms 0.1.3 has no such function; its prompt and REPL use `answer["content"]` and `answer["ready"] = True`. The pin is a floor (`>=0.1.1`), so which signal applies depends on the installed version.
2. **RLM recursion.** The same docs say `rlm_query()` spawns a child RLM with its own REPL. With the service default `max_depth` 1 and a node schema that cannot set it, the library gives no sub-call callback and `rlm_query` is a plain model call. The doc's own parameters section records that the field is not configurable.
3. **RLM "sandboxed".** The docs call the REPL sandboxed. The installed local environment keeps `__import__` and `open` in its builtins and changes the process working directory and standard streams while a block runs.
4. **Stale line references** in [docs-internal/rlm_service.md](../../../rlm_service.md): it cites the service's context code at lines 48-56, 202-203, 230-234 and 241-246; the code is now at 50-60, 232-235, 260-268 and 271-276. It cites the tool-name stamping in the plugin base at line 257; it is at 486-488.
5. **Monty card, tests.** [docs-internal/node-logic-flows/code_fs_process/montyExecutor.md](../../../node-logic-flows/code_fs_process/montyExecutor.md) names the general code tests file as the node's tests. The Monty tests are in [server/tests/nodes/test_monty_executor.py](../../../../server/tests/nodes/test_monty_executor.py); the general file has no Monty test.
6. **`NODEJS_EXECUTOR_TIMEOUT`.** The JS card ([docs-internal/node-logic-flows/code_fs_process/javascriptExecutor.md](../../../node-logic-flows/code_fs_process/javascriptExecutor.md), section "Sidecar client singleton"), the project instructions and the comment in [.env.template](../../../../.env.template) name this variable as the client's request timeout. No code reads it (grep over `.py` and `.ts`); the timeout is the default argument 30 in [server/nodes/code/_nodejs.py:33](../../../../server/nodes/code/_nodejs.py#L33).
7. **TypeScript "type safety".** The node description says "with type safety" ([server/nodes/code/typescript_executor/__init__.py:16-18](../../../../server/nodes/code/typescript_executor/__init__.py#L16-L18)). The sidecar only strips types ([server/nodejs/src/index.ts:105-115](../../../../server/nodejs/src/index.ts#L105-L115)). INFERRED that no type error can be reported; the test that shows a `TS2304` error uses a mocked client.
8. **Autonomous agent card.** [docs-internal/node-logic-flows/specialized_agents/autonomous_agent.md](../../../node-logic-flows/specialized_agents/autonomous_agent.md) says the tools are "typically combined so the agent writes code to orchestrate other tools". No executor lets code call a tool, and the code-mode skill tells the model to call other tools separately. The card's other statement, that the behaviour "comes entirely from the attached skill content, not a different execution engine", matches the code.
9. **Class timeouts.** [server/services/plugin/scaling.py:1-8](../../../../server/services/plugin/scaling.py#L1-L8) says the Temporal dispatcher reads each node's `start_to_close_timeout` and `heartbeat_timeout`. For graph nodes the workflow uses 24 h and 2 min regardless and takes only the retry policy from the class, unless the node is a workspace task ([server/services/temporal/workflow.py:861-884](../../../../server/services/temporal/workflow.py#L861-L884)). The class values apply to agent tool calls. `CODE_START_TO_CLOSE` (5 min, [server/services/plugin/scaling.py:99](../../../../server/services/plugin/scaling.py#L99)) is defined and used nowhere.
10. **MCP discovery comment.** The comment in [server/main.py:682-685](../../../../server/main.py#L682-L685) says CLI sessions find the MCP server through a lockfile. The MCP module says Claude reaches it through `--mcp-config` and the lockfile serves only the generic session path ([server/services/cli_agent/mcp_server.py:3-6](../../../../server/services/cli_agent/mcp_server.py#L3-L6)).
11. **Pi doc.** [docs-internal/pi_coding_agent.md](../../../pi_coding_agent.md) matched the code on every point checked: queue name, driver slots, 30 s / 90 s / 30 min, the 256 KiB threshold, the 1,900,000-byte guard, parking before delegated work. One narrowing: it names RLM and Claude Code as inline callers; the code branch is keyed only on the target type and the flag, so any caller of `execute_tool` reaches it.
12. **Archive.** [docs-internal/ARCHIVE/autonomous_agent_creation.md](../../../ARCHIVE/autonomous_agent_creation.md) is external research on code-mode agents and states itself that it never described this repository's plugin.

## 10. Not verified / open questions

- Nothing was executed. Every statement about pydantic-monty and rlms beyond the repository's own calls comes from the installed package files (type stub, README, library source), not from a run.
- Whether a Monty 0.0.18 script can `import asyncio` and use `asyncio.gather`, and how concurrent external calls behave under `run`, `run_async` and `start`. The plugin lists only "async def/await" as supported; the stub shows `FutureSnapshot`. Not tested in the repository.
- Whether Monty's `max_duration_secs` counts time spent inside an external function, and what happens to the worker thread when the activity is cancelled while `m.run` is in progress. INFERRED: the thread runs until Monty's own limit ends it.
- Whether `MountDir` confines paths as the plugin comment says. Taken from the comment.
- The size of a Monty snapshot from `dump()` and whether it is stable across package versions. The package is alpha and pinned exactly.
- How `AgentWorkflow` dispatches a tool call whose target is an agent outside `AGENT_WORKFLOW_TYPES` (for example `delegate_to_rlm_agent`), and how Task Manager dispatches a teammate from a caller that is not a workflow. Left to the delegation map.
- The legacy `execute_node_activity` path for executor nodes beyond the lines read in [server/services/temporal/activities.py](../../../../server/services/temporal/activities.py) (it sends `execute_node` over the internal WebSocket and heartbeats every 30 s).
- [server/pi/src/codec.ts](../../../../server/pi/src/codec.ts) after line 40, [server/pi/scripts/build.ts](../../../../server/pi/scripts/build.ts), the Pi SDK's internals, and the bun tests under `server/pi/tests/`.
- The test harness used by the node contract tests (`harness.execute`) was not opened; test descriptions here come from the test bodies.
- Whether rlms 0.1.1 or 0.1.2 used `FINAL`. Only the installed 0.1.3 was read.
- The INFERRED items in sections 3 and 4 (the event-loop block in `pythonExecutor`, the JS HTTP timeout ordering, the RLM crash behaviour, late promise results in the sidecar) follow from reading the code and are not covered by a test that was opened.
