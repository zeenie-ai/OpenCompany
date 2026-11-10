/**
 * The Workspace dock's width on Home: while the dock pushes the page aside
 * it leaves the page room for its header, more with the sidebar closed
 * (the header then holds the logo too); lying over the page, only the
 * window limits it.
 */

import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useHomeStore } from '../../state/homeStore';
import { useHomeDockWidth } from '../dockWidth';

/** The window is wide enough for the dock to push the page aside, or not. */
const windowPushes = (pushes: boolean) =>
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: pushes,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));

const width = () => renderHook(() => useHomeDockWidth()).result.current;

beforeEach(() => {
  useHomeStore.setState({ workspaceWidth: 500, workspaceWide: false, sidebarOpen: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useHomeDockWidth', () => {
  it('is limited only by the window while the dock lies over the page', () => {
    windowPushes(false);
    expect(width()).toBe('min(500px, max(340px, 100vw - 40px))');
    useHomeStore.setState({ sidebarOpen: false });
    expect(width()).toBe('min(500px, max(340px, 100vw - 40px))');
  });

  it('leaves the page 560px beside the open sidebar while it pushes the page aside', () => {
    windowPushes(true);
    expect(width()).toBe('min(500px, max(360px, 100vw - calc(var(--w-home-sidebar) + 560px)))');
  });

  it('leaves the page 760px with the sidebar closed, when the header holds the logo too', () => {
    windowPushes(true);
    useHomeStore.setState({ sidebarOpen: false });
    expect(width()).toBe('min(500px, max(360px, 100vw - 760px))');
  });

  it('gives Expand the same room', () => {
    windowPushes(true);
    useHomeStore.setState({ workspaceWide: true });
    expect(width()).toBe('min(1100px, max(360px, 100vw - calc(var(--w-home-sidebar) + 560px)))');
  });
});
