/**
 * `shellMode` is read once when the store is created. The first read
 * migrates the legacy palette choice (`ui_pro_mode`): someone who had
 * switched the palette to Dev starts in Dev mode, everyone else in Normal.
 * After that `ui_shell_mode` is the only key that matters.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/workflowApi', () => ({
  workflowApi: {
    getAllWorkflows: vi.fn(),
    getWorkflow: vi.fn(),
    saveWorkflow: vi.fn(),
    deleteWorkflow: vi.fn(),
  },
}));

async function freshStore(stored: Record<string, string>) {
  localStorage.clear();
  for (const [key, value] of Object.entries(stored)) localStorage.setItem(key, value);
  vi.resetModules();
  const { useAppStore } = await import('../useAppStore');
  return useAppStore;
}

afterEach(() => {
  localStorage.clear();
});

describe('shellMode', () => {
  it.each<[string, Record<string, string>, 'normal' | 'dev']>([
    ['a first visit', {}, 'normal'],
    ['someone who had switched the palette to Dev', { ui_pro_mode: 'true' }, 'dev'],
    ['someone on the Normal palette', { ui_pro_mode: 'false' }, 'normal'],
    ['a saved shell choice over the palette one', { ui_shell_mode: 'normal', ui_pro_mode: 'true' }, 'normal'],
    ['a saved Dev choice', { ui_shell_mode: 'dev' }, 'dev'],
    ['an unknown saved value, falling back to the palette', { ui_shell_mode: 'kiosk', ui_pro_mode: 'true' }, 'dev'],
  ])('starts right for %s', async (_label, stored, expected) => {
    const store = await freshStore(stored);
    expect(store.getState().shellMode).toBe(expected);
  });

  it('persists a switch and leaves the palette choice alone', async () => {
    const store = await freshStore({ ui_pro_mode: 'false' });
    store.getState().setShellMode('dev');
    expect(store.getState().shellMode).toBe('dev');
    expect(localStorage.getItem('ui_shell_mode')).toBe('dev');
    expect(localStorage.getItem('ui_pro_mode')).toBe('false');
    expect(store.getState().proMode).toBe(false);
  });

  it('does not notify subscribers when the mode is unchanged', async () => {
    const store = await freshStore({ ui_shell_mode: 'normal' });
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.getState().setShellMode('normal');
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });
});
