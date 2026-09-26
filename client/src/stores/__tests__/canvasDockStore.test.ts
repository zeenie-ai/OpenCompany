import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { CanvasItem } from '../../lib/canvasBoard';
import { useCanvasDockStore } from '../canvasDockStore';

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
    widthPx: 380,
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
});

describe('canvasDockStore prefs', () => {
  beforeEach(() => {
    localStorage.clear();
    resetStore();
  });

  it('persists open/width/autoOpen/followMode and clamps width', () => {
    useCanvasDockStore.getState().setWidth(5000);
    expect(useCanvasDockStore.getState().widthPx).toBe(4000);
    useCanvasDockStore.getState().setWidth(10);
    expect(useCanvasDockStore.getState().widthPx).toBe(280);

    useCanvasDockStore.getState().toggle();
    useCanvasDockStore.getState().setFollowMode(true);

    const raw = localStorage.getItem('canvas_dock_prefs_v1');
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw as string);
    expect(parsed).toMatchObject({ open: true, widthPx: 280, followMode: true });
    // Session-only fields never persist.
    expect(parsed).not.toHaveProperty('selectedNodeId');
    expect(parsed).not.toHaveProperty('ephemeralItem');
  });

  it('falls back to defaults on corrupt stored prefs', async () => {
    localStorage.setItem('canvas_dock_prefs_v1', '{not json');
    vi.resetModules();
    const fresh = await import('../canvasDockStore');
    const state = fresh.useCanvasDockStore.getState();
    expect(state.open).toBe(false);
    expect(state.widthPx).toBe(380);
    expect(state.autoOpen).toBe(true);
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
