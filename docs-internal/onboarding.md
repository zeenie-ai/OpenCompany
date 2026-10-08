# Onboarding

## Overview

Onboarding is the **Welcome guide** on Home (Normal mode, [normal_mode.md](./normal_mode.md)): three steps, Welcome, Connect an AI model and Your first hire, in a dialog shaped like Home's Settings (a left nav, the page, a footer bar). It opens by itself the first time Home shows for an owner who has not finished it, and again from the header's **Guide** button or **Settings → Help → Welcome guide · Replay**. Progress is saved on the user settings row (`onboarding_completed`, `onboarding_step`), so it resumes where the owner left it, and skipping finishes it.

The guide replaced the editor's four-step wizard (it taught blocks and the Start button, and only appeared once Dev mode opened). The design is the onboarding handoff of 2026-10-08 (`design_handoff_onboarding/`, untracked; its README's revisions R1 and R2 trim the copy).

Once the guide is finished, a dismissable **Get started checklist** sits in Home's bottom-right corner and ticks off four first steps (see below). The editor has neither.

## Files

```
client/src/features/home/
├── onboarding/
│   ├── WelcomeGuide.tsx        # the dialog: nav, the three steps, the footer bar
│   ├── useOnboarding.ts        # saved progress + moves (goTo / next / back / skip / complete)
│   ├── GetStartedChecklist.tsx # the corner card and its folded pill
│   ├── useGetStarted.ts        # the four steps' state, the two latches, useShowGetStarted
│   ├── getStartedItems.ts      # the steps, in order
│   └── steps/
│       ├── WelcomeStep.tsx     # eyebrow with the owner's call name, the headline, the demo
│       ├── WelcomeDemo.tsx     # three beats and a stage drawing the picked one
│       ├── ConnectStep.tsx     # the shared CredentialsBrowser, cut to AI models
│       └── FirstHireStep.tsx   # starter chips + Home's composer, bound to the same draft
├── header/GuideButton.tsx      # the header's Guide pill
├── settings/HelpTab.tsx        # Settings > Help: Replay the guide, Show the checklist
└── state/homeStore.ts          # `guide` state: open, step, furthest, checked
```

`HomeShell.tsx` mounts `WelcomeGuide` and `GetStartedChecklist` beside `HomeSettings`.

## State

The open state and the step live in `homeStore` (`guide: { open, step, furthest, checked, provider, pendingDraft }`, steps `GUIDE_STEPS = ['welcome', 'connect', 'first-hire']`; `provider` is the page step 2 shows in place of its list, `pendingDraft` a step-3 job waiting for a model), so every entry point opens the guide directly (`openGuide(step)`, which clears both) and nothing counts replays. That is what keeps a remount from reopening it: a mode switch re-keys the screens, and the old wizard's replay counter reopened the wizard on every remount after one replay.

`useOnboarding()` (mounted once, in `WelcomeGuide`) adds the saved side:

- **First launch.** When the settings query first succeeds it calls `checkGuide(settings)`, which runs once per session (`checked`). For an owner whose `onboarding_completed` is false it opens the guide at `GUIDE_STEPS[onboarding_step]`, or at Welcome when that index is not a step (rows saved by the old four-step wizard can hold 2 or 3). A finished owner never sees it unasked.
- **Moves.** `goTo(step)` (the nav, Next, Back, "I'll do this later") saves `{ onboarding_step }`.
- **Finishing.** `skip()` (Skip for now, the close button, Esc, a click outside) saves `{ onboarding_completed: true, onboarding_step: <step reached> }`; `complete()` (the last step's Create) saves `onboarding_step: 3`. A replay never writes `onboarding_completed: false`.

It reads and writes the settings through `useOwnerSettings` / `useSaveUserSettingsMutationCore` with the stable `useWebSocketActions()`, so Home never re-renders on a WebSocket message.

Existing owners keep skipping: the server's migration marked every row with `examples_loaded = 1` as `onboarding_completed = 1` when the columns were added (`server/core/database.py` `_migrate_user_settings`).

## The dialog

`WelcomeGuide` uses the shared `Modal` the way `HomeSettings` does (`hideHeader`, `motion="spring"`, `scrollableBody={false}`, `rounded-panel bg-bg-panel shadow-dialog`), at `min(var(--w-guide), 100vw - 3rem)` by `min(var(--h-guide), 100vh - 3rem)` (940 by 620).

- **Nav** (`w-(--w-settings-nav)`): the "Get started" label, then one Radix Tabs trigger per step (`NAV_ITEM` from `features/home/ui/nav.ts`, shared with Settings), with Sparkles / KeyRound / UserPlus. A step is ticked (`CircleCheck` in `action-run-ink`) once passed; Connect is ticked as soon as any AI model is connected. The nav reaches the steps already visited (`furthest`) and always Connect; the others are disabled triggers. The Open Council mark sits at the foot.
- **Footer**: Skip for now, the mono "n / 3", Back (hidden on Welcome), then Next (`ActionButton intent="tools"`) on Welcome and on Connect once a model is connected, or "I'll do this later" on Connect without one. The last step has no footer button: its composer's own Create is the action.
- Inactive steps unmount (Radix Tabs), and each step's blocks rise in with the Settings stagger (`staggerSettings`).

### Step 1, Welcome

The eyebrow says "Welcome, {call name}" (`callName` from the profile; "Welcome" without one) in the agent ink, over "Your AI team, hired in plain words." (`text-headline`). The demo beside it has three beats, each a button: Describe the job (a small composer with the job typed in), Check and hire (the setup card as the R2 design draws it: identity, a WHEN / THEY / THEN routine, "Ask before sending" and Hire), and Talk to them (the owner's "Hi Maya!", the reply, and a WhatsApp draft that "Needs you"). The stage (`.home-welcome-stage`, two washes of `--node-agent-fill` and `--node-model-soft` over `--bg-app`) is decoration (`aria-hidden`), drawn with token classes, never real controls. It plays by itself on an 80 ms clock (`TICK_MS`): the job types itself a character a tick and Create pops when it is written, a pause, then the setup (its rows rising one by one, then Hire), then the conversation (the reply, then the draft), each beat for its `BEAT_TICKS`, round again; a bar under the playing beat fills as it plays (an inline `scaleX` of the clock), and clicking a beat starts it. It runs only while the step shows, since inactive steps unmount. Under reduced motion there is no clock: the conversation shows, still, with no bar, and a click shows another beat.

### Step 2, Connect an AI model

The shared `CredentialsBrowser` (the same as Settings > Connectors), handed a catalogue cut to `consumer_category === 'ai'` with no categories, so Yours counts AI models only and there is no category filter. It runs with `variant="embedded"` (a smaller title and search box), `discoverLimit={null}` (every AI model, so the ones that run on this computer are not behind "Show all") and AI copy: "Connect an AI model" ("You're connected" once one is), "Search AI models", "AI models", and "No AI models connected yet".

Connect and Manage open the provider's page **in place of the list** (`components/credentials/ProviderPage.tsx`, the page the credentials dialog shows as its second layer, here with its own "Connect {name}" heading and focus on "All AI models"). The footer's Back and Next step aside while it shows; "All AI models" and Esc return to the list, focusing the card it came from (a disconnect confirmation, its own layer, closes first on Esc). The list stays mounted underneath, hidden, so a card that turns connected still glows and reports it through `onItemAdded`: a pill says "{Provider} is connected" and, after a connect, the step returns to the list (Manage stays open).

**A job waiting for a model.** On step 3 without a model, Create (or the notice's button) goes to step 2 with `guide.pendingDraft` set when the box holds a job. Connecting a model then finishes the guide, shows the hire view and sends the job. Closing the guide drops the flag. This is the guide's own path; a draft Home already sent that failed with `no_ai_provider` is still re-sent by `useHireComposer` once a model is connected anywhere.

### Step 3, Your first hire

"Who should we hire first?", the starter chips (left-aligned, without Hire now) and Home's `Composer` (`flat`, `idleLabel="Create their setup"`), bound through genui's `useJobComposer()`: the same draft as Home's composer, without the effects `useHireComposer` runs (they stay mounted once, in the hire view), and picking a starter takes no focus from Home. Create finishes the guide, shows the hire view and sends the job; the setup then writes itself under Home's composer. Without an AI model it sends nothing: a pink notice, "Connect an AI model first.", and its button (or Create) lead to step 2, where the job waits (above).

## Get started checklist

**Location**: `features/home/onboarding/GetStartedChecklist.tsx`, state in `useGetStarted.ts`, steps in `getStartedItems.ts` (onboarding handoff D).

A card fixed in Home's bottom-right corner (`w-80`, `shadow-popover`), shown once `onboarding_completed` is true and until `getting_started_dismissed`, and never while the Welcome guide is open. Four steps, each ticking itself:

| Step | Done when | Under the label | A click |
|------|-----------|-----------------|---------|
| Connect an AI model | an AI model is connected now (`useConnectors().hasAi`) | "OpenAI, Anthropic, Gemini or a local model", then "{Provider} is connected" | the guide at Connect |
| Hire your first employee | someone on the team was hired (not `derived`) | "Describe a job or pick a starter", then "{Name}, {role}" | the hire view, composer focused |
| Say hello | latched: the owner has written to their first hire (the one hired earliest) | "Send {Name} a message" | their page |
| Approve a first draft | latched: an `approval_lifecycle` `decided` event with status `approved` arrived while Home was open | "They ask before sending anything" | the page of whoever has a draft waiting, else the first hire |

The two latches are `UserSettings.getting_started_said_hello` and `getting_started_approved_draft`, written once each (the saved flag is the guard), since New conversation or a restart empties the thread and an approval is an event. The first hire's conversation is read only until Say hello is latched. The card folds to a "Get started · n/4" pill; hiding it says "Get started hidden. Reopen it from Settings → Help.", and Settings → Help → Get started checklist · Show brings it back (`useShowGetStarted`). The four earlier latch columns (`getting_started_added_key`, `_ran_example`, `_built_workflow`, `_tried_theme`, from the editor's checklist) are no longer read.

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
| `VITE_NORMAL_MODE=false` | There is no Home, so no guide |
| Auth disabled | Works unchanged: the settings row is the anonymous owner's |

## Tests

- `features/home/__tests__/welcomeGuide.test.tsx`: opens at the saved step, Welcome for an out-of-range step, never for a finished owner, no reopen on remount; the nav's reachability; Next saves the step; Connect lists every AI model and no apps, "I'll do this later", "You're connected"; the provider page in place of the list, Back and Esc (focus returns to the card), the pill and the return to the list on connect; closing saves the step reached; the last step finishes and sends, or sends nothing without a model until one is connected, which finishes the guide.
- `features/home/__tests__/welcomeDemo.test.tsx` (fake timers): the job typed a character a tick, the beats in turn and round again, a click starting a beat, reduced motion holding the conversation with no bar.
- `features/home/__tests__/guideStore.test.ts`: `checkGuide`, `openGuide`, `goToGuideStep`, `closeGuide`, the provider page and the waiting job.
- `components/__tests__/CredentialsModal.test.tsx`: the dialog around the same `ProviderPage`.
- `features/home/genui/__tests__/useHireComposer.test.tsx`: `useJobComposer` (no focus taken, a new job only, no dialog of its own).
- `composer.test.tsx` (`flat`, the idle label, chips without Hire now), `catalogLayout.test.tsx` (no cap, the list label), `homeHeader.test.tsx` (Guide), `homeSettings.test.tsx` (Help → Replay).
- `features/home/__tests__/getStarted.test.tsx`: shown once the guide is finished and never over it, each step's click, what is done now, each latch written once (and not for a discarded draft), the folded pill, hiding with its toast.
- Server: `tests/test_user_settings_contract.py` and `tests/services/test_getting_started_settings.py` (the latch columns, their migration and the getter).

## Key files

| File | Description |
|------|-------------|
| `client/src/features/home/onboarding/WelcomeGuide.tsx` | The dialog |
| `client/src/features/home/onboarding/useOnboarding.ts` | First-launch check and saved progress |
| `client/src/features/home/state/homeStore.ts` | `guide` state, `GUIDE_STEPS` |
| `client/src/components/credentials/CredentialsBrowser.tsx` | `copy`, `variant`, `discoverLimit`, `onItemAdded` |
| `client/src/components/credentials/ProviderPage.tsx` | One provider's page, shared by the guide and the credentials dialog |
| `client/src/components/credentials/aiProviderLinks.ts` | "Get a key from …" links for the featured AI providers |
| `client/src/components/catalog/CatalogLayout.tsx` | `variant`, `discoverLimit`, `listLabel` |
| `client/src/features/home/genui/useHireComposer.ts` | `useJobComposer` |
| `client/src/features/home/onboarding/useGetStarted.ts` | The checklist's steps and latches |
| `server/models/database.py` | `UserSettings.onboarding_completed`, `onboarding_step`, the `getting_started_*` flags |
| `server/core/database.py` | The migration that marks existing owners finished |
