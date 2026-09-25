/**
 * Hire: turn the setup screen into an employee.
 *
 * One hire at a time. The idempotency key stays the same while the payload
 * does, so a retry after a lost response finds the employee the first
 * attempt created instead of hiring twice. On success the draft collapses,
 * the new employee lands at the top of the team with a glow, the logo
 * pulses and a toast says they joined; one that could not start yet (an
 * app or the AI model is not connected) opens on its card, which names
 * what to connect.
 */

import { useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { upsertEmployee } from '../data/employees';
import { parseEmployee } from '../data/schemas';
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
  not_allowed: 'This setup needs something Normal mode cannot build yet. Open the editor to build it by hand.',
  build_failed: "Couldn't put this employee together. Try changing the setup.",
};

/** A failure with words for the owner (not a transport error). */
class HireError extends Error {}

export function hireErrorMessage(code: unknown): string {
  return (typeof code === 'string' && HIRE_ERRORS[code]) || "Couldn't hire them. Try again.";
}

interface HireResponse {
  success?: boolean;
  error?: string;
  employee?: unknown;
  started?: boolean;
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
        if (response?.success === false) throw new HireError(hireErrorMessage(response.error));
        const employee = parseEmployee(response?.employee);
        if (!employee) throw new HireError('They were hired, but could not be shown here. Refresh to see them.');
        // The owner may have started another draft while this one saved.
        const unchanged = useDraftStore.getState().version === draft.version;
        if (unchanged) await collapse();
        endHire();
        if (unchanged) clearHiredDraft();
        upsertEmployee(queryClient, employee, true);
        const home = useHomeStore.getState();
        home.glowRow(employee.workflow_id);
        home.pulseLogo();
        spikeOrb(SPIKE.hire);
        pillToast(`${employee.name} joined your team`);
        if (response?.started === false) home.showEmployee(employee.workflow_id);
      } catch (error) {
        endHire();
        // A dropped connection may have hired them already; the same key on
        // the next press finds that employee instead of hiring twice.
        const message = error instanceof HireError ? error.message : 'The connection dropped before the hire finished. Press Hire again.';
        pillToast(message, { tone: 'error' });
      }
    },
    [collapse, queryClient, sendRequest],
  );
}
