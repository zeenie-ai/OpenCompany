import React, { useState, useEffect, useMemo } from 'react';
import { Loader2, Save } from 'lucide-react';
import { toast } from 'sonner';
import { useMutation, useQueries, useQuery } from '@tanstack/react-query';

import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Checkbox } from '@/components/ui/checkbox';
import { cn } from '@/lib/utils';
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from '@/components/ui/accordion';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Zap, Pencil, Sparkles, TerminalSquare, Trash2, RotateCcw } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { ActionButton } from '@/components/ui/action-button';
import ParameterRenderer from '../ParameterRenderer';
import ToolSchemaEditor from './ToolSchemaEditor';
import MasterSkillEditor from './MasterSkillEditor';
import { useAppStore } from '../../store/useAppStore';
import { useWebSocket, CompactionStats } from '../../contexts/WebSocketContext';
import { useUserSettingsQuery } from '../../hooks/useUserSettingsQuery';
import { nodeParamsQueryKey, type NodeParametersResponse } from '../../hooks/useNodeParamsQuery';
import { folderSkillsQueryKey, type AvailableSkill } from '../../hooks/useFolderSkills';
import { queryKeys, STALE_TIME } from '../../lib/queryConfig';
import { INodeTypeDescription, INodeProperties } from '../../types/INodeProperties';
import { NodeIcon } from '../../assets/icons';
import { ExecutionResult } from '../../services/executionService';
import { Edge } from 'reactflow';
import { shouldShowParameter } from '../../utils/parameterVisibility';

import { resolveNodeDescription, getCachedNodeSpec } from '../../lib/nodeSpec';

const isMasterSkillNodeType = (nodeType: string | undefined): boolean => {
  if (!nodeType) return false;
  return (getCachedNodeSpec(nodeType)?.uiHints as any)?.isMasterSkillEditor === true;
};
// Wave 10.G.3: retired the three tribal arrays `SKILL_NODE_TYPES`,
// `TOOL_NODE_TYPES`, and `AGENT_WITH_SKILLS_TYPES`. The parameter panel
// now reads `uiHints.hasSkills` / `uiHints.isToolPanel` /
// `uiHints.isMasterSkillEditor` / `uiHints.isMemoryPanel` directly from
// the NodeSpec each plugin module declares.

const Stat: React.FC<{ title: React.ReactNode; value: React.ReactNode }> = ({ title, value }) => (
  <div className="flex flex-col">
    <span className="text-xs text-muted-foreground">{title}</span>
    <span className="text-lg font-semibold tabular-nums">{value}</span>
  </div>
);

interface ConnectedSkill {
  id: string;
  name: string;
  type: string;
  icon: string;
  description: string;
  color: string;
}

interface MiddleSectionProps {
  nodeId: string;
  nodeDefinition: INodeTypeDescription;
  parameters: Record<string, any>;
  onParameterChange: (paramName: string, value: any) => void;
  isLoadingParameters?: boolean;
  executionResults?: ExecutionResult[];
}

// shouldShowParameter moved to utils/parameterVisibility for sharing
// with ParameterRenderer's nested fixedCollection/collection recursion.

const MiddleSection: React.FC<MiddleSectionProps> = ({
  nodeId,
  nodeDefinition,
  parameters,
  onParameterChange,
  isLoadingParameters = false,
  executionResults = []
}) => {
  const currentWorkflow = useAppStore((s) => s.currentWorkflow);
  const { clearMemory, resetSkill, sendRequest, getNodeParameters, compactionStats: contextCompactionStats, updateCompactionStats } = useWebSocket();

  const [showClearMemoryDialog, setShowClearMemoryDialog] = useState(false);
  const [showResetSkillDialog, setShowResetSkillDialog] = useState(false);
  const [clearLongTermMemory, setClearLongTermMemory] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);

  const [isEditingThreshold, setIsEditingThreshold] = useState(false);
  const [editThresholdValue, setEditThresholdValue] = useState<number>(0);

  // Threshold edit commit goes through TanStack Query so the WS call,
  // toast feedback, and context-store patch live in one hook instead of
  // a setState + try/finally chain inline on the Save button.
  const configureCompactionMutation = useMutation({
    mutationFn: async (input: { sessionId: string; threshold: number }) => {
      await sendRequest('configure_compaction', {
        session_id: input.sessionId,
        threshold: input.threshold,
      });
      return input;
    },
    onSuccess: ({ sessionId, threshold }) => {
      const existing = contextCompactionStats[sessionId];
      if (existing && currentWorkflow?.id) {
        updateCompactionStats(currentWorkflow.id, sessionId, { ...existing, threshold });
      }
      setIsEditingThreshold(false);
      toast.success('Threshold updated');
    },
    onError: () => {
      toast.error('Failed to update threshold');
    },
  });
  const savingThreshold = configureCompactionMutation.isPending;

  // For Memory nodes: track the connected agent's ID for auto-session display
  const [connectedAgentId, setConnectedAgentId] = useState<string | null>(null);

  // Clear memory handler. Schema-canonical keys are snake_case
  // (post-Wave-11 plugin Pydantic models). Passing the active workflow
  // id lets the backend also clear sibling session-scoped state
  // (TodoService) keyed by workflow_id — see
  // ``services/memory.clear_agent_session_state``.
  const handleClearMemory = async () => {
    setIsProcessing(true);
    try {
      const sessionId = parameters.session_id || 'default';
      const result = await clearMemory(sessionId, clearLongTermMemory, currentWorkflow?.id);
      if (result.success && result.default_content) {
        onParameterChange('memory_content', result.default_content);
      }
    } finally {
      setIsProcessing(false);
      setShowClearMemoryDialog(false);
      setClearLongTermMemory(false);
    }
  };

  // Reset skill handler
  const handleResetSkill = async () => {
    setIsProcessing(true);
    try {
      const skillName = parameters.skillName;
      if (!skillName) return;
      const result = await resetSkill(skillName);
      if (result.success && result.original_content) {
        onParameterChange('instructions', result.original_content);
      }
    } finally {
      setIsProcessing(false);
      setShowResetSkillDialog(false);
    }
  };

  const visibleParams = (nodeDefinition.properties || [])
    .filter((param: INodeProperties) => shouldShowParameter(param, parameters));

  // Wave 10.G.3: pure schema-driven dispatch. Each flag is a declared
  // uiHint on the backend plugin registration — no `??` fallback
  // chains, no tribal arrays. Every widget decision reads the hint the
  // node's own module emits.
  const hints = nodeDefinition.uiHints ?? {};
  const isMasterSkillNode = hints.isMasterSkillEditor === true;
  const isMemoryNode = hints.isMemoryPanel === true;
  const needsCodeEditorLayout = hints.hasCodeEditor === true;
  const isCodeExecutorNode = needsCodeEditorLayout && !isMemoryNode && !isMasterSkillNode;
  // No seedable skills today besides masterSkill (handled via its own
  // editor). The reset-skill branch used to fire for SKILL_NODE_TYPES
  // members other than masterSkill; that set was empty after Wave 10, so
  // it's now permanently dead.
  const isSkillNode = false;
  const isToolNode = hints.isToolPanel === true;
  const isAgentWithSkills = hints.hasSkills === true;

  const { data: userSettings } = useUserSettingsQuery();

  // Apply the global memory_window_size default to new Memory nodes. Only
  // fires when a Memory node is opened AND the node hasn't had window_size
  // set yet (brand-new instance). Plugin schema field is snake_case.
  useEffect(() => {
    if (!isMemoryNode) return;
    const globalWindowSize = userSettings?.memory_window_size;
    if (globalWindowSize !== undefined && parameters.window_size === undefined) {
      onParameterChange('window_size', globalWindowSize);
    }
    // Same dep set as the previous imperative effect — only wake up on
    // node change, not on every parameter edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, isMemoryNode, userSettings?.memory_window_size]);

  const memoryEdgeSourceId = useMemo<string | null>(() => {
    if (!isAgentWithSkills || !currentWorkflow) return null;
    const edges: Edge[] = currentWorkflow.edges || [];
    const memoryEdge = edges.find(
      (edge) => edge.target === nodeId && edge.targetHandle === 'input-memory',
    );
    return memoryEdge?.source ?? null;
  }, [isAgentWithSkills, currentWorkflow, nodeId]);

  const memoryParamsQuery = useQuery<NodeParametersResponse | null, Error>({
    queryKey: memoryEdgeSourceId ? nodeParamsQueryKey(memoryEdgeSourceId) : nodeParamsQueryKey('none'),
    queryFn: () => (memoryEdgeSourceId ? getNodeParameters(memoryEdgeSourceId) : Promise.resolve(null)),
    enabled: !!memoryEdgeSourceId,
    staleTime: STALE_TIME.MEDIUM,
  });

  const memoryParamsData = memoryParamsQuery.data?.parameters;

  const connectedMemorySessionId = useMemo<string | null>(() => {
    if (!memoryEdgeSourceId) return null;
    // Schema-canonical session_id (Pydantic snake_case). Falls back to
    // the agent's nodeId when sessionId is the default placeholder so
    // each agent gets its own bucket.
    const configured: string = memoryParamsData?.session_id || '';
    return configured && configured !== 'default' ? configured : nodeId;
  }, [memoryEdgeSourceId, memoryParamsData?.session_id, nodeId]);

  const compactionStatsQuery = useQuery<{
    session_id: string;
    total: number;
    threshold: number;
    context_length?: number;
    count: number;
  }, Error>({
    ...queryKeys.compactionStats.bySession(
      connectedMemorySessionId ?? '',
      parameters.model || '',
      parameters.provider || '',
    ),
    queryFn: () =>
      sendRequest('get_compaction_stats', {
        session_id: connectedMemorySessionId,
        model: parameters.model || '',
        provider: parameters.provider || '',
      }),
    enabled: !!connectedMemorySessionId,
    staleTime: STALE_TIME.SHORT,
  });
  const compactionLoading = compactionStatsQuery.isFetching;

  useEffect(() => {
    const stats = compactionStatsQuery.data;
    if (!stats || !connectedMemorySessionId || !currentWorkflow?.id) return;
    updateCompactionStats(currentWorkflow.id, connectedMemorySessionId, {
      session_id: stats.session_id || connectedMemorySessionId,
      total: stats.total || 0,
      threshold: stats.threshold,
      context_length: stats.context_length || 0,
      count: stats.count || 0,
    });
  }, [compactionStatsQuery.data, connectedMemorySessionId, currentWorkflow?.id, updateCompactionStats]);

  const compactionStats: CompactionStats | null = connectedMemorySessionId
    ? contextCompactionStats[connectedMemorySessionId] || null
    : null;

  // For Memory nodes: find which AI Agent this memory is connected TO
  // Used to display the auto-derived session ID
  useEffect(() => {
    if (!isMemoryNode || !currentWorkflow) {
      setConnectedAgentId(null);
      return;
    }

    const edges = currentWorkflow.edges || [];

    // Find edge where this memory node is the SOURCE and target handle is 'input-memory'
    const agentEdge = edges.find((edge: Edge) =>
      edge.source === nodeId && edge.targetHandle === 'input-memory'
    );

    if (agentEdge) {
      setConnectedAgentId(agentEdge.target);
    } else {
      setConnectedAgentId(null);
    }
  }, [nodeId, isMemoryNode, currentWorkflow]);

  const skillEdges = useMemo<Edge[]>(() => {
    if (!isAgentWithSkills || !currentWorkflow) return [];
    return (currentWorkflow.edges || []).filter(
      (edge: Edge) => edge.target === nodeId && edge.targetHandle === 'input-skill',
    );
  }, [isAgentWithSkills, currentWorkflow, nodeId]);

  const masterSkillEdgeSources = useMemo<string[]>(() => {
    const nodes = currentWorkflow?.nodes || [];
    return skillEdges
      .filter((edge) => isMasterSkillNodeType(nodes.find((n: any) => n.id === edge.source)?.type))
      .map((edge) => edge.source);
  }, [skillEdges, currentWorkflow?.nodes]);

  const masterSkillParamsQueries = useQueries({
    queries: masterSkillEdgeSources.map((id) => ({
      queryKey: nodeParamsQueryKey(id),
      queryFn: () => getNodeParameters(id),
      staleTime: STALE_TIME.MEDIUM,
    })),
  });

  const masterSkillParams = useMemo<Record<string, any>>(() => {
    const out: Record<string, any> = {};
    masterSkillEdgeSources.forEach((id, i) => {
      const data = masterSkillParamsQueries[i]?.data;
      if (data?.parameters) out[id] = data.parameters;
    });
    return out;
  }, [masterSkillEdgeSources, masterSkillParamsQueries]);

  // Unique folders selected across the connected MasterSkill nodes;
  // each folder is fetched via `scan_skill_folder` so the per-child
  // SKILL.md frontmatter (icon / color / description) flows from the
  // backend instead of being hardcoded in the frontend. Default to
  // `'assistant'` when a master-skill node has no `skillFolder` value
  // saved yet, mirroring MasterSkillEditor's UI default — otherwise
  // freshly-toggled child skills render with no icon/color metadata.
  const masterSkillFolders = useMemo<string[]>(() => {
    if (masterSkillEdgeSources.length === 0) return [];
    const folders = new Set<string>();
    for (const id of masterSkillEdgeSources) {
      const folder = masterSkillParams[id]?.skill_folder;
      folders.add(typeof folder === 'string' && folder.length > 0 ? folder : 'assistant');
    }
    return Array.from(folders);
  }, [masterSkillEdgeSources, masterSkillParams]);

  const folderSkillsQueries = useQueries({
    queries: masterSkillFolders.map((folder) => ({
      queryKey: folderSkillsQueryKey(folder),
      queryFn: async (): Promise<AvailableSkill[]> => {
        const response = await sendRequest<{
          success: boolean;
          skills?: Array<{
            name: string;
            description: string;
            metadata?: Record<string, any>;
          }>;
        }>('scan_skill_folder', { folder });
        if (!response?.success || !response.skills) return [];
        return response.skills.map((s) => ({
          type: s.name,
          skillName: s.name,
          displayName: s.name,
          icon: s.metadata?.icon ?? '',
          color: s.metadata?.color ?? 'var(--node-agent)',
          description: s.description ?? '',
        }));
      },
      staleTime: STALE_TIME.MEDIUM,
      enabled: !!folder,
    })),
  });

  const folderSkillMetadataByName = useMemo<Record<string, AvailableSkill>>(() => {
    const out: Record<string, AvailableSkill> = {};
    for (const q of folderSkillsQueries) {
      for (const s of q.data ?? []) out[s.skillName] = s;
    }
    return out;
  }, [folderSkillsQueries]);

  // Names of currently-enabled skills inside connected Master Skill
  // nodes that are NOT covered by the folder-scoped query above (e.g.
  // a skill the user enabled when the masterSkill was on folder A but
  // then moved to folder B). Backend `lookup_skill_metadata` queries
  // the global registry by name so we can resolve their metadata
  // regardless of folder.
  const orphanSkillNames = useMemo<string[]>(() => {
    const names = new Set<string>();
    for (const id of masterSkillEdgeSources) {
      const cfg = (masterSkillParams[id]?.skills_config ?? {}) as Record<string, any>;
      for (const [skillName, c] of Object.entries(cfg)) {
        if (c?.enabled && !folderSkillMetadataByName[skillName]) {
          names.add(skillName);
        }
      }
    }
    return Array.from(names).sort();
  }, [masterSkillEdgeSources, masterSkillParams, folderSkillMetadataByName]);

  const orphanSkillsQuery = useQuery<Record<string, AvailableSkill>, Error>({
    queryKey: ['skillMetadataLookup', orphanSkillNames.join(',')],
    queryFn: async () => {
      const response = await sendRequest<{
        success: boolean;
        skills?: Array<{ name: string; description: string; metadata?: Record<string, any> }>;
      }>('lookup_skill_metadata', { names: orphanSkillNames });
      if (!response?.success || !response.skills) return {};
      const out: Record<string, AvailableSkill> = {};
      for (const s of response.skills) {
        out[s.name] = {
          type: s.name,
          skillName: s.name,
          displayName: s.name,
          icon: s.metadata?.icon ?? '',
          color: s.metadata?.color ?? 'var(--node-agent)',
          description: s.description ?? '',
        };
      }
      return out;
    },
    enabled: orphanSkillNames.length > 0,
    staleTime: STALE_TIME.MEDIUM,
  });

  const skillMetadataByName = useMemo<Record<string, AvailableSkill>>(
    () => ({ ...folderSkillMetadataByName, ...(orphanSkillsQuery.data ?? {}) }),
    [folderSkillMetadataByName, orphanSkillsQuery.data],
  );

  const connectedSkills = useMemo<ConnectedSkill[]>(() => {
    const nodes = currentWorkflow?.nodes || [];
    const skills: ConnectedSkill[] = [];
    for (const edge of skillEdges) {
      const sourceNode = nodes.find((n: any) => n.id === edge.source);
      const nodeType = sourceNode?.type || '';
      if (isMasterSkillNodeType(nodeType)) continue;
      const def = resolveNodeDescription(nodeType);
      skills.push({
        id: edge.source,
        name: sourceNode?.data?.label || def?.displayName || nodeType,
        type: nodeType,
        icon: def?.icon || '',
        description: def?.description || '',
        color: (def?.defaults?.color as string) || 'var(--node-agent)',
      });
    }
    return skills;
  }, [skillEdges, currentWorkflow?.nodes]);

  // Expand Master Skill enabled skills into connected skills list
  const expandedConnectedSkills = React.useMemo(() => {
    const skills = [...connectedSkills];

    // Get Master Skill node IDs from edges
    const nodes = currentWorkflow?.nodes || [];
    const edges = currentWorkflow?.edges || [];
    const skillEdges = edges.filter((edge: Edge) =>
      edge.target === nodeId && edge.targetHandle === 'input-skill'
    );

    for (const edge of skillEdges) {
      const sourceNode = nodes.find((n: any) => n.id === edge.source);
      if (isMasterSkillNodeType(sourceNode?.type)) {
        const params = masterSkillParams[edge.source];
        const skillsConfig = params?.skills_config || {};

        // Wave 10.G.3: the previous `SKILL_NODE_TYPES.find(...)` loop
        // was dead code. After Wave 10 `skillNodes.ts` exports only
        // `masterSkill` itself, so the per-skill lookup always missed.
        // Master Skill children are instruction blobs seeded from
        // skill folders on the backend, not separate frontend node
        // defs — render them with the masterSkill fallback inline.
        for (const [skillName, config] of Object.entries(skillsConfig as Record<string, any>)) {
          if (!config?.enabled) continue;
          // Pull icon / color / description from the backend's
          // SKILL.md frontmatter (via `scan_skill_folder`) so each
          // child skill renders with its authored branding instead
          // of a hardcoded frontend placeholder.
          const meta = skillMetadataByName[skillName];
          skills.push({
            id: `${edge.source}_${skillName}`,
            name: meta?.displayName || skillName,
            type: 'masterSkill',
            icon: meta?.icon || '',
            description: meta?.description || '',
            color: meta?.color || 'var(--node-agent)',
          });
        }
      }
    }

    return skills;
  }, [connectedSkills, masterSkillParams, currentWorkflow, nodeId, skillMetadataByName]);

  // Extract console output from execution results
  const getConsoleOutput = (): string => {
    if (executionResults.length === 0) return '';

    const latestResult = executionResults[0];
    const outputs = latestResult.outputs || latestResult.data || latestResult.nodeData?.[0]?.[0]?.json;

    if (!outputs) return '';

    // Check for console_output or stdout in the result
    if (outputs.console_output) return outputs.console_output;
    if (outputs.stdout) return outputs.stdout;
    if (outputs.result?.console_output) return outputs.result.console_output;
    if (outputs.result?.stdout) return outputs.result.stdout;

    // Check for error output
    if (latestResult.error) return `Error: ${latestResult.error}`;
    if (outputs.error) return `Error: ${outputs.error}`;
    if (outputs.stderr) return `Error: ${outputs.stderr}`;

    return '';
  };

  const consoleOutput = isCodeExecutorNode ? getConsoleOutput() : '';

  return (
    <div className="relative flex h-full flex-1 flex-col overflow-hidden">
      {/* Description - hide for code editor nodes (Python, Skill) and masterSkill */}
      {!needsCodeEditorLayout && !isMasterSkillNode && (
        <div className="shrink-0 border-b border-border-default bg-bg-panel px-6 pt-4 pb-2">
          <p className="m-0 text-base leading-[1.5] text-fg-muted">
            {nodeDefinition.description}
          </p>
        </div>
      )}

      {/* Main Content Area - Flexible */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {/* Master Skill Editor - Full panel for masterSkill nodes */}
        {isMasterSkillNode ? (
          <div className="flex flex-1 flex-col overflow-hidden p-4">
            <MasterSkillEditor
              skillsConfig={parameters.skills_config || {}}
              onConfigChange={(config) => onParameterChange('skills_config', config)}
              skillFolder={parameters.skill_folder || 'assistant'}
              onSkillFolderChange={(folder) => onParameterChange('skill_folder', folder)}
              nodeId={nodeId}
            />
          </div>
        ) : (
        <>
        {/* Parameters */}
        <div
          className={cn(
            'box-border min-h-0 w-full overflow-x-hidden p-6',
            needsCodeEditorLayout
              ? 'flex flex-[3] flex-col overflow-y-hidden'
              : 'block flex-1 overflow-y-auto',
          )}
        >
          {/* Parameters Container */}
          <div
            className={cn(
              'box-border rounded-md border border-border-default bg-bg-elevated p-4 shadow-sm',
              needsCodeEditorLayout ? 'flex h-full flex-col' : 'block h-auto',
            )}
          >
            {/* All Parameters - standard n8n style */}
            {visibleParams.map((param: INodeProperties, index: number) => {
              // Check if this parameter is a code editor - give it more flex space
              const isCodeParam = (param as any).typeOptions?.editor === 'code';
              const isLast = index === visibleParams.length - 1;
              return (
              <div
                key={param.name}
                className={cn(
                  needsCodeEditorLayout ? 'flex flex-col' : 'block',
                  !isLast && 'mb-3 border-b border-border-default pb-3',
                  needsCodeEditorLayout && isCodeParam ? 'min-h-[300px] flex-1' : 'min-h-0 flex-none',
                )}
              >
                <ParameterRenderer
                  parameter={param}
                  value={parameters[param.name]}
                  onChange={(value) => onParameterChange(param.name, value)}
                  allParameters={parameters}
                  onParameterChange={onParameterChange}
                  onClosePanel={() => {}}
                  isLoadingParameters={isLoadingParameters}
                  connectedAgentId={isMemoryNode ? connectedAgentId : undefined}
                />
              </div>
              );
            })}

            {/* Tool Schema Editor - Only for tool nodes */}
            {isToolNode && (
              <ToolSchemaEditor
                nodeId={nodeId}
                toolName={parameters.tool_name || nodeDefinition.name}
                toolDescription={parameters.tool_description || nodeDefinition.description || ''}
              />
            )}

            {/* Clear Memory Button - Only for memory nodes */}
            {isMemoryNode && (
              <div className="mt-3 flex justify-end border-t border-border-default pt-3">
                <ActionButton
                  intent="stop"
                  onClick={() => setShowClearMemoryDialog(true)}
                  className="h-8"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Clear Memory
                </ActionButton>
              </div>
            )}

            {/* Reset Skill Button - Only for built-in skill nodes */}
            {isSkillNode && (
              <div className="mt-3 flex justify-end border-t border-border-default pt-3">
                <ActionButton
                  intent="config"
                  onClick={() => setShowResetSkillDialog(true)}
                  className="h-8"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  Reset to Default
                </ActionButton>
              </div>
            )}
          </div>

          {/* Clear Memory Confirmation Dialog (shadcn AlertDialog) */}
          <AlertDialog
            open={showClearMemoryDialog}
            onOpenChange={(open) => {
              if (!open) {
                setShowClearMemoryDialog(false);
                setClearLongTermMemory(false);
              }
            }}
          >
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Clear Conversation Memory</AlertDialogTitle>
                <AlertDialogDescription>
                  This will reset the conversation history to its initial state. This action cannot be undone.
                </AlertDialogDescription>
              </AlertDialogHeader>
              {parameters.long_term_enabled && (
                <label className="flex cursor-pointer items-center gap-2 text-sm text-foreground">
                  <Checkbox
                    checked={clearLongTermMemory}
                    onCheckedChange={(checked) => setClearLongTermMemory(checked === true)}
                  />
                  Also clear long-term memory (vector store)
                </label>
              )}
              <AlertDialogFooter>
                <AlertDialogCancel disabled={isProcessing}>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  variant="destructive"
                  disabled={isProcessing}
                  onClick={handleClearMemory}
                >
                  {isProcessing ? 'Clearing...' : 'Clear Memory'}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {/* Reset Skill Confirmation Dialog (shadcn AlertDialog) */}
          <AlertDialog
            open={showResetSkillDialog}
            onOpenChange={(open) => !open && setShowResetSkillDialog(false)}
          >
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Reset Skill to Default</AlertDialogTitle>
                <AlertDialogDescription>
                  This will restore the skill instructions to their original content. Any customizations will be lost.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={isProcessing}>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  disabled={isProcessing}
                  onClick={handleResetSkill}
                >
                  {isProcessing ? 'Resetting...' : 'Reset to Default'}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {/* Token Usage Section - Only for agent nodes with memory connected */}
          {isAgentWithSkills && connectedMemorySessionId && (
            <Accordion type="single" collapsible defaultValue="tokens" className="mt-4">
              <AccordionItem value="tokens">
                <AccordionTrigger>
                  {(() => {
                    const ctxLen = compactionStats?.context_length || 0;
                    const displayMax = ctxLen > 0 ? ctxLen : compactionStats?.threshold || 0;
                    return (
                      <span className="font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-fg-default flex flex-1 items-center gap-2">
                        <Zap className="h-4 w-4" />
                        Token Usage
                        {compactionStats && displayMax > 0 && (
                          <span className="ml-auto text-xs text-fg-muted normal-case tracking-normal">
                            {Math.round(compactionStats.total / 1000)}K / {Math.round(displayMax / 1000)}K{ctxLen > 0 ? ' context' : ''}
                          </span>
                        )}
                      </span>
                    );
                  })()}
                </AccordionTrigger>
                <AccordionContent>
                  {compactionLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                ) : compactionStats ? (() => {
                  const ctxLen = compactionStats.context_length || 0;
                  const hasContext = ctxLen > 0;
                  const progressMax = hasContext ? ctxLen : compactionStats.threshold;
                  const percent = progressMax > 0 ? Math.round((compactionStats.total / progressMax) * 100) : 0;
                  const isWarning = hasContext
                    ? compactionStats.total >= ctxLen * 0.8
                    : compactionStats.total >= compactionStats.threshold * 0.8;
                  return (
                    <>
                      <Progress
                        value={Math.min(percent, 100)}
                        className={cn(isWarning && '[&>div]:bg-destructive')}
                      />
                      {hasContext && compactionStats.threshold > 0 && (
                        <span className="mt-1 block text-[11px] text-muted-foreground">
                          Compaction at {Math.round(compactionStats.threshold / 1000)}K ({Math.round(compactionStats.threshold / ctxLen * 100)}% of context)
                        </span>
                      )}
                      <div className="mt-3 grid grid-cols-12 gap-4">
                        <div className="col-span-4">
                          <Stat title="Total" value={compactionStats.total} />
                        </div>
                        <div className="col-span-4">
                          {isEditingThreshold ? (
                            <div>
                              <span className="text-xs text-muted-foreground">Threshold</span>
                              <div className="mt-1 flex gap-1">
                                <Input
                                  type="number"
                                  value={editThresholdValue}
                                  onChange={(e) => setEditThresholdValue(Number(e.target.value) || compactionStats.threshold)}
                                  min={10000}
                                  max={2000000}
                                  step={10000}
                                  className="h-7 w-28 text-xs"
                                />
                                <ActionButton
                                  intent="save"
                                  className="h-7 w-7 px-0"
                                  disabled={savingThreshold || !connectedMemorySessionId}
                                  onClick={() => {
                                    if (!connectedMemorySessionId) return;
                                    configureCompactionMutation.mutate({
                                      sessionId: connectedMemorySessionId,
                                      threshold: editThresholdValue,
                                    });
                                  }}
                                >
                                  {savingThreshold ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                                </ActionButton>
                              </div>
                            </div>
                          ) : (
                            <div onClick={() => { setEditThresholdValue(compactionStats.threshold); setIsEditingThreshold(true); }} className="cursor-pointer">
                              <Stat
                                title={
                                  <span className="inline-flex items-center gap-1">
                                    Threshold
                                    <Pencil className="h-3 w-3" />
                                  </span>
                                }
                                value={compactionStats.threshold}
                              />
                            </div>
                          )}
                        </div>
                        <div className={hasContext ? 'col-span-2' : 'col-span-4'}>
                          <Stat title="Compactions" value={compactionStats.count} />
                        </div>
                        {hasContext && (
                          <div className="col-span-2">
                            <Stat title="Context" value={`${Math.round(ctxLen / 1000)}K`} />
                          </div>
                        )}
                      </div>
                      <span className="mt-2 block text-xs text-muted-foreground">
                        Session: {compactionStats.session_id}
                      </span>
                    </>
                  );
                })() : (
                  <span className="text-xs text-muted-foreground">No data yet. Run the agent to start tracking.</span>
                )}
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          )}

          {isAgentWithSkills && (
            <Accordion
              type="single"
              collapsible
              defaultValue="skills"
              className="mt-4"
            >
              <AccordionItem value="skills">
                <AccordionTrigger>
                  <span className="font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-fg-default flex flex-1 items-center gap-2">
                    <Sparkles className="h-4 w-4" />
                    Connected Skills
                    <Badge
                      variant={expandedConnectedSkills.length > 0 ? 'default' : 'outline'}
                      className="ml-auto normal-case tracking-normal"
                    >
                      {expandedConnectedSkills.length}
                    </Badge>
                  </span>
                </AccordionTrigger>
                <AccordionContent>
                  {expandedConnectedSkills.length === 0 ? (
                    <div className="flex flex-col items-center justify-center gap-1 py-6 text-center text-muted-foreground">
                      <Sparkles className="mb-1 h-8 w-8 opacity-50" />
                      <span className="text-sm">No skills connected</span>
                      <span className="text-xs">
                        Connect skill nodes to the Skill handle to add capabilities
                      </span>
                    </div>
                  ) : (
                    <div className="flex flex-col gap-2">
                      {expandedConnectedSkills.map((skill) => (
                        <div
                          key={skill.id}
                          className="flex items-start gap-3 rounded-md border border-border bg-muted/40 p-3"
                          style={{ borderLeft: `3px solid ${skill.color}` }}
                        >
                          <div
                            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-tint-soft"
                            // currentColor is the skill's brand color
                            // (SKILL.md frontmatter); `bg-tint-soft`
                            // mixes it against transparent at the
                            // canonical alpha (--tint-soft).
                            style={{ color: skill.color }}
                          >
                            <NodeIcon
                              icon={skill.icon}
                              className="h-5 w-5 text-lg"
                            />
                          </div>
                          <div className="min-w-0 flex-1 overflow-hidden">
                            <div className="mb-0.5 truncate text-sm font-semibold text-foreground">
                              {skill.name}
                            </div>
                            <div className="line-clamp-2 break-words text-xs text-muted-foreground">
                              {skill.description}
                            </div>
                          </div>
                          <Badge variant="success" className="shrink-0">Active</Badge>
                        </div>
                      ))}
                    </div>
                  )}
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          )}
        </div>

        {isCodeExecutorNode && (
          <div className="flex min-h-0 flex-1 flex-col px-6 pb-6">
            <Accordion type="single" collapsible defaultValue="console" className="flex min-h-0 flex-1 flex-col">
              <AccordionItem value="console" className="flex min-h-0 flex-1 flex-col">
                <AccordionTrigger>
                  <span className="font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-fg-default flex flex-1 items-center gap-2">
                    <TerminalSquare className="h-4 w-4" />
                    Console
                    {consoleOutput && (
                      <Badge variant="success" className="ml-auto normal-case tracking-normal">Output</Badge>
                    )}
                  </span>
                </AccordionTrigger>
                <AccordionContent className="flex min-h-0 flex-1 flex-col">
                  <div className="min-h-0 flex-1 overflow-y-auto rounded-md bg-bg-app p-2 font-mono text-sm leading-relaxed">
                    {consoleOutput ? (
                      <pre
                        className={cn(
                          'm-0 whitespace-pre-wrap break-words',
                          consoleOutput.startsWith('Error') ? 'text-destructive' : 'text-success',
                        )}
                      >
                        {consoleOutput}
                      </pre>
                    ) : (
                      <div className="flex h-full min-h-10 flex-col items-center justify-center text-muted-foreground">
                        <TerminalSquare className="mb-1 h-6 w-6 opacity-50" />
                        <span className="text-xs">Run the code to see console output</span>
                      </div>
                    )}
                  </div>
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          </div>
        )}
        </>
        )}
      </div>
    </div>
  );
};

export default MiddleSection;
