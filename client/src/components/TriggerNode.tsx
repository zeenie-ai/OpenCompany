/**
 * TriggerNode - Visual component for trigger nodes (no input connections)
 *
 * Trigger nodes start workflows and have only output handles.
 * Based on SquareNode design but without input handles.
 *
 * Used for: cronScheduler, webhookTrigger, whatsappReceive, start
 */
import React, { memo, useState, useEffect, useCallback } from 'react';
import { nodePropsEqual } from './nodeMemoEquality';
import { Handle, Position, NodeProps } from 'reactflow';
import { NodeData, NodeStyle } from '../types/NodeTypes';
import { useAppStore } from '../store/useAppStore';
import { resolveNodeDescription, useNodeSpec } from '../lib/nodeSpec';
import { NodeIcon } from '../assets/icons';
import { useAppTheme } from '../hooks/useAppTheme';
import { useWhatsAppStatus, useNodeStatus } from '../contexts/WebSocketContext';
import EditableNodeLabel from './ui/EditableNodeLabel';

const TriggerNode: React.FC<NodeProps<NodeData>> = ({ id, type, data, isConnectable, selected }) => {
  const theme = useAppTheme();
  const setSelectedNode = useAppStore((s) => s.setSelectedNode);
  const setRenamingNodeId = useAppStore((s) => s.setRenamingNodeId);
  const updateNodeData = useAppStore((s) => s.updateNodeData);
  const [isConfigured, setIsConfigured] = useState(false);
  const isDisabled = data?.disabled === true;

  // Per-id slice subscription so unrelated node-status broadcasts do
  // not re-render this trigger.
  const nodeStatus = useNodeStatus(id);
  const executionStatus = nodeStatus?.status || 'idle';

  // Check if this is a WhatsApp trigger
  const isWhatsAppTrigger = type === 'whatsappReceive';
  const whatsappStatus = useWhatsAppStatus();

  // Combine waiting and executing states for glow animation (matching SquareNode pattern)
  // - waiting: Trigger is listening for events (cron scheduled, webhook listening)
  // - executing: Trigger is actively running
  // Both states show the glow animation to indicate active state
  const isExecuting = executionStatus === 'executing' || executionStatus === 'waiting';

  // Wave 6 Phase 3e: backend NodeSpec -> legacy fallback
  const definition = resolveNodeDescription(type || '');

  // Check configuration status
  useEffect(() => {
    const hasRequiredParams = data && Object.keys(data).length > 0;
    setIsConfigured(hasRequiredParams);
  }, [data]);

  const defaultLabel = definition?.displayName || type || '';
  // updateNodeData already merges partial updates onto existing node.data,
  // so passing only { label } avoids depending on the full data object
  // (which gets a fresh ref every parent render).
  const handleLabelChange = useCallback(
    (newLabel: string) => updateNodeData(id, { label: newLabel }),
    [id, updateNodeData]
  );
  const handleLabelActivate = useCallback(
    () => setRenamingNodeId(id),
    [id, setRenamingNodeId]
  );

  const handleParametersClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedNode({ id, type, data, position: { x: 0, y: 0 } });
  };

  // Get status indicator color based on execution state
  const getStatusIndicatorColor = () => {
    // Combined executing/waiting state - show purple with glow
    if (isExecuting) {
      return theme.dracula.purple;
    }
    if (executionStatus === 'success') {
      return theme.dracula.green;
    }
    if (executionStatus === 'error') {
      return theme.dracula.red;
    }

    // WhatsApp trigger - use connection status when idle
    if (isWhatsAppTrigger) {
      if (whatsappStatus.connected) return theme.dracula.green;
      if (whatsappStatus.pairing) return theme.dracula.orange;
      return theme.dracula.red;
    }

    // Idle state - show configured status
    return isConfigured ? theme.dracula.green : theme.dracula.orange;
  };

  const getStatusTitle = () => {
    switch (executionStatus) {
      case 'executing':
        return 'Executing...';
      case 'waiting':
        return nodeStatus?.message || 'Waiting for trigger event...';
      case 'success':
        return 'Trigger fired successfully';
      case 'error':
        return `Error: ${nodeStatus?.data?.error || 'Unknown error'}`;
      default:
        // WhatsApp trigger status
        if (isWhatsAppTrigger) {
          if (whatsappStatus.connected) return 'WhatsApp connected - ready to receive';
          if (whatsappStatus.pairing) return 'Pairing in progress...';
          return 'WhatsApp not connected';
        }
        return isConfigured ? 'Trigger configured and ready' : 'Click to configure trigger';
    }
  };

  // Schema-driven icon dispatch. `useNodeSpec` subscribes to the
  // NodeSpec cache so the icon populates when prefetch lands.
  // <NodeIcon> resolves the ref and tints lucide icons via currentColor.
  const iconSpec = useNodeSpec(type);
  const iconRef = (iconSpec?.icon as string | undefined) ?? (definition?.icon as string | undefined);

  // Get the node color from definition or use default trigger color
  const nodeColor = definition?.defaults?.color || '#f59e0b';

  return (
    // `node` + `node-trigger` + `selected` co-classes activate per-theme
    // trigger-node decorations.
    <div
      className={`node node-trigger ${selected ? 'selected' : ''}`}
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
      {/* Main Trigger Node — visual styling lives in base.css + per-theme
          CSS targeting `.node.node-trigger`. Inner box keeps only layout
          (size, flex), and per-theme decorations like Cyber neon glow,
          Renaissance wax seal, and Steampunk rivets reach the pixels. */}
      <div
        style={{
          position: 'relative',
          width: theme.nodeSize.square,
          height: theme.nodeSize.square,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: theme.colors.text,
          fontSize: theme.nodeSize.squareIcon,
          fontWeight: '600',
          transition: 'all 0.2s ease',
          opacity: isDisabled ? 0.5 : 1,
        }}
      >
        {/* Disabled Overlay */}
        {isDisabled && (
          <div style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(128, 128, 128, 0.4)',
            borderRadius: 'inherit',
            zIndex: 35,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            pointerEvents: 'none',
          }}>
            <span style={{ fontSize: '20px', opacity: 0.8, color: theme.colors.textSecondary }}>||</span>
          </div>
        )}

        {/* Trigger Icon */}
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
          title="Configure Trigger"
        >
          ⚙️
        </button>

        {/* Execution Status Indicator */}
        {/* Status Indicator - glows for both waiting and executing states */}
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
                ? `0 0 6px ${theme.dracula.purple}80`
                : '0 0 4px rgba(37, 99, 235, 0.5)'
              : theme.isDarkMode
                ? `0 1px 2px ${theme.colors.shadow}`
                : '0 1px 3px rgba(0,0,0,0.15)',
            zIndex: 30,
            // Subtle pulse animation for both modes
            animation: isExecuting ? 'pulse 1s ease-in-out infinite' : 'none',
          }}
          title={getStatusTitle()}
        />

        {/* NO INPUT HANDLE - Trigger nodes don't have inputs */}

        {/* Trigger Badge - Lightning bolt indicator on bottom-left */}
        <div
          style={{
            position: 'absolute',
            bottom: '-4px',
            left: '-4px',
            width: theme.nodeSize.outputBadge,
            height: theme.nodeSize.outputBadge,
            borderRadius: theme.borderRadius.sm,
            backgroundColor: theme.dracula.yellow,
            border: `1px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: theme.fontSize.xs,
            zIndex: 30,
            boxShadow: theme.isDarkMode
              ? `0 1px 3px ${theme.colors.shadow}`
              : '0 1px 3px rgba(0,0,0,0.15)',
          }}
          title="Trigger Node - Starts workflow execution"
        >
          <span style={{ lineHeight: 1, color: theme.isDarkMode ? theme.colors.background : '#1a1d21' }}>⚡</span>
        </div>

        {/* Output Handle (right side) */}
        <Handle
          id="output-main"
          type="source"
          position={Position.Right}
          isConnectable={isConnectable}
          style={{
            position: 'absolute',
            right: '-6px',
            top: '50%',
            transform: 'translateY(-50%)',
            width: theme.nodeSize.handle,
            height: theme.nodeSize.handle,
            backgroundColor: isConfigured ? nodeColor : theme.colors.textSecondary,
            border: `2px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
            borderRadius: '50%',
            zIndex: 20
          }}
          title="Trigger Output"
        />

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
            title="Output data available - click node to view"
          >
            <span style={{ lineHeight: 1 }}>D</span>
          </div>
        )}
      </div>

      {/* Trigger Name Below Node */}
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

export default memo(TriggerNode, nodePropsEqual);
