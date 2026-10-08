import { create } from 'zustand';
import { isCancelledError } from '@tanstack/react-query';
import { Node, Edge } from 'reactflow';
import { toast } from 'sonner';
import { NEW_WORKFLOW_ID } from '../utils/workflow';
import { theme } from '../styles/theme';
import {
  exportWorkflowToJSON as exportToJSON,
  exportWorkflowToFile as exportToFile,
  importWorkflowFromJSON as importFromJSON,
  sanitizeNodes,
} from '../utils/workflowExport';
import type { ImportedWorkflow } from '../utils/workflowExport';
import { workflowApi, type SaveWorkflowFailure } from '../services/workflowApi';
import { queryClient } from '../lib/queryClient';
import { addSavedEdges, addSavedNodes, type WorkflowOperation } from '../lib/workflowOps';
import { BRAND_STORAGE_KEYS, readAndMigrateStorageValue } from '../lib/brandStorage';
import { WORKFLOWS_QUERY_KEY, workflowQueryKey, workflowQueryOptions, type SavedWorkflow } from '../hooks/useWorkflowsQuery';
import { removeEmployee } from '../features/home/data/employeeCache';

const invalidateWorkflowsList = (): void => {
  void queryClient.invalidateQueries({ queryKey: WORKFLOWS_QUERY_KEY });
};

export interface WorkflowData {
  nodes: Node[];
  edges: Edge[];
  name: string;
  /** Stable backend-allocated decimal identity. Never changes on rename. */
  id: string;
  /** Human-readable slug derived from name (e.g. "AI_Assistant_1").
   *  Backend recomputes on every name change and rotates the on-disk
   *  workspace dir + Temporal Web UI prefix to match. Read-only on
   *  the client — server is the source of truth. */
  slug: string;
  createdAt: Date;
  lastModified: Date;
}

// Per-workflow UI state (n8n pattern - each workflow has isolated execution state)
export interface WorkflowUIState {
  selectedNodeId: string | null;
  executedNodes: string[];  // Array instead of Set for serialization
  executionOrder: string[];
  isExecuting: boolean;
  viewport?: { x: number; y: number; zoom: number };
}

/** Which screen the app shell shows: `normal` is Home (hire and supervise
 *  AI employees), `dev` is the workflow editor. */
export type ShellMode = 'normal' | 'dev';

interface AppStore {
  // Core state - SINGLE source of truth
  currentWorkflow: WorkflowData | null;
  hasUnsavedChanges: boolean;

  // Per-workflow UI state (n8n pattern - isolated per workflow)
  workflowUIStates: Record<string, WorkflowUIState>;

  // Global UI state (not workflow-specific)
  selectedNode: Node | null;  // Kept for backward compatibility, derived from workflowUIStates
  sidebarVisible: boolean;
  componentPaletteVisible: boolean;
  consolePanelVisible: boolean;
  proMode: boolean;  // false = noob mode (only AI categories), true = pro mode (all categories)
  /** The shell's current screen. Persisted as `ui_shell_mode` and only read
   *  when `featureFlags.normalMode` is on (with the flag off the shell always
   *  shows the editor and `proMode` keeps filtering the palette). The first
   *  read migrates `ui_pro_mode`: someone who had switched the palette to
   *  Dev starts in Dev. */
  shellMode: ShellMode;
  /** WebAudio sound effects toggle (per-theme pack picked from
   *  --sound-pack CSS token by `useSoundSync()`). Persisted to
   *  localStorage as `opencompany-sound`; default ON (user disables in
   *  Settings -> Audio). The AudioContext starts suspended per
   *  browser autoplay policy — `Sounds.unlock()` resumes it on the
   *  user's first interaction (no separate audio permission needed). */
  soundEnabled: boolean;
  renamingNodeId: string | null;
  /** Monotonic counter — ConsolePanel focuses the chat input whenever it
   *  increments. Not persisted. */
  chatFocusRequest: number;

  // Workflow actions
  setCurrentWorkflow: (workflow: WorkflowData) => void;
  updateWorkflow: (updates: Partial<Omit<WorkflowData, 'id' | 'createdAt'>>) => void;
  createNewWorkflow: () => Promise<void>;
  /** True when the server saved the workflow. On false it has already told
   *  the user why, and the changes stay marked unsaved. */
  saveWorkflow: () => Promise<boolean>;
  loadWorkflow: (id: string) => Promise<void>;
  deleteWorkflow: (id: string) => Promise<boolean>;
  /** Apply a successful deletion from this tab or a lifecycle broadcast. */
  forgetWorkflow: (id: string) => void;
  migrateCurrentWorkflow: () => Promise<void>;
  /** Adopt a batch the server already saved (`workflow_ops_apply` with
   *  `persisted: true`) into the open workflow, whichever screen shows.
   *  Leaves `hasUnsavedChanges` and `lastModified` alone: nothing new
   *  needs saving, and an open canvas is not reset. No-op for any other
   *  workflow. */
  adoptSavedOperations: (workflowId: string, operations: WorkflowOperation[]) => void;
  /** Show the graph Start admitted (normalized, canonical ids) in the open
   *  workflow. Start stores that graph on the new generation, not on the
   *  workflow, so `hasUnsavedChanges` stays as it was: clearing it would
   *  hide edits that exist only in the running generation. No-op for any
   *  other workflow. */
  adoptStartedGraph: (workflowId: string, nodes: Node[], edges: Edge[]) => void;

  // UI actions
  setSelectedNode: (node: Node | null) => void;
  toggleSidebar: () => void;
  toggleComponentPalette: () => void;
  toggleProMode: () => void;
  /** Switch screens without animation. UI code goes through
   *  app/useShellActions (unsaved-changes guard, preload, transition). */
  setShellMode: (mode: ShellMode) => void;
  setSoundEnabled: (enabled: boolean) => void;
  toggleSoundEnabled: () => void;
  setRenamingNodeId: (nodeId: string | null) => void;

  // UI defaults from database
  setSidebarVisible: (visible: boolean) => void;
  setComponentPaletteVisible: (visible: boolean) => void;
  setConsolePanelVisible: (visible: boolean) => void;
  toggleConsolePanelVisible: () => void;
  requestChatFocus: () => void;
  applyUIDefaults: (defaults: { sidebarDefaultOpen?: boolean; componentPaletteDefaultOpen?: boolean; consolePanelDefaultOpen?: boolean }) => void;

  // Per-workflow UI state actions (n8n pattern)
  getWorkflowUIState: (workflowId: string) => WorkflowUIState;
  setWorkflowExecuting: (workflowId: string, isExecuting: boolean) => void;
  setWorkflowExecutedNodes: (workflowId: string, nodes: string[]) => void;
  setWorkflowExecutionOrder: (workflowId: string, order: string[]) => void;
  setWorkflowViewport: (workflowId: string, viewport: { x: number; y: number; zoom: number }) => void;
  clearWorkflowExecutionState: (workflowId: string) => void;

  // Node/Edge actions (operate on currentWorkflow)
  updateNodeData: (nodeId: string, newData: any) => void;
  updateNodes: (nodes: Node[]) => void;
  updateEdges: (edges: Edge[]) => void;
  addNode: (node: Node) => void;
  removeNodes: (nodeIds: string[]) => void;
  removeEdges: (edgeIds: string[]) => void;

  // Workflow export/import
  exportWorkflowToJSON: (nodeParameters?: Record<string, Record<string, any>>) => string;
  exportWorkflowToFile: (nodeParameters?: Record<string, Record<string, any>>) => void;
  importWorkflowFromJSON: (jsonString: string) => ImportedWorkflow;
}

// Helper functions
const createDefaultWorkflow = (): WorkflowData => ({
  id: NEW_WORKFLOW_ID,
  name: theme.constants.defaultWorkflowName,
  slug: '',  // Server allocates on first save.
  nodes: [],
  edges: [],
  createdAt: new Date(),
  lastModified: new Date(),
});

const createDefaultUIState = (): WorkflowUIState => ({
  selectedNodeId: null,
  executedNodes: [],
  executionOrder: [],
  isExecuting: false,
  viewport: undefined,
});

// Helper to migrate old node types
const migrateNodes = (nodes: Node[]): Node[] => {
  return nodes.map(node => {
    if (node.type === 'googleChatModel') {
      return { ...node, type: 'geminiChatModel' };
    }
    return node;
  });
};

/** What to tell the user when a workflow was not saved. */
const saveFailureMessage = (name: string, failure: SaveWorkflowFailure | null): string => {
  if (failure?.error === 'workflow_not_found') return `"${name}" no longer exists, so it was not saved.`;
  const [first, ...rest] = failure?.messages ?? [];
  if (first) return `Could not save "${name}": ${first}${rest.length ? ` (and ${rest.length} more)` : ''}`;
  const reason = failure?.error === 'unreachable' ? ': the server did not answer' : '';
  return `Could not save "${name}"${reason}. Your changes are still in the editor.`;
};

// Storage keys for UI state persistence
const STORAGE_KEYS = {
  sidebarVisible: 'ui_sidebar_visible',
  componentPaletteVisible: 'ui_component_palette_visible',
  consolePanelVisible: 'ui_console_panel_visible',
  proMode: 'ui_pro_mode',
  shellMode: 'ui_shell_mode',
  /** Canonical OpenCompany sound preference. The pre-rebrand key is read
   *  once during initialization so a returning user's choice survives. */
  soundEnabled: BRAND_STORAGE_KEYS.sound.canonical,
};

// Helper to load boolean from localStorage
const loadBooleanFromStorage = (
  key: string,
  defaultValue: boolean,
  legacyKey?: string,
): boolean => {
  try {
    const saved = legacyKey
      ? readAndMigrateStorageValue(localStorage, { canonical: key, legacy: legacyKey })
      : localStorage.getItem(key);
    if (saved !== null) {
      return saved === 'true';
    }
  } catch {
    // Ignore storage errors
  }
  return defaultValue;
};

/** The saved screen: `ui_shell_mode`, else the old palette choice
 *  (`ui_pro_mode === 'true'` means Dev). index.html's pre-paint theme
 *  script repeats this rule. */
export const readStoredShellMode = (): ShellMode => {
  try {
    const saved = localStorage.getItem(STORAGE_KEYS.shellMode);
    if (saved === 'normal' || saved === 'dev') return saved;
    return localStorage.getItem(STORAGE_KEYS.proMode) === 'true' ? 'dev' : 'normal';
  } catch {
    return 'normal';
  }
};

// Helper to save boolean to localStorage
const saveBooleanToStorage = (key: string, value: boolean): void => {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    // Ignore storage errors
  }
};

export const useAppStore = create<AppStore>((set, get) => ({
  currentWorkflow: null,
  hasUnsavedChanges: false,
  workflowUIStates: {},
  selectedNode: null,
  sidebarVisible: loadBooleanFromStorage(STORAGE_KEYS.sidebarVisible, true),
  componentPaletteVisible: loadBooleanFromStorage(STORAGE_KEYS.componentPaletteVisible, true),
  consolePanelVisible: loadBooleanFromStorage(STORAGE_KEYS.consolePanelVisible, false),
  proMode: loadBooleanFromStorage(STORAGE_KEYS.proMode, false),  // Default to noob mode
  shellMode: readStoredShellMode(),
  soundEnabled: loadBooleanFromStorage(
    STORAGE_KEYS.soundEnabled,
    true,
    BRAND_STORAGE_KEYS.sound.legacy,
  ),  // On by default; user can disable in Settings -> Audio. Browsers gesture-gate WebAudio (no separate permission), so the AC unlocks on first interaction via Sounds.unlock().
  renamingNodeId: null,
  chatFocusRequest: 0,

  // Workflow management
  setCurrentWorkflow: (workflow) => {
    set({ currentWorkflow: workflow, hasUnsavedChanges: false });
  },
  
  updateWorkflow: (updates) => {
    const current = get().currentWorkflow;
    if (!current) return;
    
    const updatedWorkflow = {
      ...current,
      ...updates,
      lastModified: new Date(),
    };
    
    set({ 
      currentWorkflow: updatedWorkflow,
      hasUnsavedChanges: true,
    });
  },
  
  createNewWorkflow: async () => {
    const newWorkflow = createDefaultWorkflow();
    // The database owns the cross-client sequence. Persist the empty draft to
    // reserve its id before any node parameters are written against the graph.
    const result = await workflowApi.saveWorkflow(
      NEW_WORKFLOW_ID,
      newWorkflow.name,
      { nodes: [], edges: [] },
    );
    if (!result?.id) {
      console.error('Failed to allocate workflow identity');
      toast.error('Could not create a workflow. Check the connection and try again.');
      return;
    }
    set({
      currentWorkflow: {
        ...newWorkflow,
        id: result.id,
        slug: result.slug ?? newWorkflow.slug,
        nodes: migrateNodes(result.data?.nodes ?? newWorkflow.nodes),
        edges: result.data?.edges ?? newWorkflow.edges,
      },
      hasUnsavedChanges: false,
      selectedNode: null,
    });
    invalidateWorkflowsList();
  },
  
  saveWorkflow: async () => {
    const { currentWorkflow } = get();
    if (!currentWorkflow) return false;

    // Save to database - sanitize node.data to only include UI fields (label, disabled, condition).
    // Parameters live in the DB node_parameters table, not in node.data.
    // The save handler returns the canonical slug (recomputed server-side
    // when the display name changed) so we can sync the store with it.
    let failure: SaveWorkflowFailure | null = null;
    const result = await workflowApi.saveWorkflow(
      currentWorkflow.id,
      currentWorkflow.name,
      { nodes: sanitizeNodes(currentWorkflow.nodes), edges: currentWorkflow.edges },
      (reason) => { failure = reason; },
    );

    if (!result) {
      console.error('Failed to save workflow to database', failure);
      toast.error(saveFailureMessage(currentWorkflow.name, failure));
      return false;
    }
    // A delete, navigation, or newer edit can finish while the save is in
    // flight. Its response must not restore the old graph or clear new edits.
    if (get().currentWorkflow !== currentWorkflow) return true;

    const aliases = result.nodeIdAliases ?? {};
    // Server normalization is authoritative: it creates Context companions,
    // repairs protected edges, applies migrations, and returns canonical ids
    // atomically. Alias rewriting is only for older servers that omit data.
    const nodes = result.data?.nodes
      ? migrateNodes(result.data.nodes as Node[])
      : currentWorkflow.nodes.map(node => {
          const canonicalId = aliases[node.id];
          return canonicalId ? { ...node, id: canonicalId } : node;
        });
    const edges = result.data?.edges
      ? (result.data.edges as Edge[])
      : currentWorkflow.edges.map(edge => ({
          ...edge,
          source: aliases[edge.source] ?? edge.source,
          target: aliases[edge.target] ?? edge.target,
        }));
    const selected = get().selectedNode;
    const selectedId = selected ? aliases[selected.id] ?? selected.id : null;
    const normalizedSelection = selectedId
      ? nodes.find((node) => node.id === selectedId) ?? null
      : null;

    set({
      currentWorkflow: {
        ...currentWorkflow,
        id: result.id,
        nodes,
        edges,
        slug: result.slug ?? currentWorkflow.slug,
        lastModified: new Date(),
      },
      selectedNode: normalizedSelection,
      hasUnsavedChanges: false,
    });
    invalidateWorkflowsList();
    return true;
  },

  loadWorkflow: async (id) => {
    // Use the same server-record query as other readers. Deletion cancels it,
    // so late responses cannot restore a removed graph. No client ID registry
    // decides whether a workflow exists; every later load asks the backend.
    const key = workflowQueryKey(id);
    let result;
    try {
      result = await queryClient.fetchQuery({
        ...workflowQueryOptions(id),
        staleTime: 0,
      });
    } catch (error) {
      if (isCancelledError(error)) return;
      throw error;
    }
    if (result && queryClient.getQueryData(key) === result) {
      // Migrate old node types
      const nodes = migrateNodes(result.data?.nodes || []);
      const edges = result.data?.edges || [];

      const workflowData: WorkflowData = {
        id: result.id,
        name: result.name,
        slug: result.slug ?? '',
        nodes,
        edges,
        createdAt: new Date(result.createdAt),
        lastModified: new Date(result.lastModified),
      };

      set({
        currentWorkflow: workflowData,
        hasUnsavedChanges: false,
        selectedNode: null,
      });
    }
  },
  
  deleteWorkflow: async (id) => {
    const success = await workflowApi.deleteWorkflow(id);
    if (!success) {
      console.error('Failed to delete workflow from database');
      return false;
    }

    get().forgetWorkflow(id);
    return true;
  },

  forgetWorkflow: (id) => {
    set((state) => {
      const { [id]: _deletedUI, ...workflowUIStates } = state.workflowUIStates;
      return state.currentWorkflow?.id === id
        ? { workflowUIStates, currentWorkflow: null, hasUnsavedChanges: false, selectedNode: null, renamingNodeId: null }
        : { workflowUIStates };
    });
    // Cancel stale list/detail reads before pruning, even while Home is
    // unmounted. Its cache does not automatically refetch on mount.
    void queryClient.cancelQueries({ queryKey: WORKFLOWS_QUERY_KEY, exact: true });
    void queryClient.cancelQueries({ queryKey: workflowQueryKey(id) });
    queryClient.setQueryData<SavedWorkflow[]>(WORKFLOWS_QUERY_KEY, (list) => list?.filter((workflow) => workflow.id !== id));
    queryClient.removeQueries({ queryKey: workflowQueryKey(id) });
    removeEmployee(queryClient, id);
    invalidateWorkflowsList();
  },

  adoptSavedOperations: (workflowId, operations) => {
    const workflow = get().currentWorkflow;
    if (!workflow || workflow.id !== workflowId) return;
    const nodes = addSavedNodes(workflow.nodes, operations);
    const edges = addSavedEdges(workflow.edges, operations);
    if (nodes === workflow.nodes && edges === workflow.edges) return;
    set({ currentWorkflow: { ...workflow, nodes, edges } });
  },

  adoptStartedGraph: (workflowId, nodes, edges) => {
    const workflow = get().currentWorkflow;
    if (!workflow || workflow.id !== workflowId) return;
    set({ currentWorkflow: { ...workflow, nodes, edges } });
  },

  migrateCurrentWorkflow: async () => {
    const { currentWorkflow } = get();
    if (!currentWorkflow || !currentWorkflow.nodes) return;

    const migratedNodes = migrateNodes(currentWorkflow.nodes);

    const hasChanges = migratedNodes.some((node, idx) =>
      node.type !== currentWorkflow.nodes[idx]?.type
    );

    if (hasChanges) {
      const migratedWorkflow = {
        ...currentWorkflow,
        nodes: migratedNodes
      };

      // Save migrated workflow to database (sanitize node.data)
      const result = await workflowApi.saveWorkflow(
        migratedWorkflow.id,
        migratedWorkflow.name,
        { nodes: sanitizeNodes(migratedWorkflow.nodes), edges: migratedWorkflow.edges }
      );
      if (!result) return;
      if (get().currentWorkflow !== currentWorkflow) return;

      set({
        currentWorkflow: {
          ...migratedWorkflow,
          id: result.id,
          slug: result.slug ?? migratedWorkflow.slug,
          nodes: result.data?.nodes
            ? migrateNodes(result.data.nodes as Node[])
            : migratedWorkflow.nodes,
          edges: result.data?.edges
            ? (result.data.edges as Edge[])
            : migratedWorkflow.edges,
        },
        hasUnsavedChanges: false
      });
      invalidateWorkflowsList();
    }
  },

  // UI management
  setSelectedNode: (node) => {
    set({ selectedNode: node });
  },

  toggleSidebar: () => {
    set((state) => {
      const newValue = !state.sidebarVisible;
      saveBooleanToStorage(STORAGE_KEYS.sidebarVisible, newValue);
      return { sidebarVisible: newValue };
    });
  },

  toggleComponentPalette: () => {
    set((state) => {
      const newValue = !state.componentPaletteVisible;
      saveBooleanToStorage(STORAGE_KEYS.componentPaletteVisible, newValue);
      return { componentPaletteVisible: newValue };
    });
  },

  toggleProMode: () => {
    set((state) => {
      const newValue = !state.proMode;
      saveBooleanToStorage(STORAGE_KEYS.proMode, newValue);
      return { proMode: newValue };
    });
  },

  setShellMode: (mode) => {
    try {
      localStorage.setItem(STORAGE_KEYS.shellMode, mode);
    } catch {
      // Ignore storage errors
    }
    set((state) => (state.shellMode === mode ? state : { shellMode: mode }));
  },

  setSoundEnabled: (enabled) => {
    saveBooleanToStorage(STORAGE_KEYS.soundEnabled, enabled);
    set({ soundEnabled: enabled });
  },
  toggleSoundEnabled: () => {
    set((state) => {
      const newValue = !state.soundEnabled;
      saveBooleanToStorage(STORAGE_KEYS.soundEnabled, newValue);
      return { soundEnabled: newValue };
    });
  },

  setRenamingNodeId: (nodeId) => {
    set({ renamingNodeId: nodeId });
  },

  // UI defaults setters (for database sync)
  setSidebarVisible: (visible) => {
    saveBooleanToStorage(STORAGE_KEYS.sidebarVisible, visible);
    set({ sidebarVisible: visible });
  },

  setComponentPaletteVisible: (visible) => {
    saveBooleanToStorage(STORAGE_KEYS.componentPaletteVisible, visible);
    set({ componentPaletteVisible: visible });
  },

  setConsolePanelVisible: (visible) => {
    saveBooleanToStorage(STORAGE_KEYS.consolePanelVisible, visible);
    set({ consolePanelVisible: visible });
  },

  toggleConsolePanelVisible: () => {
    set((state) => {
      const newValue = !state.consolePanelVisible;
      saveBooleanToStorage(STORAGE_KEYS.consolePanelVisible, newValue);
      return { consolePanelVisible: newValue };
    });
  },

  requestChatFocus: () => {
    set((state) => ({ chatFocusRequest: state.chatFocusRequest + 1 }));
  },

  applyUIDefaults: (defaults) => {
    const updates: Partial<{ sidebarVisible: boolean; componentPaletteVisible: boolean; consolePanelVisible: boolean }> = {};

    if (defaults.sidebarDefaultOpen !== undefined) {
      updates.sidebarVisible = defaults.sidebarDefaultOpen;
      saveBooleanToStorage(STORAGE_KEYS.sidebarVisible, defaults.sidebarDefaultOpen);
    }

    if (defaults.componentPaletteDefaultOpen !== undefined) {
      updates.componentPaletteVisible = defaults.componentPaletteDefaultOpen;
      saveBooleanToStorage(STORAGE_KEYS.componentPaletteVisible, defaults.componentPaletteDefaultOpen);
    }

    if (defaults.consolePanelDefaultOpen !== undefined) {
      updates.consolePanelVisible = defaults.consolePanelDefaultOpen;
      saveBooleanToStorage(STORAGE_KEYS.consolePanelVisible, defaults.consolePanelDefaultOpen);
    }

    if (Object.keys(updates).length > 0) {
      set(updates);
    }
  },

  // Per-workflow UI state management (n8n pattern - isolated execution state per workflow)
  getWorkflowUIState: (workflowId) => {
    const { workflowUIStates } = get();
    return workflowUIStates[workflowId] || createDefaultUIState();
  },

  setWorkflowExecuting: (workflowId, isExecuting) => {
    set((state) => {
      const prevState = state.workflowUIStates[workflowId];
      return {
        workflowUIStates: {
          ...state.workflowUIStates,
          [workflowId]: {
            ...(prevState || createDefaultUIState()),
            isExecuting,
          },
        },
      };
    });
  },

  setWorkflowExecutedNodes: (workflowId, nodes) => {
    set((state) => ({
      workflowUIStates: {
        ...state.workflowUIStates,
        [workflowId]: {
          ...(state.workflowUIStates[workflowId] || createDefaultUIState()),
          executedNodes: nodes,
        },
      },
    }));
  },

  setWorkflowExecutionOrder: (workflowId, order) => {
    set((state) => ({
      workflowUIStates: {
        ...state.workflowUIStates,
        [workflowId]: {
          ...(state.workflowUIStates[workflowId] || createDefaultUIState()),
          executionOrder: order,
        },
      },
    }));
  },

  setWorkflowViewport: (workflowId, viewport) => {
    set((state) => ({
      workflowUIStates: {
        ...state.workflowUIStates,
        [workflowId]: {
          ...(state.workflowUIStates[workflowId] || createDefaultUIState()),
          viewport,
        },
      },
    }));
  },

  clearWorkflowExecutionState: (workflowId) => {
    set((state) => ({
      workflowUIStates: {
        ...state.workflowUIStates,
        [workflowId]: {
          ...(state.workflowUIStates[workflowId] || createDefaultUIState()),
          isExecuting: false,
          executedNodes: [],
          executionOrder: [],
        },
      },
    }));
  },

  updateNodeData: (nodeId, newData) => {
    const { currentWorkflow, selectedNode } = get();
    if (!currentWorkflow) return;
    
    const updatedNodes = currentWorkflow.nodes.map(node => 
      node.id === nodeId ? { ...node, data: { ...node.data, ...newData } } : node
    );
    
    const updatedWorkflow = {
      ...currentWorkflow,
      nodes: updatedNodes,
      lastModified: new Date(),
    };
    
    set({ 
      currentWorkflow: updatedWorkflow,
      hasUnsavedChanges: true,
      selectedNode: selectedNode?.id === nodeId ? 
        { ...selectedNode, data: { ...selectedNode.data, ...newData } } : 
        selectedNode
    });
  },
  
  updateNodes: (nodes) => {
    const { currentWorkflow } = get();
    if (!currentWorkflow) return;
    
    const updatedWorkflow = {
      ...currentWorkflow,
      nodes,
      lastModified: new Date(),
    };
    
    set({ 
      currentWorkflow: updatedWorkflow,
      hasUnsavedChanges: true,
    });
  },
  
  updateEdges: (edges) => {
    const { currentWorkflow } = get();
    if (!currentWorkflow) return;
    
    const updatedWorkflow = {
      ...currentWorkflow,
      edges,
      lastModified: new Date(),
    };
    
    set({ 
      currentWorkflow: updatedWorkflow,
      hasUnsavedChanges: true,
    });
  },
  
  addNode: (node) => {
    const { currentWorkflow } = get();
    if (!currentWorkflow) return;
    
    const updatedWorkflow = {
      ...currentWorkflow,
      nodes: [...currentWorkflow.nodes, node],
      lastModified: new Date(),
    };
    
    set({ 
      currentWorkflow: updatedWorkflow,
      hasUnsavedChanges: true,
    });
  },
  
  removeNodes: (nodeIds) => {
    const { currentWorkflow } = get();
    if (!currentWorkflow) return;
    
    const updatedNodes = currentWorkflow.nodes.filter(node => !nodeIds.includes(node.id));
    const updatedEdges = currentWorkflow.edges.filter(edge => 
      !nodeIds.includes(edge.source) && !nodeIds.includes(edge.target)
    );
    
    const updatedWorkflow = {
      ...currentWorkflow,
      nodes: updatedNodes,
      edges: updatedEdges,
      lastModified: new Date(),
    };
    
    set({ 
      currentWorkflow: updatedWorkflow,
      hasUnsavedChanges: true,
      selectedNode: nodeIds.includes(get().selectedNode?.id || '') ? null : get().selectedNode,
    });
  },
  
  removeEdges: (edgeIds) => {
    const { currentWorkflow } = get();
    if (!currentWorkflow) return;

    const updatedEdges = currentWorkflow.edges.filter(edge => !edgeIds.includes(edge.id));

    const updatedWorkflow = {
      ...currentWorkflow,
      edges: updatedEdges,
      lastModified: new Date(),
    };

    set({
      currentWorkflow: updatedWorkflow,
      hasUnsavedChanges: true,
    });
  },

  exportWorkflowToJSON: (nodeParameters?) => {
    const { currentWorkflow } = get();
    if (!currentWorkflow) {
      throw new Error('No workflow to export');
    }

    return exportToJSON(currentWorkflow, nodeParameters);
  },

  exportWorkflowToFile: (nodeParameters?) => {
    const { currentWorkflow } = get();
    if (!currentWorkflow) {
      throw new Error('No workflow to export');
    }

    exportToFile(currentWorkflow, nodeParameters);
  },

  importWorkflowFromJSON: (jsonString: string) => {
    const imported = importFromJSON(jsonString);
    set({
      currentWorkflow: imported,
      hasUnsavedChanges: true
    });
    return imported;
  },
}));
