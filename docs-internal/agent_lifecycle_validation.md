# Reusable agent lifecycle validation

Validated on 2026-11-20 using deterministic fixtures, isolated SQLite databases,
mock provider boundaries and recorded Temporal histories. This record describes
protocol checks, not live model evaluation. It establishes no comparative Grok,
DeepSeek or Kimi performance result.

The current contract is in [Agent Teams](./agent_teams.md), with
[conversation isolation](./agent_context_flow.md),
[adapter continuation](./cli_agent_framework.md) and
[Temporal ownership/recovery](./TEMPORAL_ARCHITECTURE.md) documented separately.

## Acceptance coverage

| Area | Primary regression files | Checked behavior |
|---|---|---|
| Public tool contract | `server/tests/services/test_agent_lifecycle_tools.py` | Instructions-only spawn, required fields, unknown-argument rejection, trusted scope and frozen template bindings. |
| Durable storage | `server/tests/services/agent_instances/test_store.py` | Atomic/idempotent admission, changed-payload conflicts, task revision/attempt fences, preserved feedback, retry/reassignment, dependencies, mailbox quotas, peer chains, fair recovery scans and additive upgrade. |
| Session owner | `server/tests/temporal/test_agent_instance_workflow.py` | Serialized turns, three-error halt, repeated provider call IDs, complete tool receipts, duplicate review triggers, interruption, waits that release capacity, orphaned permit recovery and late-acquisition fencing. |
| CLI/MCP boundary | `server/tests/services/cli_agent/test_orchestration_boundary.py`, `server/tests/services/cli_agent/test_instance_adapters.py` | Validation before generated handler signatures, durable budgets, explicit provider identity, startup obligations, missing-session failure and checkpoint/full-result retention through actual node execution. |
| RLM dispatch | `server/tests/services/test_rlm_admission.py` | Root/nested/fallback request fencing, effect admission and draining an already-admitted background completion. |
| Owner review recovery | `server/tests/services/employees/test_review_recovery.py` | Owner authentication, idempotent retry and reuse of the original lead/job. |
| Worker startup | `server/tests/temporal/test_worker_restart.py` | Recovery runs alongside the worker and its local task is reaped on shutdown. |
| Reconciler retirement | `server/tests/temporal/test_reconciler_retirement.py` | A real Temporal service records zero workflow executions for idle local scans and closes the running legacy reconciler on upgrade. |
| Compatibility | `server/tests/temporal/test_workflow_identity_golden.py`, `server/tests/temporal/test_replay_corpus.py` | Registered command identities, generation flags and retained previous-release replay corpus. |
| UI | Context, Team Monitor, Job Progress and Employee Work component tests | Separate instance display, read-only job context and review-retry interaction. |

The runtime fixtures test representative malformed requests without invoking a
paid provider. A successful unrelated tool call does not clear the orchestration
error budget; automatic inbox wakeups do not recover a halted instance.

## Recorded results

| Suite | Result |
|---|---|
| Full `server/tests/temporal` plus instance storage | 925 passed, 8 skipped, one subprocess teardown timeout on the first run. |
| Focused detached Pi replay rerun | 1 passed in 7.49 seconds. All six scenarios and their replays had already passed before the first run's teardown timeout. Together these runs resolved all 926 executed checks. |
| Storage/tool/CLI/RLM/review/context boundary selection | 110 passed. |
| Configuration suite | 78 passed. |
| Four affected UI test files | 27 passed. |
| TypeScript and whitespace validation | `bun run typecheck:tsc` and `git diff --check` passed. |

These suites overlap; their counts must not be added as a unique test total.
Skipped cases are not evidence of runtime coverage. Provider-boundary tests do
not establish production account/session availability, throughput or model task
quality. This run used SQLite; it is not a PostgreSQL deployment certification.

## Reproducing the checks

From `server/`, use the project virtual environment and set `DEBUG=false`:

```powershell
$env:DEBUG = 'false'
.venv/Scripts/python.exe -m pytest tests/temporal tests/services/agent_instances -q
.venv/Scripts/python.exe -m pytest tests/services/test_agent_lifecycle_tools.py tests/services/cli_agent/test_orchestration_boundary.py tests/services/cli_agent/test_instance_adapters.py tests/services/cli_agent/test_workflow_tool_context.py tests/services/test_rlm_admission.py tests/services/test_agent_bindings.py tests/nodes/test_specialized_context_bridges.py tests/services/cli_agent/test_claude_pool_lease.py tests/services/employees/test_review_recovery.py tests/nodes/test_context_handlers.py -q
.venv/Scripts/python.exe -m pytest tests/core_config -q
```

For a focused check of the recorded teardown timeout:

```powershell
.venv/Scripts/python.exe -m pytest tests/temporal/test_pi_inline_replay.py -q
```

From `client/`:

```sh
bun run test src/components/parameterPanel/__tests__/ContextMemoryPanels.test.tsx src/components/parameterPanel/__tests__/TeamMonitorPanel.test.tsx src/features/home/employee/__tests__/JobProgress.test.tsx src/features/home/employee/__tests__/EmployeeWork.test.tsx
bun run typecheck:tsc
```

When changing workflow commands, preserve the recorded generation gate, update
the explicit versioning entry in the identity fixture and run the replay checks.
Do not regenerate old histories to conceal a replay incompatibility.
