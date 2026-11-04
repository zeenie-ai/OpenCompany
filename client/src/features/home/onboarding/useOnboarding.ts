/**
 * The Welcome guide's moves and saved progress.
 *
 * Whether the guide is open, and at which step, lives in homeStore
 * (`guide`): the header's Guide button and Settings > Help open it
 * directly, and since nothing counts replays, a remount (a mode switch
 * re-keys the screens) never reopens it. This hook adds the saved side:
 * once per session it opens the guide for an owner who has not finished it
 * (`onboarding_completed`, at their saved `onboarding_step`), and it saves
 * the step on every move. Skip, the close button, Esc and a click outside
 * finish the guide at the step reached; "Create their setup" finishes it.
 * A replay never marks it unfinished.
 *
 * Mount it once (WelcomeGuide). It reads the settings through the stable
 * actions context, so Home never re-renders on a WebSocket message.
 */

import { useCallback, useEffect } from 'react';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { useSaveUserSettingsMutationCore } from '@/hooks/useUserSettingsQuery';
import { useOwnerSettings } from '../data/profile';
import { useSendJob } from '../genui';
import { GUIDE_STEPS, useHomeStore, type GuideStep } from '../state/homeStore';

export function useOnboarding() {
  const guide = useHomeStore((s) => s.guide);
  const { data: settings, isSuccess } = useOwnerSettings();
  const { sendRequest } = useWebSocketActions();
  const { mutate: save } = useSaveUserSettingsMutationCore(sendRequest);
  const sendJob = useSendJob();

  useEffect(() => {
    if (isSuccess && settings) useHomeStore.getState().checkGuide(settings);
  }, [isSuccess, settings]);

  const goTo = useCallback(
    (step: GuideStep) => {
      useHomeStore.getState().goToGuideStep(step);
      save({ onboarding_step: GUIDE_STEPS.indexOf(step) });
    },
    [save],
  );

  const done = useCallback(
    (step: number) => {
      useHomeStore.getState().closeGuide();
      save({ onboarding_completed: true, onboarding_step: step });
    },
    [save],
  );

  const index = GUIDE_STEPS.indexOf(guide.step);
  const nextStep: GuideStep | undefined = GUIDE_STEPS[index + 1];
  const prevStep: GuideStep | undefined = GUIDE_STEPS[index - 1];

  return {
    ...guide,
    index,
    goTo,
    /** Undefined on the last step. */
    next: nextStep ? () => goTo(nextStep) : undefined,
    /** Undefined on the first step. */
    back: prevStep ? () => goTo(prevStep) : undefined,
    /** Skip for now, the close button, Esc: done, at the step reached. */
    skip: () => done(index),
    /** The guide's end, Create their setup: done, then Home's hire view
     *  writes the setup for the job in the box. */
    finish: () => {
      done(GUIDE_STEPS.length);
      useHomeStore.getState().showHire();
      void sendJob();
    },
  };
}
