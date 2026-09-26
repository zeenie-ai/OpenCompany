# Normal mode ("Home")

Normal mode is the landing screen for owners who are not technical. They
describe a job in plain words, an LLM on the server drafts a setup screen for
a new AI employee, they adjust it and press **Hire**, and the server builds,
saves and starts a workflow that does the job. Each employee appears in a
sidebar with a live status card. The workflow editor is **Dev mode**, one
switch away.

**One employee is one workflow.** The workflow id is the employee id and the
workflow's name is the employee's name. Workflows created in the editor show
up in Home too, with details derived from their graph.

| Layer | Where |
|---|---|
| Shell (screen switch, mode toggle, theme rule) | [client/src/app/](../client/src/app/), [components/shell/ModeToggle.tsx](../client/src/components/shell/ModeToggle.tsx) |
| Home UI | [client/src/features/home/](../client/src/features/home/) |
| Setup-screen pipeline (parse, normalise, render) | [client/src/features/home/genui/](../client/src/features/home/genui/) |
| Employees service (list, setup, hire, start, run records) | [server/services/employees/](../server/services/employees/) |
| Approvals (the "ask me first" step) | [server/services/approvals/](../server/services/approvals/), node [approvalGate](./node-logic-flows/workflow_triggers/approvalGate.md) |
| Built-in skills offered to new hires (Settings > Skills > Discover) | [server/skills/employee/](../server/skills/employee/) |

The design reference is the `design_handoff_opencompany_home/` bundle (kept
out of git; the 2026-09-25 export). Where its token values differ from the
repo's, the repo wins. Its README's Settings section predates the Settings
redesign, so for Settings the prototype (`reference/OpenCompany Home.dc.html`)
is the reference.

## Switching screens

- **Flag**: `featureFlags.normalMode` ([lib/featureFlags.ts](../client/src/lib/featureFlags.ts))
  is on by default; `VITE_NORMAL_MODE=false` turns Home off. The app then
  opens straight into the editor, and the toolbar's Normal/Dev switch goes back
  to filtering the component palette.
- **State**: `useAppStore.shellMode` (`'normal' | 'dev'`), persisted as
  `ui_shell_mode`. While that key is unset it comes from the old palette
  choice: `ui_pro_mode === 'true'` starts in Dev.
- **Switching** goes through `enterNormal` / `enterDev` in
  [app/useShellActions.ts](../client/src/app/useShellActions.ts), never
  `setShellMode` directly. `enterDev({workflowId})` settles unsaved editor work
  first (saves it when auto-save is on, asks otherwise), opens the workflow and
  preloads the editor chunk; `enterNormal` preloads Home. Then
  [app/shellTransition.ts](../client/src/app/shellTransition.ts) fades the
  current screen out and swaps. Requests during a switch coalesce. The toggle
  lives in the editor toolbar and the Home header; Ctrl/Cmd+Shift+D toggles too.
- **Screens are lazy chunks** ([app/ShellModeSwitch.tsx](../client/src/app/ShellModeSwitch.tsx)),
  so ReactFlow stays out of a Normal-mode session and Home out of a Dev one.
- **App-level concerns** live in [app/AppShell.tsx](../client/src/app/AppShell.tsx)
  rather than in `Dashboard`: the `.app-frame`, boot effects (current workflow,
  sound sync, page activity, UI defaults, the shortcut), and the Settings and
  Credentials dialogs.
- **Themes**: Home shows only light and dark.
  [app/ShellThemeProvider.tsx](../client/src/app/ShellThemeProvider.tsx) passes
  `baseOnly` to `ThemeProvider` while Home is showing, so a stylized theme
  chosen in the editor appears as its family's base (Atomic as light, Cyber as
  dark) and applies again in Dev mode. See [Theme System](./theme_system.md).

## The Home screen

[features/home/](../client/src/features/home/):

| Folder | Contents |
|---|---|
| `HomeShell.tsx` | Sidebar, header, the current view (hire or one employee), the Workspace dock, Settings, the connect dialog, the orb's stage |
| `sidebar/`, `header/` | The team list, New employee, the profile row; the view title, the Workspace pill, mode toggle and theme button |
| `hire/` | The hero, the composer, and the starter bundles (`starters.json`), which the template chips and Settings > Plugins both read |
| `genui/` | The setup draft under the composer (below) |
| `employee/` | One employee's card: status, the current task, apps, "done today", Start / Pause / Resume, Watch live, the drafts waiting for the owner |
| `workspace/` | The Workspace dock (below), its header pill, and its Canvas tab, which loads in its own chunk |
| `settings/` | The Settings pages (Profile, Billing, Skills, Connectors, Plugins), the shared catalog page the last three build on, and `ConnectDialog` (the editor's credential panel for one provider, in its compact variant) |
| `approvals/` | The drafts query, the decide mutation (optimistic), the approval broadcast listener |
| `data/` | zod-parsed queries for employees, connectors and the profile; `presentation.ts` maps server state to pills and actions without deriving new rules |
| `orb/` | The 3D orb (below) |
| `ui/` | Small shared pieces (avatar, status dot and pill, app mark) and the pill toast |
| `state/homeStore.ts` | UI state only: the view, the sidebar, Settings, the Workspace dock, one-shot glow and pulse signals |

Status comes from the server: an employee summary is `working`, `ready`,
`paused` or `attention` (an automatic pause, see below), and a pending draft
shows as "Needs you". So does a browser waiting for the owner: when an agent
calls `request_user`, the summary's `browser_request` (`{node_id, reason,
since}`, never the agent's message, since summaries reach every socket) takes
over the task line, and the card's Watch live button becomes Help in browser,
which opens the Workspace on its Browser tab. The Browser plugin publishes
that state through `services/employees/node_signals.py` and re-sends the
summary when it changes. Motion goes through [lib/motion.ts](../client/src/lib/motion.ts),
which reads the `--dur-*` / `--ease-*` tokens, runs at 1 ms under reduced
motion and while the page is hidden ([lib/pageActivity.ts](../client/src/lib/pageActivity.ts)),
and never starts loops then. Toasts are a second sonner toaster
(`ui/pillToast.tsx`): one bottom-centre pill at a time.

### The orb

A 3D version of the logo drawn behind Home's content
([orb/orbEngine.ts](../client/src/features/home/orb/orbEngine.ts), a port of
the design prototype's scene on current three.js; loaded in its own chunk the
first time it runs). [orb/orb.ts](../client/src/features/home/orb/orb.ts) holds
what the engine reads each frame (the slot to fill, an energy target, a spike)
and its lifecycle: leaving Home keeps the renderer, the app shell disposes it.
`OrbStage` is the canvas host below the header; each view's `OrbSlot` reserves
the square the orb glides into. The composer sets the energy target (focused,
holding text, a setup being written). These spike it: a hire, a theme or mode
switch, a connect, a task change, opening Settings or the Workspace, a setup
arriving or failing, and saving the profile (`SPIKE` in orb.ts). In dark the orb keeps the
logo's colours with white particles and packets; in light it is glossy
piano-black under a white rim light. Its glows and particles blend additively
in dark and normally in light (additive glow vanishes on white), fading through
zero at the midpoint of a theme change. Without WebGL, under reduced motion, or
after a lost WebGL context, the slot shows the static mark.

## The Workspace

A dock on the right of Home ([workspace/WorkspaceDock.tsx](../client/src/features/home/workspace/WorkspaceDock.tsx))
that shows what one employee is working on. The header's Workspace pill
opens and closes it, and carries a blinking dot while it is closed and
someone is working. Watch live on an employee's card opens it on that
employee. It shows the employee last opened or watched, else the first on
the team.

- **Header**: the avatar, "{Name}’s workspace", the live task line
  (`useLiveTask`, else the summary's task), and a pill: Live while the
  employee works, otherwise the card's own pill. Expand and Close.
- **Canvas**: the board named by the summary's `canvas_node_id`, drawn by
  the editor's Canvas renderer (`CanvasContent`, see [Canvas Node](./canvas_node.md)).
  It loads in its own chunk, which keeps the board's markdown, code and
  JSON viewers out of Home's, and refreshes on `canvas_updated` like the
  editor's hosts. An employee without a Canvas gets a note and Open
  workflow.
- **Browser**: the shared `components/browser/BrowserWorkspace` attaches to
  the employee's saved Browser nodes, supplied by `browser_nodes` in its
  summary. Multiple nodes get a selector. Running sessions appear automatically;
  Start browser opens an idle session. The view supports navigation, tabs,
  Take control / Hand back, and browser dialogs. Frames use `/ws/browser`,
  not a URL iframe. See [Browser workspace](./browser_workspace.md).
- **Android**: a shared panel explains that live mirroring is not yet available.
  Dev mode uses the same three workspace tabs and browser viewer.
- **Size and motion**: 460px wide by default. The left edge drags from
  360px to the window less 420px, and Expand gives a bigger dock without
  a drag. At 1100px and wider the dock pushes the page aside; narrower, it
  lies over it with `--shadow-dock`. Like the sidebar it stays mounted and
  transitions its width (`--dur-dock-in` to open, `--dur-sidebar-out` to
  close), so a reload with it open does not animate, and its contents
  mount on the first open. Open, width and tab persist under
  `home_workspace_v1`; Expand and the employee last only for the session.

## Hiring

### 1. The setup screen

`generate_employee_setup {job, refine?, history?, draft_token}`
([services/employees/setup.py](../server/services/employees/setup.py)):

- **The server builds every message** ([setup_prompt.py](../server/services/employees/setup_prompt.py)):
  a preamble, the component catalogue, and a context block
  ([context.py](../server/services/employees/context.py)) with the connected
  and connectable apps, the team's names, the owner profile, the local time and
  whether an AI model is set up. A change request carries the latest reply
  plus at most two earlier turns, within a character budget.
- **Model choice** ([llm.py](../server/services/employees/llm.py)): a local
  provider when the owner prefers local AI, else the global default provider
  when it has a key, else the first usable provider, else the first saved
  endpoint. Calls go through `ChatUnifier` with a deadline (longer for local
  models). One retry when the reply was cut off or cannot be salvaged. Usage is
  recorded for every call.
- **One request in flight per owner**: a newer `draft_token` cancels the older
  call, and `cancel_employee_setup` cancels explicitly. The payload field is
  `draft_token` because `request_id` is the WebSocket correlation id.
- **Privacy**: neither the job nor the reply is ever logged.
- **Errors**: `no_ai_provider` (the composer opens Connectors on AI),
  `timeout`, `provider_error`, `unparseable`, `cancelled`, `busy`,
  `invalid_request`.

The reply is a flat JSON UI spec. The client parses, repairs and normalises it
and renders it from a fixed component catalogue
([genui/](../client/src/features/home/genui/): `catalog.ts`, `parse.ts`,
`normalize.ts`, `expressions.ts`, `render.tsx`). The normaliser keeps the
root a vertical stack, enforces a tree, guarantees one Hire button, one
change button and the "Ask me before sending anything" toggle (on by default),
and caps sizes. The server mirrors the catalogue in
[config/genui_catalog.json](../server/config/genui_catalog.json);
`server/tests/test_genui_catalog_sync.py` keeps the two in step, and a shared
corpus of bad model replies (`genui/__fixtures__/replies.json`) is parsed the
same way on both sides. Only what `genui/index.ts` exports (`HireDraftPanel`,
`useHireComposer`, `DraftMessagePreview`) leaves the folder: ESLint refuses
imports of its internal modules.

### 2. Hire

`hire_employee` ([hire.py](../server/services/employees/hire.py)) takes a
`HireEmployeeRequest` ([hire_request.py](../server/services/employees/hire_request.py);
the client's `HIRE_PAYLOAD_KEYS` must match, locked by
`test_hire_payload_contract.py`) and:

1. checks size and shape, then the idempotency key: the same key with the same
   payload returns the employee already hired, a different payload `conflict`;
2. resolves the apps it names through the app registry
   ([config/employee_apps.json](../server/config/employee_apps.json),
   [apps.py](../server/services/employees/apps.py)): each app's provider,
   its trigger / reply / notify-owner / tool nodes with parameter templates,
   and a `side_effects` class. Apps the registry does not know are recorded as
   unsupported, named in the instructions, and never block a start;
3. builds the graph ([builder.py](../server/services/employees/builder.py),
   pure): one trigger (an app event, a schedule on `cronScheduler`, or manual
   chat), one `aiAgent` whose system message comes from
   [prompt.py](../server/services/employees/prompt.py), always-on tools (web
   search, todos, clock, a Canvas, plus memory when the owner allows it) and
   the apps' tools, the owner's skill library, delivery, and an "Activity
   log" console node. The instructions ask the employee to put finished
   work the owner will want to look at later on its Canvas, and to leave
   routine replies off it. The library is every skill that is on in Settings > Skills. It goes
   on one Skills node (`masterSkill`) with each skill's text copied in, so a
   later change to the library never alters an employee already hired. A
   skill named `skill`, or one ending in `-personality`, is left out: it would
   take over the Skill tool, or replace the whole system message. With "Ask me before
   sending anything" on, a reply goes through `approvalGate`, and tools that
   send or spend money are left off, except one that declares
   `ask_first_params`: the "Web browser" app's browser stays on with
   `interaction: read_only`, so it can read pages and hands any change to the
   owner through `request_user`. The Web browser app has nothing to connect
   (its catalogue entry's `connected_check` is `builtin`); its Connectors
   panel manages optional login profiles. Every node type must pass
   `node_allowlist.is_hire_allowed` ([Node Allowlist](./node_allowlist.md));
   node types never come from the payload;
4. validates it with `validate_workflow` and saves it with
   [persist_new_workflow](../server/services/workflow_storage/persist.py)
   (shared with workflow import);
5. answers `{employee, started, missing_apps, needs_ai, unsupported_apps, warnings, idempotent}`
   and, when every app is connected and an AI model exists, starts the
   employee in the background.

### 3. Start, pause, resume

`start_employee {workflow_id, expected_revision, idempotency_key}`
([start.py](../server/services/employees/start.py)) refuses with
`missing_apps` or `needs_ai`, moves the agent onto a usable model, and calls
`start_saved_workflow`, the same start path the editor uses. Pause and Resume
are the editor's `pause_workflow` / `resume_workflow`.

Automatic pauses now record why: `WorkflowControlExecution.pause_reason`
(`failures` from the circuit breaker, `recovery` after a crash,
`controller_missing`) and `pause_detail`, emitted by `serialize_control` and
cleared on resume. Home shows such an employee as "Needs attention". See
[Temporal Workflow Control](./temporal-workflow-control.md#recovery-policies).

## Asking before sending

With the rule on, a reply waits in an `approvalGate` until the owner decides
on the employee's card. The gate stores the draft in `approval_requests`,
wakes the moment it is decided, survives restarts (its idempotency key finds
the same row on every attempt), expires after `timeout_hours`, and fails
closed: nothing is sent unless it was approved, and the recipient always comes
from the run's trigger. Full contract:
[approvalGate](./node-logic-flows/workflow_triggers/approvalGate.md).

## "Done today"

Every trigger-spawned run that finishes writes a `workflow_run_records` row
([runs.py](../server/services/employees/runs.py)): on Temporal from the
`workflow_runs.record_completion` activity, scheduled at the end of
`MachinaWorkflow.run` behind the `machina-run-record-v1` patch; in-process
from `DeploymentManager`. The count starts at local midnight in the owner's
timezone, and rows are pruned after 35 days, which is longer than a month, so
Billing's count of this month's tasks (from the 1st, in the owner's timezone,
across the whole team) is always complete.

## Settings

A 1040x760 dialog ([settings/HomeSettings.tsx](../client/src/features/home/settings/HomeSettings.tsx))
with a 224px nav. One `PAGES` list drives the nav and the panels. The pages
are grouped under *Settings* (Profile, Billing) and *Customize* (Skills,
Connectors, Plugins), and the nav's search matches each page's label and
keywords, dropping a group with no match. Opening Settings on a category
(`openSettings('connectors', 'ai')`) only sets where the page starts:
changing page clears it.

- **Profile**: `profile_full_name`, `profile_call_name`, `profile_role`,
  `profile_preferences`, `profile_timezone` (written from `Intl` on save),
  `memory_across_chats` and `prefer_local_ai` on `UserSettings`, normalised on
  save by [services/settings/profile.py](../server/services/settings/profile.py).
  Every hire reads them into its instructions.
- **Billing**: usage only. It shows the tasks done this month
  (`get_employee_usage`) and the number of employees in the sidebar. A count
  that can't be read shows a dash, never a zero.
- **Skills**: the owner's library, which is the user-skills table. Each row is
  on (`is_active`) or off for employees hired from now on.
  - *Discover* lists the built-in `server/skills/employee/` folder. These are
    short skills written for AI employees, with no tools to connect, and their
    cards read the SKILL.md `metadata.title` and `metadata.summary`.
  - *Adding* a built-in copies its text into the library under the same name.
  - *Create* writes a new skill in plain words. Its name is the title's slug,
    never a taken, built-in or reserved one.

  The Dev editor's Master Skill panel reads the same library. It no longer
  switches a skill back on when saving it, and it keeps the extra keys a hired
  employee's Skills node carries. `DISCOVER_SKILL_FOLDER` in
  `features/home/data/skills.ts` names the folder.
- **Plugins**: starter bundles, from `features/home/hire/starters.json`, the
  same list the composer's template chips send. A bundle is a job, the apps it
  needs (named in the job, since the job is all the setup model reads), and
  its skills.
  - *Install* adds the bundle's skills to the library, switching on any that
    are off. It then starts a hire from the job on the hire view.
  - A bundle counts as installed when all its skills are in the library;
    nothing else is stored.

  `tests/test_home_catalog_contract.py` holds the bundles and the Discover
  folder to each other and to the app registry.
- **Connectors**: every provider in `config/credential_providers.json`, the
  same set as the editor's Credentials modal. Each declares a
  `consumer_category` (`messages`, `organize`, `business`, `research`,
  `language`, `developer`, `devices`, `ai`), a short `description`, a
  `publisher` (the card's "by …" line) and `verified`, and
  `test_credential_catalogue_consumer_fields.py` fails when one does not, or
  when a plugin credential has no catalogue entry at all, so a new connector
  cannot silently miss the page. They are listed in category order, so apps
  come before AI models. The catalogue adds `connected`, which differs from
  `stored` for providers with a `connected_check` (WhatsApp's live pairing,
  the IMAP/SMTP account's keys).

**The catalog page** ([settings/CatalogLayout.tsx](../client/src/features/home/settings/CatalogLayout.tsx)).
Skills, Connectors and Plugins are built on a shared page:
- a title with Yours / Discover, search, and a category filter behind a
  button;
- an optional primary action that opens an inline form;
- a grid of cards.

The page supplies both lists, so the layout never needs to know which page it
is on:
- Discover shows eight cards until Show all.
- A card's "+" turns into a check, which removes on hover only when the page
  can remove.
- Yours shows a switch only when the page can toggle.
- A card glows when its item turns added while it is on screen.

## Wire contract

WebSocket requests (snake_case; failures come back as `success: false` with an
`error` code):

| Type | Payload | Response |
|---|---|---|
| `list_employees` | `{}` | `{employees}`; each summary's `canvas_node_id` is its Canvas board: the one it was hired with, else the graph's first Canvas node, else null |
| `get_employee` | `{workflow_id}` | the summary plus `description`, `job`, `plan`, `rules`, `choices`, `trigger_text`, `last_run`, `latest_report` |
| `get_employee_usage` | `{}` | `{tasks_this_month}` (successful runs since the 1st, owner's timezone, whole team) |
| `generate_employee_setup` | `{job, refine?, history?, draft_token}` | `{draft_token, reply, provider, model, usage, retried, finish_reason, apps}` |
| `cancel_employee_setup` | `{draft_token}` | `{cancelled}` |
| `hire_employee` | `HireEmployeeRequest` | `{employee, started, missing_apps, needs_ai, unsupported_apps, warnings, idempotent}` |
| `start_employee` | `{workflow_id, expected_revision, idempotency_key}` | as `start_workflow` |
| `list_approvals` | `{workflow_id?, status?, limit <= 100}` | `{approvals, counts, server_time}` |
| `decide_approval` | `{approval_id, decision, text?, subject?, decision_key}` | `{approval, will_send_on_resume}` |

Broadcasts, all CloudEvents envelopes broadcast directly (no Temporal
consumer; see [Event Framework](./event_framework.md#ui-only-lifecycle-events-broadcast-directly-never-through-emit)):

| Wire key | Type | Notes |
|---|---|---|
| `employee_lifecycle` | `com.opencompany.employee.{hired,updated,removed}` | Subject is the workflow id; `updated` is coalesced to one per second per employee, except control changes, which go out at once |
| `approval_lifecycle` | `com.opencompany.approval.{requested,decided,expired,cancelled}` | Identity only, never the message or the recipient |
| `workflow_lifecycle` | gains `created` and `deleted` stages | So open editors refresh their workflow lists |

## Tests

Server: `tests/services/employees/`, `tests/services/approvals/`,
`tests/test_edge_condition_parity.py`, `tests/test_genui_catalog_sync.py`,
`tests/test_hire_payload_contract.py`, `tests/test_node_allowlist_hire.py`,
`tests/test_user_settings_profile_fields.py`,
`tests/test_credential_catalogue_consumer_fields.py`,
`tests/test_home_catalog_contract.py` (the starter bundles and the Discover
folder against each other and the app registry),
`tests/temporal/test_machina_run_record.py` (including replay of a pre-patch
history). Client: `features/home/**/__tests__`, `app/__tests__`,
`contexts/__tests__/themePrePaint.test.ts`.

## Known gaps

- An employee's latest report is not captured yet (`latest_report` is always
  empty).
- `list_employees` is not filtered per owner (multi-user mode shares one
  store anyway; see [Authentication](./authentication.md)).
- OAuth sign-in is not opened ahead of the request, so a browser may block
  the popup.
- Each waiting draft holds one `triggers-event` worker slot.
- Billing has no plans, payment method or invoices: no billing account sits
  behind OpenCompany. A deleted employee's runs leave the month's count,
  because deleting a workflow deletes its run records.
- Connectors has no custom (MCP) connector.
- Skill library changes reach only new hires: an employee keeps the skills it
  was hired with, and one hired before the library existed has none. The
  library is shared across users in multi-user mode, like the team list.
- An employee hired before Canvas has no Canvas node, so its
  `canvas_node_id` is null. Adding one in Dev mode fills it in.
- The Workspace's Android tab has no live mirror yet. Browser now uses the
  shared live viewer described above; timeline and replay are not available.
- With login on, an agent's Canvas writes land under the default owner:
  its tool call carries no user id
  ([agent_workflow.py](../server/services/temporal/agent_workflow.py)).
  The Workspace reads the signed-in owner's board, so it shows nothing
  there. This predates the Workspace and applies to the editor's Memory
  and Data Source tools as well. Fixing it moves existing memories to a
  different owner, so it needs its own change.
- A manual-chat employee cannot be given work from Home yet; the setup prompt
  steers towards app and schedule triggers.
