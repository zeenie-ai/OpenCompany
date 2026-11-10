/**
 * The Workspace dock's shell, the same on Home and in the editor: a panel on
 * the right whose left edge drags. At 1100px and wider it pushes the page
 * aside; narrower, it lies over the page with a modal's shadow (its parent
 * is the positioned box it lies over). It stays mounted and opens and closes
 * by transitioning its width, so a reload with it open does not animate and
 * reopening it mid-close turns it around. Its contents mount on the first
 * open and stay through a close.
 *
 * The host keeps the open state and the width (dockSize.ts has the limits)
 * and passes the width as CSS.
 */

import { Maximize2, Minimize2, X } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { usePanelResize } from '@/hooks/usePanelResize';
import { animate } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { DOCK_DEFAULT_WIDTH } from './dockSize';

export function WorkspaceDockShell({ open, width, onWidth, onResizing, children }: {
  open: boolean;
  /** Its width while open, as CSS (dockSize `dockWidth`). */
  width: string;
  /** A drag of the left edge asks for this many pixels. */
  onWidth: (px: number) => void;
  /** The edge is being dragged, for what sits beside the dock. */
  onResizing?: (resizing: boolean) => void;
  children: ReactNode;
}) {
  const asideRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  // The contents mount on the first open and stay through a close.
  const [visited, setVisited] = useState(open);
  if (open && !visited) setVisited(true);

  // Opening slides the contents in behind the widening edge; a reload with
  // the dock open does not.
  const shownOpen = useRef(open);
  useLayoutEffect(() => {
    if (shownOpen.current === open) return;
    shownOpen.current = open;
    if (!open) return;
    animate(
      bodyRef.current,
      [
        { opacity: 0, transform: 'translateX(24px)' },
        { opacity: 1, transform: 'none' },
      ],
      { duration: 'dock-in', delay: 80, easing: 'spring' },
    );
  }, [open]);

  // The handle is the left edge, so dragging left widens. A drag starts
  // from the width on screen, which is Expand's while that is on.
  const onMove = useCallback((deltaPx: number, startPx: number) => onWidth(startPx - deltaPx), [onWidth]);
  const getStartValue = useCallback(() => asideRef.current?.getBoundingClientRect().width ?? DOCK_DEFAULT_WIDTH, []);
  const resize = usePanelResize({ axis: 'x', cursor: 'ew-resize', onMove, getStartValue });
  useEffect(() => {
    onResizing?.(resize.isResizing);
  }, [resize.isResizing, onResizing]);

  return (
    <aside
      ref={asideRef}
      aria-label="Workspace"
      aria-hidden={!open}
      inert={!open}
      style={{ width: open ? width : 0 }}
      className={cn(
        'relative flex shrink-0 justify-end overflow-hidden bg-bg-panel',
        'max-[1100px]:absolute max-[1100px]:inset-y-0 max-[1100px]:right-0 max-[1100px]:z-30',
        resize.isResizing ? 'transition-none' : 'transition-[width,opacity] motion-reduce:transition-none',
        open
          ? 'border-l border-border-default opacity-100 duration-(--dur-dock-in) ease-spring max-[1100px]:shadow-modal'
          : 'opacity-0 duration-(--dur-sidebar-out) ease-(--ease-default)',
      )}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize workspace"
        title="Drag to resize"
        onMouseDown={resize.start}
        className={cn(
          // Neutral: colour marks state or role, and a handle is neither.
          'absolute inset-y-0 left-0 z-10 w-1.5 cursor-ew-resize transition-colors hover:bg-border-strong',
          resize.isResizing && 'bg-border-strong',
        )}
      />
      {visited && (
        // Laid out at the full width and clipped while the dock animates,
        // so nothing reflows. No pointer events while dragging: a web or
        // PDF item's iframe would swallow the mouse moves.
        <div
          ref={bodyRef}
          style={{ width }}
          className={cn('flex h-full shrink-0 flex-col', resize.isResizing && 'pointer-events-none')}
        >
          {children}
        </div>
      )}
    </aside>
  );
}

/** Expand and Close, at the end of the dock's row. */
export function DockButtons({ wide, onToggleWide, onClose }: { wide: boolean; onToggleWide: () => void; onClose: () => void }) {
  const wideLabel = wide ? 'Restore size' : 'Expand';
  return (
    <>
      <Button variant="quiet" size="icon-sm" onClick={onToggleWide} aria-label={wideLabel} title={wideLabel} className="size-7.5 rounded-full">
        {wide ? <Minimize2 aria-hidden className="size-3.5" strokeWidth={1.75} /> : <Maximize2 aria-hidden className="size-3.5" strokeWidth={1.75} />}
      </Button>
      <Button variant="quiet" size="icon-sm" onClick={onClose} aria-label="Close workspace" title="Close workspace" className="size-7.5 rounded-full">
        <X aria-hidden strokeWidth={1.75} />
      </Button>
    </>
  );
}
