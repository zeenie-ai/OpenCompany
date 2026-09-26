# Desktop App

The OpenCompany desktop app is an Electron shell in [`desktop/`](../desktop/)
that bundles `uv`, a standalone CPython and bun (the only JavaScript runtime the
backend needs — no Node, no npm), provisions the backend's
virtual environment on first launch, runs the same uvicorn backend the CLI
runs, and shows the backend-served SPA in a native window. Nothing in
`client/` changes: every URL in the SPA is relative and the WebSocket URL is
derived from `window.location`, so a window pointed at
`http://127.0.0.1:<port>` is the app. The backend's half of the arrangement
is [desktop_host_contract.md](./desktop_host_contract.md).

## Why Electron, why provision at first run

Both decisions are recorded with their alternatives in the plan that
produced this feature. Short form:

- **Electron over Tauri.** One rendering engine (Chromium, what the SPA is
  tested on) instead of three; a TypeScript host instead of a Rust one the
  team has no runway for; ComfyUI Desktop as a direct precedent for the
  uv-based provisioning model. The 60 MB installer difference is noise next
  to the ~430 MB first-run download both would need.
- **Bundle uv + Python, `uv sync` at first run** (over a pre-built venv in
  the installer). The venv is built at its final path, so nothing has to be
  made relocatable; the macOS signing scope (later) is a fixed set of
  ~60 CPython Mach-O files plus uv and bun; updates re-sync only when
  `uv.lock` / `pyproject.toml` / the bundled Python changes. OpenCompany
  already provisions at first run (`install.js` runs `uv sync`, Temporal and
  the plugin CLIs (`bun add` into `~/.opencompany/packages/`) download on
  first use, the Browser node installs the browser-use CLI with `uv tool install`), so uv ships in every
  design.

## Layout

```
OpenCompany.app / OpenCompany/ / AppImage
  resources/
    app-root/                     the backend sibling layout (core.approot):
      .env.template               server/, client/dist/, .env.template,
      package.json                package.json, .opencompany/workflows/,
      .opencompany/workflows/     server/uv.lock, server/pyproject.toml
      client/dist/
      server/  (+ nodejs/dist/index.js — the sidecar's own `bun build --target=bun`, dependencies inlined)
    runtime/
      uv/uv[.exe]                 pinned uv (runtimes.json)
      python/                     python-build-standalone install_only_stripped
      bun/bun[.exe]               official bun release zip (runtimes.json): the JS executor runtime
                                  and the installer for the plugin CLIs; LICENSE.md fetched from the bun repo
    licenses/                     OpenCompany-LICENSE, Bun-LICENSE.md, Python-LICENSE.txt

<userData>                        %APPDATA%/OpenCompany | ~/Library/Application Support/OpenCompany | ~/.config/OpenCompany
    pyenv/venv/                   UV_PROJECT_ENVIRONMENT (+ .provision.json stamp)
    pyenv/python/                 UV_PYTHON_INSTALL_DIR (fallback path only)
    pyenv/cache/  pyenv/tools/    UV_CACHE_DIR, UV_TOOL_DIR / UV_TOOL_BIN_DIR
    bun/                          BUN_INSTALL (+ bun/install/cache = BUN_INSTALL_CACHE_DIR): bun's package
                                  cache and global state, kept out of a dev install's ~/.bun
    pycache/                      PYTHONPYCACHEPREFIX (bundle is read-only)
    logs/main.log, backend.log, backend.stdout.log
    desktop-state.json            persisted port
    desktop.env                   operator overrides -> OPENCOMPANY_ENV_FILE

~/.opencompany/                   DATA_DIR, unchanged, shared with CLI installs
```

`desktop/stage/` is the exact same tree before packaging; in development the
shell reads it directly (`stage/runtime/<os>-<arch>/`), in a packaged app
`process.resourcesPath`.

## Boot and quit

1. Single-instance lock; a second launch focuses the existing window.
2. Setup window (`desktop/src/renderer/setup/`) shows progress and errors.
3. `resolveInterpreter` (`desktop/src/main/index.ts`): compute the stamp
   `sha256(uv.lock + pyproject.toml + bundled Python version + uv version)`;
   if the venv is missing or its `.provision.json` differs, run
   `uv sync --frozen --no-dev --extra docs --project <app-root>/server --python <bundled python>`
   with `UV_PYTHON_PREFERENCE=only-system`, streaming uv's output to the
   setup window. If uv rejects the bundled interpreter, fall back to
   `uv python install 3.12` + the same sync with `only-managed`.
4. `choosePort`: persisted port, else 5678, else scan 5679-5699. If the
   preferred port is busy and `/health` says an OpenCompany backend is there
   (a `company serve` the user left running), attach instead of spawning.
5. `spawnBackend`: `<venv>/python -m uvicorn main:app --host 127.0.0.1 --port N --timeout-graceful-shutdown 5`
   with the contract env (`desktop/src/main/env.ts`), stdin kept as a pipe,
   stdout/stderr to `logs/backend.stdout.log`, detached on POSIX. The
   graceful-shutdown bound is load-bearing: uvicorn otherwise waits forever
   for a renderer WebSocket that died with a reset, never reaches the
   lifespan teardown, and the shell ends up tree-killing instead of the
   backend reaping its own children.
6. `waitReady` polls `/health/ready` (180 s budget) and maps the backend's
   `phase` to splash text, including "Downloading the Temporal engine" on
   first run.
7. Main window loads `http://127.0.0.1:<port>/`. `setWindowOpenHandler` and
   `will-navigate` send every off-origin URL to the OS browser, which is what
   OAuth / device-flow logins and `target="_blank"` links need.
8. `checkForUpdatesAndNotify`.

Quit: `POST /api/desktop/shutdown` with the per-launch token, close stdin,
wait up to 30 s, then `taskkill /T` (Windows) or SIGKILL the process group.
On the next launch a still-alive pid in `backend.pid` is tree-killed first.
If the backend crashes it is restarted with 1 s / 3 s / 9 s backoff, at most
three times in five minutes, then the setup window shows the error.

## Building

Standalone bun package (deliberately not in the root `workspaces`, which
`cli/tests/test_release_pipeline_config.py` locks). Zero native Node
modules, so both macOS architectures build on one arm64 runner.

```bash
cd desktop
bun install
bun run stage                 # app-root from `bun pm pack --dry-run` + runtimes for this host
bun run typecheck && bun run test && bun run test:invariants
bun run build && bun run test:e2e
bun run gen-icons && bun run dist
```

`desktop/scripts/stage.ts` takes the file list from `bun pm pack --dry-run` (the
root `files` allowlist, i.e. what the published tarball ships), drops what a bundle
never needs (CLI, install scripts, client sources, backend tests) and
force-includes `server/uv.lock`. It builds the JS executor sidecar with the
sidecar package's own `bun run build` (`bun build --target=bun`, a self-contained
bundle with `express` inlined) and copies `dist/index.js` — no re-bundle of its
own. `desktop/scripts/fetch-runtimes.ts` downloads the versions pinned
in `runtimes.json`, verifies each against the upstream checksum manifest
(`.sha256` sidecars for uv, `SHA256SUMS` for python-build-standalone,
`SHASUMS256.txt` for bun), fetches bun's `LICENSE.md` from the bun repo (the
release zip carries no license file; electron-builder ships it as
`licenses/Bun-LICENSE.md`), caches in `vendor/`, prunes stale runtime dirs (a
leftover `node/` from an older stage), and extracts into
`stage/runtime/<os>-<arch>/`. The staged-tree invariants assert bun is present,
no `node/` dir exists, and the sidecar boots on the bundled bun with Node
stripped from PATH. Extraction uses the platform `tar`; on Windows
the System32 bsdtar is used explicitly because Git's GNU tar cannot read
zips.

`electron-builder.yml` maps `stage/app-root/` and
`stage/runtime/${os}-${arch}/` to `resources/` outside the asar. Targets:
NSIS x64; DMG + zip for arm64 and x64 (zip is required by electron-updater);
AppImage + deb x64. Artifact names spell the OS out and carry no version
(`OpenCompany-windows-x64.exe`, `OpenCompany-macos-arm64.dmg`,
`OpenCompany-linux-x86_64.AppImage`, `OpenCompany-linux-amd64.deb`; on
Linux electron-builder's `${arch}` takes each format's own spelling, so
the README links use those exact names): the README links to them through
GitHub's `releases/latest/download/<file>` redirect, which only works for
a name that is identical on every release. The version lives in the tag
and in the `latest*.yml` feeds, which electron-updater compares by their
`version` field, not by file name; the feeds are generated from whatever
names are produced, so the rename was safe between releases.

## Signing and updates

Unsigned first release: `mac.identity: null`, `hardenedRuntime: false`,
`notarize: false`, no Windows certificate, `CSC_IDENTITY_AUTO_DISCOVERY=false`
in CI. Electron's own ad-hoc signature keeps the arm64 binary launchable.
What users see:

| OS | First launch |
|---|---|
| macOS 15 | "Apple could not verify..." then System Settings > Privacy & Security > Open Anyway (right-click Open no longer bypasses on Sequoia) |
| Windows | SmartScreen "Windows protected your PC" > More info > Run anyway |
| Linux | nothing |

Updates: electron-updater against the GitHub Release feed
(`latest.yml`, `latest-mac.yml`, `latest-linux.yml`). Windows NSIS and Linux
AppImage download and apply; deb has no auto-update. **macOS is notify-only
until the app is signed** because Squirrel.Mac refuses to apply an update to
an unsigned app; the shell shows the new version and opens the releases
page.

Adding signing later: Windows needs `CSC_LINK` + `CSC_KEY_PASSWORD` (or
Azure Trusted Signing); macOS needs a Developer ID certificate in
`CSC_LINK`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`, and
`hardenedRuntime: true`, `notarize: true`, `identity` unset, plus an
entitlements file allowing JIT and unsigned executable memory for the
bundled Python. Then remove the macOS notify-only branch in
`desktop/src/main/updater.ts`.

## CI

- `desktop-release.yml` on `v*.*.*` tags (and manual dispatch): a `prepare`
  job drafts the GitHub Release from the annotated tag's message (title +
  notes) so the three legs upload into one draft; the matrix
  windows-latest / macos-14 / ubuntu-22.04 builds the client and sidecar
  with the repo toolchain, syncs the version and refuses to continue when it
  differs from the tag (the build ships the committed desktop version, not
  the tag's), stages, runs typecheck + unit + invariant tests, then
  `electron-builder --publish always` into that draft; a `finalize` job
  undrafts it once all legs pass. Separate from `release.yml` so the
  registry publish (`bun publish`) is never blocked and its locked strings are untouched.
  The step-by-step is in [ci_cd.md -> Cutting a release](./ci_cd.md#cutting-a-release).
- `desktop-ci.yml` on PRs touching `desktop/**` or the backend contract
  files: typecheck, unit, invariants, build, and the Playwright Electron
  smoke (xvfb on Linux) against a `uv sync`ed checkout venv.

## Development shortcuts

| Env var | Effect |
|---|---|
| `OPENCOMPANY_DESKTOP_APP_ROOT` | serve this app-root instead of `stage/app-root` (e.g. the repo checkout) |
| `OPENCOMPANY_DESKTOP_RUNTIME_DIR` | use these `uv/python/bun` dirs |
| `OPENCOMPANY_DESKTOP_VENV_PYTHON` | use this interpreter and skip provisioning |
| `OPENCOMPANY_DESKTOP_SKIP_PROVISION=1` | trust the existing venv even if the stamp differs |
| `OPENCOMPANY_DESKTOP_NO_ATTACH=1` | never attach to a running backend (tests) |

## Known limits

- First launch needs internet for the wheel download (~170 MB) and the
  Temporal binary (~114 MB, unchanged from the CLI). The setup window shows
  a retry button and the log tail on failure. An offline / enterprise build
  with a bundled wheel cache is a later variant.
- Windows Defender first-touch scanning of the fresh venv makes the first
  boot after provisioning slower (documented 21 s cold boot for the CLI).
- A fallback port (when 5678 is taken by something that is not OpenCompany)
  changes the SPA origin, which resets localStorage-held UI preferences and
  invalidates OAuth redirect URIs registered against 5678.
