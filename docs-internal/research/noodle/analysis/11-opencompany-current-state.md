# OpenCompany frontend research snapshot, mapped against Noodle

> Research snapshot: the original OpenCompany source revision was not recorded.
> Stop and chat behavior described below predates execution-control commit
> `516ecaca`. For current acknowledged Stop/Resume and chat protocol v2, use
> [workflow control](../../../temporal-workflow-control.md) and
> [chat protocol](../../../chat_protocol.md). The inventory is retained as
> research evidence rather than a maintained runtime contract.

Research note 11 of 11 for the Noodle RFC. The other ten notes analyse Noodle's UI;
this one records what OpenCompany's own frontend already has, where it lives, what
feeds it, and which rules any Noodle-like work must obey. It describes the state
observed during that research review and proposes no design.

## Summary

- **OpenCompany already has a conversation-first owner screen.** Normal mode ("Home",
  `client/src/features/home/`) is a team sidebar of AI employees (round avatar, status
  pip, name, role), a header naming the employee on screen, a shared chat
  (`client/src/features/chat/`) with right-aligned owner bubbles and left-aligned,
  bubble-less replies beside the employee's avatar, a composer with Attach, paste and
  drop, dictation, slash commands, Web and Ask first chips and a round Send/Stop button,
  and a right-hand Workspace dock with Browser, Canvas and Mobile tabs.
- **A Noodle "bot" is closest to an OpenCompany "employee", which is one saved
  workflow.** The employee id is the workflow id, the name is the workflow name, and the
  conversation is the chat session whose id is that workflow id. That coupling is the
  biggest structural difference from Noodle: a chat session belongs to exactly one
  workflow, answers only its owner, follows the workflow's control state (send, queue or
  closed), allows one live answer at a time, and is cleared by a Reset.
- **The chat is richer than Noodle's in agent-specific areas**: AG-UI style streamed
  runs with tool steps, generated UI (json-render, 12 components), approval cards with
  Undo, edit and regenerate branches with version steppers, Good/Bad ratings, numbered
  citations, follow-up suggestions and document cards that open the Canvas.
- **The main gaps against Noodle**: no group conversations (one persona per chat, one
  workflow per session, no @mentions), no sidebar search, sections, previews, timestamps
  or unread markers, no wallpapers or glass surfaces (Home is light and dark only, and blur
  exists only on scrims), no emoji reactions (only thumbs up/down on replies), no voice
  messages or calls (dictation only), no screen capture or annotation, no link unfurls,
  no chat effects, no message context menu, no floating windows, no Hub sharing (multi-user
  mode has no data isolation), no MCP connectors in Home, no keybinding settings, and no
  harness, model or backstory editing from Home.
- **Strict frontend rules apply**: shadcn primitives, `ActionButton` intents, token-only
  Tailwind, no opacity arithmetic, no palette names, lucide icons, Zustand slice
  selectors, TanStack Query for server state, snake_case on the wire, CloudEvents
  broadcasts, the WCAG 2.5.7 drag alternative, and the chat protocol's
  change-the-doc-in-the-same-commit rule.

## Scope (files read)

Documentation:

- `CLAUDE.md` (section 7 "Frontend Design + Theme System (strict)" at `CLAUDE.md:449`,
  section 8 "Naming Conventions" at `CLAUDE.md:469`, "Frontend Performance Architecture"
  at `CLAUDE.md:543`, "Normal/Dev Mode Toggle" at `CLAUDE.md:936`, "Console Panel" at
  `CLAUDE.md:951`, and the broadcast notes at `CLAUDE.md:2791` and `CLAUDE.md:2812`).
- `docs-internal/normal_mode.md`, `docs-internal/chat_protocol.md` (client, messages,
  generated UI, approvals, attachments, branches, feedback, sources, error codes),
  `docs-internal/frontend_architecture.md`, `docs-internal/theme_system.md` (token tiers,
  Home tokens, anti-patterns, class registry), `docs-internal/design-system/IMPLEMENTATION.md`,
  `docs-internal/design-system/HANDOFF.md` (skimmed), `docs-internal/browser_workspace.md`,
  `docs-internal/canvas_node.md` (skimmed), `docs-internal/employee_teams.md`,
  `docs-internal/agent_teams.md` (opening), `docs-internal/browser.md` (profiles section),
  `docs/mobile-workspace.md` (opening).
- The local, gitignored design handoff `design_handoff_opencompany_home/` (its
  `chat/README.md`, `tokens/chat.css`, `tokens/home.css`, a keyword scan of
  `reference/OpenCompany Chat.dc.html`, and the top-level `README.md`).

Source:

- `client/src/features/home/`: `HomeShell.tsx`, `sidebar/HomeSidebar.tsx`,
  `header/HomeHeader.tsx`, `header/NewConversationButton.tsx`, `header/ThemeButton.tsx`,
  `employee/EmployeeView.tsx`, `employee/EmployeeChat.tsx`, `employee/EmployeeWork.tsx`,
  `employee/EmployeeAccess.tsx`, `employee/GiveTeam.tsx`, `employee/useEmployeeControl.ts`,
  `hire/HireView.tsx`, `hire/Composer.tsx`, `genui/HireDraftPanel.tsx`,
  `workspace/WorkspaceDock.tsx`, `workspace/WorkspaceButton.tsx`, `settings/HomeSettings.tsx`,
  `settings/AccessTab.tsx`, `settings/BillingTab.tsx`, `settings/ProfileTab.tsx`,
  `orb/OrbStage.tsx`, `orb/OrbSlot.tsx`, `approvals/data.ts`, `data/employees.ts`,
  `data/schemas.ts`, `data/presentation.ts`, `data/identity.ts`, `data/liveTask.ts`,
  `data/talk.ts`, `state/homeStore.ts`, `ui/primitives.tsx`, `ui/pillToast.tsx`.
- `client/src/features/chat/`: `ChatPane.tsx`, `host.ts`, `index.ts`,
  `thread/ChatThread.tsx`, `thread/ChatAvatar.tsx`, `thread/model.ts`, `thread/timeLabel.ts`,
  `turns/UserTurn.tsx`, `turns/AssistantTurn.tsx`, `turns/ReplyActions.tsx`,
  `turns/StepsDisclosure.tsx`, `turns/ArtifactCard.tsx`, `turns/MessageAttachments.tsx`,
  `turns/SourceChips.tsx`, `turns/GeneratedUiBlock.tsx`, `composer/Composer.tsx`,
  `composer/VoiceRecorder.tsx`, `composer/SlashMenu.tsx`, `composer/attachments.ts`,
  `data/schemas.ts`, `data/thread.ts`, `data/conversation.ts`, `data/chatContext.ts` (plus
  the request types in `data/approvals.ts`, `data/runs.ts`, `data/send.ts`, `data/stop.ts`,
  `data/branches.ts`, `data/uiState.ts`), `markdown/ReplyMarkdown.tsx`,
  `markdown/SafeImage.tsx`, `markdown/workspaceImage.ts`, `markdown/components.tsx`,
  `approval/ApprovalCard.tsx` (opening), `genui/catalog.ts` and `genui/views.tsx`
  (structure).
- Shell and shared: `client/src/components/shell/ModeToggle.tsx`,
  `client/src/app/AppShell.tsx`, `client/src/app/ShellThemeProvider.tsx`, the
  `client/src/components/ui/` inventory (plus the `button.tsx`, `textarea.tsx` and
  `toggle.tsx` variants and `ConsoleChat.tsx`), `client/src/components/catalog/presentation.ts`,
  `client/src/components/catalog/primitives.tsx`, `client/src/components/workspace/WorkspaceTabs.tsx`,
  `client/src/lib/motion.ts` (opening), `client/src/stores/chatRunStore.ts` (opening),
  `client/src/contexts/WebSocketContext.tsx` (chat, lifecycle and default cases).
- Theming: `client/src/themes/base.css`, `client/src/themes/light.css`,
  `client/src/themes/dark.css`, the `.chat-msg` rules in the ten skin files, the helper
  list in `client/src/themes/animations.css`, and `client/src/index.css` (imports, the
  `@theme inline` bridge, chat markdown).
- Server spot checks: `server/services/chat/access.py`, `server/services/employees/builder.py`,
  `server/services/employees/start.py`, `server/services/employees/handlers.py` and
  `server/services/employees/safe_apply.py` (grep level), `server/config/credential_providers.json`
  and `server/config/ai_cli_providers.json` (summaries), and the Electron window handler
  in `desktop/src/main/index.ts`.

Nothing was built, run or tested; statements about behaviour come from code and docs.

## Findings

### 1. Shell, screens and the theme rule

- **Two screens, one switch.** `useAppStore.shellMode` (`'normal' | 'dev'`, persisted as
  `ui_shell_mode`) selects Home or the workflow editor; both are lazy chunks
  (`client/src/app/ShellModeSwitch.tsx`). Switching always goes through `enterNormal` /
  `enterDev` (`client/src/app/useShellActions.ts`), never `setShellMode`. The switch is
  `ModeToggle` (`client/src/components/shell/ModeToggle.tsx:19-52`, a segmented
  `ToggleGroup`), shown in the Home header and the editor toolbar, plus Ctrl/Cmd+Shift+D.
  `VITE_NORMAL_MODE=false` turns Home off.
- **App shell.** `client/src/app/AppShell.tsx:86-101` owns the decorative `.app-frame`,
  boot effects (sound sync, page-activity pause, current-workflow sync, saved-graph sync,
  UI defaults, the mode shortcut, orb disposal) and the app-level Settings and
  Credentials dialogs.
- **Home is light and dark only.** `client/src/app/ShellThemeProvider.tsx:14-17` passes
  `baseOnly` to `ThemeProvider` while Home shows, so a stylized theme chosen in Dev shows
  as its family's base (Atomic as light, Cyber as dark) and applies again in Dev. The
  pre-paint script in `client/index.html` applies the same rule to the first frame. The theme
  button (`client/src/features/home/header/ThemeButton.tsx:20-41`) flips light and dark
  with a circular View Transition reveal from the button.

### 2. Home layout regions

```
+------------------+------------------------------------------------+------------------+
| HomeSidebar      | HomeHeader (--h-home-header, 52px)             | WorkspaceDock    |
| --w-home-sidebar | [open][logo] identity or title ... [New conv]  | 460px default,   |
| (260px,          |   [Workspace pill] [Normal|Dev] [theme]        | drag 360px ..    |
|  collapsible)    +------------------------------------------------+ window - 420px,  |
|  logo   [close]  | OrbStage: absolute, z-0, behind the content    | Expand / Close   |
|  [+ New employee]| current view, z-10:                            | header: avatar,  |
|  AI EMPLOYEES  n |   hire: orb, greeting, hero, hire composer,    | "{Name}'s        |
|  rows: avatar +  |         template chips, setup draft            |  workspace",     |
|   status pip,    |   employee: ChatPane (thread above, dock with  | task line, pill, |
|   name, role,    |         notices, composer, footnote below)     | main action      |
|   approvals      |                                                | tabs: Browser /  |
|   badge, delete  |                                                | Canvas / Mobile  |
|  profile row     |                                                |                  |
+------------------+------------------------------------------------+------------------+
 Overlays: HomeSettings (Modal, 1040x760), shell Credentials dialog, AlertDialogs,
 pill toasts (bottom-centre, one at a time).
```

`client/src/features/home/HomeShell.tsx:78-110` assembles it: `HomeSidebar`, a `<main>`
column holding `OrbStage`, `HomeHeader` and the current view, then `WorkspaceDock` and
`HomeSettings`. The hire view scrolls as one page; the employee view gets a column that
does not scroll so the chat can scroll its own thread
(`client/src/features/home/HomeShell.tsx:88-106`). A view switch scrolls to the top and
plays a fade, rise and unblur through `lib/motion`
(`client/src/features/home/HomeShell.tsx:62-76`).

### 3. Home component inventory

| Region | Component (path) | What it renders | Data behind it |
|---|---|---|---|
| Sidebar | `client/src/features/home/sidebar/HomeSidebar.tsx:204-247` | Logo with intro and pulse, close button, "New employee" (`ActionButton intent="run"`, lines 235-242), team list, profile row | `useHomeStore` (open, view), `useEmployeesQuery` |
| Sidebar row | `client/src/features/home/sidebar/HomeSidebar.tsx:35-144` (`EmployeeRow`) | `Avatar` with status pip and working pulse (line 99), name, role (one line), pending-drafts count badge (lines 104-111), hover/focus delete X with `AlertDialog` (lines 113-141), new-hire glow animation (lines 62-82) | `presentEmployee` (`client/src/features/home/data/presentation.ts:92-105`), `useWorkflowControlPending`, `useAppStore.deleteWorkflow` |
| Sidebar list | `client/src/features/home/sidebar/HomeSidebar.tsx:146-182` (`TeamList`) | Micro label "AI employees" with count, skeleton rows, error retry, empty hint | `useEmployeesQuery` |
| Sidebar footer | `client/src/features/home/sidebar/HomeSidebar.tsx:184-202` (`ProfileRow`) | Owner avatar, name, gear; opens Settings > Profile | `useOwnerSettings` (`client/src/features/home/data/profile.ts`) |
| Header | `client/src/features/home/header/HomeHeader.tsx:163-194` | Open-sidebar and logo when collapsed; hire title or the employee identity; New conversation (Talk on only), Workspace pill, mode toggle, theme button | `useHomeStore`, employee summary |
| Header identity | `client/src/features/home/header/HomeHeader.tsx:147-161` | Small avatar (photo menu: upload or remove, lines 40-91), name with rename pencil (lines 93-145), subtitle "role · apps", compact status pill | `useRenameEmployee`, `useSetEmployeePhoto` (`client/src/features/home/data/identity.ts:24-55`) |
| Header action | `client/src/features/home/header/NewConversationButton.tsx:24-63` | "New conversation" with a confirming `AlertDialog`; clears the thread and what the employee remembers | `useClearChat` (`clear_chat_messages`) |
| Hire view | `client/src/features/home/hire/HireView.tsx:24-107` | Orb slot, greeting line with "N working now", hero "Who should we hire today?", hire composer, template chips, setup draft | `useHireComposer`, `useStarterHire`, `useConnectors`, `useEmployeesQuery`, `useOwnerSettings` |
| Hire composer | `client/src/features/home/hire/Composer.tsx` | Job box (`rounded-composer`, `shadow-float`), connected-apps pill, Create | presentational |
| Setup draft | `client/src/features/home/genui/HireDraftPanel.tsx` | "Writing their setup…" with timer and Cancel, the json-render setup screen, failures | `generate_employee_setup`, `cancel_employee_setup`, `hire_employee` |
| Employee page | `client/src/features/home/employee/EmployeeView.tsx:54-101` and `client/src/features/home/employee/EmployeeChat.tsx:107-176` | Loading and missing states, then `ChatPane` with Home's host: orb and hire notes on top, notices above the box, a footnote on Ask first | team list, detail fallback, `useLaneRun`, `useRetryNote` |
| Notices | `client/src/features/home/employee/EmployeeChat.tsx:73-105` | Failure-pause alert, pending changes (Apply), live work card, Give them a team, pending app-access requests, Help in browser, Turn on Talk or a "can't message" note, a state notice beside the main action | summary fields (`pause_reason`, `pending_changes`, `browser_request`, `talk.state`, `can_give_team`) |
| Live work | `client/src/features/home/employee/EmployeeWork.tsx:105-123` | "Live work" card: state message, elapsed time, last activity, up to three current steps, each with `member · status`, "Show all activity" | `useEmployeeDetailQuery(live)` (`work_progress`, `job_progress`), the chat lane |
| Team offer | `client/src/features/home/employee/GiveTeam.tsx:10-50` | "Give them a team": a review listing responsibilities, then apply | `plan_employee_team`, `give_employee_team` |
| App access | `client/src/features/home/employee/EmployeeAccess.tsx:8-37` | Allow / Not now / Remove access per app grant | `list_employee_access` (polled every 5 s), `decide_employee_access` |
| Workspace pill | `client/src/features/home/workspace/WorkspaceButton.tsx:14-40` | Toggle tinted purple while open; a blinking dot while closed and someone works | `useHomeStore`, `useEmployeesQuery` |
| Workspace dock | `client/src/features/home/workspace/WorkspaceDock.tsx:159-254` | Header (avatar, "{Name}'s workspace", live task line, Live or status pill, main action, Expand, Close), left-edge resize separator, `WorkspaceTabs` with `BrowserWorkspace`, `WorkspaceCanvas` (lazy) and `MobileWorkspace` | employee summary (`browser_nodes`, `workspace_nodes`, `canvas_node_id`), `useLiveTask`, `useEmployeeControl` |
| Settings | `client/src/features/home/settings/HomeSettings.tsx:134-180` | `Modal` with a searchable left nav; pages App access, Profile, Billing (Settings group) and Skills, Connectors, Plugins (Customize group) (`PAGES`, lines 52-94) | per page: `EmployeeAccess`, `useOwnerSettings`, `get_employee_usage`, user-skill handlers, `CredentialsBrowser`, `client/src/features/home/hire/starters.json` |
| Orb | `client/src/features/home/orb/OrbStage.tsx:9-24`, `client/src/features/home/orb/OrbSlot.tsx:17-34`, `client/src/features/home/orb/orbEngine.ts` | A three.js render of the logo behind the content, gliding into the slot each view reserves; the static mark without WebGL or under reduced motion | `client/src/features/home/orb/orb.ts` energy targets and spikes |
| Toasts | `client/src/features/home/ui/pillToast.tsx:45-58` | One bottom-centre pill at a time on its own sonner toaster, with sound | none |
| Small parts | `client/src/features/home/ui/primitives.tsx` | `Avatar` (sm/md/lg, photo or initial, status pip, lines 24-63), `StatusDot` (65-73), `StatusPill` (83-106), `MicroLabel` (108-112); re-exports `AppMark` and `SearchField` from `client/src/components/catalog/primitives.tsx` | presentation tables |

### 4. Home data layer, WebSocket requests and broadcasts

State ownership follows the repo rule: server data in TanStack Query, UI state in a
Zustand store.

- **`useHomeStore`** (`client/src/features/home/state/homeStore.ts:82-219`): `view`
  (`{kind:'hire'} | {kind:'employee', workflowId}`, line 15), sidebar open (persisted
  `home_sidebar_open`), Settings open, tab and category, the new-hire row glow and logo
  pulse nonces, a composer-focus nonce, the hire notice, and the Workspace (open, width
  and tab persisted under `home_workspace_v1`; `workspaceFor`, `workspaceWide` and a
  Canvas focus request kept per session). It holds no server data by design (lines 1-7).
- **Team queries** (`client/src/features/home/data/employees.ts`): `useEmployeesQuery`
  (`list_employees`, `staleTime` 30 s, `refetchOnMount: 'always'`, lines 36-53) and
  `useEmployeeDetailQuery` (`get_employee`; with `live` it polls every 5 s while work is
  active and every 15 s otherwise, and invalidates at most every 3 s on node-status
  changes, lines 55-100). Summaries are parsed forgivingly with zod
  (`client/src/features/home/data/schemas.ts:38-86`).
- **Presentation** (`client/src/features/home/data/presentation.ts`): maps server status
  to pill tone and label (`working`, `ready`, `paused`, `attention`, and `waiting`
  "Needs you" when drafts or a browser request wait, lines 52-105), the main action
  (Pause, Resume, Start, Connect an app, Connect an AI model, Open in Dev mode), and the
  message-box mode (`send`, `queue`, `start`, lines 134-151). Token classes live there
  too (lines 190-210).
- **Other hooks**: `client/src/features/home/data/identity.ts` (rename and photo),
  `client/src/features/home/data/talk.ts` (Turn on Talk, Apply, "Stop work and apply",
  and the retry note), `client/src/features/home/data/liveTask.ts:36-63` (the live "Now"
  line from the todo cache and node statuses), `client/src/features/home/data/usage.ts`,
  `client/src/features/home/data/skills.ts`, `client/src/features/home/data/profile.ts`,
  `client/src/features/home/approvals/data.ts`.

Requests Home sends (all over the main WebSocket, snake_case payloads):

| Purpose | Request types |
|---|---|
| Team | `list_employees`, `get_employee`, `get_employee_usage` |
| Hiring | `generate_employee_setup`, `cancel_employee_setup`, `hire_employee` |
| Control | `start_employee`, `pause_workflow`, `resume_workflow` (through `useWebSocketActions`), `enable_employee_talk`, `apply_employee_changes` (optionally `stop_work: true`) |
| Identity | `rename_employee`, `set_employee_photo` (after `POST /api/workspace/{workflow_id}/uploads`) |
| Teams and access | `plan_employee_team`, `give_employee_team`, `list_employee_access`, `decide_employee_access` |
| Settings | user settings read and save, the user-skill family (`create_user_skill`, `update_user_skill`, `delete_user_skill`, `get_skill_content`, `lookup_skill_metadata`), the credential catalogue |
| Delete | `DELETE /api/database/workflows/{id}` through `useAppStore.deleteWorkflow` |

Broadcasts Home and the chat react to (routing in
`client/src/contexts/WebSocketContext.tsx`):

| Wire key | Routing | Effect |
|---|---|---|
| `employee_lifecycle` | default fan-out (`client/src/contexts/WebSocketContext.tsx:2094-2101`) to `useEmployeeLifecycle` (`client/src/features/home/data/employees.ts:165-176`) | Refetch on `hired` and `updated`, prune on `removed`; dedupe by `(source, id)` |
| `workflow_lifecycle` | switch case that also calls `dispatchToListeners` (`client/src/contexts/WebSocketContext.tsx:1120-1143`) | Editor list refresh, `forgetWorkflow` on delete; Home refreshes the team |
| `approval_lifecycle` | default fan-out to the chat's approvals listener (`client/src/features/chat/data/approvals.ts:390-391`) and Home's toast (`client/src/features/home/approvals/data.ts:24-36`) | Refetch drafts; a toast for drafts from the employee's own work |
| `chat.updated` | switch case (`client/src/contexts/WebSocketContext.tsx:1192-1204`) | Invalidate the session's thread queries |
| `chat_run_event` | switch case to `useChatRunStore.receive` (`client/src/contexts/WebSocketContext.tsx:1206-1211`) | Per-run AG-UI frames, folded once per animation frame (`client/src/stores/chatRunStore.ts:1-23`) |
| `canvas_updated` | switch case, no listener fan-out (`client/src/contexts/WebSocketContext.tsx:1213-1236`) | Invalidate `['canvasBoard']`; the Dev dock follows the push |
| `browser_updated` | switch case (`client/src/contexts/WebSocketContext.tsx:1238-1252`) | Dev opens its dock on Browser for an `awaiting_user` request; Home relies on the summary's `browser_request` |

### 5. Chat feature inventory (`client/src/features/chat/`)

#### 5.1 Host contract and hosts

- Only `client/src/features/chat/index.ts` is importable from outside (ESLint
  `no-restricted-imports`, `client/eslint.config.js:9` and following). It exports
  `ChatPane`, the host types, `useClearChat`, `useLaneRun`, `useChatThread`, the markdown
  components and `SafeImage`.
- `ChatHost` (`client/src/features/chat/host.ts:34-71`) is the seam between the chat and
  its surroundings: `kind` (`home | dev`), `sessionId`, `scope` (`all` reads every
  generation, `live` the current one), a single `persona` (`{name, colorRole, photo}`,
  `client/src/features/chat/host.ts:16-21`), `composer` (`send | queue | closed`), and
  slots `notices` (above the box), `top` (the first item in the scroll area),
  `afterThread`, `emptyState` and `footnote`, plus callbacks `notify`, `onSendRefused`,
  `liveNote`, `onScrolledChange`, `compact` and `openArtifact`. `ChatPaneHandle` exposes
  `focusComposer`.
- Two hosts: Home's `EmployeeChat` (`client/src/features/home/employee/EmployeeChat.tsx:149-175`:
  scope `all`, pill toasts, the orb on top, an Ask first footnote, Canvas items opened in
  the Workspace) and Dev's `ConsoleChat` (`client/src/components/ui/ConsoleChat.tsx:36-95`:
  compact, scope `live`, persona = workflow name, sonner toasts, the Canvas dock).

#### 5.2 Thread and turns

- **Thread** (`client/src/features/chat/thread/ChatThread.tsx:68-251`): a centred column
  (`--w-chat-column`, 760px; compact in Dev), the host's `top`, a `role="log"`
  `aria-live="polite"` list, then `afterThread`. It sticks to the bottom while the newest
  turn is in view, counts arrivals while scrolled up behind a "Jump to latest, N new"
  pill (`client/src/features/chat/thread/ChatThread.tsx:235-248`), and new turns rise in
  with `chat-rise` (`client/src/features/chat/thread/ChatThread.tsx:141-164`). A
  "{Name} restarted — they start fresh from here" divider marks a generation change
  (`client/src/features/chat/thread/ChatThread.tsx:35-43`). A bottom fade
  (`bg-linear-to-b from-transparent to-bg-app`,
  `client/src/features/chat/thread/ChatThread.tsx:234`) assumes the opaque app
  background. The newest 200 messages load (`client/src/features/chat/data/thread.ts:19`).
- **Turn model** (`client/src/features/chat/thread/model.ts`): pure; one turn per message
  plus a run's own turn before it answers; keys survive the optimistic-to-saved swap;
  work (steps, duration) rides the run's first turn.
- **Owner turn** (`client/src/features/chat/turns/UserTurn.tsx:29-109`): right-aligned;
  attachments above (`client/src/features/chat/turns/MessageAttachments.tsx:12-46`: image
  thumbnails that open full size, other files as chips with a type badge, name and
  size); the bubble is `chat-msg chat-msg-user`, `rounded-draft rounded-br-sm` (a tail
  corner), `border-border-default bg-bg-elevated`, at most 85% wide
  (`client/src/features/chat/turns/UserTurn.tsx:59-68`); a button press from generated UI
  reads as a quieter pill with a pointer icon
  (`client/src/features/chat/turns/UserTurn.tsx:48-58`). The meta bar under it shows
  "Sending…" while pending, then the time (`client/src/features/chat/thread/timeLabel.ts`:
  time, "Yesterday, 9:41 AM", weekday or date), a version stepper, Edit and Copy
  (`client/src/features/chat/turns/UserTurn.tsx:69-106`), faint until hover except on the
  latest turn.
- **Employee turn** (`client/src/features/chat/turns/AssistantTurn.tsx:91-269`):
  `ChatAvatar` on the left with a spinning `opencompany-live-ring` while the run works
  (`client/src/features/chat/thread/ChatAvatar.tsx:12-27`); then, in order, the steps
  disclosure, three shimmer skeleton lines until text arrives, the markdown text
  (`chat-msg chat-msg-bot`, no bubble in light and dark), generated UI blocks, document
  cards, approval cards, a status line ("Thinking", "Writing · N tok/s", "Stopping…", an
  Esc hint, a retry note), notes for a queued, stopped or failed run (with Try again),
  cited sources, follow-up chips, and the reply bar.

#### 5.3 Runs and streaming

- A message the deployed chat trigger will answer starts a **chat run**; one live run per
  session (the lane). Send becomes Stop while it lives; Esc anywhere in the pane stops it
  (`client/src/features/chat/ChatPane.tsx:285-294`).
- Run events arrive only for subscribed sessions (`chat_subscribe`,
  `client/src/features/chat/data/runs.ts:65`), carry `seq` and `hub_epoch`, and are folded
  by `client/src/lib/agui/reduceRun.ts` into `chatRunStore` once per animation frame;
  gaps trigger a snapshot resync. Only the agent answering the owner streams text; tool
  calls show as steps with plugin-provided labels; reasoning never streams.
- Text renders block by block while streaming
  (`client/src/features/chat/markdown/ReplyMarkdown.tsx:78-90`,
  `client/src/features/chat/markdown/blocks.ts`) with a pulsing caret dot
  (`client/src/index.css:659-679`).
- The steps disclosure (`client/src/features/chat/turns/StepsDisclosure.tsx:52-87`) is a
  pill reading "Working…" (open, shimmering) and then "Worked for 12s · 3 steps",
  expanding to a step list with a spinner, tick, cross or dash.

#### 5.4 Composer (`client/src/features/chat/composer/Composer.tsx:68-294`)

- Surface: `chat-composer`, `border-border-default bg-bg-panel`, `rounded-card` (12px),
  `shadow-modal` on Home; `rounded-lg` and an inline layout when compact
  (`client/src/features/chat/composer/Composer.tsx:263-268`). It is a card, not a pill.
- Text box: `Textarea variant="bare"`, auto-grows to `--h-chat-composer-max` (220px);
  Enter sends, Shift+Enter breaks a line, IME-safe (`client/src/lib/composerKeys.ts`);
  ArrowUp in an empty box edits the last message; placeholder "Message {Name}… Type / for
  commands".
- Row: "+" (a round quiet button that opens the file picker directly,
  `client/src/features/chat/composer/Composer.tsx:184-209`; not a menu), the chips slot
  (Web and Ask first), the microphone (dictation,
  `client/src/features/chat/composer/Composer.tsx:210-215`), and a round `invert` Send with
  an up arrow that becomes a filled square Stop
  (`client/src/features/chat/composer/Composer.tsx:159-183`).
- Slash commands (`client/src/features/chat/composer/SlashMenu.tsx:16-86`): cmdk inside a
  Popover anchored above the box; focus stays in the textarea with `aria-controls` and
  `aria-activedescendant`. Commands come from `get_chat_context` (generic plus the
  employee's apps); those marked `suggest` show as cards in an empty Home chat.
- Drafts are kept per session in `client/src/features/chat/state/composerStore.ts` (they
  survive switching employees), and attachments in
  `client/src/features/chat/state/attachmentStore.ts`.

#### 5.5 Attachments and media

- Attach, paste and drop all call `addAttachments`
  (`client/src/features/chat/composer/attachments.ts:24-61`): at most 6 files per message
  (`MAX_ATTACHMENTS`, `client/src/features/chat/composer/attachments.ts:17`), each at most
  25 MB, uploaded at once into the workflow's workspace under `uploads/`, shown as chips
  with progress and Remove; Send waits for uploads. A drop anywhere on the chat shows
  `DropOverlay`. The file's header comment names CLAUDE.md rule 9: the picker is the
  drop's pointer alternative.
- The server rebuilds each reference; the answering agent reads them as an
  `[attachments]` line; images also reach vision-capable providers.
- Replies can show workspace images inline; any other image waits behind a "Show image
  from {host}" chip (`client/src/features/chat/markdown/SafeImage.tsx:19-33`,
  `client/src/features/chat/markdown/workspaceImage.ts`). Links open in a new tab with
  `noopener noreferrer` (`client/src/features/chat/markdown/components.tsx:14-21`). There
  is no audio player in the thread: a sent audio file shows as a generic file chip.

#### 5.6 Dictation

`client/src/features/chat/composer/VoiceRecorder.tsx:31-190`: MediaRecorder with a
blinking dot, elapsed time and 36 live level bars, Cancel and Use dictation, a
3-minute cap; the recording is uploaded, turned into text by `transcribe_audio` (which
deletes it) and appended to the draft. The microphone shows only when
`dictation_status` reports a speech provider with a key
(`client/src/features/chat/data/chatContext.ts:77-104`). This is speech-to-text into the
box, not a voice message.

#### 5.7 Generated UI

`client/src/features/chat/turns/GeneratedUiBlock.tsx` lazily renders a reply's `ui`
parts with json-render (`client/src/features/chat/genui/ChatUi.tsx`), re-sanitised
(`client/src/features/chat/genui/prepare.ts`, `client/src/lib/jsonRender/`), revealed
element by element while live, and drawn from a fixed catalogue of twelve components
(`client/src/features/chat/genui/catalog.ts`: Stack, Row, Card, SlotPicker, Select,
Toggle, TextField, Text, StatGrid, BarChart, Callout, Button). Owner edits sync through
`chat_ui_state` (after 300 ms idle and on hide); a button press becomes
`send_chat_message{ui_event}` and an `action` turn. The server mirrors the catalogue in
`server/config/chat_genui_catalog.json`. Development builds add an Inspect view. The Home
setup screen uses a separate catalogue (`client/src/features/home/genui/`, private behind
its `index.ts`).

#### 5.8 Approval cards

`client/src/features/chat/approval/ApprovalCard.tsx` (theme hook `chat-approval`):
recipient, channel, the message as it will go, Discard / Edit / Send; after Send a
countdown with Undo (5 s), then Sending and Sent (or failed with Try again behind a
confirmation); a discarded draft can be restored while its window lasts; "Sends when you
resume" for a paused employee's gate. Cards sit on the reply that made them, or after
the thread for drafts from the employee's own work
(`client/src/features/chat/approval/StandaloneApprovals.tsx`). Ctrl/Cmd+Enter sends the
newest pending draft (`client/src/features/chat/ChatPane.tsx:276-294`). The Ask first
chip changes the live rule (`set_ask_first`).

#### 5.9 Branches, versions and feedback

Edit opens in place (`client/src/features/chat/turns/UserEditBox.tsx`) and starts a new
branch; Try again regenerates the latest answer; `‹ 1 / 2 ›` switches versions
(`client/src/features/chat/turns/VersionStepper.tsx`); the server decides what is
`editable` and refuses with codes such as `revision_conflict` or `cannot_rewind`, told in
the host's words (`client/src/features/chat/turns/runCopy.ts`). The reply bar
(`client/src/features/chat/turns/ReplyActions.tsx:51-97`) offers Copy, Good and Bad
(toggle buttons with `aria-pressed`; the toast says the employee will see the rating
next time), Try again, the version stepper and the time.

#### 5.10 Sources, follow-ups and artifacts

- `[n]` citations become chips; cited sources list under the answer as host-and-title
  chips (`client/src/features/chat/turns/SourceChips.tsx:19-63`); numbering is per
  conversation.
- Up to three follow-up suggestions show under the latest finished answer
  (`client/src/features/chat/turns/FollowUps.tsx`).
- A document the run wrote on its Canvas shows as a card with title, kind and version
  (`client/src/features/chat/turns/ArtifactCard.tsx:12-37`); Open asks the host to show
  it (Home: the Workspace's Canvas tab at that version; Dev: the Canvas dock).

#### 5.11 Keyboard map (fixed, not configurable)

Cmd/Ctrl+K focuses the box on Home
(`client/src/features/home/employee/EmployeeChat.tsx:126-134`); Esc stops a running
answer; Ctrl/Cmd+Enter sends the newest draft; ArrowUp edits the last message; Enter and
Tab pick a slash command; Ctrl/Cmd+Shift+D switches modes; the editor has its own command
palette (`client/src/components/ui/CommandPalette.tsx`) and F2 rename on the canvas.

#### 5.12 Chat wire summary

Requests: `send_chat_message`, `get_chat_messages`, `chat_subscribe`, `chat_unsubscribe`,
`get_chat_run`, `stop_chat_run`, `chat_ui_state`, `edit_chat_message`,
`regenerate_chat_reply`, `switch_chat_branch`, `set_chat_feedback`, `get_chat_context`,
`clear_chat_messages`, `list_approvals`, `get_approvals`, `decide_approval`,
`get_ask_first`, `set_ask_first`, `canvas_version`, `dictation_status`,
`transcribe_audio`; HTTP uploads and file reads under `/api/workspace/{workflow_id}/`.
Message rows carry `id`, `role`, `kind` (`text`, `report`, `action`, `notice`), `text`,
`timestamp`, `run_key`, `run_id`, `parent_id`, `status`, `attachments`, `parts` (`ui`,
`artifacts`, `approvals`, `sources`, `followups`), `siblings`, `editable`, `feedback`
and an optional `run` summary (`client/src/features/chat/data/schemas.ts:69-111`). There
is no author field beyond `role`, no reaction field and no read marker.

### 6. Workspace dock, Settings and hiring (context)

- **Workspace** (`client/src/features/home/workspace/WorkspaceDock.tsx`): one employee at
  a time (the one last opened, else the first). Browser uses the shared live viewer
  (`client/src/components/browser/BrowserWorkspace.tsx`: binary JPEG frames over
  `/ws/browser`, Take control / Hand back, Help in browser); Canvas shows the employee's
  board (`client/src/features/home/workspace/WorkspaceCanvas.tsx`, lazy); Mobile shows
  the managed local Android emulator (`client/src/components/mobile/MobileWorkspace.tsx`).
  At 1100px and wider it pushes the page; below, it overlays with `shadow-dock`. Its
  resize separator is mouse-drag only
  (`client/src/features/home/workspace/WorkspaceDock.tsx:218-228`); Expand
  (`client/src/features/home/workspace/WorkspaceDock.tsx:67`) is the non-drag size
  control. Full view (`client/src/components/workspace/FullView.tsx`) uses native
  fullscreen.
- **Settings**: Profile (names, role, preferences, timezone, `memory_across_chats`,
  `prefer_local_ai`, appearance), Billing (tasks this month and the employee count only,
  `client/src/features/home/settings/BillingTab.tsx:27-42`), Skills (the library plus a
  Discover folder), Connectors (the shared `CredentialsBrowser`: every provider in
  `server/config/credential_providers.json`, grouped by `consumer_category`: `ai`,
  `developer` including `claude_code` and `codex_cli`, `messages`, `organize`, `devices`
  including `browser` and `android_remote`, `research`, `business`, `language`), Plugins
  (starter bundles), and App access.
- **Hiring**: describe a job, an LLM drafts a json-render setup screen, and Hire builds,
  validates, saves and starts the workflow. The builder always creates an `aiAgent` whose
  provider and model come from the model that wrote the setup screen
  (`server/services/employees/builder.py:766-781`); Start moves an agent on an unusable
  provider to the owner's current model (`server/services/employees/start.py:50-82`).

### 7. Design tokens and theming

#### 7.1 Token tiers

Colour values are hex plus `color-mix()` in per-theme files; `client/src/index.css` is a
bridge only (`@theme inline`, `--color-X: var(--X)`, `client/src/index.css:61-315`).

| Tier | Tokens (utilities) | Defined in |
|---|---|---|
| Surfaces | `bg-app`, `bg-panel`, `bg-canvas`, `bg-elevated`, `bg-input`, `bg-hover`, `bg-active`, `bg-overlay`, `bg-scrim-soft` | `client/src/themes/light.css:26-33`, `client/src/themes/dark.css:22-29`, `client/src/themes/base.css:205-206` |
| Foreground and borders | `fg-default`, `fg-muted`, `fg-faint`, `fg-on-*`; `border-default`, `border-strong`, `border-focus` | `client/src/themes/light.css:36-48`, `client/src/themes/dark.css:32-44` |
| shadcn semantic | `background`, `foreground`, `card`, `popover`, `primary`, `secondary`, `muted`, `accent`, `destructive`, `success`, `warning`, `info`, `border`, `input`, `ring` | `client/src/themes/light.css:82-104`, `client/src/themes/dark.css:78-100` |
| Action roles | `action-{run,stop,save,config,secret,tools}` with `-soft`, `-hover`, `-border`, `-ink` | `client/src/themes/light.css:146-175`, inks in `client/src/themes/dark.css:104-109` |
| Node roles | `node-{agent,model,skill,tool,trigger,workflow}` with `-soft`, `-border`, and Home's identity variants `-fill`, `-hover`, `-edge`, `-ink` | `client/src/themes/light.css:123-207`, inks in `client/src/themes/dark.css:112-117` |
| Employee status | `status-{working,ready,paused,attention,waiting}-{dot,fill,border,ink}` | `client/src/themes/base.css:208-233` |
| Code and syntax | `code-*` | `client/src/themes/light.css:237-261`, `client/src/themes/dark.css:141-164`, one block per skin |
| Dracula raw | `dracula-*` (the palette under the roles; not for components) | `client/src/themes/light.css:109-118` |
| Tint scale | `--tint-*` percentages, the only home of alpha numbers | `client/src/themes/base.css:75-110` |

Avatar colours come from a role, not a free colour: `AVATAR_CLASS`
(`client/src/components/catalog/presentation.ts:5-11`) maps `agent | model | tool |
trigger | workflow` to `bg-node-X-fill border-node-X-edge text-node-X-ink`.

#### 7.2 Type

Geist Variable for display and body on light and dark, and the system mono stack for
numbers, state and code (`client/src/themes/light.css:50-53`;
`@fontsource-variable/geist` imported at `client/src/index.css:4`). The base scale runs
11 to 44px (`client/src/themes/base.css:57-65`), plus Home steps `--text-meta` 12.5,
`--text-row` 13.5, `--text-lead` 15, `--text-title` 22 and `--text-hero` 36 with hero
tracking and leading (`client/src/themes/base.css:162-169`). Sentence case; uppercase
with `--tracking-label` only in micro labels.

#### 7.3 Radii

Home radii are multiples of each theme's `--radius-lg`, so square themes stay square:
`--radius-row` 10, `--radius-card` 12, `--radius-panel` 16, `--radius-draft` 18,
`--radius-composer` 22, plus `--radius-pill` (`client/src/themes/base.css:153-160`). The
Tailwind `rounded-sm/md/lg/xl` utilities are computed from `--radius` and do not follow
themes (`client/src/index.css:156-159`, noted in `docs-internal/theme_system.md`).

#### 7.4 Motion

All Web Animations go through `client/src/lib/motion.ts`, which reads `--dur-*` and
`--ease-*` tokens, runs at 1 ms under reduced motion or a hidden page, and never starts
loops then. Home choreography durations and easings live at
`client/src/themes/base.css:121-151`; chat durations (`--dur-chat-rise`,
`--dur-genui-enter`, `--dur-follow-in`, `--dur-version-swap`, `--dur-popover-in`,
`--dur-chip-pop`, `--dur-live-ring`, `--dur-shimmer`, `--dur-caret` and others) at
`client/src/themes/base.css:184-206`. CSS helper classes (`opencompany-live-ring`,
`-shimmer`, `-shimmer-text`, `-shimmer-slot`, `-spinner`, `-dots`, `-caret`, `-draw`,
`-grow`, `-pip-pulse`) are in `client/src/themes/animations.css`; every CSS animation
pauses while the page is hidden (`client/src/themes/base.css:326-330`).

#### 7.5 Shadows, glows and anything glass-like

- Elevation: `--shadow-card`, `--shadow-card-hover`, `--shadow-modal`
  (`client/src/themes/base.css:52-54`, `client/src/themes/dark.css:120-122`) and Home's
  `--shadow-float`, `--shadow-popover`, `--shadow-dialog`, `--shadow-dock`
  (`client/src/themes/light.css:209-215`, `client/src/themes/dark.css:123-126`).
- Glows: `--glow-connect`, `--glow-hire`, `--glow-hire-settle`, `--glow-refine`,
  `--glow-task` and `--tint-pip-ring`; dark keeps the neon, light swaps each for its ink
  (`client/src/themes/light.css:217-230`, `client/src/themes/dark.css:128-136`).
- Blur: one token, `--blur-scrim` (6px, `client/src/themes/base.css:151`), used behind
  Home dialogs and by the chat's drop overlay (`backdrop-blur-(--blur-scrim)` over
  `bg-bg-scrim-soft`, `client/src/features/chat/composer/DropOverlay.tsx:15`); the shadcn
  `Dialog` and `AlertDialog` overlays use `backdrop-blur-xs`. There is no translucent or
  "glass" surface token, no frosted bubble, and no wallpaper or background-image slot
  outside the editor canvas (`--canvas-grid`, `client/src/themes/base.css:249-254`).
- The nearest thing to a full-bleed backdrop is the orb layer: `OrbStage` is
  `pointer-events-none absolute inset-x-0 top-(--h-home-header) bottom-0 z-0` behind the
  `z-10` content (`client/src/features/home/orb/OrbStage.tsx:17-23`,
  `client/src/features/home/HomeShell.tsx:81-106`).

#### 7.6 Chat theme hooks

`.chat-msg .chat-msg-user` on the owner's bubble, `.chat-msg .chat-msg-bot` on a reply's
text, `.chat-turn-user` and `.chat-turn-bot` on turns, `.chat-composer`,
`.chat-approval` and `.chat-genui` (the class registry in
`docs-internal/theme_system.md`). In light and dark a reply has no bubble; each of the ten
skins paints `.chat-msg-bot` as a bubble with its own padding (for example
`client/src/themes/atomic.css:242-244` and `client/src/themes/cyber.css:710-731`), and
`client/src/index.css:655-658` keeps code and tables in such a bubble free of text glow.
Because Home is light and dark only, the skin bubbles appear only in Dev's console chat.

#### 7.7 The local design handoff's chat specification

`design_handoff_opencompany_home/` (gitignored; the 2026-10-03 export according to the
repo's notes) holds `chat/README.md`, `tokens/chat.css` and
`reference/OpenCompany Chat.dc.html`. It specifies: a header with avatar, name, a live
status pill, an Ask first toggle, a theme toggle and an artifact toggle; owner bubbles on
the right in `--bg-elevated`; assistant rows with the avatar on the left and no bubble;
edit and branch on owner turns; regenerate, copy and thumbs on replies; a fixed order
inside a reply (steps, text with source chips, generated UI, approval card, sources,
follow-ups); a composer with auto-grow, attachments, slash commands, voice with live
bars, Web and Ask first chips and Send/Stop; and a right-hand artifact panel. Its token
rule is "no custom colour or size tokens": every tinted surface uses the action-role
recipe, and `tokens/chat.css` adds keyframes only. A keyword scan of the prototype found
one `backdrop-filter` (the drop overlay) and no reactions, wallpaper, receipts or glass.
The handoff's top-level `README.md:18` still says "This is not a chat product", which
predates the chat screen. The repo's tokens win where values differ
(`docs-internal/normal_mode.md:26-30`).

### 8. Backend facts that shape any Noodle-like UX

- **A chat session is one saved workflow.** `server/services/chat/access.py:48-61`
  refuses a session id that names no saved workflow and any socket whose principal is not
  the workflow's owner; only the editor's `"default"` session has no workflow.
- **One persona, one answering agent.** The talk line is chatTrigger, then an agent, then
  Reply in Chat (`docs-internal/normal_mode.md:538-578`). Teammates in a team work
  through Task Manager behind the lead and never post into the thread
  (`docs-internal/employee_teams.md`, `docs-internal/agent_teams.md`); the live work card
  can name the member per step (`client/src/features/home/employee/EmployeeWork.tsx:72`).
- **Conversation lifetime follows the workflow.** The thread is stamped with the
  generation; a Reset clears it with the agent's Context; "New conversation" clears it;
  threads are per workflow, not per user (`docs-internal/normal_mode.md`, Known gaps).
- **The message box follows control state.** Running, starting or resuming sends;
  paused or pausing queues one message; anything else has no box
  (`client/src/features/home/data/presentation.ts:134-151`, the `delivery` of
  `send_chat_message`).
- **One live run per session.** A second send answers `run_in_progress`; edit,
  regenerate and switch are refused while a run holds the lane.
- **Summaries carry no conversation metadata.** `list_employees` returns status, task,
  `done_today`, `pending_approvals`, apps, control, `hired_at` and workspace nodes, but no
  last message, last activity time or unread count. Searches of `server/services/` for
  unread, last-message, reaction and presence terms, and of `server/services/chat/` for
  read and seen timestamps, found no such fields (the only "delivered" hits are chat
  notes' `delivered_at` and the run failure code `not_delivered`).
- **Multi-user is authentication without isolation.** `AUTH_MODE=multi` shares one
  workflow and credential store (`CLAUDE.md`, Authentication System).
- **Desktop shell.** `desktop/src/main/index.ts:159-163` allows same-origin
  `window.open` and sends every other URL to the OS browser, so a same-origin pop-out
  window is possible in the Electron app; the SPA has no route or state model for one.

### 9. Drift and rule exceptions observed

These matter before treating any document as the source of truth.

- `docs-internal/normal_mode.md:591-622` describes Turn on Talk and Apply as a restart
  through Reset that clears the conversation, while
  `client/src/features/home/data/talk.ts:20-59` and `server/services/employees/handlers.py`
  call `server/services/employees/safe_apply.py` ("after current work finishes", without
  clearing), which matches `docs-internal/employee_teams.md`'s Safe Apply.
- `docs-internal/normal_mode.md:192-193` says the Workspace's Android tab explains that
  mirroring is unavailable; `client/src/features/home/workspace/WorkspaceDock.tsx:133`
  renders `MobileWorkspace`.
- `docs-internal/normal_mode.md:72` lists a `connectAI/` folder that does not exist;
  Connect an AI model now opens the shell's credentials dialog
  (`client/src/features/home/state/homeStore.ts:184-187`).
- `docs-internal/normal_mode.md:710-787` lists five Settings pages; the code has six,
  including App access (`client/src/features/home/settings/HomeSettings.tsx:53`).
- The "no polling" rule (`docs-internal/frontend_architecture.md:484-487`) has
  documented exceptions (employee detail at 5 s and 15 s, the Mobile status poll) and an
  undocumented one (`client/src/features/home/employee/EmployeeAccess.tsx:16`,
  `refetchInterval: 5000`).
- `client/src/features/home/employee/GiveTeam.tsx:10-50` drives a request with
  `useState` plus `sendRequest`, which `docs-internal/frontend_architecture.md:561` calls a
  code smell (wrap it in a query or mutation).

## Mapping table

Status: **exists** (usable as is), **partial** (a related piece exists with a gap),
**missing** (nothing comparable).

| Noodle concept | OpenCompany equivalent (paths or service) | Status | Notes on the gap |
|---|---|---|---|
| Bot | Employee = one saved workflow; the `list_employees` summary (`client/src/features/home/data/schemas.ts:38-86`), `server/services/employees/` | exists | Heavier than a bot: a graph with trigger, agent, tools and Talk line; created by Hire or in Dev mode |
| Bot name | `Workflow.name`; the rename pencil (`client/src/features/home/header/HomeHeader.tsx:93-145`), `rename_employee` | exists | At most 40 characters; a rename rewrites the hire-written instruction opening |
| Illustrated avatar | Uploaded photo (`set_employee_photo`, `client/src/features/home/data/identity.ts`) or an initial on a role colour (`client/src/components/catalog/presentation.ts:5-11`) | partial | No illustrated or generated avatars; editor-built workflows cannot hold a photo (`unsupported`) |
| Backstory (role prompt) | Agent `system_message` from `server/services/employees/prompt.py`; `role`, and the detail's `job`, `plan`, `rules` | partial | No Home editor; editable only in Dev (agent parameters) or by asking in Talk (Agent Builder) |
| Public description | `description` in the `get_employee` detail; the sidebar shows `role` | partial | No public profile or audience |
| Harness and model | `aiAgent` provider and model set at hire (`server/services/employees/builder.py:766-781`), healed at Start (`server/services/employees/start.py:50-82`); CLI agents (Claude Code, Codex, Gemini; `server/config/ai_cli_providers.json`) as Dev node types; local models via Ollama, LM Studio, OpenAI-compatible endpoints and `prefer_local_ai` | partial | Home offers no harness or model choice; hires never use a CLI harness; no Antigravity, OpenCode, Grok Build or Apple Intelligence harness |
| Own workspace folder | `~/.opencompany/workspaces/<slug>/`, `uploads/`, the Canvas, the gallery node | exists | No Home file browser; the Canvas shows only pushed items |
| Tools and MCP | App-registry tools (`server/config/employee_apps.json`), the Agent Builder from Talk, the Skills library | partial | No MCP connectors in Home (`docs-internal/normal_mode.md`, Known gaps) |
| Assigned computers | Mobile tab: one managed local Android emulator (`docs/mobile-workspace.md`); host shell and process-manager nodes | partial | No VMs; one shared emulator per installation, Windows host only |
| Assigned browsers | Browser nodes with persistent owner profiles (`docs-internal/browser.md`), the live view, Take control, Help in browser | exists | Assignment is per node in Dev; profiles are managed in Connectors > Web browser |
| Sharing with Hub members | none | missing | Multi-user has no data isolation; threads are per workflow |
| Group | Employee teams (a lead plus specialists via Task Manager; `client/src/features/home/employee/GiveTeam.tsx`, `docs-internal/employee_teams.md`); Agent Teams in Dev (`ai_employee`, `orchestrator_agent`, `input-teammates`) | partial | Specialists never speak in the thread; one persona per chat (`client/src/features/chat/host.ts:16-21`); a session is one workflow (`server/services/chat/access.py:48-61`) |
| Group name and goal line | Team responsibilities shown in the GiveTeam review | missing | No group entity, title or goal |
| Member list, stacked avatars | `EmployeeWork` shows `member · status` per step | missing | No roster UI or stacked avatars |
| @name completion | Slash commands (`client/src/features/chat/composer/SlashMenu.tsx:16-86`) | missing | No mentions; the slash menu is the reusable pattern |
| Sidebar search | `SearchField` (`client/src/components/catalog/primitives.tsx:62-87`), used in Settings and catalogue pages | missing | Not in the sidebar |
| Bots and Groups sections | One "AI employees" section with a count (`client/src/features/home/sidebar/HomeSidebar.tsx:146-182`) | partial | No sections or grouping |
| Avatar with presence dot | `Avatar` with a status pip that pulses while working (`client/src/features/home/sidebar/HomeSidebar.tsx:99`, `client/src/features/home/ui/primitives.tsx:24-73`) | exists | Shows work status (working, ready, paused, attention, needs you), not presence |
| Name, timestamp, two-line preview | Name and role, one line each | partial | No last message and no time; summaries carry neither |
| Unread dot | Pending-drafts count badge (`client/src/features/home/sidebar/HomeSidebar.tsx:104-111`) | partial | No read markers on the server or the client |
| Sidebar toggle | PanelLeft buttons (`client/src/features/home/sidebar/HomeSidebar.tsx:224-233`, `client/src/features/home/header/HomeHeader.tsx:176-184`), persisted | exists | |
| "+" new menu (New Bot, New Group) | "New employee" (`client/src/features/home/sidebar/HomeSidebar.tsx:235-242`) opens the Hire view | partial | One action, no menu, no group creation |
| Conversation settings button | Inline controls: rename, photo menu, New conversation, the Ask first chip, the Workspace pill, the mode toggle | partial | No per-conversation settings panel; most configuration lives in Dev |
| Per-conversation wallpaper | `OrbStage`, a full-bleed layer behind Home's main column (`client/src/features/home/orb/OrbStage.tsx:17-23`) | missing | The thread fade and surfaces assume an opaque `bg-bg-app`; nothing stores a per-employee background |
| Centred header (large avatar, name, subtitle, members) | Left-aligned identity in `HomeHeader` (small avatar, name, "role · apps", status pill); the orb in the thread's `top` slot | partial | No centred conversation header; `Avatar` already has an `lg` size |
| Translucent glass bubbles | Owner bubble `bg-bg-elevated` with a tail corner (`client/src/features/chat/turns/UserTurn.tsx:59-68`); replies bubble-less in light and dark; skins paint `.chat-msg-bot` | missing | No glass tokens; blur only on scrims and the drop overlay (`--blur-scrim`) |
| User right, bot left with small avatar | `UserTurn`, and `AssistantTurn` with `ChatAvatar` | exists | |
| Emoji tapback reactions | Good/Bad on replies (`client/src/features/chat/turns/ReplyActions.tsx:21-49`), `set_chat_feedback`, told to the employee as `[feedback]` | missing | Two values, replies only; the design-system voice forbids decorative emoji (`docs-internal/design-system/IMPLEMENTATION.md:156`) |
| "Delivered" status | "Sending…", then the time; run states (a queued note, the live ring, Thinking, failure lines) | partial | Delivery means run admission (`delivery: now` or `queued`); no receipt label |
| Markdown (bold, lists) | `ReplyMarkdown` (GFM, breaks, Prism, citations) | exists | |
| Image and report attachments | Owner attachments (thumbnails, chips); document cards (`client/src/features/chat/turns/ArtifactCard.tsx`); generated StatGrid and BarChart; inline workspace images | exists | Employee-sent files appear only through the Canvas or markdown images |
| Link previews | Source chips with host and title (`client/src/features/chat/turns/SourceChips.tsx:19-63`); links open in a new tab | partial | No unfurl cards; external images gated by `SafeImage` |
| Chat effects | none (theme sound packs and Home glows exist) | missing | |
| Message context menu | Hover bars on owner turns and replies | partial | No shadcn `context-menu` primitive installed |
| Composer "+" attachment menu | "+" opens the file picker; paste; the drop overlay | partial | Not a menu |
| Pill text field | A rounded card with auto-grow (`client/src/features/chat/composer/Composer.tsx:263-268`) | partial | A card, not a pill |
| Mic (voice messages) | The mic dictates into the box (`client/src/features/chat/composer/VoiceRecorder.tsx`, `transcribe_audio`) | partial | No voice message sent or played |
| Circular send button | Round `invert` Send with an up arrow, square Stop | exists | |
| Voice messages | Dictation; an audio file attaches as a generic chip | partial | No audio bubble or player in the thread |
| Voice calls with bots | none | missing | No realtime audio path |
| Keyboard annotations of screen regions | none | missing | |
| Screen-capture attachments | Pasting an image from the clipboard | partial | No in-app capture (`getDisplayMedia` is unused) |
| Noodlets (mini-apps in chat) | Generated UI (the twelve-component catalogue, state sync, actions); the Canvas renders sandboxed HTML in the Workspace | partial | A fixed catalogue in chat; no arbitrary code inside the thread |
| Floating conversation windows | none (Electron allows same-origin `window.open`) | missing | A single-window SPA |
| Agent Activity window | The live work card, the steps disclosure, the Workspace task line; Dev's Console and Terminal panels | partial | No standalone activity window |
| Usage window | Billing (tasks this month, employee count); Dev's credentials usage sections (API and LLM cost) | partial | Home shows no tokens or costs |
| Settings: Harness | Connectors `ai` and `developer` categories (`claude_code` and `codex_cli` login), guided Connect an AI model | partial | Sign-in exists; no per-bot harness choice |
| Settings: Bots | The sidebar and each employee's page | partial | No Bots settings page |
| Settings: Groups | none | missing | |
| Settings: Hub | none | missing | |
| Settings: Permissions | Ask first (`set_ask_first`), App access (`client/src/features/home/settings/AccessTab.tsx`), the browser read-only while asking, workspace containment, data mounts | partial | No restricted versus unrestricted sandbox switch |
| Settings: Keybindings | Fixed shortcuts (section 5.11) | missing | Not configurable |
| Settings: MCP connections | none in Home | missing | |
| Settings: Companion apps | The Workspace dock tabs | partial | |
| Companion: Computer | The Mobile tab, host shell nodes | partial | No VMs |
| Companion: Browser | The Browser tab, profiles, Take control, help requests | exists | Previews reach the chat only as Canvas items or workspace images |
| Companion: Applet | Canvas sandboxed HTML, URL and PDF items; code executors; deploy nodes | partial | No applet runner surface |
| Companion: Hub | `company deploy`, Docker, desktop hosting | partial | Hosting exists; sharing does not |
| Companion: Mobile (iOS client) | Messaging channels (Telegram, WhatsApp and others) and the web UI | partial | No native client |

## Constraints any implementation must obey

From `CLAUDE.md` section 7 (`CLAUDE.md:449-466`), quoted briefly:

1. "Compose shadcn primitives from `client/src/components/ui/` ... Do not hand-roll modals,
   dropdowns, menus, toasts, dialogs, or buttons when a primitive exists." Missing ones are
   added with `bun x shadcn@latest add <name>` (from `client/`, with
   `OPENCOMPANY_INSTALLING=true`, `docs-internal/frontend_architecture.md:703-708`).
   Installed today: accordion, alert, alert-dialog, badge, button, card, checkbox,
   collapsible, command, dialog, dropdown-menu, form, input, label, popover, progress,
   select, skeleton, slider, sonner, switch, tabs, textarea, toggle, toggle-group, tooltip,
   plus our `action-button` and `Modal`. Not installed: context-menu, sheet, scroll-area,
   hover-card, separator, resizable, avatar.
2. "Action buttons → `<ActionButton intent="...">`", with intents `run | stop | save |
   config | secret | tools`, never a colour.
3. "Style with Tailwind classes, not `style={{...}}`"; inline style only for genuinely
   dynamic values.
4. Use the token tier table (section 7.1 above).
5. "No opacity arithmetic at call sites" (`bg-primary/10`, `${color}25`): add a named
   variant to the theme instead.
6. "No palette names in components" and no theme-locked names; go through `--action-X` or
   `--node-X`.
7. "No `useAppTheme()` in new files."
8. "Icons → `lucide-react`"; backend icons go through `<NodeIcon size={token}>`, never
   sized with `h-*`, `w-*` or `text-*`.
9. "Any `draggable` element that performs a function must ship a pointer-operable,
   non-drag control reaching the same end state through the same write path" (WCAG 2.2 SC
   2.5.7); keyboard support alone does not satisfy it. The chat's attachments already
   follow this (the picker and the drop share one `addAttachments`).

Naming (`CLAUDE.md:469`): snake_case for Python, JSON config keys, WebSocket message types
and database columns; camelCase for TypeScript identifiers; hooks named `useX`; payloads
cross the wire in snake_case and are bound to camelCase locals by hand, never by a
serializer.

State and performance (`CLAUDE.md:543` onward,
`docs-internal/frontend_architecture.md:546-565`):

- Read Zustand with slice selectors, never whole-store destructuring.
- Server records live in TanStack Query, never duplicated in Zustand; mutate, then
  invalidate the key. Imperative `useEffect` plus `sendRequest` plus `setState` is a smell.
- High-frequency push state goes in a small slice-subscribed store (the
  `nodeStatusStore` and `chatRunStore` pattern), never on `WebSocketContext.value`.
- Gate catalogue and spec queries on `isReady`; entries read through
  `useSyncExternalStore` need `gcTime: GC_TIME.FOREVER`; persisted prefixes need matching
  `setQueryDefaults`.
- No polling for anything the backend can push; panels whose data agents write while they
  are closed declare `refetchOnMount: 'always', staleTime: 0` (`CLAUDE.md:2812`).
- `React.memo` with `nodePropsEqual` on canvas nodes (editor only).

Real-time and wire contracts:

- New server-to-client lifecycle broadcasts use a typed CloudEvents factory
  (`source = "opencompany://services/<area>"`, `type = "com.opencompany.<area>.<event>"`,
  `subject` = the entity id, scope inside `data`); clients validate and dedupe by
  `(source, id)`, and `server/tests/test_status_broadcasts.py` keeps raw call sites out
  (`CLAUDE.md:2791`). Plugin-owned events live in the plugin's `_events.py`.
- Identity-only broadcasts plus an authorized refetch is the house pattern
  (`chat.updated`, `canvas_updated`, `employee_lifecycle`).
- A `WebSocketContext` switch case shadows the default listener fan-out unless it calls
  `dispatchToListeners` (`client/src/contexts/WebSocketContext.tsx:998-1008`, and the
  warning at `client/src/contexts/WebSocketContext.tsx:1219`).
- The chat protocol (`docs-internal/chat_protocol.md:10-11`): "Change this document in the
  same commit as any change to the shapes below, and bump `protocol_version` ... when an
  existing field changes meaning." Run events go only to subscribed sockets, in
  snake_case, in the CloudEvents envelope; chat commands refuse the internal socket and
  non-owners.
- Module boundaries: `client/src/features/chat/` and `client/src/features/home/genui/`
  are importable only through their `index.ts` (ESLint).
- Contracts locked by tests include `server/tests/test_genui_catalog_sync.py`,
  `server/tests/test_hire_payload_contract.py`, `server/tests/test_home_catalog_contract.py`,
  `server/tests/test_frontend_no_node_type_copies.py` (no node-type strings copied into
  the client, which is why the Canvas tab id is `board`,
  `client/src/features/home/state/homeStore.ts:18-21`),
  `server/tests/test_credential_catalogue_consumer_fields.py` and
  `server/tests/test_status_broadcasts.py`.

Theme and motion:

- Home shows light and dark only (`ShellThemeProvider`, `baseOnly`); never branch on a
  theme name in a component; visual differences belong in theme CSS
  (`docs-internal/theme_system.md:268-279`).
- Motion goes through `client/src/lib/motion.ts` and the `--dur-*` / `--ease-*` tokens;
  honour reduced motion and the hidden-page pause; never animate `opacity` on whole canvas
  nodes.
- Use the Home radii tokens (`rounded-row`, `-card`, `-panel`, `-draft`, `-composer`,
  `-pill`) where a shape must follow the theme; `rounded-sm` to `rounded-xl` do not.
- The design handoff's chat rule: no custom colour or size tokens; tinted surfaces use the
  action recipe. The repo's tokens win over the bundle's values.
- Design-system voice: sentence case, second person, "No decorative emoji — in-product
  emoji are functional node glyphs only"
  (`docs-internal/design-system/IMPLEMENTATION.md:156`).

Security posture the chat already enforces: model-written images load only from the
workspace route; links open with `noopener noreferrer`; attachments are rebuilt server
side from `uploads/` (at most six); Canvas iframes keep their sandbox matrix and never
`allow-same-origin`; `NEVER_INLINE` content types are served as attachments.

## Reuse versus new, and integration points

### Reusable as is or with small extensions

| Piece | Path | Reuse notes |
|---|---|---|
| Chat pane and host slots | `client/src/features/chat/ChatPane.tsx`, `client/src/features/chat/host.ts` | `top`, `notices`, `afterThread`, `emptyState`, `footnote` and `compact` already let a host frame the conversation |
| Thread mechanics | `client/src/features/chat/thread/ChatThread.tsx`, `client/src/features/chat/thread/model.ts`, `client/src/features/chat/thread/timeLabel.ts` | Sticky bottom, unread-while-scrolled count, rise-in, restart divider, human time labels |
| Turns | `client/src/features/chat/turns/UserTurn.tsx`, `client/src/features/chat/turns/AssistantTurn.tsx`, `client/src/features/chat/thread/ChatAvatar.tsx` | Right bubbles, left avatar rows, the live ring, theme hooks |
| Composer | `client/src/features/chat/composer/Composer.tsx`, `SlashMenu.tsx`, `VoiceRecorder.tsx`, `attachments.ts`, `DropOverlay.tsx` (same folder), `client/src/features/chat/state/composerStore.ts`, `client/src/features/chat/state/attachmentStore.ts` | "+", mic, round Send/Stop, the chips slot, per-session drafts; the slash menu is the pattern for a mention list |
| Attachment display | `client/src/features/chat/turns/MessageAttachments.tsx`, `client/src/features/chat/markdown/SafeImage.tsx` | Image thumbnails and file chips |
| Reply extras | `client/src/features/chat/turns/ReplyActions.tsx`, `VersionStepper.tsx`, `StepsDisclosure.tsx`, `SourceChips.tsx`, `FollowUps.tsx`, `ArtifactCard.tsx`, `GeneratedUiBlock.tsx` (same folder), `client/src/features/chat/approval/ApprovalCard.tsx` | Rating, versions, steps, citations, suggestions, document cards, generated UI, drafts |
| Run plumbing | `client/src/stores/chatRunStore.ts`, `client/src/lib/agui/`, `client/src/features/chat/data/` | Ordered, resyncing run events and optimistic sends |
| Identity primitives | `client/src/features/home/ui/primitives.tsx`, `client/src/components/catalog/presentation.ts`, `client/src/components/catalog/primitives.tsx` | `Avatar` (with `lg`), `StatusDot`, `StatusPill`, `MicroLabel`, `SearchField`, `AppMark`, `AVATAR_CLASS` |
| Home shell and store | `client/src/features/home/HomeShell.tsx`, `client/src/features/home/state/homeStore.ts`, `client/src/features/home/ui/pillToast.tsx` | View switching, the persisted UI-prefs pattern (zod-validated localStorage), toasts |
| Team data | `client/src/features/home/data/employees.ts`, `presentation.ts`, `schemas.ts` (same folder) | The query plus lifecycle-listener pattern; presentation tables |
| Settings frame | `client/src/features/home/settings/HomeSettings.tsx` (`PAGES`), `client/src/components/catalog/CatalogLayout.tsx`, `client/src/components/credentials/CredentialsBrowser.tsx` | Adding a page is one entry |
| Workspace | `client/src/features/home/workspace/WorkspaceDock.tsx`, `client/src/components/workspace/WorkspaceTabs.tsx`, `client/src/components/workspace/FullView.tsx`, `client/src/components/browser/BrowserWorkspace.tsx`, `client/src/components/mobile/MobileWorkspace.tsx` | Browser and device views, a resizable dock |
| Motion, sound, theme | `client/src/lib/motion.ts`, `client/src/lib/sound.ts`, `client/src/themes/`, `client/src/contexts/ThemeContext.tsx` | Token-driven animation, sound packs, the theme reveal |
| Desktop shell | `desktop/src/main/index.ts` | Same-origin windows are allowed |

### New work implied by Noodle's concepts

- Group conversations: a session model that is not one workflow, author identity per
  message (today only `role`), several personas in one thread, a member roster, stacked
  avatars and mentions.
- Sidebar conversation metadata: the last message, its time and unread state need server
  fields and read markers that do not exist; sections and search are client work over
  them.
- Wallpapers and glass: a per-conversation background store and upload path, and new
  translucent surface and blur tokens defined for light and dark with legible text over
  photos and video.
- Reactions: a wire field, storage and UI beyond the two-value feedback.
- Voice messages (an audio bubble and player), voice calls (realtime audio), screen
  capture and region annotation.
- Link unfurls (a server-side fetch with its own security review), chat effects, a message
  context menu (a new primitive), floating windows, an activity window and a usage window.
- Hub sharing (needs multi-user data isolation first), MCP connectors, keybinding
  settings, and Home controls for harness, model and backstory.

### Integration points

- **Sidebar**: `client/src/features/home/sidebar/HomeSidebar.tsx` (`TeamList`,
  `EmployeeRow`), fed by `useEmployeesQuery` and the summary built in
  `server/services/employees/summaries.py`; refreshed through `employee_lifecycle`.
- **Conversation header**: `client/src/features/home/header/HomeHeader.tsx` (`Identity`)
  and the chat host's `top` slot in `client/src/features/home/employee/EmployeeChat.tsx`
  (where the orb sits now).
- **Wallpaper**: the `<main>` column in `client/src/features/home/HomeShell.tsx:81-106`,
  where `OrbStage` already draws a full-bleed `z-0` layer behind `z-10` content; the
  thread's bottom fade (`client/src/features/chat/thread/ChatThread.tsx:234`) and the
  composer and bubble surfaces assume an opaque background.
- **Bubbles**: the `.chat-msg-user` and `.chat-msg-bot` hooks
  (`client/src/features/chat/turns/UserTurn.tsx:62`,
  `client/src/features/chat/turns/AssistantTurn.tsx:202`) and new tokens in
  `client/src/themes/base.css`, `client/src/themes/light.css` and
  `client/src/themes/dark.css`, bridged in `client/src/index.css`.
- **Reactions**: `client/src/features/chat/turns/ReplyActions.tsx` and the `UserTurn`
  meta bar on the client; `set_chat_feedback` (`server/services/chat/feedback.py`, the
  `chat_feedback` table, `[feedback]` notes) is the precedent for storing a per-message
  owner signal and telling the employee.
- **Voice**: `client/src/features/chat/composer/VoiceRecorder.tsx`,
  `client/src/lib/workspaceUpload.ts`, `transcribe_audio` and `dictation_status` in
  `server/nodes/speech/`, the `textToSpeech` node's `AudioRef`
  (`docs-internal/media_transport.md`), and
  `client/src/features/chat/turns/MessageAttachments.tsx` for playback.
- **Mentions**: `client/src/features/chat/composer/SlashMenu.tsx` and
  `client/src/features/chat/composer/slash.ts`.
- **Group chat**: `ChatHost.persona` (`client/src/features/chat/host.ts:16-21`),
  `AssistantTurn` (one persona), the message schema
  (`client/src/features/chat/data/schemas.ts:69-111`), `server/services/chat/access.py`
  and the talk line in `server/services/employees/talk.py`; teams in
  `server/services/employees/team_runtime.py` and the Task Manager.
- **Floating windows**: `desktop/src/main/index.ts:159-163` and the SPA's shell
  (`client/src/app/ShellModeSwitch.tsx`).

## Open questions for the RFC author

1. Is a Noodle bot an OpenCompany employee (a whole workflow with a trigger and a talk
   line), or should a lighter chat-only agent exist beside employees?
2. Should a group be a new conversation type spanning several workflows, or the existing
   team inside one workflow with its specialists made visible and addressable in the
   thread?
3. Should Home keep its light-and-dark-only rule, or do wallpapers and glass surfaces call
   for a new appearance dimension, and how do the ten skins relate to it?
4. How do emoji reactions square with the design system's "no decorative emoji" rule, and
   should a reaction reach the employee the way a rating does?
5. Should a conversation outlive Reset, Apply and New conversation, and should one
   employee have several conversations (Noodle-style threads) instead of one clearable
   thread?
6. What does "Delivered" mean here: saved, admitted as a run, or picked up by the
   workflow? Today those are distinct states.
7. Sidebar previews and unread state need per-owner read markers on the server; with
   multi-user lacking isolation, whose read state is it?
8. Hub-style sharing presupposes data isolation that `AUTH_MODE=multi` does not provide;
   is isolation in scope?
9. Should Home expose harness and model per employee, given that hires always build
   `aiAgent` and the CLI harnesses exist only as Dev node types?
10. The documentation drift in finding 9 (Apply and Turn on Talk, the Mobile tab, the
    Settings page list) should be settled against the code before the RFC cites
    `docs-internal/normal_mode.md`.
11. The no-polling rule already has exceptions; should new presence or activity features
    be push-only (a new CloudEvents broadcast) or may they poll?
