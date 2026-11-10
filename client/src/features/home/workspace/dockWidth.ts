/**
 * The Workspace dock's width on Home, as CSS, for the dock itself and for
 * what sits at its left edge (the Get started card).
 *
 * While the dock pushes the page aside it leaves the page room for its
 * header, so the header's buttons never run under the dock: 560px beside
 * the open sidebar, and 760px with the sidebar closed, when the header also
 * holds the sidebar's button and the logo. The header drops its buttons'
 * words as the page narrows (header/HomeHeader), which is what fits there.
 */

import { dockWidth, useDockPushes } from '@/components/workspace/dockSize';
import { useHomeStore } from '../state/homeStore';

const PAGE_KEEPS = {
  sidebarOpen: 'calc(var(--w-home-sidebar) + 560px)',
  sidebarClosed: '760px',
} as const;

export function useHomeDockWidth(): string {
  const widthPx = useHomeStore((s) => s.workspaceWidth);
  const wide = useHomeStore((s) => s.workspaceWide);
  const sidebarOpen = useHomeStore((s) => s.sidebarOpen);
  const pushes = useDockPushes();
  return dockWidth(widthPx, wide, pushes ? PAGE_KEEPS[sidebarOpen ? 'sidebarOpen' : 'sidebarClosed'] : undefined);
}
