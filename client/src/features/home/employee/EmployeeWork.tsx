import { useEffect, useState } from 'react';
import { Check, ChevronDown, Circle, Pause, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { RingSpinner } from '@/components/ui/ring-spinner';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { useLaneRun } from '@/features/chat';
import { useEmployeeDetailQuery } from '../data/employees';
import { cn } from '@/lib/utils';
import type { EmployeeSummary, WorkProgress } from '../data/schemas';
import { JobProgress } from './JobProgress';

const ACTIVE = new Set(['queued', 'running', 'working', 'waiting', 'reviewing', 'stopping', 'delivering']);
const LABELS: Record<string, string> = {
  queued: 'Queued', waiting: 'Waiting', running: 'In progress', reviewing: 'Checking the result',
  done: 'Finished', failed: 'Needs attention', cancelled: 'Stopped', stopping: 'Stopping',
};
/** Refresh progress is a neutral outline: colour marks a state or a role,
 *  not a refresh. */
const REFRESH_BUTTON = 'border-border-default text-fg-default';

function age(timestamp: string | null, now: number): number | null {
  if (!timestamp) return null;
  const parsed = Date.parse(timestamp);
  return Number.isFinite(parsed) ? Math.max(0, Math.floor((now - parsed) / 1000)) : null;
}

function duration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** The spinner in the pill's neutral ink, as the chat's live work pill. */
const SPINNER = 'size-3 border-border-default border-t-fg-default';
const WORKING = ['running', 'working', 'reviewing', 'stopping', 'delivering'];

/** The mark before a state's text: a spinner while it works, else an icon. */
function StateMark({ state }: { state: string }) {
  if (WORKING.includes(state)) return <RingSpinner className={SPINNER} />;
  const Mark = state === 'done' ? Check : state === 'failed' ? X : state === 'paused' || state === 'cancelled' ? Pause : Circle;
  return <Mark aria-hidden className="size-3 shrink-0 text-fg-muted" strokeWidth={2.2} />;
}

/**
 * The employee's own work as one pill above the message box, like the chat's
 * live work pill (design handoff chat V2 section 3, "Live work"): the state's
 * mark, what is going on, the steps finished of those known and how long it
 * has run ("1/3 · 3m 2s"). It opens to the detail: when it last moved and each
 * step with who has it, in a list that scrolls. Never a card that crowds the
 * conversation. A lost connection or a failed refresh says so under the pill.
 */
function WorkPanel({ progress, connected, failed, refresh }: {
  progress: WorkProgress; connected: boolean; failed: boolean; refresh: () => void;
}) {
  const [now, setNow] = useState(Date.now);
  const [open, setOpen] = useState(false);
  const active = ACTIVE.has(progress.state);
  useEffect(() => {
    if (!active || !connected) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, connected]);
  const elapsed = age(progress.started_at, now);
  const updated = age(progress.updated_at, now);
  const finished = progress.steps.filter((step) => step.status === 'done').length;
  const slow = connected && !failed && active && !['waiting', 'queued', 'stopping'].includes(progress.state) && updated !== null && updated >= 60;
  const meta = [
    progress.steps.length > 0 ? `${finished}/${progress.steps.length}` : null,
    elapsed !== null ? (active ? duration(elapsed) : `${duration(elapsed)} ago`) : null,
  ].filter(Boolean).join(' · ');
  return (
    <div className="flex max-w-full flex-col items-center gap-1.5 self-center">
      <Collapsible asChild open={open} onOpenChange={setOpen}>
        <section
          aria-label="Employee work progress"
          className="flex max-w-full flex-col overflow-hidden rounded-card border border-border-default bg-bg-panel"
        >
          <CollapsibleTrigger className="flex h-8.5 max-w-full items-center gap-2 pr-2.5 pl-3 text-xs font-medium text-fg-default hover:bg-bg-hover focus-visible:-outline-offset-2">
            <StateMark state={progress.state} />
            <span role="status" className="min-w-0 truncate">{progress.message}</span>
            {/* Out of the button's name, which would otherwise change every second. */}
            {meta && <span aria-hidden className="shrink-0 text-fg-muted tabular-nums">{meta}</span>}
            <ChevronDown
              aria-hidden
              strokeWidth={2.2}
              className={cn('size-3 shrink-0 text-fg-muted transition-transform duration-(--dur-default) motion-reduce:transition-none', open && 'rotate-180')}
            />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="flex max-h-40 flex-col gap-1 overflow-y-auto border-t border-border-default px-3 pt-2 pb-2.5 text-xs text-fg-muted">
              <p className="m-0 break-words text-fg-default">{progress.message}</p>
              {elapsed !== null && <p className="m-0">{active ? 'Elapsed' : 'Started'} {duration(elapsed)}{!active && ' ago'}</p>}
              {connected && !failed && updated !== null && <p className="m-0">Last activity {updated < 5 ? 'just now' : `${duration(updated)} ago`}</p>}
              {slow && <p className="m-0">This is taking longer. The last reported step is shown below; no new activity has been reported yet.</p>}
              {progress.steps.length > 0 && <>
                <p className="m-0">{finished} of {progress.steps.length}{progress.truncated ? ' shown' : ''} steps finished</p>
                <ol aria-label="Steps" className="m-0 flex list-none flex-col gap-0.5 p-0">
                  {progress.steps.map((step) => (
                    <li key={step.id} className={cn('flex min-h-6 items-center gap-2', WORKING.includes(step.status) && 'text-fg-default')}>
                      <StateMark state={step.status} />
                      <span className="min-w-0 truncate">{step.label}</span>
                      <span className="shrink-0">{step.member ? `${step.member} · ` : ''}{LABELS[step.status] ?? 'Waiting'}</span>
                    </li>
                  ))}
                </ol>
                {progress.truncated && <p className="m-0">Showing the most recent activity.</p>}
              </>}
            </div>
          </CollapsibleContent>
        </section>
      </Collapsible>
      {!connected && <p className="m-0 text-xs text-fg-muted">Last known activity. Connection lost; updates will resume when you reconnect.</p>}
      {connected && failed && <div className="flex flex-wrap items-center gap-2">
        <p className="m-0 text-xs text-fg-muted">Couldn’t refresh progress. Showing the last update.</p>
        <Button variant="quiet" size="sm" onClick={refresh} className={REFRESH_BUTTON}>Refresh progress</Button>
      </div>}
    </div>
  );
}

/** Detailed work stays separate from list-owned controls and composer state:
 *  the employee's own work (team tasks, scheduled work, what waits for the
 *  owner), while no reply is under way. */
export function EmployeeWork({ employee }: { employee: EmployeeSummary }) {
  const { isReady } = useWebSocketActions();
  const detail = useEmployeeDetailQuery(employee.workflow_id, { live: true });
  const lane = useLaneRun(employee.workflow_id);
  const saved = detail.data?.work_progress;
  // A reply under way shows in the chat, above the box (features/chat
  // LiveWork); the employee's own snapshot describes it too.
  const progress = lane ? null : saved ?? null;
  const job = detail.data ? detail.data.job_progress : employee.job_progress;
  const delivery = job && ['delivery_needs_review', 'waiting_for_approval', 'failed', 'cancelled'].includes(job.state);
  return <>
    {progress && <WorkPanel progress={progress} connected={isReady} failed={detail.isError || progress.state === 'unavailable'} refresh={() => void detail.refetch()} />}
    {!saved && !lane && detail.isError && <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
      <span>Work progress is unavailable.</span>
      <Button variant="quiet" size="sm" onClick={() => void detail.refetch()} className={REFRESH_BUTTON}>Refresh progress</Button>
    </div>}
    {(delivery || (!saved && !lane && job)) && <JobProgress key={`${job.request_id}:${job.state}`} employee={{ ...employee, job_progress: job }} />}
  </>;
}
