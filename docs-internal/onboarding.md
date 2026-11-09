# Onboarding

## Overview

Onboarding is the **Welcome guide** on Home (Normal mode, [normal_mode.md](./normal_mode.md)): three steps, Welcome, Connect an AI model and Your first hire, in a dialog shaped like Home's Settings (a left nav, the page, a footer bar). It opens by itself the first time Home shows for an owner who has not finished it, and again from the header's **Guide** button or **Settings → Help → Welcome guide · Replay**. Progress is saved on the user settings row (`onboarding_completed`, `onboarding_step`), so it resumes where the owner left it, and skipping finishes it.

The guide replaced the editor's four-step wizard (it taught blocks and the Start button, and only appeared once Dev mode opened). The design is the onboarding handoff of 2026-10-08 (`design_handoff_onboarding/`, untracked; its README's revisions R1 and R2 trim the copy). The AI models dialog, the checklist's header pill and its wording follow the onboarding v3 handoff (`design_handoff_onboarding_v3/`, untracked); that handoff also drops the guide, which stays.

Once the guide is finished, a dismissable **Get started checklist** ticks off four first steps (see below): a card in the hire view's bottom-right corner (at the Workspace dock's left edge while the dock is open), and on an employee's page a Get started pill in the header. Whatever on Home needs an AI model opens the **AI models dialog** (below), the guide's AI model list in a dialog of its own; Settings > Connectors still opens the shared credentials dialog. The editor has none of them.

## Files

```
client/src/features/home/
├── onboarding/
│   ├── WelcomeGuide.tsx        # the dialog: nav, the three steps, the footer bar
│   ├── useOnboarding.ts        # saved progress + moves (goTo / next / back / skip / complete)
│   ├── AiModelsDialog.tsx      # Home's AI models dialog: the AI model list, a model's page
│   ├── GetStartedChecklist.tsx # the hire view's corner card and its folded pill
│   ├── GetStartedPill.tsx      # the header's Get started pill on an employee's page
│   ├── useGetStarted.ts        # the four steps' state, the two latches, the visibility rule, useShowGetStarted
│   ├── getStartedItems.ts      # the steps, in order
│   └── steps/
│       ├── WelcomeStep.tsx     # eyebrow with the owner's call name, the headline, the demo
│       ├── WelcomeDemo.tsx     # three beats and a stage drawing the picked one
│       ├── ConnectStep.tsx     # the shared CredentialsBrowser, cut to AI models (step 2 and the AI models dialog)
│       ├── AiModelPage.tsx     # a model's page in the AI models dialog: the plain page, else the full one
│       └── FirstHireStep.tsx   # starter chips + Home's composer, bound to the same draft
├── header/GuideButton.tsx      # the header's Guide pill
├── header/HomeHeader.tsx       # shows GetStartedPill on an employee's page
├── settings/HelpTab.tsx        # Settings > Help: Replay the guide, Show the checklist
└── state/homeStore.ts          # `guide` (open, step, furthest, checked), `connectAI`, `getStartedFolded`
```

`HomeShell.tsx` mounts `WelcomeGuide` and `AiModelsDialog` beside `HomeSettings`, and `GetStartedChecklist` on the hire view only; on an employee's page `HomeHeader` shows `GetStartedPill` in its place.

## State

The open state and the step live in `homeStore` (`guide: { open, step, furthest, checked, provider, pendingDraft }`, steps `GUIDE_STEPS = ['welcome', 'connect', 'first-hire']`; `provider` is the page step 2 shows in place of its list, `pendingDraft` a step-3 job waiting for a model), so every entry point opens the guide directly (`openGuide(step)`, which clears both) and nothing counts replays. That is what keeps a remount from reopening it: a mode switch re-keys the screens, and the old wizard's replay counter reopened the wizard on every remount after one replay.

`useOnboarding()` (mounted once, in `WelcomeGuide`) adds the saved side:

- **First launch.** When the settings query first succeeds it calls `checkGuide(settings)`, which runs once per session (`checked`). For an owner whose `onboarding_completed` is false it opens the guide at `GUIDE_STEPS[onboarding_step]`, or at Welcome when that index is not a step (rows saved by the old four-step wizard can hold 2 or 3). A finished owner never sees it unasked.
- **Moves.** `goTo(step)` (the nav, Next, Back, "I'll do this later") saves `{ onboarding_step }`.
- **Finishing.** `skip()` (Skip for now, the close button, Esc, a click outside) saves `{ onboarding_completed: true, onboarding_step: <step reached> }`; `finish()` (the last step's Create, or a model connected while a job waits) saves `onboarding_step: 3`, shows the hire view and sends the job through genui's `useSendJob` (it reads the box when sending, so the guide does not re-render as the job is typed). A replay never writes `onboarding_completed: false`.

It reads and writes the settings through `useOwnerSettings` / `useSaveUserSettingsMutationCore` with the stable `useWebSocketActions()`, so Home never re-renders on a WebSocket message.

Existing owners keep skipping: the server's migration marked every row with `examples_loaded = 1` as `onboarding_completed = 1` when the columns were added (`server/core/database.py` `_migrate_user_settings`).

## The dialog

`WelcomeGuide` uses the shared `Modal` the way `HomeSettings` does (`hideHeader`, `motion="spring"`, `scrollableBody={false}`, `rounded-panel bg-bg-panel shadow-dialog`), at `min(var(--w-guide), 100vw - 3rem)` by `min(var(--h-guide), 100vh - 3rem)` (940 by 620).

- **Nav** (`w-(--w-settings-nav)`): the "Get started" label, then one Radix Tabs trigger per step (`NAV_ITEM` from `features/home/ui/nav.ts`, shared with Settings), with Sparkles / KeyRound / UserPlus. A step is ticked (`CircleCheck` in `action-run-ink`) once passed; Connect is ticked as soon as any AI model is connected. The nav reaches the steps already visited (`furthest`) and always Connect; the others are disabled triggers. The Open Council mark sits at the foot.
- **Footer**: Skip for now, the mono "n / 3", Back (hidden on Welcome), then Next (`ActionButton intent="tools"`) on Welcome and on Connect once a model is connected, or "I'll do this later" on Connect without one. The last step has no footer button: its composer's own Create is the action.
- Inactive steps unmount (Radix Tabs), and each step's blocks rise in with the Settings stagger (`staggerSettings`).

### Step 1, Welcome

The eyebrow says "Welcome, {call name}" (`callName` from the profile; "Welcome" without one) in the agent ink, over "Your AI employees, hired in plain words." (`text-headline`). The demo beside it has three beats, each a button: Describe the job (a small composer with the job typed in), Check and hire (the setup card as the R2 design draws it: identity, a WHEN / THEY / THEN routine, "Ask before sending" and Hire), and Talk to them (the owner's "Hi Maya!", the reply, and a WhatsApp draft that "Needs you"). The stage (`.home-welcome-stage`, two washes of `--node-agent-fill` and `--node-model-soft` over `--bg-app`) is decoration (`aria-hidden`), drawn with token classes, never real controls. It plays by itself on an 80 ms clock (`TICK_MS`): the job types itself a character a tick and Create pops when it is written, a pause, then the setup (its rows rising one by one, then Hire), then the conversation (the reply, then the draft), each beat for its `BEAT_TICKS`, round again; a bar under the playing beat fills as it plays (an inline `scaleX` of the clock), and clicking a beat starts it. It runs only while the step shows, since inactive steps unmount. Under reduced motion there is no clock: the conversation shows, still, with no bar, and a click shows another beat.

### Step 2, Connect an AI model

`ConnectStep`: the shared `CredentialsBrowser` (the same as Settings > Connectors), handed a catalogue cut to `consumer_category === 'ai'` with no categories, so Yours counts AI models only and there is no category filter. It runs with `variant="embedded"` (a smaller title and search box), `discoverLimit={null}` (every AI model, so the ones that run on this computer are not behind "Show all") and AI copy: "Connect an AI model" ("You’re connected" once one is), "Search AI models", "AI models", and "No AI models connected yet". The AI models dialog shows the same list. `ConnectStep` takes its state from its host (`selection`, `onSelect`, `finishOnConnect`, `onFinish` and an optional `renderPage`); the guide passes `guide.provider` and `guide.pendingDraft` and no `renderPage`, so here a model's page is always the full one.

Connect and Manage open the provider's page **in place of the list** (`components/credentials/ProviderPage.tsx`, the page the credentials dialog shows as its second layer, here with its own "Connect {name}" heading and focus on "All AI models"). The footer's Back and Next step aside while it shows; "All AI models" and Esc return to the list, focusing the card it came from (a disconnect confirmation, its own layer, closes first on Esc). The list stays mounted underneath, hidden, so a card that turns connected still glows and reports it through `onItemAdded`: a pill says "{Provider} is connected" and, after a connect, the step returns to the list (Manage stays open).

**A job waiting for a model.** On step 3 without a model, Create (or the notice's button) goes to step 2 with `guide.pendingDraft` set when the box holds a job. Connecting a model then finishes the guide, shows the hire view and sends the job. Closing the guide drops the flag. This is the guide's own path; a draft Home already sent that failed with `no_ai_provider` is still re-sent by `useHireComposer` once a model is connected anywhere.

### Step 3, Your first hire

"Who should we hire first?", the starter chips (left-aligned, without Hire now) and Home's `Composer` (`flat`, `idleLabel="Create their setup"`), bound through genui's `useJobComposer()`: the same draft as Home's composer, without the effects `useHireComposer` runs (they stay mounted once, in the hire view), and picking a starter takes no focus from Home. Create finishes the guide, shows the hire view and sends the job; the setup then writes itself under Home's composer. Without an AI model it sends nothing: a pink notice, "Connect an AI model first.", and its button (or Create) lead to step 2, where the job waits (above).

## The AI models dialog

**Location**: `features/home/onboarding/AiModelsDialog.tsx`, a model's page in `steps/AiModelPage.tsx`, state in `homeStore.connectAI` (onboarding v3, "AI models dialog").

Whatever on Home needs an AI model opens it with `homeStore.openConnectAI()`: a setup answered `no_ai_provider` (`useHireComposer`), a hire answered `needs_ai` (`useHire`), Start refused with `needs_ai` and the employee's Connect an AI model action (`useEmployeeControl`), the draft panel's Connect an AI model, and the checklist's first row. It sets `connectAI: { open: true, provider: null, closeOnConnect }` and spikes the orb. It no longer opens the app shell's credentials dialog, which Home still opens to connect an app and from Settings > Connectors.

It uses the shared `Modal` as the guide does (`hideHeader`, `motion="spring"`, `rounded-panel bg-bg-panel shadow-dialog`), at `min(var(--w-ai-models), 100vw - 3rem)` by `min(var(--h-ai-models), 100vh - 3rem)` (780 by 600), with a Close button and no nav or footer. It holds `ConnectStep`, step 2's list of every AI model in catalogue order, and shows a model's page in place of the list (`setConnectAIProvider`); its blocks rise in when it opens and when the list and a page swap. Close, Esc and a click outside close it; Esc first leaves a model's page, and a disconnect confirmation (its own layer) before that. Focus goes back to whatever had it when the dialog opened. Closing keeps the page, so the dialog does not change page while it closes, and the next open starts on the list.

The list reports a connect, as on step 2: the card glows and a pill says "{Provider} is connected". With `closeOnConnect` (the default) the dialog then closes, so the task that wanted a model goes on: a setup refused for want of one is asked for again (`useHireComposer`) and written for review, and nothing hires on its own. The checklist's row opens it with `closeOnConnect: false`, so after a connect from a model's page it returns to the list, now titled "You’re connected". The checklist steps aside while the dialog is open.

**A model's page** (`AiModelPage`). A model the catalogue describes as one field and a button gets the plain page (`hasPlainPage`: kind `apiKey`, exactly one field, and no `endpoints`, `sourceRequired` or `ws.status`), which covers the API-key models and the local servers (Ollama, LM Studio). It shows the logo, name and description, a Connected pill once connected, and a form:

- the field, labelled "API key", or "Server address" for a server, with "Get a key from {name}" beside the label for the featured providers (`aiProviderLinks.ts`);
- a help line: "OpenCompany keeps your key encrypted.", or "Start {name} first. OpenCompany connects to it at this address.";
- an error alert that keeps what was typed;
- Connect (Save once connected), the invert button, which says "Checking…" while the key is checked;
- once connected, a neutral Disconnect that asks first (`ConfirmDisconnect`, the confirmation the catalog cards use).

It never shows a saved key: the key field is typed into, its placeholder saying "•••••••• saved" once connected, while a server's address shows the saved one or the catalogue's default. Save on a model already connected says "{Provider} is connected" and returns to the list; Disconnect says "{Provider} disconnected" and returns too. The plain page offers no 1Password choice (Settings > Connectors and the full page keep it). Every other model gets the full page, `ProviderPage` as the guide shows it (`FullProviderPage`): the Gateway with its allowance line, the named OpenAI-compatible endpoints, a model whose install requires 1Password, and a key saved as a 1Password binding, which only the full page can change (the plain page switches to it once the saved values show the binding).

The cards are the shared catalog cards (`components/catalog/CatalogLayout.tsx`, as in Settings, Dev's credentials dialog and step 2), whose controls are neutral: the "+" and the remove hovers, a bordered Manage and the ink enable switch. The green done mark stays, since it marks a state.

## Get started checklist

**Location**: `features/home/onboarding/GetStartedChecklist.tsx` (the card), `GetStartedPill.tsx` (the header pill), state and the visibility rule in `useGetStarted.ts`, steps in `getStartedItems.ts` (onboarding handoff D; the pill and the wording under each step are onboarding v3).

A card fixed in the hire view's bottom-right corner (`w-80`, `shadow-popover`): `HomeShell` mounts it on the hire view only. While the Workspace dock is open, the card sits at the dock's left edge instead, a 1rem margin from it but never past the window's left edge (`right: min(calc(<dock width> + 1rem), calc(100vw - 21rem))`), and it moves with the dock as it opens, closes or is expanded, on the dock's own timing (`--dur-dock-in` while open, `--dur-sidebar-out` back to the corner), and at once while the dock's edge is dragged (`workspaceResizing` in `homeStore`). The dock's width is `dockWidth` in `homeStore`, which the dock reads too, passed as `--dock-width`. On an employee's page, where it would cover Send, the header's **Get started** pill ("Get started · n/4", before the Guide pill) stands in for it, and a click goes back to the hire view with the card open and the composer focused (`homeStore.openGetStarted`). The card and the pill show by one rule (`useGetStarted().visible`): the settings have loaded, `onboarding_completed` is true, `getting_started_dismissed` is not, and neither the Welcome guide nor the AI models dialog is open. Four steps, each ticking itself:

| Step | Done when | Under the label | A click |
|------|-----------|-----------------|---------|
| Connect an AI model | an AI model is connected now (`useConnectors().hasAi`) | "So your team can think", then "{Provider} is connected" | the AI models dialog, which stays on its list after a connect (`openConnectAI({ closeOnConnect: false })`) |
| Hire your first employee | someone on the team was hired (not `derived`) | "Describe a job in plain words", then "{Name} joined your team" | the hire view, composer focused |
| Say hello | latched: the owner has written to their first hire (the one hired earliest) | "Send {Name} a first message" ("Once your first hire joins" before anyone is hired) | their page, message box focused |
| Approve a first draft | latched: an `approval_lifecycle` `decided` event with status `approved` arrived while Home was open | "Nothing goes out until you say so" | the page of whoever has a draft waiting, else the first hire, message box focused |

The two latches are `UserSettings.getting_started_said_hello` and `getting_started_approved_draft`, written once each, since New conversation or a restart empties the thread and an approval is an event. The saved flag guards each, and only one of the card (on the hire view) and the pill (on an employee's page) runs `useGetStarted` at a time, so a latch is never saved twice. The first hire's conversation is read only until Say hello is latched.

The card rises in whenever it opens (`rise`, over `--dur-panel-in`): when it first shows, when it unfolds and when the owner comes back to the hire view. It folds to a "Get started · n/4" pill in the same place, the corner or the dock's edge; whether it is folded is kept in `homeStore` for the session (`getStartedFolded`, `setGetStartedFolded`), so it outlives the card. Once all four steps are done it says "You’re all set!" and offers Done; Done and the X both hide it, saying "Get started hidden. Reopen it from Settings → Help.", and Settings → Help → Get started checklist · Show brings it back (`useShowGetStarted`). A done step's disc is on the working status tokens (`status-working-*`), and Done, the card's sparkle and both pills are neutral, since the hire view's one primary action is Create. The four earlier latch columns (`getting_started_added_key`, `_ran_example`, `_built_workflow`, `_tried_theme`, from the editor's checklist) are no longer read.

## WebSocket handlers

No onboarding-specific handlers. Progress rides the user settings handlers (`server/services/settings/handlers.py`) through the TanStack Query layer:

| Handler | Usage |
|---------|-------|
| `get_user_settings` | `onboarding_completed` and `onboarding_step` for the first-launch check; the checklist's `getting_started_*` flags |
| `save_user_settings` | the step on every move; `onboarding_completed` on skip and complete; the checklist's latches and `getting_started_dismissed` |

## Edge cases

| Scenario | Behaviour |
|----------|-----------|
| Settings query not resolved yet | The guide stays closed until it resolves |
| Settings query fails | The guide stays closed; the Guide button still opens it |
| A mode switch or remount | Does not reopen it: `checked` holds for the session |
| Saved step from the old wizard (2 or 3) | 2 opens Your first hire; 3 or more opens Welcome |
| Multiple tabs | Finishing in one tab does not close it in another until that one reloads |
| `VITE_NORMAL_MODE=false` | There is no Home, so no guide, no checklist and no AI models dialog |
| Auth disabled | Works unchanged: the settings row is the anonymous owner's |

## Tests

- `features/home/__tests__/welcomeGuide.test.tsx`: opens at the saved step, Welcome for an out-of-range step, never for a finished owner, no reopen on remount; the nav's reachability; Next saves the step; Connect lists every AI model and no apps, "I'll do this later", "You're connected"; the provider page in place of the list, Back and Esc (focus returns to the card), the pill and the return to the list on connect; closing, Skip and Esc save the step reached (Esc leaves a provider's page first); the last step's only way on is the composer's Create, which finishes and sends, or sends nothing without a model until one is connected, which finishes the guide.
- `features/home/__tests__/welcomeDemo.test.tsx` (fake timers): the job typed a character a tick, the beats in turn and round again, a click starting a beat, reduced motion holding the conversation with no bar.
- `features/home/__tests__/guideStore.test.ts`: `checkGuide`, `openGuide`, `goToGuideStep`, `closeGuide`, the provider page and the waiting job.
- `components/__tests__/CredentialsModal.test.tsx`: the dialog around the same `ProviderPage`.
- `features/home/__tests__/aiModelsDialog.test.tsx`: the AI models dialog opens on every AI model, closes with Close and gives focus back to its opener, Esc leaves a model's page first; the plain page (what it is, where to get a key, Connect once something is typed, "Checking…", a refused key's reason with the draft kept, a local server's saved or default address, Connected, Save returning to the list, Disconnect asking first); closing once the list reports a connect, or staying on the list when the checklist opened it; the full page for the Gateway, named endpoints, an install that requires 1Password and a key held in 1Password.
- `features/home/__tests__/connectAI.test.ts`: `openConnectAI` opens the dialog on its list (to close once a model connects, or to stay when asked), keeps the view and Settings underneath, and keeps its page while it closes.
- `features/home/genui/__tests__/useHireComposer.test.tsx`: a `no_ai_provider` answer opens the AI models dialog and the setup is asked for again once a model connects; `useJobComposer` (no focus taken, a new job only, no dialog of its own) and `useSendJob` (sends `generate_employee_setup` with what the box holds).
- `composer.test.tsx` (`flat`, the idle label, chips without Hire now), `catalogLayout.test.tsx` (no cap, the list label, the neutral card controls), `homeHeader.test.tsx` (Guide, and the Get started pill on an employee's page only), `homeSettings.test.tsx` (Help → Replay).
- `features/home/__tests__/getStarted.test.tsx`: shown once the guide is finished and never over it or the AI models dialog, each step's click (Connect an AI model opens the dialog; Approve a first draft opens whoever has one waiting), what is done now, each latch written once (and not for a discarded draft), the folded pill, the card and its pill at the open Workspace dock's left edge and back in the corner once it closes, hiding with its toast, Done once all four are done; the header pill goes back to the hire view with the card open, shows by the card's rule and writes both latches from an employee's page.
- Server: `tests/test_user_settings_contract.py` and `tests/services/test_getting_started_settings.py` (the latch columns, their migration and the getter).

## Key files

| File | Description |
|------|-------------|
| `client/src/features/home/onboarding/WelcomeGuide.tsx` | The dialog |
| `client/src/features/home/onboarding/useOnboarding.ts` | First-launch check and saved progress |
| `client/src/features/home/state/homeStore.ts` | `guide` state, `GUIDE_STEPS`, `connectAI`, `getStartedFolded`, `openGetStarted` |
| `client/src/features/home/onboarding/steps/ConnectStep.tsx` | The AI model list, shared by step 2 and the AI models dialog |
| `client/src/features/home/onboarding/AiModelsDialog.tsx` | Home's AI models dialog |
| `client/src/features/home/onboarding/steps/AiModelPage.tsx` | A model's page in the dialog: the plain page (`hasPlainPage`), else the full one |
| `client/src/components/credentials/ConfirmDisconnect.tsx` | The Disconnect confirmation, shared by the catalog cards and the plain page |
| `client/src/components/credentials/CredentialsBrowser.tsx` | `copy`, `variant`, `discoverLimit`, `onItemAdded` |
| `client/src/components/credentials/ProviderPage.tsx` | One provider's page, shared by the guide, the AI models dialog's full page and the credentials dialog |
| `client/src/components/credentials/aiProviderLinks.ts` | "Get a key from …" links for the featured AI providers |
| `client/src/components/catalog/CatalogLayout.tsx` | `variant`, `discoverLimit`, `listLabel` |
| `client/src/features/home/genui/useHireComposer.ts` | `useJobComposer`, `useSendJob` |
| `client/src/features/home/onboarding/useGetStarted.ts` | The checklist's steps, latches and visibility rule |
| `client/src/features/home/onboarding/GetStartedPill.tsx` | The header's Get started pill on an employee's page |
| `server/models/database.py` | `UserSettings.onboarding_completed`, `onboarding_step`, the `getting_started_*` flags |
| `server/core/database.py` | The migration that marks existing owners finished |
