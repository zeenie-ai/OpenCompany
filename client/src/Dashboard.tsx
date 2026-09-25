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
  StepEdge,
  Node,
  Edge,
} from 'reactflow';
import { featureFlags } from './lib/featureFlags';
import { deriveCanvasLock } from './lib/canvasLock';
import { prefetchAllNodeSpecs, listCachedNodeSpecs, cachedNodeSpecTypesKey } from './lib/nodeSpec';
import AIAgentNode from './components/AIAgentNode';
import SquareNode from './components/SquareNode';
import TriggerNode from './components/TriggerNode';
import ToolkitNode from './components/ToolkitNode';
import TeamMonitorNode from './components/TeamMonitorNode';
import StartNode from './components/StartNode';
import ConditionalEdge from './components/ConditionalEdge';
import NodeContextMenu from './components/ui/NodeContextMenu';
import { getNodeTypesInGroup, getCachedNodeSpec } from './lib/nodeSpec';
import ParameterPanel from './ParameterPanel';
import LocationParameterPanel from './components/LocationParameterPanel';
import { useAppStore } from './store/useAppStore';
import ComponentPalette from './components/ui/ComponentPalette';
import TopToolbar from './components/ui/TopToolbar';
import WorkflowSidebar from './components/ui/WorkflowSidebar';
import AIResultModal from './components/ui/AIResultModal';
import OnboardingWizard from './components/onboarding/OnboardingWizard';
import GetStartedChecklist from './components/onboarding/GetStartedChecklist';
import { useShellDialogsStore } from './stores/shellDialogsStore';
import ErrorBoundary from './components/ui/ErrorBoundary';
import ConsolePanel from './components/ui/ConsolePanel';
import CanvasDock from './components/ui/CanvasDock';
import StatusBar from './components/ui/StatusBar';
import CommandPaletteHost from './components/ui/CommandPaletteHost';
import { withSound } from './hooks/useSound';
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
import {
  useWebSocket,
  type WorkflowStartResult,
} from './contexts/WebSocketContext';
import {
  sanitizeNodesForComparison,
  sanitizeEdgesForComparison,
} from './utils/workflow';
import { importWorkflowFromFile } from './utils/workflowExport';
import type { ValidationIssue } from './hooks/useWorkflowValidation';
import { buildCanvasStyles } from './styles/canvasAnimations';

// Static stylesheet: fully token-driven (var(--edge-*) + semantic
// tokens), so it is built once at module scope — theme switches restyle
// it via CSS variable resolution, never a rebuild.
const canvasCss = buildCanvasStyles();

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
  // Design-handoff edge contract: every default edge renders as the
  // orthogonal step edge. Persisted workflows (incl. the shipped example
  // seeds) carry `type: 'smoothstep'` from the pre-step era, so the
  // built-in name is remapped to React Flow's StepEdge renderer — no
  // data migration needed; stored graphs and exports stay byte-stable.
  smoothstep: StepEdge,
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
  // App-level dialogs live in the shell (app/AppShell) so either screen can
  // open them.
  const openSettings = useShellDialogsStore((s) => s.openSettings);
  const openCredentials = useShellDialogsStore((s) => s.openCredentials);
  const onboardingReplay = useShellDialogsStore((s) => s.onboardingReplay);
  // Per-workflow UI state (n8n pattern)
  const setWorkflowExecuting = useAppStore((s) => s.setWorkflowExecuting);
  const setWorkflowExecutionOrder = useAppStore((s) => s.setWorkflowExecutionOrder);
  const setWorkflowViewport = useAppStore((s) => s.setWorkflowViewport);
  const clearWorkflowExecutionState = useAppStore((s) => s.clearWorkflowExecutionState);

  // The node-status store's current-workflow sync, the status resync on
  // reconnect, sound sync, page-activity tracking and the UI-defaults load
  // run in the app shell (app/AppShell), so they hold on either screen.

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
  const { saveNodeParameters, getAllNodeParameters, executeWorkflow, nodeStatuses, deploymentStatus, workflowControlStatuses, workflowControlPending, startWorkflow, pauseWorkflow, resumeWorkflow, resetWorkflow, getWorkflowControlStatus, workflowLock, isReady, sendRequest, clearNodeStatus } = useWebSocket();

  // Workflows list: server-owned data, cached by TanStack Query.
  const queryClient = useQueryClient();
  const { data: savedWorkflows = [] } = useWorkflowsQuery();

  // Scope deployment and lock to current workflow (n8n pattern)
  // Only show as "running" or "locked" if it applies to the currently viewed workflow
  const isCurrentWorkflowDeployed = deploymentStatus.isRunning &&
    deploymentStatus.workflow_id === currentWorkflow?.id;
  // Durable lifecycle capabilities are the only source of truth for toolbar
  // and command-palette actions. Ad-hoc `isExecuting` remains separate and
  // only drives canvas execution visuals.
  const workflowControl = currentWorkflow?.id
    ? workflowControlStatuses[currentWorkflow.id] || {
        workflow_id: currentWorkflow.id, state: 'never_started' as const, revision: 0,
        active_count: 0, in_flight_count: 0, queued_count: 0,
        // Status is unresolved until the backend snapshot arrives. Keep every
        // lifecycle action disabled rather than briefly exposing a stale Start;
        // the canvas stays editable until the server says otherwise.
        can_start: false, can_pause: false, can_resume: false, can_reset: false,
        can_edit: true,
      }
    : { state: 'never_started' as const, revision: 0, active_count: 0, in_flight_count: 0, queued_count: 0, can_start: false, can_pause: false, can_resume: false, can_reset: false, can_edit: true };
  const workflowControlPendingMutation = currentWorkflow?.id
    ? workflowControlPending[currentWorkflow.id]
    : undefined;
  // Canvas edit lock — the server-owned `can_edit` capability from the
  // durable control plane is authoritative (backend SSOT; the FE only
  // renders it). The legacy broadcaster lock remains a secondary input
  // for deployments driven outside the control plane.
  const canvasLock = deriveCanvasLock({
    control: workflowControl,
    legacyLock: workflowLock,
    workflowId: currentWorkflow?.id ?? null,
  });
  const isCurrentWorkflowLocked = canvasLock.locked;
  // Single guard for every canvas mutation the React Flow props can't
  // reach (palette drop, paste, context-menu delete/rename, node
  // disable, import, parameter saves). Toasts the reason so a blocked
  // edit never reads as a dead UI.
  const guardCanvasEdit = React.useCallback((): boolean => {
    if (!canvasLock.locked) return true;
    toast.warning(canvasLock.reason ?? 'Workflow is running — pause it to edit the canvas');
    return false;
  }, [canvasLock.locked, canvasLock.reason]);
  const [globalModelDefaults, setGlobalModelDefaults] = React.useState<{ provider: string; model: string } | null>(null);
  const { onDragOver, onDrop, handleComponentDragStart } = useDragAndDrop({ nodes, setNodes, saveNodeParameters, globalModelDefaults, workflowId: currentWorkflow?.id ?? 'new' });
  // Palette drop creates nodes AND persists their default parameters —
  // an HTML5 drop that React Flow's nodesDraggable/Connectable cannot
  // block, so it goes through the shared guard.
  const handleGuardedDrop = React.useCallback((event: React.DragEvent) => {
    if (!guardCanvasEdit()) {
      event.preventDefault();
      return;
    }
    onDrop(event);
  }, [guardCanvasEdit, onDrop]);
  const { onConnect: baseOnConnect, onNodesDelete, onEdgesDelete: baseOnEdgesDelete } = useReactFlowNodes({ setNodes, setEdges, clearNodeStatus });
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

  const applyAuthoritativeStartGraph = React.useCallback((
    workflowId: string,
    result: WorkflowStartResult,
  ) => {
    if (
      !result.graph
      || !Array.isArray(result.graph.nodes)
      || !Array.isArray(result.graph.edges)
    ) {
      return;
    }

    // Start admits and normalizes the graph on the backend. Once it succeeds,
    // replace both UI copies with that exact topology. Aliases only preserve
    // the user's selected-node identity across canonical ID assignment.
    const store = useAppStore.getState();
    const workflow = store.currentWorkflow;
    if (!workflow || workflow.id !== workflowId) return;

    const aliases = result.aliases ?? {};
    const selectedId = store.selectedNode
      ? aliases[store.selectedNode.id] ?? store.selectedNode.id
      : null;
    const authoritativeNodes = (result.graph.nodes as Node[]).map((node) => ({
      ...node,
      selected: selectedId !== null && node.id === selectedId,
    }));
    const authoritativeEdges = result.graph.edges as Edge[];
    const canonicalSelection = selectedId
      ? authoritativeNodes.find((node) => node.id === selectedId) ?? null
      : null;

    store.setCurrentWorkflow({
      ...workflow,
      nodes: authoritativeNodes,
      edges: authoritativeEdges,
    });
    store.setSelectedNode(canonicalSelection);
    setNodes(authoritativeNodes);
    setEdges(authoritativeEdges);
  }, [setEdges, setNodes]);

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

  const [commandPaletteOpen, setCommandPaletteOpen] = React.useState(false);

  // Console panel visibility from store (database-backed)
  const consolePanelVisible = useAppStore((state) => state.consolePanelVisible);
  const toggleConsolePanelVisible = useAppStore((state) => state.toggleConsolePanelVisible);

  // Context menu state for node right-click
  const [contextMenu, setContextMenu] = React.useState<{
    nodeId: string;
    x: number;
    y: number;
  } | null>(null);

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
    if (!isReady) {
      // A backend restart can add or change NodeSpecs while this editor tab
      // remains open. Re-arm the catalogue check so reconnect runs the
      // revision-based cache invalidation and restores new icons/handles.
      hasPrefetchedSpecs.current = false;
      return;
    }
    if (hasPrefetchedSpecs.current) return;
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

  // Memoize ReactFlow options to prevent unnecessary re-renders.
  // No inline `style` / connectionLineStyle: edge visuals (stroke,
  // width, dash) are owned entirely by the theme tokens in
  // themes/base.css via the static stylesheet from canvasAnimations.ts,
  // which also styles the in-progress .react-flow__connection-path.
  const defaultEdgeOptions = React.useMemo(() => ({
    type: 'step',
    animated: false,
  }), []);

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

    // Workflow entry points are backend-declared: componentKind 'trigger'
    // (inherited from TriggerNode, so a new trigger plugin cannot forget it)
    // or 'start'. This used to be a hardcoded list, and it had drifted in
    // BOTH directions — it named `workflowTrigger`, which is not in the
    // registry at all, while omitting `stripeReceive`, so a workflow whose
    // only entry point was a Stripe webhook could not be deployed.
    //
    // An unknown kind counts as "maybe an entry point" on purpose. This gate
    // is a convenience check; the backend validates entry points itself, and
    // failing closed on a cold spec cache would refuse a valid workflow.
    const hasTriggerNode = nodes.some((node) => {
      const kind = node.type ? getCachedNodeSpec(node.type)?.componentKind : undefined;
      return kind == null || kind === 'trigger' || kind === 'start';
    });
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

      const result = await startWorkflow(
        currentWorkflow.id,
        nodes,
        edges,
        'default',
        workflowControl.revision,
      );
      applyAuthoritativeStartGraph(currentWorkflow.id, result);
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

    try {
      const status = await getWorkflowControlStatus(workflow.id);
      if (!status.can_start) return;
      const result = await startWorkflow(
        workflow.id,
        workflow.nodes,
        workflow.edges,
        'default',
        status.revision,
      );
      applyAuthoritativeStartGraph(workflow.id, result);
    } catch {
      toast.error('Could not start the example — press Start on the toolbar.');
    }
  }, [
    applyAuthoritativeStartGraph,
    getWorkflowControlStatus,
    openExampleAndChat,
    startWorkflow,
  ]);

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
      const allParams = await getAllNodeParameters(nodeIds, {
        purpose: 'export',
        workflowId: currentWorkflow.id,
      });
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

  // Leaving the editor (switching to Normal mode) unmounts it. The last
  // canvas edits may still be inside the debounce window above, so flush
  // them into the store, and keep the viewport for the next visit.
  const latestCanvasRef = React.useRef({ nodes, edges });
  useEffect(() => {
    latestCanvasRef.current = { nodes, edges };
  }, [nodes, edges]);
  useEffect(() => () => {
    const store = useAppStore.getState();
    const workflow = store.currentWorkflow;
    if (!workflow?.id) return;
    const latest = latestCanvasRef.current;
    try {
      const changed =
        JSON.stringify(sanitizeNodesForComparison(latest.nodes)) !== JSON.stringify(sanitizeNodesForComparison(workflow.nodes || []))
        || JSON.stringify(sanitizeEdgesForComparison(latest.edges)) !== JSON.stringify(sanitizeEdgesForComparison(workflow.edges || []));
      if (changed) store.updateWorkflow({ nodes: latest.nodes, edges: latest.edges });
    } catch (error) {
      console.warn('Failed to flush canvas state on unmount:', error);
    }
    try {
      store.setWorkflowViewport(workflow.id, reactFlowInstance.getViewport());
    } catch {
      // The viewport is a convenience; a failed read keeps the last one.
    }
  }, []);

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
    if (contextMenu && guardCanvasEdit()) {
      setRenamingNodeId(contextMenu.nodeId);
    }
    closeContextMenu();
  }, [contextMenu, guardCanvasEdit, setRenamingNodeId, closeContextMenu]);

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
    // The emptied deleteKeyCode only blocks the keyboard path — this
    // menu action bypassed the lock entirely before the shared guard.
    if (contextMenu && guardCanvasEdit()) {
      onNodesDelete([nodes.find(n => n.id === contextMenu.nodeId)].filter(Boolean) as Node[]);
    }
    closeContextMenu();
  }, [contextMenu, guardCanvasEdit, nodes, onNodesDelete, closeContextMenu]);

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
        if (guardCanvasEdit()) {
          setRenamingNodeId(selectedNode.id);
        }
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
            // Paste creates nodes AND persists their parameters to the
            // DB — canvas-mutation guard applies.
            if (guardCanvasEdit()) {
              pasteNodes();
            }
            break;
        }
      } else {
        // Non-modifier shortcuts
        switch (event.key.toLowerCase()) {
          case 'd':
            // Toggle disable on selected nodes
            event.preventDefault();
            if (guardCanvasEdit()) {
              toggleDisableSelected();
            }
            break;
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [handleSave, copySelectedNodes, pasteNodes, toggleDisableSelected, selectedNode, renamingNodeId, setRenamingNodeId, guardCanvasEdit]);

  return (
    <>
      <style>{canvasCss}</style>
      {/* The shell (app/AppShell) owns the `.app-frame` decorative layer
          around both screens; the editor fills it. */}
      <div className="flex min-h-0 w-full flex-1 flex-col">
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
          workflowControl={workflowControl}
          workflowControlPending={workflowControlPendingMutation}
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
          onOpenSettings={openSettings}
          onOpenCredentials={openCredentials}
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
                  onDrop={handleGuardedDrop}
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
                  connectionLineType={ConnectionLineType.Step}
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

            {/* Docked Canvas sidebar — persistent content viewer at the
                rightmost edge. Owns its own state (canvasDockStore);
                auto-opens on canvas_updated for the current workflow. */}
            <CanvasDock nodes={nodes} />
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
            workflowControlPending: workflowControlPendingMutation,
            exportFile: handleExportFile,
            importJSON: handleImportJSON,
            openSettings,
            openCredentials,
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

        {/* Onboarding Wizard (editor only; Settings and Credentials are
            shell dialogs, see app/AppShell). */}
        <OnboardingWizard
          onOpenCredentials={openCredentials}
          reopenTrigger={onboardingReplay}
          onFinish={() => void handleRunExample()}
        />

        {/* Get Started checklist (appears after onboarding completes) */}
        <GetStartedChecklist
          actions={{
            'add-key': openCredentials,
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
