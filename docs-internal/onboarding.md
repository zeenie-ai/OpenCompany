# Onboarding Service

## Overview

The onboarding service provides a four-step welcome wizard that appears after a user's first launch: what OpenCompany is, how the canvas works, connecting an AI provider, and trying the shipped AI Assistant example. It is database-backed, skippable, resumable, and replayable from Settings. The wizard is part of the workflow editor (`Dashboard.tsx`), so with Normal mode on it first appears when Dev mode opens; Normal mode's first screen is Home itself ([normal_mode.md](./normal_mode.md)). A separate, dismissable **Get Started checklist** (`GetStartedChecklist.tsx`) sits in the corner of the canvas after the wizard and tracks five first-session milestones.

The frontend is **fully shadcn/ui + Tailwind** — antd was removed from `client/src/`. The wizard composes the project's `Modal` primitive, shadcn `Button` / `ActionButton` / `Card` / `Badge` / `Alert` / `Skeleton`, and `lucide-react` icons. The step progress indicator is a hand-rolled `<ol>` driven by node-role tokens (no antd `Steps`).

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                         Dashboard.tsx                           │
│  ┌────────────────────────────────────────────────────────┐    │
│  │               OnboardingWizard.tsx                      │    │
│  │  ┌────────────────────────────────────────────────┐    │    │
│  │  │  useOnboarding(reopenTrigger, STEPS.length)    │    │    │
│  │  │  - Reads onboarding_completed/step via         │    │    │
│  │  │    useUserSettingsQuery (TanStack Query, WS)   │    │    │
│  │  │  - Manages step navigation + persistence       │    │    │
│  │  └───────────────┬────────────────────────────────┘    │    │
│  │                  │                                      │    │
│  │  STEPS array (single source of truth in wizard):       │    │
│  │  ┌─────────┬──────────────┬─────────────────┬────────┐ │    │
│  │  │Step 0   │Step 1        │Step 2           │Step 3  │ │    │
│  │  │Welcome  │How it works  │Connect your AI  │Try it  │ │    │
│  │  └─────────┴──────────────┴─────────────────┴────────┘ │    │
│  │                                                         │    │
│  │  Modal (project primitive, Radix-backed)               │    │
│  │   + <ol> progress stepper (Tailwind + role tokens)     │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                │
│  SettingsPanel.tsx → "Replay Welcome Guide" button             │
│    └── onReplayOnboarding → increments reopenTrigger           │
└────────────────────────────────────────────────────────────────┘
          │                              │
          │ WebSocket (via TanStack Q)   │ WebSocket
          ▼                              ▼
┌──────────────────────────────────────────────────────────────┐
│  server/services/settings/handlers.py                        │
│  - get_user_settings → returns onboarding_completed, step    │
│  - save_user_settings → persists onboarding_completed, step  │
│                                                               │
│  server/core/database.py                                      │
│  - _migrate_user_settings() adds columns + marks existing    │
│    users (examples_loaded=1) as onboarding_completed=1       │
│                                                               │
│  server/models/database.py                                    │
│  - UserSettings.onboarding_completed: bool                    │
│  - UserSettings.onboarding_step: int                          │
└──────────────────────────────────────────────────────────────┘
```

## Database Schema

### UserSettings Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `onboarding_completed` | `bool` | `False` | Whether onboarding was completed or skipped |
| `onboarding_step` | `int` | `0` | Last completed step (for resuming mid-wizard) |

### Migration

In `server/core/database.py` `_migrate_user_settings()`:

```python
if "onboarding_completed" not in columns:
    await conn.execute(text(
        "ALTER TABLE user_settings ADD COLUMN onboarding_completed BOOLEAN DEFAULT 0"
    ))
    # Existing users (examples_loaded=1) skip onboarding
    await conn.execute(text(
        "UPDATE user_settings SET onboarding_completed = 1 WHERE examples_loaded = 1"
    ))

if "onboarding_step" not in columns:
    await conn.execute(text(
        "ALTER TABLE user_settings ADD COLUMN onboarding_step INTEGER DEFAULT 0"
    ))
```

**Existing user handling**: The migration marks all rows with `examples_loaded=1` as `onboarding_completed=1`, so returning users never see the wizard.

## Frontend File Structure

```
client/src/
├── components/
│   └── onboarding/
│       ├── OnboardingWizard.tsx        # Wizard modal orchestrator + STEPS SSOT + <ol> stepper
│       ├── GetStartedChecklist.tsx     # Post-wizard corner checklist (collapsible, dismissable)
│       ├── getStartedItems.ts          # The five checklist milestones + EXAMPLE_WORKFLOW_NAMES
│       ├── aiProviderLinks.ts          # FEATURED_AI_PROVIDERS: hint + key-page URL per provider id
│       ├── nodeRoleClasses.ts          # Shared role→Tailwind-token map for step cards
│       ├── steps/
│       │   ├── WelcomeStep.tsx          # Step 0: what OpenCompany is, 2×2 feature grid
│       │   ├── HowItWorksStep.tsx       # Step 1: blocks, agents, chat; Normal/Dev switch
│       │   ├── ConnectAIStep.tsx        # Step 2: provider tiles from the live catalogue
│       │   └── TryItStep.tsx            # Step 3: three-step recipe for the AI Assistant example
│       └── __tests__/                   # ConnectAIStep, GetStartedChecklist, HowItWorksStep, OnboardingWizard
└── hooks/
    ├── useOnboarding.ts                # Wizard state + TanStack-Query persistence
    └── useGetStarted.ts                # Checklist state (which milestones are done, dismissed)
```

## Components

### useOnboarding Hook

**Location**: `client/src/hooks/useOnboarding.ts`

Custom hook managing the full onboarding lifecycle. Persistence rides the **TanStack Query** server-state layer (`useUserSettingsQuery` / `useSaveUserSettingsMutation` from `useUserSettingsQuery.ts`), which are themselves WebSocket-backed (`get_user_settings` / `save_user_settings`). The hook does NOT call WebSocket handlers directly.

```typescript
export const useOnboarding = (
  reopenTrigger?: number,
  totalSteps: number = DEFAULT_TOTAL_STEPS,   // 4; the wizard passes STEPS.length
) => {
  // Returns (spread of OnboardingState + actions):
  // - isVisible: boolean       - Whether wizard should render
  // - currentStep: number      - Active step index (0..totalSteps-1)
  // - isCompleted: boolean     - Whether already completed/skipped
  // - isLoading: boolean       - Settings query in progress
  // - hasChecked: boolean      - Initial hydration done
  // - totalSteps: number       - Echoed back from the param
  // - nextStep(): void         - Advance (completes + persists when next >= totalSteps)
  // - prevStep(): void         - Go back one step (clamped at 0)
  // - skip(): void             - Skip: persist current step, completed=true, hide
  // - complete(): void         - Persist totalSteps, completed=true, hide
};
```

**Key behaviors**:
- `totalSteps` is a **parameter**. The wizard owns the step list and passes `STEPS.length`, so the hook never hardcodes the count — it uses `totalSteps` only to detect last-step completion in `nextStep`. The default only matters if a caller omits it; it matches the shipped wizard.
- Hydrates UI state from `settingsQuery.data` on `isSuccess`: reads `onboarding_completed` / `onboarding_step`. Visibility flips only on first hydration (`prev.hasChecked ? prev.isVisible : !completed`) so a user-closed wizard does not re-open on later query refetches.
- Each navigation (`nextStep` / `prevStep` / `skip` / `complete`) calls `saveSettings.mutate({ onboarding_step, onboarding_completed })` to persist progress.
- Query errors surface as a non-blocking "checked" state (`isLoading=false, hasChecked=true`) so the app continues even if the round-trip failed.
- `reopenTrigger` prop change (when `> 0`) resets state and reopens the wizard from step 0.

### OnboardingWizard

**Location**: `client/src/components/onboarding/OnboardingWizard.tsx`

**Props**:
| Prop | Type | Description |
|------|------|-------------|
| `onOpenCredentials` | `() => void` | Opens CredentialsModal (passed from Dashboard) |
| `reopenTrigger` | `number?` | Incrementing counter triggers wizard reopen |
| `onFinish` | `() => void?` | Called after the last step's button completes the wizard; the Dashboard opens the AI Assistant example and focuses chat. Never called on skip or modal close. |

**`STEPS` is the single source of truth.** The wizard declares a module-scope `STEPS` array of `{ title, render }` entries. Its `.length` feeds the hook's `totalSteps`, the progress indicator renders one node per entry, and the active step's `render` is dispatched by index. Adding a step is a one-line edit to this array.

```typescript
const STEPS = [
  { title: 'Welcome',         render: () => <WelcomeStep /> },
  { title: 'How it works',    render: () => <HowItWorksStep /> },
  { title: 'Connect your AI', render: ({ onOpenCredentials }) => <ConnectAIStep onOpenCredentials={onOpenCredentials} /> },
  { title: 'Try it',          render: () => <TryItStep /> },
];
```

**UI Structure** (all shadcn/Tailwind, no antd):
- Project `Modal` primitive with `maxWidth="95vw"`, `maxHeight="95vh"`, titled "Welcome Guide"; `onClose` is wired to `skip`.
- Progress indicator: a hand-rolled `<ol>` of step pills. Each pill is a rounded number/`Check` (lucide) badge with one of three statuses — `completed` (filled `bg-primary text-primary-foreground`), `active` (`border-primary text-primary`), `upcoming` (`border-border text-muted-foreground`) — joined by a connector `<div>` (`bg-primary` once passed, else `bg-border`). No antd `Steps`.
- Step content rendered via `STEPS[safeIndex].render({ onOpenCredentials })` inside a scrollable `max-h-[calc(95vh-200px)]` container.
- Footer: shadcn `Button variant="ghost"` "Skip for now" (left) | `Button variant="outline"` "Back" (shown when `currentStep > 0`) + an `ActionButton` on the right.
- The right-side primary button uses **`ActionButton` intents**, not raw colour hex: `<ActionButton intent="tools">` for "Next" (with `ArrowRight`), `<ActionButton intent="run">` for the final "Open AI Assistant" (with `MessageCircle`), which calls `complete()` and then `onFinish`.
- Only renders when `isVisible && hasChecked && !isLoading`.

### Node-role token map

**Location**: `client/src/components/onboarding/nodeRoleClasses.ts`

`NODE_ROLE_CLASSES` maps a `NodeRole` (`model | skill | agent | workflow | trigger`) to the matching `--node-X` triplet (`{ card: 'bg-node-X-soft border-node-X-border', text: 'text-node-X' }`). Every step keys its card surfaces off this so the cards track every theme with **no opacity arithmetic at the call site**.

### Step Components

All steps are shadcn/Tailwind compositions using `lucide-react` icons. No antd, no `@ant-design/icons`. The copy is deliberately non-technical (blocks, agents, chat), not node-type vocabulary.

| Step | Component | Heading | Purpose | Notable data sources |
|------|-----------|---------|---------|----------------------|
| 0 | `WelcomeStep` | Build your own AI team | Platform intro + 2×2 feature grid (agents, drag-and-drop, bring your AI, local and private) | static; `Card` / `CardContent`, role-token cards |
| 1 | `HowItWorksStep` | See how it works | Three ideas: snap blocks together, agents do the thinking, chat to make it go; explains the Normal / Dev toolbar switch | `useNodeGroups()` — the Normal-mode group labels render live as `Badge`s |
| 2 | `ConnectAIStep` | Connect your AI (or "You're connected") | Featured provider tiles with "Get a key" links, the remaining AI providers as chips, and the Connect / Manage button | `useCatalogueQuery()` for name, icon and `stored` state; `FEATURED_AI_PROVIDERS` for hint + key URL |
| 3 | `TryItStep` | Say hello to your first agent | Three-step recipe (open AI Assistant, press Start, say hello) plus "more to explore" cards for Claude Assistant and AI Employee | static |

**ConnectAIStep** takes an `onOpenCredentials` prop so it links to the existing CredentialsModal without duplicating key input. Everything about a provider except its marketing hint and key-page URL comes from the live credential catalogue: `FEATURED_AI_PROVIDERS` (`aiProviderLinks.ts`) lists only `{ id, hint, keyUrl }` for openai, anthropic and gemini, and the step joins that to `useCatalogueQuery().providers` filtered to `category === 'ai'`. Providers not featured render as a chip row with a note that Ollama and LM Studio run locally. While the catalogue loads it shows three `Skeleton` tiles; once any AI provider is `stored` the heading, button label and closing `Alert` all switch to the connected variant.

**HowItWorksStep** reads `useNodeGroups()` and renders the labels of every group whose `visibility` is `normal` or `all`, so the "Normal shows just the AI blocks" sentence stays true as groups are added.

### Get Started checklist

**Location**: `client/src/components/onboarding/GetStartedChecklist.tsx`, state in `client/src/hooks/useGetStarted.ts`, items in `getStartedItems.ts`.

A fixed-position card (bottom right, above the console) that appears after the wizard and tracks five milestones: workspace set up (auto-complete), add an AI key, chat with the AI Assistant, build your own workflow (told apart from editing a shipped example via `EXAMPLE_WORKFLOW_NAMES`), and try a theme. Rows flagged `actionable` take a click handler from the Dashboard through the `actions` prop. It collapses to a pill and can be dismissed; dismissal is reversible from Settings → Help.

## Integration Points

### Dashboard.tsx

```typescript
// State for replay trigger
const [onboardingReopenTrigger, setOnboardingReopenTrigger] = React.useState(0);

// SettingsPanel gets replay callback
<SettingsPanel
  onReplayOnboarding={() => {
    setSettingsOpen(false);
    setOnboardingReopenTrigger(prev => prev + 1);
  }}
/>

// OnboardingWizard rendered after CredentialsModal
<OnboardingWizard
  onOpenCredentials={() => setCredentialsOpen(true)}
  reopenTrigger={onboardingReopenTrigger}
  onFinish={openAiAssistantAndFocusChat}
/>
```

### SettingsPanel.tsx

`SettingsPanel` takes an `onReplayOnboarding?: () => void` prop. The Help section renders a shadcn `Button variant="default"` "Replay Welcome Guide" (lucide `HelpCircle` icon, `disabled` when the callback is absent) that fires `onReplayOnboarding`.

## WebSocket Handlers

No new handlers were needed. The onboarding system reuses the generic user-settings handlers (registered from `server/services/settings/handlers.py`), accessed through the TanStack Query user-settings layer:

| Handler | Usage |
|---------|-------|
| `get_user_settings` | Check `onboarding_completed` and `onboarding_step` on hydration |
| `save_user_settings` | Persist step progress on each navigation, skip, or complete |

## Lifecycle

### First Launch (New User)

1. User opens app, WebSocket connects
2. `useOnboarding` reads `useUserSettingsQuery` -- no settings exist yet
3. `onboarding_completed` defaults to `false`, `onboarding_step` defaults to `0`
4. Wizard opens at step 0
5. User navigates steps -- each transition saves via the save mutation (`save_user_settings`)
6. On "Open AI Assistant" or "Skip for now", `onboarding_completed` set to `true`; only the former also fires `onFinish`
7. Wizard closes, does not reappear on refresh

### Existing User (Database Migration)

1. Server starts, `_migrate_user_settings()` runs
2. Adds `onboarding_completed` column, sets to `1` where `examples_loaded = 1`
3. User opens app, `useOnboarding` checks -- sees `onboarding_completed = true`
4. Wizard does not appear

### Resume Mid-Wizard

1. User advances to step 2, closes browser
2. `onboarding_step = 2` was saved on last navigation
3. User reopens app, `useOnboarding` reads `step = 2, completed = false`
4. Wizard opens at step 2

### Replay from Settings

1. User opens Settings, clicks "Replay Welcome Guide"
2. `onReplayOnboarding()` callback fires:
   - Closes SettingsPanel
   - Increments `onboardingReopenTrigger`
3. `useOnboarding` detects the trigger change (`> 0`):
   - Sets `isVisible = true, currentStep = 0, isCompleted = false`
4. Wizard opens from step 0

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Auth disabled (`VITE_AUTH_ENABLED=false`) | Works unchanged -- reads from `user_id="default"` |
| Settings query not resolved yet | `isLoading=true` prevents render until `hasChecked` |
| Settings query errors | Non-blocking: `isLoading=false, hasChecked=true`, app continues |
| Browser closed mid-wizard | `onboarding_step` saved on each transition, resumes from last step |
| Multiple tabs | Completing in one tab doesn't update others until query refetch |
| Replay from Settings | Resets local state and reopens wizard from step 0 |
| Fresh database (no workflow.db) | Onboarding appears after first settings query resolves |
| Credential catalogue still loading on step 2 | `Skeleton` tiles; the Connect button is always available |

## Verification Checklist

1. **Fresh database**: Delete `~/.opencompany/workflow.db` (or the configured DB), start server -- wizard appears
2. **Step navigation**: Click through all 4 steps -- the `<ol>` stepper updates, Back/Next work
3. **Skip**: Click "Skip for now" -- wizard closes, doesn't reappear on refresh
4. **Resume**: Advance to step 2, close browser, reopen -- wizard resumes at step 2
5. **Complete**: Finish via "Open AI Assistant" -- wizard doesn't reappear, the AI Assistant example opens with chat focused
6. **Connect step**: Click "Connect your AI account" -- CredentialsModal opens; after saving a key, the step re-renders as connected
7. **Existing user migration**: With existing `workflow.db` where `examples_loaded=1` -- onboarding does NOT appear
8. **Theme support**: Switch themes -- role-token cards adapt correctly
9. **Replay**: Open Settings, click "Replay Welcome Guide" -- wizard reopens from step 0
10. **Tests**: `bun run --filter react-flow-client test` runs the four `onboarding/__tests__/` suites; `bun run typecheck` (root gate, TypeScript 7) passes clean

## Key Files

| File | Description |
|------|-------------|
| `client/src/hooks/useOnboarding.ts` | Wizard state hook; persists via TanStack-Query user-settings layer |
| `client/src/hooks/useGetStarted.ts` | Get Started checklist state |
| `client/src/hooks/useUserSettingsQuery.ts` | `useUserSettingsQuery` / `useSaveUserSettingsMutation` (WS-backed) |
| `client/src/components/onboarding/OnboardingWizard.tsx` | Main wizard modal: `STEPS` SSOT + `<ol>` stepper + ActionButton footer |
| `client/src/components/onboarding/GetStartedChecklist.tsx` | Post-wizard milestone checklist |
| `client/src/components/onboarding/getStartedItems.ts` | Checklist items + `EXAMPLE_WORKFLOW_NAMES` |
| `client/src/components/onboarding/aiProviderLinks.ts` | `FEATURED_AI_PROVIDERS` hints and key-page URLs |
| `client/src/components/onboarding/nodeRoleClasses.ts` | `NODE_ROLE_CLASSES` role→token map for step cards |
| `client/src/components/onboarding/steps/WelcomeStep.tsx` | Step 0: what OpenCompany is |
| `client/src/components/onboarding/steps/HowItWorksStep.tsx` | Step 1: blocks, agents, chat, Normal/Dev switch |
| `client/src/components/onboarding/steps/ConnectAIStep.tsx` | Step 2: provider tiles from the catalogue + Credentials link |
| `client/src/components/onboarding/steps/TryItStep.tsx` | Step 3: AI Assistant recipe |
| `client/src/Dashboard.tsx` | Integration: renders wizard + checklist, passes replay trigger and `onFinish` |
| `client/src/components/ui/SettingsPanel.tsx` | "Replay Welcome Guide" button in Help section |
| `server/models/database.py` | `UserSettings.onboarding_completed`, `onboarding_step` fields |
| `server/core/database.py` | Migration + CRUD for onboarding fields |

## Adding New Steps

To add a new onboarding step:

1. Create `client/src/components/onboarding/steps/NewStep.tsx` composing shadcn primitives + Tailwind tokens + lucide icons (use `NODE_ROLE_CLASSES` for tinted cards). Do NOT introduce antd. Prefer live data (`useCatalogueQuery`, `useNodeGroups`) over hardcoded lists, as steps 1 and 2 do.
2. Add a `{ title, render }` entry to the `STEPS` array in `OnboardingWizard.tsx`. Its `.length` automatically updates the hook's `totalSteps` and the progress stepper — no separate count to maintain. If the new step is last, the "Open AI Assistant" button and `onFinish` move to it automatically.
3. No backend changes needed (step index is just a number).
