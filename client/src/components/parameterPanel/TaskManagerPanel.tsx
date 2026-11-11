/**
 * The Task Manager's panel: a team lead's durable tasks, kept across
 * workflow restarts, with what the owner may do to each.
 *
 * The head names the lead, switches between executions and holds the panel's
 * one strong button, Finish team. Under it a chip per status carries its
 * count and filters the table; a task's row opens it (taskManager/
 * TaskDetailModal). Whatever changes a task goes through one confirm dialog,
 * which sends the task's revision so a stale click is refused.
 *
 * Colour marks state only (theme_system.md, "Colour marks state or role"):
 * a status is a dot beside neutral words (taskManager/TaskStatus), and every
 * button but Finish team and Confirm is neutral.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Eye, RefreshCw, Search } from 'lucide-react';
import { toast } from 'sonner';
import type { Edge, Node } from 'reactflow';

import { useWebSocket } from '@/contexts/WebSocketContext';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { cn } from '@/lib/utils';
import { NodePanel } from './NodePanel';
import { TASK_STATUSES, actionLabel, elapsed, formatTimestamp, taskUsage, type TeamTaskView } from './taskManager/model';
import { TaskDetailModal, type LoadTrace } from './taskManager/TaskDetailModal';
import { TaskStatus, TaskStatusDot } from './taskManager/TaskStatus';

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

const PAGE_SIZE = 25;
const COLUMNS = ['Task', 'Assignee', 'Status', 'Started', 'Completed', 'Elapsed', 'Attempt', 'Tokens'];
/** Operations whose confirm dialog takes an optional note. */
const NOTED = ['cancel', 'retry', 'accept', 'finish'];

const NEUTRAL = 'border-border-default text-fg-default';
const HEAD_CELL = 'border-b border-border-default px-3 py-2 font-medium';
const TIME_CELL = 'px-3 py-2.5 font-mono text-xs whitespace-nowrap text-fg-muted tabular-nums';

interface Props { nodeId: string; workflowId?: string; nodes: Node[]; edges: Edge[] }

const TaskManagerPanel: React.FC<Props> = ({ nodeId, workflowId, nodes, edges }) => {
  const { sendRequest, addEventListener, getTeamTaskTrace } = useWebSocket();
  const [status, setStatus] = useState<TeamStatusView | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  /** The status the table is filtered to; none shows every task. */
  const [filter, setFilter] = useState('');
  const [executionId, setExecutionId] = useState<string>('active');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction>(null);
  const [actionText, setActionText] = useState('');
  const [assignee, setAssignee] = useState('');
  const [busy, setBusy] = useState(false);
  const [page, setPage] = useState(0);
  const [nowMs, setNowMs] = useState(0);

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

  /** A teammate's name on the canvas, for its node id. */
  const agentName = useCallback((id?: string) => {
    if (!id) return 'Unassigned';
    return String(nodes.find((node) => node.id === id)?.data?.label || id);
  }, [nodes]);

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

  const allTasks = useMemo(
    () => (status?.tasks?.length ? status.tasks : (status?.active_tasks || [])),
    [status],
  );
  const tasks = useMemo(() => {
    const term = search.toLowerCase();
    return allTasks.filter((task) => {
      if (filter && task.status !== filter) return false;
      const haystack = `${task.title} ${task.mission || task.description || ''} ${task.assignee_label || task.assigned_to || ''}`.toLowerCase();
      return haystack.includes(term);
    });
  }, [allTasks, filter, search]);
  // The opened task is read from the list, so it follows each refresh: its
  // status, and the revision its actions send.
  const selected = useMemo(
    () => allTasks.find((task) => task.id === selectedId) ?? null,
    [allTasks, selectedId],
  );
  const counts = useMemo(() => Object.fromEntries(TASK_STATUSES.map((name) => [name,
    (status?.tasks || []).filter((task) => task.status === name).length || Number(status?.[`${name}_count`] || 0),
  ])), [status]);
  const active = Number(status?.active_count ?? counts.running ?? 0);
  const limit = Number(status?.max_concurrent_subagents || 3);
  const pages = Math.max(1, Math.ceil(tasks.length / PAGE_SIZE));
  const visible = tasks.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  useEffect(() => setPage(0), [filter, search, executionId]);
  useEffect(() => {
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  const leadId = connectedLead?.id;
  const traceExecutionId = status?.execution_id;
  const loadTrace = useCallback<LoadTrace>(async (taskId, attempt, cursor) => {
    if (!workflowId || !leadId || !traceExecutionId) return { state: 'execution_not_registered', events: [] };
    return getTeamTaskTrace({
      workflow_id: workflowId,
      team_lead_node_id: leadId,
      execution_id: traceExecutionId,
      task_id: taskId,
      ...(attempt !== null ? { attempt } : {}),
      ...(cursor ? { cursor } : {}),
      limit: 50,
      detail: 'timeline',
    });
  }, [getTeamTaskTrace, leadId, traceExecutionId, workflowId]);

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

  const openAction = (operation: string, task?: TeamTaskView) => {
    setActionText(operation === 'modify' ? (task?.mission || task?.description || '') : '');
    setAssignee('');
    setPending({ operation, task, label: operation === 'finish' ? 'Finish team' : actionLabel(operation) });
  };

  if (!connectedLead) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-sm text-fg-muted">
        Connect Task Manager to a team lead’s Tools handle to inspect its tasks.
      </div>
    );
  }

  return (
    <NodePanel
      title="Team Tasks"
      description={`${connectedLead.data?.label || 'Team lead'} · ${status?.status || 'No active team'}`}
      actions={(
        <>
          {(status?.archived_executions?.length || 0) > 0 && (
            <Select value={executionId} onValueChange={setExecutionId}>
              <SelectTrigger className="w-48" aria-label="Execution"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="active">Latest execution</SelectItem>
                {status!.archived_executions!.map((run) => (
                  <SelectItem key={run.execution_id} value={run.execution_id}>{run.label || run.execution_id.slice(0, 12)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button variant="quiet" className={NEUTRAL} onClick={() => void fetchStatus()} disabled={loading}>
            <RefreshCw className={cn(loading && 'animate-spin')} /> Refresh
          </Button>
          {/* The panel's one strong action. */}
          <Button variant="invert" className="px-3.5 font-semibold" disabled={!status?.team_id} onClick={() => openAction('finish')}>
            Finish team
          </Button>
        </>
      )}
      summary={(
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <ToggleGroup
            type="single"
            variant="chips"
            aria-label="Show tasks with status"
            value={filter}
            onValueChange={setFilter}
          >
            {TASK_STATUSES.map((name) => (
              <ToggleGroupItem key={name} value={name} className="capitalize">
                <TaskStatusDot status={name} />
                {/* The space is for a screen reader: "running 2", not "running2". */}
                {name}{' '}
                <span className="text-fg-faint tabular-nums">{counts[name] || 0}</span>
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <span className="ml-auto text-xs whitespace-nowrap text-fg-muted">
            Concurrency <span className="font-medium text-fg-default tabular-nums">{active} / {limit}</span>
          </span>
        </div>
      )}
    >
      <div className="flex min-h-0 flex-1 flex-col p-4">
        <div className="relative mb-3">
          <Search aria-hidden className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-muted" />
          <Input
            className="pl-9"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search tasks, missions, or assignees"
            aria-label="Search tasks"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-border-default bg-bg-elevated">
          <table className="w-full min-w-[1040px] border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-bg-panel text-left text-xs text-fg-muted">
              <tr>
                {COLUMNS.map((label) => <th key={label} scope="col" className={HEAD_CELL}>{label}</th>)}
                <th scope="col" className={HEAD_CELL}><span className="sr-only">Open</span></th>
              </tr>
            </thead>
            <tbody>
              {visible.map((task) => {
                const usage = taskUsage(task);
                return (
                  <tr key={task.id} className="border-b border-border-default last:border-b-0 hover:bg-bg-hover">
                    <td className="max-w-[320px] px-3 py-2.5">
                      <button type="button" className="block w-full text-left" onClick={() => setSelectedId(task.id)}>
                        <span className="block truncate font-medium text-fg-default">{task.title}</span>
                        <span className="block truncate text-xs text-fg-muted">{task.mission || task.description || 'No mission provided'}</span>
                      </button>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="block text-fg-default">{task.assignee_label || agentName(task.assigned_to)}</span>
                      {task.assignee_type && <span className="block text-xs text-fg-muted">{task.assignee_type}</span>}
                    </td>
                    <td className="px-3 py-2.5"><TaskStatus status={task.status} /></td>
                    <td className={TIME_CELL}>{formatTimestamp(task.started_at)}</td>
                    <td className={TIME_CELL}>{formatTimestamp(task.completed_at)}</td>
                    <td className="px-3 py-2.5 text-fg-default tabular-nums">{elapsed(task, nowMs)}</td>
                    <td className="px-3 py-2.5 text-fg-default tabular-nums">
                      {task.current_attempt ?? 0}{task.retry_count ? ` (+${task.retry_count})` : ''}
                    </td>
                    <td className="px-3 py-2.5 tabular-nums">
                      <span className="block font-medium text-fg-default">{usage.total.toLocaleString()}</span>
                      <span className="block text-xs whitespace-nowrap text-fg-muted">{usage.input.toLocaleString()} in · {usage.output.toLocaleString()} out</span>
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      <Button variant="quiet" size="icon-sm" onClick={() => setSelectedId(task.id)} aria-label={`Inspect ${task.title}`}>
                        <Eye />
                      </Button>
                    </td>
                  </tr>
                );
              })}
              {!loading && visible.length === 0 && (
                <tr><td colSpan={COLUMNS.length + 1} className="p-12 text-center text-fg-muted">No tasks match this view.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {pages > 1 && (
          <div className="mt-3 flex items-center justify-end gap-2 text-xs text-fg-muted">
            <Button variant="quiet" size="sm" className={NEUTRAL} disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
            <span className="tabular-nums">{page + 1} / {pages}</span>
            <Button variant="quiet" size="sm" className={NEUTRAL} disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</Button>
          </div>
        )}
      </div>

      <TaskDetailModal
        task={selected}
        nowMs={nowMs}
        agentName={agentName}
        loadTrace={loadTrace}
        onAction={(operation, task) => { setSelectedId(null); openAction(operation, task); }}
        onClose={() => setSelectedId(null)}
      />

      <AlertDialog open={!!pending} onOpenChange={(open) => !open && setPending(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{pending?.label}</AlertDialogTitle>
            <AlertDialogDescription>
              {pending?.task
                ? `Apply this action to “${pending.task.title}”? The latest task revision will be checked.`
                : 'Finish this team only if every required task is resolved.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {pending?.operation === 'modify' && (
            <Textarea value={actionText} onChange={(event) => setActionText(event.target.value)} placeholder="Updated mission" rows={5} />
          )}
          {pending?.operation === 'reassign' && (
            <Select value={assignee} onValueChange={setAssignee}>
              <SelectTrigger><SelectValue placeholder="Choose a connected teammate" /></SelectTrigger>
              <SelectContent>
                {teammates.map((node) => <SelectItem key={node.id} value={node.id}>{node.data?.label || node.type || node.id}</SelectItem>)}
              </SelectContent>
            </Select>
          )}
          {NOTED.includes(pending?.operation || '') && (
            <Textarea value={actionText} onChange={(event) => setActionText(event.target.value)} placeholder="Reason or review note (optional)" rows={3} />
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="invert"
              disabled={busy || (pending?.operation === 'reassign' && !assignee) || (pending?.operation === 'modify' && !actionText.trim())}
              onClick={(event) => { event.preventDefault(); void runAction(); }}
            >
              {busy ? 'Applying…' : 'Confirm'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </NodePanel>
  );
};

export default TaskManagerPanel;
