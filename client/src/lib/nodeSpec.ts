/**
 * NodeSpec fetch + resolve utilities.
 *
 * Wave 6 Phase 2. Thin wrapper around TanStack Query's `fetchQuery`
 * mirroring the Wave 3 pattern at InputSection.tsx:28-36 — no new hook
 * file, just a shared async helper so every consumer can opt in behind
 * the VITE_NODESPEC_BACKEND flag without duplicating boilerplate.
 *
 * Query key convention: ['nodeSpec', nodeType]
 * Stale time: Infinity (node shapes only change with a deploy).
 *
 * See C:\\Users\\Tgroh\\.claude\\plans\\typed-splashing-crown.md.
 */

import { useEffect, useSyncExternalStore } from 'react';
import { hashKey, useQuery, type UseQueryResult } from '@tanstack/react-query';
import { nodeSpecToDescription, type NodeSpec } from '../adapters/nodeSpecToDescription';
import type { INodeTypeDescription } from '../types/INodeProperties';
import { featureFlags } from './featureFlags';
import { BRAND_STORAGE_KEYS, readAndMigrateStorageValue } from './brandStorage';
import { queryClient } from './queryClient';
import { GC_TIME, STALE_TIME } from './queryConfig';
import { useWebSocket } from '../contexts/WebSocketContext';

type SendRequest = (type: string, data?: any) => Promise<any>;

export const nodeSpecQueryKey = (nodeType: string) => ['nodeSpec', nodeType] as const;

/**
 * Fetch a single NodeSpec via the WS handler, caching per node type.
 * Returns `null` when the type is unknown on the backend — callers fall
 * back to the legacy `nodeDefinitions/*` entry.
 */
export async function fetchNodeSpec(
  nodeType: string,
  sendRequest: SendRequest,
): Promise<NodeSpec | null> {
  return queryClient.fetchQuery({
    queryKey: nodeSpecQueryKey(nodeType),
    queryFn: async () => {
      try {
        const response = await sendRequest('get_node_spec', { node_type: nodeType });
        return (response?.spec ?? null) as NodeSpec | null;
      } catch {
        return null;
      }
    },
    staleTime: STALE_TIME.FOREVER,
    // useNodeSpec subscribes via useSyncExternalStore (no observer), so
    // without an explicit gcTime override every spec entry is evicted
    // ~5 min after prefetch lands. That makes node icons + handles
    // disappear on every idle canvas. Keep the entries forever; the
    // persistor in lib/queryPersist.ts also writes them to localStorage.
    gcTime: GC_TIME.FOREVER,
  });
}

const NODESPEC_REVISION_STORAGE_KEYS = BRAND_STORAGE_KEYS.nodeSpecRevision;

/**
 * Prefetch every node type the backend knows about, in a single idle
 * burst at editor boot. Non-blocking — failures are swallowed so a flaky
 * WS doesn't block UI. Matches Activepieces' summary+lazy pattern.
 *
 * Self-busts the persisted spec cache when the backend catalogue
 * changes. The backend returns a content-hash `revision` derived from
 * the full NodeSpec catalogue (icons, uiHints, handles, schemas);
 * without this, persisted entries with `staleTime: FOREVER` survive
 * server deploys and the editor keeps rendering pre-deploy shapes
 * (e.g. masterSkill missing its `hideInputSection` hint). On revision
 * mismatch we evict the relevant prefixes before refetching so the
 * persistor overwrites stale localStorage entries on the next sync.
 */
export async function prefetchAllNodeSpecs(sendRequest: SendRequest): Promise<void> {
  try {
    const response = await sendRequest('list_node_specs', {});
    const nodeTypes: string[] = response?.node_types ?? [];
    const revision: string | undefined = response?.revision;

    if (revision && typeof window !== 'undefined') {
      const lastSeen = readAndMigrateStorageValue(
        window.localStorage,
        NODESPEC_REVISION_STORAGE_KEYS,
      );
      if (lastSeen !== revision) {
        queryClient.removeQueries({ queryKey: ['nodeSpec'] });
        queryClient.removeQueries({ queryKey: ['nodeGroups'] });
        window.localStorage.setItem(NODESPEC_REVISION_STORAGE_KEYS.canonical, revision);
      }
    }

    await Promise.all(nodeTypes.map(t => fetchNodeSpec(t, sendRequest)));
  } catch {
    // Prefetch is best-effort — logged by the WS layer, not us.
  }
}

/**
 * Synchronous read of a previously-cached NodeSpec. Used by the
 * non-reactive merge helpers (`resolveNodeDescription`,
 * `isNodeInBackendGroup`). Prefer `useNodeSpec` in React components
 * so the render updates when prefetch lands.
 */
export function getCachedNodeSpec(nodeType: string): NodeSpec | null {
  return queryClient.getQueryData<NodeSpec | null>(nodeSpecQueryKey(nodeType)) ?? null;
}

/**
 * Reactive spec subscription via the QueryCache as an external store.
 *
 * Previous implementation opened a `useQuery` observer per call site;
 * with ~80 palette items + N canvas nodes that produced 80+N observers,
 * each woken up on every cache write. The slice-subscription pattern
 * here re-renders only consumers of the *specific* spec key that
 * changed — observer count drops to zero. Pattern documented at
 * https://react.dev/reference/react/useSyncExternalStore and called
 * out by TanStack's docs as the canonical escape hatch from useQuery.
 *
 * Cache population is owned by `prefetchAllNodeSpecs` (boot once) and
 * the persisted localStorage hydration set up in lib/queryPersist.ts;
 * a missing key triggers a one-shot lazy fetch via useEffect rather
 * than a long-lived observer.
 */
export function useNodeSpec(nodeType: string | undefined | null): NodeSpec | null {
  const { sendRequest, isReady } = useWebSocket();
  const key = nodeSpecQueryKey(nodeType ?? '__none__');

  const targetHash = hashKey(key);
  const subscribe = (onChange: () => void) => {
    const unsub = queryClient.getQueryCache().subscribe((event) => {
      if (event.query.queryHash === targetHash) onChange();
    });
    return unsub;
  };

  const getSnapshot = () =>
    queryClient.getQueryData<NodeSpec | null>(key) ?? null;

  const data = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    if (!nodeType || !isReady) return;
    if (queryClient.getQueryData(key) !== undefined) return;
    void fetchNodeSpec(nodeType, sendRequest);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key derives from nodeType; including it is redundant.
  }, [nodeType, isReady, sendRequest]);

  return data;
}

/**
 * Reactive node-groups subscription. Same WS-in-queryFn pattern as
 * `useNodeSpec`. Returns the full `UseQueryResult` so callers can
 * branch on `data` / `isPending` / `error` and render a proper loading
 * state rather than masking missing data. Matches TkDodo's recommended
 * status-check pattern: check data first, error second, loading last.
 */
export function useNodeGroups(): UseQueryResult<Record<string, NodeGroupEntry>> {
  const { sendRequest, isReady } = useWebSocket();
  return useQuery<Record<string, NodeGroupEntry>>({
    queryKey: nodeGroupsQueryKey,
    queryFn: async () => {
      const response = await sendRequest('get_node_groups', {});
      return (response?.groups ?? {}) as Record<string, NodeGroupEntry>;
    },
    enabled: isReady,
    staleTime: STALE_TIME.FOREVER,
    gcTime: GC_TIME.FOREVER,
  });
}

/** Wire shape for one entry in the GET /api/schemas/nodes/groups
 *  response (Wave 10.B): per-group palette metadata + member types. */
export interface NodeGroupEntry {
  types: string[];
  label: string;
  icon: string;
  color: string;
  visibility: 'all' | 'normal' | 'dev';
}

export const nodeGroupsQueryKey = ['nodeGroups'] as const;

/**
 * Wave 10.B: fetch the full per-group palette index from the backend.
 * Cached forever (group metadata only changes with a redeploy). The
 * frontend ComponentPalette consumes this directly — no hand-rolled
 * `CATEGORY_ICONS` / `labelMap` / `SIMPLE_MODE_CATEGORIES` tables.
 */
export async function fetchNodeGroups(
  sendRequest: SendRequest,
): Promise<Record<string, NodeGroupEntry>> {
  return queryClient.fetchQuery({
    queryKey: nodeGroupsQueryKey,
    queryFn: async () => {
      const response = await sendRequest('get_node_groups', {});
      return (response?.groups ?? {}) as Record<string, NodeGroupEntry>;
    },
    staleTime: STALE_TIME.FOREVER,
    gcTime: GC_TIME.FOREVER,
  });
}

/**
 * Wave 10.E: enumerate every NodeSpec the cache currently holds.
 * Used by component palette / drag-drop / execution routing to derive
 * filtered lists from spec metadata instead of importing per-family
 * type arrays from `nodeDefinitions/*`.
 */
export function listCachedNodeSpecs(): NodeSpec[] {
  const all = queryClient.getQueriesData<NodeSpec | null>({ queryKey: ['nodeSpec'] });
  return all
    .map(([, spec]) => spec)
    .filter((s): s is NodeSpec => !!s);
}

/**
 * Wave 10.E: types whose backend spec lists ``group`` includes the
 * given group key. Returns an empty array until prefetch lands; callers
 * that need a synchronous answer should rely on the spec-driven
 * dispatcher rather than this enumeration.
 */
export function getNodeTypesInGroup(group: string): string[] {
  return listCachedNodeSpecs()
    .filter(s => (s.group ?? []).includes(group))
    .map(s => s.type);
}

/**
 * Wave 10.E: types whose backend spec carries the given componentKind.
 */
export function getNodeTypesWithKind(kind: NonNullable<NodeSpec['componentKind']>): string[] {
  return listCachedNodeSpecs()
    .filter(s => s.componentKind === kind)
    .map(s => s.type);
}

/**
 * Wave 6 Phase 5.b: backend-group membership check.
 *
 * Returns `undefined` when the NodeSpec isn't cached yet (caller falls
 * back to its legacy `*_NODE_TYPES` array), `true`/`false` when the
 * cached spec's `group` array decides. Lets components retire local
 * helper arrays without introducing a hard dependency on prefetch
 * ordering — when the flag is off and prefetch hasn't run, the legacy
 * path runs unchanged.
 */
export function isNodeInBackendGroup(
  nodeType: string | null | undefined,
  group: string,
): boolean | undefined {
  if (!nodeType) return false;
  const spec = getCachedNodeSpec(nodeType);
  if (!spec) return undefined;
  return (spec.group ?? []).includes(group);
}

/**
 * Wave 7 flag-gated resolver.
 *
 * Flag OFF: returns `localFallback` unchanged.
 * Flag ON + spec cold: falls back to `localFallback` so the editor
 *   never renders an empty panel during prefetch warmup.
 * Flag ON + spec warm: returns the backend NodeSpec adapted to the
 *   INodeTypeDescription shape. Local UX-only hints that are absent
 *   from the NodeSpec (starter code defaults, placeholder JSON) are
 *   merged in per-property so the adapter never regresses UX.
 *
 * Merge rules (backend wins on schema, local wins on UX):
 *   - Schema fields (type, options, displayOptions, typeOptions,
 *     validation, required, description): always backend when present
 *   - UX fields (placeholder): backend when non-empty, else local
 *   - default: backend when non-empty, else local (preserves starter
 *     code + example JSON blobs that live only in the frontend)
 */
export function resolveNodeDescription(
  nodeType: string,
  localFallback?: INodeTypeDescription | null,
): INodeTypeDescription | null {
  if (!featureFlags.nodeSpecBackend) {
    return localFallback ?? null;
  }
  const spec = getCachedNodeSpec(nodeType);
  if (!spec) return localFallback ?? null;

  const backend = nodeSpecToDescription(spec);
  if (!localFallback) return backend;

  // Per-property merge: keep backend as authoritative but pull UX
  // niceties (placeholder + non-empty default) from local when the
  // backend version is empty. Unknown local properties (no matching
  // backend entry) are dropped - backend is the schema SSOT.
  const localByName = new Map(
    (localFallback.properties ?? []).map((p) => [p.name, p]),
  );
  const mergedProperties = (backend.properties ?? []).map((bp) => {
    const lp = localByName.get(bp.name);
    if (!lp) return bp;
    const merged = { ...bp };
    const bpDefault = (bp as any).default;
    const bpDefaultEmpty =
      bpDefault === undefined ||
      bpDefault === null ||
      bpDefault === '' ||
      (typeof bpDefault === 'object' &&
        !Array.isArray(bpDefault) &&
        Object.keys(bpDefault).length === 0);
    if (bpDefaultEmpty && (lp as any).default !== undefined) {
      (merged as any).default = (lp as any).default;
    }
    if (!bp.placeholder && lp.placeholder) merged.placeholder = lp.placeholder;
    if (!bp.description && lp.description) merged.description = lp.description;
    return merged;
  });

  // Wave 10.B: backend NodeSpec is the sole source for top-level
  // visual metadata (icon, subtitle, description, color). Local
  // nodeDefinitions/*.ts entries carry no icons anymore, so we just
  // pass the backend values through and only preserve local
  // `defaults.color` when the backend doesn't declare one — color is
  // the last remaining UX field that a few specialised agent configs
  // still set locally.
  return {
    ...backend,
    defaults: {
      ...localFallback.defaults,
      ...backend.defaults,
      color: backend.defaults?.color || localFallback.defaults?.color,
    },
    properties: mergedProperties,
  };
}
