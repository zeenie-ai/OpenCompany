/**
 * The shell's boot effects: page-activity tracking (the attribute that
 * pauses CSS animation, and the store script-driven motion reads) and the
 * once-per-load UI defaults.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const sendRequest = vi.fn();
const actions = { isConnected: true, sendRequest };

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocketActions: () => actions,
}));

vi.mock('../../services/workflowApi', () => ({
  workflowApi: { getAllWorkflows: vi.fn(), getWorkflow: vi.fn(), saveWorkflow: vi.fn(), deleteWorkflow: vi.fn() },
}));

import { usePageActivitySync } from '../usePageActivitySync';
import { resetUIDefaultsForTests, useUIDefaultsOnce } from '../useUIDefaultsOnce';
import { isModeShortcut } from '../useModeShortcut';
import { pageActivity } from '../../lib/pageActivity';
import { useAppStore } from '../../store/useAppStore';
import { useWorkflowSettingsStore } from '../../stores/workflowSettingsStore';

const root = document.documentElement;
const hidden = () => root.hasAttribute('data-page-hidden');

describe('usePageActivitySync', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'setTimeout', 'clearTimeout'] });
  });

  afterEach(() => {
    vi.useRealTimers();
    root.removeAttribute('data-page-hidden');
    pageActivity.set(true);
  });

  it('pauses on blur and resumes two frames after focus', () => {
    renderHook(() => usePageActivitySync());
    act(() => {
      window.dispatchEvent(new Event('blur'));
    });
    expect(hidden()).toBe(true);
    expect(pageActivity.isActive()).toBe(false);

    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    expect(hidden()).toBe(true);
    act(() => {
      vi.advanceTimersToNextFrame();
      vi.advanceTimersToNextFrame();
    });
    expect(hidden()).toBe(false);
    expect(pageActivity.isActive()).toBe(true);
  });

  it('a hide during the resume frames wins', () => {
    renderHook(() => usePageActivitySync());
    act(() => {
      window.dispatchEvent(new Event('blur'));
      window.dispatchEvent(new Event('focus'));
      window.dispatchEvent(new Event('blur'));
    });
    act(() => {
      vi.advanceTimersToNextFrame();
      vi.advanceTimersToNextFrame();
      vi.advanceTimersToNextFrame();
    });
    expect(hidden()).toBe(true);
    expect(pageActivity.isActive()).toBe(false);
  });

  it('never leaves animation paused after unmount, and cancels pending frames', () => {
    const cancel = vi.spyOn(window, 'cancelAnimationFrame');
    const { unmount } = renderHook(() => usePageActivitySync());
    act(() => {
      window.dispatchEvent(new Event('blur'));
      window.dispatchEvent(new Event('focus'));
    });
    unmount();
    expect(cancel).toHaveBeenCalled();
    expect(hidden()).toBe(false);
    expect(pageActivity.isActive()).toBe(true);
    // A late frame must not flip anything back.
    act(() => {
      vi.advanceTimersToNextFrame();
      vi.advanceTimersToNextFrame();
    });
    expect(hidden()).toBe(false);
  });
});

describe('useUIDefaultsOnce', () => {
  beforeEach(() => {
    resetUIDefaultsForTests();
    sendRequest.mockReset();
    localStorage.clear();
  });

  it('applies the saved UI defaults once per page load, not per mount', async () => {
    sendRequest.mockResolvedValue({
      settings: { sidebar_default_open: false, console_panel_default_open: true, auto_save: false },
    });
    const first = renderHook(() => useUIDefaultsOnce());
    await act(async () => {
      await Promise.resolve();
    });
    expect(sendRequest).toHaveBeenCalledWith('get_user_settings', {});
    expect(useAppStore.getState().sidebarVisible).toBe(false);
    expect(useAppStore.getState().consolePanelVisible).toBe(true);
    expect(useWorkflowSettingsStore.getState().settings.autoSave).toBe(false);
    first.unmount();

    // The owner reopens the sidebar, leaves the editor and comes back.
    useAppStore.getState().setSidebarVisible(true);
    renderHook(() => useUIDefaultsOnce());
    await act(async () => {
      await Promise.resolve();
    });
    expect(sendRequest).toHaveBeenCalledTimes(1);
    expect(useAppStore.getState().sidebarVisible).toBe(true);
  });

  it('retries after a failed load', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    sendRequest.mockRejectedValueOnce(new Error('socket closed'));
    const first = renderHook(() => useUIDefaultsOnce());
    await act(async () => {
      await Promise.resolve();
    });
    first.unmount();
    sendRequest.mockResolvedValue({ settings: {} });
    renderHook(() => useUIDefaultsOnce());
    await act(async () => {
      await Promise.resolve();
    });
    expect(sendRequest).toHaveBeenCalledTimes(2);
  });
});

describe('isModeShortcut', () => {
  const key = (init: KeyboardEventInit) => new KeyboardEvent('keydown', init);

  it.each<[string, KeyboardEventInit, boolean]>([
    ['Ctrl+Shift+D', { key: 'D', code: 'KeyD', ctrlKey: true, shiftKey: true }, true],
    ['Cmd+Shift+D', { key: 'd', code: 'KeyD', metaKey: true, shiftKey: true }, true],
    ['a non-Latin layout', { key: 'в', code: 'KeyD', ctrlKey: true, shiftKey: true }, true],
    ['Ctrl+D', { key: 'd', code: 'KeyD', ctrlKey: true }, false],
    ['Shift+D', { key: 'D', code: 'KeyD', shiftKey: true }, false],
    ['Ctrl+Shift+Alt+D', { key: 'D', code: 'KeyD', ctrlKey: true, shiftKey: true, altKey: true }, false],
    ['Ctrl+Shift+E', { key: 'E', code: 'KeyE', ctrlKey: true, shiftKey: true }, false],
    ['IME composition', { key: 'D', code: 'KeyD', ctrlKey: true, shiftKey: true, isComposing: true }, false],
  ])('%s -> %s', (_label, init, expected) => {
    expect(isModeShortcut(key(init))).toBe(expected);
  });
});
