/**
 * useOnboarding step-hydration tests.
 *
 * Locks in the persisted-step validation contract: on hydration the hook
 * keeps `onboarding_step` only when `0 <= step < totalSteps`; anything
 * out of range (the old 5-step wizard persisted step 4, the new wizard
 * has 4 steps) resets to 0 so a replay never starts past the end.
 * Also locks the visibility gate on `onboarding_completed`.
 *
 * useUserSettingsQuery / useSaveUserSettingsMutation are module-mocked
 * (full replace) so no WebSocket / TanStack plumbing is exercised.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { wrapperWithProviders } from '../../test/providers';

// --- Mocks (declared BEFORE importing the hook -- vi.mock is hoisted) -------

interface SettingsQueryState {
  isSuccess: boolean;
  isError: boolean;
  data: Record<string, unknown> | undefined;
  error: Error | null;
}

let settingsQueryState: SettingsQueryState;
const mutateMock = vi.fn();

vi.mock('../useUserSettingsQuery', () => ({
  useUserSettingsQuery: () => settingsQueryState,
  useSaveUserSettingsMutation: () => ({ mutate: mutateMock }),
}));

import { useOnboarding } from '../useOnboarding';

const TOTAL_STEPS = 4;

const renderOnboarding = () =>
  renderHook(() => useOnboarding(undefined, TOTAL_STEPS), {
    wrapper: wrapperWithProviders(),
  });

beforeEach(() => {
  mutateMock.mockClear();
  settingsQueryState = {
    isSuccess: true,
    isError: false,
    data: {},
    error: null,
  };
});

// --- Tests ------------------------------------------------------------------

describe('useOnboarding persisted-step hydration', () => {
  it('resets an out-of-range persisted step (old 5-step wizard) to 0 and stays visible', () => {
    settingsQueryState.data = { onboarding_step: 4, onboarding_completed: false };
    const { result } = renderOnboarding();

    expect(result.current.currentStep).toBe(0);
    expect(result.current.isVisible).toBe(true);
    expect(result.current.isCompleted).toBe(false);
    expect(result.current.hasChecked).toBe(true);
    expect(result.current.isLoading).toBe(false);
  });

  it('keeps an in-range persisted step', () => {
    settingsQueryState.data = { onboarding_step: 2, onboarding_completed: false };
    const { result } = renderOnboarding();

    expect(result.current.currentStep).toBe(2);
    expect(result.current.isVisible).toBe(true);
  });

  it('resets a negative persisted step to 0', () => {
    settingsQueryState.data = { onboarding_step: -1, onboarding_completed: false };
    const { result } = renderOnboarding();

    expect(result.current.currentStep).toBe(0);
    expect(result.current.isVisible).toBe(true);
  });

  it('stays hidden when onboarding is already completed', () => {
    settingsQueryState.data = { onboarding_step: 1, onboarding_completed: true };
    const { result } = renderOnboarding();

    expect(result.current.isVisible).toBe(false);
    expect(result.current.isCompleted).toBe(true);
    expect(result.current.hasChecked).toBe(true);
    expect(result.current.isLoading).toBe(false);
  });
});
