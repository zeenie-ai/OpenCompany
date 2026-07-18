import React, { memo, useState, useEffect, useCallback, useMemo } from 'react';
import { nodePropsEqual } from './nodeMemoEquality';
import { Handle, Position, NodeProps, useEdges, useNodes } from 'reactflow';
import { NodeData, NodeStyle } from '../types/NodeTypes';
import { useAppStore } from '../store/useAppStore';
import { resolveNodeDescription } from '../lib/nodeSpec';
import { useAppTheme } from '../hooks/useAppTheme';
import { useWebSocket } from '../contexts/WebSocketContext';

// AI Employee node types that can have teams
const TEAM_LEAD_TYPES = ['ai_employee', 'orchestrator_agent'];

interface TeamTask {
  id: string;
  title: string;
  status: 'blocked' | 'queued' | 'running' | 'submitted' | 'accepted' | 'failed' | 'cancelled' | 'pending' | 'in_progress' | 'completed' | 'skipped';
  assigned_to?: string;
}

interface TeamMember {
  agent_node_id: string;
  agent_type: string;
  status: string;
  label?: string;
}

interface TeamStatus {
  team_id: string;
  members: TeamMember[];
  tasks: {
    total: number;
    completed: number;
    active: number;
    queued: number;
    pending: number;
    failed: number;
  };
  active_tasks: TeamTask[];
}

const TeamMonitorNode: React.FC<NodeProps<NodeData>> = ({ id, type, data, isConnectable, selected }) => {
  const theme = useAppTheme();
  const setSelectedNode = useAppStore((s) => s.setSelectedNode);
  const workflowId = useAppStore((s) => s.currentWorkflow?.id);
  const { sendRequest } = useWebSocket();
  const edges = useEdges();
  const nodes = useNodes();

  const [teamStatus, setTeamStatus] = useState<TeamStatus | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // Wave 6 Phase 3e: backend NodeSpec -> legacy fallback
  const definition = resolveNodeDescription(type || '');
  const nodeColor = definition?.defaults?.color || 'var(--node-agent)';

  // Find connected AI Employee/Orchestrator node to get workflow context
  const connectedTeamLead = useMemo(() => {
    // Find edges where this node is the target (input)
    const incomingEdges = edges.filter(e => e.target === id);
    for (const edge of incomingEdges) {
      const sourceNode = nodes.find(n => n.id === edge.source);
      if (sourceNode && TEAM_LEAD_TYPES.includes(sourceNode.type || '')) {
        return sourceNode;
      }
    }
    return null;
  }, [edges, nodes, id]);

  const connectedTeammates = useMemo<TeamMember[]>(() => {
    if (!connectedTeamLead) return [];
    return edges
      .filter((edge) => edge.target === connectedTeamLead.id && edge.targetHandle === 'input-teammates')
      .map((edge) => nodes.find((node) => node.id === edge.source))
      .filter((node): node is NonNullable<typeof node> => !!node)
      .map((node) => ({
        agent_node_id: node.id,
        agent_type: node.type || 'aiAgent',
        label: String((node.data as NodeData | undefined)?.label || node.type || 'Agent'),
        status: 'connected',
      }));
  }, [connectedTeamLead, edges, nodes]);

  const visibleMembers = useMemo(() => {
    const persisted = teamStatus?.members || [];
    const byId = new Map(persisted.map((member) => [member.agent_node_id, member]));
    for (const member of connectedTeammates) {
      byId.set(member.agent_node_id, { ...member, ...(byId.get(member.agent_node_id) || {}) });
    }
    return [...byId.values()].filter((member) => member.agent_node_id !== connectedTeamLead?.id);
  }, [connectedTeamLead?.id, connectedTeammates, teamStatus?.members]);

  // Fetch team status
  const fetchTeamStatus = useCallback(async () => {
    // Use explicit teamId from data, or get from connected node's workflow context
    const teamId = data?.teamId;
    if (!teamId && !connectedTeamLead) return;

    setIsLoading(true);
    try {
      // Request team status - backend will find the active team for the workflow
      const response = await sendRequest<{ status?: any }>('get_team_status', {
        team_id: teamId,
        workflow_id: workflowId,
        team_lead_node_id: connectedTeamLead?.id,
      });
      if (response?.status) {
        setTeamStatus({
          team_id: response.status.team_id || teamId || '',
          members: response.status.members || [],
          tasks: {
            total: response.status.task_count || 0,
            completed: response.status.completed_count || 0,
            active: response.status.active_count || 0,
            queued: response.status.queued_count ?? response.status.pending_count ?? 0,
            pending: response.status.pending_count || 0,
            failed: response.status.failed_count || 0,
          },
          active_tasks: response.status.active_tasks || [],
        });
      }
    } catch (error) {
      console.error('Failed to fetch team status:', error);
    } finally {
      setIsLoading(false);
    }
  }, [data?.teamId, connectedTeamLead, workflowId, sendRequest]);

  // Auto-refresh when connected to a team lead
  useEffect(() => {
    const interval = data?.refreshInterval || 2000;
    if (interval > 0 && (data?.teamId || connectedTeamLead)) {
      fetchTeamStatus();
      const timer = setInterval(fetchTeamStatus, interval);
      return () => clearInterval(timer);
    }
  }, [data?.refreshInterval, data?.teamId, connectedTeamLead, fetchTeamStatus]);

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedNode({ id, type, data, position: { x: 0, y: 0 } });
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'accepted':
      case 'completed': return 'var(--success)';
      case 'submitted': return 'var(--node-agent)';
      case 'running':
      case 'in_progress': return 'var(--info)';
      case 'failed': return 'var(--destructive)';
      case 'blocked':
      case 'queued':
      case 'pending': return 'var(--warning)';
      default: return theme.colors.textSecondary;
    }
  };

  return (
    // Visual styling (background, border, radius) lives in base.css `.node`
    // defaults + per-theme overrides; reads `var(--node-color)` for accent.
    <div
      className={`node ${selected ? 'selected' : ''}`}
      onClick={handleClick}
      style={{
        '--node-color': nodeColor,
        width: 200,
        minHeight: 120,
        fontFamily: 'system-ui, -apple-system, sans-serif',
        fontSize: 10,
        overflow: 'hidden',
      } as NodeStyle}
    >
      {/* Header */}
      <div style={{
        padding: '6px 8px',
        borderBottom: `1px solid ${theme.colors.border}`,
        background: `${nodeColor}20`,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
      }}>
        <span style={{ fontSize: 14 }}>📊</span>
        <span style={{ fontWeight: 600, color: theme.colors.text, fontSize: 11 }}>
          {data?.label || 'Team Monitor'}
        </span>
      </div>

      {/* Stats Grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(5, 1fr)',
        gap: 2,
        padding: '6px 4px',
        borderBottom: `1px solid ${theme.colors.border}`,
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 12, fontWeight: 'bold', color: 'var(--node-agent)' }}>
            {visibleMembers.length}
          </div>
          <div style={{ fontSize: 8, color: theme.colors.textSecondary }}>Team</div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 12, fontWeight: 'bold', color: 'var(--info)' }}>
            {teamStatus?.tasks?.total || 0}
          </div>
          <div style={{ fontSize: 8, color: theme.colors.textSecondary }}>Tasks</div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 12, fontWeight: 'bold', color: 'var(--success)' }}>
            {teamStatus?.tasks?.completed || 0}
          </div>
          <div style={{ fontSize: 8, color: theme.colors.textSecondary }}>Done</div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 12, fontWeight: 'bold', color: 'var(--warning)' }}>
            {teamStatus?.tasks?.active || 0}
          </div>
          <div style={{ fontSize: 8, color: theme.colors.textSecondary }}>Active</div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 12, fontWeight: 'bold', color: 'var(--warning)' }}>
            {teamStatus?.tasks?.queued || 0}
          </div>
          <div style={{ fontSize: 8, color: theme.colors.textSecondary }}>Queued</div>
        </div>
      </div>

      {/* Connected teammates */}
      {visibleMembers.length > 0 && (
        <div style={{ padding: '4px 6px', borderBottom: `1px solid ${theme.colors.border}` }}>
          {visibleMembers.slice(0, 4).map((member) => (
            <div key={member.agent_node_id} title={member.agent_type} style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '2px 0', fontSize: 9 }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', backgroundColor: member.status === 'working' ? 'var(--info)' : 'var(--success)', flexShrink: 0 }} />
              <span style={{ color: theme.colors.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{member.label || member.agent_type}</span>
              <span style={{ marginLeft: 'auto', color: theme.colors.textSecondary }}>{member.status}</span>
            </div>
          ))}
          {visibleMembers.length > 4 && <div style={{ color: theme.colors.textSecondary, fontSize: 8 }}>+{visibleMembers.length - 4} more</div>}
        </div>
      )}

      {/* Active Tasks */}
      <div style={{ padding: '4px 6px', maxHeight: 80, overflow: 'auto' }}>
        {!data?.teamId && !connectedTeamLead ? (
          <div style={{ color: theme.colors.textSecondary, textAlign: 'center', padding: 8, fontSize: 9 }}>
            Connect to AI Employee
          </div>
        ) : isLoading && !teamStatus ? (
          <div style={{ color: theme.colors.textSecondary, textAlign: 'center', padding: 8, fontSize: 9 }}>
            Loading...
          </div>
        ) : teamStatus?.active_tasks && teamStatus.active_tasks.length > 0 ? (
          teamStatus.active_tasks.slice(0, 3).map((task) => (
            <div key={task.id} style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              padding: '2px 0',
              fontSize: 9,
            }}>
              <span style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                backgroundColor: getStatusColor(task.status),
                flexShrink: 0,
              }} />
              <span style={{
                color: theme.colors.text,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                flex: 1,
              }}>
                {task.title}
              </span>
            </div>
          ))
        ) : (
          <div style={{ color: theme.colors.textSecondary, textAlign: 'center', padding: 4, fontSize: 9 }}>
            No active tasks
          </div>
        )}
      </div>

      {/* Input Handle — visual styling owned by `.node-handle.in`
          in base.css + per-theme overrides. Inline keeps layout only. */}
      <Handle
        id="input-team"
        type="target"
        position={Position.Left}
        isConnectable={isConnectable}
        className="node-handle in"
        style={{
          left: -6,
          top: '50%',
          width: 10,
          height: 10,
          borderRadius: '50%',
        }}
      />

      {/* Output Handle — visual styling owned by `.node-handle.out`. */}
      <Handle
        id="output-main"
        type="source"
        position={Position.Right}
        isConnectable={isConnectable}
        className="node-handle out"
        style={{
          right: -6,
          top: '50%',
          width: 10,
          height: 10,
          borderRadius: '50%',
        }}
      />
    </div>
  );
};

export default memo(TeamMonitorNode, nodePropsEqual);
