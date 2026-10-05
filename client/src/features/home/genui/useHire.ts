/**
 * Hire: turn the setup screen into an employee. (A starter hired in one
 * click goes through useStarterHire, which lands the same way.)
 *
 * One hire at a time. The idempotency key stays the same while the payload
 * does, so a retry after a lost response finds the employee the first
 * attempt created instead of hiring twice. On success the draft collapses,
 * the new employee lands at the top of the team with a glow, the logo
 * pulses, a toast says they joined, and Home opens their page. What the
 * hire could not set up as asked shows there once (the hire notice), and
 * when no AI model is set up the guided connect dialog opens, since they
 * cannot start without one.
 */

import { useCallback } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { refreshEmployee } from '../data/employees';
import { parseEmployee, type EmployeeSummary } from '../data/schemas';
import { SPIKE, spikeOrb } from '../orb/orb';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';
import { beginHire, clearHiredDraft, endHire, useDraftStore } from './draftStore';
import { buildHirePayload, fitsHireLimit } from './hirePayload';

/** Building and saving the workflow is quick; starting it runs in the
 *  background on the server, so this never waits for a start. */
export const HIRE_TIMEOUT_MS = 60_000;

const HIRE_ERRORS: Record<string, string> = {
  invalid_request: 'Something in this setup could not be used. Try changing it and hire again.',
  too_large: 'This setup is too long to hire. Try a shorter job description.',
  conflict: 'This hire changed while it was being saved. Press Hire again.',
  busy: 'They are still being set up. Give it a moment, then press Hire again.',
  not_allowed: "This setup needs something that can't be set up from here yet. Try changing it.",
  build_failed: "Couldn't put this employee together. Try changing the setup.",
  save_failed: "Couldn't save them. Press Hire again.",
  teams_disabled: 'Team hiring is not available on this installation yet. Ask your administrator to enable it.',
};

/** A failure with words for the owner (not a transport error). */
export class HireError extends Error {}

export function hireErrorMessage(code: unknown): string {
  return (typeof code === 'string' && HIRE_ERRORS[code]) || "Couldn't hire them. Try again.";
}

export interface HireResponse {
  success?: boolean;
  error?: string;
  employee?: unknown;
  started?: boolean;
  needs_ai?: boolean;
  warnings?: unknown;
  activation_state?: string;
  readiness_issue?: string | null;
}

/** The employee a hire made; a HireError with words for the owner otherwise. */
export function hiredEmployee(response: HireResponse | undefined): EmployeeSummary {
  if (response?.success === false) throw new HireError(hireErrorMessage(response.error));
  const employee = parseEmployee(response?.employee);
  if (!employee) throw new HireError('They were hired, but could not be shown here. Refresh to see them.');
  return employee;
}

/** What to tell the owner when a hire did not go through. */
export function hireFailureMessage(error: unknown): string {
  // A dropped connection may have hired them already; the same key on the
  // next press finds that employee instead of hiring twice.
  return error instanceof HireError ? error.message : 'The connection dropped before the hire finished. Press Hire again.';
}

/** The new employee joins the team: a glow, the logo, a toast, and their
 *  page, with what the hire said. */
export function welcomeHire(queryClient: QueryClient, employee: EmployeeSummary, response: HireResponse): void {
  refreshEmployee(queryClient, employee.workflow_id);
  const home = useHomeStore.getState();
  home.glowRow(employee.workflow_id);
  home.pulseLogo();
  spikeOrb(SPIKE.hire);
  pillToast(`${employee.name} joined your team`);
  home.showEmployee(employee.workflow_id);
  const warnings = Array.isArray(response.warnings)
    ? response.warnings.filter((warning): warning is string => typeof warning === 'string' && warning.trim() !== '')
    : [];
  if (response.readiness_issue === 'team_temporal_required' || response.readiness_issue === 'team_agent_workflow_required') {
    warnings.push('They are hired and their team is saved. Team work needs to be set up on this installation before they can start. Ask your administrator to finish setup, then press Start.');
  } else if (response.readiness_issue === 'team_runtime_not_ready') {
    warnings.push('They are hired and their team is saved. The service is getting ready. Try Start again in a moment.');
  } else if (response.activation_state === 'failed') {
    warnings.push('They are hired and their setup is saved, but they could not start yet. Press Start to try again.');
  }
  if (warnings.length > 0) home.setHireNotice({ workflowId: employee.workflow_id, name: employee.name, warnings });
  if (response.needs_ai) home.openConnectAI();
}

/** `collapse` plays the draft's exit before it is cleared. */
export function useHire(collapse: () => Promise<void>) {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();

  return useCallback(
    async (params: Record<string, unknown>) => {
      const draft = useDraftStore.getState();
      if (!draft.spec || draft.status === 'working') return;
      const base = buildHirePayload({
        spec: draft.spec,
        state: draft.uiState,
        params,
        job: draft.job,
        idempotencyKey: '',
        source: draft.source,
      });
      const key = beginHire(`${draft.version}:${JSON.stringify(base)}`);
      if (!key) return;
      const payload = { ...base, idempotency_key: key };
      if (!fitsHireLimit(payload)) {
        endHire();
        pillToast(hireErrorMessage('too_large'), { tone: 'error' });
        return;
      }
      try {
        const response = await sendRequest<HireResponse>('hire_employee', payload, HIRE_TIMEOUT_MS);
        const employee = hiredEmployee(response);
        // The owner may have started another draft while this one saved.
        const current = useDraftStore.getState();
        const unchanged = current.version === draft.version && current.spec === draft.spec;
        if (unchanged) await collapse();
        endHire(true);
        if (unchanged) clearHiredDraft();
        welcomeHire(queryClient, employee, response);
      } catch (error) {
        endHire();
        pillToast(hireFailureMessage(error), { tone: 'error' });
      }
    },
    [collapse, queryClient, sendRequest],
  );
}
