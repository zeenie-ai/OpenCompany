/**
 * Tests for useGetStarted — the Get Started checklist state hook.
 *
 * Locks the contract:
 *   - visible = settings query success && onboarding_completed && !dismissed
 *   - completion per item = persisted latch field || live signal
 *   - newly-true live signals persist a one-way latch via saveSettings.mutate,
 *     guarded by the settings flag (no re-write when already latched)
 *   - build-workflow excludes the three seeded example workflow names
 *   - try-theme compares against the theme captured on first render
 *   - dismiss()/restore() write getting_started_dismissed true/false
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

// --- controllable mock state -------------------------------------------------
// Factories below return closures that read these at call time (safe under
// vi.mock hoisting — the values are only dereferenced during render).

const settingsState: {
  data: Record<string, any> | undefined;
  isSuccess: boolean;
} = { data: undefined, isSuccess: false };

const saveMutate = vi.fn();

vi.mock('../useUserSettingsQuery', () => ({
  useUserSettingsQuery: () => ({
    data: settingsState.data,
    isSuccess: settingsState.isSuccess,
  }),
  useSaveUserSettingsMutation: () => ({ mutate: saveMutate }),
}));

const catalogueState = { storedCount: 0 };
vi.mock('../useCatalogueQuery', () => ({
  useStoredProviderCount: () => catalogueState.storedCount,
}));

const wsState = {
  chatMessages: [] as Array<{ role: 'user' | 'assistant'; message: string; timestamp: string }>,
};
// Full-module replace — importActual+spread is broken under React 19 (see
// CredentialsModal.test.tsx for the canonical note).
vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ chatMessages: wsState.chatMessages }),
}));

const themeState = { theme: 'dark' };
vi.mock('../../contexts/ThemeContext', () => ({
  useTheme: () => ({ theme: themeState.theme }),
}));

import { useGetStarted } from '../useGetStarted';
import { useAppStore, type WorkflowData } from '../../store/useAppStore';
import type { GetStartedItemId } from '../../components/onboarding/getStartedItems';

// --- helpers -----------------------------------------------------------------

const makeWorkflow = (name: string, nodeCount = 1): WorkflowData => ({
  id: 'wf-1',
  name,
  slug: '',
  nodes: Array.from({ length: nodeCount }, (_, i) => ({
    id: `n${i}`,
    type: 'aiAgent',
    position: { x: 0, y: 0 },
    data: {},
  })) as WorkflowData['nodes'],
  edges: [],
  createdAt: new Date(),
  lastModified: new Date(),
});

const completedSettings = (extra: Record<string, any> = {}) => ({
  onboarding_completed: true,
  ...extra,
});

function itemCompleted(
  result: { current: ReturnType<typeof useGetStarted> },
  id: GetStartedItemId,
): boolean {
  const item = result.current.items.find((i) => i.id === id);
  expect(item).toBeDefined();
  return item!.completed;
}

beforeEach(() => {
  vi.clearAllMocks();
  settingsState.data = completedSettings();
  settingsState.isSuccess = true;
  catalogueState.storedCount = 0;
  wsState.chatMessages = [];
  themeState.theme = 'dark';
  useAppStore.setState({ currentWorkflow: null, hasUnsavedChanges: false });
});

// --- visibility --------------------------------------------------------------

describe('useGetStarted visibility', () => {
  it('is hidden when getting_started_dismissed is set', () => {
    settingsState.data = completedSettings({ getting_started_dismissed: true });
    const { result } = renderHook(() => useGetStarted());
    expect(result.current.visible).toBe(false);
  });

  it('is hidden when onboarding is not completed', () => {
    settingsState.data = { onboarding_completed: false };
    const { result } = renderHook(() => useGetStarted());
    expect(result.current.visible).toBe(false);
  });

  it('is hidden while the settings query is pending, and persists no latches', () => {
    settingsState.data = undefined;
    settingsState.isSuccess = false;
    catalogueState.storedCount = 3; // live signal true, but query not settled
    const { result } = renderHook(() => useGetStarted());
    expect(result.current.visible).toBe(false);
    expect(saveMutate).not.toHaveBeenCalled();
  });

  it('is visible for completed-onboarding, non-dismissed settings', () => {
    const { result } = renderHook(() => useGetStarted());
    expect(result.current.visible).toBe(true);
  });
});

// --- baseline ----------------------------------------------------------------

describe('useGetStarted baseline', () => {
  it('endows fresh settings with only the setup item completed (1 of 5)', () => {
    const { result } = renderHook(() => useGetStarted());
    expect(result.current.totalCount).toBe(5);
    expect(result.current.completedCount).toBe(1);
    expect(itemCompleted(result, 'setup')).toBe(true);
    expect(itemCompleted(result, 'add-key')).toBe(false);
    expect(itemCompleted(result, 'chat-example')).toBe(false);
    expect(itemCompleted(result, 'build-workflow')).toBe(false);
    expect(itemCompleted(result, 'try-theme')).toBe(false);
    expect(saveMutate).not.toHaveBeenCalled();
  });
});

// --- add-key -----------------------------------------------------------------

describe('useGetStarted add-key', () => {
  it('completes when a provider credential is stored and persists the latch', () => {
    catalogueState.storedCount = 2;
    const { result } = renderHook(() => useGetStarted());
    expect(itemCompleted(result, 'add-key')).toBe(true);
    expect(result.current.completedCount).toBe(2); // setup + add-key
    expect(saveMutate).toHaveBeenCalledWith({ getting_started_added_key: true });
    expect(saveMutate).toHaveBeenCalledTimes(1);
  });

  it('does not re-persist when the latch flag is already set', () => {
    settingsState.data = completedSettings({ getting_started_added_key: true });
    catalogueState.storedCount = 2;
    const { result } = renderHook(() => useGetStarted());
    expect(itemCompleted(result, 'add-key')).toBe(true);
    expect(saveMutate).not.toHaveBeenCalled();
  });
});

// --- chat-example ------------------------------------------------------------

describe('useGetStarted chat-example', () => {
  it('stays incomplete with only user messages', () => {
    wsState.chatMessages = [{ role: 'user', message: 'hi', timestamp: 't1' }];
    const { result } = renderHook(() => useGetStarted());
    expect(itemCompleted(result, 'chat-example')).toBe(false);
    expect(saveMutate).not.toHaveBeenCalled();
  });

  it('completes on an assistant message and persists the latch', () => {
    wsState.chatMessages = [
      { role: 'user', message: 'hi', timestamp: 't1' },
      { role: 'assistant', message: 'hello!', timestamp: 't2' },
    ];
    const { result } = renderHook(() => useGetStarted());
    expect(itemCompleted(result, 'chat-example')).toBe(true);
    expect(saveMutate).toHaveBeenCalledWith({ getting_started_ran_example: true });
  });
});

// --- build-workflow ----------------------------------------------------------

describe('useGetStarted build-workflow', () => {
  it('completes for a saved, non-example workflow with nodes', () => {
    useAppStore.setState({
      currentWorkflow: makeWorkflow('My Automation', 2),
      hasUnsavedChanges: false,
    });
    const { result } = renderHook(() => useGetStarted());
    expect(itemCompleted(result, 'build-workflow')).toBe(true);
    expect(saveMutate).toHaveBeenCalledWith({ getting_started_built_workflow: true });
  });

  it('does NOT complete via live signal for a seeded example workflow name', () => {
    useAppStore.setState({
      currentWorkflow: makeWorkflow('AI Assistant', 3),
      hasUnsavedChanges: false,
    });
    const { result } = renderHook(() => useGetStarted());
    expect(itemCompleted(result, 'build-workflow')).toBe(false);
    expect(saveMutate).not.toHaveBeenCalledWith({ getting_started_built_workflow: true });
  });

  it('does not complete while the workflow has unsaved changes', () => {
    useAppStore.setState({
      currentWorkflow: makeWorkflow('My Automation', 2),
      hasUnsavedChanges: true,
    });
    const { result } = renderHook(() => useGetStarted());
    expect(itemCompleted(result, 'build-workflow')).toBe(false);
  });

  it('does not complete for an empty (node-less) workflow', () => {
    useAppStore.setState({
      currentWorkflow: makeWorkflow('My Automation', 0),
      hasUnsavedChanges: false,
    });
    const { result } = renderHook(() => useGetStarted());
    expect(itemCompleted(result, 'build-workflow')).toBe(false);
  });
});

// --- try-theme ---------------------------------------------------------------

describe('useGetStarted try-theme', () => {
  it('stays incomplete while the theme matches the initial theme', () => {
    const { result, rerender } = renderHook(() => useGetStarted());
    expect(itemCompleted(result, 'try-theme')).toBe(false);
    rerender();
    expect(itemCompleted(result, 'try-theme')).toBe(false);
    expect(saveMutate).not.toHaveBeenCalled();
  });

  it('completes when the theme changes from the initial one and persists the latch', () => {
    const { result, rerender } = renderHook(() => useGetStarted());
    expect(itemCompleted(result, 'try-theme')).toBe(false);

    themeState.theme = 'cyber';
    rerender();

    expect(itemCompleted(result, 'try-theme')).toBe(true);
    expect(saveMutate).toHaveBeenCalledWith({ getting_started_tried_theme: true });
  });
});

// --- one-way latch -----------------------------------------------------------

describe('useGetStarted one-way latch', () => {
  it('keeps an item completed via the latch even when the live signal is false', () => {
    settingsState.data = completedSettings({
      getting_started_tried_theme: true,
      getting_started_added_key: true,
    });
    // All live signals false: no stored providers, no chat, same theme.
    const { result } = renderHook(() => useGetStarted());
    expect(itemCompleted(result, 'try-theme')).toBe(true);
    expect(itemCompleted(result, 'add-key')).toBe(true);
    expect(result.current.completedCount).toBe(3); // setup + the two latches
    expect(saveMutate).not.toHaveBeenCalled();
  });
});

// --- dismiss / restore -------------------------------------------------------

describe('useGetStarted dismiss/restore', () => {
  it('dismiss() persists getting_started_dismissed: true', () => {
    const { result } = renderHook(() => useGetStarted());
    act(() => result.current.dismiss());
    expect(saveMutate).toHaveBeenCalledWith({ getting_started_dismissed: true });
  });

  it('restore() persists getting_started_dismissed: false', () => {
    settingsState.data = completedSettings({ getting_started_dismissed: true });
    const { result } = renderHook(() => useGetStarted());
    act(() => result.current.restore());
    expect(saveMutate).toHaveBeenCalledWith({ getting_started_dismissed: false });
  });
});
