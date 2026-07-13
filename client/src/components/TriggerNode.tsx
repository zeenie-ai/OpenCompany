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

  // Triggers have two DISTINCT active visuals (design-system animations.css):
  // - waiting   -> .opencompany-trigger-armed: a gentle continuous "listening"
  //                heartbeat while deployed and waiting for events, paired
  //                with the .opencompany-bolt ⚡ badge pulse.
  // - executing -> the one-shot execution pulse (base.css token-driven rule,
  //                bound via data-executing on the wrapper).
  const isExecuting = executionStatus === 'executing';
  const isArmed = executionStatus === 'waiting';

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

  // Compute pip status bucket for `data-status` attribute. CSS owns the
  // visual color via per-status rules in base.css and per-theme overrides.
  // For WhatsApp triggers in idle state, fold connection state into the
  // bucket: connected -> success, pairing -> waiting, otherwise -> error.
  const pipStatus = (() => {
    if (executionStatus === 'executing' || executionStatus === 'waiting') return 'executing';
    if (executionStatus === 'success') return 'success';
    if (executionStatus === 'error') return 'error';
    if (isWhatsAppTrigger) {
      if (whatsappStatus.connected) return 'success';
      if (whatsappStatus.pairing) return 'waiting';
      return 'error';
    }
    return isConfigured ? 'success' : 'idle';
  })();

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
  const nodeColor = definition?.defaults?.color || 'var(--node-trigger)';

  return (
    // `sq-node` + `node-trigger` + `selected` co-classes activate per-theme
    // square-node decorations (Renaissance wax seal, Steampunk rivets, Edo
    // hanko seal, Surveillance REC LED, Cyber neon underglow). The
    // `node-trigger` co-class lets per-type theme rules narrow further.
    // `data-executing` binds the CSS pulse animation owned by base.css.
    <div
      className={`sq-node node-trigger ${selected ? 'selected' : ''}`}
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
      {/* Main Trigger Node — visual styling lives in base.css + per-theme
          CSS targeting `.sq-node-box`. Inner box keeps only layout
          (size, flex), and per-theme decorations like Cyber neon glow,
          Renaissance wax seal, and Steampunk rivets reach the pixels. */}
      <div
        className={`sq-node-box ${isArmed ? 'opencompany-trigger-armed' : ''}`}
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
            backgroundColor: 'color-mix(in srgb, var(--fg-faint) 40%, transparent)',
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
          title="Configure Trigger"
        >
          ⚙️
        </button>

        {/* Status Indicator — CSS owns the bg color via per-status rule
            on `.sq-node-pip[data-status="…"]`. Pulse on executing/waiting
            also lives in CSS. */}
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
          title={getStatusTitle()}
        />

        {/* NO INPUT HANDLE - Trigger nodes don't have inputs */}

        {/* Trigger Badge - Lightning bolt indicator on bottom-left.
            `.opencompany-bolt` adds the soft armed-pulse (design-system
            animations.css) while the trigger is listening for events. */}
        <div
          className={isArmed ? 'opencompany-bolt' : undefined}
          style={{
            position: 'absolute',
            bottom: '-4px',
            left: '-4px',
            width: theme.nodeSize.outputBadge,
            height: theme.nodeSize.outputBadge,
            borderRadius: theme.borderRadius.sm,
            backgroundColor: 'var(--warning)',
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

        {/* Output Handle (right side) — CSS owns bg/border via .sq-node-handle.out */}
        <Handle
          id="output-main"
          type="source"
          position={Position.Right}
          isConnectable={isConnectable}
          className="sq-node-handle out"
          style={{
            position: 'absolute',
            right: '-6px',
            top: '50%',
            transform: 'translateY(-50%)',
            width: theme.nodeSize.handle,
            height: theme.nodeSize.handle,
            zIndex: 20,
          }}
          title="Trigger Output"
        />

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
              backgroundColor: 'var(--success)',
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
