/**
 * The shell's two pieces of current-workflow bookkeeping (moved from
 * Dashboard so they hold on either screen):
 *
 * - Push `currentWorkflow.id` from the app store into the node-status store,
 *   which files incoming broadcasts per workflow. One source-to-store sync;
 *   a second mirror once landed broadcasts in the wrong bucket during
 *   workflow switches.
 * - Resync that workflow's execution and control status whenever the socket
 *   becomes ready or the workflow changes. Broadcasts only fire on
 *   transitions, so a reconnect or a switch would otherwise leave Start /
 *   Stop stale.
 */

import { useEffect } from 'react';
import { useAppStore } from '../store/useAppStore';
import { useNodeStatusStore } from '../stores/nodeStatusStore';
import { useWebSocketActions } from '../contexts/WebSocketContext';

export function useCurrentWorkflowSync(): void {
  const workflowId = useAppStore((s) => s.currentWorkflow?.id);
  const { isReady, getWorkflowStatus, getWorkflowControlStatus } = useWebSocketActions();

  useEffect(() => {
    useNodeStatusStore.getState().setCurrentWorkflowId(workflowId);
  }, [workflowId]);

  useEffect(() => {
    if (!isReady || !workflowId) return;
    let cancelled = false;
    (async () => {
      try {
        const [{ executing }] = await Promise.all([
          getWorkflowStatus(workflowId),
          getWorkflowControlStatus(workflowId),
        ]);
        if (cancelled) return;
        useAppStore.getState().setWorkflowExecuting(workflowId, executing);
      } catch {
        // Opportunistic: the next broadcast resyncs.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isReady, workflowId, getWorkflowStatus, getWorkflowControlStatus]);
}
