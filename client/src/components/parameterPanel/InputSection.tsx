import React, { useState, useEffect } from 'react';
import {
  Database,
  Link as LinkIcon,
  ChevronDown,
  CheckCircle2,
  AlertTriangle,
  ArrowDown,
  Info,
  Loader2,
} from 'lucide-react';
import { useAppStore } from '../../store/useAppStore';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { Node, Edge } from 'reactflow';
import { useDragVariable } from '../../hooks/useDragVariable';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { queryClient } from '../../lib/queryClient';
import { getCachedNodeSpec } from '../../lib/nodeSpec';
import { NodeIcon } from '../../assets/icons';
import { resolveNodeDescription } from '../../lib/nodeSpec';

// ---------------------------------------------------------------------------
// Backend-driven node output schema lookup.
//
// Mirrors n8n's schemaPreview pattern (see
// docs-internal/schema_source_of_truth_rfc.md): the shape shown in the
// drag-drop variable panel for a node that has not been executed yet
// comes from the backend's Pydantic model registry via the
// `get_node_output_schema` WS handler. Results are cached per node
// type in the shared TanStack Query client (in-memory only, matching
// n8n's approach — schemas are small and the cache is cheap).
// ---------------------------------------------------------------------------

type NodeOutputSchema = Record<string, any> | null;

const nodeOutputSchemaQueryKey = (nodeType: string) =>
  ['nodeOutputSchema', nodeType] as const;

async function fetchNodeOutputSchema(
  nodeType: string,
  sendRequest: (type: string, data: any) => Promise<any>,
): Promise<NodeOutputSchema> {
  return queryClient.fetchQuery({
    queryKey: nodeOutputSchemaQueryKey(nodeType),
    queryFn: async () => {
      try {
        const response = await sendRequest('get_node_output_schema', { node_type: nodeType });
        return (response?.schema ?? null) as NodeOutputSchema;
      } catch {
        return null;
      }
    },
    staleTime: Infinity,
  });
}

/**
 * Flatten a JSON-Schema-7 "object" shape into the plain
 * { field: 'primitive-type-name' | nestedObject } map the variable
 * panel expects.
 */
function jsonSchemaToShape(schema: Record<string, any> | null | undefined): Record<string, any> | null {
  if (!schema || typeof schema !== 'object') return null;
  const props = schema.properties;
  if (!props || typeof props !== 'object') return null;

  const out: Record<string, any> = {};
  for (const [key, raw] of Object.entries(props)) {
    if (!raw || typeof raw !== 'object') continue;
    const prop = raw as Record<string, any>;
    if (prop.type === 'object' && prop.properties) {
      out[key] = jsonSchemaToShape(prop) ?? 'object';
    } else if (prop.type === 'array') {
      out[key] = 'array';
    } else if (typeof prop.type === 'string') {
      out[key] = prop.type === 'integer' ? 'number' : prop.type;
    } else {
      out[key] = 'any';
    }
  }
  return out;
}

interface InputSectionProps {
  nodeId: string;
  visible?: boolean;
}

interface NodeData {
  id: string;
  sourceNodeId: string;
  name: string;
  type: string;
  icon: string;
  inputData?: any;
  outputSchema: Record<string, any>;
  hasExecutionData: boolean;
}

// ---------------------------------------------------------------------------
// Reusable draggable-variable card.
//
// Encapsulates the dragstart wiring + visual chrome that was repeated 4x in
// the original render loop. Hover is pure Tailwind (`hover:` classes) so we
// no longer mutate currentTarget.style on mouse enter/leave.
// ---------------------------------------------------------------------------

interface DraggableVarProps {
  templateName: string;
  templatePath: string;       // e.g. "key" or "items[0].sub"
  value: any;                 // the actual value being dragged
  onDragStart: (e: React.DragEvent, sourceNodeId: string, path: string, value: any) => void;
  sourceNodeId: string;
  showLabel?: boolean;        // show "key: type" subtitle
  labelKey?: string;
  className?: string;
}

const DraggableVar: React.FC<DraggableVarProps> = ({
  templateName,
  templatePath,
  value,
  onDragStart,
  sourceNodeId,
  showLabel,
  labelKey,
  className,
}) => (
  <div
    draggable
    onDragStart={(e) => onDragStart(e, sourceNodeId, templatePath, value)}
    className={cn(
      'mb-2 cursor-grab rounded-md border border-border bg-card p-2 transition-colors',
      'hover:border-info hover:bg-info/10',
      className
    )}
  >
    <div className="flex items-center justify-between gap-2">
      <div className="min-w-0 flex-1">
        <div className="font-mono text-sm font-medium text-info truncate">
          {`{{${templateName}.${templatePath}}}`}
        </div>
        {showLabel && (
          <div className="text-xs text-muted-foreground">
            {labelKey}: {typeof value}
          </div>
        )}
      </div>
      <ArrowDown className="h-3.5 w-3.5 shrink-0 text-info" />
    </div>
  </div>
);

// ---------------------------------------------------------------------------

const InputSection: React.FC<InputSectionProps> = ({ nodeId, visible = true }) => {
  const currentWorkflow = useAppStore((s) => s.currentWorkflow);
  const { getNodeOutput, sendRequest } = useWebSocket();
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
  const [connectedNodes, setConnectedNodes] = useState<NodeData[]>([]);
  const [loading, setLoading] = useState(false);
  const { handleVariableDragStart, getTemplateVariableName } = useDragVariable(nodeId);

  // Fetch connected node data with execution results from backend
  useEffect(() => {
    const fetchConnectedNodes = async () => {
      if (!currentWorkflow || !nodeId) {
        setConnectedNodes([]);
        return;
      }

      setLoading(true);
      const nodes = currentWorkflow.nodes || [];
      const edges = currentWorkflow.edges || [];

      // Helper to check if a handle is a config/auxiliary handle
      const isConfigHandle = (handle: string | null | undefined): boolean => {
        if (!handle) return false;
        if (handle.startsWith('input-') && handle !== 'input-main' && handle !== 'input-chat' && handle !== 'input-task' && handle !== 'input-teammates') {
          return true;
        }
        return false;
      };

      // Helper to check if a node is a config/auxiliary node.
      // Backend auto-derives `uiHints.isConfigNode` from group membership
      // (`memory` / `tool`) at registration time — see
      // services/plugin/base.py::_derive_auto_ui_hints. Plugins can also
      // set the flag explicitly to override.
      const isConfigNode = (nodeType: string | undefined): boolean => {
        if (!nodeType) return false;
        const definition = resolveNodeDescription(nodeType);
        return definition?.uiHints?.isConfigNode === true;
      };

      const currentNode = nodes.find((node: Node) => node.id === nodeId);
      const currentNodeType = currentNode?.type;
      const agentSpec = currentNodeType ? getCachedNodeSpec(currentNodeType) : null;
      const isAgentWithSkills = (agentSpec?.uiHints as any)?.hasSkills === true;

      interface EdgeWithLabel { edge: Edge; label?: string; targetHandleLabel?: string }
      const edgesToProcess: EdgeWithLabel[] = [];

      // 1. Add direct incoming edges to main data handles
      const directEdges = edges.filter((edge: Edge) => edge.target === nodeId);
      directEdges.forEach(edge => {
        if (isAgentWithSkills && isConfigHandle(edge.targetHandle)) return;
        let targetHandleLabel: string | undefined;
        if (edge.targetHandle && edge.targetHandle.startsWith('input-') && edge.targetHandle !== 'input-main') {
          targetHandleLabel = edge.targetHandle.replace('input-', '');
        }
        edgesToProcess.push({ edge, targetHandleLabel });
      });

      // 2. If current node is a config node, inherit parent node's main inputs
      if (isConfigNode(currentNodeType)) {
        const outgoingEdges = edges.filter((edge: Edge) => edge.source === nodeId);
        for (const outEdge of outgoingEdges) {
          if (isConfigHandle(outEdge.targetHandle)) {
            const targetNode = nodes.find((node: Node) => node.id === outEdge.target);
            if (!targetNode) continue;
            const targetDef = resolveNodeDescription(targetNode.type || '');
            const targetName = targetDef?.displayName || targetNode.type;
            const parentInputEdges = edges.filter(
              (e: Edge) => e.target === targetNode.id && !isConfigHandle(e.targetHandle)
            );
            for (const parentEdge of parentInputEdges) {
              edgesToProcess.push({ edge: parentEdge, label: `via ${targetName}` });
            }
          }
        }
      }

      const nodeDataPromises = edgesToProcess.map(async ({ edge, label, targetHandleLabel }) => {
        const sourceNode = nodes.find((node: Node) => node.id === edge.source);
        const nodeType = sourceNode?.type || '';
        const nodeDef = resolveNodeDescription(nodeType);

        let outputKey = 'output_0';
        if (edge.sourceHandle && edge.sourceHandle.startsWith('output-')) {
          const handleName = edge.sourceHandle.replace('output-', '');
          outputKey = `output_${handleName}`;
        }

        let executionData = await getNodeOutput(edge.source, outputKey);
        if (!executionData && outputKey !== 'output_0') {
          executionData = await getNodeOutput(edge.source, 'output_0');
        }
        let inputData: any = null;
        let outputSchema: Record<string, any>;
        let hasExecutionData = false;

        if (executionData && executionData[0] && executionData[0][0]) {
          const rawData = executionData[0][0].json || executionData[0][0];
          if (typeof rawData === 'object' && rawData !== null) {
            inputData = rawData;
            outputSchema = rawData;
            hasExecutionData = true;
          } else {
            inputData = { value: rawData };
            outputSchema = { value: typeof rawData };
            hasExecutionData = true;
          }
        } else {
          hasExecutionData = false;
          const sourceSpec = nodeType ? getCachedNodeSpec(nodeType) : null;
          const sourceHints = (sourceSpec?.uiHints as Record<string, any>) ?? {};

          if (sourceHints.hasInitialDataBlob === true) {
            try {
              const initialData = sourceNode?.data?.initial_data || '{}';
              outputSchema = typeof initialData === 'string' ? JSON.parse(initialData) : initialData;
            } catch (e) {
              outputSchema = {};
            }
          } else {
            const backendSchema = jsonSchemaToShape(await fetchNodeOutputSchema(nodeType, sendRequest));
            if (backendSchema) {
              outputSchema = backendSchema;

              const outputHandleCount = (sourceSpec?.handles ?? []).filter(h => h.kind === 'output').length;
              if (outputHandleCount > 1 && edge.sourceHandle?.startsWith('output-')) {
                const handleName = edge.sourceHandle.replace('output-', '');
                const nested = handleName && (backendSchema as Record<string, any>)[handleName];
                if (nested !== undefined) {
                  outputSchema = typeof nested === 'object' && nested !== null
                    ? nested
                    : { [handleName]: nested };
                }
              }
            } else {
              outputSchema = { data: 'any' };
            }
          }
        }

        const baseName = sourceNode?.data?.label || nodeDef?.displayName || nodeType;

        let displayName = baseName;
        let handleSuffix = '';
        if (edge.sourceHandle && edge.sourceHandle.startsWith('output-')) {
          const handleName = edge.sourceHandle.replace('output-', '');
          handleSuffix = handleName;
          displayName = `${baseName} → ${handleName}`;
        }
        if (targetHandleLabel) {
          displayName = `${displayName} (${targetHandleLabel})`;
          handleSuffix = handleSuffix ? `${handleSuffix}-${targetHandleLabel}` : targetHandleLabel;
        }
        if (label) {
          displayName = `${displayName} (${label})`;
        }

        const uniqueId = handleSuffix ? `${edge.source}-${handleSuffix}` : edge.source;

        return {
          id: uniqueId,
          sourceNodeId: edge.source,
          name: displayName,
          type: nodeType,
          icon: nodeDef?.icon || '',
          inputData,
          outputSchema,
          hasExecutionData,
        };
      });

      const nodeDataResults = await Promise.all(nodeDataPromises);
      setConnectedNodes(nodeDataResults);
      setExpandedNodes(new Set(nodeDataResults.map(n => n.id)));
      setLoading(false);
    };

    fetchConnectedNodes();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sendRequest is a stable WS-context callback; including it would re-fetch on every reconnect.
  }, [nodeId, currentWorkflow, getNodeOutput]);

  const toggleNode = (id: string) => {
    setExpandedNodes(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // ---------------------------------------------------------------------------
  // Recursive render of a property (primitive | object | array).
  // ---------------------------------------------------------------------------

  const renderDraggableProperty = (
    key: string,
    value: any,
    sourceNodeId: string,
    path: string = '',
    depth: number = 0,
    maxArrayItems: number = 3,
  ): React.ReactNode => {
    const currentPath = path ? `${path}.${key}` : key;
    const isObject = typeof value === 'object' && value !== null && !Array.isArray(value);
    const isArray = Array.isArray(value);
    const templateName = getTemplateVariableName(sourceNodeId);
    const indentClass = depth > 0 ? 'ml-4' : '';

    // Arrays
    if (isArray && value.length > 0) {
      const itemsToShow = Math.min(value.length, maxArrayItems);
      return (
        <div key={currentPath} className={cn('mb-2', indentClass)}>
          <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            {key}
            <Badge variant="secondary" className="bg-node-agent-soft px-1.5 text-[10px] text-node-agent">
              [{value.length} items]
            </Badge>
          </div>
          <div>
            {value.slice(0, itemsToShow).map((item: any, index: number) => {
              const indexedPath = `${key}[${index}]`;
              const fullIndexedPath = path ? `${path}.${indexedPath}` : indexedPath;

              if (typeof item === 'object' && item !== null) {
                return (
                  <div
                    key={`${currentPath}[${index}]`}
                    className="mb-2 ml-2 rounded-sm border border-dashed border-border bg-muted p-1"
                  >
                    <div className="mb-1 text-xs font-medium text-info">[{index}]</div>
                    {Object.entries(item).map(([itemKey, itemValue]) => {
                      const itemPath = `${fullIndexedPath}.${itemKey}`;
                      if (typeof itemValue === 'object' && itemValue !== null && !Array.isArray(itemValue)) {
                        return (
                          <div key={itemPath} className="mb-1 ml-2">
                            <div className="mb-0.5 text-xs text-muted-foreground">{itemKey}:</div>
                            {Object.entries(itemValue as Record<string, any>).map(([nestedKey, nestedValue]) => (
                              <DraggableVar
                                key={`${itemPath}.${nestedKey}`}
                                templateName={templateName}
                                templatePath={`${itemPath}.${nestedKey}`}
                                value={nestedValue}
                                onDragStart={handleVariableDragStart}
                                sourceNodeId={sourceNodeId}
                                className="mb-1 ml-2 p-1"
                              />
                            ))}
                          </div>
                        );
                      }
                      return (
                        <DraggableVar
                          key={itemPath}
                          templateName={templateName}
                          templatePath={itemPath}
                          value={itemValue}
                          onDragStart={handleVariableDragStart}
                          sourceNodeId={sourceNodeId}
                          showLabel
                          labelKey={itemKey}
                          className="mb-1 ml-2 p-1"
                        />
                      );
                    })}
                  </div>
                );
              }
              // Primitive array item
              return (
                <DraggableVar
                  key={`${currentPath}[${index}]`}
                  templateName={templateName}
                  templatePath={fullIndexedPath}
                  value={item}
                  onDragStart={handleVariableDragStart}
                  sourceNodeId={sourceNodeId}
                  showLabel
                  labelKey={`[${index}]`}
                  className="mb-1 ml-2 p-1"
                />
              );
            })}
            {value.length > maxArrayItems && (
              <div className="ml-2 text-xs italic text-muted-foreground">
                ... and {value.length - maxArrayItems} more items
              </div>
            )}
          </div>
        </div>
      );
    }

    // Empty array
    if (isArray && value.length === 0) {
      return (
        <div key={currentPath} className={cn('mb-2', indentClass)}>
          <div className="text-xs text-muted-foreground">
            {key}: <span className="italic">empty array</span>
          </div>
        </div>
      );
    }

    // Object
    if (isObject) {
      return (
        <div key={currentPath} className={cn('mb-2', indentClass)}>
          <div className="mb-1 text-xs font-medium text-muted-foreground">{key}:</div>
          <div>
            {Object.entries(value as Record<string, any>).map(([subKey, subValue]) =>
              renderDraggableProperty(subKey, subValue, sourceNodeId, currentPath, depth + 1)
            )}
          </div>
        </div>
      );
    }

    // Primitive (top-level draggable)
    return (
      <DraggableVar
        key={currentPath}
        templateName={templateName}
        templatePath={currentPath}
        value={value}
        onDragStart={handleVariableDragStart}
        sourceNodeId={sourceNodeId}
        showLabel
        labelKey={key}
        className={indentClass}
      />
    );
  };

  if (!visible) return null;

  // Loading state
  if (loading) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center bg-card p-12">
        <Loader2 className="mb-4 h-8 w-8 animate-spin text-info" />
        <div className="text-sm text-muted-foreground">Loading input data...</div>
      </div>
    );
  }

  // Empty state
  if (connectedNodes.length === 0) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center bg-card p-12">
        <LinkIcon className="mb-4 h-12 w-12 stroke-1 text-muted-foreground" />
        <div className="mb-1 text-base font-medium text-foreground">No connected inputs</div>
        <div className="text-center text-sm text-muted-foreground">
          Connect nodes to see input data and available variables
        </div>
      </div>
    );
  }

  return (
    // Transparent column shell — sits on the modal's bg-bg-app body per the
    // design system's PanelModal recipe (column heads on bg-bg-panel, only
    // data cards elevated).
    <div className="flex h-full w-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border-default bg-bg-panel px-4 py-3">
        <Database className="h-4 w-4 text-muted-foreground" />
        <span className="font-display text-sm font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">Input Data &amp; Variables</span>
        <Badge variant="info">{connectedNodes.length}</Badge>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-3">
        {connectedNodes.map((node) => {
          const isExpanded = expandedNodes.has(node.id);
          return (
            <div
              key={node.id}
              className={cn(
                'mb-3 overflow-hidden rounded-md border border-border bg-background border-l-[3px]',
                node.hasExecutionData ? 'border-l-success' : 'border-l-warning'
              )}
            >
              {/* Node Header */}
              <div
                onClick={() => toggleNode(node.id)}
                className="flex cursor-pointer items-center justify-between bg-muted px-3 py-2 transition-colors hover:bg-card"
              >
                <div className="flex items-center gap-2">
                  <NodeIcon icon={node.icon} className="h-5 w-5 text-lg" />
                  <span className="font-display text-sm font-semibold text-fg-default">{node.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant={node.hasExecutionData ? 'success' : 'warning'}>
                    {node.hasExecutionData ? (
                      <CheckCircle2 className="h-3 w-3" />
                    ) : (
                      <AlertTriangle className="h-3 w-3" />
                    )}
                    {node.hasExecutionData ? 'LIVE' : 'SCHEMA'}
                  </Badge>
                  <ChevronDown
                    className={cn(
                      'h-3 w-3 text-muted-foreground transition-transform',
                      isExpanded && 'rotate-180'
                    )}
                  />
                </div>
              </div>

              {/* Node Content */}
              {isExpanded && (
                <div className="border-t border-border p-3">
                  {!node.hasExecutionData && (
                    <div className="mb-3 flex items-center gap-2 rounded-sm border border-info bg-info/10 px-2 py-1 text-xs text-info">
                      <Info className="h-3 w-3" />
                      Schema view - Execute this node to see actual input data
                    </div>
                  )}

                  {node.hasExecutionData && (
                    <div className="mb-3">
                      <div className="mb-1 text-xs font-medium tracking-wider text-muted-foreground uppercase">
                        Received Data
                      </div>
                      <pre className="m-0 max-h-[120px] overflow-auto rounded-sm border border-border bg-muted p-2 font-mono text-xs whitespace-pre-wrap break-words text-foreground">
                        {JSON.stringify(node.inputData, null, 2)}
                      </pre>
                    </div>
                  )}

                  <div className="mb-2 text-xs font-medium tracking-wider text-muted-foreground uppercase">
                    Drag Variables to Parameters
                  </div>
                  <div>
                    {typeof node.outputSchema === 'object' && node.outputSchema !== null
                      ? Object.entries(node.outputSchema).map(([key, value]) =>
                          renderDraggableProperty(key, value, node.sourceNodeId || node.id)
                        )
                      : (
                        <div className="text-sm italic text-muted-foreground">
                          No variables available
                        </div>
                      )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Footer hint */}
      <div className="flex shrink-0 items-center justify-center gap-1 border-t border-border bg-background px-4 py-2 text-center text-xs text-muted-foreground">
        <Info className="h-3 w-3" />
        Drag variables into parameter fields to use them
      </div>
    </div>
  );
};

export default InputSection;
