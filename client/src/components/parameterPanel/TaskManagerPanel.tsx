import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, Eye, Filter, RefreshCw, Search } from 'lucide-react';
import { toast } from 'sonner';
import type { Edge, Node } from 'reactflow';

import { useWebSocket, type TeamTaskTraceResponse } from '@/contexts/WebSocketContext';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { cn } from '@/lib/utils';

export type TeamTaskStatus = 'blocked' | 'queued' | 'running' | 'submitted' | 'accepted' | 'failed' | 'cancelled';

export interface TeamTaskAttempt {
  id: string;
  attempt_number: number;
  assignee_node_id?: string;
  status: string;
  result?: unknown;
  error?: string;
  started_at?: string;
  completed_at?: string;
  workflow_id?: string;
  run_id?: string;
  trace_id?: string;
}

export interface TeamTaskView {
  id: string;
  title: string;
  mission?: string;
  description?: string;
  context?: unknown;
  acceptance_criteria?: string | string[];
  status: TeamTaskStatus | string;
  assigned_to?: string;
  assignee_label?: string;
  assignee_type?: string;
  queue_position?: number;
  queue_sequence?: number;
  depends_on?: string[];
  current_attempt?: number;
  retry_count?: number;
  revision?: number;
  result?: unknown;
  error?: string;
  usage?: { input_tokens?: number; output_tokens?: number; total_tokens?: number; cost?: number };
  trace_id?: string;
  child_workflow_id?: string;
  child_run_id?: string;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  attempts?: TeamTaskAttempt[];
  allowed_actions?: string[];
}

interface TeamExecution {
  execution_id: string;
  label?: string;
  status?: string;
  created_at?: string;
}

interface TeamStatusView {
  team_id?: string;
  execution_id?: string;
  root_execution_id?: string;
  status?: string;
  tasks?: TeamTaskView[];
  active_tasks?: TeamTaskView[];
  archived_executions?: TeamExecution[];
  max_concurrent_subagents?: number;
  active_count?: number;
  [key: string]: unknown;
}

type PendingAction = { operation: string; task?: TeamTaskView; label: string } | null;

const STATUSES: TeamTaskStatus[] = ['blocked', 'queued', 'running', 'submitted', 'accepted', 'failed', 'cancelled'];
const STATUS_STYLES: Record<string, string> = {
  blocked: 'border-fg-muted/30 bg-bg-panel text-fg-muted',
  queued: 'border-warning/30 bg-warning/10 text-warning',
  running: 'border-info/30 bg-info/10 text-info',
  submitted: 'border-node-agent/30 bg-node-agent/10 text-node-agent',
  accepted: 'border-success/30 bg-success/10 text-success',
  failed: 'border-destructive/30 bg-destructive/10 text-destructive',
  cancelled: 'border-fg-muted/30 bg-bg-panel text-fg-muted',
};

const jsonText = (value: unknown): string => {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
};

const timestampMs = (value?: string): number | null => {
  if (!value) return null;
  // SQLite commonly returns UTC timestamps without a trailing zone. Browsers
  // otherwise interpret those as local time and skew elapsed durations.
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value.replace(' ', 'T')}Z`;
  const parsed = new Date(normalized).getTime();
  return Number.isFinite(parsed) ? parsed : null;
};

const formatTimestamp = (value?: string): string => {
  const parsed = timestampMs(value);
  return parsed == null ? '—' : new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium', timeStyle: 'medium',
  }).format(new Date(parsed));
};

const elapsed = (task: TeamTaskView, nowMs: number): string => {
  const startMs = timestampMs(task.started_at);
  if (startMs == null) return '—';
  const completedMs = timestampMs(task.completed_at);
  const end = completedMs ?? nowMs;
  const seconds = Math.max(0, Math.floor((end - startMs) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
};

const taskUsage = (task: TeamTaskView) => {
  const nested = task.result && typeof task.result === 'object'
    ? (task.result as { usage?: TeamTaskView['usage'] }).usage : undefined;
  const usage = task.usage || nested || {};
  const input = Number(usage.input_tokens || 0);
  const output = Number(usage.output_tokens || 0);
  const total = Number(usage.total_tokens ?? (input + output));
  return { input, output, total, cost: Number(usage.cost || 0) };
};

interface Props { nodeId: string; workflowId?: string; nodes: Node[]; edges: Edge[] }

const TaskManagerPanel: React.FC<Props> = ({ nodeId, workflowId, nodes, edges }) => {
  const { sendRequest, addEventListener, getTeamTaskTrace } = useWebSocket();
  const [status, setStatus] = useState<TeamStatusView | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [executionId, setExecutionId] = useState<string>('active');
  const [selected, setSelected] = useState<TeamTaskView | null>(null);
  const [pending, setPending] = useState<PendingAction>(null);
  const [actionText, setActionText] = useState('');
  const [assignee, setAssignee] = useState('');
  const [busy, setBusy] = useState(false);
  const [page, setPage] = useState(0);
  const [nowMs, setNowMs] = useState(0);
  const [detailTab, setDetailTab] = useState<'details' | 'trace'>('details');
  const [trace, setTrace] = useState<TeamTaskTraceResponse | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceAttempt, setTraceAttempt] = useState('current');
  const [traceSearch, setTraceSearch] = useState('');
  const pageSize = 25;

  const connectedLead = useMemo(() => {
    const selected = nodes.find((node) => node.id === nodeId);
    if (selected && ['orchestrator_agent', 'ai_employee'].includes(selected.type || '')) return selected;
    const edge = edges.find((candidate) => candidate.source === nodeId && candidate.targetHandle === 'input-tools');
    return edge ? nodes.find((node) => node.id === edge.target) : undefined;
  }, [edges, nodeId, nodes]);

  const teammates = useMemo(() => {
    if (!connectedLead) return [];
    return edges
      .filter((edge) => edge.target === connectedLead.id && edge.targetHandle === 'input-teammates')
      .map((edge) => nodes.find((node) => node.id === edge.source))
      .filter((node): node is Node => !!node);
  }, [connectedLead, edges, nodes]);

  const fetchStatus = useCallback(async (quiet = false) => {
    if (!workflowId || !connectedLead) return;
    if (!quiet) setLoading(true);
    try {
      const scope = {
        workflow_id: workflowId,
        team_lead_node_id: connectedLead.id,
        ...(executionId !== 'active' ? { execution_id: executionId } : {}),
      };
      const response = await sendRequest<{ status?: TeamStatusView }>('get_team_status', scope);
      const resolvedExecutionId = response?.status?.execution_id;
      const taskResponse = await sendRequest<{ tasks?: TeamTaskView[] }>('get_team_tasks', {
        ...scope,
        ...(executionId === 'active'
          ? { include_history: true }
          : resolvedExecutionId ? { execution_id: resolvedExecutionId } : {}),
      });
      setStatus(response?.status ? { ...response.status, tasks: taskResponse?.tasks || response.status.tasks || [] } : null);
    } catch (error) {
      if (!quiet) toast.error(error instanceof Error ? error.message : 'Unable to load team tasks');
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [connectedLead, executionId, sendRequest, workflowId]);

  useEffect(() => { void fetchStatus(); }, [fetchStatus]);
  useEffect(() => {
    const refresh = (event: any) => {
      const eventTeamId = event?.team_id ?? event?.data?.team_id;
      if (!eventTeamId || !status?.team_id || eventTeamId === status.team_id) void fetchStatus(true);
    };
    const unsubscribers = ['team_event', 'task_added', 'task_claimed', 'task_completed', 'task_failed',
      'team.task.submitted', 'team.task.requeued', 'team.task.failed', 'team.task.cancelled', 'team.task.accepted']
      .map((name) => addEventListener(name, refresh));
    return () => unsubscribers.forEach((unsubscribe) => unsubscribe());
  }, [addEventListener, fetchStatus, status?.team_id]);
  useEffect(() => {
    const interval = window.setInterval(() => void fetchStatus(true), 15_000);
    return () => window.clearInterval(interval);
  }, [fetchStatus]);

  const tasks = useMemo(() => {
    const full = status?.tasks?.length ? status.tasks : (status?.active_tasks || []);
    return full.filter((task) => {
      if (filter !== 'all' && task.status !== filter) return false;
      const haystack = `${task.title} ${task.mission || task.description || ''} ${task.assignee_label || task.assigned_to || ''}`.toLowerCase();
      return haystack.includes(search.toLowerCase());
    });
  }, [filter, search, status]);
  const counts = useMemo(() => Object.fromEntries(STATUSES.map((name) => [name,
    (status?.tasks || []).filter((task) => task.status === name).length || Number(status?.[`${name}_count`] || 0),
  ])), [status]);
  const active = Number(status?.active_count ?? counts.running ?? 0);
  const limit = Number(status?.max_concurrent_subagents || 3);
  const pages = Math.max(1, Math.ceil(tasks.length / pageSize));
  const visible = tasks.slice(page * pageSize, (page + 1) * pageSize);

  useEffect(() => setPage(0), [filter, search, executionId]);
  useEffect(() => {
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  const fetchTrace = useCallback(async (cursor?: string) => {
    if (!selected || !workflowId || !connectedLead || !status?.execution_id) return;
    setTraceLoading(true);
    try {
      const response = await getTeamTaskTrace({
        workflow_id: workflowId,
        team_lead_node_id: connectedLead.id,
        execution_id: status.execution_id,
        task_id: selected.id,
        ...(traceAttempt !== 'current' ? { attempt: Number(traceAttempt) } : {}),
        ...(cursor ? { cursor } : {}),
        limit: 50,
        detail: 'timeline',
      });
      setTrace((previous) => cursor && previous
        ? { ...response, events: [...previous.events, ...response.events] }
        : response);
    } catch (error) {
      setTrace({ state: 'temporal_unavailable', events: [], error: error instanceof Error ? error.message : 'Unable to load trace' });
    } finally { setTraceLoading(false); }
  }, [connectedLead, getTeamTaskTrace, selected, status?.execution_id, traceAttempt, workflowId]);

  useEffect(() => {
    if (detailTab !== 'trace' || !selected) return;
    setTrace(null);
    void fetchTrace();
  }, [detailTab, fetchTrace, selected, traceAttempt]);

  const traceEvents = useMemo(() => (trace?.events || []).filter((event) => {
    const value = `${event.event_type} ${event.name || ''} ${event.summary || ''} ${event.failure || ''}`.toLowerCase();
    return value.includes(traceSearch.toLowerCase());
  }), [trace, traceSearch]);

  const runAction = async () => {
    if (!pending || !workflowId || !connectedLead) return;
    setBusy(true);
    try {
      const isFinish = pending.operation === 'finish';
      const response = await sendRequest<{ success?: boolean; error?: string }>(isFinish ? 'finish_team' : 'manage_team_task', {
        ...(!isFinish ? { operation: pending.operation } : {}),
        workflow_id: workflowId,
        team_lead_node_id: connectedLead.id,
        execution_id: status?.execution_id,
        task_id: pending.task?.id,
        revision: pending.task?.revision,
        reason: actionText || undefined,
        mission: pending.operation === 'modify' ? actionText : undefined,
        assignee_node_id: pending.operation === 'reassign' ? assignee : undefined,
      });
      if (response?.success === false || response?.error) throw new Error(response.error || 'Action rejected');
      toast.success(`${pending.label} requested`);
      setPending(null); setActionText(''); setAssignee('');
      await fetchStatus(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Task changed before this action could be applied');
      await fetchStatus(true);
    } finally { setBusy(false); }
  };

  const openAction = (operation: string, label: string, task?: TeamTaskView) => {
    setActionText(operation === 'modify' ? (task?.mission || task?.description || '') : '');
    setAssignee('');
    setPending({ operation, label, task });
  };
  const actionsFor = (task: TeamTaskView) => task.allowed_actions || ({
    blocked: ['modify', 'cancel'], queued: ['modify', 'cancel'], running: ['cancel'],
    submitted: ['accept', 'retry', 'reassign'], failed: ['retry', 'reassign'],
    cancelled: ['retry', 'reassign'], accepted: [],
  }[task.status] || []);

  if (!connectedLead) return <div className="flex h-full items-center justify-center p-8 text-center text-fg-muted">Connect Task Manager to a team lead’s Tools handle to inspect its tasks.</div>;

  return <div className="flex h-full min-h-0 flex-col bg-bg-panel">
    <div className="shrink-0 border-b border-border-default px-5 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="text-lg font-semibold text-fg-default">Team tasks</h2><p className="text-sm text-fg-muted">{connectedLead.data?.label || 'Team lead'} · {status?.status || 'No active team'}</p></div>
        <div className="flex items-center gap-2">
          {(status?.archived_executions?.length || 0) > 0 && <Select value={executionId} onValueChange={setExecutionId}><SelectTrigger className="w-48" aria-label="Execution"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="active">Latest execution</SelectItem>{status!.archived_executions!.map((run) => <SelectItem key={run.execution_id} value={run.execution_id}>{run.label || run.execution_id.slice(0, 12)}</SelectItem>)}</SelectContent></Select>}
          <Button variant="outline" size="sm" onClick={() => void fetchStatus()} disabled={loading}><RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} /> Refresh</Button>
          <Button size="sm" disabled={!status?.team_id} onClick={() => openAction('finish', 'Finish team')}>Finish team</Button>
        </div>
      </div>
      <div className="mt-4 grid grid-cols-4 gap-2 lg:grid-cols-8">
        <button className="rounded-md border border-border-default bg-bg-elevated p-2 text-left" onClick={() => setFilter('running')}><span className="text-xs text-fg-muted">Concurrency</span><div className="font-semibold tabular-nums text-info">{active} / {limit}</div></button>
        {STATUSES.map((name) => <button key={name} className={cn('rounded-md border p-2 text-left', STATUS_STYLES[name], filter === name && 'ring-2 ring-node-agent')} onClick={() => setFilter(filter === name ? 'all' : name)}><span className="text-xs capitalize">{name}</span><div className="font-semibold tabular-nums">{counts[name] || 0}</div></button>)}
      </div>
    </div>
    <div className="flex min-h-0 flex-1 flex-col p-4">
      <div className="mb-3 flex gap-2"><div className="relative flex-1"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" /><Input className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search tasks, missions, or assignees" /></div><Select value={filter} onValueChange={setFilter}><SelectTrigger className="w-40"><Filter className="h-4 w-4" /><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All statuses</SelectItem>{STATUSES.map((name) => <SelectItem key={name} value={name} className="capitalize">{name}</SelectItem>)}</SelectContent></Select></div>
      <div className="min-h-0 flex-1 overflow-auto rounded-md border border-border-default bg-bg-elevated">
        <table className="w-full min-w-[1100px] border-collapse text-sm"><thead className="sticky top-0 z-10 bg-bg-panel text-left text-xs text-fg-muted"><tr>{['Task','Assignee','Status','Started','Completed','Elapsed','Attempt','Tokens',''].map((h) => <th key={h} className="border-b border-border-default px-3 py-2 font-medium">{h}</th>)}</tr></thead><tbody>
          {visible.map((task) => { const usage = taskUsage(task); return <tr key={task.id} className="border-b border-border-default/70 hover:bg-bg-panel"><td className="max-w-[320px] px-3 py-3"><button className="block w-full text-left" onClick={() => setSelected(task)}><span className="block truncate font-medium text-fg-default">{task.title}</span><span className="block truncate text-xs text-fg-muted">{task.mission || task.description || 'No mission provided'}</span></button></td><td className="px-3 py-3"><span className="block">{task.assignee_label || task.assigned_to || 'Unassigned'}</span><span className="text-xs text-fg-muted">{task.assignee_type || ''}</span></td><td className="px-3 py-3"><Badge variant="outline" className={cn('capitalize', STATUS_STYLES[task.status])}>{task.status}</Badge></td><td className="whitespace-nowrap px-3 py-3 text-xs">{formatTimestamp(task.started_at)}</td><td className="whitespace-nowrap px-3 py-3 text-xs">{formatTimestamp(task.completed_at)}</td><td className="px-3 py-3 tabular-nums">{elapsed(task, nowMs)}</td><td className="px-3 py-3 tabular-nums">{task.current_attempt ?? 0}{task.retry_count ? ` (+${task.retry_count})` : ''}</td><td className="px-3 py-3 tabular-nums"><span className="block font-medium">{usage.total.toLocaleString()}</span><span className="whitespace-nowrap text-[11px] text-fg-muted">{usage.input.toLocaleString()} in · {usage.output.toLocaleString()} out</span></td><td className="px-3 py-3"><Button variant="ghost" size="sm" onClick={() => setSelected(task)} aria-label={`Inspect ${task.title}`}><Eye className="h-4 w-4" /></Button></td></tr>; })}
          {!loading && visible.length === 0 && <tr><td colSpan={9} className="p-12 text-center text-fg-muted">No tasks match this view.</td></tr>}
        </tbody></table>
      </div>
      {pages > 1 && <div className="mt-3 flex items-center justify-end gap-2 text-sm text-fg-muted"><Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button><span>{page + 1} / {pages}</span><Button variant="outline" size="sm" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</Button></div>}
    </div>

    <Dialog open={!!selected} onOpenChange={(open) => { if (!open) { setSelected(null); setDetailTab('details'); setTrace(null); } }}><DialogContent className="max-h-[85vh] max-w-4xl overflow-y-auto"><DialogHeader><DialogTitle>{selected?.title}</DialogTitle></DialogHeader>{selected && <div className="space-y-5 text-sm">
      <div className="flex gap-1 border-b border-border-default"><Button variant={detailTab === 'details' ? 'secondary' : 'ghost'} size="sm" onClick={() => setDetailTab('details')}>Details</Button><Button variant={detailTab === 'trace' ? 'secondary' : 'ghost'} size="sm" onClick={() => setDetailTab('trace')}><Activity className="h-4 w-4" /> Temporal trace</Button></div>
      {detailTab === 'details' && <>
      <div className="flex flex-wrap items-center gap-2"><Badge variant="outline" className={cn('capitalize', STATUS_STYLES[selected.status])}>{selected.status}</Badge><span className="text-fg-muted">Revision {selected.revision ?? 0} · Attempt {selected.current_attempt ?? 0} · {elapsed(selected, nowMs)}</span></div>
      <section className="grid gap-2 rounded border border-border-default p-3 text-xs text-fg-muted sm:grid-cols-3"><span>Created<br/><strong className="font-medium text-fg-default">{formatTimestamp(selected.created_at)}</strong></span><span>Started<br/><strong className="font-medium text-fg-default">{formatTimestamp(selected.started_at)}</strong></span><span>Completed<br/><strong className="font-medium text-fg-default">{formatTimestamp(selected.completed_at)}</strong></span></section>
      <section><h3 className="mb-2 font-medium">Token usage</h3><div className="grid grid-cols-3 gap-2 rounded border border-border-default p-3 text-center"><div><div className="text-xs text-fg-muted">Input</div><div className="font-semibold tabular-nums">{taskUsage(selected).input.toLocaleString()}</div></div><div><div className="text-xs text-fg-muted">Output</div><div className="font-semibold tabular-nums">{taskUsage(selected).output.toLocaleString()}</div></div><div><div className="text-xs text-fg-muted">Total</div><div className="font-semibold tabular-nums">{taskUsage(selected).total.toLocaleString()}</div></div></div></section>
      <section><h3 className="mb-1 font-medium">Mission</h3><p className="whitespace-pre-wrap text-fg-muted">{selected.mission || selected.description || '—'}</p></section>
      <section><h3 className="mb-1 font-medium">Acceptance criteria</h3><pre className="whitespace-pre-wrap rounded bg-bg-panel p-3 text-fg-muted">{jsonText(selected.acceptance_criteria) || '—'}</pre></section>
      <section><h3 className="mb-1 font-medium">Context</h3><pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-bg-panel p-3 text-xs text-fg-muted">{jsonText(selected.context) || '—'}</pre></section>
      {(selected.result != null || selected.error) && <section><h3 className="mb-1 font-medium">Result</h3><pre className={cn('max-h-64 overflow-auto whitespace-pre-wrap rounded p-3 text-xs', selected.error ? 'bg-destructive/10 text-destructive' : 'bg-success/10')}>{selected.error || jsonText(selected.result)}</pre></section>}
      <section><h3 className="mb-2 font-medium">Attempt history</h3><div className="space-y-2">{(selected.attempts || []).map((attempt) => <div key={attempt.id} className="rounded border border-border-default p-3"><div className="flex justify-between"><span>Attempt {attempt.attempt_number} · {attempt.assignee_node_id || 'Unassigned'}</span><Badge variant="outline">{attempt.status}</Badge></div>{attempt.error && <p className="mt-2 text-destructive">{attempt.error}</p>}</div>)}{!selected.attempts?.length && <p className="text-fg-muted">No previous attempts.</p>}</div></section>
      <section className="grid grid-cols-2 gap-2 rounded border border-border-default p-3 text-xs text-fg-muted"><span>Trace: {selected.trace_id || '—'}</span><span>Child workflow: {selected.child_workflow_id || '—'}</span><span>Child run: {selected.child_run_id || '—'}</span><span>Dependencies: {selected.depends_on?.join(', ') || 'None'}</span></section>
      <div className="flex flex-wrap justify-end gap-2">{actionsFor(selected).map((action) => <Button key={action} variant={action === 'cancel' ? 'destructive' : action === 'accept' ? 'default' : 'outline'} onClick={() => { setSelected(null); openAction(action.replace('_task', ''), action.replace('_task','').replace('_',' '), selected); }}>{action.replace('_task','').replace('_',' ')}</Button>)}</div>
      </>}
      {detailTab === 'trace' && <section className="space-y-3">
        <div className="flex flex-wrap gap-2"><Select value={traceAttempt} onValueChange={setTraceAttempt}><SelectTrigger className="w-44" aria-label="Trace attempt"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="current">Current attempt</SelectItem>{(selected.attempts || []).map((attempt) => <SelectItem key={attempt.id} value={String(attempt.attempt_number)}>Attempt {attempt.attempt_number}</SelectItem>)}</SelectContent></Select><div className="relative min-w-52 flex-1"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted"/><Input className="pl-9" value={traceSearch} onChange={(event) => setTraceSearch(event.target.value)} placeholder="Search events, activities, failures"/></div><Button variant="outline" onClick={() => void fetchTrace()} disabled={traceLoading}><RefreshCw className={cn('h-4 w-4', traceLoading && 'animate-spin')}/> Refresh</Button></div>
        {trace?.state === 'available' && <><div className="grid gap-2 rounded border border-border-default p-3 text-xs text-fg-muted sm:grid-cols-3"><span>Workflow<br/><strong className="break-all font-medium text-fg-default">{trace.workflow_id || selected.child_workflow_id || '—'}</strong></span><span>Run<br/><strong className="break-all font-medium text-fg-default">{trace.run_id || selected.child_run_id || '—'}</strong></span><span>Trace<br/><strong className="break-all font-medium text-fg-default">{trace.trace_id || selected.trace_id || '—'}</strong></span></div><ol className="divide-y divide-border-default overflow-hidden rounded border border-border-default">{traceEvents.map((event) => <li key={event.event_id} className="grid grid-cols-[9rem_minmax(0,1fr)] gap-3 p-3"><time className="text-xs text-fg-muted">{formatTimestamp(event.timestamp)}</time><div className="min-w-0"><div className="flex items-center gap-2"><Badge variant="outline">{event.category || event.event_type}</Badge><span className="truncate font-medium">{event.name || event.event_type}</span></div>{event.summary && <p className="mt-1 whitespace-pre-wrap text-xs text-fg-muted">{event.summary}</p>}{event.failure && <p className="mt-2 whitespace-pre-wrap rounded bg-destructive/10 p-2 text-xs text-destructive"><AlertTriangle className="mr-1 inline h-3 w-3"/>{event.failure}</p>}</div></li>)}{!traceEvents.length && <li className="p-8 text-center text-fg-muted">No trace events match this view.</li>}</ol>{trace.next_cursor && <Button variant="outline" className="w-full" disabled={traceLoading} onClick={() => void fetchTrace(trace.next_cursor || undefined)}>Load older events</Button>}</>}
        {trace && trace.state !== 'available' && <div className="rounded border border-warning/30 bg-warning/10 p-6 text-center"><AlertTriangle className="mx-auto mb-2 h-5 w-5 text-warning"/><p className="font-medium capitalize">{trace.state.replace(/_/g, ' ')}</p><p className="mt-1 text-sm text-fg-muted">{trace.error || (trace.state === 'retention_expired' ? 'Temporal history has expired; the durable task result remains available in Details.' : 'No Temporal execution history is currently available for this attempt.')}</p></div>}
        {!trace && traceLoading && <div className="p-10 text-center text-fg-muted">Loading Temporal history…</div>}
      </section>}
    </div>}</DialogContent></Dialog>

    <AlertDialog open={!!pending} onOpenChange={(open) => !open && setPending(null)}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle className="capitalize">{pending?.label}</AlertDialogTitle><AlertDialogDescription>{pending?.task ? `Apply this action to “${pending.task.title}”? The latest task revision will be checked.` : 'Finish this team only if every required task is resolved.'}</AlertDialogDescription></AlertDialogHeader>
      {pending?.operation === 'modify' && <Textarea value={actionText} onChange={(e) => setActionText(e.target.value)} placeholder="Updated mission" rows={5} />}
      {pending?.operation === 'reassign' && <Select value={assignee} onValueChange={setAssignee}><SelectTrigger><SelectValue placeholder="Choose a connected teammate" /></SelectTrigger><SelectContent>{teammates.map((node) => <SelectItem key={node.id} value={node.id}>{node.data?.label || node.type || node.id}</SelectItem>)}</SelectContent></Select>}
      {['cancel','retry','accept','finish'].includes(pending?.operation || '') && <Textarea value={actionText} onChange={(e) => setActionText(e.target.value)} placeholder="Reason or review note (optional)" rows={3} />}
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction disabled={busy || (pending?.operation === 'reassign' && !assignee) || (pending?.operation === 'modify' && !actionText.trim())} onClick={(event) => { event.preventDefault(); void runAction(); }}>{busy ? 'Applying…' : 'Confirm'}</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
  </div>;
};

export default TaskManagerPanel;
