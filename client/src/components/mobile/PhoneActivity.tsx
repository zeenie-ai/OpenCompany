import { useEffect, useState } from 'react';

const seconds = (from: unknown, to: number) => typeof from === 'number' ? Math.max(0, Math.floor(to - from)) : 0;
const duration = (milliseconds: number) => `${(Math.max(0, milliseconds) / 1000).toFixed(1)}s`;

type PlanItem = { id: string; description: string; status: 'pending' | 'not_started' | 'success' | 'failure'; reason?: string };
type AgentActivity = {
  at: number; step: number; kind: 'plan' | 'action'; message: string; detail?: string; outcome?: string;
  state?: 'started' | 'completed' | 'failed' | 'returned'; duration_ms?: number; action_id?: string;
};
const planLabels = { pending: 'In progress', not_started: 'Not started', success: 'Done', failure: 'Failed' };
const actionLabels = { started: 'In progress', completed: 'Completed', failed: 'Failed', returned: 'Unconfirmed' };

export function PhoneActivity({ task }: { task?: Record<string, unknown> | null }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const ended = typeof task?.finished_at === 'number';
  useEffect(() => {
    if (!task?.run_id || ended) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [task?.run_id, ended]);
  if (!task?.run_id) return null;
  const end = ended ? Number(task.finished_at) : now;
  const quiet = seconds(task.updated_at, end);
  const events = Array.isArray(task.activity) ? task.activity as { at: number; message: string; step: number }[] : [];
  const plan = Array.isArray(task.plan) ? task.plan as PlanItem[] : [];
  const goal = typeof task.current_goal === 'string' ? task.current_goal : '';
  const reportedActions = Array.isArray(task.agent_activity) ? task.agent_activity as AgentActivity[] : [];
  const currentAction = task.current_action && typeof task.current_action === 'object' ? task.current_action as AgentActivity : null;
  const actions = reportedActions.length ? reportedActions : currentAction ? [currentAction] : [];
  return <details open className="max-h-64 shrink-0 overflow-y-auto border-t border-border-default text-xs group-data-[full-view=true]/fullview:hidden" aria-label="Android activity">
    <summary className="cursor-pointer p-2 font-medium">AI activity · {ended ? String(task.status) : String(task.phase || 'Preparing phone')} · {seconds(task.started_at, end)}s total</summary>
    <div className="space-y-2 px-2 pb-2">
      {goal && <section aria-label="Current Android subtask" className="rounded border border-border-default bg-bg-panel p-2">
        <p className="m-0 text-fg-muted">{ended ? 'Last subtask' : 'Current subtask'}</p>
        <p className="m-0 break-words font-medium">{goal}</p>
      </section>}
      <p className="m-0 text-fg-muted" title="An engine step may include several phone actions.">Engine step {Number(task.steps || 0)} / {Number(task.max_steps || 0)}</p>
      {!ended && <p className="m-0" role="status">Current phase: {seconds(task.phase_started_at, now)}s · Last activity: {quiet}s ago</p>}
      {!ended && quiet >= 30 && <p className="m-0 text-warning">No new activity for {quiet}s. The last reported phase is shown above. This may be a slow request; it does not confirm the task is stuck. Take over to use the phone yourself.</p>}
      {ended && task.error_code != null && <p className="m-0 text-destructive">Stopped during {String(task.phase || 'startup')} · {String(task.error_code)}</p>}
      {!!plan.length && <details aria-label="Android task plan">
        <summary className="cursor-pointer font-medium">Plan · {plan.filter((item) => item.status === 'success').length} / {plan.length} done</summary>
        <ol className="m-0 list-none space-y-1 p-0">
          {plan.map((item) => <li key={item.id} className="break-words">
            <span className={item.status === 'failure' ? 'text-destructive' : 'text-fg-muted'}>{planLabels[item.status] || item.status} · </span>{item.description}
            {item.reason && <p className="m-0 text-fg-muted">{item.reason}</p>}
          </li>)}
        </ol>
      </details>}
      {!!actions.length && <ol className="m-0 list-none space-y-2 p-0" aria-label="Recent Android actions">
        {[...actions].reverse().map((item, index) => {
          const elapsed = typeof item.duration_ms === 'number' ? item.duration_ms
            : item.state === 'started' && !ended ? Math.max(0, end - item.at) * 1000 : null;
          return <li key={item.action_id || `${item.kind}-${item.at}-${index}`} className="break-words">
            <p className="m-0 text-fg-muted">+{seconds(task.started_at, item.at)}s · Engine step {item.step}
              {item.state && <> · <span className={item.state === 'failed' ? 'text-destructive' : undefined}>{ended && item.state === 'started' ? 'Started' : actionLabels[item.state]}</span></>}
              {elapsed !== null && <> · {duration(elapsed)}</>}
            </p>
            <p className="m-0 font-medium">{item.message}</p>
            {item.detail && <p className="m-0 text-fg-muted">{item.detail}</p>}
            {item.outcome && <p className={`m-0 ${item.state === 'failed' ? 'text-destructive' : 'text-fg-muted'}`}>Result: {item.outcome}</p>}
          </li>;
        })}
      </ol>}
      <details aria-label="Android technical activity">
        <summary className="cursor-pointer text-fg-muted">Technical activity</summary>
        <p className="my-1 text-fg-muted">{String(task.provider || '')} {String(task.model || '')}</p>
        <ol className="m-0 list-none space-y-1 p-0" aria-label="Recent Android technical events">
          {[...events].reverse().map((item, index) => <li key={`${item.at}-${index}`} className="break-words"><span className="text-fg-muted">+{seconds(task.started_at, item.at)}s · Engine step {item.step} · </span>{item.message}</li>)}
        </ol>
      </details>
    </div>
  </details>;
}
