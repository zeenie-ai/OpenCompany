/**
 * Settings blocks rise in, 30ms apart (design handoff "Settings modal"):
 * every `[data-stagger]` under `root`. HomeSettings plays it when the dialog
 * opens or the tab changes; a catalog page plays it again when its view or
 * category changes.
 */

import { stagger } from '@/lib/motion';

export function staggerSettings(root: ParentNode | null | undefined): void {
  if (!root) return;
  stagger(
    root.querySelectorAll('[data-stagger]'),
    [
      { opacity: 0, transform: 'translateY(10px)' },
      { opacity: 1, transform: 'none' },
    ],
    { base: 40, step: 30, cap: 14, duration: 420, easing: 'spring' },
  );
}
