# Release Build Pipeline

Compile-step plan for the npm distribution `machinaos`. Goal: cut cold-start ~20s further on top of the lazy-LangChain fix already in v0.0.76, shrink the Vite main bundle below 200 KB gz, and drop the `tsx` interpreter cost from the Node.js sidecar.

User scope (confirmed before this work): stay on npm distribution; **no** Nuitka/PyOxidizer standalone binary channel; optimize Vite output too; **skip** the plugin-walker (~11s) and service-status-refresh (~20s) hotspots — those are runtime concerns, not compile-time.

## Tooling

| Layer | Tool | Why |
|---|---|---|
| TypeScript type-check | `@typescript/native-preview` (tsgo) | Microsoft's Go-port of `tsc`, ~10x faster on `--noEmit`. Type-check only — JS emit in tsgo is still preview. Vite/esbuild keep producing the actual JS bundles. Stays in `devDependencies`, never ships to users. |
| Vite output | `manualChunks` + `target: 'es2022'` | Split heavy libs (reactflow, radix-ui, lobehub, react-markdown stack) so the main bundle no longer hits the 1500KB warning ceiling. ES2022 unlocks `findLast` / optional-chaining-assignment without polyfills (Chrome 94+, FF 93+, Safari 15.4+ — within React 19 / Tailwind 4 baseline). |
| Node sidecar | `esbuild` bundle to `dist/index.js`, run via `node` | Drops tsx interpreter startup (~500ms-1s every server boot). `--packages=external` keeps Express in `node_modules/` for patch flow. |
| Python | `python -O -m compileall -q -j 0 server/` | Pre-compile bytecode. Implemented as step `[5/6]` in [`cli/commands/build.py`](../cli/commands/build.py) (`COMPILEALL_SOURCE_DIRS` constant lists the dirs). ~5-10s cold-start gain. `-O` strips asserts + `__debug__` branches. |

## Implementation steps

### 1. tsgo type-check

- `client/package.json` → add `"@typescript/native-preview"` to `devDependencies`, add scripts:
  - `"typecheck": "tsgo --noEmit"`
  - `"typecheck:tsc": "tsc --noEmit"` (fallback during the rollout)
- `.github/workflows/release.yml` → add `pnpm --filter react-flow-client run typecheck` to the predeploy gate before the build job.

### 2. Vite manualChunks + target

- `client/vite.config.js` → extend `build` block:
  - `target: 'es2022'`
  - `chunkSizeWarningLimit: 600` (down from 1500)
  - `rollupOptions.output.manualChunks` mapping:
    - `vendor-react`: `react`, `react-dom`, `react-hook-form`, `@hookform/resolvers`
    - `vendor-flow`: `reactflow`
    - `vendor-radix`: `@radix-ui/*`, `radix-ui`
    - `vendor-icons`: `lucide-react`, `@lobehub/icons`
    - `vendor-query`: `@tanstack/react-query`, `@tanstack/query-sync-storage-persister`, `@tanstack/react-query-persist-client`, `@lukemorales/query-key-factory`
    - `vendor-markdown`: `react-markdown`, `remark-gfm`, `remark-breaks`, `prismjs`, `react-simple-code-editor`, `@uiw/react-json-view`
    - `vendor-misc`: `idb-keyval`, `fuzzysort`, `cmdk`, `sonner`, `qrcode.react`

Keep `sourcemap: analyze` (already correct), keep React Compiler config.

### 3. Node sidecar esbuild bundle

- `server/nodejs/package.json` → add `esbuild` devDep; replace scripts:
  - `"build": "esbuild src/index.ts --bundle --platform=node --target=node22 --format=esm --packages=external --outfile=dist/index.js"`
  - `"start": "node dist/index.js"` (was `tsx src/index.ts`)
  - keep `"dev": "tsx watch src/index.ts"`
- `server/nodejs/.gitignore` → new file: `dist/`

### 4. Python bytecode pre-compile

- [`cli/commands/build.py`](../cli/commands/build.py) → after `uv sync` (step `[4/6]`), step `[5/6]` runs:
  ```python
  run(
      uv_run("python", "-O", "-m", "compileall", "-q", "-j", "0", *COMPILEALL_SOURCE_DIRS),
      cwd=server_cwd,
      check=False,  # missing pyc is non-fatal — runtime regenerates as needed
  )
  ```
  The list of source dirs is the public `cli.commands.build.COMPILEALL_SOURCE_DIRS` constant — `scripts/install.js` mirrors it.

The npm tarball still excludes `__pycache__/` per `package.json` `files` (cross-Python-minor pyc fragility) — `compileall` runs on the user's machine via `machina build` or `scripts/install.js` post-install.

### 4b. Temporal binary fetch + DATA_DIR parity

- Step `[6/6]` runs `uv run python -m services.temporal._install`, which pooch-downloads the official `temporal` CLI into `<DATA_DIR>/packages/temporal/` (= `~/.machina/packages/temporal/` by default). Pre-fetching at build time turns the ~114 MB download into a sub-second cache hit on first `machina start`.
- **`machina build` layers `.env.dev` first.** `build_command()` calls `cli.config.load_dev_overrides(root)` before the install steps, so the build's `DATA_DIR` matches what the runtime sees. Without it, a repo checkout's `machina build` read `DATA_DIR=~/.machina` from `.env.template` and installed Temporal under user home, but `machina dev` then read `DATA_DIR=.machina` from `.env.dev` and re-downloaded into `<repo>/.machina/` — a redundant ~114 MB fetch on every fresh clone.
- **Safe for global installs.** `.env.dev` is git-committed for contributors but is NOT in the npm `files` list, so an npm-distributed copy has no `.env.dev` — `load_dev_overrides` is a no-op and everything falls through to the `.env.template` default (`DATA_DIR=~/.machina`), matching `machina start` / `machina daemon`.

### 5. Wire bundle + compileall into install.js

- `scripts/install.js` → after `uv sync`:
  1. `npm --prefix server/nodejs run build` — produce `dist/index.js`
  2. `python -O -m compileall -q -j 0 server/` — same as build.py

Idempotent on re-runs (compileall only rewrites stale pyc; esbuild is deterministic).

### 6. Tarball verification

- `npm pack --dry-run` after the change. Confirm `server/nodejs/dist/index.js` is included (existing `server/` glob already covers it). Confirm no `__pycache__/` leakage.

## Critical files

| File | Action |
|---|---|
| `client/package.json` | + tsgo devDep, + typecheck scripts |
| `client/vite.config.js` | + manualChunks, target, lower warning |
| `server/nodejs/package.json` | + esbuild devDep, build script, change start |
| `server/nodejs/.gitignore` | new — ignore `dist/` |
| `cli/commands/build.py` | + compileall step (`[5/6]`), `COMPILEALL_SOURCE_DIRS` constant |
| `scripts/install.js` | + sidecar bundle + compileall calls |
| `.github/workflows/release.yml` | + typecheck gate before build |

## Verification

1. `pnpm --filter react-flow-client run typecheck` → <5s, zero errors.
2. `ANALYZE=1 pnpm --filter react-flow-client run build` → open `client/dist/stats.html`. Expect: no chunk above 600 KB gz, main < 200 KB gz, `vendor-flow` split.
3. `cd server/nodejs && npm run build && node dist/index.js` → starts on :3020 in <100ms.
4. `cd server && uv run python -O -m compileall -q -j 0 .` → `__pycache__/*.opt-1.pyc` present.
5. Cold-start: clean install + `machina start > start.log 2>&1` → `Application startup complete` at ≤+50s (was +66.9s).
6. `npm pack --dry-run` → `server/nodejs/dist/index.js` included; no `__pycache__/`; tarball size ≤ v0.0.76.
7. Smoke: `machina start` → load http://localhost:3000 → run "AI Assistant" example → agent responds.

## Out of scope (future work)

- Nuitka / PyOxidizer standalone binaries (separate release channel, ~1-2 weeks CI matrix).
- Plugin walker lazy-loading (~11s).
- Service-status refresh parallelisation (~20s post-startup-complete).
- mypyc / Cython for hot paths (low ROI — pydantic V2 already Rust, httpx/aiohttp already C).
- swc / stc TypeScript checker (not production-grade in 2026).
