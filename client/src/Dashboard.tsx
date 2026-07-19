/* eslint-disable react-hooks/exhaustive-deps -- curated dep arrays; reactive-graph derivations + workflow debouncers intentionally omit deps to avoid infinite loops. */
import React, { useEffect } from 'react';
import { toast } from 'sonner';
import {
  ReactFlow,
  ReactFlowProvider,
  Controls,
  useNodesState,
  useEdgesState,
  useReactFlow,
  ConnectionMode,
  ConnectionLineType,
  SelectionMode,
  Node,
  Edge,
} from 'reactflow';
import { featureFlags } from './lib/featureFlags';
import { prefetchAllNodeSpecs, listCachedNodeSpecs, cachedNodeSpecTypesKey } from './lib/nodeSpec';
import AIAgentNode from './components/AIAgentNode';
import SquareNode from './components/SquareNode';
import TriggerNode from './components/TriggerNode';
import ToolkitNode from './components/ToolkitNode';
import TeamMonitorNode from './components/TeamMonitorNode';
import StartNode from './components/StartNode';
import ConditionalEdge from './components/ConditionalEdge';
import NodeContextMenu from './components/ui/NodeContextMenu';
import { getNodeTypesInGroup } from './lib/nodeSpec';
import ParameterPanel from './ParameterPanel';
import LocationParameterPanel from './components/LocationParameterPanel';
import { useAppStore } from './store/useAppStore';
import ComponentPalette from './components/ui/ComponentPalette';
import TopToolbar from './components/ui/TopToolbar';
import WorkflowSidebar from './components/ui/WorkflowSidebar';
import SettingsPanel, { WorkflowSettings, defaultSettings } from './components/ui/SettingsPanel';
import AIResultModal from './components/ui/AIResultModal';
import CredentialsModal from './components/CredentialsModal';
import OnboardingWizard from './components/onboarding/OnboardingWizard';
import GetStartedChecklist from './components/onboarding/GetStartedChecklist';
import { useSaveUserSettingsMutation } from './hooks/useUserSettingsQuery';
import ErrorBoundary from './components/ui/ErrorBoundary';
import ConsolePanel from './components/ui/ConsolePanel';
import StatusBar from './components/ui/StatusBar';
import CommandPaletteHost from './components/ui/CommandPaletteHost';
import { useSoundSync, withSound } from './hooks/useSound';
import { useAppTheme } from './hooks/useAppTheme';
import { useWorkflowManagement } from './hooks/useWorkflowManagement';
import { useWorkflowsQuery, WORKFLOWS_QUERY_KEY } from './hooks/useWorkflowsQuery';
import { useQueryClient } from '@tanstack/react-query';
import { workflowApi } from './services/workflowApi';
import { useDragAndDrop } from './hooks/useDragAndDrop';
import { useComponentPalette } from './hooks/useComponentPalette';
import { useReactFlowNodes } from './hooks/useReactFlowNodes';
import { useAutoSkillEdges } from './hooks/useAutoSkillEdges';
import { useWorkflowOpsListener } from './hooks/useWorkflowOpsListener';
import { useCopyPaste } from './hooks/useCopyPaste';
import { useWebSocket } from './contexts/WebSocketContext';
import { useNodeStatusStore } from './stores/nodeStatusStore';
import {
  sanitizeNodesForComparison,
  sanitizeEdgesForComparison,
} from './utils/workflow';
import { importWorkflowFromFile } from './utils/workflowExport';
import type { ValidationIssue } from './hooks/useWorkflowValidation';
import { buildCanvasStyles } from './styles/canvasAnimations';

/** Wire shape returned by the backend ``import_workflow`` WS handler.
 *  Mirrors ``services.workflow_import.import_workflow`` — flat optional
 *  fields rather than a discriminated union, since callers branch on
 *  ``response.success`` + ``response.preview`` anyway. */
interface ImportWorkflowResponse {
  success: boolean;
  preview?: boolean;
  workflow_id?: string;
  name?: string;
  node_count?: number;
  edge_count?: number;
  saved_parameters?: number;
  error?: string;
  report?: { errors: ValidationIssue[]; warnings: ValidationIssue[] };
  missing_credentials?: Array<{ provider_id: string; display_name: string; kind: string }>;
  name_conflict?: boolean;
  suggested_name?: string | null;
  requirements?: {
    credentials: Array<{ provider_id: string }>;
    nodes: Array<{ type: string; version: number }>;
  };
}

import 'reactflow/dist/style.css';

// Wave 10.D step 2: React-component dispatch table.
//
// The frontend's only per-node-type knowledge: which React Flow
// component renders which `componentKind`. Everything else (icon,
// color, handles, subtitle, size, uiHints) comes from the backend
// NodeSpec served by server/nodes/*.py plugin modules.
//
// `tool` and `square` both render via SquareNode today; `chat` reuses
// AIAgentNode (the chat agent has the same handle topology). Adding a
// new componentKind takes one entry here + the corresponding Pydantic
// `Literal` value in server/models/node_metadata.NodeMetadata.
const COMPONENT_BY_KIND: Record<string, React.ComponentType<any>> = {
  start: StartNode,
  trigger: TriggerNode,
  agent: AIAgentNode,
  chat: AIAgentNode,
  model: SquareNode,
  square: SquareNode,
  tool: SquareNode,
  generic: SquareNode,
};

// Build the React Flow `nodeTypes` map: spec.componentKind → component.
// Falls back to a small set of legacy hints for the few cases the spec
// doesn't yet cover (skill nodes use ToolkitNode; teamMonitor has its
// own live-display component). Once those are spec-driven too, the
// fallback collapses to a single `SquareNode` default.
const createNodeTypes = (): Record<string, React.ComponentType<any>> => {
  const types: Record<string, React.ComponentType<any>> = {};
  // Cache-driven enumeration: empty on cold boot, filled once
  // prefetchAllNodeSpecs resolves and the `specsKey` change rebuilds.
  listCachedNodeSpecs().forEach(spec => {
    const kind = spec.componentKind;
    if (kind && COMPONENT_BY_KIND[kind]) {
      types[spec.type] = COMPONENT_BY_KIND[kind];
    } else if (spec.type === 'teamMonitor') {
      types[spec.type] = TeamMonitorNode;
    } else if ((spec.uiHints as any)?.isMasterSkillEditor === true) {
      types[spec.type] = ToolkitNode;
    } else {
      types[spec.type] = SquareNode;
    }
  });
  return types;
};

// Edge types configuration - enables conditional edge rendering.
// nodeTypes is built inside the component (see useMemo below) so the
// build runs after PersistQueryClientProvider has hydrated the cache
// from localStorage. A module-scope build runs at import time when
// the cache is always empty, which forces React Flow to remount every
// canvas node when prefetch lands (the "canvas-wide snap" symptom).
const moduleEdgeTypes = {
  conditional: ConditionalEdge,
};

const initialNodes: Node[] = [];
const initialEdges: Edge[] = [];

// Inner component that uses useReactFlow() - must be inside ReactFlowProvider
const DashboardContent: React.FC = () => {
  const theme = useAppTheme();
  // Slice selectors so a sidebar/palette toggle (or any other store
  // mutation) does NOT re-render every node, edge, and toolbar in the
  // Dashboard subtree. Action setters are stable refs from Zustand —
  // single-field selectors are the cheapest way to read them.
  const currentWorkflow = useAppStore((s) => s.currentWorkflow);
  const hasUnsavedChanges = useAppStore((s) => s.hasUnsavedChanges);
  const sidebarVisible = useAppStore((s) => s.sidebarVisible);
  const componentPaletteVisible = useAppStore((s) => s.componentPaletteVisible);
  const updateWorkflow = useAppStore((s) => s.updateWorkflow);
  const loadWorkflow = useAppStore((s) => s.loadWorkflow);
  const createNewWorkflow = useAppStore((s) => s.createNewWorkflow);
  const deleteWorkflow = useAppStore((s) => s.deleteWorkflow);
  const migrateCurrentWorkflow = useAppStore((s) => s.migrateCurrentWorkflow);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const toggleComponentPalette = useAppStore((s) => s.toggleComponentPalette);
  const proMode = useAppStore((s) => s.proMode);
  const toggleProMode = useAppStore((s) => s.toggleProMode);
  const exportWorkflowToJSON = useAppStore((s) => s.exportWorkflowToJSON);
  const exportWorkflowToFile = useAppStore((s) => s.exportWorkflowToFile);
  const selectedNode = useAppStore((s) => s.selectedNode);
  const setSelectedNode = useAppStore((s) => s.setSelectedNode);
  const renamingNodeId = useAppStore((s) => s.renamingNodeId);
  const setRenamingNodeId = useAppStore((s) => s.setRenamingNodeId);
  const openExampleAndChat = useAppStore((s) => s.openExampleAndChat);
  const saveUserSettings = useSaveUserSettingsMutation();
  // Per-workflow UI state (n8n pattern)
  const setWorkflowExecuting = useAppStore((s) => s.setWorkflowExecuting);
  const setWorkflowExecutionOrder = useAppStore((s) => s.setWorkflowExecutionOrder);
  const setWorkflowViewport = useAppStore((s) => s.setWorkflowViewport);
  const clearWorkflowExecutionState = useAppStore((s) => s.clearWorkflowExecutionState);
  
  // Single source-to-store sync: push currentWorkflow.id into the
  // node-status Zustand store from the canonical app store. Previously
  // this was mirrored from inside WebSocketProvider; consolidating here
  // removes the multi-mirror gap that left broadcasts landing in the
  // wrong workflow bucket during workflow switches.
  useEffect(() => {
    useNodeStatusStore.getState().setCurrentWorkflowId(currentWorkflow?.id);
  }, [currentWorkflow?.id]);

  // ReactFlow state management (local state for performance)
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // ReactFlow instance for viewport control (n8n pattern - per-workflow viewport)
  const reactFlowInstance = useReactFlow();

  // AI execution state - result and modal are local, execution tracking is per-workflow
  const [executionResult, setExecutionResult] = React.useState<any>(null);
  const [showResult, setShowResult] = React.useState(false);

  // Get per-workflow execution state (n8n pattern - isolated per workflow)
  // Subscribe to workflowUIStates directly so Zustand triggers re-renders when it changes
  const workflowUIStates = useAppStore(state => state.workflowUIStates);
  const workflowUIState = React.useMemo(() => {
    if (!currentWorkflow?.id) return null;
    return workflowUIStates[currentWorkflow.id] || { isExecuting: false, executedNodes: [], executionOrder: [], selectedNodeId: null };
  }, [workflowUIStates, currentWorkflow?.id]);
  const isExecuting = workflowUIState?.isExecuting || false;
  const executedNodes = React.useMemo(() => new Set(workflowUIState?.executedNodes || []), [workflowUIState?.executedNodes]);
  const executionOrder = workflowUIState?.executionOrder || [];
  // Custom hooks for different concerns
  const { 
    handleWorkflowNameChange,
    handleSave,
    handleNew,
    handleOpen,
    handleSelectWorkflow,
  } = useWorkflowManagement();

  const { collapsedSections, searchQuery, setSearchQuery, toggleSection } = useComponentPalette();
  const { saveNodeParameters, getAllNodeParameters, executeWorkflow, deployWorkflow, getWorkflowStatus, nodeStatuses, deploymentStatus, workflowControlStatuses, startWorkflow, pauseWorkflow, resumeWorkflow, resetWorkflow, getWorkflowControlStatus, workflowLock, isConnected, isReady, sendRequest } = useWebSocket();
  const applyUIDefaults = useAppStore((state) => state.applyUIDefaults);

  // Workflows list: server-owned data, cached by TanStack Query.
  const queryClient = useQueryClient();
  const { data: savedWorkflows = [] } = useWorkflowsQuery();

  // Scope deployment and lock to current workflow (n8n pattern)
  // Only show as "running" or "locked" if it applies to the currently viewed workflow
  const isCurrentWorkflowDeployed = deploymentStatus.isRunning &&
    deploymentStatus.workflow_id === currentWorkflow?.id;
  // Backend is the source of truth: per-workflow `isExecuting` is set by
  // the `workflow_status` handler whenever ANY active run exists for the
  // current workflow (ad-hoc node, whole-workflow run, deployed trigger
  // run, or deployment registration).  The toolbar Start/Stop button
  // tracks this unified signal so it stays Stop while nodes glow.
  const workflowControl = currentWorkflow?.id
    ? workflowControlStatuses[currentWorkflow.id] || {
        workflow_id: currentWorkflow.id, state: 'never_started' as const, revision: 0,
        active_count: 0, in_flight_count: 0, queued_count: 0,
        can_start: true, can_pause: false, can_resume: false, can_reset: false,
      }
    : { state: 'never_started' as const, revision: 0, active_count: 0, in_flight_count: 0, queued_count: 0, can_start: false, can_pause: false, can_resume: false, can_reset: false };
  const isCurrentWorkflowLocked = workflowLock.locked &&
    workflowLock.workflow_id === currentWorkflow?.id;
  const [globalModelDefaults, setGlobalModelDefaults] = React.useState<{ provider: string; model: string } | null>(null);
  const { onDragOver, onDrop, handleComponentDragStart } = useDragAndDrop({ nodes, setNodes, saveNodeParameters, globalModelDefaults, workflowId: currentWorkflow?.id ?? 'new' });
  const { onConnect: baseOnConnect, onNodesDelete, onEdgesDelete: baseOnEdgesDelete } = useReactFlowNodes({ setNodes, setEdges });
  const { onConnect, onEdgesDelete } = useAutoSkillEdges({
    baseOnConnect,
    baseOnEdgesDelete,
    nodes,
    edges,
    setNodes,
    setEdges,
  });
  // Apply runtime canvas mutations pushed from the backend (e.g.,
  // Agent Builder tools called by the LLM mid-execution).
  useWorkflowOpsListener({ nodes, edges, setNodes, setEdges });
  const { copySelectedNodes, pasteNodes } = useCopyPaste({ nodes, edges, setNodes, setEdges, saveNodeParameters, workflowId: currentWorkflow?.id ?? 'new' });

  // Override all agent nodes to use the global model. Agent membership is
  // derived at call time from the backend-served `group` field — by the
  // time the user hits this button, prefetch has always completed.
  const handleOverrideAllAgents = React.useCallback(async (provider: string, model: string) => {
    const agentTypes = new Set(getNodeTypesInGroup('agent'));
    const agentNodes = nodes.filter(n => agentTypes.has(n.type || ''));
    if (agentNodes.length === 0) return;
    const nodeIds = agentNodes.map(n => n.id);
    const allParams = await getAllNodeParameters(nodeIds);
    await Promise.all(agentNodes.map(n => {
      // `allParams[n.id]` is the NodeParameters wrapper
      // `{parameters, version, timestamp}` — unwrap to the raw params
      // dict before spreading, otherwise we'd persist {parameters: {...},
      // version, timestamp, provider, model} and every declared field
      // (temperature, system_message, prompt, ...) gets buried inside
      // the nested `parameters` key, unreachable by the panel.
      const existing = allParams[n.id]?.parameters || {};
      return saveNodeParameters(n.id, { ...existing, provider, model });
    }));
  }, [nodes, getAllNodeParameters, saveNodeParameters]);

  // Toggle disabled state on selected nodes
  const toggleDisableSelected = React.useCallback(() => {
    setNodes(nds => nds.map(node => {
      if (node.selected) {
        return {
          ...node,
          data: {
            ...node.data,
            disabled: !node.data?.disabled,
          },
        };
      }
      return node;
    }));
  }, [setNodes]);

  // Note: executedNodes and executionOrder are now derived from per-workflow state above

  // Settings state with localStorage persistence
  const [settings, setSettings] = React.useState<WorkflowSettings>(() => {
    try {
      const saved = localStorage.getItem('workflow_settings');
      return saved ? { ...defaultSettings, ...JSON.parse(saved) } : defaultSettings;
    } catch {
      return defaultSettings;
    }
  });
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [credentialsOpen, setCredentialsOpen] = React.useState(false);
  const [onboardingReopenTrigger, setOnboardingReopenTrigger] = React.useState(0);
  const [commandPaletteOpen, setCommandPaletteOpen] = React.useState(false);

  // Console panel visibility from store (database-backed)
  const consolePanelVisible = useAppStore((state) => state.consolePanelVisible);
  const toggleConsolePanelVisible = useAppStore((state) => state.toggleConsolePanelVisible);

  // Sound effects: mirror the soundEnabled slice into the WebAudio
  // engine and re-read --sound-pack from :root on every theme change.
  // Mounting once here keeps every event handler that calls useSound()
  // in lockstep with the active theme + user preference.
  useSoundSync();

  // Wave 33: pause CSS animations while the tab is in the background.
  //
  // Without this, browsers continue advancing CSS animation timing on
  // hidden tabs (RAF is throttled to ~1Hz but `animation` keyframes
  // accumulate paused frames in the compositor's queue). When the user
  // returns, all 50+ executing nodes' three-layer box-shadow `node-pulse`
  // animations resume simultaneously and the GPU compositor stalls for
  // 100-200ms blending the paused frames + Cyber theme's full-viewport
  // `cyber-flicker` / `cyber-roll` decorations. During the stall, input
  // events queue but don't dispatch — first click on tab return appears
  // unresponsive until the composite pass finishes (then the second
  // click works, hence the "wakes up on interaction" pattern).
  //
  // Setting `animation-play-state: paused` on every element via a CSS
  // rule keyed off `<html data-page-hidden>` flushes the queue. The
  // requestAnimationFrame on resume defers the unpause until after the
  // first input is ready to dispatch (one frame's delay, imperceptible).
  useEffect(() => {
    const root = document.documentElement;
    // Wave 33+: visibilitychange alone misses window-minimize on Windows
    // and "switch app" on macOS — neither always fires the Page
    // Visibility API event. window.blur / window.focus do fire in those
    // cases, so we drive the same flag off both signals. The flag
    // toggles based on whichever signal indicates "not interactive":
    // either the tab is hidden OR the window has lost focus.
    let blurred = false;
    const setHidden = (hidden: boolean) => {
      if (hidden) {
        root.setAttribute('data-page-hidden', '');
      } else {
        // Double rAF so the browser has two frames to clear the input
        // dispatch queue before composite resumes — first click after
        // return is then guaranteed to land before paused-frame flush.
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            // Only remove if the page is genuinely active again — a fast
            // tab switch could fire blur+focus in tight succession and
            // we'd otherwise race the second rAF.
            if (!document.hidden && !blurred) {
              root.removeAttribute('data-page-hidden');
            }
          });
        });
      }
    };
    const onVisibility = () => setHidden(document.hidden || blurred);
    const onBlur = () => { blurred = true; setHidden(true); };
    const onFocus = () => { blurred = false; setHidden(document.hidden); };
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('blur', onBlur);
    window.addEventListener('focus', onFocus);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('blur', onBlur);
      window.removeEventListener('focus', onFocus);
    };
  }, []);

  // Context menu state for node right-click
  const [contextMenu, setContextMenu] = React.useState<{
    nodeId: string;
    x: number;
    y: number;
  } | null>(null);

  // Persist settings to localStorage
  React.useEffect(() => {
    localStorage.setItem('workflow_settings', JSON.stringify(settings));
  }, [settings]);

  // Resync per-workflow execution status whenever the WS connection becomes
  // ready or the current workflow changes.  Closes the gap where a mid-run
  // reconnect or a workflow switch left the toolbar Start/Stop button stale
  // because broadcasts only fire on transitions, not on join.
  React.useEffect(() => {
    if (!isReady || !currentWorkflow?.id) return;
    const wfId = currentWorkflow.id;
    let cancelled = false;
    (async () => {
      try {
        const [{ executing }] = await Promise.all([
          getWorkflowStatus(wfId),
          getWorkflowControlStatus(wfId),
        ]);
        if (cancelled) return;
        useAppStore.getState().setWorkflowExecuting(wfId, executing);
      } catch {
        // Silent: the next broadcast will resync; this is opportunistic.
      }
    })();
    return () => { cancelled = true; };
  }, [isReady, currentWorkflow?.id, getWorkflowStatus, getWorkflowControlStatus]);

  // Load UI defaults from database on initial WebSocket connection
  const hasLoadedUIDefaults = React.useRef(false);
  React.useEffect(() => {
    if (!isConnected || hasLoadedUIDefaults.current) return;
    hasLoadedUIDefaults.current = true;

    const loadUIDefaults = async () => {
      try {
        const response = await sendRequest<{ settings: any }>('get_user_settings', {});
        if (response?.settings) {
          applyUIDefaults({
            sidebarDefaultOpen: response.settings.sidebar_default_open,
            componentPaletteDefaultOpen: response.settings.component_palette_default_open,
            consolePanelDefaultOpen: response.settings.console_panel_default_open,
          });
          // Also update local settings state for auto-save preferences
          setSettings(prev => ({
            ...prev,
            autoSave: response.settings.auto_save ?? prev.autoSave,
            autoSaveInterval: response.settings.auto_save_interval ?? prev.autoSaveInterval,
            sidebarDefaultOpen: response.settings.sidebar_default_open ?? prev.sidebarDefaultOpen,
            componentPaletteDefaultOpen: response.settings.component_palette_default_open ?? prev.componentPaletteDefaultOpen,
            consolePanelDefaultOpen: response.settings.console_panel_default_open ?? prev.consolePanelDefaultOpen,
          }));
          console.log('[Dashboard] UI defaults loaded from database');
        }
      } catch (error) {
        console.error('[Dashboard] Failed to load UI defaults:', error);
      }
    };

    loadUIDefaults();
  }, [isConnected, sendRequest, applyUIDefaults]);

  // Wave 6 Phase 2: warm the NodeSpec cache in the background once the
  // WS is up. No-op when VITE_NODESPEC_BACKEND is off. After prefetch
  // completes, recomputing `specsKey` flips the React Flow nodeTypes ref
  // so spec.componentKind dispatch becomes effective (Wave 10.D step 2).
  const hasPrefetchedSpecs = React.useRef(false);
  // Seed from the persisted cache. With PersistQueryClientProvider in
  // place the cache is hydrated from localStorage before the first
  // render, so a warm start can skip the cold→warm remount entirely.
  //
  // `specsKey` is the identity of the cached spec-type SET, not a
  // latched boolean: when the prefetch burst busts a stale persisted
  // revision and lands types the hydrated snapshot didn't have (a node
  // shipped since the last visit), the key change rebuilds nodeTypes.
  // With the old `specsReady` boolean the set-change was invisible
  // (true -> true bails out of setState) and a freshly deployed node
  // type stayed missing from React Flow's map for the whole session —
  // the palette offered it (its groupIndex dep is reactive) but the
  // canvas rendered the fallback default node on first drop.
  const [specsKey, setSpecsKey] = React.useState(() => cachedNodeSpecTypesKey());
  React.useEffect(() => {
    if (!isReady || hasPrefetchedSpecs.current) return;
    if (!featureFlags.nodeSpecBackend) {
      // When the backend is disabled, mark ready so the legacy fallback
      // dispatch runs without waiting on a never-completing prefetch.
      setSpecsKey('__legacy__');
      return;
    }
    hasPrefetchedSpecs.current = true;
    void prefetchAllNodeSpecs(sendRequest).finally(() => setSpecsKey(cachedNodeSpecTypesKey()));
  }, [isReady, sendRequest]);

  // Paint executing / completed / error / pending classes on the React
  // Flow node wrapper so the canvas-wide CSS rules at the top of this
  // file (.react-flow__node.executing, etc.) match -- in particular the
  // nodeGlowDark / nodeGlowLight outer-glow keyframes that per-node
  // inline `pulse` animations cannot replicate. Re-deriving on every
  // nodeStatuses broadcast is cheap because node components are
  // React.memo + useNodeStatus(id) slice-subscribed, so only nodes
  // whose className actually changed will re-render.
  const styledNodes = React.useMemo(() => {
    return nodes.map(node => {
      const status = nodeStatuses[node.id]?.status;
      let className = '';
      if (status === 'executing' || status === 'waiting') {
        className = 'executing';
      } else if (status === 'success') {
        className = 'completed';
      } else if (status === 'error') {
        className = 'error';
      } else if (
        isExecuting &&
        executionOrder.includes(node.id) &&
        !executedNodes.has(node.id)
      ) {
        className = 'pending';
      }
      return { ...node, className };
    });
  }, [nodes, nodeStatuses, isExecuting, executionOrder, executedNodes]);

  // Update edges with execution status classes
  const styledEdges = React.useMemo(() => {
    return edges.map(edge => {
      const sourceStatus = nodeStatuses[edge.source];
      const targetStatus = nodeStatuses[edge.target];
      const targetNode = nodes.find(n => n.id === edge.target);

      let className = '';

      // Check if this edge connects to an AI Agent's memory or tools/skill handle
      const isMemoryConnection = edge.targetHandle === 'input-memory';
      const isToolConnection = edge.targetHandle === 'input-tools';
      const isSkillConnection = edge.targetHandle === 'input-skill';
      const isAIAgentTarget = targetNode?.type === 'aiAgent' || targetNode?.type === 'chatAgent';

      // Highlight memory/tool connections when AI Agent is executing and using them
      if (isAIAgentTarget && targetStatus?.status === 'executing') {
        const phase = targetStatus?.data?.phase as string | undefined;
        const hasMemory = targetStatus?.data?.has_memory;

        // Memory connection highlights during memory phases
        if (isMemoryConnection && hasMemory) {
          if (phase === 'loading_memory' || phase === 'memory_loaded' || phase === 'saving_memory') {
            className = 'memory-active';
          } else if (phase === 'invoking_llm') {
            // Keep memory edge highlighted during LLM invocation to show context is being used
            className = 'memory-active';
          }
        }
        // Tool connection highlights when the specific tool node is executing
        // Only highlight the edge whose source (tool node) is actually being used
        else if (isToolConnection) {
          const toolNodeStatus = sourceStatus?.status;
          if (toolNodeStatus === 'executing') {
            // This specific tool is being executed - highlight its edge
            className = 'tool-active';
          } else if ((phase === 'invoking_llm' || phase === 'building_graph') && toolNodeStatus === 'success') {
            // Tool completed successfully - keep edge showing success
            className = 'completed';
          }
        }
        // Skill connection highlights during skill loading phase (Zeenie)
        // Skills provide context to LLM, so highlight only when loading skills
        else if (isSkillConnection) {
          if (phase === 'loading_skills') {
            className = 'skill-active';
          }
        }
      }

      // Standard edge status classes - ONLY apply during active execution or deployment
      // When not executing/deploying, all edges should have the same default cyan color
      const isActiveExecution = isExecuting || isCurrentWorkflowDeployed;
      if (!className && isActiveExecution) {
        const srcStatus = sourceStatus?.status;
        const tgtStatus = targetStatus?.status;

        // Edge is executing if target is currently executing (data flowing into it)
        if (tgtStatus === 'executing') {
          className = 'executing';
        }
        // Edge is completed if both source and target are successful during this execution
        else if (srcStatus === 'success' && tgtStatus === 'success') {
          className = 'completed';
        }
        // Edge has error if target has error
        else if (tgtStatus === 'error') {
          className = 'error';
        }
        // Edge shows data flowing when source completed and target is waiting for inputs
        // This indicates data has been produced and is available to the target
        else if (srcStatus === 'success' && tgtStatus === 'waiting') {
          className = 'executing';
        }
        // Edge is pending if source completed but target hasn't started
        else if (srcStatus === 'success' && !tgtStatus) {
          className = 'pending';
        }
        // Edge is pending if source is waiting (hasn't produced output yet)
        // This keeps downstream edges from glowing until source completes
        else if (srcStatus === 'waiting') {
          className = 'pending';
        }
      }

      return {
        ...edge,
        className
      };
    });
  }, [edges, nodeStatuses, isExecuting, isCurrentWorkflowDeployed, nodes]);

  // Memoize ReactFlow options to prevent unnecessary re-renders
  const defaultEdgeOptions = React.useMemo(() => ({
    type: 'smoothstep',
    animated: true,
    style: { stroke: theme.dracula.cyan, strokeWidth: 3 },
  }), [theme.dracula.cyan]);

  const connectionLineStyle = React.useMemo(() => ({
    stroke: theme.dracula.cyan,
    strokeWidth: 2
  }), [theme.dracula.cyan]);

  // Memoize the injected canvas <style> so the stylesheet isn't
  // regenerated + re-parsed (a full-document style recalc) on every
  // Dashboard render — only when the theme palette actually changes.
  const canvasCss = React.useMemo(() => buildCanvasStyles(theme.colors), [theme.colors]);

  // `.react-flow` is intentionally transparent so the parent
  // `.canvas-host` / `.canvas` background-image (per-theme
  // `--canvas-grid` + multi-layer gradient stack from
  // client/src/themes/<theme>.css) paints through. Painting a
  // backgroundColor here would hide every theme decoration —
  // Cyber perspective grid, Renaissance fleur-de-lis, Surveillance
  // CCTV reticle, Steampunk brass bolts, etc.
  const reactFlowStyle = React.useMemo(() => ({
    width: '100%',
    height: '100%',
  }), []);

  const snapGrid: [number, number] = React.useMemo(() => [20, 20], []);

  const proOptions = React.useMemo(() => ({ hideAttribution: true }), []);

  // Wave 10.D step 2: nodeTypes dispatch map. Keyed on the cached
  // spec-type SET identity — seeded from the persisted cache, so warm
  // starts get a populated map on the first render and no canvas-wide
  // remount when an unchanged prefetch lands (same set -> same key).
  // Rebuilds exactly when the set changes: cold first-ever visit, and
  // a revision bust that adds/removes node types (one-time cost each).
  const nodeTypes = React.useMemo(() => createNodeTypes(), [specsKey]);
  const edgeTypes = moduleEdgeTypes;

  // Execute entire workflow from start node to end
  const handleRun = async () => {
    if (!currentWorkflow) return;
    const workflowId = currentWorkflow.id;

    // Use per-workflow state setters (n8n pattern)
    setWorkflowExecuting(workflowId, true);
    setExecutionResult(null);
    clearWorkflowExecutionState(workflowId);
    setWorkflowExecuting(workflowId, true); // Re-set after clear

    try {
      // Check if there's a start node
      const startNode = nodes.find(node => node.type === 'start');
      if (!startNode) {
        alert('No Start node found in workflow.\n\nAdd a Start node to begin workflow execution.');
        setWorkflowExecuting(workflowId, false);
        return;
      }

      // Build execution order for visual feedback (BFS from start node)
      const buildOrder = () => {
        const order: string[] = [];
        const visited = new Set<string>();
        const queue = [startNode.id];
        const adjacencyMap = new Map<string, string[]>();

        edges.forEach(edge => {
          const sources = adjacencyMap.get(edge.source) || [];
          sources.push(edge.target);
          adjacencyMap.set(edge.source, sources);
        });

        while (queue.length > 0) {
          const currentId = queue.shift()!;
          if (visited.has(currentId)) continue;
          visited.add(currentId);
          order.push(currentId);

          const connected = adjacencyMap.get(currentId) || [];
          connected.forEach(id => {
            if (!visited.has(id)) queue.push(id);
          });
        }
        return order;
      };

      const order = buildOrder();
      setWorkflowExecutionOrder(workflowId, order);

      console.log('[Workflow Run] Starting workflow execution with', nodes.length, 'nodes and', edges.length, 'edges');
      console.log('[Workflow Run] Execution order:', order);

      // Execute the entire workflow via WebSocket
      const workflowResult = await executeWorkflow(nodes, edges, undefined, workflowId);

      console.log('[Workflow Run] Execution complete:', workflowResult);

      // Build result for display
      const result = {
        success: workflowResult.success,
        nodeId: 'workflow',
        nodeName: currentWorkflow.name || 'Workflow',
        timestamp: new Date().toISOString(),
        executionTime: workflowResult.execution_time || 0,
        outputs: workflowResult.node_results || {},
        data: workflowResult,
        error: workflowResult.error || (workflowResult.errors?.length > 0 ? workflowResult.errors[0].error : undefined),
        nodeData: workflowResult,
        // Workflow-specific display data
        nodesExecuted: workflowResult.nodes_executed || [],
        executionOrder: workflowResult.execution_order || [],
        totalNodes: workflowResult.total_nodes || 0,
        completedNodes: workflowResult.completed_nodes || 0,
        nodeResults: workflowResult.node_results || {},
        errors: workflowResult.errors || [],
        // For backwards compatibility with AI result modal
        response: workflowResult.success
          ? `Workflow executed successfully. ${workflowResult.completed_nodes}/${workflowResult.total_nodes} nodes completed.`
          : `Workflow failed: ${workflowResult.error || 'Unknown error'}`,
        model: 'workflow'
      };

      // Set result and show modal
      setExecutionResult(result);
      setShowResult(true);

    } catch (error: any) {
      console.error('Workflow execution error:', error);

      // Create error result for modal display
      const errorResult = {
        success: false,
        nodeId: 'workflow',
        nodeName: currentWorkflow?.name || 'Workflow',
        timestamp: new Date().toISOString(),
        executionTime: 0,
        error: error.message || 'Unknown execution error',
        response: `Error: ${error.message}`,
        model: 'workflow'
      };

      setExecutionResult(errorResult);
      setShowResult(true);
    } finally {
      setWorkflowExecuting(workflowId, false);
    }
  };

  // Deploy workflow - runs continuously until cancelled
  const handleDeploy = async () => {
    if (!currentWorkflow) return;

    // Check if there's at least one trigger node (workflow entry points)
    // Trigger types: start, cronScheduler, webhookTrigger, whatsappReceive, telegramReceive, twitterReceive, googleGmailReceive, workflowTrigger, chatTrigger, taskTrigger
    const triggerTypes = ['start', 'cronScheduler', 'webhookTrigger', 'whatsappReceive', 'telegramReceive', 'twitterReceive', 'googleGmailReceive', 'emailReceive', 'workflowTrigger', 'chatTrigger', 'taskTrigger'];
    const hasTriggerNode = nodes.some(node => triggerTypes.includes(node.type || ''));
    if (!hasTriggerNode) {
      alert('No trigger node found in workflow.\n\nAdd a trigger node (Cron Scheduler, WhatsApp Receive, Webhook, Chat Trigger, etc.) to begin deployment.');
      return;
    }

    try {
      // Settings are already synced to backend via WebSocket from SettingsPanel
      // Backend will use the stored settings

      // DEBUG: Log edges being sent to deployment
      console.log('[Dashboard] Deploying with edges:', {
        edgeCount: edges.length,
        edges: edges.map(e => ({
          id: e.id,
          source: e.source,
          target: e.target,
          sourceHandle: e.sourceHandle,
          targetHandle: e.targetHandle
        })),
      });

      await startWorkflow(currentWorkflow.id, nodes, edges, 'default', workflowControl.revision);
    } catch (error: any) {
      console.error('[Dashboard] Deployment error:', error);
      alert(`Deployment error: ${error.message}`);
    }
  };

  // Onboarding handoff: open the AI Assistant example, focus chat, and
  // deploy it so the chat trigger is live. Shared by the wizard's finish
  // button and the Get Started checklist.
  const handleRunExample = React.useCallback(async () => {
    const workflow = await openExampleAndChat('AI Assistant');
    if (!workflow) return;

    const alreadyDeployed = deploymentStatus.isRunning && deploymentStatus.workflow_id === workflow.id;
    if (alreadyDeployed) return;

    try {
      const result = await deployWorkflow(workflow.id, workflow.nodes, workflow.edges, 'default');
      if (!result.success) {
        toast.error('Could not start the example — press Start on the toolbar.');
      }
    } catch {
      toast.error('Could not start the example — press Start on the toolbar.');
    }
  }, [openExampleAndChat, deployWorkflow, deploymentStatus.isRunning, deploymentStatus.workflow_id]);

  const handlePauseWorkflow = async () => {
    const workflowId = currentWorkflow?.id;
    if (!workflowId) return;
    try {
      await pauseWorkflow(workflowId, workflowControl.revision);
    } catch (error: any) {
      toast.error(error?.message || 'Unable to pause workflow');
    }
  };

  const handleResumeWorkflow = async () => {
    if (!currentWorkflow?.id) return;
    try { await resumeWorkflow(currentWorkflow.id, workflowControl.revision); }
    catch (error: any) { toast.error(error?.message || 'Unable to resume workflow'); }
  };

  const handleResetWorkflow = async () => {
    if (!currentWorkflow?.id) return;
    try {
      await resetWorkflow(currentWorkflow.id, workflowControl.revision);
      clearWorkflowExecutionState(currentWorkflow.id);
      setExecutionResult(null);
      setShowResult(false);
    }
    catch (error: any) { toast.error(error?.message || 'Unable to reset workflow'); }
  };

  // Helper: fetch all node parameters from DB for export
  const fetchNodeParametersForExport = async (): Promise<Record<string, Record<string, any>>> => {
    if (!currentWorkflow?.nodes.length) return {};
    const nodeIds = currentWorkflow.nodes.map(n => n.id);
    try {
      const allParams = await getAllNodeParameters(nodeIds);
      const result: Record<string, Record<string, any>> = {};
      for (const [nodeId, np] of Object.entries(allParams)) {
        if (np?.parameters && Object.keys(np.parameters).length > 0) {
          result[nodeId] = np.parameters;
        }
      }
      return result;
    } catch (error) {
      console.error('Failed to fetch node parameters for export:', error);
      return {};
    }
  };

  const handleExportJSON = async () => {
    try {
      const nodeParameters = await fetchNodeParametersForExport();
      const jsonString = exportWorkflowToJSON(nodeParameters);
      await navigator.clipboard.writeText(jsonString);
      alert('Workflow JSON copied to clipboard');
    } catch (error) {
      console.error('Export JSON error:', error);
      alert('Failed to export workflow JSON');
    }
  };

  const handleExportFile = async () => {
    try {
      const nodeParameters = await fetchNodeParametersForExport();
      exportWorkflowToFile(nodeParameters);
    } catch (error) {
      console.error('Export file error:', error);
      alert('Failed to export workflow file');
    }
  };

  const handleImportJSON = () => {
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.json';
    fileInput.onchange = async (e) => {
      try {
        const file = (e.target as HTMLInputElement).files?.[0];
        if (!file) return;

        // Frontend parses the file (nice error message on malformed JSON);
        // everything else — validation, name-conflict check, missing-
        // credential cross-reference, node-id remap, save — lives in the
        // backend `import_workflow` orchestrator. This UI is reduced to
        // file pick + user prompts + view switch.
        const imported = await importWorkflowFromFile(file);
        const proposedName = imported.name || 'Imported Workflow';

        let response = await sendRequest<ImportWorkflowResponse>('import_workflow', {
          workflow: imported,
        });

        if (!response.success) {
          const reportLines = (response.report?.errors ?? [])
            .map((err: ValidationIssue) => `- ${err.code}: ${err.message}`)
            .join('\n');
          alert(
            `Failed to import workflow: ${response.error ?? 'unknown error'}` +
              (reportLines ? `\n\nValidation errors:\n${reportLines}` : ''),
          );
          return;
        }

        // Preview branch — backend wants user confirmation before saving.
        if (response.preview) {
          let forceCredentials = false;
          let finalName = proposedName;

          // Missing credentials prompt
          const missing = response.missing_credentials ?? [];
          if (missing.length > 0) {
            const lines = missing
              .map((c: { provider_id: string; display_name: string }) =>
                `- ${c.display_name} (${c.provider_id})`)
              .join('\n');
            const proceed = window.confirm(
              `This workflow needs ${missing.length} credential${missing.length === 1 ? '' : 's'} ` +
                `that ${missing.length === 1 ? 'is' : 'are'} not configured:\n\n${lines}\n\n` +
                `Click OK to import anyway and configure ${missing.length === 1 ? 'it' : 'them'} later, ` +
                `or Cancel to abort.`,
            );
            if (!proceed) return;
            forceCredentials = true;
          }

          // Name conflict prompt
          if (response.name_conflict) {
            const suggested = response.suggested_name ?? `${proposedName} (imported)`;
            const userInput = window.prompt(
              `A workflow named "${proposedName}" already exists.\n\nEnter a new name for the imported workflow:`,
              suggested,
            );
            if (userInput === null) return;
            finalName = userInput.trim();
            if (!finalName) {
              alert('Workflow name cannot be empty');
              return;
            }
          }

          // Commit with confirmations
          response = await sendRequest<ImportWorkflowResponse>('import_workflow', {
            workflow: imported,
            name: finalName,
            force_credentials: forceCredentials,
          });

          if (!response.success) {
            alert(`Failed to import workflow: ${response.error ?? 'unknown error'}`);
            return;
          }
        }

        if (!response.preview && response.workflow_id) {
          // Saved server-side. The backend already broadcast
          // `workflow.imported` (handled in WebSocketContext), so other
          // tabs refetch the workflows list automatically. This tab
          // switches the editor view to the new workflow.
          await loadWorkflow(response.workflow_id);
          alert(
            `Workflow "${response.name}" imported with ${response.node_count} nodes ` +
              `and ${response.edge_count} connections`,
          );
        }
      } catch (error: any) {
        console.error('Import error:', error);
        alert(`Failed to import workflow: ${error.message}`);
      }
    };
    fileInput.click();
  };
  // Load saved workflows on mount and auto-select most recent or create new if none exist
  const hasMigrated = React.useRef(false);
  const hasInitialized = React.useRef(false);
  useEffect(() => {
    if (hasInitialized.current) return;
    hasInitialized.current = true;

    console.log('[Dashboard] Mount effect - loading workflows', {
      hasCurrentWorkflow: !!currentWorkflow,
      currentWorkflowId: currentWorkflow?.id,
    });

    const fetchWorkflowsList = () => queryClient.fetchQuery({
      queryKey: WORKFLOWS_QUERY_KEY,
      queryFn: async () => {
        const summaries = await workflowApi.getAllWorkflows();
        return summaries.map(w => ({
          id: w.id,
          name: w.name,
          nodeCount: w.nodeCount,
          createdAt: new Date(w.createdAt),
          lastModified: new Date(w.lastModified),
        }));
      },
    });

    const initWorkflows = async () => {
      const list = await fetchWorkflowsList();
      if (list.length > 0) {
        const mostRecent = [...list].sort(
          (a, b) => b.lastModified.getTime() - a.lastModified.getTime()
        )[0];
        await loadWorkflow(mostRecent.id);
      }
      const state = useAppStore.getState();
      if (!state.currentWorkflow) {
        console.log('[Dashboard] No saved workflows found, creating new one');
        createNewWorkflow();
      }
    };

    if (!currentWorkflow) {
      initWorkflows();
    } else if (!hasMigrated.current) {
      console.log('[Dashboard] Migrating current workflow');
      migrateCurrentWorkflow();
      hasMigrated.current = true;
      void fetchWorkflowsList(); // seed sidebar list cache
    }
  }, [queryClient, currentWorkflow, loadWorkflow, createNewWorkflow, migrateCurrentWorkflow]);

  // Sync workflow state → ReactFlow state (when loading workflows or data changes)
  // Note: Database is the source of truth for parameters - node.data should NOT store parameters
  // Parameters are loaded from database when parameter panel opens (useParameterPanel hook)
  // and when backend executes nodes (NodeExecutor._prepare_parameters)
  useEffect(() => {
    if (currentWorkflow && currentWorkflow.id) {
      const workflowNodes = currentWorkflow.nodes || [];
      setNodes(workflowNodes);
      setEdges(currentWorkflow.edges || []);
      // Do NOT sync database parameters to node.data
      // Database is the single source of truth for parameters
      // This prevents dual storage issues where node.data could diverge from database
    }
  }, [currentWorkflow?.id, currentWorkflow?.lastModified, setNodes, setEdges]);
  
  // Sync ReactFlow state → workflow state (debounced for performance)
  useEffect(() => {
    if (!currentWorkflow || !currentWorkflow.id) return;

    const timeoutId = setTimeout(() => {
      try {
        const currentNodesStr = JSON.stringify(sanitizeNodesForComparison(nodes));
        const currentEdgesStr = JSON.stringify(sanitizeEdgesForComparison(edges));
        const workflowNodesStr = JSON.stringify(sanitizeNodesForComparison(currentWorkflow.nodes || []));
        const workflowEdgesStr = JSON.stringify(sanitizeEdgesForComparison(currentWorkflow.edges || []));

        if (currentNodesStr !== workflowNodesStr || currentEdgesStr !== workflowEdgesStr) {
          console.log('[Dashboard] Syncing ReactFlow -> Store', {
            reactFlowEdgeCount: edges.length,
            storeEdgeCount: (currentWorkflow.edges || []).length,
            newEdges: edges.filter(e => !(currentWorkflow.edges || []).find(we => we.id === e.id))
          });
          updateWorkflow({ nodes, edges });
        }
      } catch (error) {
        console.warn('Failed to sync workflow state:', error);
      }
    }, theme.constants.debounceDelay.workflowUpdate);

    return () => clearTimeout(timeoutId);
  }, [nodes, edges, currentWorkflow?.id, updateWorkflow]);

  // Track previous workflow ID for viewport save/restore (n8n pattern)
  const prevWorkflowIdRef = React.useRef<string | null>(null);
  // Track if we've already restored viewport for current workflow (prevent duplicate restores)
  const viewportRestoredForRef = React.useRef<string | null>(null);

  // Save viewport when switching workflows, restore after nodes load (n8n pattern)
  useEffect(() => {
    const currentId = currentWorkflow?.id;
    const prevId = prevWorkflowIdRef.current;

    // Save viewport of previous workflow before switching
    if (prevId && prevId !== currentId) {
      try {
        const viewport = reactFlowInstance.getViewport();
        setWorkflowViewport(prevId, viewport);
      } catch {
        // Failed to save viewport - ignore
      }
      // Reset the restored flag when switching to new workflow
      viewportRestoredForRef.current = null;
    }

    prevWorkflowIdRef.current = currentId || null;
  }, [currentWorkflow?.id, reactFlowInstance, setWorkflowViewport]);

  // Restore viewport AFTER nodes are loaded and rendered
  // Only restores saved viewport - never auto-centers
  useEffect(() => {
    const currentId = currentWorkflow?.id;
    if (!currentId) return;

    // Skip if we already restored viewport for this workflow
    if (viewportRestoredForRef.current === currentId) {
      return;
    }

    // Get saved viewport from store
    const uiState = workflowUIStates[currentId];
    const savedViewport = uiState?.viewport;

    // Only restore if we have a saved viewport
    if (!savedViewport) {
      viewportRestoredForRef.current = currentId;
      return;
    }

    // Use delay to ensure ReactFlow has finished rendering nodes
    const timeoutId = setTimeout(() => {
      try {
        reactFlowInstance.setViewport(savedViewport, { duration: 0 });
        viewportRestoredForRef.current = currentId;
      } catch {
        // Viewport restore failed - ignore silently
      }
    }, 100);

    return () => clearTimeout(timeoutId);
  }, [currentWorkflow?.id, nodes.length, workflowUIStates, reactFlowInstance]);

  // Node context menu handler (right-click)
  const onNodeContextMenu = React.useCallback(
    (event: React.MouseEvent, node: Node) => {
      event.preventDefault();
      // Select the node when right-clicking
      setSelectedNode(node);
      setContextMenu({
        nodeId: node.id,
        x: event.clientX,
        y: event.clientY,
      });
    },
    [setSelectedNode]
  );

  // Close context menu
  const closeContextMenu = React.useCallback(() => {
    setContextMenu(null);
  }, []);

  // Context menu actions
  const handleContextMenuRename = React.useCallback(() => {
    if (contextMenu) {
      setRenamingNodeId(contextMenu.nodeId);
    }
    closeContextMenu();
  }, [contextMenu, setRenamingNodeId, closeContextMenu]);

  const handleContextMenuCopy = React.useCallback(() => {
    if (contextMenu) {
      // Select the node first, then copy
      const node = nodes.find(n => n.id === contextMenu.nodeId);
      if (node) {
        setNodes(nds => nds.map(n => ({ ...n, selected: n.id === contextMenu.nodeId })));
        // Small delay to ensure selection is applied before copy
        setTimeout(() => copySelectedNodes(), 0);
      }
    }
    closeContextMenu();
  }, [contextMenu, nodes, setNodes, copySelectedNodes, closeContextMenu]);

  const handleContextMenuDelete = React.useCallback(() => {
    if (contextMenu) {
      onNodesDelete([nodes.find(n => n.id === contextMenu.nodeId)].filter(Boolean) as Node[]);
    }
    closeContextMenu();
  }, [contextMenu, nodes, onNodesDelete, closeContextMenu]);

  // Keyboard shortcut handler for workflow operations
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // Ignore shortcuts when typing in input/textarea
      if (event.target instanceof HTMLInputElement ||
          event.target instanceof HTMLTextAreaElement) {
        return;
      }

      // Ignore shortcuts when renaming a node
      if (renamingNodeId) {
        return;
      }

      // F2 to rename selected node
      if (event.key === 'F2' && selectedNode) {
        event.preventDefault();
        setRenamingNodeId(selectedNode.id);
        return;
      }

      // Check for Ctrl/Cmd key shortcuts
      if (event.ctrlKey || event.metaKey) {
        switch (event.key.toLowerCase()) {
          case 's':
            event.preventDefault();
            // Match the toolbar Save button — fire the per-theme save sound.
            withSound('save', handleSave)();
            break;
          case 'c':
            event.preventDefault();
            copySelectedNodes();
            break;
          case 'v':
            event.preventDefault();
            pasteNodes();
            break;
        }
      } else {
        // Non-modifier shortcuts
        switch (event.key.toLowerCase()) {
          case 'd':
            // Toggle disable on selected nodes
            event.preventDefault();
            toggleDisableSelected();
            break;
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [handleSave, copySelectedNodes, pasteNodes, toggleDisableSelected, selectedNode, renamingNodeId, setRenamingNodeId]);

  return (
    <>
      <style>{canvasCss}</style>
      {/* `app-frame` is the decorative-layer hook from the design handoff —
          per-theme CSS files target this class for outer ornaments
          (gilded corners under Renaissance, scanline overlay + corner
          brackets under Cyber, riveted ridged frame under Steampunk,
          REC dot under Surveillance, etc.). Decorations declare
          pointer-events: none so they don't intercept clicks. */}
      <div className="app-frame bg-bg-app" style={{
        width: '100%',
        height: '100vh',
        display: 'flex',
        flexDirection: 'column',
        fontFamily: 'system-ui, sans-serif',
      }}>
        {/* Top Toolbar */}
        <TopToolbar
          workflowName={currentWorkflow?.name || 'Untitled Workflow'}
          onWorkflowNameChange={handleWorkflowNameChange}
          // `withSound('save'|'run', ...)` fires the per-theme audio
          // cue BEFORE the async dispatch so feedback is instant
          // regardless of save/deploy latency. No-op when sound is
          // disabled or the active pack is `none`.
          onSave={withSound('save', handleSave)}
          onNew={handleNew}
          onOpen={handleOpen}
          onRun={withSound('run', handleRun)}
          isRunning={isExecuting}
          workflowControl={workflowControl}
          onStartWorkflow={withSound('run', handleDeploy)}
          onPauseWorkflow={handlePauseWorkflow}
          onResumeWorkflow={withSound('run', handleResumeWorkflow)}
          onResetWorkflow={handleResetWorkflow}
          hasUnsavedChanges={hasUnsavedChanges}
          sidebarVisible={sidebarVisible}
          onToggleSidebar={toggleSidebar}
          componentPaletteVisible={componentPaletteVisible}
          onToggleComponentPalette={toggleComponentPalette}
          proMode={proMode}
          onToggleProMode={toggleProMode}
          onOpenSettings={() => setSettingsOpen(true)}
          onOpenCredentials={() => setCredentialsOpen(true)}
          onExportJSON={handleExportJSON}
          onExportFile={handleExportFile}
          onImportJSON={handleImportJSON}
          onGlobalModelChange={(provider, model) => setGlobalModelDefaults({ provider, model })}
          onOverrideAllAgents={handleOverrideAllAgents}
        />
        
        {/* Main Content Area */}
        <div style={{
          flex: 1,
          minHeight: 0, // Allow flex item to shrink below content size
          display: 'flex',
          overflow: 'hidden',
        }}>
          {/* Left Workflow Sidebar */}
          <div style={{
            width: sidebarVisible ? '280px' : '0px',
            overflow: 'hidden',
            transition: 'width 0.3s ease',
            borderRight: sidebarVisible ? '1px solid var(--border-default)' : 'none',
            display: 'flex',
            flexDirection: 'column',
          }}>
            {sidebarVisible && (
              <WorkflowSidebar
                workflows={savedWorkflows}
                currentWorkflowId={currentWorkflow?.id}
                onSelectWorkflow={handleSelectWorkflow}
                onDeleteWorkflow={deleteWorkflow}
              />
            )}
          </div>
          
          {/* Canvas Area */}
          <div style={{
            flex: 1,
            display: 'flex',
            position: 'relative',
          }}>
            {/* `canvas-host` + `canvas` activate per-theme canvas
                decorations (cyber grid backplane, atomic starburst, rot
                candlelight pools, surveillance crosshair brackets,
                renaissance fleur-de-lis + marginalia, greek temple key
                pattern). The `canvas` co-class is the handoff selector
                used by every per-theme CSS file. Decorative pseudo-
                elements declare pointer-events: none. */}
            <div
              className="canvas-host canvas"
              style={{
                flex: 1,
                // `backgroundColor` intentionally omitted — the per-theme
                // CSS in client/src/themes/<theme>.css owns the canvas
                // surface paint via the `:root[data-theme="..."] .canvas`
                // multi-layer `background` declaration (radial-gradient
                // vignette + grid pattern + noise texture + var(--bg-canvas)).
                // base.css `.canvas-host { background-image: var(--canvas-grid); }`
                // provides the fallback grid layer.
                position: 'relative',
              }}
            >
              <ErrorBoundary>
                <ReactFlow
                  nodes={styledNodes}
                  edges={styledEdges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  onNodesDelete={onNodesDelete}
                  onEdgesDelete={onEdgesDelete}
                  onConnect={onConnect}
                  onDragOver={onDragOver}
                  onDrop={onDrop}
                  onNodeContextMenu={onNodeContextMenu}
                  nodeTypes={nodeTypes}
                  edgeTypes={edgeTypes}
                  connectionMode={ConnectionMode.Loose}
                  deleteKeyCode={isCurrentWorkflowLocked ? [] : ['Delete', 'Backspace']}
                  edgesFocusable={!isCurrentWorkflowLocked}
                  edgesUpdatable={!isCurrentWorkflowLocked}
                  nodesDraggable={!isCurrentWorkflowLocked}
                  nodesConnectable={!isCurrentWorkflowLocked}
                  nodesFocusable={!isCurrentWorkflowLocked}
                  elementsSelectable={!isCurrentWorkflowLocked}
                  selectNodesOnDrag={false}
                  onlyRenderVisibleElements={true}
                  selectionOnDrag={true}
                  selectionMode={SelectionMode.Partial}
                  selectionKeyCode="Control"
                  panOnDrag={true}
                  panOnScroll={false}
                  zoomOnScroll={true}
                  preventScrolling={true}
                  proOptions={proOptions}
                  defaultEdgeOptions={defaultEdgeOptions}
                  connectionLineStyle={connectionLineStyle}
                  connectionLineType={ConnectionLineType.SmoothStep}
                  snapToGrid={true}
                  snapGrid={snapGrid}
                  style={reactFlowStyle}
                >
                  <Controls />
                </ReactFlow>
              </ErrorBoundary>
            </div>
            
            {/* Right Component Palette */}
            <div style={{
              width: componentPaletteVisible ? theme.layout.sidebarWidth : '0px',
              overflow: 'hidden',
              transition: 'width 0.3s ease',
              borderLeft: componentPaletteVisible ? '1px solid var(--border-default)' : 'none',
              display: 'flex',
              flexDirection: 'column',
            }}>
              {componentPaletteVisible && (
                <ComponentPalette
                  searchQuery={searchQuery}
                  onSearchChange={setSearchQuery}
                  collapsedSections={collapsedSections}
                  onToggleSection={toggleSection}
                  onDragStart={handleComponentDragStart}
                  proMode={proMode}
                  specsKey={specsKey}
                />
              )}
            </div>
          </div>
        </div>

        {/* Console Panel - n8n-style debug output at bottom */}
        <ConsolePanel
          isOpen={consolePanelVisible}
          onToggle={toggleConsolePanelVisible}
          nodes={nodes}
        />

        {/* Status bar — fixed-bottom system console line; surfaces
            connection state, workflow context, theme, clock. Always
            present below ConsolePanel. */}
        <StatusBar workflowName={currentWorkflow?.name} nodeCount={nodes.length} />

        {/* Global command palette — ⌘K (Ctrl+K). Surface common shell
            actions; the registered list is local to this component
            because every handler is already in scope here. */}
        <CommandPaletteHost
          open={commandPaletteOpen}
          onOpenChange={setCommandPaletteOpen}
          handlers={{
            save: withSound('save', handleSave),
            newWorkflow: handleNew,
            open: handleOpen,
            start: withSound('run', handleDeploy),
            pause: handlePauseWorkflow,
            resume: withSound('run', handleResumeWorkflow),
            reset: handleResetWorkflow,
            workflowControl,
            exportFile: handleExportFile,
            importJSON: handleImportJSON,
            openSettings: () => setSettingsOpen(true),
            openCredentials: () => setCredentialsOpen(true),
            toggleSidebar,
            toggleComponentPalette,
            toggleConsolePanel: toggleConsolePanelVisible,
          }}
        />


        {/* Parameter Panels */}
        <ErrorBoundary>
          <ParameterPanel key={`${currentWorkflow?.id || 'none'}:${workflowControl.generation || 0}:${workflowControl.state}`} />
          <LocationParameterPanel />
        </ErrorBoundary>
        
        {/* AI Result Modal */}
        <AIResultModal
          isOpen={showResult}
          onClose={() => setShowResult(false)}
          result={executionResult}
        />

        {/* Settings Panel Modal */}
        <SettingsPanel
          isOpen={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          settings={settings}
          onSettingsChange={setSettings}
          onReplayOnboarding={() => {
            setSettingsOpen(false);
            setOnboardingReopenTrigger(prev => prev + 1);
          }}
          onShowGetStarted={() => {
            saveUserSettings.mutate({ getting_started_dismissed: false });
            setSettingsOpen(false);
          }}
        />

        {/* Credentials Modal */}
        <CredentialsModal
          visible={credentialsOpen}
          onClose={() => setCredentialsOpen(false)}
        />

        {/* Onboarding Wizard */}
        <OnboardingWizard
          onOpenCredentials={() => setCredentialsOpen(true)}
          reopenTrigger={onboardingReopenTrigger}
          onFinish={() => void handleRunExample()}
        />

        {/* Get Started checklist (appears after onboarding completes) */}
        <GetStartedChecklist
          actions={{
            'add-key': () => setCredentialsOpen(true),
            'chat-example': () => void handleRunExample(),
            'build-workflow': handleNew,
          }}
        />

        {/* Node Context Menu (right-click) */}
        {contextMenu && (
          <NodeContextMenu
            nodeId={contextMenu.nodeId}
            x={contextMenu.x}
            y={contextMenu.y}
            onClose={closeContextMenu}
            onRename={handleContextMenuRename}
            onCopy={handleContextMenuCopy}
            onDelete={handleContextMenuDelete}
          />
        )}
      </div>
    </>
  );
};

// Outer wrapper component that provides ReactFlowProvider context
const Dashboard: React.FC = () => {
  return (
    <ReactFlowProvider>
      <DashboardContent />
    </ReactFlowProvider>
  );
};

export default Dashboard;
