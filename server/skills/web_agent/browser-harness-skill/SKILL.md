---
name: browser-harness-skill
description: Drive the user's real Chrome over raw CDP by writing Python against browser-harness helpers - screenshot, coordinate clicks, JS evaluation, form fill, tabs. For tasks needing full freedom or the user's own logins.
allowed-tools: browserHarness
metadata:
  author: machina
  version: "1.0"
  category: web

---

# Browser Harness Skill

Controls the user's **real Chrome** through raw CDP (browser-use/browser-harness). Unlike the `browser` tool (accessibility tree + `@eN` refs), this tool is **vision-first**: you look at screenshots and click coordinates, with `js()` for reading the DOM. Use it when you need the user's logged-in browser, sites that fight automation frameworks, or interactions the structured tool can't express.

## Core Workflow

The **see -> act -> verify** loop, written as Python in `operation: run_python`:

1. `capture_screenshot()` to SEE the page (the returned path is an image you can view)
2. `click_at_xy(x, y)` at coordinates you identified from the screenshot
3. `wait_for_load()` after anything that navigates
4. `capture_screenshot()` again to verify the result
5. `js(...)` when you need exact text/attributes instead of pixels

```json
{"operation": "run_python", "code": "ensure_real_tab()\ngoto_url('https://example.com')\nwait_for_load()\nprint(page_info())"}
```

## browserHarness Tool

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | run_python (primary), goto, screenshot, js, tabs, doctor |
| code | string | run_python | Python executed with helpers pre-imported. Print a JSON object as the LAST line for structured output |
| url | string | goto | URL to open |
| expression | string | js | JavaScript to evaluate in the page |
| full_page | bool | screenshot | Capture the full scrollable page (default: false) |
| timeout | int | no | Script timeout in seconds (default: 60) |

## Helper Reference (inside run_python)

All pre-imported — never `import` them.

**Navigation & tabs**
- `goto_url(url)` — navigate current tab
- `new_tab(url="about:blank")` / `close_tab(target=None)` / `switch_tab(target)`
- `list_tabs(include_chrome=True)` — enumerate open tabs
- `current_tab()` / `ensure_real_tab()` — make sure a real page tab is focused (call first!)
- `page_info()` — `{url, title, w, h, sx, sy, pw, ph}` (viewport + scroll + page size)
- `iframe_target(url_substr)` — target for js/interaction inside an iframe

**Seeing**
- `capture_screenshot(path=None, full=False, max_dim=None)` — returns the saved PNG path. Use `max_dim=1200` to keep images small

**Acting**
- `click_at_xy(x, y, button="left", clicks=1)` — coordinates from the screenshot
- `type_text(text)` — types into the focused element (click the field first)
- `fill_input(selector, text, clear_first=True)` — CSS-selector form fill
- `press_key(key, modifiers=0)` / `dispatch_key(selector, key="Enter")`
- `scroll(x, y, dy=-300, dx=0)` — scroll at a point (negative dy = down varies; verify with page_info)
- `upload_file(selector, path)`

**Reading & escape hatches**
- `js(expression, target_id=None)` — evaluate JS, returns the value. Your precision tool for text, attributes, element rects
- `cdp(method, session_id=None, **params)` — raw CDP call when nothing else fits
- `http_get(...)` — plain HTTP fetch without the browser

**Waiting**
- `wait_for_load(timeout=15.0)` — after navigation/clicks that navigate
- `wait_for_element(selector, ...)` / `wait_for_network_idle(...)` / `wait()`

## Patterns

**Find something on a page** (screenshot first, then read precisely):
```python
ensure_real_tab()
goto_url("https://news.ycombinator.com")
wait_for_load()
titles = js("[...document.querySelectorAll('.titleline > a')].slice(0,5).map(a => a.textContent)")
import json  # stdlib imports are fine; helpers are pre-imported
print(json.dumps({"top5": titles}))
```

**Click something you can see** (get coordinates from the element itself, not by eye):
```python
r = js("(() => { const el = [...document.querySelectorAll('a')].find(a => a.textContent.includes('More')); const b = el.getBoundingClientRect(); return {x: b.x + b.width/2, y: b.y + b.height/2}; })()")
click_at_xy(r["x"], r["y"])
wait_for_load()
print(page_info())
```

**Fill a login/search form**:
```python
fill_input("input[name=q]", "machina os")
dispatch_key("input[name=q]", "Enter")
wait_for_load()
print(page_info())
```

## Rules

- **Always start with `ensure_real_tab()`** in the first call of a session — the daemon may be attached to a chrome:// or extension target.
- **Print a JSON object as the final line** — the tool parses it into `result` for you; everything else lands in `output`.
- **Prefer `js()`-computed coordinates** (getBoundingClientRect) over guessing pixels from a screenshot; screenshots confirm state, JS locates precisely.
- **One logical step per call.** Short scripts fail cleanly; long ones lose all progress on any exception.
- Screenshots return a **file path** under the harness tmp dir — mention the path in your reply if the user should look at it.
- If a call fails with a Chrome-connection error, run `{"operation": "doctor"}` and relay its report: the user may need to start Chrome with `--remote-debugging-port=9222` or allow remote debugging via chrome://inspect.
- The harness drives **one shared browser** — do not assume parallel calls get isolated windows. State (cookies, logins, open tabs) persists across calls by design.
- This is the user's REAL browser: never touch logged-in accounts, submit forms, or make purchases beyond what the task explicitly asks.

## When to use `browser` instead

Prefer the structured `browser` tool when the site is simple and the accessibility tree works — its `@eN` snapshot loop is cheaper (no screenshots) and more deterministic. Come to `browserHarness` for: the user's own Chrome profile/logins, canvas/shadow-DOM-heavy or bot-hostile sites, iframes needing `iframe_target`, or anything requiring raw CDP.
