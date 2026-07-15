/** Shared React Query identity for live writeTodos state. */

export interface TodoEventIdentity {
  workflow_id?: string | null;
  node_id?: string | null;
  session_key?: string | null;
}

/** One cache entry per workflow + writeTodos node. */
export const todoQueryKey = (workflowId: string | null | undefined, nodeId: string) =>
  ['todos', 'v2', workflowId ?? 'unsaved', nodeId] as const;

/**
 * Route a todos_updated event to the exact node cache. Events from legacy
 * servers that omit node_id retain their historical session-key route.
 */
export const todoQueryKeyFromEvent = (
  identity: TodoEventIdentity | null | undefined,
  subject?: string | null,
) => {
  if (identity?.node_id) {
    return todoQueryKey(identity.workflow_id, identity.node_id);
  }

  const legacySessionKey = identity?.session_key || subject;
  return legacySessionKey ? (['todos', legacySessionKey] as const) : null;
};
