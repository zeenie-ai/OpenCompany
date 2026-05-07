import React, { memo, useCallback } from 'react';
import { nodePropsEqual } from './nodeMemoEquality';
import { Handle, Position, NodeProps } from 'reactflow';
import { NodeData, NodeStyle } from '../types/NodeTypes';
import { useAppStore } from '../store/useAppStore';
import { useAppTheme } from '../hooks/useAppTheme';
import { PlayCircle } from 'lucide-react';
import { resolveNodeDescription } from '../lib/nodeSpec';
import EditableNodeLabel from './ui/EditableNodeLabel';

const StartNode: React.FC<NodeProps<NodeData>> = ({ id, type, data, isConnectable, selected }) => {
  const setSelectedNode = useAppStore((s) => s.setSelectedNode);
  const setRenamingNodeId = useAppStore((s) => s.setRenamingNodeId);
  const updateNodeData = useAppStore((s) => s.updateNodeData);
  const theme = useAppTheme();

  const defaultLabel = 'Start';

  // Definition-driven color (Wave 26.A): backend NodeSpec is SSOT for
  // node color. Falls back to dracula cyan if the spec hasn't loaded.
  const definition = resolveNodeDescription(type || '');
  const nodeColor = definition?.defaults?.color || theme.dracula.cyan;

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

  return (
    // `sq-node` + `selected` co-classes activate per-theme square-node
    // decorations (Renaissance wax seal, Steampunk rivets, Edo hanko
    // seal, Surveillance REC LED, Cyber neon underglow). Visual styling
    // (background, border, radius, shadow) lives in base.css
    // `.sq-node-box { ... }` defaults + per-theme overrides and reads
    // `var(--node-color)` for the per-definition accent.
    <div
      className={`sq-node ${selected ? 'selected' : ''}`}
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
      {/* Main Square Node — layout only; visuals live in CSS. */}
      <div
        className="sq-node-box"
        style={{
          position: 'relative',
          width: '60px',
          height: '60px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: theme.colors.text,
          fontSize: '28px',
          fontWeight: '600',
        }}
      >
        {/* Play Icon (color reads from definition-driven nodeColor) */}
        <PlayCircle className="h-7 w-7" style={{ color: nodeColor }} />

        {/* Parameters Button — CSS owns bg/border via .sq-node-gear */}
        <button
          onClick={handleParametersClick}
          className="sq-node-gear"
          style={{
            position: 'absolute',
            top: '-8px',
            right: '-8px',
            width: '16px',
            height: '16px',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '8px',
            fontWeight: '400',
            zIndex: 30,
          }}
          title="Edit Parameters"
        >
          {'⚙️'}
        </button>

        {/* Status Indicator — Start is always "ready" (success bucket).
            CSS owns bg color via per-status rule on .sq-node-pip. */}
        <div
          className="sq-node-pip"
          data-status="success"
          style={{
            position: 'absolute',
            top: '-4px',
            left: '-4px',
            width: '10px',
            height: '10px',
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

        {/* Output Handle — CSS owns bg/border via .sq-node-handle.out */}
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
            width: '8px',
            height: '8px',
            zIndex: 20,
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
