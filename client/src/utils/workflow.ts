import { Node, Edge } from 'reactflow';

/** Marker used until the backend atomically allocates the next workflow id. */
export const NEW_WORKFLOW_ID = 'new';

/**
 * Allocate the next stable instance id for a plugin node in a workflow.
 *
 * Plugin `type` is the fixed metadata identity. The final ordinal keeps
 * multiple instances of that plugin distinct without timestamps/randomness.
 * The server repeats this validation while saving/importing, so this helper is
 * primarily what keeps live canvas operations canonical before persistence.
 */
export const nextNodeInstanceId = (
  workflowId: string,
  nodeType: string,
  existingNodes: Pick<Node, 'id' | 'type'>[],
): string => {
  const scope = workflowId || NEW_WORKFLOW_ID;
  const prefix = `${scope}:${nodeType}:`;
  let largest = 0;

  for (const node of existingNodes) {
    if (node.type !== nodeType || !node.id.startsWith(prefix)) continue;
    const ordinal = Number.parseInt(node.id.slice(prefix.length), 10);
    if (Number.isSafeInteger(ordinal) && ordinal > largest) largest = ordinal;
  }

  // Legacy ids do not contain an ordinal. Counting them prevents a migrated
  // canvas from issuing `:1` for what is visibly a later plugin instance.
  const sameTypeCount = existingNodes.filter(node => node.type === nodeType).length;
  return `${prefix}${Math.max(largest, sameTypeCount) + 1}`;
};

export const sanitizeNodesForComparison = (nodes: Node[]): Node[] =>
  nodes.map(n => ({ ...n, selected: undefined, dragging: undefined }));

export const sanitizeEdgesForComparison = (edges: Edge[]): Edge[] =>
  edges.map(e => ({ ...e, selected: undefined }));

export const serializeDateFields = <T extends { createdAt: Date; lastModified: Date }>(obj: T) => ({
  ...obj,
  createdAt: obj.createdAt.toISOString(),
  lastModified: obj.lastModified.toISOString(),
});

export const deserializeDateFields = <T extends { createdAt: string; lastModified: string }>(obj: T) => ({
  ...obj,
  createdAt: new Date(obj.createdAt),
  lastModified: new Date(obj.lastModified),
});

export const snapToGrid = (position: { x: number; y: number }, gridSize = 20) => ({
  x: Math.round(position.x / gridSize) * gridSize,
  y: Math.round(position.y / gridSize) * gridSize,
});

export const getDefaultNodePosition = (nodeCount: number): { x: number; y: number } =>
  nodeCount === 0 ? { x: 100, y: 200 } : { x: 0, y: 0 };

// Node-id remap on import lives backend-side in
// ``server/services/workflow_import.py::remap_node_ids`` — the import
// path is now backend-authoritative (``import_workflow`` WS handler),
// so there's no need for a duplicate frontend implementation. The
// in-canvas copy/paste path uses its own id scheme in ``useCopyPaste``.
