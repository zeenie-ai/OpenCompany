# Browser workspace

For installation, profiles, policy and agent execution, see the
[native browser runtime](./browser.md). This document describes the shared
workspace surface and its live-stream contract. By default the runtime launches
installed Chrome/Edge/Chromium headless in a dedicated OpenCompany profile and
renders it inside this workspace. It does not attach to your personal profile.
`BROWSER_HEADLESS=false` additionally opens a desktop window; Chrome for Testing
requires `BROWSER_RUNTIME=testing`. `BROWSER_FAMILY=chrome` (the default) discovers
installed Chrome dynamically and keeps it selected even when another browser
has a newer version; no machine-specific executable path is needed.

Normal mode and Dev mode share `components/workspace/WorkspaceTabs.tsx`
(Browser, Canvas, Mobile) and `components/browser/BrowserWorkspace.tsx`.
The Dev toolbar's Workspace button opens the existing resizable dock;
Canvas boards, push notifications and temporary file previews still use
`canvasDockStore`. Its selected tab is persisted. A Canvas push does not
switch an already-open Browser tab; explicitly opening a file preview does
switch to Canvas. Mobile renders the local Android phone through
`MobileWorkspace`; see [Mobile Workspace](../docs/mobile-workspace.md).
The Browser node's parameter panel (`MiddleSection`, `isBrowserPanel`) shows
the same viewer above the node's settings.

## Full view

The normal Browser and Mobile views fill the remaining Workspace height and
width, without an outer gutter or a dedicated Full view button row. The compact
toolbar keeps status and takeover visible. Its **Browser controls** toggle
expands navigation, address and tab selection without reconnecting the stream.
Mobile has a **Phone controls** toggle and a collapsible AI task panel; the phone
image keeps its original proportions, so a wide panel can still have side bars.

Click **Full view** in the toolbar to fill the screen. Navigation, address,
control and exit buttons remain outside the page image. **Exit full view** or
**Esc** returns to the previous layout. The same mounted canvas and stream stay
active, so entering or leaving does not reopen the session. The shared
`components/workspace/FullView.tsx` boundary handles native fullscreen changes
and restores focus to its button on exit. Mobile uses the same boundary, hiding
its task form while enlarged; secondary controls stay available through the
toolbar toggle. Full view does not grant
manual control; use the existing control button separately. If fullscreen is
unavailable, the UI explains how to use the Workspace expand button instead.

## Discovery and identity

Normal-mode employee summaries include two lists. `browser_nodes: [{node_id,
label}]` comes from `services/employees/graph_index.py`, which discovers nodes
by the registered plugin's `isBrowserPanel` hint, including disabled nodes for
manual viewing; Home's Browser tab uses it. `workspace_nodes: [{kind, node_id,
label}]` comes from `services/workspace_capabilities.py` (`workspace_nodes`),
which reads each plugin's `ui_hints.workspace.kind` and treats `isBrowserPanel`
as `browser`; Home's Mobile tab uses its `mobile` entries. Dev reads the same
NodeSpec hints from the current graph and observes schema hydration:
`isBrowserPanel` for the Browser tab, and `uiHints.workspace.kind` through
`components/workspace/descriptors.ts` for the Mobile tab. The Mobile tab's
value is `android` (`WorkspaceTab` in `WorkspaceTabs.tsx`), which is what the
persisted tab selection stores.
The viewer selects one node and keys its session by workflow and node ID.
Viewing a saved node attaches a session but does not launch Chrome; Start
browser calls the authorized `browser_session_open` handler. Unsaved workflows
must be saved first. Adding a node to an existing workflow must also be saved
before the server can authorize it.

## Direct Browser tasks

The task panel beside the selected Browser tool discovers connected
`browser_agent` nodes from the saved server graph. A standalone Browser tool
offers **Add Browser AI Agent**, which uses the same atomic creation recipe as
canvas and Agent Builder and adopts the returned saved graph operations.
Discovery never marks the agent itself as a Browser viewer node.

Choose an associated agent, enter a task and submit. The API resolves its
Browser tool, policy, profile and owner; the client sends saved IDs, prompt and
a submission UUID. Retrying a lost acknowledgement uses the same UUID. The task
runs through Workspace admission and native AI execution, independently of
workflow Start/Stop/Resume, employee chat and deployed Context. **Cancel** uses
the existing invocation cancellation and waits for owner cleanup. Reset keeps
history and can remain in progress while an owner is unavailable.

Recent tasks show prompt, lifecycle status, a bounded safe result/error and
authorized artifact links. This projection is separate from chat, excludes
tool transcripts and secret values, and uses stable invocation IDs. History
pages contain 20 records (API maximum 100); terminal entries expire after
35 days while active tasks remain. The UI polls current history and offers
older pages. A missing owner displays **Browser unavailable — waiting for its
owner.** Tasks stay queued for the existing owner and do not trigger another
model turn merely because a Browser Activity has not been picked up.

## Protected login observations

Configured 1Password login displays **Browser observations are paused during
protected login.** The server blocks capture refresh, drains pending frames
and raw metadata, and suppresses tabs, page addresses, dialogs and clipboard
content before retrieving credentials. A `sensitive` event invalidates pending
client image decodes and clears cached picture, address, tabs and dialogs.
The same gate applies to newly attached viewers and recovered owners.

If private submission cannot be confirmed, the gate remains in place. Use
**Close browser for manual login**, then **Start browser** and Take control to
finish login. Closing must be confirmed by the owner before the sensitive
latch clears. The normal handoff covers MFA, passkeys and multi-step login.
See [configured login](browser.md#configured-1password-login).

## When the agent asks for help

`request_user` puts the profile's controller in `awaiting_user`. The viewer
shows the agent's message, which arrives on the live socket's `state`
message. Elsewhere only identity and the reason travel, because broadcasts
and employee summaries reach every connected socket:

- **Home:** the employee summary's `browser_request` (`{node_id, reason,
  since}`) turns the employee's status to Needs you, and their page says why
  above the message box, beside Help in browser, which opens the workspace on
  its Browser tab. The live task overlay stands back meanwhile, since the
  agent's node is still executing while it waits.
- **Dev:** a `browser_updated` broadcast for the open workflow in
  `awaiting_user` opens the dock on its Browser tab
  (`canvasDockStore.showBrowser`).

## Transport and display

The dedicated same-origin `/ws/browser` socket uses the application's auth
and Origin checks. The first message attaches a node target and reports the
visible surface's width, height and DPR. Binary frames contain version/kind,
a big-endian uint16 JSON-header length, JSON metadata, and JPEG bytes.
`protocol.ts` validates the envelope. The viewer draws frames serially and
acknowledges each consumed or skipped frame so the server's two-frame window
does not stall. Object URLs are released after decoding and on cleanup.

In distributed mode the accepting backend proxies that same binary envelope
to the saved browser owner. Its signed internal attach binds the authenticated
principal and original attach body, and the owner reauthorizes the saved
target. This adds no public CDP endpoint and does not let clients select a
backend URL. Cross-replica invalidation uses identifiers followed by an
authorized refresh, never browser frames or private page payloads.

Frames fit the available surface without cropping. Input coordinates exclude
letterboxing and map through the frame metadata to CDP page coordinates.
ResizeObserver reports panel resizing; document visibility and dock visibility
pause delivery. Switching tabs/nodes or modes releases the socket and input
control. A hidden Home dock also releases control and stops visible streaming.

Navigation, page-tab selection, mouse, wheel, keyboard, pasted text and page
dialogs require Take control; the server remains the authority on the control
lease. Hand back returns control to the agent. Another controlling viewer is
identified explicitly before a forced takeover is offered.

Transient socket disconnects reconnect. Authentication and target errors need
explicit action. Initial CDP screencast failures produce visible errors and
retry after 1, 2 and 4 seconds while a viewer is visible. After that, resizing,
visibility changes or reconnecting can attempt attachment again.

## Live-view latency and diagnostics

The browser socket's receive loop handles frame ACKs without awaiting page
commands. Each profile's ordered scheduler holds at most 64 commands and
64 KiB of serialized queued messages. Only adjacent pointer moves or compatible
wheel events coalesce; wheel coordinates, button state and modifiers must match.
Click/key transitions retain ordering. Overflow reports an error and starts a
safe release instead of silently dropping a key-up or click. Control release,
disconnect and page changes invalidate pending input. Input has its own CDP
session so resizing the screencast does not detach an active input command.

Release immediately blocks additional input. It then settles the dispatched
command and releases held keys/buttons before handing control back. A cancelled
takeover cannot later authorize a hidden viewer. Unknown CDP outcomes require
retiring the managed browser before agent work resumes; failure to retire keeps
control held. Pointer cancellation, window blur and hidden documents also
request release. The server is authoritative for these barriers.

Each viewer retains the newest unsent frame when its FPS limit is reached,
then sends it when the limit allows. The final update must be delivered even
when the page stops producing frames. Viewer ACKs identify an outstanding
sequence number; duplicate or unknown ACKs do not open additional credit.
Sequences remain unique across replacement hubs on a surviving viewer socket.
The client bounds decoding to two frames, acknowledges skipped frames and
updates canvas backing dimensions only when the image size changes. Frames
being decoded during disconnect, hiding or stream invalidation cannot repaint
an obsolete live view.
Chrome screencast ACKs are tracked per event and per capture session, rather
than replacing an earlier unacknowledged event with the newest one. The hub
acknowledges each capture after admitting it to bounded viewer slots; it does
not wait for client paint or a flush timer before returning Chrome's capture
credit. The separate two-frame viewer window still bounds client delivery.

Set `OPENCOMPANY_BROWSER_DIAGNOSTICS=1` before starting the server to enable
bounded timing samples and counts (`nodes/browser/_stream_metrics.py`).
Snapshots include p50/p95 timing values and counts, with at most 256 samples
per timing name. For frontend decoding/drawing diagnostics, append
`?browserStreamDebug=1` to the app URL and inspect the browser console.
These diagnostics contain numeric timing/count information, not page content,
typed text, credentials or screenshots. Drawing duration alone is not
input-to-paint latency.

### Reproducible local benchmark

`server/scripts/benchmark_browser_stream.py` launches an **already installed**
Chrome in a disposable profile, serves a controlled page on loopback, and
executes the real `browser_live_view`, `Viewer`, `ScreencastHub`, WebSocket and
CDP input paths. It never downloads Chrome or attaches to a user's profile.
Authentication and profile/control lookup are process-local fixtures; it does
not open the application database. External DNS resolution is blocked for the
isolated browser. Chrome and the temporary server are stopped afterward.

The page paints a binary counter for each injected mouse press. A small
reference viewer decodes the streamed JPEG, reads that counter, draws it on a
canvas, and measures through two `requestAnimationFrame` callbacks. This is a
**real transport measurement with a browser paint proxy**, not physical display
latency and not the production React renderer. Frame age uses Chrome's capture
timestamp and the local viewer's clock. A sample that is not painted within
the deadline is reported as a missed update; percentiles cover only observed
updates and must always be read alongside the miss count. The nominal target
is p95 at or below 200 ms with zero missed updates.

From `server/` in PowerShell, preserve the baseline **before** changing stream
code, then use matching settings for both measurements:

```powershell
$browserBaseline = Join-Path $env:TEMP 'opencompany-stream-baseline.py'
Copy-Item -LiteralPath nodes/browser/_stream.py -Destination $browserBaseline
.venv/Scripts/python scripts/benchmark_browser_stream.py --stream-source $browserBaseline --label baseline --samples 50 --warmup 5 --output "$env:TEMP/browser-before.json"
# Apply the implementation change, then run:
.venv/Scripts/python scripts/benchmark_browser_stream.py --label current --samples 50 --warmup 5 --output "$env:TEMP/browser-after.json"
```

Use `.venv/bin/python` on Linux/macOS. Supply `--chrome /path/to/chrome` if
automatic discovery cannot find an installed browser. `--fps 10 --samples 20
--warmup 3` exercises explicit frame throttling; keep all other settings equal.
Reports record Chrome version, loaded stream-source hash, benchmark-script hash,
configuration, raw timing samples, observed page counters, target-side click
count and missed updates. Keep the complete source revision with the reports:
`--stream-source` replaces only the stream module, while its supporting modules
come from the current checkout.

Run comparisons sequentially under comparable machine load; alternate before
and after runs and repeat them before drawing performance conclusions. This
fixture does not validate WAN latency, large third-party pages, the browser-use
CLI/agent path, production ownership checks or all human-control races. Those
remain separate correctness and end-to-end checks.

### Historical local measurements, 2026-09-26

These measurements predate the installed-browser/default-visible runtime change.
The final paired runs used installed **Chrome 153.0.8010.53**, not the then-default
pinned Chrome 154. Chrome 153's live `/json/protocol` did not advertise
`Page.startScreencast.sendLastFrame`; the implementation requests that optional
field, but these results do not establish its behavior on pinned Chrome 154.
The reference viewer reported `document.visibilityState === "visible"`.
Runs were sequential, baseline then current, on the same host with a 1,000 ms
sample deadline and 25–31 ms pauses between samples. The 30 fps runs used 50
samples after five warmups; 10 fps used 20 samples after three warmups.

| FPS | Stream | Input-to-paint p50 / p95 (ms) | Frame-age p50 / p95 (ms) | Missed by deadline |
| --- | --- | --- | --- | --- |
| 30 | Baseline | 30.4 / 40.1 | 15.05 / 18.81 | 1 / 50 |
| 30 | Final | 29.6 / 41.8 | 16.10 / 22.64 | 0 / 50 |
| 10 | Baseline | 36.7 / 49.4 | 20.10 / 30.66 | 10 / 20 |
| 10 | Final | 80.8 / 129.9 | 65.84 / 87.18 | 0 / 20 |

All target-side presses arrived in each run: 55/55 at 30 fps and 23/23 at
10 fps, including warmups. No transport errors were recorded. Final reference
decode/draw p95 was 7.1 ms at 30 fps and 8.2 ms at 10 fps; draw-to-double-rAF
p95 was 5.7 ms in both. The final runs met the reference target of p95 below
200 ms with zero misses. This is evidence of better delivery in this fixture,
not a general speedup: baseline percentiles exclude missed updates, and the
final 30 fps p95 was slightly higher.

During development an intermediate implementation measured p95 265.8 ms and
one missed deadline at 30 fps, although that counter eventually appeared.
Earlier pilot and intermediate runs also varied substantially. The table
measures the completed capture-credit change; it does not justify a production
latency guarantee. Repeat alternating comparisons and test the production
React renderer and selected runtime before making that claim.

The four raw JSON reports were emitted with labels `baseline-paired`,
`current-paired`, `baseline-paired-fps10`, and `current-paired-fps10`. The
benchmark instructions above reproduce their settings. Recorded SHA-256s:

```text
baseline _stream.py: f290448fbe696bbe362a2b7343a3cb977556e3d28dc350997945e4ccd3fb14b3
final _stream.py:    78d4f948a8cce3b0f8a2f5a14390f03c3bea76802f2c445955a835cd7f2f700c
benchmark script:   283e28a5afa86c16370e39b989b8d800462b940d1e81f6755f29bded129a7716
```

## Tests

- `client/src/components/browser/__tests__`: stream envelope, geometry, attach,
  rendering/ACK, cleanup, visibility and control gating.
- `client/src/features/home/__tests__/workspace.test.tsx`: employee identity and
  browser discovery integration, tab changes and hidden dock state.
- `client/src/components/ui/__tests__/WorkspaceDock.test.tsx`: Dev tabs, schema
  hydration and viewer unmount on close.
- `client/src/components/mobile/__tests__/MobileWorkspace.test.tsx`: the Mobile
  tab's phone view.
- `client/src/features/home/__tests__/presentation.test.ts`: Needs you while the
  agent waits in the browser.
- `client/src/stores/__tests__/canvasDockStore.test.ts`: a Canvas push never
  leaves a live browser; `showBrowser` opens the Browser tab.
- `server/tests/nodes/browser/test_browser_stream.py`: fake-CDP startup failure
  recovery and cancellation.
- `server/tests/nodes/browser/test_browser_frame_delivery.py`: trailing frames,
  independent viewer credit, duplicate ACKs, capture replacement and diagnostics.
- `server/tests/nodes/browser/test_browser_live_control.py`: bounded input queues,
  responsive receive loop, takeover/release races, held-input cleanup and teardown.
- `server/tests/services/employees/test_list_employees.py`: capability-based
  browser discovery in employee summaries, and the browser request (never the
  agent's message).
