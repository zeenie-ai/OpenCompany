# 05. Workflow execution

Status: current implementation.

Baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`. Source review is static; no provider or device sessions were run.

## Boundary and layout

Three columns: admission at left, Temporal orchestration and three dispatch lanes across the center, plugin/provider work and durable results at right. Place the eligible sequential path in a lower inset, and a small dispatch-error exit beside TemporalExecutor.

## Exact components

- "Graph execution request" (client)
- "WorkflowService" (service)
- "Admission and routing" (service)
- "TemporalExecutor" (temporal)
- "MachinaWorkflow" (temporal)
- "Typed or legacy activity" (temporal)
- "AgentWorkflow" (temporal)
- "PiCodingAgentWorkflow" (temporal)
- "NodeExecutor" (service)
- "Registered plugin" (service)
- "Recorded model / tool operations" (temporal)
- "Providers and tools" (external)
- "Stored results and status" (state)
- "Eligible sequential execution" (service)
- "Error returned" (service)

## Exact directed relationships

- Graph execution request → WorkflowService
- WorkflowService → Admission and routing
- Admission and routing → TemporalExecutor [Temporal selected]
- TemporalExecutor → MachinaWorkflow
- MachinaWorkflow → Typed or legacy activity [frozen dispatch]
- MachinaWorkflow → AgentWorkflow [native agent]
- MachinaWorkflow → PiCodingAgentWorkflow [recorded Pi runtime]
- Typed or legacy activity → NodeExecutor
- NodeExecutor → Registered plugin
- Registered plugin → Providers and tools
- AgentWorkflow → Recorded model / tool operations
- PiCodingAgentWorkflow → Recorded model / tool operations
- Recorded model / tool operations → Providers and tools
- Registered plugin → Stored results and status [node outputs]
- Recorded model / tool operations → Stored results and status [recorded results]
- Admission and routing → Eligible sequential execution [eligible local path]
- Eligible sequential execution → NodeExecutor
- TemporalExecutor → Error returned [dispatch failure]

## Conditions and caveats

- Routing is frozen for a run; old histories retain their recorded dispatch.
- Redis parallel code is unreachable in shipped configurations.
- A Temporal dispatch failure returns an error; it does not rerun the graph locally.

## Code evidence

- `server/services/workflow.py` — WorkflowService.execute_workflow, _execute_temporal, _execute_sequential; lines 474, 541–615, 619, 726.
- `server/services/temporal/workflow.py` — MachinaWorkflow._resolve_dispatch and _resolve_activity; lines 972–1105.
- `server/services/node_executor.py` — NodeExecutor.execute; plugin admission/parameter resolution/execution.
- `server/services/temporal/executor.py` — TemporalExecutor.execute_workflow; starts MachinaWorkflow.

## Artifacts

- Editable source: [05-workflow-execution.mmd](05-workflow-execution.mmd).
- Full generation prompt: [05-workflow-execution.prompt.txt](05-workflow-execution.prompt.txt).
- Image: `05-workflow-execution-v1.png` (reviewed built-in ImageGen output).

## Visual review

- Reviewed native generated image: all dispatch branches, frozen routing, eligible sequential route, output persistence and failure caveats present.
- Three targeted ImageGen edits removed duplicated NodeExecutor and ambiguous operations/sequential routing; final routes remain separated.

- Peer review requested a clearer AgentWorkflow input arrow. Two further targeted edits produced a top-entry agent/Pi input separated from rightward provider/result outputs; final selected image reviewed.
