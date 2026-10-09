/**
 * Take over (design handoff "Workspace panel"): the owner takes the screen
 * the Workspace shows (Browser or Mobile) and the employee stops while they
 * use it; Hand back gives the screen back and, when Take over stopped them,
 * resumes them. Take over claims the screen first, so the employee never
 * acts on it again once the owner has it, then stops them. What happened is
 * kept in homeStore (`takeover`), so Hand back knows whether to resume them
 * even after the dock was closed or the tab changed.
 */

import { useState } from 'react';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import type { SurfaceControl } from '@/components/workspace/surface';
import { presentEmployee } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { controlErrorMessage } from '../employee/useEmployeeControl';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

export interface TakeOver {
  /** The owner has this employee's screen. */
  active: boolean;
  busy: boolean;
  takeOver: () => Promise<void>;
  handBack: () => Promise<void>;
}

/** `surface`: the screen the Workspace shows now, or null on the Canvas tab. */
export function useTakeOver(employee: EmployeeSummary, surface: () => SurfaceControl | null): TakeOver {
  const { pauseWorkflow, resumeWorkflow } = useWebSocketActions();
  const takeover = useHomeStore((s) => s.takeover);
  const setTakeover = useHomeStore((s) => s.setTakeover);
  const [busy, setBusy] = useState(false);
  const mine = takeover?.workflowId === employee.workflow_id ? takeover : null;

  const takeOver = async () => {
    const screen = surface();
    if (!screen || busy) return;
    setBusy(true);
    try {
      if (!(await screen.claim())) {
        pillToast('Couldn’t take the screen. Try again.', { tone: 'error' });
        return;
      }
      // Only a running employee is stopped, and only then resumed later.
      const running = presentEmployee(employee).primary.kind === 'pause';
      if (running) await pauseWorkflow(employee.workflow_id, employee.control.revision);
      setTakeover({ workflowId: employee.workflow_id, stopped: running });
      pillToast(`You have control — ${employee.name} will wait`);
    } catch (error) {
      screen.release();
      pillToast(controlErrorMessage(error), { tone: 'error' });
    } finally {
      setBusy(false);
    }
  };

  const handBack = async () => {
    if (!mine || busy) return;
    setBusy(true);
    try {
      surface()?.release();
      if (mine.stopped) await resumeWorkflow(employee.workflow_id, employee.control.revision);
      pillToast(`Handed back to ${employee.name}`);
    } catch (error) {
      pillToast(controlErrorMessage(error), { tone: 'error' });
    } finally {
      setTakeover(null);
      setBusy(false);
    }
  };

  return { active: Boolean(mine), busy, takeOver, handBack };
}
