/**
 * Home's Workspace: the dock on the right showing what one employee is
 * working on (homeStore's dockEmployee). The dock itself is shared with the
 * editor: the shell (components/workspace/WorkspaceDockShell: the sliding
 * panel whose left edge drags, lying over the page under 1100px) and what
 * it shows (WorkspaceView: the row of tabs with the employee's status, the
 * Browser, Canvas and Mobile views, the floating status pill, Take over and
 * Hand back). This file is what Home adds: whose Workspace it is, their
 * main action at the end of the row (a round Stop or Resume, else a small
 * labelled Start, Start again, Connect or Retry reset; the page's own
 * header says who they are), their Canvas board, and the pill toast.
 *
 * Open, width and tab persist (state/homeStore); the left edge drags from
 * 360px to the window less 420px, and Expand gives a bigger dock without a
 * drag (components/workspace/dockSize).
 */

import type { LucideIcon } from 'lucide-react';
import { Code, Monitor, PanelsTopLeft, Pause, Play } from 'lucide-react';
import { Suspense, lazy, useMemo, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { dockWidth } from '@/components/workspace/dockSize';
import type { Notify } from '@/components/workspace/surface';
import { DockButtons, WorkspaceDockShell } from '@/components/workspace/WorkspaceDockShell';
import { enterDev } from '../../../app/useShellActions';
import { useEmployeesQuery } from '../data/employees';
import type { EmployeeSummary } from '../data/schemas';
import { PrimaryActionButton } from '../employee/PrimaryActionButton';
import { useEmployeeControl } from '../employee/useEmployeeControl';
import { dockEmployee, useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';
import { WorkspaceView } from './WorkspaceView';

// Its own chunk: the board's viewers (markdown, code, JSON) stay out of Home's.
const WorkspaceCanvas = lazy(() => import('./WorkspaceCanvas'));

const PANEL = 'flex min-h-0 flex-1 flex-col bg-bg-app p-3 outline-none';

/** Home says what happened in its pill toast. */
const notify: Notify = (message, tone) => {
  pillToast(message, { tone });
};

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

function HomeDockButtons() {
  const wide = useHomeStore((s) => s.workspaceWide);
  const toggleWide = useHomeStore((s) => s.toggleWorkspaceWide);
  const close = useHomeStore((s) => s.closeWorkspace);
  return <DockButtons wide={wide} onToggleWide={toggleWide} onClose={close} />;
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

/** One employee's Workspace: the shared view, with what Home knows of them. */
function EmployeeWorkspace({ employee, visible, onConnect }: { employee: EmployeeSummary; visible: boolean; onConnect: (providerId: string) => void }) {
  const tab = useHomeStore((s) => s.workspaceTab);
  const setTab = useHomeStore((s) => s.setWorkspaceTab);
  const wide = useHomeStore((s) => s.workspaceWide);
  const toggleWide = useHomeStore((s) => s.toggleWorkspaceWide);
  const close = useHomeStore((s) => s.closeWorkspace);
  const mobileNodes = useMemo(() => employee.workspace_nodes.filter((node) => node.kind === 'mobile'), [employee.workspace_nodes]);
  return (
    <WorkspaceView
      workflowId={employee.workflow_id}
      employee={employee}
      browserNodes={employee.browser_nodes}
      mobileNodes={mobileNodes}
      canvasNodeId={employee.canvas_node_id}
      board={<CanvasTab employee={employee} />}
      tab={tab}
      onTabChange={setTab}
      visible={visible}
      action={<DockAction employee={employee} onConnect={onConnect} />}
      wide={wide}
      onToggleWide={toggleWide}
      onClose={close}
      notify={notify}
    />
  );
}

/** No one to show yet: a plain row with Expand and Close, and why. */
function NoEmployee({ status }: { status: 'pending' | 'error' | 'success' }) {
  return (
    <>
      <div className="flex h-(--h-home-header) shrink-0 items-center gap-1.5 border-b border-border-default pr-2 pl-4">
        <span className="min-w-0 flex-1 truncate text-base font-semibold text-fg-default">Workspace</span>
        <HomeDockButtons />
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
  // What sits beside the dock (the Get started card) follows a drag at once.
  const setResizing = useHomeStore((s) => s.setWorkspaceResizing);
  const team = useEmployeesQuery();
  const employee = dockEmployee(team.data, workspaceFor);

  return (
    <WorkspaceDockShell open={open} width={dockWidth(widthPx, wide)} onWidth={setWidth} onResizing={setResizing}>
      {employee ? <EmployeeWorkspace employee={employee} visible={open} onConnect={onConnect} /> : <NoEmployee status={team.status} />}
    </WorkspaceDockShell>
  );
}

export default WorkspaceDock;
