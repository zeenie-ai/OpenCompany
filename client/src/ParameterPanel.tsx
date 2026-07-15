import React from 'react';
import Modal from './components/ui/Modal';
import ParameterPanelLayout from './components/parameterPanel/ParameterPanelLayout';
import { useParameterPanel } from './hooks/useParameterPanel';
import { useAppStore } from './store/useAppStore';
import { useWebSocket } from './contexts/WebSocketContext';
import { ExecutionService, ExecutionResult } from './services/executionService';
import { ActionButton } from './components/ui/action-button';
import { NodeIcon } from './assets/icons';
import { nodeExecutionReducer } from './utils/parameterPanelExecutionState';

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
  const { executeNode, getNodeParameters, clearNodeStatus, cancelEventWait, getNodeStatus } = useWebSocket();

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
          className="h-5 w-5 text-xl"
        />
        <span>{nodeDefinition.displayName}</span>
        {hasUnsavedChanges && <span className="text-warning">*</span>}
      </div>
      <div className="flex items-center gap-2">
        {canExecute && !hideRunButton && (
          <ActionButton
            intent="run"
            onClick={handleRun}
            disabled={selectedExecution.running}
            title={selectedExecution.running ? 'Execution in progress...' : 'Execute this node'}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z" />
            </svg>
            {selectedExecution.running ? 'Running...' : 'Run'}
          </ActionButton>
        )}
        <ActionButton intent="tools" onClick={handleSave} disabled={!hasUnsavedChanges}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
            <polyline points="17 21 17 13 7 13 7 21" />
            <polyline points="7 3 7 8 15 8" />
          </svg>
          Save
        </ActionButton>
        <ActionButton
          intent="stop"
          onClick={async () => {
            if (isWaiting && selectedNode) {
              const result = await cancelEventWait(selectedNode.id, nodeStatus?.data?.waiter_id);
              console.log('[ParameterPanel] Cancel result:', result);
              return;
            }
            handleCancel();
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
          {isWaiting ? 'Stop' : 'Cancel'}
        </ActionButton>
      </div>
    </div>
  );

  return (
    <Modal
      isOpen={!!selectedNode}
      onClose={handleModalClose}
      title="Node Configuration"
      // Structural hook so themes can scope decorations to THIS modal
      // (e.g. atomic keeps its accent header off the parameter panel so
      // the tinted Run / Save / Cancel header actions stay legible).
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
