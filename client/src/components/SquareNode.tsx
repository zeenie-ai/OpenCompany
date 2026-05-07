import React, { memo, useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { nodePropsEqual } from './nodeMemoEquality';
import { Handle, Position, NodeProps } from 'reactflow';
import { NodeData } from '../types/NodeTypes';
import { useAppStore } from '../store/useAppStore';
import { useAppTheme } from '../hooks/useAppTheme';
import EditableNodeLabel from './ui/EditableNodeLabel';
import { useWebSocket, useWhatsAppStatus, useNodeStatus } from '../contexts/WebSocketContext';
import { getCachedNodeSpec, isNodeInBackendGroup, resolveNodeDescription, useNodeSpec } from '../lib/nodeSpec';
import { NodeIcon } from '../assets/icons';
import { AI_MODEL_PROVIDER_MAP } from '../lib/aiModelProviders';
import { useProviderStored } from '../hooks/useCatalogueQuery';

// Nodes with 'tool' in their group can connect to AI Agent/Zeenie tool handles
const hasToolGroup = (definition: any): boolean => {
  const groups = definition?.group || [];
  return groups.includes('tool');
};

// Wave 10.E: AI model dispatch + provider-id-by-type still need a
// frontend lookup because the per-provider visual mapping
// (icon component, credential key) lives in the icon registry below.
// The map of node types itself comes from the backend NodeSpec registry.
const AI_MODEL_NODE_TYPES = AI_MODEL_PROVIDER_MAP;

const CREDENTIAL_TO_PROVIDER: Record<string, string> = {
  googleMapsApi: 'google_maps',
  openaiApi: 'openai',
  anthropicApi: 'anthropic',
  googleAiApi: 'gemini',
};

const SquareNode: React.FC<NodeProps<NodeData>> = ({ id, type, data, isConnectable, selected }) => {
  const theme = useAppTheme();
  // Slice selectors so an unrelated store mutation (sidebar toggle,
  // workflow rename, parameter save on another node) does NOT re-render
  // every canvas node. Setters are stable refs — single-field selector
  // is the cheapest read.
  const setSelectedNode = useAppStore((s) => s.setSelectedNode);
  const setRenamingNodeId = useAppStore((s) => s.setRenamingNodeId);
  const updateNodeData = useAppStore((s) => s.updateNodeData);
  const isDisabled = data?.disabled === true;

  // Get Android status + API key status from the broad WebSocket
  // context, but subscribe to node status via the per-id slice selector
  // so an unrelated node's status update does NOT re-render this node.
  // Reference: zustand slice subscription pattern, see
  // https://github.com/pmndrs/zustand#selecting-multiple-state-slices
  const { androidStatus, getApiKeyStatus } = useWebSocket();
  const nodeStatus = useNodeStatus(id);
  const executionStatus = nodeStatus?.status || 'idle';

  // Minimum glow duration state - keeps glow visible for at least 500ms
  const [isGlowing, setIsGlowing] = useState(false);
  const glowTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Track when execution starts to ensure minimum glow duration
  useEffect(() => {
    if (executionStatus === 'executing' || executionStatus === 'waiting') {
      // Start glowing immediately
      setIsGlowing(true);
      // Clear any existing timeout that would stop the glow
      if (glowTimeoutRef.current) {
        clearTimeout(glowTimeoutRef.current);
        glowTimeoutRef.current = null;
      }
    } else if (isGlowing) {
      // Execution ended but we're still glowing - set timeout to stop after minimum duration
      if (!glowTimeoutRef.current) {
        glowTimeoutRef.current = setTimeout(() => {
          setIsGlowing(false);
          glowTimeoutRef.current = null;
        }, 500);
      }
    }

    return () => {
      if (glowTimeoutRef.current) {
        clearTimeout(glowTimeoutRef.current);
        glowTimeoutRef.current = null;
      }
    };
  }, [executionStatus, isGlowing]);

  // Wave 10.E: backend NodeSpec group membership, with a definition.group
  // fallback for the brief window before the cache warms. No more local
  // type arrays; isNodeInBackendGroup returns undefined when the cache
  // is cold, in which case we read the bundled definition's group list.
  const groupOf = (t: string | undefined): string[] => (t ? (resolveNodeDescription(t)?.group ?? []) : []);
  const inGroup = (t: string | undefined, g: string): boolean =>
    isNodeInBackendGroup(t, g) ?? groupOf(t).includes(g);
  const isGoogleMapsNode = inGroup(type, 'location');
  const googleMapsKeyStatus = isGoogleMapsNode ? getApiKeyStatus('google_maps') : undefined;

  // Check if this is an AI model node and get reactive API key status
  const isAIModelNode = type ? type in AI_MODEL_NODE_TYPES : false;
  const aiProviderId = type && AI_MODEL_NODE_TYPES[type] ? AI_MODEL_NODE_TYPES[type] : null;
  const aiKeyStatus = aiProviderId ? getApiKeyStatus(aiProviderId) : undefined;

  // Wave 6 Phase 3e: backend NodeSpec -> legacy fallback. Flag off or
  // cache cold -> returns local nodeDefinitions entry unchanged.
  const definition = resolveNodeDescription(type || '');

  // Wave 10.E: backend group membership with bundled-definition fallback
  const isAndroidNode = inGroup(type, 'android');

  // Check if this node can be used as a tool: any Android service node
  // can connect to Android Toolkit, plus any node carrying the 'tool' group.
  const isToolCapable = isAndroidNode || hasToolGroup(definition);

  // Android connection status from WebSocket (real-time updates)
  // Service nodes need a paired device to execute, not just relay connection
  const isAndroidConnected = isAndroidNode && androidStatus.paired;

  const isWhatsAppNode = inGroup(type, 'whatsapp');

  // WhatsApp connection status from WebSocket (real-time updates)
  const whatsappStatus = useWhatsAppStatus();

  // Execution state - waiting is treated identically to executing
  // Also respect minimum glow duration for fast-executing tools
  const isExecuting = executionStatus === 'executing' || executionStatus === 'waiting' || isGlowing;


  const defaultLabel = definition?.displayName || type || '';
  // useAppStore.updateNodeData performs `{ ...node.data, ...newData }`
  // so we only need to pass the changed field. Dropping `data` from deps
  // keeps the callback ref stable across parent re-renders, which lets
  // the React.memo wrapper actually short-circuit.
  const handleLabelChange = useCallback(
    (newLabel: string) => updateNodeData(id, { label: newLabel }),
    [id, updateNodeData]
  );
  const handleLabelActivate = useCallback(
    () => setRenamingNodeId(id),
    [id, setRenamingNodeId]
  );

  // Provider id for legacy credentialed nodes (non-AI, non-maps). AI
  // model nodes route through `aiProviderId` above, Google Maps through
  // `googleMapsKeyStatus` — both read from WS-context apiKeyStatuses.
  const providerId = useMemo<string>(() => {
    const credName = definition?.credentials?.[0]?.name;
    if (credName && CREDENTIAL_TO_PROVIDER[credName]) return CREDENTIAL_TO_PROVIDER[credName];
    if (type?.includes('map') || type?.includes('location')) return 'google_maps';
    return '';
  }, [definition?.credentials, type]);

  // "Is a credential configured for this provider?" reads from the
  // server-driven catalogue (single source of truth — the `stored`
  // flag is computed from `auth_service.has_valid_key()` on every
  // catalogue read). The retired `apiKeyStatuses[id].hasKey` mirror
  // duplicated this answer with no synchronisation contract.
  const hasApiKey = useProviderStored(providerId);
  const isConfigured = hasApiKey && !!data && Object.keys(data).length > 0;

  const handleParametersClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedNode({ id, type, data, position: { x: 0, y: 0 } });
  };

  // Get status indicator color based on execution state
  const getStatusIndicatorColor = () => {
    // For executing or waiting state, show blue/cyan
    if (executionStatus === 'executing' || executionStatus === 'waiting') {
      return theme.dracula.cyan;
    }
    if (executionStatus === 'success') {
      return theme.dracula.green;
    }
    if (executionStatus === 'error') {
      return theme.dracula.red;
    }

    // Idle state - use Android or configuration status
    if (isAndroidNode) {
      return isAndroidConnected ? theme.dracula.green : theme.dracula.red;
    }

    // WhatsApp nodes - use WebSocket connection status
    if (isWhatsAppNode) {
      if (whatsappStatus.connected) return theme.dracula.green;
      if (whatsappStatus.pairing) return theme.dracula.orange;
      return theme.dracula.red;
    }

    // Google Maps nodes - use WebSocket API key validation status
    if (isGoogleMapsNode && googleMapsKeyStatus) {
      return googleMapsKeyStatus.valid ? theme.dracula.green : theme.dracula.red;
    }

    // AI Model nodes - "is stored" from catalogue (hasApiKey via
    // useProviderStored above), "is validated" from apiKeyStatuses.
    if (isAIModelNode) {
      if (aiKeyStatus?.valid && hasApiKey) return theme.dracula.green;
      if (hasApiKey) return theme.dracula.orange;
      return theme.dracula.red;
    }

    return isConfigured ? theme.dracula.green : hasApiKey ? theme.dracula.orange : theme.dracula.red;
  };

  const getStatusTitle = () => {
    switch (executionStatus) {
      case 'executing':
        return 'Executing...';
      case 'waiting':
        return nodeStatus?.message || 'Waiting for event...';
      case 'success':
        return 'Execution successful';
      case 'error':
        return `Error: ${nodeStatus?.data?.error || 'Unknown error'}`;
      default:
        if (isAndroidNode) {
          return isAndroidConnected ? 'Android device connected' : 'Android device not connected';
        }
        // WhatsApp nodes - use WebSocket connection status
        if (isWhatsAppNode) {
          if (whatsappStatus.connected) return 'WhatsApp connected';
          if (whatsappStatus.pairing) return 'Pairing in progress...';
          if (whatsappStatus.running) return 'WhatsApp service running';
          return 'WhatsApp not connected';
        }
        // Google Maps nodes - use WebSocket API key validation status
        if (isGoogleMapsNode && googleMapsKeyStatus) {
          return googleMapsKeyStatus.valid
            ? 'Google Maps API key validated'
            : `API key invalid: ${googleMapsKeyStatus.message || 'Validation failed'}`;
        }
        // AI Model nodes - "is stored" from catalogue (hasApiKey),
        // "is validated" from apiKeyStatuses. Independent concerns
        // post-Wave-12; the two sources don't drift across tabs.
        if (isAIModelNode) {
          if (aiKeyStatus?.valid && hasApiKey) {
            return `${aiProviderId?.charAt(0).toUpperCase()}${aiProviderId?.slice(1)} API key validated`;
          }
          if (hasApiKey) {
            return 'API key found, validation pending';
          }
          return 'API key required - configure in Credentials';
        }
        return isConfigured
          ? 'Service configured and ready'
          : hasApiKey
            ? 'API key found, service needs configuration'
            : 'API key required';
    }
  };


  // Schema-driven icon dispatch. `useNodeSpec` subscribes to the
  // NodeSpec cache so the icon populates when prefetch lands without
  // waiting for a parent re-render. <NodeIcon> resolves the ref and
  // tints lucide icons with the node's brand color via currentColor.
  const iconSpec = useNodeSpec(type);
  const iconRef = (iconSpec?.icon as string | undefined) ?? (definition?.icon as string | undefined);

  // Get the node color from definition or use default
  const nodeColor = definition?.defaults?.color || '#1A73E8';

  return (
    // `sq-node` + `selected` co-classes are the design-handoff structural
    // hooks for per-theme square-node decorations (rivets on Steampunk
    // sq-node-box::before/::after, hanko seal on Edo, REC LED + surv-blink
    // on Surveillance, tombstone shape on Rot, gold-foil emblem on
    // Renaissance, neon underglow on Cyber, etc.).
    <div
      className={`sq-node ${selected ? 'selected' : ''}`}
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        fontSize: '11px',
        cursor: 'pointer',
      }}
    >
      {/* Main Square Node */}
      <div
        className="sq-node-box"
        style={{
          position: 'relative',
          width: theme.nodeSize.square,
          height: theme.nodeSize.square,
          borderRadius: theme.borderRadius.lg,
          background: theme.isDarkMode
            ? `linear-gradient(135deg, ${nodeColor}25 0%, ${theme.colors.background} 100%)`
            : `linear-gradient(145deg, #ffffff 0%, ${nodeColor}08 100%)`,
          border: `2px solid ${isExecuting
            ? (theme.isDarkMode ? theme.dracula.cyan : '#2563eb')
            : selected
              ? theme.colors.focus
              : theme.isDarkMode ? nodeColor + '80' : `${nodeColor}40`}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: theme.colors.text,
          fontSize: theme.nodeSize.squareIcon,
          fontWeight: '600',
          transition: 'all 0.2s ease',
          boxShadow: isExecuting
            ? theme.isDarkMode
              ? `0 4px 12px ${theme.dracula.cyan}66, 0 0 0 3px ${theme.dracula.cyan}4D`
              : `0 0 0 3px rgba(37, 99, 235, 0.5), 0 4px 16px rgba(37, 99, 235, 0.35)`
            : selected
              ? `0 4px 12px ${theme.colors.focusRing}, 0 0 0 1px ${theme.colors.focusRing}`
              : theme.isDarkMode
                ? `0 2px 8px ${nodeColor}40`
                : `0 2px 8px ${nodeColor}20, 0 4px 12px rgba(0,0,0,0.06)`,
          animation: isExecuting ? 'pulse 1.5s ease-in-out infinite' : 'none',
          opacity: isDisabled ? 0.5 : 1,
        }}
      >
        {/* Disabled Overlay */}
        {isDisabled && (
          <div style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(128, 128, 128, 0.4)',
            borderRadius: 'inherit',
            zIndex: 35,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            pointerEvents: 'none',
          }}>
            <span style={{ fontSize: '20px', opacity: 0.8, color: theme.colors.textSecondary }}>||</span>
          </div>
        )}


        {/* Service Icon */}
        <NodeIcon icon={iconRef} className="h-7 w-7 text-3xl" />

        {/* Parameters Button */}
        <button
          onClick={handleParametersClick}
          style={{
            position: 'absolute',
            top: '-8px',
            right: '-8px',
            width: theme.nodeSize.paramButton,
            height: theme.nodeSize.paramButton,
            borderRadius: theme.borderRadius.sm,
            backgroundColor: theme.isDarkMode ? theme.colors.backgroundAlt : '#ffffff',
            border: `1px solid ${theme.isDarkMode ? theme.colors.border : '#d1d5db'}`,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: theme.fontSize.xs,
            color: theme.colors.textSecondary,
            fontWeight: '400',
            transition: theme.transitions.fast,
            zIndex: 30,
            boxShadow: theme.isDarkMode
              ? `0 1px 3px ${theme.colors.shadow}`
              : '0 1px 4px rgba(0,0,0,0.1)'
          }}
          title="Edit Service Parameters"
          className="sq-node-gear"
        >
          ⚙️
        </button>

        {/* Configuration/Execution Status Indicator */}
        <div
          className="sq-node-pip"
          style={{
            position: 'absolute',
            top: '-4px',
            left: '-4px',
            width: theme.nodeSize.statusIndicator,
            height: theme.nodeSize.statusIndicator,
            borderRadius: '50%',
            backgroundColor: getStatusIndicatorColor(),
            border: `2px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
            boxShadow: isExecuting
              ? theme.isDarkMode
                ? `0 0 6px ${theme.dracula.cyan}80`
                : '0 0 4px rgba(37, 99, 235, 0.5)'
              : theme.isDarkMode
                ? `0 1px 2px ${theme.colors.shadow}`
                : '0 1px 3px rgba(0,0,0,0.15)',
            zIndex: 30,
            animation: isExecuting ? 'pulse 1s ease-in-out infinite' : 'none',
          }}
          title={getStatusTitle()}
        />

        {/* Square Input Handle (gated by spec.hideInputHandle, mirrors
            the hideOutputHandle pattern below — auto-derived True for
            usable_as_tool=True nodes via BaseNode.__init_subclass__). */}
        {!getCachedNodeSpec(type || '')?.hideInputHandle && (
          <Handle
            id="input-main"
            type="target"
            position={Position.Left}
            isConnectable={isConnectable}
            className="sq-node-handle in"
            style={{
              position: 'absolute',
              left: '-6px',
              top: '50%',
              transform: 'translateY(-50%)',
              width: theme.nodeSize.handle,
              height: theme.nodeSize.handle,
              backgroundColor: theme.isDarkMode ? theme.colors.background : '#ffffff',
              border: `2px solid ${theme.isDarkMode ? theme.colors.textSecondary : '#6b7280'}`,
              borderRadius: '50%',
              zIndex: 20
            }}
            title="Service Input"
          />
        )}

        {/* Square Output Handle (Wave 10.E: spec.hideOutputHandle replaces the
            local NO_OUTPUT_NODE_TYPES list) */}
        {!getCachedNodeSpec(type || '')?.hideOutputHandle && (
          <Handle
            id="output-main"
            type="source"
            position={Position.Right}
            isConnectable={isConnectable}
            className="sq-node-handle out"
            style={{
              position: 'absolute',
              right: '-6px',
              top: '50%',
              transform: 'translateY(-50%)',
              width: theme.nodeSize.handle,
              height: theme.nodeSize.handle,
              backgroundColor: isConfigured ? nodeColor : theme.colors.textSecondary,
              border: `2px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
              borderRadius: '50%',
              zIndex: 20
            }}
            title="Service Output"
          />
        )}

        {/* Top Tool Output Handle.
            Wave 10.E: color + label come from the node's own spec. The
            `nodeColor` carries the spec's brand color (Android green for
            Android services, dracula accents elsewhere). The tooltip
            reads from the spec's top-position output handle when one is
            declared there; otherwise falls back to a generic label. */}
        {isToolCapable && (() => {
          const spec = getCachedNodeSpec(type || '');
          const topOut = spec?.handles?.find(h => h.kind === 'output' && h.position === 'top');
          const tooltip = topOut?.label ?? 'Tool Output';
          return (
            <Handle
              id="output-tool"
              type="source"
              position={Position.Top}
              isConnectable={isConnectable}
              style={{
                position: 'absolute',
                top: '-6px',
                left: '50%',
                transform: 'translateX(-50%)',
                width: theme.nodeSize.handle,
                height: theme.nodeSize.handle,
                backgroundColor: nodeColor,
                border: `2px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
                borderRadius: '50%',
                zIndex: 20,
              }}
              title={tooltip}
            />
          );
        })()}

        {/* Output Data Indicator - shows when node has execution output */}
        {executionStatus === 'success' && nodeStatus?.data && (
          <div
            style={{
              position: 'absolute',
              bottom: '-4px',
              right: '-4px',
              width: theme.nodeSize.outputBadge,
              height: theme.nodeSize.outputBadge,
              borderRadius: theme.borderRadius.sm,
              backgroundColor: theme.dracula.green,
              border: `1px solid ${theme.isDarkMode ? theme.colors.background : '#ffffff'}`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: theme.fontSize.xs,
              color: 'white',
              fontWeight: 'bold',
              zIndex: 30,
              boxShadow: theme.isDarkMode
                ? '0 1px 3px rgba(0,0,0,0.2)'
                : '0 1px 3px rgba(0,0,0,0.15)',
            }}
            title="Output data available - click node to view"
          >
            <span style={{ lineHeight: 1 }}>D</span>
          </div>
        )}
      </div>

      {/* Service Name Below Square */}
      <EditableNodeLabel
        nodeId={id}
        label={data?.label}
        defaultLabel={defaultLabel}
        onLabelChange={handleLabelChange}
        onActivate={handleLabelActivate}
      />

    </div>
  );
};

export default memo(SquareNode, nodePropsEqual);