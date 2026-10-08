/**
 * The Welcome guide's state in homeStore: the first-launch check runs once
 * per session and only opens the guide for an owner who has not finished
 * it; opening and moving remember the furthest step visited; moving leaves
 * a provider's page; closing keeps the step but not a job waiting for a
 * model.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { useHomeStore, type GuideState } from '../state/homeStore';

const guide = () => useHomeStore.getState().guide;
const fresh = (patch: Partial<GuideState> = {}): GuideState => ({
  open: false,
  step: 'welcome',
  furthest: 0,
  checked: false,
  provider: null,
  pendingDraft: false,
  ...patch,
});

beforeEach(() => {
  useHomeStore.setState({ guide: fresh() });
});

describe('the guide state', () => {
  it('opens at the saved step once, and never for a finished owner', () => {
    useHomeStore.getState().checkGuide({ onboarding_completed: false, onboarding_step: 1 });
    expect(guide()).toEqual(fresh({ open: true, step: 'connect', furthest: 1, checked: true }));
    useHomeStore.getState().closeGuide();
    useHomeStore.getState().checkGuide({ onboarding_completed: false, onboarding_step: 2 });
    expect(guide()).toMatchObject({ open: false, step: 'connect' });

    useHomeStore.setState({ guide: fresh() });
    useHomeStore.getState().checkGuide({ onboarding_completed: true, onboarding_step: 1 });
    expect(guide()).toMatchObject({ open: false, checked: true });
  });

  it('starts on Welcome for a saved step that is not one of its steps', () => {
    for (const saved of [-1, 3, 1.5, 'two', null]) {
      useHomeStore.setState({ guide: fresh({ step: 'connect' }) });
      useHomeStore.getState().checkGuide({ onboarding_completed: false, onboarding_step: saved });
      expect(guide().step).toBe('welcome');
    }
  });

  it('remembers the furthest step, and keeps the step while closing', () => {
    useHomeStore.getState().openGuide('first-hire');
    useHomeStore.getState().goToGuideStep('welcome');
    expect(guide()).toMatchObject({ open: true, step: 'welcome', furthest: 2 });
    useHomeStore.getState().closeGuide();
    expect(guide()).toMatchObject({ open: false, step: 'welcome', furthest: 2 });
    useHomeStore.getState().openGuide();
    expect(guide()).toMatchObject({ open: true, step: 'welcome', furthest: 2 });
  });

  it('leaves a provider’s page on a move, and drops a waiting job when it closes', () => {
    useHomeStore.getState().openGuide('connect');
    useHomeStore.getState().setGuideProvider({ id: 'openai', intent: 'connect' });
    useHomeStore.getState().setGuidePendingDraft(true);
    useHomeStore.getState().goToGuideStep('connect');
    expect(guide()).toMatchObject({ provider: null, pendingDraft: true });
    useHomeStore.getState().closeGuide();
    expect(guide().pendingDraft).toBe(false);
    useHomeStore.getState().setGuideProvider({ id: 'openai', intent: 'manage' });
    useHomeStore.getState().openGuide('welcome');
    expect(guide().provider).toBeNull();
  });
});
