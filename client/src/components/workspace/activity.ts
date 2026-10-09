/**
 * Whether a Workspace surface is busy, for the dot on its tab (design
 * handoff "Workspace panel": an inactive tab with activity shows a pink
 * dot). A surface is busy while one of its nodes runs in this workflow, read
 * from the same node statuses the editor canvas shows; the host adds what
 * else counts (Home: the employee waiting for the owner in the browser).
 */

import { useCallback } from 'react';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';

/** True while one of `nodeIds` is executing in `workflowId`. Pass a stable
 *  (memoized) list. */
export function useSurfaceRunning(workflowId: string | null | undefined, nodeIds: readonly string[]): boolean {
  return useNodeStatusStore(
    useCallback(
      (state) => {
        if (!workflowId) return false;
        const statuses = state.allStatuses[workflowId];
        return nodeIds.some((id) => statuses?.[id]?.status === 'executing');
      },
      [workflowId, nodeIds],
    ),
  );
}
