import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, Users } from 'lucide-react';
import type { Edge, Node } from 'reactflow';

import { useWebSocket } from '@/contexts/WebSocketContext';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/utils';

const LEAD_TYPES = new Set(['orchestrator_agent', 'ai_employee']);
const STATUS_STYLE: Record<string, string> = {
  blocked: 'text-fg-muted', queued: 'text-warning', running: 'text-info',
  submitted: 'text-node-agent', accepted: 'text-success', failed: 'text-destructive',
  cancelled: 'text-fg-muted', working: 'text-info', idle: 'text-success', connected: 'text-success',
};

interface Props { nodeId: string; workflowId?: string; nodes: Node[]; edges: Edge[] }
interface Member { agent_node_id: string; agent_type?: string; label?: string; status?: string }
interface Task { id: string; title: string; status: string; assigned_to?: string; assignee_label?: string; queue_position?: number }

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
      'team.task.accepted', 'team.task.failed', 'team.task.cancelled'].map((event) => addEventListener(event, update));
    return () => removers.forEach((remove) => remove());
  }, [addEventListener, refresh]);

  if (!lead) return <div className="flex h-full items-center justify-center p-8 text-center text-fg-muted">Connect an Orchestrator Agent or AI Employee to Team Monitor.</div>;

  const counts = ['blocked', 'queued', 'running', 'submitted', 'accepted', 'failed', 'cancelled']
    .map((name) => ({ name, value: tasks.filter((task) => task.status === name).length }));

  return <div className="flex h-full min-h-0 flex-col bg-bg-panel">
    <div className="shrink-0 border-b border-border-default px-6 py-4">
      <div className="flex items-center justify-between gap-3">
        <div><h2 className="text-lg font-semibold text-fg-default">Team Monitor</h2><p className="text-sm text-fg-muted">{String((lead.data as any)?.label || lead.type)} · {status?.status || 'Not running'}</p></div>
        <div className="flex items-center gap-2">
          {(status?.archived_executions?.length || 0) > 0 && <Select value={executionId} onValueChange={setExecutionId}><SelectTrigger className="w-48" aria-label="Execution history"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="active">Latest execution</SelectItem>{status!.archived_executions.map((run: any) => <SelectItem key={run.execution_id || run.team_id} value={run.execution_id}>{run.label || String(run.execution_id).slice(0, 12)}</SelectItem>)}</SelectContent></Select>}
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}><RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} /> Refresh</Button>
        </div>
      </div>
      <div className="mt-4 grid grid-cols-4 gap-2 lg:grid-cols-7">
        {counts.map(({ name, value }) => <div key={name} className="rounded-md border border-border-default bg-bg-elevated p-2"><div className="text-xs capitalize text-fg-muted">{name}</div><div className={cn('text-lg font-semibold tabular-nums', STATUS_STYLE[name])}>{value}</div></div>)}
      </div>
    </div>
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-auto p-4 lg:grid-cols-[minmax(220px,0.75fr)_minmax(0,2fr)]">
      <section className="rounded-md border border-border-default bg-bg-elevated">
        <div className="flex items-center gap-2 border-b border-border-default px-4 py-3 font-medium"><Users className="h-4 w-4" /> Connected agents <Badge variant="outline">{members.length}</Badge></div>
        <div className="divide-y divide-border-default/70">{members.map((member) => <div key={member.agent_node_id} className="flex items-center gap-3 px-4 py-3"><span className={cn('h-2 w-2 rounded-full bg-current', STATUS_STYLE[member.status || 'connected'])} /><div className="min-w-0 flex-1"><div className="truncate font-medium text-fg-default">{member.label || member.agent_type}</div><div className="truncate text-xs text-fg-muted">{member.agent_type}</div></div><span className="text-xs capitalize text-fg-muted">{member.status || 'connected'}</span></div>)}{members.length === 0 && <div className="p-6 text-center text-sm text-fg-muted">No teammates connected.</div>}</div>
      </section>
      <section className="min-w-0 rounded-md border border-border-default bg-bg-elevated">
        <div className="border-b border-border-default px-4 py-3 font-medium">Current execution tasks</div>
        <div className="divide-y divide-border-default/70">{tasks.map((task) => <div key={task.id} className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-4 px-4 py-3"><div className="truncate font-medium text-fg-default">{task.title}</div><span className="text-xs text-fg-muted">{task.assignee_label || task.assigned_to || 'Unassigned'}</span><Badge variant="outline" className={cn('capitalize', STATUS_STYLE[task.status])}>{task.status}{task.queue_position ? ` #${task.queue_position}` : ''}</Badge></div>)}{tasks.length === 0 && <div className="p-8 text-center text-sm text-fg-muted">No tasks in this execution.</div>}</div>
      </section>
    </div>
  </div>;
};

export default TeamMonitorPanel;
