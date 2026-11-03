# Onboarding

## Overview

Onboarding is the **Get started checklist** on Home (Normal mode, [normal_mode.md](./normal_mode.md)): a card in Home's bottom-right corner that shows from the owner's first visit and ticks off four first steps as the owner gets going (connect an AI model, hire someone, say hello, approve a first draft). It stays until the owner hides it, and **Settings → Help → Get started checklist · Show** brings it back. What it has to remember is saved on the user settings row (`getting_started_*`). The editor has none.

The design is part D of the onboarding handoff of 2026-10-08 (`design_handoff_onboarding/`, untracked). Until 2026-11-03 a three-step Welcome guide came first; see [History](#history).

## Files

```
client/src/features/home/
├── onboarding/
│   ├── GetStartedChecklist.tsx # the corner card and its folded pill
│   ├── useGetStarted.ts        # the four steps' state, the two latches, useShowGetStarted
│   └── getStartedItems.ts      # the steps, in order
├── settings/HelpTab.tsx        # Settings > Help: Show the checklist
└── state/homeStore.ts          # openConnectAI, where Connect an AI model goes
```

`HomeShell.tsx` mounts `GetStartedChecklist` beside `HomeSettings`.

## The checklist

A card fixed in Home's bottom-right corner (`w-80`, `shadow-popover`), shown as soon as the owner's settings are read and until `getting_started_dismissed`; nothing else on the settings row decides it. Four steps, each ticking itself:

| Step | Done when | Under the label | A click |
|------|-----------|-----------------|---------|
| Connect an AI model | an AI model is connected now (`useConnectors().hasAi`) | "OpenAI, Anthropic, Gemini or a local model", then "{Provider} is connected" | the app's credentials dialog on AI models (`homeStore.openConnectAI()`), over the page the owner is on |
| Hire your first employee | someone on the team was hired (not `derived`) | "Describe a job or pick a starter", then "{Name}, {role}" | the hire view, composer focused |
| Say hello | latched: the owner has written to their first hire (the one hired earliest) | "Send {Name} a message" | their page |
| Approve a first draft | latched: an `approval_lifecycle` `decided` event with status `approved` arrived while Home was open | "They ask before sending anything" | the page of whoever has a draft waiting, else the first hire |

A step that is done, or has nowhere to go yet (Say hello and Approve a first draft before anyone is hired), is not a button.

Connect an AI model opens the dialog Home's other "connect an AI model" paths open (setup's `no_ai_provider`, hire/start's `needs_ai`, an employee's Connect an AI model action; see [Connecting an AI model](./normal_mode.md#connecting-an-ai-model)): the shared connector browser on AI models, then the provider's connection form, and the dialog closes once the model connects.

The two latches are `UserSettings.getting_started_said_hello` and `getting_started_approved_draft`, written once each (the saved flag is the guard), since New conversation or a restart empties the thread and an approval is an event. The first hire's conversation is read only until Say hello is latched. The card folds to a "Get started · n/4" pill (folding is not saved). With all four done it says "You're all set!" and offers Done. Done and the card's close button hide it: they save `getting_started_dismissed` and say "Get started hidden. Reopen it from Settings → Help." Settings → Help → Get started checklist · Show closes Settings and brings it back (`useShowGetStarted` saves `getting_started_dismissed: false`). The four earlier latch columns (`getting_started_added_key`, `_ran_example`, `_built_workflow`, `_tried_theme`, from the editor's checklist) are no longer read.

Rows already marked `onboarding_completed` when the SQLite migration added `getting_started_dismissed` got it set too, so those owners do not see the checklist unasked (`server/core/database.py` `_migrate_user_settings`; the same migration had marked every row with `examples_loaded = 1` as completed).

## Fields kept unused

`UserSettings.onboarding_completed` and `onboarding_step`, the Welcome guide's saved progress, stay in the model and in the settings API (`get_user_settings` returns them, `save_user_settings` accepts them), but the client no longer reads or writes them. They stay on purpose. A `user_settings` table created with them holds them as `NOT NULL` columns with no server default, so dropping the fields from the model would break inserts on such a database. And a PostgreSQL database gets no migrations, only the shared schema check (`_validate_shared_schema` in `server/core/database.py`), which refuses a schema that lacks a column but has no way to drop one.

## WebSocket handlers

No onboarding-specific handlers. The checklist rides the user settings handlers (`server/services/settings/handlers.py`) through the TanStack Query layer:

| Handler | Usage |
|---------|-------|
| `get_user_settings` | the checklist's `getting_started_*` flags |
| `save_user_settings` | each latch, once; `getting_started_dismissed` when the card is hidden (`true`) and when Settings → Help shows it again (`false`) |

## Edge cases

| Scenario | Behaviour |
|----------|-----------|
| Settings query not resolved yet | The checklist stays hidden until it resolves |
| Settings query fails | The checklist stays hidden |
| A row the Welcome guide left unfinished | Shows the checklist: `onboarding_completed` and `onboarding_step` are not read |
| A mode switch or remount | The card comes back unfolded |
| Multiple tabs | Hiding it in one tab hides it in another only once that tab reads its settings again |
| `VITE_NORMAL_MODE=false` | There is no Home, so no checklist |
| Auth disabled | Works unchanged: the settings row is the anonymous owner's |

## Tests

- `features/home/__tests__/getStarted.test.tsx`: shown from the first visit whatever the Welcome guide saved, and on Home at first launch with no guide over it and no Guide button; hidden once hidden; each step's click (Connect an AI model opens the shared credentials dialog on AI models and leaves the page as it was; Approve a first draft opens whoever has one waiting), what is done now, each latch written once (and not for a discarded draft), the folded pill, hiding with its toast.
- `features/home/__tests__/homeSettings.test.tsx`: Help → Show closes Settings and shows the checklist again; Help offers no guide to replay, and searching Settings for "guide" no longer finds Help.
- `features/home/__tests__/homeHeader.test.tsx`: no Guide button, while hiring or on an employee's page.
- `features/home/__tests__/connectAI.test.ts`: `openConnectAI()` opens the shared AI category with connection intent and keeps the employee and the Settings page underneath.
- Server: `tests/test_user_settings_contract.py` and `tests/services/test_getting_started_settings.py` (the latch columns, their migration and the getter).

## Key files

| File | Description |
|------|-------------|
| `client/src/features/home/onboarding/GetStartedChecklist.tsx` | The card and its folded pill |
| `client/src/features/home/onboarding/useGetStarted.ts` | The steps' state, the two latches, `useShowGetStarted` |
| `client/src/features/home/onboarding/getStartedItems.ts` | The steps, in order |
| `client/src/features/home/settings/HelpTab.tsx` | Settings > Help: Show |
| `client/src/features/home/state/homeStore.ts` | `openConnectAI`, where Connect an AI model goes |
| `server/models/database.py` | `UserSettings`: the `getting_started_*` flags, and `onboarding_completed` / `onboarding_step`, kept unused |
| `server/core/database.py` | `_migrate_user_settings` (SQLite: adds the columns, hides the checklist for rows already marked `onboarding_completed`); `_validate_shared_schema` (PostgreSQL) |

## History

- **2026-11-03**: the three-step Welcome guide (Welcome, Connect an AI model, Your first hire) was removed. It had opened on first launch, from the header's Guide button and from Settings → Help (Welcome guide · Replay); the checklist waited until it was finished or skipped, and its Connect an AI model step opened the guide at Connect. The onboarding v3 design handoff (2026-11-02) drops the guide; this change was the removal alone. What it added to shared code stays: `Composer`'s `flat` and `idleLabel` props, genui's `useJobComposer` / `useSendJob` (Home's own composer runs on them), and `CredentialsBrowser`'s `embedded` variant, which the AI models dialog planned by that handoff is expected to reuse.
- **Onboarding handoff (2026-10-08)**: the Welcome guide and this checklist replaced the editor's four-step wizard (it taught blocks and the Start button, and only appeared once Dev mode opened) and the editor's checklist.
