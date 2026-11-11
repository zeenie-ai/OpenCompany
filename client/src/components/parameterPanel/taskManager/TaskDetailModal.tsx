/**
 * An opened team task: one of the editor's modals (ui/Modal). Its name and
 * status sit at the head with the two tabs, Details and Temporal trace; the
 * one body below scrolls; and what the owner may do to the task is a row at
 * the foot, with Accept as the only strong button.
 *
 * Colour marks state only: the status dot, and a failure's frame. Buttons
 * are neutral, ids and times are in the mono face, and JSON is on the
 * theme's code surface.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Activity, AlertTriangle, ListChecks, RefreshCw, Search } from 'lucide-react';

import type { TeamTaskTraceResponse } from '@/contexts/WebSocketContext';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import Modal from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';
import {
  actionLabel, actionsFor, elapsed, formatTimestamp, isEmpty, jsonText, sentenceCase, taskUsage,
  type TeamTaskView,
} from './model';
import { TaskStatus } from './TaskStatus';

/** A page of a task attempt's Temporal history; `attempt` null is the current one. */
export type LoadTrace = (taskId: string, attempt: number | null, cursor?: string) => Promise<TeamTaskTraceResponse>;

interface TaskDetailModalProps {
  /** The opened task; none closes the modal. */
  task: TeamTaskView | null;
  /** The clock a running task's elapsed time is read against. */
  nowMs: number;
  /** A teammate's name on the canvas, for its node id. */
  agentName: (nodeId?: string) => string;
  loadTrace: LoadTrace;
  onAction: (operation: string, task: TeamTaskView) => void;
  onClose: () => void;
}

const NEUTRAL = 'border-border-default text-fg-default';
const HEADING = 'font-display font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]';
const CARD = 'rounded-lg border border-border-default bg-bg-elevated';
const PROSE = 'text-sm leading-relaxed break-words whitespace-pre-wrap text-fg-default';
const CODE = 'overflow-auto rounded-md border border-[var(--code-border)] bg-[var(--code-bg)] p-3 font-mono text-xs break-words whitespace-pre-wrap text-[var(--code-text)]';
const FAILURE = 'rounded-md border border-danger-border bg-danger-soft p-3 break-words whitespace-pre-wrap text-destructive';

const TRACE_NOTE: Partial<Record<TeamTaskTraceResponse['state'], string>> = {
  retention_expired: 'Temporal history has expired; the durable task result remains available in Details.',
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h4 className={cn(HEADING, 'text-sm')}>{title}</h4>
      {children}
    </section>
  );
}

/** One labelled fact: a time, a count or an id, in the mono face. */
function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="mt-0.5 font-mono text-xs break-words text-fg-default tabular-nums">{children}</dd>
    </div>
  );
}

/** A task field as the server sent it: prose as prose, a list of lines as a
 *  list, anything else as JSON on the code surface. */
function Value({ value, className }: { value: unknown; className?: string }) {
  if (typeof value === 'string') return <p className={PROSE}>{value}</p>;
  if (Array.isArray(value) && value.every((item) => typeof item === 'string')) {
    return (
      <ul className={cn(PROSE, 'list-disc space-y-1 pl-5')}>
        {value.map((item, index) => <li key={index}>{item}</li>)}
      </ul>
    );
  }
  return <pre className={cn(CODE, className)}>{jsonText(value)}</pre>;
}

function Details({ task, agentName }: { task: TeamTaskView; agentName: (nodeId?: string) => string }) {
  const usage = taskUsage(task);
  const mission = task.mission || task.description;
  const attempts = task.attempts ?? [];
  return (
    <div className="flex flex-col gap-5 px-5 py-4">
      <dl className={cn(CARD, 'grid grid-cols-3 gap-x-4 gap-y-3 p-3.5')}>
        <Fact label="Created">{formatTimestamp(task.created_at)}</Fact>
        <Fact label="Started">{formatTimestamp(task.started_at)}</Fact>
        <Fact label="Completed">{formatTimestamp(task.completed_at)}</Fact>
        <Fact label="Input tokens">{usage.input.toLocaleString()}</Fact>
        <Fact label="Output tokens">{usage.output.toLocaleString()}</Fact>
        <Fact label="Total tokens">{usage.total.toLocaleString()}</Fact>
      </dl>

      <Section title="Mission">
        {mission ? <p className={PROSE}>{mission}</p> : <p className="text-sm text-fg-muted">No mission provided.</p>}
      </Section>

      {!isEmpty(task.acceptance_criteria) && (
        <Section title="Acceptance criteria"><Value value={task.acceptance_criteria} className="max-h-48" /></Section>
      )}

      {!isEmpty(task.context) && (
        <Section title="Context"><Value value={task.context} className="max-h-48" /></Section>
      )}

      {task.error ? (
        <Section title="Error"><p className={cn(FAILURE, 'text-sm')}>{task.error}</p></Section>
      ) : !isEmpty(task.result) && (
        <Section title="Result"><Value value={task.result} className="max-h-96" /></Section>
      )}

      {attempts.length > 0 && (
        <Section title="Attempt history">
          <ul className={cn(CARD, 'divide-y divide-border-default')}>
            {attempts.map((attempt) => (
              <li key={attempt.id} className="flex flex-col gap-1.5 px-3.5 py-2.5">
                <div className="flex items-center justify-between gap-3">
                  <span className="min-w-0 truncate text-sm text-fg-default">
                    Attempt {attempt.attempt_number}
                    <span className="text-fg-muted"> · {agentName(attempt.assignee_node_id)}</span>
                  </span>
                  <TaskStatus status={attempt.status} />
                </div>
                {attempt.error && <p className="text-xs break-words whitespace-pre-wrap text-destructive">{attempt.error}</p>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="References">
        <dl className={cn(CARD, 'grid grid-cols-2 gap-x-4 gap-y-3 p-3.5')}>
          <Fact label="Trace">{task.trace_id || '—'}</Fact>
          <Fact label="Child workflow">{task.child_workflow_id || '—'}</Fact>
          <Fact label="Child run">{task.child_run_id || '—'}</Fact>
          <Fact label="Depends on">{task.depends_on?.join(', ') || 'None'}</Fact>
        </dl>
      </Section>
    </div>
  );
}

function Trace({ task, loadTrace }: { task: TeamTaskView; loadTrace: LoadTrace }) {
  const [trace, setTrace] = useState<TeamTaskTraceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState('current');
  const [query, setQuery] = useState('');

  const fetchTrace = useCallback(async (cursor?: string) => {
    setLoading(true);
    try {
      const response = await loadTrace(task.id, attempt === 'current' ? null : Number(attempt), cursor);
      setTrace((previous) => (cursor && previous
        ? { ...response, events: [...previous.events, ...response.events] }
        : response));
    } catch (error) {
      setTrace({ state: 'temporal_unavailable', events: [], error: error instanceof Error ? error.message : 'Unable to load trace' });
    } finally {
      setLoading(false);
    }
  }, [attempt, loadTrace, task.id]);

  useEffect(() => {
    setTrace(null);
    void fetchTrace();
  }, [fetchTrace]);

  const events = useMemo(() => {
    const term = query.toLowerCase();
    return (trace?.events ?? []).filter((event) =>
      `${event.event_type} ${event.name || ''} ${event.summary || ''} ${event.failure || ''}`.toLowerCase().includes(term));
  }, [query, trace]);

  return (
    <div className="flex flex-col gap-3 px-5 py-4">
      <div className="flex flex-wrap gap-2">
        <Select value={attempt} onValueChange={setAttempt}>
          <SelectTrigger className="w-44" aria-label="Trace attempt"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="current">Current attempt</SelectItem>
            {(task.attempts ?? []).map((item) => (
              <SelectItem key={item.id} value={String(item.attempt_number)}>Attempt {item.attempt_number}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative min-w-52 flex-1">
          <Search aria-hidden className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-muted" />
          <Input
            className="pl-9"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search events, activities, failures"
            aria-label="Search the trace"
          />
        </div>
        <Button variant="quiet" className={NEUTRAL} onClick={() => void fetchTrace()} disabled={loading}>
          <RefreshCw className={cn(loading && 'animate-spin')} /> Refresh
        </Button>
      </div>

      {!trace && loading && <p className="p-10 text-center text-sm text-fg-muted">Loading Temporal history…</p>}

      {trace?.state === 'available' && (
        <>
          <dl className={cn(CARD, 'grid grid-cols-3 gap-x-4 gap-y-3 p-3.5')}>
            <Fact label="Workflow">{trace.workflow_id || task.child_workflow_id || '—'}</Fact>
            <Fact label="Run">{trace.run_id || task.child_run_id || '—'}</Fact>
            <Fact label="Trace">{trace.trace_id || task.trace_id || '—'}</Fact>
          </dl>
          <ol className={cn(CARD, 'divide-y divide-border-default')}>
            {events.map((event) => (
              <li key={event.event_id} className="grid grid-cols-[9.5rem_minmax(0,1fr)] gap-3 px-3.5 py-2.5">
                <time className="font-mono text-xs text-fg-muted tabular-nums">{formatTimestamp(event.timestamp)}</time>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="border-border-default text-fg-muted">{event.category || event.event_type}</Badge>
                    <span className="truncate text-sm font-medium text-fg-default">{event.name || event.event_type}</span>
                  </div>
                  {event.summary && <p className="mt-1 text-xs break-words whitespace-pre-wrap text-fg-muted">{event.summary}</p>}
                  {event.failure && (
                    <p className={cn(FAILURE, 'mt-2 p-2 text-xs')}>
                      <AlertTriangle aria-hidden className="mr-1 inline size-3" />{event.failure}
                    </p>
                  )}
                </div>
              </li>
            ))}
            {events.length === 0 && <li className="p-8 text-center text-sm text-fg-muted">No trace events match this view.</li>}
          </ol>
          {trace.next_cursor && (
            <Button
              variant="quiet"
              className={cn(NEUTRAL, 'w-full')}
              disabled={loading}
              onClick={() => void fetchTrace(trace.next_cursor || undefined)}
            >
              Load older events
            </Button>
          )}
        </>
      )}

      {trace && trace.state !== 'available' && (
        <div className={cn(CARD, 'flex flex-col items-center gap-1.5 p-6 text-center')}>
          <AlertTriangle aria-hidden className="size-5 text-fg-muted" />
          <p className="text-sm font-medium text-fg-default">{sentenceCase(trace.state)}</p>
          <p className="text-sm text-fg-muted">
            {trace.error || TRACE_NOTE[trace.state] || 'No Temporal execution history is currently available for this attempt.'}
          </p>
        </div>
      )}
    </div>
  );
}

/** The opened task's head, tabs, body and actions. Keyed by the task, so a
 *  newly opened one starts on Details with no trace loaded. */
function TaskDetail({ task, nowMs, agentName, loadTrace, onAction }: Omit<TaskDetailModalProps, 'task' | 'onClose'> & { task: TeamTaskView }) {
  const [tab, setTab] = useState('details');
  const taken = elapsed(task, nowMs);
  const facts = [
    task.assignee_label || agentName(task.assigned_to),
    `Revision ${task.revision ?? 0}`,
    `Attempt ${task.current_attempt ?? 0}`,
    ...(taken === '—' ? [] : [taken]),
  ];
  // Accept is the task's one strong action, so it comes last in the row.
  const actions = [...actionsFor(task)].sort((a, b) => Number(a === 'accept') - Number(b === 'accept'));

  return (
    <Tabs value={tab} onValueChange={setTab} className="min-h-0 flex-1 gap-0">
      <div className="shrink-0 border-b border-border-default px-5 py-4">
        <h3 className={cn(HEADING, 'text-base break-words')}>{task.title}</h3>
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
          <TaskStatus status={task.status} />
          <span className="text-xs text-fg-muted">{facts.join(' · ')}</span>
          <TabsList className="ml-auto">
            <TabsTrigger value="details" className="px-2.5">Details</TabsTrigger>
            <TabsTrigger value="trace" className="px-2.5"><Activity /> Temporal trace</TabsTrigger>
          </TabsList>
        </div>
      </div>

      <TabsContent value="details" className="min-h-0 overflow-y-auto">
        <Details task={task} agentName={agentName} />
      </TabsContent>
      <TabsContent value="trace" className="min-h-0 overflow-y-auto">
        <Trace task={task} loadTrace={loadTrace} />
      </TabsContent>

      {actions.length > 0 && (
        <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-border-default bg-bg-panel px-5 py-3">
          {actions.map((operation) => (
            <Button
              key={operation}
              variant={operation === 'accept' ? 'invert' : 'quiet'}
              className={cn('px-3.5 font-semibold', operation !== 'accept' && NEUTRAL)}
              onClick={() => onAction(operation, task)}
            >
              {actionLabel(operation)}
            </Button>
          ))}
        </div>
      )}
    </Tabs>
  );
}

export function TaskDetailModal({ task, onClose, ...rest }: TaskDetailModalProps) {
  return (
    <Modal
      isOpen={task !== null}
      onClose={onClose}
      title="Task"
      titleIcon={<ListChecks className="h-4 w-4 text-fg-muted" />}
      maxWidth="min(760px, calc(100vw - 2rem))"
      maxHeight="min(85vh, 720px)"
      // The head and the action row stay put; only the tab's body scrolls.
      scrollableBody={false}
    >
      {task && <TaskDetail key={task.id} task={task} {...rest} />}
    </Modal>
  );
}
