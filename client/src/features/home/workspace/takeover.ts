/**
 * Take over (design handoff "Workspace panel"): the owner takes the screen
 * the Workspace shows (Browser or Mobile) and the employee stops while they
 * use it; Hand back gives that screen back and, when Take over stopped them,
 * resumes them.
 *
 * Take over claims the screen first, so the employee never acts on it again
 * once the owner has it, and the owner is in control from that moment. Then
 * it stops a running employee. The Stop waits for what they have in flight
 * (the server drains their admitted actions), and that can be the very call
 * in which they wait for the owner: the browser's `request_user`, or a phone
 * task. Only Hand back ends that wait, so Hand back works while the Stop is
 * still draining. A Stop that fails gives the screen back, unless Hand back
 * already has.
 *
 * The screen can let go of the owner while the employee waits: the browser
 * hands control back when the window loses focus or its view hides, and the
 * phone its lease when its view hides. Take over on that screen then takes
 * it again, and the employee goes on waiting on the same Stop.
 *
 * Hand back gives the screen back first, so that call can go on, then waits
 * for Take over's Stop and resumes them from the newest control it has, the
 * summary's or the Stop's answer: the server resumes only from paused, at the
 * current revision. A Stop answers within 10 s even while the drain goes on,
 * so while the control is still pausing Hand back sends the Stop again, which
 * waits for the drain and answers with the paused revision.
 *
 * What happened is kept in homeStore (`takeovers`, by employee): the tab
 * whose screen was taken, the way to give it back, and Take over's Stop. So
 * Hand back gives back the screen that was taken and knows whether to resume
 * them, even after the dock was closed or moved to another tab or employee.
 * Taking a screen or handing it back is `busy` per employee too
 * (`takeoverBusy`), since one dock serves them all: a Hand back that waits
 * for a long drain never holds another employee's Take over.
 */

import { mergeWorkflowControlStatus, useWebSocketActions } from '@/contexts/WebSocketContext';
import type { SurfaceControl } from '@/components/workspace/surface';
import { presentEmployee } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { controlErrorMessage } from '../employee/useEmployeeControl';
import { useHomeStore, type WorkspaceTab } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

export interface TakeOver {
  /** The owner has this employee's screen. */
  active: boolean;
  /** The tab whose screen they took, while they have it. */
  screen: WorkspaceTab | null;
  /** Taking the screen or handing it back, not while Take over's Stop drains. */
  busy: boolean;
  /** Takes the screen on show; `onTaken` runs once it is the owner's. */
  takeOver: (onTaken?: () => void) => Promise<void>;
  handBack: () => Promise<void>;
}

/** `tab`: the Workspace tab on show; `surface`: its screen, or null on the
 *  Canvas tab. */
export function useTakeOver(employee: EmployeeSummary, tab: WorkspaceTab, surface: () => SurfaceControl | null): TakeOver {
  const { pauseWorkflow, resumeWorkflow } = useWebSocketActions();
  const mine = useHomeStore((s) => s.takeovers[employee.workflow_id]);
  const setTakeover = useHomeStore((s) => s.setTakeover);
  const busy = useHomeStore((s) => Boolean(s.takeoverBusy[employee.workflow_id]));
  const setBusy = useHomeStore((s) => s.setTakeoverBusy);
  const busyNow = (id: string) => Boolean(useHomeStore.getState().takeoverBusy[id]);

  const takeOver = async (onTaken?: () => void) => {
    const screen = surface();
    const id = employee.workflow_id;
    if (!screen || busyNow(id)) return;
    setBusy(id, true);
    const giveBack = await screen.claim().finally(() => setBusy(id, false));
    if (!giveBack) {
      pillToast('Couldn’t take the screen. Try again.', { tone: 'error' });
      return;
    }
    // Taken again after it let go: they still wait, on the Stop they had.
    const taken = useHomeStore.getState().takeovers[id];
    if (taken) {
      setTakeover(id, { ...taken, giveBack });
      onTaken?.();
      return;
    }
    // Only a running employee is stopped, and only then resumed later.
    const stop = presentEmployee(employee).primary.kind === 'pause' ? pauseWorkflow(id, employee.control.revision) : undefined;
    setTakeover(id, { screen: tab, giveBack, stop });
    onTaken?.();
    pillToast(`You have control — ${employee.name} will wait`);
    if (!stop) return;
    try {
      await stop;
    } catch (error) {
      // Not stopped: give the screen back, unless Hand back already has.
      const now = useHomeStore.getState().takeovers[id];
      if (now?.stop !== stop) return;
      now.giveBack();
      setTakeover(id, null);
      if (error instanceof Error && error.message === 'workflow_control_superseded') return;
      pillToast(controlErrorMessage(error), { tone: 'error' });
    }
  };

  const handBack = async () => {
    const id = employee.workflow_id;
    if (!mine || busyNow(id)) return;
    setBusy(id, true);
    // The screen first, so the call the employee waits in can go on.
    mine.giveBack();
    setTakeover(id, null);
    try {
      if (mine.stop) {
        // A Stop that failed leaves the summary to say where they are.
        const answer = await mine.stop.catch(() => undefined);
        let control = answer ? mergeWorkflowControlStatus(employee.control, answer) : employee.control;
        // Still draining: each Stop waits up to 10 s for the drain to end.
        while (control.state === 'pausing') control = await pauseWorkflow(id, control.revision);
        await resumeWorkflow(id, control.revision);
      }
      pillToast(`Handed back to ${employee.name}`);
    } catch (error) {
      if (error instanceof Error && error.message === 'workflow_control_superseded') return;
      pillToast(controlErrorMessage(error), { tone: 'error' });
    } finally {
      setBusy(id, false);
    }
  };

  return { active: Boolean(mine), screen: mine?.screen ?? null, busy, takeOver, handBack };
}
