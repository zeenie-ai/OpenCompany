import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { Button } from '@/components/ui/button';
import { EMPLOYEES_QUERY_KEY } from '../data/employees';

type Review = { review_id: string; explanation: string; team: { responsibility: string }[] };

/** Review is ordinary language; existing employees are never converted silently. */
export function GiveTeam({ workflowId, available }: { workflowId: string; available: boolean }) {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  if (!available) return null;
  const run = async (apply: boolean) => {
    setBusy(true);
    setMessage('');
    try {
      const response = await sendRequest(apply ? 'give_employee_team' : 'plan_employee_team', apply ? { review_id: review?.review_id } : { workflow_id: workflowId });
      if (!response?.success) {
        setMessage(response?.message || (response?.error === 'needs_dev_review' ? 'Their custom setup needs a review in Dev mode first.' : 'Their team could not be set up yet. Your employee is still here. Try again.'));
      } else if (apply) {
        setReview(null);
        setMessage(response.activation_state === 'waiting' ? 'Their team will be ready after they finish their current work.' : 'Their team has been saved.');
        void queryClient.invalidateQueries({ queryKey: EMPLOYEES_QUERY_KEY });
      } else if (typeof response.review_id === 'string') {
        setReview({ review_id: response.review_id, explanation: String(response.explanation || ''), team: Array.isArray(response.team) ? response.team : [] });
      }
    } catch {
      setMessage('The connection dropped. Try again; your employee is still here.');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rounded-card border border-border-default p-3 text-sm text-fg-muted" aria-busy={busy}>
      {review ? <>
        <p className="m-0 text-fg-default">{review.explanation}</p>
        <ul className="my-2 list-disc pl-5">{review.team.map((member) => <li key={member.responsibility}>{member.responsibility}</li>)}</ul>
        <div className="flex gap-2">
          <Button disabled={busy} onClick={() => void run(true)}>Give them a team</Button>
          <Button variant="quiet" disabled={busy} onClick={() => setReview(null)}>Not now</Button>
        </div>
      </> : <Button variant="quiet" disabled={busy} onClick={() => void run(false)}>Give them a team</Button>}
      {message && <p role="status" className="mt-2 mb-0">{message}</p>}
    </div>
  );
}
