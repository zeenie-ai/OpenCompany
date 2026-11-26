import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { refreshEmployee } from '../data/employees';
import type { EmployeeSummary } from '../data/schemas';

/** The delivery choices are neutral outlines: colour marks a state or a
 *  role, not a decision. */
const DECIDE_BUTTON = 'rounded-md border-border-default px-3.5 font-semibold text-fg-default';

export function JobProgress({ employee, showStatus = true }: { employee: EmployeeSummary; showStatus?: boolean }) {
  const { sendRequest } = useWebSocketActions();
  const cache = useQueryClient();
  const attempt = useRef<{ jobId: string; decision: string; key: string } | null>(null);
  const decisionAttempt = useRef<{ decisionId: string; answer: string; key: string } | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
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
  const answerDecision = useMutation({ mutationFn: async ({ decisionId, value }: { decisionId: string; value: string }) => {
    if (!progress) return;
    const trimmed = value.trim();
    if (decisionAttempt.current?.decisionId !== decisionId || decisionAttempt.current.answer !== trimmed) {
      decisionAttempt.current = { decisionId, answer: trimmed, key: crypto.randomUUID() };
    }
    const response = await sendRequest<{ success: boolean; message?: string; error?: string }>('answer_employee_decision', {
      workflow_id: employee.workflow_id, job_id: progress.request_id, decision_id: decisionId,
      answer: trimmed, idempotency_key: decisionAttempt.current.key,
    });
    if (!response?.success) throw new Error(response?.message || 'Your answer could not be saved. Try again.');
    return response;
  }, onSuccess: () => {
    decisionAttempt.current = null;
    refreshEmployee(cache, employee.workflow_id);
  } });
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
  const ownerDecisions = progress.owner_decisions ?? [];
  return <div className="flex flex-col gap-3 rounded-card border border-border-default bg-bg-panel p-4">
    {(showStatus || resolve.isSuccess) && <p role="status" className="m-0 text-sm text-fg-muted">{resolve.isSuccess ? resolve.data?.message || 'Your delivery decision is saved.' : progress.message}</p>}
    {needsReview && !resolve.isSuccess && <div className="flex flex-wrap gap-2">
      <Button variant="quiet" disabled={resolve.isPending} onClick={() => resolve.mutate('arrived')} className={DECIDE_BUTTON}>It arrived</Button>
      <Button variant="quiet" disabled={resolve.isPending} onClick={() => resolve.mutate('retry')} className={DECIDE_BUTTON}>It did not arrive; retry</Button>
    </div>}
    {reviewBlocked && !resolve.isSuccess && <div>
      <Button variant="quiet" disabled={resolve.isPending} onClick={() => resolve.mutate('review')} className={DECIDE_BUTTON}>Retry review</Button>
    </div>}
    {resolve.isError && <p role="alert" className="m-0 text-sm">{resolve.error.message}</p>}
    {ownerDecisions.map((decision) => {
      const answered = answerDecision.isSuccess && answerDecision.variables?.decisionId === decision.decision_id;
      const answer = answers[decision.decision_id] ?? '';
      return <section key={decision.decision_id} className="flex flex-col gap-2 border-t border-border-default pt-3">
        {decision.requested_by && <p className="m-0 text-sm text-fg-muted">{decision.requested_by} needs your decision:</p>}
        <p className="m-0 whitespace-pre-wrap text-sm text-fg-default">{decision.question}</p>
        {decision.context && <p className="m-0 whitespace-pre-wrap text-sm text-fg-muted">{decision.context}</p>}
        <label htmlFor={`owner-decision-${decision.decision_id}`} className="text-xs font-medium text-fg-muted">Your answer</label>
        <Textarea
          id={`owner-decision-${decision.decision_id}`}
          value={answer}
          maxLength={4000}
          onChange={(event) => setAnswers((previous) => ({ ...previous, [decision.decision_id]: event.target.value }))}
          placeholder="Write the decision that lets the team continue"
          disabled={answerDecision.isPending}
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="quiet"
            disabled={answerDecision.isPending || !answer.trim()}
            onClick={() => answerDecision.mutate({ decisionId: decision.decision_id, value: answer })}
            className={DECIDE_BUTTON}
          >
            {answerDecision.isPending ? 'Sending…' : 'Send answer'}
          </Button>
          {answerDecision.isError && <p role="alert" className="m-0 text-sm">{answerDecision.error.message}</p>}
          {answered && <p role="status" className="m-0 text-sm text-fg-muted">Your answer is saved. The team can continue.</p>}
        </div>
      </section>;
    })}
  </div>;
}
