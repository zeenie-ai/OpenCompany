import React from 'react';
import { Node } from 'reactflow';
import InputSection from './InputSection';
import MiddleSection from './MiddleSection';
import OutputSection from './OutputSection';
import { INodeTypeDescription } from '../../types/INodeProperties';
import { ExecutionResult } from '../../services/executionService';

interface ParameterPanelLayoutProps {
  // Node data
  selectedNode: Node;
  nodeDefinition: INodeTypeDescription;
  parameters: Record<string, any>;
  hasUnsavedChanges: boolean;

  // Parameter handling
  onParameterChange: (paramName: string, value: any) => void;

  // Execution data
  executionResults: ExecutionResult[];
  onClearResults: () => void;

  // Layout configuration
  showInputSection?: boolean;
  showOutputSection?: boolean;

  // Loading state
  isLoadingParameters?: boolean;
}

const ParameterPanelLayout: React.FC<ParameterPanelLayoutProps> = ({
  selectedNode,
  nodeDefinition,
  parameters,
  hasUnsavedChanges: _hasUnsavedChanges,
  onParameterChange,
  executionResults,
  onClearResults,
  showInputSection = true,
  showOutputSection = true,
  isLoadingParameters = false
}) => {
  return (
    <div className="flex h-full min-h-0">
      {/* Left: Input Nodes JSON Data (border-r = the design system's
          column split on --border-default) */}
      {showInputSection && (
        <div className="h-full flex-[0.7] overflow-hidden border-r border-border-default">
          <InputSection
            nodeId={selectedNode.id}
            visible={showInputSection}
          />
        </div>
      )}

      {/* Middle: Parameter Content */}
      <div className="h-full min-w-0 flex-[1.6] overflow-hidden">
        <MiddleSection
          nodeId={selectedNode.id}
          nodeDefinition={nodeDefinition}
          parameters={parameters}
          onParameterChange={onParameterChange}
          isLoadingParameters={isLoadingParameters}
          executionResults={executionResults}
        />
      </div>

      {/* Right: Current Node Output (border-l = the design system's
          column split on --border-default) */}
      {showOutputSection && (
        <div className="h-full flex-[0.7] overflow-hidden border-l border-border-default">
          <OutputSection
            selectedNode={selectedNode}
            executionResults={executionResults}
            onClearResults={onClearResults}
            visible={showOutputSection}
          />
        </div>
      )}
    </div>
  );
};

export default ParameterPanelLayout;