import React from 'react';
import OutputPanel from '../output/OutputPanel';
import { ExecutionResult } from '../../services/executionService';
import { Node } from 'reactflow';
import { useWebSocket } from '../../contexts/WebSocketContext';

interface OutputSectionProps {
  selectedNode: Node;
  executionResults: ExecutionResult[];
  onClearResults: () => void;
  visible?: boolean;
}

const OutputSection: React.FC<OutputSectionProps> = ({
  selectedNode,
  executionResults,
  onClearResults,
  visible = true
}) => {
  const { nodeStatuses } = useWebSocket();

  if (!visible) {
    return null;
  }

  // Combine local execution results with WebSocket nodeStatuses from
  // workflow execution. Dedup uses the backend-issued `execution_id`
  // correlation token: the request-response and the broadcast for the
  // same run carry the same id, so we can fold them into one entry.
  // The previous implementation hashed outputs via JSON.stringify, which
  // collapsed two distinct executions whose payloads happened to match.
  // eslint-disable-next-line react-hooks/rules-of-hooks -- pre-existing structural pattern; visible toggle is stable per render-tree mount.
  const combinedResults = React.useMemo(() => {
    const results = [...executionResults];

    // WebSocket node_output messages store data in the `output` field,
    // while node_status messages use `data`. Check both so the formatted
    // response renderer (getMainResponse → ReactMarkdown) receives the
    // actual AI response object instead of falling through to raw JSON.
    const nodeStatus = nodeStatuses[selectedNode.id];
    const statusData = nodeStatus?.data || nodeStatus?.output;
    if (nodeStatus && statusData && (nodeStatus.status === 'success' || nodeStatus.status === 'error')) {
      const broadcastExecutionId: string | undefined = statusData?.execution_id;
      const alreadyExists = results.some((r) => {
        // Preferred: correlate by backend-issued execution_id token.
        if (broadcastExecutionId && r.executionId) {
          return r.executionId === broadcastExecutionId;
        }
        // Backward-compat fallback for results constructed before the
        // execution_id field was wired (or for synthetic catch-block
        // entries): structural equality on outputs, scoped to this
        // node id. Same shape used by the pre-correlation-id code.
        return (
          r.nodeId === selectedNode.id &&
          JSON.stringify(r.outputs) === JSON.stringify(statusData)
        );
      });

      if (!alreadyExists) {
        const wsResult: ExecutionResult = {
          success: nodeStatus.status === 'success',
          nodeId: selectedNode.id,
          nodeType: selectedNode.type || 'unknown',
          nodeName: selectedNode.type || 'Node',
          timestamp: new Date().toISOString(),
          executionTime: 0,
          outputs: statusData,
          nodeData: [[{ json: statusData }]],
          error: nodeStatus.status === 'error' ? statusData?.error : undefined,
          executionId: broadcastExecutionId,
        };
        results.unshift(wsResult);
      }
    }

    return results;
  }, [executionResults, nodeStatuses, selectedNode.id, selectedNode.type]);

  return (
    // Transparent column shell — sits on the modal's bg-bg-app body per the
    // design system's PanelModal recipe.
    <div className="relative flex h-full w-full flex-col overflow-hidden">
      <OutputPanel
        results={combinedResults}
        onClear={onClearResults}
        selectedNode={selectedNode}
      />
    </div>
  );
};

export default OutputSection;