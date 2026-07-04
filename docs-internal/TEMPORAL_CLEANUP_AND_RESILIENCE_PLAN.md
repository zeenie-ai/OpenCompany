# RFC: Temporal Cleanup, Queue Routing, Resilience & Performance (Waves 15-18)

- **Status**: Approved, in implementation
- **Drafted**: 2026-07-03 (all code facts verified against the working tree at that date via a 5-agent parallel sweep)
- **Baseline**: `5b384b7` (v0.0.92)
- **Supersedes**: the Wave 12/13 tracker (shipped; see `docs-internal/event_framework.md`)

## Implementation status

| Phase | Status |
|---|---|
| 15.1 — drop `WorkflowEvent.connection_status` | ✅ shipped `8bc96c1` |
| 15.2 — retire APScheduler cron stack | ✅ shipped `eda3a20` (-272 LOC; Temporal hard-required for cron) |
| 15.3 — retire Redis-Streams branch (D2b) | ✅ shipped `76576b1` (-347/+57 LOC; `dispatch_async` gone, `set_cache_service` → `capture_main_loop`, cache consumer-group methods gone; `_streams_available` flag KEPT — the surviving `stream_add` guards on it, a detail §4 originally missed) |
| 15.4 — docs sweep | ✅ shipped (this commit) |
| 16.x / 17.x / 18.x | ⏳ in progress |

---

## §1 Motivation

Wave 12 made the Temporal-native CloudEvents canary path the production default (`event_framework_enabled=True`, `core/config.py:95-98`; 8/9 trigger types canary-registered, Twitter deferred). That migration stranded three legacy subsystems that now run as unreachable or fallback-only code, and it exposed four infrastructure gaps that matter for MachinaOS's two deployment topologies (developer laptop that sleeps/hard-kills vs always-on cloud VM):

1. **Dead code** (~556 LOC): the `event_waiter.py` Redis-Streams branch, the APScheduler cron stack, and one orphaned CloudEvents factory.
2. **Declared-but-disabled queue routing**: 75 plugin `task_queue` declarations across 9 queues are silently ignored — every activity runs in one 100-slot pool on `"machina-tasks"`.
3. **Resilience gaps**: non-idempotent LLM activities retried 3× (token burn on laptop sleep), no cron catch-up bound, zero Temporal SDK interceptors (no retry-vs-fresh observability), no periodic heartbeat during long activity bodies.
4. **Performance knobs unset**: no per-queue rate limits, no sticky-cache sizing, no poller autoscaling — despite the installed SDK (temporalio **1.25.0**, verified in `.venv` dist-info) exposing all the modern APIs (`PollerBehaviorAutoscaling`, `ResourceBasedSlotSupplier`, `WorkerTuner` all confirmed present).

**Repo delta since last verification (17 commits, Jun 12 → Jul 2)**: Temporal dev-server hardening (health-check gRPC polling + worker auto-restart with exponential backoff, `6ed20af`), montyExecutor plugin (`3896b8a`), CLAUDE.md restructure 5346→2766 lines (`0b5ad7f`), docs path-drift fixes (`cbac075`), dep security bumps (`4e5d4e6`). **None of these implemented any part of this RFC** — all targets re-verified present at the line numbers cited below. The LiteLLM router RFC is **draft-only** (zero `litellm` imports, no dependency, native `services/llm/` intact).

**Wave numbering**: CLAUDE.md's latest shipped wave is **Wave 14** (Workflow Naming). Forward waves are **15-18**. On shipping each wave, add its completion marker to CLAUDE.md so future plans don't collide.

## §2 Verified current state (2026-07-03)

| Subsystem | State | Key citations |
|---|---|---|
| `WorkflowEvent.connection_status` | Defined, zero production callers (plugin-local factories replaced it in Wave-12 B1-B3) | `services/events/envelope.py:123-138` |
| APScheduler stack | Fully wired but only reachable as Temporal-down fallback | `services/scheduler.py` (125 LOC); `deployment/triggers.py:12,21,36-72`; `deployment/manager.py:81,260-262,316,393-481` (fallback L440-481, `setup_cron` call L471); `main.py:69-71,185,187,483`; `pyproject.toml:84`; `requirements.txt:38` |
| `event_waiter.py` Redis-Streams branch | Present, unused in canary-on mode | 808 LOC total; `is_redis_mode` L55-61; stream constants L408-418; `register` Redis branch L450-473; `wait_for_event` dispatch L490-507; `_wait_redis` L525-591; `_cleanup_waiter` L599-602; `dispatch_async` L635-663; `dispatch` Redis branch L684-698; `cancel` L748-751; `clear_all` L799-801; `get_backend_mode` L806-807 |
| `core/cache.py` stream methods | Consumer-group methods have zero callers outside event_waiter; `stream_add`/`stream_read` still consumed | 439 LOC; `stream_add` L287-312; `stream_read` L314-332; `stream_create_group` L334-359; `stream_read_group` L361-388; `stream_ack` L390-408; `stream_delete` L410-427; `_streams_available` flag L43 + L79-107 probe; consumers `execution/cache.py:358` (`add_event`) + `:379` (`get_events`, itself uncalled) |
| Task queues | 9-member `TaskQueue` enum; **75 plugin declarations** (DEFAULT=26, REST_API=21, AI_HEAVY=8, MESSAGING=7, TRIGGERS_EVENT=5, TRIGGERS_POLL=4, BROWSER=2, CODE_EXEC=1, ANDROID=1); routing deliberately disabled | `plugin/scaling.py:23-31` (enum), `:88-96` (timeout defaults: DEFAULT_START_TO_CLOSE=10m, DEFAULT_HEARTBEAT=2m, AI=30m, TRIGGER=24h, CODE=5m); `temporal/workflow.py:450-484` (`_resolve_activity`, return at **L484**), `:367-368` (dispatch consumer, already honours non-None queue) |
| Workers | Single `TemporalWorkerManager` on `"machina-tasks"`, 100 slots; `TemporalWorkerPool` fully implemented, never instantiated | `temporal/worker.py:159-181` (manager Worker, `max_concurrent_activities=self.pool_size` L178, no `interceptors=`/`identity=`); `:262-357` (pool class; `DEFAULT_CONCURRENCY` L282-292; `_concurrency_for` L310-317; pool Worker L334-341); lifespan start `main.py:354-359` (`app.state.temporal_worker_manager`), stop `main.py:428-434` |
| Config flags | `temporal_per_type_dispatch` (L64), `event_framework_enabled` (L95-98, default True), `temporal_graceful_shutdown_seconds` (L81-84); **`temporal_worker_pool_enabled` + `deployment_mode` do not exist** | `core/config.py` |
| Agent retry policies | `execute_llm_step.v1` callsite passes shared `AGENT_ACTIVITY_RETRY` (= `DEFAULT_ACTIVITY_RETRY`, **maximum_attempts=3**); **`refresh_tools.v1` also uses it (L509)**; no try/except around the LLM step | `temporal/agent_workflow.py:294-300` (LLM step), `:509` (refresh_tools), `:457-526` (tool-call try/except exists); `temporal/_retry_policies.py:51-54` (non-retryables), `:62-66` (DEFAULT, max=3 at L65), `:73-77` (QUICK), `:81-84` (exports) |
| Cron schedules | `ScheduleSpec` has **no `catchup_window`**; `overlap_policy=SKIP` | `temporal/schedules.py:70-82` (signature, SKIP default L81), `:121-124` (ScheduleSpec), `:125` (SchedulePolicy) |
| Heartbeats | `as_activity` beats at body start + completion only — no periodic beat during execution | `plugin/base.py:655` (method), `:734` + `:793` (the two `activity.heartbeat` calls) |
| Interceptors / perf knobs | `temporal/_interceptors.py` does not exist; zero `interceptors=`, `identity=`, `max_activities_per_second`, `PollerBehaviorAutoscaling`, `max_cached_workflows`, `ResourceBasedSlotSupplier` anywhere in worker.py | grep-verified |
| SDK | Pin `temporalio>=1.7.0`; **installed 1.25.0** — `PollerBehaviorAutoscaling`, `ResourceBasedSlotSupplier`, `WorkerTuner` all present in installed package | `pyproject.toml:131`; `.venv/Lib/site-packages/temporalio-1.25.0.dist-info` |
| Tests | No test patches any deletion target (`_wait_redis`, `is_redis_mode`, `stream_*_group`, `register_cron_job`, `setup_cron`); `test_task_queue_coverage.py` does not exist | grep-verified across `server/tests/` |

## §3 Design principles

1. **Consumers before producers** — delete callsites before the modules they call (manager → triggers → scheduler → dep).
2. **One flag per behavioural change** — every activation (queue routing) ships behind an env-var gate with a documented rollback; deletions rollback via `git revert`.
3. **Never touch the load-bearing memory path** — `event_waiter` memory mode backs canvas-Run via `TriggerNode.execute()` (`plugin/trigger.py:103`); Twitter still transits `handlers/triggers.py:handle_trigger_node`.
4. **Workflow workers ≠ activity workers** — workflows and framework activities stay on `"machina-tasks"`; only plugin activities fan out to specialized queues (canonical Temporal separation).
5. **Idempotency decides retry policy** — non-idempotent activities (LLM calls) get `maximum_attempts=1` with workflow-level retry decisions; idempotent activities keep shared multi-attempt policies.

## §4 Wave 15 — Dead-code retirement (~556 LOC removed)

### 15.1 Drop `WorkflowEvent.connection_status` (16 LOC)
Delete `services/events/envelope.py:123-138`. Zero callers.
**Verify**: `pytest server/tests/test_plugin_self_containment.py server/tests/test_status_broadcasts.py -k "connection_status or self_containment"` + full smoke.

### 15.2 Retire APScheduler cron stack (~240 LOC)
- Delete `services/scheduler.py` (125 LOC).
- `deployment/triggers.py`: drop import L12, `_active_cron_jobs` L21, methods L36-72 (`setup_cron`/`teardown_cron`/`get_cron_node_ids`/`teardown_all_crons`). All external callers are in `manager.py` (L260, L262, L471) and are removed below.
- `deployment/manager.py`: delete the legacy fallback L440-481 inside `_setup_cron_trigger` (L393-481); replace the Temporal-unavailable fall-through with `raise RuntimeError("Temporal required for cronScheduler")`; drop `_cron_iterations` (L81, L316); drop the `get_cron_node_ids`/`teardown_all_crons` dance in `cancel()` (L260-262); update the method docstring ("Two paths" framing).
- `main.py`: drop L69-71 (logger silencers), L185 (import), L187 (`start_scheduler()`), L483 (`shutdown_scheduler()`).
- `pyproject.toml:84` + `requirements.txt:38`: drop apscheduler. Note in commit: next `uv sync` evicts it + transitive `tzlocal`.
**Verify**: `pytest server/tests/test_cron_canary.py server/tests/nodes/test_workflow_triggers.py server/tests/test_deployment_canary_listener.py server/tests/test_retry_policies.py`; manual — deploy a `cronScheduler` workflow, confirm Temporal Schedule fires (Web UI :8233). **Risk**: cron deploys now raise if Temporal is down (acceptable: `6ed20af` added worker auto-restart + health-check polling, so Temporal-down is transient).

### 15.3 Retire Redis-Streams branch (~300 LOC)
- `event_waiter.py`: delete `is_redis_mode` (L55-61), stream constants + `_get_stream_name` (L408-418), `register` Redis branch (L450-473; dedent memory else), collapse `wait_for_event` dispatch (L490-507) to inline memory wait, delete `_wait_redis` (L525-591), `_cleanup_waiter` Redis branch (L599-602), `dispatch_async` (L635-663), `dispatch` Redis branch (L684-698), `cancel` Redis branch (L748-751), `clear_all` Redis branch (L799-801), `get_backend_mode` (L806-807). Simplify `set_cache_service` (L34-47): drop mode probe, **keep `_main_loop` capture** (thread-safe dispatch). Update module docstring (L1-9).
- `core/cache.py`: delete `stream_create_group` (L334-359), `stream_read_group` (L361-388), `stream_ack` (L390-408), `stream_delete` (L410-427), `is_streams_available` + `_streams_available` flag (L43, probe in L79-107 startup block). **Keep `stream_add` (L287-312) + `stream_read` (L314-332)** — consumed by `execution/cache.py:358/:379`.
**Verify**: `pytest server/tests/services/test_events.py server/tests/test_status_broadcasts.py server/tests/nodes/test_whatsapp.py server/tests/nodes/test_twitter.py server/tests/nodes/test_telegram_social.py server/tests/test_event_framework_phase_a.py`; manual — deploy `telegramReceive` + `webhookTrigger` with `REDIS_ENABLED=true`, both fire end-to-end; canvas-Run on a single trigger node still waits + fires (memory path).

### 15.4 Docs sweep
`docs-internal/event_waiter_system.md` (drop Redis-mode section), `SETUP.md`/`SCRIPTS.md`/`CLAUDE.md` (Redis = non-stream KV + parallel-exec fallback only), mark D2b shipped in the repo copy of this RFC.

## §5 Wave 16 — Activate per-queue task routing (~115 LOC)

Gate: new `temporal_worker_pool_enabled: bool = Field(default=False, env="TEMPORAL_WORKER_POOL_ENABLED")` in `core/config.py`.

### 16.1 Pre-flight invariants
NEW `server/tests/test_task_queue_coverage.py`: (a) every `TaskQueue` member except DEFAULT has ≥1 plugin or a "reserved" comment; (b) every `cls.task_queue` is a declared enum member; (c) `TemporalWorkerPool.DEFAULT_CONCURRENCY` (worker.py:282-292) covers every enum member.

### 16.2 Wire `TemporalWorkerPool` into lifespan
`main.py` after manager start (L354-359 pattern, mirror `app.state.temporal_worker_manager`): if flag on, instantiate + start pool, store `app.state.temporal_pool`; stop in the shutdown block (near L428-434). `.env.template`: `TEMPORAL_WORKER_POOL_ENABLED=false` + rollback comment.
**Verify**: flag on → `[Pool] Started worker queue='ai-heavy' activities=N concurrency=4` per populated queue; Web UI shows the extra workers. Pool is inert while 16.3 unshipped.

### 16.3 Flip `_resolve_activity` (gated)
`temporal/workflow.py:484`: `return f"node.{cls.type}.v{cls.version}", (cls.task_queue if settings.temporal_worker_pool_enabled else None)`. Update docstring L451-473 (drop "until wired" caveat). Dispatch consumer at L367-368 already honours the queue. **Verified**: no code path schedules legacy `execute_node_activity` to a specialized queue.
**Verify**: flag on → `aiAgent` activity lands on `ai-heavy` worker (Web UI Workers tab); flag off → single-manager routing (rollback drill).

### 16.4 Default flip (after one stable release)
Default `True`; invariant `test_temporal_worker_pool_enabled_defaults_true`; document topology + rollback in `docs-internal/TEMPORAL_ARCHITECTURE.md`.

## §6 Wave 17 — Resilience hardening (~105 LOC, all additive)

### 17.1 Cron `catchup_window`
`temporal/schedules.py:121-124`: add `catchup_window=timedelta(hours=24)` to `ScheduleSpec`. SKIP overlap (L81) collapses the make-up burst to one firing after laptop wake.

### 17.2 One-shot retry on `execute_llm_step.v1`
`temporal/agent_workflow.py:294-300`: replace `retry_policy=AGENT_ACTIVITY_RETRY` with inline `RetryPolicy(maximum_attempts=1)`; wrap the callsite so `AgentWorkflow.run` catches `ActivityError` and decides break-vs-retry with intact message history (tool-call dispatch already has this pattern at L457-526 to mirror).
- **Do NOT mutate `AGENT_ACTIVITY_RETRY`** (`_retry_policies.py:101` region) — shared by retry-safe activities.
- **Scope ruling (critic finding)**: `refresh_tools.v1` (agent_workflow.py:509) also uses `AGENT_ACTIVITY_RETRY` — it rebuilds the tool surface from canvas state, fully idempotent → **keeps 3 attempts**; add a one-line comment there documenting the deliberate asymmetry.
- **LiteLLM note (resolved)**: the untracked LiteLLM RFC's stage B touches in-process `services/ai.py:_run_agent_loop`; this phase touches the Temporal `execute_llm_step.v1` path. **No code-path overlap; independent ship order.**
**Verify**: kill process mid-LLM call → Event History shows attempt=1; error surfaces to canvas.

### 17.3 `ObservabilityInterceptor`
NEW `temporal/_interceptors.py` (~50 LOC): `ActivityObservabilityInterceptor` (log `activity_start` / `activity_retry` when `activity.info().attempt > 1` / `activity_end` + outcome), `WorkflowObservabilityInterceptor` (log `workflow_start` guarded by `not workflow.unsafe.is_read_only()` — replay-safe per SDK docs), `ObservabilityWorkerInterceptor` wiring class. Pass `interceptors=[...]` to manager Worker (L159-181) + pool Worker (L334-341).
**Verify**: kill worker mid-activity, restart → one `activity_retry` log at attempt=2.

### 17.4 `DEPLOYMENT_MODE` + worker identity
`core/config.py`: `deployment_mode: Literal["local", "cloud", "self_hosted"] = Field(default="local", env="DEPLOYMENT_MODE")`. Manager Worker: `identity=f"machina-default-{mode}"`; pool Workers: `identity=f"machina-{queue}-{mode}"`. `.env.template` entry.

### 17.5 Mode-aware concurrency defaults
`_concurrency_for` (worker.py:310-317): halve queue defaults when `deployment_mode == "local"`; explicit `TEMPORAL_<QUEUE>_CONCURRENCY` env vars still win. `.env.template`: commented 4-core-laptop vs 32-core-cloud examples.

### 17.6 Periodic heartbeat during long bodies
`plugin/base.py` `_node_activity` closure (existing beats at L734/L793 only bracket the body): spawn `asyncio.create_task(_beat_loop())` beating every 30s, cancel in `finally`. Skip when `start_to_close_timeout ≤ heartbeat_timeout` (2m default, scaling.py:88-96).
**Verify**: kill process 60s into a browser workflow → re-dispatch within 2m heartbeat_timeout, not 10m start_to_close.

## §7 Wave 18 — Worker performance tuning (~70 LOC + docs)

**Ungated (critic-confirmed)**: installed temporalio **1.25.0** exposes `PollerBehaviorAutoscaling`, `ResourceBasedSlotSupplier`, `WorkerTuner`. Record the floor: bump `pyproject.toml:131` pin to `temporalio>=1.25.0` as part of 18.1.

### 18.1 Per-queue activity rate limits
`TemporalWorkerPool`: `DEFAULT_RATE_LIMIT` dict (`ai-heavy: 60/s`, `rest-api: 100/s`, `browser: 10/s`, `messaging: 20/s`, others None) + `TEMPORAL_<QUEUE>_RATE_LIMIT` env override → `max_activities_per_second=` on pool Worker (L334-341).

### 18.2 Sticky workflow cache by mode
Manager Worker only (pool workers host no workflows): `max_cached_workflows=` local=50 / cloud=500 / self_hosted=100.

### 18.3 Poller autoscaling
Manager: `activity_task_poller_behavior=PollerBehaviorAutoscaling(initial=2, minimum=1, maximum=10)`, `workflow_task_poller_behavior=PollerBehaviorAutoscaling(initial=2, minimum=1, maximum=20)`. Pool workers: initial=1/max=5. Invariant: pollers < executors (Temporal docs).

### 18.4 Resource-based slot supplier for ai-heavy + browser
`ResourceBasedSlotSupplier(target_cpu_usage=0.8, target_memory_usage=0.8)` via `WorkerTuner` for the two unpredictable queues; fixed sizing elsewhere.

### 18.5 Tuning-recipe docs
`docs-internal/TEMPORAL_ARCHITECTURE.md`: per-queue concurrency/rate table, cache sizing by mode, poller autoscaling, metric watchlist (`schedule_to_start_latency`, `worker_task_slots_available`, `poll_success_rate` ≥90%, `sticky_cache_evictions_total` = 0), tuning order host → slots → pollers → rate limits.

## §8 Ship order

```
1. Wave 15 (pure deletion)                    4. Wave 16.3 (flip routing gate)
2. Wave 16.1 + 16.2 (pool wired, gated off)   5. Wave 17.3 + 17.6 (interceptors + heartbeat)
3. Wave 17.1 + 17.2 + 17.4 (independent)      6. Wave 17.5 + 18.1-18.3 (tuning)
                                              7. Wave 16.4 (default flip, after stability window)
                                              8. Wave 18.4 + 18.5 (tuner + docs)
```

Each phase = one commit, independently revertable. Rollback channels: `git revert` (Wave 15), `TEMPORAL_WORKER_POOL_ENABLED=false` (Waves 16-18 routing/tuning).

## §9 End-to-end verification

```bash
cd server && .venv/Scripts/python.exe -m pytest -q          # full suite per phase + at end

npm run dev   # manual canary smoke, Temporal Web UI http://localhost:8233:
# - TriggerListenerWorkflow fires: chat/webhook/task/telegram/whatsapp/email
# - PollingTriggerWorkflow fires: gmail
# - cronScheduler fires via Temporal Schedule (APScheduler gone)
# - aiAgent activity routes to ai-heavy worker (post-16.3)
# - activity_retry interceptor log on worker kill+restart (post-17.3)
# - re-dispatch within 2m on kill during browser body (post-17.6)
# - ai-heavy throttles at configured rate (post-18.1)

# Rollback drills: TEMPORAL_WORKER_POOL_ENABLED=false (routing) — activities return to single manager
```

## §10 Out of scope / deferred

| Item | Disposition |
|---|---|
| Twitter `PollingTriggerNode` refactor (~150 LOC) → unlocks `handlers/triggers.py:handle_trigger_node` retirement | Separate enablement wave |
| D4 message/newsletter/history dual-emit drain (6 wire keys; only `whatsapp_message_received` has an FE consumer) | Future FE session |
| LiteLLM router migration (`docs-internal/litellm_router_migration_rfc.md`, draft-only) | Its own RFC; no code-path overlap with this plan (verified §6 17.2) |
| WorkflowEnvironment integration smoke test | When prioritised |
| 22 pre-existing pytest failures (16 fixable via one `tests/nodes/_harness.py` eager-import) | Individual commits |
| gRPC event transport | **Rejected for now** — Temporal already owns the gRPC control plane; browsers need WS regardless; `WorkflowEvent` is shape-compatible with the CloudEvents gRPC binding, so a codec can be added later with zero envelope changes. Revisit on multi-process tier split or an external CloudEvents-over-gRPC partner. |

## LOC budget

| Wave | Removed | Added |
|---|---|---|
| 15 | ~556 | 0 |
| 16 | 0 | ~115 |
| 17 | 0 | ~105 |
| 18 | 0 | ~70 + ~80 docs |
| **Net** | | **≈ -266 LOC** |
