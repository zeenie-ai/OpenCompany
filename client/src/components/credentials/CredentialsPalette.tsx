/**
 * CredentialsPalette — virtualized, searchable provider picker.
 *
 * Drop-in replacement for the antd `Menu` sidebar in `CredentialsModal`.
 * Scales from 20 → 5000 providers at 60 fps with < 200 ms INP during
 * rapid typing, per the runtime/memory targets in
 * `docs-internal/credentials_scaling/architecture.md`.
 *
 * Stack:
 *   - `cmdk` for the Command shell + keyboard navigation + a11y.
 *   - `fuzzysort` for pre-indexed fuzzy search (~1–2 ms per query on
 *     5000 entries).
 *   - `react-virtuoso` `GroupedVirtuoso` for a DOM pool of ~10–50 nodes
 *     regardless of item count, with sticky category headers.
 *   - `startTransition` wrapping the filter update so the input stays
 *     synchronous and typing never stalls (measured ~30–70 ms shorter
 *     long task vs `useDeferredValue`).
 *
 * Store-shape rule: all derived data (prepared index, byId map,
 * filtered result, groupCounts) lives in `useMemo` inside this
 * component. Nothing touches Zustand. The store only holds UI state
 * (`selectedId`, `query`), which is how we avoid the #1 runtime/memory
 * trap (selector closures retaining the whole catalogue).
 */

import React, {
  memo,
  startTransition,
  useCallback,
  useDeferredValue,
  useMemo,
  useState,
} from 'react';
import { Command } from 'cmdk';
import { GroupedVirtuoso } from 'react-virtuoso';
import fuzzysort from 'fuzzysort';
import { Search, X } from 'lucide-react';

import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { NodeIcon } from '../../assets/icons';
import { useWebSocket } from '../../contexts/WebSocketContext';
import type { ProviderConfig, CategoryGroup } from './types';

// ============================================================================
// Props
// ============================================================================

export interface CredentialsPaletteProps {
  providers: ProviderConfig[];
  categories: CategoryGroup[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** Height of the scrollable list area. */
  height?: number | string;
  /** Optional placeholder for the search input. */
  placeholder?: string;
}

// ============================================================================
// Pre-indexed search
// ============================================================================

interface PreparedEntry {
  provider: ProviderConfig;
  _name: Fuzzysort.Prepared;
  _category: Fuzzysort.Prepared;
}

function buildPrepared(providers: ProviderConfig[]): PreparedEntry[] {
  return providers.map((p) => ({
    provider: p,
    _name: fuzzysort.prepare(p.name),
    _category: fuzzysort.prepare(p.categoryLabel),
  }));
}

function filterProviders(query: string, prepared: PreparedEntry[]): ProviderConfig[] {
  if (!query.trim()) return prepared.map((e) => e.provider);
  const results = fuzzysort.go(query, prepared, {
    keys: ['_name', '_category'],
    threshold: -10_000,
    limit: 500,
  });
  return results.map((r) => r.obj.provider);
}

// ============================================================================
// Grouping (derived; never stored)
// ============================================================================

interface GroupedView {
  groups: CategoryGroup[];
  groupCounts: number[];
  flatItems: ProviderConfig[];
}

function groupFiltered(filtered: ProviderConfig[], categoryOrder: CategoryGroup[]): GroupedView {
  // Preserve the canonical category order from the server payload; for
  // each category, emit only those filtered providers that belong to it.
  const byCat = new Map<string, ProviderConfig[]>();
  for (const p of filtered) {
    const arr = byCat.get(p.category);
    if (arr) arr.push(p);
    else byCat.set(p.category, [p]);
  }

  const groups: CategoryGroup[] = [];
  const groupCounts: number[] = [];
  const flatItems: ProviderConfig[] = [];

  for (const cat of categoryOrder) {
    const items = byCat.get(cat.key);
    if (!items || items.length === 0) continue;
    groups.push({ key: cat.key, label: cat.label, items });
    groupCounts.push(items.length);
    flatItems.push(...items);
  }

  // Any provider whose category isn't in `categoryOrder` (server may
  // send a new category mid-session) still needs to render — bucket them
  // under a synthetic "Other" group at the end.
  const known = new Set(categoryOrder.map((c) => c.key));
  const orphans: ProviderConfig[] = [];
  for (const [catKey, items] of byCat.entries()) {
    if (!known.has(catKey)) orphans.push(...items);
  }
  if (orphans.length > 0) {
    groups.push({ key: '_other', label: 'Other', items: orphans });
    groupCounts.push(orphans.length);
    flatItems.push(...orphans);
  }

  return { groups, groupCounts, flatItems };
}

// ============================================================================
// Status indicator — three states: configured + validated, configured but
// validation failed, not configured. "Validated" is meaningful only for
// providers that go through `validate_api_key` (AI providers + the special
// google_maps / apify validators); for OAuth + QR-pairing flows the
// validation cache is empty and a stored credential renders as success.
// ============================================================================

type CredStatus = 'unconfigured' | 'stored' | 'failed';

const STATUS_CLASSES: Record<CredStatus, string> = {
  unconfigured: 'bg-muted-foreground/30',
  stored: 'bg-success',
  failed: 'bg-destructive',
};

const STATUS_LABEL: Record<CredStatus, string> = {
  unconfigured: 'Not configured',
  stored: 'Configured',
  failed: 'Validation failed',
};

interface ProviderStatusDotProps {
  providerId: string;
  stored: boolean;
}

/** Memo'd indicator. Reads `apiKeyStatuses[id]` only when it exists, so
 *  providers without a validation flow (OAuth, QR pairing) don't pay
 *  for the lookup, and any future state (failed validation, etc.)
 *  renders without touching the row's click / selection logic. */
const ProviderStatusDot = memo<ProviderStatusDotProps>(function ProviderStatusDot({
  providerId,
  stored,
}) {
  const { apiKeyStatuses } = useWebSocket();
  const validation = apiKeyStatuses[providerId];

  // Treat the broadcast that fires on delete (`message === 'deleted'`)
  // as unconfigured for the brief window before the catalogue refetch
  // lands and flips `stored` to false. Without this the dot flashes red
  // for ~300 ms (the invalidateCatalogue debounce) every time a key is
  // removed, even though the user's intent was deletion, not failure.
  const justDeleted = validation?.message === 'deleted';

  let status: CredStatus;
  let title = STATUS_LABEL.unconfigured;
  if (!stored || justDeleted) {
    status = 'unconfigured';
  } else if (validation && validation.valid === false) {
    status = 'failed';
    title = validation.message
      ? `${STATUS_LABEL.failed}: ${validation.message}`
      : STATUS_LABEL.failed;
  } else {
    status = 'stored';
    title = STATUS_LABEL.stored;
  }

  return (
    <span
      aria-label={STATUS_LABEL[status]}
      title={title}
      className={cn(
        'h-2 w-2 shrink-0 rounded-full transition-colors',
        STATUS_CLASSES[status],
      )}
    />
  );
});

// ============================================================================
// Row renderer — memoized to stop upstream re-renders from cascading
// ============================================================================

interface RowProps {
  provider: ProviderConfig;
  selected: boolean;
  onSelect: (id: string) => void;
}

const ProviderRow = memo<RowProps>(function ProviderRow({ provider, selected, onSelect }) {
  const handleClick = useCallback(() => onSelect(provider.id), [onSelect, provider.id]);

  return (
    <Command.Item
      value={provider.id}
      onSelect={handleClick}
      className={cn(
        'opencompany-palette-row flex cursor-pointer items-center gap-3 rounded-sm border px-3 py-2 text-sm text-foreground',
        selected
          ? 'border-node-agent-border bg-node-agent-soft'
          : 'border-transparent bg-transparent'
      )}
    >
      <NodeIcon
        icon={provider.iconRef}
        className="h-3.5 w-3.5 shrink-0 text-sm"
      />
      <span className="flex-1 truncate">{provider.name}</span>
      <ProviderStatusDot providerId={provider.id} stored={!!provider.stored} />
    </Command.Item>
  );
});

// ============================================================================
// Group header renderer
// ============================================================================

interface HeaderProps {
  label: string;
  count: number;
}

const GroupHeader = memo<HeaderProps>(function GroupHeader({ label, count }) {
  return (
    <div className="sticky top-0 z-[1] flex justify-between border-b border-border bg-background px-3 py-1 text-xs font-semibold tracking-wider text-muted-foreground uppercase">
      <span>{label}</span>
      <span className="text-muted-foreground/60">{count}</span>
    </div>
  );
});

// ============================================================================
// The palette component
// ============================================================================

const CredentialsPalette: React.FC<CredentialsPaletteProps> = ({
  providers,
  categories,
  selectedId,
  onSelect,
  height = '100%',
  placeholder = 'Search providers…',
}) => {
  // Pre-indexed fuzzysort entries — rebuilt only when `providers` reference changes.
  const prepared = useMemo(() => buildPrepared(providers), [providers]);

  // Input value is synchronous; filter is deferred via startTransition.
  const [query, setQuery] = useState('');
  const [filtered, setFiltered] = useState<ProviderConfig[]>(() => providers);

  // If the providers array changes (e.g. after a server revalidate
  // replaces the TanStack Query cache), reset the filtered set.
  const prevProvidersRef = React.useRef(providers);
  if (prevProvidersRef.current !== providers) {
    prevProvidersRef.current = providers;
    // Synchronous update here is fine: happens at most once per catalogue change.
    setFiltered(filterProviders(query, prepared));
  }

  const handleQueryChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = e.target.value;
      setQuery(v);
      startTransition(() => {
        setFiltered(filterProviders(v, prepared));
      });
    },
    [prepared],
  );

  // Deferred query for group derivation (avoid recomputing on every keystroke
  // if React batches filter updates slower than the input).
  const deferredFiltered = useDeferredValue(filtered);

  const grouped = useMemo<GroupedView>(
    () => groupFiltered(deferredFiltered, categories),
    [deferredFiltered, categories],
  );

  return (
    <Command
      // cmdk disables built-in filtering so we can use fuzzysort instead.
      shouldFilter={false}
      label="Credential providers"
      className="flex h-full w-full flex-col"
    >
      <div className="border-b border-border p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={handleQueryChange}
            placeholder={placeholder}
            className="h-9 pl-8 pr-8"
            autoFocus
          />
          {query && (
            <button
              type="button"
              onClick={() => {
                setQuery('');
                startTransition(() => setFiltered(filterProviders('', prepared)));
              }}
              className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              aria-label="Clear search"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
      <div className="min-h-0 flex-1">
        {grouped.flatItems.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground opacity-70">
            No providers match &ldquo;{query}&rdquo;
          </div>
        ) : (
          <GroupedVirtuoso
            style={{ height }}
            groupCounts={grouped.groupCounts}
            groupContent={(idx) => (
              <GroupHeader
                label={grouped.groups[idx].label}
                count={grouped.groupCounts[idx]}
              />
            )}
            itemContent={(idx) => {
              const p = grouped.flatItems[idx];
              if (!p) return null;
              return (
                <ProviderRow
                  provider={p}
                  selected={p.id === selectedId}
                  onSelect={onSelect}
                />
              );
            }}
          />
        )}
      </div>
    </Command>
  );
};

export default memo(CredentialsPalette);
