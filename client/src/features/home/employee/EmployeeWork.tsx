import { useEffect, useState } from 'react';
import { Check, Circle, LoaderCircle, X } from 'lucide-react';
import { ActionButton } from '@/components/ui/action-button';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { useLaneRun } from '@/features/chat';
import type { RunSnapshot } from '@/lib/agui/reduceRun';
import { useEmployeeDetailQuery } from '../data/employees';
import type { EmployeeSummary, WorkProgress } from '../data/schemas';
import { JobProgress } from './JobProgress';

const ACTIVE = new Set(['queued', 'running', 'working', 'waiting', 'reviewing', 'stopping', 'delivering']);
const LABELS: Record<string, string> = {
  queued: 'Queued', waiting: 'Waiting', running: 'In progress', reviewing: 'Checking the result',
  done: 'Finished', failed: 'Needs attention', cancelled: 'Stopped', stopping: 'Stopping',
};

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

function chatProgress(lane: RunSnapshot | null): WorkProgress | null {
  if (!lane) return null;
  const state = lane.state === 'queued' ? 'queued' : lane.state === 'stopping' ? 'stopping' : 'running';
  return {
    state,
    message: state === 'queued' ? 'Your message is saved and waiting to start.'
      : state === 'stopping' ? 'Stopping this reply.' : 'Preparing your reply.',
    started_at: lane.startedAt ?? lane.createdAt,
    updated_at: null,
    truncated: false,
    steps: lane.steps.map((step) => ({
      id: step.stepId, node_id: null, label: step.name, member: null,
      status: step.state === 'skipped' ? 'cancelled' : step.state,
      started_at: null, updated_at: null,
    })),
  };
}

function WorkPanel({ progress, connected, failed, refresh }: {
  progress: WorkProgress; connected: boolean; failed: boolean; refresh: () => void;
}) {
  const [now, setNow] = useState(Date.now);
  const active = ACTIVE.has(progress.state);
  useEffect(() => {
    if (!active || !connected) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, connected]);
  const elapsed = age(progress.started_at, now);
  const updated = age(progress.updated_at, now);
  const finished = progress.steps.filter((step) => step.status === 'done').length;
  // Keep the active step visible without expanding a long history.
  const current = progress.steps.filter((step) => ['running', 'reviewing', 'waiting', 'stopping'].includes(step.status));
  const recent = [...current, ...progress.steps.filter((step) => !current.includes(step)).slice(-3)].slice(0, 3);
  const renderStep = (step: WorkProgress['steps'][number]) => (
    <li key={step.id} className="flex items-start gap-2 py-1 text-sm">
      {['running', 'reviewing', 'stopping'].includes(step.status)
        ? <LoaderCircle aria-hidden className="mt-0.5 size-4 shrink-0 motion-safe:animate-spin text-fg-muted" />
        : step.status === 'done' ? <Check aria-hidden className="mt-0.5 size-4 shrink-0" />
          : step.status === 'failed' ? <X aria-hidden className="mt-0.5 size-4 shrink-0" />
            : <Circle aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-muted" />}
      <div className="min-w-0 flex-1 break-words">
        <span>{step.label}</span>
        <span className="block text-xs text-fg-muted">{step.member ? `${step.member} · ` : ''}{LABELS[step.status] ?? 'Waiting'}</span>
      </div>
    </li>
  );
  return (
    <section aria-label="Employee work progress" className="flex max-h-64 flex-col gap-2 overflow-y-auto rounded-card border border-border-default bg-bg-panel p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="m-0 text-sm font-medium">{connected ? 'Live work' : 'Last known activity'}</h2>
        {elapsed !== null && <span className="shrink-0 text-xs text-fg-muted">{active ? 'Elapsed' : 'Started'} {duration(elapsed)}{!active && ' ago'}</span>}
      </div>
      <p role="status" className="m-0 break-words text-sm">{progress.message}</p>
      {!connected && <p className="m-0 text-xs text-fg-muted">Connection lost. Updates will resume when you reconnect.</p>}
      {connected && failed && <div className="flex flex-wrap items-center gap-2">
        <p className="m-0 text-xs text-fg-muted">Couldn’t refresh progress. Showing the last update.</p>
        <ActionButton intent="config" onClick={refresh}>Refresh progress</ActionButton>
      </div>}
      {connected && !failed && updated !== null && <p className="m-0 text-xs text-fg-muted">Last activity {updated < 5 ? 'just now' : `${duration(updated)} ago`}</p>}
      {connected && !failed && active && !['waiting', 'queued', 'stopping'].includes(progress.state) && updated !== null && updated >= 60 &&
        <p className="m-0 text-xs text-fg-muted">This is taking longer. The last reported step is shown below; no new activity has been reported yet.</p>}
      {progress.steps.length > 0 && <>
        <p className="m-0 text-xs text-fg-muted">{finished} of {progress.steps.length}{progress.truncated ? ' shown' : ''} steps finished</p>
        <ul aria-label="Current activity" className="m-0 list-none p-0">{recent.map(renderStep)}</ul>
        {progress.steps.length > recent.length && <details>
          <summary className="cursor-pointer text-xs text-fg-muted">Show all activity ({progress.steps.length})</summary>
          <ul aria-label="All activity" className="m-0 list-none p-0">{progress.steps.map(renderStep)}</ul>
        </details>}
        {progress.truncated && <p className="m-0 text-xs text-fg-muted">Showing the most recent activity.</p>}
      </>}
    </section>
  );
}

/** Detailed work stays separate from list-owned controls and composer state. */
export function EmployeeWork({ employee }: { employee: EmployeeSummary }) {
  const { isReady } = useWebSocketActions();
  const detail = useEmployeeDetailQuery(employee.workflow_id, { live: true });
  const lane = useLaneRun(employee.workflow_id);
  const saved = detail.data?.work_progress;
  // A new message starts immediately; a previous completed job must not
  // mask its live steps while the next detail snapshot is on its way.
  const progress = saved && !(lane && ['done', 'failed', 'cancelled'].includes(saved.state)) ? saved : chatProgress(lane);
  const job = detail.data ? detail.data.job_progress : employee.job_progress;
  const delivery = job && ['delivery_needs_review', 'waiting_for_approval', 'failed', 'cancelled'].includes(job.state);
  return <>
    {progress && <WorkPanel progress={progress} connected={isReady} failed={detail.isError || progress.state === 'unavailable'} refresh={() => void detail.refetch()} />}
    {!progress && detail.isError && <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
      <span>Work progress is unavailable.</span>
      <ActionButton intent="config" onClick={() => void detail.refetch()}>Refresh progress</ActionButton>
    </div>}
    {(delivery || (!progress && job)) && <JobProgress key={`${job.request_id}:${job.state}`} employee={{ ...employee, job_progress: job }} />}
  </>;
}
