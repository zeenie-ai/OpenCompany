/**
 * ToolkitNode - A node component for skill nodes and toolkit nodes with vertical handle layout.
 *
 * Skill nodes (whatsappSkill, memorySkill, etc.): Square (60x60px)
 *   - Output handle at TOP (connects to agent's skill input)
 *   - No input handle (passive nodes)
 *
 * Toolkit nodes (androidTool): Rectangular (100x60px)
 *   - Output handle at TOP (connects to AI Agent's tool input)
 *   - Input handle at BOTTOM (receives Android service nodes)
 */

import React, { memo, useCallback } from 'react';
import { nodePropsEqual } from './nodeMemoEquality';
import { Handle, Position, NodeProps } from 'reactflow';
import { NodeData } from '../types/NodeTypes';
import { useAppStore } from '../store/useAppStore';
import { resolveNodeDescription } from '../lib/nodeSpec';
import { NodeIcon } from '../assets/icons';
import { useNodeSpec } from '../lib/nodeSpec';
import { useAppTheme } from '../hooks/useAppTheme';
import { useWebSocket } from '../contexts/WebSocketContext';
import EditableNodeLabel from './ui/EditableNodeLabel';

// Toolkit node types that render rectangular (wider than tall)
const TOOLKIT_NODE_TYPES = ['androidTool'];

const ToolkitNode: React.FC<NodeProps<NodeData>> = ({ id, type, data, isConnectable, selected }) => {
  const theme = useAppTheme();
  const setSelectedNode = useAppStore((s) => s.setSelectedNode);
  const setRenamingNodeId = useAppStore((s) => s.setRenamingNodeId);
  const updateNodeData = useAppStore((s) => s.updateNodeData);

  // Get node status from WebSocket context
  const { getNodeStatus } = useWebSocket();
  const nodeStatus = getNodeStatus(id);
  const executionStatus = nodeStatus?.status || 'idle';

  // Wave 6 Phase 3e: backend NodeSpec -> legacy fallback
  const definition = resolveNodeDescription(type || '');

  // Check if this is a toolkit node (rectangular) vs skill node (square)
  const isToolkitNode = type ? TOOLKIT_NODE_TYPES.includes(type) : false;

  // Execution state
  const isExecuting = executionStatus === 'executing' || executionStatus === 'waiting';

  const defaultLabel = definition?.displayName || type || '';
  const handleLabelChange = useCallback(
    (newLabel: string) => updateNodeData(id, { ...data, label: newLabel }),
    [id, data, updateNodeData]
  );
  const handleLabelActivate = useCallback(
    () => setRenamingNodeId(id),
    [id, setRenamingNodeId]
  );

  // Handle parameters click
  const handleParametersClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedNode({ id, type, data, position: { x: 0, y: 0 } });
  };

  // Get the node color from definition or use Android green
  const nodeColor = definition?.defaults?.color || '#3DDC84';

  // Schema-driven icon dispatch with reactive subscription.
  // <NodeIcon> resolves the ref and tints lucide icons via currentColor.
  const iconSpec = useNodeSpec(type);
  const iconRef = (iconSpec?.icon as string | undefined) ?? (definition?.icon as string | undefined);

  // Get status indicator color
  const getStatusIndicatorColor = () => {
    if (isExecuting) return theme.dracula.cyan;
    if (executionStatus === 'success') return theme.dracula.green;
    if (executionStatus === 'error') return theme.dracula.red;
    return theme.dracula.green; // Toolkit is always ready
  };

  return (
    <div
      className={`node ${selected ? 'selected' : ''}`}
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        fontSize: '11px',
        cursor: 'pointer',
      }}
    >
      {/* Main Node - rectangular for toolkit, square for skills */}
      <div
        style={{
          position: 'relative',
          width: isToolkitNode ? theme.nodeSize.toolkitWidth : theme.nodeSize.square,
          height: isToolkitNode ? theme.nodeSize.toolkitHeight : theme.nodeSize.square,
          borderRadius: theme.borderRadius.lg,
          background: theme.isDarkMode
            ? `linear-gradient(135deg, ${nodeColor}25 0%, ${theme.colors.background} 100%)`
            : `linear-gradient(145deg, #ffffff 0%, ${nodeColor}08 100%)`,
          border: `2px solid ${isExecuting
            ? (theme.isDarkMode ? theme.dracula.cyan : '#2563eb')
            : selected
              ? theme.colors.focus
              : theme.isDarkMode ? nodeColor + '80' : `${nodeColor}40`}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: theme.colors.text,
          fontSize: theme.nodeSize.squareIcon,
          fontWeight: '600',
          transition: 'all 0.2s ease',
          boxShadow: isExecuting
            ? theme.isDarkMode
              ? `0 4px 12px ${theme.dracula.cyan}66, 0 0 0 3px ${theme.dracula.cyan}4D`
              : `0 0 0 3px rgba(37, 99, 235, 0.5), 0 4px 16px rgba(37, 99, 235, 0.35)`
            : selected
              ? `0 4px 12px ${theme.colors.focusRing}, 0 0 0 1px ${theme.colors.focusRing}`
              : theme.isDarkMode
                ? `0 2px 8px ${nodeColor}40`
                : `0 2px 8px ${nodeColor}20, 0 4px 12px rgba(0,0,0,0.06)`,
          animation: isExecuting ? 'pulse 1.5s ease-in-out infinite' : 'none',
        }}
      >
        {/* Service Icon */}
        <NodeIcon icon={iconRef} className="h-7 w-7 text-3xl" />

        {/* Parameters Button */}
        <button
          onClick={handleParametersClick}
          style={{
            position: 'absolute',
            top: '-8px',
            right: '-8px',
            width: theme.nodeSize.paramButton,
            height: theme.nodeSize.paramButton,
            borderRadius: theme.borderRadius.sm,
            backgroundColor: theme.isDarkMode ? theme.colors.backgroundAlt : '#ffffff',
            border: `1px solid ${theme.isDarkMode ? theme.colors.border : '#d1d5db'}`,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: theme.fontSize.xs,
            color: theme.colors.textSecondary,
            fontWeight: '400',
            transition: theme.transitions.fast,
            zIndex: 30,
            boxShadow: theme.isDarkMode
              ? `0 1px 3px ${theme.colors.shadow}`
              : '0 1px 4px rgba(0,0,0,0.1)'
          }}
          title="Edit Toolkit Parameters"
        >
          ⚙️
        </button>

        {/* Status Indicator */}
        <div
          style={{
            position: 'absolute',
            top: '-4px',
            left: '-4px',
            width: theme.nodeSize.statusIndicator,
            height: theme.nodeSize.statusIndicator,
            borderRadius: '50%',
            backgroundColor: getStatusIndicatorColor(),
            border: `2px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
            boxShadow: isExecuting
              ? theme.isDarkMode
                ? `0 0 6px ${theme.dracula.cyan}80`
                : '0 0 4px rgba(37, 99, 235, 0.5)'
              : theme.isDarkMode
                ? `0 1px 2px ${theme.colors.shadow}`
                : '0 1px 3px rgba(0,0,0,0.15)',
            zIndex: 30,
            animation: isExecuting ? 'pulse 1s ease-in-out infinite' : 'none',
          }}
          title={isExecuting ? 'Executing...' : 'Toolkit ready'}
        />

        {/* TOP Output Handle - connects to AI Agent's tool/skill input */}
        <Handle
          id="output-main"
          type="source"
          position={Position.Top}
          isConnectable={isConnectable}
          style={{
            position: 'absolute',
            top: '-6px',
            left: '50%',
            transform: 'translateX(-50%)',
            width: theme.nodeSize.handle,
            height: theme.nodeSize.handle,
            backgroundColor: nodeColor,
            border: `2px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
            borderRadius: '50%',
            zIndex: 20
          }}
          title={isToolkitNode ? "Connect to AI Agent's tool input" : "Connect to agent's skill input"}
        />

        {/* BOTTOM Input Handle - only for toolkit nodes (receives Android service nodes) */}
        {isToolkitNode && (
          <Handle
            id="input-services"
            type="target"
            position={Position.Bottom}
            isConnectable={isConnectable}
            style={{
              position: 'absolute',
              bottom: '-6px',
              left: '50%',
              transform: 'translateX(-50%)',
              width: theme.nodeSize.handle,
              height: theme.nodeSize.handle,
              backgroundColor: theme.isDarkMode ? theme.colors.background : '#ffffff',
              border: `2px solid ${theme.isDarkMode ? theme.colors.textSecondary : '#6b7280'}`,
              borderRadius: '50%',
              zIndex: 20
            }}
            title="Connect Android service nodes"
          />
        )}

        {/* Output Data Indicator */}
        {executionStatus === 'success' && nodeStatus?.data && (
          <div
            style={{
              position: 'absolute',
              bottom: '-4px',
              right: '-4px',
              width: theme.nodeSize.outputBadge,
              height: theme.nodeSize.outputBadge,
              borderRadius: theme.borderRadius.sm,
              backgroundColor: theme.dracula.green,
              border: `1px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: theme.fontSize.xs,
              color: 'white',
              fontWeight: 'bold',
              zIndex: 30,
              boxShadow: theme.isDarkMode
                ? '0 1px 3px rgba(0,0,0,0.2)'
                : '0 1px 3px rgba(0,0,0,0.15)',
            }}
            title="Output data available"
          >
            <span style={{ lineHeight: 1 }}>D</span>
          </div>
        )}
      </div>

      {/* Node Name Below */}
      <EditableNodeLabel
        nodeId={id}
        label={data?.label}
        defaultLabel={defaultLabel}
        onLabelChange={handleLabelChange}
        onActivate={handleLabelActivate}
      />
    </div>
  );
};

export default memo(ToolkitNode, nodePropsEqual);
