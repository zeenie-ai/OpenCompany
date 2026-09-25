/**
 * Start a new hire from a job written for the owner (a template chip, a
 * starter bundle in Settings > Plugins): the text goes to the setup model
 * as a fresh job, exactly as the composer would send it. `busy` while a
 * setup is being written or a hire is in flight, when a start would be
 * refused.
 */

import { useCallback } from 'react';
import { useDraftActions, useDraftStore } from './draftStore';

export function useStartHire() {
  const busy = useDraftStore((s) => s.status === 'working' || s.hiring);
  const actions = useDraftActions();
  const start = useCallback((job: string) => actions.submit(job, { refine: false }), [actions]);
  return { busy, start };
}
