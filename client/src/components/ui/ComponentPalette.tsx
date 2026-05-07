import React from 'react';
import { useAppTheme } from '../../hooks/useAppTheme';
import { useNodeAllowlist } from '../../hooks/useNodeAllowlist';
import { ComponentPaletteProps } from '../../types/ComponentTypes';
import { INodeTypeDescription } from '../../types/INodeProperties';
import ComponentItem from './ComponentItem';
import CollapsibleSection from './CollapsibleSection';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Search } from 'lucide-react';
import { NodeIcon } from '../../assets/icons';
import { useNodeGroups, listCachedNodeSpecs, NodeGroupEntry } from '../../lib/nodeSpec';
import { nodeSpecToDescription } from '../../adapters/nodeSpecToDescription';

// Wave 10.B: palette section metadata (icon / label / color / visibility)
// is fetched from the backend GET /api/schemas/nodes/groups endpoint.
// Frontend retains zero per-category tables.

const ComponentPalette: React.FC<ComponentPaletteProps> = ({
  searchQuery,
  onSearchChange,
  collapsedSections,
  onToggleSection,
  onDragStart,
  proMode = false,  // Default to simple mode
  specsReady = false,
}) => {
  const theme = useAppTheme();
  const { isVisible } = useNodeAllowlist();

  // Backend-driven group metadata via the shared WS-in-queryFn hook.
  // `useNodeGroups` returns the full query result so we can render a
  // loading skeleton on cold first-paint instead of masking the filter
  // or flashing an empty palette.
  const { data: groupIndex, isPending: groupsLoading } = useNodeGroups();

  const getCategoryConfig = React.useCallback((category: string) => {
    const entry = groupIndex?.[category.toLowerCase()] as NodeGroupEntry | undefined;
    // No icon/color fallback: if the backend doesn't declare the
    // group via register_group(), the empty string surfaces the gap.
    return {
      icon: entry?.icon ?? '',
      color: entry?.color || theme.colors.textSecondary,
      label: entry?.label || category,
    };
  }, [groupIndex, theme.colors.textSecondary]);

  const categorizedComponents = React.useMemo(() => {
    const categories: Record<string, INodeTypeDescription[]> = {};

    // Cached NodeSpecs adapted to the INodeTypeDescription shape.
    // Re-reads every time groupIndex changes, so the palette fills in
    // once prefetchAllNodeSpecs resolves and the WS response lands.
    const definitions = listCachedNodeSpecs().map(nodeSpecToDescription);

    const filteredDefinitions = definitions.filter((definition) => {
      // Filter by backend allowlist (server/config/node_allowlist.json).
      // Applied only in normal mode; dev mode shows every node.
      if (!proMode && !isVisible(definition.name)) return false;

      // Filter by search query
      if (searchQuery.trim()) {
        try {
          const query = searchQuery.toLowerCase();
          const matchesQuery = (
            (definition.displayName || '').toLowerCase().includes(query) ||
            (definition.description || '').toLowerCase().includes(query) ||
            (definition.group?.[0] || '').toLowerCase().includes(query)
          );
          if (!matchesQuery) return false;
        } catch (error) {
          return false;
        }
      }

      // Wave 10.B: simple-mode visibility comes from backend
      // GroupMetadata.visibility ('normal' shown, 'dev' hidden in simple
      // mode). No frontend SIMPLE_MODE_CATEGORIES table.
      if (!proMode) {
        const firstGroup = (definition.group?.[0] || '').toLowerCase();
        const groupVisibility = groupIndex?.[firstGroup]?.visibility;
        if (groupVisibility !== 'normal' && groupVisibility !== 'all') {
          return false;
        }
      }

      return true;
    });

    filteredDefinitions.forEach((definition) => {
      try {
        const categoryKey = (definition.group?.[0] || 'Uncategorized').toLowerCase();
        if (!categories[categoryKey]) categories[categoryKey] = [];
        categories[categoryKey].push(definition);
      } catch (error) {
        // Skip invalid definitions
      }
    });

    return categories;
  }, [searchQuery, proMode, groupIndex, isVisible, specsReady]);

  const totalComponents = Object.values(categorizedComponents).reduce(
    (acc, components) => acc + components.length, 
    0
  );

  return (
    // Palette shell: bg-bg-panel mirrors the sidebar (`.palette` token).
    <div className="flex h-full w-full flex-col overflow-hidden border-l border-border-default bg-bg-panel">
      {/* Header Section — bg-bg-app drops one elevation step. */}
      <div className="border-b border-border-default bg-bg-app p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-display text-base font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
            Components
          </h2>
          <Badge variant="secondary" className="font-mono text-xs font-medium">
            {totalComponents}
          </Badge>
        </div>

        {/* Search Input */}
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-faint" />
          <Input
            type="text"
            placeholder="Search..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="pl-9"
          />
        </div>
      </div>

      {/* Categories */}
      <div className="flex-1 overflow-y-auto p-3">
        {groupsLoading ? (
          <PaletteSkeleton />
        ) : Object.keys(categorizedComponents).length === 0 ? (
          <div className="flex flex-col items-center px-6 py-12 text-center text-fg-muted">
            <Search className="mb-3 h-12 w-12 opacity-50" />
            <p className="text-sm">
              No components found matching "{searchQuery}"
            </p>
          </div>
        ) : (
          Object.entries(categorizedComponents).map(([category, components]) => {
            try {
              const isCollapsed = collapsedSections[category];
              const config = getCategoryConfig(category);

              return (
                <div key={category || 'unknown'} className="mb-3">
                  <CollapsibleSection
                    title={
                      <div className="flex w-full items-center justify-between">
                        <div className="flex items-center gap-2.5">
                          <span
                            className="flex h-7 w-7 items-center justify-center rounded-md bg-tint-soft"
                            // currentColor is the category's brand color;
                            // `bg-tint-soft` mixes it against transparent
                            // at the canonical alpha (--tint-soft). The
                            // icon picks up the same color via NodeIcon.
                            style={{ color: config.color }}
                          >
                            <NodeIcon
                              icon={config.icon}
                              className="h-4 w-4 text-base"
                              fallback={<span>📦</span>}
                            />
                          </span>
                          <span className="font-display text-sm font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
                            {config.label}
                          </span>
                        </div>
                        <Badge variant="secondary" className="text-xs font-medium">
                          {components?.length || 0}
                        </Badge>
                      </div>
                    }
                    isCollapsed={isCollapsed}
                    onToggle={() => onToggleSection(category)}
                  >
                    <div className="grid gap-2 pt-2">
                      {(components || []).map((definition, idx) => {
                        try {
                          return (
                            <ComponentItem
                              key={definition?.name || `item-${idx}`}
                              definition={definition}
                              onDragStart={onDragStart}
                            />
                          );
                        } catch (error) {
                          return null;
                        }
                      })}
                    </div>
                  </CollapsibleSection>
                </div>
              );
            } catch (error) {
              return null;
            }
          })
        )}
      </div>
    </div>
  );
};

/**
 * Loading placeholder for the palette's categories area. Mirrors the
 * shape of three collapsed category sections + a handful of items so
 * the layout does not jump when real data arrives.
 */
const PALETTE_SKELETON_CATEGORIES = 3;
const PALETTE_SKELETON_ITEMS_PER_CATEGORY = 2;

const PaletteSkeleton: React.FC = () => (
  <div aria-busy="true" aria-label="Loading components">
    {Array.from({ length: PALETTE_SKELETON_CATEGORIES }).map((_, categoryIdx) => (
      <div key={categoryIdx} className="mb-3 space-y-2">
        <div className="flex items-center gap-2.5 px-1 py-2">
          <Skeleton className="h-7 w-7 rounded-md" />
          <Skeleton className="h-4 w-24" />
        </div>
        <div className="grid gap-2 pt-2">
          {Array.from({ length: PALETTE_SKELETON_ITEMS_PER_CATEGORY }).map((_, itemIdx) => (
            <Skeleton key={itemIdx} className="h-10 w-full rounded-md" />
          ))}
        </div>
      </div>
    ))}
  </div>
);

export default ComponentPalette;