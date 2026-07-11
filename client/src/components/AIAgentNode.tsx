/* eslint-disable react-hooks/exhaustive-deps -- ``handles`` derived from spec.handles; spec is a stable React-Query slice, dep list omits it intentionally. */
import React, { memo, useState, useEffect, useMemo, useCallback } from 'react';
import { nodePropsEqual } from './nodeMemoEquality';
import { Handle, Position, NodeProps } from 'reactflow';
import { NodeData, NodeStyle } from '../types/NodeTypes';
import { useAppStore } from '../store/useAppStore';
import AIAgentExecutionService from '../services/execution/aiAgentExecutionService';
import { useAppTheme } from '../hooks/useAppTheme';
import { useNodeStatus } from '../contexts/WebSocketContext';
import { useNodeSpec } from '../lib/nodeSpec';
import { NodeIcon } from '../assets/icons';
import { Badge } from '@/components/ui/badge';
import EditableNodeLabel from './ui/EditableNodeLabel';

// Agent-loop phase icons and labels. Colors reference the semantic theme
// tokens (var(--*)) so each phase label recolors per active theme — init=info,
// memory=agent-purple, building=warning, thinking/tool=trigger-pink, done=success.
const PHASE_CONFIG: Record<string, { icon: string; label: string; color: string }> = {
  initializing: { icon: '⚡', label: 'Initializing', color: 'var(--info)' },
  loading_memory: { icon: '💾', label: 'Loading Memory', color: 'var(--node-agent)' },
  memory_loaded: { icon: '✓', label: 'Memory Ready', color: 'var(--success)' },
  building_tools: { icon: '🔧', label: 'Building Tools', color: 'var(--warning)' },
  building_graph: { icon: '🔗', label: 'Building Graph', color: 'var(--warning)' },
  invoking_llm: { icon: '🧠', label: 'Thinking...', color: 'var(--node-trigger)' },
  executing_tool: { icon: '⚡', label: 'Using Tool', color: 'var(--node-trigger)' },
  tool_completed: { icon: '✓', label: 'Tool Done', color: 'var(--success)' },
  saving_memory: { icon: '💾', label: 'Saving Memory', color: 'var(--node-agent)' },
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
  const setRenamingNodeId = useAppStore((s) => s.setRenamingNodeId);
  const updateNodeData = useAppStore((s) => s.updateNodeData);
  const [_configValid, setConfigValid] = useState(true);
  const [_configErrors, setConfigErrors] = useState<string[]>([]);

  // Wave 10.D: every piece of per-type config comes from the backend
  // NodeSpec envelope (server/nodes/agents.py → register_node()). The
  // component only knows how to render whatever topology the spec
  // declares.
  const spec = useNodeSpec(type || 'aiAgent');
  const handles: SpecHandle[] = (spec?.handles as SpecHandle[] | undefined) ?? [];
  const accentColor = spec?.color || 'var(--node-agent)';
  const width = (spec?.uiHints as any)?.width ?? 300;
  const height = (spec?.uiHints as any)?.height ?? 200;
  const defaultLabel = spec?.displayName || type || 'Agent';
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
  // Live agent-loop counter. Backed by the `agent_progress` CloudEvents
  // broadcast (services/ai.py:_run_agent_loop emits one per iteration via
  // the progress_callback). `iteration` advances per turn; `max_iterations`
  // mirrors the loop's hard cap (llm_defaults.json:agent.recursion_limit).
  const iteration = nodeStatus?.data?.iteration as number | undefined;
  const maxIterations = nodeStatus?.data?.max_iterations as number | undefined;
  const showIterationBadge =
    isExecuting && typeof iteration === 'number' && typeof maxIterations === 'number';

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
  }, [data, id]);

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

  const hasRightOutputs = rightOutputs.length > 0;
  const hasLeftLabels = hasMainInput || leftInputs.length > 0;

  return (
    // `node` + `node-agent` + `selected` co-classes are the design-handoff
    // structural hooks for per-theme decorations (Renaissance wax seal,
    // Cyber neon underglow + corner LED blink, etc.).
    <div
      className={`node node-agent ${selected ? 'selected' : ''}`}
      data-executing={isExecuting ? '' : undefined}
      style={{
        '--node-color': accentColor,
        position: 'relative',
        padding: theme.spacing.lg,
        paddingLeft: hasLeftLabels ? '72px' : theme.spacing.lg,
        paddingRight: hasRightOutputs ? '72px' : theme.spacing.lg,
        // Width is fixed (not just min) so long titles like
        // "PRODUCTIVITY AGENT" wrap inside the bordered card
        // instead of growing the node and bleeding past the border.
        width: `${width}px`,
        minHeight: `${height}px`,
        color: theme.colors.text,
        fontSize: theme.fontSize.sm,
        fontWeight: theme.fontWeight.medium,
        textAlign: 'center',
        cursor: 'pointer',
        transition: 'transform 0.3s ease, border-color 0.3s ease, opacity 0.3s ease',
        overflow: 'visible',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: theme.spacing.sm,
      } as NodeStyle}
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
            className="node-handle in"
            style={{
              position: 'absolute', left: '-6px', top: '30%', transform: 'translateY(-50%)',
              width: theme.nodeSize.handle, height: theme.nodeSize.handle,
              borderRadius: '50%',
            }}
            title="Input"
          />
        </>
      )}

      {/* Live agent-loop iteration counter (e.g. "12 / 500"). shadcn
          Badge primitive with `outline` variant — picks up `--border`
          and `--foreground` from whichever theme is active. The
          per-node-type accent rides via the `--node-color` custom
          property already set on the parent at line ~105 (one of the
          two grandfathered inline-style channels for canvas nodes per
          CLAUDE.md). `tabular-nums` keeps "1 / 500" -> "12 / 500" from
          jittering as digits widen. */}
      {showIterationBadge && (
        <Badge
          variant="outline"
          title={`Iteration ${iteration} of ${maxIterations} (agent.recursion_limit)`}
          className="absolute top-1 left-1 z-20 tabular-nums pointer-events-none"
          style={{
            color: 'var(--node-color)',
            borderColor: 'var(--node-color)',
          }}
        >
          {iteration} / {maxIterations}
        </Badge>
      )}

      {/* Parameters gear */}
      <button
        onClick={handleParametersClick}
        className="node-gear"
        style={{
          position: 'absolute', top: theme.spacing.xs, right: theme.spacing.xs,
          width: theme.nodeSize.paramButton, height: theme.nodeSize.paramButton,
          cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: theme.fontSize.xs,
          fontWeight: theme.fontWeight.normal, zIndex: 20,
        }}
        title="Edit Parameters"
      >⚙️</button>

      {/* Icon — `color` on the wrapper feeds currentColor to lucide;
          NodeIcon stretches to fill via `h-7 w-7 text-3xl`. */}
      <div style={{ marginBottom: theme.spacing.xs, color: accentColor }}>
        <NodeIcon icon={spec?.icon} className="h-7 w-7 text-3xl" />
      </div>

      {/* Title — alignSelf:stretch + width:100% forces the flex child
          to fill the parent's content area (parent is align-items:center
          which would otherwise size the child to its content). Inline
          rename rides the shared EditableNodeLabel (F2 / double-click /
          context-menu Rename, coordinated via useAppStore.renamingNodeId);
          the persisted name lives in node.data.label like every other
          renameable node. */}
      <div style={{
        alignSelf: 'stretch',
        width: '100%',
        marginBottom: theme.spacing.xs,
      }}>
        <EditableNodeLabel
          nodeId={id}
          label={data?.label}
          defaultLabel={defaultLabel}
          onLabelChange={handleLabelChange}
          onActivate={handleLabelActivate}
          className="m-0 w-full max-w-full text-base font-semibold break-words whitespace-normal"
        />
      </div>

      {/* Subtitle. Phase color is JS-driven (PHASE_CONFIG[phase].color) so
          we keep the inline color while still exposing `node-sub` so themes
          can style the resting subtitle. */}
      <div className="node-sub" style={{
        alignSelf: 'stretch',
        width: '100%',
        fontSize: theme.fontSize.xs, fontWeight: theme.fontWeight.normal,
        color: isExecuting && phaseConfig ? phaseConfig.color : theme.colors.focus,
        lineHeight: '1.2', marginBottom: theme.spacing.lg, transition: 'color 0.3s ease',
        overflowWrap: 'break-word', wordBreak: 'break-word', whiteSpace: 'normal',
        textAlign: 'center',
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
            className="node-handle in"
            style={{
              position: 'absolute', left: '-6px', top: h.offset || '50%',
              width: theme.nodeSize.handle, height: theme.nodeSize.handle,
              borderRadius: '0',
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
          className="node-handle in"
          style={{
            position: 'absolute', bottom: '-6px', left: h.offset || '50%',
            width: theme.nodeSize.handle, height: theme.nodeSize.handle,
            borderRadius: '0',
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
          className="node-handle out"
          style={{
            position: 'absolute', top: '-6px', left: '50%', transform: 'translateX(-50%)',
            width: theme.nodeSize.handle, height: theme.nodeSize.handle,
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
            className="node-handle out"
            style={{
              position: 'absolute', right: '-6px', top: h.offset || '50%',
              transform: 'translateY(-50%)',
              width: theme.nodeSize.handle, height: theme.nodeSize.handle,
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
          className="node-handle out"
          style={{
            position: 'absolute', right: '-6px', top: '50%', transform: 'translateY(-50%)',
            width: theme.nodeSize.handle, height: theme.nodeSize.handle,
            borderRadius: '50%',
          }}
          title="Main Output"
        />
      )}
    </div>
  );
};

export default memo(AIAgentNode, nodePropsEqual);
