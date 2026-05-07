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
  status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'skipped';
  assigned_to?: string;
}

interface TeamMember {
  agent_node_id: string;
  agent_type: string;
  status: string;
}

interface TeamStatus {
  team_id: string;
  members: TeamMember[];
  tasks: {
    total: number;
    completed: number;
    active: number;
    pending: number;
    failed: number;
  };
  active_tasks: TeamTask[];
}

const TeamMonitorNode: React.FC<NodeProps<NodeData>> = ({ id, type, data, isConnectable, selected }) => {
  const theme = useAppTheme();
  const setSelectedNode = useAppStore((s) => s.setSelectedNode);
  const { sendRequest } = useWebSocket();
  const edges = useEdges();
  const nodes = useNodes();

  const [teamStatus, setTeamStatus] = useState<TeamStatus | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // Wave 6 Phase 3e: backend NodeSpec -> legacy fallback
  const definition = resolveNodeDescription(type || '');
  const nodeColor = definition?.defaults?.color || '#8b5cf6';

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
        team_lead_node_id: connectedTeamLead?.id
      });
      if (response?.status) {
        setTeamStatus({
          team_id: response.status.team_id || teamId || '',
          members: response.status.members || [],
          tasks: {
            total: response.status.task_count || 0,
            completed: response.status.completed_count || 0,
            active: response.status.active_count || 0,
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
  }, [data?.teamId, connectedTeamLead, sendRequest]);

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
      case 'completed': return theme.dracula.green;
      case 'in_progress': return theme.dracula.cyan;
      case 'failed': return theme.dracula.red;
      case 'pending': return theme.dracula.orange;
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
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: 2,
        padding: '6px 4px',
        borderBottom: `1px solid ${theme.colors.border}`,
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 12, fontWeight: 'bold', color: theme.dracula.purple }}>
            {teamStatus?.members?.length || 0}
          </div>
          <div style={{ fontSize: 8, color: theme.colors.textSecondary }}>Team</div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 12, fontWeight: 'bold', color: theme.dracula.cyan }}>
            {teamStatus?.tasks?.total || 0}
          </div>
          <div style={{ fontSize: 8, color: theme.colors.textSecondary }}>Tasks</div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 12, fontWeight: 'bold', color: theme.dracula.green }}>
            {teamStatus?.tasks?.completed || 0}
          </div>
          <div style={{ fontSize: 8, color: theme.colors.textSecondary }}>Done</div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 12, fontWeight: 'bold', color: theme.dracula.orange }}>
            {teamStatus?.tasks?.active || 0}
          </div>
          <div style={{ fontSize: 8, color: theme.colors.textSecondary }}>Active</div>
        </div>
      </div>

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
