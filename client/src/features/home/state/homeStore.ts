/**
 * Normal mode's UI state: which view is showing, the sidebar, the Settings
 * dialog, the Workspace dock, and
 * the one-shot signals the hire choreography fires (a sidebar row's glow,
 * the logo pulse, what a hire could not set up as asked). Server data lives
 * in TanStack Query (features/home/data), never here.
 */

import { z } from 'zod';
import { create } from 'zustand';
import type { CanvasFocus } from '@/lib/canvasBoard';
import { useShellDialogsStore } from '@/stores/shellDialogsStore';
import { SPIKE, spikeOrb } from '../orb/orb';

export type HomeView = { kind: 'hire' } | { kind: 'employee'; workflowId: string };
export type SettingsTab = 'profile' | 'billing' | 'skills' | 'connectors' | 'plugins' | 'access';

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
  /** Bumped to ask the composer to take focus. */
  composerFocus: number;
  /** What the last hire said, for its employee's page (hire/HireNotice). */
  hireNotice: HireNotice | null;
  /** The Workspace dock. Open, width and tab persist; the rest is this
   *  session's. */
  workspaceOpen: boolean;
  workspaceWidth: number;
  workspaceTab: WorkspaceTab;
  workspaceWide: boolean;
  /** Whose workspace it shows: the employee last opened or watched. */
  workspaceFor: string | null;
  /** A Canvas item to show (a reply's document card); the nonce lets the
   *  same one be asked for again. */
  workspaceFocus: WorkspaceFocus | null;

  showHire: (options?: { focus?: boolean }) => void;
  showEmployee: (workflowId: string) => void;
  toggleSidebar: () => void;
  openSettings: (tab?: SettingsTab, category?: string) => void;
  closeSettings: () => void;
  setSettingsTab: (tab: SettingsTab) => void;
  glowRow: (workflowId: string) => void;
  pulseLogo: () => void;
  openConnectAI: () => void;
  setHireNotice: (notice: HireNotice | null) => void;
  /** The composer took the focus it was asked for; a remount must not
   *  take it again. */
  consumeComposerFocus: () => void;
  openWorkspace: (workflowId?: string) => void;
  closeWorkspace: () => void;
  setWorkspaceTab: (tab: WorkspaceTab) => void;
  /** Clamped to 360px .. the window less 420px; ends Expand. */
  setWorkspaceWidth: (px: number) => void;
  toggleWorkspaceWide: () => void;
  /** Open the Workspace on the Canvas tab, at an item and version of an
   *  employee's board. */
  openCanvasItem: (target: { workflowId: string; canvasNodeId: string; itemId: string; version?: number | null }) => void;
}

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
  workspaceFor: null,
  workspaceFocus: null,

  showHire: (options) =>
    set((state) => ({
      view: { kind: 'hire' },
      composerFocus: options?.focus ? state.composerFocus + 1 : state.composerFocus,
      hireNotice: null,
    })),
  showEmployee: (workflowId) =>
    set((state) => ({
      view: { kind: 'employee', workflowId },
      workspaceFor: workflowId,
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
  openConnectAI: () => {
    spikeOrb(SPIKE.connect);
    useShellDialogsStore.getState().openCredentials({ categoryId: 'ai', intent: 'connect' });
  },
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
}));
