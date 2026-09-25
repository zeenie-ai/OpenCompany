/**
 * Normal mode's UI state: which view is showing, the sidebar, the Settings
 * dialog, and the one-shot "look here" signals (a sidebar row's glow, the
 * logo pulse) that the hire choreography fires. Server data lives in
 * TanStack Query (features/home/data), never here.
 */

import { create } from 'zustand';

export type HomeView = { kind: 'hire' } | { kind: 'employee'; workflowId: string };
export type SettingsTab = 'profile' | 'connectors';

const SIDEBAR_KEY = 'home_sidebar_open';

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
  /** Connectors category to open on ('all' when unset). */
  settingsCategory: string;
  /** The sidebar row to glow, and a nonce so repeating it replays. */
  glow: { workflowId: string; nonce: number } | null;
  /** Bumped to replay the logo pulse. 0 = never played. */
  logoPulse: number;
  /** Bumped to ask the composer to take focus. */
  composerFocus: number;

  showHire: (options?: { focus?: boolean }) => void;
  showEmployee: (workflowId: string) => void;
  toggleSidebar: () => void;
  openSettings: (tab?: SettingsTab, category?: string) => void;
  closeSettings: () => void;
  setSettingsTab: (tab: SettingsTab) => void;
  setSettingsCategory: (category: string) => void;
  glowRow: (workflowId: string) => void;
  pulseLogo: () => void;
  /** The composer took the focus it was asked for; a remount must not
   *  take it again. */
  consumeComposerFocus: () => void;
}

export const useHomeStore = create<HomeState>((set) => ({
  view: { kind: 'hire' },
  sidebarOpen: loadSidebarOpen(),
  settingsOpen: false,
  settingsTab: 'profile',
  settingsCategory: 'all',
  glow: null,
  logoPulse: 0,
  composerFocus: 0,

  showHire: (options) =>
    set((state) => ({
      view: { kind: 'hire' },
      composerFocus: options?.focus ? state.composerFocus + 1 : state.composerFocus,
    })),
  showEmployee: (workflowId) => set({ view: { kind: 'employee', workflowId } }),
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
  openSettings: (tab = 'profile', category = 'all') =>
    set({ settingsOpen: true, settingsTab: tab, settingsCategory: category }),
  closeSettings: () => set({ settingsOpen: false }),
  setSettingsTab: (tab) => set({ settingsTab: tab }),
  setSettingsCategory: (category) => set({ settingsCategory: category }),
  glowRow: (workflowId) =>
    set((state) => ({ glow: { workflowId, nonce: (state.glow?.nonce ?? 0) + 1 } })),
  pulseLogo: () => set((state) => ({ logoPulse: state.logoPulse + 1 })),
  consumeComposerFocus: () => set((state) => (state.composerFocus === 0 ? state : { composerFocus: 0 })),
}));
