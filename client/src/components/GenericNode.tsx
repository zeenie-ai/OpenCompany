import React, { memo, useCallback } from 'react';
import { nodePropsEqual } from './nodeMemoEquality';
import { Handle, Position, NodeProps } from 'reactflow';
import { resolveNodeDescription } from '../lib/nodeSpec';
import { NodeIcon } from '../assets/icons';
import { useNodeSpec } from '../lib/nodeSpec';
import { NodeData } from '../types/NodeTypes';
import { INodeInputDefinition, INodeOutputDefinition, NodeConnectionType } from '../types/INodeProperties';
import { useAppStore } from '../store/useAppStore';
import { useAppTheme } from '../hooks/useAppTheme';
import { useNodeStatus } from '../contexts/WebSocketContext';
import EditableNodeLabel from './ui/EditableNodeLabel';

const GenericNode: React.FC<NodeProps<NodeData>> = ({ id, type, data, isConnectable, selected }) => {
  const theme = useAppTheme();
  const setSelectedNode = useAppStore((s) => s.setSelectedNode);
  const setRenamingNodeId = useAppStore((s) => s.setRenamingNodeId);
  const updateNodeData = useAppStore((s) => s.updateNodeData);
  const isDisabled = data?.disabled === true;

  // Per-id slice subscription so an unrelated node's status update
  // does not re-render this generic node.
  const nodeStatus = useNodeStatus(id);
  const executionStatus = nodeStatus?.status || 'idle';
  const isExecuting = executionStatus === 'executing' || executionStatus === 'waiting';

  // Wave 6 Phase 3e: backend NodeSpec -> legacy fallback
  const definition = type ? resolveNodeDescription(type) : null;
  // Wave 10.B: reactive spec subscription keeps the icon fresh the
  // moment the prefetch lands (no waiting for parent re-render).
  const iconSpec = useNodeSpec(type);

  const defaultLabel = definition?.displayName || type || '';
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

  if (!type || !definition) {
    return (
      <div style={{
        padding: '8px 12px',
        backgroundColor: '#ef4444',
        color: 'white',
        borderRadius: '8px',
        fontSize: '12px',
        minWidth: '120px',
        textAlign: 'center'
      }}>
        Unknown node type
      </div>
    );
  }
  
  // Helper functions to get inputs/outputs for both enhanced and legacy nodes
  const getNodeInputs = (): INodeInputDefinition[] => {
    if (!definition.inputs) return [];
    
    // Enhanced nodes: array of input objects
    if (definition.inputs.length > 0 && typeof definition.inputs[0] === 'object') {
      return definition.inputs as INodeInputDefinition[];
    }
    
    // Legacy nodes: array of strings - convert to input objects
    return (definition.inputs as string[]).map((input, index) => ({
      name: `input_${index}`,
      displayName: 'Input',
      type: (input as NodeConnectionType) || 'main',
      description: 'Node input connection'
    }));
  };
  
  const getNodeOutputs = (): INodeOutputDefinition[] => {
    if (!definition.outputs) return [];
    
    // Enhanced nodes: array of output objects
    if (definition.outputs.length > 0 && typeof definition.outputs[0] === 'object') {
      return definition.outputs as INodeOutputDefinition[];
    }
    
    // Legacy nodes: array of strings - convert to output objects
    return (definition.outputs as string[]).map((output, index) => ({
      name: `output_${index}`,
      displayName: 'Output',
      type: (output as NodeConnectionType) || 'main',
      description: 'Node output connection'
    }));
  };

  const nodeInputs = getNodeInputs();
  const nodeOutputs = getNodeOutputs();

  // Helper functions for color management
  const getNodeColor = () => definition.defaults.color || '#9E9E9E';
  const getBorderColor = () => {
    const color = getNodeColor();
    if (color.startsWith('#')) {
      const hex = color.substring(1);
      const r = Math.max(0, parseInt(hex.substring(0, 2), 16) - 40);
      const g = Math.max(0, parseInt(hex.substring(2, 4), 16) - 40);
      const b = Math.max(0, parseInt(hex.substring(4, 6), 16) - 40);
      return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
    }
    return color;
  };

  return (
    // `node` + `selected` co-classes activate per-theme generic-node
    // decorations (wax seal on Renaissance, neon LED on Cyber, etc.).
    <div
      className={`node ${selected ? 'selected' : ''}`}
      style={{
        position: 'relative',
        padding: '12px 32px 12px 16px',
        minWidth: '160px',
        minHeight: '60px',
        borderRadius: '12px',
        background: `linear-gradient(135deg, ${getNodeColor()} 0%, ${getBorderColor()} 100%)`,
        border: `2px solid ${isExecuting
          ? (theme.isDarkMode ? theme.dracula.cyan : '#2563eb')
          : selected
            ? '#3b82f6'
            : getBorderColor()}`,
        color: 'white',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        fontSize: '14px',
        fontWeight: '600',
        textAlign: 'center',
        cursor: 'pointer',
        transition: 'all 0.2s ease',
        boxShadow: isExecuting
          ? theme.isDarkMode
            ? `0 4px 12px ${theme.dracula.cyan}66, 0 0 0 3px ${theme.dracula.cyan}4D`
            : `0 0 0 3px rgba(37, 99, 235, 0.5), 0 4px 16px rgba(37, 99, 235, 0.35)`
          : selected
            ? `0 8px 25px ${getNodeColor()}40, 0 0 0 2px ${theme.colors.focus}`
            : theme.isDarkMode
              ? `0 4px 12px ${getNodeColor()}40`
              : `0 2px 8px ${getNodeColor()}25, 0 4px 16px rgba(0, 0, 0, 0.08)`,
        overflow: 'visible',
        opacity: isDisabled ? 0.5 : 1,
        animation: isExecuting ? 'pulse 1.5s ease-in-out infinite' : 'none',
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
          zIndex: 25,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          pointerEvents: 'none',
        }}>
          <span style={{ fontSize: '24px', opacity: 0.8 }}>||</span>
        </div>
      )}
      {/* Input Handles - Multiple handles based on node definition */}
      {nodeInputs.map((input, index) => {
        const totalInputs = nodeInputs.length;
        const topPosition = totalInputs === 1 ? '50%' : 
          `${20 + (60 * index) / Math.max(totalInputs - 1, 1)}%`;
        
        return (
          <Handle
            key={`input-${input.name}-${index}`}
            id={`input-${input.name}`}
            type="target"
            position={Position.Left}
            isConnectable={isConnectable}
            style={{
              position: 'absolute',
              left: '-6px',
              top: topPosition,
              transform: 'translateY(-50%)',
              width: '12px',
              height: '12px',
              backgroundColor: 'rgba(255,255,255,0.9)',
              border: `2px solid ${getBorderColor()}`,
              borderRadius: '50%'
            }}
            title={`${input.displayName}: ${input.description}`}
          />
        );
      })}
      

      {/* Parameters Button */}
      <button
        onClick={handleParametersClick}
        className="absolute top-2 right-2 z-20 flex h-5 w-5 cursor-pointer items-center justify-center rounded-full border-0 bg-white/95 text-[10px] font-semibold shadow-sm transition-all duration-200 hover:scale-115 hover:bg-white hover:shadow-md"
        style={{ color: getNodeColor() }}
        title="Edit Parameters"
      >
        ⚙️
      </button>
      
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '8px',
        position: 'relative',
        zIndex: 10,
        paddingRight: '4px'
      }}>
        <NodeIcon
          icon={iconSpec?.icon ?? definition.icon}
          className="h-6 w-6 text-2xl"
        />
        <EditableNodeLabel
          nodeId={id}
          label={data?.label}
          defaultLabel={defaultLabel}
          onLabelChange={handleLabelChange}
          onActivate={handleLabelActivate}
          className="m-0 max-w-full truncate"
        />
      </div>

      
      {/* Output Handles - Multiple handles based on node definition */}
      {nodeOutputs.map((output, index) => {
        const totalOutputs = nodeOutputs.length;
        const topPosition = totalOutputs === 1 ? '50%' : 
          `${20 + (60 * index) / Math.max(totalOutputs - 1, 1)}%`;
        
        return (
          <Handle
            key={`output-${output.name}-${index}`}
            id={`output-${output.name}`}
            type="source"
            position={Position.Right}
            isConnectable={isConnectable}
            style={{
              position: 'absolute',
              right: '-6px',
              top: topPosition,
              transform: 'translateY(-50%)',
              width: '12px',
              height: '12px',
              backgroundColor: 'rgba(255,255,255,0.9)',
              border: `2px solid ${getBorderColor()}`,
              borderRadius: '50%'
            }}
            title={`${output.displayName}: ${output.description}`}
          />
        );
      })}
    </div>
  );
};

export default memo(GenericNode, nodePropsEqual);