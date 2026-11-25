/**
 * Wraps useReactFlowNodes' onConnect / onEdgesDelete with the
 * auto-add-skill behaviour: when a tool node is connected to (or
 * disconnected from) an AI agent's `input-tools` handle, dispatch the
 * decision to the backend (`evaluate_auto_skill`) and apply the
 * returned workflow-ops batch via `applyOperations`.
 *
 * This hook is a pure orchestrator -- no domain rules live here. The
 * backend owns:
 *   - whether the source node has a paired skill (visuals.json)
 *   - whether the target node is an agent (plugin registry)
 *   - the canonical SkillConfig shape and toggle semantics
 *
 * The frontend only does:
 *   - a trivial edge filter to find any Master Skill already wired to
 *     the agent's input-skill handle (pure React Flow state lookup)
 *   - apply the backend's chosen ops via the standard applier
 *     (lib/workflowOps.ts) which handles add_node / add_edge /
 *     set_node_parameters and resolves cross-op client_ref placeholders.
 *
 * The user-level toggle (`auto_add_skill_for_tools` in UserSettings)
 * gates the WS round-trip so disabled state has zero overhead.
 */

import { useCallback, useRef } from 'react';
import { useAppStore } from '../store/useAppStore';
import type { Node, Edge, Connection } from 'reactflow';
import { useWebSocket } from '../contexts/WebSocketContext';
import { useUserSettingsQuery } from './useUserSettingsQuery';
import { applyOperations, addSavedNodes, addSavedEdges, type WorkflowOperation } from '../lib/workflowOps';
import { fetchNodeSpec, getCachedNodeSpec } from '../lib/nodeSpec';
import { createPrivateBrowser, createSavedGraphResource } from '../services/agentCreationApi';
import { toast } from 'sonner';
import { queryClient } from '../lib/queryClient';
import { nodeParamsQueryKey } from './useNodeParamsQuery';
import type { NodeParameters } from '../contexts/WebSocketContext';

const SKILL_HANDLE = 'input-skill';

const isMasterSkillNode = (nodeType: string | undefined): boolean => {
  if (!nodeType) return false;
  return (getCachedNodeSpec(nodeType)?.uiHints as any)?.isMasterSkillEditor === true;
};

interface UseAutoSkillEdgesProps {
  baseOnConnect: (params: Edge | Connection) => boolean | void;
  baseOnEdgesDelete: (deleted: Edge[]) => void;
  nodes: Node[];
  edges: Edge[];
  setNodes: (updater: (ns: Node[]) => Node[]) => void;
  setEdges: (updater: (es: Edge[]) => Edge[]) => void;
  flushCanvas?: () => void;
}

/** Presentation hint only; the server validates the saved reservation. */
export function browserBindingProblem(source: Node | undefined, target: Node | undefined, targetHandle?: string | null, runtimeVersion = 0): 'private_browser_required' | 'reserved_browser' | null {
  if (!source || !target || targetHandle !== 'input-tools' || source.type !== 'browser') return null;
  const owner = source.data?.private_browser_owner;
  const fresh = runtimeVersion === 1;
  if (fresh && (owner?.runtime_version !== 1 || owner.agent_node_id !== target.id)) return 'private_browser_required';
  if (!fresh && owner?.runtime_version === 1) return 'reserved_browser';
  return null;
}

interface EvaluateAutoSkillResponse {
  operations: WorkflowOperation[];
}

export function useAutoSkillEdges({
  baseOnConnect,
  baseOnEdgesDelete,
  nodes,
  edges,
  setNodes,
  setEdges,
  flushCanvas,
}: UseAutoSkillEdgesProps) {
  const { sendRequest, saveNodeParameters, getNodeParameters } = useWebSocket();
  const settingsQuery = useUserSettingsQuery();
  const enabled = settingsQuery.data?.auto_add_skill_for_tools ?? true;
  const pendingConnections = useRef(new Set<string>());
  const acceptedConnections = useRef(new Set<string>());
  const latestCanvas = useRef({ nodes, edges });
  latestCanvas.current = { nodes, edges };
  const lastEdges = useRef(edges);
  if (lastEdges.current !== edges) {
    lastEdges.current = edges;
    acceptedConnections.current.clear();
  }

  const dispatch = useCallback(
    async (action: 'connect' | 'disconnect', edgeLike: Edge | Connection) => {
      if (!enabled) return;
      const sourceId = edgeLike.source;
      const targetId = edgeLike.target;
      if (!sourceId || !targetId) return;

      const source = nodes.find(n => n.id === sourceId);
      const target = nodes.find(n => n.id === targetId);
      if (!source || !target) return;

      // Trivial RF state lookup: which masterSkill (if any) is wired
      // into the target agent's input-skill handle? Not policy.
      const skillEdge = edges.find(
        e => e.target === targetId && e.targetHandle === SKILL_HANDLE,
      );
      const masterSkillNode = skillEdge && nodes.find(
        n => n.id === skillEdge.source && isMasterSkillNode(n.type),
      );
      const masterSkillId = masterSkillNode?.id ?? null;
      // node.data only stores the label (see CLAUDE.md "Node Data
      // Architecture") -- params live in the database. Fetch the
      // current skills_config so the backend can toggle the new tool's
      // skill without wiping previously enabled siblings.
      const masterParams = masterSkillId
        ? await getNodeParameters(masterSkillId)
        : null;
      const masterSkillConfig = masterParams?.parameters?.skills_config ?? null;

      const result = await sendRequest<EvaluateAutoSkillResponse>('evaluate_auto_skill', {
        action,
        source_type: source.type ?? '',
        target_type: target.type ?? '',
        target_handle: edgeLike.targetHandle ?? '',
        target_node_id: targetId,
        master_skill_id: masterSkillId,
        master_skill_config: masterSkillConfig,
      });

      if (!result?.operations?.length) return;

      await applyOperations(result.operations, {
        workflowId: useAppStore.getState().currentWorkflow?.id,
        nodes,
        edges,
        setNodes,
        setEdges,
        saveNodeParameters,
      });
    },
    [enabled, nodes, edges, sendRequest, saveNodeParameters, getNodeParameters, setNodes, setEdges],
  );

  const onConnect = useCallback(
    async (params: Edge | Connection) => {
      const graph = useAppStore.getState().currentWorkflow;
      const source = nodes.find((node) => node.id === params.source);
      const target = nodes.find((node) => node.id === params.target);
      let runtimeVersion = 0;
      const pendingKey = JSON.stringify([graph?.id, params.source, params.target, params.targetHandle]);
      if (source?.type === 'browser' && target && params.targetHandle === 'input-tools') {
        if (pendingConnections.current.has(pendingKey) || acceptedConnections.current.has(pendingKey)) return;
        pendingConnections.current.add(pendingKey);
        try {
          const type = target.type || '';
          const spec = getCachedNodeSpec(type) ?? await fetchNodeSpec(type, sendRequest);
          if (!spec?.uiHints?.createsBrowserAgent) {
            toast.error(spec ? 'Connect the Browser tool to a Browser AI Agent.' : 'Could not check the browser connection. Try again.');
            return;
          }
          const cached = queryClient.getQueryData<NodeParameters>(nodeParamsQueryKey(target.id));
          const row = cached ?? await getNodeParameters(target.id);
          if (!row || useAppStore.getState().currentWorkflow !== graph
            || latestCanvas.current.nodes !== nodes || latestCanvas.current.edges !== edges) return;
          runtimeVersion = row.parameters?.runtime_behavior_version === 1 ? 1 : 0;
        } catch {
          toast.error('Could not check the browser connection. Try again.');
          return;
        } finally { pendingConnections.current.delete(pendingKey); }
        if ([...edges, ...(graph?.edges ?? [])].some((edge) => edge.source === params.source && edge.target === params.target && edge.targetHandle === params.targetHandle)) return;
      }
      const problem = browserBindingProblem(source, target, params.targetHandle, runtimeVersion);
      if (problem && target) {
        if (problem === 'reserved_browser') {
          toast.error('This browser belongs to another agent. Choose a separate Browser tool.');
          return;
        }
        const workflowId = useAppStore.getState().currentWorkflow?.id;
        const mutationId = crypto.randomUUID();
        const create = async () => {
          if (!workflowId) return;
          try {
            const result = await createSavedGraphResource(workflowId,
              flushCanvas ?? (() => useAppStore.getState().updateWorkflow({ nodes, edges })),
              () => createPrivateBrowser(target.id, { workflow_id: workflowId, position: [target.position.x - 240, target.position.y + 180] }, mutationId));
            if (result && useAppStore.getState().currentWorkflow?.id === workflowId) {
              setNodes((current) => addSavedNodes(current, result.operations));
              setEdges((current) => addSavedEdges(current, result.operations));
            }
          } catch (error) {
            toast.error(error instanceof Error ? error.message : 'Could not create the browser.', { action: { label: 'Retry', onClick: () => void create() } });
          }
        };
        toast.error('This agent needs a separate Browser profile. Sign in again in its new browser.', {
          action: { label: 'Create separate Browser', onClick: () => void create() },
        });
        return;
      }
      if (baseOnConnect(params) === false) return;
      if (source?.type === 'browser' && params.targetHandle === 'input-tools') acceptedConnections.current.add(pendingKey);
      void dispatch('connect', params);
    },
    [baseOnConnect, dispatch, nodes, edges, flushCanvas, setNodes, setEdges, getNodeParameters],
  );

  const onEdgesDelete = useCallback(
    (deleted: Edge[]) => {
      baseOnEdgesDelete(deleted);
      // Each deleted edge gets its own dispatch -- backend filters out
      // anything that doesn't match the tool->agent input-tools shape.
      for (const edge of deleted) {
        void dispatch('disconnect', edge);
      }
    },
    [baseOnEdgesDelete, dispatch],
  );

  return { onConnect, onEdgesDelete };
}
