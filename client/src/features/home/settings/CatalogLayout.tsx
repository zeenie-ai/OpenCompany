/**
 * The shared catalog page behind Settings > Skills, Connectors and Plugins
 * (design handoff "Settings: Billing, Skills, Plugins"): a title with Yours /
 * Discover, search, an optional category filter and primary action, and a
 * grid of cards.
 *
 * Every rule follows from the props and the item data, never from which
 * page is showing:
 * - the page supplies both lists, and Yours counts its own;
 * - the category filter exists when there are categories, and starts open
 *   when the page is opened on one;
 * - Discover shows DISCOVER_LIMIT cards until "Show all";
 * - a card offers remove only with `onRemove`, and a switch only with
 *   `onToggle`;
 * - a card glows when its item turns 'added' while it is on screen, then
 *   `onItemAdded` runs (never on first render).
 *
 * The view, search and filter live here, so they start fresh each time the
 * page mounts (the Settings tabs unmount when hidden).
 */

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, BadgeCheck, Check, LoaderCircle, Plus, SlidersHorizontal } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Toggle } from '@/components/ui/toggle';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { animate } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { AppMark, SearchField } from '../ui/primitives';
import { DISCOVER_LIMIT, catalogSlice, type CatalogItem, type CatalogView } from './catalog';
import { staggerSettings } from './stagger';

export interface CatalogLayoutProps {
  title: string;
  /** Also the search box's accessible name. */
  searchPlaceholder: string;
  yours: { items: CatalogItem[]; sectionTitle: string; empty: { title: string; detail: string } };
  discover: { items: CatalogItem[]; sectionTitle: string };
  loading?: boolean;
  categories?: { key: string; label: string }[];
  initialCategory?: string;
  verbs: { add: string; remove?: string; busy?: string };
  onAdd: (item: CatalogItem) => void;
  onRemove?: (item: CatalogItem) => void;
  onToggle?: (item: CatalogItem, on: boolean) => void;
  onItemAdded?: (item: CatalogItem) => void;
  /** A header button that opens an inline form above the list. */
  primaryAction?: { label: string; form: (close: () => void) => ReactNode };
}

const GRID = '-mt-2 grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3';

export function CatalogLayout({
  title,
  searchPlaceholder,
  yours,
  discover,
  loading = false,
  categories = [],
  initialCategory = 'all',
  verbs,
  onAdd,
  onRemove,
  onToggle,
  onItemAdded,
  primaryAction,
}: CatalogLayoutProps) {
  const [view, setView] = useState<CatalogView>('discover');
  const [category, setCategory] = useState(initialCategory);
  const [filterOpen, setFilterOpen] = useState(initialCategory !== 'all');
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const shownKey = useRef(`${view}|${category}`);

  // The blocks rise in again on a new view or category. The first render is
  // the dialog's to stagger.
  useLayoutEffect(() => {
    const key = `${view}|${category}`;
    if (shownKey.current === key) return;
    shownKey.current = key;
    staggerSettings(rootRef.current);
  }, [view, category]);

  const list = view === 'yours' ? yours.items : discover.items;
  const { shown, total } = catalogSlice(list, { category, query, cap: view === 'discover' && !showAll ? DISCOVER_LIMIT : null });
  const canShowAll = view === 'discover' && total > DISCOVER_LIMIT;
  const q = query.trim();

  const changeView = (next: CatalogView) => {
    setView(next);
    setShowAll(false);
  };
  const toggleFilter = (open: boolean) => {
    setFilterOpen(open);
    if (!open) setCategory('all');
  };

  const filtered = q !== '' || category !== 'all';
  const empty = filtered
    ? {
        title: q ? `Nothing matches “${q}”` : 'Nothing in this category',
        detail: q ? 'Try another word.' : 'Try another category.',
        discover: false,
      }
    : view === 'yours'
      ? { ...yours.empty, discover: true }
      : { title: 'Nothing here yet', detail: 'Check back soon.', discover: false };

  return (
    <div ref={rootRef} className="flex flex-col gap-5.5 px-8 pt-6.5 pb-8">
      <div data-stagger className="flex flex-wrap items-center gap-3.5 pr-10">
        <h2 className="text-xl font-semibold tracking-[-0.02em] text-fg-default">{title}</h2>
        <ToggleGroup
          type="single"
          variant="segmented"
          aria-label={`${title} to show`}
          value={view}
          onValueChange={(next) => next && changeView(next as CatalogView)}
        >
          <ToggleGroupItem value="yours" className="px-3 text-sm">
            Yours
            <span className="font-mono text-2xs font-normal text-fg-faint">{yours.items.length}</span>
          </ToggleGroupItem>
          <ToggleGroupItem value="discover" className="px-3 text-sm">
            Discover
          </ToggleGroupItem>
        </ToggleGroup>
        <div className="ml-auto flex flex-[1_1_280px] items-center justify-end gap-2">
          <SearchField value={query} onChange={setQuery} placeholder={searchPlaceholder} className="max-w-75 flex-1" />
          {categories.length > 0 && (
            <Toggle
              variant="chips"
              pressed={filterOpen}
              onPressedChange={toggleFilter}
              aria-label="Filter by category"
              title="Filter"
              className="size-9 rounded-row px-0 [&_svg:not([class*='size-'])]:size-4"
            >
              <SlidersHorizontal strokeWidth={1.75} />
            </Toggle>
          )}
          {primaryAction && (
            <Button
              variant="invert"
              size="lg"
              aria-expanded={formOpen}
              onClick={() => setFormOpen((open) => !open)}
              className="rounded-row pr-3.5 pl-3 font-semibold active:scale-97"
            >
              <Plus strokeWidth={2.2} />
              {primaryAction.label}
            </Button>
          )}
        </div>
      </div>

      {categories.length > 0 && filterOpen && (
        <ToggleGroup
          data-stagger
          type="single"
          variant="chips"
          size="lg"
          className="-mt-2 gap-2"
          aria-label="Category"
          value={category}
          onValueChange={(next) => next && setCategory(next)}
        >
          <ToggleGroupItem value="all">All</ToggleGroupItem>
          {categories.map((c) => (
            <ToggleGroupItem key={c.key} value={c.key}>
              {c.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      )}

      {primaryAction && formOpen && (
        <div data-stagger className="flex flex-col gap-2.5 rounded-card border border-border-strong bg-bg-elevated p-4">
          {primaryAction.form(() => setFormOpen(false))}
        </div>
      )}

      <div data-stagger className="flex items-center gap-2.5">
        <span className="text-lead font-semibold whitespace-nowrap text-fg-default">
          {view === 'yours' ? yours.sectionTitle : discover.sectionTitle}
        </span>
        <Badge variant="outline" className="rounded-md border-border-default bg-bg-app px-1.75 font-mono text-2xs text-fg-muted">
          {total}
        </Badge>
        {canShowAll && (
          <Button variant="quiet" size="sm" onClick={() => setShowAll((all) => !all)} className="ml-auto text-row text-fg-default">
            {showAll ? 'Show less' : 'Show all'}
            <ArrowRight />
          </Button>
        )}
      </div>

      {loading && list.length === 0 ? (
        <div className={GRID}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-30 rounded-card" />
          ))}
        </div>
      ) : total > 0 ? (
        <div className={GRID}>
          {shown.map((item) => (
            <CatalogCard
              key={item.id}
              item={item}
              view={view}
              verbs={verbs}
              onAdd={onAdd}
              onRemove={onRemove}
              onToggle={onToggle}
              onItemAdded={onItemAdded}
            />
          ))}
        </div>
      ) : (
        <div
          data-stagger
          className="flex flex-col items-center gap-2.5 rounded-panel border border-dashed border-border-strong px-5 py-10 text-center"
        >
          <span className="text-base font-medium text-fg-default">{empty.title}</span>
          <span className="text-sm text-fg-muted">{empty.detail}</span>
          {empty.discover && (
            <Button
              variant="quiet"
              size="sm"
              onClick={() => changeView('discover')}
              className="mt-1 h-8 border-border-strong px-3.5 font-semibold text-fg-default"
            >
              Discover
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function CatalogCard({
  item,
  view,
  verbs,
  onAdd,
  onRemove,
  onToggle,
  onItemAdded,
}: {
  item: CatalogItem;
  view: CatalogView;
  verbs: CatalogLayoutProps['verbs'];
  onAdd: CatalogLayoutProps['onAdd'];
  onRemove?: CatalogLayoutProps['onRemove'];
  onToggle?: CatalogLayoutProps['onToggle'];
  onItemAdded?: CatalogLayoutProps['onItemAdded'];
}) {
  const ref = useRef<HTMLDivElement>(null);
  const lastState = useRef(item.state);
  const added = item.state === 'added';

  useEffect(() => {
    const before = lastState.current;
    lastState.current = item.state;
    if (item.state !== 'added' || before === 'added') return;
    animate(ref.current, [{ boxShadow: 'var(--glow-connect)' }, { boxShadow: '0 0 0 0 transparent' }], {
      duration: 1200,
      easing: 'default',
      fill: 'none',
    });
    onItemAdded?.(item);
  }, [item, onItemAdded]);

  return (
    <div
      ref={ref}
      data-catalog-item={item.id}
      data-stagger
      className={cn(
        'flex gap-3.5 rounded-card border bg-bg-elevated p-4.5 transition-[border-color,translate,box-shadow] duration-(--dur-default) ease-(--ease-default) hover:-translate-y-0.5 hover:border-border-strong hover:shadow-popover motion-reduce:hover:translate-y-0',
        added ? 'border-status-working-border' : 'border-border-default',
      )}
    >
      <AppMark name={item.name} iconRef={item.tile.iconRef} tone={item.tile.tone} size="xl" />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-lead font-semibold text-fg-default">{item.name}</span>
          {item.verified && (
            <BadgeCheck role="img" aria-label="Verified" strokeWidth={1.75} className="size-3.75 shrink-0 text-fg-faint" />
          )}
        </div>
        {item.description && <p className="line-clamp-2 text-row leading-snug text-fg-muted">{item.description}</p>}
        {item.byline && <span className="mt-0.5 text-sm text-fg-faint">{item.byline}</span>}
        {item.meta && <span className="mt-0.5 font-mono text-2xs text-fg-faint">{item.meta}</span>}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        <CardAction item={item} view={view} verbs={verbs} onAdd={onAdd} onRemove={onRemove} onToggle={onToggle} />
      </div>
    </div>
  );
}

const DONE = 'size-8.5 rounded-lg border border-action-run-border bg-action-run-soft text-action-run-ink';

function CardAction({
  item,
  view,
  verbs,
  onAdd,
  onRemove,
  onToggle,
}: {
  item: CatalogItem;
  view: CatalogView;
  verbs: CatalogLayoutProps['verbs'];
  onAdd: CatalogLayoutProps['onAdd'];
  onRemove?: CatalogLayoutProps['onRemove'];
  onToggle?: CatalogLayoutProps['onToggle'];
}) {
  const removeVerb = verbs.remove ?? 'Remove';
  if (item.state === 'busy') {
    return (
      <span
        role="status"
        aria-label={`${item.name}: ${verbs.busy ?? 'Working…'}`}
        className="grid size-8.5 place-items-center rounded-lg border border-status-ready-border bg-status-ready-fill"
      >
        <LoaderCircle aria-hidden className="size-3.5 animate-spin text-status-ready-ink" />
      </span>
    );
  }
  if (view === 'discover') {
    if (item.state === 'available') {
      return (
        <Button
          variant="quiet"
          size="icon"
          aria-label={`${verbs.add} ${item.name}`}
          title={`${verbs.add} ${item.name}`}
          onClick={() => onAdd(item)}
          className="size-8.5 border-border-strong bg-bg-app text-fg-default hover:border-action-run-border hover:bg-action-run-soft hover:text-action-run-ink active:scale-92"
        >
          <Plus />
        </Button>
      );
    }
    return onRemove ? (
      <Button
        variant="quiet"
        size="icon"
        aria-label={`${removeVerb} ${item.name}`}
        title={`${removeVerb} ${item.name}`}
        onClick={() => onRemove(item)}
        className={cn(DONE, 'hover:border-action-stop-border hover:bg-action-stop-soft hover:text-action-stop-ink')}
      >
        <Check strokeWidth={2.2} />
      </Button>
    ) : (
      <span role="img" aria-label={`${item.name} added`} className={cn('grid place-items-center', DONE)}>
        <Check aria-hidden strokeWidth={2.2} className="size-4" />
      </span>
    );
  }
  return (
    <>
      {onToggle && (
        <Switch size="md" tone="run" checked={item.enabled ?? false} onCheckedChange={(on) => onToggle(item, on)} aria-label={item.name} />
      )}
      {onRemove && (
        <Button
          variant="quiet"
          size="xs"
          aria-label={`${removeVerb} ${item.name}`}
          onClick={() => onRemove(item)}
          className="mt-auto h-6.5 px-2 text-xs text-fg-faint hover:bg-action-stop-soft hover:text-action-stop-ink"
        >
          {removeVerb}
        </Button>
      )}
    </>
  );
}

export default CatalogLayout;
