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
 * Hand back gives the screen back first, so that call can go on, then waits
 * for Take over's Stop and resumes them from the newest control it has, the
 * summary's or the Stop's answer: the server resumes only from paused, at the
 * current revision. A Stop answers within 10 s even while the drain goes on,
 * so while the control is still pausing Hand back sends the Stop again, which
 * waits for the drain and answers with the paused revision.
 *
 * What happened is kept in homeStore (`takeovers`, by employee), with the
 * taken screen's way of giving it back, so Hand back gives back the screen
 * that was taken and knows whether to resume them, even after the dock was
 * closed or moved to another tab or employee.
 */

import { useRef, useState } from 'react';
import { mergeWorkflowControlStatus, useWebSocketActions, type WorkflowControlStatus } from '@/contexts/WebSocketContext';
import type { SurfaceControl } from '@/components/workspace/surface';
import { presentEmployee } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { controlErrorMessage } from '../employee/useEmployeeControl';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

export interface TakeOver {
  /** The owner has this employee's screen. */
  active: boolean;
  /** Taking the screen or handing it back, not while Take over's Stop drains. */
  busy: boolean;
  takeOver: () => Promise<void>;
  handBack: () => Promise<void>;
}

/** `surface`: the screen the Workspace shows now, or null on the Canvas tab. */
export function useTakeOver(employee: EmployeeSummary, surface: () => SurfaceControl | null): TakeOver {
  const { pauseWorkflow, resumeWorkflow } = useWebSocketActions();
  const mine = useHomeStore((s) => s.takeovers[employee.workflow_id]);
  const setTakeover = useHomeStore((s) => s.setTakeover);
  const [busy, setBusy] = useState(false);
  // Take over's Stop, by employee, until Hand back reads its answer.
  const stops = useRef(new Map<string, Promise<WorkflowControlStatus>>());

  const takeOver = async () => {
    const screen = surface();
    if (!screen || busy) return;
    const id = employee.workflow_id;
    setBusy(true);
    const giveBack = await screen.claim().finally(() => setBusy(false));
    if (!giveBack) {
      pillToast('Couldn’t take the screen. Try again.', { tone: 'error' });
      return;
    }
    // Only a running employee is stopped, and only then resumed later.
    const running = presentEmployee(employee).primary.kind === 'pause';
    const taken = { stopped: running, giveBack };
    setTakeover(id, taken);
    pillToast(`You have control — ${employee.name} will wait`);
    if (!running) return;
    const stop = pauseWorkflow(id, employee.control.revision);
    stops.current.set(id, stop);
    try {
      await stop;
    } catch (error) {
      // Not stopped: give the screen back, unless Hand back already has.
      if (useHomeStore.getState().takeovers[id] !== taken) return;
      giveBack();
      setTakeover(id, null);
      if (error instanceof Error && error.message === 'workflow_control_superseded') return;
      pillToast(controlErrorMessage(error), { tone: 'error' });
    }
  };

  const handBack = async () => {
    if (!mine || busy) return;
    const id = employee.workflow_id;
    const stop = stops.current.get(id);
    stops.current.delete(id);
    setBusy(true);
    // The screen first, so the call the employee waits in can go on.
    mine.giveBack();
    setTakeover(id, null);
    try {
      if (mine.stopped) {
        // A Stop that failed leaves the summary to say where they are.
        const answer = await stop?.catch(() => undefined);
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
      setBusy(false);
    }
  };

  return { active: Boolean(mine), busy, takeOver, handBack };
}
