import { useCallback, useRef } from 'react';
import { Node, Edge } from 'reactflow';
import { generateUniqueLabel } from './useDragAndDrop';
import { resolveNodeDescription } from '../lib/nodeSpec';
import { nextNodeInstanceId } from '../utils/workflow';
interface UseCopyPasteProps {
  nodes: Node[];
  edges: Edge[];
  setNodes: (nodes: Node[] | ((nodes: Node[]) => Node[])) => void;
  setEdges: (edges: Edge[] | ((edges: Edge[]) => Edge[])) => void;
  saveNodeParameters?: (nodeId: string, parameters: Record<string, any>) => Promise<boolean>;
  workflowId: string;
}

interface ClipboardData {
  nodes: Node[];
  edges: Edge[];
}

/**
 * Hook for copy/paste functionality with n8n-style auto-labeling.
 *
 * Features:
 * - Copy selected nodes and their connecting edges
 * - Paste with offset position and unique labels
 * - Persist node parameters to database
 */
export const useCopyPaste = ({
  nodes,
  edges,
  setNodes,
  setEdges,
  saveNodeParameters,
  workflowId,
}: UseCopyPasteProps) => {
  // In-memory clipboard (simpler than browser clipboard API)
  const clipboardRef = useRef<ClipboardData | null>(null);

  /**
   * Copy selected nodes and their connecting edges to clipboard.
   */
  const copySelectedNodes = useCallback(() => {
    const selectedNodes = nodes.filter(n => n.selected);
    if (selectedNodes.length === 0) {
      console.log('[CopyPaste] No nodes selected to copy');
      return;
    }

    // Get edges that connect selected nodes (both ends must be selected)
    const selectedNodeIds = new Set(selectedNodes.map(n => n.id));
    const selectedEdges = edges.filter(
      e => selectedNodeIds.has(e.source) && selectedNodeIds.has(e.target)
    );

    clipboardRef.current = {
      nodes: selectedNodes,
      edges: selectedEdges,
    };

    console.log(`[CopyPaste] Copied ${selectedNodes.length} nodes and ${selectedEdges.length} edges`);
  }, [nodes, edges]);

  /**
   * Paste nodes from clipboard with offset and unique labels.
   */
  const pasteNodes = useCallback(async () => {
    if (!clipboardRef.current) {
      console.log('[CopyPaste] Nothing in clipboard to paste');
      return;
    }

    const { nodes: copiedNodes, edges: copiedEdges } = clipboardRef.current;

    // Generate ID mapping (old ID -> new ID)
    const idMap = new Map<string, string>();
    const allocatedNodes = [...nodes];
    copiedNodes.forEach((node) => {
      const newId = nextNodeInstanceId(workflowId, node.type!, allocatedNodes);
      idMap.set(node.id, newId);
      allocatedNodes.push({ ...node, id: newId });
    });

    // Offset for pasted nodes to avoid stacking on original
    const PASTE_OFFSET = 50;

    // Create new nodes with offset, new IDs, and unique labels
    const newNodes: Node[] = [];

    for (let i = 0; i < copiedNodes.length; i++) {
      const node = copiedNodes[i];
      const newId = idMap.get(node.id)!;

      // Always use the original display name from node definition as base
      // This ensures "WhatsApp Receive 2" copies become "WhatsApp Receive 3", not "WhatsApp Receive 2 1"
      const baseDisplayName = resolveNodeDescription(node.type!)?.displayName || node.type!;

      // Generate unique label considering existing nodes AND nodes we're about to add
      const allNodes = [...nodes, ...newNodes];
      const uniqueLabel = generateUniqueLabel(baseDisplayName, node.type!, allNodes);

      const newNode: Node = {
        ...node,
        id: newId,
        position: {
          x: node.position.x + PASTE_OFFSET,
          y: node.position.y + PASTE_OFFSET,
        },
        selected: true,
        data: {
          ...node.data,
          label: uniqueLabel,
        },
      };

      // Save parameters for new node to database
      if (saveNodeParameters && newNode.data) {
        try {
          await saveNodeParameters(newId, newNode.data);
        } catch (error) {
          console.error(`[CopyPaste] Failed to save parameters for ${newId}:`, error);
        }
      }

      newNodes.push(newNode);
    }

    // Create new edges with updated source/target IDs
    const newEdges: Edge[] = copiedEdges.map(edge => ({
      ...edge,
      id: `e-${idMap.get(edge.source)}-${idMap.get(edge.target)}-${Date.now()}`,
      source: idMap.get(edge.source)!,
      target: idMap.get(edge.target)!,
      selected: false,
    }));

    // Deselect existing nodes and add new ones as selected
    setNodes(nds => [
      ...nds.map(n => ({ ...n, selected: false })),
      ...newNodes,
    ]);

    setEdges(eds => [...eds, ...newEdges]);

    console.log(`[CopyPaste] Pasted ${newNodes.length} nodes and ${newEdges.length} edges`);
  }, [nodes, setNodes, setEdges, saveNodeParameters, workflowId]);

  /**
   * Check if clipboard has content.
   */
  const hasClipboard = clipboardRef.current !== null && clipboardRef.current.nodes.length > 0;

  return {
    copySelectedNodes,
    pasteNodes,
    hasClipboard,
  };
};
