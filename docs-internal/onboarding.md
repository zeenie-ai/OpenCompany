# Onboarding Service

## Overview

The onboarding service provides a multi-step welcome wizard that appears after a user's first launch, guiding them through platform capabilities, key concepts, API key setup, UI layout, and getting started. It is database-backed, skippable, resumable, and replayable from Settings.

The frontend is **fully shadcn/ui + Tailwind** — antd was removed from `client/src/`. The wizard composes the project's `Modal` primitive, shadcn `Button` / `ActionButton` / `Card` / `Badge` / `Alert`, and `lucide-react` icons. The step progress indicator is a hand-rolled `<ol>` driven by node-role tokens (no antd `Steps`).

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
│  │  ┌───────┬───────┬───────┬──────────┬──────────┐       │    │
│  │  │Step 0 │Step 1 │Step 2 │Step 3    │Step 4    │       │    │
│  │  │Welcome│Concept│APIKey │Canvas    │GetStarted│       │    │
│  │  └───────┴───────┴───────┴──────────┴──────────┘       │    │
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
│  server/routers/websocket.py                                  │
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
│   ├── onboarding/
│   │   ├── OnboardingWizard.tsx        # Main wizard modal orchestrator + STEPS SSOT + <ol> stepper
│   │   ├── nodeRoleClasses.ts          # Shared role→Tailwind-token map for step cards
│   │   └── steps/
│   │       ├── WelcomeStep.tsx          # Step 0: Platform intro
│   │       ├── ConceptsStep.tsx         # Step 1: Nodes, Edges, Agents, Skills, Modes
│   │       ├── ApiKeyStep.tsx           # Step 2: AI provider key setup
│   │       ├── CanvasStep.tsx           # Step 3: UI layout tour
│   │       └── GetStartedStep.tsx       # Step 4: First workflow tips
│   └── icons/
│       └── AIProviderIcons.tsx          # Provider SVG icons used by ApiKeyStep
└── hooks/
    └── useOnboarding.ts                # State management + TanStack-Query persistence
```

## Components

### useOnboarding Hook

**Location**: `client/src/hooks/useOnboarding.ts`

Custom hook managing the full onboarding lifecycle. Persistence rides the **TanStack Query** server-state layer (`useUserSettingsQuery` / `useSaveUserSettingsMutation` from `useUserSettingsQuery.ts`), which are themselves WebSocket-backed (`get_user_settings` / `save_user_settings`). The hook does NOT call WebSocket handlers directly.

```typescript
export const useOnboarding = (
  reopenTrigger?: number,
  totalSteps: number = 5,   // DEFAULT_TOTAL_STEPS; caller passes STEPS.length
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
- `totalSteps` is a **parameter** (default 5). The wizard owns the step list and passes `STEPS.length`, so the hook never hardcodes the count — it uses `totalSteps` only to detect last-step completion in `nextStep`.
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

**`STEPS` is the single source of truth.** The wizard declares a module-scope `STEPS` array of `{ title, render }` entries. Its `.length` feeds the hook's `totalSteps`, the progress indicator renders one node per entry, and the active step's `render` is dispatched by index. Adding a step is a one-line edit to this array.

```typescript
const STEPS = [
  { title: 'Welcome',     render: () => <WelcomeStep /> },
  { title: 'Concepts',    render: () => <ConceptsStep /> },
  { title: 'API Keys',    render: ({ onOpenCredentials }) => <ApiKeyStep onOpenCredentials={onOpenCredentials} /> },
  { title: 'Canvas',      render: () => <CanvasStep /> },
  { title: 'Get Started', render: () => <GetStartedStep /> },
];
```

**UI Structure** (all shadcn/Tailwind, no antd):
- Project `Modal` primitive with `maxWidth="95vw"`, `maxHeight="95vh"`, titled "Welcome Guide"; `onClose` is wired to `skip`.
- Progress indicator: a hand-rolled `<ol>` of step pills. Each pill is a rounded number/`Check` (lucide) badge with one of three statuses — `completed` (filled `bg-primary text-primary-foreground`), `active` (`border-primary text-primary`), `upcoming` (`border-border text-muted-foreground`) — joined by a connector `<div>` (`bg-primary` once passed, else `bg-border`). No antd `Steps`.
- Step content rendered via `STEPS[safeIndex].render({ onOpenCredentials })` inside a scrollable `max-h-[calc(95vh-200px)]` container.
- Footer: shadcn `Button variant="ghost"` "Skip for now" (left) | `Button variant="outline"` "Back" (shown when `currentStep > 0`) + an `ActionButton` on the right.
- The right-side primary button uses **`ActionButton` intents**, not raw colour hex: `<ActionButton intent="tools">` for "Next" (with `ArrowRight`), `<ActionButton intent="run">` for the final "Start Building" (with `Check`). The pre-antd-removal dracula-purple/green hardcoding is gone.
- Only renders when `isVisible && hasChecked && !isLoading`.

### Node-role token map

**Location**: `client/src/components/onboarding/nodeRoleClasses.ts`

`NODE_ROLE_CLASSES` maps a `NodeRole` (`model | skill | agent | workflow | trigger`) to the matching `--node-X` triplet (`{ card: 'bg-node-X-soft border-node-X-border', text: 'text-node-X' }`). `ConceptsStep` and `GetStartedStep` key their card surfaces off this so the cards track every theme with **no opacity arithmetic at the call site**.

### Step Components

All steps are shadcn/Tailwind compositions using `lucide-react` icons (`Card` / `CardContent`, `Badge`, `Alert` / `AlertDescription`, `Button`, plus role tokens). No antd, no `@ant-design/icons`.

| Step | Component | Title | Purpose | Primitives used |
|------|-----------|-------|---------|-----------------|
| 0 | `WelcomeStep` | Welcome to OpenCompany | Platform intro + 2×2 feature grid | `Card` / `CardContent`, lucide `Rocket` / `Plug` / `Move` / `Zap`, `bg-node-agent-soft` |
| 1 | `ConceptsStep` | Key Concepts | Nodes, Edges, AI Agents, Skills & Tools, Normal vs Dev Mode | role-token cards via `NODE_ROLE_CLASSES`, lucide `LayoutGrid` / `GitBranch` / `Bot` / `Wrench` / `ArrowLeftRight` |
| 2 | `ApiKeyStep` | API Key Setup | Provider list + "Open Credentials" button | shadcn `Button`, `Alert variant="info"`, `AIProviderIcons`, lucide `Key` / `ExternalLink` |
| 3 | `CanvasStep` | Canvas Tour | Visual UI layout diagram + keyboard shortcuts | `Badge`, role-token region tints, lucide `Layout` / `Wrench` / `Terminal` |
| 4 | `GetStartedStep` | Get Started | Example workflows, quick recipe, tips | `Card` / `CardContent`, `Badge`, role-token cards, lucide `Play` / `FlaskConical` / `BookOpen` / `Settings` |

**ApiKeyStep** accepts an `onOpenCredentials` prop to link to the existing CredentialsModal without duplicating key input logic. It lists six providers (OpenAI, Anthropic, Google, Groq, OpenRouter, Cerebras) with brand icons from `client/src/components/icons/AIProviderIcons.tsx` (imported as `../../icons/AIProviderIcons`), and closes with an `Alert variant="info"` noting keys can be changed later from the toolbar.

**CanvasStep** renders a miniature UI-region diagram whose tints map node groups to regions: toolbar→`workflow`, sidebar→`model`, canvas→`agent`, palette→`skill`, console→`trigger`, each via the `--node-X-soft` / `text-node-X` tokens. The shortcut list (`Ctrl+S` Save, `F2` Rename node, `Delete` Remove node, `Ctrl+C` Copy node) renders as `Badge variant="outline"` mono chips.

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
/>
```

### SettingsPanel.tsx

`SettingsPanel` takes an `onReplayOnboarding?: () => void` prop. The Help section renders a shadcn `Button variant="default"` "Replay Welcome Guide" (lucide `HelpCircle` icon, `disabled` when the callback is absent) that fires `onReplayOnboarding`.

## WebSocket Handlers

No new handlers were needed. The onboarding system reuses existing generic handlers, accessed through the TanStack Query user-settings layer:

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
6. On "Start Building" or "Skip for now", `onboarding_completed` set to `true`
7. Wizard closes, does not reappear on refresh

### Existing User (Database Migration)

1. Server starts, `_migrate_user_settings()` runs
2. Adds `onboarding_completed` column, sets to `1` where `examples_loaded = 1`
3. User opens app, `useOnboarding` checks -- sees `onboarding_completed = true`
4. Wizard does not appear

### Resume Mid-Wizard

1. User advances to step 3, closes browser
2. `onboarding_step = 3` was saved on last navigation
3. User reopens app, `useOnboarding` reads `step = 3, completed = false`
4. Wizard opens at step 3

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

## Verification Checklist

1. **Fresh database**: Delete `server/workflow.db` (or the configured DB), start server -- wizard appears
2. **Step navigation**: Click through all 5 steps -- the `<ol>` stepper updates, Back/Next work
3. **Skip**: Click "Skip for now" -- wizard closes, doesn't reappear on refresh
4. **Resume**: Advance to step 3, close browser, reopen -- wizard resumes at step 3
5. **Complete**: Finish all steps via "Start Building" -- wizard doesn't reappear
6. **API Key step**: Click "Open Credentials" button -- CredentialsModal opens
7. **Existing user migration**: With existing `workflow.db` where `examples_loaded=1` -- onboarding does NOT appear
8. **Theme support**: Switch themes -- role-token cards and region tints adapt correctly
9. **Replay**: Open Settings, click "Replay Welcome Guide" -- wizard reopens from step 0
10. **TypeScript**: `tsgo --noEmit` (or `npx tsc --noEmit`) passes clean

## Key Files

| File | Description |
|------|-------------|
| `client/src/hooks/useOnboarding.ts` | Onboarding state hook; persists via TanStack-Query user-settings layer |
| `client/src/hooks/useUserSettingsQuery.ts` | `useUserSettingsQuery` / `useSaveUserSettingsMutation` (WS-backed) |
| `client/src/components/onboarding/OnboardingWizard.tsx` | Main wizard modal: `STEPS` SSOT + `<ol>` stepper + ActionButton footer |
| `client/src/components/onboarding/nodeRoleClasses.ts` | `NODE_ROLE_CLASSES` role→token map for step cards |
| `client/src/components/onboarding/steps/WelcomeStep.tsx` | Step 0: Platform introduction |
| `client/src/components/onboarding/steps/ConceptsStep.tsx` | Step 1: Key concepts (Nodes, Edges, Agents, Skills, Modes) |
| `client/src/components/onboarding/steps/ApiKeyStep.tsx` | Step 2: API key setup with Credentials link |
| `client/src/components/onboarding/steps/CanvasStep.tsx` | Step 3: UI layout diagram + shortcuts |
| `client/src/components/onboarding/steps/GetStartedStep.tsx` | Step 4: Getting started tips |
| `client/src/components/icons/AIProviderIcons.tsx` | Provider brand icons used by ApiKeyStep |
| `client/src/Dashboard.tsx` | Integration: renders wizard + passes replay trigger |
| `client/src/components/ui/SettingsPanel.tsx` | "Replay Welcome Guide" button in Help section |
| `server/models/database.py` | `UserSettings.onboarding_completed`, `onboarding_step` fields |
| `server/core/database.py` | Migration + CRUD for onboarding fields |

## Adding New Steps

To add a new onboarding step:

1. Create `client/src/components/onboarding/steps/NewStep.tsx` composing shadcn primitives + Tailwind tokens + lucide icons (use `NODE_ROLE_CLASSES` for tinted cards). Do NOT introduce antd.
2. Add a `{ title, render }` entry to the `STEPS` array in `OnboardingWizard.tsx`. Its `.length` automatically updates the hook's `totalSteps` and the progress stepper — no separate count to maintain.
3. No backend changes needed (step index is just a number).
