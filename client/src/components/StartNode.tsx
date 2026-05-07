import React, { memo, useCallback } from 'react';
import { nodePropsEqual } from './nodeMemoEquality';
import { Handle, Position, NodeProps } from 'reactflow';
import { NodeData } from '../types/NodeTypes';
import { useAppStore } from '../store/useAppStore';
import { useAppTheme } from '../hooks/useAppTheme';
import { PlayCircle } from 'lucide-react';
import EditableNodeLabel from './ui/EditableNodeLabel';

const StartNode: React.FC<NodeProps<NodeData>> = ({ id, type, data, isConnectable, selected }) => {
  const setSelectedNode = useAppStore((s) => s.setSelectedNode);
  const setRenamingNodeId = useAppStore((s) => s.setRenamingNodeId);
  const updateNodeData = useAppStore((s) => s.updateNodeData);
  const theme = useAppTheme();

  const defaultLabel = 'Start';

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

  const nodeColor = theme.dracula.cyan; // Cyan color for start node (neutral "begin" color)

  return (
    // `node` + `selected` co-classes activate per-theme node decorations.
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
      {/* Main Square Node */}
      <div
        style={{
          position: 'relative',
          width: '60px',
          height: '60px',
          borderRadius: '8px',
          background: theme.isDarkMode
            ? `linear-gradient(135deg, ${nodeColor}20 0%, ${theme.colors.background} 100%)`
            : `linear-gradient(145deg, #ffffff 0%, ${nodeColor}10 100%)`,
          border: `2px solid ${selected
            ? theme.colors.focus
            : theme.isDarkMode ? `${nodeColor}60` : `${nodeColor}50`}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: theme.colors.text,
          fontSize: '28px',
          fontWeight: '600',
          transition: 'all 0.2s ease',
          boxShadow: selected
            ? `0 4px 12px ${theme.colors.focusRing}, 0 0 0 1px ${theme.colors.focusRing}`
            : theme.isDarkMode
              ? `0 2px 8px ${nodeColor}30`
              : `0 2px 8px ${nodeColor}25, 0 4px 12px rgba(0,0,0,0.06)`,
        }}
      >
        {/* Play Icon */}
        <PlayCircle className="h-7 w-7" style={{ color: nodeColor }} />

        {/* Parameters Button */}
        <button
          onClick={handleParametersClick}
          style={{
            position: 'absolute',
            top: '-8px',
            right: '-8px',
            width: '16px',
            height: '16px',
            borderRadius: '3px',
            backgroundColor: theme.isDarkMode ? theme.colors.backgroundAlt : '#ffffff',
            border: `1px solid ${theme.isDarkMode ? theme.colors.border : `${nodeColor}40`}`,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '8px',
            color: theme.colors.textSecondary,
            fontWeight: '400',
            transition: 'all 0.2s ease',
            zIndex: 30,
            boxShadow: theme.isDarkMode
              ? `0 1px 3px ${theme.colors.shadow}`
              : `0 1px 4px ${nodeColor}20`
          }}
          title="Edit Parameters"
        >
          {'\u2699\uFE0F'}
        </button>

        {/* Status Indicator - always green for start */}
        <div
          style={{
            position: 'absolute',
            top: '-4px',
            left: '-4px',
            width: '10px',
            height: '10px',
            borderRadius: '50%',
            backgroundColor: nodeColor,
            border: `2px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
            boxShadow: theme.isDarkMode
              ? `0 1px 2px ${theme.colors.shadow}`
              : '0 1px 3px rgba(0,0,0,0.15)',
            zIndex: 30,
          }}
          title="Workflow start point"
        />

        {/* Input Handle - hidden but present for consistency */}
        <Handle
          id="input-main"
          type="target"
          position={Position.Left}
          isConnectable={false}
          style={{
            visibility: 'hidden',
            width: '1px',
            height: '1px',
          }}
        />

        {/* Output Handle */}
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
            width: '8px',
            height: '8px',
            backgroundColor: nodeColor,
            border: `2px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
            borderRadius: '50%',
            zIndex: 20
          }}
          title="Workflow Output"
        />
      </div>

      {/* Name Below Square */}
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

export default memo(StartNode, nodePropsEqual);
