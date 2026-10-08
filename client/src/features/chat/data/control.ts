import { useMutation } from '@tanstack/react-query';
import { mergeWorkflowControlStatus, useWebSocketActions, type WorkflowControlStatus } from '@/contexts/WebSocketContext';
import type { RunSnapshot } from '@/lib/agui/reduceRun';
import { useWorkflowControl } from '@/stores/workflowControlStore';

/** Prefer live generation broadcasts while retaining the reload snapshot. */
export function useChatWorkflowControl(workflowId: string | null, run: RunSnapshot | null): WorkflowControlStatus | undefined {
  const latest = useWorkflowControl(workflowId);
  const owned = run?.workflowControl;
  if (owned && latest?.root_execution_id !== owned.root_execution_id) return owned;
  return owned && latest ? mergeWorkflowControlStatus(owned, latest) : owned ?? latest;
}

export function useResumeChatGeneration(control: WorkflowControlStatus | undefined, onFailed: () => void) {
  const { getWorkflowControlStatus, resumeWorkflow } = useWebSocketActions();
  return useMutation({
    mutationFn: async () => {
      if (!control?.workflow_id) throw new Error('missing_control');
      const current = await getWorkflowControlStatus(control.workflow_id);
      if (current.root_execution_id !== control.root_execution_id) throw new Error('control_generation_conflict');
      return resumeWorkflow(control.workflow_id, current.revision);
    },
    onError: onFailed,
  });
}
