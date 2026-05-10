import React, { useState } from 'react';
import { useAppStore } from '../store/useAppStore';
import { INodeOutputDefinition, NodeConnectionType } from '../types/INodeProperties';
import { useDragVariable } from '../hooks/useDragVariable';
import { isNodeInBackendGroup } from '../lib/nodeSpec';

import { resolveNodeDescription } from '../lib/nodeSpec';
interface OutputPanelProps {
  nodeId: string;
}

interface ConnectedNodeData {
  nodeId: string;
  nodeName: string;
  outputs: INodeOutputDefinition[];
}

// Android node output schemas - matches the flattened output structure from backend
// These are shown as draggable outputs for template variable creation
const ANDROID_OUTPUT_SCHEMAS: Record<string, Record<string, string>> = {
  batteryMonitor: {
    battery_level: 'number',
    is_charging: 'boolean',
    temperature_celsius: 'number',
    health: 'string',
    voltage: 'number'
  },
  systemInfo: {
    device_model: 'string',
    android_version: 'string',
    api_level: 'number',
    manufacturer: 'string',
    total_memory: 'number',
    available_memory: 'number'
  },
  networkMonitor: {
    connected: 'boolean',
    type: 'string',
    ssid: 'string',
    ip_address: 'string',
    signal_strength: 'number'
  },
  wifiAutomation: {
    enabled: 'boolean',
    connected: 'boolean',
    ssid: 'string',
    networks: 'array'
  },
  bluetoothAutomation: {
    enabled: 'boolean',
    connected: 'boolean',
    paired_devices: 'array'
  },
  audioAutomation: {
    media_volume: 'number',
    ring_volume: 'number',
    muted: 'boolean'
  },
  location: {
    latitude: 'number',
    longitude: 'number',
    accuracy: 'number',
    provider: 'string',
    altitude: 'number',
    speed: 'number',
    bearing: 'number'
  },
  appLauncher: {
    package_name: 'string',
    launched: 'boolean',
    app_name: 'string'
  },
  appList: {
    apps: 'array',
    count: 'number'
  },
  deviceStateAutomation: {
    airplane_mode: 'boolean',
    screen_on: 'boolean',
    brightness: 'number'
  },
  screenControlAutomation: {
    brightness: 'number',
    auto_brightness: 'boolean',
    screen_timeout: 'number'
  },
  motionDetection: {
    acceleration_x: 'number',
    acceleration_y: 'number',
    acceleration_z: 'number',
    is_moving: 'boolean'
  },
  environmentalSensors: {
    temperature: 'number',
    humidity: 'number',
    pressure: 'number',
    light: 'number'
  },
  cameraControl: {
    cameras: 'array',
    photo_path: 'string'
  },
  mediaControl: {
    playing: 'boolean',
    volume: 'number',
    track: 'string'
  },
  airplaneModeControl: {
    enabled: 'boolean'
  }
};

// List of Android service node types
const ANDROID_NODE_TYPES = [
  'batteryMonitor', 'systemInfo', 'networkMonitor', 'location',
  'wifiAutomation', 'bluetoothAutomation', 'audioAutomation',
  'deviceStateAutomation', 'screenControlAutomation', 'airplaneModeControl',
  'motionDetection', 'environmentalSensors', 'cameraControl', 'mediaControl',
  'appLauncher', 'appList'
];

const OutputPanel: React.FC<OutputPanelProps> = ({ nodeId }) => {
  const currentWorkflow = useAppStore((s) => s.currentWorkflow);
  const [expandedNode, setExpandedNode] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [draggedParam, setDraggedParam] = useState<{ nodeId: string; output: string } | null>(null);

  // Use the same template variable naming as InputSection for consistency
  const { getTemplateVariableName } = useDragVariable(nodeId);

  // Helper to get outputs from a node definition
  const getNodeOutputs = (nodeType: string): INodeOutputDefinition[] => {
    // Wave 6 Phase 5.b: backend group → legacy fallback
    const isAndroid = isNodeInBackendGroup(nodeType, 'android') ?? ANDROID_NODE_TYPES.includes(nodeType);
    if (isAndroid) {
      const schema = ANDROID_OUTPUT_SCHEMAS[nodeType];
      if (schema) {
        return Object.entries(schema).map(([name, type]) => ({
          name,
          displayName: name.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' '),
          type: type as NodeConnectionType,
          description: `${type} value`
        }));
      }
    }

    const definition = resolveNodeDescription(nodeType);
    if (!definition) return [];

    if (!definition.outputs) {
      return [{
        name: 'output',
        displayName: 'Output',
        type: 'main' as NodeConnectionType,
        description: 'Node output'
      }];
    } else if (Array.isArray(definition.outputs)) {
      if (typeof definition.outputs[0] === 'string') {
        return (definition.outputs as string[]).map(outputName => ({
          name: outputName,
          displayName: outputName.charAt(0).toUpperCase() + outputName.slice(1),
          type: 'main' as NodeConnectionType,
          description: `${outputName} output`
        }));
      } else {
        return definition.outputs as INodeOutputDefinition[];
      }
    }
    return [{
      name: 'output',
      displayName: 'Output',
      type: 'main' as NodeConnectionType,
      description: 'Node output'
    }];
  };

  // Helper to check if a handle is a config/auxiliary handle (not main data flow)
  const isConfigHandle = (handle: string | null | undefined): boolean => {
    if (!handle) return false;
    // Config handles follow pattern: input-<type> where type is not 'main', 'chat', or 'task'
    // Examples: input-memory, input-tools, input-model
    // Non-config (data flow) handles: input-main, input-chat, input-task
    if (handle.startsWith('input-') && handle !== 'input-main' && handle !== 'input-chat' && handle !== 'input-task') {
      return true;
    }
    return false;
  };

  // Helper to check if a node is a config/auxiliary node (connects to config handles).
  // Backend auto-derives `uiHints.isConfigNode` from group membership
  // (`memory` / `tool`) at registration time — see
  // services/plugin/base.py::_derive_auto_ui_hints. Plugins can also
  // set the flag explicitly to override.
  const isConfigNode = (nodeType: string | undefined): boolean => {
    if (!nodeType) return false;
    const definition = resolveNodeDescription(nodeType);
    return definition?.uiHints?.isConfigNode === true;
  };

  // Get connected nodes with their outputs
  const getConnectedNodes = (): ConnectedNodeData[] => {
    if (!currentWorkflow || !nodeId) return [];

    const connectedNodes: ConnectedNodeData[] = [];
    const addedNodeIds = new Set<string>();

    // Helper to add a node to connected list
    const addConnectedNode = (sourceNodeId: string, label?: string) => {
      if (addedNodeIds.has(sourceNodeId)) return;

      const sourceNode = currentWorkflow.nodes.find(n => n.id === sourceNodeId);
      if (!sourceNode?.type) return;

      const sourceDefinition = resolveNodeDescription(sourceNode.type);
      if (!sourceDefinition) return;

      addedNodeIds.add(sourceNodeId);
      connectedNodes.push({
        nodeId: sourceNode.id,
        nodeName: label ? `${sourceDefinition.displayName} (${label})` : sourceDefinition.displayName,
        outputs: getNodeOutputs(sourceNode.type)
      });
    };

    // Find all edges that connect TO the current node
    const incomingEdges = currentWorkflow.edges.filter(edge => edge.target === nodeId);

    // Get current node info
    const currentNode = currentWorkflow.nodes.find(n => n.id === nodeId);
    const currentNodeType = currentNode?.type;

    for (const edge of incomingEdges) {
      // Skip config handle connections - they're for auxiliary nodes, not main data flow
      if (isConfigHandle(edge.targetHandle)) {
        continue;
      }
      addConnectedNode(edge.source);
    }

    // If current node is a config node (memory, tool), inherit parent node's main inputs
    if (isConfigNode(currentNodeType)) {
      // Find which parent node this config node is connected to
      const outgoingEdges = currentWorkflow.edges.filter(edge => edge.source === nodeId);

      for (const edge of outgoingEdges) {
        // Check if connected to a config handle on the target
        if (isConfigHandle(edge.targetHandle)) {
          const targetNode = currentWorkflow.nodes.find(n => n.id === edge.target);
          if (!targetNode) continue;

          const targetDef = resolveNodeDescription(targetNode.type || '');
          const targetName = targetDef?.displayName || targetNode.type;

          // Find nodes connected to the parent's main input (non-config handles)
          const parentInputEdges = currentWorkflow.edges.filter(
            e => e.target === targetNode.id && !isConfigHandle(e.targetHandle)
          );

          for (const parentEdge of parentInputEdges) {
            addConnectedNode(parentEdge.source, `via ${targetName}`);
          }
        }
      }
    }

    return connectedNodes;
  };

  const connectedNodes = getConnectedNodes();

  const handleDragStart = (e: React.DragEvent, sourceNodeId: string, outputName: string) => {
    setIsDragging(true);
    setDraggedParam({ nodeId: sourceNodeId, output: outputName });
    e.dataTransfer.effectAllowed = 'copy';

    // Use the same template naming as InputSection for consistency
    // This ensures Android nodes and all other nodes work properly with template variables
    const templateName = getTemplateVariableName(sourceNodeId);

    e.dataTransfer.setData('text/plain', `{{${templateName}.${outputName}}}`);
  };

  const handleDragEnd = () => {
    setIsDragging(false);
    setDraggedParam(null);
  };

  if (connectedNodes.length === 0) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center border-r border-border-default bg-bg-panel p-3"
        style={{ width: '300px' }}
      >
        <p className="m-0 text-center text-sm italic text-fg-muted">
          No connected nodes.
          <br />
          Connect nodes to see their outputs here.
        </p>
      </div>
    );
  }

  return (
    <div
      className="flex h-full flex-col border-r border-border-default bg-bg-panel"
      style={{ width: '300px' }}
    >
      {/* Header */}
      <div className="border-b border-border-default bg-bg-panel p-3 font-display text-sm font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
        Connected Outputs
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto p-2">
        {connectedNodes.map((node) => (
          <div
            key={node.nodeId}
            className="mb-1 overflow-hidden rounded-sm border border-border-default bg-bg-elevated"
          >
            <div
              onClick={() => setExpandedNode(expandedNode === node.nodeId ? null : node.nodeId)}
              className="flex cursor-pointer items-center justify-between bg-bg-panel p-2 transition-colors"
            >
              <span className="text-sm font-medium text-fg-default">
                {node.nodeName}
              </span>
              <span
                className="text-xs text-fg-muted"
                style={{
                  transform: expandedNode === node.nodeId ? 'rotate(180deg)' : 'rotate(0deg)',
                  transition: 'transform 0.2s ease'
                }}
              >
                ▼
              </span>
            </div>

            {expandedNode === node.nodeId && (
              <div className="border-t border-border-default p-2">
                {node.outputs.length === 0 ? (
                  <p className="m-0 p-1 text-xs italic text-fg-muted">
                    No output parameters available
                  </p>
                ) : (
                  node.outputs.map((output) => {
                    const isActiveDrag =
                      isDragging &&
                      draggedParam?.nodeId === node.nodeId &&
                      draggedParam?.output === output.name;
                    return (
                      <div
                        key={output.name}
                        draggable
                        onDragStart={(e) => handleDragStart(e, node.nodeId, output.name)}
                        onDragEnd={handleDragEnd}
                        className={`mb-1 cursor-grab rounded-sm border border-border-default p-1 transition-colors ${
                          isActiveDrag ? 'bg-accent' : 'bg-bg-panel'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <div>
                            <div className="mb-0.5 text-sm font-medium text-fg-default">
                              {output.displayName}
                            </div>
                            <div className="text-xs text-fg-muted">
                              {output.type} • {output.description}
                            </div>
                          </div>
                          <div className="rounded-sm border border-border-default bg-bg-elevated px-1 py-0.5 font-mono text-xs text-fg-muted">
                            {output.name}
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="border-t border-border-default bg-bg-panel p-2 text-center text-xs text-fg-muted">
        Drag outputs to parameter fields
      </div>
    </div>
  );
};

export default OutputPanel;
