# Browser Harness (browser-use/browser-harness)

Wave 19 integration of [browser-use/browser-harness](https://github.com/browser-use/browser-harness) — a minimal (~1,000 LoC, MIT, **alpha**) Python CDP bridge that drives the user's **real Chrome** with no Playwright and no bundled Chromium. Sibling of the `browser` node (agent-browser), **not** a replacement.

## Architecture

```
browserHarness node ──> BrowserHarnessService.run_code(code)
                              │  (subprocess: pipe Python to CLI stdin; CLI exits after exec)
                              ▼
                        browser-harness CLI ──IPC──> daemon (detached, persists)
                              │                        │ AF_UNIX (POSIX) / token-auth TCP loopback (Windows)
                              │                        ▼
                              │                  one CDP WebSocket
                              ▼                        ▼
                        stdout (result)           user's Chrome
```

- **Plugin**: [`server/nodes/browser/browser_harness/`](../server/nodes/browser/browser_harness/) — `_install.py` (uv tool install → `<DATA_DIR>/packages/browser-harness/{tools,bin}`, lazy on first use), `_service.py` (subprocess wrapper + `NodeUserError` mapping + daemon shutdown hook), `__init__.py` (`BrowserHarnessNode`).
- **Node ops**: `run_python` (primary — Python piped verbatim to the CLI, executed against ~25 pre-imported helpers), plus `goto` / `screenshot` / `js` / `tabs` shortcuts and `doctor` diagnostics. `task_queue=BROWSER`, `usable_as_tool=True`, `tool_name="browser_harness"`.
- **Skill**: [`server/skills/web_agent/browser-harness-skill/SKILL.md`](../server/skills/web_agent/browser-harness-skill/SKILL.md) — the see→act→verify loop (`capture_screenshot` → `click_at_xy` → `wait_for_load` → `js` fallback), helper reference, print-JSON-last convention.
- **State isolation**: `BH_RUNTIME_DIR` + `BH_TMP_DIR` are pinned to `<DATA_DIR>/daemons/browser-harness/` on every spawn — daemon pid/port/sock + screenshots + logs never land in the user-global `~/.config/browser-harness/`.
- **Windows**: verified working (2026-07-10 spike) — upstream `_ipc.py` uses AF_UNIX on POSIX and **token-authenticated TCP loopback on Windows**; daemon detaches with `CREATE_NO_WINDOW`.

## Chrome prerequisite

The harness needs a CDP-reachable Chrome, discovered in this order (upstream `daemon.py`):
1. `BU_CDP_URL` / `BU_CDP_WS` env override (dedicated automation Chrome) — passed through from the OpenCompany process env untouched.
2. `DevToolsActivePort` written by Chrome when the user enables **chrome://inspect/#remote-debugging** ("Allow remote debugging for this browser instance").
3. Port probe on 9222/9223 (e.g. `chrome --remote-debugging-port=9222`).

The node's `doctor` operation runs `browser-harness doctor` and returns the full checklist — the first thing to try when calls fail with connection guidance.

## vs the `browser` node (agent-browser)

| | `browser` (agent-browser) | `browserHarness` |
|---|---|---|
| Engine | npm CLI + bundled Chrome-for-Testing (or system browser) | user's real Chrome over raw CDP |
| Interaction | accessibility tree, `@eN` refs | screenshots + coordinate clicks + `js()` |
| Sessions | named sessions, instance cap, idle timeout | one shared browser (daemon-held CDP socket) |
| Agent surface | 15 structured operations | freeform Python against helpers |
| Maturity | stable, Windows-tested | **alpha (v0.1.x)** |

Default to `browser`; reach for `browserHarness` when the task needs the user's logins/profile, bot-hostile or shadow-DOM-heavy sites, iframes (`iframe_target`), or raw CDP.

## Security caveats (know before enabling widely)

- Upstream is **alpha**; the HN launch thread reported an unaddressed RCE and prompt-injection exposure. The `run_python` op executes agent-authored Python **outside** the Monty sandbox (same trust level as `pythonExecutor`, but with the user's real browser attached).
- It drives the user's real, logged-in Chrome — the skill instructs agents to never touch accounts/purchases beyond the explicit task, but treat the node as `destructive + open_world` (its annotations say so).
- Node is visible in **dev mode only** (not in `enabled_nodes` — same posture as `browser`).

## Ops notes

- Install is lazy: first use runs `uv tool install --python 3.12 browser-harness` (requires `uv` on PATH — already a OpenCompany toolchain dependency).
- Daemon shutdown: FastAPI lifespan runs the `browser_harness` shutdown hook → kills the daemon via `<DATA_DIR>/daemons/browser-harness/bu.pid`.
- Upgrades: delete `<DATA_DIR>/packages/browser-harness/` and let the next use reinstall, or run the CLI's `--update`.
