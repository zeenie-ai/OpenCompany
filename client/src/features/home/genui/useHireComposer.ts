/**
 * The composer's binding to the draft: its text, whether it is editing the
 * current draft, and what sending does. A template chip sends its job
 * straight away.
 *
 * Whether an AI model is set up is the server's call (it answers
 * `no_ai_provider` before any model is asked); when it says none is, the
 * Connectors tab opens on AI and the owner's words stay in the draft for
 * Try again.
 */

import { useCallback, useEffect, useRef } from 'react';
import { useHomeStore } from '../state/homeStore';
import { useDraftActions, useDraftStore } from './draftStore';

export function useHireComposer() {
  const value = useDraftStore((s) => s.input);
  const refining = useDraftStore((s) => s.refining);
  const status = useDraftStore((s) => s.status);
  const hiring = useDraftStore((s) => s.hiring);
  const failure = useDraftStore((s) => s.failure);
  const actions = useDraftActions();

  // Open Connectors > AI once per "no AI model" answer.
  const handled = useRef<unknown>(null);
  useEffect(() => {
    if (failure?.code === 'no_ai_provider' && handled.current !== failure) {
      handled.current = failure;
      useHomeStore.getState().openSettings('connectors', 'ai');
    }
  }, [failure]);

  const send = useCallback((text: string, options?: { refine?: boolean }) => void actions.submit(text, options), [actions]);

  return {
    value,
    onChange: actions.setInput,
    onSubmit: useCallback(() => send(useDraftStore.getState().input), [send]),
    refining,
    onStopRefining: useCallback(() => actions.setRefining(false), [actions]),
    working: status === 'working' || hiring,
    /** A template: its job replaces whatever is in the box and is sent. */
    pick: useCallback((job: string) => send(job, { refine: false }), [send]),
  };
}
