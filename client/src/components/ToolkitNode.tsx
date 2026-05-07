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
import { NodeData, NodeStyle } from '../types/NodeTypes';
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

  // Pip status bucket — CSS owns the visual color via per-status rules.
  // Toolkit's idle state always reads as "success" (it's always ready).
  const pipStatus = (() => {
    if (isExecuting) return 'executing';
    if (executionStatus === 'success') return 'success';
    if (executionStatus === 'error') return 'error';
    return 'success';
  })();

  return (
    // `sq-node` + `selected` co-classes activate per-theme square-node
    // decorations (Renaissance wax seal, Steampunk rivets, Edo hanko
    // seal, Surveillance REC LED, Cyber neon underglow). Visuals
    // (background, border, radius, shadow, executing pulse) live in
    // base.css `.sq-node-box` defaults + per-theme overrides; reads
    // `var(--node-color)` for the per-definition accent.
    <div
      className={`sq-node ${selected ? 'selected' : ''}`}
      data-executing={isExecuting ? '' : undefined}
      style={{
        '--node-color': nodeColor,
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        fontSize: '11px',
        cursor: 'pointer',
      } as NodeStyle}
    >
      {/* Main Node - rectangular for toolkit, square for skills.
          Layout only; visuals live in CSS. */}
      <div
        className="sq-node-box"
        style={{
          position: 'relative',
          width: isToolkitNode ? theme.nodeSize.toolkitWidth : theme.nodeSize.square,
          height: isToolkitNode ? theme.nodeSize.toolkitHeight : theme.nodeSize.square,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: theme.colors.text,
          fontSize: theme.nodeSize.squareIcon,
          fontWeight: '600',
        }}
      >
        {/* Service Icon */}
        <NodeIcon icon={iconRef} className="h-7 w-7 text-3xl" />

        {/* Parameters Button — CSS owns bg/border via .sq-node-gear */}
        <button
          onClick={handleParametersClick}
          className="sq-node-gear"
          style={{
            position: 'absolute',
            top: '-8px',
            right: '-8px',
            width: theme.nodeSize.paramButton,
            height: theme.nodeSize.paramButton,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: theme.fontSize.xs,
            fontWeight: '400',
            transition: theme.transitions.fast,
            zIndex: 30,
          }}
          title="Edit Toolkit Parameters"
        >
          ⚙️
        </button>

        {/* Status Indicator — CSS owns bg/animation via per-status rules. */}
        <div
          className="sq-node-pip"
          data-status={pipStatus}
          style={{
            position: 'absolute',
            top: '-4px',
            left: '-4px',
            width: theme.nodeSize.statusIndicator,
            height: theme.nodeSize.statusIndicator,
            zIndex: 30,
          }}
          title={isExecuting ? 'Executing...' : 'Toolkit ready'}
        />

        {/* TOP Output Handle - connects to AI Agent's tool/skill input */}
        <Handle
          id="output-main"
          type="source"
          position={Position.Top}
          isConnectable={isConnectable}
          className="sq-node-handle out"
          style={{
            position: 'absolute',
            top: '-6px',
            left: '50%',
            transform: 'translateX(-50%)',
            width: theme.nodeSize.handle,
            height: theme.nodeSize.handle,
            zIndex: 20,
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
            className="sq-node-handle in"
            style={{
              position: 'absolute',
              bottom: '-6px',
              left: '50%',
              transform: 'translateX(-50%)',
              width: theme.nodeSize.handle,
              height: theme.nodeSize.handle,
              zIndex: 20,
            }}
            title="Connect Android service nodes"
          />
        )}

        {/* Output Data Indicator (bespoke; keeps inline style) */}
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
