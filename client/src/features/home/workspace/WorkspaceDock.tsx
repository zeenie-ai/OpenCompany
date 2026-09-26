/**
 * The Workspace (design handoff "Workspace panel"): a dock on the right of
 * Home showing what one employee is working on. Canvas shows the
 * employee's board; Browser attaches to their saved browser nodes. The
 * editor shares the same tabs and live browser surface.
 *
 * At 1100px and wider the dock pushes the page aside; narrower, it lies
 * over the page with a shadow. The left edge drags from 360px to the
 * window less 420px, and Expand gives a bigger dock without a drag. Like
 * the sidebar it stays mounted and opens and closes by transitioning its
 * width, so a reload with it open does not animate and reopening it
 * mid-close turns it around. Its contents mount on the first open. Open,
 * width and tab persist (state/homeStore).
 */

import type { LucideIcon } from 'lucide-react';
import { Code, Maximize2, Minimize2, Monitor, PanelsTopLeft, X } from 'lucide-react';
import BrowserWorkspace from '@/components/browser/BrowserWorkspace';
import { WorkspaceTabs } from '@/components/workspace/WorkspaceTabs';
import { Suspense, lazy, useCallback, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { usePanelResize } from '@/hooks/usePanelResize';
import { animate } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { enterDev } from '../../../app/useShellActions';
import { useEmployeesQuery } from '../data/employees';
import { useLiveTask } from '../data/liveTask';
import { presentEmployee } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { useHomeStore } from '../state/homeStore';
import { Avatar, StatusPill } from '../ui/primitives';

// Its own chunk: the board's viewers (markdown, code, JSON) stay out of Home's.
const WorkspaceCanvas = lazy(() => import('./WorkspaceCanvas'));

/** The dragged width, or Expand's, always leaving the window some room. */
function dockWidth(widthPx: number, wide: boolean): string {
  return wide ? 'max(420px, min(1100px, 100vw - 420px))' : `min(${widthPx}px, max(340px, 100vw - 40px))`;
}

const PANEL = 'flex min-h-0 flex-1 flex-col bg-bg-app p-3 outline-none';

function Note({ icon: Icon, title, detail, children }: { icon: LucideIcon; title: string; detail: string; children?: ReactNode }) {
  return (
    <div className="m-auto flex max-w-80 flex-col items-center gap-2 p-6 text-center">
      <Icon aria-hidden className="size-6 text-fg-faint" strokeWidth={1.5} />
      <p className="m-0 text-base font-medium text-fg-default">{title}</p>
      <p className="m-0 text-sm text-fg-muted">{detail}</p>
      {children && <div className="pt-2">{children}</div>}
    </div>
  );
}

function DockButtons() {
  const wide = useHomeStore((s) => s.workspaceWide);
  const toggleWide = useHomeStore((s) => s.toggleWorkspaceWide);
  const close = useHomeStore((s) => s.closeWorkspace);
  const wideLabel = wide ? 'Restore size' : 'Expand';
  return (
    <>
      <Button variant="quiet" size="icon-sm" onClick={toggleWide} aria-label={wideLabel} title={wideLabel} className="size-7.5 rounded-lg">
        {wide ? <Minimize2 aria-hidden strokeWidth={1.75} /> : <Maximize2 aria-hidden strokeWidth={1.75} />}
      </Button>
      <Button variant="quiet" size="icon-sm" onClick={close} aria-label="Close workspace" title="Close workspace" className="size-7.5 rounded-lg">
        <X aria-hidden strokeWidth={1.75} />
      </Button>
    </>
  );
}

/** Who, what they are doing, and a Live pill while they work. */
function Identity({ employee }: { employee: EmployeeSummary }) {
  const live = useLiveTask(employee);
  const pill =
    employee.status === 'working' && !employee.browser_request ? { tone: 'live' as const, label: 'Live' } : presentEmployee(employee).pill;
  return (
    <>
      <Avatar name={employee.name} colorRole={employee.color_role} size="sm" />
      <div className="flex min-w-0 flex-1 flex-col gap-px">
        <span className="truncate text-base font-semibold text-fg-default">{employee.name}’s workspace</span>
        <span className="truncate font-mono text-2xs text-fg-muted">{(live ?? employee.task).text}</span>
      </div>
      <StatusPill size="sm" tone={pill.tone} label={pill.label} />
    </>
  );
}

function CanvasTab({ employee }: { employee: EmployeeSummary }) {
  if (!employee.canvas_node_id) {
    return (
      <Note
        icon={PanelsTopLeft}
        title={`${employee.name} has no Canvas`}
        detail="Add a Canvas node to their workflow, and what they put on it shows up here."
      >
        <Button
          variant="quiet"
          onClick={() => void enterDev({ workflowId: employee.workflow_id })}
          className="h-9 gap-2 rounded-row border-border-strong px-3.5 font-semibold text-fg-default"
        >
          <Code aria-hidden className="size-3.25" />
          Open workflow
        </Button>
      </Note>
    );
  }
  return (
    <Suspense fallback={<Skeleton className="flex-1 rounded-card" />}>
      <WorkspaceCanvas workflowId={employee.workflow_id} nodeId={employee.canvas_node_id} name={employee.name} />
    </Suspense>
  );
}

function DockTabs({ employee, visible }: { employee: EmployeeSummary; visible: boolean }) {
  const tab = useHomeStore((s) => s.workspaceTab);
  const setTab = useHomeStore((s) => s.setWorkspaceTab);
  return (
    <WorkspaceTabs
      tab={tab}
      onTabChange={setTab}
      browser={<BrowserWorkspace key={employee.workflow_id} workflowId={employee.workflow_id} nodes={employee.browser_nodes} visible={visible && tab === 'browser'} />}
      board={<CanvasTab employee={employee} />}
    />
  );
}

function NoEmployee({ status }: { status: 'pending' | 'error' | 'success' }) {
  if (status === 'pending') {
    return (
      <div className={PANEL}>
        <Skeleton className="flex-1 rounded-card" />
      </div>
    );
  }
  return (
    <div className={PANEL}>
      {status === 'error' ? (
        <Note icon={Monitor} title="Couldn’t load your team" detail="Check your connection, then open the Workspace again." />
      ) : (
        <Note icon={Monitor} title="No one on your team yet" detail="Hire someone, and you’ll see what they are working on here." />
      )}
    </div>
  );
}

export function WorkspaceDock() {
  const open = useHomeStore((s) => s.workspaceOpen);
  const widthPx = useHomeStore((s) => s.workspaceWidth);
  const wide = useHomeStore((s) => s.workspaceWide);
  const workspaceFor = useHomeStore((s) => s.workspaceFor);
  const setWidth = useHomeStore((s) => s.setWorkspaceWidth);
  const team = useEmployeesQuery();
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
  const onMove = useCallback((deltaPx: number, startPx: number) => setWidth(startPx - deltaPx), [setWidth]);
  const getStartValue = useCallback(
    () => asideRef.current?.getBoundingClientRect().width ?? useHomeStore.getState().workspaceWidth,
    [],
  );
  const resize = usePanelResize({ axis: 'x', cursor: 'ew-resize', onMove, getStartValue });

  const employee = team.data?.find((e) => e.workflow_id === workspaceFor) ?? team.data?.[0] ?? null;
  const width = dockWidth(widthPx, wide);

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
          ? 'border-l border-border-default opacity-100 duration-(--dur-dock-in) ease-spring max-[1100px]:shadow-dock'
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
          'absolute inset-y-0 left-0 z-10 w-1.5 cursor-ew-resize transition-colors hover:bg-node-agent-soft',
          resize.isResizing && 'bg-node-agent-soft',
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
          <header className="flex h-(--h-home-header) shrink-0 items-center gap-2.5 border-b border-border-default pr-2.5 pl-4">
            {employee ? (
              <Identity employee={employee} />
            ) : (
              <span className="min-w-0 flex-1 truncate text-base font-semibold text-fg-default">Workspace</span>
            )}
            <DockButtons />
          </header>
          {employee ? <DockTabs employee={employee} visible={open} /> : <NoEmployee status={team.status} />}
        </div>
      )}
    </aside>
  );
}

export default WorkspaceDock;
