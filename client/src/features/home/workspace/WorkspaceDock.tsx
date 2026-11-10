/**
 * The Workspace (chat/V2.md §4b/4c, after the ChatGPT agent panel): a dock
 * on the right of Home showing what one employee is working on (homeStore's
 * dockEmployee). Canvas shows the employee's board; Browser and Mobile
 * attach to their saved browser and phone nodes, and the editor shares the
 * same tabs and live surfaces. One header-high row holds the tabs and, at
 * its end, their status as a dot (workspaceStatus), their main action (a
 * round Stop or Resume, else a small labelled Start, Start again, Connect
 * or Retry reset), Expand and Close; the page's own header says who they
 * are. A pill floating at the foot says what is going on, from their status
 * and their latest step.
 *
 * Take over (takeover.ts) sits with the screen it takes, in the Browser
 * tab's row of page controls and in the phone's buttons, and stops a
 * running employee while the owner uses that screen. Meanwhile the body
 * below the row is framed, and the pill at the foot is the banner saying
 * the employee is waiting, with the one Hand back, on every tab. A screen
 * that lets go of the owner (the window lost focus, the view hid) offers
 * Take over again.
 *
 * At 1100px and wider the dock pushes the page aside; narrower, it lies
 * over the page with a modal's shadow. The left edge drags from 360px to
 * the window less 420px, and Expand gives a bigger dock without a drag.
 * Like the sidebar it stays mounted and opens and closes by transitioning
 * its width, so a reload with it open does not animate and reopening it
 * mid-close turns it around. Its contents mount on the first open. Open,
 * width and tab persist (state/homeStore).
 */

import type { LucideIcon } from 'lucide-react';
import { Code, Maximize2, Minimize2, Monitor, PanelsTopLeft, Pause, Play, X } from 'lucide-react';
import BrowserWorkspace from '@/components/browser/BrowserWorkspace';
import { useSurfaceRunning } from '@/components/workspace/activity';
import { WorkspaceTabs } from '@/components/workspace/WorkspaceTabs';
import MobileWorkspace from '@/components/mobile/MobileWorkspace';
import type { SurfaceControl } from '@/components/workspace/surface';
import { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type Ref } from 'react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { usePanelResize } from '@/hooks/usePanelResize';
import { animate } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { enterDev } from '../../../app/useShellActions';
import { useEmployeesQuery } from '../data/employees';
import { workspaceStatus, type StatusTone } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { PrimaryActionButton } from '../employee/PrimaryActionButton';
import { useEmployeeControl } from '../employee/useEmployeeControl';
import { dockEmployee, dockWidth, useHomeStore, type WorkspaceTab } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';
import { StatusDot } from '../ui/primitives';
import { useWorkspaceSteps } from './steps';
import { useTakeOver, type TakeOver } from './takeover';

// Its own chunk: the board's viewers (markdown, code, JSON) stay out of Home's.
const WorkspaceCanvas = lazy(() => import('./WorkspaceCanvas'));

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
      <Button variant="quiet" size="icon-sm" onClick={toggleWide} aria-label={wideLabel} title={wideLabel} className="size-7.5 rounded-full">
        {wide ? <Minimize2 aria-hidden className="size-3.5" strokeWidth={1.75} /> : <Maximize2 aria-hidden className="size-3.5" strokeWidth={1.75} />}
      </Button>
      <Button variant="quiet" size="icon-sm" onClick={close} aria-label="Close workspace" title="Close workspace" className="size-7.5 rounded-full">
        <X aria-hidden strokeWidth={1.75} />
      </Button>
    </>
  );
}

/** Their main action (useEmployeeControl), named in its title: a round
 *  Stop, or a round Resume, the invert primary as starting them is; Start, Start again,
 *  Connect and Retry reset small and labelled (PrimaryActionButton, which
 *  shows nothing where Home has no action for them). */
function DockAction({ employee, onConnect }: { employee: EmployeeSummary; onConnect: (providerId: string) => void }) {
  const control = useEmployeeControl(employee, onConnect);
  const { kind } = control.view.primary;
  if (kind === 'pause') {
    return (
      <Button
        variant="quiet"
        size="icon-sm"
        disabled={control.busy}
        onClick={control.act}
        aria-label={control.label}
        title={control.label}
        className="size-7.5 rounded-full"
      >
        <Pause aria-hidden fill="currentColor" className="size-3.25" />
      </Button>
    );
  }
  if (kind === 'resume') {
    return (
      <Button
        variant="invert"
        size="icon-sm"
        disabled={control.busy}
        onClick={control.act}
        aria-label={control.label}
        title={control.label}
        className="size-7.5 shrink-0 justify-center rounded-full p-0"
      >
        <Play aria-hidden fill="currentColor" className="size-3.25" />
      </Button>
    );
  }
  return <PrimaryActionButton control={control} className="h-7.5 px-3" />;
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

/** A step's time, as the owner reads a clock. */
function clock(at: string): string {
  return new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/**
 * The pill floating at the dock's foot, over the room each tab body keeps
 * free there: their status dot and what is going on. While the owner has
 * the screen it is the banner, "You’re in control · {Name} is waiting",
 * with the one Hand back; at the foot it covers nothing (under the
 * toolbar it hid the employee's request). Otherwise their status while
 * they are not live (Stopped, Needs you…); while they are, their latest
 * step (steps.ts) and its time, or Waiting for work before the first. The
 * same live region carries all of it, so each change is read out.
 */
function StatusLine({ employee, status, takeover, handBack }: {
  employee: EmployeeSummary;
  status: { label: string; tone: StatusTone };
  takeover: TakeOver;
  handBack: Ref<HTMLButtonElement>;
}) {
  const steps = useWorkspaceSteps(employee.workflow_id).data ?? [];
  const step = steps[steps.length - 1];
  const [text, at] = takeover.active
    ? [`You’re in control · ${employee.name} is waiting`, null]
    : status.tone !== 'live'
      ? [status.label, null]
      : step
        ? [step.text, clock(step.at)]
        : ['Waiting for work', null];
  return (
    <div className="pointer-events-none absolute inset-x-4 bottom-3.5 z-30 flex justify-center">
      <div
        data-slot="workspace-status"
        className={cn(
          'flex h-8 min-w-0 items-center gap-2.5 rounded-pill border bg-bg-elevated text-xs font-medium whitespace-nowrap shadow-card-hover',
          takeover.active
            ? 'pointer-events-auto border-action-config-border pr-0.75 pl-3.5 text-action-config-ink'
            : 'border-border-default px-3.5 text-fg-default',
        )}
      >
        <span aria-live="polite" className="flex min-w-0 items-center gap-2">
          <StatusDot tone={status.tone} className="shrink-0" />
          <span className="truncate">{text}</span>
          {at && <span className="shrink-0 text-fg-faint">{at}</span>}
        </span>
        {takeover.active && (
          <Button
            ref={handBack}
            variant="invert"
            size="xs"
            disabled={takeover.busy}
            onClick={() => void takeover.handBack()}
            className="rounded-pill px-2.5 font-semibold"
          >
            Hand back
          </Button>
        )}
      </div>
    </div>
  );
}

/**
 * The tabs and their row, the floating status, and Take over: the Browser
 * and phone views hold it next to their controls while the owner does not
 * have the screen; while they do, the body below the row is framed and the
 * pill at its foot is the banner holding Hand back, whichever tab shows.
 * The keyboard goes to Hand back once they take a screen, and to the tab on
 * show once the takeover ends.
 */
function DockBody({ employee, visible, onConnect }: { employee: EmployeeSummary; visible: boolean; onConnect: (providerId: string) => void }) {
  const tab = useHomeStore((s) => s.workspaceTab);
  const setTab = useHomeStore((s) => s.setWorkspaceTab);
  const browserRef = useRef<SurfaceControl>(null);
  const mobileRef = useRef<SurfaceControl>(null);
  const takeover = useTakeOver(employee, tab, () =>
    tab === 'browser' ? browserRef.current : tab === 'android' ? mobileRef.current : null,
  );
  const status = workspaceStatus(employee, takeover.active);
  // A dot on a tab while its surface is busy: one of its nodes running, or,
  // for the browser, the employee waiting for the owner there.
  const browserIds = useMemo(() => employee.browser_nodes.map((node) => node.node_id), [employee.browser_nodes]);
  const mobileNodes = useMemo(() => employee.workspace_nodes.filter((node) => node.kind === 'mobile'), [employee.workspace_nodes]);
  const mobileIds = useMemo(() => mobileNodes.map((node) => node.node_id), [mobileNodes]);
  const browserRunning = useSurfaceRunning(employee.workflow_id, browserIds);
  const mobileRunning = useSurfaceRunning(employee.workflow_id, mobileIds);

  // Take over leaves the screen the owner now holds, so taking one moves the
  // keyboard to Hand back, after the render that shows it, unless they went
  // on elsewhere while the screen was being taken. When the takeover ends
  // (Hand back, or a Stop that failed) Hand back goes with the keyboard on
  // it, so the keyboard goes to the tab on show.
  const root = useRef<HTMLDivElement>(null);
  const handBack = useRef<HTMLButtonElement>(null);
  const focusHandBack = useRef(false);
  const wasActive = useRef(takeover.active);
  useEffect(() => {
    const focused = document.activeElement;
    if (focusHandBack.current) {
      focusHandBack.current = false;
      if (!focused || focused === document.body || root.current?.contains(focused)) handBack.current?.focus();
    }
    if (wasActive.current && !takeover.active && focused === document.body) {
      root.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus();
    }
    wasActive.current = takeover.active;
  });

  // Each view shows Take over while the owner does not hold its screen.
  // While they have this employee, only the screen they took offers it: it
  // lets go when the window loses focus or the view hides, and they take it
  // again.
  const takeOverOn = (screen: WorkspaceTab) =>
    takeover.active && takeover.screen !== screen ? undefined : (
      <Button
        variant="quiet"
        disabled={takeover.busy}
        onClick={() =>
          void takeover.takeOver(() => {
            focusHandBack.current = true;
          })
        }
        className="h-8.5 shrink-0 rounded-pill border-border-default bg-bg-elevated px-3.5 text-sm font-medium text-fg-default"
      >
        Take over
      </Button>
    );

  return (
    <div ref={root} className="relative flex min-h-0 flex-1 flex-col">
      <WorkspaceTabs
        chrome="home"
        tab={tab}
        onTabChange={setTab}
        activity={{ browser: browserRunning || Boolean(employee.browser_request), android: mobileRunning }}
        end={
          <>
            <span role="status" aria-label={status.label} title={status.label} className="grid h-7.5 w-5.5 shrink-0 place-items-center">
              <StatusDot tone={status.tone} className="size-2" />
            </span>
            <DockAction employee={employee} onConnect={onConnect} />
            <DockButtons />
          </>
        }
        browser={
          <BrowserWorkspace
            key={employee.workflow_id}
            ref={browserRef}
            workflowId={employee.workflow_id}
            nodes={employee.browser_nodes}
            visible={visible && tab === 'browser'}
            agentName={employee.name}
            chrome="home"
            action={takeOverOn('browser')}
          />
        }
        android={
          <MobileWorkspace
            key={employee.workflow_id}
            ref={mobileRef}
            workflowId={employee.workflow_id}
            nodes={mobileNodes}
            visible={visible && tab === 'android'}
            canvasNodeId={employee.canvas_node_id}
            notify={(message, tone) => pillToast(message, { tone })}
            chrome="home"
            action={takeOverOn('android')}
          />
        }
        board={<CanvasTab employee={employee} />}
      />
      <StatusLine employee={employee} status={status} takeover={takeover} handBack={handBack} />
      {takeover.active && (
        <div
          aria-hidden
          data-slot="workspace-takeover-frame"
          className="pointer-events-none absolute inset-x-0 top-(--h-home-header) bottom-0 z-20 border-2 border-action-config-border"
        />
      )}
    </div>
  );
}

/** No one to show yet: a plain row with Expand and Close, and why. */
function NoEmployee({ status }: { status: 'pending' | 'error' | 'success' }) {
  return (
    <>
      <div className="flex h-(--h-home-header) shrink-0 items-center gap-1.5 border-b border-border-default pr-2 pl-4">
        <span className="min-w-0 flex-1 truncate text-base font-semibold text-fg-default">Workspace</span>
        <DockButtons />
      </div>
      <div className={PANEL}>
        {status === 'pending' ? (
          <Skeleton className="flex-1 rounded-card" />
        ) : status === 'error' ? (
          <Note icon={Monitor} title="Couldn’t load your team" detail="Check your connection, then open the Workspace again." />
        ) : (
          <Note icon={Monitor} title="No one on your team yet" detail="Hire someone, and you’ll see what they are working on here." />
        )}
      </div>
    </>
  );
}

/** `onConnect`: opens an app's connect dialog, for a main action that connects one. */
export function WorkspaceDock({ onConnect }: { onConnect: (providerId: string) => void }) {
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
  // What sits beside the dock (the Get started card) follows a drag at once.
  const setResizing = useHomeStore((s) => s.setWorkspaceResizing);
  useEffect(() => setResizing(resize.isResizing), [resize.isResizing, setResizing]);

  const employee = dockEmployee(team.data, workspaceFor);
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
          // Neutral: on Home colour marks state or role, and a handle is neither.
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
          {employee ? <DockBody employee={employee} visible={open} onConnect={onConnect} /> : <NoEmployee status={team.status} />}
        </div>
      )}
    </aside>
  );
}

export default WorkspaceDock;
