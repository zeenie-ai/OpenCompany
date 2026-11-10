/**
 * What the Workspace dock shows, the same on Home and in the editor (chat/V2.md
 * §4b/4c, after the ChatGPT agent panel). One header-high row holds the tabs
 * and, at its end, the employee's status as a dot (workspaceStatus), the
 * host's main action, Expand and Close. Canvas shows a board; Browser and
 * Mobile attach to the workflow's browser and phone nodes. A pill floating
 * at the foot says what is going on, from the employee's status and their
 * latest step.
 *
 * Take over (takeover.ts) sits with the screen it takes, in the Browser
 * tab's row of page controls and in the phone's buttons, and stops a
 * running employee while the owner uses that screen. Meanwhile the body
 * below the row is framed, and the pill at the foot is the banner saying
 * the employee is waiting, with the one Hand back, on every tab. A screen
 * that lets go of the owner (the window lost focus, the view hid) offers
 * Take over again.
 *
 * Every workflow is an employee, so the editor passes the open workflow's.
 * Where there is no one (an unsaved workflow) there is no status, pill or
 * dock-run Take over: the tabs and the views only, and each screen's own
 * Take over.
 */

import { useEffect, useMemo, useRef, type ReactNode, type Ref } from 'react';
import BrowserWorkspace from '@/components/browser/BrowserWorkspace';
import MobileWorkspace from '@/components/mobile/MobileWorkspace';
import { Button } from '@/components/ui/button';
import { useSurfaceRunning } from '@/components/workspace/activity';
import type { Notify, SurfaceControl } from '@/components/workspace/surface';
import { TakeOverButton } from '@/components/workspace/TakeOverButton';
import { DockButtons } from '@/components/workspace/WorkspaceDockShell';
import { WorkspaceTabs, type WorkspaceTab } from '@/components/workspace/WorkspaceTabs';
import { cn } from '@/lib/utils';
import { workspaceStatus, type StatusTone } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { StatusDot } from '../ui/primitives';
import { useWorkspaceSteps } from './steps';
import { useTakeOver, type TakeOver } from './takeover';

interface SurfaceNode {
  node_id: string;
  label: string;
}

export interface WorkspaceViewProps {
  /** The workflow whose Workspace this is; none for the editor's unsaved one. */
  workflowId: string | null | undefined;
  /** Who works in it: their status, the status pill and Take over. Null
   *  when there is no one (the editor's unsaved workflow); undefined while
   *  that is not known yet, when no Take over shows at all, so the owner
   *  never takes a screen the dock is about to run Take over for. */
  employee: EmployeeSummary | null | undefined;
  /** Its browser and phone nodes. Pass stable (memoized) lists. */
  browserNodes: SurfaceNode[];
  mobileNodes: SurfaceNode[];
  /** The Canvas board a phone screenshot goes to. */
  canvasNodeId: string | null;
  /** The Canvas tab's body. */
  board: ReactNode;
  tab: WorkspaceTab;
  onTabChange: (tab: WorkspaceTab) => void;
  /** The dock is open; closed, the live views stop. */
  visible: boolean;
  /** Between the status dot and Expand: Home's main action for the employee
   *  (the editor's toolbar has the workflow's). */
  action?: ReactNode;
  wide: boolean;
  onToggleWide: () => void;
  onClose: () => void;
  notify: Notify;
  /** Dev mode's extras in the Browser and phone views. */
  dev?: boolean;
  /** Dev mode: saves the canvas before Browser AI tasks adds an agent to it. */
  flushCanvas?: () => void;
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
export function WorkspaceView({
  workflowId,
  employee: known,
  browserNodes,
  mobileNodes,
  canvasNodeId,
  board,
  tab,
  onTabChange,
  visible,
  action,
  wide,
  onToggleWide,
  onClose,
  notify,
  dev = false,
  flushCanvas,
}: WorkspaceViewProps) {
  const employee = known ?? null;
  const browserRef = useRef<SurfaceControl>(null);
  const mobileRef = useRef<SurfaceControl>(null);
  const takeover = useTakeOver(
    employee,
    tab,
    () => (tab === 'browser' ? browserRef.current : tab === 'android' ? mobileRef.current : null),
    notify,
  );
  const status = employee ? workspaceStatus(employee, takeover.active) : null;
  // A dot on a tab while its surface is busy: one of its nodes running, or,
  // for the browser, the employee waiting for the owner there.
  const browserIds = useMemo(() => browserNodes.map((node) => node.node_id), [browserNodes]);
  const mobileIds = useMemo(() => mobileNodes.map((node) => node.node_id), [mobileNodes]);
  const browserRunning = useSurfaceRunning(workflowId, browserIds);
  const mobileRunning = useSurfaceRunning(workflowId, mobileIds);

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
  // again. With no employee the view keeps its own (undefined), which takes
  // the screen and stops no one; while it is not known whether there is one,
  // none shows (null).
  const takeOverOn = (screen: WorkspaceTab) => {
    if (known === undefined) return null;
    if (!employee) return undefined;
    if (takeover.active && takeover.screen !== screen) return null;
    return (
      <TakeOverButton
        disabled={takeover.busy}
        onClick={() =>
          void takeover.takeOver(() => {
            focusHandBack.current = true;
          })
        }
      />
    );
  };

  // The live views belong to the workflow: another one starts them afresh.
  const viewKey = workflowId ?? 'unsaved';

  return (
    <div ref={root} className="relative flex min-h-0 flex-1 flex-col">
      <WorkspaceTabs
        tab={tab}
        onTabChange={onTabChange}
        activity={{ browser: browserRunning || Boolean(employee?.browser_request), android: mobileRunning }}
        end={
          <>
            {status && (
              <span role="status" aria-label={status.label} title={status.label} className="grid h-7.5 w-5.5 shrink-0 place-items-center">
                <StatusDot tone={status.tone} className="size-2" />
              </span>
            )}
            {action}
            <DockButtons wide={wide} onToggleWide={onToggleWide} onClose={onClose} />
          </>
        }
        browser={
          <BrowserWorkspace
            key={viewKey}
            ref={browserRef}
            workflowId={workflowId}
            nodes={browserNodes}
            visible={visible && tab === 'browser'}
            agentName={employee?.name}
            action={takeOverOn('browser')}
            dev={dev}
            flushCanvas={flushCanvas}
          />
        }
        android={
          <MobileWorkspace
            key={viewKey}
            ref={mobileRef}
            workflowId={workflowId}
            nodes={mobileNodes}
            visible={visible && tab === 'android'}
            canvasNodeId={canvasNodeId}
            notify={notify}
            action={takeOverOn('android')}
            dev={dev}
          />
        }
        board={board}
      />
      {employee && status && <StatusLine employee={employee} status={status} takeover={takeover} handBack={handBack} />}
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

export default WorkspaceView;
