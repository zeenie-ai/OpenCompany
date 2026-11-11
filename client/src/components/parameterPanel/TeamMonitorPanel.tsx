/**
 * The Team Monitor's panel: a read-only view of a team lead's teammates and
 * of the tasks in one execution. Connect the lead's output to the node.
 *
 * The head names the lead, switches between executions and counts the tasks
 * by status; the body lists the teammates beside the tasks.
 *
 * Colour marks state only (theme_system.md, "Colour marks state or role"):
 * a status is a dot beside neutral words, here as in the Task Manager.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import type { Edge, Node } from 'reactflow';

import { useWebSocket } from '@/contexts/WebSocketContext';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { StatusTone } from '@/features/home/data/presentation';
import { cn } from '@/lib/utils';
import { NodePanel } from './NodePanel';
import { StatePill } from './StatePill';
import { TASK_STATUSES } from './taskManager/model';
import { TaskStatus, TaskStatusDot } from './taskManager/TaskStatus';

const LEAD_TYPES = new Set(['orchestrator_agent', 'ai_employee']);

const NEUTRAL = 'border-border-default text-fg-default';
const CARD = 'rounded-lg border border-border-default bg-bg-elevated';
const CARD_HEAD = 'flex items-center gap-2 border-b border-border-default px-4 py-2.5 text-sm font-medium text-fg-default';
const COUNT = 'rounded-md border border-border-default bg-bg-panel px-1.75 py-0.5 font-mono text-2xs font-medium text-fg-muted';
const MONO_NOTE = 'truncate font-mono text-2xs text-fg-muted';

/** A teammate's state: working is under way, idle is free for the next task. */
const MEMBER_TONE: Partial<Record<string, StatusTone>> = { working: 'working', idle: 'ready' };

interface Props { nodeId: string; workflowId?: string; nodes: Node[]; edges: Edge[] }
interface Member { agent_node_id: string; agent_type?: string; label?: string; status?: string }
interface Task { id: string; title: string; status: string; assigned_to?: string; assignee_label?: string; queue_position?: number; child_workflow_id?: string; child_run_id?: string; temporal_status?: string; trace_id?: string }

const TeamMonitorPanel: React.FC<Props> = ({ nodeId, workflowId, nodes, edges }) => {
  const { sendRequest, addEventListener } = useWebSocket();
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [executionId, setExecutionId] = useState('active');

  const lead = useMemo(() => {
    const incoming = edges.filter((edge) => edge.target === nodeId);
    return incoming.map((edge) => nodes.find((node) => node.id === edge.source))
      .find((node) => node && LEAD_TYPES.has(node.type || ''));
  }, [edges, nodeId, nodes]);

  const connected = useMemo<Member[]>(() => {
    if (!lead) return [];
    return edges.filter((edge) => edge.target === lead.id && edge.targetHandle === 'input-teammates')
      .map((edge) => nodes.find((node) => node.id === edge.source))
      .filter((node): node is Node => !!node)
      .map((node) => ({
        agent_node_id: node.id, agent_type: node.type,
        label: String((node.data as any)?.label || node.type || 'Agent'), status: 'connected',
      }));
  }, [edges, lead, nodes]);

  const members = useMemo(() => {
    const merged = new Map(connected.map((member) => [member.agent_node_id, member]));
    for (const member of (status?.members || []) as Member[]) {
      if (member.agent_node_id === lead?.id) continue;
      merged.set(member.agent_node_id, { ...merged.get(member.agent_node_id), ...member });
    }
    return [...merged.values()];
  }, [connected, lead?.id, status?.members]);

  const refresh = useCallback(async (quiet = false) => {
    if (!workflowId || !lead) return;
    if (!quiet) setLoading(true);
    try {
      const scope = {
        workflow_id: workflowId,
        team_lead_node_id: lead.id,
        ...(executionId !== 'active' ? { execution_id: executionId } : {}),
      };
      const teamResponse = await sendRequest<{ status?: Record<string, any> }>('get_team_status', scope);
      const resolvedExecutionId = teamResponse?.status?.execution_id;
      const taskResponse = await sendRequest<{ tasks?: Task[] }>('get_team_tasks', {
        ...scope,
        ...(resolvedExecutionId ? { execution_id: resolvedExecutionId } : {}),
      });
      setStatus(teamResponse.status || null);
      setTasks(taskResponse.tasks || []);
    } finally { if (!quiet) setLoading(false); }
  }, [executionId, lead, sendRequest, workflowId]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const update = () => void refresh(true);
    const removers = ['team_event', 'team.task.queued', 'team.task.running', 'team.task.submitted',
      'team.task.accepted', 'team.task.failed', 'team.task.cancelled', 'workflow_control_status',
      'workflow.control.status'].map((event) => addEventListener(event, update));
    return () => removers.forEach((remove) => remove());
  }, [addEventListener, refresh]);

  if (!lead) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-sm text-fg-muted">
        Connect an Orchestrator Agent or AI Employee to Team Monitor.
      </div>
    );
  }

  /** The ids an execution is known by, shortened to fit one line. */
  const references: Array<[string, string]> = [
    ['Generation', String(status?.control_generation ?? status?.generation ?? '—')],
    ['Root execution', status?.root_execution_id ? String(status.root_execution_id).slice(0, 16) : '—'],
    ['Controller', status?.controller_workflow_id ? String(status.controller_workflow_id).slice(0, 20) : '—'],
    ['Temporal state', String(status?.control_state || status?.temporal_status || 'unavailable')],
  ];

  return (
    <NodePanel
      title="Team Monitor"
      description={`${String((lead.data as any)?.label || lead.type)} · ${status?.status || 'Not running'}`}
      actions={(
        <>
          {(status?.archived_executions?.length || 0) > 0 && (
            <Select value={executionId} onValueChange={setExecutionId}>
              <SelectTrigger className="w-48" aria-label="Execution history"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="active">Latest execution</SelectItem>
                {status!.archived_executions.map((run: any) => (
                  <SelectItem key={run.execution_id || run.team_id} value={run.execution_id}>
                    {run.label || String(run.execution_id).slice(0, 12)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button type="button" variant="quiet" className={NEUTRAL} onClick={() => void refresh()} disabled={loading}>
            <RefreshCw className={cn(loading && 'animate-spin')} /> Refresh
          </Button>
        </>
      )}
      summary={(
        <div className="flex flex-col gap-2.5">
          <ul aria-label="Tasks by status" className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-fg-muted">
            {TASK_STATUSES.map((name) => (
              <li key={name} className="flex items-center gap-1.5 capitalize">
                <TaskStatusDot status={name} />
                {name}
                <span className="font-medium text-fg-default tabular-nums">
                  {tasks.filter((task) => task.status === name).length}
                </span>
              </li>
            ))}
          </ul>
          <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-fg-muted">
            {references.map(([label, value]) => (
              <div key={label} className="flex items-baseline gap-1.5">
                <dt>{label}</dt>
                <dd className="font-mono text-fg-default">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    >
      <div className="grid min-h-0 flex-1 grid-cols-1 content-start gap-4 overflow-auto p-4 lg:grid-cols-[minmax(220px,0.75fr)_minmax(0,2fr)]">
        <section className={CARD}>
          <h3 className={CARD_HEAD}>Connected agents <span className={COUNT}>{members.length}</span></h3>
          <ul className="divide-y divide-border-default">
            {members.map((member) => {
              const state = member.status || 'connected';
              return (
                <li key={member.agent_node_id} className="flex items-center gap-3 px-4 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-fg-default">{member.label || member.agent_type}</div>
                    <div className={MONO_NOTE}>{member.agent_type}</div>
                  </div>
                  <StatePill tone={MEMBER_TONE[state] ?? 'paused'} pulse={state === 'working'}>{state}</StatePill>
                </li>
              );
            })}
            {members.length === 0 && <li className="p-6 text-center text-sm text-fg-muted">No teammates connected.</li>}
          </ul>
        </section>

        <section className={cn(CARD, 'min-w-0')}>
          <h3 className={CARD_HEAD}>Current execution tasks <span className={COUNT}>{tasks.length}</span></h3>
          <ul className="divide-y divide-border-default">
            {tasks.map((task) => (
              <li key={task.id} className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-4 px-4 py-2.5">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-fg-default">{task.title}</div>
                  <div className={MONO_NOTE}>
                    {task.child_workflow_id
                      ? `Temporal ${task.child_workflow_id}${task.child_run_id ? ` · ${task.child_run_id.slice(0, 8)}` : ''}`
                      : 'Temporal execution not registered'}
                  </div>
                </div>
                <span className="text-xs text-fg-muted">{task.assignee_label || task.assigned_to || 'Unassigned'}</span>
                <div className="flex flex-col items-end gap-1">
                  <div className="flex items-center gap-1.5">
                    {task.queue_position ? <span className="font-mono text-2xs text-fg-muted">#{task.queue_position}</span> : null}
                    <TaskStatus status={task.status} />
                  </div>
                  {task.temporal_status && <span className="text-2xs text-fg-muted capitalize">{task.temporal_status}</span>}
                </div>
              </li>
            ))}
            {tasks.length === 0 && <li className="p-8 text-center text-sm text-fg-muted">No tasks in this execution.</li>}
          </ul>
        </section>
      </div>
    </NodePanel>
  );
};

export default TeamMonitorPanel;
