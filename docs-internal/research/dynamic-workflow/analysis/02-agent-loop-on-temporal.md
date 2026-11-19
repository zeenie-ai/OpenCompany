# The agent loop on Temporal: code map

> **Status: Historical snapshot.** A map of the code at commit 908a8ce6 (2026-11-12), written for the [dynamic workflow research](../README.md) and not updated since, so a line number here may be stale. The maintained documents for this subject are [Agent Architecture](../../../agent_architecture.md), [Temporal Architecture](../../../TEMPORAL_ARCHITECTURE.md) and [Agent Context Flow](../../../agent_context_flow.md).

Scope: `AgentWorkflow`, the activities it schedules, and how every kind of tool call is executed on the Temporal path. Delegation, Task Manager and teams are recorded only where they hook into the loop.
Written 2026-11-12, as of commit 908a8ce6. The code files cited here are identical between that commit and the working tree that was read (checked with a diff over the cited paths). Nothing was run; every statement comes from reading the source.

Link legend: AW is the file `server/services/temporal/agent_workflow.py`, AA is the file `server/services/temporal/agent_activities.py`. Other files are named in the link text by their base name. Every link target is the full path.

## 1. Summary

1. `AgentWorkflow` is an unsandboxed child workflow that runs one agent: one `agent.prepare_payload` activity, then per iteration one `agent.execute_llm_step` activity and one activity or child workflow per tool call. Dispatch decisions are workflow code; model calls, database access and broadcasts are activities.
2. The whole transcript (`messages`, a list of MessageWire dicts) is workflow state and is sent in full as the input of every LLM step. It crosses continue-as-new and is bounded by a per-result cap (100,000 characters), a byte budget (393,216 bytes) and compaction.
3. Tool calls of one model message run one after another, in model order. The only concurrency inside a turn is the Task Manager `assign_task` preflight (`workflow.start_activity` plus `asyncio.gather`) and delegated child workflows (a window of 3 by default).
4. A call is routed by its LLM-visible name to a tool entry recorded at prepare time, and from there to `node.<type>.v<version>`, `agent.skill.invoke`, `coding.workspace_tool` or a child workflow. A `run_workflow` tool call would enter at the same branch ([AW:2391](../../../../server/services/temporal/agent_workflow.py#L2391) to [AW:2578](../../../../server/services/temporal/agent_workflow.py#L2578)).
5. Stop and pause are cooperative gates in workflow code (`ExecutionControl`). The in-turn approval wait is a child `ApprovalWorkflow`; it is active only in a generation with `approval_wait_version` 1, which Start does not write today.
6. Replay safety rests on 8 `workflow.patched` ids plus version fields recorded by `agent.prepare_payload` or frozen in the generation. Tool timeouts and retry policies come from the plugin class, not from the workflow.

## 2. Files and responsibilities

| File | Lines (about) | What it owns |
|---|---|---|
| [server/services/temporal/agent_workflow.py](../../../../server/services/temporal/agent_workflow.py) | 3,550 | `AgentWorkflow` (the loop, tool-call dispatch, pressure relief calls, continue-as-new), `DelegatedTaskWorkflow`, id helpers, `_serialise_tool_result` |
| [server/services/temporal/agent_activities.py](../../../../server/services/temporal/agent_activities.py) | 2,905 | The `agent.*` activities: prepare payload, LLM step, persist turn, broadcast progress, store output, refresh tools, skill invoke and clear, compaction, and the delegation and permit lifecycle |
| [server/services/temporal/agent_context_pressure.py](../../../../server/services/temporal/agent_context_pressure.py) | 342 | Pure functions that clear, measure and clip the transcript; `CONTEXT_PRESSURE_VERSION` |
| [server/services/temporal/_retry_policies.py](../../../../server/services/temporal/_retry_policies.py) | 229 | Every retry policy a workflow schedules with, and `frozen_retry_policy` |
| [server/services/temporal/execution_control.py](../../../../server/services/temporal/execution_control.py) | 229 | `ExecutionControl`: admission counters and the pause state the loop gates on |
| [server/services/temporal/approval_workflow.py](../../../../server/services/temporal/approval_workflow.py) | 75 | `ApprovalWorkflow`: waits for the owner's answer to one held call |
| [server/services/temporal/approved_tool_call_workflow.py](../../../../server/services/temporal/approved_tool_call_workflow.py) | 125 | `run_approved_call` (claim, run once, record) and `ApprovedToolCallWorkflow` |
| [server/services/temporal/workflow.py](../../../../server/services/temporal/workflow.py) | 1,291 | `MachinaWorkflow`; `AGENT_WORKFLOW_TYPES`; starts `AgentWorkflow` as a child; `GENERATION_FLAGS` (read in part) |
| [server/services/temporal/worker.py](../../../../server/services/temporal/worker.py) | 960 | Worker and pool construction: which activities each queue serves, concurrency and rate limits (read in part) |
| [server/services/plugin/base.py](../../../../server/services/plugin/base.py) | 1,638 | `BaseNode.as_activity` (the body of every `node.<type>.v<version>` activity), `execute_as_tool`, result size check, default timeouts |
| [server/services/plugin/scaling.py](../../../../server/services/plugin/scaling.py) | 99 | `TaskQueue` names, plugin `RetryPolicy` defaults, default timeouts |
| [server/services/plugin/tool.py](../../../../server/services/plugin/tool.py) | 252 | `ToolNode`, `ToolBinding`, `tool_bindings`, `inline_schema_refs` |
| [server/services/agent_bindings.py](../../../../server/services/agent_bindings.py) | 140 | `node_tools` (one tool or one per binding), `unique_node_bindings`, `is_runtime_tool` |
| [server/services/ai.py](../../../../server/services/ai.py) | 2,793 | `_build_tool_from_node`, `_get_tool_schema` (tool name, description and argument schema) |
| [server/services/tool_output.py](../../../../server/services/tool_output.py) | 108 | `bound_tool_output`, `tool_output_is_capped`, `resolve_tool_output_limit` |
| [server/services/approvals/tool_calls.py](../../../../server/services/approvals/tool_calls.py) | 788 | `check`: holds, restricts or lets a tool call run; the redo guard for a broken-off attempt |
| [server/services/approvals/waiting.py](../../../../server/services/approvals/waiting.py) | 202 | The row a waiting turn reads, the `nudge` Signal, decline by message |
| [server/services/approvals/classes.py](../../../../server/services/approvals/classes.py) | 561 | The class of each node call (read, write, send, money) |
| [server/services/approvals/repeats.py](../../../../server/services/approvals/repeats.py) | 300 | Which node calls may run again after an attempt broke off |
| [server/services/approvals/contract.py](../../../../server/services/approvals/contract.py) | 66 | Statuses, the 168 hour expiry, the 5 second Undo window |
| [server/services/chat/steps.py](../../../../server/services/chat/steps.py) | 191 | Chat steps around a tool call; the stopped-run answer |
| [server/services/chat/stream.py](../../../../server/services/chat/stream.py) | 253 | `ChatStreamEmitter`; `chat_stream_for` decides which agent answers a chat run |
| [server/services/agent_runtime.py](../../../../server/services/agent_runtime.py) | 494 | `run_native_llm_step` (shared with the Temporal path) and the in-process `run_native_agent_loop` |
| [server/services/handlers/tools.py](../../../../server/services/handlers/tools.py) | 1,100+ | `execute_tool`: the in-process tool dispatch. `AgentWorkflow` does not call it |

## 3. How it executes

Each step names the Temporal primitive. Three terms are used throughout:

- **Controlled**: the run has `execution_control_version` 1. Every generation Start admits is controlled: its manifest is written with `execution_control_version` 1 and `pi_harness_version` 1 ([deployment/control.py:168](../../../../server/services/deployment/control.py#L168), [executor.py:163](../../../../server/services/temporal/executor.py#L163)).
- **Durable**: controlled, or a run with `native_workspace_version` 1, `browser_runtime_version` 1, `browser_routing_version` 1, or the Pi harness. This is the `durable_runtime` flag ([AW:1087](../../../../server/services/temporal/agent_workflow.py#L1087)); the same five conditions select the frozen tool options ([AW:138](../../../../server/services/temporal/agent_workflow.py#L138)).
- **Legacy**: none of those markers, for example a manual canvas Run.

1. **Start (child workflow).** `MachinaWorkflow` resolves an agent node to `{"kind": "child_workflow", "name": "AgentWorkflow"}` when the frozen routing has `agent_workflow_enabled` and the type is in `AGENT_WORKFLOW_TYPES` ([workflow.py:1035](../../../../server/services/temporal/workflow.py#L1035), [workflow.py:115](../../../../server/services/temporal/workflow.py#L115)). It calls `workflow.start_child_workflow` with `args=[context]` and `id="<root workflow id>-agent-<node id>"`, no timeout and no task queue, so the child inherits the parent's queue ([workflow.py:836](../../../../server/services/temporal/workflow.py#L836), [workflow.py:176](../../../../server/services/temporal/workflow.py#L176)). `rlm_agent` and `claude_code_agent` are not in the set and run as one activity each ([workflow.py:111](../../../../server/services/temporal/workflow.py#L111)). With `pi_harness_version` 1 a `coding_agent` resolves to `PiCodingAgentWorkflow`, a subclass of `AgentWorkflow` ([workflow.py:1029](../../../../server/services/temporal/workflow.py#L1029), [pi_workflow.py:24](../../../../server/services/temporal/pi_workflow.py#L24)).
2. **Init and wrapper (workflow code).** `@workflow.init` creates the `ExecutionControl` and binds it to the input ([AW:823](../../../../server/services/temporal/agent_workflow.py#L823)). `run` calls `_run_impl` and, on the way out, records an employee job failure when applicable, releases Browser claims with a shielded `browser_tasks.release` activity, and waits for update handlers to finish ([AW:953](../../../../server/services/temporal/agent_workflow.py#L953)).
3. **Prepare (activity `agent.prepare_payload`).** `workflow.execute_activity("agent.prepare_payload", ..., activity_id="prepare-payload")` with a 60 second `start_to_close_timeout` and `AGENT_ACTIVITY_RETRY`, inside `execution_control.action()`. A durable continuation that carries `prepared_payload` skips it ([AW:1085](../../../../server/services/temporal/agent_workflow.py#L1085)). The activity reads node parameters, resolves templates, picks provider and model, checks that a credential exists (the key itself is resolved later, inside the LLM activity), walks the edges, builds the tool entries, loads the stored conversation, computes the compaction threshold and reads the limits ([AA:1133](../../../../server/services/temporal/agent_activities.py#L1133), result at [AA:1821](../../../../server/services/temporal/agent_activities.py#L1821)).
4. **Derive ids and limits (workflow code).** `execution_id`, `root_execution_id` and `max_iterations` come from the resume marker, the context and the payload ([AW:1113](../../../../server/services/temporal/agent_workflow.py#L1113), [AW:1129](../../../../server/services/temporal/agent_workflow.py#L1129)). Tool entries are deduplicated ([AW:1152](../../../../server/services/temporal/agent_workflow.py#L1152)). Two tools with the same LLM-visible name end the run before any model call: a `failed` progress event and a `success: False` result with `error_type` `DuplicateToolNameError` ([AW:1159](../../../../server/services/temporal/agent_workflow.py#L1159)).
5. **Seed the transcript (workflow code).** Precedence: carried rollover transcript, then stored conversation (this firing's system message first, stored system messages dropped, this firing's prompt last), then the bare opening ([AW:1202](../../../../server/services/temporal/agent_workflow.py#L1202)). `tool_index` maps the LLM-visible name to its tool entry ([AW:1243](../../../../server/services/temporal/agent_workflow.py#L1243)). A `starting` progress event follows ([AW:1286](../../../../server/services/temporal/agent_workflow.py#L1286)).
6. **Loop.** `for iteration in range(iteration_offset, max_iterations)` ([AW:1303](../../../../server/services/temporal/agent_workflow.py#L1303)). Each pass:
   1. Gate: `await self._wait_until_resumed()` ([AW:1304](../../../../server/services/temporal/agent_workflow.py#L1304)), then activity `agent.broadcast_progress` with phase `llm_step` ([AW:1311](../../../../server/services/temporal/agent_workflow.py#L1311)).
   2. Build `llm_payload` in workflow code: the whole `messages` list, the definitions of the tools not marked `llm_hidden`, system message, temperature, `max_tokens`, thinking config, the conversation key, the chat run id and stream, effort, attribution ids ([AW:1322](../../../../server/services/temporal/agent_workflow.py#L1322)).
   3. Activity `agent.execute_llm_step`, `activity_id="llm-step-<iteration+1>"`, inside `execution_control.action()` ([AW:1395](../../../../server/services/temporal/agent_workflow.py#L1395), [AW:3373](../../../../server/services/temporal/agent_workflow.py#L3373)). Inside the activity: decode the messages, return at once if the chat run is stopping, resolve the API key, call the provider through `run_native_llm_step` with a heartbeat every 20 seconds and a stop poll, save the conversation, and return `kind` `final` or `tool_calls` ([AA:505](../../../../server/services/temporal/agent_activities.py#L505)).
   4. Workflow code adds the step's usage to the totals and the reasoning text to `thinking_accumulated` ([AW:1487](../../../../server/services/temporal/agent_workflow.py#L1487)), remembers `turn_start = len(messages)` and appends the assistant message verbatim ([AW:1514](../../../../server/services/temporal/agent_workflow.py#L1514)).
   5. Branch on the result. A provider stop with `finish_reason` `compaction` and no tool calls is a no-op turn (`continue`) ([AW:1526](../../../../server/services/temporal/agent_workflow.py#L1526)). `final` ends the loop; a lead that owns a team first runs activity `agent.finalize_team` and may get one corrective turn ([AW:1535](../../../../server/services/temporal/agent_workflow.py#L1535)). Any other `kind` than `tool_calls` logs an error and ends the loop ([AW:1572](../../../../server/services/temporal/agent_workflow.py#L1572)).
   6. Tool batch (section 4): gate ([AW:1581](../../../../server/services/temporal/agent_workflow.py#L1581)), Task Manager preflights started together ([AW:2059](../../../../server/services/temporal/agent_workflow.py#L2059)), the first window of delegation children started ([AW:2173](../../../../server/services/temporal/agent_workflow.py#L2173)), then one sequential pass over the calls ([AW:2187](../../../../server/services/temporal/agent_workflow.py#L2187)). Each call ends with one tool message appended to `messages`.
   7. After the batch: a runtime hook ([AW:2857](../../../../server/services/temporal/agent_workflow.py#L2857)), permit re-acquire for a delegated child that yielded its permit ([AW:2859](../../../../server/services/temporal/agent_workflow.py#L2859)), and `_persist_turn(interim=True)`, which returns without scheduling anything because the assistant text of an interim turn is empty ([AW:2877](../../../../server/services/temporal/agent_workflow.py#L2877), [AW:3488](../../../../server/services/temporal/agent_workflow.py#L3488)).
   8. A tool result that needs the owner (`blocked_error`) ends the run here with a failure result ([AW:2885](../../../../server/services/temporal/agent_workflow.py#L2885)).
   9. Transcript pressure and compaction (section 7): pure functions in workflow code, plus activity `agent.compact_context` when triggered ([AW:2926](../../../../server/services/temporal/agent_workflow.py#L2926), [AW:3018](../../../../server/services/temporal/agent_workflow.py#L3018)).
   10. Continue-as-new check ([AW:3178](../../../../server/services/temporal/agent_workflow.py#L3178)).
7. **Finish.** At the iteration cap the `for ... else` branch runs ([AW:3261](../../../../server/services/temporal/agent_workflow.py#L3261)). The result payload is built in workflow code ([AW:3283](../../../../server/services/temporal/agent_workflow.py#L3283)), then activities `agent.store_output` ([AW:3301](../../../../server/services/temporal/agent_workflow.py#L3301)), `agent.skill.clear` ([AW:3315](../../../../server/services/temporal/agent_workflow.py#L3315)), for a managed employee lead `employee.job.deliver` ([AW:3323](../../../../server/services/temporal/agent_workflow.py#L3323)), and a `completed` progress event ([AW:3332](../../../../server/services/temporal/agent_workflow.py#L3332)). The workflow returns `{"success": True, "result": result_payload}` ([AW:3341](../../../../server/services/temporal/agent_workflow.py#L3341)).

What is workflow code and what is an activity, in one view:

| Part | Where it runs | Evidence |
|---|---|---|
| Parameter and edge resolution, tool building, conversation load | activity `agent.prepare_payload` | [AA:1133](../../../../server/services/temporal/agent_activities.py#L1133) |
| Building the request message list, appending assistant and tool messages | workflow code | [AW:1202](../../../../server/services/temporal/agent_workflow.py#L1202), [AW:1519](../../../../server/services/temporal/agent_workflow.py#L1519), [AW:2841](../../../../server/services/temporal/agent_workflow.py#L2841) |
| Model call, API key resolution, text streaming, conversation save | activity `agent.execute_llm_step` | [AA:565](../../../../server/services/temporal/agent_activities.py#L565), [AA:652](../../../../server/services/temporal/agent_activities.py#L652) |
| Tool-call routing, payload building, result serialisation and capping | workflow code | [AW:2213](../../../../server/services/temporal/agent_workflow.py#L2213), [AW:2322](../../../../server/services/temporal/agent_workflow.py#L2322), [AW:2601](../../../../server/services/temporal/agent_workflow.py#L2601) |
| Tool execution | activity (or child workflow for delegation) | [AW:151](../../../../server/services/temporal/agent_workflow.py#L151) |
| Clearing and clipping old tool results, the compaction decision | workflow code (pure functions) | [agent_context_pressure.py:1](../../../../server/services/temporal/agent_context_pressure.py#L1) |
| Summarisation | activity `agent.compact_context` | [AA:2788](../../../../server/services/temporal/agent_activities.py#L2788) |
| Progress broadcasts | activity `agent.broadcast_progress` | [AW:3406](../../../../server/services/temporal/agent_workflow.py#L3406) |

## 4. Tool-call dispatch, case by case

### 4.1 The common path before the branch

The batch reads `calls = step_result.get("calls") or []` ([AW:1582](../../../../server/services/temporal/agent_workflow.py#L1582)) and walks it with `for call_index, call in enumerate(calls):` ([AW:2187](../../../../server/services/temporal/agent_workflow.py#L2187)). For each call, in order:

1. A runtime hook may reject the call. The base class hook is a no-op; `PiCodingAgentWorkflow` overrides it ([AW:2188](../../../../server/services/temporal/agent_workflow.py#L2188), [AW:3400](../../../../server/services/temporal/agent_workflow.py#L3400)).
2. A call whose arguments were not valid JSON (`parse_error`) gets a tool message with the parse error and the raw arguments. Nothing is scheduled ([AW:2193](../../../../server/services/temporal/agent_workflow.py#L2193)).
3. `tool_info = tool_index.get(call.get("name", ""))`. An unknown name gets the tool message `Error: tool '<name>' is not connected to this agent.` Nothing is scheduled ([AW:2213](../../../../server/services/temporal/agent_workflow.py#L2213)).
4. `is_delegation = tool_name.startswith("delegate_to_")` ([AW:2254](../../../../server/services/temporal/agent_workflow.py#L2254)). A delegation call with neither `task` nor `context`, or past the depth limit, gets an error tool message ([AW:2256](../../../../server/services/temporal/agent_workflow.py#L2256)).
5. `tool_node_data` is the entry's saved `parameters` with the model's arguments over them (for delegation: the task mapped to `system_message` and `prompt`). The canvas (`nodes`, `edges`) is forwarded only when the plugin declares `needs_canvas` or the call is a delegation ([AW:2284](../../../../server/services/temporal/agent_workflow.py#L2284)).
6. `tool_payload` is built ([AW:2322](../../../../server/services/temporal/agent_workflow.py#L2322)). Fields are listed in section 5. The model's arguments also travel unmerged as `tool_args`.
7. Activity `agent.broadcast_progress`, phase `executing_tool` ([AW:2397](../../../../server/services/temporal/agent_workflow.py#L2397)).
8. The branch (4.2 and 4.3).

The workflow does not validate arguments against the tool's schema. Validation happens inside the tool activity (INFERRED from the absence of any schema check between [AW:2193](../../../../server/services/temporal/agent_workflow.py#L2193) and [AW:2533](../../../../server/services/temporal/agent_workflow.py#L2533); the in-process loop validates in `_validated_tool_args`, [agent_runtime.py:178](../../../../server/services/agent_runtime.py#L178)).

### 4.2 Cases

Queue column: "own" means no `task_queue` is passed, so the activity runs on the workflow's task queue (`TEMPORAL_TASK_QUEUE`, `machina-tasks` in [.env.template:95](../../../../.env.template#L95)). The main-queue worker registers every plugin activity (without the Browser in distributed mode) and every `agent.*` activity ([worker.py:648](../../../../server/services/temporal/worker.py#L648), [worker.py:638](../../../../server/services/temporal/worker.py#L638)).

| # | Kind of call | Primitive and name | Task queue | Timeouts and retry | Where the branch is taken |
|---|---|---|---|---|---|
| 1 | Plugin tool: a `ToolNode`, or an `ActionNode` with `usable_as_tool` (dual-purpose) | `workflow.execute_activity("node.<type>.v<version>", args=[tool_payload], activity_id="tool-<tool node id>-<iteration+1>-<call index+1>")` | Durable: the plugin's queue when the frozen `temporal_worker_pool_enabled` is true, else own. Legacy: own | The plugin class's `start_to_close_timeout`, `heartbeat_timeout`, `retry_policy` (defaults 10 min, 2 min, 3 attempts). Durable runs read them from the frozen `activity_policy` and add `WAIT_CANCELLATION_COMPLETED` | Name at [AW:2391](../../../../server/services/temporal/agent_workflow.py#L2391); call at [AW:2527](../../../../server/services/temporal/agent_workflow.py#L2527); options at [AW:123](../../../../server/services/temporal/agent_workflow.py#L123) |
| 2 | MCP connector tool (`mcpConnector`) | Same as 1, activity `node.mcpConnector.v<version>`. One tool entry per binding; the binding's locked settings (`mcp_tool`, `mcp_ask`, labels) are in the entry's `parameters` | As 1; the class declares `rest-api` | As 1 (class defaults) | Entries built by `node_tools` ([agent_bindings.py:10](../../../../server/services/agent_bindings.py#L10)); bindings at [mcp_connector.py:92](../../../../server/nodes/mcp/mcp_connector.py#L92) |
| 3 | Skill load (`Skill` tool, node type `_builtin_skill`) | `workflow.execute_activity("agent.skill.invoke", ...)` | Own (the policy records no queue for a tool without a plugin class) | `BaseNode` defaults: 10 min, 2 min, 3 attempts | [AW:2392](../../../../server/services/temporal/agent_workflow.py#L2392); policy at [AA:67](../../../../server/services/temporal/agent_activities.py#L67); body at [AA:2044](../../../../server/services/temporal/agent_activities.py#L2044) |
| 4 | `writeTodos` | Same as 1, activity `node.writeTodos.v<version>`. No special branch | As 1; the class declares `machina-default` | Class defaults | [write_todos/__init__.py:52](../../../../server/nodes/tool/write_todos/__init__.py#L52) |
| 5 | Task Manager `assign_task` | Preflight: `workflow.start_activity("node.taskManager.v<version>", activity_id="task-manager-preflight-<iteration+1>-<call index+1>")` for every such call of the turn, then one `asyncio.gather`. The result is reused when the sequential pass reaches the call | As 1; the class declares `machina-default` | Class policy, or the frozen policy in controlled and Pi runs ([AW:2108](../../../../server/services/temporal/agent_workflow.py#L2108)) | [AW:2059](../../../../server/services/temporal/agent_workflow.py#L2059), [AW:2127](../../../../server/services/temporal/agent_workflow.py#L2127), [AW:2519](../../../../server/services/temporal/agent_workflow.py#L2519) |
| 6 | Task Manager, any other operation | Same as 1 | As 1 | As 1 | [AW:2523](../../../../server/services/temporal/agent_workflow.py#L2523) |
| 7 | Task Manager result that carries `delegation_request` | Workflow code runs bookkeeping activities, then `workflow.start_child_workflow("DelegatedTaskWorkflow", id="<child id>-runner", parent_close_policy=ABANDON)`. The lead does not await the runner's result; the tool result says `queued` | Inherited | No workflow timeouts | [AW:2557](../../../../server/services/temporal/agent_workflow.py#L2557), [AW:1915](../../../../server/services/temporal/agent_workflow.py#L1915), [AW:2045](../../../../server/services/temporal/agent_workflow.py#L2045) |
| 8 | `delegate_to_*` whose target type is in `AGENT_WORKFLOW_TYPES` | `workflow.start_child_workflow(agent_workflow_name(...), args=[child_context], id="<agent workflow id>-delegate-<tool node id>-<iteration+1>-<call index+1>")`, started before the sequential pass in a sliding window; `tool_result = await handle` when the pass reaches the call | Inherited | No workflow timeouts | Indices at [AW:1598](../../../../server/services/temporal/agent_workflow.py#L1598); start at [AW:1886](../../../../server/services/temporal/agent_workflow.py#L1886); await at [AW:2420](../../../../server/services/temporal/agent_workflow.py#L2420) |
| 9 | `delegate_to_*` whose target type is not in the set (`rlm_agent`, `claude_code_agent`) | Same as 1: the target's per-type activity, with the task mapped into `node_data` and the canvas forwarded | As 1 | The target plugin's policy | [AW:2284](../../../../server/services/temporal/agent_workflow.py#L2284), then the `else` at [AW:2513](../../../../server/services/temporal/agent_workflow.py#L2513) |
| 10 | Coding Agent workspace tool (entry flagged `virtual_workspace`, Pi runtime only) | `workflow.execute_activity("coding.workspace_tool", ...)` from the entry's `activity_name` | From the underlying class's frozen policy | Underlying class's policy | [AW:2392](../../../../server/services/temporal/agent_workflow.py#L2392); [coding_workspace_tools.py:59](../../../../server/services/coding_workspace_tools.py#L59); body at [AA:1894](../../../../server/services/temporal/agent_activities.py#L1894) |
| 11 | Browser tool | Same as 1, plus `_browser_owner` and `_browser_task_id` in the payload | The owner's queue when the policy has `owner_routing` | Browser plugin policy (35 min, [browser/__init__.py:414](../../../../server/nodes/browser/browser/__init__.py#L414)) | [AW:2375](../../../../server/services/temporal/agent_workflow.py#L2375), [AW:168](../../../../server/services/temporal/agent_workflow.py#L168) |
| 12 | A call held for the owner that the turn waits on | Child workflow `ApprovalWorkflow` (id `approval-<approval id>`), then activities `approvals.claim_send`, the tool's own activity with one attempt, `approvals.record_outcome` | Inherited; activities own | See 4.5 | [AW:2534](../../../../server/services/temporal/agent_workflow.py#L2534) |
| 13 | Malformed arguments, unknown tool, rejected or invalid delegation | None: a tool message is built in workflow code | n/a | n/a | [AW:2188](../../../../server/services/temporal/agent_workflow.py#L2188) to [AW:2283](../../../../server/services/temporal/agent_workflow.py#L2283) |

In the base class no tool call is executed by plain workflow code: every executed call is an activity or a child workflow. (The Pi subclass's hooks were not read.)

The legacy single dispatcher `execute_node_activity` is never scheduled by `AgentWorkflow`; the tool activity name is always the per-type name, whatever `per_type_dispatch_enabled` says ([AW:2391](../../../../server/services/temporal/agent_workflow.py#L2391)). The in-process dispatcher `execute_tool` ([handlers/tools.py:90](../../../../server/services/handlers/tools.py#L90)) is not on this path either.

### 4.3 The quoted branch

Activity name selection ([AW:2391](../../../../server/services/temporal/agent_workflow.py#L2391)):

```python
                tool_activity_name = (
                    tool_info["activity_name"] if tool_info.get("virtual_workspace") else "agent.skill.invoke"
                    if tool_info["node_type"] == "_builtin_skill"
                    else f"node.{tool_info['node_type']}.v{tool_info['version']}"
                )
```

Child workflow or activity ([AW:2420](../../../../server/services/temporal/agent_workflow.py#L2420) and [AW:2513](../../../../server/services/temporal/agent_workflow.py#L2513)):

```python
                        if is_delegation and tool_info["node_type"] in AGENT_WORKFLOW_TYPES:
                            handle = delegation_handles[call_index]
                            try:
                                tool_result = await handle
```

```python
                        else:
                            tool_activity_id = _tool_activity_id(
                                tool_info["tool_node_id"],
                                iteration,
                                call_index,
                            )
                            if call_index in task_manager_preflight_results:
                                tool_result = task_manager_preflight_results[call_index]
                                if isinstance(tool_result, BaseException):
                                    raise tool_result
                            else:
                                await self._wait_until_resumed()
                                await self._execution_control.begin_action()
                                tool_action_active = self._execution_control.enabled
                                tool_result = await _execute_plugin_tool_activity(
```

Activity options ([AW:137](../../../../server/services/temporal/agent_workflow.py#L137)); the function ends with `workflow.execute_activity(activity_name, args=[tool_payload], activity_id=activity_id, **options)` ([AW:151](../../../../server/services/temporal/agent_workflow.py#L151)):

```python
    policy = (tool_info or {}).get("activity_policy")
    if (context.get("execution_control_version") == 1 or context.get("native_workspace_version") == 1
            or context.get("browser_runtime_version") == 1 or context.get("browser_routing_version") == 1
            or uses_pi(context.get("node_type"), context)) and policy:
        options = _frozen_tool_activity_options(policy, context)
        tool_payload = {**tool_payload, **_inherited_scope(context), "user_id": str(context.get("user_id") or "owner")}
    else:
        options = _declared_tool_options(tool_payload["node_type"])
```

The frozen options set `task_queue` only when `context["temporal_worker_pool_enabled"]` is true or the policy has `owner_routing` ([AW:159](../../../../server/services/temporal/agent_workflow.py#L159)). The legacy options never set a queue, except for classes flagged `workspace_task` behind the patch `workspace-task-activity-policy-v1` ([AW:146](../../../../server/services/temporal/agent_workflow.py#L146)).

### 4.4 Inside the per-type activity

`BaseNode.as_activity` builds the activity named `node.<type>.v<version>` ([base.py:1224](../../../../server/services/plugin/base.py#L1224)). In order, for an agent's tool call:

1. A call of a chat run the owner stopped is answered with `STOPPED_RESULT` without running ([base.py:1288](../../../../server/services/plugin/base.py#L1288), [steps.py:40](../../../../server/services/chat/steps.py#L40)).
2. A chat step starts when the payload carries `chat_stream` and a `tool_call_id` ([base.py:1314](../../../../server/services/plugin/base.py#L1314), [steps.py:89](../../../../server/services/chat/steps.py#L89)).
3. The approval check may answer instead of running (held, refused, restricted) ([base.py:1319](../../../../server/services/plugin/base.py#L1319), [tool_calls.py:244](../../../../server/services/approvals/tool_calls.py#L244)).
4. The redo guard: on attempt 2 or later, a call that is not safe to repeat answers `OutcomeUnknown` or `SendOutcomeUnknown` instead of running again ([base.py:1346](../../../../server/services/plugin/base.py#L1346), [tool_calls.py:607](../../../../server/services/approvals/tool_calls.py#L607)). The safe list is by node type and operation; a type that is not listed is not safe ([repeats.py:26](../../../../server/services/approvals/repeats.py#L26)).
5. A background task heartbeats every 30 seconds when the class's `start_to_close_timeout` exceeds its `heartbeat_timeout` ([base.py:1384](../../../../server/services/plugin/base.py#L1384)).
6. `workflow_service.execute_node(...)` runs the node through `NodeExecutor` ([base.py:1451](../../../../server/services/plugin/base.py#L1451), [node_executor.py:130](../../../../server/services/node_executor.py#L130)). The registry handler is the plugin's adapter: a `ToolNode` with `tool_args` in its context goes through `execute_as_tool`, any other node through `execute` with the merged parameters ([base.py:665](../../../../server/services/plugin/base.py#L665)).
7. A transient failure reported by the node body (a rate limit or a server error) raises a benign retryable `ApplicationError`, so the attempt runs again under the plugin's retry policy, only when the call is repeatable and not a send ([base.py:1484](../../../../server/services/plugin/base.py#L1484), [base.py:231](../../../../server/services/plugin/base.py#L231)).
8. Any other node failure comes back as a result dict (`success: False`), not as an exception: `NodeExecutor.execute` catches `Exception` and returns an error envelope ([node_executor.py:204](../../../../server/services/node_executor.py#L204)). Temporal therefore does not retry it.
9. The activity broadcasts node status (`executing`, then `success` or `error`) and node output for the tool node ([base.py:1368](../../../../server/services/plugin/base.py#L1368), [base.py:1520](../../../../server/services/plugin/base.py#L1520)).

`agent.skill.invoke` does none of this: it calls `execute_skill_tool` directly ([AA:2044](../../../../server/services/temporal/agent_activities.py#L2044)).

### 4.5 Held calls and approvals

Two regimes, selected by the generation flag `approval_wait_version` ([tool_calls.py:262](../../../../server/services/approvals/tool_calls.py#L262)). Start writes `{"execution_control_version": 1, "pi_harness_version": 1}` only ([deployment/control.py:168](../../../../server/services/deployment/control.py#L168)), so the first regime is the one employees run today.

**Draft (flag absent).** The check applies to plugins that declare an approval spec. While the workflow asks first and the call sends, `hold` writes a `tool_call` row and the activity returns the held marker: `result.status` `waiting_for_owner`, an `approval_id`, and a message telling the model it is not sent yet ([tool_calls.py:264](../../../../server/services/approvals/tool_calls.py#L264), [tool_calls.py:175](../../../../server/services/approvals/tool_calls.py#L175)). The workflow treats the marker as an ordinary tool result and the turn goes on. When the owner presses Send, `start_send` starts `ApprovedToolCallWorkflow` (id `approval-send-<row id>-r<revision>`, main task queue) ([execution.py:69](../../../../server/services/approvals/execution.py#L69)). That workflow sleeps until the Undo window closes, then runs `run_approved_call` ([approved_tool_call_workflow.py:104](../../../../server/services/temporal/approved_tool_call_workflow.py#L104)).

**Wait in the turn (flag 1).** The class of the call and the workflow's mode decide; every tool an agent calls is judged, with or without a spec ([tool_calls.py:293](../../../../server/services/approvals/tool_calls.py#L293), [classes.py:502](../../../../server/services/approvals/classes.py#L502)). The workflow marks a call it will wait on:

```python
                waits_for_owner = (
                    context.get("approval_wait_version") == 1
                    and self._execution_control.enabled
                    and bool(payload.get("chat_stream"))
                )
                if waits_for_owner:
                    tool_payload["approval_wait"] = True
```

([AW:2383](../../../../server/services/temporal/agent_workflow.py#L2383)). So only the agent that answers a chat run waits; every other held call is a draft. A held row written with `approval_wait` has `runtime` `turn` ([tool_calls.py:461](../../../../server/services/approvals/tool_calls.py#L461)). On a held result:

```python
                                held_for = _held_for_owner(tool_result) if waits_for_owner else None
                                if held_for:
                                    # Outside any admitted action, so Stop
                                    # completes while the owner decides.
                                    if tool_action_active:
                                        self._execution_control.end_action()
                                        tool_action_active = False
                                    answer = await self._await_owner(held_for)
                                    if answer.get("status") == "approved":
```

([AW:2534](../../../../server/services/temporal/agent_workflow.py#L2534)).

- `_await_owner` starts the child `ApprovalWorkflow` with input `{"approval_id": ...}`, id `approval-<approval id>`, `ParentClosePolicy.TERMINATE`, `ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED`, `WorkflowIDReusePolicy.ALLOW_DUPLICATE`, inside `child_start()`, and awaits it ([AW:871](../../../../server/services/temporal/agent_workflow.py#L871)).
- `ApprovalWorkflow` loops: activity `approvals.read` (30 seconds, `BOOKKEEPING_RETRY`); if the row is still pending, `workflow.wait_condition(lambda: self._nudged, timeout=max(due - workflow.now(), MIN_WAIT))`; then read again. Its only message is the Signal `nudge`. Cancelled, it marks a pending row cancelled in a shielded activity and re-raises ([approval_workflow.py:43](../../../../server/services/temporal/approval_workflow.py#L43)).
- The nudge is sent by an approval listener on every change to a turn row ([waiting.py:79](../../../../server/services/approvals/waiting.py#L79), [approvals/__init__.py:41](../../../../server/services/approvals/__init__.py#L41)). The row is the only record; the child returns `{status, revision, note?, by_message?}` ([waiting.py:155](../../../../server/services/approvals/waiting.py#L155)).
- Approved: after `_wait_until_resumed()` and `begin_action()`, `_run_allowed_call` calls `run_approved_call` with the tool's own activity name, the tool options with `retry_policy` replaced by `SEND_ONCE` (one attempt), an overlay that lays the claim's `node_data`, `tool_args` and `approval_execution` over the payload, and activity ids `approval-claim-<id>`, `approval-send-<id>`, `approval-record-<id>` ([AW:887](../../../../server/services/temporal/agent_workflow.py#L887)). `run_approved_call` is three activities in sequence: `approvals.claim_send`, the tool activity, and a shielded `approvals.record_outcome` ([approved_tool_call_workflow.py:39](../../../../server/services/temporal/approved_tool_call_workflow.py#L39)).
- Any other answer becomes a tool result built by `_owner_answer`: declined (with the owner's note or message), expired, or not run ([AW:457](../../../../server/services/temporal/agent_workflow.py#L457)). The loop continues.
- What happens to the turn meanwhile: the sequential pass is blocked at that call, so later calls of the same model message have not started. The workflow holds no admitted action, so a Stop checkpoint completes ([AW:871](../../../../server/services/temporal/agent_workflow.py#L871)). A message the owner sends meanwhile declines the call ([waiting.py:118](../../../../server/services/approvals/waiting.py#L118)). The row expires after 168 hours ([contract.py:27](../../../../server/services/approvals/contract.py#L27)).

### 4.6 After the call: serialise, cap, rebind, append

- `_serialise_tool_result` returns a string as is; unwraps `{"success", "result", ...}` to its `result`; and JSON-dumps anything else with `default=str` ([AW:3528](../../../../server/services/temporal/agent_workflow.py#L3528)). An error envelope has no `result` key ([node_executor.py:64](../../../../server/services/node_executor.py#L64)), so the whole envelope is dumped.
- The model's copy is cut to `tool_result_max_chars` when the limit is set, the call is not a delegation and `tool_output_is_capped(node_type)` is true ([AW:2601](../../../../server/services/temporal/agent_workflow.py#L2601)).
- Activity `agent.broadcast_progress`, phase `tool_completed` ([AW:2612](../../../../server/services/temporal/agent_workflow.py#L2612)).
- Hot rebind: a result dict with a non-empty `operations` list, and `auto_rebind_tools` true, schedules activity `agent.refresh_tools` (`activity_id="refresh-tools-<tool node id>-<iteration+1>-<call index+1>"`, 30 seconds, `AGENT_ACTIVITY_RETRY`). The returned tool entries are appended to `tools` and `tool_index`, and are visible to the next LLM step ([AW:2643](../../../../server/services/temporal/agent_workflow.py#L2643), [AA:1910](../../../../server/services/temporal/agent_activities.py#L1910)).
- A result dict with `error` and `requires_user_action is True` sets `blocked_error` behind the patch `agent-user-action-error-v1` ([AW:2579](../../../../server/services/temporal/agent_workflow.py#L2579)).
- One tool message is appended per call: `role` `tool`, `content` the string, `tool_call_id`, `name` ([AW:245](../../../../server/services/temporal/agent_workflow.py#L245)). A controlled run appends right after the call, before any hot rebind ([AW:2636](../../../../server/services/temporal/agent_workflow.py#L2636)); any other run appends after the `try` block ([AW:2841](../../../../server/services/temporal/agent_workflow.py#L2841)). In a controlled run that is not Pi, a Task Manager preflight result is appended at gather time, before the results of calls that precede it in model order ([AW:2134](../../../../server/services/temporal/agent_workflow.py#L2134)).
- The tool message carries no image blocks. `image_blocks_from_tool_result` has one production call site, the in-process loop ([agent_runtime.py:440](../../../../server/services/agent_runtime.py#L440)).

### 4.7 Parallelism

- Ordinary tool calls: one at a time. `_execute_plugin_tool_activity` is awaited inside the sequential `for` ([AW:2187](../../../../server/services/temporal/agent_workflow.py#L2187), [AW:2527](../../../../server/services/temporal/agent_workflow.py#L2527)). There is no setting that makes them concurrent.
- Task Manager `assign_task` calls of one turn: all preflight activities are started with `workflow.start_activity` and awaited together ([AW:2111](../../../../server/services/temporal/agent_workflow.py#L2111)):

```python
                    preflight_results = await asyncio.gather(
                        *task_manager_preflight_handles, return_exceptions=True
                    )
```

  The follow-up delegations run as `asyncio.create_task(_run_task_manager_delegation(...))` and are awaited in call order ([AW:2165](../../../../server/services/temporal/agent_workflow.py#L2165), [AW:2568](../../../../server/services/temporal/agent_workflow.py#L2568)). No cap is applied to the number of preflights.
- Direct `delegate_to_*` calls: up to `max_concurrent_subagents` children are started before the sequential pass, and each finished child starts the next one ([AW:2175](../../../../server/services/temporal/agent_workflow.py#L2175), [AW:2507](../../../../server/services/temporal/agent_workflow.py#L2507), [AW:2853](../../../../server/services/temporal/agent_workflow.py#L2853)). The cap is `max(1, int(payload.get("max_concurrent_subagents") or 3))` ([AW:1595](../../../../server/services/temporal/agent_workflow.py#L1595)). With a team, each child also takes a root-wide permit through the heartbeating activity `agent.acquire_subagent_permit` ([AW:1831](../../../../server/services/temporal/agent_workflow.py#L1831)). Children run while earlier ordinary calls of the same turn execute.
- Depth: `max_delegation_depth = min(2, int(payload.get("max_delegation_depth") or 2))` ([AW:1592](../../../../server/services/temporal/agent_workflow.py#L1592)).
- Worker slots are the outer bound: the main worker has `max_concurrent_activities` 100 (the manager's `pool_size`) and `max_concurrent_workflow_tasks` 10 ([worker.py:119](../../../../server/services/temporal/worker.py#L119), [worker.py:251](../../../../server/services/temporal/worker.py#L251)); pool queues have their own slots and rate limits ([worker.py:371](../../../../server/services/temporal/worker.py#L371), [worker.py:388](../../../../server/services/temporal/worker.py#L388)).

### 4.8 Contrast: the in-process loop

| Aspect | Temporal `AgentWorkflow` | In-process `run_native_agent_loop` |
|---|---|---|
| Tool calls in one message | Sequential ([AW:2187](../../../../server/services/temporal/agent_workflow.py#L2187)) | Sequential ([agent_runtime.py:361](../../../../server/services/agent_runtime.py#L361)) |
| Argument validation | In the tool activity | In the loop, against `args_schema` ([agent_runtime.py:370](../../../../server/services/agent_runtime.py#L370)) |
| Tool dispatch | Activity or child workflow per call | `tool_executor` callback, which reaches `execute_tool` ([handlers/tools.py:90](../../../../server/services/handlers/tools.py#L90)) |
| Conversation save | Inside the LLM step, after the provider answered ([AA:652](../../../../server/services/temporal/agent_activities.py#L652)) | After the assistant append and after the tool results ([agent_runtime.py:303](../../../../server/services/agent_runtime.py#L303), [agent_runtime.py:449](../../../../server/services/agent_runtime.py#L449)) |
| Usage summed | `response.usage` ([AA:644](../../../../server/services/temporal/agent_activities.py#L644)) | `response.billing_usage or response.usage` ([agent_runtime.py:291](../../../../server/services/agent_runtime.py#L291)) |
| At the iteration cap | Returns success with a placeholder response ([AW:3274](../../../../server/services/temporal/agent_workflow.py#L3274)) | Appends a recursion-limit message, `truncated: True` ([agent_runtime.py:469](../../../../server/services/agent_runtime.py#L469)) |
| Owner-only tool error | Returns a failure result ([AW:2910](../../../../server/services/temporal/agent_workflow.py#L2910)) | Raises `NodeUserError` ([agent_runtime.py:451](../../../../server/services/agent_runtime.py#L451)) |
| Image blocks from `llm_media` | Not attached | Attached ([agent_runtime.py:440](../../../../server/services/agent_runtime.py#L440)) |

## 5. Inputs, outputs and state

### 5.1 Workflow input (`context`)

Built by `MachinaWorkflow` per node ([workflow.py:729](../../../../server/services/temporal/workflow.py#L729)): `node_id`, `node_type`, `node_data`, `inputs`, `workflow_id`, `workflow_slug`, `tenant_id`, `session_id`, `execution_id`, `nodes` and `edges` (the full canvas), `parameter_snapshot`, `pre_executed`, `trigger_output`; in a controlled run the `ExecutionControl.carry()` keys; `graphVersion`, `generation`, `root_execution_id`, `data_scope_id`, `context_execution_id`, `context_session_id`; `user_id`; `run_scope`; `temporal_worker_pool_enabled`; the generation flags. A delegation child also gets `invocation: {task, context}`, `parent_node_id`, `delegation_depth`, `team_id`, `team_task_id`, `trace_id` ([AW:1748](../../../../server/services/temporal/agent_workflow.py#L1748)). A continuation adds `_agent_resume` (5.6).

### 5.2 Prepared payload (result of `agent.prepare_payload`)

Keys ([AA:1821](../../../../server/services/temporal/agent_activities.py#L1821)): `runtime_behavior_version` (when set), `llm_warning_seconds`, `workspace_dir`, `agent_label`, `chat_run_id`, `chat_stream`, `node_id`, `node_type`, `pi_harness_version`, `tool_mode`, `workflow_id`, `session_id`, `parameter_snapshot`, `browser_bindings`, `browser_runtime_version`, `employee_job_id`, `employee_team_plan`, `employee_runtime_delivery`, `provider`, `model`, `llm_attempt_timeout_seconds`, `effort`, `max_tokens`, `temperature`, `system_message`, `user_prompt`, `user_images`, `tools`, `memory_node_id`, `memory_content`, `memory_window_size`, `context_descriptor`, `conversation`, `conversation_key`, `max_iterations`, `thinking_config`, `compaction_threshold`, `tool_result_max_chars`, `transcript_budget_bytes`, `context_pressure_version`, `auto_rebind_tools`, `max_concurrent_subagents`, `max_delegation_depth`, `team_id`, `team_execution_id`, `team_task_id`, `owns_execution_team`, `root_execution_id`.

No API key is in the payload. `agent.prepare_payload` only checks that one exists ([AA:1328](../../../../server/services/temporal/agent_activities.py#L1328)); the LLM activity resolves it ([AA:176](../../../../server/services/temporal/agent_activities.py#L176)).

### 5.3 A tool entry (`payload["tools"][i]`)

Built per connected node, or per binding ([AA:1624](../../../../server/services/temporal/agent_activities.py#L1624)): `name` (LLM-visible, the dispatch key), `definition` (`{name, description, parameters}` JSON schema, [AA:352](../../../../server/services/temporal/agent_activities.py#L352)), `node_type`, `version`, `task_queue`, `tool_node_id`, `binding_key`, `parameters`, `tool_info` (the raw connection entry), `llm_hidden`; and, only when the context has `execution_control_version` 1, `native_workspace_version` 1, `browser_routing_version` 1, `pi_harness_version` 1 or the node is a `browser_agent`: `activity_policy` and `needs_canvas` ([AA:1645](../../../../server/services/temporal/agent_activities.py#L1645)). `activity_policy` holds `start_to_close_seconds`, `heartbeat_seconds`, `task_queue` and the retry policy as numbers ([AA:67](../../../../server/services/temporal/agent_activities.py#L67)).

Which nodes become entries: the tools on `input-tools` (minus web search tools when the owner turned Web off), the progressive `Skill` tool when standard skills are connected, and for a team lead the teammates as `delegate_to_*` entries with `llm_hidden` true ([AA:1603](../../../../server/services/temporal/agent_activities.py#L1603), [AA:1453](../../../../server/services/temporal/agent_activities.py#L1453), [AA:1652](../../../../server/services/temporal/agent_activities.py#L1652)). Name and schema come from `_build_tool_from_node` and `_get_tool_schema` ([ai.py:2414](../../../../server/services/ai.py#L2414), [ai.py:2600](../../../../server/services/ai.py#L2600)). An agent connected as a tool gets the `(task, context)` schema ([ai.py:2653](../../../../server/services/ai.py#L2653)); an agent class's default tool name is `delegate_to_<type>` ([base.py:488](../../../../server/services/plugin/base.py#L488)).

### 5.4 LLM step input and output

Input ([AW:1323](../../../../server/services/temporal/agent_workflow.py#L1323)): `node_id`, `workflow_id`, `provider`, `iteration`, `max_iterations`, `model`, `messages`, `tools` (definitions only), `system_message`, `temperature`, `max_tokens`, `thinking_config`, `conversation_key`, `include_finish_reason`, `chat_run_id`, `chat_stream`, `effort`, `attribution`, and with `runtime_behavior_version` 1 also `llm_warning_seconds`, `agent_label`, `runtime_scope`. There is no `context_management` key.

Output ([AA:673](../../../../server/services/temporal/agent_activities.py#L673)):

- `{"kind": "tool_calls", "assistant_message": <MessageWire>, "calls": [{"id", "name", "args", "raw_arguments"?, "parse_error"?}], "usage": {...}, "finish_reason"?}`
- `{"kind": "final", "assistant_message": <MessageWire>, "content": str, "thinking": str or None, "usage": {...}, "finish_reason"?}`
- A stopped step returns `kind` `final` with `stopped: True` and `finish_reason` `stopped` ([AA:476](../../../../server/services/temporal/agent_activities.py#L476)).

`usage` keys: `input_tokens`, `output_tokens`, `total_tokens`, `cache_creation_tokens`, `cache_read_tokens`, `reasoning_tokens` ([protocol.py:237](../../../../server/services/llm/protocol.py#L237)). A MessageWire dict has `role`, `content`, `blocks`, `tool_calls`, `tool_call_id`, `name`, `provider_state` ([protocol.py:582](../../../../server/services/llm/protocol.py#L582)).

### 5.5 Tool activity input and output

Input `tool_payload` ([AW:2322](../../../../server/services/temporal/agent_workflow.py#L2322)): `node_id` (the tool node's id), `node_type`, `node_data` (settings with the model's arguments merged over them), `tool_args` (the model's arguments alone), `parameter_snapshot`, `inputs: {}`, `workflow_id`, `session_id`, `execution_id`, `parent_node_id` and `team_lead_node_id` (the agent), `team_id`, `employee_job_id`, `team_task_id`, `root_execution_id`, `delegation_depth`, `nodes`, `edges`, `auto_rebind_tools`, `tool_call_id`, the correlation fields `invoking_agent_node_id`, `agent_iteration`, `tool_call_index` ([AW:486](../../../../server/services/temporal/agent_workflow.py#L486)), `chat_run_id`, `chat_stream`, and when set `approval_wait`, `_browser_owner`, `_browser_task_id`, `runtime_behavior_version`, `pi_harness_version`. Behind the patch `employee-task-manager-tool-scope-v2` the inherited scope keys and `outputs` are added ([AW:2378](../../../../server/services/temporal/agent_workflow.py#L2378), [AW:310](../../../../server/services/temporal/agent_workflow.py#L310)).

Output: a JSON-serialisable dict. An `ActionNode` returns the envelope `{"success", "result", "node_id", "node_type", "execution_id", "timestamp", ...}`; a `ToolNode` returns its flat result plus the same identity keys; a failure returns `{"success": False, "error", "error_type", ...}` ([base.py:1465](../../../../server/services/plugin/base.py#L1465), [tool.py:233](../../../../server/services/plugin/tool.py#L233), [base.py:1183](../../../../server/services/plugin/base.py#L1183)). Keys of the result that the loop interprets:

| Key in the tool result | Effect in the workflow | Evidence |
|---|---|---|
| `result.status == "waiting_for_owner"` with an `approval_id` | Held call; the turn waits when it marked the call | [AW:447](../../../../server/services/temporal/agent_workflow.py#L447) |
| `delegation_request` (Task Manager only) | Starts the detached runner | [AW:2557](../../../../server/services/temporal/agent_workflow.py#L2557) |
| `operations` | Hot rebind through `agent.refresh_tools` | [AW:2643](../../../../server/services/temporal/agent_workflow.py#L2643) |
| `error` with `requires_user_action: True` | Ends the run after this turn | [AW:2579](../../../../server/services/temporal/agent_workflow.py#L2579) |
| `screenshot` (Browser, durable runs) | Adds an artifact ref to the final result (at most 32) | [AW:2592](../../../../server/services/temporal/agent_workflow.py#L2592) |
| `_step_detail` | Taken out inside the activity and shown under the chat step | [steps.py:169](../../../../server/services/chat/steps.py#L169) |

A delegated child returns its own workflow result (5.7). The parent turns it into `{"success", "status": "submitted" or "failed", "result": <child response text>, "usage", "error"?, "hint"?, ...}` ([AW:2499](../../../../server/services/temporal/agent_workflow.py#L2499), [AW:3507](../../../../server/services/temporal/agent_workflow.py#L3507)).

### 5.6 State the workflow holds between turns

Local variables of `_run_impl`: `context`, `payload`, `messages` (the full transcript), `tools` and `tool_index` (mutable: hot rebind appends), `usage_total`, `context_usage_total`, `thinking_accumulated`, `browser_artifacts`, `correction_count`, `execution_id`, `root_execution_id`, `max_iterations`, `compaction_threshold`, `tool_output_limit`, `pressure_version`, `transcript_budget` ([AW:1245](../../../../server/services/temporal/agent_workflow.py#L1245), [AW:1262](../../../../server/services/temporal/agent_workflow.py#L1262)). Instance attributes: `_execution_control`, `_control_paused`, `_browser_claims`, `_continuing`, `_parent_node_id`, `_execution_id`, `_root_execution_id`, `_provider`, `_llm_wait_execution_key` ([AW:824](../../../../server/services/temporal/agent_workflow.py#L824), [AW:1132](../../../../server/services/temporal/agent_workflow.py#L1132)).

The resume marker a durable continuation carries ([AW:3197](../../../../server/services/temporal/agent_workflow.py#L3197)):

```python
                            _RESUME_MARKER: {
                                "iteration": iteration + 1,
                                "delegation_correction_count": correction_count,
                                "execution_id": execution_id,
                                "transcript": messages,
                                "usage": usage_total,
                                "context_usage": context_usage_total,
                                "prepared_payload": _carried_payload(payload),
                                "tools": tools,
                                "thinking": thinking_accumulated,
                                "browser_artifacts": browser_artifacts,
                            },
```

`_carried_payload` drops `conversation`, `memory_content` and `parameter_snapshot` ([AW:393](../../../../server/services/temporal/agent_workflow.py#L393)). A legacy continuation carries only `iteration`, `delegation_correction_count`, `execution_id`, `transcript`, `usage`, `context_usage`, and runs `agent.prepare_payload` again ([AW:3245](../../../../server/services/temporal/agent_workflow.py#L3245), [AW:1088](../../../../server/services/temporal/agent_workflow.py#L1088)).

### 5.7 Workflow result

Success: `{"success": True, "result": {"response", "thinking", "model", "provider", "usage", "team_id", "execution_id", "root_execution_id", "artifacts"?}}` ([AW:3283](../../../../server/services/temporal/agent_workflow.py#L3283)). Failure results are returned, not raised: `{"success": False, "error", "error_type", ..., "result": {"iterations", "usage"}}` with `error_type` `DuplicateToolNameError`, `LLMStepError`, `CompactionError`, `NodeUserError` (blocked tool), `EmployeeLeadNoAssignments` or `PiIterationExhausted` ([AW:1179](../../../../server/services/temporal/agent_workflow.py#L1179), [AW:1475](../../../../server/services/temporal/agent_workflow.py#L1475), [AW:3064](../../../../server/services/temporal/agent_workflow.py#L3064), [AW:2910](../../../../server/services/temporal/agent_workflow.py#L2910), [AW:3368](../../../../server/services/temporal/agent_workflow.py#L3368), [AW:3280](../../../../server/services/temporal/agent_workflow.py#L3280)).

### 5.8 The other activities of the loop

| Activity | Input | Output | Evidence |
|---|---|---|---|
| `agent.broadcast_progress` | `node_id`, `workflow_id`, `iteration`, `max_iterations`, `phase`, `execution_id`, `root_execution_id`, `provider`, optional `status` and the tool or error fields | `{"emitted": True}` | [AW:3425](../../../../server/services/temporal/agent_workflow.py#L3425), [AA:920](../../../../server/services/temporal/agent_activities.py#L920) |
| `agent.compact_context` | `session_id`, `node_id`, `messages` (the turns to summarise, reduced to role, content, tool calls, call id and name), `provider`, `model`, `attribution` | `{"success", "summary", "usage"}`; raises when there is no summary | [AW:2997](../../../../server/services/temporal/agent_workflow.py#L2997), [AA:2788](../../../../server/services/temporal/agent_activities.py#L2788), [agent_context_pressure.py:284](../../../../server/services/temporal/agent_context_pressure.py#L284) |
| `agent.refresh_tools` | `operations`, `agent_node_type`, the call's correlation fields, and behind the binding patch `bound_node_ids`, `graph_snapshot`, `parameter_snapshot` | `{"tools": [<tool entry>], "graph_snapshot", "parameter_updates"}`, plus Browser bindings when routed | [AW:2655](../../../../server/services/temporal/agent_workflow.py#L2655), [AA:2032](../../../../server/services/temporal/agent_activities.py#L2032) |
| `agent.persist_turn` | Either `memory_node_id`, `human_text`, `assistant_text`, `window_size`, or `conversation_key` with `tool_results` | `{"appended", "applied", "trimmed_count"}` | [AW:3492](../../../../server/services/temporal/agent_workflow.py#L3492), [AW:2889](../../../../server/services/temporal/agent_workflow.py#L2889), [AA:836](../../../../server/services/temporal/agent_activities.py#L836) |
| `agent.store_output` | `node_id`, `session_id`, `result` (the result payload) | `{"stored": True}`; writes the output keys `output_main`, `output_top`, `output_0` | [AW:3301](../../../../server/services/temporal/agent_workflow.py#L3301), [AA:1051](../../../../server/services/temporal/agent_activities.py#L1051) |
| `agent.skill.clear` | `workflow_id`, `execution_id`, `agent_node_id` | `{"cleared": True}` | [AW:3315](../../../../server/services/temporal/agent_workflow.py#L3315), [AA:2074](../../../../server/services/temporal/agent_activities.py#L2074) |
| `agent.skill.invoke` | The tool payload; the activity reads `action`, `skill_name`, `path`, `query`, `cursor`, `limit` from `node_data` | The skill tool's result dict | [AA:2044](../../../../server/services/temporal/agent_activities.py#L2044) |

## 6. Determinism, versioning and durability

### 6.1 What the workflow code does itself

- The class is declared `@workflow.defn(sandboxed=False, name="AgentWorkflow")` ([AW:808](../../../../server/services/temporal/agent_workflow.py#L808)). It uses `asyncio.gather`, `asyncio.create_task` and `asyncio.shield` directly ([AW:2127](../../../../server/services/temporal/agent_workflow.py#L2127), [AW:2165](../../../../server/services/temporal/agent_workflow.py#L2165), [AW:965](../../../../server/services/temporal/agent_workflow.py#L965)).
- Pure computation in workflow code: message building, JSON serialisation of results, `hashlib` digests of workflow ids, the functions of `agent_context_pressure` (declared pure: no I/O, clock or randomness, [agent_context_pressure.py:3](../../../../server/services/temporal/agent_context_pressure.py#L3)).
- Reads of live process state from workflow code exist in three places. `_declared_tool_options` reads the plugin class's timeouts and retry policy on the legacy path; the comment says options are not compared on replay ([AW:111](../../../../server/services/temporal/agent_workflow.py#L111)). The legacy path reads `plugin_cls.needs_canvas`, which changes an activity input ([AW:2307](../../../../server/services/temporal/agent_workflow.py#L2307)). `_default_max_iterations` reads `Settings` when the payload omits `max_iterations` ([AW:177](../../../../server/services/temporal/agent_workflow.py#L177)). Durable runs use the frozen `activity_policy` instead of the live class ([AW:159](../../../../server/services/temporal/agent_workflow.py#L159)); controlled runs use the frozen `needs_canvas` ([AW:2304](../../../../server/services/temporal/agent_workflow.py#L2304)).
- Every workflow type in the package is unsandboxed, because the package's `__init__` has import side effects ([approval_workflow.py:13](../../../../server/services/temporal/approval_workflow.py#L13)). The workflow module imports the plugin registry and plugin base at import time ([AW:59](../../../../server/services/temporal/agent_workflow.py#L59)). INFERRED from `sandboxed=False`: determinism of workflow code is kept by convention, not enforced by the SDK sandbox.
- Command ids are derived from position: `tool-<node>-<iteration+1>-<call+1>`, `refresh-tools-...`, `llm-step-<iteration+1>`, `compact-context-<iteration+1>`, `<workflow id>-delegate-<node>-<iteration+1>-<call+1>` ([AW:285](../../../../server/services/temporal/agent_workflow.py#L285), [AW:3377](../../../../server/services/temporal/agent_workflow.py#L3377), [AW:3021](../../../../server/services/temporal/agent_workflow.py#L3021)).

### 6.2 Every `workflow.patched` id in the file

| Patch id | Where | What it gates |
|---|---|---|
| `workspace-task-activity-policy-v1` | [AW:146](../../../../server/services/temporal/agent_workflow.py#L146) | Legacy options path, classes flagged `workspace_task`: adds `WAIT_CANCELLATION_COMPLETED`, routes to the class queue when the pool flag is set, adds inherited scope and `user_id` to the tool payload |
| `employee-task-manager-runtime-v2` | [AW:961](../../../../server/services/temporal/agent_workflow.py#L961), [AW:3323](../../../../server/services/temporal/agent_workflow.py#L3323) | For a lead with `employee_recipe_version` 2: `employee.job.failed` on failure or cancel, `employee.job.deliver` after the output is stored |
| `agent-node-binding-refresh-v2` | [AW:1152](../../../../server/services/temporal/agent_workflow.py#L1152) | Deduplicates tool entries; sends bound node ids, graph and parameter snapshot to `agent.refresh_tools`; adopts the refreshed graph; appends a teammate roster system message after a rebind |
| `agent-tool-binding-key-v1` | [AW:1155](../../../../server/services/temporal/agent_workflow.py#L1155) | Deduplicates by `binding_key` instead of `tool_node_id`, so the tools of one node are kept apart |
| `employee-no-delegation-repair-v1` | [AW:1544](../../../../server/services/temporal/agent_workflow.py#L1544), [AW:3264](../../../../server/services/temporal/agent_workflow.py#L3264) | `agent.finalize_team` gets job arguments and a counted activity id; one corrective turn; failure when a lead assigned nothing |
| `employee-task-manager-tool-scope-v2` | [AW:2099](../../../../server/services/temporal/agent_workflow.py#L2099), [AW:2378](../../../../server/services/temporal/agent_workflow.py#L2378) | Adds the trusted scope (inherited keys plus `outputs`) to the Task Manager preflight payload and to every tool payload |
| `agent-user-action-error-v1` | [AW:2583](../../../../server/services/temporal/agent_workflow.py#L2583) | A tool result with `error` and `requires_user_action` true ends the run after the turn |
| `agent-blocked-tool-turn-save-v1` | [AW:2888](../../../../server/services/temporal/agent_workflow.py#L2888) | On such a blocked turn, schedules `agent.persist_turn` with the turn's tool results |

A new patch id ships dormant for one release through `DORMANT_PATCHES` and the worker's `patch_activation_callback` ([patches.py:14](../../../../server/services/temporal/patches.py#L14), [worker.py:271](../../../../server/services/temporal/worker.py#L271)). None of the eight ids above is in that set today.

### 6.3 Version fields and what each selects

| Field | Recorded or frozen where | What it selects in the loop |
|---|---|---|
| `context_pressure_version` | `agent.prepare_payload` result ([AA:1873](../../../../server/services/temporal/agent_activities.py#L1873)); constant 1 ([agent_context_pressure.py:42](../../../../server/services/temporal/agent_context_pressure.py#L42)) | 1: clear old results, gate compaction on the next request, keep the latest turn. Absent: the original cumulative-token gate that summarises everything ([AW:2928](../../../../server/services/temporal/agent_workflow.py#L2928), [AW:2975](../../../../server/services/temporal/agent_workflow.py#L2975)) |
| `tool_result_max_chars` | Prepare result ([AA:1871](../../../../server/services/temporal/agent_activities.py#L1871)) | The per-result cap; 0 or absent keeps results whole ([AW:1250](../../../../server/services/temporal/agent_workflow.py#L1250)) |
| `transcript_budget_bytes` | Prepare result ([AA:1872](../../../../server/services/temporal/agent_activities.py#L1872)) | The byte budget for clearing and clipping ([AW:1252](../../../../server/services/temporal/agent_workflow.py#L1252)) |
| `llm_attempt_timeout_seconds` | Prepare result ([AA:1847](../../../../server/services/temporal/agent_activities.py#L1847)) | The LLM step's and compaction's `start_to_close_timeout`; options only ([AW:101](../../../../server/services/temporal/agent_workflow.py#L101)) |
| `runtime_behavior_version` | Saved agent parameters, copied by prepare ([AA:1230](../../../../server/services/temporal/agent_activities.py#L1230), [agent_runtime_policy.py:54](../../../../server/services/agent_runtime_policy.py#L54)) | Extra LLM payload fields (`runtime_scope`, `agent_label`, warning seconds), the wait key in progress events, extra refresh fields ([AW:1136](../../../../server/services/temporal/agent_workflow.py#L1136), [AW:1324](../../../../server/services/temporal/agent_workflow.py#L1324)) |
| `browser_runtime_version` | Prepare result ([AA:1839](../../../../server/services/temporal/agent_activities.py#L1839)) | Browser bindings and claims; makes the run durable ([AW:1099](../../../../server/services/temporal/agent_workflow.py#L1099)) |
| `activity_policy`, `needs_canvas` (per tool) | Prepare and refresh results, only in the cases listed in 5.3 | Tool activity options and canvas forwarding from history instead of live classes ([AW:137](../../../../server/services/temporal/agent_workflow.py#L137), [AW:2304](../../../../server/services/temporal/agent_workflow.py#L2304)) |
| `execution_control_version` | Generation manifest, in the workflow input ([deployment/control.py:168](../../../../server/services/deployment/control.py#L168)) | Cooperative control (gates, Updates), the frozen `needs_canvas`, immediate tool message append; it is also one of the five conditions for the frozen tool options and the durable continuation (carried payload, whole-input byte guard) ([execution_control.py:35](../../../../server/services/temporal/execution_control.py#L35), [AW:1087](../../../../server/services/temporal/agent_workflow.py#L1087)) |
| `pi_harness_version` | Generation flag ([workflow.py:187](../../../../server/services/temporal/workflow.py#L187)) | `coding_agent` runs as `PiCodingAgentWorkflow`; a plain `AgentWorkflow` refuses such a context ([AW:955](../../../../server/services/temporal/agent_workflow.py#L955)) |
| `approval_wait_version` | Generation flag; frozen by `freeze_generation_harness` from the manifest ([coding_agent_runtime.py:60](../../../../server/services/coding_agent_runtime.py#L60)); not written by Start | Class-and-mode approval check; the in-turn wait ([AW:2383](../../../../server/services/temporal/agent_workflow.py#L2383), [tool_calls.py:262](../../../../server/services/approvals/tool_calls.py#L262)) |
| `native_workspace_version`, `browser_routing_version`, `pi_local_control_version` | Workflow input | Durable-runtime branches and control class selection ([AW:1087](../../../../server/services/temporal/agent_workflow.py#L1087), [AW:826](../../../../server/services/temporal/agent_workflow.py#L826)) |
| `lease_version: 2` | Constant in the permit acquire payload ([AW:1829](../../../../server/services/temporal/agent_workflow.py#L1829)) | The permit lease scheme in the activity ([AA:2373](../../../../server/services/temporal/agent_activities.py#L2373)) |

### 6.4 Durability

- The conversation is saved best effort inside the LLM step, after the provider answered: `[...sent, assistant]`, whole-row upsert; a failed save is logged and swallowed ([AA:703](../../../../server/services/temporal/agent_activities.py#L703)). Tool results reach the store with the next LLM step's save. A blocked turn saves them through `agent.persist_turn` ([AW:2888](../../../../server/services/temporal/agent_workflow.py#L2888)); a non-retryable LLM error saves them from inside the activity ([AA:600](../../../../server/services/temporal/agent_activities.py#L600)).
- Loads fail loudly: `ConversationLoadFailed` (retryable) and `ConversationTooLarge` (non-retryable, over 1,000,000 bytes) ([AA:1565](../../../../server/services/temporal/agent_activities.py#L1565), [AA:1576](../../../../server/services/temporal/agent_activities.py#L1576)).
- `agent.persist_turn` (legacy memory markdown) runs only when `memory_node_id` is set, which requires a memory descriptor whose `kind` is not `context`, and only for a non-interim turn ([AW:3485](../../../../server/services/temporal/agent_workflow.py#L3485), [AA:1528](../../../../server/services/temporal/agent_activities.py#L1528)).
- At most once for effects: activity retries of a call that is not safe to repeat answer instead of running again (4.4, step 4), and an owner-approved call runs with one attempt ([approved_tool_call_workflow.py:34](../../../../server/services/temporal/approved_tool_call_workflow.py#L34)).
- Exits: cleanup that schedules commands is skipped when the exception is a workflow bug (`is_workflow_bug`), so a fixed release can replay ([_exits.py:16](../../../../server/services/temporal/_exits.py#L16), [AW:976](../../../../server/services/temporal/agent_workflow.py#L976)). Inside the tool branch, however, `except Exception` turns any exception raised while handling one call into a tool error for the model, including a `ValueError` raised by workflow code such as the Task Manager bridge ([AW:2761](../../../../server/services/temporal/agent_workflow.py#L2761), [AW:1934](../../../../server/services/temporal/agent_workflow.py#L1934)).

### 6.5 Continue-as-new and history growth

- Trigger: `_history_pressure(_AGENT_HISTORY_SOFT_CAP)` is true when the server suggests continue-as-new or the history is longer than 10,000 events ([trigger_listener_workflow.py:90](../../../../server/services/temporal/trigger_listener_workflow.py#L90), [AW:350](../../../../server/services/temporal/agent_workflow.py#L350)). It is checked only at the end of a tool turn, after pressure relief ([AW:3182](../../../../server/services/temporal/agent_workflow.py#L3182)). A final turn and a provider compaction pause never reach it.
- Blocked while a delegation is live: `delegation_handles` or `task_manager_delegation_tasks` non-empty skips the rollover for this turn ([AW:3183](../../../../server/services/temporal/agent_workflow.py#L3183)). Finished handles are popped so they do not block it ([AW:2444](../../../../server/services/temporal/agent_workflow.py#L2444)).
- Durable run: waits until resumed and until all update handlers finished, builds the next input (context, control carry, resume marker), measures the converter's encoding of the whole input, and raises non-retryable `AgentContinuationTooLarge` above 1,900,000 bytes; otherwise `workflow.continue_as_new(args=[next_context])` ([AW:3187](../../../../server/services/temporal/agent_workflow.py#L3187), [AW:384](../../../../server/services/temporal/agent_workflow.py#L384)).
- Legacy run: a transcript over 1,000,000 bytes is dropped (the resumed run restarts from the stored conversation or the opening prompt); then `continue_as_new` with the short marker ([AW:3229](../../../../server/services/temporal/agent_workflow.py#L3229), [AW:383](../../../../server/services/temporal/agent_workflow.py#L383)).
- `execution_id` is carried because `run_id` changes on continue-as-new ([AW:1110](../../../../server/services/temporal/agent_workflow.py#L1110)).
- Growth per turn, from the code: a turn with k ordinary tool calls schedules 2 + 3k activities (one progress and one LLM step, then per call a progress, the tool, a progress). Each progress call is doubled in a delegated child, which mirrors it onto the parent node ([AW:3447](../../../../server/services/temporal/agent_workflow.py#L3447)). Refresh, compaction and delegation bookkeeping add more. INFERRED: at three history events per activity plus workflow task events, a single-tool turn is about 30 events.
- Growth in bytes: every `agent.execute_llm_step` input holds the whole transcript, all visible tool definitions and a second copy of the system prompt ([AW:1335](../../../../server/services/temporal/agent_workflow.py#L1335), [agent_context_pressure.py:62](../../../../server/services/temporal/agent_context_pressure.py#L62)). The prepare result holds the stored conversation (up to 1,000,000 bytes) and every tool entry; the workflow input holds the full canvas.

## 7. Limits, budgets, timeouts and retries

### 7.1 Iterations and usage

- `max_iterations` precedence in `agent.prepare_payload`: the node's `max_iterations` parameter, then the owner's `agent_recursion_limit` setting, then `Settings.agent_recursion_limit` (env `AGENT_RECURSION_LIMIT`, default 200), then a literal 200 ([AA:1774](../../../../server/services/temporal/agent_activities.py#L1774), [config.py:519](../../../../server/core/config.py#L519)). The workflow reads `payload["max_iterations"]` ([AW:1129](../../../../server/services/temporal/agent_workflow.py#L1129)).
- At the cap: the loop's `else` branch logs a warning and sets the response to `[AgentWorkflow truncated after max_iterations; the model did not produce a final response]`. The run then stores output and returns success. Exceptions: a Pi coding agent returns a `PiIterationExhausted` failure, and a team lead that assigned nothing fails ([AW:3261](../../../../server/services/temporal/agent_workflow.py#L3261)). Every pass counts, including a provider compaction pause and the corrective turn.
- A delegated child has its own budget: it runs its own `agent.prepare_payload`.
- Usage: each step's integer counters are added to `usage_total` and `context_usage_total` ([AW:1487](../../../../server/services/temporal/agent_workflow.py#L1487)). Compaction usage is added to `usage_total` only ([AW:3076](../../../../server/services/temporal/agent_workflow.py#L3076)). Both totals cross continue-as-new ([AW:1270](../../../../server/services/temporal/agent_workflow.py#L1270)). The final result returns `usage_total`.
- A delegated child's usage is not added to the parent's `usage_total`. It appears in the tool result the model reads (`"usage": child_summary.get("usage")`, [AW:2503](../../../../server/services/temporal/agent_workflow.py#L2503)).
- Cost: the workflow computes no cost. No call that persists per-step token usage or cost exists under the Temporal package or the LLM package (searched for `track(`, `TokenUsageMetric`, `pricing`). The LLM step passes attribution ids to the log context for the gateway's spend rows ([AW:1383](../../../../server/services/temporal/agent_workflow.py#L1383)).

### 7.2 Result size and transcript pressure

| Limit | Value | Defined at | Applied at |
|---|---|---|---|
| Per-result cap for the model's copy | `tool_result_max_chars`: owner setting, else env `TOOL_RESULT_MAX_CHARS`, default 100,000 characters; 0 disables | [config.py:514](../../../../server/core/config.py#L514), [tool_output.py:93](../../../../server/services/tool_output.py#L93) | [AW:2602](../../../../server/services/temporal/agent_workflow.py#L2602) |
| Exempt from the cap | `_builtin_skill`, `_builtin_check_delegated_tasks`, `taskManager`, `employeeJobSource`, every type in `AI_AGENT_TYPES`, and every `delegate_to_*` call. Unknown types are capped | [tool_output.py:34](../../../../server/services/tool_output.py#L34), [tool_output.py:54](../../../../server/services/tool_output.py#L54) | [AW:1254](../../../../server/services/temporal/agent_workflow.py#L1254) |
| Node result refused | 2,097,152 bytes or more raises `NodeUserError`; 524,288 or more logs a warning | [base.py:1144](../../../../server/services/plugin/base.py#L1144), [limits.py:26](../../../../server/services/media/limits.py#L26) | Inside the node, before the activity returns |
| Transcript byte budget | 393,216 bytes (three quarters of 524,288) | [agent_context_pressure.py:59](../../../../server/services/temporal/agent_context_pressure.py#L59) | [AW:2929](../../../../server/services/temporal/agent_workflow.py#L2929) |
| Clearing target | Half the budget; external results first, oldest first; the latest turn is never cleared | [agent_context_pressure.py:90](../../../../server/services/temporal/agent_context_pressure.py#L90) | [AW:2929](../../../../server/services/temporal/agent_workflow.py#L2929) |
| Clip of the latest turn | One shared length found by bisection, never below 2,000 characters | [agent_context_pressure.py:163](../../../../server/services/temporal/agent_context_pressure.py#L163), [agent_context_pressure.py:56](../../../../server/services/temporal/agent_context_pressure.py#L56) | [AW:3145](../../../../server/services/temporal/agent_workflow.py#L3145) |
| Compaction threshold | Ratio times the model's context length, floor 10,000 tokens; ratio from the owner setting (0.1 to 0.9), else the model registry's agent default (`Settings.compaction_ratio` defaults to 0.8, [config.py:509](../../../../server/core/config.py#L509)). `None` when compaction is disabled, which turns summarisation off | [compaction.py:86](../../../../server/services/compaction.py#L86), [compaction.py:101](../../../../server/services/compaction.py#L101), [AA:1703](../../../../server/services/temporal/agent_activities.py#L1703) | [AW:2953](../../../../server/services/temporal/agent_workflow.py#L2953) |
| Seed conversation | 1,000,000 bytes; a save over half logs a warning | [AA:64](../../../../server/services/temporal/agent_activities.py#L64), [AA:727](../../../../server/services/temporal/agent_activities.py#L727) | [AA:1576](../../../../server/services/temporal/agent_activities.py#L1576) |
| Continuation input | Whole input 1,900,000 bytes (durable runs); transcript alone 1,000,000 bytes (legacy runs) | [AW:383](../../../../server/services/temporal/agent_workflow.py#L383) | [AW:3214](../../../../server/services/temporal/agent_workflow.py#L3214), [AW:3233](../../../../server/services/temporal/agent_workflow.py#L3233) |
| History soft cap | 10,000 events | [AW:350](../../../../server/services/temporal/agent_workflow.py#L350) | [AW:3182](../../../../server/services/temporal/agent_workflow.py#L3182) |
| Concurrent delegated children | 3 by default (env `MAX_CONCURRENT_SUBAGENTS`, 1 to 50; owner setting overrides) | [config.py:525](../../../../server/core/config.py#L525), [AA:1750](../../../../server/services/temporal/agent_activities.py#L1750) | [AW:1595](../../../../server/services/temporal/agent_workflow.py#L1595) |
| Delegation depth | 2, hard ceiling 2 | [config.py:526](../../../../server/core/config.py#L526) | [AW:1592](../../../../server/services/temporal/agent_workflow.py#L1592) |

Version 1 pressure sequence after each tool turn ([AW:2928](../../../../server/services/temporal/agent_workflow.py#L2928)): clear old results past the budget; estimate the next request as the last step's request tokens plus the turn's added characters divided by 4 ([agent_context_pressure.py:242](../../../../server/services/temporal/agent_context_pressure.py#L242)); compact when that estimate reaches the threshold or the earlier turns alone keep the transcript over budget, and only when something older than this turn exists; then clip this turn if it still does not fit. Compaction replaces `messages` with the original system message, one user message holding the summary and the current request, and this turn verbatim ([AW:3116](../../../../server/services/temporal/agent_workflow.py#L3116)). A compaction failure ends the run with `error_type` `CompactionError` ([AW:3026](../../../../server/services/temporal/agent_workflow.py#L3026)).

### 7.3 Activities the loop schedules: timeouts and retry

| Activity | `start_to_close_timeout` | Heartbeat | Retry policy | Evidence |
|---|---|---|---|---|
| `agent.prepare_payload` | 60 s | none | `DEFAULT_ACTIVITY_RETRY` | [AW:1092](../../../../server/services/temporal/agent_workflow.py#L1092) |
| `agent.execute_llm_step` | `llm_attempt_timeout`: the provider's recorded value, else 10 min | 1 min; the activity beats every 20 s and ends its own attempt 30 s before the deadline | `LLM_STEP_RETRY` | [AW:3375](../../../../server/services/temporal/agent_workflow.py#L3375), [AA:382](../../../../server/services/temporal/agent_activities.py#L382) |
| `agent.compact_context` | Same as the LLM step | 1 min | `LLM_STEP_RETRY`; a non-LLM failure turns non-retryable at attempt 3 | [AW:3018](../../../../server/services/temporal/agent_workflow.py#L3018), [AA:2898](../../../../server/services/temporal/agent_activities.py#L2898) |
| Tool activity (`node.<type>.v<version>`, `agent.skill.invoke`, `coding.workspace_tool`) | Plugin class value, default 10 min | Plugin class value, default 2 min; the body beats every 30 s | Plugin class policy, default 3 attempts | [AW:111](../../../../server/services/temporal/agent_workflow.py#L111), [base.py:368](../../../../server/services/plugin/base.py#L368), [scaling.py:91](../../../../server/services/plugin/scaling.py#L91) |
| `agent.broadcast_progress`, `agent.persist_turn`, `agent.store_output`, `agent.skill.clear`, `agent.finalize_team` | 30 s | none | `DEFAULT_ACTIVITY_RETRY` | [AW:3425](../../../../server/services/temporal/agent_workflow.py#L3425), [AW:3492](../../../../server/services/temporal/agent_workflow.py#L3492), [AW:3301](../../../../server/services/temporal/agent_workflow.py#L3301) |
| `agent.refresh_tools` | 30 s | none | `DEFAULT_ACTIVITY_RETRY` | [AW:2675](../../../../server/services/temporal/agent_workflow.py#L2675) |
| `approvals.read`, `approvals.claim_send`, `approvals.record_outcome` | 30 s | none | `BOOKKEEPING_RETRY` | [approval_workflow.py:50](../../../../server/services/temporal/approval_workflow.py#L50), [approved_tool_call_workflow.py:63](../../../../server/services/temporal/approved_tool_call_workflow.py#L63) |
| An owner-approved tool call | The tool's own options | The tool's own | `SEND_ONCE` (1 attempt) | [AW:901](../../../../server/services/temporal/agent_workflow.py#L901) |
| `agent.acquire_subagent_permit` | 1 h | 10 s | `PERMIT_WAIT_RETRY` | [AW:1831](../../../../server/services/temporal/agent_workflow.py#L1831) |
| `browser_tasks.claim`, `browser_tasks.release` | 30 s, 120 s | none, 30 s | 1 attempt, unlimited | [AW:942](../../../../server/services/temporal/agent_workflow.py#L942), [AW:989](../../../../server/services/temporal/agent_workflow.py#L989) |
| `employee.job.deliver` | 31 days | 2 min | `REVIEWED_DELIVERY_RETRY` | [AW:3325](../../../../server/services/temporal/agent_workflow.py#L3325) |

Plugin overrides of the 10 minute tool default found by search: Browser 35 min, the mobile agent 3 h, the approval gate 24 h ([browser/__init__.py:414](../../../../server/nodes/browser/browser/__init__.py#L414), [mobile/_node.py:149](../../../../server/nodes/mobile/_node.py#L149), [approval_gate/__init__.py:184](../../../../server/nodes/workflow/approval_gate/__init__.py#L184)).

Child workflows (`AgentWorkflow`, `DelegatedTaskWorkflow`, `ApprovalWorkflow`) are started with no execution or run timeout ([AW:201](../../../../server/services/temporal/agent_workflow.py#L201), [AW:877](../../../../server/services/temporal/agent_workflow.py#L877), [AW:1886](../../../../server/services/temporal/agent_workflow.py#L1886)).

### 7.4 Retry policy values

| Policy | Initial interval | Backoff | Maximum interval | Maximum attempts | Non-retryable types | Defined at |
|---|---|---|---|---|---|---|
| `DEFAULT_ACTIVITY_RETRY` (alias `AGENT_ACTIVITY_RETRY`) | 1 s | not set (SDK default) | 30 s | 3 | `NodeUserError`, `InvalidEvent`, `PayloadsTooLarge` | [_retry_policies.py:66](../../../../server/services/temporal/_retry_policies.py#L66), [AW:199](../../../../server/services/temporal/agent_workflow.py#L199) |
| `LLM_STEP_RETRY` | 5 s | 2.0 | 5 min | 0 (unlimited) | same three | [_retry_policies.py:97](../../../../server/services/temporal/_retry_policies.py#L97) |
| Plugin `DEFAULT_RETRY` | 1 s | 2.0 | 60 s | 3 | `ValidationError`, `PermissionDeniedError`, `InvalidParametersError`, `NodeUserError`, `PayloadsTooLarge` | [scaling.py:48](../../../../server/services/plugin/scaling.py#L48) |
| `BOOKKEEPING_RETRY`, `DELEGATION_CLEANUP_RETRY` | 1 s | not set | 30 s | 0 (unlimited) | none | [_retry_policies.py:117](../../../../server/services/temporal/_retry_policies.py#L117), [_retry_policies.py:128](../../../../server/services/temporal/_retry_policies.py#L128) |
| `PERMIT_WAIT_RETRY` | 1 s | not set | 30 s | 0 (unlimited) | the three above | [_retry_policies.py:143](../../../../server/services/temporal/_retry_policies.py#L143) |
| `REVIEWED_DELIVERY_RETRY` | 2 s | not set | 1 min | 0 (unlimited) | the three above | [_retry_policies.py:159](../../../../server/services/temporal/_retry_policies.py#L159) |
| `SEND_ONCE` | n/a | n/a | n/a | 1 | n/a | [approved_tool_call_workflow.py:34](../../../../server/services/temporal/approved_tool_call_workflow.py#L34) |

A retry past its limit raises an attention item; the limit is 8 for the LLM step and compaction and 10 otherwise ([_interceptors.py:46](../../../../server/services/temporal/_interceptors.py#L46)).

### 7.5 Errors: what the model reads and what fails the run

- **LLM failure, retryable** (rate limit, 5xx, network, attempt deadline): the activity raises `ApplicationError` of type `LLMError.<category>` with `next_retry_delay` set to the provider's retry-after or to `min(5 min, 5 s * 2^(attempt-1))`, category benign, and publishes a `retry_wait` node status ([AA:246](../../../../server/services/temporal/agent_activities.py#L246), [AA:597](../../../../server/services/temporal/agent_activities.py#L597)). Temporal retries without limit. The workflow sees nothing until the step completes.
- **LLM failure, non-retryable** (invalid request, authentication, missing credential, empty prompt, a step that cannot be built or handled): the `except` around the step runs `agent.skill.clear`, emits `failed`, and returns `{"success": False, "error": "LLM step failed: ...", "error_type": "LLMStepError", ...}`. The run ends; nothing is raised ([AW:1404](../../../../server/services/temporal/agent_workflow.py#L1404)).
- **Tool failure reported by the node**: it is a normal activity result (`success: False`), serialised into the tool message. The model reads the error and the loop continues (4.4, step 8).
- **Tool timeout or worker loss** (`start_to_close_timeout` or heartbeat): Temporal retries under the plugin's policy. On a later attempt a call that is not safe to repeat answers `OutcomeUnknown` instead of running. When attempts run out, `execute_activity` raises into the workflow and the `except` writes the tool message from this line ([AW:2815](../../../../server/services/temporal/agent_workflow.py#L2815)):

```python
                        tool_content = f'{{"error": "{type(e).__name__}: {e}"}}'
```

  The `tool_completed` event carries `tool_failed: True` and the loop continues ([AW:2817](../../../../server/services/temporal/agent_workflow.py#L2817)).
- **Tool result that needs the owner** (`requires_user_action`): the turn is completed and saved, then the run returns a failure result (section 3, step 6, item 8).
- **Prepare failure**: `agent.prepare_payload` is awaited outside any `try`, so its failure after retries fails the workflow run ([AW:1091](../../../../server/services/temporal/agent_workflow.py#L1091), [AW:976](../../../../server/services/temporal/agent_workflow.py#L976)).
- **Progress broadcast failure**: `_emit_phase` is not guarded, so an `agent.broadcast_progress` failure after 3 attempts propagates (INFERRED from [AW:3425](../../../../server/services/temporal/agent_workflow.py#L3425) and the unguarded call at [AW:1311](../../../../server/services/temporal/agent_workflow.py#L1311)).

### 7.6 Stopping, pausing and cancelling

- **Chat Stop in a controlled generation** is the employee's pause: `handle_stop_chat_run` calls `handle_pause_workflow` ([chat/handlers.py:522](../../../../server/services/chat/handlers.py#L522)). The controller sends the Updates `set_control_state` and `wait_for_checkpoint` to the participants of the generation's tree, skipping `ApprovalWorkflow` ([deployment/execution_control.py:29](../../../../server/services/deployment/execution_control.py#L29)). `AgentWorkflow` exposes both Updates and the Query `execution_control_status` ([AW:836](../../../../server/services/temporal/agent_workflow.py#L836)).
- **Gates in the loop.** `_wait_until_resumed()` blocks while the state is not running ([AW:864](../../../../server/services/temporal/agent_workflow.py#L864), [execution_control.py:121](../../../../server/services/temporal/execution_control.py#L121)). It is called before each iteration, before the tool batch, before each tool activity, before refresh, compaction, delegation steps and rollover ([AW:1304](../../../../server/services/temporal/agent_workflow.py#L1304), [AW:1581](../../../../server/services/temporal/agent_workflow.py#L1581), [AW:2524](../../../../server/services/temporal/agent_workflow.py#L2524), [AW:2673](../../../../server/services/temporal/agent_workflow.py#L2673), [AW:3013](../../../../server/services/temporal/agent_workflow.py#L3013), [AW:3190](../../../../server/services/temporal/agent_workflow.py#L3190)). An admitted action (`action()` or `begin_action()`) is counted, and `wait_for_checkpoint` returns when no action and no child start is pending ([execution_control.py:114](../../../../server/services/temporal/execution_control.py#L114)). So a Stop drains the running command and parks before the next one; with calls A and B in one message, a Stop during A keeps A's result and B waits for Resume.
- **Legacy pause**: the Signals `pause` and `resume` set a flag that the same gate reads ([AW:856](../../../../server/services/temporal/agent_workflow.py#L856)).
- **Chat Stop in an uncontrolled run** is terminal and reaches the loop through the database, not through a Signal. `request_stop` moves the run to `stopping` ([ledger.py:608](../../../../server/services/chat/ledger.py#L608)). The LLM step checks the flag before calling the provider and polls it every `runs.stop_poll_s` (1 second) during the call; it returns what was written so far as a `final` step marked stopped ([AA:556](../../../../server/services/temporal/agent_activities.py#L556), [AA:448](../../../../server/services/temporal/agent_activities.py#L448), [chat_defaults.json:6](../../../../server/config/chat_defaults.json#L6)). A tool activity of a stopping run answers without running ([base.py:1288](../../../../server/services/plugin/base.py#L1288)). The workflow then ends through the normal `final` path. A run still stopping after `runs.stop_grace_s` (45 seconds) has its workflow cancelled by the chat watchdog ([ledger.py:724](../../../../server/services/chat/ledger.py#L724)).
- **Cancellation.** In the tool branch `asyncio.CancelledError` runs the delegation cleanup and re-raises ([AW:2758](../../../../server/services/temporal/agent_workflow.py#L2758)). `run` records the job failure, releases Browser claims in a shielded task and re-raises ([AW:973](../../../../server/services/temporal/agent_workflow.py#L973)). Frozen-policy tool activities use `WAIT_CANCELLATION_COMPLETED` ([AW:166](../../../../server/services/temporal/agent_workflow.py#L166)); legacy tool activities use the SDK default. Inside the tool activity a cancel leaves the owner a card when the call is not safe to repeat ([base.py:1544](../../../../server/services/plugin/base.py#L1544)).

### 7.7 Streaming and progress

| What | Emitted from | Detail |
|---|---|---|
| `agent_progress` CloudEvent (`com.opencompany.agent.progress`) with `iteration`, `max_iterations`, `phase` | Activity `agent.broadcast_progress`, scheduled by the workflow's `_emit_phase` | Phases: `starting`, `llm_step`, `executing_tool`, `tool_completed`, `tool_error`, `failed`, `completed`; a delegated child also emits `delegating` on its parent's node ([AW:3406](../../../../server/services/temporal/agent_workflow.py#L3406), [AA:1041](../../../../server/services/temporal/agent_activities.py#L1041)) |
| Raw node status for the agent node | Same activity | When a `status` is given, or the event names a tool ([AA:973](../../../../server/services/temporal/agent_activities.py#L973)) |
| `agent_capability` CloudEvent (tool started, completed, failed) | Same activity | For `executing_tool` and `tool_completed`, not for the `Skill` tool; the event id is a hash of workflow, execution, node, call id, name and state ([AA:1006](../../../../server/services/temporal/agent_activities.py#L1006)) |
| Node status `llm_step` and `retry_wait` for the agent node | Inside `agent.execute_llm_step` | Best effort ([AA:317](../../../../server/services/temporal/agent_activities.py#L317)) |
| Chat text: `text.started`, `text.content`, `text.ended`, and `opencompany.segment_discarded` | Inside `agent.execute_llm_step`, through `ChatStreamEmitter` | Only for the agent whose payload has `chat_stream`; one segment per attempt, flushed every 50 ms or 160 characters ([AA:559](../../../../server/services/temporal/agent_activities.py#L559), [stream.py:117](../../../../server/services/chat/stream.py#L117), [chat_defaults.json:15](../../../../server/config/chat_defaults.json#L15)) |
| Chat steps: `step.started`, `step.finished`, saved on the run | Inside the tool activity (`BaseNode.as_activity`) | Only when the tool payload has `chat_stream` and a `tool_call_id`; the label is the plugin's `chat_step`, else `Used <display name>` ([steps.py:89](../../../../server/services/chat/steps.py#L89), [steps.py:111](../../../../server/services/chat/steps.py#L111)) |
| Node status and node output for the tool node | Inside the tool activity | [base.py:1368](../../../../server/services/plugin/base.py#L1368), [base.py:1520](../../../../server/services/plugin/base.py#L1520) |

`chat_stream` is set by `chat_stream_for`: the run's scope reached the agent, the agent has no `parent_node_id`, and its output edge goes to a node whose class has `answers_chat_run` ([stream.py:61](../../../../server/services/chat/stream.py#L61)). A delegated child therefore has no `chat_stream`: its text does not stream and its tool calls are not chat steps.

## 8. Extension points relevant to a dynamic workflow

Facts only.

**Where a new kind of tool call would be dispatched.** The existing precedents, each a different mechanism:

1. A registered plugin node connected on `input-tools`. `agent.prepare_payload` builds an entry for every connection the edge walker returns and `node_tools` can build ([AA:1403](../../../../server/services/temporal/agent_activities.py#L1403), [AA:1607](../../../../server/services/temporal/agent_activities.py#L1607)), and the workflow runs it as `node.<type>.v<version>` with no change to workflow code ([AW:2394](../../../../server/services/temporal/agent_workflow.py#L2394)). The activity body is the node's operation, in one activity attempt, on a worker.
2. A pseudo node type with its own `agent.*` activity. The `Skill` tool is appended by prepare ([AA:1604](../../../../server/services/temporal/agent_activities.py#L1604)), named and given a schema in `_build_tool_from_node` and `_get_tool_schema` ([ai.py:2445](../../../../server/services/ai.py#L2445), [ai.py:2676](../../../../server/services/ai.py#L2676)), and routed by the `node_type == "_builtin_skill"` test ([AW:2393](../../../../server/services/temporal/agent_workflow.py#L2393)). The activity must be registered in `collect_agent_activities` ([AA:2659](../../../../server/services/temporal/agent_activities.py#L2659)).
3. A per-entry `activity_name`. Entries flagged `virtual_workspace` name their own activity ([AW:2392](../../../../server/services/temporal/agent_workflow.py#L2392)).
4. Workflow-code handling that starts child workflows. Three places start a child for a tool call: the `delegate_to_*` branch, the Task Manager bridge and the approval wait ([AW:2420](../../../../server/services/temporal/agent_workflow.py#L2420), [AW:2557](../../../../server/services/temporal/agent_workflow.py#L2557), [AW:2541](../../../../server/services/temporal/agent_workflow.py#L2541)).
5. Subclass hooks. `_execute_model_step`, `_before_runtime_model_step`, `_runtime_tool_call_allowed`, `_before_runtime_tool_batch`, `_before_runtime_tool_call`, `_complete_runtime_turn`, `_before_runtime_rollover`, `_runtime_child_started` and `_runtime_child_finished` are no-ops or defaults in the base class and are overridden by `PiCodingAgentWorkflow` ([AW:3373](../../../../server/services/temporal/agent_workflow.py#L3373), [pi_workflow.py:234](../../../../server/services/temporal/pi_workflow.py#L234)).

**Concurrency already present in workflow code.**

- Several activities started and joined: `workflow.start_activity` handles in a list, `asyncio.gather(..., return_exceptions=True)` ([AW:2111](../../../../server/services/temporal/agent_workflow.py#L2111)).
- Several child workflows running at once: handles from `workflow.start_child_workflow` kept in a dict and awaited later ([AW:1886](../../../../server/services/temporal/agent_workflow.py#L1886), [AW:2421](../../../../server/services/temporal/agent_workflow.py#L2421)).
- Workflow coroutines as tasks: `asyncio.create_task`, each running a sequence of activities and a child start ([AW:2165](../../../../server/services/temporal/agent_workflow.py#L2165)).
- All of this concurrency is across the tool calls of one turn. No existing branch starts and joins several activities or children inside one tool call. Within one call the existing multi-command sequences are serial: claim, run, record ([approved_tool_call_workflow.py:63](../../../../server/services/temporal/approved_tool_call_workflow.py#L63)); queue, permit, begin, start child, register ([AW:1809](../../../../server/services/temporal/agent_workflow.py#L1809)).
- Live handles block continue-as-new for the turn ([AW:3183](../../../../server/services/temporal/agent_workflow.py#L3183)).
- Child and activity ids are built from the tool node id, the iteration and the call index, so they identify one tool call, not several commands inside it ([AW:285](../../../../server/services/temporal/agent_workflow.py#L285), [AW:290](../../../../server/services/temporal/agent_workflow.py#L290)).

**How a sub-agent is called today.**

- The child input is a full agent context: the tool node's id and type, its saved parameters, the whole canvas, the inherited scope, and `invocation: {task, context}` ([AW:1748](../../../../server/services/temporal/agent_workflow.py#L1748)). The child runs its own `agent.prepare_payload`, where the invocation wins over stored configuration ([AA:1301](../../../../server/services/temporal/agent_activities.py#L1301)).
- The child's result is reduced to its response text, plus usage, model and provider ([AW:3507](../../../../server/services/temporal/agent_workflow.py#L3507)).
- For a team lead the `delegate_to_*` entries are hidden from the model and are reached only through Task Manager; the entries stay in `tool_index` for the bridge ([AA:1652](../../../../server/services/temporal/agent_activities.py#L1652), [AW:1322](../../../../server/services/temporal/agent_workflow.py#L1322), [AW:1936](../../../../server/services/temporal/agent_workflow.py#L1936)).
- A direct delegation takes a root-wide permit only when a team id exists ([AW:1779](../../../../server/services/temporal/agent_workflow.py#L1779)); without one the window alone bounds concurrency.
- Agents outside `AGENT_WORKFLOW_TYPES`, called as a tool, run as one activity under their plugin's options: the default 10 minute attempt timeout (no class overrides it) and one attempt (`rlm_agent`, `claude_code_agent` and `codex_agent` declare `maximum_attempts=1`) ([AW:2513](../../../../server/services/temporal/agent_workflow.py#L2513), [rlm_agent/__init__.py:44](../../../../server/nodes/agent/rlm_agent/__init__.py#L44), [claude_code_agent/__init__.py:260](../../../../server/nodes/agent/claude_code_agent/__init__.py#L260), [codex_agent/__init__.py:108](../../../../server/nodes/agent/codex_agent/__init__.py#L108)). As a graph node the same agent gets `MachinaWorkflow`'s 24 hour activity timeout instead ([workflow.py:173](../../../../server/services/temporal/workflow.py#L173)). Agents are not in the repeatable list ([repeats.py:10](../../../../server/services/approvals/repeats.py#L10)).

**What a tool result may contain and how large it may be.**

- It must be JSON-serialisable; the model reads one string per call ([AW:3528](../../../../server/services/temporal/agent_workflow.py#L3528)).
- Size: refused at 2,097,152 bytes inside the node; the model's copy is cut at 100,000 characters unless the tool type is exempt; old results are cleared past the 393,216 byte transcript budget; the latest turn can be clipped to 2,000 characters per result (7.2).
- Exemptions from the cap are by node type and by the `delegate_to_` name prefix; a type that is not listed is capped ([tool_output.py:54](../../../../server/services/tool_output.py#L54), [AW:1254](../../../../server/services/temporal/agent_workflow.py#L1254)).
- Reserved result keys are listed in 5.5.
- Files travel as references. A node result over the payload limit is refused with a message pointing at the workspace ([base.py:1144](../../../../server/services/plugin/base.py#L1144)).

**What a tool activity receives beyond the model's arguments.** The agent's node id (`parent_node_id`), the run identity (`execution_id`, `root_execution_id`, `workflow_id`, `session_id`), the call identity (`tool_call_id`, `agent_iteration`, `tool_call_index`), `chat_run_id` and `chat_stream`, and the canvas only for `needs_canvas` plugins ([AW:2322](../../../../server/services/temporal/agent_workflow.py#L2322)). It does not receive the transcript, the tool list or the delegate entries. Task Manager is the existing example of a tool that both sees the canvas (`needs_canvas`) and returns a request (`delegation_request`) that the workflow then acts on ([task_manager/__init__.py:156](../../../../server/nodes/tool/task_manager/__init__.py#L156)).

**Approval and retry classification of a new tool type.** Both tables are keyed by node type. An unlisted type is classed `send` ([classes.py:521](../../../../server/services/approvals/classes.py#L521)) and is not safe to repeat ([repeats.py:285](../../../../server/services/approvals/repeats.py#L285)). `montyExecutor` is already listed in both: its class follows its `capabilities` field, and it is repeatable ([classes.py:371](../../../../server/services/approvals/classes.py#L371), [repeats.py:63](../../../../server/services/approvals/repeats.py#L63)). The agent types that run as `AgentWorkflow` are classed `read` as tools: handing work to one asks nothing, and each of its own tool calls is checked ([classes.py:121](../../../../server/services/approvals/classes.py#L121)). The CLI and REPL agents (`claude_code_agent`, `codex_agent`, `rlm_agent`) and the code executors are classed `send` ([classes.py:162](../../../../server/services/approvals/classes.py#L162)).

**Budget facts that a fan-out would meet.** One LLM-step iteration counts once however many calls the message holds ([AW:1303](../../../../server/services/temporal/agent_workflow.py#L1303)). Child usage is not summed into the parent (7.1). The parent's `max_iterations` does not bound its children. Delegation depth stops at 2 ([AW:1592](../../../../server/services/temporal/agent_workflow.py#L1592)).

**Messages a running `AgentWorkflow` accepts.** Updates `set_control_state` and `wait_for_checkpoint`, Query `execution_control_status`, Signals `pause` and `resume` ([AW:836](../../../../server/services/temporal/agent_workflow.py#L836)). There is no Signal or Update that delivers data into the loop.

## 9. Where the docs disagree with the code

1. [agent_architecture.md](../../../agent_architecture.md), section "Multimodal tool results", says the Temporal activity path attaches ref-only image blocks to the tool message. The code attaches them only in the in-process loop ([agent_runtime.py:440](../../../../server/services/agent_runtime.py#L440)); the workflow builds tool messages without blocks ([AW:245](../../../../server/services/temporal/agent_workflow.py#L245)), and neither Temporal agent file references `llm_media`.
2. [TEMPORAL_ARCHITECTURE.md](../../../TEMPORAL_ARCHITECTURE.md), section "Agent-as-child-workflow (F4.B)" (the working-tree copy had uncommitted edits by another session when read). Its loop lists `agent.persist_turn` as a per-iteration step; the code skips interim turns and requires a legacy memory node ([AW:3485](../../../../server/services/temporal/agent_workflow.py#L3485)). It shows delegation as `execute_child_workflow("AgentWorkflow", child_context)` with `child_context = {**tool_payload, ...}`; the code starts children ahead of the pass with `start_child_workflow(agent_workflow_name(...))` and builds `child_context` on its own ([AW:1748](../../../../server/services/temporal/agent_workflow.py#L1748), [AW:1886](../../../../server/services/temporal/agent_workflow.py#L1886)).
3. [RFC-0007](../../../../RFC-0007-APPROVALS-ON-TEMPORAL.md), section 4.6 item 5, says a cancelled answer ends the turn and that `AgentWorkflow` ends its loop itself. The code returns a `not_run` tool result ("It was cancelled before it ran; nothing happened.") and the loop continues ([AW:473](../../../../server/services/temporal/agent_workflow.py#L473), [AW:2555](../../../../server/services/temporal/agent_workflow.py#L2555)). The RFC's line numbers are from an older commit; its statement that tool calls run one after another matches the code.
4. [agent_architecture.md](../../../agent_architecture.md), table under "Temporal dispatch routing", says version 1 agent-tool dispatch uses the plugin's timeout and heartbeat. The legacy path uses them too, read from the live class instead of from history ([AW:111](../../../../server/services/temporal/agent_workflow.py#L111)).
5. Model-facing text. The default description of an agent used as a tool says "Call ONCE per task, returns task_id immediately. Agent works in background" ([base.py:491](../../../../server/services/plugin/base.py#L491), [ai.py:2546](../../../../server/services/ai.py#L2546)), and the delegation schema's docstring names a `check_delegated_tasks` tool ([ai.py:2656](../../../../server/services/ai.py#L2656)). On the Temporal path a direct delegation awaits the child and returns its answer ([AW:2420](../../../../server/services/temporal/agent_workflow.py#L2420)), and `agent.prepare_payload` adds no `check_delegated_tasks` tool (that pseudo tool is injected only by in-process callers, [ai.py:1136](../../../../server/services/ai.py#L1136)).
6. Stale comments in the code itself. The module docstring of the workflow file names `agent.execute_llm_step.v1` and a per-turn `agent.persist_turn` ([AW:14](../../../../server/services/temporal/agent_workflow.py#L14)); the activity is unsuffixed and persist is skipped on interim turns. A comment calls `LLM_STEP_RETRY` one-shot ([AW:2646](../../../../server/services/temporal/agent_workflow.py#L2646)); the policy is unlimited ([_retry_policies.py:97](../../../../server/services/temporal/_retry_policies.py#L97)). The activities module docstring says the LLM step lives on the `ai-heavy` queue when the pool is wired ([AA:12](../../../../server/services/temporal/agent_activities.py#L12)); the workflow passes no queue ([AW:3375](../../../../server/services/temporal/agent_workflow.py#L3375)) and the queue table says LLM steps use the main queue ([scaling.py:25](../../../../server/services/plugin/scaling.py#L25)).

Checked and consistent with the code: the `max_iterations` precedence and the seeding precedence in [agent_architecture.md](../../../agent_architecture.md) and [agent_context_flow.md](../../../agent_context_flow.md); the 1,900,000 and 1,000,000 byte continuation limits; the version 1 pressure rules; the compaction swap; and the status note in RFC-0007 that Start does not write `approval_wait_version`.

## 10. Not verified / open questions

- Nothing was executed. No history was inspected, so event counts per turn are an estimate from the scheduled commands, not a measurement.
- The exact text of the exception the model reads after a tool activity exhausts its attempts (the format is known, the SDK's message for an `ActivityError` is not).
- Whether a provider can return `finish_reason` `compaction` on this path: the LLM payload has no `context_management` key ([AW:1323](../../../../server/services/temporal/agent_workflow.py#L1323)), and the activity passes `payload.get("context_management")` ([AA:579](../../../../server/services/temporal/agent_activities.py#L579)).
- How `MachinaWorkflow` treats an `AgentWorkflow` result with `success: False`, and whether the chat watchdog's cancel of the root workflow reaches the agent child as a cancellation (the parent side was not read; SDK default propagation is assumed).
- The controller side of Stop ([deployment/execution_control.py](../../../../server/services/deployment/execution_control.py)) was read only by search; the Update order and the tree walk are taken from its function names and the workflow-side helper.
- `PiCodingAgentWorkflow` was read only at the level of its method list. How its hooks change tool dispatch is not covered here.
- Whether token usage or cost of Temporal agent runs is persisted anywhere (gateway spend rows, compaction events) beyond the returned `usage`. No such call was found in the Temporal or LLM packages; other packages were not searched exhaustively.
- Whether the `approval_wait_version` 1 path has run outside tests. Start does not write the flag ([deployment/control.py:168](../../../../server/services/deployment/control.py#L168)); the tests that set it were not read.
- Whether providers accept tool messages in an order different from the calls (the controlled preflight append in 4.6 can produce one).
- `workflow.start_activity` not yielding to the event loop is stated in a code comment ([AW:2103](../../../../server/services/temporal/agent_workflow.py#L2103)); the SDK behaviour was not checked.
- The delegation and Task Manager lifecycle activities (`agent.queue_delegation`, `agent.begin_delegation`, `agent.finish_delegation`, permits) and `DelegatedTaskWorkflow` were read but are left to the delegation map; only their hook points are recorded here.
