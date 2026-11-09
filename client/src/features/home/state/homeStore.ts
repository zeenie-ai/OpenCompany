/**
 * Normal mode's UI state: which view is showing, the sidebar, the Settings
 * dialog, the Workspace dock, the Welcome guide, the AI models dialog,
 * whether the Get started checklist is folded, and the one-shot signals the
 * hire choreography fires (a sidebar row's glow, the logo pulse, what a hire
 * could not set up as asked, a new hire's first day). Server data lives in
 * TanStack Query (features/home/data), never here.
 */

import { z } from 'zod';
import { create } from 'zustand';
import type { WorkflowControlStatus } from '@/contexts/WebSocketContext';
import type { CanvasFocus } from '@/lib/canvasBoard';
import type { CredentialsIntent } from '@/stores/shellDialogsStore';
import { SPIKE, spikeOrb } from '../orb/orb';

export type HomeView = { kind: 'hire' } | { kind: 'employee'; workflowId: string };
export type SettingsTab = 'profile' | 'billing' | 'skills' | 'connectors' | 'plugins' | 'access' | 'help';

/** The Welcome guide's steps, in order. The saved `onboarding_step` is an
 *  index into this list. */
export const GUIDE_STEPS = ['welcome', 'connect', 'first-hire'] as const;
export type GuideStep = (typeof GUIDE_STEPS)[number];

export interface GuideState {
  open: boolean;
  step: GuideStep;
  /** The furthest step visited; the nav reaches any step up to it. */
  furthest: number;
  /** The first-launch check ran this session (checkGuide). */
  checked: boolean;
  /** The provider page the Connect step shows in place of the list. */
  provider: { id: string; intent: CredentialsIntent } | null;
  /** The last step's job waits for an AI model: connecting one finishes
   *  the guide and sends it. */
  pendingDraft: boolean;
}

/** Home's AI models dialog (onboarding/AiModelsDialog). */
export interface ConnectAIState {
  open: boolean;
  /** The model's page it shows in place of the list. */
  provider: { id: string; intent: CredentialsIntent } | null;
  /** Connecting a model closes it, so the task that wanted one goes on.
   *  The Get started checklist opens it to stay on the list instead. */
  closeOnConnect: boolean;
}

/** An employee's screen the owner took (Take over, workspace/takeover.ts). */
export interface Takeover {
  /** The Workspace tab whose screen it is. */
  screen: WorkspaceTab;
  /** Gives back that screen (components/workspace/surface.ts). */
  giveBack: () => void;
  /** Take over's Stop, when it stopped them: Hand back resumes them from its
   *  answer. */
  stop?: Promise<WorkflowControlStatus>;
}

// View ids, not node types: the Canvas tab is 'board' so no id reads as the
// `canvas` node type (tests/test_frontend_no_node_type_copies.py).
const WORKSPACE_TABS = ['browser', 'board', 'android'] as const;
export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];

const SIDEBAR_KEY = 'home_sidebar_open';
const WORKSPACE_KEY = 'home_workspace_v1';
/** Narrowest the Workspace drags to (design handoff "Workspace panel"). */
export const WORKSPACE_MIN_WIDTH = 360;
/** Room a drag always leaves for the rest of the screen. */
const WORKSPACE_KEEP_PX = 420;

/** The Workspace dock's width, as CSS: the dragged width, or Expand's,
 *  always leaving the window some room. The Get started card sits at its
 *  left edge while it is open. */
export function dockWidth(widthPx: number, wide: boolean): string {
  return wide ? 'max(420px, min(1100px, 100vw - 420px))' : `min(${widthPx}px, max(340px, 100vw - 40px))`;
}

/** Whose workspace the dock shows: `workspaceFor`, else the first on the
 *  team. The header's Workspace button reads the same one for its dot. */
export function dockEmployee<T extends { workflow_id: string }>(team: readonly T[] | undefined, workspaceFor: string | null): T | null {
  return team?.find((member) => member.workflow_id === workspaceFor) ?? team?.[0] ?? null;
}

const workspacePrefsSchema = z.object({
  open: z.boolean().catch(false),
  // A bound for stored values only; while dragging, the window sets the limit.
  widthPx: z.number().min(WORKSPACE_MIN_WIDTH).max(4000).catch(460),
  tab: z.enum(WORKSPACE_TABS).catch('board'),
});
type WorkspacePrefs = z.infer<typeof workspacePrefsSchema>;

/** The Workspace's saved open state, width and tab. A bad field falls back
 *  on its own; unreadable storage gives the defaults. */
export function loadWorkspacePrefs(): WorkspacePrefs {
  try {
    const raw = localStorage.getItem(WORKSPACE_KEY);
    if (raw) return workspacePrefsSchema.parse(JSON.parse(raw));
  } catch {
    // Unreadable or blocked storage: the defaults below.
  }
  return workspacePrefsSchema.parse({});
}

function saveWorkspacePrefs(state: HomeState): void {
  const prefs: WorkspacePrefs = { open: state.workspaceOpen, widthPx: state.workspaceWidth, tab: state.workspaceTab };
  try {
    localStorage.setItem(WORKSPACE_KEY, JSON.stringify(prefs));
  } catch {
    // Storage blocked: the dock still works for this session.
  }
}

/** A Canvas item the Workspace was asked to show (homeStore.openCanvasItem). */
export interface WorkspaceFocus extends CanvasFocus {
  workflowId: string;
  canvasNodeId: string;
}

/** A hire made in this session, from the hire until a reload, New
 *  conversation or deleting them (employee/FirstDay). */
export interface FirstDay {
  /** The hire started them itself (nothing was missing). */
  started: boolean;
  /** Blocks in the workflow the hire built; null when the reply did not say. */
  nodeCount: number | null;
}

/** What a hire could not set up as asked (a tool left out while they ask
 *  first, a skill an employee cannot be given), shown on the new
 *  employee's page until the owner dismisses it or moves on. */
export interface HireNotice {
  workflowId: string;
  name: string;
  warnings: string[];
}

function loadSidebarOpen(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) !== 'false';
  } catch {
    return true;
  }
}

interface HomeState {
  view: HomeView;
  sidebarOpen: boolean;
  settingsOpen: boolean;
  settingsTab: SettingsTab;
  /** The category a catalog page opens on ('all' when unset). Only the way
   *  in: switching tabs clears it, and the page owns its filter after. */
  settingsCategory: string;
  /** The sidebar row to glow, and a nonce so repeating it replays. */
  glow: { workflowId: string; nonce: number } | null;
  /** Bumped to replay the logo pulse. 0 = never played. */
  logoPulse: number;
  /** Bumped to ask the composer to take focus: the hire box, or the
   *  message box on an employee's page. */
  composerFocus: number;
  /** What the last hire said, for its employee's page (hire/HireNotice). */
  hireNotice: HireNotice | null;
  /** The Workspace dock. Open, width and tab persist; the rest is this
   *  session's. */
  workspaceOpen: boolean;
  workspaceWidth: number;
  workspaceTab: WorkspaceTab;
  workspaceWide: boolean;
  /** The dock's edge is being dragged: what moves with it follows at once. */
  workspaceResizing: boolean;
  /** Whose workspace it shows: the employee last opened or watched
   *  (dockEmployee falls back to the first on the team). */
  workspaceFor: string | null;
  /** A Canvas item to show (a reply's document card); the nonce lets the
   *  same one be asked for again. */
  workspaceFocus: WorkspaceFocus | null;
  /** The employees whose screen the owner took from the Workspace (Take
   *  over), by workflow id. */
  takeovers: Record<string, Takeover>;
  /** The employees whose screen is being taken or handed back, by workflow
   *  id: one employee's slow Hand back never holds another's. */
  takeoverBusy: Record<string, true>;
  /** The Welcome guide (onboarding/WelcomeGuide). */
  guide: GuideState;
  /** The AI models dialog. */
  connectAI: ConnectAIState;
  /** The Get started checklist is folded to its pill. Kept here for this
   *  session, since the card mounts on the hire view only and the header's
   *  pill opens it from an employee's page. */
  getStartedFolded: boolean;
  /** New hires on their first day, by workflow id. */
  firstDays: Record<string, FirstDay>;

  showHire: (options?: { focus?: boolean }) => void;
  showEmployee: (workflowId: string, options?: { focus?: boolean }) => void;
  toggleSidebar: () => void;
  openSettings: (tab?: SettingsTab, category?: string) => void;
  closeSettings: () => void;
  setSettingsTab: (tab: SettingsTab) => void;
  glowRow: (workflowId: string) => void;
  pulseLogo: () => void;
  /** Open the AI models dialog on its list (closeOnConnect defaults to true). */
  openConnectAI: (options?: { closeOnConnect?: boolean }) => void;
  closeConnectAI: () => void;
  /** Show a model's page in the AI models dialog (null: back to the list). */
  setConnectAIProvider: (provider: ConnectAIState['provider']) => void;
  setHireNotice: (notice: HireNotice | null) => void;
  /** The composer took the focus it was asked for; a remount must not
   *  take it again. */
  consumeComposerFocus: () => void;
  openWorkspace: (workflowId?: string) => void;
  closeWorkspace: () => void;
  setWorkspaceTab: (tab: WorkspaceTab) => void;
  /** Clamped to 360px .. the window less 420px; ends Expand. */
  setWorkspaceWidth: (px: number) => void;
  /** null: handed back. */
  setTakeover: (workflowId: string, takeover: Takeover | null) => void;
  setTakeoverBusy: (workflowId: string, busy: boolean) => void;
  setWorkspaceResizing: (resizing: boolean) => void;
  toggleWorkspaceWide: () => void;
  /** Open the Workspace on the Canvas tab, at an item and version of an
   *  employee's board. */
  openCanvasItem: (target: { workflowId: string; canvasNodeId: string; itemId: string; version?: number | null }) => void;
  /** Open the Welcome guide at a step (the header's Guide button, Settings
   *  > Help, the first-launch check). */
  openGuide: (step?: GuideStep) => void;
  closeGuide: () => void;
  goToGuideStep: (step: GuideStep) => void;
  /** Show a provider's page in the Connect step (null: back to the list). */
  setGuideProvider: (provider: GuideState['provider']) => void;
  setGuidePendingDraft: (pending: boolean) => void;
  /** Once per session: open the guide for an owner who has not finished it,
   *  at the step they left. A completed owner never sees it unasked. */
  checkGuide: (settings: { onboarding_completed?: unknown; onboarding_step?: unknown }) => void;
  setGetStartedFolded: (folded: boolean) => void;
  /** The header's Get started pill: back to the hire view, with the
   *  checklist open and focus in the hire box (the pill goes with the page). */
  openGetStarted: () => void;
  beginFirstDay: (workflowId: string, firstDay: FirstDay) => void;
  /** New conversation, or they are gone. */
  endFirstDay: (workflowId: string) => void;
}

const stepIndex = (step: GuideStep) => GUIDE_STEPS.indexOf(step);

const workspace = loadWorkspacePrefs();

export const useHomeStore = create<HomeState>((set, get) => ({
  view: { kind: 'hire' },
  sidebarOpen: loadSidebarOpen(),
  settingsOpen: false,
  settingsTab: 'profile',
  settingsCategory: 'all',
  glow: null,
  logoPulse: 0,
  composerFocus: 0,
  hireNotice: null,
  workspaceOpen: workspace.open,
  workspaceWidth: workspace.widthPx,
  workspaceTab: workspace.tab,
  workspaceWide: false,
  workspaceResizing: false,
  workspaceFor: null,
  workspaceFocus: null,
  takeovers: {},
  takeoverBusy: {},
  guide: { open: false, step: 'welcome', furthest: 0, checked: false, provider: null, pendingDraft: false },
  connectAI: { open: false, provider: null, closeOnConnect: true },
  getStartedFolded: false,
  firstDays: {},

  showHire: (options) =>
    set((state) => ({
      view: { kind: 'hire' },
      composerFocus: options?.focus ? state.composerFocus + 1 : state.composerFocus,
      hireNotice: null,
    })),
  showEmployee: (workflowId, options) =>
    set((state) => ({
      view: { kind: 'employee', workflowId },
      workspaceFor: workflowId,
      composerFocus: options?.focus ? state.composerFocus + 1 : state.composerFocus,
      hireNotice: state.hireNotice?.workflowId === workflowId ? state.hireNotice : null,
    })),
  toggleSidebar: () =>
    set((state) => {
      const next = !state.sidebarOpen;
      try {
        localStorage.setItem(SIDEBAR_KEY, String(next));
      } catch {
        // Storage blocked: the toggle still works for this session.
      }
      return { sidebarOpen: next };
    }),
  openSettings: (tab = 'profile', category = 'all') => {
    if (!get().settingsOpen) spikeOrb(SPIKE.settings);
    set({ settingsOpen: true, settingsTab: tab, settingsCategory: category });
  },
  closeSettings: () => set({ settingsOpen: false }),
  setSettingsTab: (tab) => set({ settingsTab: tab, settingsCategory: 'all' }),
  glowRow: (workflowId) =>
    set((state) => ({ glow: { workflowId, nonce: (state.glow?.nonce ?? 0) + 1 } })),
  pulseLogo: () => set((state) => ({ logoPulse: state.logoPulse + 1 })),
  openConnectAI: ({ closeOnConnect = true } = {}) => {
    spikeOrb(SPIKE.connect);
    set({ connectAI: { open: true, provider: null, closeOnConnect } });
  },
  // The page stays, so the dialog does not change page while it closes.
  closeConnectAI: () => set((state) => ({ connectAI: { ...state.connectAI, open: false } })),
  setConnectAIProvider: (provider) => set((state) => ({ connectAI: { ...state.connectAI, provider } })),
  setHireNotice: (notice) => set({ hireNotice: notice }),
  consumeComposerFocus: () => set((state) => (state.composerFocus === 0 ? state : { composerFocus: 0 })),
  openWorkspace: (workflowId) => {
    if (!get().workspaceOpen) spikeOrb(SPIKE.workspace);
    set((state) => ({ workspaceOpen: true, workspaceFor: workflowId ?? state.workspaceFor }));
    saveWorkspacePrefs(get());
  },
  closeWorkspace: () => {
    set({ workspaceOpen: false });
    saveWorkspacePrefs(get());
  },
  setTakeover: (workflowId, takeover) =>
    set((state) => {
      const takeovers = { ...state.takeovers };
      if (takeover) takeovers[workflowId] = takeover;
      else delete takeovers[workflowId];
      return { takeovers };
    }),
  setTakeoverBusy: (workflowId, busy) =>
    set((state) => {
      const takeoverBusy = { ...state.takeoverBusy };
      if (busy) takeoverBusy[workflowId] = true;
      else delete takeoverBusy[workflowId];
      return { takeoverBusy };
    }),
  setWorkspaceResizing: (resizing) => set({ workspaceResizing: resizing }),
  setWorkspaceTab: (tab) => {
    set({ workspaceTab: tab });
    saveWorkspacePrefs(get());
  },
  setWorkspaceWidth: (px) => {
    const max = Math.max(WORKSPACE_MIN_WIDTH, window.innerWidth - WORKSPACE_KEEP_PX);
    set({ workspaceWidth: Math.round(Math.min(max, Math.max(WORKSPACE_MIN_WIDTH, px))), workspaceWide: false });
    saveWorkspacePrefs(get());
  },
  toggleWorkspaceWide: () => set((state) => ({ workspaceWide: !state.workspaceWide })),
  openCanvasItem: ({ workflowId, canvasNodeId, itemId, version }) => {
    if (!get().workspaceOpen) spikeOrb(SPIKE.workspace);
    set((state) => ({
      workspaceOpen: true,
      workspaceFor: workflowId,
      workspaceTab: 'board',
      workspaceFocus: { workflowId, canvasNodeId, itemId, version: version ?? null, nonce: (state.workspaceFocus?.nonce ?? 0) + 1 },
    }));
    saveWorkspacePrefs(get());
  },
  openGuide: (step = 'welcome') =>
    set((state) => ({
      guide: {
        ...state.guide,
        open: true,
        step,
        furthest: Math.max(state.guide.furthest, stepIndex(step)),
        provider: null,
        pendingDraft: false,
      },
    })),
  // The step and any provider page stay, so the dialog does not change page
  // while it closes; a job waiting for a model is not carried past it.
  closeGuide: () => set((state) => ({ guide: { ...state.guide, open: false, pendingDraft: false } })),
  goToGuideStep: (step) =>
    set((state) => ({
      guide: { ...state.guide, step, furthest: Math.max(state.guide.furthest, stepIndex(step)), provider: null },
    })),
  setGuideProvider: (provider) => set((state) => ({ guide: { ...state.guide, provider } })),
  setGuidePendingDraft: (pendingDraft) => set((state) => ({ guide: { ...state.guide, pendingDraft } })),
  checkGuide: (settings) => {
    if (get().guide.checked) return;
    const saved = settings.onboarding_step;
    const step = (typeof saved === 'number' && GUIDE_STEPS[saved]) || 'welcome';
    if (settings.onboarding_completed) {
      set((state) => ({ guide: { ...state.guide, checked: true } }));
      return;
    }
    set((state) => ({
      guide: { ...state.guide, open: true, step, furthest: Math.max(state.guide.furthest, stepIndex(step)), checked: true },
    }));
  },
  setGetStartedFolded: (getStartedFolded) => set({ getStartedFolded }),
  openGetStarted: () => {
    get().showHire({ focus: true });
    set({ getStartedFolded: false });
  },
  beginFirstDay: (workflowId, firstDay) => set((state) => ({ firstDays: { ...state.firstDays, [workflowId]: firstDay } })),
  endFirstDay: (workflowId) =>
    set((state) => {
      if (!(workflowId in state.firstDays)) return state;
      const firstDays = { ...state.firstDays };
      delete firstDays[workflowId];
      return { firstDays };
    }),
}));
