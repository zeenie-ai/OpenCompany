# Performance — Cold-Start, Bundle, Runtime Latency

Living record of MachinaOs's performance posture: where launch time is
spent, what each optimisation cost / saved, and how to reproduce the
measurements. Update this file when you ship a perf-affecting change
or take a new measurement; numbers without timestamps and a commit
reference rot fast.

## Headline numbers (current baseline)

Measured on Windows, dev mode (`pnpm run dev` → `python -m machina dev`),
warm OS file cache, with bytecode pre-compile applied:

| Metric | Value | Source |
|---|---|---|
| Application startup complete | **2.90 s** | `start.log` 14:38 (post-`e77215c`, May 6 2026) |
| HTTP `ready on port 3010` | **3.17 s** | same |
| First WebSocket client connected | **8.29 s** | same |
| Status broadcasters fully settled | **12.27 s** | same |
| AIService import (warm) | **703 ms** | same — was ~31 s on v0.0.75 |
| Vite production build | **~16 s** | last `vite build` run |
| Vite main bundle | **234 KB gz** | `client/dist/assets/index-*.js` |

The cold-disk first launch on the same code is ~2× slower because the
Python interpreter regenerates `.pyc` files on the fly. Run
`machina build` once to pre-compile (see ["Pre-compile Python bytecode"]
below) and subsequent launches hit the warm number.

## Optimisation history

In chronological order. Each row links to the commit and the
corresponding `start.log` measurement.

| Date | Change | Saved | Commit | Plan / RFC |
|---|---|---|---|---|
| 2026-05-04 | Lazy LangChain imports in `services/ai.py` (BaseMessage stays eager; everything else moves into local imports) | **~30 s** AIService cold import | `74b75b6` | inline plan |
| 2026-05-05 | `tsgo` for client `--noEmit` typecheck (5× faster than `tsc`) | ~6 s in CI gate | `0b45fb1` | [release_build_pipeline.md](release_build_pipeline.md) |
| 2026-05-05 | Vite `manualChunks` (split reactflow / radix / lobehub-icons / TanStack Query / markdown stack) + `target: 'es2022'` | main bundle 232 KB gz, 7 vendor chunks separately cached | `0b45fb1` | same |
| 2026-05-05 | Pre-bundle Node.js sidecar with esbuild (`tsx src/index.ts` → `node dist/index.js`) | ~500 ms-1 s of tsx startup per server boot | `0b45fb1` | same |
| 2026-05-05 | Scoped `python -O -m compileall` over project source dirs (excludes `.venv/`, `tests/`) | 3-5 s on warm-disk imports | `0b45fb1` | same |
| 2026-05-05 | Test coverage: 12 build-orchestrator + 32 config-contract tests under `cli/tests/` | n/a (regression guard) | `0f1e55e` | same |
| 2026-05-06 | Frontend WS reconnect → PartySocket; auth bootstrap → TanStack Query; CloudEvents envelope typed | **~20 s** (eliminates +12 s WS drop + +7 s reconnect cycle on cold start) | `e77215c` | inline plan |

## Where launch time is spent today (post `e77215c`, warm cache)

```
T+0.00 — port-free begin
       ├── client / server / temporal processes spawn (0.06 s)
       ├── Vite dev ready (0.90 s) ────── (frontend cheap)
       └── Server import phase
           ├── FastAPI base imports (0.27 s)
           ├── DI container (0.42 s)
           ├── Core service imports (0.36 s)
           ├── AIService import (0.70 s) ← lazy LangChain payoff
           ├── Routers + plugin walker (0.84 s, 137 plugins)
           └── All imports complete (1.98 s)
T+2.25 — Lifespan startup
       ├── DB + cache (0.04 s)
       ├── Credentials + encryption (0.10 s)
       ├── Compaction service (0.12 s)
       └── All services initialized (2.63 s)
T+2.90 — Application startup complete ◀ HTTP-ready point
T+3.17 — `ready on port 3010` (uvicorn accepting)
T+3.85 — Temporal worker started (background)
        ├── ... (auth state propagates to FE; React mounts; queries fire)
T+8.29 — First WebSocket client connected ◀ UI-interactive point
       ├── Stripe daemon spawned + clean exit (0)
       ├── Telegram bot validated + polling started
       └── WhatsApp RPC connected (10.93 s)
T+12.27 — Status broadcasters settled ◀ "everything green"
```

## The remaining +5 s gap (HTTP-ready → first WS connect)

`ready on port 3010` at +3.17 s, first WS connect at +8.29 s. Backend
is idle in this window; the cost lives on the frontend. Likely
contributors:

1. **TanStack Query auth-bootstrap retry budget** ([client/src/contexts/AuthContext.tsx](../client/src/contexts/AuthContext.tsx) + [client/src/lib/connectionConfig.ts](../client/src/lib/connectionConfig.ts)). The `AUTH_RETRY` envelope (BASE 50 ms, CAP 4000 ms, MAX_ATTEMPTS 7) covers the typical 4 s backend cold-start window in 4-5 attempts. If the backend finishes mid-retry-cycle, the next jittered draw can land 1-3 s after readiness.
2. **React Strict Mode dual-mount** in dev. The 100 ms guard in [WebSocketContext.tsx:2554](../client/src/contexts/WebSocketContext.tsx#L2554) absorbs the bulk; remaining cost is React reconciliation + babel-plugin-react-compiler overhead on first render.
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
| 1 | Plugin walker on lifespan (137 modules under `server/nodes/`) | ~0.8 s | Backend | Out-of-scope per user direction; `_HANDLER_REGISTRY` populates via `BaseNode.__init_subclass__`. Lazy-load would shave another ~0.5 s. |
| 2 | TanStack Query auth bootstrap retry window | ~3-5 s | Frontend | See "remaining +5 s gap" above. |
| 3 | LangChain ecosystem imports inside `services/ai.py` | ~0.7 s | Backend | Already lazied; further wins require refactoring `AgentState`'s `Annotated[Sequence[BaseMessage], ...]` to defer `BaseMessage` resolution. |
| 4 | Status-broadcaster refresh (`refresh_all_services`, 2.6 s) | ~2.6 s | Backend | Runs after `Application startup complete`, doesn't block server-ready. WhatsApp + Telegram are the long tails. |
| 5 | Process spawn + Python interpreter init | ~0.4 s | Platform | Unavoidable without compiling Python to a binary (Nuitka / PyOxidizer — explicitly out of scope). |

## Pre-compile Python bytecode

The build-pipeline step that produces `.opt-1.pyc` files for the
project's own modules (excludes `.venv/`, `tests/`):

```bash
cd server
uv run python -O -m compileall -q -j 0 services core nodes routers models middleware main.py constants.py
```

`machina build` runs this as step `[5/6]`. The path list lives in
[cli/commands/build.py](../cli/commands/build.py)'s
`COMPILEALL_SOURCE_DIRS` constant; install.js mirrors the same list.
Tests at
[cli/tests/test_build_compile_pipeline.py](../cli/tests/test_build_compile_pipeline.py)
lock the contract.

Without this step on first launch, every `.py` import compiles
on-the-fly and the first launch is ~2× slower. Repeated launches use
the OS file cache anyway, so the steady-state difference is small —
but the user-visible "first run after `git pull`" is dominated by it.

## How to reproduce a measurement

### Cold-start timeline

```bash
# Warm path (typical dev iteration):
machina start > start.log 2>&1
# In another shell, wait for "Status broadcasters settled" then Ctrl-C the first.
```

Then extract phase markers:

```bash
grep -E "Freeing ports|Importing FastAPI|AIService imported|All imports complete|Lifespan startup begin|Application startup complete|ready on port 3010|StatusBroadcaster\] Client connected|broadcaster.refresh_all_services" start.log
```

### Bundle size + chunk shape

```bash
cd client
ANALYZE=1 pnpm exec vite build
# open client/dist/stats.html in a browser → treemap with gzip sizes
```

The `850 KB` chunk-size warning ceiling lives in
[client/vite.config.js](../client/vite.config.js). `vendor-icons` is
~830 KB (lucide + lobehub brand SVGs); the limit is set just above so
real regressions in other chunks fire while the icons baseline doesn't.

### Frontend retry envelope sanity

```bash
cd client
pnpm exec vitest run src/lib/__tests__/connectionConfig.test.ts
```

10 tests, locks RFC 6455 close code, AUTH_RETRY envelope, full-jitter
formula bound check across 100 × MAX_ATTEMPTS draws.

## Anti-patterns we've removed (don't reintroduce)

These were observed and fixed; the lessons are durable.

- **Eager `from langchain_openai import ChatOpenAI` at module top.** Cost ~21 s cold on Windows because it transitively pulls openai SDK + tiktoken + httpx wrappers. With `from __future__ import annotations` already in place, all type hints become strings; no eager import was structurally required. Lazy via per-function local imports + a small `BaseMessage`-only eager hold-out.
- **Recursive `setTimeout` retry chain in `useEffect` without `AbortController`.** Survived unmount, leaked timers, called `setState` on stale closures. The React docs explicitly flag this in [https://react.dev/reference/react/useEffect](https://react.dev/reference/react/useEffect). Replaced with TanStack Query's `signal`-aware `queryFn`.
- **Flat `setTimeout(connect, 3000)` reconnect loop.** No exponential backoff, no jitter, no `code === 1000` honouring, no message replay. Replaced with PartySocket — see [client/src/contexts/WebSocketContext.tsx](../client/src/contexts/WebSocketContext.tsx).
- **`if (event.code !== 1000)` magic numbers** scattered through the WS lifecycle. Replaced with `WS_CLOSE.NORMAL_CLOSURE` from [connectionConfig.ts](../client/src/lib/connectionConfig.ts) per RFC 6455 §7.4.1.
- **Inline `chunkSizeWarningLimit: 1500`** silently masking bundle bloat. Lowered to 850 KB so future regressions surface at `vite build` time.

## Open follow-ups

Tracked but explicitly **not** in any active plan.

| Item | Estimated saving | Notes |
|---|---|---|
| Plugin walker lazy-loading | ~0.5-0.8 s on server-ready | Would need to defer registration until first NodeSpec request rather than at module-import time. Touches every `BaseNode` subclass — biggest blast radius of the candidates. |
| `BaseMessage`-free agent loop (drop the only eager `langchain_core.messages` import in `services/ai.py`) | ~0.3-0.5 s | Native-SDK provider classes already use their own message types; `_run_agent_loop` could be retyped against them and the eager import dropped. |
| `+5 s` HTTP-ready → first-WS-connect gap | up to 5 s | Diagnostics needed (see "remaining +5 s gap"). May reveal nothing actionable. |
| Standalone Nuitka / PyOxidizer release binary | full Python interpreter init (~0.4 s) + `.pyc` regeneration on cold disk | User explicitly declined when scoping the build pipeline; revisit if "ship a single binary" becomes a product requirement. |

## References

- AWS Architecture Blog, ["Exponential Backoff and Jitter"](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) — full-jitter formula used by `AUTH_RETRY`.
- RFC 6455 §7.4.1 — WebSocket close codes ([https://datatracker.ietf.org/doc/html/rfc6455#section-7.4.1](https://datatracker.ietf.org/doc/html/rfc6455#section-7.4.1)).
- TanStack Query v5 retry guide — [https://tanstack.com/query/v5/docs/framework/react/guides/query-retries](https://tanstack.com/query/v5/docs/framework/react/guides/query-retries).
- PartySocket API — [https://docs.partykit.io/reference/partysocket-api/](https://docs.partykit.io/reference/partysocket-api/).
- React `useEffect` cleanup pattern — [https://react.dev/reference/react/useEffect](https://react.dev/reference/react/useEffect).
- CloudEvents v1.0 spec — [https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md). Mirrored in [client/src/types/cloudEvents.ts](../client/src/types/cloudEvents.ts) and [server/services/events/envelope.py](../server/services/events/envelope.py).
- Companion docs: [release_build_pipeline.md](release_build_pipeline.md) for the build-time wins, [frontend_architecture.md](frontend_architecture.md) for the cache + slice-subscription model that bounds runtime latency.
