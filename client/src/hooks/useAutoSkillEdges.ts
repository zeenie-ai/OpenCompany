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

import { useCallback } from 'react';
import { useAppStore } from '../store/useAppStore';
import type { Node, Edge, Connection } from 'reactflow';
import { useWebSocket } from '../contexts/WebSocketContext';
import { useUserSettingsQuery } from './useUserSettingsQuery';
import { applyOperations, type WorkflowOperation } from '../lib/workflowOps';
import { getCachedNodeSpec } from '../lib/nodeSpec';

const SKILL_HANDLE = 'input-skill';

const isMasterSkillNode = (nodeType: string | undefined): boolean => {
  if (!nodeType) return false;
  return (getCachedNodeSpec(nodeType)?.uiHints as any)?.isMasterSkillEditor === true;
};

interface UseAutoSkillEdgesProps {
  baseOnConnect: (params: Edge | Connection) => void;
  baseOnEdgesDelete: (deleted: Edge[]) => void;
  nodes: Node[];
  edges: Edge[];
  setNodes: (updater: (ns: Node[]) => Node[]) => void;
  setEdges: (updater: (es: Edge[]) => Edge[]) => void;
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
}: UseAutoSkillEdgesProps) {
  const { sendRequest, saveNodeParameters, getNodeParameters } = useWebSocket();
  const settingsQuery = useUserSettingsQuery();
  const enabled = settingsQuery.data?.auto_add_skill_for_tools ?? true;

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
    (params: Edge | Connection) => {
      baseOnConnect(params);
      void dispatch('connect', params);
    },
    [baseOnConnect, dispatch],
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
