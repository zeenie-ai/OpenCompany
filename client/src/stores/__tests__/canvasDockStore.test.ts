import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CanvasItem } from '../../lib/canvasBoard';
import { useCanvasDockStore } from '../canvasDockStore';

const PREFS_KEY = 'canvas_dock_prefs_v1';

const ephemeral: CanvasItem = {
  id: 'ephemeral-x',
  kind: 'note',
  title: 'Preview',
  ref: null,
  url: null,
  content: 'hello',
  language: null,
  source: 'workflow',
  created_at: null,
};

const resetStore = () =>
  useCanvasDockStore.setState({
    open: false,
    widthPx: 460,
    autoOpen: true,
    followMode: false,
    tab: 'board',
    mode: 'node',
    selectedNodeId: null,
    ephemeralItem: null,
  });

describe('canvasDockStore.notifyPushed', () => {
  beforeEach(() => {
    localStorage.clear();
    resetStore();
  });

  it('opens on the pushed node when closed and autoOpen is on', () => {
    useCanvasDockStore.getState().notifyPushed('canvas-1');
    const state = useCanvasDockStore.getState();
    expect(state.open).toBe(true);
    expect(state.mode).toBe('node');
    expect(state.selectedNodeId).toBe('canvas-1');
  });

  it('does nothing when closed and autoOpen is off', () => {
    useCanvasDockStore.setState({ autoOpen: false });
    useCanvasDockStore.getState().notifyPushed('canvas-1');
    expect(useCanvasDockStore.getState().open).toBe(false);
    expect(useCanvasDockStore.getState().selectedNodeId).toBeNull();
  });

  it('follows the pushed node when already open in node mode', () => {
    useCanvasDockStore.setState({ open: true, selectedNodeId: 'canvas-1' });
    useCanvasDockStore.getState().notifyPushed('canvas-2');
    expect(useCanvasDockStore.getState().selectedNodeId).toBe('canvas-2');
  });

  it('never yanks a deliberate ephemeral preview', () => {
    useCanvasDockStore.getState().showEphemeral(ephemeral);
    useCanvasDockStore.getState().notifyPushed('canvas-1');
    const state = useCanvasDockStore.getState();
    expect(state.mode).toBe('ephemeral');
    expect(state.ephemeralItem).toEqual(ephemeral);
  });

  it('does not switch away from a live browser on a Canvas push', () => {
    useCanvasDockStore.setState({ open: true, tab: 'browser' });
    useCanvasDockStore.getState().notifyPushed('canvas-2');
    expect(useCanvasDockStore.getState().tab).toBe('browser');
  });

  it('opens on the Browser tab when an agent asks for help', () => {
    useCanvasDockStore.getState().showBrowser();
    const state = useCanvasDockStore.getState();
    expect(state.open).toBe(true);
    expect(state.tab).toBe('browser');
  });

  it('opens on a document a chat reply names, leaving a preview, and asks again with a new nonce', () => {
    useCanvasDockStore.setState({ tab: 'browser' });
    useCanvasDockStore.getState().showEphemeral(ephemeral);
    useCanvasDockStore.getState().focusItem('canvas-2', 'item-1', 3);
    const state = useCanvasDockStore.getState();
    expect(state).toMatchObject({ open: true, tab: 'board', mode: 'node', selectedNodeId: 'canvas-2', ephemeralItem: null });
    expect(state.focus).toEqual({ nodeId: 'canvas-2', itemId: 'item-1', version: 3, nonce: 1 });

    useCanvasDockStore.getState().focusItem('canvas-2', 'item-1');
    expect(useCanvasDockStore.getState().focus).toEqual({ nodeId: 'canvas-2', itemId: 'item-1', version: null, nonce: 2 });
  });
});

describe('canvasDockStore prefs', () => {
  beforeEach(() => {
    localStorage.clear();
    resetStore();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('persists open/width/autoOpen/followMode and keeps a dragged width inside the dock’s limits', () => {
    vi.stubGlobal('innerWidth', 1280);
    // The dock's own limits (components/workspace/dockSize), as on Home: the
    // window less 420px at the widest, 360px at the narrowest, whole pixels.
    useCanvasDockStore.getState().setWidth(5000);
    expect(useCanvasDockStore.getState().widthPx).toBe(860);
    useCanvasDockStore.getState().setWidth(512.6);
    expect(useCanvasDockStore.getState().widthPx).toBe(513);
    useCanvasDockStore.getState().setWidth(10);
    expect(useCanvasDockStore.getState().widthPx).toBe(360);

    useCanvasDockStore.getState().toggle();
    useCanvasDockStore.getState().setFollowMode(true);

    const raw = localStorage.getItem(PREFS_KEY);
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw as string);
    expect(parsed).toMatchObject({ open: true, widthPx: 360, followMode: true });
    // Session-only fields never persist.
    expect(parsed).not.toHaveProperty('selectedNodeId');
    expect(parsed).not.toHaveProperty('ephemeralItem');
  });

  it('falls back to defaults on corrupt stored prefs', async () => {
    localStorage.setItem(PREFS_KEY, '{not json');
    vi.resetModules();
    const fresh = await import('../canvasDockStore');
    const state = fresh.useCanvasDockStore.getState();
    expect(state.open).toBe(false);
    expect(state.widthPx).toBe(460);
    expect(state.autoOpen).toBe(true);
  });

  it.each([
    // Inside the limits, the earlier dock's 380px default among them: kept.
    [360, 360],
    [380, 380],
    [4000, 4000],
    // Outside them (the earlier dock went down to 280px), or no number at all.
    [300, 460],
    [359, 460],
    [4001, 460],
    ['wide', 460],
  ])('restores a stored width of %s as %spx, and the other fields as stored', async (stored, widthPx) => {
    localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({ open: true, widthPx: stored, autoOpen: false, followMode: true, tab: 'browser' }),
    );
    vi.resetModules();
    const fresh = await import('../canvasDockStore');
    expect(fresh.useCanvasDockStore.getState()).toMatchObject({
      open: true,
      widthPx,
      autoOpen: false,
      followMode: true,
      tab: 'browser',
    });
  });

  it('showEphemeral opens the dock and backToNode returns to the board', () => {
    useCanvasDockStore.getState().setTab('browser');
    useCanvasDockStore.getState().showEphemeral(ephemeral);
    expect(useCanvasDockStore.getState().open).toBe(true);
    expect(useCanvasDockStore.getState().mode).toBe('ephemeral');
    expect(useCanvasDockStore.getState().tab).toBe('board');

    useCanvasDockStore.getState().backToNode();
    expect(useCanvasDockStore.getState().mode).toBe('node');
    expect(useCanvasDockStore.getState().ephemeralItem).toBeNull();
  });
});
