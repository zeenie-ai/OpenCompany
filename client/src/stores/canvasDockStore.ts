/**
 * Docked Canvas sidebar state.
 *
 * A small Zustand store (nodeStatusStore shape: slice-selector reads,
 * `getState()` writes from the WS handler and click-to-preview call sites)
 * — deliberately NOT on useAppStore or WebSocketContext.value, both of
 * which are context fan-out traps for high-frequency or unrelated state.
 *
 * Prefs (open / width / autoOpen / followMode) persist to localStorage via
 * a zod-validated envelope, the ConsolePanel idiom — no persist middleware.
 * Node selection and ephemeral items are session-only: node ids are
 * workflow-scoped and meaningless across reloads.
 */

import { z } from 'zod';
import { create } from 'zustand';

import type { CanvasItem } from '../lib/canvasBoard';
import type { WorkspaceTab } from '../components/workspace/WorkspaceTabs';

const dockPrefsSchema = z.object({
  open: z.boolean().default(false),
  widthPx: z.number().min(280).max(4000).default(380),
  autoOpen: z.boolean().default(true),
  followMode: z.boolean().default(false),
  tab: z.enum(['browser', 'board', 'android']).catch('board'),
});
type CanvasDockPrefs = z.infer<typeof dockPrefsSchema>;
const DOCK_PREFS_KEY = 'canvas_dock_prefs_v1';

export const DOCK_MIN_WIDTH = 280;
// Sanity bound for persisted/corrupt values only — NOT a UX limit. The live
// ceiling while dragging is viewport-relative (CanvasDock's onMove), so the
// dock can take nearly the whole window.
export const DOCK_MAX_WIDTH = 4000;

function loadDockPrefs(): CanvasDockPrefs {
  try {
    const raw = localStorage.getItem(DOCK_PREFS_KEY);
    if (raw) {
      const parsed = dockPrefsSchema.safeParse(JSON.parse(raw));
      if (parsed.success) return parsed.data;
    }
  } catch { /* fall through to defaults */ }
  return dockPrefsSchema.parse({});
}

function saveDockPrefs(prefs: CanvasDockPrefs): void {
  try {
    localStorage.setItem(DOCK_PREFS_KEY, JSON.stringify(prefs));
  } catch { /* ignore */ }
}

interface CanvasDockState {
  open: boolean;
  widthPx: number;
  autoOpen: boolean;
  followMode: boolean;
  tab: WorkspaceTab;
  setTab: (tab: WorkspaceTab) => void;
  /** 'node' renders a Canvas node's board; 'ephemeral' a transient preview. */
  mode: 'node' | 'ephemeral';
  selectedNodeId: string | null;
  ephemeralItem: CanvasItem | null;

  toggle: () => void;
  close: () => void;
  setWidth: (px: number) => void;
  setAutoOpen: (value: boolean) => void;
  setFollowMode: (value: boolean) => void;
  /** Show a Canvas node's board (also switches out of ephemeral mode). */
  selectNode: (nodeId: string) => void;
  /** Open the dock on a transient item that lives in no node's board. */
  showEphemeral: (item: CanvasItem) => void;
  /** Leave ephemeral mode, back to the selected node's board. */
  backToNode: () => void;
  /**
   * A canvas_updated broadcast for the CURRENT workflow (caller verifies).
   * Closed + autoOpen -> open on the pushed node; open in node mode ->
   * follow the pushed node; ephemeral mode -> never yank a deliberate
   * preview.
   */
  notifyPushed: (nodeId: string) => void;
}

const persisted = loadDockPrefs();

const persist = (state: CanvasDockState) =>
  saveDockPrefs({
    open: state.open,
    widthPx: state.widthPx,
    autoOpen: state.autoOpen,
    followMode: state.followMode,
    tab: state.tab,
  });

export const useCanvasDockStore = create<CanvasDockState>((set, get) => ({
  open: persisted.open,
  widthPx: persisted.widthPx,
  autoOpen: persisted.autoOpen,
  followMode: persisted.followMode,
  tab: persisted.tab,
  setTab: (tab) => {
    set({ tab });
    persist(get());
  },
  mode: 'node',
  selectedNodeId: null,
  ephemeralItem: null,

  toggle: () => {
    set((state) => ({ open: !state.open }));
    persist(get());
  },
  close: () => {
    set({ open: false });
    persist(get());
  },
  setWidth: (px) => {
    const clamped = Math.min(DOCK_MAX_WIDTH, Math.max(DOCK_MIN_WIDTH, Math.round(px)));
    set({ widthPx: clamped });
    persist(get());
  },
  setAutoOpen: (value) => {
    set({ autoOpen: value });
    persist(get());
  },
  setFollowMode: (value) => {
    set({ followMode: value });
    persist(get());
  },
  selectNode: (nodeId) =>
    set({ mode: 'node', selectedNodeId: nodeId, ephemeralItem: null, tab: 'board' }),
  showEphemeral: (item) => {
    set({ mode: 'ephemeral', ephemeralItem: item, open: true, tab: 'board' });
    persist(get());
  },
  backToNode: () => set({ mode: 'node', ephemeralItem: null }),

  notifyPushed: (nodeId) => {
    const state = get();
    if (state.mode === 'ephemeral') return;
    if (!state.open) {
      if (!state.autoOpen) return;
      set({ open: true, mode: 'node', selectedNodeId: nodeId, tab: 'board' });
      persist(get());
      return;
    }
    if (state.selectedNodeId !== nodeId) {
      set({ mode: 'node', selectedNodeId: nodeId });
    }
  },
}));
