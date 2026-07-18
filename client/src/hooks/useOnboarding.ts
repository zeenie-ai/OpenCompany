import { useState, useCallback, useEffect } from 'react';
import {
  useUserSettingsQuery,
  useSaveUserSettingsMutation,
} from './useUserSettingsQuery';

export interface OnboardingState {
  isVisible: boolean;
  currentStep: number;
  isCompleted: boolean;
  isLoading: boolean;
  hasChecked: boolean;
}

const DEFAULT_TOTAL_STEPS = 5;

/**
 * Onboarding wizard state hook.
 *
 * @param reopenTrigger - bump from SettingsPanel to replay the wizard.
 * @param totalSteps - number of steps the wizard will render. Caller
 *   owns the step list (`STEPS.length` in OnboardingWizard); the hook
 *   uses this only to detect last-step completion. Defaults to 5 for
 *   backwards compatibility.
 */
export const useOnboarding = (
  reopenTrigger?: number,
  totalSteps: number = DEFAULT_TOTAL_STEPS,
) => {
  const settingsQuery = useUserSettingsQuery();
  const saveSettings = useSaveUserSettingsMutation();
  const [state, setState] = useState<OnboardingState>({
    isVisible: false,
    currentStep: 0,
    isCompleted: false,
    isLoading: true,
    hasChecked: false,
  });

  // Hydrate UI state from query result.
  useEffect(() => {
    if (!settingsQuery.isSuccess) return;
    const settings = settingsQuery.data;
    const completed = settings?.onboarding_completed ?? false;
    const storedStep = settings?.onboarding_step ?? 0;
    const step = storedStep >= 0 && storedStep < totalSteps ? storedStep : 0;
    setState((prev) => ({
      ...prev,
      // Only flip visibility on first hydration; later renders shouldn't
      // re-open the wizard if the user manually closed it.
      isVisible: prev.hasChecked ? prev.isVisible : !completed,
      currentStep: prev.hasChecked ? prev.currentStep : step,
      isCompleted: completed,
      isLoading: false,
      hasChecked: true,
    }));
  }, [settingsQuery.isSuccess, settingsQuery.data, totalSteps]);

  // Surface query errors as a non-blocking "checked" state so the app
  // continues even if the WS round-trip failed.
  useEffect(() => {
    if (settingsQuery.isError) {
      console.error('[Onboarding] Failed to check status:', settingsQuery.error);
      setState((prev) => ({ ...prev, isLoading: false, hasChecked: true }));
    }
  }, [settingsQuery.isError, settingsQuery.error]);

  // Replay trigger from SettingsPanel.
  useEffect(() => {
    if (reopenTrigger && reopenTrigger > 0) {
      setState((prev) => ({
        ...prev,
        isVisible: true,
        currentStep: 0,
        isCompleted: false,
      }));
    }
  }, [reopenTrigger]);

  const saveProgress = useCallback(
    (step: number, completed: boolean) => {
      saveSettings.mutate({
        onboarding_step: step,
        onboarding_completed: completed,
      });
    },
    [saveSettings],
  );

  const nextStep = useCallback(() => {
    setState((prev) => {
      const next = prev.currentStep + 1;
      if (next >= totalSteps) {
        saveProgress(totalSteps, true);
        return { ...prev, currentStep: next, isCompleted: true, isVisible: false };
      }
      saveProgress(next, false);
      return { ...prev, currentStep: next };
    });
  }, [saveProgress, totalSteps]);

  const prevStep = useCallback(() => {
    setState((prev) => {
      const next = Math.max(0, prev.currentStep - 1);
      saveProgress(next, false);
      return { ...prev, currentStep: next };
    });
  }, [saveProgress]);

  const skip = useCallback(() => {
    saveProgress(state.currentStep, true);
    setState((prev) => ({ ...prev, isVisible: false, isCompleted: true }));
  }, [saveProgress, state.currentStep]);

  const complete = useCallback(() => {
    saveProgress(totalSteps, true);
    setState((prev) => ({ ...prev, isVisible: false, isCompleted: true }));
  }, [saveProgress, totalSteps]);

  return {
    ...state,
    totalSteps,
    nextStep,
    prevStep,
    skip,
    complete,
  };
};
