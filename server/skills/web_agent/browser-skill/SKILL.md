---
name: browser-skill
description: Interactive browser automation - navigate, click, type, fill forms, take screenshots, get accessibility snapshots. Supports system Chrome/Edge via auto-detection.
allowed-tools: browser
metadata:
  author: machina
  version: "2.0"
  category: web

---

# Browser Automation Skill

## Core Workflow

Use the **snapshot -> act -> snapshot** loop:

1. `navigate` to a URL
2. `snapshot` to see interactive elements (returns `@eN` refs)
3. `click` / `type` / `fill` / `select` using `@eN` refs as selectors
4. `snapshot` again to see the result
5. Repeat until task is complete

## browser Tool

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | One of: navigate, click, type, fill, screenshot, snapshot, get_text, get_html, eval, wait, scroll, select, console, errors |
| url | string | navigate | URL to open |
| selector | string | click/type/fill/get_text/get_html/wait/select | CSS selector or `@eN` ref from snapshot |
| text | string | type | Text to type keystroke by keystroke |
| value | string | fill/select | Value to fill or dropdown option to select |
| expression | string | eval | JavaScript to execute in page context |
| direction | string | scroll | up, down, left, right (default: down) |
| amount | int | scroll | Pixels to scroll (default: 500) |
| fullPage | bool | screenshot | Capture full scrollable page (default: false) |
| annotate | bool | screenshot | Add numbered labels to elements (default: false) |
| screenshotFormat | string | screenshot | Image format: png (default) or jpeg |
| screenshotQuality | int | screenshot | JPEG quality 1-100 (default: 80, only for jpeg) |

## Operations

### navigate
Open a URL in the browser.
```json
{"operation": "navigate", "url": "https://example.com"}
```

### snapshot
Get the accessibility tree with `@eN` element refs. This is the primary way to see what is on the page.
```json
{"operation": "snapshot"}
```
Returns interactive elements like:
```
- heading "Example Domain" [ref=@e1]
- link "More information..." [ref=@e2]
- textbox "Search" [ref=@e3]
```

### click
Click an element using its `@eN` ref or CSS selector.
```json
{"operation": "click", "selector": "@e2"}
```

### type
Type text into an element keystroke by keystroke.
```json
{"operation": "type", "selector": "@e3", "text": "search query"}
```

### fill
Clear an input field and fill it with a value.
```json
{"operation": "fill", "selector": "@e3", "value": "new value"}
```

### screenshot
Take a screenshot of the current page.
```json
{"operation": "screenshot", "fullPage": true}
```

**Annotated screenshot** (numbered labels on interactive elements -- best for AI vision):
```json
{"operation": "screenshot", "annotate": true}
```

**JPEG format** (smaller file size):
```json
{"operation": "screenshot", "screenshotFormat": "jpeg", "screenshotQuality": 80}
```

### get_text
Extract text content from an element.
```json
{"operation": "get_text", "selector": "@e1"}
```

### eval
Execute JavaScript in the page context.
```json
{"operation": "eval", "expression": "document.title"}
```

### wait
Wait for an element to appear on the page.
```json
{"operation": "wait", "selector": "#results"}
```

### scroll
Scroll the page.
```json
{"operation": "scroll", "direction": "down", "amount": 500}
```

### select
Select a dropdown option.
```json
{"operation": "select", "selector": "@e5", "value": "option-value"}
```

### console
Get browser console output (log, warn, error messages).
```json
{"operation": "console"}
```
Returns `{"messages": [{"text": "hello", "type": "log"}, ...]}`.

### errors
Get JavaScript errors from the page.
```json
{"operation": "errors"}
```
Returns `{"errors": [...]}`.

## Using Your Real Browser

By default the browser node uses a bundled Chromium. To use your system browser (with existing logins, extensions, etc.), select it in the **Browser** dropdown under Advanced:

| Option | Description |
|--------|-------------|
| **Bundled Chrome** | Default. Downloads and uses its own Chromium. |
| **Google Chrome** | Auto-detected from system PATH or Windows registry. |
| **Microsoft Edge** | Auto-detected from system PATH or Windows registry. |
| **Chromium** | Auto-detected from system PATH or Windows registry. |
| **Custom Path** | Manually specify an executable path. |

Browser detection uses `shutil.which()` on Linux/macOS (PATH lookup) and the Windows App Paths registry (`HKLM\...\App Paths\chrome.exe`) -- the same method Selenium and Playwright use. No hardcoded paths.

### Additional options

- **New Window**: Opens a new browser window instead of a tab in an existing instance. On by default when using a system browser. Only visible for non-bundled browsers.
- **Chrome Profile**: Reuse login state from a named Chrome profile (e.g. `Default`, `Profile 1`).
- **Auto Connect**: Attach to an already-running Chrome with remote debugging:
  ```
  chrome --remote-debugging-port=9222
  ```

### Lifecycle

The browser daemon auto-starts on first use and persists between commands (for session reuse). It is automatically shut down when MachinaOs stops -- no manual cleanup needed.

### Sessions

Each distinct session name maps to ONE browser instance. When the `session` field is left empty (the normal case), it auto-derives as `machina_<execution_id>` -- stable for the whole workflow/agent run, including delegated sub-agents -- so every browser call in a run reuses the same browser window and keeps its cookies, tabs, and login state.

- **Leave `session` empty.** Do not invent a session name per call; the auto-derived session already chains your calls onto one browser.
- Set `session` explicitly only to persist state across separate runs (e.g. `my_login_session`) or to isolate parallel flows within one run.
- Concurrent instances are capped by `BROWSER_MAX_INSTANCES` (default 3); when a new session would exceed the cap, the oldest active session is closed first. Idle browsers auto-close after `BROWSER_IDLE_TIMEOUT_MS` (default 10 minutes) without commands.

## Stealth / Anti-Detection

These settings reduce bot detection. Configure them in the node's Advanced section, not as tool arguments.

- **Action Delay**: Native wait (ms) before each action. Set 500-2000ms for bot-protected sites.
- **User Agent**: Custom user-agent string to override Chrome default.
- **Proxy**: Route all browser traffic through a proxy (e.g. `http://user:pass@host:port`).

## Tips

- Always `snapshot` first to discover `@eN` element refs before interacting.
- Prefer `@eN` refs over CSS selectors -- they are stable across the session.
- Use `fill` for form inputs (clears first), `type` for search boxes (keystroke events).
- Use `screenshot` to visually verify page state when uncertain.
- Use `wait` before interacting with dynamically loaded elements.
- Use `eval` sparingly -- prefer snapshot + click/fill for most tasks.
- Select **Google Chrome** or **Microsoft Edge** in the Browser dropdown to use your real browser with existing logins.
