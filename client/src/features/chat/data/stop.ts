/**
 * Stopping the run that is answering (`stop_chat_run`). One nothing picked
 * up yet ends at once; a working one stops at its next step and keeps what
 * it wrote. The run's own events say what happens next; the server's answer
 * is applied at once too, so the Stop button settles without waiting for
 * them. A run that ended meanwhile (`not_stoppable`) is not a failure.
 */

import { useMutation } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { isLiveRun, type RunSnapshot } from '@/lib/agui/reduceRun';
import { useChatRunStore } from '@/stores/chatRunStore';

type StopReply = { success?: boolean; error?: string; state?: string };

export function useStopChatRun(sessionId: string, onFailed?: () => void) {
  const { sendRequest } = useWebSocketActions();
  return useMutation<string, Error, RunSnapshot>({
    mutationFn: async (run) => {
      let reply: StopReply | undefined;
      try {
        reply = await sendRequest<StopReply>('stop_chat_run', { run_id: run.runId });
      } catch {
        throw new Error('transport');
      }
      if (reply?.success === false) throw new Error(reply.error || 'stop_failed');
      return typeof reply?.state === 'string' ? reply.state : 'stopping';
    },
    onSuccess: (state, run) => {
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
