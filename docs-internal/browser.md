# Native browser runtime

The `browser` node launches installed Chrome, Edge or Chromium in an
OpenCompany-owned profile. The default opens a visible browser window; it
does not attach to or copy your personal browser profile. Agents use the
browser-use CLI for ordinary browser operations; people watch and control the
same Chrome through the [Browser workspace](./browser_workspace.md). The old
agent-browser driver and separate `browserHarness` node are retired.

## Execution paths

| Path | Implementation | Lifetime |
| --- | --- | --- |
| Agent and workflow operations | `nodes/browser/browser/__init__.py` → `_scripts.py` / `_cli.py` → browser-use → Chrome CDP | A CLI subprocess per operation; Chrome and the profile's CLI daemon persist |
| WebMCP tools | `_webmcp.py` through the runtime's CDP connection | Tracked per page/frame, subject to saved node policy |
| Live picture | `_stream.py` → `Page.startScreencast` → binary `/ws/browser` frames | One capture hub per running profile, bounded queues per viewer |
| Human input | `_live_control.py` → dedicated page CDP session | One ordered command worker per hub; independent of CLI and capture resizing |
| Session and profile requests | `_handlers.py` over the application's authenticated request socket | Resolve ownership and saved node settings before opening sessions |

The native viewer is a streamed Chrome surface. Canvas URL items continue to
use sandboxed iframe previews. Neither displaying a Canvas URL nor attaching a
viewer silently starts a browser; **Start browser** explicitly opens an idle
session. Normal and Dev modes use the same viewer component.

## Installation and configuration

The default `BROWSER_RUNTIME=system` discovers installed Chrome first, then
Edge/Chromium. `BROWSER_CHROME_PATH` selects an explicit executable. If none
is available, startup fails with setup guidance: there is no automatic Chrome
for Testing download or fallback. System mode preserves the browser's native
user agent. An installed browser is not a guarantee that a website will accept
automation or permit a login; Take control remains available for human steps.

`BROWSER_RUNTIME=testing` explicitly selects pinned Chrome for Testing. Its
version, platform checksums and the browser-use CLI pin live in
[`server/config/browser_runtime.json`](../server/config/browser_runtime.json).
Only testing mode installs Chrome under `<DATA_DIR>/packages/chrome-for-testing/`.
Both modes install the pinned browser-use CLI through an isolated uv tool
installation under `<DATA_DIR>/packages/browser-use/`, outside the shared Bun
package tree. `uv` must be on PATH or selected by `OPENCOMPANY_UV_BIN`.
Installation can outlive the open request; the workspace reports progress.

Runtime settings are declared in
[`server/core/config.py`](../server/core/config.py); stream diagnostics reads
its opt-in flag directly from the process environment:

| Setting | Default | Purpose |
| --- | --- | --- |
| `BROWSER_RUNTIME` | `system` | Installed browser discovery; `testing` explicitly enables pinned Chrome for Testing |
| `BROWSER_HEADLESS` | `false` | Visible browser by default; set `true` explicitly for containers or unattended hosts |
| `BROWSER_CHROME_PATH` | Empty | Explicit Chrome/Edge/Chromium executable override |
| `BROWSER_MAX_INSTANCES` | `3` | Bound simultaneously running browser profiles |
| `BROWSER_IDLE_TIMEOUT_MS` | `600000` | Reap idle profiles; attached viewers and pending user work count as busy |
| `BROWSER_INSTALL_TIMEOUT_SECONDS` | `900` | Bound installer work |
| `BROWSER_SANDBOX` | `auto` | Select `auto`, `on` or `off`; auto accounts for Linux root and restricted user namespaces |
| `OPENCOMPANY_BROWSER_DIAGNOSTICS` | Unset | Set to `1` for bounded, payload-free live-stream timing summaries |

`AGENT_BROWSER_EXECUTABLE_PATH` is a legacy driver setting and is not read by
the native installer. See [Docker](./docker.md) for the current image's browser
extra and explicit system-Chromium configuration. CDP stays internal to the
runtime; deploy the authenticated application socket, not an exposed Chrome
debugging endpoint.

## Profiles, ownership and control

Profile metadata is stored in the `browser_profiles` table. Chrome state is
under `<DATA_DIR>/browser/profiles/<profile-id>/user-data/`, with generated
directory IDs rather than user-supplied names. A saved `profile_id` selects an
owner-scoped profile; leaving it empty creates/reuses the workflow's profile.
The CLI receives its own per-profile home, runtime and temporary directories.

The profile version guard checks the browser executable actually selected, not
the testing pin. A browser older than the version that last wrote the profile
is rejected with actionable guidance. OpenCompany neither deletes that profile
nor silently creates a replacement; select a compatible browser or explicitly
choose a different profile. This protects saved sessions during runtime changes.

The server derives identity from the authenticated caller and the saved
workflow. Browser discovery uses the plugin's `isBrowserPanel` hint, not a
second frontend list of browser type names. The node's profile, read-only mode,
WebMCP permissions, domain restrictions and private-network permission come
from saved operator settings, not model-supplied tool arguments.

`ProfileController` coordinates the profile lease and agent operation lock.
**Take control** waits for or interrupts an agent step before granting user
ownership. Human commands carry viewer and page generations and are checked
again immediately before CDP dispatch. **Hand back**, hiding, blur and
disconnect stop admission, discard stale queued work, settle dispatched work
and release held keys/buttons before the agent can resume. If a timed-out CDP
command has an unknown outcome, safe hand-back retires the managed Chrome; if
that fails, control remains held rather than resuming the agent prematurely.

The policy proxy in `_egress.py` enforces allowed destinations for browser
traffic. `allow_private_network` does not permit OpenCompany's protected ports
or cloud metadata endpoints. This network enforcement is separate from the
control lease and from agent read-only behavior.

## Node contract and compatibility

The tool schema exposes navigation, snapshots, element interaction, screenshots,
tabs, page reading, waits, WebMCP, user assistance and diagnostics. Workflow-only
`evaluate`, `run_python` and `close` are excluded from the model's tool schema.
See the [browser node flow](./node-logic-flows/web_automation/browser.md) for
operation and output details.

Snapshots return compact `[eN]` references; their backend node-ID mappings stay
on the server. Screenshot output is a workspace `FileRef`, never inline image
bytes or an unservable absolute host path. The screenshot helper copies the
CLI's contained temporary PNG into the workflow workspace and removes the
temporary file; persistence failure produces a notice.

[`services/workflow_migrations.py`](../server/services/workflow_migrations.py)
contains the legacy browser-node migration. `BrowserParams` also normalizes
legacy parameters from saved deployment snapshots. Use the current `browser`
node for new graphs; the [retired harness reference](./browser_harness.md)
exists to direct older links to the current implementation.

## Troubleshooting and validation

- **No Browser node listed:** save the workflow and browser node first. Normal
  mode discovers nodes from the employee summary; Dev mode observes hydrated
  node-schema hints.
- **First launch is slow:** inspect `browser_runtime_status` for installer
  progress and use `diagnose` for runtime/host details. Cold installation and
  warm live-view latency are separate measurements.
- **Frames stop or the view disconnects:** use Reconnect. Screencast attachment
  retries at 1, 2 and 4 seconds; authentication and target failures require
  explicit correction. Details and diagnostics are in
  [Browser workspace](./browser_workspace.md#live-view-latency-and-diagnostics).
- **Agent steps are slow but manual input is responsive:** each ordinary agent
  operation still starts a CLI process and can wait for page readiness. The
  stream changes do not replace that path or prove its latency improved.

Backend regression tests live in `server/tests/nodes/browser/`; viewer and
workspace tests live beside their client components. The
[local benchmark](./browser_workspace.md#reproducible-local-benchmark) exercises
real Chrome/CDP and production stream code with an isolated reference canvas.
Its recorded Chrome 153 results describe the prior benchmark configuration,
not a full React test or validation of the current default runtime selection.
