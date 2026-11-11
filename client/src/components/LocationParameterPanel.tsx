import React, { useState } from 'react';
import Modal from './ui/Modal';
import LocationPanelLayout from './parameterPanel/LocationPanelLayout';
import { useParameterPanel } from '../hooks/useParameterPanel';
import { ExecutionService } from '../services/executionService';
import { Button } from '@/components/ui/button';

// Colour marks state or role (docs-internal/theme_system.md): the header's
// buttons are neutral outlines, and Save, the panel's one strong action, is
// `invert`, as on the node panel (ParameterPanel.tsx).
const NEUTRAL_ACTION = 'rounded-md border-border-default px-3.5 text-[13px] font-semibold text-fg-default';
const ACTION_SHAPE = 'rounded-md px-3.5 text-[13px] font-semibold';

const LocationParameterPanel: React.FC = () => {
  const {
    selectedNode,
    nodeDefinition,
    parameters,
    hasUnsavedChanges,
    handleParameterChange,
    handleSave,
    handleCancel,
  } = useParameterPanel();


  // Execution state (kept for potential future location node execution)
  const [isExecuting, setIsExecuting] = useState(false);

  const handleModalClose = () => {
    handleCancel();
  };

  // Execute the current node (simplified - location nodes typically don't execute)
  const handleRun = async () => {
    if (!selectedNode || !nodeDefinition) return;

    if (hasUnsavedChanges) {
      await handleSave();
    }

    setIsExecuting(true);
    try {
      // Location nodes like gmaps_create typically don't need backend execution
      // They're configuration nodes that output map instances
    } catch (error: any) {
      console.error('Location node execution failed:', error);
    } finally {
      setIsExecuting(false);
    }
  };

  // Check if node can be executed
  const canExecute = selectedNode && nodeDefinition &&
    ExecutionService.isNodeTypeSupported(nodeDefinition.name);

  if (!selectedNode || !nodeDefinition) {
    return null;
  }

  // Only show LocationParameterPanel for nodes flagged via uiHints; legacy
  // name fallback for nodes not yet annotated.
  const showPanel = nodeDefinition.uiHints?.showLocationPanel
    ?? (nodeDefinition.name === 'gmaps_create');
  if (!showPanel) {
    return null;
  }

  // Header actions with node name and buttons in middle area
  const headerActions = (
    <div className="flex items-center gap-4">
      <div className="flex items-center gap-2 text-[15px] font-semibold text-foreground">
        <span>{nodeDefinition.icon}</span>
        <span>{nodeDefinition.displayName}</span>
        {hasUnsavedChanges && <span className="text-warning">*</span>}
      </div>
      <div className="flex items-center gap-2">
        {canExecute && (
          <Button
            type="button"
            variant="quiet"
            className={NEUTRAL_ACTION}
            onClick={handleRun}
            disabled={isExecuting}
            title={isExecuting ? 'Execution in progress...' : 'Execute this node'}
          >
            {isExecuting ? 'Running...' : 'Run'}
          </Button>
        )}
        <Button type="button" variant="invert" className={ACTION_SHAPE} onClick={handleSave} disabled={!hasUnsavedChanges}>
          Save
        </Button>
        <Button type="button" variant="quiet" className={NEUTRAL_ACTION} onClick={handleCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );

  return (
    <Modal
      isOpen={!!selectedNode}
      onClose={handleModalClose}
      title="Node Configuration"
      maxWidth="95vw"
      maxHeight="95vh"
      headerActions={headerActions}
    >
      {/* Modular Location Panel Layout */}
      <LocationPanelLayout
        selectedNode={selectedNode}
        nodeDefinition={nodeDefinition}
        parameters={parameters}
        hasUnsavedChanges={hasUnsavedChanges}
        onParameterChange={handleParameterChange}
        showInputSection={true}
      />
    </Modal>
  );
};

export default LocationParameterPanel;