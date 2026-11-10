/**
 * The editor's Workspace dock state (Home keeps its own in homeStore; the
 * dock they show is the same one).
 *
 * A small Zustand store (nodeStatusStore shape: slice-selector reads,
 * `getState()` writes from the WS handler and click-to-preview call sites)
 * — deliberately NOT on useAppStore or WebSocketContext.value, both of
 * which are context fan-out traps for high-frequency or unrelated state.
 *
 * Prefs (open / width / autoOpen / followMode / tab) persist to localStorage via
 * a zod-validated envelope, the ConsolePanel idiom — no persist middleware.
 * Node selection and ephemeral items are session-only: node ids are
 * workflow-scoped and meaningless across reloads. The width's limits are the
 * dock's own (components/workspace/dockSize), the same as on Home.
 */

import { z } from 'zod';
import { create } from 'zustand';

import type { CanvasFocus, CanvasItem } from '../lib/canvasBoard';
import { DOCK_DEFAULT_WIDTH, DOCK_MAX_STORED_WIDTH, DOCK_MIN_WIDTH, clampDockWidth } from '../components/workspace/dockSize';
import type { WorkspaceTab } from '../components/workspace/WorkspaceTabs';

const dockPrefsSchema = z.object({
  open: z.boolean().default(false),
  // A width stored before the dock's limits changed falls back on its own.
  widthPx: z.number().min(DOCK_MIN_WIDTH).max(DOCK_MAX_STORED_WIDTH).catch(DOCK_DEFAULT_WIDTH),
  autoOpen: z.boolean().default(true),
  followMode: z.boolean().default(false),
  tab: z.enum(['browser', 'board', 'android']).catch('board'),
});
type CanvasDockPrefs = z.infer<typeof dockPrefsSchema>;
const DOCK_PREFS_KEY = 'canvas_dock_prefs_v1';

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
  /** An item to show on a node's board (a chat reply's document card). */
  focus: (CanvasFocus & { nodeId: string }) | null;

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
  /** An agent in the CURRENT workflow asked for help in its browser (caller verifies): show the Browser tab. */
  showBrowser: () => void;
  /** Open the dock on a Canvas node's board at an item and version (a
   *  document a chat reply names, in the CURRENT workflow; caller verifies). */
  focusItem: (nodeId: string, itemId: string, version?: number | null) => void;
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
  focus: null,

  toggle: () => {
    set((state) => ({ open: !state.open }));
    persist(get());
  },
  close: () => {
    set({ open: false });
    persist(get());
  },
  setWidth: (px) => {
    set({ widthPx: clampDockWidth(px) });
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
  showBrowser: () => {
    set({ open: true, tab: 'browser' });
    persist(get());
  },
  focusItem: (nodeId, itemId, version) => {
    set((state) => ({
      open: true,
      tab: 'board',
      mode: 'node',
      selectedNodeId: nodeId,
      ephemeralItem: null,
      focus: { nodeId, itemId, version: version ?? null, nonce: (state.focus?.nonce ?? 0) + 1 },
    }));
    persist(get());
  },
}));
