/**
 * The mode switch choreography (design handoff "Mode switch"): the current
 * screen fades out with a slight blur and scale over --dur-mode-out, the
 * screens swap, and the new screen plays its entry. Each screen owns its own
 * entrance; the shell only marks that one is due (`consumeShellEntry`).
 *
 * Requests that arrive while a switch is running coalesce: the latest target
 * wins once the running exit lands, and a request that returns to where it
 * started restores the screen instead of swapping.
 */

import { flushSync } from 'react-dom';
import { animate, finished } from '../lib/motion';
import { useAppStore, type ShellMode } from '../store/useAppStore';

/** Each screen's root carries this attribute (see ShellScreen). */
export const SHELL_SCREEN_ATTR = 'data-shell-screen';

const EXIT: Keyframe[] = [
  { opacity: 1, filter: 'blur(0px)', transform: 'none' },
  { opacity: 0, filter: 'blur(6px)', transform: 'scale(0.985)' },
];

let running: Promise<void> | null = null;
let wanted: ShellMode | null = null;
let entryDue = false;

/** True once after a transition swapped screens: the mounting screen plays
 *  its entrance. False on first load, where screens run their own intro. */
export function consumeShellEntry(): boolean {
  const due = entryDue;
  entryDue = false;
  return due;
}

async function drive(): Promise<void> {
  for (;;) {
    const current = useAppStore.getState().shellMode;
    if (!wanted || wanted === current) return;
    const screen = document.querySelector(`[${SHELL_SCREEN_ATTR}]`);
    const exit = animate(screen, EXIT, { duration: 'mode-out', fill: 'forwards' });
    await finished(exit);
    const target = wanted;
    if (!target || target === current) {
      // Toggled back while fading out: bring the screen back as it was.
      exit?.cancel();
      return;
    }
    entryDue = true;
    // Commit now, so a coalesced follow-up finds the new screen.
    flushSync(() => useAppStore.getState().setShellMode(target));
  }
}

export function transitionShell(to: ShellMode): Promise<void> {
  wanted = to;
  if (!running) {
    // Cleared in a chained finally, which always runs after this
    // assignment. (A try/finally inside the async body can run first, when
    // there is nothing to do, and leave `running` set forever.)
    running = drive().finally(() => {
      running = null;
      const next = wanted;
      wanted = null;
      // A request that landed after the loop's last check.
      if (next && next !== useAppStore.getState().shellMode) void transitionShell(next);
    });
  }
  return running;
}
