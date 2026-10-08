/**
 * Controlled Stop suspends the owning generation through the existing
 * workflow-control mutation flow and keeps the chat run alive. Legacy Stop
 * ends the answer using its existing chat lifecycle. A run that ended
 * meanwhile (`not_stoppable`) is not a failure.
 */

import { useMutation } from '@tanstack/react-query';
import { useWebSocketActions, type WorkflowControlStatus } from '@/contexts/WebSocketContext';
import { isLiveRun, type RunSnapshot } from '@/lib/agui/reduceRun';
import { useChatRunStore } from '@/stores/chatRunStore';

type StopReply = { success?: boolean; error?: string; state?: string; resumable?: boolean; control?: WorkflowControlStatus };

export function useStopChatRun(sessionId: string, onFailed?: () => void, control?: WorkflowControlStatus) {
  const { sendRequest, getWorkflowControlStatus, stopChatRun } = useWebSocketActions();
  return useMutation<StopReply, Error, RunSnapshot>({
    mutationFn: async (run) => {
      const currentControl = control ?? (run.workflowId ? await getWorkflowControlStatus(run.workflowId) : undefined);
      if (currentControl?.execution_control_version === 1 && run.workflowId) {
        await stopChatRun(run.workflowId, currentControl.revision, run.runId);
        return { resumable: true };
      }
      let reply: StopReply | undefined;
      try {
        reply = await sendRequest<StopReply>('stop_chat_run', { run_id: run.runId });
      } catch {
        throw new Error('transport');
      }
      if (reply?.success === false) throw new Error(reply.error || 'stop_failed');
      if (reply?.resumable && run.workflowId) {
        await getWorkflowControlStatus(run.workflowId);
      }
      return reply ?? { state: 'stopping' };
    },
    onSuccess: (reply, run) => {
      if (reply.resumable) return;
      const state = reply.state;
      const store = useChatRunStore.getState();
      const current = store.sessions[sessionId]?.runs[run.runId] ?? run;
      if (!isLiveRun(current)) return;
      if (state === 'stopped') {
        store.upsertRun({ ...current, state: 'stopped', outcome: { type: 'stopped' }, result: { noReply: true } });
      } else if (state === 'stopping' && current.state !== 'stopping') {
        store.upsertRun({ ...current, state: 'stopping' });
      }
    },
    onError: (error) => {
      if (error.message !== 'not_stoppable') onFailed?.();
    },
  });
}
