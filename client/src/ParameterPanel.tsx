import React from 'react';
import Modal from './components/ui/Modal';
import ParameterPanelLayout from './components/parameterPanel/ParameterPanelLayout';
import { useParameterPanel } from './hooks/useParameterPanel';
import { useAppStore } from './store/useAppStore';
import { useWebSocket } from './contexts/WebSocketContext';
import { ExecutionService, ExecutionResult } from './services/executionService';
import { NodeIcon } from './assets/icons';
import { theme } from './styles/theme';
import { nodeExecutionReducer } from './utils/parameterPanelExecutionState';
import { deriveCanvasLock } from './lib/canvasLock';
import { Play, Save, X } from 'lucide-react';
import { Button } from '@/components/ui/button';

// Colour marks state or role (docs-internal/theme_system.md): the header's
// buttons are neutral outlines, and Save, the panel's one strong action, is
// `invert`. The shape is the editor toolbar's.
const NEUTRAL_ACTION = 'rounded-md border-border-default px-3.5 text-[13px] font-semibold text-fg-default';
const ACTION_SHAPE = 'rounded-md px-3.5 text-[13px] font-semibold';

const ParameterPanel: React.FC = () => {
  const {
    selectedNode,
    nodeDefinition,
    parameters,
    hasUnsavedChanges,
    handleParameterChange,
    handleSave,
    handleCancel,
    isLoading,
  } = useParameterPanel();

  const currentWorkflow = useAppStore((s) => s.currentWorkflow);
  const { executeNode, getNodeParameters, clearNodeStatus, cancelEventWait, getNodeStatus, workflowControlStatuses, workflowLock } = useWebSocket();

  // Server-owned canvas-edit capability: parameter saves mutate the
  // workflow's persisted configuration, so they follow the same
  // `can_edit` capability the canvas gates on.
  const canvasLock = deriveCanvasLock({
    control: currentWorkflow?.id ? workflowControlStatuses[currentWorkflow.id] : null,
    legacyLock: workflowLock,
    workflowId: currentWorkflow?.id ?? null,
  });

  // Get current node status to check if waiting
  const nodeStatus = selectedNode ? getNodeStatus(selectedNode.id) : null;
  const isWaiting = nodeStatus?.status === 'waiting';

  // Execution state is owned by the physical node. A run may remain in
  // flight while the user inspects or starts another node, so a panel-wide
  // boolean/result list would let one node block or overwrite another.
  const [executionByNode, dispatchExecution] = React.useReducer(nodeExecutionReducer, {});

  const selectedExecution = selectedNode
    ? executionByNode[selectedNode.id] ?? { running: false, results: [] }
    : { running: false, results: [] };
  

  const handleModalClose = () => {
    handleCancel();
  };

  // Execute the current node
  const handleRun = async () => {
    if (!selectedNode || !nodeDefinition) return;

    // Capture identity before the first await. Selecting another node while
    // this run is pending must not redirect its result or finally block.
    const runNode = selectedNode;
    const runDefinition = nodeDefinition;
    const runNodeId = runNode.id;
    
    dispatchExecution({ type: 'start', nodeId: runNodeId });

    try {
      // Save parameters first if there are unsaved changes. The per-node run
      // flag is already set so a second click cannot start an overlapping run
      // while this await is pending.
      if (hasUnsavedChanges) {
        await handleSave();
      }

      
      // Get current workflow nodes and edges from app store
      const nodes = currentWorkflow?.nodes || [];
      const edges = currentWorkflow?.edges || [];
      
      // Execute node via WebSocket
      const result: ExecutionResult = await ExecutionService.executeNodeViaWebSocket(
        runNodeId,
        runDefinition.name,
        executeNode,
        getNodeParameters,
        nodes,
        edges
      );

      // Debug logging
      console.log('[ParameterPanel] Execution result:', result);
      console.log('[ParameterPanel] Result nodeId:', result.nodeId);
      console.log('[ParameterPanel] Result outputs:', result.outputs);
      console.log('[ParameterPanel] Selected node id:', runNodeId);

      // Add result to the beginning of the array (newest first)
      dispatchExecution({ type: 'append', nodeId: runNodeId, result });
      
    } catch (error: any) {
      console.error('Execution failed:', error);
      
      // Add error result
      const errorResult: ExecutionResult = {
        success: false,
        nodeId: runNodeId,
        nodeType: runDefinition.name,
        nodeName: runDefinition.displayName,
        timestamp: new Date().toISOString(),
        executionTime: 0,
        error: error.message || 'Unknown execution error',
        nodeData: [[{
          json: {
            error: error.message || 'Unknown execution error',
            nodeId: runNodeId,
            success: false,
            timestamp: new Date().toISOString()
          }
        }]]
      };
      
      dispatchExecution({ type: 'append', nodeId: runNodeId, result: errorResult });
    } finally {
      dispatchExecution({ type: 'finish', nodeId: runNodeId });
    }
  };

  // Clear execution results (both local state and WebSocket nodeStatuses)
  const handleClearResults = () => {
    if (!selectedNode) return;
    const nodeId = selectedNode.id;
    dispatchExecution({ type: 'clear', nodeId });
    // Also clear the node status from WebSocket context
    clearNodeStatus(nodeId);
  };


  // Check if node can be executed
  const canExecute = selectedNode && nodeDefinition &&
    ExecutionService.isNodeTypeSupported(nodeDefinition.name);

  // Wave 10.G.5: panel visibility is now pure schema-driven.
  // `hideInputSection` / `hideOutputSection` / `hideRunButton` are
  // declared directly in each plugin module's `uiHints`. Tool-kind
  // nodes (Master Skill, Write Todos, Simple Memory …) don't emit the
  // run button because they're passive — they inherit from the node's
  // own `uiHints`, not from a frontend type-array.
  const hints = (nodeDefinition?.uiHints as Record<string, any>) ?? {};
  const hideInputSection = hints.hideInputSection === true;
  const hideOutputSection = hints.hideOutputSection === true;
  const hideRunButton = hints.hideRunButton === true;

  if (!selectedNode || !nodeDefinition) {
    return null;
  }



  // Header actions with node name and buttons in middle area
  const headerActions = (
    <div className="flex items-center gap-4">
      <div className="flex items-center gap-2 font-display text-[15px] font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
        <NodeIcon
          icon={nodeDefinition.icon}
          size={theme.iconSize.md}
        />
        <span>{nodeDefinition.displayName}</span>
        {hasUnsavedChanges && <span className="text-warning">*</span>}
      </div>
      <div className="flex items-center gap-2">
        {canExecute && !hideRunButton && (
          <Button
            type="button"
            variant="quiet"
            className={NEUTRAL_ACTION}
            onClick={handleRun}
            disabled={selectedExecution.running}
            title={selectedExecution.running ? 'Execution in progress...' : 'Execute this node'}
          >
            <Play className="size-3 fill-current" />
            {selectedExecution.running ? 'Running...' : 'Run'}
          </Button>
        )}
        {/* Save is the panel's one strong action; it fills once there is something to save. */}
        <Button
          type="button"
          variant="invert"
          className={ACTION_SHAPE}
          onClick={handleSave}
          disabled={!hasUnsavedChanges || canvasLock.locked}
          title={canvasLock.locked ? (canvasLock.reason ?? undefined) : undefined}
        >
          <Save className="size-3" />
          Save
        </Button>
        <Button
          type="button"
          variant="quiet"
          className={NEUTRAL_ACTION}
          onClick={async () => {
            if (isWaiting && selectedNode) {
              const result = await cancelEventWait(selectedNode.id, nodeStatus?.data?.waiter_id);
              console.log('[ParameterPanel] Cancel result:', result);
              return;
            }
            handleCancel();
          }}
        >
          <X className="size-3" />
          {isWaiting ? 'Stop' : 'Cancel'}
        </Button>
      </div>
    </div>
  );

  return (
    <Modal
      isOpen={!!selectedNode}
      onClose={handleModalClose}
      title="Node Configuration"
      // Structural hook so themes can scope decorations to THIS modal
      // (e.g. keep an accent-filled modal header off the parameter panel
      // so the Run / Save / Cancel header actions stay legible).
      className="param-panel-modal"
      maxWidth="95vw"
      maxHeight="95vh"
      headerActions={headerActions}
      // ParameterPanelLayout already manages its own scroll regions
      // (params wrapper, console accordion). Opt out of the body's
      // auto-scroll so tall accordion content (Connected Skills,
      // Token Usage) scrolls inside the panel instead of being
      // clipped by a competing outer scroller.
      scrollableBody={false}
    >
      {/* Modular Three Panel Layout */}
      <ParameterPanelLayout
        selectedNode={selectedNode}
        nodeDefinition={nodeDefinition}
        parameters={parameters}
        hasUnsavedChanges={hasUnsavedChanges}
        onParameterChange={handleParameterChange}
        executionResults={selectedExecution.results}
        onClearResults={handleClearResults}
        showInputSection={!hideInputSection}
        showOutputSection={!hideOutputSection}
        isLoadingParameters={isLoading}
      />
    </Modal>
  );
};

export default ParameterPanel;
