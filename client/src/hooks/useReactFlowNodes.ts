import { useCallback } from 'react';
import { Node, Edge, addEdge, Connection } from 'reactflow';
import { useAppStore } from '../store/useAppStore';
import { INodeInputDefinition, INodeOutputDefinition, NodeConnectionType } from '../types/INodeProperties';

import { resolveNodeDescription } from '../lib/nodeSpec';
interface UseReactFlowNodesProps {
  setNodes: (nodes: Node[] | ((nodes: Node[]) => Node[])) => void;
  setEdges: (edges: Edge[] | ((edges: Edge[]) => Edge[])) => void;
  clearNodeStatus?: (nodeId: string) => void | Promise<void>;
}

export const useReactFlowNodes = ({ setNodes, setEdges, clearNodeStatus }: UseReactFlowNodesProps) => {
  const selectedNode = useAppStore((s) => s.selectedNode);
  const setSelectedNode = useAppStore((s) => s.setSelectedNode);

  // Helper function to get node inputs/outputs for both enhanced and legacy nodes
  const getNodeInputs = (nodeType: string): INodeInputDefinition[] => {
    const definition = resolveNodeDescription(nodeType);
    if (!definition?.inputs) return [];
    
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
  
  const getNodeOutputs = (nodeType: string): INodeOutputDefinition[] => {
    const definition = resolveNodeDescription(nodeType);
    if (!definition?.outputs) return [];
    
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

  // Validate connection compatibility
  const isValidConnection = (connection: Connection, nodes: Node[]): boolean => {
    const sourceNode = nodes.find(n => n.id === connection.source);
    const targetNode = nodes.find(n => n.id === connection.target);
    
    if (!sourceNode || !targetNode) {
      console.warn('Connection validation: Source or target node not found');
      return false;
    }

    const sourceOutputs = getNodeOutputs(sourceNode.type || '');
    const targetInputs = getNodeInputs(targetNode.type || '');

    // Find the specific output and input being connected
    const sourceHandle = connection.sourceHandle || 'output_0';
    const targetHandle = connection.targetHandle || 'input_0';

    const sourceOutput = sourceOutputs.find(output =>
      `output-${output.name}` === sourceHandle || output.name === sourceHandle
    );
    const targetInput = targetInputs.find(input =>
      `input-${input.name}` === targetHandle || input.name === targetHandle
    );

    if (!sourceOutput || !targetInput) {
      console.log('[Connection Debug]', {
        sourceType: sourceNode.type,
        targetType: targetNode.type,
        sourceHandle,
        targetHandle,
        sourceOutputs: sourceOutputs.map(o => ({ name: o.name, computed: `output-${o.name}` })),
        targetInputs: targetInputs.map(i => ({ name: i.name, computed: `input-${i.name}` })),
        sourceOutput: sourceOutput ? 'found' : 'NOT FOUND',
        targetInput: targetInput ? 'found' : 'NOT FOUND'
      });
      console.warn('Connection validation: Handle not found', { sourceHandle, targetHandle });
      // Allow connection if we can't find handle definitions (fallback for legacy nodes)
      return true;
    }

    // Check type compatibility
    const isCompatible = areTypesCompatible(sourceOutput.type, targetInput.type);
    
    if (!isCompatible) {
      console.warn(`Connection rejected: Incompatible types`, {
        source: `${sourceNode.type}.${sourceOutput.name} (${sourceOutput.type})`,
        target: `${targetNode.type}.${targetInput.name} (${targetInput.type})`
      });
    }
    
    return isCompatible;
  };

  // Check if two connection types are compatible
  const areTypesCompatible = (outputType: NodeConnectionType, inputType: NodeConnectionType): boolean => {
    // Same types are always compatible
    if (outputType === inputType) return true;
    
    // 'main' is compatible with most types (universal data format)
    if (outputType === 'main' || inputType === 'main') return true;
    
    // Specific compatibility rules
    const compatibilityMatrix: Record<NodeConnectionType, NodeConnectionType[]> = {
      'main': ['main', 'trigger', 'ai', 'file', 'binary', 'webhook'], // Main accepts everything
      'trigger': ['main', 'trigger'], // Triggers only connect to main or other triggers
      'ai': ['main', 'ai'], // AI outputs connect to main or other AI inputs
      'file': ['main', 'file', 'binary'], // Files can connect to binary
      'binary': ['main', 'file', 'binary'], // Binary connects to file/binary
      'webhook': ['main', 'webhook'] // Webhooks connect to main or other webhooks
    };
    
    return compatibilityMatrix[outputType]?.includes(inputType) ?? false;
  };

  const onConnect = useCallback(
    (params: Edge | Connection) => {
      const { currentWorkflow } = useAppStore.getState();
      const nodes = currentWorkflow?.nodes || [];

      // Convert Edge to Connection format for validation
      const connection: Connection = {
        source: params.source,
        target: params.target,
        sourceHandle: params.sourceHandle ?? null,
        targetHandle: params.targetHandle ?? null
      };

      // Debug: Log all connection attempts
      console.log('[onConnect] Connection attempt:', {
        source: params.source,
        target: params.target,
        sourceHandle: params.sourceHandle,
        targetHandle: params.targetHandle
      });

      // Validate connection before adding
      if (!isValidConnection(connection, nodes)) {
        // Show user feedback for rejected connection (non-blocking)
        console.warn('Connection rejected: Incompatible connection types', connection);
        // Could implement a toast notification system here instead of alert
        setTimeout(() => {
          alert('Connection not allowed: Incompatible connection types.\n\nTip: Connect outputs to compatible inputs (e.g., AI → Main, File → Binary).');
        }, 0);
        return;
      }

      console.log('[onConnect] Connection accepted, adding edge');
      // Add the connection
      setEdges((eds) => addEdge(params, eds));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- isValidConnection is captured by closure; including would re-bind on every render.
    [setEdges]
  );

  const onNodesDelete = useCallback(
    (deleted: Node[]) => {
      const removable = deleted.filter((node) => node.type !== 'taskManager');
      setNodes((nds) => nds.filter((node) => !removable.find((d) => d.id === node.id)));
      // Node ids can be reused after deletion. Clear the exact backend and
      // workflow-scoped frontend status slot so a replacement node never
      // inherits the deleted node's skill/tool capability label.
      removable.forEach((node) => {
        void clearNodeStatus?.(node.id);
      });
      
      // Clear selected node if it was deleted
      if (selectedNode && removable.find((d) => d.id === selectedNode.id)) {
        setSelectedNode(null);
      }
    },
    [setNodes, selectedNode, setSelectedNode, clearNodeStatus]
  );

  const onEdgesDelete = useCallback(
    (deleted: Edge[]) => {
      setEdges((eds) => eds.filter((edge) => !deleted.find((d) => d.id === edge.id)));
    },
    [setEdges]
  );

  return {
    onConnect,
    onNodesDelete,
    onEdgesDelete,
  };
};
