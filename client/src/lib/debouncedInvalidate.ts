/**
 * Coalesce bursts of "this query is stale" signals into one refetch.
 *
 * Broadcast handlers often fire in the same tick (a reconnect replays
 * several; an OAuth completes and the status follows). Each call restarts a
 * trailing-edge timer, so the freshest state wins and the query refetches
 * once per quiet window. One timer per invalidator, module scope.
 */

import type { QueryClient, QueryKey } from '@tanstack/react-query';

export function makeDebouncedInvalidator(queryKey: QueryKey, delayMs: number) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return (queryClient: QueryClient): void => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      void queryClient.invalidateQueries({ queryKey });
    }, delayMs);
  };
}
