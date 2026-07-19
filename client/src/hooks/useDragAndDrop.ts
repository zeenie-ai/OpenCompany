import { useCallback } from 'react';
import { Node } from 'reactflow';
import { snapToGrid, getDefaultNodePosition, nextNodeInstanceId } from '../utils/workflow';
import { theme } from '../styles/theme';
import { getCachedNodeSpec } from '../lib/nodeSpec';

import { resolveNodeDescription } from '../lib/nodeSpec';
// Wave 10.E: agent detection reads `componentKind: "agent"` from the
// backend NodeSpec. Falls back to the node-definition group during the
// brief window before the spec cache warms.
const isAgentType = (nodeType: string): boolean => {
  const spec = getCachedNodeSpec(nodeType);
  if (spec?.componentKind === 'agent') return true;
  return nodeType === 'aiAgent' || nodeType === 'chatAgent';
};

interface UseDragAndDropProps {
  nodes: Node[];
  setNodes: (nodes: Node[] | ((nodes: Node[]) => Node[])) => void;
  saveNodeParameters?: (nodeId: string, parameters: Record<string, any>) => Promise<boolean>;
  globalModelDefaults?: { provider: string; model: string } | null;
  workflowId: string;
}

/**
 * Generate a unique label for a new node (n8n pattern).
 * First node of type: "Cron Scheduler"
 * Second node of type: "Cron Scheduler 1"
 * Third node of type: "Cron Scheduler 2"
 */
export const generateUniqueLabel = (displayName: string, nodeType: string, existingNodes: Node[]): string => {
  // Collect all labels from nodes of the same type
  const existingLabels = existingNodes
    .filter(n => n.type === nodeType)
    .map(n => n.data?.label as string | undefined)
    .filter((label): label is string => !!label);

  // If no nodes have this base displayName, use it as-is
  if (!existingLabels.includes(displayName)) {
    return displayName;
  }

  // Find next available suffix
  let suffix = 1;
  while (existingLabels.includes(`${displayName} ${suffix}`)) {
    suffix++;
  }
  return `${displayName} ${suffix}`;
};

export const useDragAndDrop = ({ nodes, setNodes, saveNodeParameters, globalModelDefaults, workflowId }: UseDragAndDropProps) => {
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    async (event: React.DragEvent) => {
      event.preventDefault();

      try {
        const nodeData = JSON.parse(event.dataTransfer.getData('application/reactflow'));
        if (!nodeData || !nodeData.type) {
          return;
        }

        const reactFlowBounds = (event.target as Element).getBoundingClientRect();

        let position = {
          x: event.clientX - reactFlowBounds.left - theme.constants.dragOffset.x,
          y: event.clientY - reactFlowBounds.top - theme.constants.dragOffset.y,
        };

        // Snap to grid for better alignment
        position = snapToGrid(position);

        // If no nodes exist, use default position
        if (nodes.length === 0) {
          position = getDefaultNodePosition(nodes.length);
        }

        // Get node definition to access displayName
        const nodeDef = resolveNodeDescription(nodeData.type);
        const displayName = nodeDef?.displayName || nodeData.type;

        // Generate unique label for template variable resolution (n8n pattern)
        const uniqueLabel = generateUniqueLabel(displayName, nodeData.type, nodes);

        const newNode: Node = {
          id: nextNodeInstanceId(workflowId, nodeData.type, nodes),
          type: nodeData.type,
          position,
          data: {
            label: uniqueLabel,  // Only UI-display fields in node.data (not parameters)
          },
        };

        // Save full default parameters to DB only (not to node.data)
        const defaults = nodeData.data || {};

        // Apply global model defaults for agent nodes (componentKind="agent")
        if (globalModelDefaults && isAgentType(nodeData.type)) {
          defaults.provider = globalModelDefaults.provider;
          defaults.model = globalModelDefaults.model;
        }

        if (Object.keys(defaults).length > 0 && saveNodeParameters) {
          try {
            await saveNodeParameters(newNode.id, { ...defaults, label: uniqueLabel });
            console.log(`[DragAndDrop] Saved default parameters for node ${newNode.id}`);
          } catch (error) {
            console.error(`[DragAndDrop] Failed to save parameters for node ${newNode.id}:`, error);
          }
        }

        setNodes((nds) => nds.concat(newNode));
      } catch (error) {
        console.error('Error dropping node:', error);
      }
    },
    [setNodes, nodes, saveNodeParameters, globalModelDefaults, workflowId]
  );

  const handleComponentDragStart = useCallback((event: React.DragEvent, definition: any) => {
    const defaults: any = {};
    // Handle both old (parameters) and new (properties) interface formats
    const properties = definition.properties || definition.parameters || [];
    properties.forEach((param: any) => {
      defaults[param.name] = param.default;
    });
    event.dataTransfer.setData('application/reactflow', JSON.stringify({ 
      type: definition.name || definition.type, // Handle both new (name) and old (type) formats
      data: defaults 
    }));
    event.dataTransfer.effectAllowed = 'move';
  }, []);

  return {
    onDragOver,
    onDrop,
    handleComponentDragStart,
  };
};
