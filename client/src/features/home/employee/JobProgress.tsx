import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { Button } from '@/components/ui/button';
import { refreshEmployee } from '../data/employees';
import type { EmployeeSummary } from '../data/schemas';

/** The delivery choices are neutral outlines: colour marks a state or a
 *  role, not a decision. */
const DECIDE_BUTTON = 'rounded-md border-border-default px-3.5 font-semibold text-fg-default';

export function JobProgress({ employee }: { employee: EmployeeSummary }) {
  const { sendRequest } = useWebSocketActions();
  const cache = useQueryClient();
  const attempt = useRef<{ jobId: string; decision: string; key: string } | null>(null);
  const progress = employee.job_progress;
  const resolve = useMutation({ mutationFn: async (decision: 'arrived' | 'retry' | 'review') => {
    if (!progress) return;
    if (attempt.current?.jobId !== progress.request_id || attempt.current.decision !== decision) {
      attempt.current = { jobId: progress.request_id, decision, key: crypto.randomUUID() };
    }
    const response = await sendRequest<{ success: boolean; message?: string; error?: string }>(decision === 'review' ? 'retry_employee_review' : 'resolve_employee_delivery', {
      workflow_id: employee.workflow_id, job_id: progress.request_id, ...(decision === 'review' ? {} : { decision }),
      idempotency_key: attempt.current.key,
    });
    if (!response?.success) throw new Error(response?.message || (decision === 'review' ? 'The review could not be resumed. Try again.' : 'Your delivery decision could not be saved. Try again.'));
    return response;
  }, onSuccess: () => refreshEmployee(cache, employee.workflow_id) });
  const progressKey = `${progress?.request_id}:${progress?.state}:${progress?.revision ?? ''}`;
  const previousProgress = useRef(progressKey);
  // TanStack's MutationObserver binds reset once, so it is stable and the
  // effect still runs only when the job's progress changes.
  const resetResolve = resolve.reset;
  useEffect(() => {
    if (previousProgress.current !== progressKey) {
      previousProgress.current = progressKey;
      attempt.current = null;
      resetResolve();
    }
  }, [progressKey, resetResolve]);
  if (!progress) return null;
  const needsReview = progress.state === 'delivery_needs_review';
  const reviewBlocked = progress.state === 'review_blocked';
  return <div className="flex flex-col gap-3 rounded-card border border-border-default bg-bg-panel p-4">
    <p role="status" className="m-0 text-sm text-fg-muted">{resolve.isSuccess ? resolve.data?.message || 'Your delivery decision is saved.' : progress.message}</p>
    {needsReview && !resolve.isSuccess && <div className="flex flex-wrap gap-2">
      <Button variant="quiet" disabled={resolve.isPending} onClick={() => resolve.mutate('arrived')} className={DECIDE_BUTTON}>It arrived</Button>
      <Button variant="quiet" disabled={resolve.isPending} onClick={() => resolve.mutate('retry')} className={DECIDE_BUTTON}>It did not arrive; retry</Button>
    </div>}
    {reviewBlocked && !resolve.isSuccess && <div>
      <Button variant="quiet" disabled={resolve.isPending} onClick={() => resolve.mutate('review')} className={DECIDE_BUTTON}>Retry review</Button>
    </div>}
    {resolve.isError && <p role="alert" className="m-0 text-sm">{resolve.error.message}</p>}
  </div>;
}
