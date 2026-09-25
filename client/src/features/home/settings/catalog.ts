/**
 * The shared catalog behind Settings > Skills, Connectors and Plugins
 * (design handoff "Settings: Billing, Skills, Plugins"): what one card
 * shows, and how a list is narrowed for display. Each page maps its own
 * records to `CatalogItem`s; nothing here knows which page it is on.
 */

import type { ColorRole } from '../data/schemas';

export type CatalogView = 'yours' | 'discover';

export interface CatalogItem {
  id: string;
  name: string;
  description?: string;
  /** "by Google", "by you". Searched along with the name and description. */
  byline?: string;
  verified?: boolean;
  /** A category key, for the filter chips. */
  category?: string;
  /** One short mono line under the byline. */
  meta?: string;
  /** A brand icon, or tinted initials when there is none. */
  tile: { iconRef?: string | null; tone?: ColorRole };
  state: 'available' | 'busy' | 'added';
  /** The switch in Yours, shown only when the page can toggle items. */
  enabled?: boolean;
}

/** Discover shows this many cards until "Show all". */
export const DISCOVER_LIMIT = 8;

/** Items in `category` ('all' for every one) matching `query`, the first
 *  `cap` of them when a cap is given. `total` counts every match. */
export function catalogSlice(
  items: CatalogItem[],
  { category, query, cap }: { category: string; query: string; cap: number | null },
): { shown: CatalogItem[]; total: number } {
  const q = query.trim().toLowerCase();
  const matched = items.filter((item) => {
    if (category !== 'all' && item.category !== category) return false;
    if (!q) return true;
    return [item.name, item.description, item.byline].some((text) => text?.toLowerCase().includes(q));
  });
  return { shown: cap === null ? matched : matched.slice(0, cap), total: matched.length };
}
