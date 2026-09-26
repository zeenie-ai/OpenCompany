# Browser (`browser`)

The Browser node launches installed Chrome/Edge/Chromium in a dedicated OpenCompany profile, rendered headless in the workspace UI by default, and exposes that profile to the live browser workspace. `BROWSER_RUNTIME=testing` explicitly selects Chrome for Testing; `BROWSER_HEADLESS=false` additionally opens a desktop window. It never attaches to a personal browser profile. It runs as a workflow step or as the agent tool named `browser`. The old `agent-browser` integration and separate `browserHarness` node have been replaced.

See [native browser architecture](../../browser.md) for runtime installation, profile storage, networking and lifecycle, and [browser workspace](../../browser_workspace.md) for the viewer and UI protocol.

## Implementation map

| Responsibility | Source |
| --- | --- |
| Node schema, dispatch and output mapping | [`BrowserNode`](../../../server/nodes/browser/browser/__init__.py) |
| Plugin registration and shutdown | [`nodes/browser/__init__.py`](../../../server/nodes/browser/__init__.py) |
| Profile runtime and managed Chrome | [`_runtime.py`](../../../server/nodes/browser/_runtime.py), [`_chrome.py`](../../../server/nodes/browser/_chrome.py) |
| Agent/workflow CLI calls and generated scripts | [`_cli.py`](../../../server/nodes/browser/_cli.py), [`_scripts.py`](../../../server/nodes/browser/_scripts.py) |
| Leases and control state | [`_session.py`](../../../server/nodes/browser/_session.py) |
| Direct live viewer capture and input | [`_stream.py`](../../../server/nodes/browser/_stream.py), [`_live_control.py`](../../../server/nodes/browser/_live_control.py) |
| Saved-workflow compatibility | [`workflow_migrations.py`](../../../server/services/workflow_migrations.py) |

The node has main input/output handles and a tool output handle. Its `ui_hints.isBrowserPanel` flag makes it discoverable as a browser panel. Activities use the `BROWSER` task queue, at most three Temporal attempts, and a 35-minute start-to-close timeout to accommodate human handoff.

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
| `diagnose` | Runtime, host and profile diagnostics without starting Chrome. |
| `evaluate`, `run_python`, `close` | **Workflow-only**: saved `expression`, saved `code`, or stop the profile. Excluded from the agent tool schema and rejected on tool calls. |

Refs resolve against a backend-node-ID map held per target, not against model-provided executable code. The legacy selector spelling `@eN` is accepted as a ref. A missing ref raises an error asking for a fresh snapshot.

## Execution flow

1. Read operator configuration. For agent calls, reread the saved node settings so tool arguments cannot override the profile, policy, timeouts or executable code.
2. Reject workflow-only operations from agent calls and operations prohibited by `interaction`. Refuse automatic retries of operations in the node's `MUTATING` set when Temporal reports attempt greater than one; the previous action may already have occurred.
3. Resolve the owner's profile: explicit `profile_id`, otherwise the workflow's persistent default profile. Unsaved runs have a separate fallback. Register a session identified by owner, workflow and node, associated with that profile.
4. Handle `close` and `diagnose` without opening a new runtime. Other operations open or reuse the profile runtime, discovering the installed browser and installing the pinned CLI on first use if needed. Testing mode may also install pinned Chrome. The actual selected browser version must be compatible with the saved profile; downgrade errors never delete or replace the profile automatically.
5. `request_user` enters the human-handoff flow. Other operations acquire the profile's agent-operation lease/lock; human control blocks agent work.
6. `webmcp_list` reads the native tracker's tool cache; `webmcp_call` invokes through the native CDP integration. Remaining operations use a generated Python script sent to the isolated browser-use CLI, whose daemon persists across calls and connects to managed Chrome.
7. Update active target, URL/title and snapshot refs from the result. Map output to the node schema and emit page/session changes. CLI daemon failure permits one retry for operations outside `MUTATING`; operations inside that set are not retried by this wrapper.

The profile is persistent across executions; it is not the old execution-ID-named CLI session. Closing it stops the shared profile runtime, so other views of that profile observe the closure.

## Policy and control

Operator settings include `profile_id`, `interaction` (`full` or `read_only`), `webmcp_mode` (`disabled`, `read_only`, `all`), `allowed_domains`, `allow_private_network`, `op_timeout_s` (default 45, range 5–300), and `request_user_timeout_s` (default 600, range 60–1800). They are server-controlled for tool calls. The `profile_id` dropdown (`browserProfiles` loader) lists only the caller's own profiles; the loader takes the caller from the authenticated request, never from its parameters.

The current `MUTATING` set is exactly `click`, `type`, `press`, `select`, `back`, `forward`, `reload`, `webmcp_call`, `evaluate`, and `run_python`. `read_only` rejects this set. It does not prohibit navigation, scrolling, hovering or tab operations. In particular, `webmcp_call` is rejected by `interaction=read_only` before its tool-level read-only metadata is considered; `webmcp_mode=read_only` with full interaction is the setting that admits advertised read-only WebMCP tools.

Empty `allowed_domains` allows public destinations. The managed egress proxy checks destinations and resolved addresses for browser traffic; private-network access is disabled by default. Enabling it does not allow cloud metadata or OpenCompany's own protected ports. See the canonical runtime document for policy details.

Control states are `idle`, `agent`, `awaiting_user`, and `user`. `request_user` includes instructions for login, CAPTCHA, two-factor authentication, confirmation or another manual step. Agent calls wait at most 480 seconds per handoff call; a longer configured pending request can return `still_waiting` and be awaited by another call. Workflow steps can wait for the configured deadline.

The authenticated live viewer attaches through `/ws/browser` to a saved workflow/node or a profile-login session. Watching does not grant control. Takeover, handback, visibility and disconnect are coordinated with the profile controller. Live mouse/keyboard/navigation commands use a bounded ordered queue and direct CDP, independently of the subprocess CLI path used by node operations. Frame acknowledgements and visibility messages remain responsive while commands run. Handback settles dispatched input and releases held keys/buttons before agent work resumes. See [browser workspace](../../browser_workspace.md) for frame delivery and frontend lifecycle.

## Normal mode

Hire adds this node through the **Web browser** app (`web` in `config/employee_apps.json`, recorded under the employee's `browser` role). With "Ask me before sending anything" on, it is attached with `interaction=read_only`, so the agent reads pages and hands any change to the owner through `request_user`. While the agent waits there, the employee summary's `browser_request` (`{node_id, reason, since}`) makes Home show Needs you and Help in browser; the agent's message itself stays in the live viewer.

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
