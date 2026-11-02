# 06. Independent-node execution

Status: current implementation.

Baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`. Source review is static; no provider or device sessions were run.

## Boundary and layout

Four clearly separated horizontal entry lanes, with no implication that one entry universally supports every node. Top: Canvas Run through WebSocket and WorkflowService into NodeExecutor for ordinary nodes, with a distinct new-Coding-Agent Pi workflow branch from WorkflowService to Activity or agent child. Second: graph admission into MachinaWorkflow with activity or agent-child dispatch. Third: agent tool call into the trusted connected-tool dispatcher and plugin boundary. Bottom: authenticated Workspace task through saved-node authorization/eligibility, durable controller, then invocation. Share only the registered-plugin destination where valid.

## Exact components

- "Canvas single-node Run" (client)
- "WebSocket handler" (service)
- "WorkflowService" (service)
- "NodeExecutor" (service)
- "Graph execution" (client)
- "MachinaWorkflow" (temporal)
- "Activity or agent child" (temporal)
- "Agent tool call" (client)
- "Trusted tool dispatch" (service)
- "Registered plugin boundary" (service)
- "Workspace task REST" (client)
- "Saved-node authorization" (service)
- "Workspace-task eligibility" (service)
- "WorkspaceTaskControllerWorkflow" (temporal)
- "NodeInvocationWorkflow" (temporal)

## Exact directed relationships

- Canvas single-node Run → WebSocket handler
- WebSocket handler → WorkflowService
- WorkflowService → NodeExecutor [ordinary nodes]
- WorkflowService → Activity or agent child [new Coding Agent: Pi workflow]
- NodeExecutor → Registered plugin boundary
- Graph execution → MachinaWorkflow [Temporal admission]
- MachinaWorkflow → Activity or agent child
- Activity or agent child → Registered plugin boundary [runtime-specific]
- Agent tool call → Trusted tool dispatch
- Trusted tool dispatch → Registered plugin boundary [connected tool scope]
- Workspace task REST → Saved-node authorization
- Saved-node authorization → Workspace-task eligibility
- Workspace-task eligibility → WorkspaceTaskControllerWorkflow [enabled and capable]
- WorkspaceTaskControllerWorkflow → NodeInvocationWorkflow [deduplicated submission]
- NodeInvocationWorkflow → Activity or agent child [recorded task dispatch]

## Conditions and caveats

- Independent does not mean every node accepts direct Workspace tasks.
- Saved-node identity, capability, graph connections and credentials remain prerequisites.
- Workspace submissions are epoch-fenced and deduplicated by their controller.

## Code evidence

- `server/routers/websocket.py` — handle_execute_node; saved graph context and WorkflowService admission; line 446 onward.
- `server/services/workflow.py` — WorkflowService.execute_node; lines 202–286, including native Pi admission.
- `server/services/handlers/tools.py` — execute_tool and _dispatch_tool; trusted configuration/connected tool scope.
- `server/services/node_invocations.py` — submit; authorization, workspace_task, enabled state, admission epoch and controller-owned deduplication; lines 167–275.
- `server/services/temporal/node_invocation.py` — NodeInvocationWorkflow; activity/native-agent/Pi dispatch.
- `server/services/temporal/workspace_tasks_workflow.py` — WorkspaceTaskControllerWorkflow; durable per-workflow submission control.

## Artifacts

- Editable source: [06-independent-node-execution.mmd](06-independent-node-execution.mmd).
- Full generation prompt: [06-independent-node-execution.prompt.txt](06-independent-node-execution.prompt.txt).
- Image: `06-independent-node-execution-v1.png` (reviewed built-in ImageGen output).

## Visual review

- Native review: four entry modes remain distinct; ordinary Canvas nodes and new Coding Agent Pi exception both shown.
- Saved-node authorization/eligibility and controller-owned deduplication remain explicit.
- One targeted edit corrected service/process legend and added a line jump separating tool and Workspace execution paths.
