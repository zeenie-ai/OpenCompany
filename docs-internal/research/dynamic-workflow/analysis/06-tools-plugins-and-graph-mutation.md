# Tools, plugins and graph mutation: code map

Scope: what an OpenCompany plugin can declare and register, how a node becomes a model-visible tool and how its calls are routed (in process and on Temporal), and how the Agent Builder and Hire build and change a workflow graph at runtime.
Date: 2026-11-12, as of commit 908a8ce6. The later commits on the branch at the time of reading (up to 48c34266) change none of the files cited here.
Method: read-only pass. Nothing was run. INFERRED marks a conclusion drawn from the code that no line states outright; NOT VERIFIED marks something not checked.

## 1. Summary

- A plugin is a `BaseNode` subclass in a folder under server/nodes. Importing it registers metadata, schemas, a handler and a per-type Temporal activity `node.<type>.v<version>` with no other edit. Thirty-nine `register_*` functions cover WebSocket commands, routers, triggers, apps, shutdown and Temporal `SimplePlugin`s (section 3.3).
- How a tool call is routed is decided in core, not in the plugin. In process it is `_dispatch_tool`; on Temporal `AgentWorkflow` chooses between a child workflow, `agent.skill.invoke`, a payload-supplied activity name and `node.<type>.v<version>` from hard-coded rules (section 4.5).
- A plugin can ship a new workflow type through `register_temporal_plugin` (one in-tree user, the cron scheduler). No registry lets a plugin make `AgentWorkflow` start a child workflow for its tool call; the one tool that does (Task Manager) has its own branch in the workflow. A tool's activity can start a detached workflow through the Temporal client (section 8).
- The tool calls of one turn run one after another in both runtimes; sub-agents are the exception. On Temporal, delegation children run up to 3 at once and 2 levels deep by default and the parent waits for each result, while a Task Manager assignment goes to a detached `DelegatedTaskWorkflow` and its tool call returns `queued`. In process a delegation is a background task and its tool call returns a task id (section 4.5).
- The tool surface is built once per run and grows mid-run only when a tool result carries `operations` (the hot rebind). The Skill tool is the only load-on-demand surface: a catalogue in the tool description, the body on `load` (section 4.9).
- A tool sees teammates only through the canvas: `needs_canvas = True` forwards `nodes` and `edges`, and `build_teammate_descriptors` rebuilds the list from them. A team lead's model gets teammates as a roster in its system prompt, not as callable tools (section 4.10).
- Graph changes from the server go through one function, `apply_graph_additions`: one write transaction, a ledger keyed by a mutation id, then a `workflow_ops_apply` push. It adds nodes and edges, merges parameters and removes named edges. A live generation keeps running its snapshot until Safe Apply or a restart (section 5).
- Three validate-and-refuse loops exist: `show_ui` (`check_spec` lists every problem, raised as `NodeUserError`), the Agent Builder's one-sentence refusals, and `validate_workflow` error codes (sections 5.3, 5.7, 5.8).

## 2. Files and responsibilities

| File | Lines | Owns |
|---|---|---|
| [server/services/plugin/base.py](../../../../server/services/plugin/base.py) | 1,638 | `BaseNode`: class attributes, registration, `execute`, `execute_as_tool`, result envelope and size check, `as_activity` |
| [server/services/plugin/tool.py](../../../../server/services/plugin/tool.py) | 252 | `ToolNode`, `ToolInput`, `tool_schema_locked`, `ToolBinding`, `tool_bindings`, `inline_schema_refs` |
| [server/services/plugin/approval.py](../../../../server/services/plugin/approval.py) | 131 | `ApprovalSpec`: which calls send, what the owner's card shows |
| [server/services/plugin/context.py](../../../../server/services/plugin/context.py) | 94 | `NodeContext` |
| [server/services/plugin/scaling.py](../../../../server/services/plugin/scaling.py) | 99 | `TaskQueue`, `RetryPolicy`, default timeouts |
| [server/services/plugin/edge_walker.py](../../../../server/services/plugin/edge_walker.py) | 790 | `collect_agent_connections`, `collect_teammate_connections`, `build_teammate_descriptors`, two registries |
| [server/services/plugin/registry.py](../../../../server/services/plugin/registry.py) | 193 | `IdempotentRegistry`, `IdempotentList` |
| [server/services/node_registry.py](../../../../server/services/node_registry.py) | 165 | `register_node`, `register_node_class`, `get_node_class` |
| [server/services/ws_handler_registry.py](../../../../server/services/ws_handler_registry.py) | 265 | WS handler, router, option loader, OAuth path and public ingress registries |
| [server/nodes/__init__.py](../../../../server/nodes/__init__.py) | 113 | Recursive plugin discovery |
| [server/services/ai.py](../../../../server/services/ai.py) | 2,793 | `_build_tool_from_node`, `_get_tool_schema`, the in-process tool surface and rebind closures |
| [server/services/agent_runtime.py](../../../../server/services/agent_runtime.py) | 494 | `AgentToolSpec`, `run_native_agent_loop` |
| [server/services/agent_bindings.py](../../../../server/services/agent_bindings.py) | 140 | `node_tools`, `is_runtime_tool`, `rebind_allowed`, `extend_runtime_graph` |
| [server/services/handlers/tools.py](../../../../server/services/handlers/tools.py) | 1,128 | `execute_tool`, `_dispatch_tool` (in-process routing), `_execute_delegated_agent` (in-process delegation) |
| [server/services/tool_output.py](../../../../server/services/tool_output.py) | 108 | Cap on what one tool result adds to the conversation |
| [server/services/tool_identity.py](../../../../server/services/tool_identity.py) | 74 | Duplicate tool-name error |
| [server/services/skill_runtime.py](../../../../server/services/skill_runtime.py) | 325 | The Skill tool: catalogue, load, read and search resources |
| [server/services/temporal/agent_activities.py](../../../../server/services/temporal/agent_activities.py) | 2,905 | `prepare_agent_payload` (tools payload), `refresh_agent_tools`, `invoke_agent_skill`, `collect_agent_activities` |
| [server/services/temporal/agent_workflow.py](../../../../server/services/temporal/agent_workflow.py) | 3,549 | `AgentWorkflow`: tool dispatch, delegation children, hot rebind; `DelegatedTaskWorkflow` |
| [server/services/temporal/worker.py](../../../../server/services/temporal/worker.py) | 960 | Worker construction, what each worker registers |
| [server/services/temporal/plugin_registry.py](../../../../server/services/temporal/plugin_registry.py) | 82 | `register_temporal_plugin` |
| [server/services/temporal/workflow_registry.py](../../../../server/services/temporal/workflow_registry.py) | 38 | `framework_workflows`, `replay_workflows` |
| [server/services/temporal/plugin_activities.py](../../../../server/services/temporal/plugin_activities.py) | 144 | Per-plugin activity collection |
| [server/services/temporal/workflow.py](../../../../server/services/temporal/workflow.py) | 1,291 | `AGENT_WORKFLOW_TYPES`, `_resolve_dispatch` |
| [server/services/workflow_ops.py](../../../../server/services/workflow_ops.py) | 334 | Workflow-ops wire format, push broadcast |
| [client/src/lib/workflowOps.ts](../../../../client/src/lib/workflowOps.ts) | 380 | The editor's applier for a workflow-ops batch |
| [client/src/hooks/useWorkflowOpsListener.ts](../../../../client/src/hooks/useWorkflowOpsListener.ts) | 117 | The two listeners for the `workflow_ops_apply` push |
| [server/services/graph_build.py](../../../../server/services/graph_build.py) | 402 | Labels, ids, edges, `GraphAdditions`, `add_to_graph` |
| [server/services/workflow_storage/mutate.py](../../../../server/services/workflow_storage/mutate.py) | 272 | `apply_graph_additions` |
| [server/nodes/tool/agent_builder/__init__.py](../../../../server/nodes/tool/agent_builder/__init__.py) | 1,391 | Agent Builder: ten operations |
| [server/nodes/tool/task_manager/__init__.py](../../../../server/nodes/tool/task_manager/__init__.py) | 373 | Task Manager: the tool that reads the teammate list |
| [server/nodes/mcp/mcp_connector.py](../../../../server/nodes/mcp/mcp_connector.py) | 183 | One node, one tool per binding |
| [server/nodes/chat/chat_ui/__init__.py](../../../../server/nodes/chat/chat_ui/__init__.py) | 118 | `show_ui`: spec checked, refused with reasons |
| [server/services/genui/spec.py](../../../../server/services/genui/spec.py) | 524 | `check_spec`, `SpecError` |
| [server/services/employees/builder.py](../../../../server/services/employees/builder.py) | 909 | Hire's graph builder |
| [server/services/employees/team_recipe.py](../../../../server/services/employees/team_recipe.py) | 310 | Lead plus specialists recipe |
| [server/services/employees/hire.py](../../../../server/services/employees/hire.py) | 376 | `hire_employee` |
| [server/services/employees/policy.py](../../../../server/services/employees/policy.py) | 201 | `check_tool`, `check_skill`, `BASE_TOOLS` |
| [server/services/employees/apps.py](../../../../server/services/employees/apps.py) | 352 | App registry, `register_app_source` |
| [server/services/employees/upgrade.py](../../../../server/services/employees/upgrade.py) | 286 | Upgrading older hires |
| [server/services/employees/safe_apply.py](../../../../server/services/employees/safe_apply.py) | 363 | Applying a saved graph to a live generation |
| [server/services/employees/permissions.py](../../../../server/services/employees/permissions.py) | 105 | Owner grants for Agent Builder changes |
| [server/services/workflow_validator.py](../../../../server/services/workflow_validator.py) | 419 | `validate_workflow` |
| [server/services/node_allowlist.py](../../../../server/services/node_allowlist.py) | 156 | Allowlist config, `is_hire_allowed` |
| [server/services/approvals/tool_calls.py](../../../../server/services/approvals/tool_calls.py) | 788 | Holding a tool call for the owner |
| [server/services/approvals/classes.py](../../../../server/services/approvals/classes.py) | 561 | Central table of call classes |
| [server/services/builder_documentation.py](../../../../server/services/builder_documentation.py) | 135 | Bounded doc search and read for the Agent Builder |

## 3. The plugin system: what a plugin can and cannot add

### 3.1 How a plugin loads

- Discovery is [`_discover()`](../../../../server/nodes/__init__.py#L91): `pkgutil.walk_packages` over the `nodes` package, skipping any module or package whose name segment starts with `_`. A failing import is logged with `logger.exception` and skipped, it does not stop startup ([nodes/__init__.py:103](../../../../server/nodes/__init__.py#L103)). It runs when the package is first imported ([nodes/__init__.py:112](../../../../server/nodes/__init__.py#L112)).
- The first import: [routers/schemas.py:13](../../../../server/routers/schemas.py#L13) imports `nodes._visuals` at module top, and [main.py:77](../../../../server/main.py#L77) imports the routers. The lifespan repeats `import nodes` at [main.py:132](../../../../server/main.py#L132). INFERRED: discovery runs while `main` is imported, before the lifespan starts. The standalone worker and the replay gate import `nodes` themselves ([worker.py:854](../../../../server/services/temporal/worker.py#L854), [workflow_registry.py:32](../../../../server/services/temporal/workflow_registry.py#L32)).
- Registration is [`BaseNode.__init_subclass__`](../../../../server/services/plugin/base.py#L453): it collects `@Operation` methods, returns early for `abstract=True` or an empty `type`, sets handle defaults for tool-oriented classes, derives `delegate_to_<type>` as the tool name of a `component_kind == "agent"` class, then calls `register_node_class` and `register_node` ([base.py:503](../../../../server/services/plugin/base.py#L503)).
- [`register_node`](../../../../server/services/node_registry.py#L44) writes `NODE_METADATA`, `_DIRECT_MODELS`, `NODE_OUTPUT_SCHEMAS` and `_HANDLER_REGISTRY`, and drops the cached NodeSpec and input schema for that type.
- The Temporal activity is produced on demand: [`collect_plugin_activities`](../../../../server/services/temporal/plugin_activities.py#L37) walks the class registry and calls `cls.as_activity()`, which names it `node.{type}.v{version}` ([base.py:1224](../../../../server/services/plugin/base.py#L1224)). Polling triggers add `poll.{type}.v{version}` ([plugin_activities.py:92](../../../../server/services/temporal/plugin_activities.py#L92)).

### 3.2 What a node class declares

| Declaration | Defined at | Effect |
|---|---|---|
| `type`, `version` | [base.py:351](../../../../server/services/plugin/base.py#L351) | Registry key; activity name |
| `display_name`, `group`, `handles`, `visibility`, `ui_hints`, `component_kind` | [base.py:353](../../../../server/services/plugin/base.py#L353) | NodeSpec, built by [`_metadata_dict`](../../../../server/services/plugin/base.py#L517) |
| `Params`, `Output` | [base.py:364](../../../../server/services/plugin/base.py#L364) | Input validation; output contract enforced in [`_serialize_result`](../../../../server/services/plugin/base.py#L1076) |
| `credentials` | [base.py:366](../../../../server/services/plugin/base.py#L366) | `ctx.connection(id)`; a routed operation uses `credentials[0]` ([base.py:1045](../../../../server/services/plugin/base.py#L1045)) |
| `task_queue`, `retry_policy`, `start_to_close_timeout`, `heartbeat_timeout` | [base.py:368](../../../../server/services/plugin/base.py#L368) | Temporal options for the node's activity (section 4.5) |
| `usable_as_tool` | [base.py:375](../../../../server/services/plugin/base.py#L375) | An ActionNode becomes a tool; main handles hidden unless declared ([base.py:472](../../../../server/services/plugin/base.py#L472)); an `output-tool` handle is added ([base.py:574](../../../../server/services/plugin/base.py#L574)) |
| `tool_name`, `tool_description` | [base.py:416](../../../../server/services/plugin/base.py#L416) | Model-facing name and description |
| `ToolInput`, `tool_schema_locked`, `server_controlled_fields` | [tool.py:148](../../../../server/services/plugin/tool.py#L148) | Separate model-argument schema; schema pinned against stored overrides; fields the model may never set |
| `tool_bindings(parameters)` | [tool.py:166](../../../../server/services/plugin/tool.py#L166) | One node yields several tools |
| `needs_canvas` | [base.py:394](../../../../server/services/plugin/base.py#L394) | Temporal tool calls receive the run's `nodes` and `edges` |
| `context_keys` | [base.py:400](../../../../server/services/plugin/base.py#L400) | Extra server-owned keys copied into `ctx.raw` on both paths |
| `tool_error_fields` | [base.py:380](../../../../server/services/plugin/base.py#L380) | Extra fields kept on a tool error payload |
| `requires_context` | [base.py:385](../../../../server/services/plugin/base.py#L385) | May be the target of a Context edge |
| `approval` | [base.py:438](../../../../server/services/plugin/base.py#L438) | An `ApprovalSpec` (section 4.7) |
| `app_field` | [base.py:444](../../../../server/services/plugin/base.py#L444) | The parameter that names the node's app (custom connectors) |
| `answers_chat_run`, `chat_step`, `chat_step_hidden`, `chat_sources` | [base.py:428](../../../../server/services/plugin/base.py#L428) | How a call shows in the owner's chat |
| `reset_execution_state(...)` classmethod | [base.py:329](../../../../server/services/plugin/base.py#L329) | Called on Reset ([runtime_state.py:44](../../../../server/services/deployment/runtime_state.py#L44)) |
| `validate_graph(node_id, graph)` classmethod | [base.py:347](../../../../server/services/plugin/base.py#L347) | Extra validator errors ([workflow_validator.py:318](../../../../server/services/workflow_validator.py#L318)) |
| `prepare_deployment(...)` classmethod (triggers) | [trigger.py:52](../../../../server/services/plugin/trigger.py#L52) | Called when a deployment arms the trigger |
| `supports_delegation`, `workspace_task` | read with `getattr` | Not declared on `BaseNode`. `supports_delegation` makes [`is_registered_agent`](../../../../server/services/workspace_capabilities.py#L22) true; `workspace_task` changes activity options ([agent_workflow.py:146](../../../../server/services/temporal/agent_workflow.py#L146)) |

Two uiHints are derived: `isConfigNode` for a group containing `memory` or `tool` ([base.py:258](../../../../server/services/plugin/base.py#L258)), and `executionTimeoutMs` from `start_to_close_timeout` ([base.py:624](../../../../server/services/plugin/base.py#L624)).

### 3.3 The registries

Forty lines start with `def register_` under server/services and server/core. Thirty-nine are in this table; the fortieth, `register_batch`, is listed after it. A plugin (or a core package) calls these from its package on import.

| Function | Registers | Read by |
|---|---|---|
| [`register_node`, `register_node_class`](../../../../server/services/node_registry.py#L44) | A node's metadata, models, handler, class | `NodeExecutor`, NodeSpec, the Temporal worker; called by `__init_subclass__` |
| [`register_group`](../../../../server/services/node_registry.py#L157) | Palette group metadata | `list_node_groups`; called from [server/nodes/groups.py](../../../../server/nodes/groups.py) |
| [`register_ws_handlers`](../../../../server/services/ws_handler_registry.py#L102) | WebSocket message type to handler | The WebSocket router through `get_ws_handlers` |
| [`register_router`](../../../../server/services/ws_handler_registry.py#L130) | A FastAPI router | The mount loop at [main.py:672](../../../../server/main.py#L672) |
| [`register_option_loader`](../../../../server/services/ws_handler_registry.py#L155) | A `loadOptionsMethod` loader | `dispatch_load_options` |
| [`register_oauth_callback_path`](../../../../server/services/ws_handler_registry.py#L222) | A provider's OAuth callback path | `get_oauth_callback_path`, called at [oauth_utils.py:48](../../../../server/services/oauth_utils.py#L48) |
| [`register_public_ingress_path`](../../../../server/services/ws_handler_registry.py#L252) | A route that needs no session cookie | `is_public_ingress_path`, called by the auth middleware at [auth.py:100](../../../../server/middleware/auth.py#L100) |
| [`register_filter_builder`](../../../../server/services/event_waiter.py#L286) | A trigger's event filter | `event_waiter.build_filter` |
| [`register_trigger_precheck`](../../../../server/services/event_waiter.py#L335) | A check before a trigger waits | `run_trigger_precheck` |
| [`register_canary_trigger_type`](../../../../server/services/deployment/canary_registry.py#L55) | A trigger type and its CloudEvents type | The deployment manager |
| [`register_poll_coroutine_factory`](../../../../server/services/deployment/poll_registry.py#L43) | A polling coroutine factory | The deployment manager |
| [`register_webhook_source`](../../../../server/services/events/webhook.py#L68) | A webhook source for a path | The webhook router |
| [`register_output_schema`](../../../../server/services/node_output_schemas.py#L817) | An output model for a node type | NodeSpec |
| [`register_service_refresh`](../../../../server/services/status_broadcaster.py#L1385) | A status refresh callback | One refresh cycle at lifespan startup |
| [`register_disconnect_listener`](../../../../server/services/status_broadcaster.py#L1401) | A callback on socket disconnect | The status broadcaster |
| [`register_master_skill_expander`](../../../../server/services/plugin/edge_walker.py#L123) | How a Skills node expands | `_append_skill_entries` |
| [`register_agent_context_builder`](../../../../server/services/plugin/edge_walker.py#L154) | The Context descriptor builder | `collect_agent_connections` |
| [`register_social_send_handler`](../../../../server/services/plugin/social_provider_registry.py#L52) | A platform's send handler | The social send node |
| [`register_shutdown_hook`](../../../../server/services/plugin/shutdown_hooks.py#L66) | An async teardown | `run_shutdown_hooks` at [main.py:518](../../../../server/main.py#L518) |
| [`register_service_factory`](../../../../server/services/plugin/service_factories.py#L57) | A factory for a container provider | [core/container.py:94](../../../../server/core/container.py#L94) |
| [`register_supervisor`](../../../../server/services/_supervisor/registry.py#L25) | A process supervisor | `shutdown_all_supervisors` at [main.py:532](../../../../server/main.py#L532) |
| [`register_temporal_plugin`](../../../../server/services/temporal/plugin_registry.py#L46) | A Temporal `SimplePlugin` | Main-queue workers and the replayer (section 3.4) |
| [`register_app_source`](../../../../server/services/employees/apps.py#L266) | A source of one owner's apps | `source_apps`, then Hire and the Agent Builder |
| [`register_node_state_source`](../../../../server/services/employees/node_signals.py#L32) | Live node state for the employee card | `node_state` |
| [`register_port_source`](../../../../server/services/netpolicy.py#L83) | A plugin's live ports | The network policy |
| [`register_workflow_deleted_hook`](../../../../server/services/workflow_storage/hooks.py#L26) | Cleanup after a workflow delete | `run_workflow_deleted_hooks` |
| [`register_graph_listener`](../../../../server/services/workflow_storage/listeners.py#L27) | A callback after a saved graph change | `notify_graph_changed` |
| [`register_control_listener`](../../../../server/services/deployment/control.py#L95) | A callback after a control-status broadcast | `notify_control_changed` |
| [`register_conversation_listener`](../../../../server/services/agent_context/listeners.py#L38) | A callback after a conversation save | `notify_conversation_saved` |
| [`register_chat_cleared_listener`](../../../../server/services/chat_thread.py#L196) | A callback after the owner clears a chat | `clear_chat_session` |
| [`register_approval_listener`](../../../../server/services/approvals/listeners.py#L40) | A callback on an approval change | `notify_approval_changed` |
| [`register_rule_seeder`, `register_rule_listener`](../../../../server/services/approvals/rules.py#L66) | A workflow's default Ask first value; a callback on a rule change | `get_rule`, `notify_rule_changed` |
| [`register_provider`, `register_session_pool`, `register_skill_materialiser`](../../../../server/services/cli_agent/factory.py#L61) | A CLI agent provider, its session pool, its skill writer | The CLI agent service |
| [`register_provider`](../../../../server/services/llm/registry.py#L42) | An LLM provider spec | `ChatUnifier` through `get_provider` |
| [`register_log_source_tag`](../../../../server/core/logging.py#L72) | A short log tag for a logger prefix | `_resolve_source_tag` |

Not registries, though the name matches: [`register_batch`](../../../../server/services/cli_agent/mcp_server.py#L109) registers one CLI agent run's token and tools on the MCP bridge while that run lasts. Four more are async functions or methods: [`register_local_model`](../../../../server/services/model_registry.py#L303) (a model found on a local server), [`register_browser_owner`](../../../../server/services/browser_owners.py#L71) (this process's browser lease), [`register_task_execution`](../../../../server/services/temporal/agent_activities.py#L2598) (an activity) and [`register_search_attributes`](../../../../server/services/temporal/search_attributes.py#L128) (Temporal Search Attributes at startup). Workflow classes and the database also have methods named `register_*`; none is a plugin registry.

Most are built on [`IdempotentRegistry`](../../../../server/services/plugin/registry.py#L54): the same value for a key is a no-op, a different one raises `ValueError` at import. The listener registries are plain lists that skip a repeat.

### 3.4 What a plugin cannot do without editing core code

1. **Change how its tool call is dispatched on Temporal.** `AgentWorkflow` picks the route itself (section 4.5). The activity name is computed at [agent_workflow.py:2391](../../../../server/services/temporal/agent_workflow.py#L2391); the Task Manager's `delegation_request` result is handled by a branch on the node type at [agent_workflow.py:2557](../../../../server/services/temporal/agent_workflow.py#L2557).
2. **Change in-process dispatch.** [`_dispatch_tool`](../../../../server/services/handlers/tools.py#L161) is a fixed sequence.
3. **Be an agent to the engine.** The lists are literals: `AGENT_WORKFLOW_TYPES` ([temporal/workflow.py:115](../../../../server/services/temporal/workflow.py#L115)), `AI_AGENT_TYPES` ([constants.py:34](../../../../server/constants.py#L34)), the delegation schema tuple ([ai.py:2633](../../../../server/services/ai.py#L2633)), `TEAM_LEAD_TYPES` ([edge_walker.py:53](../../../../server/services/plugin/edge_walker.py#L53)). The one opening is `supports_delegation = True`, read by `is_registered_agent`; the validator's teammate check does not use it and reads `AI_AGENT_TYPES` only ([workflow_validator.py:222](../../../../server/services/workflow_validator.py#L222)).
4. **Add to the framework workflow or `agent.*` activity lists.** They are literals: [`framework_workflows`](../../../../server/services/temporal/workflow_registry.py#L6), [`collect_agent_activities`](../../../../server/services/temporal/agent_activities.py#L2659), [`_main_queue_activities`](../../../../server/services/temporal/worker.py#L648).
5. **Declare its own approval class or repeat safety.** Both are central tables keyed by node type: `CLASSES` and `CLASSES_WHEN` ([classes.py:35](../../../../server/services/approvals/classes.py#L35)) and `REPEATABLE` ([repeats.py:26](../../../../server/services/approvals/repeats.py#L26)). An unlisted type is `send` ([classes.py:32](../../../../server/services/approvals/classes.py#L32)) and is never repeated after a broken-off attempt ([repeats.py:16](../../../../server/services/approvals/repeats.py#L16)).
6. **Add a container provider.** `register_service_factory` only supplies the factory behind names the container already declares, `maps` and `android` ([core/container.py:113](../../../../server/core/container.py#L113)).
7. **Wrap every node execution.** [`InterceptorChain`](../../../../server/services/plugin/interceptor.py#L42) is defined and exported, and nothing calls it (section 9).
8. **Receive upstream outputs as `connected_outputs`.** The node types that get them are a literal set ([node_executor.py:359](../../../../server/services/node_executor.py#L359)).
9. **Be offered by Hire as an always-on tool or a specialist.** `BASE_TOOLS` ([policy.py:63](../../../../server/services/employees/policy.py#L63)) and `RESPONSIBILITIES` ([team_recipe.py:32](../../../../server/services/employees/team_recipe.py#L32)) are literals. App tools are open: [server/config/employee_apps.json](../../../../server/config/employee_apps.json) or `register_app_source`.
10. **Add a task queue, a palette group or a uiHint.** A queue must be in `TaskQueue.ALL` ([scaling.py:33](../../../../server/services/plugin/scaling.py#L33)); a group is declared in [server/nodes/groups.py](../../../../server/nodes/groups.py); a uiHint must be in the test's known set (section 8).

What a plugin can add to Temporal:

- **Its own activity.** Every node class gets `node.<type>.v<version>`, registered on the main-queue worker ([worker.py:638](../../../../server/services/temporal/worker.py#L638)) and on the pool worker of its `task_queue` ([worker.py:513](../../../../server/services/temporal/worker.py#L513)).
- **A workflow type.** `register_temporal_plugin(SimplePlugin(name=..., workflows=[...]))`. The one in-tree use is [cron_scheduler/__init__.py:43](../../../../server/nodes/scheduler/cron_scheduler/__init__.py#L43). The snapshot is passed as `plugins=` to the manager's worker ([worker.py:247](../../../../server/services/temporal/worker.py#L247)), the standalone worker ([worker.py:877](../../../../server/services/temporal/worker.py#L877)), `create_worker` ([worker.py:932](../../../../server/services/temporal/worker.py#L932)) and the replayer ([workflow_registry.py:36](../../../../server/services/temporal/workflow_registry.py#L36)). Per-queue pool workers are built without `plugins=` ([worker.py:531](../../../../server/services/temporal/worker.py#L531)).
- The registry's docstring says a `SimplePlugin` may also carry activities and interceptors ([plugin_registry.py:54](../../../../server/services/temporal/plugin_registry.py#L54)). No in-tree plugin does. NOT VERIFIED.

### 3.5 Lifecycle

- **Load:** once per process, at import. Re-import is tolerated: `IdempotentRegistry` treats a callable or class with the same module and qualified name as the same registration ([registry.py:111](../../../../server/services/plugin/registry.py#L111)), and `register_node` replaces the prior entry ([node_registry.py:54](../../../../server/services/node_registry.py#L54)).
- **Unload or reload at runtime:** none. A search for `def unregister` in non-test code finds [`unregister_provider`](../../../../server/services/cli_agent/factory.py#L79) ("Used by tests") and lease, batch and execution functions; nothing removes a node class, a WebSocket handler or a router. Routers are mounted once, at module level ([main.py:672](../../../../server/main.py#L672)). uvicorn reloads only when `settings.debug` is set ([main.py:872](../../../../server/main.py#L872)).
- **Temporal:** a `Worker` is built from the registries as they are at build time, and rebuilt from them after a crash ([worker.py:186](../../../../server/services/temporal/worker.py#L186), [worker.py:280](../../../../server/services/temporal/worker.py#L280)).
- **Shutdown:** [`run_shutdown_hooks`](../../../../server/services/plugin/shutdown_hooks.py#L89) runs hooks in registration order, each under a 10 second timeout, logging a failure and moving on. The process service and the supervisors stop after it ([main.py:518](../../../../server/main.py#L518)).
- **Long-lived state:** the [`ServiceSingleton`](../../../../server/services/plugin/singleton.py#L28) mixin, a supervisor registered with `register_supervisor`, or a module-level dict (the Skill tool's `_loaded`, [skill_runtime.py:21](../../../../server/services/skill_runtime.py#L21)). State tied to a run is cleared through `reset_execution_state`.

## 4. Tools: from node to model, from model to execution

### 4.1 Discovery

[`collect_agent_connections`](../../../../server/services/plugin/edge_walker.py#L185) walks the edges into one agent and returns `(context, skills, tools, input, task)`.

- A node on `input-tools` becomes a `tool_data` entry `{node_id, node_type, parameters, label}`; an agent on that handle also gets `child_tools` ([edge_walker.py:474](../../../../server/services/plugin/edge_walker.py#L474)).
- Parameters come from the database, or from the run's `parameter_snapshot` when the context carries one ([edge_walker.py:205](../../../../server/services/plugin/edge_walker.py#L205)).
- Entries added without an edge, each marked `builtin`: Task Manager for a team lead and for an employee's Talk contact ([edge_walker.py:313](../../../../server/services/plugin/edge_walker.py#L313)), and `employeeJobSource` for an employee job ([edge_walker.py:354](../../../../server/services/plugin/edge_walker.py#L354)). Show UI is removed while the owner's Generated UI switch is off ([edge_walker.py:338](../../../../server/services/plugin/edge_walker.py#L338)).
- Teammates on `input-teammates` are not in that tuple. [`collect_teammate_connections`](../../../../server/services/plugin/edge_walker.py#L707) returns them. Both prepare paths call it only for the two team-lead types ([agent_activities.py:1450](../../../../server/services/temporal/agent_activities.py#L1450), [nodes/agent/_inline.py:133](../../../../server/nodes/agent/_inline.py#L133)); in-process delegation calls it again when it creates the run's team ([tools.py:503](../../../../server/services/handlers/tools.py#L503)).
- Skills become one pseudo tool entry of type `_builtin_skill` ([skill_runtime.py:63](../../../../server/services/skill_runtime.py#L63)).

### 4.2 Name, description and schema

[`_build_tool_from_node`](../../../../server/services/ai.py#L2414) turns one entry into `(AgentToolSpec, config)`. A failure returns `(None, None)` and the node is left out ([ai.py:2596](../../../../server/services/ai.py#L2596)).

Name and description, in the order the code checks them:

1. A `tool_schema_locked` class: `cls.tool_name` (else the node type) and `cls.tool_description`. The stored row and the node's parameters are ignored ([ai.py:2503](../../../../server/services/ai.py#L2503)).
2. Else, when a stored `ToolSchema` row exists: `delegate_tool_name`, then the row's `tool_name`, then the class default, then `tool_<label>` ([ai.py:2512](../../../../server/services/ai.py#L2512)).
3. Else: `delegate_tool_name`, then the node parameter `tool_name`, then the class default, then `tool_<label>` ([ai.py:2520](../../../../server/services/ai.py#L2520)).

The class default is `cls.tool_name` with `cls.tool_description`, falling back to `cls.description` ([ai.py:2466](../../../../server/services/ai.py#L2466)). An agent with child tools gets a generated description listing them ([ai.py:2531](../../../../server/services/ai.py#L2531)). The name is cleaned to letters, digits and underscores ([ai.py:2556](../../../../server/services/ai.py#L2556)).

Schema, in [`_get_tool_schema`](../../../../server/services/ai.py#L2600):

1. A locked class: `tool_input_model()` ([ai.py:2620](../../../../server/services/ai.py#L2620)).
2. A stored `schema_config`: a model built from it ([ai.py:2625](../../../../server/services/ai.py#L2625)).
3. An agent type in the literal tuple: `{task, context}` ([ai.py:2633](../../../../server/services/ai.py#L2633)).
4. `_builtin_skill` and `_builtin_check_delegated_tasks`: fixed schemas ([ai.py:2676](../../../../server/services/ai.py#L2676)).
5. Any other plugin: `tool_input_model()` when the class has one, else `Params` ([ai.py:2712](../../../../server/services/ai.py#L2712)).
6. Otherwise one string field `input` ([ai.py:2719](../../../../server/services/ai.py#L2719)).

`ToolInput` defaults to `Params` for a ToolNode that declares none ([tool.py:156](../../../../server/services/plugin/tool.py#L156)). The JSON schema has `$ref` inlined by [`inline_schema_refs`](../../../../server/services/plugin/tool.py#L24). `ToolNode.as_tool_schema` is called only by tests.

The stored `ToolSchema` override is read at [ai.py:2501](../../../../server/services/ai.py#L2501). Its writer is the `save_tool_schema` WebSocket handler ([websocket.py:263](../../../../server/routers/websocket.py#L263)); a search of client/src finds no caller.

Names must be unique on one agent: [`ensure_unique_tool_names`](../../../../server/services/tool_identity.py#L70) in process ([ai.py:1166](../../../../server/services/ai.py#L1166)), `_duplicate_visible_tool_name_error` on Temporal ([agent_workflow.py:525](../../../../server/services/temporal/agent_workflow.py#L525)).

### 4.3 One node, many tools

[`node_tools`](../../../../server/services/agent_bindings.py#L10) is what every caller uses. It asks the class for `tool_bindings(parameters)`. `None` means one tool from the class. A list means one tool per [`ToolBinding`](../../../../server/services/plugin/tool.py#L110): the binding supplies the name, description and JSON schema, `args_schema` is `None`, the call's settings are the node's with the binding's `parameters` over them, and `binding_key` is `<node_id>:<key>`. A failure to read the bindings drops the node ([agent_bindings.py:22](../../../../server/services/agent_bindings.py#L22)).

The one implementation is the custom connector: [`McpConnectorNode.tool_bindings`](../../../../server/nodes/mcp/mcp_connector.py#L93) returns one binding per usable, enabled remote tool, with `mcp_tool`, `mcp_ask`, `mcp_label` and `mcp_title` in its parameters; those four and `mcp_connector` are `server_controlled_fields` ([mcp_connector.py:80](../../../../server/nodes/mcp/mcp_connector.py#L80)).

### 4.4 What the model receives, per runtime

- **In process:** `execute_agent` builds `AgentToolSpec` values and a name-to-config map ([ai.py:1100](../../../../server/services/ai.py#L1100)), then hands the list to `run_native_agent_loop`. Each step sends only the `ToolDef` declarations ([agent_runtime.py:132](../../../../server/services/agent_runtime.py#L132)).
- **On Temporal:** `prepare_agent_payload` builds one JSON entry per tool ([agent_activities.py:1607](../../../../server/services/temporal/agent_activities.py#L1607)); the fields are in section 6. Each LLM step sends the `definition` of every entry not marked `llm_hidden` ([agent_workflow.py:1322](../../../../server/services/temporal/agent_workflow.py#L1322)).
- **Hidden entries:** a team lead's `delegate_to_*` entries are `llm_hidden` on Temporal ([agent_activities.py:1652](../../../../server/services/temporal/agent_activities.py#L1652)). In process, `execute_chat_agent` (the method team leads run through) does not build them at all when Task Manager is bound ([ai.py:1758](../../../../server/services/ai.py#L1758)).
- **Dropped for one run:** search tools when the chat's Web option is off ([agent_activities.py:1098](../../../../server/services/temporal/agent_activities.py#L1098)).
- **Virtual entries:** a Coding Agent in workspace mode gets tools with no canvas node, marked `virtual_workspace` with an `activity_name` ([coding_workspace_tools.py:58](../../../../server/services/coding_workspace_tools.py#L58)). They are built only for that agent type under one harness flag ([agent_activities.py:1659](../../../../server/services/temporal/agent_activities.py#L1659)).
- **Runtime facts in descriptions:** with `runtime_behavior_version` 1, `enrich_runtime_tools` appends platform and path facts to some tools' descriptions ([runtime_tool_context.py:104](../../../../server/services/runtime_tool_context.py#L104)).

### 4.5 Routing a call

In process, [`run_native_agent_loop`](../../../../server/services/agent_runtime.py#L205):

- An unknown name returns `{"error": "Unknown tool", "details": ...}` to the model ([agent_runtime.py:362](../../../../server/services/agent_runtime.py#L362)).
- Arguments are validated against `args_schema` when there is one; a failure returns `{"error": "Invalid tool arguments", "details": ...}` ([agent_runtime.py:370](../../../../server/services/agent_runtime.py#L370)). Only fields the model supplied are kept ([agent_runtime.py:198](../../../../server/services/agent_runtime.py#L198)).
- The provider's call id is put on the trusted config, not in the arguments ([agent_runtime.py:384](../../../../server/services/agent_runtime.py#L384)).
- An exception from the tool becomes `{"error": str(exc)}`, or the `NodeUserError` fields ([agent_runtime.py:391](../../../../server/services/agent_runtime.py#L391)). An error with `requires_user_action` ends the run after the turn is saved ([agent_runtime.py:451](../../../../server/services/agent_runtime.py#L451)).

Then [`execute_tool`](../../../../server/services/handlers/tools.py#L90) broadcasts the node's status and calls `_dispatch_tool`, which checks in this order: `_builtin_skill` ([tools.py:213](../../../../server/services/handlers/tools.py#L213)); any registered plugin that is not an agent, through `check_in_process` and then `execute_as_tool` ([tools.py:237](../../../../server/services/handlers/tools.py#L237)); the built-in delegated-task check ([tools.py:308](../../../../server/services/handlers/tools.py#L308)); a registered agent, through `_execute_delegated_agent` ([tools.py:315](../../../../server/services/handlers/tools.py#L315)); else a generic echo ([tools.py:320](../../../../server/services/handlers/tools.py#L320)).

On Temporal, `AgentWorkflow` handles each call in the loop starting at [agent_workflow.py:2187](../../../../server/services/temporal/agent_workflow.py#L2187):

- Arguments that were not valid JSON, or a name not in the tool index, return an error message to the model without scheduling anything ([agent_workflow.py:2193](../../../../server/services/temporal/agent_workflow.py#L2193), [agent_workflow.py:2213](../../../../server/services/temporal/agent_workflow.py#L2213)).
- A name starting `delegate_to_` is a delegation ([agent_workflow.py:2254](../../../../server/services/temporal/agent_workflow.py#L2254)). When the target's type is in `AGENT_WORKFLOW_TYPES` its result comes from a child workflow ([agent_workflow.py:2420](../../../../server/services/temporal/agent_workflow.py#L2420)); otherwise it runs as that node's activity with the task mapped into `system_message` and `prompt` ([agent_workflow.py:2284](../../../../server/services/temporal/agent_workflow.py#L2284)).
- Any other call's activity name is chosen at [agent_workflow.py:2391](../../../../server/services/temporal/agent_workflow.py#L2391):

```python
tool_activity_name = (
    tool_info["activity_name"] if tool_info.get("virtual_workspace") else "agent.skill.invoke"
    if tool_info["node_type"] == "_builtin_skill"
    else f"node.{tool_info['node_type']}.v{tool_info['version']}"
)
```

- The activity input carries the merged `node_data` and the model's unmerged `tool_args` ([agent_workflow.py:2322](../../../../server/services/temporal/agent_workflow.py#L2322)).
- Options: by default the plugin's declared timeouts and retry policy, with no queue ([agent_workflow.py:111](../../../../server/services/temporal/agent_workflow.py#L111)). In a controlled generation (`execution_control_version` 1, and the other versioned runtimes tested at [agent_workflow.py:138](../../../../server/services/temporal/agent_workflow.py#L138)) the policy frozen at prepare time, with the queue when the worker pool is on ([agent_workflow.py:159](../../../../server/services/temporal/agent_workflow.py#L159), [agent_activities.py:67](../../../../server/services/temporal/agent_activities.py#L67)).
- Task Manager has two branches of its own: its calls in one turn are scheduled together first ([agent_workflow.py:2059](../../../../server/services/temporal/agent_workflow.py#L2059)), and a `delegation_request` in its result starts the teammate's run ([agent_workflow.py:2557](../../../../server/services/temporal/agent_workflow.py#L2557)).
- An activity that fails after its retries becomes `{"error": "<Type>: <message>"}` for the model ([agent_workflow.py:2815](../../../../server/services/temporal/agent_workflow.py#L2815)). The same `except` clause ([agent_workflow.py:2761](../../../../server/services/temporal/agent_workflow.py#L2761)) covers a failed delegation child and an error raised by the Task Manager branch.

Order and concurrency within one turn:

- Both loops take the calls of a turn in the model's order and wait for each tool before the next ([agent_runtime.py:361](../../../../server/services/agent_runtime.py#L361), [agent_workflow.py:2187](../../../../server/services/temporal/agent_workflow.py#L2187), [agent_workflow.py:2527](../../../../server/services/temporal/agent_workflow.py#L2527)).
- On Temporal two kinds of call start before that loop: every Task Manager `assign_task` call of the turn, as activities awaited together ([agent_workflow.py:2127](../../../../server/services/temporal/agent_workflow.py#L2127)); and delegation children, up to `max_concurrent_subagents`, the next one starting as each finishes ([agent_workflow.py:2175](../../../../server/services/temporal/agent_workflow.py#L2175), [agent_workflow.py:2507](../../../../server/services/temporal/agent_workflow.py#L2507)).

How a delegation runs:

- **Temporal, a direct `delegate_to_*` call.** The child is a child workflow with the id `<agent workflow id>-delegate-<node id>-<iteration>-<call index>` ([agent_workflow.py:290](../../../../server/services/temporal/agent_workflow.py#L290)). Its input carries the teammate entry's parameters, the task and context as `invocation`, and the parent's `nodes` and `edges` ([agent_workflow.py:1748](../../../../server/services/temporal/agent_workflow.py#L1748)). The parent waits for it, and the model reads `{success, status: "submitted" or "failed", result, usage}` in the same turn ([agent_workflow.py:2499](../../../../server/services/temporal/agent_workflow.py#L2499)). When the run has a team id, each child first takes a permit keyed by the root execution, limit `max_concurrent_subagents`, from the activity `agent.acquire_subagent_permit` ([agent_workflow.py:1831](../../../../server/services/temporal/agent_workflow.py#L1831)). A team is created at prepare time for a lead type that has teammates ([agent_activities.py:1473](../../../../server/services/temporal/agent_activities.py#L1473)).
- **Temporal, Task Manager `assign_task`.** The tool's result carries a `delegation_request`. The workflow looks the assignee up in its tool index by `delegate_name`, which is what a lead's hidden `delegate_to_*` entries are for, and refuses one whose node id or type does not match ([agent_workflow.py:1936](../../../../server/services/temporal/agent_workflow.py#L1936)). It starts `DelegatedTaskWorkflow` with parent close policy `ABANDON` ([agent_workflow.py:2046](../../../../server/services/temporal/agent_workflow.py#L2046)), and the model reads `delegation_status: "queued"` with no result ([agent_workflow.py:2573](../../../../server/services/temporal/agent_workflow.py#L2573)). The runner takes the permit, runs the teammate's agent workflow as its own child and records the outcome with `agent.finish_delegation` ([agent_workflow.py:553](../../../../server/services/temporal/agent_workflow.py#L553)).
- **In process.** `_execute_delegated_agent` starts the child as a background asyncio task and returns `{status: "delegated", task_id}` at once; the model reads the result later through `check_delegated_tasks` ([tools.py:900](../../../../server/services/handlers/tools.py#L900), [tools.py:923](../../../../server/services/handlers/tools.py#L923)). A semaphore per root execution caps concurrency ([tools.py:485](../../../../server/services/handlers/tools.py#L485)), and the same task sent to the same teammate while the first is still running answers `ALREADY_DELEGATED` ([tools.py:443](../../../../server/services/handlers/tools.py#L443)). A config key, `delegation_wait_seconds`, makes the call wait for the child ([tools.py:423](../../../../server/services/handlers/tools.py#L423)); in the Python tree only tests set it.
- **Depth.** `max_delegation_depth` is at most 2 ([agent_workflow.py:1592](../../../../server/services/temporal/agent_workflow.py#L1592)). Past it the model reads `{"error": "Maximum delegation depth <n> exceeded."}` ([agent_workflow.py:2273](../../../../server/services/temporal/agent_workflow.py#L2273)).

Inside the activity, [`as_activity`](../../../../server/services/plugin/base.py#L1205) runs, in order: the pre-executed and disabled short cuts; the stopped-chat-run answer ([base.py:1288](../../../../server/services/plugin/base.py#L1288)); the approval check ([base.py:1319](../../../../server/services/plugin/base.py#L1319)); the refusal of a later attempt that is not safe to repeat ([base.py:1346](../../../../server/services/plugin/base.py#L1346)); a 30 second heartbeat loop when the declared attempt is longer than the heartbeat timeout ([base.py:1383](../../../../server/services/plugin/base.py#L1383)); then `workflow_service.execute_node` ([base.py:1451](../../../../server/services/plugin/base.py#L1451)), which reaches the class's legacy handler.

### 4.6 `execute_as_tool` versus merged parameters

The legacy handler decides ([base.py:665](../../../../server/services/plugin/base.py#L665)): a ToolNode whose context carries `tool_args` goes to `execute_as_tool`; everything else goes to `execute` with the merged parameters. In process, every plugin tool goes to `execute_as_tool` ([tools.py:272](../../../../server/services/handlers/tools.py#L272)).

[`execute_as_tool`](../../../../server/services/plugin/base.py#L885) has three cases:

| Class | Validation | What the operation receives |
|---|---|---|
| ActionNode with `usable_as_tool` | `Params` on `{**node_params, **model_args}`, locked fields removed from the model's side | The merged `Params` object ([base.py:923](../../../../server/services/plugin/base.py#L923)) |
| ToolNode, `ToolInput is Params` | `ToolInput` on the model's arguments, a partial model on the saved ones, then `Params` on the merge | The merged `Params` object ([base.py:951](../../../../server/services/plugin/base.py#L951)) |
| ToolNode with its own `ToolInput` | `ToolInput` on the model's arguments, `Params` on the saved ones, never merged | The `ToolInput` object; the saved configuration is at `ctx.raw["_tool_config"]` ([base.py:969](../../../../server/services/plugin/base.py#L969)) |

A validation failure returns `{"success": False, "error": "Invalid tool input/configuration: ...", "error_type": "ValidationError"}` ([base.py:973](../../../../server/services/plugin/base.py#L973)). A ToolNode returns a flat dict; an ActionNode's `{success, result}` envelope is unwrapped; an error keeps `success: False`, `error`, `error_type`, `hint`, `requires_user_action`, `retryable` and the class's `tool_error_fields` ([base.py:1009](../../../../server/services/plugin/base.py#L1009)).

`server_controlled_fields` is enforced in three places: `execute_as_tool` (above); [`NodeExecutor.prepare_parameters`](../../../../server/services/node_executor.py#L231), which strips them from `tool_args` and puts the saved value back ([node_executor.py:297](../../../../server/services/node_executor.py#L297)); and the approval check, which judges a call on the settings it will run with ([tool_calls.py:153](../../../../server/services/approvals/tool_calls.py#L153)).

### 4.7 Approval and Ask first

A class declares an [`ApprovalSpec`](../../../../server/services/plugin/approval.py#L39): which operations send, the recipient and body fields, `refuse_while_asking`, `restrict_while_asking`.

- **Temporal:** `as_activity` calls [`check`](../../../../server/services/approvals/tool_calls.py#L244). No rule row (an editor-built workflow) means the call runs. With Ask first on, a call that sends is held as a row and the model reads `HELD_MESSAGE`; a refusing spec answers `REFUSED_MESSAGE`; a restricting spec runs with the restricted settings.
- **A generation with `approval_wait_version` 1:** [`_check_by_class`](../../../../server/services/approvals/tool_calls.py#L293) asks by the workflow's mode and the call's class, for every tool, spec or not. The modes are `outside`, `everything`, `money_only` ([rules.py:47](../../../../server/services/approvals/rules.py#L47)).
- **In process:** [`check_in_process`](../../../../server/services/approvals/tool_calls.py#L318). In an older generation a call that sends is refused, not held.
- **Revoked access:** `assert_runtime_access` raises a `NodeUserError` when the owner removed a grant ([permissions.py:86](../../../../server/services/employees/permissions.py#L86)); it is called before a tool in process ([ai.py:1251](../../../../server/services/ai.py#L1251)) and in `NodeExecutor.execute` ([node_executor.py:144](../../../../server/services/node_executor.py#L144)).

### 4.8 Result size

- The node's own result: a warning at 524,288 bytes, a `NodeUserError` at 2,097,152 bytes ([base.py:1144](../../../../server/services/plugin/base.py#L1144), [limits.py:26](../../../../server/services/media/limits.py#L26)).
- The copy the model reads: cut to `tool_result_max_chars` with a note saying how much was cut ([tool_output.py:64](../../../../server/services/tool_output.py#L64)), in process at [agent_runtime.py:425](../../../../server/services/agent_runtime.py#L425) and on Temporal at [agent_workflow.py:2601](../../../../server/services/temporal/agent_workflow.py#L2601). Exempt: `_builtin_skill`, `_builtin_check_delegated_tasks`, `taskManager`, `employeeJobSource` and every type in `AI_AGENT_TYPES` ([tool_output.py:34](../../../../server/services/tool_output.py#L34)). A delegation is exempt on Temporal by its name prefix.

### 4.9 Growing the surface mid-run, and loading on demand

**Hot rebind.** When a tool result is a dict with a non-empty `operations` list, both loops try to bind the `add_node` operations in it as new tools.

- In process: [agent_runtime.py:405](../../../../server/services/agent_runtime.py#L405) calls the agent's `_rebind_from_operations` closure ([ai.py:1334](../../../../server/services/ai.py#L1334)); new tools join the list after the turn ([agent_runtime.py:461](../../../../server/services/agent_runtime.py#L461)).
- On Temporal: the workflow schedules `agent.refresh_tools` ([agent_workflow.py:2675](../../../../server/services/temporal/agent_workflow.py#L2675)), and [`refresh_agent_tools`](../../../../server/services/temporal/agent_activities.py#L1910) returns entries in the prepare-time shape, which the workflow appends to `tools` and its name index ([agent_workflow.py:2739](../../../../server/services/temporal/agent_workflow.py#L2739)).
- Both are gated by the owner setting `auto_rebind_tools_after_canvas_change`, default on ([ai.py:1322](../../../../server/services/ai.py#L1322), [agent_activities.py:1756](../../../../server/services/temporal/agent_activities.py#L1756)).
- An operation is bound only if: its type is `add_node`; [`rebind_allowed`](../../../../server/services/agent_bindings.py#L92) finds an edge from that node into the calling agent's `input-tools` or `input-teammates` (it allows everything when no calling agent id is given); the class passes [`is_runtime_tool`](../../../../server/services/agent_bindings.py#L58); the node is not already bound.
- A duplicate name rejects the batch: the model reads `rebind_error` in process ([agent_runtime.py:414](../../../../server/services/agent_runtime.py#L414)) or a `DuplicateToolNameError` payload on Temporal ([agent_workflow.py:2704](../../../../server/services/temporal/agent_workflow.py#L2704)).
- New delegates produce a system message listing the connected teammates ([agent_runtime.py:443](../../../../server/services/agent_runtime.py#L443), [agent_workflow.py:2743](../../../../server/services/temporal/agent_workflow.py#L2743)).
- The docstring of `refresh_agent_tools` says the only producer of `operations` today is the Agent Builder ([agent_activities.py:1914](../../../../server/services/temporal/agent_activities.py#L1914)).

**The Skill tool.** Standard skills add nothing to the system prompt ([skill_prompt.py:15](../../../../server/services/skill_prompt.py#L15)); a skill whose name ends `-personality` replaces it ([skill_runtime.py:52](../../../../server/services/skill_runtime.py#L52)).

- The catalogue is the tool's description: one line `- name: description` per connected skill, cut at 7,000 characters ([skill_runtime.py:74](../../../../server/services/skill_runtime.py#L74)).
- Arguments: `action` (`load`, `read_resource`, `search_resource`), `skill_name`, `path`, `query`, `cursor`, `limit` ([ai.py:2679](../../../../server/services/ai.py#L2679)).
- `load` returns `{status, skill_name, content_hash, instructions, resources}`; a second load of unchanged text returns `already_loaded` without the body ([skill_runtime.py:231](../../../../server/services/skill_runtime.py#L231)). A resource can be read only after its skill was loaded ([skill_runtime.py:240](../../../../server/services/skill_runtime.py#L240)).
- "Loaded" is kept in a module dict keyed by `(workflow_id, execution_id, parent_node_id, skill name)` ([skill_runtime.py:21](../../../../server/services/skill_runtime.py#L21)). INFERRED: it is per process and does not survive a restart or a second worker.
- Errors are `SkillRuntimeError` codes: `DUPLICATE_CONNECTED_SKILL_NAME`, `SKILL_NOT_CONNECTED`, `SKILL_NOT_FOUND`, `SKILL_NOT_LOADED`, `INVALID_SKILL_RESOURCE`, `SKILL_RESOURCE_NOT_FOUND`, `INVALID_SKILL_QUERY`, `INVALID_SKILL_ACTION`.
- On Temporal the call is the activity `agent.skill.invoke` ([agent_activities.py:2044](../../../../server/services/temporal/agent_activities.py#L2044)).

No other tool is deferred this way. Every other connected tool's full definition is in every request.

### 4.10 What a tool can see at call time

`ctx.raw` is the only channel. Its keys differ by path.

| Key | In process ([tools.py:176](../../../../server/services/handlers/tools.py#L176)) | Temporal activity ([base.py:1410](../../../../server/services/plugin/base.py#L1410), [workflow.py:304](../../../../server/services/workflow.py#L304)) |
|---|---|---|
| `workflow_id`, `execution_id`, `user_id`, `workspace_dir` | yes | yes |
| `session_id` | when the run has it | yes |
| `nodes`, `edges` | always the run's canvas | only when the class sets `needs_canvas` ([agent_workflow.py:2304](../../../../server/services/temporal/agent_workflow.py#L2304)), else empty lists |
| `parent_node_id` (the calling agent) | yes | yes |
| `invoking_agent_node_id`, `agent_iteration`, `tool_call_index`, `team_lead_node_id` | no | yes |
| `tool_call_id` | yes | yes |
| `generation`, `run_scope`, `root_execution_id`, `data_scope_id`, `graphVersion` | when the run has them | when the run has them |
| `auto_rebind_tools` | yes | yes |
| `chat_stream`, `chat_run_id` | not in the list | yes |
| `employee_job_id`, `team_id`, `team_task_id`, `team_execution_id`, `parameter_snapshot` | when the run has them | only with `pi_harness_version` 1 ([base.py:1407](../../../../server/services/plugin/base.py#L1407)) or through `context_keys` |
| `ai_service`, `database` objects | yes | no |
| the class's `context_keys` | yes ([tools.py:264](../../../../server/services/handlers/tools.py#L264)) | yes |
| `_tool_config`, `_validated_tool_input`, `_raw_parameters` | yes | yes |

Where `pi_harness_version` comes from: Start records it, with `execution_control_version`, on every new generation ([control.py:168](../../../../server/services/deployment/control.py#L168)); the starters copy it from the generation into each run's payload ([executor.py:167](../../../../server/services/temporal/executor.py#L167)); `MachinaWorkflow` puts it on each node's context ([temporal/workflow.py:783](../../../../server/services/temporal/workflow.py#L783)); and a tool payload inherits it with the other scope keys under the patch `employee-task-manager-tool-scope-v2` ([agent_workflow.py:2378](../../../../server/services/temporal/agent_workflow.py#L2378), keys at [agent_workflow.py:310](../../../../server/services/temporal/agent_workflow.py#L310)). A run with no admitted generation, such as a manual canvas Run, skips the copy ([executor.py:154](../../../../server/services/temporal/executor.py#L154)). The keys forwarded under the flag are `INLINE_SCOPE_KEYS` ([pi_inline_delegation.py:28](../../../../server/services/temporal/pi_inline_delegation.py#L28)).

The teammate list:

- [`build_teammate_descriptors(node_id, {"nodes", "edges"})`](../../../../server/services/plugin/edge_walker.py#L67) is a pure function over the canvas. It returns, per agent wired to the lead's `input-teammates`, `{node_id, node_type, label, delegate_tool_name}`.
- Task Manager is the tool that uses it: it sets `needs_canvas = True` ([task_manager:156](../../../../server/nodes/tool/task_manager/__init__.py#L156)), takes the lead's id from `parent_node_id` or `team_lead_node_id`, and resolves an assignee by node id or delegate name ([task_manager:184](../../../../server/nodes/tool/task_manager/__init__.py#L184)).
- The roster a lead's model reads is built at prepare time from `collect_teammate_connections`, one line per teammate with its tools and skills ([edge_walker.py:694](../../../../server/services/plugin/edge_walker.py#L694), [agent_activities.py:1670](../../../../server/services/temporal/agent_activities.py#L1670), [ai.py:1739](../../../../server/services/ai.py#L1739)).
- On Temporal the workflow's `tools` list also holds the hidden delegate entries (node id, type, the raw teammate descriptor) for the whole run. The Task Manager branch resolves an assignee against them (section 4.5).

Other connected tools: a tool call's context has no list of its sibling tools. With `needs_canvas` a tool can read the edges into the calling agent itself, as the Agent Builder's `inspect_canvas` does ([agent_builder:899](../../../../server/nodes/tool/agent_builder/__init__.py#L899)).

## 5. Graph building and mutation at runtime

### 5.1 The workflow-ops format

Seven operation types, defined as TypedDicts in [workflow_ops.py:97](../../../../server/services/workflow_ops.py#L97): `add_node`, `add_edge`, `set_node_parameters`, `delete_node`, `delete_edge`, `move_node`, `replace_node`. A batch is `{"operations": [...]}`, applied in order; a node added earlier in a batch is referenced as `{"client_ref": ...}`.

Two delivery modes:

- **Request and response.** A WebSocket handler returns the batch and the editor applies and saves it. The one user is `evaluate_auto_skill` ([skills/handlers.py:145](../../../../server/services/skills/handlers.py#L145)).
- **Push.** [`broadcast_workflow_ops`](../../../../server/services/workflow_ops.py#L315) sends `{"type": "workflow_ops_apply", "data": {workflow_id, caller_node_id, operations, persisted?}}` to every client. The editor's listener handles a push three ways ([useWorkflowOpsListener.ts:57](../../../../client/src/hooks/useWorkflowOpsListener.ts#L57)): a batch for a workflow that is not open shows a toast; a batch with `persisted: true` is added to the canvas under the server's ids and nothing is saved; any other batch goes through [`applyOperations`](../../../../client/src/lib/workflowOps.ts#L255), which mints node ids (or adopts `minted_id`), updates the canvas and saves each new node's parameter row. A second listener, mounted in the app shell, copies persisted batches into the stored copy of the open workflow and into the parameter cache ([useWorkflowOpsListener.ts:97](../../../../client/src/hooks/useWorkflowOpsListener.ts#L97)). INFERRED: a batch that is not persisted reaches the saved graph only through an open editor and its own save.

### 5.2 `apply_graph_additions`

[`apply_graph_additions(database, workflow_id, additions, mutation_id=..., caller_node_id=..., prepare=..., authorize=..., authorization_grant_ids=...)`](../../../../server/services/workflow_storage/mutate.py#L73):

1. Runs inside `database.run_runtime_mutation` ([database.py:772](../../../../server/core/database.py#L772)): on SQLite a `BEGIN IMMEDIATE` transaction. A ledger row keyed by `mutation_id` is written in the same transaction; a repeat of the id returns the first result with `applied=False`.
2. Reads `workflow.data`, runs `authorize(session)` and `prepare(graph)` under that lock, then places the batch with [`add_to_graph`](../../../../server/services/graph_build.py#L278): canonical ids `<workflow_id>:<type>:<n>`, labels unique by template key, edges skipped when the graph already has them, named edges removed.
3. Writes a fresh parameter row per new node and merges into existing rows ([mutate.py:145](../../../../server/services/workflow_storage/mutate.py#L145)).
4. For an employee with a team plan, adds a new agent wired to the lead's `input-teammates` to `team_plan.members` and `node_roles`, and writes child grants ([mutate.py:160](../../../../server/services/workflow_storage/mutate.py#L160)).
5. After the commit: pushes the batch with `persisted=True` and calls `notify_graph_changed` ([mutate.py:257](../../../../server/services/workflow_storage/mutate.py#L257)).

It returns `None` when the workflow does not exist and raises `ValueError` for a batch that does not fit: a ref reused or unknown, a Context companion that is not a Context node, an agent given a second Context ([graph_build.py:308](../../../../server/services/graph_build.py#L308)). It never deletes a node and never replaces the graph.

Callers: the Agent Builder ([agent_builder:250](../../../../server/nodes/tool/agent_builder/__init__.py#L250)), Turn on Talk ([handlers.py:207](../../../../server/services/employees/handlers.py#L207)), the upgrade ([upgrade.py:221](../../../../server/services/employees/upgrade.py#L221)).

### 5.3 The Agent Builder

One ToolNode, type `agentBuilder`, tool name `agent_builder`, `needs_canvas = True`, with an `operation` field ([agent_builder:742](../../../../server/nodes/tool/agent_builder/__init__.py#L742)). It names the calling agent from `invoking_agent_node_id` or `parent_node_id` ([agent_builder:192](../../../../server/nodes/tool/agent_builder/__init__.py#L192)) and reads the saved graph fresh on each call ([agent_builder:162](../../../../server/nodes/tool/agent_builder/__init__.py#L162)). An "employee" below means the workflow has an employee row.

| Operation | Arguments | What it checks | Returns |
|---|---|---|---|
| [`inspect_canvas`](../../../../server/nodes/tool/agent_builder/__init__.py#L862) | none | nothing | `nodes`, `edges`, `you`, `employee`, `available_tools`, `available_agents`, `available_skills` |
| [`inspect_node`](../../../../server/nodes/tool/agent_builder/__init__.py#L802) | `node_id` or `node_type` | the node is in the workflow, the type is registered | `contract`: metadata, parameter and output schemas, locked fields, availability, documents |
| [`search_docs`, `read_doc`](../../../../server/nodes/tool/agent_builder/__init__.py#L784) | `query`, `limit`; `document_id`, `offset`, `max_chars` | the document id is in the manifest | matches; one bounded page |
| [`add_tool`](../../../../server/nodes/tool/agent_builder/__init__.py#L943) | `node_type`, `app_id`, `target_member_id`, `dry_run` | editor-built: type in `_allowed_tool_types()`; employee: `check_tool`, the target is a member, an owner grant | `summary`, `operations`, `binding_results`, `saved_revision` |
| [`add_skill`](../../../../server/nodes/tool/agent_builder/__init__.py#L1106) | `skill_name`, `target_member_id`, `dry_run` | the skill exists in the library or the offered built-ins; employee: `check_skill`, an owner grant | `summary`; `operations` always empty |
| [`add_subagent`](../../../../server/nodes/tool/agent_builder/__init__.py#L1224) | `agent_type`, `purpose`, `tool_types`, `tool_parameters`, `skill_names`, `dry_run` | the caller is a team lead; the type is an allowed agent and not a lead; each tool type is allowed and its parameters validate; no locked or secret key; employee: an owner grant | `summary`, `operations` |
| [`plan_update`](../../../../server/nodes/tool/agent_builder/__init__.py#L829) | `change_operation` plus that operation's arguments | runs the operation with `dry_run=True` | the dry-run result |
| [`apply_update`](../../../../server/nodes/tool/agent_builder/__init__.py#L839) | `change_operation`, `stop_work` | `apply_saved`: a trusted owner request | the Safe Apply result |
| [`create_workflow`](../../../../server/nodes/tool/agent_builder/__init__.py#L1361) | `workflow_name`, `workflow_description` | a trusted owner request | the Hire response |

What may be added:

- **Editor-built workflow.** Tools: every ToolNode and every `usable_as_tool` ActionNode that is not a chat model, minus `agentBuilder`, `masterSkill`, `taskManager`, minus the allowlist's `disabled_nodes` and `disabled_groups` ([agent_builder:351](../../../../server/nodes/tool/agent_builder/__init__.py#L351)). Agents: every `component_kind == "agent"` class not blocked ([agent_builder:373](../../../../server/nodes/tool/agent_builder/__init__.py#L373)). `enabled_nodes` is not consulted.
- **Employee, tools.** [`check_tool`](../../../../server/services/employees/policy.py#L122): the type is one of `BASE_TOOLS` or an app's tool; under Ask first a tool that sends or spends needs an approval spec or `ask_first_params`; `is_hire_allowed`; the app is connected. One target member, with its own node ([agent_builder:990](../../../../server/nodes/tool/agent_builder/__init__.py#L990)).
- **Employee, skills.** [`check_skill`](../../../../server/services/employees/policy.py#L175) refuses the name `skill` and any `*-personality` name. Sources: the owner's library, then the `employee` skill folder ([agent_builder:416](../../../../server/nodes/tool/agent_builder/__init__.py#L416)).
- **Employee, teammates.** With a team plan the caller is replaced by the lead ([agent_builder:1240](../../../../server/nodes/tool/agent_builder/__init__.py#L1240)); the agent type and tool types are checked against the editor-built sets above, then an owner grant is required ([agent_builder:1323](../../../../server/nodes/tool/agent_builder/__init__.py#L1323)). Without a team plan the caller stays the agent that called; a hire's talk agent is not a team lead, so the call is refused ([test_agent_builder_employee.py:450](../../../../server/tests/nodes/test_agent_builder_employee.py#L450)).

Owner grants: [`require_builder_access`](../../../../server/services/employees/permissions.py#L26) answers `authorized` only when the call belongs to an authenticated chat run of the owner ([permissions.py:11](../../../../server/services/employees/permissions.py#L11)) and an approved `EmployeeGrant` row exists whose id is the hash of the full scope. Otherwise it creates the row unapproved and the tool returns `required_access` with `activation_state: "blocked"` ([agent_builder:277](../../../../server/nodes/tool/agent_builder/__init__.py#L277)). The grant is checked again inside the mutation transaction ([agent_builder:235](../../../../server/nodes/tool/agent_builder/__init__.py#L235)).

A refusal is a normal tool result, not an error: `summary` is one sentence, `operations` is empty, `validation_issues` is `[{"code": "request_refused", "message": ...}]` ([agent_builder:273](../../../../server/nodes/tool/agent_builder/__init__.py#L273)). Section 7 lists the sentences.

Idempotency: the mutation id is `agent-builder:<execution_id>:<tool_call_id>`, or a hash of the request when there is no call id ([agent_builder:200](../../../../server/nodes/tool/agent_builder/__init__.py#L200)).

### 5.4 When a change takes effect

- **A new tool, in the calling run:** through the hot rebind (section 4.9). `add_tool` returns only the operations that bind to the caller ([agent_builder:577](../../../../server/nodes/tool/agent_builder/__init__.py#L577)). When the new node replaces a same-type tool the run already holds, it returns no operations and says so ([agent_builder:1075](../../../../server/nodes/tool/agent_builder/__init__.py#L1075)).
- **A saved tool this run started without:** `add_tool` saves nothing and returns one `add_node` operation for the saved node ([agent_builder:1086](../../../../server/nodes/tool/agent_builder/__init__.py#L1086)). Whether the rebind accepts that operation is an open question (section 10).
- **A skill:** `add_skill` returns no operations. Its summaries say a skill merged into an existing Skills node "applies from the next message", and a new node or edge needs Apply ([agent_builder:1204](../../../../server/nodes/tool/agent_builder/__init__.py#L1204)).
- **Everything else, on a live generation:** runs use the generation's snapshot. [`pending_changes`](../../../../server/services/deployment/restart.py#L130) is true while the generation is live and the saved graph differs from `control.graph_snapshot` in node id, type, label key, disabled flag, or an edge's ends, handles or condition.
- **Safe Apply:** [`apply_saved_changes`](../../../../server/services/employees/safe_apply.py#L87) validates the saved graph, pauses admissions, waits for open work, sends the controller a `replace_graph` Update, rewrites cron Schedules' arguments, saves the new snapshot with the revision raised by one, and resumes if it was running ([safe_apply.py:183](../../../../server/services/employees/safe_apply.py#L183)). A failure rolls the snapshot back ([safe_apply.py:320](../../../../server/services/employees/safe_apply.py#L320)).

### 5.5 Hire

[`handle_hire_employee`](../../../../server/services/employees/hire.py#L311) takes a `HireEmployeeRequest`. Node types never come from the request; they come from the app registry and the builder ([builder.py:47](../../../../server/services/employees/builder.py#L47)).

1. Size and shape, then the idempotency key: same key and payload returns the first employee, a different payload is `conflict`, a build in flight is `busy` ([hire.py:202](../../../../server/services/employees/hire.py#L202)).
2. App names in the request are resolved against the registry and the owner's sources; unknown names are kept as unsupported ([hire.py:85](../../../../server/services/employees/hire.py#L85)).
3. [`build_employee_graph`](../../../../server/services/employees/builder.py#L875) with `team=True` ([hire.py:259](../../../../server/services/employees/hire.py#L259)). First the single-agent graph ([builder.py:727](../../../../server/services/employees/builder.py#L727)): one trigger, one `aiAgent`, the base tools, the apps' tools decided by `check_tool`, a Context for owner-facing triggers, a Skills node from the library, delivery (an app reply goes behind an approval gate), a console node, and the Talk line with the Agent Builder on the talk agent ([talk.py:358](../../../../server/services/employees/talk.py#L358)). Every node passes `allowed(node_type)` ([builder.py:268](../../../../server/services/employees/builder.py#L268)).
4. [`build_team`](../../../../server/services/employees/team_recipe.py#L76) then turns it into a team: an `ai_employee` lead; one to three specialists chosen by keyword match on the job text ([team_recipe.py:42](../../../../server/services/employees/team_recipe.py#L42)), each with its own Context and Skills node; app and search tools moved from the lead and Talk to the matching specialist ([team_recipe.py:187](../../../../server/services/employees/team_recipe.py#L187)); one Clock per agent, and one Memory each when the owner keeps memory; a `taskTrigger` wired to the lead's `input-task`; a "Reviewed result" reply node. A specialist type that is not registered or not allowed falls back to `aiAgent` ([team_recipe.py:142](../../../../server/services/employees/team_recipe.py#L142)). It writes `team_plan` ([team_recipe.py:308](../../../../server/services/employees/team_recipe.py#L308)).
5. A tool wired to two agents fails the build with `invalid_tool_ownership` ([builder.py:881](../../../../server/services/employees/builder.py#L881)).
6. `validate_workflow`; any error refuses the hire with `build_failed` ([hire.py:267](../../../../server/services/employees/hire.py#L267)). Then the row and graph are committed and the start is scheduled when nothing is missing ([hire.py:292](../../../../server/services/employees/hire.py#L292)).

Versions: `BUILDER_VERSION = 6`, `LIVE_RULE_BUILDER_VERSION = 3` ([builder.py:94](../../../../server/services/employees/builder.py#L94)), `RECIPE_VERSION = 2` ([team_recipe.py:19](../../../../server/services/employees/team_recipe.py#L19)).

The Agent Builder's `create_workflow` reaches the same handler with a synthesized request: manual trigger, Ask first on, two fixed steps, no apps ([control.py:8](../../../../server/services/employees/control.py#L8)).

### 5.6 Upgrading older employees

[`upgrade_employee`](../../../../server/services/employees/upgrade.py#L182) runs on Start ([start.py:128](../../../../server/services/employees/start.py#L128)), Turn on Talk and Apply ([handlers.py:178](../../../../server/services/employees/handlers.py#L178), [handlers.py:231](../../../../server/services/employees/handlers.py#L231)). It does nothing for an employee with a team plan or a `builder_version` of 3 or more ([upgrade.py:203](../../../../server/services/employees/upgrade.py#L203)). Otherwise [`plan_upgrade`](../../../../server/services/employees/upgrade.py#L114) adds an approval gate in front of an ungated app reply, the app tools Ask first had left out, the talk tools, and sets a read-only browser to full. It is one `apply_graph_additions` call keyed `upgrade:<workflow_id>:v3`. A failure is logged and never stops the start.

Start also moves agents whose provider is no longer usable onto the owner's current model ([start.py:50](../../../../server/services/employees/start.py#L50)) and checks a team's approval wiring ([upgrade.py:236](../../../../server/services/employees/upgrade.py#L236)).

### 5.7 Validation

[`validate_workflow`](../../../../server/services/workflow_validator.py#L51) returns `{"errors": [...], "warnings": [...]}`. Issue shape: `{code, node_id, message}` plus extras.

| Code | Kind | Condition | Line |
|---|---|---|---|
| `DANGLING_EDGE` | error | An edge end names no node | [100](../../../../server/services/workflow_validator.py#L100) |
| `LEGACY_MEMORY_EDGE` | warning | A Memory node on `input-memory` | [135](../../../../server/services/workflow_validator.py#L135) |
| `INVALID_CONTEXT_EDGE` | error | A Context edge not from `output-context` to `input-context` of a class with `requires_context` | [155](../../../../server/services/workflow_validator.py#L155) |
| `MULTIPLE_CONTEXTS` | error | One agent, several Context nodes | [176](../../../../server/services/workflow_validator.py#L176) |
| `SHARED_CONTEXT` | error | One Context node, several agents | [188](../../../../server/services/workflow_validator.py#L188) |
| `INVALID_TEAM_EDGE` | error | A handle starting `input-team` other than `input-teammates`; a target that is not a team lead; a source not in `AI_AGENT_TYPES` | [205](../../../../server/services/workflow_validator.py#L205) |
| `DUPLICATE_TEAMMATE_TYPE` | error | Two teammates of one type on a lead, except `aiAgent` | [240](../../../../server/services/workflow_validator.py#L240) |
| `DUPLICATE_DELEGATE_NAME` | error | Two teammates with the same delegate name | [249](../../../../server/services/workflow_validator.py#L249) |
| `TEAM_DELEGATION_CYCLE` | error | A cycle through teammate edges | [279](../../../../server/services/workflow_validator.py#L279) |
| `TEAM_DEPTH_EXCEEDED` | error | More than two child layers under a lead | [286](../../../../server/services/workflow_validator.py#L286) |
| `UNKNOWN_NODE_TYPE` | error | No registered class for the type | [307](../../../../server/services/workflow_validator.py#L307) |
| plugin codes | error | Whatever `cls.validate_graph` returns | [318](../../../../server/services/workflow_validator.py#L318) |
| `INVALID_PARAM` | warning | `Params` rejects the node's parameters | [340](../../../../server/services/workflow_validator.py#L340) |
| `MISSING_CREDENTIAL` | warning | A declared credential is not stored | [378](../../../../server/services/workflow_validator.py#L378) |
| `CYCLE` | error | A cycle over all edges | [410](../../../../server/services/workflow_validator.py#L410) |

There is no code for tools: a node on `input-tools` is not checked for being usable as a tool. The Skill and tool-name collisions are found when a run is prepared, not here.

Allowlist tiers, from [`get_config`](../../../../server/services/node_allowlist.py#L27) over [server/config/node_allowlist.json](../../../../server/config/node_allowlist.json):

- `enabled_nodes`: a positive list. With Normal mode on it governs what Hire may build, through [`is_hire_allowed`](../../../../server/services/node_allowlist.py#L120). Empty means everything not blocked.
- `disabled_nodes`, `disabled_groups`: absolute blocklists, checked first by `is_hire_allowed` and by the Agent Builder's catalogues.
- `disabled_credential_categories`, `disabled_skill_folders`: absolute, for the credentials modal and skill lists.

The shipped `enabled_nodes` includes `agentBuilder`, `ai_employee`, `taskTrigger`, `mcpConnector` and the specialist types the recipe names; it does not include `orchestrator_agent`, `taskManager` or the code executors.

### 5.8 The generated-UI precedent

A model writes a JSON spec, the server checks it against a fixed catalogue, and every problem goes back in one tool error so the model writes it again.

- The catalogue is [server/config/chat_genui_catalog.json](../../../../server/config/chat_genui_catalog.json): 12 components, 2 actions, and limits. The tool's description is generated from it and pinned with `tool_schema_locked` ([chat_ui:76](../../../../server/nodes/chat/chat_ui/__init__.py#L76)).
- [`check_spec`](../../../../server/services/genui/spec.py#L499) collects problems in a list as it walks the spec and raises [`SpecError`](../../../../server/services/genui/spec.py#L57) with all of them. Refused: wrong shape, limits exceeded, a child its parent may not hold, a cycle, a prop its schema rejects, a forbidden state path, a reserved action.
- Things it can fix it drops and reports instead: unknown element types, unreachable elements, undeclared props. They come back as `dropped` and `notes` on a successful result ([spec.py:65](../../../../server/services/genui/spec.py#L65)).

The tool turns the error into a `NodeUserError` ([chat_ui:92](../../../../server/nodes/chat/chat_ui/__init__.py#L92)):

```python
try:
    checked = check_spec(params.spec)
except SpecError as error:
    raise NodeUserError(
        "The interface was not shown. Fix these and call show_ui again: " + "; ".join(error.problems)
    ) from None
```

`NodeUserError` is in the default non-retryable list ([scaling.py:59](../../../../server/services/plugin/scaling.py#L59)). `BaseNode` returns it to the model as a tool result and logs one warning line ([base.py:849](../../../../server/services/plugin/base.py#L849)).

## 6. Inputs, outputs and state

| Shape | Fields | Defined at |
|---|---|---|
| `NodeContext` | `node_id`, `node_type`, `workflow_id`, `session_id`, `execution_id`, `user_id`, `credential_customer_id`, `workspace_dir`, `outputs`, `nodes`, `edges`, `raw` | [context.py:20](../../../../server/services/plugin/context.py#L20) |
| `ToolBinding` | `key`, `name`, `description`, `schema`, `parameters` | [tool.py:110](../../../../server/services/plugin/tool.py#L110) |
| `AgentToolSpec` | `definition` (`ToolDef`: `name`, `description`, `parameters`), `args_schema`, `execution` | [agent_runtime.py:49](../../../../server/services/agent_runtime.py#L49) |
| tool entry from the edge walker | `node_id`, `node_type`, `parameters`, `label`; `child_tools` for agents; `builtin` for added entries | [edge_walker.py:487](../../../../server/services/plugin/edge_walker.py#L487) |
| teammate entry | `node_id`, `node_type`, `label`, `delegate_tool_name`, `parameters`, `child_tools`, `child_skills`, `capabilities` | [edge_walker.py:742](../../../../server/services/plugin/edge_walker.py#L742) |
| in-process tool config | `node_type`, `node_id`, `parameters`, `label`, `connected_services`, `binding_key` for a binding; the agent adds run scope before each call | [ai.py:2572](../../../../server/services/ai.py#L2572), [ai.py:1214](../../../../server/services/ai.py#L1214) |
| Temporal tools entry | `name`, `definition`, `node_type`, `version`, `task_queue`, `tool_node_id`, `binding_key`, `parameters`, `tool_info`, `llm_hidden`; in controlled generations `activity_policy`, `needs_canvas`; virtual ones add `virtual_workspace`, `activity_name` | [agent_activities.py:1624](../../../../server/services/temporal/agent_activities.py#L1624) |
| Temporal tool activity input | `node_id`, `node_type`, `node_data`, `tool_args`, `workflow_id`, `session_id`, `execution_id`, `parent_node_id`, `team_lead_node_id`, `team_id`, `root_execution_id`, `nodes`, `edges`, `auto_rebind_tools`, `tool_call_id`, `invoking_agent_node_id`, `agent_iteration`, `tool_call_index`, chat keys | [agent_workflow.py:2322](../../../../server/services/temporal/agent_workflow.py#L2322) |
| `ApprovalSpec` | `channel`, `action`, `operations`, `operation_field`, `when`, `recipient`, `body`, `subject`, `details`, `max_length`, `refuse_while_asking`, `restrict_while_asking`, `outcome_labels` | [approval.py:39](../../../../server/services/plugin/approval.py#L39) |
| `Checked` (approval answer) | `result`, `node_data`, `tool_args`, `record` | [tool_calls.py:110](../../../../server/services/approvals/tool_calls.py#L110) |
| workflow operation | `add_node`: `client_ref`, `node_type`, `parameters`, `label`, `position`, `minted_id`, `data`. `add_edge`: `source`, `target`, `source_handle`, `target_handle`, `edge_id`, `condition` | [workflow_ops.py:97](../../../../server/services/workflow_ops.py#L97) |
| `NewNode` | `ref`, `type`, `label`, `params`, `position`, `context_of` | [graph_build.py:171](../../../../server/services/graph_build.py#L171) |
| `GraphAdditions` | `nodes`, `edges`, `merges` (`ParamMerge`: `node`, `params`), `removed_edges` (`EdgeRemoval`: `source`, `target`, `target_handle`) | [graph_build.py:208](../../../../server/services/graph_build.py#L208) |
| `GraphAdditionsResult` | `node_ids`, `labels`, `label_keys`, `operations`, `applied`, `saved_revision` | [mutate.py:44](../../../../server/services/workflow_storage/mutate.py#L44) |
| `AgentBuilderOutput` | `operation`, `summary`, `operations`, `request_id`, `validation_issues`, `required_access`, `saved_revision`, `activation_state`, `binding_results`, `nodes`, `edges`, `you`, `employee`, `available_tools`, `available_agents`, `available_skills`, `workflow_id`; extras allowed | [agent_builder:707](../../../../server/nodes/tool/agent_builder/__init__.py#L707) |
| `ToolDecision` | `allowed`, `code`, `reason`, `node_type`, `app`, `label`, `params`, `read_only`, `role` | [policy.py:87](../../../../server/services/employees/policy.py#L87) |
| `BuiltEmployee` | `nodes`, `edges`, `parameters`, `node_roles`, `trigger`, `delivery`, `delivery_app`, `app_ids`, `warnings`, `team_plan` | [builder.py:205](../../../../server/services/employees/builder.py#L205) |
| `team_plan` | `version`, `recipe`, `lead_node_id`, `talk_node_id`, `members` (`node_id`, `node_type`, `responsibility`, `role`, `context_node_id`, `tools`, `skills`), `model`, `requires`, `delivery_node_ids`, `talk_delivery_node_ids` | [team_recipe.py:308](../../../../server/services/employees/team_recipe.py#L308) |
| `EmployeeGrant` row | `id`, `workflow_id`, `owner_id`, `capability`, `member_id`, `account_id`, `limits`, `revoked_at` | [models/employees.py:122](../../../../server/models/employees.py#L122) |
| `CheckedSpec` | `spec`, `dropped`, `notes` | [spec.py:65](../../../../server/services/genui/spec.py#L65) |

Where state lives:

- The graph: `workflow.data` (`nodes`, `edges`). Parameters: one `NodeParameter` row per node. Neither is in the other.
- The mutation ledger: `RuntimeMutation` rows keyed by mutation id, resource type and resource id.
- What a live generation runs: `WorkflowControlExecution.graph_snapshot`, changed only by Start and Safe Apply ([safe_apply.py:294](../../../../server/services/employees/safe_apply.py#L294)).
- On Temporal, an agent run's tool list is workflow state, carried across continue-as-new ([agent_workflow.py:1156](../../../../server/services/temporal/agent_workflow.py#L1156)).
- Process memory: the registries, the Skill tool's `_loaded` and `_turn_activity`, in-process delegation tracking ([tools.py:44](../../../../server/services/handlers/tools.py#L44)).

## 7. Limits, policies and refusal messages

| Limit | Value | Defined at |
|---|---|---|
| Tool result copy for the model | 100,000 characters by default (`TOOL_RESULT_MAX_CHARS`, or the owner's setting); 0 means no cap | [config.py:514](../../../../server/core/config.py#L514) |
| Node result | warn at 524,288 bytes, refuse at 2,097,152 bytes | [limits.py:26](../../../../server/services/media/limits.py#L26) |
| Agent loop | 200 iterations by default | [config.py:519](../../../../server/core/config.py#L519) |
| Concurrent subagents; delegation depth | 3; 2 (cannot be raised above 2) | [config.py:525](../../../../server/core/config.py#L525) |
| Team depth in the validator | two child layers | [workflow_validator.py:270](../../../../server/services/workflow_validator.py#L270) |
| Skill catalogue in the tool description | 7,000 characters | [skill_runtime.py:75](../../../../server/services/skill_runtime.py#L75) |
| Skill resource page; search matches | 16,000 characters (default 4,000); 50 (default 20), each line cut at 500 | [skill_runtime.py:14](../../../../server/services/skill_runtime.py#L14) |
| Teammate capability blurb | 160 characters per tool or skill | [edge_walker.py:587](../../../../server/services/plugin/edge_walker.py#L587) |
| Agent Builder arguments | `tool_types` and `skill_names` 20 each; `purpose` 4,000; `query` 1,000; `limit` 1 to 50; `max_chars` 1 to 24,000 | [agent_builder:641](../../../../server/nodes/tool/agent_builder/__init__.py#L641) |
| Builder documentation | 50 search results; 24,000 characters a read; files over 2,000,000 bytes left out | [builder_documentation.py:38](../../../../server/services/builder_documentation.py#L38) |
| Generated-UI spec | 24 elements, depth 6, 16,384 bytes, 12 children, id 40 characters, path 6 segments | `limits` in the catalogue JSON |
| Hire request | 32 KiB; name 40, role 60, description 280, job 2,000 characters; 6 apps, 6 steps | [hire_request.py:22](../../../../server/services/employees/hire_request.py#L22) |
| Team recipe | 1 to 3 specialists | [team_recipe.py:46](../../../../server/services/employees/team_recipe.py#L46) |
| Shutdown hook | 10 seconds each | [shutdown_hooks.py:55](../../../../server/services/plugin/shutdown_hooks.py#L55) |
| Default activity retry | 3 attempts, 1 s initial, factor 2, 60 s cap; non-retryable: `ValidationError`, `PermissionDeniedError`, `InvalidParametersError`, `NodeUserError`, `PayloadsTooLarge` | [scaling.py:55](../../../../server/services/plugin/scaling.py#L55) |
| Default activity timeouts | 10 minutes an attempt, 2 minutes heartbeat | [scaling.py:91](../../../../server/services/plugin/scaling.py#L91) |
| `agent.refresh_tools` | 30 seconds an attempt | [agent_workflow.py:2679](../../../../server/services/temporal/agent_workflow.py#L2679) |
| Safe Apply | 30 seconds per controller Update; a 2 minute claim renewed every 30 seconds | [safe_apply.py:18](../../../../server/services/employees/safe_apply.py#L18) |
| Approval card edit | 20,000 characters | [approval.py:65](../../../../server/services/plugin/approval.py#L65) |

Feature switches: `EMPLOYEE_TEAMS_ENABLED`, `EMPLOYEE_CAPABILITY_UPDATES_ENABLED`, `EMPLOYEE_SAFE_APPLY_ENABLED`, all default on ([config.py:135](../../../../server/core/config.py#L135)); `_CREATE_WORKFLOW_ENABLED`, a module constant set to `True` ([agent_builder:95](../../../../server/nodes/tool/agent_builder/__init__.py#L95)).

Messages a model reads. Text is verbatim; `<...>` marks a filled-in value.

| Where | Message |
|---|---|
| Unknown tool, in process | `{"error": "Unknown tool", "details": "Tool '<name>' is not connected to this agent."}` |
| Unknown tool, Temporal | `Error: tool '<name>' is not connected to this agent.` |
| Bad arguments, in process | `{"error": "Invalid tool arguments", "details": <reasons>}` |
| Bad JSON arguments, Temporal | `{"error": "Tool arguments were invalid JSON", "detail": ..., "raw_arguments": ...}` |
| Plugin validation | `Invalid tool input/configuration: <reasons>`, type `ValidationError` |
| Duplicate names | `Duplicate LLM-visible tool names are not allowed: <name>: <label> (<node id>), ... . Assign a unique Tool Name to each connected tool.` |
| Result cut | `[Tool result truncated: showing the first <n> of <total> characters. Call the tool again with narrower parameters to see the rest.]` |
| Result too large | `<type> returned <n> KB, over the 2 MB the workflow engine can carry. ...` ([base.py:1145](../../../../server/services/plugin/base.py#L1145)) |
| Held for the owner | `Not sent yet: it waits for the owner's OK on a card in the chat. ...` ([tool_calls.py:83](../../../../server/services/approvals/tool_calls.py#L83)) |
| Cannot wait | `Not run: <channel> cannot wait for the owner's OK, and they ask to approve everything first. ...` ([tool_calls.py:92](../../../../server/services/approvals/tool_calls.py#L92)) |
| Iteration cap | `[Recursion limit reached: <n> iterations. ...]` ([agent_runtime.py:469](../../../../server/services/agent_runtime.py#L469)) |
| Generated UI | `The interface was not shown. Fix these and call show_ui again: <problem>; <problem>` |
| Skill not loaded | `Load the skill before reading its resources` |
| Task Manager assignee | `Assignee must resolve to exactly one connected teammate; available=<list>` ([task_manager:193](../../../../server/nodes/tool/task_manager/__init__.py#L193)) |
| Task Manager assignee, in the workflow | `{"error": "ValueError: Task Manager assignee is not a connected Temporal delegate"}`; also `Maximum delegation depth <n> exceeded` and `Task Manager delegation requires an execution team` ([agent_workflow.py:1942](../../../../server/services/temporal/agent_workflow.py#L1942)) |
| Delegation without a task, Temporal | `{"error": "delegate_to_* requires a non-empty 'task' argument describing what the agent should do."}` ([agent_workflow.py:2259](../../../../server/services/temporal/agent_workflow.py#L2259)) |
| Delegation past the depth limit | `{"error": "Maximum delegation depth <n> exceeded."}` on Temporal; `Maximum delegation depth <n> exceeded` in process ([tools.py:470](../../../../server/services/handlers/tools.py#L470)) |
| Delegation accepted, in process | `SUCCESS: Task delegated to '<label>' (task_id: <id>). Agent is now working INDEPENDENTLY in the background. ... To check results later, use 'check_delegated_tasks' with task_id='<id>'.` ([tools.py:923](../../../../server/services/handlers/tools.py#L923)) |

Agent Builder refusals (the `summary`), with their lines in [agent_builder](../../../../server/nodes/tool/agent_builder/__init__.py):

- Not saved ([262](../../../../server/nodes/tool/agent_builder/__init__.py#L262)): `Save the workflow first, then ask again.`; `Only an agent can ask me to add things.`; `Save the workflow first: the agent that asked isn't in the saved workflow yet.`
- `add_tool` ([954](../../../../server/nodes/tool/agent_builder/__init__.py#L954)): `add_tool: node_type is required.`; `add_tool: '<type>' is not an allowed tool type. Allowed types: <list>. Call inspect_canvas for the full catalogue with descriptions.`; `add_tool: '<type>' is not a node type. Use a type from inspect_canvas available_tools.`; `add_tool: '<type>' needs the app_id of its entry in inspect_canvas available_tools.`; `Choose a member of this employee’s team.`
- Policy reasons ([policy.py:140](../../../../server/services/employees/policy.py#L140)): `<name> isn't something a hired employee can be given.`; `<App> can send things on your behalf, so it stays off while "<rule label>" is on.` (or `can spend money`); `<App> isn't available to hired employees.`; `<App> isn't connected yet. Connect it in Settings > Connectors first.`
- Skills ([1117](../../../../server/nodes/tool/agent_builder/__init__.py#L1117)): `add_skill: skill_name is required.`; `There's no skill called '<name>' in the owner's library or in Discover.`; `add_skill: no skill named '<name>'. Call inspect_canvas for the available skills.`; `This team shares instructions between members. Review those connections before changing one member’s skills.`; from policy, `No skill can be called "skill": that is the Skill tool's own name.`
- `add_subagent` ([1235](../../../../server/nodes/tool/agent_builder/__init__.py#L1235)): `add_subagent: only team-lead agents (ai_employee, orchestrator_agent) can spawn delegates. This agent is '<type>'.`; `add_subagent: cannot spawn another team-lead ('<type>'); pick a specialized agent instead.`; `The specialist cannot use '<type>'. Inspect available tools first.`; `Specialist tools must use existing connections and preserve locked settings.`; `'<type>' needs valid configuration. Inspect its node contract first.`; `Teammate '<type>' is already wired to you (node id=<id>). Reusing existing instance.`
- Concurrency and access ([1060](../../../../server/nodes/tool/agent_builder/__init__.py#L1060)): `The owner’s permission changed. Ask them to allow access again.`; `The workflow changed while I was adding to it. Try again.`
- Access requests ([permissions.py:30](../../../../server/services/employees/permissions.py#L30)): `Changes to employee capabilities are currently turned off.`; `Ask the owner in Talk to change this employee’s access.`; `Allow <role>’s team to use <app> for their assigned responsibilities?`
- `apply_update` ([852](../../../../server/nodes/tool/agent_builder/__init__.py#L852)): `Ask the owner in Talk to apply this change.`

Safe Apply answers ([safe_apply.py:103](../../../../server/services/employees/safe_apply.py#L103)): `activation_state` is `saved`, `waiting`, `starting`, `running`, `paused` or `failed`; errors are `safe_apply_disabled`, `not_found`, `conflict`, `invalid_changes` (with `validation_issues`), `safe_apply_runtime_required`, `apply_failed`.

Hire errors ([hire.py:22](../../../../server/services/employees/hire.py#L22)): `invalid_request`, `too_large`, `conflict`, `busy`, `not_allowed`, `build_failed`, `save_failed`, plus `teams_disabled` and `invalid_tool_ownership`.

## 8. Extension points relevant to a dynamic workflow

Facts only.

**Reading the teammate list**

- At prepare time: `collect_teammate_connections`, called only for `orchestrator_agent` and `ai_employee` ([agent_activities.py:1450](../../../../server/services/temporal/agent_activities.py#L1450)). It takes the lead's node id, a context with `nodes` and `edges`, and a database.
- At call time, in a tool: `build_teammate_descriptors` over `ctx.nodes` and `ctx.edges`, which on Temporal needs `needs_canvas = True`; the lead's id is `ctx.raw["parent_node_id"]`. Task Manager is the precedent.
- A teammate's tools and skills are not in the descriptor from `build_teammate_descriptors`; `collect_teammate_connections` adds them and needs a database.
- The contract test lists the tools allowed to set `needs_canvas`: `agentBuilder`, `taskManager`, `android_tool` ([test_plugin_contract.py:362](../../../../server/tests/test_plugin_contract.py#L362)).

**Giving a tool runtime services**

- Getters in [services/plugin/deps.py](../../../../server/services/plugin/deps.py#L40): `get_database`, `get_auth_service`, `get_ai_service`, `get_cache`. They resolve from the container on each call.
- `ctx.connection(credential_id)` for a declared credential ([context.py:82](../../../../server/services/plugin/context.py#L82)).
- `ctx.raw` keys (section 4.10); a class adds server-owned keys with `context_keys`.

**Starting durable work from a tool call**

- Through the workflow: only Task Manager, by returning `delegation_request`, which a type-specific branch in `AgentWorkflow` turns into a detached `DelegatedTaskWorkflow` (section 4.5). The branch tests `node_type == "taskManager"` ([agent_workflow.py:2558](../../../../server/services/temporal/agent_workflow.py#L2558)).
- Through the client, detached: Task Manager's `submit_job` calls `create_job` and `dispatch_job` ([task_manager:210](../../../../server/nodes/tool/task_manager/__init__.py#L210)), which starts `EmployeeJobWorkflow` with `client.start_workflow` ([jobs.py:264](../../../../server/services/employees/jobs.py#L264)).
- A delegated agent in process: `_execute_delegated_agent` ([tools.py:372](../../../../server/services/handlers/tools.py#L372)), a background asyncio task with no durability.
- A Coding Agent delegated from an agent that runs in process: `start_inline_pi_delegation` starts `PiInlineDelegationWorkflow` through the client ([tools.py:388](../../../../server/services/handlers/tools.py#L388), [pi_inline_delegation.py:226](../../../../server/services/temporal/pi_inline_delegation.py#L226)). The workflow id comes from a digest of the root execution, the parent, the target and the call id, with reuse policy `REJECT_DUPLICATE` ([pi_inline_delegation.py:164](../../../../server/services/temporal/pi_inline_delegation.py#L164)).

**Adding tools to a running agent**

- Return `operations` containing `add_node` and an `add_edge` from the new node to the calling agent's `input-tools` or `input-teammates` (section 4.9). The node must exist as a registered class that passes `is_runtime_tool`.
- A tool entry with no canvas node exists only as the Coding Agent's virtual workspace tools, built inside `prepare_agent_payload`.

**Names**

- A tool name starting `delegate_to_` is treated as a delegation by `AgentWorkflow`; a test forbids it on anything that is not a registered agent ([test_tool_registry.py:111](../../../../server/tests/test_tool_registry.py#L111)).
- `Skill` and `check_delegated_tasks` are taken by the two pseudo tools ([ai.py:2440](../../../../server/services/ai.py#L2440)).

**uiHints and other registries a new capability would touch**

- A new uiHint key must be added to the `known` set in [test_node_spec.py:931](../../../../server/tests/test_node_spec.py#L931) and to `INodeUIHints` in [client/src/types/INodeProperties.ts](../../../../client/src/types/INodeProperties.ts#L210).
- The `workspace` hint is read by [`workspace_nodes`](../../../../server/services/workspace_capabilities.py#L6).
- Central tables keyed by node type: `CLASSES` / `CLASSES_WHEN`, `REPEATABLE`, `enabled_nodes` (for Hire), `_UNCAPPED_TOOL_TYPES`. A test requires every operation value of a `CLASSES_WHEN` type to have a class ([test_classes.py:60](../../../../server/tests/services/approvals/test_classes.py#L60)).

**What the tests lock**

- [test_plugin_self_containment.py](../../../../server/tests/test_plugin_self_containment.py#L137): the WebSocket router imports no plugin module; migrated plugins have no router file under routers; a plugin with `_handlers.py` or `_router.py` self-registers; `main.py` mounts no plugin router by name; each listed plugin package imports cleanly.
- [test_plugin_contract.py](../../../../server/tests/test_plugin_contract.py#L403): every registered node class has exactly one documentation card under docs-internal/node-logic-flows; every tool schema has no `$ref`; `task_queue` is a known queue; `needs_canvas` only on the allowlisted tools.
- [test_node_spec.py:925](../../../../server/tests/test_node_spec.py#L925): `uiHints` carry only known keys.
- [test_tool_registry.py:79](../../../../server/tests/test_tool_registry.py#L79): each type listed in the tool-name snapshot keeps its `tool_name`. It checks only the listed rows.

**Replay**

- Workflow code that changes which commands run needs a `workflow.patched` guard or a generation flag. `AgentWorkflow` already uses both for tool handling, for example `agent-node-binding-refresh-v2` and `agent-tool-binding-key-v1` ([agent_workflow.py:1152](../../../../server/services/temporal/agent_workflow.py#L1152)). The generation flags are `pi_harness_version` and `approval_wait_version` ([temporal/workflow.py:187](../../../../server/services/temporal/workflow.py#L187)). A new patch id ships dormant for one release ([patches.py:14](../../../../server/services/temporal/patches.py#L14)).

## 9. Where the docs disagree with the code

1. **`add_subagent` on a hired employee.** [normal_mode.md](../../../normal_mode.md), section "Adding tools and skills from Talk", says "`add_subagent` is refused for a hired employee", and the [agentBuilder card](../../../node-logic-flows/ai_tools/agentBuilder.md) says the same. The code allows it for an employee with a team plan, after an owner grant ([agent_builder:1240](../../../../server/nodes/tool/agent_builder/__init__.py#L1240), [agent_builder:1323](../../../../server/nodes/tool/agent_builder/__init__.py#L1323)); a test covers it ([test_agent_builder_docs.py:144](../../../../server/tests/nodes/test_agent_builder_docs.py#L144)). Only an employee without a team plan is refused.
2. **Who gets a new tool.** The same section of normal_mode.md says "A new tool is wired to both the worker and the talk agent". The code adds it to one member, `target_member_id` or the caller ([agent_builder:990](../../../../server/nodes/tool/agent_builder/__init__.py#L990)).
3. **What Hire builds.** normal_mode.md, section "2. Hire", describes "one `aiAgent`". Hire passes `team=True` and the result has an `ai_employee` lead and specialists ([hire.py:259](../../../../server/services/employees/hire.py#L259)). The document's "Known gaps" does mention team hires.
4. **"A hire can only be given what Hire could build."** [node_allowlist.md](../../../node_allowlist.md), section "agentBuilder integration", says every tool of a hired employee goes through policy.py, `enabled_nodes` included. On the `add_subagent` path the agent type, the specialist's tool types and skills are checked against the blocklists only ([agent_builder:1248](../../../../server/nodes/tool/agent_builder/__init__.py#L1248), [agent_builder:1285](../../../../server/nodes/tool/agent_builder/__init__.py#L1285), [agent_builder:1305](../../../../server/nodes/tool/agent_builder/__init__.py#L1305)), then an owner grant.
5. **Who sets `needs_canvas`.** [plugin_system.md](../../../plugin_system.md), section "Class attributes", says "Today only `AgentBuilderNode` opts in", and so does a comment at [base.py:393](../../../../server/services/plugin/base.py#L393). Task Manager and the mobile plugin set it too ([task_manager:156](../../../../server/nodes/tool/task_manager/__init__.py#L156), [mobile/_tool.py:26](../../../../server/nodes/mobile/_tool.py#L26)).
6. **A Tool Schema Editor.** [tool_building_pipeline.md](../../../tool_building_pipeline.md), section 3b, says the stored override applies when the "User customized the schema in the Tool Schema Editor UI". No file under client/src refers to `save_tool_schema` or such an editor.
7. **Name precedence.** The docstring of `_build_tool_from_node` lists class variables before node parameters ([ai.py:2420](../../../../server/services/ai.py#L2420)). The code reads the node parameter `tool_name` first ([ai.py:2522](../../../../server/services/ai.py#L2522)). tool_building_pipeline.md section 3a has the right order and omits the `tool_schema_locked` branch, which outranks all of it.
8. **Interceptors.** The docstring of [interceptor.py](../../../../server/services/plugin/interceptor.py#L1) says the chain is "Registered globally in `services/plugin/base.py`". base.py has no reference to it; a search finds only its definition and its re-export.
9. **When the service refresh runs.** [server/nodes/README.md](../../../../server/nodes/README.md), in its registries table, says "Service-status refresh on WS connect". The function's docstring says the only cycle is the lifespan's startup task and that client connects do not trigger one ([status_broadcaster.py:1385](../../../../server/services/status_broadcaster.py#L1385)). plugin_system.md has it right.
10. **Legacy memory edges.** [workflow-schema.md](../../../workflow-schema.md), section "Handle Naming Conventions", says the validator rejects `input-memory`. The validator emits a warning, `LEGACY_MEMORY_EDGE`.
11. **The registry table is partial.** plugin_system.md's hand-curated table omits `register_temporal_plugin`, `register_app_source`, `register_node_state_source`, `register_port_source`, `register_workflow_deleted_hook`, `register_graph_listener`, `register_control_listener`, the approval and rule listeners, the chat-cleared and disconnect listeners and the CLI agent trio. The document says the table is hand-curated, so this is an omission.
12. **Module docstrings.** [nodes/__init__.py](../../../../server/nodes/__init__.py#L45) describes legacy bulk-registration files at the package root; none exist. [scaling.py:19](../../../../server/services/plugin/scaling.py#L19) says a new queue needs a worker pool provisioned in worker.py; the pool reads the queues from the registered plugins ([plugin_activities.py:125](../../../../server/services/temporal/plugin_activities.py#L125)).

## 10. Not verified / open questions

1. **Does a bind-only `add_tool` result bind?** The Agent Builder returns a lone `add_node` for a saved tool the run lacks. `rebind_allowed` accepts an operation only when the graph it is given, or the batch, holds an edge from that node to the calling agent. On Temporal the refresh activity loads the saved graph only when the payload carries `workflow_id`, which the workflow adds only under the browser flags ([agent_workflow.py:2666](../../../../server/services/temporal/agent_workflow.py#L2666), [agent_activities.py:1947](../../../../server/services/temporal/agent_activities.py#L1947)). In process the run's edges are extended only by edges named in the batch ([agent_bindings.py:125](../../../../server/services/agent_bindings.py#L125)). INFERRED from reading: the lone operation is skipped on both paths whenever the calling agent's id is known. Not run; the existing tests call the activity without an agent id.
2. **`SimplePlugin` activities and interceptors.** Only `workflows=` is used in the tree. Whether activities supplied that way reach a worker that the calling workflow's task queue uses was not checked.
3. **Which parameters a run reads after `add_skill`.** An employee job keeps its own copy of the graph and parameters, taken when the job is created: for a team plan from the generation's admitted graph, with a live parameter row only for a node that copy lacks ([jobs.py:153](../../../../server/services/employees/jobs.py#L153)). Every agent of the job reads its parameters from that copy ([team_runtime.py:197](../../../../server/services/employees/team_runtime.py#L197), [agent_activities.py:1228](../../../../server/services/temporal/agent_activities.py#L1228)). Start fills the generation's parameters from `normalization.node_parameters` ([deployment/handlers.py:2213](../../../../server/services/deployment/handlers.py#L2213)). Which nodes that covers, and what `parameter_snapshot` a run that is not a job carries, were not traced. So whether a skill merged into an existing Skills node is read "from the next message", as the tool's summary says ([agent_builder:1208](../../../../server/nodes/tool/agent_builder/__init__.py#L1208)), is open.
4. **Skill "loaded" state across workers.** It is a module dict. Behaviour with several worker processes, or after a restart mid-run, was not tested.
5. **After a detached assignment finishes.** `agent.finish_delegation`, the event that starts the lead's review, and `agent.acquire_subagent_permit` (which enforces the concurrency limit) were not read.
6. **Home's setup screen.** It is a second model-authored spec checked against a catalogue ([genui_catalog.py](../../../../server/services/employees/genui_catalog.py)); its parser was not read.
7. **Mobile agent as a delegate.** `mobile_use_agent` is an ActionNode with `supports_delegation` ([mobile/_node.py:128](../../../../server/nodes/mobile/_node.py#L128)) and is not in the delegation schema tuple ([ai.py:2633](../../../../server/services/ai.py#L2633)). INFERRED: `_get_tool_schema` gives its tool the class's own `Params` ([ai.py:2712](../../../../server/services/ai.py#L2712)). Its tool name and its route on Temporal were not traced.
8. **Delegation children without a team.** In process, only an agent on `input-teammates` joins the durable team lifecycle; one on `input-tools` keeps the standalone behaviour ([tools.py:490](../../../../server/services/handlers/tools.py#L490)). On Temporal the permit and the lifecycle rows are skipped when the run has no team id ([agent_workflow.py:1780](../../../../server/services/temporal/agent_workflow.py#L1780)). INFERRED: for such a run the only concurrency cap is the start window of one turn of one agent; nothing counts children across the root execution. Not run.
