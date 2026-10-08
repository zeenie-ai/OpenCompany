/**
 * The composer's binding to the draft: its text, whether it is editing the
 * current draft, and what sending does. A template chip puts its job in the
 * box for the owner to read, change and send.
 *
 * Whether an AI model is set up is the server's call (it answers
 * `no_ai_provider` before any model is asked); when it says none is, the
 * guided "Connect an AI model" dialog opens, the owner's words stay in the
 * draft, and the draft is sent again by itself once a model is connected.
 */

import { useCallback, useEffect, useRef } from 'react';
import { useConnectors } from '../data/connectors';
import { useHomeStore } from '../state/homeStore';
import { useDraftActions, useDraftStore } from './draftStore';

/**
 * The job box alone, with no side effects: its text, whether a setup is
 * being written, picking a starter's job, and sending a new job. The
 * Welcome guide's composer uses it, so the effects below stay mounted once
 * (in the hire view) and picking a starter there takes no focus from Home.
 */
export function useJobComposer() {
  const value = useDraftStore((s) => s.input);
  const status = useDraftStore((s) => s.status);
  const hiring = useDraftStore((s) => s.hiring);
  const actions = useDraftActions();
  return {
    value,
    onChange: actions.setInput,
    working: status === 'working' || hiring,
    pick: useCallback(
      (job: string) => {
        actions.setRefining(false);
        actions.setInput(job);
      },
      [actions],
    ),
    /** Send the box as a new job (never a change to the current draft). */
    submit: useCallback(() => actions.submit(useDraftStore.getState().input, { refine: false }), [actions]),
  };
}

export function useHireComposer() {
  const job = useJobComposer();
  const refining = useDraftStore((s) => s.refining);
  const failure = useDraftStore((s) => s.failure);
  const actions = useDraftActions();
  const { hasAi, isLoading } = useConnectors();

  // Open the guided dialog once per "no AI model" answer.
  const handled = useRef<unknown>(null);
  useEffect(() => {
    if (failure?.code === 'no_ai_provider' && handled.current !== failure) {
      handled.current = failure;
      useHomeStore.getState().openConnectAI();
    }
  }, [failure]);

  // A model connected since: send the draft that was waiting for one.
  const hadAi = useRef<boolean | null>(null);
  useEffect(() => {
    if (isLoading) return;
    const before = hadAi.current;
    hadAi.current = hasAi;
    if (before === false && hasAi && useDraftStore.getState().failure?.code === 'no_ai_provider') void actions.retry();
  }, [hasAi, isLoading, actions]);

  const send = useCallback((text: string, options?: { refine?: boolean }) => void actions.submit(text, options), [actions]);
  const pickJob = job.pick;

  return {
    value: job.value,
    onChange: job.onChange,
    onSubmit: useCallback(() => send(useDraftStore.getState().input), [send]),
    refining,
    onStopRefining: useCallback(() => actions.setRefining(false), [actions]),
    working: job.working,
    /** A template: its job replaces whatever is in the box, ready to send. */
    pick: useCallback(
      (text: string) => {
        pickJob(text);
        useHomeStore.getState().showHire({ focus: true });
      },
      [pickJob],
    ),
  };
}
