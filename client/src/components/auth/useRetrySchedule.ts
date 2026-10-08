/**
 * When the Connecting screen checks the server again (onboarding handoff
 * R4): after each wait in CONNECT_RETRY.DELAYS_S in turn, then the last one
 * every time, counted down a second at a time where the owner can see it.
 * Try now checks at once. `check` resolves true once the server answered;
 * the countdown then stops and the screen says it found the server.
 *
 * `attempt` counts the checks that failed, the first being the one that
 * brought the screen up.
 */

import { useCallback, useEffect, useReducer } from 'react';
import { CONNECT_RETRY } from '@/lib/connectionConfig';

interface Schedule {
  attempt: number;
  /** Seconds until the next check. */
  wait: number;
  /** A check is under way. */
  checking: boolean;
  /** The server answered. */
  found: boolean;
}

type Event = 'second' | 'now' | 'failed' | 'found';

/** The wait after `attempt` failed checks. */
function delayAfter(attempt: number): number {
  const delays = CONNECT_RETRY.DELAYS_S;
  return delays[Math.min(attempt, delays.length) - 1];
}

const START: Schedule = { attempt: 1, wait: delayAfter(1), checking: false, found: false };

function next(state: Schedule, event: Event): Schedule {
  if (state.found) return state;
  switch (event) {
    case 'second':
      if (state.checking) return state;
      return state.wait <= 1 ? { ...state, wait: 0, checking: true } : { ...state, wait: state.wait - 1 };
    case 'now':
      return state.checking ? state : { ...state, wait: 0, checking: true };
    case 'failed':
      return { attempt: state.attempt + 1, wait: delayAfter(state.attempt + 1), checking: false, found: false };
    case 'found':
      return { ...state, checking: false, found: true };
  }
}

export function useRetrySchedule(check: () => Promise<boolean>): Schedule & { tryNow: () => void } {
  const [state, dispatch] = useReducer(next, START);
  const { checking, found } = state;

  useEffect(() => {
    if (checking || found) return;
    const timer = window.setInterval(() => dispatch('second'), 1000);
    return () => window.clearInterval(timer);
  }, [checking, found]);

  useEffect(() => {
    if (!checking) return;
    let current = true;
    check().then(
      (answered) => {
        if (current) dispatch(answered ? 'found' : 'failed');
      },
      () => {
        if (current) dispatch('failed');
      },
    );
    return () => {
      current = false;
    };
  }, [checking, check]);

  const tryNow = useCallback(() => dispatch('now'), []);
  return { ...state, tryNow };
}
