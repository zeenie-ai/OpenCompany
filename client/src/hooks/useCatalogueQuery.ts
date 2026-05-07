/**
 * Credentials catalogue fetch hook.
 *
 * Owns the 20 → 5000 provider catalogue. Follows the Phase 3 architecture
 * from `docs-internal/credentials_scaling/architecture.md`:
 *
 *   1. TanStack Query owns the server cache (`['credentialCatalogue']`).
 *   2. `idb-keyval` provides a warm-start persistence layer so the second
 *      app open is `< 50 ms` instead of a fresh WebSocket roundtrip.
 *   3. The hydration write is deferred via `requestIdleCallback` to avoid
 *      the 50-200 ms structured-clone main-thread block at app open.
 *   4. A server-side content-sha256 version hash lets us do conditional
 *      `since` fetches — if the catalogue is unchanged, the server returns
 *      `{unchanged: true}` and we keep using the cached data.
 *
 * The store (`useCredentialRegistry`) holds only UI state. Derived data
 * like the `byId` Map, the fuzzysort-prepared index, and the filtered
 * result set all live in `useMemo` inside `CredentialsPalette.tsx`.
 *
 * See: `docs-internal/credentials_scaling/research_react_stack.md` for the
 * store-shape decision and runtime/memory traps this design avoids.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useQuery, useQueryClient, type QueryClient, type UseQueryResult } from '@tanstack/react-query';
import { get as idbGet, set as idbSet } from 'idb-keyval';

import { useWebSocket } from '../contexts/WebSocketContext';

// ============================================================================
// Server-side shape (what the registry JSON actually contains)
// ============================================================================

/** Category metadata from credential_providers.json `categories` map. */
export interface ServerCategory {
  key: string;
  label: string;
  order: number;
}

/** Raw field descriptor from the JSON registry (no React / callable fields). */
export interface ServerFieldDef {
  key: string;
  label?: string;
  type?: 'string' | 'password';
  secret?: boolean;
  placeholder?: string;
  /** Initial value pre-populated when the user has nothing stored yet
      (e.g. canonical local-LLM Base URL — http://localhost:1234/v1).
      Distinct from `placeholder`: placeholder is a UI ghost-text hint;
      default actually fills the field so the user can click Fetch
      without typing. */
  default?: string;
  required?: boolean;
}

/** Raw status-row descriptor — uses a string `ok_field` instead of a callable. */
export interface ServerStatusRowDef {
  key: string;
  label: string;
  ok_field: string;
  true_text: string;
  false_text: string;
  warn?: boolean;
}

/** Raw action descriptor — disabled_when is a list of status field refs. */
export interface ServerActionDef {
  key: string;
  label: string;
  theme_color: string;
  disabled_when?: string[];
}

/** Raw QR config — uses string field refs instead of callables. */
export interface ServerQrDef {
  qr_field: string;
  is_connected_field: string;
  connected_title: string;
  connected_subtitle_static?: string;
  connected_subtitle_field?: string;
  connected_subtitle_fallback?: string;
  is_loading_fields?: string[];
  empty_text_no_key?: string;
  empty_text_running?: string;
  empty_text_stopped?: string;
  scan_text: string;
}

/** One entry as it appears in the `providers` array of the catalogue response. */
export interface ServerProviderConfig {
  id: string;
  name: string;
  category: string;
  category_label: string;
  color: string;
  kind: 'apiKey' | 'oauth' | 'qrPairing' | 'email';
  icon_ref?: string;
  fields?: ServerFieldDef[];
  ws?: { login: string; logout: string; status: string };
  status_hook?: 'whatsapp' | 'android' | 'twitter' | 'google' | 'telegram';
  status_rows?: ServerStatusRowDef[];
  actions?: ServerActionDef[];
  qr?: ServerQrDef;
  validate_as?: string;
  callback_url?: string;
  instructions?: string;
  has_defaults?: boolean;
  has_rate_limits?: boolean;
  usage_service?: string;
  /** Server-resolved: whether a key/token is stored in the credentials DB. */
  stored?: boolean;
  /** Connected account identifier (email or display name) for OAuth providers. */
  account_label?: string | null;
}

export interface CatalogueResponse {
  providers: ServerProviderConfig[];
  categories: ServerCategory[];
  version: string;
}

/** Conditional-fetch response when the client already has the current version. */
interface UnchangedResponse {
  unchanged: true;
  version: string;
  /** Narrowing marker — never present on CatalogueResponse. */
  providers?: never;
  categories?: never;
}

type WsResponse = CatalogueResponse | UnchangedResponse;

/** Type guard: true if the server returned the 304-style unchanged marker. */
function isUnchanged(response: WsResponse): response is UnchangedResponse {
  return 'unchanged' in response && response.unchanged === true;
}

// ============================================================================
// Constants
// ============================================================================

export const CATALOGUE_QUERY_KEY = ['credentialCatalogue'] as const;

// ============================================================================
// Debounced invalidation
//
// 8 broadcast handlers in WebSocketContext (api_key_status, whatsapp_status,
// twitter_oauth_complete, google_oauth_complete, google_status,
// telegram_status, credential_catalogue_updated, initial_status) all want to
// refetch the catalogue. During init-burst or multi-service reconnect, those
// fire within the same tick and would trigger N back-to-back roundtrips.
// Coalesce them onto a single invalidate at the trailing edge of a 300ms
// quiet window. Trailing-edge debounce so the freshest state always wins.
// ============================================================================

const CATALOGUE_INVALIDATE_DEBOUNCE_MS = 300;
let _catalogueInvalidateTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Request a catalogue refetch, coalesced across rapid bursts of broadcasts.
 * Replaces direct `queryClient.invalidateQueries({ queryKey: CATALOGUE_QUERY_KEY })`.
 */
export function invalidateCatalogue(queryClient: QueryClient): void {
  if (_catalogueInvalidateTimer) clearTimeout(_catalogueInvalidateTimer);
  _catalogueInvalidateTimer = setTimeout(() => {
    _catalogueInvalidateTimer = null;
    void queryClient.invalidateQueries({ queryKey: CATALOGUE_QUERY_KEY });
  }, CATALOGUE_INVALIDATE_DEBOUNCE_MS);
}

/** IDB key — we only store the current version, overwritten on each update. */
const IDB_STORAGE_KEY = 'credentials:catalogue:current';

/** Shape persisted to IndexedDB. Thin wrapper so we can bump format later. */
interface PersistedCatalogue {
  schemaVersion: 1;
  savedAt: number;
  catalogue: CatalogueResponse;
}

// ============================================================================
// Idle-callback helper (Safari fallback)
// ============================================================================

type IdleCallback = () => void;
type RequestIdleHandle = number | ReturnType<typeof setTimeout>;

function scheduleIdle(callback: IdleCallback): RequestIdleHandle {
  if (typeof window === 'undefined') {
    return setTimeout(callback, 0);
  }
  const ric = (window as unknown as {
    requestIdleCallback?: (cb: IdleCallback, opts?: { timeout: number }) => number;
  }).requestIdleCallback;
  if (typeof ric === 'function') {
    return ric(callback, { timeout: 2000 });
  }
  return setTimeout(callback, 0);
}

function cancelIdle(handle: RequestIdleHandle | null): void {
  if (handle === null) return;
  if (typeof window === 'undefined') {
    clearTimeout(handle as ReturnType<typeof setTimeout>);
    return;
  }
  const cic = (window as unknown as {
    cancelIdleCallback?: (handle: number) => void;
  }).cancelIdleCallback;
  if (typeof cic === 'function' && typeof handle === 'number') {
    cic(handle);
    return;
  }
  clearTimeout(handle as ReturnType<typeof setTimeout>);
}

// ============================================================================
// Persistence helpers
// ============================================================================

async function readPersistedCatalogue(): Promise<CatalogueResponse | null> {
  try {
    const raw = await idbGet<PersistedCatalogue>(IDB_STORAGE_KEY);
    if (!raw || raw.schemaVersion !== 1 || !raw.catalogue) return null;
    return raw.catalogue;
  } catch (err) {
    console.warn('[credentials] failed to read IDB cache', err);
    return null;
  }
}

async function writePersistedCatalogue(catalogue: CatalogueResponse): Promise<void> {
  try {
    const payload: PersistedCatalogue = {
      schemaVersion: 1,
      savedAt: Date.now(),
      catalogue,
    };
    await idbSet(IDB_STORAGE_KEY, payload);
  } catch (err) {
    console.warn('[credentials] failed to write IDB cache', err);
  }
}

// ============================================================================
// The hook
// ============================================================================

/**
 * Return shape of `useCatalogueQuery` — the TanStack Query result plus an
 * explicit `refresh()` helper. Modelled as an intersection type because
 * `UseQueryResult` is a discriminated union that cannot be `extend`ed.
 */
export type UseCatalogueQueryResult = UseQueryResult<CatalogueResponse, Error> & {
  /** Force-reload from the server, bypassing IDB and the staleTime. */
  refresh: () => Promise<void>;
};

/**
 * Fetch the credential catalogue with warm-start persistence.
 *
 * - Reads the previous session's snapshot from IndexedDB into the query
 *   cache on first mount (zero network; <50 ms warm start).
 * - Kicks off a background revalidate via WebSocket; if the server's
 *   version hash differs, replaces the cache and schedules an
 *   idle-callback persist write.
 * - Returns the standard TanStack Query shape plus a `refresh()` helper.
 */
export function useCatalogueQuery(): UseCatalogueQueryResult {
  const queryClient = useQueryClient();
  const { sendRequest, isReady } = useWebSocket();
  const hydratedFromIdbRef = useRef(false);

  // Warm start: on first mount, hydrate the TanStack Query cache from IDB.
  // This runs synchronously (well, on first effect tick) so the UI renders
  // the cached catalogue instantly while the background fetch is in flight.
  useEffect(() => {
    if (hydratedFromIdbRef.current) return;
    hydratedFromIdbRef.current = true;

    let cancelled = false;
    void readPersistedCatalogue().then((cached) => {
      if (cancelled || !cached) return;
      const existing = queryClient.getQueryData<CatalogueResponse>(CATALOGUE_QUERY_KEY);
      if (existing) return; // server fetch already won
      queryClient.setQueryData<CatalogueResponse>(CATALOGUE_QUERY_KEY, cached);
    });

    return () => {
      cancelled = true;
    };
  }, [queryClient]);

  // Query function: WebSocket fetch with conditional since-based fetch.
  const queryFn = useCallback(async (): Promise<CatalogueResponse> => {
    const existing = queryClient.getQueryData<CatalogueResponse>(CATALOGUE_QUERY_KEY);
    const payload: Record<string, unknown> = {};
    if (existing?.version) payload.since = existing.version;

    const response = await sendRequest<WsResponse>('get_credential_catalogue', payload);

    if (isUnchanged(response)) {
      // Server returned 304-style unchanged → keep existing cache.
      if (existing) return existing;
      // Defensive fallback: server said unchanged but we have nothing — refetch.
      const full = await sendRequest<WsResponse>('get_credential_catalogue', {});
      if (isUnchanged(full)) {
        throw new Error('server returned unchanged on empty cache');
      }
      return full;
    }

    // response is CatalogueResponse here — type guard narrowed the union.
    return response;
  }, [queryClient, sendRequest]);

  const query = useQuery<CatalogueResponse, Error>({
    queryKey: CATALOGUE_QUERY_KEY,
    queryFn,
    // The catalogue changes rarely. We invalidate explicitly on the
    // `credential_catalogue_updated` WebSocket broadcast (not yet wired;
    // added in Phase A verification).
    staleTime: Infinity,
    gcTime: 10 * 60_000,
    // Only fetch once the WebSocket is actually connected. Prevents a
    // failed fetch during the initial reconnect window.
    enabled: isReady,
    // Placeholder so the first render can show the warm-start data.
    placeholderData: (prev) => prev,
  });

  // Idle persist: when the server cache updates, write it to IDB in an
  // idle callback so the 50-200 ms structured-clone write never blocks
  // first paint or user interaction.
  useEffect(() => {
    if (!query.data) return;
    const handle = scheduleIdle(() => {
      void writePersistedCatalogue(query.data as CatalogueResponse);
    });
    return () => cancelIdle(handle);
  }, [query.data]);

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: CATALOGUE_QUERY_KEY });
  }, [queryClient]);

  return useMemo(() => ({ ...query, refresh }), [query, refresh]);
}

/**
 * Single-provider "is stored?" selector derived from the catalogue.
 *
 * Replaces the retired ``apiKeyStatuses[id].hasKey`` duplication —
 * `provider.stored` on the server-driven catalogue is the canonical
 * answer (computed from `auth_service.has_valid_key` on every catalogue
 * read). Consumers re-render only when this provider's `stored` flag
 * actually flips, not on every credential mutation, because TanStack
 * Query produces a new array reference per refetch and React's
 * referential-equality short-circuits the boolean derivation.
 */
export function useProviderStored(providerId: string | null | undefined): boolean {
  const { data } = useCatalogueQuery();
  if (!providerId || !data?.providers) return false;
  return Boolean(data.providers.find((p) => p.id === providerId)?.stored);
}

/**
 * Count of providers with a stored credential (any kind — API key,
 * OAuth, paired QR). Read from the catalogue's `stored` flag.
 */
export function useStoredProviderCount(): number {
  const { data } = useCatalogueQuery();
  if (!data?.providers) return 0;
  return data.providers.filter((p) => p.stored).length;
}
