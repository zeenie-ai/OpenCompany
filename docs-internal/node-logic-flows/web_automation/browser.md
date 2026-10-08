# Browser (`browser`)

The Browser node launches installed Chrome/Edge/Chromium in a dedicated OpenCompany profile, rendered headless in the workspace UI by default, and exposes that profile to the live browser workspace. `BROWSER_RUNTIME=testing` explicitly selects Chrome for Testing; `BROWSER_HEADLESS=false` additionally opens a desktop window. It never attaches to a personal browser profile. It runs as a workflow step or as the agent tool named `browser`. The old `agent-browser` integration and separate `browserHarness` node have been replaced.

See [native browser architecture](../../browser.md) for runtime installation, profile storage, networking and lifecycle, and [browser workspace](../../browser_workspace.md) for the viewer and UI protocol.

The dedicated `browser_agent` composes this tool with OpenCompany's existing
reasoning loop. It is a separate agent node; the Browser tool keeps its viewer,
profile, policy and output contracts. [Node creation](../../node_creation.md#browser-ai-agent-creation)
documents the shared atomic recipe, and [deployment](../../browser_agent_deployment.md)
documents native Workspace tasks and distributed ownership.

## Implementation map

| Responsibility | Source |
| --- | --- |
| Node schema, dispatch and output mapping | [`BrowserNode`](../../../server/nodes/browser/browser/__init__.py) |
| Plugin registration and shutdown | [`nodes/browser/__init__.py`](../../../server/nodes/browser/__init__.py) |
| Profile runtime and managed Chrome | [`_runtime.py`](../../../server/nodes/browser/_runtime.py), [`_chrome.py`](../../../server/nodes/browser/_chrome.py) |
| Agent/workflow CLI calls and generated scripts | [`_cli.py`](../../../server/nodes/browser/_cli.py), [`_scripts.py`](../../../server/nodes/browser/_scripts.py) |
| Leases and control state | [`_session.py`](../../../server/nodes/browser/_session.py) |
| Private configured login and capture gate | [`_credentials.py`](../../../server/nodes/browser/_credentials.py) |
| Durable owners, task claims and authenticated routing | [`browser_owners.py`](../../../server/services/browser_owners.py), [`_routing.py`](../../../server/nodes/browser/_routing.py) |
| Direct live viewer capture and input | [`_stream.py`](../../../server/nodes/browser/_stream.py), [`_live_control.py`](../../../server/nodes/browser/_live_control.py) |
| Saved-workflow compatibility | [`workflow_migrations.py`](../../../server/services/workflow_migrations.py) |

The node has main input/output handles and a tool output handle. Its `ui_hints.isBrowserPanel` flag makes it discoverable as a browser panel; the agent does not receive that flag. Its logical Activity queue is `BROWSER`, with at most three Temporal attempts and a 35-minute start-to-close timeout for paths that consume plugin policy. New distributed execution overrides the physical queue with the frozen profile owner's queue, even with worker pools disabled. Generic orchestration workers exclude Browser Activities.

## Operations

The agent schema defaults to `snapshot`; saved workflow parameters default to `navigate`.

| Operations | Important inputs and behavior |
| --- | --- |
| `navigate` | Requires `url`; validates destination policy before opening. |
| `snapshot` | Accessibility-tree text with `[eN]` references; `interactive_only` and `max_chars` limit output. Refresh after page changes. |
| `click`, `hover`, `type`, `select` | Target by `ref`, CSS `selector`, or supported coordinates. `type` uses `text`, `clear` (default true), and optional `submit`; `select` uses `values`. |
| `press` | `key` plus CDP modifier bitmask: Alt 1, Ctrl 2, Meta 4, Shift 8. |
| `scroll` | `direction` and `amount` (default 600). |
| `screenshot` | Optional `full_page`; persists an image to the workspace and returns a FileRef. |
| `tabs` | `tab_action`: `list`, `new`, `switch`, or `close`; `url`/`tab_id` as appropriate. |
| `back`, `forward`, `reload` | History/reload operations on the active tab. |
| `page_text`, `page_info` | Extract bounded page text or page information. |
| `wait` | `wait_for`: `load`, `network_idle`, `selector`, `text`, or `time`; optional `wait_value`. |
| `webmcp_list`, `webmcp_call` | Discover page-provided tools or invoke `webmcp_tool` with `webmcp_input` and optional `frame_id`. |
| `request_user` | Human handoff using `reason` and `message`. |
| `credential_bindings` | Discover authorized opaque login binding IDs and nonsecret field/origin metadata. |
| `credential_fill` | Approved `credential_binding_id` with current `username_ref`, `password_ref` and separately selected `submit_ref`; values resolve privately on the owner. |
| `diagnose` | Runtime, host and profile diagnostics without starting Chrome. |
| `evaluate`, `run_python`, `close` | **Workflow-only**: saved `expression`, saved `code`, or stop the profile. Excluded from the agent tool schema and rejected on tool calls. |

Refs resolve against a backend-node-ID map held per target, not against model-provided executable code. The legacy selector spelling `@eN` is accepted as a ref. A missing ref raises an error asking for a fresh snapshot.

## Execution flow

1. Read operator configuration. Agent calls use trusted prepared configuration/resource bindings or reread saved settings. Model arguments cannot override profile, policy, timeouts or executable code. Saved policy and trusted restrictions combine monotonically: Ask first or a newly saved read-only restriction can tighten access, never broaden it.
2. Reject workflow-only operations from agent calls and operations prohibited by `interaction`. Independently refuse automatic retries of site actions (including navigation and tab changes) when Temporal reports attempt greater than one; the previous action may already have occurred.
3. Resolve the authorized profile: a frozen active-task binding, otherwise saved `profile_id` or the workflow's persistent default. Unsaved local runs have a separate fallback. Distributed execution routes to its permanently registered backend owner; it cannot create a runtime on an orchestration worker or adopt an unavailable owner's profile. Register a session identified by principal, workflow and node, associated with that profile.
4. Handle `close` and `diagnose` without opening a new runtime. Other operations open or reuse the profile runtime, discovering the installed browser and installing the pinned CLI on first use if needed. Testing mode may also install pinned Chrome. The actual selected browser version must be compatible with the saved profile; downgrade errors never delete or replace the profile automatically.
5. Browser Agent tasks hold a task-lifetime profile claim during model reasoning, browser operations and human assistance. Competing tasks fail with `BrowserBusy` before effects. `request_user` enters human handoff; other operations acquire the existing operation lease/lock. Human control retains its separate lease.
6. `credential_bindings` reads authorized metadata without opening a browser or resolving secrets. `credential_fill` uses the protected private CDP flow below. `webmcp_list` reads the native tracker cache; `webmcp_call` invokes native CDP. Other ordinary operations use generated scripts through the isolated pinned browser-use CLI and its persistent daemon.
7. Update active target, URL/title and snapshot refs from the result; invalidate refs on navigation. Map output to the node schema and emit page/session changes. CLI daemon failure permits one retry only for explicit observations, as defined in `_controls.py`. An uncertain site action requires a fresh observation before another action.

The profile is persistent across executions; it is not the old execution-ID-named CLI session. Closing it stops the shared profile runtime, so other views of that profile observe the closure.

## Policy and control

Operator settings include `profile_id`, `interaction` (`full` or `read_only`), `webmcp_mode` (`disabled`, `read_only`, `all`), `allowed_domains`, `allow_private_network`, `op_timeout_s` (default 45, range 5–300), and `request_user_timeout_s` (default 600, range 60–1800). They are server-controlled for tool calls. The `profile_id` dropdown (`browserProfiles` loader) lists only the caller's own profiles; the loader takes the caller from the authenticated request, never from its parameters.

`min_action_interval_ms` (default 1000), `max_actions_per_minute` (30), and
`max_repeat_actions` (3) add operator-controlled action limits. One bounded
in-memory ledger shares origin budgets across profiles in the backend;
repeat counts are per profile/origin. Observation and human input are exempt.
See [Browser controls](../../browser-controls.md) for exact limits and scope.

The current `MUTATING` set is `click`, `type`, `press`, `select`, `back`, `forward`, `reload`, `webmcp_call`, `evaluate`, `run_python` and `credential_fill`. `read_only` rejects this set. It does not prohibit navigation, scrolling, hovering or tab operations. In particular, `webmcp_call` is rejected by `interaction=read_only` before its tool-level read-only metadata is considered; `webmcp_mode=read_only` with full interaction is the setting that admits advertised read-only WebMCP tools.

Empty `allowed_domains` allows public destinations. The managed egress proxy checks destinations and resolved addresses for browser traffic; private-network access is disabled by default. Enabling it does not allow cloud metadata or OpenCompany's own protected ports. See the canonical runtime document for policy details.

Control states are `idle`, `agent`, `awaiting_user`, and `user`. `request_user` includes instructions for login, CAPTCHA, two-factor authentication, confirmation or another manual step. Agent calls wait at most 480 seconds per handoff call; a longer configured pending request can return `still_waiting` and be awaited by another call. Workflow steps can wait for the configured deadline.

Strong recognized site challenges automatically create a CAPTCHA request and
latch `challenge_required`. Timeout, viewer loss, decline, and lease release
do not clear this pause. Only explicit handback from the current controlling
owner clears it; scripts inspect the page again before the next site action.
Ordinary requests retain their existing timeout behavior.

The authenticated live viewer attaches through `/ws/browser` to a saved workflow/node or a profile-login session. Watching does not grant control. Takeover, handback, visibility and disconnect are coordinated with the profile controller. Live mouse/keyboard/navigation commands use a bounded ordered queue and direct CDP, independently of the subprocess CLI path used by node operations. Frame acknowledgements and visibility messages remain responsive while commands run. Handback settles dispatched input and releases held keys/buttons before agent work resumes. See [browser workspace](../../browser_workspace.md) for frame delivery and frontend lifecycle.

## Normal mode

New employee **Web browser** capabilities use the shared Browser AI Agent
recipe and the existing delegation mechanism. The saved Browser tool remains
the employee's viewer resource. Existing employee graphs are not rewritten
just to add this capability. With "Ask me before sending anything" on, trusted
`interaction=read_only` restrictions reach both execution adapters, so the
agent reads pages and hands changes or login to the owner through
`request_user`. The existing `browser_request` summary (`{node_id, reason,
since}`) continues to show Needs you and Help in browser.

## Protected configured login

Website enrollment is described in [1Password credentials](../../onepassword_credentials.md#website-login-bindings).
Before resolution, the owner validates binding scope, exact origin, current
tab/frame/refs, saved policy and task claim. It persists a sensitive-login
latch, confirms CLI daemon suspension and drains/gates viewer captures and
page metadata. Only the private CDP session receives resolved values. It
validates exact field assignment and submits the selected button once.

Snapshots, text, screenshots, vision, WebMCP and ordinary actions stay blocked
until private success checks confirm saved origin/path/cues, absence of
sensitive fields and no reflected password. Failure or uncertain submission
retains the gate, including after owner restart. **Close browser for manual
login** must confirm shutdown before clearing it; reopen and use takeover.
Ask first/read-only, MFA, passkeys and multi-step sign-in use human help.
Agent arguments never contain credential values or 1Password references.

## Output and failures

Outputs can include `operation`, `session_id`, profile name, `url`, `title`, `tab_id`, `snapshot`, `text`, `truncated`, screenshot FileRef, tabs, WebMCP tools/results, handoff result, `data`, and `notice`. Screenshot bytes are not embedded in node output; saving failure produces a notice. `run_python` may return the final 20,000 characters of captured script output.

Policy rejection, stale refs, missing required fields, disallowed operations and failed CLI results surface as node errors. An uncertain mutating attempt asks the caller to inspect a snapshot before trying again. Installation/runtime errors and control contention are distinct from an empty page snapshot.

## Legacy workflows

Saved-workflow normalization and the parameter model's compatibility validator cover older representations, including deployed snapshots. The retired node type `browserHarness` becomes `browser`; if this creates two same-named browser tools attached to one agent, migration keeps the original browser tool edge and drops the duplicate migrated edge with a warning.

| Legacy input | Current mapping |
| --- | --- |
| `browserHarness`: `goto`, `js`, `doctor` | `navigate`, `evaluate`, `diagnose` |
| `browserHarness`: `tabs` | `tabs` with `tab_action=list` |
| `browserHarness`: `run_python` | Code retained with a warning to check changed helper names. |
| `fill` | `type`, `clear=true`, text from legacy `value` or `text`. |
| Legacy `type` | Defaults `clear=false` when unspecified. |
| `get_text`, `get_html`, `eval` | `page_text`, `evaluate` with an HTML expression, `evaluate`. |
| `wait` with selector | `wait_for=selector`, `wait_value` from selector when mode was absent. |
| `select` with `value` | Single-item `values` when absent. |
| `console`, `errors` | `page_info`, with a warning. |
| `batch` | `run_python` containing the original commands as comments and an explicit error requiring a rewrite. |
| Numeric `timeout` | `op_timeout_s`, clamped to 5–300 when no replacement exists. |

Old agent-browser runtime settings such as `session`, `headed`, `executable_path`, `chrome_profile`, `proxy`, `user_agent`, `auto_connect` and `action_delay` are removed by migration. Nonempty profile/proxy/executable/user-agent settings produce warnings. The harness-specific branch retains other fields but they do not restore the former external-Chrome attachment behavior. Review migrated executable operations: `evaluate` and `run_python` are now workflow-only.
