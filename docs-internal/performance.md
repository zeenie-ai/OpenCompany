# Performance — Cold-Start, Bundle, Runtime Latency

Living record of OpenCompany's performance posture: where launch time is
spent, what each optimisation cost / saved, and how to reproduce the
measurements. Update this file when you ship a perf-affecting change
or take a new measurement; numbers without timestamps and a commit
reference rot fast.

## Native browser live view (2026-09-26)

Commit `87322eeb` separates frame acknowledgements from ordered browser input,
retains trailing frames under FPS throttling, and returns every Chrome capture
credit after bounded queue admission. Commit `681a340f` adds the shared Normal
and Dev workspace viewer, bounded decoding and canvas backing-store reuse.

The final isolated Chrome 153 benchmark used production stream/CDP code and
a reference canvas renderer, with a 1,000 ms observation deadline:

| FPS | Baseline missed updates | Updated missed updates | Updated input-to-paint p50 / p95 |
| --- | --- | --- | --- |
| 30 | 1 / 50 | 0 / 50 | 29.6 / 41.8 ms |
| 10 | 10 / 20 | 0 / 20 | 80.8 / 129.9 ms |

All injected clicks reached the target. The evidence supports more reliable
frame delivery in this fixture, not a general speedup: baseline percentiles
exclude missed updates, runs varied during development, and the full React
renderer and pinned Chrome 154 were not measured. Agent CLI operation latency
and cold installation are separate paths. See
[Browser workspace](./browser_workspace.md#local-measurements-2026-09-26) for
complete results, source hashes, limitations and repeatable commands.

Use `OPENCOMPANY_BROWSER_DIAGNOSTICS=1` for server timing summaries and
`?browserStreamDebug=1` for numeric client decode/draw summaries. No screenshot
or input payloads are logged by these diagnostics.

## Headline numbers (current baseline)

Measured on Windows, dev mode (`bun run dev` → `company dev`),
warm OS file cache, with bytecode pre-compile applied:

| Metric | Value | Source |
|---|---|---|
| Application startup complete | **2.90 s** | `start.log` 14:38 (post-`e77215c`, May 6 2026) |
| HTTP `ready on port $PYTHON_BACKEND_PORT` | **3.17 s** | same |
| First WebSocket client connected | **8.29 s** | same |
| Status broadcasters fully settled | **12.27 s** | same |
| AIService import (warm) | **703 ms** | same — was ~31 s on v0.0.75 |
| Application startup complete (cold, post-`company clean` first launch) | **21.5 s** | `cold.txt` 18:44 (2026-07-14, post boot-delay fixes; was **71 s** the same day pre-fix) |
| LLM provider registration (all 13, cold) | **17 ms** | same — was 44.6 s pre-fix (eager SDK imports) |
| Vite production build | **~16 s** | last `vite build` run |
| Vite main bundle | **234 KB gz** | `client/dist/assets/index-*.js` |

The cold-disk first launch on the same code is slower because Windows
Defender first-touch-scans freshly written files (the rebuilt `.venv`
is ~389 MB / 18,300 files) and fresh DBs/salt are created. Before the
2026-07-14 boot-delay fixes a post-`company clean` boot measured
**71 s** (5.2× the warm boot / 24× the baseline; `cold.txt` 12:55);
after the fixes the same cycle measures **21.5 s** (`cold.txt` 18:44).
The remaining cold gap over the 2.90 s warm number is Defender/disk
I/O plus the items in "Open follow-ups" — not bytecode compilation,
which now happens at build time (`[tool.uv] compile-bytecode = true`
+ the `-O`-less compileall step below).

### Re-measured after the pnpm → bun migration (2026-09-02)

Warm boots on the same Windows machine, taken from the supervisor's
timestamped console output right after the switch to bun@1.4.0:

| Metric | Pre-bun (table above) | Post-bun |
|---|---|---|
| `company start` — Application startup complete | 2.90 s | ~3.6 s |
| `company start` — `ready on port $PYTHON_BACKEND_PORT` | 3.17 s | 4.56 s |
| `company dev` — Vite `ready in` | — | 8.4 s |
| `company dev` — backend Application startup complete | — | 12.4 s |

None of the boot delta is attributable to bun: it only installs the
workspace and launches the `company` CLI; everything after spawn
(uvicorn imports, lifespan, Vite on Node 22) is byte-identical. The
extra ~0.7 s sits entirely in the import phase — "all imports complete"
at 3.29 s vs 1.98 s in the May timeline, and the routers + plugin-walker
segment at 1.83 s vs 0.84 s — because the walker now loads **185**
plugin modules against the **137** it walked in May. Lifespan cost is
unchanged.

**Counting rule for every plugin figure in this document.** The numbers
(137 in May, 152 in July, 185 in September 2026) are the walker's
*module* count — the `node plugins loaded: N modules` log line from
`server/nodes/__init__.py`, i.e. `len(nodes._DISCOVERED)`, which
counts every non-private module and subpackage (`_*.py` helpers and
`_*/` subpackages are skipped by `_discover()`). They are **not**
node-type counts: `len(services.node_registry.NODE_METADATA)` was 148
at the September measurement, spread across 37 group folders under `server/nodes/`. Both are
computed from the tree; neither is hand-maintained here. The dev-mode rows were never benchmarked before (Vite and
uvicorn compete for I/O during a dev boot, so they are not comparable
to the prod numbers), and the first dev boot after a reinstall also
rebuilds Vite's dep cache.

**Build time is a different story.** A fresh `company build` after the
migration measured `bun install` **74 s** and the Vite client build
**124 s** (vs `~16 s` in the table above, recorded 2026-05-06). Neither
is bun's resolver or layout: the same tree rebuilt warm in **32–37 s**
under both the isolated and hoisted linkers, and CI on Linux got faster
(install 10 s → 7 s, full build 45 s → 28 s). The 16 → 35 s warm gap is
codebase growth since May (main bundle 234 → 288 KB gz, 16 chunks). The
rest is Windows Defender first-touch scanning of freshly *written*
package files: pnpm hardlinked from a per-drive store and wrote nothing,
bun copied ~600 MB per install (cache on `C:`, checkout on `D:`; copies
even from a same-drive cache unless `--backend=hardlink`). With the
cache on the checkout's drive and the hardlink backend, purge → install
→ first build measures **15 s + 41 s**. Details and the per-machine
fix in [errors.md §11](errors.md#11-bun-install-copies-every-package--2-minute-first-vite-build-windows).

## Optimisation history

In chronological order. Each row links to the commit and the
corresponding `start.log` measurement.

| Date | Change | Saved | Commit | Plan / RFC |
|---|---|---|---|---|
| 2026-05-04 | Lazy LangChain imports in `services/ai.py` (BaseMessage stays eager; everything else moves into local imports) | **~30 s** AIService cold import | `74b75b6` | inline plan |
| 2026-05-05 | `tsgo` for client `--noEmit` typecheck | ~6 s in CI gate | `0b45fb1` | [release_build_pipeline.md](release_build_pipeline.md) |
| 2026-05-05 | Vite `manualChunks` (split reactflow / radix / lobehub-icons / TanStack Query / markdown stack) + `target: 'es2022'` | main bundle 232 KB gz, 7 vendor chunks separately cached | `0b45fb1` | same |
| 2026-05-05 | Pre-bundle the JS executor sidecar with esbuild (`tsx src/index.ts` → `node dist/index.js`); since superseded by `bun build --target=bun` → `bun dist/index.js` when bun became the only shipped JS runtime | ~500 ms-1 s of tsx startup per server boot | `0b45fb1` | same |
| 2026-05-05 | Scoped `python -O -m compileall` over project source dirs (excludes `.venv/`, `tests/`) | 3-5 s on warm-disk imports | `0b45fb1` | same |
| 2026-05-05 | Test coverage: 12 build-orchestrator + 32 config-contract tests under `cli/tests/` | n/a (regression guard) | `0f1e55e` | same |
| 2026-05-06 | Frontend WS reconnect → PartySocket; auth bootstrap → TanStack Query; CloudEvents envelope typed | **~20 s** (eliminates +12 s WS drop + +7 s reconnect cycle on cold start) | `e77215c` | inline plan |
| 2026-07-14 | Boot-delay fix set: (1) lazy `"module:Class"` SDK exception refs on `ProviderSpec` (no SDK import at provider registration); (2) `[tool.uv] compile-bytecode = true` + `-O`-less compileall (bytecode compiled at build for the interpreter that actually runs); (3) Vite dep cache preserved across `company dev` boots (`--force` → `VITE_FORCE` → `optimizeDeps.force`) + `optimizeDeps.include` for heavy lazily-reached deps; (4) temporalio build-id hash pre-warmed off-loop via `asyncio.to_thread` | **~50 s** on post-clean cold boot (71 s → 21.5 s); ~7 s on warm boot (AIService import back to baseline); ~2 min of Vite re-optimize per warm dev boot; event loop no longer frozen ~3 s during Temporal worker start | this change | plan file `analyze-the-log-txt-file-eager-locket.md`; log evidence `log.txt` / `cold.txt` (2026-07-14) |
| 2026-07-27 | TypeScript 7 GA (`typescript@7.0.2`, native Go) replaces the frozen `@typescript/native-preview` dev build; gate moves to the repo root | **820 ms vs 4830 ms** on the client `--noEmit` gate | this change | [release_build_pipeline.md](release_build_pipeline.md) |

The 2026-07-27 row is the first **measured** figure for this gate — 3 warm runs of
each compiler, best-of spread under 70 ms. It supersedes the "5×" that sat in the
2026-05-05 row above and the "~10×" in `release_build_pipeline.md`; neither had a
recorded baseline, a benchmark command, or a reproduction. Reproduce with:

```bash
cd client
../node_modules/.bin/tsc --noEmit -p tsconfig.json   # TS 7 (the gate)
./node_modules/.bin/tsc --noEmit                     # TS 5.9.3 (second opinion)
```

## Where launch time is spent today (post `e77215c`, warm cache)

```
T+0.00 — port-free begin
       ├── client / server / temporal processes spawn (0.06 s)
       ├── Vite dev ready (0.90 s) ────── (frontend cheap)
       └── Server import phase
           ├── FastAPI base imports (0.27 s)
           ├── DI container (0.42 s)
           ├── Core service imports (0.36 s)
           ├── AIService import (0.70 s) ← native boundary + lazy history shims
           ├── Routers + plugin walker (0.84 s, 137 plugin modules — May count; see counting rule)
           └── All imports complete (1.98 s)
T+2.25 — Lifespan startup
       ├── DB + cache (0.04 s)
       ├── Credentials + encryption (0.10 s)
       ├── Compaction service (0.12 s)
       └── All services initialized (2.63 s)
T+2.90 — Application startup complete ◀ HTTP-ready point
T+3.17 — `ready on port $PYTHON_BACKEND_PORT` (uvicorn accepting)
T+3.85 — Temporal worker started (background)
        ├── ... (auth state propagates to FE; React mounts; queries fire)
T+8.29 — First WebSocket client connected ◀ UI-interactive point
       ├── Stripe daemon spawned + clean exit (0)
       ├── Telegram bot validated + polling started
       └── WhatsApp RPC connected (10.93 s)
T+12.27 — Status broadcasters settled ◀ "everything green"
```

## Cold post-clean boot (2026-07-14, `cold.txt` 18:44, post-fix)

First launch after `company clean` → `company build` (fresh `.venv`,
fresh DATA_DIR: new salt + example import). Segment deltas vs the
same-day pre-fix cold boot in parentheses:

```
T+0.00 — port-free begin
T+2.6  — container: core imports done          (was 10.2 s → 1.2 s segment)
T+3.2  — AIService imported, 13 providers in 17 ms  (was 44.6 s segment)
T+8.7  — 152 node plugins loaded               (was 10.8 s → 4.7 s segment)
T+11.9 — Lifespan startup begin                (3.2 s gap: CLI-agent MCP mount)
T+21.5 — Application startup complete          (lifespan 9.6 s: fresh-DB init 4.4 s,
                                                salt/PBKDF2 + encryption ~3 s — see follow-ups)
T+22.2 — ready on port $PYTHON_BACKEND_PORT    (pre-fix: probe timed out at 30 s,
                                                line never printed)
T+27    — Temporal worker registered; worker_start span 7.6 s wall but OFF-LOOP:
          broadcaster refreshes + WhatsApp RPC handshake interleave mid-hash
          (pre-fix: 3.1 s synchronous loop freeze)
T+44    — browser connects once, full init burst answered in <0.6 s
          (pre-fix: insta-disconnect + reload churn + 8 s starved gap;
           ~40 s here is post-clean one-time Vite dep optimize + human open)
T+51    — example workflows imported (still inline — see follow-ups)
```

## The remaining +5 s gap (HTTP-ready → first WS connect)

`ready on port $PYTHON_BACKEND_PORT` at +3.17 s, first WS connect at +8.29 s. Backend
is idle in this window; the cost lives on the frontend. Likely
contributors:

1. **TanStack Query auth-bootstrap retry budget** ([client/src/contexts/AuthContext.tsx](../client/src/contexts/AuthContext.tsx) + [client/src/lib/connectionConfig.ts](../client/src/lib/connectionConfig.ts)). The `AUTH_RETRY` envelope (BASE 50 ms, CAP 4000 ms, MAX_ATTEMPTS 7) covers the typical 4 s backend cold-start window in 4-5 attempts. If the backend finishes mid-retry-cycle, the next jittered draw can land 1-3 s after readiness.
2. **React Strict Mode dual-mount** in dev. The 100 ms `setTimeout` guard in the connect effect at [WebSocketContext.tsx:3558](../client/src/contexts/WebSocketContext.tsx#L3558) absorbs the bulk; remaining cost is React reconciliation + babel-plugin-react-compiler overhead on first render.
3. **PartySocket upgrade handshake**. Sub-100 ms in normal cases; would only matter on slow networks.

To attribute definitively: add `console.time('auth.queryFn')` / `console.timeLog('auth.queryFn')` markers and a corresponding pair around `connect()`. Not blocking — the contract this layer was meant to fix (the +12 s disconnect-reconnect cycle) is resolved.

## Frontend retry / reconnect envelope

Single source of truth: [client/src/lib/connectionConfig.ts](../client/src/lib/connectionConfig.ts).

| Constant | Value | Notes |
|---|---|---|
| `AUTH_RETRY.BASE_MS` | 50 ms | Full-jitter base; first failure waits up to 50 ms vs. 1 s previously |
| `AUTH_RETRY.CAP_MS` | 4000 ms | Cap on per-retry delay |
| `AUTH_RETRY.MAX_ATTEMPTS` | 7 | Cumulative upper bound ~10 s (vs. 31 s on the old recursive `setTimeout`) |
| `WS_RECONNECT.MIN_DELAY_MS` | 250 ms | First reconnect attempt |
| `WS_RECONNECT.MAX_DELAY_MS` | 8000 ms | Cap on any single reconnect delay |
| `WS_RECONNECT.GROW_FACTOR` | 1.3 | Multiplier per attempt |
| `WS_RECONNECT.MAX_ENQUEUED_MESSAGES` | 200 | PartySocket send-while-disconnected buffer |
| `WS_CLOSE.NORMAL_CLOSURE` | 1000 | RFC 6455 §7.4.1; PartySocket skips reconnect for this code |

Backoff formula (AWS Architecture Blog "full jitter" pattern):

    sleep = random(0, min(CAP_MS, BASE_MS * 2^attempt))

Lock-in tests: [client/src/lib/__tests__/connectionConfig.test.ts](../client/src/lib/__tests__/connectionConfig.test.ts).

## Bottleneck inventory (cold-start, warm cache)

Ranked by absolute cost. None are individually large after the
optimisations above; sum is what hurts.

| # | Bottleneck | Cost | Class | Notes |
|---|---|---|---|---|
| 1 | Plugin walker at import time (152 modules under `server/nodes/` at the July measurement, 185 today — see the counting rule above; ~2 s warm / ~4.7 s cold, dominated by `nodes/google`'s eager `googleapiclient` import) | ~2 s | Backend | `_HANDLER_REGISTRY` populates via `BaseNode.__init_subclass__` at import. Lazy `googleapiclient` is the cheap win (see follow-ups); full lazy-loading of the walker is the big-blast-radius option. |
| 2 | TanStack Query auth bootstrap retry window | ~3-5 s | Frontend | See "remaining +5 s gap" above. |
| 3 | AIService/native orchestration import graph | ~0.7 s | Backend | Historical May baseline; new agent execution uses native `Message` / `AgentToolSpec` values and provider registration remains lazy. Re-measure before attributing this cost further. |
| 4 | Status-broadcaster refresh (`refresh_all_services`, 2.6 s) | ~2.6 s | Backend | Runs after `Application startup complete`, doesn't block server-ready. WhatsApp + Telegram are the long tails. |
| 5 | Process spawn + Python interpreter init | ~0.4 s | Platform | Unavoidable without compiling Python to a binary (Nuitka / PyOxidizer — explicitly out of scope). |

## Pre-compile Python bytecode

Two halves, both required — measured on a post-`company clean` cold
boot (2026-07-14, `cold.txt`): without them the boot hit **71 s** to
"Application startup complete" (5.2× the warm boot, 24× the 2.90 s
baseline), not the "~2×" this doc previously claimed.

**1. Site-packages** — `server/pyproject.toml` sets
`[tool.uv] compile-bytecode = true` so `uv sync` compiles the ~9,700
dependency `.py` files at install time. uv's default is **false**;
without it every dependency compiles lazily on the FIRST import after
a fresh sync — tens of seconds on Windows where Defender also
first-touch-scans each newly written file.

**2. Project source** — the build-pipeline step (excludes `.venv/`,
`tests/`):

```bash
cd server
uv run python -m compileall -q -j 0 services core nodes routers models middleware main.py constants.py
```

No `-O`: every runtime launches python without `-O`, and per PEP 488 a
non-optimized interpreter only loads plain `.pyc` — the previous `-O`
invocation produced `.opt-1.pyc` files that nothing ever loaded.

`company build` runs this as step `[5/6]`. The path list lives in
[cli/commands/build.py](../cli/commands/build.py)'s
`COMPILEALL_SOURCE_DIRS` constant; install.js mirrors the same list.
Tests at
[cli/tests/test_build_compile_pipeline.py](../cli/tests/test_build_compile_pipeline.py)
and
[cli/tests/test_release_pipeline_config.py](../cli/tests/test_release_pipeline_config.py)
lock the contract (including the `compile-bytecode = true` setting).

## How to reproduce a measurement

### Cold-start timeline

```bash
# Warm path (typical dev iteration):
company start > start.log 2>&1
# In another shell, wait for "Status broadcasters settled" then Ctrl-C the first.
```

Then extract phase markers:

```bash
grep -E "Freeing ports|Importing FastAPI|AIService imported|All imports complete|Lifespan startup begin|Application startup complete|ready on port ${PYTHON_BACKEND_PORT}|StatusBroadcaster\] Client connected|broadcaster.refresh_all_services" start.log
```

### Bundle size + chunk shape

```bash
cd client
ANALYZE=1 bun run vite build
# open client/dist/stats.html in a browser → treemap with gzip sizes
```

The `850 KB` chunk-size warning ceiling lives in
[client/vite.config.js](../client/vite.config.js). `vendor-icons` is
~830 KB (lucide + lobehub brand SVGs); the limit is set just above so
real regressions in other chunks fire while the icons baseline doesn't.

### Frontend retry envelope sanity

```bash
cd client
bun run vitest run src/lib/__tests__/connectionConfig.test.ts
```

10 tests, locks RFC 6455 close code, AUTH_RETRY envelope, full-jitter
formula bound check across 100 × MAX_ATTEMPTS draws.

## Anti-patterns we've removed (don't reintroduce)

These were observed and fixed; the lessons are durable.

- **Eager `from langchain_openai import ChatOpenAI` at module top.** Cost ~21 s cold on Windows because it transitively pulls openai SDK + tiktoken + httpx wrappers. With `from __future__ import annotations` already in place, all type hints become strings; no eager import was structurally required. Lazy via per-function local imports + a small `BaseMessage`-only eager hold-out.
- **Eager SDK import at LLM provider registration, just to reference typed exception classes.** The `services/llm/providers/*` registration blocks did `import anthropic` / `import openai` / `from google.genai import errors` at module bottom solely to populate `ProviderSpec.sdk_exception_types` — re-creating the anti-pattern above through the raw SDKs (~7.6 s warm / ~45 s cold for the AIService import vs the 703 ms baseline; google.genai alone was ~4 s warm / ~15 s cold). Fixed with lazy `"module:ClassName"` refs (`ProviderSpec.sdk_exception_refs`) resolved via `pkgutil.resolve_name` at except/read time — by then the provider factory has already imported the SDK, so resolution is a `sys.modules` cache hit. Locked by [server/tests/llm/test_lazy_sdk_imports.py](../server/tests/llm/test_lazy_sdk_imports.py) (subprocess purity probe). When adding a provider: pass a string ref, never import the SDK at module level. **The guard is per-provider-layer, and every new one needs its own copy** — the probe runs in a clean interpreter because the pytest process already has SDKs loaded by other tests, so an in-process assertion would pass vacuously. `nodes/speech/` ships its equivalent in [tests/nodes/test_speech.py](../server/tests/nodes/test_speech.py) (`TestLazySdkImports`), asserting that importing the whole speech plugin leaves `openai` / `anthropic` / `google.genai` out of `sys.modules`.
- **Unconditional `client/node_modules/.vite` wipe on every `company dev`.** Forced a full esbuild dependency re-optimization (1-2 minutes on Windows) on every first page load. Vite self-invalidates the dep cache via lockfile/config/NODE_ENV hashes in `.vite/deps/_metadata.json`; the wipe was pure waste on a stable lockfile. Replaced with `company dev --force` → `VITE_FORCE=1` → `optimizeDeps.force` (Vite's own re-bundle mechanism), plus `optimizeDeps.include` for the heavy lazily-reached deps so late discovery can't trigger the mid-session re-optimization behind the "Outdated Optimize Dep" 504 (vitejs/vite#14284).
- **Synchronous temporalio `Worker()` construction on the event loop.** The constructor derives a default build id by MD5-hashing the bytecode of every module in `sys.modules` (disk reads included) — ~3.1 s at our module count, freezing the loop and inflating concurrent boot work (`broadcaster.refresh_whatsapp` measured 4.2 s vs its ~0.4 s siblings). The value is memoized SDK-globally, so `TemporalWorkerManager.start()` pre-warms it once via `asyncio.to_thread(load_default_build_id)` before constructing the manager worker; all pool workers then construct cheaply. Any new long synchronous call in an async startup path should get the same `to_thread` treatment.
- **Recursive `setTimeout` retry chain in `useEffect` without `AbortController`.** Survived unmount, leaked timers, called `setState` on stale closures. The React docs explicitly flag this in [https://react.dev/reference/react/useEffect](https://react.dev/reference/react/useEffect). Replaced with TanStack Query's `signal`-aware `queryFn`.
- **Flat `setTimeout(connect, 3000)` reconnect loop.** No exponential backoff, no jitter, no `code === 1000` honouring, no message replay. Replaced with PartySocket — see [client/src/contexts/WebSocketContext.tsx](../client/src/contexts/WebSocketContext.tsx).
- **`if (event.code !== 1000)` magic numbers** scattered through the WS lifecycle. Replaced with `WS_CLOSE.NORMAL_CLOSURE` from [connectionConfig.ts](../client/src/lib/connectionConfig.ts) per RFC 6455 §7.4.1.
- **Inline `chunkSizeWarningLimit: 1500`** silently masking bundle bloat. Lowered to 850 KB so future regressions surface at `vite build` time.

- **Setting `PYTHONPYCACHEPREFIX` without pre-warming it.** With a prefix set, CPython ignores every in-tree `__pycache__` — site-packages included — so the first boot recompiles ~20k files (measured 32 s vs 2.9 s warm). The desktop shell needs the prefix because its bundle is read-only, and pays the cost once during provisioning with `python -m compileall -q -j 0 <venv> <server>` under the same prefix (`desktop/src/main/provision.ts`). The CLI path does not set a prefix and keeps uv's `compile-bytecode = true` output in-tree.

## Open follow-ups

Tracked but explicitly **not** in any active plan.

| Item | Estimated saving | Notes |
|---|---|---|
| Lazy `googleapiclient` import in `nodes/google` (`_option_loaders.py` / `_oauth.py` / `_base.py` import `googleapiclient.discovery` at module top) | ~2-4 s of the cold plugin walk | googleapiclient is 98 MB / 612 files on disk; now the single biggest import cost left on the cold boot path. Move `from googleapiclient.discovery import build` into function bodies (same idiom as `services/plugin/credential.py`). `nodes/telegram/_service.py`'s top-level `telegram` imports are the smaller sibling. |
| Defer first-launch example import off the workflows REST request | ~8-10 s of first-launch first paint | `routers/database.py:get_all_workflows` awaits `import_examples_for_user` inline, holding the HTTP response. Move to a lifespan background task (pattern: `_refresh_registry` / `_refresh_all_services` / Temporal init in `main.py`) + emit `workflow_lifecycle("imported")` per example so the sidebar refreshes. Secondary: negative cache in `AuthService.has_valid_key` (validation does one credentials-DB read per declared credential per node). |
| Cold-boot lifespan I/O (9.6 s measured 2026-07-14 vs 1.5 s pre-fix cold boot) | unclear — needs a re-run | Fresh-DB creation 4.4 s + salt/PBKDF2 + encryption ~3 s under Defender/disk contention now that the whole boot compresses into ~20 s. May be noise; measure before optimising. |
| Plugin walker lazy-loading | ~0.5-0.8 s on server-ready | Would need to defer registration until first NodeSpec request rather than at module-import time. Touches every `BaseNode` subclass — biggest blast radius of the candidates. |
| Retire legacy Temporal history adapters after the rollback / retention window | negligible startup impact | New executions already use native messages and the compatibility imports are lazy. This is dependency and maintenance cleanup, not a measured cold-start win. |
| `+5 s` HTTP-ready → first-WS-connect gap | up to 5 s | Diagnostics needed (see "remaining +5 s gap"). May reveal nothing actionable. |
| Supervisor backend `ready_timeout` (default 30 s, shortest of the three services) | cosmetic | The probe is inert (one-shot, no restart/gating) but a >30 s boot prints an alarming "timed out waiting for port $PYTHON_BACKEND_PORT" and skips the ready line. Post-fix boots fit the window; revisit only if cold boots regress past 30 s. |
| Standalone Nuitka / PyOxidizer release binary | full Python interpreter init (~0.4 s) + `.pyc` regeneration on cold disk | User explicitly declined when scoping the build pipeline; revisit if "ship a single binary" becomes a product requirement. |

## References

- AWS Architecture Blog, ["Exponential Backoff and Jitter"](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) — full-jitter formula used by `AUTH_RETRY`.
- RFC 6455 §7.4.1 — WebSocket close codes ([https://datatracker.ietf.org/doc/html/rfc6455#section-7.4.1](https://datatracker.ietf.org/doc/html/rfc6455#section-7.4.1)).
- TanStack Query v5 retry guide — [https://tanstack.com/query/v5/docs/framework/react/guides/query-retries](https://tanstack.com/query/v5/docs/framework/react/guides/query-retries).
- PartySocket API — [https://docs.partykit.io/reference/partysocket-api/](https://docs.partykit.io/reference/partysocket-api/).
- React `useEffect` cleanup pattern — [https://react.dev/reference/react/useEffect](https://react.dev/reference/react/useEffect).
- CloudEvents v1.0 spec — [https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md). Mirrored in [client/src/types/cloudEvents.ts](../client/src/types/cloudEvents.ts) and [server/services/events/envelope.py](../server/services/events/envelope.py).
- Companion docs: [release_build_pipeline.md](release_build_pipeline.md) for the build-time wins, [frontend_architecture.md](frontend_architecture.md) for the cache + slice-subscription model that bounds runtime latency.
