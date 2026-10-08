# Browser controls and product research

Reviewed 2026-09-28. This complements [browser.md](browser.md) and
[browser_workspace.md](browser_workspace.md).

## Decision

Keep the existing local Chrome, dedicated profiles, browser-use CLI and
Workspace viewer. Enforce operation limits and human handoff in the backend.
Use established interaction patterns: inspect the page, wait for readiness,
perform one action, inspect its outcome. Random delays are not a substitute
for readiness checks and do not establish that a site will accept automation.

The running system browser was verified to expose `HeadlessChrome` in its
user agent and `navigator.webdriver=true`. Installing a genuine Chrome
binary does not remove those properties. This change does not alter launch
mode, spoof browser properties, or promise that Google will stop challenging
automated searches.

## Products considered

| Product | Established pattern | Fit here |
| --- | --- | --- |
| [Playwright](https://playwright.dev/python/docs/actionability) | Locator actions wait for visibility, stability, enabled state and event reception. | Strong candidate if replacing the interaction driver. Python fits the server; [CDP attachment has lower fidelity](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp), so adopting it needs integration testing. |
| [Puppeteer](https://pptr.dev/guides/page-interactions) | Locator-based interaction with readiness checks. | Comparable Chromium controls, but would add a JavaScript service to the current Python path. |
| [Selenium](https://www.selenium.dev/documentation/webdriver/actions_api/) | Standardized pointer, keyboard and wheel actions. | Useful for a future cross-browser requirement; does not itself solve destination-site challenges. |
| [Browser Use CLI](https://docs.browser-use.com/open-source/browser-use-cli) | Browser Harness with an existing browser/CDP session or a cloud browser. | The current pinned CLI uses this supported path. Python `Browser` configuration is a different integration surface, not automatically applied to the CLI. |
| [Stagehand v4](https://docs.stagehand.dev/v4/basics/observe) | Observe, validate and act through a structured browser interface. | Useful separation of observation and action; migration would also change its extension/runtime integration. |
| [Browserbase](https://docs.browserbase.com/platform/browser/core-features/contexts) | Persistent contexts and [live human control](https://docs.browserbase.com/platform/browser/observability/session-live-view). | Relevant for managed cloud operation, with cost, data-boundary and lifecycle tradeoffs. No cloud service is required by these changes. |

Playwright recommends its ordinary input APIs, with
[sequential key events](https://playwright.dev/python/docs/input) when an
application specifically requires them. It discourages fixed sleeps as a
test-readiness mechanism. The local implementation improves readiness and
failure handling without adding random pointer paths or timing claims.

Chrome also recommends a
[separate data directory for remote debugging](https://developer.chrome.com/blog/remote-debugging-port).
The existing managed profiles preserve that separation. The
[Playwright extension](https://github.com/microsoft/playwright/tree/main/packages/extension)
is an alternative for explicit access to an existing browser session, but
would require a separate permissions and network-enforcement design here.

## Implemented controls

The Browser node exposes these saved operator settings through the existing
schema-driven parameter panel. Model arguments cannot change them:

| Setting | Default | Meaning |
| --- | --- | --- |
| `min_action_interval_ms` | `1000` | Minimum interval between agent actions for one origin. Zero disables only this interval. |
| `max_actions_per_minute` | `30` | Rolling 60-second action budget for one origin, shared by profiles in this backend process. |
| `max_repeat_actions` | `3` | Identical action limit per profile/origin in 60 seconds. Irrelevant tool fields do not change the action fingerprint. |

Origins include scheme, hostname and port. Limits count tool operations,
not every subresource request or every operation inside operator-authored
`run_python`/`evaluate` code. They are process-local, not a distributed quota
across multiple backend machines. Restarting the backend resets the ledger.
Only hashes and timestamps are retained by this bounded ledger; typed text
and complete URLs are not stored in it. Observation calls and human input
do not consume the agent action budget.

Existing `interaction`, WebMCP permissions, destination allowlists, private
network restrictions and profile ownership remain authoritative. Literal IP
destinations now obey the allowlist too. Read-only interaction still requires
human help for clicks, typing and site changes; these changes do not infer
business-action approval from button text.

Ask first restrictions from the trusted tool adapter tighten saved full access
to read-only in both execution adapters. A model cannot undo that restriction,
nor can prepared native task parameters broaden a newly saved read-only policy.
Browser AI Agent tasks hold their profile claim during reasoning and human
assistance; a competing task receives `BrowserBusy` before an effect. Cleanup
is token matched and confirms daemon suspension before releasing the claim.

`credential_fill` is a mutating site action subject to these same policies,
budgets and uncertain-attempt refusal. It supports only an approved configured
username/password form with fresh element references and a separate submit
target. It gates CLI, viewer and WebMCP observations before secret resolution.
Targets are checked again after desktop authorization and before submission.
Uncertain outcomes keep capture paused and require close/reopen plus manual
login; the agent cannot replay the fill. `credential_bindings` only exposes
permitted opaque IDs and nonsecret login metadata.

### Challenge handoff

Generated scripts check for strong Google `/sorry` and corroborated
Cloudflare interstitial signals before site actions and after operations.
A normal CAPTCHA widget or text discussing bot detection is insufficient.
This is deliberately a narrow detector, not a complete catalogue of every
site's login, CAPTCHA or denial mechanism. Agents must still request help
when they encounter an unsupported challenge.

Detection sets a sticky `challenge_required` pause and creates the existing
user request in the same session. The viewer displays the request. Deadline
expiry, blur, hiding/closing the viewer, idle handback, decline, and workflow
lease release do not resolve the pause. The current owner must use **Hand
back and resume**. If the challenge remains, the next guarded operation
pauses again. Stopping/recreating the browser does not bypass page checks.

### Uncertain results and retries

Permission and retry safety are separate. Navigation and tab changes may be
allowed in read-only mode, but their uncertain outcomes cannot be
automatically replayed. Only explicit observation operations receive the
CLI's one daemon-recovery retry.

Timeout/interruption stops the CLI and its daemon. It cannot undo commands
Chrome already received. An uncertain action returns `outcome_unknown` and
blocks further actions until a fresh successful snapshot/page observation.
The agent must assess that observation before deciding what remains to do.
Navigation-load timeout is no longer silently reported as success. A failed
requested-tab switch cannot fall through to an action in another tab.

Structured errors include `error_type`, `retry_after` where relevant, and
`next_action`. `rate_limited` means wait for the returned interval;
`repeat_limit` means inspect the page or request help; `challenge_required`
means request human control. These details survive the agent-tool wrapper.
WebMCP invocation also checks the current page for a challenge and respects
the configured invocation timeout.

## Limits and further evaluation

[Google's unusual-traffic guidance](https://support.google.com/websearch/answer/86640?hl=en)
includes automated searches and shared network traffic. Neither a driver
change nor human-like input establishes that Google will accept a session.
Network reputation and Google’s private decision rules were not measured.

Future work can observe HTTP `429`/`Retry-After` responses and apply
server-directed cooldowns, following [RFC 6585](https://www.rfc-editor.org/rfc/rfc6585.html#section-4)
and [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3).
The current limits are operator budgets, not an implementation of those
response-driven cooldowns. A driver replacement should be tested against
local fixture pages for frames, downloads, navigation, disconnection,
target changes and user takeover before adoption.
