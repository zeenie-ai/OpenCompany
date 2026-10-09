/**
 * Settings blocks rise in, 30ms apart (design handoff "Settings modal"):
 * every `[data-stagger]` under `root`. HomeSettings plays it when the dialog
 * opens or the tab changes; a catalog page plays it again when its view or
 * category changes. A page picked in the nav also slides in first.
 */

import { animate, stagger } from '@/lib/motion';

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

/** A page the owner picks in the settings nav slides in from 14px to the
 *  right (design handoff "Settings modal"); its blocks then stagger in. */
export function slideSettingsPage(root: Element | null | undefined): void {
  animate(
    root,
    [
      { opacity: 0, transform: 'translateX(14px)' },
      { opacity: 1, transform: 'none' },
    ],
    { duration: 'slow' },
  );
}
