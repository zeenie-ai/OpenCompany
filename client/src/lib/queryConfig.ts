/**
 * Centralized TanStack Query configuration.
 *
 * All cache-time constants and cross-file query keys live here. Hook-local
 * keys (those consumed in a single file) stay next to their owning hook;
 * only keys read or written from more than one module are centralized.
 *
 * Query keys are built with the community-standard
 * `@lukemorales/query-key-factory` (recommended by the TanStack Query
 * docs) so key shapes, partial-match `_def` scopes, and TypeScript
 * inference are all library-managed instead of hand-rolled.
 *
 * See:
 *   docs-internal/credentials_scaling/architecture.md (cache shape)
 *   docs-internal/frontend_architecture.md            (WS-push model)
 *   https://tanstack.com/query/v5/docs/framework/react/community/lukemorales-query-key-factory
 */

import { createQueryKeys, mergeQueryKeys } from '@lukemorales/query-key-factory';

/**
 * Stale-time buckets keyed by update cadence of the underlying data.
 * Values are milliseconds.
 */
export const STALE_TIME = {
  /** Fast-changing data; tolerates occasional refetch on mount. */
  SHORT: 30_000,
  /** Moderately stable (user settings, node parameters). */
  MEDIUM: 60_000,
  /** Stable data held across route changes and modal cycles. */
  LONG: 5 * 60_000,
  /** Data that only changes with a backend deploy or explicit invalidate. */
  FOREVER: Infinity,
} as const;

/**
 * Garbage-collection times. Longer values keep cache entries in memory
 * after the last consumer unmounts, improving warm-open latency.
 */
export const GC_TIME = {
  DEFAULT: 5 * 60_000,
  CATALOGUE: 10 * 60_000,
  /**
   * Never garbage-collect. Use for catalogue / NodeSpec entries that
   * are subscribed via `useSyncExternalStore` (no `useQuery` observer
   * holds them alive) and survive page reloads via the persistor in
   * `lib/queryPersist.ts`. Without this, idle entries vanish from the
   * cache after `DEFAULT` and every consumer reads `undefined`.
   */
  FOREVER: Infinity,
} as const;

/**
 * Stored credential field values for a provider panel in the credentials
 * modal. One entry per provider. useCredentialPanel reads + updates it;
 * validate/save/remove mutations write through queryClient.setQueryData
 * so the cache always matches backend state and stays visible in
 * devtools.
 */
const credentialValues = createQueryKeys('credentialValues', {
  byProvider: (provider: string) => [provider],
});

/**
 * Compaction / token-usage stats for a memory session. MiddleSection
 * reads it; WebSocketContext bridges `token_usage_update` and
 * `compaction_completed` broadcasts into it.
 */
const compactionStats = createQueryKeys('compactionStats', {
  bySession: (sessionId: string, model: string, provider: string) =>
    [sessionId, model, provider],
});

/**
 * One chat session's conversation, across restarts (session id = the
 * workflow id). Home's employee thread reads it; WebSocketContext
 * invalidates it on `chat.updated`, on a runtime reset and whenever the
 * socket reopens.
 */
const chatThread = createQueryKeys('chatThread', {
  bySession: (sessionId: string) => [sessionId],
});

/**
 * What one chat session's message box offers (`get_chat_context`; session
 * id = the workflow id): its commands, attachments, Web, and whether the
 * employee takes the model picker's choice. The workflow's saved graph
 * decides it, so useSavedGraphSync invalidates it when the server saves a
 * change to that graph (Turn on Talk, the Agent Builder).
 */
const chatContext = createQueryKeys('chatContext', {
  bySession: (sessionId: string) => [sessionId],
});

/**
 * What each chat run of one session cost (`get_chat_run_costs`; session id
 * = the workflow id). The reply bar reads it (hooks/useUsageQueries.ts);
 * WebSocketContext invalidates it on `chat.updated`, which the server sends
 * when a reply is saved and when a run ends.
 */
const chatRunCosts = createQueryKeys('chatRunCosts', {
  bySession: (sessionId: string) => [sessionId],
});

/**
 * Merged factory. Consumers call e.g.
 *   queryKeys.credentialValues.byProvider(id).queryKey
 * for a specific key, and
 *   queryKeys.credentialValues._def
 * for partial-match invalidation across the namespace.
 */
export const queryKeys = mergeQueryKeys(credentialValues, compactionStats, chatThread, chatContext, chatRunCosts);
