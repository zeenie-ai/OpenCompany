# Let the Orchestrator sleep while its team works

Implementation plan for managed employee teams, reviewed against OpenCompany `a078f889` on November 26, 2026.

## Outcome

Once an Orchestrator assigns work to the Coding Agent, the runtime saves the assignment and releases the Orchestrator's execution slot. During an uneventful ten-minute coding task the Orchestrator makes **zero further model calls and zero Task Manager checks**. A submission, failure, or an explicit question that blocks the worker can wake its immediate manager. Chat receives the Orchestrator's own reviewed submission and replies in the originating conversation.

In the November 26 incident, the Coding Agent worked from 14:21:02 to 14:31:25 IST. The Orchestrator issued 17 thirty-second waits, one task read and one inspection. Its 19 model calls during that interval cost approximately $0.1982. The worker sent one completion notice. This is a baseline from one run. [Native agent tool loop](../../../server/services/temporal/agent_workflow.py) · [Instance review routing](../../../server/services/agent_instances/service.py)

## Architecture

The repository's Dynamic Workflow study recommends retaining managed employees' task receipts, isolated instances and review inbox. At the study's upstream commit, Pydantic's Dynamic Workflow dispatches child calls and records their results. Its Code Mode executor awaits outstanding calls on the host and returns results or exceptions to the suspended computation. OpenCompany can use the same division of work with its existing Task Manager and Temporal session. A checkpointed manager owns an open task while the worker executes. [Repository study, §11.3](CROSS-REFERENCE.md#113-what-changed-for-the-build) · [Pinned Dynamic Workflow dispatch](https://github.com/pydantic/pydantic-ai/blob/d94586aad1acd640715f2bf1152e8d730c8f9938/src/pydantic_ai_harness/pydantic_ai_harness/dynamic_workflow/_toolset.py#L802) · [Pinned Code Mode executor](https://github.com/pydantic/pydantic-ai/blob/d94586aad1acd640715f2bf1152e8d730c8f9938/src/pydantic_ai_harness/pydantic_ai_harness/_monty_exec.py#L446)

| Event | Who takes a turn | What happens to the assignment |
| --- | --- | --- |
| Orchestrator assigns coding and research together | Both workers can start within the configured slot limit | The Orchestrator checkpoints the whole batch and releases its slot. |
| Worker reports ordinary progress | The worker continues | Its update is available to the UI and to the manager at its next turn. |
| Coding Agent needs a decision to proceed | The Coding Agent parks. The Orchestrator answers the question. | The answer resumes the same Coding Agent attempt. The Orchestrator yields again. |
| A worker submits or fails | Its immediate manager reviews the outcome | The manager can accept, request changes, handle the failure, or assign a next step. |
| Orchestrator submits its own result | Chat reviews and replies | The existing delivery receipt binds the answer to the original conversation. |

## Implementation order

1. **Reproduce the cost before changing the loop.** Extend the existing local Temporal single-slot test. Hold a coding worker open and have the manager's model double call `wait_for_agents` if it is scheduled again. Count every model activity, tool check and permit acquisition by instance and attempt. The current test counts only iteration zero, so it misses polling inside one turn. Use the same fixture for the before/after comparison. [Single-slot integration test](../../../server/tests/temporal/test_employee_single_slot_integration.py)

2. **Park at the committed tool-batch boundary.** Scope the behavior to task-scoped managers and reviewers in the admitted managed hierarchy, including Chat when it sends the outer task back for revision. Use the existing `starts_child` tool trait and each operation's successful receipt. Finish the entire batch, preserve call/result order, checkpoint, and finish the execution turn with the parent task still open. Let the session release the permit. A parked turn produces no task submission, completion trigger or user reply. For a mixed batch, retain successful assignments and give the manager the failed operation to correct once actionable. When every dispatch fails, surface the error immediately. [Tool traits](../../../server/services/agent_instances/tools.py) · [Checkpoint and execution loop](../../../server/services/temporal/agent_workflow.py) · [Permit owner](../../../server/services/temporal/agent_instance_workflow.py)

3. **Resume on actionable inbox work.** Preserve the existing assignment identity and review the first child that submits or fails. Consume other ready notices for the same upstream attempt together. Keep every unreviewed outcome actionable across a checkpoint, including one that arrives while the manager is parking. Park again when only outstanding work remains. Accepting a prerequisite can release its dependent even while another sibling runs. For “code, then verify in a browser,” let the manager pass the accepted coding result into the verification assignment. `after` currently supplies ordering only. [Inbox admission, review gate and dependencies](../../../server/services/agent_instances/service.py) · [Event OS wake and reconciliation](../../../RFC-0005-EVENT-OS-ON-TEMPORAL.md)

4. **Support the agreed exception for a blocking decision.** Extend existing instance messaging with an explicit decision request and correlated answer. Derive the immediate assigner and child/upstream attempt identities from the active task. Persist the request, park the worker and release its slot. Admit the manager's decision turn against its upstream task so it can answer. Resume only the requesting worker on a valid answer. Keep routine updates available without waking a parked manager. An unanswered request keeps the worker's task open, including a leaf worker that has no children. Fence duplicate, stale and cancelled requests and answers. Owner-only approvals continue through the approval flow. Cover the native Coding Agent adapter, including Pi when configured, before enabling this behavior for that team. [Current peer messages and task-scoped permissions](../../../server/services/agent_instances/service.py) · [Instance tool contracts](../../../server/services/agent_instances/tools.py)

5. **Separate accepting evidence from finishing the parent mission.** With **Skip reviews when sure** enabled, the current automatic reviewer can accept a child's result and immediately submit the parent's accumulated child results. For a parked manager, keep automatic child acceptance and give the manager one continuation when it needs to decide what to assign next or to write its own submission. In particular, a successful coding task must still lead to required browser verification. Chat retains final review. Update managed-role instructions to batch independent assignments and explain the wake conditions. [Automatic review](../../../server/services/agent_instances/auto_review.py) · [Parent submission](../../../server/services/agent_instances/service.py)

6. **Release through generation admission.** Freeze the behavior at Start and carry it through job admission, instance configuration, tool preparation and resumed turns. Older generations and in-flight jobs retain their admitted behavior. Ship reader support across workers before enabling new admissions. Verify old and new recorded Temporal histories, Stop/Resume, budget pause and Reset. The change fits the existing session, inbox, task board and execution permit. [Instance protocol](../../../server/services/agent_instances/protocol.py) · [Temporal design rules](../../../RFC-0006-TEMPORAL-DESIGN-PATTERNS.md) · [Replay and release checks](../../TEMPORAL_ARCHITECTURE.md)

### Two details that affect correctness

- Record an explicit parked-turn outcome at the checkpoint. The instance session must recognize it before its normal success, output and delivery paths. This matters when Chat requests a revision and when a leaf worker asks a question. Both can end their execution turn with a task still in progress.
- Make wake eligibility consistent in inbox admission and reconciliation. Suppressing a Temporal signal alone would leave an ordinary progress message eligible for the next recovery scan. A decision request carries its own identity, the task attempt and the intended reviewer. The answer refers to that request. Treat a duplicate as the same exchange. Workers choose this message type explicitly when they need a decision.

## Release checks

| Scenario | Evidence required |
| --- | --- |
| One slot, slow Coding Agent, no actionable event | Zero manager model activities, status tools and wait timers between committed handoff and outcome. The worker acquires the released slot. |
| Parallel siblings, dependency, or partial assignment failure | Successful siblings run once. Independent results can arrive in either order. Accepting A releases dependent B. A failed dispatch is visible and correctable. |
| Blocking question alongside routine progress | Progress causes zero manager turns. One question reaches the immediate assigner and one answer resumes the same worker attempt, including with one slot. |
| Nested manager, failure, retry, or requested changes | Each outcome travels one level. The revised task keeps its board identity. Chat sends one final reply after reviewing the outer assignment. |
| Code followed by browser verification | With automatic review both on and off, the required verification happens before parent submission. |
| Completion at checkpoint, duplicate delivery, restart, Stop/Resume, budget pause, or Reset | No lost review or duplicated work. A paused team consumes no model turns. A stale answer or outcome cannot resume a replacement attempt. Old histories replay. |

After these checks, compare a controlled normal-UI job with the incident using the existing model usage ledger, task receipts and inbox timestamps. Report manager calls during idle time separately from the cost of dispatch, substantive decisions and final review. The UI can refresh progress throughout.
