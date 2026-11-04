/**
 * A row of a Home dialog's left nav (Settings, the Welcome guide). Used on a
 * Radix Tabs trigger, so the open page reads from `data-state`; a step the
 * guide cannot reach yet is a disabled trigger.
 */
export const NAV_ITEM =
  'flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-row font-medium text-fg-muted outline-none transition-colors hover:bg-bg-hover hover:text-fg-default focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none data-[state=active]:bg-bg-hover data-[state=active]:text-fg-default';
