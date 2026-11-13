# Performance — Cold-Start, Bundle, Runtime Latency

The dated measurements below are historical local observations, not a fresh
benchmark of this checkout or a startup/latency guarantee. Current source
and tests substantiate the described mechanisms; remeasure on the target
machine before using the numbers for a performance decision.

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
[Browser workspace](./browser_workspace.md#historical-local-measurements-2026-09-26) for
complete results, source hashes, limitations and repeatable commands.

Use `OPENCOMPANY_BROWSER_DIAGNOSTICS=1` for server timing summaries and
`?browserStreamDebug=1` for numeric client decode/draw summaries. No screenshot
or input payloads are logged by these diagnostics.

## Headline numbers (current baseline)

Measured 2026-11-11 on Windows 11 at commit `ac88bd98` (v0.3.1). Method: a
clean worktree, a fresh `company build`, then real `company start` and
`company dev` launches on their own ports against an empty `DATA_DIR` (no
connected accounts, no saved workflows; only the package cache that
`company clean` keeps was seeded). Times are seconds after the CLI's
"Freeing ports..." line. A headless Chrome opened the app the moment its
port answered, so "first WebSocket client" does not include a person
reaching for the browser. Warm figures are the median of ten launches with
the fastest and slowest in brackets; the first launch ran once. Each launch
was ended by killing its process tree, not with Ctrl-C.

| Metric | Warm `company start` | First launch after the build |
|---|---|---|
| Application startup complete | **4.5 s** (3.9 to 7.2) | **9.8 s** |
| HTTP `ready on port $PYTHON_BACKEND_PORT` | **4.7 s** (4.1 to 7.3) | 9.9 s |
| First WebSocket client connected | **5.6 s** (4.8 to 8.3) | 11.0 s |
| Temporal worker pool started (execution engine ready) | **9.6 s** (7.9 to 13.9) | 15.9 s |
| Routers + plugin walker segment (196 modules) | 1.7 s (1.4 to 3.5) | 5.4 s |
| Lifespan startup | 0.4 s (0.35 to 0.7) | 1.5 s |
| AIService import | 0.11 s | 0.15 s |
| Status refresh (`broadcaster.refresh_all_services`, no accounts connected) | 0.15 s | not measured |

`company dev`, same day and worktree (three launches, the browser opened at
backend startup complete):

| Metric | Warm (launches 2 and 3) | First dev launch (empty Vite dep cache) |
|---|---|---|
| Vite `ready in` | 0.4 and 0.6 s | 1.3 s |
| Backend Application startup complete | 4.3 and 5.3 s | 9.8 s |
| First WebSocket client connected | 10.1 and 12.0 s | 30.9 s |

| Build and bundle | Value |
|---|---|
| `company build`, fresh worktree, bun and uv caches warm | **98 s** |
| Vite production build inside it | **32 s** |
| Entry bundle `client/dist/assets/index-*.js` | **128 KB gz** (401 KB raw), one of 47 JS chunks |

**The spread is the machine, not the code.** The three slowest warm launches
(5.5, 6.7 and 7.2 s) lost their time in one segment, routers + plugin walker
(2.4 to 3.5 s against 1.4 to 1.8 s in the other seven). That segment is
thousands of small file opens, and other work was running on the machine
during the session. Read 4 to 4.5 s as the warm figure for this machine and
the higher values as what a busy machine adds.

**Against September** (~3.6 s startup complete, 4.56 s port ready, single
launches): the fastest launches (3.9 s, port ready 4.1 s) are within 0.3 s
of it and the median is about 0.9 s slower. The walker loads 196 modules
now against 185.

**The first launch is 9.8 s where July measured 21.5 s**, but the two are not
the same experiment: this build linked its packages from warm bun and uv
caches instead of downloading them, and the cache state of the July run was
not recorded. The 5.3 s a first launch costs over a warm one is 3.7 s in the
plugin walk (the plugin dependencies are read from disk for the first time),
0.5 s mounting the CLI-agent MCP server and 1.1 s of lifespan (new databases
0.6 s, new salt and encryption 0.5 s). Everything before "Importing routers"
takes the same 2.1 s either way.

**`company dev` reaches the first socket later than `company start`** although
its backend is ready just as fast: Vite serves the app as separate source
modules and transforms each on request after every dev-server start (5.8 to
6.7 s here), and on the first dev launch it also pre-bundles the dependencies
(21 s in all). That launch's backend also took 9.8 s to start, 7.1 s of it
in the plugin walk, although its imports were already warm; most likely the
pre-bundle was competing for the disk at the same moment.

### Earlier baseline (May and July 2026)

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
(137 in May, 152 in July, 185 in September and 196 in November 2026) are the walker's
*module* count — the `node plugins loaded: N modules` log line from
`server/nodes/__init__.py`, i.e. `len(nodes._DISCOVERED)`, which
counts every non-private module and subpackage (`_*.py` helpers and
`_*/` subpackages are skipped by `_discover()`). They are **not**
node-type counts: `len(services.node_registry.NODE_METADATA)` was 148
at the September measurement, spread across 37 group folders under `server/nodes/`, and 157
across 41 folders on 2026-11-11. Both are
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

**Re-measured 2026-11-11** (`ac88bd98`, fresh worktree, bun and uv caches
warm): `company build` took **98 s** end to end.

| Stage | Time | Detail from the log |
|---|---|---|
| Install dependencies | 6.4 s | `1036 packages installed [6.38s]` |
| Build client | 32.7 s | Vite `built in 31.93s` |
| Build JS executor sidecar | 3.1 s | |
| Build Pi Coding Agent runtime | 22.3 s | `124 packages installed [10.59s]` |
| Install Python dependencies | 16.5 s | `Installed 164 packages in 2.46s`, `Bytecode compiled 10107 files in 8.88s` |
| Compile project bytecode | 15.6 s | |
| Install Temporal binaries | 1.7 s | already in the package cache |

The entry bundle is 128 KB gz against 288 KB in September; Home, the editor
and the Workspace now load as their own lazy chunks (`HomeShell` 61 KB gz,
`Dashboard` 97 KB gz, `WorkspaceView` 88 KB gz). The largest chunk is
`vendor-icons` at 612 KB raw (160 KB gz).

## Optimisation history

In chronological order. Each row links to the commit and the
corresponding `start.log` measurement.

| Date | Change | Saved | Commit | Plan / RFC |
|---|---|---|---|---|
| 2026-05-04 | Deferred model-wrapper imports in `services/ai.py` (historical startup optimization) | **~30 s** AIService cold import | `74b75b6` | inline plan |
| 2026-05-05 | `tsgo` for client `--noEmit` typecheck | ~6 s in CI gate | `0b45fb1` | [release_build_pipeline.md](release_build_pipeline.md) |
| 2026-05-05 | Vite `manualChunks` (split reactflow / radix / lobehub-icons / TanStack Query / markdown stack) + `target: 'es2022'` | main bundle 232 KB gz, 7 vendor chunks separately cached | `0b45fb1` | same |
| 2026-05-05 | Pre-bundle the JS executor sidecar with esbuild (`tsx src/index.ts` → `node dist/index.js`); since superseded by `bun build --target=bun` → `bun dist/index.js` when bun became the only shipped JS runtime | ~500 ms-1 s of tsx startup per server boot | `0b45fb1` | same |
| 2026-05-05 | Scoped `python -O -m compileall` over project source dirs (excludes `.venv/`, `tests/`) | 3-5 s on warm-disk imports | `0b45fb1` | same |
| 2026-05-05 | Test coverage: 12 build-orchestrator + 32 config-contract tests under `cli/tests/` | n/a (regression guard) | `0f1e55e` | same |
| 2026-05-06 | Frontend WS reconnect → PartySocket; auth bootstrap → TanStack Query; CloudEvents envelope typed | **~20 s** (eliminates +12 s WS drop + +7 s reconnect cycle on cold start) | `e77215c` | inline plan |
| 2026-07-14 | Boot-delay fix set: (1) lazy `"module:Class"` SDK exception refs on `ProviderSpec` (no SDK import at provider registration); (2) `[tool.uv] compile-bytecode = true` + `-O`-less compileall (bytecode compiled at build for the interpreter that actually runs); (3) Vite dep cache preserved across `company dev` boots (`--force` → `VITE_FORCE` → `optimizeDeps.force`) + `optimizeDeps.include` for heavy lazily-reached deps; (4) temporalio build-id hash pre-warmed off-loop via `asyncio.to_thread` | **~50 s** on post-clean cold boot (71 s → 21.5 s); ~7 s on warm boot (AIService import back to baseline); ~2 min of Vite re-optimize per warm dev boot; event loop no longer frozen ~3 s during Temporal worker start | this change | plan file `analyze-the-log-txt-file-eager-locket.md`; log evidence `log.txt` / `cold.txt` (2026-07-14) |
| 2026-07-27 | TypeScript 7 GA (`typescript@7.0.2`, native Go) replaces the frozen `@typescript/native-preview` dev build; gate moves to the repo root | **820 ms vs 4830 ms** on the client `--noEmit` gate | this change | [release_build_pipeline.md](release_build_pipeline.md) |
| 2026-11-11 | `TemporalWorkerPool.start` builds each queue worker with `asyncio.to_thread` (each costs about 80 ms, mostly four `Settings()` builds) | the 1.0 to 1.8 s event-loop stall at every boot: longest loop gap while the manager and pool start 1.45 s → 0.08 s; no stall logged in three boots after it | `3d771e28` | bottleneck inventory, row 5 |

The 2026-07-27 row is the first **measured** figure for this gate — 3 warm runs of
each compiler, best-of spread under 70 ms. It supersedes the "5×" that sat in the
2026-05-05 row above and the "~10×" in `release_build_pipeline.md`; neither had a
recorded baseline, a benchmark command, or a reproduction. Reproduce with:

```bash
cd client
../node_modules/.bin/tsc --noEmit -p tsconfig.json   # TS 7 (the gate)
./node_modules/.bin/tsc --noEmit                     # TS 5.9.3 (second opinion)
```

## Where launch time is spent today (2026-11-11, warm `company start`)

Each line is the median of that marker over the ten warm launches, so
neighbouring lines do not subtract exactly.

```
T+0.00 — "Freeing ports..." (the CLI has already spent 0.7-1.0 s on its own
         start, the build check and the SQLAlchemy preflight)
T+0.03 — server process spawned
T+0.27 — Importing FastAPI               (interpreter start 0.24 s)
T+0.51 — Importing settings + logging    (FastAPI 0.24 s)
T+0.73 — Importing DI container          (settings, logging, tracing 0.22 s)
       ├── core imports done (1.35)                       0.62 s
       ├── AIService imported (1.46)                      0.11 s
       ├── all service imports done, LLM registry (1.66)  0.20 s
       └── Temporal client import + container definition  0.46 s
T+2.12 — Importing routers
       └── 196 plugin modules loaded (3.75)               1.6 s
T+3.77 — All imports complete
T+4.06 — CLI-agent MCP server mounted    (app, CORS, routers 0.29 s)
T+4.08 — Lifespan startup begin
       ├── Database + cache (4.22)                        0.14 s
       ├── Credentials + encryption (4.35)                0.13 s
       └── model registry, teams, proxy, MCP session (4.47)  0.12 s
T+4.47 — Application startup complete ◀ HTTP-ready point
T+4.66 — `ready on port $PYTHON_BACKEND_PORT` (uvicorn accepting; browser opened here)
T+5.64 — First WebSocket client connected ◀ UI-interactive point
T+5.93 — Temporal dev server spawned and connected (background)
T+8.56 — Temporal activities registered  (build-id hash 2.6 s, off the loop)
T+9.56 — Temporal worker pool started, 9 queues ◀ execution engine ready
T+9.81 — "The event loop is blocked" logged once (1.0 to 1.6 s; gone since 3d771e28, see the inventory)
```

With no accounts connected the status refresh
(`broadcaster.refresh_all_services`) finishes 0.15 s after it is scheduled,
at about the moment the port is ready; in May, with WhatsApp and Telegram
connected, it was the last thing to settle at 12.27 s.

### May 2026 timeline (post `e77215c`, dev mode, warm cache)

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

## First launch after a fresh build (2026-11-11)

One `company start`, 17 s after `company build` finished in a new worktree:
fresh `.venv`, empty `DATA_DIR` (new databases and salt), packages linked
from warm bun and uv caches.

```
T+0.00 — "Freeing ports..."
T+0.25 — Importing FastAPI
T+1.29 — container: core imports done
T+1.44 — AIService imported
T+2.05 — Importing routers               (same as a warm launch up to here)
T+7.41 — 196 plugin modules loaded       (5.4 s; 1.6 s warm)
T+8.25 — CLI-agent MCP server mounted    (0.8 s; 0.3 s warm)
T+8.27 — Lifespan startup begin
T+8.90 — Database + cache started        (0.63 s: new databases)
T+9.36 — Credentials + encryption        (0.46 s: new salt, PBKDF2)
T+9.79 — Application startup complete    (lifespan 1.5 s; 0.4 s warm)
T+9.87 — ready on port $PYTHON_BACKEND_PORT
T+11.00 — First WebSocket client connected (browser opened at 9.89)
T+11.85 — Temporal dev server spawned and connected
T+14.82 — Temporal activities registered
T+15.88 — Temporal worker pool started, 9 queues
T+16.27 — "The event loop is blocked" logged once (1.46 s; gone since 3d771e28)
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

## HTTP-ready to first WebSocket client

**2026-11-11:** with a browser opened the moment the port answers, the first
WebSocket client connects **0.9 s** later on the production build (median of
ten launches, 0.7 to 1.2 s). That covers Chrome's own start, the page load,
the auth check and the socket handshake. Under `company dev` the same step
takes 5.8 to 6.7 s with a warm Vite dep cache and 21 s on the first dev
launch (see the headline section). Each of the fifteen launches shows one
connect and no disconnect in the 15 s after it, so the +12 s
disconnect-reconnect cycle of early May has not come back.

The May figure (`ready on port $PYTHON_BACKEND_PORT` at +3.17 s, first WS
connect at +8.29 s, "the remaining +5 s gap") was taken in dev mode with a
person opening the browser, so it measured Vite and the person; the backend
is idle in this window. What can still add seconds for a real user:

1. **The sign-in gate's visible retry schedule** ([client/src/components/auth/ProtectedRoute.tsx](../client/src/components/auth/ProtectedRoute.tsx) + [client/src/lib/connectionConfig.ts](../client/src/lib/connectionConfig.ts)). The auth check no longer retries on its own: when the backend is not up at the first check, the Connecting screen checks again after 2, 3, 5 and then every 8 s (`CONNECT_RETRY`) and holds "Connected" 1.5 s before sign-in or the app. A backend that becomes ready just after a check waits for the next one. (The May measurement predates this; it used a 50 ms full-jitter backoff. The November launches opened the browser after the port answered, so they never showed Connecting.) The desktop shell loads the SPA only after `/health/ready`, so it never shows Connecting at launch.
2. **React Strict Mode dual-mount** in dev. The 100 ms `connectTimeout` guard in `WebSocketProvider`'s auth-gated connect effect ([WebSocketContext.tsx](../client/src/contexts/WebSocketContext.tsx)) absorbs the bulk; remaining cost is React reconciliation + babel-plugin-react-compiler overhead on first render.
3. **PartySocket upgrade handshake**. Sub-100 ms in normal cases; would only matter on slow networks.

## Frontend retry / reconnect envelope

Single source of truth: [client/src/lib/connectionConfig.ts](../client/src/lib/connectionConfig.ts).

| Constant | Value | Notes |
|---|---|---|
| `CONNECT_RETRY.DELAYS_S` | 2, 3, 5, 8, 8 s | The Connecting screen's waits before each check, the last repeated |
| `CONNECT_RETRY.CONNECTED_HOLD_MS` | 1500 ms | "Connected" shows this long before sign-in or the app |
| `CONNECT_RETRY.HELP_FROM_ATTEMPT` | 3 | "Still nothing? Make sure OpenCompany is running." from here |
| `WS_RECONNECT.MIN_DELAY_MS` | 250 ms | First reconnect attempt |
| `WS_RECONNECT.MAX_DELAY_MS` | 8000 ms | Cap on any single reconnect delay |
| `WS_RECONNECT.GROW_FACTOR` | 1.3 | Multiplier per attempt |
| `WS_RECONNECT.MAX_ENQUEUED_MESSAGES` | 200 | PartySocket send-while-disconnected buffer |
| `WS_CLOSE.NORMAL_CLOSURE` | 1000 | RFC 6455 §7.4.1; explicit disposal stops retries, remote close still reconnects |

PartySocket's reconnect delay (no jitter: it randomises only its own default
minimum, which `MIN_DELAY_MS` replaces):

    delay(n) = min(MAX_DELAY_MS, MIN_DELAY_MS * GROW_FACTOR^(n-1))

Lock-in tests: [connection constants](../client/src/lib/__tests__/connectionConfig.test.ts)
and [WebSocket lifecycle](../client/src/contexts/__tests__/webSocketLifecycle.test.tsx).
The wrapper reconnects after remote close code 1000; only explicit disposal
on logout/unmount stops retries. Already-sent requests reject on close because
their outcome is unknown, while unsent queued requests retain their deadline.

## Bottleneck inventory (2026-11-11)

Ranked by absolute cost, from the warm `company start` launches unless a row
says otherwise. Rows 3, 4, 8 and 9 are on the path to HTTP-ready; with the
0.3 s that builds the app and mounts the CLI-agent MCP server they add up to
the 4.5 s. The rest happen before it, after it or only in dev.

| # | Bottleneck | Cost | Class | Notes |
|---|---|---|---|---|
| 1 | Vite transforming source modules on request (`company dev` only) | 5.8 to 6.7 s from page request to first socket, after every dev-server start; 21 s on the first dev launch | Frontend, dev | `company start` serves the built bundle and takes 0.9 s for the same step. |
| 2 | Temporal engine start, after HTTP-ready | about 5 s (port ready at 4.7 s, worker pool at 9.6 s) | Backend, background | Dev server spawn + connect 1.3 s, build-id hash 2.6 s in a thread, worker construction about 1 s. Not on the path to HTTP-ready. `temporal.worker_start` span: 1.9 s. |
| 3 | Imports before the routers | 1.85 s | Backend | FastAPI 0.24 s, settings + logging + tracing 0.22 s, DI container and services 1.39 s. Warm import profile of `main` (3.3 s in all): `core.container` 1.13 s (`core.database` 0.46 s, `services.temporal` 0.35 s, `services.workflow` 0.15 s, `services.ai` 0.09 s), `fastapi` 0.26 s, `mcp.server.fastmcp` 0.22 s. AIService alone is 0.11 s, down from 0.7 s in May. |
| 4 | Routers + plugin walker (196 modules, see the counting rule above) | 1.7 s warm (1.4 to 3.5), 5.4 s on a first launch | Backend | `_HANDLER_REGISTRY` populates via `BaseNode.__init_subclass__` at import. In the warm profile `nodes` is 1.31 s: 0.41 s in `nodes/__init__.py` itself, outside any plugin import, then `nodes.google._oauth` 0.24 s (`googleapiclient.discovery` 0.10 s) and `nodes.telegram._service` 0.13 s as the largest single plugins. It is also the segment that swings with disk load. |
| 5 | Event loop blocked while the Temporal workers are constructed | 1.0 to 1.8 s once per boot at `ac88bd98`; fixed in `3d771e28` | Backend | The nine queue workers were built back to back on the loop, four to five seconds after HTTP-ready, so requests and broadcasts stalled while the UI was loading; `services/loop_watchdog.py` logged "The event loop is blocked" in every one of the fifteen launches. Each queue worker costs about 80 ms, mostly four `Settings()` builds (about 19 ms each, since the env files are read every time), and `TemporalWorkerPool.start` now builds them with `asyncio.to_thread`. After the change three real boots logged no stall (two before it logged 1.32 and 1.07 s), and the longest loop gap while the manager and pool start fell from 1.45 s to 0.08 s (0.27 s the first time in a process). The main worker, which also hosts the workflows, is still built on the loop. |
| 6 | HTTP-ready → first WebSocket client | 0.9 s (0.7 to 1.2) | Frontend | Chrome start, page load, auth check, socket handshake. See the section above. |
| 7 | CLI before "Freeing ports" | 0.7 to 1.0 s for `company start`, 0.2 to 0.3 s for `company dev` | CLI | Before T+0 in every timeline here. `start` also checks the build and runs the SQLAlchemy preflight. Measured from `python -m cli`; the `company` shim's own start is not included. |
| 8 | Lifespan startup | 0.4 s warm, 1.5 s on a first launch | Backend | Database + cache 0.14 s, credentials + encryption 0.13 s, the rest 0.12 s. A first launch creates the databases (0.6 s) and the salt (0.5 s). |
| 9 | Process spawn + Python interpreter init | 0.27 s | Platform | Unavoidable without compiling Python to a binary (Nuitka / PyOxidizer — explicitly out of scope). |
| 10 | Status-broadcaster refresh (`refresh_all_services`) | 0.15 s with no accounts connected; 2.6 s in May with WhatsApp and Telegram connected | Backend | Runs as a background task scheduled at the end of lifespan startup, so it never blocks server-ready. Connected accounts add their own network round trips. |

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
# TRACING_CONSOLE_SPANS=true prints the span lines (status refresh, worker start);
# without it (or LOG_LEVEL=DEBUG) they are not in the log.
TRACING_CONSOLE_SPANS=true company start > start.log 2>&1
# Open the app the moment "ready on port" prints, or script a headless browser to
# do it, or the first-WebSocket figure measures you. Wait for "Worker pool
# started", then Ctrl-C.
```

Then extract phase markers. Every line carries the supervisor's
`[HH:MM:SS.ffffff]` stamp; subtract the "Freeing ports" one:

```bash
grep -E "Freeing .*ports|Importing FastAPI|core imports done|AIService imported|Importing routers|node plugins loaded|All imports complete|Lifespan startup begin|Application startup complete|ready on port|StatusBroadcaster\] Client connected|broadcaster.refresh_all_services|Worker pool started|event loop is blocked" start.log
```

Launch several times and report the median with the range: on 2026-11-11 ten
warm launches of the same build ran from 3.9 to 7.2 s. The first launch after
a build is its own figure (packages and databases are read or created for the
first time), and so is the first `company dev` launch after an install (Vite
pre-bundles the dependencies).

To measure without touching a running install, use a separate worktree and
give the launch its own data folder and ports through the process
environment, which wins over `.env`: `DATA_DIR`, `VITE_CLIENT_PORT`,
`PYTHON_BACKEND_PORT`, `PORT`, `TEMPORAL_SERVER_ADDRESS`,
`TEMPORAL_FRONTEND_GRPC_PORT`, `TEMPORAL_UI_PORT`, `TEMPORAL_HTTP_PORT`,
`TEMPORAL_METRICS_PORT`, `NODEJS_EXECUTOR_PORT`, `WHATSAPP_RPC_PORT` and
`CORS_ORIGINS`. The CLI frees its configured ports before it starts anything,
so a second instance on the default ports would stop the first.

### Import profile

```bash
cd server
DATA_DIR=/some/scratch/folder uv run python -X importtime -c "import main" 2> importtime.txt
sort -t'|' -k2 -n importtime.txt | tail -40   # costliest imports, cumulative microseconds
```

Cumulative times nest: a package's figure includes everything it imported
first.

### Bundle size + chunk shape

```bash
cd client
ANALYZE=1 bun run vite build
# open client/dist/stats.html in a browser → treemap with gzip sizes
```

The `850 KB` chunk-size warning ceiling lives in
[client/vite.config.js](../client/vite.config.js). `vendor-icons` (all of
lucide-react) is about 630 KB and `vendor-brand-icons` (lobehub) about
310 KB; the limit sits above both so real regressions in other chunks fire
while the icons baseline doesn't.

### Frontend retry envelope sanity

```bash
cd client
bun run vitest run src/lib/__tests__/connectionConfig.test.ts
```

Locks the RFC 6455 close code, the Connecting schedule (`CONNECT_RETRY`) and
the reconnect envelope.

## Anti-patterns we've removed (don't reintroduce)

These were observed and fixed; the lessons are durable.

- **Eager model-wrapper imports at module scope (historical).** A provider wrapper once added ~21 s to cold Windows startup by pulling in SDK and tokenizer dependencies. Current providers defer SDK imports until needed; retain that boundary when adding providers.
- **Eager SDK import at LLM provider registration, just to reference typed exception classes.** The `services/llm/providers/*` registration blocks did `import anthropic` / `import openai` / `from google.genai import errors` at module bottom solely to populate `ProviderSpec.sdk_exception_types` — re-creating the anti-pattern above through the raw SDKs (~7.6 s warm / ~45 s cold for the AIService import vs the 703 ms baseline; google.genai alone was ~4 s warm / ~15 s cold). Fixed with lazy `"module:ClassName"` refs (`ProviderSpec.sdk_exception_refs`) resolved via `pkgutil.resolve_name` at except/read time — by then the provider factory has already imported the SDK, so resolution is a `sys.modules` cache hit. Locked by [server/tests/llm/test_lazy_sdk_imports.py](../server/tests/llm/test_lazy_sdk_imports.py) (subprocess purity probe). When adding a provider: pass a string ref, never import the SDK at module level. **The guard is per-provider-layer, and every new one needs its own copy** — the probe runs in a clean interpreter because the pytest process already has SDKs loaded by other tests, so an in-process assertion would pass vacuously. `nodes/speech/` ships its equivalent in [tests/nodes/test_speech.py](../server/tests/nodes/test_speech.py) (`TestLazySdkImports`), asserting that importing the whole speech plugin leaves `openai` / `anthropic` / `google.genai` out of `sys.modules`.
- **Unconditional `client/node_modules/.vite` wipe on every `company dev`.** Forced a full esbuild dependency re-optimization (1-2 minutes on Windows) on every first page load. Vite self-invalidates the dep cache via lockfile/config/NODE_ENV hashes in `.vite/deps/_metadata.json`; the wipe was pure waste on a stable lockfile. Replaced with `company dev --force` → `VITE_FORCE=1` → `optimizeDeps.force` (Vite's own re-bundle mechanism), plus `optimizeDeps.include` for the heavy lazily-reached deps so late discovery can't trigger the mid-session re-optimization behind the "Outdated Optimize Dep" 504 (vitejs/vite#14284).
- **Synchronous temporalio `Worker()` construction on the event loop.** The constructor derives a default build id by MD5-hashing the bytecode of every module in `sys.modules` (disk reads included) — ~3.1 s at our module count, freezing the loop and inflating concurrent boot work (`broadcaster.refresh_whatsapp` measured 4.2 s vs its ~0.4 s siblings). The value is memoized SDK-globally, so `TemporalWorkerManager.start()` pre-warms it once via `asyncio.to_thread(load_default_build_id)` before constructing the manager worker. The pool workers then skip the hash, but each still costs about 80 ms (four `Settings()` builds), and nine of them back to back stalled the loop for over a second at every boot until `TemporalWorkerPool.start` built them with `asyncio.to_thread` too (`3d771e28`, 2026-11-11). Any new long synchronous call in an async startup path should get the same `to_thread` treatment.
- **Recursive `setTimeout` retry chain in `useEffect` without `AbortController`.** Survived unmount, leaked timers, called `setState` on stale closures. The React docs explicitly flag this in [https://react.dev/reference/react/useEffect](https://react.dev/reference/react/useEffect). Replaced with TanStack Query's `signal`-aware `queryFn`; the retries themselves are now the sign-in gate's visible schedule (`components/auth/useRetrySchedule.ts`).
- **Flat `setTimeout(connect, 3000)` reconnect loop.** No exponential backoff or lifecycle-owned disposal. The current wrapper reconnects after remote code 1000 and preserves unsent requests within their deadlines; explicit disposal stops retries, and already-sent requests are not replayed. Replaced with PartySocket — see [client/src/contexts/WebSocketContext.tsx](../client/src/contexts/WebSocketContext.tsx).
- **`if (event.code !== 1000)` magic numbers** scattered through the WS lifecycle. Replaced with `WS_CLOSE.NORMAL_CLOSURE` from [connectionConfig.ts](../client/src/lib/connectionConfig.ts) per RFC 6455 §7.4.1.
- **Inline `chunkSizeWarningLimit: 1500`** silently masking bundle bloat. Lowered to 850 KB so future regressions surface at `vite build` time.

- **Setting `PYTHONPYCACHEPREFIX` without pre-warming it.** With a prefix set, CPython ignores every in-tree `__pycache__` — site-packages included — so the first boot recompiles ~20k files (measured 32 s vs 2.9 s warm). The desktop shell needs the prefix because its bundle is read-only, and pays the cost once during provisioning with `python -m compileall -q -j 0 <venv> <server>` under the same prefix (`desktop/src/main/provision.ts`). The CLI path does not set a prefix and keeps uv's `compile-bytecode = true` output in-tree.

## Open follow-ups

Tracked but explicitly **not** in any active plan.

| Item | Estimated saving | Notes |
|---|---|---|
| Build the Temporal workers off the event loop | none — done 2026-11-11 (`3d771e28`) | The queue workers are built in threads; see row 5 of the bottleneck inventory. The main worker, which also hosts the workflows, stays on the loop. Closed. |
| Lazy `googleapiclient` import in `nodes/google` (`_option_loaders.py` / `_oauth.py` / `_base.py` import `googleapiclient.discovery` at module top) | 0.1 s warm (2026-11-11 profile: `googleapiclient.discovery` 0.10 s inside a 0.24 s `nodes.google._oauth` import); its share of the 5.4 s first-launch walk was not profiled. The July estimate was ~2-4 s of the cold walk | googleapiclient is 98 MB / 612 files on disk. Move `from googleapiclient.discovery import build` into function bodies (same idiom as `services/plugin/credential.py`). `nodes/telegram/_service.py`'s top-level `telegram` imports are the smaller sibling (0.13 s warm). |
| Defer first-launch example import off the workflows REST request | ~8-10 s of first-launch first paint | `routers/database.py:get_all_workflows` awaits `import_examples_for_user` inline, holding the HTTP response. Move to a lifespan background task (pattern: `_refresh_registry` / `_refresh_all_services` / Temporal init in `main.py`) + emit `workflow_lifecycle("imported")` per example so the sidebar refreshes. Secondary: negative cache in `AuthService.has_valid_key` (validation does one credentials-DB read per declared credential per node). |
| Cold-boot lifespan I/O (9.6 s measured 2026-07-14 vs 1.5 s pre-fix cold boot) | none — re-run 2026-11-11 | The first launch's lifespan took 1.5 s (new databases 0.6 s, salt + encryption 0.5 s). The 9.6 s did not reproduce; nothing to optimise. |
| Plugin walker lazy-loading | up to 1.3 s warm and about 5 s on a first launch (2026-11-11) | Would need to defer registration until first NodeSpec request rather than at module-import time. Touches every `BaseNode` subclass — biggest blast radius of the candidates. |
| Retire legacy Temporal history adapters after the rollback / retention window | negligible startup impact | New executions already use native messages and the compatibility imports are lazy. This is dependency and maintenance cleanup, not a measured cold-start win. |
| `+5 s` HTTP-ready → first-WS-connect gap | none — measured 2026-11-11 | 0.9 s on the production build when the browser opens as the port answers (see "HTTP-ready to first WebSocket client"). The seconds seen in dev are Vite's on-request transforms. Closed. |
| Supervisor backend `ready_timeout` (default 30 s, shortest of the three services) | cosmetic | The probe is inert (one-shot, no restart/gating) but a >30 s boot prints an alarming "timed out waiting for port $PYTHON_BACKEND_PORT" and skips the ready line. Post-fix boots fit the window; revisit only if cold boots regress past 30 s. |
| Standalone Nuitka / PyOxidizer release binary | full Python interpreter init (~0.4 s) + `.pyc` regeneration on cold disk | User explicitly declined when scoping the build pipeline; revisit if "ship a single binary" becomes a product requirement. |

## References

- RFC 6455 §7.4.1 — WebSocket close codes ([https://datatracker.ietf.org/doc/html/rfc6455#section-7.4.1](https://datatracker.ietf.org/doc/html/rfc6455#section-7.4.1)).
- TanStack Query v5 retry guide — [https://tanstack.com/query/v5/docs/framework/react/guides/query-retries](https://tanstack.com/query/v5/docs/framework/react/guides/query-retries).
- PartySocket API — [https://docs.partykit.io/reference/partysocket-api/](https://docs.partykit.io/reference/partysocket-api/).
- React `useEffect` cleanup pattern — [https://react.dev/reference/react/useEffect](https://react.dev/reference/react/useEffect).
- CloudEvents v1.0 spec — [https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md). Mirrored in [client/src/types/cloudEvents.ts](../client/src/types/cloudEvents.ts) and [server/services/events/envelope.py](../server/services/events/envelope.py).
- Companion docs: [release_build_pipeline.md](release_build_pipeline.md) for the build-time wins, [frontend_architecture.md](frontend_architecture.md) for the cache + slice-subscription model that bounds runtime latency.
