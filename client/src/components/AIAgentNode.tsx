import React, { memo, useState, useEffect, useMemo } from 'react';
import { nodePropsEqual } from './nodeMemoEquality';
import { Handle, Position, NodeProps } from 'reactflow';
import { NodeData } from '../types/NodeTypes';
import { useAppStore } from '../store/useAppStore';
import AIAgentExecutionService from '../services/execution/aiAgentExecutionService';
import { useAppTheme } from '../hooks/useAppTheme';
import { useNodeStatus } from '../contexts/WebSocketContext';
import { dracula } from '../styles/theme';
import { useNodeSpec } from '../lib/nodeSpec';
import { NodeIcon } from '../assets/icons';

// LangGraph phase icons and labels. Colors reference the dracula token
// constants so a future palette change in tokens.css propagates without
// editing each phase entry.
const PHASE_CONFIG: Record<string, { icon: string; label: string; color: string }> = {
  initializing: { icon: '⚡', label: 'Initializing', color: dracula.cyan },
  loading_memory: { icon: '💾', label: 'Loading Memory', color: dracula.purple },
  memory_loaded: { icon: '✓', label: 'Memory Ready', color: dracula.green },
  building_tools: { icon: '🔧', label: 'Building Tools', color: dracula.orange },
  building_graph: { icon: '🔗', label: 'Building Graph', color: dracula.orange },
  invoking_llm: { icon: '🧠', label: 'Thinking...', color: dracula.pink },
  executing_tool: { icon: '⚡', label: 'Using Tool', color: dracula.pink },
  tool_completed: { icon: '✓', label: 'Tool Done', color: dracula.green },
  saving_memory: { icon: '💾', label: 'Saving Memory', color: dracula.purple },
};

// Spec-driven handle record. Mirrors server/models/node_metadata.NodeHandle.
interface SpecHandle {
  name: string;
  kind: 'input' | 'output';
  position: 'top' | 'bottom' | 'left' | 'right';
  offset?: string;
  label?: string;
  role?: string;
}

const REACT_POSITION: Record<SpecHandle['position'], Position> = {
  top: Position.Top,
  bottom: Position.Bottom,
  left: Position.Left,
  right: Position.Right,
};

const AIAgentNode: React.FC<NodeProps<NodeData>> = ({ id, type, data, isConnectable, selected }) => {
  const theme = useAppTheme();
  const setSelectedNode = useAppStore((s) => s.setSelectedNode);
  const [_configValid, setConfigValid] = useState(true);
  const [_configErrors, setConfigErrors] = useState<string[]>([]);

  // Wave 10.D: every piece of per-type config comes from the backend
  // NodeSpec envelope (server/nodes/agents.py → register_node()). The
  // component only knows how to render whatever topology the spec
  // declares.
  const spec = useNodeSpec(type || 'aiAgent');
  const handles: SpecHandle[] = (spec?.handles as SpecHandle[] | undefined) ?? [];
  const accentColor = spec?.color || dracula.purple;
  const width = (spec?.uiHints as any)?.width ?? 300;
  const height = (spec?.uiHints as any)?.height ?? 200;
  const title = data?.label || spec?.displayName || type || 'Agent';
  const subtitle = spec?.subtitle ?? '';

  // Partition once; React Flow layout per-position.
  const leftInputs   = useMemo(() => handles.filter(h => h.kind === 'input'  && h.position === 'left'  && h.name !== 'input-main'), [handles]);
  const bottomInputs = useMemo(() => handles.filter(h => h.kind === 'input'  && h.position === 'bottom'), [handles]);
  const rightOutputs = useMemo(() => handles.filter(h => h.kind === 'output' && h.position === 'right'), [handles]);
  const topOutput    = useMemo(() => handles.find(h => h.kind === 'output' && h.position === 'top'), [handles]);
  const hasMainInput = useMemo(() => handles.some(h => h.name === 'input-main' || (h.kind === 'input' && h.position === 'left' && h.role === 'main')), [handles]);

  // Get real-time node status from WebSocket
  const nodeStatus = useNodeStatus(id);
  const isExecuting = nodeStatus?.status === 'executing';
  const currentPhase = nodeStatus?.data?.phase as string | undefined;
  const phaseConfig = currentPhase ? PHASE_CONFIG[currentPhase] : null;

  // Validate configuration whenever data changes
  useEffect(() => {
    try {
      const validation = AIAgentExecutionService.validateConfiguration(data || {});
      setConfigValid(validation.valid);
      setConfigErrors(validation.errors);
    } catch (error) {
      console.error('Configuration validation error:', error);
      setConfigValid(false);
      setConfigErrors(['Configuration validation failed']);
    }
  }, [data, id, title]);

  const handleParametersClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedNode({ id, type, data, position: { x: 0, y: 0 } });
  };

  const getBorderColor = () => {
    if (isExecuting) {
      if (theme.isDarkMode && phaseConfig) return phaseConfig.color;
      return accentColor;
    }
    if (selected) return theme.colors.focus;
    return theme.colors.border;
  };

  const getBoxShadow = () => {
    if (isExecuting) {
      if (theme.isDarkMode && phaseConfig) {
        return `0 0 20px ${phaseConfig.color}80, 0 0 40px ${phaseConfig.color}40`;
      }
      return `0 0 0 3px ${accentColor}80, 0 4px 16px ${accentColor}60`;
    }
    if (selected) {
      return `0 4px 12px ${theme.colors.focusRing}, 0 0 0 1px ${theme.colors.focusRing}`;
    }
    return `0 2px 4px ${theme.colors.shadow}`;
  };

  const hasRightOutputs = rightOutputs.length > 0;
  const hasLeftLabels = hasMainInput || leftInputs.length > 0;

  return (
    // `node` + `node-agent` + `selected` co-classes are the design-handoff
    // structural hooks for per-theme decorations (Renaissance wax seal,
    // Cyber neon underglow + corner LED blink, etc.).
    <div
      className={`node node-agent ${selected ? 'selected' : ''}`}
      style={{
        position: 'relative',
        padding: theme.spacing.lg,
        paddingLeft: hasLeftLabels ? '72px' : theme.spacing.lg,
        paddingRight: hasRightOutputs ? '72px' : theme.spacing.lg,
        minWidth: `${width}px`,
        minHeight: `${height}px`,
        borderRadius: theme.borderRadius.lg,
        background: theme.isDarkMode
          ? `linear-gradient(135deg, ${accentColor}20 0%, ${theme.colors.backgroundAlt} 100%)`
          : `linear-gradient(145deg, #ffffff 0%, ${accentColor}08 100%)`,
        border: `2px solid ${getBorderColor()}`,
        color: theme.colors.text,
        fontSize: theme.fontSize.sm,
        fontWeight: theme.fontWeight.medium,
        textAlign: 'center',
        cursor: 'pointer',
        transition: 'all 0.3s ease',
        boxShadow: getBoxShadow(),
        overflow: 'visible',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: theme.spacing.sm,
        animation: isExecuting ? 'pulse 1.5s ease-in-out infinite' : 'none',
      }}
    >
      {/* Main input (left, top area) — shown when the spec declares a main input */}
      {hasMainInput && (
        <>
          <div style={{
            position: 'absolute', left: '10px', top: '30%', transform: 'translateY(-50%)',
            fontSize: theme.fontSize.sm, color: theme.colors.text,
            fontWeight: theme.fontWeight.medium, pointerEvents: 'none', whiteSpace: 'nowrap',
          }}>Input</div>
          <Handle
            id="input-main"
            type="target"
            position={Position.Left}
            isConnectable={isConnectable}
            style={{
              position: 'absolute', left: '-6px', top: '30%', transform: 'translateY(-50%)',
              width: theme.nodeSize.handle, height: theme.nodeSize.handle,
              backgroundColor: theme.colors.background,
              border: `2px solid ${theme.colors.textSecondary}`, borderRadius: '50%',
            }}
            title="Input"
          />
        </>
      )}

      {/* Parameters gear */}
      <button
        onClick={handleParametersClick}
        style={{
          position: 'absolute', top: theme.spacing.xs, right: theme.spacing.xs,
          width: theme.nodeSize.paramButton, height: theme.nodeSize.paramButton,
          borderRadius: theme.borderRadius.sm, backgroundColor: theme.colors.backgroundAlt,
          border: `1px solid ${theme.colors.border}`, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: theme.fontSize.xs, color: theme.colors.textSecondary,
          fontWeight: theme.fontWeight.normal, transition: theme.transitions.fast, zIndex: 20,
        }}
        title="Edit Parameters"
      >⚙️</button>

      {/* Icon — `color` on the wrapper feeds currentColor to lucide;
          NodeIcon stretches to fill via `h-7 w-7 text-3xl`. */}
      <div style={{ marginBottom: theme.spacing.xs, color: accentColor }}>
        <NodeIcon icon={spec?.icon} className="h-7 w-7 text-3xl" />
      </div>

      {/* Title */}
      <div style={{
        fontSize: theme.fontSize.base, fontWeight: theme.fontWeight.semibold,
        color: theme.colors.text, lineHeight: '1.2', marginBottom: theme.spacing.xs,
      }}>{title}</div>

      {/* Subtitle */}
      <div style={{
        fontSize: theme.fontSize.xs, fontWeight: theme.fontWeight.normal,
        color: isExecuting && phaseConfig ? phaseConfig.color : theme.colors.focus,
        lineHeight: '1.2', marginBottom: theme.spacing.lg, transition: 'color 0.3s ease',
      }}>{isExecuting && phaseConfig ? phaseConfig.label : subtitle}</div>

      {/* Left inputs below the main one (Memory / Task / etc.) */}
      {leftInputs.map(h => (
        <React.Fragment key={h.name}>
          <div style={{
            position: 'absolute', left: '12px', top: h.offset || '50%',
            transform: 'translateY(-50%)', fontSize: theme.fontSize.sm,
            color: theme.colors.text, fontWeight: theme.fontWeight.medium,
            pointerEvents: 'none', whiteSpace: 'nowrap',
          }}>{h.label || h.name}</div>
          <Handle
            id={h.name}
            type="target"
            position={REACT_POSITION[h.position]}
            isConnectable={isConnectable}
            style={{
              position: 'absolute', left: '-6px', top: h.offset || '50%',
              width: theme.nodeSize.handle, height: theme.nodeSize.handle,
              backgroundColor: theme.colors.background,
              border: `2px solid ${theme.colors.textSecondary}`, borderRadius: '0',
              transform: 'translateY(-50%) rotate(45deg)',
            }}
            title={h.label || h.name}
          />
        </React.Fragment>
      ))}

      {/* Bottom input labels */}
      {bottomInputs.map(h => (
        <span key={`label-${h.name}`} style={{
          position: 'absolute', bottom: theme.spacing.lg, left: h.offset || '50%',
          transform: 'translateX(-50%)', fontSize: theme.fontSize.sm,
          color: theme.colors.text, fontWeight: theme.fontWeight.medium, whiteSpace: 'nowrap',
        }}>{h.label || h.name}</span>
      ))}

      {/* Bottom input handles */}
      {bottomInputs.map(h => (
        <Handle
          key={h.name}
          id={h.name}
          type="target"
          position={Position.Bottom}
          isConnectable={isConnectable}
          style={{
            position: 'absolute', bottom: '-6px', left: h.offset || '50%',
            width: theme.nodeSize.handle, height: theme.nodeSize.handle,
            backgroundColor: theme.colors.background,
            border: `2px solid ${theme.colors.textSecondary}`, borderRadius: '0',
            transform: 'translateX(-50%) rotate(45deg)',
          }}
          title={h.label || h.name}
        />
      ))}

      {/* Top output */}
      {topOutput && (
        <Handle
          id={topOutput.name}
          type="source"
          position={Position.Top}
          isConnectable={isConnectable}
          style={{
            position: 'absolute', top: '-6px', left: '50%', transform: 'translateX(-50%)',
            width: theme.nodeSize.handle, height: theme.nodeSize.handle,
            backgroundColor: accentColor,
            border: `2px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
            borderRadius: '50%', zIndex: 20,
          }}
          title={topOutput.label || topOutput.name}
        />
      )}

      {/* Right outputs — rendered whenever the spec ships any, independent
          of whether the top output also exists. Agents that expose both
          a top-position Output (team/delegate) and a right-position
          Output (workflow) need both handles visible. */}
      {hasRightOutputs && rightOutputs.map(h => (
        <React.Fragment key={h.name}>
          <div style={{
            position: 'absolute', right: '14px', top: h.offset || '50%',
            transform: 'translateY(-50%)', fontSize: theme.fontSize.sm,
            color: theme.colors.text, fontWeight: theme.fontWeight.medium,
            pointerEvents: 'none', whiteSpace: 'nowrap', textAlign: 'right',
            lineHeight: 1,
          }}>{h.label || h.name}</div>
          <Handle
            id={h.name}
            type="source"
            position={Position.Right}
            isConnectable={isConnectable}
            style={{
              position: 'absolute', right: '-6px', top: h.offset || '50%',
              transform: 'translateY(-50%)',
              width: theme.nodeSize.handle, height: theme.nodeSize.handle,
              backgroundColor: accentColor,
              border: `2px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
              borderRadius: '50%', zIndex: 20,
            }}
            title={h.label || h.name}
          />
        </React.Fragment>
      ))}

      {/* Single default right output when the spec declares neither a
          right-position nor a top-position output. Keeps the node
          connectable even if the backend omits an explicit output. */}
      {!hasRightOutputs && !topOutput && (
        <Handle
          id="output-main"
          type="source"
          position={Position.Right}
          isConnectable={isConnectable}
          style={{
            position: 'absolute', right: '-6px', top: '50%', transform: 'translateY(-50%)',
            width: theme.nodeSize.handle, height: theme.nodeSize.handle,
            backgroundColor: theme.colors.background,
            border: `2px solid ${theme.colors.textSecondary}`, borderRadius: '50%',
          }}
          title="Main Output"
        />
      )}
    </div>
  );
};

export default memo(AIAgentNode, nodePropsEqual);
